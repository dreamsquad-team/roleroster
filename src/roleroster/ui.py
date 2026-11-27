"""Terminal do roleroster: linhas de status, passos e perguntas.

Dois consoles com a mesma interface:

  Console        texto simples (stdlib); serve para scripts, pipes e testes
  PromptConsole  caixas, linhas de separacao e tabela (rich) e perguntas e listas
                 interativas (questionary)

`make_console()` escolhe o segundo so quando ha terminal de verdade. rich e
questionary so sao importados quando algo precisa deles, para nao pesar nos
comandos que nao mostram nada. Tudo vai para o stderr: o stdout fica livre para
o nome do perfil que o shell captura.

Cores so com terminal, e `NO_COLOR` desliga (https://no-color.org).
"""
import contextlib
import getpass
import os
import re
import shutil
import subprocess
import sys
import webbrowser

STYLES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "cyan": "36"}
GLYPHS = {"ok": ("✔", "+"), "fail": ("✖", "x"), "warn": ("!", "!"), "note": ("›", ">")}


def color_enabled():
    return sys.stderr.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def paint(text, *styles):
    if not color_enabled():
        return text
    codes = ";".join(STYLES[s] for s in styles)
    return f"\033[{codes}m{text}\033[0m"


def glyph(kind):
    unicode_ok = (getattr(sys.stderr, "encoding", None) or "").lower().startswith("utf")
    return GLYPHS[kind][0 if unicode_ok else 1]


def ok_line(text):
    return f"{paint(glyph('ok'), 'green')} {text}"


def fail_line(text):
    return f"{paint(glyph('fail'), 'red')} {text}"


def warn_line(text):
    return f"{paint(glyph('warn'), 'yellow', 'bold')} {text}"


def note_line(text):
    return f"{paint(glyph('note'), 'dim')} {paint(text, 'dim')}"


def plural(count, singular, plural_form=None):
    return f"{count} {singular if count == 1 else (plural_form or singular + 's')}"


class Console:
    """Entrada e saida em texto simples. Os testes trocam esta classe por uma falsa."""

    INDENT = "  "

    def interactive(self):
        return sys.stdin.isatty() and sys.stderr.isatty()

    # ---- saida

    def say(self, text=""):
        print(text, file=sys.stderr)

    def header(self, title, subtitle=""):
        self.say(paint(title, "bold"))
        if subtitle:
            self.say(paint(subtitle, "dim"))

    def step(self, index, total, title):
        self.say("")
        self.say(paint(f"{index}/{total}  {title}" if total > 1 else title, "bold"))

    def ok(self, text):
        self.say(self.INDENT + ok_line(text))

    def fail(self, text):
        self.say(self.INDENT + fail_line(text))

    def note(self, text):
        self.say(self.INDENT + note_line(text))

    def warn(self, text):
        self.say(self.INDENT + warn_line(text))

    def summary(self, title, rows):
        """Resumo do que foi configurado: lista de (rotulo, valor)."""
        self.say("")
        self.say(paint(title, "bold"))
        width = max(len(label) for label, _ in rows)
        for label, value in rows:
            self.say(f"{self.INDENT}{paint(label.ljust(width), 'dim')}  {value}")

    def link(self, url):
        """URL clicavel (OSC 8) nos terminais que suportam; texto puro nos outros."""
        if sys.stderr.isatty():
            return f"\033]8;;{url}\033\\{url}\033]8;;\033\\"
        return url

    def open_url(self, url):
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            pass

    # ---- entrada

    def ask(self, prompt, default=""):
        suffix = f" [{default}]" if default else ""
        print(f"{self.INDENT}{prompt}{suffix}: ", end="", file=sys.stderr, flush=True)
        answer = sys.stdin.readline()
        if answer == "":
            raise EOFError
        return answer.strip() or default

    def ask_secret(self, prompt):
        return getpass.getpass(f"{self.INDENT}{prompt}: ", stream=sys.stderr).strip()

    def choose(self, prompt, options, default=0):
        """options: lista de (rotulo, dica). Devolve o indice escolhido."""
        self.say(self.INDENT + prompt)
        for number, (label, hint) in enumerate(options, 1):
            self.say(f"{self.INDENT}  {number}) {label}" + (f"  {paint(hint, 'dim')}" if hint else ""))
        while True:
            answer = self.ask("Número", default=str(default + 1))
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                self.ok(options[int(answer) - 1][0])
                return int(answer) - 1
            self.fail("Escolha um dos números da lista.")

    def confirm(self, prompt, default=True):
        hint = "S/n" if default else "s/N"
        answer = self.ask(f"{prompt} [{hint}]").lower()
        return default if not answer else answer.startswith(("s", "y"))

    def choose_squads(self, squads, selected_ids):
        """Devolve os ids marcados, ou None se o usuario cancelar."""
        if shutil.which("fzf"):
            return self._choose_with_fzf(squads, selected_ids)
        return self._choose_by_number(squads, selected_ids)

    def _choose_with_fzf(self, squads, selected_ids):
        width = max(len(s["title"]) for s in squads)
        lines = [f"{s['title']:<{width}}  {s['id']}" for s in squads]
        # pos(N)+toggle marca o N-esimo item assim que a lista carrega
        marked = [i for i, s in enumerate(squads, 1) if s["id"] in selected_ids]
        bind = "load:" + "".join(f"pos({i})+toggle+" for i in marked) + "first"
        command = [
            "fzf", "--multi", "--height=50%", "--reverse", "--prompt=squads> ",
            "--header=TAB marca e desmarca, Enter confirma, Esc cancela",
            f"--bind={bind}",
        ]
        result = subprocess.run(command, input="\n".join(lines), stdout=subprocess.PIPE, text=True)
        if result.returncode != 0:
            return None
        return [line.split()[-1] for line in result.stdout.splitlines() if line.strip()]

    def _choose_by_number(self, squads, selected_ids):
        for number, squad in enumerate(squads, 1):
            mark = "*" if squad["id"] in selected_ids else " "
            self.say(f"{self.INDENT}{mark} {number}) {squad['title']}")
        answer = self.ask("Números dos squads, separados por vírgula (* = já selecionado)")
        numbers = [int(n) for n in re.split(r"[,\s]+", answer) if n.isdigit()]
        picked = [squads[n - 1]["id"] for n in numbers if 1 <= n <= len(squads)]
        return picked or None


MAX_WIDTH = 76
URL_FIND_RE = re.compile(r"https?://[^\s)]+")


def _rich_console():
    from rich.console import Console as RichConsole

    return RichConsole(stderr=True, highlight=False, width=min(shutil.get_terminal_size().columns, MAX_WIDTH))


@contextlib.contextmanager
def spinner(text):
    """Spinner enquanto algo demora (rede). Sem terminal, nao mostra nada."""
    if not (sys.stderr.isatty() and color_enabled()):
        yield
        return
    with _rich_console().status(text, spinner="dots"):
        yield


class PromptConsole(Console):
    """Mesma interface: caixas e separadores (rich) e setas, busca e campo oculto (questionary)."""

    def __init__(self):
        self._rich = None

    @property
    def rich(self):
        if self._rich is None:
            self._rich = _rich_console()
        return self._rich

    def header(self, title, subtitle=""):
        from rich import box
        from rich.panel import Panel
        from rich.text import Text

        body = Text(title, style="bold")
        if subtitle:
            body.append("\n" + subtitle, style="dim")
        self.rich.print(Panel(body, box=box.ROUNDED, border_style="cyan", padding=(0, 2)))

    def step(self, index, total, title):
        from rich.rule import Rule
        from rich.text import Text

        label = f"{index}/{total} · {title}" if total > 1 else title
        self.rich.print()
        self.rich.print(Rule(Text(label, style="bold cyan"), style="cyan", align="left"))

    def link(self, url):
        return url  # o rich torna clicavel em _line, sem sequencias de escape no meio do texto

    def _line(self, kind, style, text, text_style=""):
        from rich.text import Text

        body = Text(text, style=text_style)
        for match in URL_FIND_RE.finditer(text):
            body.stylize(f"underline cyan link {match.group()}", match.start(), match.end())
        self.rich.print(Text.assemble(self.INDENT, (glyph(kind), style), " ", body))

    def ok(self, text):
        self._line("ok", "green", text)

    def fail(self, text):
        self._line("fail", "red", text)

    def note(self, text):
        self._line("note", "dim", text, "dim")

    def warn(self, text):
        self._line("warn", "bold yellow", text)

    def summary(self, title, rows):
        from rich import box
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        table = Table.grid(padding=(0, 2))
        for label, value in rows:
            table.add_row(Text(label, style="dim"), Text(str(value)))  # Text: sem interpretar [colchetes]
        self.rich.print()
        self.rich.print(Panel(table, title=f"[bold green]{glyph('ok')} {title}[/]", title_align="left",
                              box=box.ROUNDED, border_style="green", padding=(0, 2)))

    def _questionary(self):
        import questionary
        from prompt_toolkit.output import create_output
        from prompt_toolkit.styles import Style

        style = Style([
            ("qmark", "fg:ansicyan bold"), ("question", "bold"), ("answer", "fg:ansicyan"),
            ("pointer", "fg:ansicyan bold"), ("highlighted", "fg:ansicyan bold"),
            ("selected", "fg:ansicyan"), ("instruction", "fg:ansibrightblack"),
            ("text", ""), ("disabled", "fg:ansibrightblack"),
        ])
        # as perguntas saem no stderr: o stdout pode estar sendo capturado pelo shell
        return questionary, {"style": style, "qmark": " ", "output": create_output(stdout=sys.stderr)}

    def _run(self, question):
        return question.unsafe_ask()  # Ctrl-C levanta KeyboardInterrupt: o comando inteiro cancela

    def ask(self, prompt, default=""):
        q, common = self._questionary()
        answer = self._run(q.text(f"{prompt}:", default=default, **common))
        return (answer or "").strip() or default

    def ask_secret(self, prompt):
        q, common = self._questionary()
        return (self._run(q.password(f"{prompt}:", **common)) or "").strip()

    def choose(self, prompt, options, default=0):
        """Rotulos alinhados com a dica ao lado. Dicas curtas: o eco da escolha leva o titulo inteiro
        e nao pode passar de uma linha (80 colunas)."""
        q, common = self._questionary()
        width = max(len(label) for label, _ in options)
        choices = [
            q.Choice(title=[("", label.ljust(width)), ("class:instruction", f"  {hint}" if hint else "")], value=index)
            for index, (label, hint) in enumerate(options)
        ]
        message = prompt if prompt.endswith("?") else f"{prompt}:"
        return self._run(q.select(
            message, choices=choices, default=choices[default], use_indicator=False,
            instruction=" ", **common,
        ))

    def confirm(self, prompt, default=True):
        return self.choose(prompt, [("Sim", ""), ("Não", "")], default=0 if default else 1) == 0

    def choose_squads(self, squads, selected_ids):
        q, common = self._questionary()
        choices = [q.Choice(title=s["title"], value=s["id"], checked=s["id"] in selected_ids) for s in squads]
        picked = self._run(q.checkbox(
            "Squads", choices=choices, instruction="(espaço marca, Enter confirma, digite para filtrar)",
            use_search_filter=True, use_jk_keys=False,
            validate=lambda chosen: True if chosen else "Marque ao menos um squad.",
            **common,
        ))
        # o questionary ecoa "done (N selections)" (em ingles, fixo): apaga a linha, o chamador confirma com ok()
        sys.stderr.write("\033[1A\033[2K\r")
        sys.stderr.flush()
        return picked or None


def make_console():
    """Console interativo so com terminal de verdade nas tres pontas."""
    if sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty():
        return PromptConsole()
    return Console()
