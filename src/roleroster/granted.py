"""Ajustes que o roleroster exige do Granted, em ~/.granted/config.

O fluxo depende de duas chaves:
  ExportCredsToAWS = true           o assume grava <perfil>-exported no arquivo de credenciais
  ExportCredentialSuffix = "exported"  sem ela o Granted pergunta o sufixo no meio do assume,
                                    o que travaria a renovacao automatica

O `granted settings set` so cobre a primeira; por isso a segunda e escrita no arquivo.
Num config novo, o proprio Granted cria o arquivo completo (com os padroes dele).
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

REQUIRED = {"ExportCredsToAWS": "true", "ExportCredentialSuffix": '"exported"'}


def config_path():
    return Path.home() / ".granted" / "config"


def _split_top_level(text):
    """Divide o TOML em (parte antes da primeira [secao], resto)."""
    match = re.search(r"^\[", text, re.MULTILINE)
    return (text, "") if not match else (text[:match.start()], text[match.start():])


def _setting_re(key):
    return re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(.*?)[ \t]*$", re.MULTILINE)


def current_value(text, key):
    top, _ = _split_top_level(text)
    match = _setting_re(key).search(top)
    return match.group(1) if match else None


def with_setting(text, key, value):
    top, rest = _split_top_level(text)
    line = f"{key} = {value}"
    if _setting_re(key).search(top):
        top = _setting_re(key).sub(lambda _: line, top, count=1)  # lambda: barras invertidas do caminho nao sao escapes
    else:
        body = top.rstrip("\n")
        top = (body + "\n" if body else "") + line + (top[len(body):] or "\n")
    return top + rest


def _write_private(path, text):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def executable():
    """Caminho do granted: no PATH ou em ~/.local/bin (onde o `role init` instala sem Homebrew)."""
    found = shutil.which("granted")
    local = Path.home() / ".local" / "bin" / "granted"
    return found or (str(local) if local.is_file() and os.access(local, os.X_OK) else None)


def _create_default_config():
    subprocess.run(
        [executable() or "granted", "settings", "set", "-s", "ExportCredsToAWS", "-v", "true"],
        capture_output=True, stdin=subprocess.DEVNULL, check=True,
    )


def ensure_settings():
    """Devolve as chaves que precisou ajustar. Sem o Granted instalado, nao faz nada."""
    if not executable():
        return []
    path = config_path()
    if not path.exists():
        _create_default_config()
    text = path.read_text()
    changed = []
    for key, value in REQUIRED.items():
        if current_value(text, key) != value:
            text = with_setting(text, key, value)
            changed.append(key)
    if changed:
        _write_private(path, text)
    return changed


def browser_missing():
    """True se o config do Granted ainda nao tem navegador (ele perguntaria no meio do login)."""
    if not executable():
        return False
    path = config_path()
    value = current_value(path.read_text(), "DefaultBrowser") if path.exists() else None
    return value in (None, "", '""')


def quote(text):
    """Texto como string do TOML."""
    return json.dumps(text, ensure_ascii=False)


def set_browsers(browser_key, browser_path, sso_path):
    """Grava o navegador do console e o do login SSO no config do Granted.

    Mesmo formato que o `granted browser set` produz (DefaultBrowser = "CHROME",
    CustomBrowserPath). O caminho do SSO e escrito aqui porque o `granted browser set-sso`
    nao grava nada sem as perguntas. Devolve False se o Granted nao esta instalado.
    """
    if not executable():
        return False
    ensure_settings()  # cria o config se for a primeira vez
    path = config_path()
    text = path.read_text()
    for key, value in (
        ("DefaultBrowser", quote(browser_key.upper())),
        ("CustomBrowserPath", quote(browser_path)),
        ("CustomSSOBrowserPath", quote(sso_path)),
    ):
        text = with_setting(text, key, value)
    _write_private(path, text)
    return True
