"""Comandos de terminal por tras do `role`: sync, squads, setup, status, sessao e busca de perfil.

  roleroster sync [-i]            atualiza o ~/.aws/config com o Confluence
  roleroster squads               escolhe de quais squads listar os perfis
  roleroster setup                preenche o trecho inicial do config (sso-session e perfil base)
  roleroster status               mostra ate quando a credencial vale e se renova sozinha
  roleroster session-save P EXP   grava a sessao compartilhada (usado pelo sso e pela renovacao)
  roleroster pick [termos...]     escolhe um perfil (fzf) e imprime o nome
  roleroster prune-exported [P]   limpa os perfis *-exported do credentials, menos o P

No primeiro uso o `sync` pergunta o que faltar no .env (URL, e-mail, token e
squads). Fora de um terminal interativo ele nao pergunta. O `sync` nunca
derruba o comando: se o Confluence falhar, avisa e o config atual continua
valendo; so cancelar a configuracao (Ctrl-C) interrompe, com saida 130. Os
avisos vao para o stderr; o stdout fica livre para o nome do perfil escolhido.

Projetos inativos (paginas dentro de uma pasta "Inativos ...") ficam fora por
padrao; `sync -i` / `sync --inactives` os traz de volta.

Codigos de saida do `pick`: 0 escolheu, 1 nada encontrado, 130 cancelado,
3 fzf nao instalado (o shell cai no seletor do Granted).
"""
import argparse
import contextlib
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import awsconfig as sync
from . import browsers, confluence, doctor, granted, granted_install, onboarding, paths, preset, settings, ui
from .confluence import ConfluenceAuthError, ConfluenceError

EXPORT_SUFFIX = "-exported"
SYNC_TIMEOUT = 8  # segundos por requisicao ao Confluence
EXIT_NO_FZF = 3
EXIT_CANCELLED = 130
SESSION_FILE = paths.session_file()
RENEW_WINDOW = 12 * 3600  # a renovacao automatica vale por ate 12 h depois do sso
DEFAULT_LIFETIME = 3600  # se o assume nao informar a expiracao, assume 1 h

ARN_RE = re.compile(r"arn:aws:iam::(\d{12}):role/([\w+=,.@/-]+)")
HEADING_RE = re.compile(r"^#\s*-{3,}\s*(.*?)\s*-*\s*$")
SECTION_RE = re.compile(r"^\[(.+)\]$")
MAX_TITLE_WIDTH = 30
INACTIVE_MARK = "[inativo]"  # o sync_aws_config.py escreve isto no cabecalho do grupo
RED = "\033[31m"
RESET = "\033[0m"


def default_credentials_path():
    """~/.aws/credentials, ou o arquivo de AWS_SHARED_CREDENTIALS_FILE."""
    return Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or Path.home() / ".aws" / "credentials").expanduser()


def config_path(args):
    """--aws-config, ou AWS_CONFIG_FILE (que o onboarding pode ter acabado de gravar)."""
    return Path(args.aws_config) if args.aws_config else sync.default_config_path()


def credentials_path(args):
    return Path(args.credentials) if args.credentials else default_credentials_path()


def warn(message):
    print(message, file=sys.stderr)


# ---------------------------------------------------------------- sync


def with_token_renewal(console, action, label=None):
    """Roda action(). Se o token falhar num terminal, pede outro e tenta uma vez mais.

    Com `label`, mostra um spinner enquanto action() roda (so quando action nao pergunta nada).
    """
    def run():
        with ui.spinner(label) if label else contextlib.nullcontext():
            return action()

    try:
        return run()
    except ConfluenceAuthError as error:
        if not console.interactive():
            raise
        onboarding.renew_token(console, f"O token do Confluence não vale mais: {error}.")
        return run()


def cmd_sync(args, console=None):
    console = console or ui.make_console()
    confluence.REQUEST_TIMEOUT = SYNC_TIMEOUT
    try:
        onboarding.ensure_ready(console)
        target = config_path(args)
        squad_ids = confluence.parse_ids(os.environ["CONFLUENCE_SQUAD_IDS"])
        plan = with_token_renewal(
            console,
            lambda: sync.plan_sync(target, sync.source_profile(), squad_ids, args.inactives),
            label="Sincronizando com o Confluence",
        )
        if plan.has_changes:
            sync.write_config(target, plan)
    except ConfluenceError as error:
        warn(ui.fail_line(f"Sync ignorado, usando o config atual: {error}"))
        return 0
    selection = plan.selection
    summary = f"{ui.plural(len(selection.profiles), 'perfil', 'perfis')} · {ui.plural(len(squad_ids), 'squad')}"
    if plan.has_changes:
        summary += f" · +{plan.added} -{plan.removed} ~{plan.changed}"
    if plan.stats["inactive_skipped"]:
        summary += f" · {plan.stats['inactive_skipped']} inativos fora (-i)"
    warn(ui.ok_line(summary))
    for label, names in (
        ("nome inválido", selection.invalid),
        ("já existirem fora do bloco", selection.reserved),
        ("mesmo nome com ARNs diferentes", selection.conflicts),
    ):
        if names:
            warn(ui.note_line(f"Ignorados por {label}: {', '.join(names)}"))
    return 0


def cmd_squads(args, console=None):
    """Abre o seletor de squads, grava a escolha e roda o sync."""
    console = console or ui.make_console()
    if not console.interactive():
        warn(ui.fail_line("Escolher os squads exige um terminal interativo."))
        return 1
    confluence.REQUEST_TIMEOUT = SYNC_TIMEOUT
    try:
        onboarding.ensure_ready(console, need_squads=False)
        with_token_renewal(console, lambda: onboarding.choose_squads(console))
    except ConfluenceError as error:
        warn(ui.fail_line(f"Squads não alterados: {error}"))
        return 1
    return cmd_sync(argparse.Namespace(aws_config=args.aws_config, inactives=False), console)


def cmd_setup(args, console=None):
    """Preenche o trecho inicial do config (sso-session e perfil base).

    Com --advanced pergunta tambem os valores que normalmente usam o padrao
    (sessao, regioes, nome e role do perfil base, perfis extras e a pasta raiz do Confluence).
    """
    console = console or ui.make_console()
    advanced = getattr(args, "advanced", False)
    if not console.interactive():
        warn(ui.fail_line("O setup exige um terminal interativo."))
        return 1
    try:
        onboarding.ensure_ready(console, need_squads=False, advanced=advanced)
        onboarding.ensure_initial_section(console, confluence.ENV_PATH, force=True, advanced=advanced)
        if advanced:
            root = onboarding.ask_root_id(console, settings.from_user_or_preset("root_id"))
            confluence.save_env({"CONFLUENCE_ROOT_ID": root}, path=confluence.ENV_PATH)
    except ConfluenceError as error:
        warn(ui.fail_line(f"Setup interrompido: {error}"))
        return 1
    return 0


def cmd_browser(args, console=None):
    """Escolhe o navegador do console e do login SSO e como as contas abrem no console."""
    console = console or ui.make_console()
    if not console.interactive():
        warn(ui.fail_line("Escolher o navegador exige um terminal interativo."))
        return 1
    onboarding.Steps(console, {"browser": onboarding.TITLE_BROWSER}).begin("browser")
    onboarding.ask_browser(console, confluence.ENV_PATH)
    return 0


def cmd_preset(args, console=None):
    """Mostra o preset instalado, instala um (caminho ou URL https) ou remove."""
    console = console or ui.make_console()
    if args.remove:
        warn(ui.ok_line("Preset removido") if preset.remove() else ui.note_line("Não havia preset instalado."))
        return 0
    if not args.source:
        values, extra = preset.load_installed(), preset.load_installed_profiles()
        if not values and not extra:
            warn(ui.note_line("Nenhum preset instalado. Instale com role preset <caminho ou URL https>."))
            return 0
        console.summary("Preset instalado", preset.describe({"values": values, "profiles": extra}))
        return 0
    try:
        installed = preset.install_with_confirmation(console, args.source, assume_yes=args.yes)
    except preset.PresetError as error:
        warn(ui.fail_line(str(error)))
        return 1
    if not installed:
        warn(ui.note_line("Nada foi instalado."))
        return 1
    return 0


def cmd_notices(args):
    """Liga ou desliga a linha de andamento da renovacao automatica ("Renovando as credenciais...")."""
    enabled = os.environ.get("ROLEROSTER_RENEW_NOTICES", "1") != "0"
    if args.state is None:
        state = "ligados" if enabled else "desligados"
        warn(ui.note_line(f"Avisos de renovação {state}. Mude com role notices on ou role notices off."))
        return 0
    turn_on = args.state == "on"
    confluence.save_env({"ROLEROSTER_RENEW_NOTICES": "1" if turn_on else "0"}, path=confluence.ENV_PATH)
    if turn_on:
        warn(ui.ok_line("Avisos de renovação ligados"))
    else:
        warn(ui.ok_line("Avisos de renovação desligados (falhas e o fim da janela de 12 h continuam aparecendo)"))
    return 0


def cmd_doctor(args, console=None):
    """Confere a instalacao e a configuracao e diz como consertar."""
    confluence.load_env_file()
    return doctor.run(console or ui.make_console(), fix=args.fix, online=args.online)


def cmd_token(args, console=None):
    """Troca o token do Confluence (valida antes de guardar)."""
    console = console or ui.make_console()
    if not console.interactive():
        warn(ui.fail_line("Trocar o token exige um terminal interativo."))
        return 1
    if not (os.environ.get("CONFLUENCE_BASE_URL") and os.environ.get("CONFLUENCE_EMAIL")):
        warn(ui.fail_line("Configure o Confluence primeiro: rode role sso."))
        return 1
    try:
        onboarding.ask_token(console, confluence.ENV_PATH)
    except ConfluenceError as error:
        warn(ui.fail_line(str(error)))
        return 1
    return 0


def cmd_open_console(args, stdin=None):
    """Interno: abre a URL do console (lida do stdin) numa janela visitante.

    A URL carrega credenciais temporarias; por isso vem pelo stdin e nao como argumento.
    """
    url = (stdin or sys.stdin).readline().strip()
    browser_path = os.environ.get("ROLEROSTER_BROWSER_PATH", "")
    if not browser_path or not Path(browser_path).exists():
        warn(ui.fail_line("Navegador não configurado para o modo visitante. Rode role browser."))
        return 1
    try:
        browsers.open_guest(url, browser_path)
    except (ValueError, OSError) as error:
        warn(ui.fail_line(f"Não consegui abrir a janela visitante: {error}"))
        return 1
    return 0


# ---------------------------------------------------------------- sessao e renovacao


def parse_expiration(text):
    """Epoch de uma expiracao ISO 8601 (2026-11-26T05:31:01-03:00, com Z ou fracao). None se nao entender."""
    text = re.sub(r"\.\d+", "", (text or "").strip()).replace("Z", "+00:00")
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return int(moment.timestamp())


def read_session(path):
    session = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.partition("=")
            if sep:
                session[key.strip()] = value.strip()
    return session


def write_session(path, session):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_private(path, "".join(f"{key}={value}\n" for key, value in session.items()))


def clock(epoch):
    return datetime.datetime.fromtimestamp(epoch).strftime("%H:%M")


def humanize(seconds):
    minutes = max(0, int(seconds // 60))
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def describe_session(session, now):
    """Texto de uma linha: ate quando a credencial vale e se renova sozinha."""
    expires, renew_until = int(session["expires"]), int(session["renew_until"])
    text = f"{session['profile']}: credencial válida até {clock(expires)} ({humanize(expires - now)})"
    if session.get("renew") != "1":
        return text + "; renovação automática desligada"
    if renew_until <= now:
        return text + "; a janela de renovação automática terminou (rode role sso)"
    return text + f"; renova sozinha até {clock(renew_until)} (role sso -n desliga)"


def cmd_session_save(args):
    """Grava a sessao compartilhada. --renewed mantem a janela e a escolha de renovar."""
    now = int(time.time())
    expires = parse_expiration(args.expiration) or now + DEFAULT_LIFETIME
    if args.renewed:
        previous = read_session(SESSION_FILE)
        renew_until = int(previous.get("renew_until", now + RENEW_WINDOW))
        renew = previous.get("renew", "1")
    else:
        renew_until = now + RENEW_WINDOW
        renew = "0" if args.no_renew else "1"
    session = {"profile": args.profile, "expires": expires, "renew_until": renew_until, "renew": renew}
    write_session(SESSION_FILE, session)
    if not args.renewed:
        warn(ui.ok_line(describe_session({k: str(v) for k, v in session.items()}, now)))
    elif os.environ.get("ROLEROSTER_RENEW_NOTICES", "1") != "0":  # renovacao e rotina: respeita `role notices`
        warn(ui.ok_line(f"{args.profile} renovado, válido até {clock(expires)} ({humanize(expires - now)})"))
    return 0


def cmd_status(args):
    session = read_session(SESSION_FILE)
    if not session.get("expires"):
        warn(ui.note_line("Nenhuma sessão compartilhada (rode role sso)."))
        return 1
    warn(ui.ok_line(describe_session(session, int(time.time()))))
    return 0


# ---------------------------------------------------------------- pick


def read_entries(config_text):
    """Perfis do config para o seletor, sem os `*-exported`.

    O titulo de cada perfil e o cabecalho `# --- Titulo` mais proximo acima.
    Se o cabecalho termina em `[inativo]`, o perfil e marcado como inativo e o
    marcador sai do titulo.
    """
    entries = []
    title = ""
    inactive = False
    current = None
    for raw in config_text.splitlines():
        line = raw.strip()
        heading = HEADING_RE.match(line)
        if heading:
            title = heading.group(1)
            inactive = title.endswith(INACTIVE_MARK)
            if inactive:
                title = title[: -len(INACTIVE_MARK)].rstrip()
            current = None
            continue
        if not line or line[0] in "#;":
            continue
        section = SECTION_RE.match(line)
        if section:
            name = section.group(1).strip()
            current = None
            if name.startswith("profile ") and not name.endswith(EXPORT_SUFFIX):
                current = {
                    "name": name[len("profile "):].strip(),
                    "title": title,
                    "inactive": inactive,
                    "keys": {},
                }
                entries.append(current)
            continue
        key, sep, value = line.partition("=")
        if current is not None and sep:
            current["keys"][key.strip().lower()] = value.strip()
    return entries


def detail(entry):
    keys = entry["keys"]
    match = ARN_RE.search(keys.get("role_arn", ""))
    if match:
        account, role = match.group(1), match.group(2).rsplit("/", 1)[-1]
    else:
        account, role = keys.get("sso_account_id", ""), keys.get("sso_role_name", "")
    return "  ".join(part for part in (account, role, keys.get("region", "")) if part)


def format_lines(entries, color=False):
    """Uma linha por perfil: nome, [inativo], titulo da pagina, conta, role e regiao.

    A coluna [inativo] so existe quando ha algum inativo na lista, e vai em
    vermelho (codigo ANSI, lido pelo fzf com --ansi) quando color=True.
    """
    name_width = max((len(e["name"]) for e in entries), default=0)
    title_width = min(MAX_TITLE_WIDTH, max((len(e["title"]) for e in entries), default=0))
    show_mark = any(e["inactive"] for e in entries)
    lines = []
    for e in entries:
        mark = ""
        if show_mark:
            text = INACTIVE_MARK if e["inactive"] else " " * len(INACTIVE_MARK)
            mark = f"{RED}{text}{RESET}" if color and e["inactive"] else text
            mark += "  "
        title = e["title"][:title_width]
        lines.append(f"{e['name']:<{name_width}}  {mark}{title:<{title_width}}  {detail(e)}".rstrip())
    return lines


def exact_match(terms, entries):
    return terms[0] if len(terms) == 1 and any(e["name"] == terms[0] for e in entries) else None


def run_fzf(lines, query, list_only):
    if list_only:
        command = ["fzf", "--ansi", "--filter", query]
    else:
        command = [
            "fzf", "--ansi", "--height=60%", "--reverse", "--prompt=perfil> ",
            "--header=digite parte do nome, cliente, conta ou role", "--query", query,
            "--select-1", "--exit-0",
        ]
    return subprocess.run(command, input="\n".join(lines), stdout=subprocess.PIPE, text=True)


def cmd_pick(args):
    entries = read_entries(config_path(args).read_text())
    chosen = exact_match(args.terms, entries)
    if chosen:
        print(chosen)
        return 0
    if not shutil.which("fzf"):
        return EXIT_NO_FZF
    candidates = format_lines(entries, color=not os.environ.get("NO_COLOR"))
    result = run_fzf(candidates, " ".join(args.terms), args.list)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if result.returncode == 0 and lines:
        names = [line.split(None, 1)[0] for line in lines]
        print("\n".join(names) if args.list else names[0])
        return 0
    if result.returncode == 1:
        hint = "" if args.inactives else " (projetos inativos so aparecem com -i)"
        warn(f"nenhum perfil encontrado para: {' '.join(args.terms)}{hint}")
        return 1
    return EXIT_CANCELLED


# ---------------------------------------------------------------- prune


def prune_exported(text, keep):
    """Remove do credentials as secoes `*-exported`, menos `keep`."""
    kept = []
    skipping = False
    for line in text.splitlines(keepends=True):
        section = SECTION_RE.match(line.strip())
        if section:
            name = section.group(1).strip()
            skipping = name.endswith(EXPORT_SUFFIX) and name != keep
        if not skipping:
            kept.append(line)
    return re.sub(r"\n{3,}", "\n\n", "".join(kept)).lstrip("\n")


def write_private(path, text):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".credentials.")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def cmd_prune(args):
    path = credentials_path(args)
    if not path.exists():
        return 0
    old_text = path.read_text()
    new_text = prune_exported(old_text, args.keep)
    if new_text != old_text or (path.stat().st_mode & 0o777) != 0o600:
        write_private(path, new_text)
    return 0


# ---------------------------------------------------------------- cli


def build_parser():
    parser = argparse.ArgumentParser(prog="roleroster", description=__doc__.splitlines()[0])
    parser.add_argument("--aws-config", help="padrao: AWS_CONFIG_FILE ou ~/.aws/config")
    commands = parser.add_subparsers(dest="command", required=True)

    inactives_help = "inclui os projetos inativos (por padrao ficam de fora)"
    sync_parser = commands.add_parser("sync")
    sync_parser.add_argument("-i", "--inactives", action="store_true", help=inactives_help)
    sync_parser.set_defaults(run=cmd_sync)

    commands.add_parser("squads").set_defaults(run=cmd_squads)
    setup_parser = commands.add_parser("setup")
    setup_parser.add_argument("-a", "--advanced", action="store_true", help="pergunta tambem os valores que usam o padrao")
    setup_parser.set_defaults(run=cmd_setup)
    commands.add_parser("status").set_defaults(run=cmd_status)
    commands.add_parser("browser").set_defaults(run=cmd_browser)
    preset_parser = commands.add_parser("preset")
    preset_parser.add_argument("source", nargs="?", help="caminho ou URL https do arquivo TOML")
    preset_parser.add_argument("-r", "--remove", action="store_true", help="remove o preset instalado")
    preset_parser.add_argument("-y", "--yes", action="store_true", help="instala sem pedir confirmação")
    preset_parser.set_defaults(run=cmd_preset)
    notices = commands.add_parser("notices")
    notices.add_argument("state", nargs="?", choices=("on", "off"))
    notices.set_defaults(run=cmd_notices)
    commands.add_parser("open-console").set_defaults(run=cmd_open_console)
    commands.add_parser("token").set_defaults(run=cmd_token)
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("-f", "--fix", action="store_true", help="aplica os consertos automáticos")
    doctor_parser.add_argument("-o", "--online", action="store_true", help="testa o token no Confluence")
    doctor_parser.set_defaults(run=cmd_doctor)

    save = commands.add_parser("session-save")
    save.add_argument("profile")
    save.add_argument("expiration", nargs="?", default="")
    save.add_argument("--renewed", action="store_true", help="renovacao: mantem a janela e a escolha de renovar")
    save.add_argument("--no-renew", action="store_true", help="desliga a renovacao automatica desta sessao")
    save.set_defaults(run=cmd_session_save)

    pick = commands.add_parser("pick")
    pick.add_argument("--inactives", action="store_true", help="so muda a dica quando nada e encontrado")
    pick.add_argument("terms", nargs="*")
    pick.add_argument("--list", action="store_true", help="so lista os que combinam, sem tela")
    pick.set_defaults(run=cmd_pick)

    prune = commands.add_parser("prune-exported")
    prune.add_argument("keep", nargs="?", default="")
    prune.add_argument("--credentials", help="padrao: AWS_SHARED_CREDENTIALS_FILE ou ~/.aws/credentials")
    prune.set_defaults(run=cmd_prune)
    return parser


def main(argv=None):
    confluence.load_env_file()  # AWS_CONFIG_FILE etc. escolhidos no onboarding
    args = build_parser().parse_args(argv)
    if args.command in ("sync", "squads", "setup"):
        console = ui.make_console()
        if not granted.executable() and console.interactive():  # sem terminal ninguem responde: o shell avisa depois
            granted_install.offer(console)
        adjusted = granted.ensure_settings()
        if adjusted:
            warn(f"Granted ajustado ({', '.join(adjusted)}).")
        if onboarding.reapply_browser():
            warn("Navegador gravado no Granted.")
    try:
        code = args.run(args)
    except (KeyboardInterrupt, EOFError):
        warn("\n" + ui.note_line("Cancelado."))
        code = EXIT_CANCELLED
    return code

