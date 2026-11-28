"""Onde o roleroster guarda os seus arquivos.

Tudo fica numa pasta so (ROLEROSTER_HOME, ou a pasta de configuracao do usuario):

  .env        URL, e-mail, token e squads do Confluence (permissao 600)
  state/      sessao compartilhada entre terminais
  shell/      o script que o `role init` liga no .zshrc
  backup/     a versao anterior do rc do shell, guardada antes de o `role init` mexer nele
"""
import os
import sys
from pathlib import Path


def home():
    explicit = os.environ.get("ROLEROSTER_HOME")
    if explicit:
        return Path(explicit).expanduser()
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "roleroster"
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "roleroster"


def env_file():
    return home() / ".env"


def state_dir():
    return home() / "state"


def session_file():
    return state_dir() / "session"


def shell_dir():
    return home() / "shell"


def backup_dir():
    return home() / "backup"
