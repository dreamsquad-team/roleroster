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


@pytest.fixture(autouse=True)
def no_real_vault(monkeypatch):
    """Nenhum teste toca no Keychain de verdade: o cofre fica indisponivel, salvo se o teste instalar um falso."""
    from roleroster import vault

    monkeypatch.setattr(vault, "_keyring", lambda: None)
    yield


@pytest.fixture(autouse=True)
def clean_home():
    """Cada teste comeca com um HOME vazio."""
    for child in _HOME.iterdir():
        shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink()
    yield


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SANDBOX, ignore_errors=True)
