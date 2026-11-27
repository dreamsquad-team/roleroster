"""Ajuda do `role -h`: as mesmas secoes em dois formatos.

Com terminal, secoes como linhas de separacao e comandos em destaque (rich);
sem terminal (`role -h | less`), texto simples alinhado. A ajuda vai para o
stdout, diferente das mensagens, que vao para o stderr.
"""
import os
import re
import sys

from . import __version__

TAGLINE = "Perfis de Switch Role do Confluence no ~/.aws/config, com o Granted."
USAGE = "role <comando> [opções]"

# (titulo, nota, [(nome, descricao)])
SECTIONS = (
    ("Acesso", "exigem o shell ligado com role init", (
        ("sso [termos]", "sincroniza, escolhe e assume (renova sozinho, 12 h)"),
        ("console [termos]", "sincroniza, escolhe o perfil e abre o console da AWS"),
        ("assume <perfil>", "o assume do Granted, neste terminal"),
    )),
    ("Opções de sso e console", "", (
        ("-i, --inactives", "inclui os projetos inativos"),
        ("-S, --squads", "escolhe de quais squads listar os perfis"),
    )),
    ("Opções só do sso", "", (
        ("-n, --no-renew", "não renova as credenciais sozinho"),
        ("-U, --unset", "limpa o terminal e as credenciais"),
        ("-B, --setup", "refaz o trecho inicial do config"),
        ("-T, --status", "até quando a credencial vale"),
    )),
    ("Configuração", "", (
        ("sync [-i]", "atualiza o ~/.aws/config com os perfis do Confluence"),
        ("squads", "escolhe de quais squads listar os perfis"),
        ("setup [-a]", "refaz o trecho inicial do config (-a: todos os padrões)"),
        ("status", "até quando a credencial vale e se renova sozinha"),
        ("browser", "navegador do console e do login SSO, e como as contas abrem"),
        ("notices [on|off]", "liga ou desliga o aviso de renovação automática"),
        ("preset [origem]", "mostra ou instala o preset da empresa (-r remove)"),
        ("token", "troca o token do Confluence"),
        ("doctor [-f] [-o]", "confere a instalação e a configuração (-f conserta, -o testa o token)"),
        ("init", "liga o roleroster no seu shell (role init -h)"),
    )),
    ("Granted", "", (
        ("granted <comando>", "executa o comando do Granted (inclusive o doctor dele)"),
        ("<outro comando>", "o que não for do roleroster vai para o Granted"),
    )),
    ("Geral", "", (
        ("-h, --help", "esta ajuda"),
        ("-V, --version", "a versão"),
    )),
)


def command_names():
    """Primeira palavra de cada comando documentado (para os testes)."""
    return {re.split(r"[ ,]", name)[0] for _, _, rows in SECTIONS for name, _ in rows}


def render_plain():
    width = max(len(name) for _, _, rows in SECTIONS for name, _ in rows)
    lines = [f"roleroster {__version__}", TAGLINE, "", f"Uso: {USAGE}"]
    for title, note, rows in SECTIONS:
        lines += ["", f"{title}" + (f" ({note})" if note else "")]
        lines += [f"  {name.ljust(width)}  {description}" for name, description in rows]
    return "\n".join(lines) + "\n"


def render_rich(console):
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text

    console.print(Text.assemble(("roleroster ", "bold"), (__version__, "bold cyan")))
    console.print(Text(TAGLINE, style="dim"))
    console.print()
    console.print(Text.assemble(("Uso  ", "dim"), (USAGE, "bold")), end="\n\n")
    width = max(len(name) for _, _, rows in SECTIONS for name, _ in rows) + 2  # a mesma coluna em todas as secoes
    for title, note, rows in SECTIONS:
        label = Text(title, style="bold cyan")
        if note:
            label.append(f"  {note}", style="dim")
        console.print(Rule(label, style="cyan", align="left"))
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold", no_wrap=True, min_width=width)
        table.add_column()
        for name, description in rows:
            table.add_row(Text("  " + name), Text(description))
        console.print(table)
        console.print()


def show():
    if sys.stdout.isatty() and os.environ.get("TERM") != "dumb":
        import shutil

        from rich.console import Console

        render_rich(Console(highlight=False, width=min(shutil.get_terminal_size().columns, 76)))
    else:
        sys.stdout.write(render_plain())
