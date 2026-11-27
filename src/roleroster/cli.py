"""Comando `role` (e `roleroster`): ponto de entrada da lib.

Os comandos que mudam o terminal (sso, console, assume) vivem numa funcao de
shell, ligada pelo `role init`. Aqui ficam os demais; o que nao for do
roleroster vai para o Granted, entao `role <comando do granted>` funciona.
"""
import os
import shutil
import sys

from . import __version__, helptext, init, profiles

PROFILE_COMMANDS = ("sync", "squads", "setup", "status", "browser", "notices", "token", "doctor", "preset")
INTERNAL_COMMANDS = ("session-save", "pick", "prune-exported", "open-console")  # chamados pelo script de shell
SHELL_COMMANDS = ("sso", "console", "assume")
EXIT_USAGE = 2
GRANTED_INSTALL = "brew tap fwdcloudsec/granted && brew install fwdcloudsec/granted/granted"

def run_granted(args):
    granted = shutil.which("granted")
    if not granted:
        print(f"role: o Granted nao esta instalado. No macOS: {GRANTED_INSTALL}", file=sys.stderr)
        return 127
    os.execv(granted, [granted, *args])


def needs_shell(command):
    print(
        f"role {command} precisa do shell do roleroster (ele altera o terminal atual).\n"
        "Rode `role init`, abra um novo terminal e use `role "
        f"{command}` de novo.",
        file=sys.stderr,
    )
    return EXIT_USAGE


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        helptext.show()
        return 0
    command, rest = args[0], args[1:]
    if init.refresh_script():
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
