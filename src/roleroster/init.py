"""`role init`: liga o roleroster no shell (e desliga com --uninstall).

Copia o script de shell para a pasta do roleroster e grava no rc do shell um
bloco com marcadores. Rodar de novo atualiza o bloco, sem duplicar; o resto do
rc nunca e alterado. Os comandos ficam sempre atras do prefixo (`role sso`, `role console`):
nao ha atalhos soltos, para nao colidir com outros programas.
"""
import argparse
import os
import sys
import tempfile
from importlib import resources
from pathlib import Path

from . import awscli_install, granted_install, paths, preset, ui
from .granted_install import tilde

BEGIN = "# >>> roleroster (gerado por `role init`, nao edite) >>>"
END = "# <<< roleroster <<<"
SCRIPT_NAME = "roleroster.sh"  # o mesmo arquivo para zsh e bash
FISH_SCRIPT_NAME = "roleroster.fish"  # o fish tem sintaxe propria: arquivo a parte
LEGACY_SCRIPT_NAME = "roleroster.zsh"  # nome das versoes antigas; vira um atalho para o novo
SHELLS = ("zsh", "bash", "fish")
LOADED_VAR = "ROLEROSTER_SHELL_LOADED"  # a funcao `role` do shell poe isto ao chamar `role init`


def script_name(shell):
    return FISH_SCRIPT_NAME if shell == "fish" else SCRIPT_NAME


def detect_shell():
    return Path(os.environ.get("SHELL", "")).name


def rc_path(shell):
    """Arquivo que o shell le ao abrir um terminal. No macOS o Terminal abre shells de login,
    que leem o .bash_profile; no Linux o bash interativo le o .bashrc."""
    if shell == "zsh":
        return Path(os.environ.get("ZDOTDIR") or Path.home()) / ".zshrc"
    if shell == "bash":
        return Path.home() / (".bash_profile" if sys.platform == "darwin" else ".bashrc")
    if shell == "fish":
        return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "fish" / "config.fish"
    raise ValueError(shell)


def render_block(script, home=None, shell="zsh"):
    lines = [BEGIN]
    if home:
        lines.append(f'set -gx ROLEROSTER_HOME "{home}"' if shell == "fish" else f'export ROLEROSTER_HOME="{home}"')
    lines.append(f'source "{script}"')
    lines.append(END)
    return "\n".join(lines) + "\n"


def split_block(text):
    """Devolve (antes, depois) do bloco do roleroster; depois e None se nao ha bloco."""
    start = text.find(BEGIN)
    end = text.find(END, start + 1) if start != -1 else -1
    if start == -1 or end == -1:
        return text, None
    after = text[end + len(END):]
    return text[:start], after[1:] if after.startswith("\n") else after


def apply_block(text, block):
    before, after = split_block(text)
    if after is None:
        separator = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + separator + block
    return before + block + after


def remove_block(text):
    before, after = split_block(text)
    if after is None:
        return text
    return before.rstrip("\n") + ("\n" if before.strip() else "") + after.lstrip("\n")


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def packaged_script(shell="zsh"):
    return resources.files("roleroster").joinpath("shell", script_name(shell)).read_text()


def legacy_shim():
    """O rc de quem instalou antes aponta para roleroster.zsh: esse arquivo passa a so ler o novo."""
    return f'# atalho das versoes antigas: o script agora e o roleroster.sh\nsource "{paths.shell_dir() / SCRIPT_NAME}"\n'


def install_script(shell="zsh"):
    target = paths.shell_dir() / script_name(shell)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(packaged_script(shell))
    if shell == "fish":
        return target
    legacy = paths.shell_dir() / LEGACY_SCRIPT_NAME
    if legacy.exists():
        legacy.write_text(legacy_shim())
    return target


def refresh_script():
    """Depois de um upgrade da lib, atualiza o script copiado (so se o `role init` ja foi feito)."""
    shell_dir = paths.shell_dir()
    target, legacy = shell_dir / SCRIPT_NAME, shell_dir / LEGACY_SCRIPT_NAME
    fish_target = shell_dir / FISH_SCRIPT_NAME
    changed = False
    if fish_target.exists() and fish_target.read_text() != packaged_script("fish"):
        fish_target.write_text(packaged_script("fish"))
        changed = True
    if not target.exists() and not legacy.exists():
        return changed
    if not target.exists() or target.read_text() != packaged_script():
        target.write_text(packaged_script())
        changed = True
    if legacy.exists() and legacy.read_text() != legacy_shim():
        legacy.write_text(legacy_shim())
        changed = True
    return changed


def build_parser():
    parser = argparse.ArgumentParser(prog="role init", description=__doc__.strip().splitlines()[0])
    parser.add_argument("-s", "--shell", choices=SHELLS, help="zsh, bash ou fish (padrao: o shell atual, $SHELL)")
    parser.add_argument(
        "-r", "--rc",
        help="arquivo de configuracao do shell (padrao: ~/.zshrc, ~/.bash_profile no macOS ou ~/.bashrc no Linux)",
    )
    parser.add_argument("-p", "--preset", help="instala antes o preset da empresa (caminho ou URL https)")
    parser.add_argument(
        "-y", "--yes", action="store_true", help="aceita as confirmações sem perguntar (preset e instalação do Granted)"
    )
    parser.add_argument("--aws-cli", dest="aws_cli", action="store_true", default=None,
                        help="instala a AWS CLI (opcional) sem perguntar")
    parser.add_argument("--no-aws-cli", dest="aws_cli", action="store_false", help="não oferece a instalação da AWS CLI")
    parser.add_argument("-u", "--uninstall", action="store_true", help="remove o bloco do rc e o script copiado")
    parser.add_argument("-n", "--dry-run", action="store_true", help="mostra o bloco sem gravar nada")
    return parser


def main(argv):
    args = build_parser().parse_args(argv)
    shell = args.shell or detect_shell()
    if shell not in SHELLS:
        print(f"role init: shell '{shell}' ainda nao e suportado (suportado: {', '.join(SHELLS)}).", file=sys.stderr)
        return 2
    rc = Path(args.rc).expanduser() if args.rc else rc_path(shell)
    text = rc.read_text() if rc.exists() else ""

    if args.preset and not args.uninstall and not args.dry_run:
        try:
            if not preset.install_with_confirmation(ui.make_console(), args.preset, assume_yes=args.yes):
                print("role init: nada foi feito.", file=sys.stderr)
                return 1
        except preset.PresetError as error:
            print(f"role init: {error}", file=sys.stderr)
            return 1

    if args.uninstall:
        return uninstall(args, rc, text, shell)

    if not args.dry_run:
        console = ui.make_console()
        granted_install.offer(console, assume_yes=args.yes)
        awscli_install.offer(console, choice=args.aws_cli)

    script = paths.shell_dir() / script_name(shell)
    explicit_home = paths.home() if os.environ.get("ROLEROSTER_HOME") else None
    block = render_block(script, explicit_home, shell)
    if args.dry_run:
        print(f"# {rc}\n{block}", end="")
        return 0
    install_script(shell)
    new_text = apply_block(text, block)
    if new_text == text:
        print(f"role init: {rc} ja estava em dia.")
        return 0
    write_text(rc, new_text)
    if os.environ.get(LOADED_VAR):
        print(f"role init: bloco gravado em {tilde(rc)}.")  # a funcao `role` recarrega o script neste terminal
    else:
        print(f"role init: bloco gravado em {tilde(rc)}. Abra um terminal novo para usar o role.")
    return 0


def uninstall(args, rc, text, shell="zsh"):
    new_text = remove_block(text)
    if args.dry_run:
        print("role init: o bloco seria removido." if new_text != text else "role init: nao ha bloco para remover.")
        return 0
    if new_text != text:
        write_text(rc, new_text)
    if used_by_another_rc(rc, shell):
        print(f"role init: roleroster removido de {rc}. O script continua, porque outro shell seu ainda o usa.")
        return 0
    names = (FISH_SCRIPT_NAME,) if shell == "fish" else (SCRIPT_NAME, LEGACY_SCRIPT_NAME)
    for name in names:
        (paths.shell_dir() / name).unlink(missing_ok=True)
    for folder in (paths.shell_dir(), paths.home()):
        try:
            folder.rmdir()  # so some se ficou vazia: o .env e o estado nunca sao apagados aqui
        except OSError:
            break
    print(f"role init: roleroster removido de {rc}. O .env e o estado em {paths.home()} ficaram.")
    return 0


def used_by_another_rc(current_rc, current_shell="zsh"):
    """O script e um so para zsh e bash: nao apaga se o rc do outro shell ainda tem o bloco.
    O fish tem o proprio script, que nenhum outro shell usa."""
    if current_shell == "fish":
        return False
    for shell in ("zsh", "bash"):
        other = rc_path(shell)
        if other != current_rc and other.exists() and BEGIN in other.read_text():
            return True
    return False
