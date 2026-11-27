"""`role doctor`: confere o que o roleroster precisa e diz como consertar.

Tres grupos: instalacao (Granted, fzf, AWS CLI), configuracao (shell, Granted, navegador,
arquivos da AWS) e Confluence. Cada verificacao vira ✔ (ok), ! (aviso) ou ✖ (falha), com a
dica logo abaixo. `--fix` aplica o que tem conserto automatico; `--online` testa o token no Confluence.
So o Granted tem instalacao automatica (`--fix`, ou `role init`); fzf e AWS CLI mostram o comando do seu sistema.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import awsconfig as sync
from . import browsers, confluence, granted, granted_install, init, onboarding, vault

TESTED_GRANTED = granted_install.VERSION
OK, WARN, FAIL = "ok", "warn", "fail"

INSTALL_HINTS = {
    "fzf": {"darwin": "brew install fzf", "linux": "sudo apt install fzf (ou o gerenciador da sua distribuição)"},
    "aws": {
        "darwin": "brew install awscli",
        "linux": "veja https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html",
    },
}


class Result:
    def __init__(self, status, text, hint="", fix=None):
        self.status, self.text, self.hint, self.fix = status, text, hint, fix  # fix: funcao que conserta e devolve o que fez


def install_hint(tool):
    platform = "darwin" if sys.platform == "darwin" else "linux" if sys.platform.startswith("linux") else ""
    return INSTALL_HINTS[tool].get(platform, "veja a documentação do programa para o seu sistema")


def granted_version():
    try:
        command = [granted.executable() or "granted", "--version"]
        done = subprocess.run(command, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    match = re.search(r"\d+\.\d+\.\d+", done.stdout + done.stderr)
    return match.group() if match else ""


# ---------------------------------------------------------------- instalacao


def check_python():
    version = ".".join(map(str, sys.version_info[:3]))
    return [Result(OK, f"Python {version}")]


def check_granted():
    if not granted.executable():
        hint = f"rode role doctor --fix ou role init para instalar. Manual: {granted_install.manual_hint()}"
        return [Result(FAIL, "Granted não está instalado", hint, fix=granted_install.install)]
    version = granted_version()
    results = []
    if version and version != TESTED_GRANTED:
        hint = f"o roleroster foi testado com a {TESTED_GRANTED}; se algo estranhar, é o primeiro suspeito"
        results.append(Result(WARN, f"Granted {version}", hint))
    else:
        results.append(Result(OK, f"Granted {version or '(versão não identificada)'}"))
    brew_assume = Path(os.environ.get("HOMEBREW_PREFIX", "/opt/homebrew"), "bin", "assume")
    assume_paths = [brew_assume, granted_install.bin_dir() / "assume"]
    if not (shutil.which("assume") or any(path.exists() for path in assume_paths)):
        hint = "reinstale o Granted: " + granted_install.manual_hint()
        results.append(Result(FAIL, "Script assume do Granted não encontrado", hint))
    return results


def check_optional_tools():
    results = []
    if shutil.which("fzf"):
        results.append(Result(OK, "fzf (busca de perfis)"))
    else:
        hint = f"sem ele a busca usa o seletor do Granted. {install_hint('fzf')}"
        results.append(Result(WARN, "fzf não está instalado (opcional)", hint))
    if shutil.which("aws"):
        results.append(Result(OK, "AWS CLI"))
    else:
        results.append(Result(WARN, "AWS CLI não está instalada (opcional)", install_hint("aws")))
    return results


# ---------------------------------------------------------------- configuracao


def check_shell():
    shell = init.detect_shell()
    if not shell:
        text = "Não consegui identificar o seu shell (a variável SHELL está vazia)"
        return [Result(WARN, text, "rode role init --shell zsh ou role init --shell bash")]
    if shell not in init.SHELLS:
        return [Result(WARN, f"O shell {shell} ainda não é suportado", f"suportados: {', '.join(init.SHELLS)}")]
    rc = init.rc_path(shell)
    if not (rc.exists() and init.BEGIN in rc.read_text()):
        return [Result(FAIL, f"A integração com o {shell} não está ligada", "role init", fix=lambda: _run_init(shell))]
    script = Path(init.paths.shell_dir(), init.SCRIPT_NAME)
    if not script.exists() or script.read_text() != init.packaged_script():
        return [Result(WARN, "O script de shell está desatualizado", "role init", fix=lambda: _run_init(shell))]
    return [Result(OK, f"Integração com o {shell} ligada ({onboarding.tilde(rc)})")]


def _run_init(shell):
    init.install_script()
    return "script de shell atualizado"


def _fix_granted_config():
    return f"ajustado: {', '.join(granted.ensure_settings())}"


def _fix_token_location():
    if onboarding.migrate_token_to_vault(confluence.ENV_PATH):
        return f"token movido para o {vault.name()}"
    return "não consegui mover o token"


def check_granted_config():
    if not granted.executable():
        return []
    path = granted.config_path()
    text = path.read_text() if path.exists() else ""
    wrong = [key for key, value in granted.REQUIRED.items() if granted.current_value(text, key) != value]
    if wrong:
        hint = "sem isso o assume pergunta o sufixo no meio da renovação"
        return [Result(WARN, f"Config do Granted sem {', '.join(wrong)}", hint, fix=_fix_granted_config)]
    return [Result(OK, "Config do Granted (credenciais exportadas)")]


def check_browser():
    key = os.environ.get("ROLEROSTER_BROWSER", "")
    if not key:
        return [Result(WARN, "Navegador ainda não escolhido", "role browser")]
    if key == "none":
        hint = "instale Chrome, Brave, Firefox ou Edge e rode role browser"
        return [Result(WARN, "Nenhum navegador compatível foi encontrado na configuração", hint)]
    path = os.environ.get("ROLEROSTER_BROWSER_PATH", "")
    label = browsers.LABELS.get(key, key)
    if not path or not Path(path).exists():
        return [Result(FAIL, f"O {label} escolhido não foi encontrado em {path or '(sem caminho)'}", "role browser")]
    mode = os.environ.get("ROLEROSTER_CONSOLE_MODE", browsers.DEFAULT_MODE)
    how = "containers (Granted)" if key == "firefox" else browsers.mode_label(mode).lower()
    return [Result(OK, f"Navegador: {label}, console em modo {how}")]


def check_aws_files():
    config = os.environ.get("AWS_CONFIG_FILE", "")
    if not config:
        return [Result(WARN, "Arquivos da AWS ainda não escolhidos", "role sso")]
    path = Path(config)
    if not path.exists():
        return [Result(FAIL, f"O config da AWS não existe: {onboarding.tilde(path)}", "role sso")]
    name = sync.source_profile()
    if not onboarding.base_exists(path, name):
        return [Result(WARN, f"O perfil base {name} não está no config", "role setup")]
    return [Result(OK, f"Config da AWS com o perfil base {name} ({onboarding.tilde(path)})")]


def check_env_file():
    path = confluence.ENV_PATH
    if not path.exists():
        return [Result(WARN, "Ainda não há .env do roleroster", "role sso")]
    mode = path.stat().st_mode & 0o777
    if mode != 0o600 and os.name == "posix":
        return [Result(WARN, f".env com permissão {oct(mode)[2:]} (deveria ser 600)",
                       fix=lambda: (path.chmod(0o600), "permissão do .env ajustada para 600")[1])]
    return [Result(OK, ".env com permissão 600")]


# ---------------------------------------------------------------- confluence


def check_confluence(online):
    base, email = os.environ.get("CONFLUENCE_BASE_URL", ""), os.environ.get("CONFLUENCE_EMAIL", "")
    if not (base and email):
        return [Result(WARN, "Confluence ainda não configurado (URL e e-mail)", "role sso")]
    results = [Result(OK, f"Confluence {base} como {email}")]
    in_env = bool(os.environ.get("CONFLUENCE_TOKEN"))
    in_vault = bool(vault.get_token())
    token = confluence.get_token()
    if not token:
        results.append(Result(FAIL, "Sem token do Confluence", "role token"))
    elif in_env and vault.available():
        results.append(Result(WARN, "Token no arquivo .env", f"o {vault.name()} é mais seguro", fix=_fix_token_location))
    elif in_vault:
        results.append(Result(OK, f"Token no {vault.name()}"))
    else:
        results.append(Result(OK, "Token no .env (não há cofre do sistema disponível)"))
    if not os.environ.get("CONFLUENCE_SQUAD_IDS"):
        results.append(Result(WARN, "Nenhum squad escolhido", "role squads"))
    if online and token:
        results.append(check_token_online(base, email, token))
    return results


def check_token_online(base, email, token):
    try:
        confluence.REQUEST_TIMEOUT = 8
        confluence.check_credentials(base, email, token)
    except confluence.ConfluenceAuthError:
        return Result(FAIL, "O Confluence recusou o token", "role token")
    except confluence.ConfluenceError as error:
        return Result(WARN, f"Não consegui verificar o token agora ({error})")
    return Result(OK, "O Confluence aceitou o token")


# ---------------------------------------------------------------- execucao


def collect(online=False):
    """[(titulo do grupo, [Result])]."""
    return [
        ("Instalação", check_python() + check_granted() + check_optional_tools()),
        ("Configuração", check_shell() + check_granted_config() + check_browser() + check_aws_files() + check_env_file()),
        ("Confluence", check_confluence(online)),
    ]


def show(console, groups):
    shown = {OK: console.ok, WARN: console.warn, FAIL: console.fail}
    for title, results in groups:
        console.step(1, 1, title)
        for result in results:
            shown[result.status](result.text)
            if result.hint:
                console.note(result.hint)


def apply_fixes(console, groups):
    """Roda o conserto automatico de cada aviso ou falha que tem um. Devolve quantos aplicou."""
    applied = 0
    for _, results in groups:
        for result in results:
            if result.status != OK and result.fix:
                try:
                    console.ok(result.fix())
                    applied += 1
                except Exception as error:
                    console.fail(f"Não consegui consertar ({result.text}): {error}")
    return applied


def run(console, fix=False, online=False):
    """Mostra o diagnostico. Devolve 1 se alguma verificacao falhou, senao 0."""
    groups = collect(online)
    show(console, groups)
    results = [r for _, group in groups for r in group]
    fixable = [r for r in results if r.status != OK and r.fix]
    if fix and fixable:
        console.step(1, 1, "Consertos")
        apply_fixes(console, groups)
        groups = collect(online)
        results = [r for _, group in groups for r in group]
    counts = {status: sum(r.status == status for r in results) for status in (OK, WARN, FAIL)}
    console.step(1, 1, "Resumo")
    summary = f"{len(results)} verificações: {counts[OK]} ok, {counts[WARN]} avisos, {counts[FAIL]} falhas"
    (console.fail if counts[FAIL] else console.warn if counts[WARN] else console.ok)(summary)
    if fixable and not fix:
        console.note("Rode role doctor --fix para consertar o que tem conserto automático.")
    return 1 if counts[FAIL] else 0
