"""Testes do roleroster.sh: carrega o arquivo de verdade em zsh e em bash (as mesmas classes rodam nos dois),
com ROLEROSTER_HOME isolado e um `assume` de mentira que registra os argumentos recebidos."""
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from roleroster import __version__

SCRIPT = Path(__file__).resolve().parent.parent / "src" / "roleroster" / "shell" / "roleroster.sh"
FISH_SCRIPT = SCRIPT.with_name("roleroster.fish")
BIN_DIR = str(Path(sys.executable).parent)  # onde o venv instala o comando roleroster
STUB = """echo "assume $*" >> "$STUB_LOG"
if [ -n "$STUB_FAIL" ]; then return 1; fi
case " $* " in *" -u "*) echo "${STUB_URL:-https://signin.example/console?token=abc}"; return 0;; esac
export AWS_PROFILE="$1"
export AWS_CREDENTIAL_EXPIRATION="$STUB_EXPIRATION"
"""


FISH_STUB = """echo "assume $argv" >> $STUB_LOG
if test -n "$STUB_FAIL"
    false
else if contains -- -u $argv
    if test -n "$STUB_URL"
        echo $STUB_URL
    else
        echo 'https://signin.example/console?token=abc'
    end
else
    set -gx AWS_PROFILE $argv[1]
    set -gx AWS_CREDENTIAL_EXPIRATION $STUB_EXPIRATION
end
"""  # sem `return`: num source dentro de uma funcao o fish sai da funcao, e o assume.fish de verdade usa `exit`


def iso(seconds_from_now):
    moment = datetime.datetime.fromtimestamp(time.time() + seconds_from_now, datetime.UTC)
    return moment.isoformat()


class ShellTestCase(unittest.TestCase):
    SHELL_NAME = "zsh"  # as subclasses Bash... e Fish... rodam os mesmos testes no bash e no fish

    @classmethod
    def setUpClass(cls):
        if not shutil.which(cls.SHELL_NAME):
            raise unittest.SkipTest(f"{cls.SHELL_NAME} nao instalado")

    @property
    def is_fish(self):
        return self.SHELL_NAME == "fish"

    def run_shell(self, script, env, load_script=True):
        """Roda `script` num shell novo. Com load_script, carrega antes o script do roleroster do shell."""
        if self.is_fish:
            command = f"source {FISH_SCRIPT}; {script}" if load_script else script
            argv = [shutil.which("fish"), "--no-config", "-c", command]
        else:
            command = f"source {SCRIPT}; {script}" if load_script else script
            argv = [shutil.which(self.SHELL_NAME), "-c", command]  # caminho inteiro: alguns testes trocam o PATH
        return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.home = root / "roleroster_home"
        self.home.mkdir()
        self.state = self.home / "state"
        self.state.mkdir()
        self.stub = root / ("assume_stub.fish" if self.is_fish else "assume_stub.sh")
        self.stub.write_text(FISH_STUB if self.is_fish else STUB)
        self.log = root / "stub.log"
        self.config = root / "config"
        self.config.write_text("[profile perfil]\nregion = us-east-1\n")
        self.env = {
            "PATH": f"{BIN_DIR}:{os.environ['PATH']}",
            "ROLEROSTER_HOME": str(self.home),
            "HOME": os.environ["HOME"],
            "AWS_CONFIG_FILE": str(self.config),
            "AWS_SHARED_CREDENTIALS_FILE": str(root / "credentials"),
            "STUB_LOG": str(self.log),
            "STUB_EXPIRATION": iso(3600),
        }

    def sh(self, script, **extra_env):
        env = {**self.env, **extra_env}
        assign = f"set -g _AWS_ASSUME {self.stub}" if self.is_fish else f"_AWS_ASSUME={self.stub}"
        done = self.run_shell(f"{assign}; {script}", env)
        return done.stdout, done.stderr

    def write_session(self, expires_in, renew_until_in=3 * 3600, renew="1", profile="g4"):
        now = int(time.time())
        (self.state / "session").write_text(
            f"profile={profile}\nexpires={now + expires_in}\nrenew_until={now + renew_until_in}\nrenew={renew}\n"
        )

    def session(self):
        return dict(line.split("=", 1) for line in (self.state / "session").read_text().splitlines())

    def assume_calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []


class AutoRenewTest(ShellTestCase):
    def test_renews_when_less_than_10_minutes_remain_and_keeps_the_window(self):
        self.write_session(expires_in=300)
        window = self.session()["renew_until"]
        _, err = self.sh("_aws_auto_renew")
        self.assertEqual(self.assume_calls(), ["assume g4"])
        self.assertIn("Renovando as credenciais de g4", err)
        session = self.session()
        self.assertEqual(session["renew_until"], window)  # a janela de 12 h nao se estende sozinha
        self.assertGreater(int(session["expires"]), time.time() + 3000)
        self.assertEqual((self.state / "current_profile").read_text().strip(), "g4-exported")
        self.assertFalse((self.state / "renew.lock").exists())

    def test_does_nothing_while_there_is_time(self):
        self.write_session(expires_in=3000)
        self.sh("_aws_auto_renew")
        self.assertEqual(self.assume_calls(), [])

    def test_does_nothing_without_a_session_or_with_renewal_turned_off(self):
        self.sh("_aws_auto_renew")
        self.write_session(expires_in=60, renew="0")
        self.sh("_aws_auto_renew")
        self.assertEqual(self.assume_calls(), [])

    def test_stops_when_the_12_hour_window_is_over_and_says_so(self):
        self.write_session(expires_in=60, renew_until_in=-10)
        _, err = self.sh("_aws_auto_renew; _aws_auto_renew")
        self.assertEqual(self.assume_calls(), [])
        self.assertEqual(err.count("terminou"), 1)  # avisa uma vez, nao a cada prompt

    def test_a_fresh_lock_means_another_terminal_is_renewing(self):
        self.write_session(expires_in=60)
        (self.state / "renew.lock").mkdir()
        self.sh("_aws_auto_renew")
        self.assertEqual(self.assume_calls(), [])
        self.assertTrue((self.state / "renew.lock").exists())

    def test_a_stale_lock_from_a_dead_terminal_is_cleaned_and_the_next_prompt_renews(self):
        self.write_session(expires_in=60)
        lock = self.state / "renew.lock"
        lock.mkdir()
        old = time.time() - 400
        os.utime(lock, (old, old))
        self.sh("_aws_auto_renew")
        self.assertFalse(lock.exists())
        self.assertEqual(self.assume_calls(), [])
        self.sh("_aws_auto_renew")
        self.assertEqual(self.assume_calls(), ["assume g4"])

    def test_failure_backs_off_instead_of_retrying_at_every_prompt(self):
        self.write_session(expires_in=60)
        _, err = self.sh("_aws_auto_renew; _aws_auto_renew; _aws_auto_renew", STUB_FAIL="1")
        self.assertEqual(self.assume_calls(), ["assume g4"])
        self.assertIn("Não consegui renovar", err)
        self.assertFalse((self.state / "renew.lock").exists())


class SsoFlagsTest(ShellTestCase):
    def test_sso_shares_the_session_and_renews_by_default(self):
        out, err = self.sh('role sso perfil; echo "PROFILE=$AWS_PROFILE"')
        self.assertIn("PROFILE=perfil-exported", out)
        self.assertEqual(self.assume_calls(), ["assume perfil"])
        self.assertEqual(self.session()["renew"], "1")
        self.assertIn("renova sozinha até", err)
        self.assertEqual((self.state / "current_profile").read_text().strip(), "perfil-exported")

    def test_the_first_sso_on_a_new_install_creates_the_state_folder(self):
        self.state.rmdir()
        _, err = self.sh("role sso perfil")
        self.assertNotIn("No such file", err)
        self.assertEqual((self.state / "current_profile").read_text().strip(), "perfil-exported")
        self.assertEqual(self.session()["profile"], "perfil")

    def test_no_renew_flag_turns_it_off_and_is_not_passed_to_assume(self):
        _, err = self.sh("role sso --no-renew perfil")
        self.assertEqual(self.assume_calls(), ["assume perfil"])
        self.assertEqual(self.session()["renew"], "0")
        self.assertIn("desligada", err)

    def test_our_flags_can_come_in_any_order_and_never_reach_assume(self):
        self.sh("role sso -i --no-renew perfil -r us-east-1")
        self.assertEqual(self.assume_calls(), ["assume perfil -r us-east-1"])

    def test_a_new_sso_reopens_the_window_after_a_renewal_that_was_off(self):
        self.write_session(expires_in=60, renew="0")
        self.sh("role sso perfil")
        self.assertEqual(self.session()["renew"], "1")

    def test_unset_clears_state_session_and_goes_straight_to_assume(self):
        self.write_session(expires_in=3000)
        (self.state / "current_profile").write_text("g4-exported\n")
        self.sh("role sso --unset")
        self.assertEqual(self.assume_calls(), ["assume --unset"])
        self.assertFalse((self.state / "session").exists())
        self.assertFalse((self.state / "current_profile").exists())

    def test_status_prints_the_session_and_fails_without_one(self):
        _, err = self.sh("role sso --status")
        self.assertIn("Nenhuma sessão", err)
        self.write_session(expires_in=1800)
        _, err = self.sh("role sso --status")
        self.assertIn("g4: credencial válida até", err)
        self.assertEqual(self.assume_calls(), [])

    def test_console_accepts_and_drops_our_flags(self):
        self.sh("role console --no-renew -i perfil")
        self.assertEqual(self.assume_calls(), ["assume --bp AWS -c perfil"])
        self.assertFalse((self.state / "session").exists())  # o console nao cria sessao compartilhada


class RenewalNoticesTest(ShellTestCase):
    """`role notices off` esconde so a linha de andamento; falhas e o fim da janela continuam visiveis."""

    def prefs(self, value):
        (self.home / ".env").write_text(f"ROLEROSTER_RENEW_NOTICES={value}\n")

    def test_the_progress_line_is_shown_by_default(self):
        self.write_session(expires_in=60)
        _, err = self.sh("_aws_auto_renew", LANG="en_US.UTF-8")
        self.assertIn("› Renovando as credenciais de g4...", err)

    def test_turned_off_it_renews_in_silence(self):
        self.prefs("0")
        self.write_session(expires_in=60)
        _, err = self.sh("_aws_auto_renew")
        self.assertEqual(self.assume_calls(), ["assume g4"])
        self.assertNotIn("Renovando", err)
        self.assertEqual(err.strip(), "")

    def test_turned_off_a_failure_is_still_reported(self):
        self.prefs("0")
        self.write_session(expires_in=60)
        _, err = self.sh("_aws_auto_renew", STUB_FAIL="1", LANG="en_US.UTF-8")
        self.assertIn("✖ Não consegui renovar", err)

    def test_turned_off_the_end_of_the_window_is_still_reported(self):
        self.prefs("0")
        self.write_session(expires_in=60, renew_until_in=-10)
        _, err = self.sh("_aws_auto_renew")
        self.assertIn("terminou", err)

    def test_the_env_file_wins_over_what_the_terminal_already_exports(self):
        self.prefs("0")
        self.write_session(expires_in=60)
        _, err = self.sh("_aws_auto_renew", ROLEROSTER_RENEW_NOTICES="1")
        self.assertNotIn("Renovando", err)

    def test_role_notices_changes_the_current_terminal_right_away(self):
        self.write_session(expires_in=60)
        out, err = self.sh('role notices off; echo "N=$ROLEROSTER_RENEW_NOTICES"; _aws_auto_renew')
        self.assertIn("N=0", out)
        self.assertNotIn("Renovando", err)
        self.assertIn("ROLEROSTER_RENEW_NOTICES=0", (self.home / ".env").read_text())


class PortabilityTest(ShellTestCase):
    """O mesmo arquivo precisa funcionar nos dois shells sem deixar sobras."""

    def test_sourcing_prints_nothing_and_defines_the_public_function(self):
        out, err = self.sh("type role >/dev/null && echo definida")
        self.assertEqual((out.strip(), err.strip()), ("definida", ""))

    def test_the_roleroster_hooks_are_registered_exactly_once_even_if_sourced_twice(self):
        if self.is_fish:
            out, _ = self.sh(f"source {FISH_SCRIPT}; functions --handlers")
            self.assertEqual(out.count("_role_prompt"), 1)
        elif self.SHELL_NAME == "bash":
            out, _ = self.sh(f'source {SCRIPT}; echo "$PROMPT_COMMAND"')
            self.assertEqual(out.count("_role_prompt"), 1)
        else:
            out, _ = self.sh(f"source {SCRIPT}; print -l $precmd_functions")
            self.assertEqual(out.split().count("_aws_auto_renew"), 1)

    def test_the_prompt_hook_keeps_the_exit_status_of_the_users_command(self):
        if self.SHELL_NAME != "bash":
            self.skipTest("so o bash usa PROMPT_COMMAND")
        out, _ = self.sh("false; _role_prompt; echo rc=$?")
        self.assertIn("rc=1", out)

    def test_unicode_glyphs_with_a_utf8_locale_and_ascii_without_it(self):
        out, err = self.sh('_role_say ok feito; _role_say fail erro; _role_say note dica', LANG="en_US.UTF-8")
        self.assertEqual(err.splitlines(), ["✔ feito", "✖ erro", "› dica"])
        _, err = self.sh('_role_say ok feito; _role_say fail erro; _role_say note dica', LANG="C", LC_ALL="C")
        self.assertEqual(err.splitlines(), ["+ feito", "x erro", "> dica"])

    def test_colors_only_when_stderr_is_a_terminal(self):
        _, err = self.sh("_role_say ok feito")
        self.assertNotIn("\033", err)


class ShortFlagsTest(ShellTestCase):
    def test_each_short_flag_does_the_same_as_its_long_form(self):
        for short, long in (("-n", "--no-renew"), ("-i", "--inactives")):
            self.log.unlink(missing_ok=True)
            self.sh(f"role sso {short} perfil")
            short_calls = self.assume_calls()
            self.log.unlink(missing_ok=True)
            self.sh(f"role sso {long} perfil")
            self.assertEqual(short_calls, self.assume_calls())

    def test_short_no_renew_turns_renewal_off(self):
        self.sh("role sso -n perfil")
        self.assertEqual(self.session()["renew"], "0")

    def test_short_status_and_unset(self):
        self.write_session(expires_in=1800)
        (self.state / "current_profile").write_text("g4-exported\n")
        _, err = self.sh("role sso -T")
        self.assertIn("g4: credencial válida até", err)
        self.sh("role sso -U")
        self.assertEqual(self.assume_calls(), ["assume --unset"])
        self.assertFalse((self.state / "session").exists())

    def test_squads_and_setup_short_flags_go_to_the_binary_not_to_assume(self):
        _, err = self.sh("role sso -S; role sso -B; role console -S")
        self.assertEqual(self.assume_calls(), [])
        self.assertEqual(err.count("terminal"), 3)


class ConsoleModesTest(ShellTestCase):
    """role console: perfil fixo (padrao), um perfil por conta ou janela visitante."""

    def setUp(self):
        super().setUp()
        self.browser_log = self.home / "browser.log"
        self.browser = self.home / "fake_browser.sh"
        self.browser.write_text(f'#!/bin/sh\necho "$@" >> "{self.browser_log}"\n')
        self.browser.chmod(0o755)

    def prefs(self, **values):
        (self.home / ".env").write_text("".join(f"{k}={v}\n" for k, v in values.items()))

    def browser_calls(self, expected, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            if self.browser_log.exists() and len(self.browser_log.read_text().splitlines()) >= expected:
                break
            time.sleep(0.05)
        return self.browser_log.read_text().splitlines() if self.browser_log.exists() else []

    def test_default_is_a_fixed_profile_named_aws(self):
        self.sh("role console perfil")
        self.assertEqual(self.assume_calls(), ["assume --bp AWS -c perfil"])

    def test_the_profile_name_comes_from_the_env_file(self):
        self.prefs(ROLEROSTER_CONSOLE_MODE="profile", ROLEROSTER_CONSOLE_PROFILE="Contas")
        self.sh("role console perfil")
        self.assertEqual(self.assume_calls(), ["assume --bp Contas -c perfil"])

    def test_one_profile_per_account_is_granted_default_without_bp(self):
        self.prefs(ROLEROSTER_CONSOLE_MODE="account")
        self.sh("role console perfil")
        self.assertEqual(self.assume_calls(), ["assume -c perfil"])

    def test_firefox_always_uses_the_granted_containers(self):
        self.prefs(ROLEROSTER_BROWSER="firefox", ROLEROSTER_CONSOLE_MODE="guest")
        self.sh("role console perfil")
        self.assertEqual(self.assume_calls(), ["assume -c perfil"])

    def test_the_env_file_wins_over_what_the_terminal_already_exports(self):
        self.prefs(ROLEROSTER_CONSOLE_MODE="account")
        self.sh("role console perfil", ROLEROSTER_CONSOLE_MODE="guest")
        self.assertEqual(self.assume_calls(), ["assume -c perfil"])

    def test_guest_gets_the_url_from_granted_and_opens_an_isolated_window(self):
        self.prefs(ROLEROSTER_CONSOLE_MODE="guest", ROLEROSTER_BROWSER="chrome", ROLEROSTER_BROWSER_PATH=self.browser)
        out, err = self.sh("role console perfil -r us-west-2")
        self.assertEqual(self.assume_calls(), ["assume perfil -r us-west-2 -u"])
        calls = self.browser_calls(1)
        self.assertEqual(len(calls), 1, err)
        self.assertIn("https://signin.example/console?token=abc", calls[0])
        self.assertIn("--user-data-dir=", calls[0])
        self.assertNotIn("token=abc", out + err)  # a URL nao vaza para o terminal

    def test_each_guest_window_gets_its_own_data_directory(self):
        self.prefs(ROLEROSTER_CONSOLE_MODE="guest", ROLEROSTER_BROWSER="chrome", ROLEROSTER_BROWSER_PATH=self.browser)
        self.sh("role console perfil; role console perfil")
        dirs = {part for call in self.browser_calls(2) for part in call.split() if part.startswith("--user-data-dir=")}
        self.assertEqual(len(dirs), 2)

    def test_guest_never_touches_the_current_terminal_credentials(self):
        self.prefs(ROLEROSTER_CONSOLE_MODE="guest", ROLEROSTER_BROWSER="chrome", ROLEROSTER_BROWSER_PATH=self.browser)
        setup = "set -gx AWS_PROFILE em-uso" if self.is_fish else "AWS_PROFILE=em-uso"
        out, _ = self.sh(f'{setup}; role console perfil; echo "PROFILE=$AWS_PROFILE"')
        self.assertIn("PROFILE=em-uso", out)

    def test_guest_without_a_url_from_granted_explains_and_opens_nothing(self):
        self.prefs(ROLEROSTER_CONSOLE_MODE="guest", ROLEROSTER_BROWSER="chrome", ROLEROSTER_BROWSER_PATH=self.browser)
        _, err = self.sh("role console perfil", STUB_URL="nao-e-uma-url")
        self.assertIn("não devolveu a URL", err)
        self.assertEqual(self.browser_calls(1, timeout=0.5), [])

    def test_guest_without_a_configured_browser_points_to_role_browser(self):
        self.prefs(ROLEROSTER_CONSOLE_MODE="guest")
        _, err = self.sh("role console perfil")
        self.assertIn("role browser", err)

    def test_guest_when_the_assume_fails_stops_without_opening_anything(self):
        self.prefs(ROLEROSTER_CONSOLE_MODE="guest", ROLEROSTER_BROWSER="chrome", ROLEROSTER_BROWSER_PATH=self.browser)
        self.sh("role console perfil", STUB_FAIL="1")
        self.assertEqual(self.browser_calls(1, timeout=0.5), [])


class RoleDispatchTest(ShellTestCase):
    def test_assume_runs_in_this_terminal(self):
        out, _ = self.sh('role assume perfil -r us-east-1; echo "PROFILE=$AWS_PROFILE"')
        self.assertEqual(self.assume_calls(), ["assume perfil -r us-east-1"])
        self.assertIn("PROFILE=perfil", out)

    def test_other_commands_go_to_the_roleroster_binary(self):
        out, err = self.sh("role --version; role status")
        self.assertIn(f"roleroster {__version__}", out)
        self.assertIn("Nenhuma sessão", err)
        self.assertEqual(self.assume_calls(), [])

    def test_role_init_reloads_the_script_in_this_terminal(self):
        """Recarregar zera o contador de espera da renovacao, que o script poe em 0 ao ser lido."""
        shell_dir = self.home / "shell"
        shell_dir.mkdir()
        installed = FISH_SCRIPT if self.is_fish else SCRIPT
        shutil.copy(installed, shell_dir / installed.name)  # o que o role init instala
        if self.is_fish:
            script = "set -g _AWS_RENEW_RETRY_AT 99; role init --dry-run --shell zsh; echo FIM=$status R=$_AWS_RENEW_RETRY_AT"
        else:
            script = "_AWS_RENEW_RETRY_AT=99; role init --dry-run --shell zsh; echo FIM=$? R=$_AWS_RENEW_RETRY_AT"
        out, err = self.sh(script)
        self.assertIn("FIM=0 R=0", out)
        self.assertNotIn("novo terminal", err)  # nada de pedir terminal novo com a funcao ja carregada

    def test_role_init_passes_its_exit_code_on(self):
        out, _ = self.sh("role init --shell invalido; echo FIM=$status" if self.is_fish
                         else "role init --shell invalido; echo FIM=$?")
        self.assertIn("FIM=2", out)

    def test_tells_granted_not_to_offer_its_own_alias(self):
        out, _ = self.sh('echo "g=$GRANTED_ALIAS_CONFIGURED"')
        self.assertIn("g=true", out)


class LoadFilesTest(ShellTestCase):
    def setUp(self):
        super().setUp()
        for key in ("AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE"):
            del self.env[key]
        (self.home / ".env").write_text(
            'CONFLUENCE_BASE_URL=x\n AWS_CONFIG_FILE="~/.aws/config_teste"\nexport AWS_SHARED_CREDENTIALS_FILE=/tmp/cred_x\n'
        )

    def test_exports_only_the_aws_files_expanding_the_tilde_and_ignoring_spaces_and_quotes(self):
        other = ('if set -q CONFLUENCE_BASE_URL; echo "b=$CONFLUENCE_BASE_URL"; else; echo b=nada; end' if self.is_fish
                 else 'echo "b=${CONFLUENCE_BASE_URL:-nada}"')
        out, _ = self.sh(f'echo "c=$AWS_CONFIG_FILE"; echo "k=$AWS_SHARED_CREDENTIALS_FILE"; {other}')
        self.assertIn(f"c={Path.home()}/.aws/config_teste", out)
        self.assertIn("k=/tmp/cred_x", out)
        self.assertIn("b=nada", out)

    def test_what_is_already_exported_wins(self):
        out, _ = self.sh('echo "c=$AWS_CONFIG_FILE"', AWS_CONFIG_FILE="/ja/exportado")
        self.assertIn("c=/ja/exportado", out)

    def test_a_commented_line_is_not_exported(self):
        (self.home / ".env").write_text("# AWS_CONFIG_FILE=/comentado\n")
        out, _ = self.sh('echo "c=[$AWS_CONFIG_FILE]"')
        self.assertIn("c=[]", out)


class LocalGrantedTest(ShellTestCase):
    """Sem Homebrew o role init instala o Granted em ~/.local/bin, que pode nao estar no PATH."""

    def setUp(self):
        super().setUp()
        self.fake_home = Path(self.tmp.name) / "home"
        self.local = self.fake_home / ".local" / "bin"
        self.local.mkdir(parents=True)
        for name in ("assume", "assume.fish", "assumego", "granted"):
            (self.local / name).write_text("#!/bin/sh\n")
            (self.local / name).chmod(0o755)
        self.assume_name = "assume.fish" if self.is_fish else "assume"
        self.show_path = 'string join : $PATH' if self.is_fish else 'echo "$PATH"'

    def load(self, script, path=None):
        env = {**self.env, "HOME": str(self.fake_home), "PATH": path or f"{BIN_DIR}:/usr/bin:/bin"}
        return self.run_shell(script, env).stdout.strip()

    def test_the_local_folder_is_added_to_the_path_and_assume_is_found_there(self):
        out = self.load(f'echo "$_AWS_ASSUME"; {self.show_path}')
        assume, path = out.splitlines()
        self.assertEqual(assume, str(self.local / self.assume_name))
        self.assertTrue(path.endswith(f":{self.local}"))

    def test_the_path_is_left_alone_when_granted_is_not_there(self):
        for name in ("assume", "assume.fish", "assumego", "granted"):
            (self.local / name).unlink()
        self.assertNotIn(str(self.local), self.load(self.show_path))

    def test_the_folder_is_not_added_twice(self):
        env_path = f"{BIN_DIR}:/usr/bin:/bin:{self.local}"
        self.assertEqual(self.load(self.show_path, path=env_path), env_path)


class MissingGrantedTest(ShellTestCase):
    """Sem o Granted o role avisa em portugues em vez de mostrar um erro de arquivo."""

    def setUp(self):
        super().setUp()
        self.fake_home = Path(self.tmp.name) / "home"
        self.local = self.fake_home / ".local" / "bin"
        self.local.mkdir(parents=True)

    def load(self, script):
        env = {**self.env, "HOME": str(self.fake_home), "PATH": f"{BIN_DIR}:/usr/bin:/bin", "HOMEBREW_PREFIX": str(self.fake_home / "brew")}
        done = self.run_shell(script, env)
        return done.stdout, done.stderr

    @property
    def rc(self):
        return "$status" if self.is_fish else "$?"

    def stubs(self, sync, choose):
        """Troca _aws_sync e _aws_choose por funcoes de mentira, na sintaxe do shell."""
        if self.is_fish:
            return f"function _aws_sync; {sync}; end; function _aws_choose; {choose}; end"
        return f"_aws_sync() {{ {sync}; }}; _aws_choose() {{ {choose}; }}"

    def test_role_assume_says_granted_is_missing(self):
        out, err = self.load(f'role assume algo; echo "rc={self.rc}"')
        self.assertIn("Granted não está instalado", err)
        self.assertIn("role init", err)
        self.assertIn("rc=1", out)
        self.assertNotIn("No such file", err)

    def test_sso_stops_after_the_sync_and_before_choosing_a_profile(self):
        out, err = self.load(f'{self.stubs("echo sync-ok; return 0", "echo escolheu")}; role sso; echo "rc={self.rc}"')
        self.assertIn("sync-ok", out)
        self.assertNotIn("escolheu", out)
        self.assertIn("rc=1", out)
        self.assertIn("Granted não está instalado", err)

    def test_console_stops_the_same_way(self):
        out, err = self.load(f'{self.stubs("return 0", "echo escolheu")}; role console; echo "rc={self.rc}"')
        self.assertNotIn("escolheu", out)
        self.assertIn("rc=1", out)

    def test_granted_installed_after_the_terminal_opened_is_found_on_the_next_use(self):
        name = "assume.fish" if self.is_fish else "assume"
        script = (f'_aws_need_granted 2>/dev/null; echo "antes={self.rc}"; mkdir -p "{self.local}"; '
                  f'printf "#!/bin/sh\\n" > "{self.local}/{name}"; _aws_need_granted; echo "depois={self.rc} $_AWS_ASSUME"')
        out, _ = self.load(script)
        self.assertIn("antes=1", out)
        self.assertIn(f"depois=0 {self.local}/{name}", out)


def _add_shell_variants():
    """As mesmas classes de teste, rodando no bash e no fish (pulam sozinhas se o shell nao existir)."""
    for name, cls in list(globals().items()):
        if isinstance(cls, type) and issubclass(cls, ShellTestCase) and cls is not ShellTestCase:
            for prefix, shell in (("Bash", "bash"), ("Fish", "fish")):
                globals()[prefix + name] = type(prefix + name, (cls,), {"SHELL_NAME": shell})


_add_shell_variants()


if __name__ == "__main__":
    unittest.main()
