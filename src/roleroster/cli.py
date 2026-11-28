"""Comando `role` (e `roleroster`): ponto de entrada da lib.

Os comandos que mudam o terminal (sso, console, assume) vivem numa funcao de
shell, ligada pelo `role init`. Aqui ficam os demais; o que nao for do
roleroster vai para o Granted, entao `role <comando do granted>` funciona.
"""
import os
import sys

from . import __version__, granted, helptext, init, profiles

PROFILE_COMMANDS = ("sync", "squads", "setup", "status", "browser", "notices", "token", "doctor", "preset")
INTERNAL_COMMANDS = ("session-save", "pick", "prune-exported", "open-console")  # chamados pelo script de shell
SHELL_COMMANDS = ("sso", "console", "assume")
EXIT_USAGE = 2
GRANTED_INSTALL = "brew tap fwdcloudsec/granted && brew install fwdcloudsec/granted/granted"

def run_granted(args):
    path = granted.executable()
    if not path:
        print(f"role: o Granted nao esta instalado. Rode `role init` para instalar ou use: {GRANTED_INSTALL}", file=sys.stderr)
        return 127
    os.execv(path, [path, *args])


def needs_shell(command):
    print(
        f"role {command} precisa do shell do roleroster (ele altera o terminal atual).\n"
        "Se ainda não rodou, use `role init`. Depois abra um terminal novo (ou rode `source ~/.zshrc`,\n"
        "`source ~/.bash_profile` no bash do macOS, `source ~/.config/fish/config.fish` no fish)\n"
        f"e use `role {command}` de novo.",
        file=sys.stderr,
    )
    return EXIT_USAGE


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        helptext.show()
        return 0
    command, rest = args[0], args[1:]
    if init.refresh_script() and not (command == "init" and os.environ.get(init.LOADED_VAR)):
        print("role: script de shell atualizado; abra um novo terminal para usar a versao nova.", file=sys.stderr)
    if command in ("-V", "--version", "version"):
        print(f"roleroster {__version__}")
        return 0
    if command in PROFILE_COMMANDS + INTERNAL_COMMANDS:
        return profiles.main(args)
    if command == "init":
        return init.main(rest)
    if command in SHELL_COMMANDS:
        return needs_shell(command)
    if command == "granted":
        return run_granted(rest)
    return run_granted(args)
