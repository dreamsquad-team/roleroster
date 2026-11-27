import io
import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

from roleroster import ui

SQUADS = [{"id": "11", "title": "Squad A"}, {"id": "22", "title": "Squad B"}, {"id": "33", "title": "Squad C"}]


class FakeStderr(io.StringIO):
    def __init__(self, tty=False, encoding="utf-8"):
        super().__init__()
        self._tty = tty
        self._encoding = encoding

    @property
    def encoding(self):
        return self._encoding

    def isatty(self):
        return self._tty


def render(console_call, tty=False, env=None, encoding="utf-8"):
    err = FakeStderr(tty=tty, encoding=encoding)
    with mock.patch.dict(os.environ, env or {}, clear=False), mock.patch("sys.stderr", err):
        console_call()
    return err.getvalue()


class StyleTest(unittest.TestCase):
    def test_plain_text_without_a_terminal(self):
        text = render(lambda: print(ui.ok_line("pronto"), file=sys.stderr))
        self.assertEqual(text, "✔ pronto\n")

    def test_colors_only_with_a_terminal(self):
        with mock.patch("sys.stderr", FakeStderr(tty=True)), mock.patch.dict(os.environ, {"TERM": "xterm"}):
            os.environ.pop("NO_COLOR", None)
            self.assertIn("\033[", ui.ok_line("pronto"))

    def test_no_color_turns_colors_off_even_in_a_terminal(self):
        with mock.patch("sys.stderr", FakeStderr(tty=True)), mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            self.assertNotIn("\033[", ui.fail_line("erro"))

    def test_ascii_glyphs_when_the_terminal_is_not_utf8(self):
        with mock.patch("sys.stderr", FakeStderr(encoding="cp1252")):
            self.assertEqual(ui.glyph("ok"), "+")
            self.assertEqual(ui.glyph("fail"), "x")

    def test_plural(self):
        self.assertEqual(ui.plural(1, "squad"), "1 squad")
        self.assertEqual(ui.plural(2, "squad"), "2 squads")
        self.assertEqual(ui.plural(2, "perfil", "perfis"), "2 perfis")


class ConsoleOutputTest(unittest.TestCase):
    def lines(self, call):
        return render(call).splitlines()

    def test_step_numbers_only_when_there_is_more_than_one(self):
        console = ui.Console()
        self.assertEqual(self.lines(lambda: console.step(2, 4, "Confluence")), ["", "2/4  Confluence"])
        self.assertEqual(self.lines(lambda: console.step(1, 1, "Confluence")), ["", "Confluence"])

    def test_status_lines_are_indented_under_the_step(self):
        console = ui.Console()
        self.assertEqual(self.lines(lambda: console.warn("atenção")), ["  ! atenção"])
        self.assertEqual(self.lines(lambda: console.ok("feito")), ["  ✔ feito"])
        self.assertEqual(self.lines(lambda: console.fail("erro")), ["  ✖ erro"])
        self.assertEqual(self.lines(lambda: console.note("dica")), ["  › dica"])

    def test_header_has_title_and_optional_subtitle(self):
        console = ui.Console()
        self.assertEqual(self.lines(lambda: console.header("T", "sub")), ["T", "sub"])
        self.assertEqual(self.lines(lambda: console.header("T")), ["T"])


class PlainSummaryTest(unittest.TestCase):
    def test_summary_lists_label_and_value_aligned(self):
        text = render(lambda: ui.Console().summary("Pronto", [("Config", "~/a"), ("Credenciais", "~/b")]))
        lines = text.splitlines()
        self.assertEqual(lines[1], "Pronto")
        self.assertEqual(lines[2], "  Config       ~/a")
        self.assertEqual(lines[3], "  Credenciais  ~/b")


class SpinnerTest(unittest.TestCase):
    def test_without_a_terminal_it_shows_nothing_and_still_runs_the_body(self):
        ran = []
        err = FakeStderr(tty=False)
        with mock.patch("sys.stderr", err):
            with ui.spinner("Sincronizando"):
                ran.append(True)
        self.assertEqual((ran, err.getvalue()), ([True], ""))


class RichOutputTest(unittest.TestCase):
    """O PromptConsole desenha com o rich; aqui num console de texto, sem terminal."""

    def console(self):
        from rich.console import Console as RichConsole

        buffer = io.StringIO()
        prompt = ui.PromptConsole()
        prompt._rich = RichConsole(file=buffer, width=60, highlight=False, color_system=None)
        return prompt, buffer

    def test_header_is_a_box_with_title_and_subtitle(self):
        prompt, out = self.console()
        prompt.header("roleroster · primeira configuração", "Cerca de 2 minutos.")
        text = out.getvalue()
        self.assertTrue(text.startswith("╭") and "╰" in text)
        self.assertIn("roleroster · primeira configuração", text)
        self.assertIn("Cerca de 2 minutos.", text)

    def test_step_is_a_rule_with_the_title_and_numbering(self):
        prompt, out = self.console()
        prompt.step(2, 4, "Acesso AWS SSO")
        line = [l for l in out.getvalue().splitlines() if l.strip()][0]
        self.assertTrue(line.startswith("2/4 · Acesso AWS SSO ─"))
        self.assertEqual(len(line), 60)

    def test_single_step_has_no_numbering(self):
        prompt, out = self.console()
        prompt.step(1, 1, "Confluence")
        self.assertTrue([l for l in out.getvalue().splitlines() if l.strip()][0].startswith("Confluence ─"))

    def test_status_lines(self):
        prompt, out = self.console()
        prompt.ok("feito")
        prompt.fail("erro")
        prompt.warn("atenção")
        prompt.note("veja https://exemplo.com/x")
        self.assertEqual(out.getvalue().splitlines(), ["  ✔ feito", "  ✖ erro", "  ! atenção", "  › veja https://exemplo.com/x"])

    def test_values_with_square_brackets_are_not_taken_as_markup(self):
        prompt, out = self.console()
        prompt.summary("Pronto", [("Perfis", "[inativo] x")])
        prompt.ok("lista [bold]literal[/bold]")
        text = out.getvalue()
        self.assertIn("[inativo] x", text)
        self.assertIn("[bold]literal[/bold]", text)

    def test_summary_is_a_box_with_a_title_and_all_rows(self):
        prompt, out = self.console()
        prompt.summary("Configuração concluída", [("Config", "~/a"), ("Squads", "2 selecionados")])
        text = out.getvalue()
        self.assertIn("╭─ ✔ Configuração concluída", text)
        for wanted in ("Config", "~/a", "Squads", "2 selecionados"):
            self.assertIn(wanted, text)


class ChooseTest(unittest.TestCase):
    OPTIONS = [("Próprios", "~/.aws/a"), ("Os da AWS", ""), ("Outro", "")]

    def choose(self, answers):
        console = ui.Console()
        queue = list(answers)
        console.ask = lambda prompt, default="": queue.pop(0) or default
        console.say = lambda text="": None
        console.fail = lambda text: None
        return console.choose("Onde?", self.OPTIONS)

    def test_enter_takes_the_first_option(self):
        self.assertEqual(self.choose([""]), 0)

    def test_a_number_picks_that_option(self):
        self.assertEqual(self.choose(["3"]), 2)

    def test_the_plain_console_acknowledges_the_choice(self):
        console = ui.Console()
        console.ask = lambda prompt, default="": "2"
        console.say = lambda text="": None
        seen = []
        console.ok = seen.append
        console.choose("Onde?", self.OPTIONS)
        self.assertEqual(seen, ["Os da AWS"])

    def test_invalid_numbers_are_asked_again(self):
        self.assertEqual(self.choose(["0", "9", "x", "2"]), 1)


class SquadsByNumberTest(unittest.TestCase):
    """Sem fzf o seletor cai num prompt numerado."""

    def run_choice(self, answer):
        console = ui.Console()
        console.say = lambda text="": None
        console.ask = lambda prompt, default="": answer
        with mock.patch.object(shutil, "which", return_value=None):
            return console.choose_squads(SQUADS, ["11"])

    def test_numbers_pick_squads_and_garbage_is_ignored(self):
        self.assertEqual(self.run_choice("1, 3"), ["11", "33"])
        self.assertEqual(self.run_choice("2 9 x"), ["22"])

    def test_empty_or_invalid_answer_cancels(self):
        self.assertIsNone(self.run_choice(""))
        self.assertIsNone(self.run_choice("0 7"))


class MakeConsoleTest(unittest.TestCase):
    def make(self, stdin, stdout, stderr):
        with mock.patch("sys.stdin", FakeStderr(stdin)), mock.patch("sys.stdout", FakeStderr(stdout)), \
                mock.patch("sys.stderr", FakeStderr(stderr)):
            return ui.make_console()

    def test_prompts_only_with_a_terminal_on_all_three_streams(self):
        self.assertIsInstance(self.make(True, True, True), ui.PromptConsole)
        for tty in ((False, True, True), (True, False, True), (True, True, False)):
            console = self.make(*tty)
            self.assertIs(type(console), ui.Console)

    def test_questionary_is_not_imported_until_the_first_question(self):
        code = "import sys, roleroster.ui, roleroster.cli; print('questionary' in sys.modules)"
        done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(done.stdout.strip(), "False", done.stderr)


if __name__ == "__main__":
    unittest.main()
