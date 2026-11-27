"""Navegadores do console e do login SSO, e o modo "visitante" do console.

Navegadores aceitos por enquanto: Chrome, Brave, Firefox e Edge.

Como cada conta abre no console (ROLEROSTER_CONSOLE_MODE):

  profile  um perfil fixo do navegador para todas as contas (--browser-profile do Granted)
  account  um perfil por conta, o padrao do Granted (cria um perfil novo para cada conta)
  guest    uma janela isolada a cada abertura, num diretorio de dados descartavel:
           nada entra no seletor de perfis e varias contas ficam abertas ao mesmo tempo,
           porque cada janela tem os proprios cookies (so Chrome, Brave e Edge)

Firefox nao usa nenhum desses: o Granted abre cada conta num container (extensao Granted).
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import paths

BROWSERS = (
    ("chrome", "Google Chrome"),
    ("brave", "Brave"),
    ("firefox", "Firefox"),
    ("edge", "Microsoft Edge"),
)
LABELS = dict(BROWSERS)
CHROMIUM_FAMILY = ("chrome", "brave", "edge")
FIREFOX_ADDON_URL = "https://addons.mozilla.org/en-GB/firefox/addon/granted"

MODES = (
    ("profile", "Perfil fixo", "um perfil para todas as contas"),
    ("guest", "Visitante", "janela isolada, sem perfil"),
    ("account", "Um perfil por conta", "padrão do Granted"),
)
DEFAULT_MODE = "profile"
DEFAULT_PROFILE_NAME = "AWS"
GUEST_MAX_AGE = 2 * 24 * 3600  # diretorios de janelas visitantes mais velhos que isto sao apagados

MAC_EXECUTABLES = {
    "chrome": "Google Chrome.app/Contents/MacOS/Google Chrome",
    "brave": "Brave Browser.app/Contents/MacOS/Brave Browser",
    "firefox": "Firefox.app/Contents/MacOS/firefox",
    "edge": "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
}
LINUX_COMMANDS = {
    "chrome": ("google-chrome", "google-chrome-stable"),
    "brave": ("brave-browser", "brave"),
    "firefox": ("firefox",),
    "edge": ("microsoft-edge", "microsoft-edge-stable"),
}


def find(key):
    """Caminho do executavel do navegador, ou None se nao estiver instalado."""
    if sys.platform == "darwin":
        for base in (Path("/Applications"), Path.home() / "Applications"):
            candidate = base / MAC_EXECUTABLES[key]
            if candidate.exists():
                return str(candidate)
        return None
    if sys.platform.startswith("linux"):
        for command in LINUX_COMMANDS[key]:
            found = shutil.which(command)
            if found:
                return found
    return None  # Windows: ainda nao suportado


def installed():
    """Lista de (chave, rotulo, caminho) dos navegadores encontrados, na ordem de BROWSERS."""
    found = []
    for key, label in BROWSERS:
        path = find(key)
        if path:
            found.append((key, label, path))
    return found


def mode_label(mode):
    return next((label for key, label, _ in MODES if key == mode), mode)


# ---------------------------------------------------------------- modo visitante


def guest_dir(base=None, now=None):
    """Cria o diretorio de dados descartavel de uma janela (permissao 700)."""
    base = base or paths.home() / "browser"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = int(now if now is not None else time.time())
    for attempt in range(1000):
        candidate = base / f"visitante-{stamp}-{os.getpid()}-{attempt}"
        try:
            candidate.mkdir(mode=0o700)
            return candidate
        except FileExistsError:
            continue
    raise OSError("não consegui criar o diretório da janela")


def launch_command(browser_path, data_dir, url):
    """Comando que abre `url` numa instancia nova do navegador, com dados proprios.

    No macOS, `open -n` abre uma instancia nova e traz a janela para a frente.
    """
    flags = [f"--user-data-dir={data_dir}", "--no-first-run", "--no-default-browser-check", url]
    if sys.platform == "darwin" and ".app/Contents/MacOS/" in browser_path:
        bundle = browser_path.split("/Contents/MacOS/")[0]
        return ["open", "-n", "-a", bundle, "--args", *flags]
    return [browser_path, *flags]


def cleanup_old(base=None, max_age=GUEST_MAX_AGE, now=None):
    """Apaga os diretorios de janelas visitantes antigas. Devolve quantos apagou."""
    base = base or paths.home() / "browser"
    if not base.is_dir():
        return 0
    limit = (now if now is not None else time.time()) - max_age
    removed = 0
    for entry in base.iterdir():
        if entry.name.startswith("visitante-") and entry.is_dir() and entry.stat().st_mtime < limit:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    return removed


def open_guest(url, browser_path, base=None):
    """Abre `url` numa janela visitante. So Chromium (Chrome, Brave, Edge)."""
    if not url.startswith("https://"):
        raise ValueError("a URL do console precisa começar com https://")
    cleanup_old(base)
    data_dir = guest_dir(base)
    subprocess.Popen(
        launch_command(browser_path, data_dir, url),
        start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return data_dir
