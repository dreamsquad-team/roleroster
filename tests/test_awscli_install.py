import hashlib
import io
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from test_granted_install import FakeConsole

from roleroster import awscli_install as aws
from roleroster.granted_install import InstallError


def make_zip(entries):
    """entries: {nome: bytes | ('link', alvo) | ('exec', bytes)}."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            info = zipfile.ZipInfo(name)
            if isinstance(content, tuple) and content[0] == "link":
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, content[1])
            elif isinstance(content, tuple):
                info.external_attr = (stat.S_IFREG | 0o755) << 16
                archive.writestr(info, content[1])
            else:
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, content)
    return buffer.getvalue()


class MethodTest(unittest.TestCase):
    def test_macos_uses_brew_or_points_to_the_guide(self):
        with mock.patch.object(aws.sys, "platform", "darwin"), mock.patch.object(aws.shutil, "which", return_value="/opt/homebrew/bin/brew"):
            self.assertEqual(aws.method(), "brew")
        with mock.patch.object(aws.sys, "platform", "darwin"), mock.patch.object(aws.shutil, "which", return_value=None):
            self.assertEqual(aws.method(), "")
            self.assertIn(aws.DOCS_URL, aws.manual_hint())

    def test_linux_downloads_the_official_zip_for_x86_64_and_arm64(self):
        for arch, aws_arch in (("x86_64", "x86_64"), ("arm64", "aarch64")):
            with mock.patch.object(aws.sys, "platform", "linux"), mock.patch.object(aws, "machine", return_value=("linux", arch)):
                self.assertEqual(aws.method(), "zip")
                self.assertEqual(aws.zip_name(), f"awscli-exe-linux-{aws_arch}-{aws.VERSION}.zip")

    def test_an_unknown_architecture_has_no_automatic_install(self):
        with mock.patch.object(aws.sys, "platform", "linux"), mock.patch.object(aws, "machine", return_value=("linux", "riscv64")):
            self.assertEqual(aws.method(), "")

    def test_the_pinned_hashes_are_well_formed(self):
        for digest in aws.SHA256.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")


class ExtractZipTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dest = Path(tmp.name) / "out"
        self.dest.mkdir()

    def test_files_keep_their_permissions(self):
        aws.extract_zip(make_zip({"aws/install": ("exec", b"#!/bin/sh\n"), "aws/dist/data.json": b"{}"}), self.dest)
        self.assertTrue(os.access(self.dest / "aws" / "install", os.X_OK))
        self.assertFalse(os.access(self.dest / "aws" / "dist" / "data.json", os.X_OK))
        self.assertEqual((self.dest / "aws" / "dist" / "data.json").read_bytes(), b"{}")

    def test_a_relative_link_inside_the_folder_is_created(self):
        aws.extract_zip(make_zip({"aws/real": b"x", "aws/atalho": ("link", "real")}), self.dest)
        self.assertEqual(os.readlink(self.dest / "aws" / "atalho"), "real")

    def test_paths_that_leave_the_folder_are_refused(self):
        for name in ("../fora", "aws/../../fora", "/etc/passwd"):
            with self.assertRaises(InstallError, msg=name):
                aws.extract_zip(make_zip({name: b"x"}), self.dest)
        self.assertFalse((self.dest.parent / "fora").exists())

    def test_links_that_point_outside_are_refused(self):
        for link in ("/etc/passwd", "../../fora"):
            with self.assertRaises(InstallError, msg=link):
                aws.extract_zip(make_zip({"aws/ruim": ("link", link)}), self.dest)

    def test_too_many_files_or_too_much_data_are_refused(self):
        with mock.patch.object(aws, "MAX_ENTRIES", 2), self.assertRaises(InstallError):
            aws.extract_zip(make_zip({"a": b"1", "b": b"2", "c": b"3"}), self.dest)
        with mock.patch.object(aws, "MAX_UNPACKED", 3), self.assertRaises(InstallError):
            aws.extract_zip(make_zip({"a": b"12345"}), self.dest)

    def test_garbage_is_refused(self):
        with self.assertRaises(InstallError):
            aws.extract_zip(b"nao e um zip", self.dest)


class InstallZipTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        for patcher in (
            mock.patch.object(Path, "home", return_value=self.home),
            mock.patch.object(aws, "machine", return_value=("linux", "x86_64")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def serve(self, data, digest=None):
        digest = digest or hashlib.sha256(data).hexdigest()
        for patcher in (
            mock.patch.dict(aws.SHA256, {("linux", "x86_64"): digest}),
            mock.patch.object(aws, "fetch", return_value=data),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_runs_the_official_installer_with_the_user_folders(self):
        self.serve(make_zip({"aws/install": ("exec", b"#!/bin/sh\n")}))
        done = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(aws.subprocess, "run", return_value=done) as run:
            aws.install_zip()
        command = run.call_args.args[0]
        self.assertTrue(command[0].endswith("aws/install"))
        self.assertEqual(command[1:], ["-i", str(self.home / ".local" / "aws-cli"), "-b", str(self.home / ".local" / "bin"), "--update"])

    def test_a_wrong_hash_runs_nothing(self):
        self.serve(make_zip({"aws/install": ("exec", b"x")}), digest="0" * 64)
        with mock.patch.object(aws.subprocess, "run") as run, self.assertRaises(InstallError) as caught:
            aws.install_zip()
        self.assertIn("SHA-256", str(caught.exception))
        run.assert_not_called()

    def test_a_failing_installer_reports_the_tail_of_its_output(self):
        self.serve(make_zip({"aws/install": ("exec", b"x")}))
        done = mock.Mock(returncode=1, stdout="", stderr="a\nb\nErro: sem espaço")
        with mock.patch.object(aws.subprocess, "run", return_value=done), self.assertRaises(InstallError) as caught:
            aws.install_zip()
        self.assertIn("sem espaço", str(caught.exception))


class FetchTest(unittest.TestCase):
    def test_only_the_aws_host_over_https_is_downloaded(self):
        for url in ("http://awscli.amazonaws.com/x", "https://example.com/x", "https://github.com/x"):
            with self.assertRaises(InstallError):
                aws.fetch(url)

    def test_a_network_error_becomes_an_install_error(self):
        with mock.patch.object(aws.urllib.request, "urlopen", side_effect=OSError("sem rede")), self.assertRaises(InstallError):
            aws.fetch("https://awscli.amazonaws.com/x.zip")


class OfferTest(unittest.TestCase):
    def setUp(self):
        for patcher in (
            mock.patch.object(aws, "executable", return_value=None),
            mock.patch.object(aws, "method", return_value="zip"),
            mock.patch.object(aws, "describe", return_value="baixar a AWS CLI"),
            mock.patch.object(aws, "on_path", return_value=True),
            mock.patch.object(aws, "install", return_value="AWS CLI instalada"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_already_installed_does_nothing(self):
        console = FakeConsole()
        with mock.patch.object(aws, "executable", return_value="/usr/bin/aws"):
            self.assertTrue(aws.offer(console))
        self.assertEqual((console.asked, console.notes), ([], []))

    def test_it_asks_and_installs_on_yes(self):
        console = FakeConsole(confirm=True)
        self.assertTrue(aws.offer(console))
        self.assertEqual(console.asked, ["Instalar a AWS CLI agora?"])
        aws.install.assert_called_once_with("zip")
        self.assertEqual(console.oks, ["AWS CLI instalada"])

    def test_no_means_nothing_is_installed(self):
        console = FakeConsole(confirm=False)
        self.assertFalse(aws.offer(console))
        aws.install.assert_not_called()
        self.assertTrue(any("--aws-cli" in note for note in console.notes))

    def test_without_a_terminal_it_only_mentions_the_flag(self):
        console = FakeConsole(interactive=False)
        self.assertFalse(aws.offer(console))
        aws.install.assert_not_called()
        self.assertEqual(console.asked, [])
        self.assertTrue(any("--aws-cli" in note for note in console.notes))

    def test_the_flag_installs_without_asking_even_without_a_terminal(self):
        console = FakeConsole(interactive=False)
        self.assertTrue(aws.offer(console, choice=True))
        self.assertEqual(console.asked, [])
        aws.install.assert_called_once()

    def test_the_no_flag_never_offers_and_says_nothing(self):
        console = FakeConsole()
        self.assertFalse(aws.offer(console, choice=False))
        self.assertEqual((console.asked, console.notes, console.oks), ([], [], []))
        aws.install.assert_not_called()

    def test_a_failed_install_reports_and_points_to_the_manual_way(self):
        console = FakeConsole()
        aws.install.side_effect = InstallError("o SHA-256 não confere")
        self.assertFalse(aws.offer(console))
        self.assertEqual(console.fails, ["o SHA-256 não confere"])
        self.assertTrue(any("Instalação manual" in note for note in console.notes))

    def test_an_unsupported_system_only_points_to_the_guide(self):
        console = FakeConsole()
        with mock.patch.object(aws, "method", return_value=""):
            self.assertFalse(aws.offer(console))
        self.assertTrue(any(aws.DOCS_URL in note for note in console.notes))
        self.assertEqual(console.asked, [])

    def test_it_warns_when_the_install_folder_is_not_on_the_path(self):
        console = FakeConsole()
        with mock.patch.object(aws, "on_path", return_value=False):
            self.assertTrue(aws.offer(console, choice=True))
        self.assertTrue(any("PATH" in warn for warn in console.warns))


class ExecutableTest(unittest.TestCase):
    def test_finds_aws_in_the_local_folder_when_it_is_not_on_the_path(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(Path, "home", return_value=Path(tmp)):
            folder = Path(tmp) / ".local" / "bin"
            folder.mkdir(parents=True)
            with mock.patch.object(aws.shutil, "which", return_value=None):
                self.assertIsNone(aws.executable())
                (folder / "aws").write_text("x")
                (folder / "aws").chmod(0o755)
                self.assertEqual(aws.executable(), str(folder / "aws"))


if __name__ == "__main__":
    unittest.main()
