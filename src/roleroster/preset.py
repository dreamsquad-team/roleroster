"""Preset: os valores de uma empresa, para o roleroster nao perguntar o que ela ja sabe.

Um arquivo TOML que a empresa distribui (um caminho ou uma URL https). `role preset <origem>` ou
`role init --preset <origem>` copia para a pasta do roleroster. Nada aqui e segredo: so URLs, nomes
e as convencoes das paginas do Confluence.

    name = "Acme"

    [aws]
    sso_start_url = "https://acme.awsapps.com/start"   # portal do IAM Identity Center
    sso_session = "acme-login"
    sso_region = "us-east-1"
    region = "us-east-1"
    base_profile = "ACME"            # perfil de onde saem os Switch Roles
    base_account_id = "123456789012"
    base_role = "AssumeRole"         # permission set do perfil base
    extra_role = "AdministratorAccess"  # permission set sugerido para perfis SSO extras

    [[aws.profiles]]                 # perfis SSO diretos que todos recebem (opcional, quantos quiser)
    name = "ACME---Sandbox"
    account_id = "210987654321"
    role = "AdministratorAccess"
    region = "us-east-1"             # opcional (padrao: a regiao dos perfis)

    [confluence]
    url = "https://acme.atlassian.net"
    root_id = "123456"               # pasta com uma pasta por squad
    role_section = "Switch Role"     # titulo da secao com o bloco de codigo
    inactive_prefix = "Inativ"       # paginas e pastas com este inicio de titulo ficam de fora

Todas as chaves sao opcionais. Os perfis de `[[aws.profiles]]` ficam num bloco proprio do config da AWS
que o sync reescreve a cada uso: mudar o preset muda os perfis de quem o instalou. Um perfil escrito a mao
com o mesmo nome vence.
"""
import os
import re
import tempfile
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from . import paths

MAX_BYTES = 64 * 1024
FETCH_TIMEOUT = 10

# secao -> chave do arquivo -> chave canonica usada por settings.py
SCHEMA = {
    "aws": {
        "sso_start_url": "sso_start_url", "sso_session": "sso_session", "sso_region": "sso_region",
        "region": "region", "base_profile": "base_profile", "base_account_id": "base_account_id",
        "base_role": "base_role", "extra_role": "extra_role",
    },
    "confluence": {
        "url": "confluence_url", "root_id": "root_id", "role_section": "role_section", "inactive_prefix": "inactive_prefix",
    },
}
URL_KEYS = ("sso_start_url", "confluence_url")
PROFILE_KEYS = ("name", "account_id", "role", "region")
NAME_RE = re.compile(r"[A-Za-z0-9_.@-]+")
ROLE_RE = re.compile(r"[\w+=,.@-]+")
REGION_RE = re.compile(r"[a-z]{2}(-[a-z]+)+-\d")
MAX_PROFILES = 50


class PresetError(Exception):
    pass


def installed_path():
    return paths.home() / "preset.toml"


def parse(text):
    """Texto TOML -> {"name", "values": {chave canonica: texto}, "profiles": [perfis], "ignored": [chaves desconhecidas]}."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise PresetError(f"o preset não é um TOML válido: {error}") from error
    values, ignored, profiles = {}, [], []
    for section, fields in data.items():
        if section == "name":
            continue
        if section not in SCHEMA or not isinstance(fields, dict):
            ignored.append(section)
            continue
        for key, value in fields.items():
            canonical = SCHEMA[section].get(key)
            if (section, key) == ("aws", "profiles"):
                profiles = parse_profiles(value)
            elif canonical is None:
                ignored.append(f"{section}.{key}")
            elif not isinstance(value, str):
                raise PresetError(f"{section}.{key} precisa ser um texto")
            elif value.strip():
                values[canonical] = value.strip()
    validate(values)
    check_profile_names(profiles, values.get("base_profile"))
    name = data.get("name")
    return {"name": name if isinstance(name, str) else "", "values": values, "profiles": profiles, "ignored": ignored}


def parse_profiles(items):
    """`[[aws.profiles]]` -> lista de {"name", "account_id", "role", "region"} (region pode ser vazio)."""
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise PresetError("aws.profiles precisa ser uma lista de tabelas: use [[aws.profiles]]")
    if len(items) > MAX_PROFILES:
        raise PresetError(f"aws.profiles aceita no máximo {MAX_PROFILES} perfis")
    profiles = []
    for index, item in enumerate(items, start=1):
        unknown = sorted(set(item) - set(PROFILE_KEYS))
        if unknown:
            raise PresetError(f"aws.profiles #{index}: chave desconhecida: {', '.join(unknown)}")
        fields = {key: item.get(key, "") for key in PROFILE_KEYS}
        if not all(isinstance(value, str) for value in fields.values()):
            raise PresetError(f"aws.profiles #{index}: todas as chaves precisam ser texto")
        fields = {key: value.strip() for key, value in fields.items()}
        label = fields["name"] or f"#{index}"
        if not NAME_RE.fullmatch(fields["name"]):
            raise PresetError(f"aws.profiles {label}: name precisa usar só letras, números e _ . @ -")
        if not re.fullmatch(r"\d{12}", fields["account_id"]):
            raise PresetError(f"aws.profiles {label}: account_id precisa ter 12 dígitos")
        if not ROLE_RE.fullmatch(fields["role"]):
            raise PresetError(f"aws.profiles {label}: role precisa ser o nome do permission set")
        if fields["region"] and not REGION_RE.fullmatch(fields["region"]):
            raise PresetError(f"aws.profiles {label}: region inválida")
        profiles.append(fields)
    return profiles


def check_profile_names(profiles, base_profile):
    seen = set()
    for profile in profiles:
        name = profile["name"]
        if name in seen:
            raise PresetError(f"aws.profiles: o nome {name} aparece duas vezes")
        if name == base_profile:
            raise PresetError(f"aws.profiles: {name} é o perfil base (base_profile)")
        seen.add(name)


def validate(values):
    for key in URL_KEYS:
        if key in values and not re.fullmatch(r"https://[^\s]+", values[key]):
            raise PresetError(f"{key} precisa ser uma URL https")
    if "base_account_id" in values and not re.fullmatch(r"\d{12}", values["base_account_id"]):
        raise PresetError("base_account_id precisa ter 12 dígitos")
    if "root_id" in values and not values["root_id"].isdigit():
        raise PresetError("root_id precisa ser numérico")


def read_source(source):
    """Le o texto de um caminho ou de uma URL https (no maximo 64 KB)."""
    if source.startswith("https://"):
        try:
            with urllib.request.urlopen(source, timeout=FETCH_TIMEOUT) as response:
                data = response.read(MAX_BYTES + 1)
        except (urllib.error.URLError, OSError) as error:
            raise PresetError(f"não consegui baixar o preset: {error}") from error
    elif source.startswith(("http://", "ftp://", "file://")):
        raise PresetError("use um caminho local ou uma URL https")
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise PresetError(f"arquivo não encontrado: {source}")
        data = path.read_bytes()[: MAX_BYTES + 1]
    if len(data) > MAX_BYTES:
        raise PresetError("o preset é grande demais (limite de 64 KB)")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PresetError("o preset precisa estar em UTF-8") from error


def describe(preset):
    """Linhas curtas do que o preset define, para o usuario conferir antes de aceitar."""
    values = preset["values"]
    labels = (("sso_start_url", "Portal SSO"), ("confluence_url", "Confluence"), ("base_profile", "Perfil base"),
              ("root_id", "Pasta raiz"), ("inactive_prefix", "Inativos começam com"))
    lines = [(label, values[key]) for key, label in labels if key in values]
    if preset["profiles"]:
        lines.append(("Perfis SSO", ", ".join(profile["name"] for profile in preset["profiles"])))
    return lines or [("Valores", f"{len(values)} definidos")]


def install(source):
    """Valida e copia o preset para a pasta do roleroster. Devolve o preset lido."""
    return install_text(read_source(source))


def install_text(text):
    preset = parse(text)
    target = installed_path()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix="preset.")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, target)
    return preset


def remove():
    existed = installed_path().exists()
    installed_path().unlink(missing_ok=True)
    return existed


def load_installed():
    """Valores do preset instalado ({} se nao ha ou se esta quebrado: nunca derruba o comando)."""
    path = installed_path()
    if not path.is_file():
        return {}
    try:
        return parse(path.read_text(encoding="utf-8"))["values"]
    except (PresetError, OSError):
        return {}


def load_installed_profiles():
    """Perfis `[[aws.profiles]]` do preset instalado ([] se nao ha ou se esta quebrado)."""
    path = installed_path()
    if not path.is_file():
        return []
    try:
        return parse(path.read_text(encoding="utf-8"))["profiles"]
    except (PresetError, OSError):
        return []


def install_with_confirmation(console, source, assume_yes=False):
    """Mostra o que o preset define, pede confirmacao e instala. Devolve True se instalou.

    O preset aponta o portal de login: ver o que ele define antes de aceitar protege de um preset
    desconhecido. Sem terminal, so instala com assume_yes.
    """
    text = read_source(source)
    parsed = parse(text)
    console.summary(f"Preset {parsed['name']}" if parsed["name"] else "Preset", describe(parsed))
    if parsed["ignored"]:
        console.note(f"Ignorado (esta versão não conhece): {', '.join(parsed['ignored'])}")
    if not assume_yes:
        if not console.interactive():
            raise PresetError("sem terminal para confirmar; use --yes")
        if not console.confirm("Instalar este preset?"):
            return False
    install_text(text)
    console.ok("Preset instalado" + (f": {parsed['name']}" if parsed["name"] else ""))
    return True
