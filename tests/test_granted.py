import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roleroster import granted

FULL = '''DefaultBrowser = "CHROME"
CustomBrowserPath = "/x"
Ordering = ""
ExportCredsToAWS = true

[Keyring]
  Backend = "keychain"
'''


class TextEditTest(unittest.TestCase):
    def test_reads_only_the_top_level(self):
        text = 'A = 1\n\n[Keyring]\n  A = 2\n'
        self.assertEqual(granted.current_value(text, "A"), "1")
        self.assertIsNone(granted.current_value(text, "Backend"))

    def test_adds_a_missing_key_before_the_first_section(self):
        new = granted.with_setting(FULL, "ExportCredentialSuffix", '"exported"')
        self.assertLess(new.index("ExportCredentialSuffix"), new.index("[Keyring]"))
        self.assertIn('  Backend = "keychain"', new)

    def test_keeps_the_blank_line_before_the_section(self):
        new = granted.with_setting(FULL, "ExportCredentialSuffix", '"exported"')
        self.assertIn('"exported"\n\n[Keyring]', new)

    def test_replaces_an_existing_value_in_place(self):
        new = granted.with_setting(FULL, "ExportCredsToAWS", "false")
        self.assertEqual(granted.current_value(new, "ExportCredsToAWS"), "false")
        self.assertEqual(new.count("ExportCredsToAWS"), 1)

    def test_a_key_with_the_same_name_inside_a_section_is_not_touched(self):
        text = "[S]\nExportCredsToAWS = false\n"
        new = granted.with_setting(text, "ExportCredsToAWS", "true")
        self.assertTrue(new.startswith("ExportCredsToAWS = true\n[S]"))
        self.assertIn("[S]\nExportCredsToAWS = false", new)

    def test_text_without_a_section_or_trailing_newline(self):
        self.assertEqual(granted.with_setting("A = 1", "B", "2"), "A = 1\nB = 2\n")


class SetBrowsersTest(unittest.TestCase):
    """Usa o Granted de verdade, mas no HOME de teste (tests/conftest.py)."""

    @unittest.skipUnless(__import__("shutil").which("granted"), "Granted nao instalado")
    def test_writes_the_three_keys_in_the_format_granted_itself_produces(self):
        self.assertTrue(granted.set_browsers("chrome", "/Applications/X.app/Contents/MacOS/X", "/Applications/Y.app/Contents/MacOS/Y"))
        text = granted.config_path().read_text()
        self.assertEqual(granted.current_value(text, "DefaultBrowser"), '"CHROME"')
        self.assertEqual(granted.current_value(text, "CustomBrowserPath"), '"/Applications/X.app/Contents/MacOS/X"')
        self.assertEqual(granted.current_value(text, "CustomSSOBrowserPath"), '"/Applications/Y.app/Contents/MacOS/Y"')
        self.assertEqual(granted.current_value(text, "ExportCredsToAWS"), "true")  # o resto continua
        self.assertEqual(text.count("DefaultBrowser"), 1)

    @unittest.skipUnless(__import__("shutil").which("granted"), "Granted nao instalado")
    def test_the_second_call_replaces_the_values(self):
        granted.set_browsers("chrome", "/a", "/a")
        granted.set_browsers("edge", "/b", "/c")
        text = granted.config_path().read_text()
        self.assertEqual(granted.current_value(text, "DefaultBrowser"), '"EDGE"')
        self.assertEqual(granted.current_value(text, "CustomSSOBrowserPath"), '"/c"')

    def test_without_granted_it_does_nothing(self):
        with mock.patch.object(granted.shutil, "which", return_value=None):
            self.assertFalse(granted.set_browsers("chrome", "/a", "/a"))

    def test_windows_style_backslashes_are_kept_literally(self):
        text = granted.with_setting("A = 1\n", "CustomBrowserPath", granted.quote(r"C:\Program Files\Chrome\chrome.exe"))
        self.assertIn(r'"C:\\Program Files\\Chrome\\chrome.exe"', text)  # escapado como string TOML


class EnsureSettingsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        patcher = mock.patch.object(Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        which = mock.patch.object(granted.shutil, "which", return_value="/bin/granted")
        which.start()
        self.addCleanup(which.stop)
        self.path = self.home / ".granted" / "config"

    def write(self, text):
        self.path.parent.mkdir()
        self.path.write_text(text)

    def test_browser_missing_when_the_config_has_no_default_browser(self):
        self.write('DefaultBrowser = ""\nExportCredsToAWS = true\n')
        self.assertTrue(granted.browser_missing())

    def test_browser_missing_with_no_config_file_or_no_key(self):
        self.assertTrue(granted.browser_missing())  # sem arquivo
        self.write("ExportCredsToAWS = true\n")
        self.assertTrue(granted.browser_missing())

    def test_browser_is_not_missing_when_one_is_set_and_never_without_granted(self):
        self.write(FULL)
        self.assertFalse(granted.browser_missing())
        with mock.patch.object(granted.shutil, "which", return_value=None):
            (self.home / ".granted" / "config").write_text("")
            self.assertFalse(granted.browser_missing())  # sem o Granted nao ha o que gravar

    def test_nothing_to_do_when_both_keys_are_right(self):
        self.write(FULL.replace("[Keyring]", 'ExportCredentialSuffix = "exported"\n\n[Keyring]'))
        before = self.path.read_text()
        self.assertEqual(granted.ensure_settings(), [])
        self.assertEqual(self.path.read_text(), before)

    def test_adds_the_suffix_and_keeps_the_user_settings(self):
        self.write(FULL)
        self.assertEqual(granted.ensure_settings(), ["ExportCredentialSuffix"])
        text = self.path.read_text()
        self.assertIn('DefaultBrowser = "CHROME"', text)
        self.assertEqual(granted.current_value(text, "ExportCredentialSuffix"), '"exported"')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_fixes_export_creds_when_it_is_off(self):
        self.write(FULL.replace("ExportCredsToAWS = true", "ExportCredsToAWS = false"))
        self.assertIn("ExportCredsToAWS", granted.ensure_settings())

    def test_a_missing_config_is_created_by_granted_and_then_completed(self):
        def fake_granted(*args, **kwargs):
            self.write(FULL)
        with mock.patch.object(granted.subprocess, "run", side_effect=fake_granted) as run:
            changed = granted.ensure_settings()
        self.assertEqual(run.call_args.args[0][:4], ["/bin/granted", "settings", "set", "-s"])
        self.assertEqual(changed, ["ExportCredentialSuffix"])

    def test_without_granted_installed_does_nothing(self):
        with mock.patch.object(granted.shutil, "which", return_value=None):
            self.assertEqual(granted.ensure_settings(), [])
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
