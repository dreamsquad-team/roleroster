import hashlib
import io
import os
import stat
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from roleroster import granted, granted_install


class FakeConsole:
    def __init__(self, interactive=True, confirm=True):
        self._interactive, self._confirm = interactive, confirm
        self.asked, self.oks, self.fails, self.notes, self.warns = [], [], [], [], []

    def interactive(self):
        return self._interactive

    def confirm(self, prompt, default=True):
        self.asked.append(prompt)
        return self._confirm

    def ok(self, text):
        self.oks.append(text)

    def fail(self, text):
        self.fails.append(text)

    def note(self, text):
        self.notes.append(text)

    def warn(self, text):
        self.warns.append(text)


def make_tarball(members):
    """members: {nome: bytes ou ('link', alvo)}."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in members.items():
            if isinstance(content, tuple):
                info = tarfile.TarInfo(name)
                info.type, info.linkname = tarfile.SYMTYPE, content[1]
                tar.addfile(info)
            else:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


GOOD = {"granted": b"BINARIO", "assume": b"#!/bin/sh\n", "assume.fish": b"fish", "assume.bat": b"bat"}


class PlatformTest(unittest.TestCase):
    def machine(self, system, arch):
        return mock.patch.multiple(granted_install.platform, system=lambda: system, machine=lambda: arch)

    def test_architecture_names_are_normalized(self):
        for raw, expected in (("aarch64", "arm64"), ("arm64", "arm64"), ("AMD64", "x86_64"), ("x86_64", "x86_64")):
            with self.machine("Linux", raw):
                self.assertEqual(granted_install.machine(), ("linux", expected))

    def test_macos_with_homebrew_uses_brew(self):
        with self.machine("Darwin", "arm64"), mock.patch.object(granted_install.sys, "platform", "darwin"), \
                mock.patch.object(granted_install.shutil, "which", return_value="/opt/homebrew/bin/brew"):
            self.assertEqual(granted_install.method(), "brew")

    def test_macos_without_homebrew_and_linux_download_the_binary(self):
        with self.machine("Darwin", "arm64"), mock.patch.object(granted_install.sys, "platform", "darwin"), \
                mock.patch.object(granted_install.shutil, "which", return_value=None):
            self.assertEqual(granted_install.method(), "binary")
        with self.machine("Linux", "x86_64"), mock.patch.object(granted_install.sys, "platform", "linux"):
            self.assertEqual(granted_install.method(), "binary")

    def test_an_unsupported_system_has_no_automatic_install(self):
        with self.machine("Windows", "AMD64"), mock.patch.object(granted_install.sys, "platform", "win32"):
            self.assertEqual(granted_install.method(), "")
            self.assertIn(granted_install.DOCS_URL, granted_install.manual_hint())

    def test_every_platform_has_a_well_formed_pinned_hash_and_a_matching_file_name(self):
        for (system, arch), digest in granted_install.SHA256.items():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            with self.machine(system.title(), arch):
                self.assertEqual(granted_install.tarball_name(), f"granted_{granted_install.VERSION}_{system}_{arch}.tar.gz")


class InstallBinaryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.target = Path(tmp.name) / "bin"
        machine = mock.patch.object(granted_install, "machine", return_value=("linux", "x86_64"))
        machine.start()
        self.addCleanup(machine.stop)

    def serve(self, data, digest=None):
        digest = digest or hashlib.sha256(data).hexdigest()
        for patcher in (
            mock.patch.dict(granted_install.SHA256, {("linux", "x86_64"): digest}),
            mock.patch.object(granted_install, "fetch", return_value=data),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_installs_the_three_files_and_the_assumego_link(self):
        self.serve(make_tarball(GOOD))
        granted_install.install_binary(self.target)
        for name in ("granted", "assume", "assume.fish"):
            self.assertTrue(os.access(self.target / name, os.X_OK), name)
        self.assertEqual((self.target / "granted").read_bytes(), b"BINARIO")
        self.assertEqual(os.readlink(self.target / "assumego"), "granted")
        self.assertFalse((self.target / "assume.bat").exists())
        self.assertEqual(list(self.target.glob("*.novo")), [])

    def test_a_second_run_replaces_the_old_files(self):
        self.serve(make_tarball(GOOD))
        granted_install.install_binary(self.target)
        granted_install.install_binary(self.target)
        self.assertEqual((self.target / "granted").read_bytes(), b"BINARIO")

    def test_a_wrong_hash_installs_nothing(self):
        self.serve(make_tarball(GOOD), digest="0" * 64)
        with self.assertRaises(granted_install.InstallError) as caught:
            granted_install.install_binary(self.target)
        self.assertIn("SHA-256", str(caught.exception))
        self.assertFalse(self.target.exists())

    def test_a_package_without_an_expected_file_is_refused(self):
        self.serve(make_tarball({"assume": b"x", "assume.fish": b"x"}))
        with self.assertRaises(granted_install.InstallError):
            granted_install.install_binary(self.target)
        self.assertFalse((self.target / "assume").exists())

    def test_a_link_where_a_file_should_be_is_refused(self):
        self.serve(make_tarball({**GOOD, "granted": ("link", "/etc/passwd")}))
        with self.assertRaises(granted_install.InstallError):
            granted_install.install_binary(self.target)

    def test_garbage_instead_of_a_tarball_is_refused(self):
        self.serve(b"nao e um tar")
        with self.assertRaises(granted_install.InstallError):
            granted_install.install_binary(self.target)

    def test_the_binary_keeps_execute_permission_only_for_the_owner_to_write(self):
        self.serve(make_tarball(GOOD))
        granted_install.install_binary(self.target)
        self.assertEqual(stat.S_IMODE((self.target / "granted").stat().st_mode), 0o755)


class FetchTest(unittest.TestCase):
    def test_only_github_over_https_is_downloaded(self):
        for url in ("http://github.com/x", "https://example.com/x", "file:///etc/passwd"):
            with self.assertRaises(granted_install.InstallError):
                granted_install.fetch(url)

    def test_a_network_error_becomes_an_install_error(self):
        with mock.patch.object(granted_install.urllib.request, "urlopen", side_effect=OSError("sem rede")):
            with self.assertRaises(granted_install.InstallError) as caught:
                granted_install.fetch("https://github.com/x")
        self.assertIn("sem rede", str(caught.exception))

    def test_a_download_bigger_than_the_limit_is_refused(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"x" * (granted_install.MAX_BYTES + 1)
        with mock.patch.object(granted_install.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(granted_install.InstallError):
                granted_install.fetch("https://github.com/x")


class InstallBrewTest(unittest.TestCase):
    def test_taps_then_installs(self):
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(granted_install.subprocess, "run", return_value=done) as run:
            granted_install.install_brew()
        self.assertEqual([c.args[0] for c in run.call_args_list], list(granted_install.BREW_COMMANDS))

    def test_a_failure_stops_and_shows_the_tail_of_the_output(self):
        done = mock.Mock(returncode=1, stdout="", stderr="linha1\nlinha2\nErro: sem permissão")
        with mock.patch.object(granted_install.subprocess, "run", return_value=done) as run:
            with self.assertRaises(granted_install.InstallError) as caught:
                granted_install.install_brew()
        self.assertEqual(run.call_count, 1)
        self.assertIn("sem permissão", str(caught.exception))


class OfferTest(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch.object(granted, "executable", return_value=None),
            mock.patch.object(granted_install, "method", return_value="binary"),
            mock.patch.object(granted_install, "describe", return_value="baixar o Granted"),
            mock.patch.object(granted_install, "on_path", return_value=True),
            mock.patch.object(granted_install, "install", return_value="Granted 0.40.0 instalado em ~/.local/bin"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_nothing_happens_when_granted_is_already_there(self):
        console = FakeConsole()
        with mock.patch.object(granted, "executable", return_value="/bin/granted"):
            self.assertTrue(granted_install.offer(console))
        self.assertEqual((console.asked, console.warns, console.oks), ([], [], []))
        granted_install.install.assert_not_called()

    def test_it_asks_and_installs_on_yes(self):
        console = FakeConsole(confirm=True)
        self.assertTrue(granted_install.offer(console))
        self.assertEqual(len(console.asked), 1)
        granted_install.install.assert_called_once_with("binary")
        self.assertIn("instalado", console.oks[0])

    def test_no_means_nothing_is_installed_and_the_manual_command_is_shown(self):
        console = FakeConsole(confirm=False)
        self.assertFalse(granted_install.offer(console))
        granted_install.install.assert_not_called()
        self.assertTrue(any("Instalação manual" in note for note in console.notes))

    def test_without_a_terminal_it_never_installs_by_itself(self):
        console = FakeConsole(interactive=False)
        self.assertFalse(granted_install.offer(console))
        granted_install.install.assert_not_called()
        self.assertEqual(console.asked, [])
        self.assertTrue(any("--yes" in note for note in console.notes))

    def test_yes_installs_without_asking_even_without_a_terminal(self):
        console = FakeConsole(interactive=False)
        self.assertTrue(granted_install.offer(console, assume_yes=True))
        self.assertEqual(console.asked, [])
        granted_install.install.assert_called_once()

    def test_a_failed_install_reports_and_points_to_the_manual_way(self):
        console = FakeConsole()
        granted_install.install.side_effect = granted_install.InstallError("o SHA-256 não confere")
        self.assertFalse(granted_install.offer(console))
        self.assertEqual(console.fails, ["o SHA-256 não confere"])
        self.assertTrue(any("Instalação manual" in note for note in console.notes))

    def test_an_unsupported_system_only_points_to_the_docs(self):
        console = FakeConsole()
        with mock.patch.object(granted_install, "method", return_value=""):
            self.assertFalse(granted_install.offer(console))
        self.assertTrue(any(granted_install.DOCS_URL in note for note in console.notes))
        self.assertEqual(console.asked, [])

    def test_it_warns_when_the_install_folder_is_not_on_the_path(self):
        console = FakeConsole()
        with mock.patch.object(granted_install, "on_path", return_value=False):
            self.assertTrue(granted_install.offer(console, assume_yes=True))
        self.assertTrue(any("PATH" in warn for warn in console.warns))


class ExecutableTest(unittest.TestCase):
    def test_finds_granted_in_the_local_folder_when_it_is_not_on_the_path(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            folder = Path(tmp) / ".local" / "bin"
            folder.mkdir(parents=True)
            with mock.patch.object(granted.shutil, "which", return_value=None):
                self.assertIsNone(granted.executable())
                (folder / "granted").write_text("x")
                self.assertIsNone(granted.executable())  # sem permissao de execucao
                (folder / "granted").chmod(0o755)
                self.assertEqual(granted.executable(), str(folder / "granted"))

    def test_the_path_wins_over_the_local_folder(self):
        with mock.patch.object(granted.shutil, "which", return_value="/opt/homebrew/bin/granted"):
            self.assertEqual(granted.executable(), "/opt/homebrew/bin/granted")


if __name__ == "__main__":
    unittest.main()
