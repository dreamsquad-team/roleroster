"""Primeira execucao e manutencao da configuracao: .env, token e squads.

O que o sync precisa para rodar fica no .env (permissao 600):

  AWS_CONFIG_FILE        arquivo de config da AWS que o roleroster preenche
  AWS_SHARED_CREDENTIALS_FILE  arquivo de credenciais da AWS (as exportadas)
  CONFLUENCE_BASE_URL    https://<empresa>.atlassian.net
  CONFLUENCE_EMAIL       e-mail da conta Atlassian
  CONFLUENCE_TOKEN       token de API
  CONFLUENCE_ROOT_ID     pasta com uma pasta por squad
  CONFLUENCE_SQUAD_IDS   ids dos squads escolhidos, separados por virgula

No primeiro uso, `ensure_ready` conduz em passos numerados (arquivos da AWS, acesso
SSO, Confluence, squads) e so pergunta o que ninguem consegue adivinhar; os
demais valores usam o padrao e podem ser mudados com `role setup --advanced`.
Se o token expirar, `renew_token` pede outro. `choose_squads` e o seletor de
squads (`role squads` chama de novo).

As perguntas e mensagens saem pelo console (ver ui.py), que usa o stderr para
nao misturar com o stdout dos comandos que o shell captura.
"""
import os
import re
from pathlib import Path

from . import awsconfig as sync
from . import browsers, confluence, granted, settings, vault
from .confluence import ConfluenceAuthError, ConfluenceError
from .ui import plural

TOKEN_URL = "https://id.atlassian.com/manage-profile/security/api-tokens"
MAX_ATTEMPTS = 3
FILE_KEYS = ("AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE")
ISOLATED_CONFIG = "config_roleroster"
ISOLATED_CREDENTIALS = "credentials_roleroster"
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
ACCOUNT_RE = re.compile(r"\d{12}")
URL_RE = re.compile(r"https://\S+")
PROFILE_NAME_RE = sync.PROFILE_NAME_RE


# ---------------------------------------------------------------- passos


class Steps:
    """Numera os passos de uma configuracao: `1/4  Arquivos da AWS`."""

    def __init__(self, console, titles):
        self.console = console
        self.titles = titles  # chave -> titulo, na ordem em que acontecem
        self.done = 0

    def begin(self, key):
        self.done += 1
        self.console.step(self.done, len(self.titles), self.titles[key])


TITLE_FILES = "Arquivos da AWS"
TITLE_SSO = "Acesso AWS SSO"
TITLE_CONFLUENCE = "Confluence"
TITLE_SQUADS = "Squads"
TITLE_BROWSER = "Navegador"


def aws_dir():
    return Path.home() / ".aws"


def tilde(path):
    """~/... para mostrar nos prompts (reconhece o HOME com e sem links simbolicos)."""
    text = str(path)
    for home in (Path.home(), Path.home().resolve()):
        prefix = str(home)
        if text == prefix or text.startswith(prefix + os.sep):
            return "~" + text[len(prefix):]
    return text


def seed_config(console, target):
    """Arquivo novo nao tem sso-session nem perfis base: oferece copiar do config padrao."""
    standard = aws_dir() / "config"
    if target.exists() or not standard.exists() or standard.resolve() == target.resolve():
        return
    before, after = sync.split_managed(standard.read_text())
    seed = before + (after or "")
    if not seed.strip():
        return  # nada escrito a mao para copiar
    if not console.confirm("Copiar do seu ~/.aws/config o que foi escrito à mão (sem os perfis do roleroster)?"):
        return
    sync.write_atomic(target, seed)
    console.ok(f"Copiado para {tilde(target)} (o ~/.aws/config não foi alterado)")


def base_exists(config, name):
    return config.exists() and any(p["profile"] == name for p in confluence.parse_profiles(config.read_text()))


def render_initial_section(session, start_url, sso_region, region, profiles):
    """Trecho que fica antes de ACCOUNTS: sso-session, perfil base e perfis SSO diretos.

    O primeiro de `profiles` e o perfil base (de onde saem os Switch Roles) e usa
    o credential_process do Granted; os demais sao perfis SSO diretos.
    """
    lines = [
        "# ------------------ SSO SESSION DEFINITION ------------------",
        f"[sso-session {session}]",
        f"sso_start_url = {start_url}",
        f"sso_region = {sso_region}",
        "sso_registration_scopes = sso:account:access",
        "",
        f"# ------------------ {profiles[0]['name']} ------------------",
    ]
    for index, profile in enumerate(profiles):
        lines += [
            f"[profile {profile['name']}]",
            f"sso_session = {session}",
            f"sso_region = {sso_region}",
            f"sso_account_id = {profile['account_id']}",
            f"sso_role_name = {profile['role_name']}",
            f"region = {region}",
            "output = json",
        ]
        if index == 0:
            lines.append(f"credential_process = granted credential-process --profile {profile['name']}")
        lines.append("")
    return "\n".join(lines)


def insert_initial_section(text, section):
    """Poe o trecho inicial no comeco do arquivo, ou logo acima de ACCOUNTS se ele ja existir."""
    if sync.ACCOUNTS_HEADER in text:
        cut = text.index(sync.ACCOUNTS_HEADER)
        head = text[:cut].rstrip("\n")
        return (head + "\n\n" if head else "") + section + "\n" + text[cut:]
    return section + ("\n" + text if text.strip() else "")


def ask_account_id(console, prompt):
    for _ in range(MAX_ATTEMPTS):
        answer = console.ask(prompt)
        if ACCOUNT_RE.fullmatch(answer):
            return answer
        console.fail("O ID da conta AWS tem 12 dígitos.")
    raise ConfluenceError("ID de conta inválido")


def ask_required(console, prompt, valid, message):
    """Pergunta ate vir uma resposta valida (3 tentativas); sem padrao: nao ha como adivinhar."""
    for _ in range(MAX_ATTEMPTS):
        answer = console.ask(prompt)
        if valid(answer):
            return answer
        console.fail(message)
    raise ConfluenceError(f"{prompt}: resposta inválida")


def ask_start_url(console):
    """URL do portal SSO: o preset sabe; sem preset, so o usuario."""
    known = settings.from_user_or_preset("sso_start_url")
    if known:
        return known
    console.note("Exemplo: https://<empresa>.awsapps.com/start")
    return ask_required(console, "URL do portal SSO",
                        lambda answer: bool(URL_RE.fullmatch(answer)), "Use a URL completa, começando com https://")


def ask_extra_profiles(console):
    """Perfis SSO diretos (ex.: Sandbox, Production), so no modo avancado."""
    profiles = []
    while console.confirm("Adicionar outro perfil SSO direto (ex.: Sandbox, Production)?", default=False):
        name = console.ask("Nome do perfil")
        if not name or not PROFILE_NAME_RE.fullmatch(name):
            console.fail("Nome inválido: use letras, números e _ . @ -")
            continue
        profiles.append({
            "name": name,
            "account_id": ask_account_id(console, f"ID da conta de {name} (12 dígitos)"),
            "role_name": console.ask(f"Role (permission set) de {name}", default=settings.get("extra_role")),
        })
    return profiles


def ask_initial_section(console, config, path, advanced=False):
    """Pergunta o minimo do trecho que fica antes de ACCOUNTS e grava no config.

    O que o preset da empresa traz nao e perguntado. Sem preset, pergunta a URL do portal SSO, a conta
    e o role do perfil base; sessao, regioes e nome usam o padrao. Com `advanced`, pergunta tudo.
    """
    session, sso_region = settings.get("sso_session"), settings.get("sso_region")
    start_url = ask_start_url(console)
    if advanced:
        session = console.ask("Nome da sessão SSO", default=session)
        sso_region = console.ask("Região do SSO", default=sso_region)
    region = settings.from_user_or_preset("region") or sso_region  # herda a regiao do SSO, salvo escolha em contrario
    if advanced:
        region = console.ask("Região padrão dos perfis", default=region)
    base_name = settings.get("base_profile")
    if advanced:
        base_name = console.ask("Nome do perfil base", default=base_name)

    account_id = settings.from_user_or_preset("base_account_id")
    if not ACCOUNT_RE.fullmatch(account_id):
        account_id = ask_account_id(console, "ID da conta base (12 dígitos)")
    known_role = settings.from_user_or_preset("base_role")
    if advanced:
        role = console.ask("Role (permission set) do perfil base", default=known_role)
    else:
        role = known_role or ask_required(console, "Role (permission set) do perfil base", bool, "Informe o nome do role.")
    profiles = [{"name": base_name, "account_id": account_id, "role_name": role}]
    if advanced:
        profiles += ask_extra_profiles(console)

    section = render_initial_section(session, start_url, sso_region, region, profiles)
    sync.write_atomic(config, insert_initial_section(config.read_text() if config.exists() else "", section))
    confluence.save_env({"ROLEROSTER_SOURCE_PROFILE": base_name}, path=path)
    console.ok(f"Perfil base {base_name} criado ({region}, role {role})")


def ensure_initial_section(console, path, force=False, advanced=False, steps=None):
    """Garante o perfil base no config; pergunta so se faltar (ou se force)."""
    config = Path(os.environ["AWS_CONFIG_FILE"])
    name = sync.source_profile()
    if base_exists(config, name):
        if force:
            console.note(f"O perfil base {name} já existe em {tilde(config)}. Edite o trecho acima de ACCOUNTS à mão.")
        return
    if not force and (os.environ.get("ROLEROSTER_SOURCE_PROFILE") or not console.interactive()):
        return  # ja respondeu antes (ou nao ha terminal): nao insiste
    (steps or Steps(console, {"sso": TITLE_SSO})).begin("sso")
    ask_initial_section(console, config, path, advanced=advanced)


FILE_CHOICES = (
    ("Próprios do roleroster", f"~/.aws/{ISOLATED_CONFIG}"),
    ("Os da AWS", "~/.aws/config"),
    ("Outro caminho", ""),
)


def ask_aws_files(console, path, missing):
    """Escolhe os arquivos da AWS. O primeiro item deixa o seu ~/.aws/config intacto."""
    standard = aws_dir() / "config"
    if set(missing) == set(FILE_KEYS):
        options = list(FILE_CHOICES)
        if standard.exists() and sync.BEGIN in standard.read_text():
            options[1] = (options[1][0], "já tem perfis do roleroster")
        choice = console.choose("Onde gravar os perfis?", options)
        if choice == 0:
            config, credentials = aws_dir() / ISOLATED_CONFIG, aws_dir() / ISOLATED_CREDENTIALS
        elif choice == 1:
            config, credentials = standard, aws_dir() / "credentials"
        else:
            config = Path(console.ask("Arquivo de config da AWS")).expanduser()
            suggested = tilde(config.parent / ISOLATED_CREDENTIALS)
            credentials = Path(console.ask("Arquivo de credenciais da AWS", default=suggested)).expanduser()
        _save_files(path, config, credentials)
    else:
        _ask_missing_file(console, path, missing)
    seed_config(console, Path(os.environ["AWS_CONFIG_FILE"]))


def _save_files(path, config, credentials):
    confluence.save_env({
        "AWS_CONFIG_FILE": str(config.resolve()),
        "AWS_SHARED_CREDENTIALS_FILE": str(credentials.resolve()),
    }, path=path)


def _ask_missing_file(console, path, missing):
    """So um dos dois arquivos faltava no .env (edicao manual): pergunta so ele."""
    if "AWS_CONFIG_FILE" in missing:
        answer = console.ask("Arquivo de config da AWS", default=tilde(aws_dir() / ISOLATED_CONFIG))
        confluence.save_env({"AWS_CONFIG_FILE": str(Path(answer).expanduser().resolve())}, path=path)
    config = Path(os.environ["AWS_CONFIG_FILE"])
    if "AWS_SHARED_CREDENTIALS_FILE" in missing:
        standard_pair = config == (aws_dir() / "config").resolve()
        default = aws_dir() / "credentials" if standard_pair else config.parent / ISOLATED_CREDENTIALS
        answer = console.ask("Arquivo de credenciais da AWS", default=tilde(default))
        confluence.save_env({"AWS_SHARED_CREDENTIALS_FILE": str(Path(answer).expanduser().resolve())}, path=path)


def ask_browser(console, path):
    """Navegador do console e do login SSO, e como as contas abrem no console.

    Grava a escolha no config do Granted (antes do primeiro uso, em vez de o Granted
    perguntar no meio do login) e as preferencias no .env.
    """
    found = browsers.installed()
    if not found:
        console.note("Nenhum navegador compatível encontrado (Chrome, Brave, Firefox ou Edge).")
        console.note("Instale um e rode role browser.")
        confluence.save_env({"ROLEROSTER_BROWSER": "none"}, path=path)
        return
    if len(found) == 1:
        console_browser = found[0]
        console.note(f"{console_browser[1]} é o único navegador compatível encontrado.")
    else:
        console_browser = found[console.choose("Navegador do console da AWS", [(label, "") for _, label, _ in found])]
        options = [("O mesmo do console", "")] + [(label, "") for _, label, _ in found]
        picked = console.choose("Navegador do login SSO", options)
        sso_browser = console_browser if picked == 0 else found[picked - 1]
    sso_browser = console_browser if len(found) == 1 else sso_browser

    key, label, executable = console_browser
    mode, profile_name = browsers.DEFAULT_MODE, ""
    if key == "firefox":
        console.note(f"O Firefox abre cada conta num container e precisa da extensão do Granted: {browsers.FIREFOX_ADDON_URL}")
    else:
        mode = browsers.MODES[console.choose(
            "Como abrir as contas?", [(name, hint) for _, name, hint in browsers.MODES]
        )][0]
        if mode == "profile":
            profile_name = console.ask("Nome do perfil do navegador", default=browsers.DEFAULT_PROFILE_NAME)

    if not granted.set_browsers(key, executable, sso_browser[2]):
        console.fail("O Granted não está instalado; instale e rode role browser para aplicar a escolha.")
    confluence.save_env({
        "ROLEROSTER_BROWSER": key,
        "ROLEROSTER_BROWSER_PATH": executable,
        "ROLEROSTER_SSO_BROWSER": sso_browser[0],
        "ROLEROSTER_CONSOLE_MODE": mode,
        "ROLEROSTER_CONSOLE_PROFILE": profile_name,
    }, path=path)
    if key == "firefox":
        how = "container (Granted)"
    else:
        how = browsers.mode_label(mode).lower() + (f' "{profile_name}"' if profile_name else "")
    console.ok(f"Console no {label}: {how}")
    console.ok(f"Login SSO no {sso_browser[1]}")


def normalize_base_url(text):
    """Aceita a URL completa, com /wiki no fim, ou so o nome da empresa."""
    text = text.strip().rstrip("/")
    if text.endswith("/wiki"):
        text = text[: -len("/wiki")].rstrip("/")
    if re.fullmatch(r"[A-Za-z0-9-]+", text):
        text = f"https://{text}.atlassian.net"
    return text if re.fullmatch(r"https://[A-Za-z0-9.-]+", text) else None


def ask_base_url(console, path):
    known = settings.from_user_or_preset("confluence_url")
    if known:  # o preset da empresa ja diz qual e o site
        confluence.save_env({"CONFLUENCE_BASE_URL": known}, path=path)
        return
    while True:
        answer = console.ask("URL do site (ou só o nome da empresa)")
        url = normalize_base_url(answer)
        if url:
            break
        console.fail("URL inválida. Use https://<empresa>.atlassian.net ou só o nome da empresa.")
    confluence.save_env({"CONFLUENCE_BASE_URL": url}, path=path)


def ask_email(console, path):
    while True:
        email = console.ask("E-mail da conta Atlassian")
        if EMAIL_RE.fullmatch(email):
            break
        console.fail("E-mail inválido.")
    confluence.save_env({"CONFLUENCE_EMAIL": email}, path=path)


def recheck_email(console, email, path):
    """Depois de um token recusado, o e-mail digitado errado e a causa mais comum: mostra o atual e deixa corrigir."""
    while True:
        answer = console.ask("E-mail da conta Atlassian (Enter mantém)", default=email)
        if EMAIL_RE.fullmatch(answer):
            break
        console.fail("E-mail inválido.")
    if answer != email:
        confluence.save_env({"CONFLUENCE_EMAIL": answer}, path=path)
        os.environ["CONFLUENCE_EMAIL"] = answer
    return answer


def store_token(console, token, path):
    """Guarda o token no cofre do sistema; sem cofre, no .env (permissao 600).

    Guardado no cofre, o token sai do .env, para nao ficar nos dois lugares.
    """
    if vault.set_token(token):
        confluence.save_env({}, remove=("CONFLUENCE_TOKEN",), path=path)
        console.note(f"Token guardado no {vault.name()}.")
        return
    confluence.save_env({"CONFLUENCE_TOKEN": token}, path=path)
    console.note("Cofre do sistema indisponível: token guardado no .env (permissão 600).")


def migrate_token_to_vault(path):
    """Move o token do .env para o cofre do sistema. Devolve True se moveu."""
    confluence.load_env_file(path)
    token = os.environ.get("CONFLUENCE_TOKEN", "")
    if not token or not vault.set_token(token):
        return False
    confluence.save_env({}, remove=("CONFLUENCE_TOKEN",), path=path)
    return True


def reapply_browser():
    """Regrava no Granted o navegador que o .env guarda, se o config dele nao o tem.

    Acontece quando o Granted foi instalado depois da escolha, ou o ~/.granted foi apagado: sem isto
    o Granted faz as perguntas de primeira execucao dele no meio do `role sso`. Devolve True se gravou.
    """
    key = os.environ.get("ROLEROSTER_BROWSER", "")
    if not key or key == "none" or not granted.browser_missing():
        return False
    path = os.environ.get("ROLEROSTER_BROWSER_PATH") or browsers.find(key)
    sso_path = browsers.find(os.environ.get("ROLEROSTER_SSO_BROWSER") or key) or path
    return bool(path) and granted.set_browsers(key, path, sso_path)


def ask_token(console, path):
    """Pede o token, testa no Confluence e so grava se funcionar.

    Enter em branco abre a pagina que gera o token (uma vez); so recusas do
    Confluence contam como tentativa.
    """
    base = os.environ["CONFLUENCE_BASE_URL"]
    email = os.environ["CONFLUENCE_EMAIL"]
    console.note(f"Gere em {console.link(TOKEN_URL)}")
    console.note('Use "Create API token" (sem scopes). Enter em branco abre o link.')
    opened = False
    rejected = 0
    while rejected < MAX_ATTEMPTS:
        token = console.ask_secret("Token")
        if not token:
            if opened:
                console.fail("Token vazio.")
            else:
                opened = True
                console.open_url(TOKEN_URL)
                console.note("Abri o link no navegador. Cole o token aqui quando tiver.")
            continue
        try:
            confluence.check_credentials(base, email, token)
        except ConfluenceAuthError:
            rejected += 1
            console.fail(f"O Confluence recusou o token ({rejected}/{MAX_ATTEMPTS}).")
            if rejected < MAX_ATTEMPTS:
                email = recheck_email(console, email, path)
            continue
        except ConfluenceError as error:
            store_token(console, token, path)
            console.note(f"Não consegui validar agora ({error}); token salvo mesmo assim.")
            return
        console.ok("Conectado ao Confluence")
        store_token(console, token, path)
        return
    raise ConfluenceAuthError(f"token recusado {MAX_ATTEMPTS} vezes; confira CONFLUENCE_EMAIL e CONFLUENCE_BASE_URL no .env")


def renew_token(console, reason, path=confluence.ENV_PATH):
    """Chamado quando o token expirou: explica e pede outro."""
    console.fail(reason)
    ask_token(console, path)


def ask_root_id(console, default=""):
    console.note("O ID aparece na URL da pasta (depois de /folder/ ou /pages/).")
    for _ in range(MAX_ATTEMPTS):
        answer = console.ask("ID da pasta que contém as pastas dos squads", default=default)
        if answer.isdigit():
            return answer
        console.fail("O ID é numérico.")
    raise ConfluenceError("ID da pasta inválido")


def choose_squads(console, path=confluence.ENV_PATH, advanced=False):
    """Seletor de squads: lista as pastas da raiz, marca as atuais e grava a escolha.

    A raiz vem da resposta salva ou do preset; sem nenhum dos dois (ou no modo avancado), pergunta.
    """
    base, auth = confluence.require_config()
    root = settings.from_user_or_preset("root_id")
    if advanced or not root:
        root = ask_root_id(console, root)
    current = confluence.parse_ids(os.environ.get("CONFLUENCE_SQUAD_IDS", ""))

    squads = confluence.list_folders(base, auth, root)
    if not squads:
        raise ConfluenceError(f"nenhuma pasta encontrada em {root}; informe outra raiz com role setup --advanced")
    chosen = console.choose_squads(squads, current)
    if not chosen:
        raise ConfluenceError("nenhum squad selecionado, nada foi alterado")

    confluence.save_env(
        {"CONFLUENCE_ROOT_ID": root, "CONFLUENCE_SQUAD_IDS": ",".join(chosen)}, path=path
    )
    names = [s["title"] for s in squads if s["id"] in chosen]
    console.ok(f"{plural(len(names), 'squad')}: {', '.join(names)}")
    return chosen


def migrate_legacy(path):
    """CONFLUENCE_PARENT_ID (uma pasta so) vira CONFLUENCE_SQUAD_IDS."""
    legacy = os.environ.get("CONFLUENCE_PARENT_ID")
    if legacy and not os.environ.get("CONFLUENCE_SQUAD_IDS"):
        confluence.save_env({"CONFLUENCE_SQUAD_IDS": legacy}, remove=("CONFLUENCE_PARENT_ID",), path=path)
    elif legacy:
        confluence.save_env({}, remove=("CONFLUENCE_PARENT_ID",), path=path)


def sso_step_needed(missing_files):
    """O passo do SSO acontece se o perfil base ainda nao existe e nunca foi respondido."""
    if os.environ.get("ROLEROSTER_SOURCE_PROFILE"):
        return False
    return bool(missing_files) or not base_exists(Path(os.environ["AWS_CONFIG_FILE"]), sync.source_profile())


def ensure_ready(console, need_squads=True, path=confluence.ENV_PATH, advanced=False):
    """Garante que o .env esteja completo, perguntando so o que faltar.

    Fora de um terminal interativo nao pergunta: levanta ConfluenceError.
    """
    confluence.load_env_file(path)
    migrate_legacy(path)
    missing_files = [key for key in FILE_KEYS if not os.environ.get(key)]
    missing_confluence = [k for k in ("CONFLUENCE_BASE_URL", "CONFLUENCE_EMAIL") if not os.environ.get(k)]
    if not confluence.get_token():
        missing_confluence.append("CONFLUENCE_TOKEN")
    missing_squads = need_squads and not os.environ.get("CONFLUENCE_SQUAD_IDS")
    missing = missing_files + missing_confluence + (["CONFLUENCE_SQUAD_IDS"] if missing_squads else [])
    missing_browser = console.interactive() and not os.environ.get("ROLEROSTER_BROWSER")  # opcional: nunca bloqueia
    if not missing:
        ensure_initial_section(console, path, advanced=advanced)
        if missing_browser:
            Steps(console, {"browser": TITLE_BROWSER}).begin("browser")
            ask_browser(console, path)
        return
    if not console.interactive():
        raise ConfluenceError(f"faltando no .env: {', '.join(missing)} (rode role sso em um terminal para configurar)")

    titles = {}
    if missing_files:
        titles["files"] = TITLE_FILES
    if sso_step_needed(missing_files):
        titles["sso"] = TITLE_SSO
    if missing_browser:
        titles["browser"] = TITLE_BROWSER
    if missing_confluence:
        titles["confluence"] = TITLE_CONFLUENCE
    if missing_squads:
        titles["squads"] = TITLE_SQUADS
    if len(titles) > 1:
        console.header("roleroster · primeira configuração", "Cerca de 2 minutos. Dá para refazer depois com `role setup`.")
    steps = Steps(console, titles)

    if missing_files:
        steps.begin("files")
        ask_aws_files(console, path, missing_files)
    ensure_initial_section(console, path, advanced=advanced, steps=steps)
    if missing_browser:
        steps.begin("browser")
        ask_browser(console, path)
    if missing_confluence:
        steps.begin("confluence")
        if "CONFLUENCE_BASE_URL" in missing_confluence:
            ask_base_url(console, path)
        if "CONFLUENCE_EMAIL" in missing_confluence:
            ask_email(console, path)
        if "CONFLUENCE_TOKEN" in missing_confluence:
            ask_token(console, path)
    chosen = []
    if missing_squads:
        steps.begin("squads")
        chosen = choose_squads(console, path, advanced=advanced)
    if len(titles) > 1:
        show_summary(console, chosen)


def show_summary(console, chosen_squads):
    """Resumo no fim de uma primeira configuracao completa."""
    rows = [
        ("Config", tilde(os.environ["AWS_CONFIG_FILE"])),
        ("Credenciais", tilde(os.environ["AWS_SHARED_CREDENTIALS_FILE"])),
        ("Perfil base", sync.source_profile()),
        ("Confluence", os.environ.get("CONFLUENCE_BASE_URL", "")),
        ("Squads", plural(len(chosen_squads), "selecionado") if chosen_squads else "já definidos"),
    ]
    console.summary("Configuração concluída", rows)
