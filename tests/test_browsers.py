import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from roleroster import browsers


class FindTest(unittest.TestCase):
    def test_macos_finds_the_app_in_applications_or_the_user_applications(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(browsers.sys, "platform", "darwin"), \
                mock.patch.object(Path, "home", return_value=Path(tmp)):
            executable = Path(tmp) / "Applications" / browsers.MAC_EXECUTABLES["brave"]
            executable.parent.mkdir(parents=True)
            executable.write_text("")
            self.assertEqual(browsers.find("brave"), str(executable))
            self.assertIsNone(browsers.find("firefox") if not Path("/Applications/Firefox.app").exists() else None)

    def test_linux_uses_the_first_command_found_on_the_path(self):
        found = {"google-chrome-stable": "/usr/bin/google-chrome-stable"}
        with mock.patch.object(browsers.sys, "platform", "linux"), \
                mock.patch.object(browsers.shutil, "which", side_effect=lambda c: found.get(c)):
            self.assertEqual(browsers.find("chrome"), "/usr/bin/google-chrome-stable")
            self.assertIsNone(browsers.find("brave"))

    def test_windows_is_not_supported_yet(self):
        with mock.patch.object(browsers.sys, "platform", "win32"):
            self.assertIsNone(browsers.find("chrome"))

    def test_installed_keeps_the_declared_order_and_skips_missing_ones(self):
        paths = {"edge": "/e", "chrome": "/c"}
        with mock.patch.object(browsers, "find", side_effect=paths.get):
            self.assertEqual([k for k, _, _ in browsers.installed()], ["chrome", "edge"])

    def test_only_the_four_agreed_browsers(self):
        self.assertEqual([k for k, _ in browsers.BROWSERS], ["chrome", "brave", "firefox", "edge"])


class GuestTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name) / "browser"

    def test_each_window_gets_its_own_private_directory(self):
        first = browsers.guest_dir(self.base)
        second = browsers.guest_dir(self.base)
        self.assertNotEqual(first, second)
        self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o700)

    def test_the_directory_name_never_repeats_even_in_the_same_second_and_process(self):
        names = {browsers.guest_dir(self.base, now=1000).name for _ in range(5)}
        self.assertEqual(len(names), 5)

    def test_macos_opens_a_new_instance_with_open_n(self):
        app = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        with mock.patch.object(browsers.sys, "platform", "darwin"):
            command = browsers.launch_command(app, "/d", "https://x")
        self.assertEqual(command[:5], ["open", "-n", "-a", "/Applications/Google Chrome.app", "--args"])
        self.assertIn("--user-data-dir=/d", command)
        self.assertEqual(command[-1], "https://x")

    def test_other_systems_run_the_binary_directly_with_the_same_flags(self):
        with mock.patch.object(browsers.sys, "platform", "linux"):
            command = browsers.launch_command("/usr/bin/brave", "/d", "https://x")
        self.assertEqual(command[0], "/usr/bin/brave")
        for flag in ("--user-data-dir=/d", "--no-first-run", "--no-default-browser-check"):
            self.assertIn(flag, command)

    def test_cleanup_removes_only_old_visitor_directories(self):
        old, recent, other = (self.base / n for n in ("visitante-1-1-0", "visitante-2-1-0", "outra"))
        for directory in (old, recent, other):
            directory.mkdir(parents=True)
        long_ago = time.time() - browsers.GUEST_MAX_AGE - 100
        os.utime(old, (long_ago, long_ago))
        os.utime(other, (long_ago, long_ago))
        self.assertEqual(browsers.cleanup_old(self.base), 1)
        self.assertEqual((old.exists(), recent.exists(), other.exists()), (False, True, True))

    def test_cleanup_without_the_folder_does_nothing(self):
        self.assertEqual(browsers.cleanup_old(self.base), 0)

    def test_open_guest_launches_detached_and_returns_the_directory(self):
        with mock.patch.object(browsers.subprocess, "Popen") as popen, mock.patch.object(browsers.sys, "platform", "linux"):
            data_dir = browsers.open_guest("https://console.aws.amazon.com/x", "/usr/bin/chrome", self.base)
        command = popen.call_args.args[0]
        self.assertIn(f"--user-data-dir={data_dir}", command)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertTrue(data_dir.is_dir())

    def test_open_guest_refuses_anything_but_https(self):
        for url in ("http://x", "file:///etc/passwd", "javascript:alert(1)", ""):
            with self.assertRaises(ValueError):
                browsers.open_guest(url, "/usr/bin/chrome", self.base)

    def test_open_guest_cleans_old_windows_first(self):
        old = self.base / "visitante-1-1-0"
        old.mkdir(parents=True)
        long_ago = time.time() - browsers.GUEST_MAX_AGE - 100
        os.utime(old, (long_ago, long_ago))
        with mock.patch.object(browsers.subprocess, "Popen"):
            browsers.open_guest("https://x", "/usr/bin/chrome", self.base)
        self.assertFalse(old.exists())


class ModesTest(unittest.TestCase):
    def test_modes_and_labels(self):
        self.assertEqual([m[0] for m in browsers.MODES], ["profile", "guest", "account"])
        self.assertEqual(browsers.mode_label("guest"), "Visitante")
        self.assertEqual(browsers.DEFAULT_MODE, "profile")


if __name__ == "__main__":
    unittest.main()
