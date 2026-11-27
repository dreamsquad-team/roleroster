"""Leitura dos perfis de switchRole que ficam no Confluence.

Cada pagina de cliente tem uma secao (por padrao "Switch Role"; configuravel no preset) com um
bloco de codigo no formato da extensao AWS Extend Switch Roles:

    [nome-do-perfil]
    aws_account_id=123456789012
    role_name=nome-do-role
    region=us-east-1

Este modulo so le o Confluence. Quem escreve no ~/.aws/config e o
sync_aws_config.py.

Configuracao (arquivo .env na pasta do roleroster, ver paths.py; variaveis ja
exportadas no shell tem prioridade):
  CONFLUENCE_BASE_URL    ex.: https://suaempresa.atlassian.net
  CONFLUENCE_EMAIL       email da conta Atlassian
  CONFLUENCE_TOKEN       API token (id.atlassian.com > Security > API tokens); normalmente
                         fica no cofre do sistema (vault.py) e esta variavel so existe
                         onde nao ha cofre
  CONFLUENCE_ROOT_ID     pasta com uma pasta por squad
  CONFLUENCE_SQUAD_IDS   ids das pastas de squad escolhidas, separados por virgula

O onboarding.py pergunta e grava o que estiver faltando no primeiro uso.
"""
import base64
import html
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from html.parser import HTMLParser

from . import paths, settings, vault

ENV_PATH = paths.env_file()
REQUEST_TIMEOUT = 30  # segundos; quem chama no terminal reduz
BODY_BATCH = 50  # paginas por requisicao ao baixar o corpo

CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
CODE_BODY_TAG = "ac:plain-text-body"

# Chaves que o script guarda. Qualquer outra (external_id, senhas) e descartada.
KEEP_KEYS = {
    "role_arn",
    "aws_account_id",
    "role_name",
    "source_profile",
    "region",
    "color",
    "granted_color",
    "mfa_serial",
}


class ConfluenceError(Exception):
    """Falha esperada ao falar com o Confluence (config, rede, permissao)."""


class ConfluenceAuthError(ConfluenceError):
    """O Confluence recusou as credenciais (token expirado, revogado ou errado)."""


def unwrap_cdata(storage_html):
    """Troca `<![CDATA[x]]>` por x escapado.

    O HTMLParser trata CDATA de formas diferentes conforme a versao do Python.
    Escapar antes deixa o texto chegar sempre por handle_data.
    """
    return CDATA_RE.sub(lambda m: html.escape(m.group(1), quote=False), storage_html)


def parse_profiles(text):
    """Le texto no formato INI e devolve um dict por perfil."""
    profiles = []
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            if name.lower().startswith("profile "):
                name = name[len("profile "):].strip()
            current = {"profile": name}
            profiles.append(current)
            continue
        key, sep, value = line.partition("=")
        key = key.strip().lower()
        if current is not None and sep and key in KEEP_KEYS:
            current[key] = value.strip()
    return profiles


def with_role_arn(profile):
    """Completa role_arn a partir de aws_account_id e role_name, se faltar."""
    if "role_arn" not in profile and {"aws_account_id", "role_name"} <= profile.keys():
        profile = {
            **profile,
            "role_arn": f"arn:aws:iam::{profile['aws_account_id']}:role/{profile['role_name']}",
        }
    return profile


def role_section_re():
    """Titulo da secao com os perfis. O padrao ("Switch Role") tambem aceita "switch-role" e "switchrole"."""
    text = settings.get("role_section")
    if text.lower().replace(" ", "") in ("switchrole", "switch-role"):
        pattern = r"switch\s*-?\s*role"
    else:
        pattern = r"\s+".join(map(re.escape, text.split()))
    return re.compile(pattern, re.IGNORECASE)


def inactive_re():
    """Titulos que marcam projeto encerrado. Sem prefixo configurado, nada e inativo."""
    prefix = settings.get("inactive_prefix").strip()
    return re.compile(rf"^\s*{re.escape(prefix)}", re.IGNORECASE) if prefix else None


class SwitchRoleParser(HTMLParser):
    """Le os blocos de codigo que aparecem depois da mencao a "Switch Role".

    A mencao pode estar num titulo, em negrito ou no titulo de uma macro
    expand, entao o parser nao depende da tag. Depois dela, so conta bloco de
    codigo cujos perfis tenham role_arn (ou aws_account_id e role_name).
    Nao guarda o corpo da pagina.
    """

    def __init__(self, section_re=None):
        super().__init__(convert_charrefs=True)
        self.section_re = section_re or role_section_re()
        self.profiles = []
        self.section_found = False
        self.code_blocks = 0
        self.ignored_blocks = 0
        self._armed = False
        self._in_code = False
        self._code_buf = []

    def handle_starttag(self, tag, attrs):
        if tag == CODE_BODY_TAG:
            self._in_code = True
            self._code_buf = []

    def handle_endtag(self, tag):
        if tag == CODE_BODY_TAG and self._in_code:
            self._in_code = False
            if self._armed:
                self._add_block("".join(self._code_buf))

    def handle_data(self, data):
        if self._in_code:
            self._code_buf.append(data)
        elif self.section_re.search(data):
            self._armed = True
            self.section_found = True

    def _add_block(self, text):
        completed = [with_role_arn(p) for p in parse_profiles(text)]
        found = [p for p in completed if "role_arn" in p]
        if found:
            self.code_blocks += 1
            self.profiles.extend(found)
        else:
            self.ignored_blocks += 1


def extract_switch_roles(storage_html):
    parser = SwitchRoleParser()
    parser.feed(unwrap_cdata(storage_html))
    parser.close()
    return parser


def load_env_file(path=ENV_PATH):
    """Carrega KEY=VALUE do .env sem sobrescrever o que ja esta no ambiente.

    Aceita o prefixo `export` e ignora qualquer linha que nao seja atribuicao.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
            continue
        os.environ.setdefault(key, value.strip().strip("'\""))


def get_token():
    """O token vem da variavel de ambiente (que inclui o .env) ou do cofre do sistema."""
    return os.environ.get("CONFLUENCE_TOKEN") or vault.get_token()


def require_config():
    """Devolve (base_url, cabecalho de autenticacao) ou levanta ConfluenceError."""
    base = os.environ.get("CONFLUENCE_BASE_URL", "").rstrip("/")
    email = os.environ.get("CONFLUENCE_EMAIL", "")
    token = get_token()
    missing = [
        name
        for name, value in (
            ("CONFLUENCE_BASE_URL", base),
            ("CONFLUENCE_EMAIL", email),
            ("CONFLUENCE_TOKEN", token),
        )
        if not value
    ]
    if missing:
        raise ConfluenceError("faltando no .env: " + ", ".join(missing))
    return base, basic_auth(email, token)


def basic_auth(email, token):
    return base64.b64encode(f"{email}:{token}".encode()).decode()


def check_credentials(base, email, token):
    """Chamada leve que prova que e-mail e token funcionam. Levanta se nao."""
    api_get(f"{base}/wiki/rest/api/user/current", basic_auth(email, token))


def _request(url, auth):
    return urllib.request.Request(
        url, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"}
    )


def _credentials_work(url, auth):
    """True se e-mail e token funcionam (um 403 entao e falta de permissao de verdade).

    Em caso de duvida (rede, erro do servidor) devolve True, para nao pedir token
    a quem nao tem problema de token.
    """
    parts = urllib.parse.urlparse(url)
    probe = f"{parts.scheme}://{parts.netloc}/wiki/rest/api/user/current"
    try:
        with urllib.request.urlopen(_request(probe, auth), timeout=REQUEST_TIMEOUT):
            return True
    except urllib.error.HTTPError as error:
        error.close()
        return error.code not in (401, 403)
    except (urllib.error.URLError, TimeoutError):
        return True


def api_get(url, auth):
    """GET na API do Confluence.

    O Confluence Cloud responde 403 (nao 401) para token invalido, expirado ou
    e-mail errado, e tambem para falta de permissao. Por isso, num 403, uma
    chamada leve a user/current separa os dois casos: se ela tambem falha, sao
    credenciais ruins (ConfluenceAuthError); se funciona, e permissao.
    """
    try:
        with urllib.request.urlopen(_request(url, auth), timeout=REQUEST_TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        error.close()
        probing_credentials = urllib.parse.urlparse(url).path.endswith("/user/current")
        if error.code == 401 or (error.code == 403 and (probing_credentials or not _credentials_work(url, auth))):
            raise ConfluenceAuthError("o Confluence recusou o e-mail ou o token (expirado, revogado ou errado)") from None
        if error.code == 403:
            raise ConfluenceError("sem permissao para ler esse conteudo (HTTP 403)") from None
        raise ConfluenceError(f"Confluence respondeu HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise ConfluenceError(f"sem conexao com o Confluence ({type(error).__name__})") from None


def is_inactive_page(page):
    """Projeto encerrado: a pagina, ou uma pasta/pagina acima dela, comeca com o prefixo de inativos
    da configuracao (ex.: "Inativ"). Sem prefixo configurado, nenhuma pagina e inativa."""
    pattern = inactive_re()
    if pattern is None:
        return False
    titles = [page["title"]] + [a["title"] for a in page.get("ancestors", [])]
    return any(pattern.match(title) for title in titles)


def search_pages(base, auth, cql, expand=""):
    """Percorre a busca CQL seguindo a paginacao do Confluence."""
    query = {"cql": cql, "limit": 100}
    if expand:
        query["expand"] = expand
    url = f"{base}/wiki/rest/api/content/search?{urllib.parse.urlencode(query)}"
    while url:
        data = api_get(url, auth)
        yield from data["results"]
        next_path = data.get("_links", {}).get("next")
        url = f"{base}/wiki{next_path}" if next_path else None


def parse_ids(text):
    """Ids numericos separados por virgula. Qualquer outra coisa e descartada."""
    return [part for part in re.split(r"[,\s]+", text or "") if part.isdigit()]


def list_folders(base, auth, parent_id):
    """Pastas filhas diretas de parent_id (um squad por pasta), em ordem alfabetica."""
    cql = f"parent = {int(parent_id)} and type = folder"
    folders = [{"id": r["id"], "title": r["title"]} for r in search_pages(base, auth, cql)]
    return sorted(folders, key=lambda f: f["title"].lower())


def fetch_candidates(base, auth, squad_ids, include_inactive=False):
    """Le os perfis das paginas dos squads e devolve (perfis, contadores).

    Primeiro lista as paginas sem corpo (so titulos e ancestrais). Depois baixa
    o corpo apenas das que interessam: as de projetos inativos so entram com
    include_inactive, e sao a maior parte das paginas.
    """
    ids = parse_ids(",".join(squad_ids))
    if not ids:
        raise ConfluenceError("nenhum squad selecionado (rode: role squads)")
    stats = Counter()
    listed = search_pages(base, auth, f"type = page and ancestor in ({', '.join(ids)})", "ancestors,version")
    wanted = {}
    for page in listed:
        stats["listed"] += 1
        inactive = is_inactive_page(page)
        stats["inactive_pages"] += inactive
        if inactive and not include_inactive:
            stats["inactive_skipped"] += 1
        else:
            wanted[page["id"]] = (page, inactive)

    candidates = []
    page_ids = list(wanted)
    for start in range(0, len(page_ids), BODY_BATCH):
        batch = page_ids[start:start + BODY_BATCH]
        for body_page in search_pages(base, auth, f"type = page and id in ({', '.join(batch)})", "body.storage"):
            page, inactive = wanted[body_page["id"]]
            result = extract_switch_roles(body_page["body"]["storage"]["value"])
            stats["scanned"] += 1
            stats["with_section"] += result.section_found
            stats["with_profiles"] += bool(result.profiles)
            stats["blocks"] += result.code_blocks
            stats["ignored"] += result.ignored_blocks
            for profile in result.profiles:
                candidates.append(
                    {
                        "page_id": page["id"],
                        "page_title": page["title"],
                        "page_version": page["version"]["number"],
                        "inactive": inactive,
                        **profile,
                    }
                )
    return candidates, stats


# ---------------------------------------------------------------- .env


def _env_key(line):
    """Nome da variavel de uma linha do .env, ou None se nao for atribuicao."""
    text = line.strip()
    if text.startswith("export "):
        text = text[len("export "):]
    key, sep, _ = text.partition("=")
    key = key.strip()
    return key if sep and re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) else None


def save_env(updates, remove=(), path=ENV_PATH):
    """Grava variaveis no .env (permissao 600) e tambem no ambiente deste processo.

    Troca o valor onde a variavel ja existe, acrescenta o que for novo e apaga
    as de `remove`. O resto do arquivo fica como esta.
    """
    lines = path.read_text().splitlines() if path.exists() else []
    written = set()
    result = []
    for line in lines:
        key = _env_key(line)
        if key in remove:
            continue
        if key in updates:
            result.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            result.append(line.strip() if key else line)
    result += [f"{key}={value}" for key, value in updates.items() if key not in written]

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)  # primeira execucao: a pasta ainda nao existe
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(result) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
    os.environ.update(updates)
    for key in remove:
        os.environ.pop(key, None)
