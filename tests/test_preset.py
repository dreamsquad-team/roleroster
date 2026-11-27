import argparse
import io
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from roleroster import init, paths, preset, profiles, settings

FULL = """
name = "Acme"

[aws]
sso_start_url = "https://acme.awsapps.com/start"
sso_session = "acme-login"
sso_region = "us-east-2"
region = "sa-east-1"
base_profile = "ACME"
base_account_id = "123456789012"
base_role = "AssumeRole"
extra_role = "ReadOnly"

[confluence]
url = "https://acme.atlassian.net"
root_id = "4242"
role_section = "Acessos AWS"
inactive_prefix = "Archived"
"""


class ParseTest(unittest.TestCase):
    def test_every_documented_key_is_read(self):
        parsed = preset.parse(FULL)
        self.assertEqual(parsed["name"], "Acme")
        self.assertEqual(parsed["values"], {
            "sso_start_url": "https://acme.awsapps.com/start", "sso_session": "acme-login", "sso_region": "us-east-2",
            "region": "sa-east-1", "base_profile": "ACME", "base_account_id": "123456789012", "base_role": "AssumeRole",
            "extra_role": "ReadOnly", "confluence_url": "https://acme.atlassian.net", "root_id": "4242",
            "role_section": "Acessos AWS", "inactive_prefix": "Archived",
        })
        self.assertEqual(parsed["ignored"], [])

    def test_every_key_is_optional_and_blank_values_are_dropped(self):
        self.assertEqual(preset.parse("")["values"], {})
        self.assertEqual(preset.parse('[aws]\nbase_role = "  "\n')["values"], {})

    def test_unknown_keys_and_sections_are_reported_not_fatal(self):
        parsed = preset.parse('[aws]\nfuturo = "x"\n[outra]\na = "b"\n')
        self.assertEqual(sorted(parsed["ignored"]), ["aws.futuro", "outra"])

    def test_invalid_values_are_rejected_with_a_clear_message(self):
        for text, expected in (
            ('[aws]\nsso_start_url = "http://x"\n', "URL https"),
            ('[confluence]\nurl = "acme.atlassian.net"\n', "URL https"),
            ('[aws]\nbase_account_id = "123"\n', "12 dígitos"),
            ('[confluence]\nroot_id = "abc"\n', "numérico"),
            ('[aws]\nbase_role = 5\n', "texto"),
            ("isto nao e toml", "TOML"),
        ):
            with self.assertRaises(preset.PresetError) as raised:
                preset.parse(text)
            self.assertIn(expected, str(raised.exception), text)

    def test_describe_lists_what_matters_to_a_person_checking_a_preset(self):
        labels = [label for label, _ in preset.describe(preset.parse(FULL))]
        self.assertEqual(labels, ["Portal SSO", "Confluence", "Perfil base", "Pasta raiz", "Inativos começam com"])


class SourceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_a_local_file(self):
        path = self.tmp / "p.toml"
        path.write_text(FULL)
        self.assertEqual(preset.read_source(str(path)), FULL)

    def test_a_missing_file(self):
        with self.assertRaises(preset.PresetError):
            preset.read_source(str(self.tmp / "nao-existe.toml"))

    def test_only_https_urls_are_downloaded(self):
        for source in ("http://x/p.toml", "ftp://x/p.toml", "file:///etc/passwd"):
            with self.assertRaises(preset.PresetError) as raised:
                preset.read_source(source)
            self.assertIn("https", str(raised.exception))

    def test_an_https_url_is_downloaded_with_a_timeout(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = FULL.encode()
        with mock.patch.object(preset.urllib.request, "urlopen", return_value=response) as opened:
            self.assertEqual(preset.read_source("https://exemplo.com/p.toml"), FULL)
        self.assertEqual(opened.call_args.kwargs["timeout"], preset.FETCH_TIMEOUT)

    def test_a_network_failure_is_a_preset_error(self):
        with mock.patch.object(preset.urllib.request, "urlopen", side_effect=urllib.error.URLError("sem rede")):
            with self.assertRaises(preset.PresetError):
                preset.read_source("https://exemplo.com/p.toml")

    def test_a_huge_file_is_refused(self):
        path = self.tmp / "grande.toml"
        path.write_text("#" * (preset.MAX_BYTES + 10))
        with self.assertRaises(preset.PresetError) as raised:
            preset.read_source(str(path))
        self.assertIn("grande", str(raised.exception))

    def test_a_file_that_is_not_utf8_is_refused(self):
        path = self.tmp / "ruim.toml"
        path.write_bytes(b"\xff\xfe\x00")
        with self.assertRaises(preset.PresetError):
            preset.read_source(str(path))


class InstallTest(unittest.TestCase):
    """Usa a pasta temporaria do conftest (ROLEROSTER_HOME)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.source = Path(tmp.name) / "acme.toml"
        self.source.write_text(FULL)
        self.addCleanup(lambda: preset.remove())

    def test_install_copies_the_file_and_it_is_loaded_afterwards(self):
        installed = preset.install(str(self.source))
        self.assertEqual(installed["name"], "Acme")
        self.assertEqual(preset.installed_path(), paths.home() / "preset.toml")
        self.assertEqual(preset.load_installed()["base_profile"], "ACME")

    def test_an_invalid_preset_is_never_installed(self):
        self.source.write_text('[aws]\nsso_start_url = "http://x"\n')
        with self.assertRaises(preset.PresetError):
            preset.install(str(self.source))
        self.assertFalse(preset.installed_path().exists())

    def test_installing_again_replaces_the_previous_one(self):
        preset.install(str(self.source))
        self.source.write_text('[aws]\nbase_profile = "OUTRO"\n')
        preset.install(str(self.source))
        self.assertEqual(preset.load_installed(), {"base_profile": "OUTRO"})

    def test_remove_says_whether_there_was_one(self):
        self.assertFalse(preset.remove())
        preset.install(str(self.source))
        self.assertTrue(preset.remove())
        self.assertEqual(preset.load_installed(), {})

    def test_a_broken_installed_file_never_breaks_a_command(self):
        preset.installed_path().parent.mkdir(parents=True, exist_ok=True)
        preset.installed_path().write_text("isto nao e toml")
        self.assertEqual(preset.load_installed(), {})


class SettingsTest(unittest.TestCase):
    def setUp(self):
        patch = mock.patch.dict(os.environ, {}, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def with_preset(self, values):
        patch = mock.patch.object(preset, "load_installed", return_value=values)
        patch.start()
        self.addCleanup(patch.stop)

    def test_nothing_defined_means_the_generic_default_or_empty(self):
        self.with_preset({})
        self.assertEqual(settings.get("base_profile"), "roleroster-base")
        self.assertEqual(settings.get("sso_region"), "us-east-1")
        self.assertEqual(settings.get("role_section"), "Switch Role")
        for key in ("sso_start_url", "base_account_id", "base_role", "root_id", "inactive_prefix", "confluence_url"):
            self.assertEqual(settings.get(key), "", key)  # nao ha como adivinhar

    def test_the_preset_beats_the_generic_default(self):
        self.with_preset({"base_profile": "ACME", "inactive_prefix": "Old"})
        self.assertEqual(settings.get("base_profile"), "ACME")
        self.assertEqual(settings.get("inactive_prefix"), "Old")

    def test_what_the_user_set_beats_the_preset(self):
        self.with_preset({"base_profile": "ACME"})
        os.environ["ROLEROSTER_SOURCE_PROFILE"] = "MINE"
        self.assertEqual(settings.get("base_profile"), "MINE")

    def test_from_user_or_preset_never_returns_the_generic_default(self):
        self.with_preset({})
        self.assertEqual(settings.from_user_or_preset("base_profile"), "")
        self.with_preset({"base_profile": "ACME"})
        self.assertEqual(settings.from_user_or_preset("base_profile"), "ACME")

    def test_existing_env_names_are_kept_so_old_installs_keep_working(self):
        self.assertEqual(settings.ENV_VARS["base_profile"], "ROLEROSTER_SOURCE_PROFILE")
        self.assertEqual(settings.ENV_VARS["root_id"], "CONFLUENCE_ROOT_ID")
        self.assertEqual(settings.ENV_VARS["confluence_url"], "CONFLUENCE_BASE_URL")


class FakeConsole:
    def __init__(self, interactive=True, confirm=True):
        self._interactive, self._confirm = interactive, confirm
        self.summaries, self.oks, self.notes, self.asked = [], [], [], []

    def interactive(self):
        return self._interactive

    def summary(self, title, rows):
        self.summaries.append((title, dict(rows)))

    def ok(self, text):
        self.oks.append(text)

    def note(self, text):
        self.notes.append(text)

    def confirm(self, prompt, default=True):
        self.asked.append(prompt)
        return self._confirm


class PresetCommandTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.source = Path(tmp.name) / "acme.toml"
        self.source.write_text(FULL)
        self.warnings = []
        patch = mock.patch.object(profiles, "warn", self.warnings.append)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(lambda: preset.remove())

    def run_command(self, console=None, **kwargs):
        args = argparse.Namespace(**{"source": str(self.source), "remove": False, "yes": False, **kwargs})
        return profiles.cmd_preset(args, console or FakeConsole())

    def test_it_shows_what_the_preset_defines_asks_and_installs(self):
        console = FakeConsole()
        self.assertEqual(self.run_command(console), 0)
        title, rows = console.summaries[0]
        self.assertEqual(title, "Preset Acme")
        self.assertEqual(rows["Portal SSO"], "https://acme.awsapps.com/start")
        self.assertEqual(console.asked, ["Instalar este preset?"])
        self.assertEqual(preset.load_installed()["base_profile"], "ACME")

    def test_declining_installs_nothing(self):
        self.assertEqual(self.run_command(FakeConsole(confirm=False)), 1)
        self.assertEqual(preset.load_installed(), {})

    def test_without_a_terminal_it_needs_yes(self):
        self.assertEqual(self.run_command(FakeConsole(interactive=False)), 1)
        self.assertIn("--yes", self.warnings[-1])
        self.assertEqual(preset.load_installed(), {})
        self.assertEqual(self.run_command(FakeConsole(interactive=False), yes=True), 0)
        self.assertTrue(preset.load_installed())

    def test_yes_skips_the_question_but_still_shows_the_contents(self):
        console = FakeConsole()
        self.run_command(console, yes=True)
        self.assertEqual((console.asked, len(console.summaries)), ([], 1))

    def test_an_invalid_preset_is_reported_and_not_installed(self):
        self.source.write_text('[aws]\nsso_start_url = "http://x"\n')
        self.assertEqual(self.run_command(yes=True), 1)
        self.assertIn("https", self.warnings[-1])
        self.assertEqual(preset.load_installed(), {})

    def test_unknown_keys_are_mentioned(self):
        self.source.write_text('[aws]\nfuturo = "x"\n')
        console = FakeConsole()
        self.run_command(console, yes=True)
        self.assertTrue(any("aws.futuro" in note for note in console.notes))

    def test_without_arguments_it_shows_the_installed_preset_or_says_there_is_none(self):
        self.run_command(source=None)
        self.assertIn("Nenhum preset", self.warnings[-1])
        preset.install(str(self.source))
        console = FakeConsole()
        self.run_command(console, source=None)
        self.assertEqual(console.summaries[0][0], "Preset instalado")

    def test_remove(self):
        preset.install(str(self.source))
        self.run_command(source=None, remove=True)
        self.assertIn("removido", self.warnings[-1])
        self.assertEqual(preset.load_installed(), {})
        self.run_command(source=None, remove=True)
        self.assertIn("Não havia", self.warnings[-1])

    def test_the_parser_has_short_and_long_options(self):
        parser = profiles.build_parser()
        for flags in (["preset", "x.toml", "-r", "-y"], ["preset", "x.toml", "--remove", "--yes"]):
            args = parser.parse_args(flags)
            self.assertTrue(args.remove and args.yes)


class InitPresetTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.source = self.root / "acme.toml"
        self.source.write_text(FULL)
        self.rc = self.root / "zshrc"
        self.addCleanup(lambda: preset.remove())

    def run_init(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err), mock.patch.dict(os.environ, {"SHELL": "/bin/zsh"}):
            code = init.main(["--rc", str(self.rc), *args])
        return code, out.getvalue(), err.getvalue()

    def test_init_with_a_preset_installs_it_and_links_the_shell(self):
        code, _, _ = self.run_init("--preset", str(self.source), "--yes")
        self.assertEqual(code, 0)
        self.assertTrue(preset.load_installed())
        self.assertIn(init.BEGIN, self.rc.read_text())

    def test_an_invalid_preset_stops_init_before_touching_the_rc(self):
        self.source.write_text("isto nao e toml")
        code, _, err = self.run_init("-p", str(self.source), "-y")
        self.assertEqual(code, 1)
        self.assertIn("TOML", err)
        self.assertFalse(self.rc.exists())

    def test_without_a_terminal_and_without_yes_nothing_is_changed(self):
        code, _, err = self.run_init("--preset", str(self.source))
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        self.assertFalse(self.rc.exists())
        self.assertEqual(preset.load_installed(), {})


class NoCompanyDataInThePackageTest(unittest.TestCase):
    """A lib e publica: nenhum dado de empresa pode voltar para o pacote (so para o preset da empresa)."""

    FORBIDDEN = ("dreamsquad", "dreamsquad.awsapps", "dreamsquad.atlassian", "confluence-aws-sync")

    def test_no_source_file_mentions_a_specific_company(self):
        root = Path(__file__).resolve().parent.parent / "src" / "roleroster"
        offenders = []
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".sh", ".toml", ".md"):
                text = path.read_text().lower()
                offenders += [f"{path.name}: {word}" for word in self.FORBIDDEN if word in text]
        self.assertEqual(offenders, [])

    def test_the_readme_and_examples_do_not_either(self):
        root = Path(__file__).resolve().parent.parent
        files = [root / "README.md", *(root / "examples").glob("*")] if (root / "examples").exists() else [root / "README.md"]
        offenders = [f"{p.name}: {w}" for p in files if p.is_file() for w in self.FORBIDDEN if w in p.read_text().lower()]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
