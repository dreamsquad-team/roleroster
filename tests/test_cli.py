import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from roleroster import cli, helptext


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


class HelpTest(unittest.TestCase):
    def test_no_arguments_and_dash_h_list_the_commands(self):
        for args in ((), ("-h",), ("--help",), ("help",)):
            code, out, _ = run(*args)
            self.assertEqual(code, 0)
            for word in ("sso", "console", "sync", "squads", "init", "granted"):
                self.assertIn(word, out)

    def test_every_documented_command_is_one_the_cli_really_handles(self):
        handled = set(cli.PROFILE_COMMANDS) | set(cli.SHELL_COMMANDS) | {"init", "granted", "-h", "-V", "<outro"}
        short_options = {"-i", "-S", "-n", "-U", "-B", "-T"}
        self.assertLessEqual(helptext.command_names() - short_options, handled)

    def test_the_plain_help_is_aligned_in_two_columns(self):
        lines = [l for l in helptext.render_plain().splitlines() if l.startswith("  ")]
        columns = {l.index(l.strip().split("  ", 1)[1].strip()) for l in lines}
        self.assertEqual(len(columns), 1)

    def test_the_help_goes_to_stdout_not_stderr(self):
        _, out, err = run("-h")
        self.assertTrue(out and not err)

    def test_the_rich_help_has_a_rule_per_section_and_every_row(self):
        from rich.console import Console

        buffer = io.StringIO()
        helptext.render_rich(Console(file=buffer, width=76, highlight=False, color_system=None))
        text = buffer.getvalue()
        for title, _, rows in helptext.SECTIONS:
            self.assertIn(f"{title}", text)
            for name, _ in rows:
                self.assertIn(name, text)
        self.assertEqual(sum(l.endswith("─") for l in text.splitlines()), len(helptext.SECTIONS))

    def test_internal_commands_stay_out_of_the_help(self):
        _, out, _ = run("-h")
        for word in ("session-save", "prune-exported", "pick"):
            self.assertNotIn(word, out)

    def test_version(self):
        code, out, _ = run("--version")
        self.assertEqual((code, out.strip()), (0, f"roleroster {cli.__version__}"))


class DispatchTest(unittest.TestCase):
    def test_shell_commands_explain_that_they_need_the_shell(self):
        for command in ("sso", "console", "assume"):
            code, _, err = run(command)
            self.assertEqual(code, cli.EXIT_USAGE)
            self.assertIn("role init", err)

    def test_profile_commands_go_to_profiles_with_the_full_arguments(self):
        with mock.patch.object(cli.profiles, "main", return_value=0) as main:
            self.assertEqual(run("sync", "-i")[0], 0)
        main.assert_called_once_with(["sync", "-i"])

    def test_init_gets_the_rest_of_the_arguments(self):
        with mock.patch.object(cli.init, "main", return_value=0) as main:
            run("init", "--aliases")
        main.assert_called_once_with(["--aliases"])

    def test_granted_prefix_is_dropped_before_calling_granted(self):
        with mock.patch.object(cli, "run_granted", return_value=0) as granted:
            run("granted", "sso-tokens", "--help")
        granted.assert_called_once_with(["sso-tokens", "--help"])

    def test_unknown_commands_are_inherited_from_granted(self):
        with mock.patch.object(cli, "run_granted", return_value=0) as granted:
            run("credentials", "list")
        granted.assert_called_once_with(["credentials", "list"])


class RunGrantedTest(unittest.TestCase):
    def test_executes_the_binary_with_the_arguments(self):
        with mock.patch.object(cli.shutil, "which", return_value="/bin/granted"), \
                mock.patch.object(cli.os, "execv") as execv:
            cli.run_granted(["doctor"])
        execv.assert_called_once_with("/bin/granted", ["/bin/granted", "doctor"])

    def test_explains_how_to_install_when_it_is_missing(self):
        with mock.patch.object(cli.shutil, "which", return_value=None):
            err = io.StringIO()
            with redirect_stderr(err):
                code = cli.run_granted(["doctor"])
        self.assertEqual(code, 127)
        self.assertIn("brew install", err.getvalue())


if __name__ == "__main__":
    unittest.main()
