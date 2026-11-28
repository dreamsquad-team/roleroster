"""Popula o ~/.aws/config com os perfis de switchRole que estao no Confluence.

O script so mexe num bloco delimitado por marcadores, dentro da secao ACCOUNTS.
Tudo fora dele (sso-session, o perfil base, perfis SSO diretos e a secao MANUAL, onde ficam os
perfis que voce escreve a mao) fica exatamente como esta. O Granted continua
lendo o config padrao da AWS, entao nada muda no uso.

O arquivo e o de sempre (~/.aws/config), ou o que estiver em AWS_CONFIG_FILE:
com essa variavel da para testar sem encostar no config que voce ja tem.
Se o arquivo nao existir, ele e criado.

Cada perfil gerado fica assim:

    [profile <chave que vem do Confluence>]
    source_profile = <perfil base>
    role_arn = arn:aws:iam::<aws_account_id>:role/<role_name>
    region = <region>

Quem chama isto e o `role sync` (e, antes de escolher o perfil, o `role sso` e o `role console`).
"""
import os
import re
import tempfile
from pathlib import Path

from . import preset, settings
from .confluence import (
    ConfluenceError,
    fetch_candidates,
    parse_profiles,
    require_config,
)

BEGIN = "# >>> confluence-sync (gerado pelo script, nao edite) >>>"
END = "# <<< confluence-sync <<<"
PRESET_BEGIN = "# >>> preset (gerado pelo roleroster, nao edite) >>>"
PRESET_END = "# <<< preset <<<"
ACCOUNTS_HEADER = "# ------------------ ACCOUNTS ------------------"
MANUAL_HEADER = "# ------------------ MANUAL ------------------"
MANUAL_NOTE = "# Perfis escritos a mao ficam aqui: o sync nunca altera nem apaga o que esta abaixo."
PROFILE_NAME_RE = re.compile(r"[A-Za-z0-9_.@-]+")


class Selection:
    """Perfis que entram no config e o que ficou de fora, por motivo."""

    def __init__(self):
        self.profiles = []
        self.duplicates = 0
        self.invalid = []
        self.reserved = []
        self.conflicts = []


def select_profiles(candidates, reserved_names):
    """Escolhe um perfil por nome, descartando o que nao e seguro gravar.

    Os ativos vem antes dos inativos (que so chegam aqui com `role sso -i`).

    - espacos no nome viram hifen (`Drive Match` -> `Drive-Match`)
    - nome ainda fora de [A-Za-z0-9_.@-]: descartado
    - nome que ja existe fora do bloco gerado: descartado (o manual vence)
    - mesmo nome com ARNs diferentes: descartado por inteiro, para nunca gravar
      o ARN errado
    - mesmo nome e mesmo ARN em varias paginas: conta como duplicado
    """
    result = Selection()
    chosen = {}
    conflicting = set()
    ordered = sorted(
        candidates,
        key=lambda c: (c.get("inactive", False), c["page_title"], "-".join(c["profile"].split())),
    )
    for candidate in ordered:
        name = "-".join(candidate["profile"].split())
        inactive = candidate.get("inactive", False)
        if not PROFILE_NAME_RE.fullmatch(name):
            _remember(result.invalid, name)
        elif name in reserved_names:
            _remember(result.reserved, name)
        elif name in chosen:
            if chosen[name]["role_arn"] == candidate["role_arn"]:
                result.duplicates += 1
            else:
                conflicting.add(name)
        else:
            chosen[name] = {
                "profile": name,
                "page_title": candidate["page_title"],
                "role_arn": candidate["role_arn"],
                "region": candidate.get("region", ""),
                "inactive": inactive,
            }
    result.conflicts = sorted(conflicting)
    result.profiles = [p for n, p in chosen.items() if n not in conflicting]
    return result


def _remember(items, name):
    if name not in items:
        items.append(name)


def render_block(profiles, source_profile):
    lines = [BEGIN, "# Fonte: paginas do Confluence, secao Switch Role.", ""]
    current_title = None
    for profile in profiles:
        title = " ".join(profile["page_title"].split())
        if profile.get("inactive"):
            title += " [inativo]"
        if title != current_title:
            current_title = title
            lines.append(f"# --- {title}")
            lines.append("")
        lines.append(f"[profile {profile['profile']}]")
        lines.append(f"source_profile = {source_profile}")
        lines.append(f"role_arn = {profile['role_arn']}")
        if profile["region"]:
            lines.append(f"region = {profile['region']}")
        lines.append("")
    lines.append(END)
    return "\n".join(lines) + "\n"


def split_managed(text):
    """Devolve (antes, depois) do bloco gerado, ou (texto, None) se nao houver."""
    start = text.find(BEGIN)
    end = text.find(END)
    if start == -1 and end == -1:
        return text, None
    if start == -1 or end == -1 or end < start:
        raise ConfluenceError("marcadores do bloco gerado estao corrompidos no config")
    after = text[end + len(END):]
    return text[:start], after[1:] if after.startswith("\n") else after


def managed_text(text):
    start = text.find(BEGIN)
    end = text.find(END)
    return text[start:end] if start != -1 and end > start else ""


def source_profile():
    """Perfil base dos Switch Roles: o que o usuario escolheu, o do preset ou "roleroster-base"."""
    return settings.get("base_profile")


def default_config_path():
    """~/.aws/config, ou o arquivo de AWS_CONFIG_FILE (a AWS CLI e o Granted leem o mesmo)."""
    return Path(os.environ.get("AWS_CONFIG_FILE") or Path.home() / ".aws" / "config").expanduser()


def apply_block(text, block):
    """Poe o bloco gerado no config e garante os cabecalhos ACCOUNTS e MANUAL.

    O bloco novo vai logo abaixo de ACCOUNTS (o cabecalho e criado se faltar) e
    a secao MANUAL logo abaixo do bloco. So o que esta entre os marcadores muda.
    """
    before, after = split_managed(text)
    if after is None and ACCOUNTS_HEADER in text:
        cut = text.index(ACCOUNTS_HEADER) + len(ACCOUNTS_HEADER)
        tail = text[cut:].lstrip("\n")
        before, after = text[:cut] + "\n\n", ("\n" + tail if tail else "")
    elif after is None:
        head = text.rstrip("\n")
        before, after = head + ("\n\n" if head else "") + ACCOUNTS_HEADER + "\n\n", ""
    if MANUAL_HEADER not in before + after:
        block += f"\n{MANUAL_HEADER}\n{MANUAL_NOTE}\n" + ("\n" if after.strip() else "")
    return before + block + after


def outside_block_names(text):
    before, after = split_managed(text)
    return {p["profile"] for p in parse_profiles(before + (after or ""))}


def remove_preset_block(text):
    """Tira o bloco dos perfis do preset (e a linha em branco depois dele), se houver."""
    start, end = text.find(PRESET_BEGIN), text.find(PRESET_END)
    if start == -1 and end == -1:
        return text
    if start == -1 or end == -1 or end < start:
        raise ConfluenceError("marcadores do bloco do preset estão corrompidos no config")
    tail = text[end + len(PRESET_END):]
    return text[:start] + (tail[2:] if tail.startswith("\n\n") else tail[1:] if tail.startswith("\n") else tail)


def render_preset_block(profiles, session, sso_region, region):
    lines = [PRESET_BEGIN, "# Fonte: preset da empresa (role preset), seção [[aws.profiles]].", ""]
    for profile in profiles:
        lines += [
            f"[profile {profile['name']}]",
            f"sso_session = {session}",
            f"sso_region = {sso_region}",
            f"sso_account_id = {profile['account_id']}",
            f"sso_role_name = {profile['role']}",
            f"region = {profile['region'] or region}",
            "output = json",
            "",
        ]
    lines.append(PRESET_END)
    return "\n".join(lines) + "\n\n"


def preset_profiles_to_write(text, profiles):
    """Perfis do preset que entram no config: os que nao colidem com um perfil escrito a mao ou o base."""
    taken = outside_block_names(remove_preset_block(text)) | {source_profile()}
    return [profile for profile in profiles if profile["name"] not in taken]


def apply_preset_profiles(text, wanted):
    """Reescreve o bloco dos perfis do preset logo acima do bloco gerado pelo Confluence.

    `wanted` vem de preset_profiles_to_write. Sem perfis o bloco some. O que esta fora dos marcadores nao muda.
    """
    text = remove_preset_block(text)
    if not wanted:
        return text
    sso_region = settings.get("sso_region")
    region = settings.from_user_or_preset("region") or sso_region
    block = render_preset_block(wanted, settings.get("sso_session"), sso_region, region)
    cut = text.find(BEGIN)
    return block.join((text[:cut], text[cut:])) if cut != -1 else text + "\n" + block


def change_counts(old_text, new_text):
    old = {p["profile"]: p for p in parse_profiles(managed_text(old_text))}
    new = {p["profile"]: p for p in parse_profiles(managed_text(new_text))}
    added = len(new.keys() - old.keys())
    removed = len(old.keys() - new.keys())
    changed = sum(1 for n in new.keys() & old.keys() if new[n] != old[n])
    return added, removed, changed


def write_atomic(path, text):
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config.")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


class SyncPlan:
    """O que o sync faria: o texto novo do config e as contagens."""

    def __init__(self, old_text, new_text, selection, candidates, stats):
        self.old_text = old_text
        self.new_text = new_text
        self.selection = selection
        self.candidates = candidates
        self.stats = stats
        self.added, self.removed, self.changed = change_counts(old_text, new_text)

    @property
    def has_changes(self):
        return self.new_text != self.old_text


def plan_sync(config_path, source_profile, squad_ids, include_inactive=False):
    """Le o Confluence e calcula o novo config. Nao grava nada."""
    old_text = config_path.read_text() if config_path.exists() else ""
    base, auth = require_config()
    candidates, stats = fetch_candidates(base, auth, squad_ids, include_inactive)
    wanted = preset_profiles_to_write(old_text, preset.load_installed_profiles())
    reserved = outside_block_names(remove_preset_block(old_text)) | {profile["name"] for profile in wanted}
    selection = select_profiles(candidates, reserved)
    new_text = apply_block(old_text, render_block(selection.profiles, source_profile))
    new_text = apply_preset_profiles(new_text, wanted)
    return SyncPlan(old_text, new_text, selection, candidates, stats)


def write_config(config_path, plan):
    """Grava o config novo. So o bloco gerado muda, entao nao guarda copia da versao anterior."""
    write_atomic(config_path, plan.new_text)
