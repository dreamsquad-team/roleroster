"""Instala o Granted quando falta. Sempre com confirmacao, nunca em silencio.

macOS com Homebrew usa o comando oficial do brew. No Linux, e no macOS sem Homebrew, baixa o
binario da versao testada no GitHub, confere o SHA-256 (fixado aqui, dentro do pacote publicado
no PyPI) e copia `granted`, `assume` e `assumego` para ~/.local/bin, sem sudo.

Ao subir a versao testada, atualize VERSION e SHA256 (os hashes saem do checksums.txt da release).
"""
import hashlib
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

from . import ui

VERSION = "0.40.0"
DOCS_URL = "https://docs.granted.dev/getting-started"
RELEASE_URL = "https://github.com/fwdcloudsec/granted/releases/download/v{version}/{name}"
BREW_COMMANDS = (["brew", "tap", "fwdcloudsec/granted"], ["brew", "install", "fwdcloudsec/granted/granted"])

SHA256 = {  # (sistema, arquitetura) -> SHA-256 do granted_<VERSION>_<sistema>_<arquitetura>.tar.gz
    ("darwin", "arm64"): "55d2848b914f1ccf3c16f76630cd9679030a82f5110afe74d0ebb91287cdab8c",
    ("darwin", "x86_64"): "36fc177120d23c429aa602e81772578ab7e3e07373b6fa13e6f4d477660af515",
    ("linux", "arm64"): "0da965ca9b8200ce75794be246677a2f15909a9a87db89a9dc512aecee09a55d",
    ("linux", "x86_64"): "c55e2ccaf60ed810b5d389f558c9a48a35b356375d7fe6666b19b98695eda794",
}
FILES = ("granted", "assume", "assume.fish")  # so estes nomes saem do tar; o resto (assume.bat, ps1) nao serve aqui
MAX_BYTES = 60 * 1024 * 1024
TIMEOUT = 60


class InstallError(Exception):
    pass


def bin_dir():
    return Path.home() / ".local" / "bin"


def machine():
    system = platform.system().lower()
    arch = {"aarch64": "arm64", "amd64": "x86_64", "x64": "x86_64"}.get(platform.machine().lower(), platform.machine().lower())
    return system, arch


def tarball_name():
    system, arch = machine()
    return f"granted_{VERSION}_{system}_{arch}.tar.gz"


def method():
    """'brew', 'binary' ou '' quando nao ha instalacao automatica para este sistema."""
    if sys.platform == "darwin" and shutil.which("brew"):
        return "brew"
    return "binary" if machine() in SHA256 else ""


def describe(how):
    if how == "brew":
        return "brew tap fwdcloudsec/granted && brew install fwdcloudsec/granted/granted"
    name = tarball_name()
    return f"baixar o Granted {VERSION} ({name}) do GitHub, conferir o SHA-256 e copiar para {tilde(bin_dir())}"


def manual_hint():
    how = method()
    if how == "brew":
        return describe("brew")
    return f"veja {DOCS_URL}"


def tilde(path):
    home = str(Path.home())
    return "~" + str(path)[len(home):] if str(path).startswith(home) else str(path)


# ---------------------------------------------------------------- instalacao


def fetch(url):
    """Baixa um arquivo do GitHub (https) com limite de tamanho. Os testes trocam esta funcao."""
    if not url.startswith("https://github.com/"):
        raise InstallError(f"endereço inesperado: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "roleroster"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read(MAX_BYTES + 1)
    except OSError as error:
        raise InstallError(f"não consegui baixar o Granted ({error})") from None
    if len(data) > MAX_BYTES:
        raise InstallError("o arquivo baixado é maior que o esperado")
    return data


def _write_executable(path, content):
    tmp = path.with_name(path.name + ".novo")
    tmp.write_bytes(content)
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)


def install_binary(target=None):
    """Baixa, confere o SHA-256 e copia para `target` (padrao ~/.local/bin). Devolve a pasta."""
    data = fetch(RELEASE_URL.format(version=VERSION, name=tarball_name()))
    if hashlib.sha256(data).hexdigest() != SHA256[machine()]:
        raise InstallError("o SHA-256 do arquivo baixado não confere com o esperado; nada foi instalado")
    target = target or bin_dir()
    target.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            contents = {}
            for member in FILES:
                info = tar.getmember(member)
                if not info.isfile():
                    raise InstallError(f"{member} não é um arquivo comum dentro do pacote")
                contents[member] = tar.extractfile(info).read()
    except (tarfile.TarError, KeyError) as error:
        raise InstallError(f"o pacote do Granted veio num formato inesperado ({error})") from None
    for member, content in contents.items():
        _write_executable(target / member, content)
    link = target / "assumego"  # o assume chama o assumego: o brew faz o mesmo, um link para o granted
    link.unlink(missing_ok=True)
    link.symlink_to("granted")
    return target


def install_brew():
    for command in BREW_COMMANDS:
        done = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
            raise InstallError(f"`{' '.join(command)}` falhou: {' '.join(tail)}")


def install(how=None):
    """Instala pelo caminho certo para o sistema. Devolve o texto do que fez."""
    how = how or method()
    if how == "brew":
        install_brew()
        return "Granted instalado com o Homebrew"
    if how == "binary":
        folder = install_binary()
        return f"Granted {VERSION} instalado em {tilde(folder)}"
    raise InstallError(f"não há instalação automática para este sistema; veja {DOCS_URL}")


def on_path():
    return shutil.which("granted") is not None


def offer(console, assume_yes=False):
    """Pergunta e instala o Granted. Devolve True se ele esta instalado ao final."""
    from . import granted

    if granted.executable():
        return True
    console.warn("O Granted não está instalado. O roleroster precisa dele para o login e o assume.")
    how = method()
    if not how:
        console.note(f"Não há instalação automática para este sistema. Veja {DOCS_URL}")
        return False
    console.note(f"Instalação: {describe(how)}")
    if not assume_yes:
        if not console.interactive():
            console.note("Sem terminal para perguntar. Rode role init de novo num terminal, ou use role init --yes.")
            return False
        if not console.confirm("Instalar o Granted agora?"):
            console.note(f"Para instalar depois, rode role init de novo. Instalação manual: {manual_hint()}")
            return False
    try:
        with ui.spinner("Instalando o Granted"):
            message = install(how)
    except InstallError as error:
        console.fail(str(error))
        console.note(f"Instalação manual: {manual_hint()}")
        return False
    console.ok(message)
    if not on_path():
        console.warn(f"{tilde(bin_dir())} não está no PATH. Acrescente ao seu rc: export PATH=\"$PATH:{tilde(bin_dir())}\"")
    return True
