"""Oferece instalar a AWS CLI (opcional). Nunca em silencio e nunca com `--yes` sozinho.

macOS com Homebrew usa `brew install awscli`. No Linux baixa o instalador oficial da versao fixada
aqui, confere o SHA-256 (fixado no pacote publicado no PyPI) e roda o `aws/install` dele em
~/.local/aws-cli, com o link em ~/.local/bin, sem sudo. Nos demais casos mostra o guia da AWS.

Ao subir a versao, atualize VERSION e SHA256 (baixe `awscli-exe-linux-<arq>-<versao>.zip` e rode shasum -a 256).
"""
import hashlib
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import ui
from .granted_install import InstallError, bin_dir, machine, tilde

VERSION = "2.42.5"
DOCS_URL = "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
ZIP_URL = "https://awscli.amazonaws.com/awscli-exe-linux-{arch}-{version}.zip"
SHA256 = {  # (sistema, arquitetura) -> SHA-256 do awscli-exe-linux-<arq>-<VERSION>.zip
    ("linux", "x86_64"): "7694a5b0bdd31ddccdbe9c103849cfe1a2aa4b4768e803a50ae94610aa970800",
    ("linux", "arm64"): "57cb17bdd0327a5685a12d3255b7a3b223f639f566e934ef3d2fa4557bd71675",
}
AWS_ARCH = {"x86_64": "x86_64", "arm64": "aarch64"}  # o nome da AWS para a arquitetura arm
MAX_BYTES = 150 * 1024 * 1024
MAX_UNPACKED = 1024 * 1024 * 1024
MAX_ENTRIES = 50000
TIMEOUT = 120


def install_dir():
    return Path.home() / ".local" / "aws-cli"


def executable():
    """Caminho do aws: no PATH ou em ~/.local/bin (onde o instalador oficial o liga)."""
    found = shutil.which("aws")
    local = bin_dir() / "aws"
    return found or (str(local) if local.is_file() and os.access(local, os.X_OK) else None)


def method():
    """'brew', 'zip' ou '' quando nao ha instalacao automatica para este sistema."""
    if sys.platform == "darwin":
        return "brew" if shutil.which("brew") else ""
    return "zip" if machine() in SHA256 else ""


def zip_name():
    _, arch = machine()
    return f"awscli-exe-linux-{AWS_ARCH[arch]}-{VERSION}.zip"


def describe(how):
    if how == "brew":
        return "brew install awscli"
    return f"baixar a AWS CLI {VERSION} da AWS, conferir o SHA-256 e instalar em {tilde(install_dir())}"


def manual_hint():
    return "brew install awscli" if method() == "brew" else f"veja {DOCS_URL}"


# ---------------------------------------------------------------- instalacao


def fetch(url):
    """Baixa o instalador (https, so da AWS) com limite de tamanho. Os testes trocam esta funcao."""
    if not url.startswith("https://awscli.amazonaws.com/"):
        raise InstallError(f"endereço inesperado: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "roleroster"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read(MAX_BYTES + 1)
    except OSError as error:
        raise InstallError(f"não consegui baixar a AWS CLI ({error})") from None
    if len(data) > MAX_BYTES:
        raise InstallError("o arquivo baixado é maior que o esperado")
    return data


def inside(folder, path):
    return path == folder or folder in path.parents


def extract_zip(data, destination):
    """Descompacta o zip guardando as permissoes. Recusa caminhos que saiam da pasta e arquivos demais."""
    destination = Path(destination).resolve()
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise InstallError("o instalador da AWS CLI veio num formato inesperado") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ENTRIES or sum(info.file_size for info in infos) > MAX_UNPACKED:
            raise InstallError("o instalador da AWS CLI é maior do que o esperado")
        for info in infos:
            target = (destination / info.filename).resolve()
            if not inside(destination, target):
                raise InstallError(f"caminho suspeito dentro do instalador: {info.filename}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                link = archive.read(info).decode()
                if os.path.isabs(link) or not inside(destination, (target.parent / link).resolve()):
                    raise InstallError(f"link suspeito dentro do instalador: {info.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(link)
            elif info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(info))
                target.chmod((mode & 0o777) or 0o644)


def install_zip():
    _, arch = machine()
    data = fetch(ZIP_URL.format(arch=AWS_ARCH[arch], version=VERSION))
    if hashlib.sha256(data).hexdigest() != SHA256[machine()]:
        raise InstallError("o SHA-256 do arquivo baixado não confere com o esperado; nada foi instalado")
    with tempfile.TemporaryDirectory(prefix="roleroster-awscli-") as tmp:
        extract_zip(data, tmp)
        bin_dir().mkdir(parents=True, exist_ok=True)
        command = [str(Path(tmp) / "aws" / "install"), "-i", str(install_dir()), "-b", str(bin_dir()), "--update"]
        done = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
        raise InstallError(f"o instalador da AWS CLI falhou: {' '.join(tail)}")


def install_brew():
    done = subprocess.run(["brew", "install", "awscli"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
        raise InstallError(f"`brew install awscli` falhou: {' '.join(tail)}")


def install(how=None):
    """Instala pelo caminho certo para o sistema. Devolve o texto do que fez."""
    how = how or method()
    if how == "brew":
        install_brew()
        return "AWS CLI instalada com o Homebrew"
    if how == "zip":
        install_zip()
        return f"AWS CLI {VERSION} instalada em {tilde(install_dir())}"
    raise InstallError(f"não há instalação automática para este sistema; veja {DOCS_URL}")


def on_path():
    return shutil.which("aws") is not None


def offer(console, choice=None):
    """Pergunta se instala a AWS CLI. Devolve True se ela esta instalada ao final.

    choice: True instala sem perguntar (`--aws-cli`), False nao oferece (`--no-aws-cli`), None pergunta.
    Sem terminal e sem escolha explicita, so avisa: a AWS CLI e opcional.
    """
    if executable():
        return True
    if choice is False:
        return False
    how = method()
    if not how:
        console.note(f"AWS CLI (opcional) não instalada. Sem instalação automática aqui, veja {DOCS_URL}")
        return False
    if choice is None:
        if not console.interactive():
            console.note("AWS CLI (opcional) não instalada. role init --aws-cli instala.")
            return False
        console.note(f"AWS CLI (opcional, para usar os perfis depois do login). Instalação: {describe(how)}")
        if not console.confirm("Instalar a AWS CLI agora?"):
            console.note(f"Para instalar depois: role init --aws-cli. Manual: {manual_hint()}")
            return False
    try:
        with ui.spinner("Instalando a AWS CLI"):
            message = install(how)
    except (InstallError, subprocess.TimeoutExpired) as error:
        console.fail(str(error) or "a instalação demorou demais")
        console.note(f"Instalação manual: {manual_hint()}")
        return False
    console.ok(message)
    if not on_path():
        console.warn(f"{tilde(bin_dir())} não está no PATH. Acrescente ao seu rc: export PATH=\"$PATH:{tilde(bin_dir())}\"")
    return True
