import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeKeyring  # noqa: E402

from roleroster import awsconfig as sync  # noqa: E402
from roleroster import (  # noqa: E402
    browsers,
    confluence,  # noqa: E402
    granted,
    onboarding,
    settings,
    vault,
)
from roleroster.confluence import ConfluenceAuthError, ConfluenceError  # noqa: E402

SQUADS = [{"id": "11", "title": "Squad A"}, {"id": "22", "title": "Squad B"}, {"id": "33", "title": "Squad C"}]


class FakeConsole:
    """Terminal falso: respostas pre-definidas e registro do que foi mostrado."""

    def __init__(self, answers=(), secrets=(), confirms=(), interactive=True, squads_choice=None, choices=()):
        self.answers = list(answers)
        self.secrets = list(secrets)
        self.confirms = list(confirms)
        self.choices = list(choices)
        self._interactive = interactive
        self.squads_choice = squads_choice
        self.prompts = []
        self.said = []
        self.headers = []
        self.steps = []
        self.oks = []
        self.fails = []
        self.notes = []
        self.summaries = []
        self.choose_options = None
        self.opened = []
        self.squads_seen = None

    def interactive(self):
        return self._interactive

    def say(self, text=""):
        self.said.append(text)

    def header(self, title, subtitle=""):
        self.headers.append(title)

    def step(self, index, total, title):
        self.steps.append((index, total, title))

    def ok(self, text):
        self.oks.append(text)

    def fail(self, text):
        self.fails.append(text)

    def note(self, text):
        self.notes.append(text)

    def summary(self, title, rows):
        self.summaries.append((title, dict(rows)))

    def ask(self, prompt, default=""):
        self.prompts.append(prompt)
        answer = self.answers.pop(0) if self.answers else ""
        return answer.strip() or default

    def ask_secret(self, prompt):
        self.prompts.append(prompt)
        return self.secrets.pop(0)

    def choose(self, prompt, options, default=0):
        self.choose_options = options
        return self.choices.pop(0) if self.choices else default

    def confirm(self, prompt, default=True):
        return self.confirms.pop(0) if self.confirms else default

    def link(self, url):
        return url

    def open_url(self, url):
        self.opened.append(url)

    def choose_squads(self, squads, selected_ids):
        self.squads_seen = (squads, list(selected_ids))
        return self.squads_choice


class EnvTestCase(unittest.TestCase):
    """Cada teste ganha um .env temporario e um ambiente limpo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_path = Path(self.tmp.name) / ".env"
        self.aws = Path(self.tmp.name) / "aws"
        self.aws.mkdir()
        patcher = mock.patch.dict(os.environ, {}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        patch_dir = mock.patch.object(onboarding, "aws_dir", lambda: self.aws)
        patch_dir.start()
        self.addCleanup(patch_dir.stop)
        self.fill(AWS_CONFIG_FILE=str(self.aws / "config"), AWS_SHARED_CREDENTIALS_FILE=str(self.aws / "credentials"),
                  ROLEROSTER_SOURCE_PROFILE="BASE", ROLEROSTER_BROWSER="chrome")  # navegador ja definido: so a BrowserStepTest testa o passo
        for name, value in (
            ("get_token", lambda: os.environ.get("CONFLUENCE_TOKEN", "")),
            ("require_config", lambda: (os.environ["CONFLUENCE_BASE_URL"], "auth")),
            ("check_credentials", mock.Mock()),
            ("list_folders", mock.Mock(return_value=SQUADS)),
        ):
            patch = mock.patch.object(confluence, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def env_lines(self):
        return dict(line.split("=", 1) for line in self.env_path.read_text().splitlines() if "=" in line)

    def fill(self, **values):
        for key, value in values.items():
            os.environ[key] = value


class FilesQuestionTest(EnvTestCase):
    """Sem AWS_CONFIG_FILE / AWS_SHARED_CREDENTIALS_FILE o onboarding oferece uma escolha; o 1o item e o isolado."""

    def setUp(self):
        super().setUp()
        for key in onboarding.FILE_KEYS:
            del os.environ[key]
        self.fill(CONFLUENCE_BASE_URL="https://a.atlassian.net", CONFLUENCE_EMAIL="a@b.co",
                  CONFLUENCE_TOKEN="t", CONFLUENCE_SQUAD_IDS="11")

    def ready(self, console):
        onboarding.ensure_ready(console, path=self.env_path)

    def test_variables_already_present_mean_no_question(self):
        self.fill(AWS_CONFIG_FILE="/x/config", AWS_SHARED_CREDENTIALS_FILE="/x/credentials")
        console = FakeConsole()
        self.ready(console)
        self.assertEqual((console.prompts, console.choose_options), ([], None))

    def test_the_first_option_is_the_isolated_roleroster_files(self):
        console = FakeConsole()
        self.ready(console)
        saved = self.env_lines()
        self.assertEqual(saved["AWS_CONFIG_FILE"], str((self.aws / "config_roleroster").resolve()))
        self.assertEqual(saved["AWS_SHARED_CREDENTIALS_FILE"], str((self.aws / "credentials_roleroster").resolve()))
        self.assertEqual(os.environ["AWS_CONFIG_FILE"], saved["AWS_CONFIG_FILE"])
        self.assertEqual(console.prompts, [])  # escolher nao e digitar

    def test_the_files_come_before_anything_about_confluence(self):
        for key in ("CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_TOKEN", "CONFLUENCE_SQUAD_IDS"):
            del os.environ[key]
        console = FakeConsole(answers=["acme", "dev@acme.com", "123"], secrets=["t"], squads_choice=["11"])
        self.ready(console)
        self.assertEqual([title for _, _, title in console.steps],
                         [onboarding.TITLE_FILES, onboarding.TITLE_CONFLUENCE, onboarding.TITLE_SQUADS])
        self.assertIn("URL do site", console.prompts[0])

    def test_choosing_the_standard_config_uses_the_standard_credentials_too(self):
        console = FakeConsole(choices=[1])
        self.ready(console)
        saved = self.env_lines()
        self.assertEqual(saved["AWS_CONFIG_FILE"], str((self.aws / "config").resolve()))
        self.assertEqual(saved["AWS_SHARED_CREDENTIALS_FILE"], str((self.aws / "credentials").resolve()))

    def test_another_path_asks_for_both_and_defaults_the_credentials_next_to_the_config(self):
        other = self.aws / "teste" / "meu_config"
        console = FakeConsole(choices=[2], answers=[str(other), ""])
        self.ready(console)
        self.assertEqual(self.env_lines()["AWS_CONFIG_FILE"], str(other.resolve()))
        self.assertEqual(self.env_lines()["AWS_SHARED_CREDENTIALS_FILE"], str((other.parent / "credentials_roleroster").resolve()))

    def test_only_the_missing_one_is_asked(self):
        self.fill(AWS_CONFIG_FILE=str(self.aws / "teste" / "meu_config"))
        console = FakeConsole(answers=[""])
        self.ready(console)
        self.assertEqual(len(console.prompts), 1)
        self.assertIn("credenciais", console.prompts[0])
        self.assertIsNone(console.choose_options)
        self.assertEqual(self.env_lines()["AWS_SHARED_CREDENTIALS_FILE"], str((self.aws / "teste" / "credentials_roleroster").resolve()))

    def test_non_interactive_run_does_not_ask_and_names_the_variables(self):
        with self.assertRaises(ConfluenceError) as raised:
            self.ready(FakeConsole(interactive=False))
        self.assertIn("AWS_CONFIG_FILE", str(raised.exception))
        self.assertIn("AWS_SHARED_CREDENTIALS_FILE", str(raised.exception))

    def test_the_standard_config_is_never_adopted_silently_but_the_option_says_it_has_the_block(self):
        (self.aws / "config").write_text(f"[profile x]\n{sync.BEGIN}\n{sync.END}\n")
        console = FakeConsole(confirms=[False])
        self.ready(console)
        self.assertEqual(self.env_lines()["AWS_CONFIG_FILE"], str((self.aws / "config_roleroster").resolve()))
        self.assertEqual(console.choose_options[1][1], "já tem perfis do roleroster")

    def test_no_hint_when_the_standard_config_has_no_generated_block(self):
        (self.aws / "config").write_text("[profile x]\nregion = us-east-1\n")
        console = FakeConsole(confirms=[False])
        self.ready(console)
        self.assertEqual(console.choose_options[1][1], "~/.aws/config")


class InitialSectionTest(EnvTestCase):
    """O trecho antes de ACCOUNTS (sso-session e perfil base) nao aparece sozinho: o onboarding pergunta o minimo."""

    def setUp(self):
        super().setUp()
        del os.environ["ROLEROSTER_SOURCE_PROFILE"]
        self.config = self.aws / "config"
        self.fill(AWS_CONFIG_FILE=str(self.config), CONFLUENCE_BASE_URL="https://a.atlassian.net",
                  CONFLUENCE_EMAIL="a@b.co", CONFLUENCE_TOKEN="t", CONFLUENCE_SQUAD_IDS="11")

    def ready(self, console):
        onboarding.ensure_ready(console, path=self.env_path)

    SIMPLE = ["https://acme.awsapps.com/start", "111111111111", "AssumeRole"]  # URL, conta e role do perfil base

    def test_without_a_preset_it_asks_what_nobody_can_guess_and_nothing_else(self):
        console = FakeConsole(answers=self.SIMPLE)
        self.ready(console)
        self.assertEqual(len(console.prompts), 3)
        self.assertIn("URL do portal", console.prompts[0])
        self.assertIn("ID da conta base", console.prompts[1])
        self.assertIn("Role", console.prompts[2])
        text = self.config.read_text()
        self.assertIn("[sso-session roleroster]", text)
        self.assertIn("sso_start_url = https://acme.awsapps.com/start", text)
        self.assertIn("[profile roleroster-base]", text)
        self.assertIn("sso_account_id = 111111111111", text)
        self.assertIn("sso_role_name = AssumeRole", text)
        self.assertIn("region = us-east-1", text)
        self.assertIn("credential_process = granted credential-process --profile roleroster-base", text)
        self.assertEqual(self.env_lines()["ROLEROSTER_SOURCE_PROFILE"], "roleroster-base")
        self.assertEqual(console.oks, ["Perfil base roleroster-base criado (us-east-1, role AssumeRole)"])

    def test_a_complete_preset_means_no_questions_at_all(self):
        values = {"sso_start_url": "https://acme.awsapps.com/start", "base_account_id": "999999999999",
                  "base_role": "Admin", "base_profile": "ACME", "sso_session": "acme-login", "sso_region": "sa-east-1"}
        with mock.patch.object(settings.preset, "load_installed", return_value=values):
            console = FakeConsole()
            self.ready(console)
        self.assertEqual(console.prompts, [])
        text = self.config.read_text()
        self.assertIn("[sso-session acme-login]", text)
        self.assertIn("sso_region = sa-east-1", text)
        self.assertIn("region = sa-east-1", text)  # herda a regiao do SSO
        self.assertIn("[profile ACME]", text)
        self.assertIn("sso_account_id = 999999999999", text)
        self.assertIn("sso_role_name = Admin", text)

    def test_a_partial_preset_only_skips_what_it_knows(self):
        with mock.patch.object(settings.preset, "load_installed", return_value={"sso_start_url": "https://acme.awsapps.com/start"}):
            console = FakeConsole(answers=["111111111111", "AssumeRole"])
            self.ready(console)
        self.assertEqual([p.split(" (")[0] for p in console.prompts], ["ID da conta base", "Role"])

    def test_what_the_user_set_wins_over_the_preset(self):
        os.environ["ROLEROSTER_SSO_URL"] = "https://meu.awsapps.com/start"
        values = {"sso_start_url": "https://acme.awsapps.com/start", "base_account_id": "999999999999", "base_role": "Admin"}
        with mock.patch.object(settings.preset, "load_installed", return_value=values):
            self.ready(FakeConsole())
        self.assertIn("sso_start_url = https://meu.awsapps.com/start", self.config.read_text())

    def test_the_profile_region_can_differ_from_the_sso_region_through_the_preset(self):
        values = {"sso_start_url": "https://a.awsapps.com/start", "base_account_id": "999999999999", "base_role": "R",
                  "sso_region": "us-east-1", "region": "sa-east-1"}
        with mock.patch.object(settings.preset, "load_installed", return_value=values):
            self.ready(FakeConsole())
        text = self.config.read_text()
        self.assertIn("sso_region = us-east-1", text)
        self.assertIn("region = sa-east-1", text)

    def test_the_sso_step_is_announced_with_its_title(self):
        console = FakeConsole(answers=self.SIMPLE)
        self.ready(console)
        self.assertEqual(console.steps, [(1, 1, onboarding.TITLE_SSO)])
        self.assertEqual(console.headers, [])  # um passo so: sem cabecalho de primeira configuracao

    def test_advanced_asks_everything_and_accepts_extra_sso_profiles(self):
        answers = ["https://x.awsapps.com/start", "minha-sessao", "sa-east-1", "", "BASE", "123456789012", "Admin",
                   "Sandbox", "210987654321", ""]
        console = FakeConsole(answers=answers, confirms=[True, False])
        onboarding.ensure_ready(console, path=self.env_path, advanced=True)
        text = self.config.read_text()
        self.assertIn("[sso-session minha-sessao]", text)
        self.assertIn("sso_region = sa-east-1", text)
        self.assertIn("region = sa-east-1", text)  # a regiao dos perfis herda a do SSO
        self.assertEqual(text.count("credential_process"), 1)  # so o perfil base usa o Granted
        self.assertIn("[profile Sandbox]\nsso_session = minha-sessao", text)
        self.assertIn("sso_role_name = AdministratorAccess", text)
        self.assertEqual(self.env_lines()["ROLEROSTER_SOURCE_PROFILE"], "BASE")

    def test_a_portal_url_without_https_is_asked_again(self):
        console = FakeConsole(answers=["acme.awsapps.com", "https://ok.awsapps.com/start", "111111111111", "AssumeRole"])
        self.ready(console)
        self.assertIn("sso_start_url = https://ok.awsapps.com/start", self.config.read_text())
        self.assertEqual(len(console.fails), 1)

    def test_the_section_goes_above_accounts_and_keeps_what_was_there(self):
        self.config.write_text(f"{sync.ACCOUNTS_HEADER}\n\n[profile antigo]\nregion = us-east-1\n")
        self.ready(FakeConsole(answers=self.SIMPLE))
        text = self.config.read_text()
        self.assertLess(text.index("[sso-session"), text.index(sync.ACCOUNTS_HEADER))
        self.assertLess(text.index(sync.ACCOUNTS_HEADER), text.index("[profile antigo]"))

    def test_existing_base_profile_asks_nothing(self):
        self.config.write_text("[profile roleroster-base]\nregion = us-east-1\n")
        console = FakeConsole()
        self.ready(console)
        self.assertEqual((console.prompts, console.steps), ([], []))

    def test_a_saved_answer_is_not_asked_again_even_if_the_profile_is_missing(self):
        self.fill(ROLEROSTER_SOURCE_PROFILE="OUTRO")
        console = FakeConsole()
        self.ready(console)
        self.assertEqual(console.prompts, [])
        self.assertFalse(self.config.exists())

    def test_non_interactive_never_asks(self):
        console = FakeConsole(interactive=False)
        self.ready(console)
        self.assertEqual(console.prompts, [])

    def test_invalid_account_id_is_asked_again(self):
        self.ready(FakeConsole(answers=["https://a.awsapps.com/start", "123", "abc", "111111111111", "AssumeRole"]))
        self.assertIn("sso_account_id = 111111111111", self.config.read_text())

    def test_three_invalid_account_ids_give_up_without_writing(self):
        with self.assertRaises(ConfluenceError):
            self.ready(FakeConsole(answers=["https://a.awsapps.com/start", "1", "2", "3"]))
        self.assertFalse(self.config.exists())

    def test_force_reruns_even_after_an_answer_and_explains_when_it_already_exists(self):
        self.fill(ROLEROSTER_SOURCE_PROFILE="OUTRO")
        console = FakeConsole(answers=["https://a.awsapps.com/start", "111111111111", "AssumeRole"])
        onboarding.ensure_initial_section(console, self.env_path, force=True)
        self.assertIn("[profile OUTRO]", self.config.read_text())
        again = FakeConsole()
        onboarding.ensure_initial_section(again, self.env_path, force=True)
        self.assertEqual(again.prompts, [])
        self.assertTrue(any("já existe" in line for line in again.notes))

    def test_rendering_is_valid_ini_that_the_profile_reader_understands(self):
        text = onboarding.render_initial_section("s", "https://u", "us-east-1", "us-east-1",
                                                 [{"name": "B", "account_id": "1" * 12, "role_name": "R"}])
        names = [p["profile"] for p in confluence.parse_profiles(text)]
        self.assertEqual(names, ["sso-session s", "B"])


class SeedConfigTest(EnvTestCase):
    STANDARD = (
        "[sso-session s]\nsso_region = us-east-1\n\n[profile BASE]\nregion = us-east-1\n\n"
        f"{sync.ACCOUNTS_HEADER}\n\n{sync.BEGIN}\n[profile gerado]\nrole_arn = arn:aws:iam::111111111111:role/x\n{sync.END}\n\n"
        f"{sync.MANUAL_HEADER}\n[profile meu]\nregion = sa-east-1\n"
    )

    def setUp(self):
        super().setUp()
        (self.aws / "config").write_text(self.STANDARD)
        self.target = self.aws / "novo" / "config_roleroster"

    def test_accepting_copies_only_what_was_written_by_hand(self):
        onboarding.seed_config(FakeConsole(confirms=[True]), self.target)
        copied = self.target.read_text()
        for wanted in ("[sso-session s]", "[profile BASE]", "[profile meu]", sync.MANUAL_HEADER):
            self.assertIn(wanted, copied)
        for unwanted in ("[profile gerado]", sync.BEGIN, sync.END):
            self.assertNotIn(unwanted, copied)
        self.assertEqual((self.aws / "config").read_text(), self.STANDARD)

    def test_declining_creates_nothing(self):
        onboarding.seed_config(FakeConsole(confirms=[False]), self.target)
        self.assertFalse(self.target.exists())

    def test_an_existing_target_or_a_missing_standard_config_asks_nothing(self):
        self.target.parent.mkdir()
        self.target.write_text("meu conteudo\n")
        console = FakeConsole(confirms=[True])
        onboarding.seed_config(console, self.target)
        self.assertEqual(self.target.read_text(), "meu conteudo\n")
        (self.aws / "config").unlink()
        onboarding.seed_config(console, self.aws / "outro")
        self.assertEqual(console.confirms, [True])  # nenhuma pergunta consumiu a resposta

    def test_nothing_to_copy_means_no_question(self):
        (self.aws / "config").write_text(f"{sync.BEGIN}\n[profile gerado]\n{sync.END}\n")
        console = FakeConsole(confirms=[True])
        onboarding.seed_config(console, self.target)
        (self.aws / "config").write_text("\n\n")
        onboarding.seed_config(console, self.target)
        self.assertEqual(console.confirms, [True])
        self.assertFalse(self.target.exists())

    def test_the_seeded_file_then_receives_the_block_under_accounts_before_manual(self):
        onboarding.seed_config(FakeConsole(confirms=[True]), self.target)
        profiles_block = sync.render_block(sync.select_profiles([candidate_for_seed()], set()).profiles, "BASE")
        text = sync.apply_block(self.target.read_text(), profiles_block)
        self.assertLess(text.index(sync.ACCOUNTS_HEADER), text.index(sync.BEGIN))
        self.assertLess(text.index(sync.END), text.index(sync.MANUAL_HEADER))
        self.assertLess(text.index(sync.MANUAL_HEADER), text.index("[profile meu]"))
        self.assertEqual(text.count(sync.MANUAL_HEADER), 1)


def candidate_for_seed():
    return {"profile": "novo", "page_title": "Cliente", "role_arn": "arn:aws:iam::222222222222:role/y", "region": "us-east-1"}


class TildeTest(unittest.TestCase):
    def test_home_is_shown_as_tilde_with_or_without_symlinks_and_siblings_are_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "casa"
            home.mkdir()
            with mock.patch.object(Path, "home", return_value=home):
                self.assertEqual(onboarding.tilde(home / ".aws" / "config"), "~/.aws/config")
                self.assertEqual(onboarding.tilde(home.resolve() / ".aws" / "config"), "~/.aws/config")
                self.assertEqual(onboarding.tilde(home), "~")
                self.assertEqual(onboarding.tilde(Path(str(home) + "2") / "x"), str(home) + "2/x")
                self.assertEqual(onboarding.tilde(Path("/outro/x")), "/outro/x")


class NormalizeBaseUrlTest(unittest.TestCase):
    def test_accepts_full_url_wiki_suffix_and_bare_company_name(self):
        for text in ("https://acme.atlassian.net", "https://acme.atlassian.net/", "https://acme.atlassian.net/wiki", "acme"):
            self.assertEqual(onboarding.normalize_base_url(text), "https://acme.atlassian.net", text)

    def test_rejects_garbage_and_plain_http(self):
        for text in ("", "http://acme.atlassian.net", "acme corp", "https://", "ftp://x"):
            self.assertIsNone(onboarding.normalize_base_url(text), text)


FOUND = [
    ("chrome", "Google Chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ("brave", "Brave", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
    ("firefox", "Firefox", "/Applications/Firefox.app/Contents/MacOS/firefox"),
    ("edge", "Microsoft Edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
]


class BrowserStepTest(EnvTestCase):
    """Navegador do console, do login SSO e modo de abertura: perguntados antes do primeiro uso."""

    def setUp(self):
        super().setUp()
        del os.environ["ROLEROSTER_BROWSER"]
        self.set_browsers = mock.Mock(return_value=True)
        for name, value in (("installed", mock.Mock(return_value=FOUND)),):
            patch = mock.patch.object(browsers, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(granted, "set_browsers", self.set_browsers)
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_four_browsers_are_offered_and_the_console_one_is_applied_to_granted(self):
        console = FakeConsole(choices=[1, 0, 1], answers=[])  # brave, SSO igual, modo visitante
        onboarding.ask_browser(console, self.env_path)
        self.set_browsers.assert_called_once_with("brave", FOUND[1][2], FOUND[1][2])
        saved = self.env_lines()
        self.assertEqual(saved["ROLEROSTER_BROWSER"], "brave")
        self.assertEqual(saved["ROLEROSTER_CONSOLE_MODE"], "guest")
        self.assertEqual(saved["ROLEROSTER_CONSOLE_PROFILE"], "")
        self.assertEqual(saved["ROLEROSTER_BROWSER_PATH"], FOUND[1][2])

    def test_the_sso_browser_can_differ_from_the_console_one(self):
        console = FakeConsole(choices=[0, 2, 0], answers=[""])  # chrome no console; SSO no 2o da lista (brave); perfil fixo
        onboarding.ask_browser(console, self.env_path)
        self.set_browsers.assert_called_once_with("chrome", FOUND[0][2], FOUND[1][2])
        self.assertEqual(self.env_lines()["ROLEROSTER_SSO_BROWSER"], "brave")
        self.assertTrue(any("Login SSO no Brave" in text for text in console.oks))

    def test_fixed_profile_asks_the_name_with_aws_as_default(self):
        console = FakeConsole(choices=[0, 0, 0], answers=[""])
        onboarding.ask_browser(console, self.env_path)
        self.assertEqual(self.env_lines()["ROLEROSTER_CONSOLE_PROFILE"], "AWS")
        self.assertIn("Nome do perfil do navegador", console.prompts[0])
        console = FakeConsole(choices=[0, 0, 0], answers=["Contas"])
        onboarding.ask_browser(console, self.env_path)
        self.assertEqual(self.env_lines()["ROLEROSTER_CONSOLE_PROFILE"], "Contas")

    def test_other_modes_do_not_ask_for_a_profile_name(self):
        for mode_index, mode in ((1, "guest"), (2, "account")):
            console = FakeConsole(choices=[0, 0, mode_index])
            onboarding.ask_browser(console, self.env_path)
            self.assertEqual(console.prompts, [])
            self.assertEqual(self.env_lines()["ROLEROSTER_CONSOLE_MODE"], mode)

    def test_firefox_skips_the_mode_question_and_warns_about_the_addon(self):
        console = FakeConsole(choices=[2, 0])  # firefox; SSO igual
        onboarding.ask_browser(console, self.env_path)
        self.assertEqual(self.env_lines()["ROLEROSTER_BROWSER"], "firefox")
        self.assertTrue(any(browsers.FIREFOX_ADDON_URL in note for note in console.notes))
        self.assertEqual(console.prompts, [])

    def test_a_single_installed_browser_is_not_asked(self):
        browsers.installed.return_value = FOUND[:1]
        console = FakeConsole(choices=[1])  # so a pergunta do modo (guest)
        onboarding.ask_browser(console, self.env_path)
        self.set_browsers.assert_called_once_with("chrome", FOUND[0][2], FOUND[0][2])
        self.assertTrue(any("único navegador" in note for note in console.notes))
        self.assertEqual(self.env_lines()["ROLEROSTER_CONSOLE_MODE"], "guest")

    def test_no_browser_found_is_remembered_so_it_is_not_asked_again(self):
        browsers.installed.return_value = []
        console = FakeConsole()
        onboarding.ask_browser(console, self.env_path)
        self.assertEqual(self.env_lines()["ROLEROSTER_BROWSER"], "none")
        self.set_browsers.assert_not_called()
        self.assertTrue(any("role browser" in note for note in console.notes))

    def test_without_granted_the_choice_is_saved_and_the_user_is_told(self):
        self.set_browsers.return_value = False
        console = FakeConsole(choices=[0, 0, 0], answers=[""])
        onboarding.ask_browser(console, self.env_path)
        self.assertEqual(self.env_lines()["ROLEROSTER_BROWSER"], "chrome")
        self.assertTrue(any("Granted não está instalado" in text for text in console.fails))

    def test_the_summary_line_says_what_was_chosen(self):
        console = FakeConsole(choices=[0, 0, 0], answers=["Contas"])
        onboarding.ask_browser(console, self.env_path)
        self.assertIn('Console no Google Chrome: perfil fixo "Contas"', console.oks)

    def test_existing_installs_get_only_this_step_and_it_never_blocks_a_script(self):
        self.fill(CONFLUENCE_BASE_URL="https://a.atlassian.net", CONFLUENCE_EMAIL="a@b.co", CONFLUENCE_TOKEN="t",
                  CONFLUENCE_SQUAD_IDS="11", AWS_CONFIG_FILE=str(self.aws / "c"), AWS_SHARED_CREDENTIALS_FILE=str(self.aws / "k"))
        console = FakeConsole(choices=[0, 0, 0], answers=[""])
        onboarding.ensure_ready(console, path=self.env_path)
        self.assertEqual(console.steps, [(1, 1, onboarding.TITLE_BROWSER)])
        script = FakeConsole(interactive=False)
        del os.environ["ROLEROSTER_BROWSER"]
        onboarding.ensure_ready(script, path=self.env_path)  # sem terminal: nao pergunta e nao levanta
        self.assertEqual(script.steps, [])

    def test_the_browser_step_comes_after_the_sso_step_and_before_confluence_in_a_first_run(self):
        for key in ("CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_TOKEN", "CONFLUENCE_SQUAD_IDS"):
            os.environ.pop(key, None)
        console = FakeConsole(choices=[0, 0, 1], answers=["acme", "dev@acme.com", "123"], secrets=["t"], squads_choice=["11"])
        onboarding.ensure_ready(console, path=self.env_path)
        titles = [title for _, _, title in console.steps]
        self.assertEqual(titles, [onboarding.TITLE_BROWSER, onboarding.TITLE_CONFLUENCE, onboarding.TITLE_SQUADS])


class FirstRunTest(EnvTestCase):
    def test_nothing_missing_means_no_prompts(self):
        self.fill(CONFLUENCE_BASE_URL="https://a.atlassian.net", CONFLUENCE_EMAIL="a@b.co",
                  CONFLUENCE_TOKEN="t", CONFLUENCE_SQUAD_IDS="11")
        console = FakeConsole()
        onboarding.ensure_ready(console, path=self.env_path)
        self.assertEqual((console.prompts, console.steps, console.headers), ([], [], []))

    def test_non_interactive_run_never_prompts_and_names_what_is_missing(self):
        console = FakeConsole(interactive=False)
        with self.assertRaises(ConfluenceError) as raised:
            onboarding.ensure_ready(console, path=self.env_path)
        message = str(raised.exception)
        for key in ("CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL", "CONFLUENCE_TOKEN", "CONFLUENCE_SQUAD_IDS"):
            self.assertIn(key, message)
        self.assertEqual(console.prompts, [])
        self.assertFalse(self.env_path.exists())

    def test_first_run_asks_in_order_and_saves_everything(self):
        console = FakeConsole(answers=["acme", "dev@acme.com", "123"], secrets=["tok=en"], squads_choice=["22", "33"])
        onboarding.ensure_ready(console, path=self.env_path)

        self.assertEqual(len(console.prompts), 4)
        self.assertIn("URL do site", console.prompts[0])
        self.assertIn("E-mail", console.prompts[1])
        self.assertEqual(console.prompts[2], "Token")
        self.assertIn("ID da pasta", console.prompts[3])
        saved = self.env_lines()
        self.assertEqual(saved["CONFLUENCE_BASE_URL"], "https://acme.atlassian.net")
        self.assertEqual(saved["CONFLUENCE_EMAIL"], "dev@acme.com")
        self.assertEqual(saved["CONFLUENCE_TOKEN"], "tok=en")
        self.assertEqual(saved["CONFLUENCE_ROOT_ID"], "123")
        self.assertEqual(saved["CONFLUENCE_SQUAD_IDS"], "22,33")
        self.assertEqual(stat.S_IMODE(self.env_path.stat().st_mode), 0o600)
        self.assertTrue(any(onboarding.TOKEN_URL in text for text in console.notes))
        self.assertIn("2 squads: Squad B, Squad C", console.oks)

    def test_steps_are_numbered_and_the_first_configuration_has_a_header(self):
        console = FakeConsole(answers=["acme", "dev@acme.com", "123"], secrets=["t"], squads_choice=["11"])
        onboarding.ensure_ready(console, path=self.env_path)
        self.assertEqual(console.steps, [(1, 2, onboarding.TITLE_CONFLUENCE), (2, 2, onboarding.TITLE_SQUADS)])
        self.assertEqual(console.headers, ["roleroster · primeira configuração"])

    def test_a_full_first_configuration_ends_with_a_summary(self):
        console = FakeConsole(answers=["acme", "dev@acme.com", "123"], secrets=["t"], squads_choice=["11", "22"])
        onboarding.ensure_ready(console, path=self.env_path)
        title, rows = console.summaries[0]
        self.assertEqual(title, "Configuração concluída")
        self.assertEqual(rows["Confluence"], "https://acme.atlassian.net")
        self.assertEqual(rows["Squads"], "2 selecionados")
        self.assertEqual(rows["Perfil base"], "BASE")

    def test_a_single_missing_piece_has_no_header_and_no_summary(self):
        self.fill(CONFLUENCE_BASE_URL="https://a.atlassian.net", CONFLUENCE_EMAIL="a@b.co", CONFLUENCE_SQUAD_IDS="11")
        console = FakeConsole(secrets=["t"])
        onboarding.ensure_ready(console, path=self.env_path)
        self.assertEqual((console.headers, console.steps), ([], [(1, 1, onboarding.TITLE_CONFLUENCE)]))
        self.assertEqual(console.summaries, [])

    def test_only_the_missing_variables_are_asked(self):
        self.fill(CONFLUENCE_BASE_URL="https://a.atlassian.net", CONFLUENCE_EMAIL="a@b.co", CONFLUENCE_TOKEN="t")
        console = FakeConsole(answers=["999"], squads_choice=["11"])
        onboarding.ensure_ready(console, path=self.env_path)
        self.assertEqual(len(console.prompts), 1)
        confluence.list_folders.assert_called_once_with("https://a.atlassian.net", "auth", "999")

    def test_invalid_url_and_email_are_asked_again(self):
        console = FakeConsole(answers=["http://x", "acme", "sem-arroba", "dev@acme.com", "123"], secrets=["t"], squads_choice=["11"])
        onboarding.ensure_ready(console, path=self.env_path)
        self.assertEqual(self.env_lines()["CONFLUENCE_BASE_URL"], "https://acme.atlassian.net")
        self.assertEqual(self.env_lines()["CONFLUENCE_EMAIL"], "dev@acme.com")
        self.assertEqual(len(console.fails), 2)


class TokenTest(EnvTestCase):
    def setUp(self):
        super().setUp()
        self.fill(CONFLUENCE_BASE_URL="https://a.atlassian.net", CONFLUENCE_EMAIL="a@b.co")

    def test_rejected_token_is_asked_again_and_only_a_valid_one_is_saved(self):
        confluence.check_credentials.side_effect = [ConfluenceAuthError("recusado"), None]
        console = FakeConsole(secrets=["ruim", "bom"])
        onboarding.ask_token(console, self.env_path)
        self.assertEqual(self.env_lines()["CONFLUENCE_TOKEN"], "bom")
        self.assertEqual(os.environ["CONFLUENCE_TOKEN"], "bom")
        self.assertIn("1/3", console.fails[0])
        self.assertEqual(console.oks, ["Conectado ao Confluence"])

    def test_three_rejections_give_up_without_saving(self):
        confluence.check_credentials.side_effect = ConfluenceAuthError("recusado")
        console = FakeConsole(secrets=["a", "b", "c"])
        with self.assertRaises(ConfluenceAuthError):
            onboarding.ask_token(console, self.env_path)
        self.assertFalse(self.env_path.exists())

    def test_after_a_rejection_the_email_is_asked_again_and_a_typo_is_fixed(self):
        confluence.check_credentials.side_effect = [ConfluenceAuthError("recusado"), None]
        console = FakeConsole(answers=["certo@b.co"], secrets=["a", "b"])
        onboarding.ask_token(console, self.env_path)
        self.assertEqual(confluence.check_credentials.call_args.args[1], "certo@b.co")
        self.assertEqual(self.env_lines()["CONFLUENCE_EMAIL"], "certo@b.co")

    def test_pressing_enter_on_the_email_keeps_it(self):
        confluence.check_credentials.side_effect = [ConfluenceAuthError("recusado"), None]
        console = FakeConsole(secrets=["a", "b"])
        onboarding.ask_token(console, self.env_path)
        self.assertEqual(confluence.check_credentials.call_args.args[1], os.environ["CONFLUENCE_EMAIL"])

    def test_a_blank_enter_opens_the_link_once_and_does_not_consume_a_validation(self):
        console = FakeConsole(secrets=["", "ok"])
        onboarding.ask_token(console, self.env_path)
        self.assertEqual(console.opened, [onboarding.TOKEN_URL])
        confluence.check_credentials.assert_called_once()

    def test_a_second_blank_is_reported_instead_of_opening_the_link_again(self):
        console = FakeConsole(secrets=["", "", "ok"])
        onboarding.ask_token(console, self.env_path)
        self.assertEqual(console.opened, [onboarding.TOKEN_URL])
        self.assertEqual(console.fails, ["Token vazio."])

    def test_network_failure_saves_the_token_anyway(self):
        confluence.check_credentials.side_effect = ConfluenceError("sem conexao")
        console = FakeConsole(secrets=["tok"])
        onboarding.ask_token(console, self.env_path)
        self.assertEqual(self.env_lines()["CONFLUENCE_TOKEN"], "tok")
        self.assertTrue(any("token salvo mesmo assim" in text for text in console.notes))

    def test_the_link_is_always_shown_and_never_opened_unasked(self):
        console = FakeConsole(secrets=["t"])
        onboarding.ask_token(console, self.env_path)
        self.assertTrue(any(onboarding.TOKEN_URL in text for text in console.notes))
        self.assertEqual(console.opened, [])

    def test_renew_replaces_the_old_token_and_explains_why(self):
        self.fill(CONFLUENCE_TOKEN="velho")
        self.env_path.write_text("CONFLUENCE_TOKEN=velho\nOUTRA=1\n")
        console = FakeConsole(secrets=["novo"])
        onboarding.renew_token(console, "O token expirou.", self.env_path)
        self.assertEqual(console.fails[0], "O token expirou.")
        self.assertEqual(self.env_lines(), {"CONFLUENCE_TOKEN": "novo", "OUTRA": "1"})


class TokenVaultTest(EnvTestCase):
    """Com um cofre disponivel, o token sai do .env."""

    def setUp(self):
        super().setUp()
        self.fill(CONFLUENCE_BASE_URL="https://a.atlassian.net", CONFLUENCE_EMAIL="a@b.co")
        self.fake = FakeKeyring()
        patch = mock.patch.object(vault, "_keyring", lambda: self.fake)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_valid_token_goes_to_the_vault_and_not_to_the_env_file(self):
        console = FakeConsole(secrets=["tok"])
        onboarding.ask_token(console, self.env_path)
        self.assertEqual(vault.get_token(), "tok")
        self.assertNotIn("CONFLUENCE_TOKEN", self.env_path.read_text() if self.env_path.exists() else "")
        self.assertTrue(any("Keychain do macOS" in note for note in console.notes))

    def test_a_new_token_removes_the_old_one_from_the_env_file(self):
        self.env_path.write_text("CONFLUENCE_TOKEN=velho\nOUTRA=1\n")
        os.environ["CONFLUENCE_TOKEN"] = "velho"
        onboarding.ask_token(FakeConsole(secrets=["novo"]), self.env_path)
        self.assertEqual(self.env_lines(), {"OUTRA": "1"})
        self.assertNotIn("CONFLUENCE_TOKEN", os.environ)
        self.assertEqual(vault.get_token(), "novo")

    def test_when_the_vault_refuses_the_token_stays_in_the_env_file_and_the_user_is_told(self):
        self.fake.fail_set = True
        console = FakeConsole(secrets=["tok"])
        onboarding.ask_token(console, self.env_path)
        self.assertEqual(self.env_lines()["CONFLUENCE_TOKEN"], "tok")
        self.assertTrue(any(".env" in note for note in console.notes))

    def test_a_network_failure_still_stores_the_token_in_the_vault(self):
        confluence.check_credentials.side_effect = ConfluenceError("sem conexao")
        onboarding.ask_token(FakeConsole(secrets=["tok"]), self.env_path)
        self.assertEqual(vault.get_token(), "tok")

    def test_a_rejected_token_is_never_stored(self):
        confluence.check_credentials.side_effect = ConfluenceAuthError("recusado")
        with self.assertRaises(ConfluenceAuthError):
            onboarding.ask_token(FakeConsole(secrets=["a", "b", "c"]), self.env_path)
        self.assertEqual(vault.get_token(), "")

    def test_migration_moves_the_token_out_of_the_env_file(self):
        self.env_path.write_text("CONFLUENCE_TOKEN=do-env\nOUTRA=1\n")
        self.assertTrue(onboarding.migrate_token_to_vault(self.env_path))
        self.assertEqual(vault.get_token(), "do-env")
        self.assertEqual(self.env_lines(), {"OUTRA": "1"})

    def test_migration_keeps_the_env_token_when_the_vault_fails(self):
        self.fake.fail_set = True
        self.env_path.write_text("CONFLUENCE_TOKEN=do-env\n")
        self.assertFalse(onboarding.migrate_token_to_vault(self.env_path))
        self.assertEqual(self.env_lines()["CONFLUENCE_TOKEN"], "do-env")

    def test_migration_without_a_token_in_the_env_file_does_nothing(self):
        self.env_path.write_text("OUTRA=1\n")
        self.assertFalse(onboarding.migrate_token_to_vault(self.env_path))
        self.assertEqual(self.fake.store, {})


class ChooseSquadsTest(EnvTestCase):
    def setUp(self):
        super().setUp()
        self.fill(CONFLUENCE_BASE_URL="https://a.atlassian.net", CONFLUENCE_EMAIL="a@b.co", CONFLUENCE_TOKEN="t")

    def test_current_squads_are_passed_as_preselected_and_saved_choice_replaces_them(self):
        self.fill(CONFLUENCE_ROOT_ID="777", CONFLUENCE_SQUAD_IDS="11,22")
        console = FakeConsole(squads_choice=["33"])
        onboarding.choose_squads(console, self.env_path)
        self.assertEqual(console.squads_seen, (SQUADS, ["11", "22"]))
        self.assertEqual(console.prompts, [])
        self.assertEqual(self.env_lines()["CONFLUENCE_SQUAD_IDS"], "33")
        confluence.list_folders.assert_called_once_with("https://a.atlassian.net", "auth", "777")

    def test_without_a_known_root_it_asks_and_saves_the_answer(self):
        console = FakeConsole(answers=["456"], squads_choice=["11"])
        onboarding.choose_squads(console, self.env_path)
        self.assertIn("ID da pasta", console.prompts[0])
        self.assertEqual(self.env_lines()["CONFLUENCE_ROOT_ID"], "456")
        self.assertTrue(any("/folder/" in note for note in console.notes))  # diz onde achar o ID

    def test_the_preset_knows_the_root_so_it_is_not_asked(self):
        with mock.patch.object(settings.preset, "load_installed", return_value={"root_id": "777"}):
            console = FakeConsole(squads_choice=["11"])
            onboarding.choose_squads(console, self.env_path)
        self.assertEqual(console.prompts, [])
        self.assertEqual(self.env_lines()["CONFLUENCE_ROOT_ID"], "777")

    def test_advanced_asks_the_root_and_empty_answer_keeps_the_current_one(self):
        self.fill(CONFLUENCE_ROOT_ID="777")
        console = FakeConsole(answers=[""], squads_choice=["11"])
        onboarding.choose_squads(console, self.env_path, advanced=True)
        self.assertIn("ID da pasta", console.prompts[0])
        self.assertEqual(self.env_lines()["CONFLUENCE_ROOT_ID"], "777")

    def test_advanced_accepts_another_root(self):
        console = FakeConsole(answers=["999"], squads_choice=["11"])
        onboarding.choose_squads(console, self.env_path, advanced=True)
        self.assertEqual(self.env_lines()["CONFLUENCE_ROOT_ID"], "999")
        confluence.list_folders.assert_called_once_with("https://a.atlassian.net", "auth", "999")

    def test_cancelling_keeps_the_previous_choice(self):
        self.fill(CONFLUENCE_ROOT_ID="777", CONFLUENCE_SQUAD_IDS="11")
        self.env_path.write_text("CONFLUENCE_SQUAD_IDS=11\n")
        for choice in (None, []):
            with self.assertRaises(ConfluenceError):
                onboarding.choose_squads(FakeConsole(squads_choice=choice), self.env_path)
        self.assertEqual(self.env_lines()["CONFLUENCE_SQUAD_IDS"], "11")

    def test_root_without_folders_is_an_error_that_points_to_the_advanced_setup(self):
        confluence.list_folders.return_value = []
        self.fill(CONFLUENCE_ROOT_ID="777")
        with self.assertRaises(ConfluenceError) as raised:
            onboarding.choose_squads(FakeConsole(squads_choice=["11"]), self.env_path)
        self.assertIn("role setup --advanced", str(raised.exception))

    def test_non_numeric_root_is_rejected_three_times(self):
        with self.assertRaises(ConfluenceError):
            onboarding.choose_squads(FakeConsole(answers=["a", "b", "c"]), self.env_path, advanced=True)
        confluence.list_folders.assert_not_called()


class MigrateLegacyTest(EnvTestCase):
    def test_parent_id_becomes_squad_ids(self):
        self.env_path.write_text("CONFLUENCE_EMAIL=a@b.co\nCONFLUENCE_PARENT_ID=300400500\n")
        confluence.load_env_file(self.env_path)
        onboarding.migrate_legacy(self.env_path)
        self.assertEqual(self.env_lines(), {"CONFLUENCE_EMAIL": "a@b.co", "CONFLUENCE_SQUAD_IDS": "300400500"})
        self.assertNotIn("CONFLUENCE_PARENT_ID", os.environ)

    def test_existing_squad_ids_win_and_the_legacy_line_is_dropped(self):
        self.env_path.write_text("CONFLUENCE_PARENT_ID=1\nCONFLUENCE_SQUAD_IDS=2,3\n")
        confluence.load_env_file(self.env_path)
        onboarding.migrate_legacy(self.env_path)
        self.assertEqual(self.env_lines(), {"CONFLUENCE_SQUAD_IDS": "2,3"})

    def test_ensure_ready_migrates_before_deciding_what_is_missing(self):
        self.env_path.write_text(
            "CONFLUENCE_BASE_URL=https://a.atlassian.net\nCONFLUENCE_EMAIL=a@b.co\n"
            "CONFLUENCE_TOKEN=t\nCONFLUENCE_PARENT_ID=300400500\n"
        )
        console = FakeConsole()
        onboarding.ensure_ready(console, path=self.env_path)
        self.assertEqual(console.prompts, [])
        self.assertEqual(self.env_lines()["CONFLUENCE_SQUAD_IDS"], "300400500")


if __name__ == "__main__":
    unittest.main()
