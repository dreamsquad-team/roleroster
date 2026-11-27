"""Isola a suite inteira do computador de quem roda: nenhum teste toca em ~/.aws, ~/.granted,
~/.zshrc nem na pasta do roleroster.

O HOME temporario precisa existir antes de qualquer `import roleroster`, porque alguns
caminhos (como o .env) sao calculados na importacao.
"""
import os
import shutil
import tempfile
from pathlib import Path

import pytest

_SANDBOX = Path(tempfile.mkdtemp(prefix="roleroster-tests-"))
_HOME = _SANDBOX / "home"
_HOME.mkdir()
os.environ["HOME"] = str(_HOME)
os.environ["ROLEROSTER_HOME"] = str(_SANDBOX / "roleroster_home")
for _name in ("XDG_CONFIG_HOME", "ZDOTDIR", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_PROFILE"):
    os.environ.pop(_name, None)
# um terminal com o shell do roleroster ligado exporta ROLEROSTER_*, CONFLUENCE_* e GRANTED_*: nenhum pode vazar para os testes
for _name in [n for n in os.environ if n.startswith(("ROLEROSTER_", "CONFLUENCE_", "GRANTED_")) and n != "ROLEROSTER_HOME"]:
    os.environ.pop(_name)


@pytest.fixture(autouse=True)
def no_real_vault(monkeypatch):
    """Nenhum teste toca no Keychain de verdade: o cofre fica indisponivel, salvo se o teste instalar um falso."""
    from roleroster import vault

    monkeypatch.setattr(vault, "_keyring", lambda: None)
    yield


@pytest.fixture(autouse=True)
def home_survives_a_cleared_environment(monkeypatch):
    """Testes que zeram o ambiente (`clear=True`) apagam o HOME, e o `~` passaria a apontar para o usuario real:
    sem HOME, o `~` continua sendo o HOME temporario."""
    import posixpath

    real_expanduser = posixpath.expanduser

    def expanduser(path):
        text = str(path)
        if "HOME" not in os.environ and (text == "~" or text.startswith("~/")):
            return str(_HOME) + text[1:]
        return real_expanduser(path)

    monkeypatch.setattr(posixpath, "expanduser", expanduser)
    yield


@pytest.fixture(autouse=True)
def clean_home():
    """Cada teste comeca com um HOME vazio."""
    for child in _HOME.iterdir():
        shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink()
    yield


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SANDBOX, ignore_errors=True)
