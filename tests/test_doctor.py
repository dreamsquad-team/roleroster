import argparse
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeKeyring  # noqa: E402

from roleroster import confluence, doctor, granted, init, onboarding, profiles, vault  # noqa: E402
from roleroster.confluence import ConfluenceAuthError, ConfluenceError  # noqa: E402

READ_GRANTED_VERSION = doctor.granted_version  # a funcao de verdade; o setUp troca por uma fixa


class FakeConsole:
    def __init__(self):
        self.lines = []

    def interactive(self):
        return True

    def step(self, index, total, title):
        self.lines.append(("step", title))

    def ok(self, text):
        self.lines.append(("ok", text))

    def warn(self, text):
        self.lines.append(("warn", text))

    def fail(self, text):
        self.lines.append(("fail", text))

    def note(self, text):
        self.lines.append(("note", text))

    def of(self, kind):
        return [text for k, text in self.lines if k == kind]


class DoctorTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.env_path = self.root / "rr" / ".env"
        self.env_path.parent.mkdir()
        self.config = self.root / "config"
        self.browser = self.root / "chrome"
        self.browser.write_text("")
        env = {
            "AWS_CONFIG_FILE": str(self.config), "ROLEROSTER_SOURCE_PROFILE": "BASE",
            "CONFLUENCE_BASE_URL": "https://a.atlassian.net", "CONFLUENCE_EMAIL": "a@b.co",
            "CONFLUENCE_SQUAD_IDS": "11", "ROLEROSTER_BROWSER": "chrome",
            "ROLEROSTER_BROWSER_PATH": str(self.browser), "ROLEROSTER_CONSOLE_MODE": "guest",
        }
        for patch in (mock.patch.dict(os.environ, env, clear=True),
                      mock.patch.object(confluence, "ENV_PATH", self.env_path),
                      mock.patch.object(doctor.shutil, "which", lambda name: f"/usr/bin/{name}"),
                      mock.patch.object(doctor, "granted_version", lambda: doctor.TESTED_GRANTED)):
            patch.start()
            self.addCleanup(patch.stop)
        self.config.write_text("[profile BASE]\nregion = us-east-1\n")
        self.env_path.write_text("A=1\n")
        self.env_path.chmod(0o600)
        self.fake = FakeKeyring()
        patch = mock.patch.object(vault, "_keyring", lambda: self.fake)
        patch.start()
        self.addCleanup(patch.stop)
        vault.set_token("tok")

    def statuses(self, results):
        return [r.status for r in results]


class InstallChecksTest(DoctorTestCase):
    def test_everything_installed_is_ok(self):
        self.assertEqual(set(self.statuses(doctor.check_granted() + doctor.check_optional_tools())), {doctor.OK})

    def test_a_missing_granted_fails_with_the_install_command_for_this_system(self):
        with mock.patch.object(doctor.shutil, "which", lambda name: None), mock.patch.object(doctor.sys, "platform", "darwin"):
            result = doctor.check_granted()[0]
        self.assertEqual(result.status, doctor.FAIL)
        self.assertIn("brew install fwdcloudsec/granted/granted", result.hint)

    def test_another_granted_version_is_only_a_warning_that_names_the_tested_one(self):
        with mock.patch.object(doctor, "granted_version", lambda: "0.99.0"):
            result = doctor.check_granted()[0]
        self.assertEqual(result.status, doctor.WARN)
        self.assertIn(doctor.TESTED_GRANTED, result.hint)

    def test_optional_tools_warn_but_never_fail(self):
        with mock.patch.object(doctor.shutil, "which", lambda name: None):
            results = doctor.check_optional_tools()
        self.assertEqual(self.statuses(results), [doctor.WARN, doctor.WARN])
        self.assertIn("fzf", results[0].text)

    def test_the_install_hint_depends_on_the_system(self):
        with mock.patch.object(doctor.sys, "platform", "linux"):
            self.assertIn("apt", doctor.install_hint("fzf"))
        with mock.patch.object(doctor.sys, "platform", "win32"):
            self.assertIn("documentação", doctor.install_hint("fzf"))

    def test_the_version_is_read_from_the_granted_output(self):
        with mock.patch.object(doctor.subprocess, "run", return_value=mock.Mock(stdout="Granted version: 0.40.0\n", stderr="")):
            self.assertEqual(READ_GRANTED_VERSION(), "0.40.0")

    def test_an_unreadable_or_missing_version_is_an_empty_string(self):
        with mock.patch.object(doctor.subprocess, "run", return_value=mock.Mock(stdout="sem numero", stderr="")):
            self.assertEqual(READ_GRANTED_VERSION(), "")
        with mock.patch.object(doctor.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(READ_GRANTED_VERSION(), "")


class ConfigChecksTest(DoctorTestCase):
    def test_a_complete_setup_is_all_ok(self):
        results = (doctor.check_browser() + doctor.check_aws_files() + doctor.check_env_file())
        self.assertEqual(set(self.statuses(results)), {doctor.OK})

    def test_no_browser_chosen_points_to_role_browser(self):
        del os.environ["ROLEROSTER_BROWSER"]
        result = doctor.check_browser()[0]
        self.assertEqual((result.status, result.hint), (doctor.WARN, "role browser"))

    def test_a_browser_that_disappeared_fails(self):
        self.browser.unlink()
        self.assertEqual(doctor.check_browser()[0].status, doctor.FAIL)

    def test_the_browser_line_says_the_console_mode(self):
        self.assertIn("visitante", doctor.check_browser()[0].text)
        os.environ["ROLEROSTER_BROWSER"] = "firefox"
        self.assertIn("containers", doctor.check_browser()[0].text)

    def test_missing_aws_files_or_base_profile(self):
        self.config.write_text("[profile OUTRO]\n")
        self.assertEqual(doctor.check_aws_files()[0].status, doctor.WARN)
        self.config.unlink()
        self.assertEqual(doctor.check_aws_files()[0].status, doctor.FAIL)

    def test_files_not_chosen_yet_are_a_warning_because_onboarding_resolves_them(self):
        del os.environ["AWS_CONFIG_FILE"]
        self.assertEqual(doctor.check_aws_files()[0].status, doctor.WARN)

    def test_a_loose_env_permission_is_fixed_by_the_fix(self):
        self.env_path.chmod(0o644)
        result = doctor.check_env_file()[0]
        self.assertEqual(result.status, doctor.WARN)
        result.fix()
        self.assertEqual(self.env_path.stat().st_mode & 0o777, 0o600)

    def test_granted_config_missing_the_required_keys_is_fixable(self):
        home = self.root / "home"
        (home / ".granted").mkdir(parents=True)
        (home / ".granted" / "config").write_text("DefaultBrowser = \"CHROME\"\n")
        with mock.patch.object(Path, "home", return_value=home), mock.patch.object(granted, "ensure_settings", return_value=["ExportCredsToAWS"]) as ensure:
            result = doctor.check_granted_config()[0]
            self.assertEqual(result.status, doctor.WARN)
            self.assertIn("ExportCredsToAWS", result.fix())
        ensure.assert_called_once()

    def test_granted_config_with_the_required_keys_is_ok(self):
        home = self.root / "home"
        (home / ".granted").mkdir(parents=True)
        (home / ".granted" / "config").write_text('ExportCredsToAWS = true\nExportCredentialSuffix = "exported"\n')
        with mock.patch.object(Path, "home", return_value=home):
            self.assertEqual(doctor.check_granted_config()[0].status, doctor.OK)

    def test_shell_integration_off_fails_and_on_is_ok(self):
        rc = self.root / "zshrc"
        with mock.patch.object(init, "detect_shell", lambda: "zsh"), mock.patch.object(init, "rc_path", lambda shell: rc), \
                mock.patch.object(init.paths, "shell_dir", lambda: self.root / "shell"):
            self.assertEqual(doctor.check_shell()[0].status, doctor.FAIL)
            rc.write_text(init.BEGIN + "\n" + init.END + "\n")
            self.assertEqual(doctor.check_shell()[0].status, doctor.WARN)  # bloco sem script
            init.install_script()
            self.assertEqual(doctor.check_shell()[0].status, doctor.OK)

    def test_an_unsupported_shell_is_a_warning(self):
        with mock.patch.object(init, "detect_shell", lambda: "fish"):
            result = doctor.check_shell()[0]
        self.assertEqual(result.status, doctor.WARN)
        self.assertIn("fish", result.text)

    def test_an_empty_shell_variable_gets_its_own_message_and_the_way_out(self):
        with mock.patch.object(init, "detect_shell", lambda: ""):
            result = doctor.check_shell()[0]
        self.assertIn("SHELL", result.text)
        self.assertIn("--shell", result.hint)


class ConfluenceChecksTest(DoctorTestCase):
    def test_token_in_the_vault_is_ok_and_names_the_vault(self):
        results = doctor.check_confluence(online=False)
        self.assertEqual(set(self.statuses(results)), {doctor.OK})
        self.assertTrue(any("Keychain do macOS" in r.text for r in results))

    def test_token_in_the_env_file_with_a_vault_available_is_a_fixable_warning(self):
        os.environ["CONFLUENCE_TOKEN"] = "no-env"
        self.env_path.write_text("CONFLUENCE_TOKEN=no-env\n")
        warning = [r for r in doctor.check_confluence(False) if r.status == doctor.WARN][0]
        self.assertIn("arquivo .env", warning.text)
        self.assertIn("movido", warning.fix())
        self.assertEqual(vault.get_token(), "no-env")
        self.assertNotIn("CONFLUENCE_TOKEN", self.env_path.read_text())

    def test_token_in_the_env_file_without_a_vault_is_fine(self):
        os.environ["CONFLUENCE_TOKEN"] = "no-env"
        with mock.patch.object(vault, "_keyring", lambda: None):
            results = doctor.check_confluence(False)
        self.assertEqual(set(self.statuses(results)), {doctor.OK})

    def test_no_token_fails_and_points_to_role_token(self):
        vault.delete_token()
        result = [r for r in doctor.check_confluence(False) if r.status == doctor.FAIL][0]
        self.assertEqual(result.hint, "role token")

    def test_missing_url_or_email_is_a_warning_because_onboarding_resolves_it(self):
        del os.environ["CONFLUENCE_EMAIL"]
        results = doctor.check_confluence(False)
        self.assertEqual(self.statuses(results), [doctor.WARN])

    def test_no_squads_is_a_warning(self):
        del os.environ["CONFLUENCE_SQUAD_IDS"]
        self.assertIn(doctor.WARN, self.statuses(doctor.check_confluence(False)))

    def test_online_only_runs_when_asked(self):
        with mock.patch.object(confluence, "check_credentials") as check:
            doctor.check_confluence(online=False)
            check.assert_not_called()
            results = doctor.check_confluence(online=True)
            check.assert_called_once()
        self.assertEqual(results[-1].status, doctor.OK)

    def test_online_distinguishes_rejected_from_unreachable(self):
        with mock.patch.object(confluence, "check_credentials", side_effect=ConfluenceAuthError("x")):
            self.assertEqual(doctor.check_confluence(True)[-1].status, doctor.FAIL)
        with mock.patch.object(confluence, "check_credentials", side_effect=ConfluenceError("sem rede")):
            self.assertEqual(doctor.check_confluence(True)[-1].status, doctor.WARN)


class RunTest(DoctorTestCase):
    def run_doctor(self, **kwargs):
        console = FakeConsole()
        patches = [mock.patch.object(doctor, name, lambda *a, **k: []) for name in ("check_shell", "check_granted_config")]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return doctor.run(console, **kwargs), console

    def test_exit_code_is_zero_without_failures_and_one_with(self):
        code, console = self.run_doctor()
        self.assertEqual(code, 0)
        self.assertTrue(any("verificações" in text for text in console.of("ok") + console.of("warn")))
        self.config.unlink()
        code, console = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertTrue(any("falhas" in text for text in console.of("fail")))

    def test_groups_are_shown_as_titled_sections(self):
        _, console = self.run_doctor()
        self.assertEqual(console.of("step")[:3], ["Instalação", "Configuração", "Confluence"])

    def test_it_suggests_fix_when_something_is_fixable_and_stays_quiet_otherwise(self):
        self.env_path.chmod(0o644)
        _, console = self.run_doctor()
        self.assertTrue(any("role doctor --fix" in text for text in console.of("note")))
        self.env_path.chmod(0o600)
        _, console = self.run_doctor()
        self.assertFalse(any("--fix" in text for text in console.of("note")))

    def test_fix_applies_the_automatic_repairs_and_the_summary_reflects_it(self):
        self.env_path.chmod(0o644)
        code, console = self.run_doctor(fix=True)
        self.assertEqual(self.env_path.stat().st_mode & 0o777, 0o600)
        self.assertIn("Consertos", console.of("step"))
        self.assertTrue(any("permissão do .env ajustada" in text for text in console.of("ok")))
        self.assertFalse(any("--fix" in text for text in console.of("note")))

    def test_a_failing_repair_is_reported_not_raised(self):
        result = doctor.Result(doctor.WARN, "algo", fix=lambda: 1 / 0)
        console = FakeConsole()
        self.assertEqual(doctor.apply_fixes(console, [("g", [result])]), 0)
        self.assertTrue(console.of("fail"))

    def test_the_command_is_wired_with_short_and_long_options(self):
        parser = profiles.build_parser()
        for flags in (["doctor", "-f", "-o"], ["doctor", "--fix", "--online"]):
            args = parser.parse_args(flags)
            self.assertTrue(args.fix and args.online)
        args = parser.parse_args(["doctor"])
        self.assertFalse(args.fix or args.online)


class TokenCommandTest(DoctorTestCase):
    def test_it_asks_and_stores_a_new_token(self):
        console = mock.Mock()
        console.interactive.return_value = True
        with mock.patch.object(onboarding, "ask_token") as ask:
            self.assertEqual(profiles.cmd_token(argparse.Namespace(), console), 0)
        ask.assert_called_once_with(console, confluence.ENV_PATH)

    def test_it_refuses_without_a_terminal(self):
        console = mock.Mock()
        console.interactive.return_value = False
        with mock.patch.object(profiles, "warn") as warn:
            self.assertEqual(profiles.cmd_token(argparse.Namespace(), console), 1)
        self.assertIn("terminal", warn.call_args.args[0])

    def test_it_refuses_before_the_confluence_is_configured(self):
        del os.environ["CONFLUENCE_EMAIL"]
        console = mock.Mock()
        console.interactive.return_value = True
        with mock.patch.object(profiles, "warn") as warn, mock.patch.object(onboarding, "ask_token") as ask:
            self.assertEqual(profiles.cmd_token(argparse.Namespace(), console), 1)
        ask.assert_not_called()
        self.assertIn("role sso", warn.call_args.args[0])

    def test_a_token_rejected_three_times_is_reported(self):
        console = mock.Mock()
        console.interactive.return_value = True
        with mock.patch.object(profiles, "warn") as warn, \
                mock.patch.object(onboarding, "ask_token", side_effect=ConfluenceAuthError("token recusado 3 vezes")):
            self.assertEqual(profiles.cmd_token(argparse.Namespace(), console), 1)
        self.assertIn("recusado", warn.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
