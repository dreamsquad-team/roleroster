import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from roleroster import init, paths

RC = "export PATH=$HOME/bin:$PATH\nalias ll='ls -l'\n"


class BlockTest(unittest.TestCase):
    def test_block_has_markers_and_sources_the_script(self):
        block = init.render_block("/x/roleroster.zsh")
        self.assertTrue(block.startswith(init.BEGIN) and block.rstrip().endswith(init.END))
        self.assertIn('source "/x/roleroster.zsh"', block)
        self.assertNotIn("alias", block)

    def test_there_are_never_bare_command_aliases(self):
        self.assertNotIn("alias", init.render_block("/x/roleroster.zsh"))

    def test_an_explicit_home_is_exported_before_sourcing(self):
        block = init.render_block("/x/r.zsh", home="/h")
        self.assertLess(block.index("ROLEROSTER_HOME"), block.index("source"))

    def test_apply_appends_to_the_rc_and_keeps_what_was_there(self):
        new = init.apply_block(RC, init.render_block("/x/r.zsh"))
        self.assertTrue(new.startswith(RC))
        self.assertEqual(new.count(init.BEGIN), 1)

    def test_apply_twice_does_not_duplicate(self):
        block = init.render_block("/x/r.zsh")
        once = init.apply_block(RC, block)
        self.assertEqual(init.apply_block(once, block), once)

    def test_apply_replaces_an_old_block_in_place(self):
        old = RC + init.render_block("/velho/r.zsh") + "echo depois\n"
        new = init.apply_block(old, init.render_block("/novo/r.zsh"))
        self.assertIn("/novo/r.zsh", new)
        self.assertNotIn("/velho/", new)
        self.assertTrue(new.endswith("echo depois\n"))

    def test_remove_gives_back_the_original_rc(self):
        applied = init.apply_block(RC, init.render_block("/x/r.zsh"))
        self.assertEqual(init.remove_block(applied), RC)

    def test_remove_without_a_block_changes_nothing(self):
        self.assertEqual(init.remove_block(RC), RC)

    def test_an_rc_without_trailing_newline_still_gets_a_clean_block(self):
        new = init.apply_block("alias a=b", init.render_block("/x/r.zsh"))
        self.assertTrue(new.startswith("alias a=b\n"))
        self.assertIn("\n" + init.BEGIN, new)


class MainTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.rc = self.root / "zshrc"
        self.rc.write_text(RC)
        patcher = mock.patch.dict(os.environ, {"ROLEROSTER_HOME": str(self.root / "home"), "SHELL": "/bin/zsh"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_init(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = init.main(["--rc", str(self.rc), *args])
        return code, out.getvalue(), err.getvalue()

    def test_installs_the_script_and_the_block(self):
        code, out, _ = self.run_init()
        self.assertEqual(code, 0)
        script = paths.shell_dir() / init.SCRIPT_NAME
        self.assertIn("role()", script.read_text())
        self.assertIn(f'source "{script}"', self.rc.read_text())
        self.assertIn("novo terminal", out)

    def test_keeps_a_backup_of_the_previous_rc(self):
        self.run_init()
        self.assertEqual((paths.backup_dir() / "zshrc.prev").read_text(), RC)

    def test_second_run_says_it_is_up_to_date(self):
        self.run_init()
        _, out, _ = self.run_init()
        self.assertIn("em dia", out)
        self.assertEqual(self.rc.read_text().count(init.BEGIN), 1)

    def test_dry_run_writes_nothing(self):
        _, out, _ = self.run_init("--dry-run")
        self.assertIn(init.BEGIN, out)
        self.assertEqual(self.rc.read_text(), RC)
        self.assertFalse(paths.shell_dir().exists())

    def test_uninstall_restores_the_rc_and_removes_the_script_but_not_the_env(self):
        self.run_init()
        paths.home().mkdir(parents=True, exist_ok=True)
        paths.env_file().write_text("X=1\n")
        code, out, _ = self.run_init("--uninstall")
        self.assertEqual(code, 0)
        self.assertEqual(self.rc.read_text(), RC)
        self.assertFalse((paths.shell_dir() / init.SCRIPT_NAME).exists())
        self.assertTrue(paths.env_file().exists())

    def test_running_init_removes_aliases_left_by_an_older_version(self):
        old_block = init.BEGIN + '\nsource "/x/r.zsh"\nalias sso=\'role sso\'\nalias console=\'role console\'\n' + init.END + "\n"
        self.rc.write_text(RC + old_block)
        self.run_init()
        text = self.rc.read_text()
        self.assertNotIn("alias sso", text)
        self.assertIn("alias ll='ls -l'", text)  # o resto do rc fica

    def test_refresh_updates_a_stale_script_only_after_init(self):
        self.assertFalse(init.refresh_script())
        self.assertFalse(paths.shell_dir().exists())
        self.run_init()
        self.assertFalse(init.refresh_script())
        (paths.shell_dir() / init.SCRIPT_NAME).write_text("# velho\n")
        self.assertTrue(init.refresh_script())
        self.assertIn("role()", (paths.shell_dir() / init.SCRIPT_NAME).read_text())

    def test_short_flags_match_the_long_ones(self):
        _, out, _ = self.run_init("-n")
        self.assertIn(init.BEGIN, out)
        self.run_init()
        self.run_init("-u")
        self.assertEqual(self.rc.read_text(), RC)

    def test_bash_is_supported_and_uses_the_same_script(self):
        bashrc = self.root / "bashrc"
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(init.main(["--shell", "bash", "--rc", str(bashrc)]), 0)
        script = paths.shell_dir() / init.SCRIPT_NAME
        self.assertIn(f'source "{script}"', bashrc.read_text())
        self.assertEqual(script.name, "roleroster.sh")

    def test_the_rc_file_depends_on_the_shell_and_the_system(self):
        home = Path.home()
        with mock.patch.object(init.sys, "platform", "darwin"):
            self.assertEqual(init.rc_path("bash"), home / ".bash_profile")
        with mock.patch.object(init.sys, "platform", "linux"):
            self.assertEqual(init.rc_path("bash"), home / ".bashrc")
        self.assertEqual(init.rc_path("zsh").name, ".zshrc")

    def test_the_shell_is_detected_from_the_environment(self):
        with mock.patch.dict(os.environ, {"SHELL": "/bin/bash"}):
            self.assertEqual(init.detect_shell(), "bash")

    def test_an_old_install_keeps_working_through_a_shim_and_is_migrated(self):
        shell_dir = paths.shell_dir()
        shell_dir.mkdir(parents=True)
        (shell_dir / init.LEGACY_SCRIPT_NAME).write_text("# script velho inteiro\nrole() { :; }\n")
        self.assertTrue(init.refresh_script())
        new = shell_dir / init.SCRIPT_NAME
        self.assertIn("role()", new.read_text())
        shim = (shell_dir / init.LEGACY_SCRIPT_NAME).read_text()
        self.assertIn(f'source "{new}"', shim)
        self.assertNotIn("script velho", shim)
        self.assertFalse(init.refresh_script())  # e fica em dia

    def test_uninstall_keeps_the_shared_script_while_the_other_shell_still_uses_it(self):
        zshrc, bashrc = self.root / "z", self.root / "b"
        for shell, rc in (("zsh", zshrc), ("bash", bashrc)):
            init.main(["--shell", shell, "--rc", str(rc)])
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(init, "rc_path", side_effect=lambda sh: {"zsh": zshrc, "bash": bashrc}[sh]):
            init.main(["--shell", "bash", "--rc", str(bashrc), "--uninstall"])
        self.assertTrue((paths.shell_dir() / init.SCRIPT_NAME).exists())
        self.assertIn("outro shell", out.getvalue())
        with redirect_stdout(io.StringIO()), mock.patch.object(init, "rc_path", side_effect=lambda sh: {"zsh": zshrc, "bash": bashrc}[sh]):
            init.main(["--shell", "zsh", "--rc", str(zshrc), "--uninstall"])
        self.assertFalse((paths.shell_dir() / init.SCRIPT_NAME).exists())

    def test_unsupported_shell_is_refused_with_the_list_of_supported_ones(self):
        with mock.patch.dict(os.environ, {"SHELL": "/usr/bin/fish"}):
            code, _, err = self.run_init()
        self.assertEqual(code, 2)
        self.assertIn("zsh", err)
        self.assertIn("bash", err)
        self.assertEqual(self.rc.read_text(), RC)


class PathsTest(unittest.TestCase):
    def test_explicit_home_wins(self):
        with mock.patch.dict(os.environ, {"ROLEROSTER_HOME": "/tmp/rr_home"}):
            self.assertEqual(paths.home(), Path("/tmp/rr_home"))
            self.assertEqual(paths.session_file(), Path("/tmp/rr_home/state/session"))

    def test_follows_xdg_config_home_when_there_is_no_explicit_home(self):
        env = {"XDG_CONFIG_HOME": "/tmp/xdg"}
        with mock.patch.dict(os.environ, env), mock.patch.dict(os.environ):
            os.environ.pop("ROLEROSTER_HOME", None)
            self.assertEqual(paths.home(), Path("/tmp/xdg/roleroster"))


if __name__ == "__main__":
    unittest.main()
