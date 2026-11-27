# roleroster no zsh e no bash: a funcao `role` e a renovacao automatica das credenciais.
# Ligado pelo `role init`, que grava no rc do shell um bloco com uma unica linha:
#   source "<pasta do roleroster>/shell/roleroster.sh"
#
# Um arquivo so para os dois shells: o que e diferente (relogio, achar o assume e o gancho do
# prompt) fica nos blocos "zsh" e "bash" abaixo; o resto usa so o que os dois entendem
# (inclusive o bash 3.2 do macOS).
#
# So ficam aqui as funcoes que precisam alterar o terminal atual (AWS_PROFILE).
# A logica de verdade esta no pacote Python (comando `roleroster`).
#
#   role sso [termos] [flags]      sync dos perfis, escolha com busca e assume
#   role console [termos] [flags]  sync dos perfis, escolha com busca e abre o console
#   role assume ...                o assume do Granted, no terminal atual
#   -i, --inactives    inclui os projetos inativos (por padrao ficam de fora)
#   -n, --no-renew     (sso) nao renova as credenciais sozinho (por padrao renova por ate 12 h)
#   -U, --unset        (sso) limpa o terminal, o perfil compartilhado e as credenciais
#   -S, --squads       escolhe de quais squads listar os perfis
#   -B, --setup        (sso) refaz o trecho inicial do config (sso-session e perfil base)
#   -T, --status       (sso) ate quando a credencial vale
#   role <qualquer outro>          vai para o `roleroster` (sync, squads, status, init...) e,
#                                  se nao for dele, para o Granted
#
# Na primeira vez o sync pergunta o que faltar (arquivos da AWS, trecho inicial, Confluence).

_ROLE_HOME="${ROLEROSTER_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}/roleroster}"
_AWS_STATE_FILE="$_ROLE_HOME/state/current_profile"
_AWS_SESSION_FILE="$_ROLE_HOME/state/session"
_AWS_RENEW_LOCK="$_ROLE_HOME/state/renew.lock"
_AWS_EXPORT_SUFFIX="-exported"
_AWS_RENEW_MARGIN=600   # renova quando faltam menos de 10 min para a credencial vencer
_AWS_RENEW_BACKOFF=300  # depois de uma falha, este terminal espera 5 min para tentar de novo
_AWS_RENEW_RETRY_AT=0

# ---------------------------------------------------------------- o que muda de shell para shell

# O role init instala o Granted em ~/.local/bin quando nao ha Homebrew; o assume chama o assumego pelo PATH.
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) [ -x "$HOME/.local/bin/assumego" ] && export PATH="$PATH:$HOME/.local/bin" ;;
esac

if [ -n "${ZSH_VERSION:-}" ]; then
  zmodload zsh/datetime
  _role_tick() { _ROLE_NOW=$EPOCHSECONDS; }
  _AWS_ASSUME="${commands[assume]:-$HOME/.local/bin/assume}"
  [ -x "$_AWS_ASSUME" ] || _AWS_ASSUME="${HOMEBREW_PREFIX:-/opt/homebrew}/bin/assume"
  # O script assume so usa `return` (em vez de `exit`, que fecharia o terminal) quando existe
  # um alias com o mesmo nome. Por isso, no zsh, o alias precisa continuar aqui.
  alias assume="source $_AWS_ASSUME"
else
  _role_tick() { _ROLE_NOW=$(date +%s); }
  _AWS_ASSUME="$(type -P assume 2>/dev/null)"
  _AWS_ASSUME="${_AWS_ASSUME:-$HOME/.local/bin/assume}"
  [ -x "$_AWS_ASSUME" ] || _AWS_ASSUME="${HOMEBREW_PREFIX:-/opt/homebrew}/bin/assume"
  # No bash o assume detecta sozinho que foi lido com `source`; uma funcao basta.
  assume() { source "$_AWS_ASSUME" "$@"; }
fi
# GRANTED_ALIAS_CONFIGURED evita que o Granted ofereca instalar o alias dele.
export GRANTED_ALIAS_CONFIGURED=true

# ---------------------------------------------------------------- mensagens

# Mesmo padrao do restante do roleroster: ✔ feito, ✖ erro, › informacao (cinza).
_role_say() {
  local kind=$1 text=$2 glyph code
  case ${LC_ALL:-${LC_CTYPE:-${LANG:-}}} in
    *[Uu][Tt][Ff]-8*|*[Uu][Tt][Ff]8*) case $kind in ok) glyph="✔" ;; fail) glyph="✖" ;; *) glyph="›" ;; esac ;;
    *) case $kind in ok) glyph="+" ;; fail) glyph="x" ;; *) glyph=">" ;; esac ;;
  esac
  case $kind in ok) code=32 ;; fail) code=31 ;; *) code=2 ;; esac
  if [ -t 2 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-}" != dumb ]; then
    printf '\033[%sm%s\033[0m %s\n' "$code" "$glyph" "$text" >&2
  else
    printf '%s %s\n' "$glyph" "$text" >&2
  fi
}

_aws_profiles() {
  command roleroster "$@"
}

# Exporta AWS_CONFIG_FILE e AWS_SHARED_CREDENTIALS_FILE escolhidos no onboarding
# (ficam no .env), para a AWS CLI e o Granted lerem os mesmos arquivos do roleroster.
# O que ja estiver exportado no terminal tem prioridade e nao e sobrescrito.
# As preferencias (ROLEROSTER_BROWSER, _CONSOLE_MODE, _CONSOLE_PROFILE, _RENEW_NOTICES) so
# existem no .env: sao sempre relidas, para `role browser` e `role notices` valerem na hora.
_aws_load_files() {
  local env_file="$_ROLE_HOME/.env" line key value
  [ -r "$env_file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line#"${line%%[![:space:]]*}"}  # tira os espacos do inicio
    line=${line#export }
    key=${line%%=*}
    value=${line#*=}
    value=${value//[\"\']/}
    case $value in "~"*) value="$HOME${value#\~}" ;; esac
    case $key in
      AWS_CONFIG_FILE) [ -n "${AWS_CONFIG_FILE:-}" ] || export AWS_CONFIG_FILE="$value" ;;
      AWS_SHARED_CREDENTIALS_FILE) [ -n "${AWS_SHARED_CREDENTIALS_FILE:-}" ] || export AWS_SHARED_CREDENTIALS_FILE="$value" ;;
      ROLEROSTER_BROWSER) export ROLEROSTER_BROWSER="$value" ;;
      ROLEROSTER_CONSOLE_MODE) export ROLEROSTER_CONSOLE_MODE="$value" ;;
      ROLEROSTER_CONSOLE_PROFILE) export ROLEROSTER_CONSOLE_PROFILE="$value" ;;
      ROLEROSTER_RENEW_NOTICES) export ROLEROSTER_RENEW_NOTICES="$value" ;;
    esac
  done < "$env_file"
}
_aws_load_files

# Flags que nao precisam de perfil: vao direto ao assume, sem sync nem busca.
_aws_is_passthrough() {
  local arg
  for arg in "$@"; do
    case $arg in
      --unset|--un|--help|-h|--version|-v) return 0 ;;
    esac
  done
  return 1
}

# _aws_has_arg <flag> <args...>: algum dos args e exatamente <flag>?
_aws_has_arg() {
  local needle=$1 arg
  shift
  for arg in "$@"; do
    [ "$arg" = "$needle" ] && return 0
  done
  return 1
}

# Sincroniza os perfis. So cancelar a configuracao (Ctrl-C, saida 130)
# interrompe o comando; qualquer outra falha so avisa e segue com o config atual.
_aws_sync() {
  _aws_profiles sync "$@"
  local rc=$?
  _aws_load_files  # o onboarding pode ter acabado de gravar os arquivos da AWS
  [ "$rc" -ne 130 ]
}

# Formas curtas das nossas flags, escolhidas para nao colidir com as do assume.
# Deixa a forma longa em _AWS_ARGV.
_aws_expand_short() {
  _AWS_ARGV=()
  local arg
  for arg in "$@"; do
    case $arg in
      -S) _AWS_ARGV+=(--squads) ;;
      -B) _AWS_ARGV+=(--setup) ;;
      -T) _AWS_ARGV+=(--status) ;;
      -U) _AWS_ARGV+=(--unset) ;;
      -n) _AWS_ARGV+=(--no-renew) ;;
      *) _AWS_ARGV+=("$arg") ;;
    esac
  done
}

# -i/--inactives e --no-renew sao nossas, nao do assume: saem dos argumentos.
# Deixa _AWS_INACTIVE_FLAG (vazio ou --inactives), _AWS_NO_RENEW (vazio ou 1)
# e o restante em _AWS_REST.
_aws_split_flags() {
  _AWS_INACTIVE_FLAG=()
  _AWS_NO_RENEW=
  _AWS_REST=()
  local arg
  for arg in "$@"; do
    case $arg in
      -i|--inactives) _AWS_INACTIVE_FLAG=(--inactives) ;;
      --no-renew) _AWS_NO_RENEW=1 ;;
      *) _AWS_REST+=("$arg") ;;
    esac
  done
}

# Os termos digitados (ate a primeira flag) viram a busca. Deixa em _AWS_ARGS
# o perfil escolhido seguido das flags. Sem fzf, repassa tudo ao seletor do Granted.
_aws_choose() {
  local -a terms
  terms=()
  while [ $# -gt 0 ]; do
    case $1 in -*) break ;; esac
    terms+=("$1")
    shift
  done
  local profile rc
  profile=$(_aws_profiles pick "${_AWS_INACTIVE_FLAG[@]}" "${terms[@]}")
  rc=$?
  case $rc in
    0) _AWS_ARGS=("$profile" "$@") ;;
    3) _AWS_ARGS=("${terms[@]}" "$@") ;;
    *) return $rc ;;
  esac
}

# Assume o perfil e compartilha a sessao com todos os terminais e agentes: eles
# passam a usar o perfil "<perfil>-exported". $1: "new" (sso) ou "renewed" (renovacao).
_aws_assume_and_share() {
  local kind=$1
  shift
  source "$_AWS_ASSUME" "$@"
  [ -n "$AWS_PROFILE" ] || return 1

  local original="${AWS_PROFILE%$_AWS_EXPORT_SUFFIX}"
  local exported="$original$_AWS_EXPORT_SUFFIX"
  mkdir -p "$_ROLE_HOME/state"  # instalacao nova: a pasta so existiria depois do session-save
  printf '%s\n' "$exported" > "$_AWS_STATE_FILE"
  _aws_profiles prune-exported "$exported"

  local -a save
  save=(session-save "$original" "$AWS_CREDENTIAL_EXPIRATION")
  if [ "$kind" = renewed ]; then
    save+=(--renewed)
  elif [ -n "$_AWS_NO_RENEW" ]; then
    save+=(--no-renew)
  fi
  _aws_profiles "${save[@]}"

  unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_CREDENTIAL_EXPIRATION AWS_SESSION_EXPIRATION
  export AWS_PROFILE="$exported"
}

_role_sso() {
  _aws_expand_short "$@"
  set -- "${_AWS_ARGV[@]}"
  if _aws_has_arg --squads "$@"; then
    _aws_profiles squads
    return
  fi
  if _aws_has_arg --setup "$@"; then
    _aws_profiles setup
    return
  fi
  if _aws_has_arg --status "$@"; then
    _aws_profiles status
    return
  fi
  if _aws_is_passthrough "$@"; then
    source "$_AWS_ASSUME" "$@"
    if _aws_has_arg --unset "$@" || _aws_has_arg --un "$@"; then
      rm -f "$_AWS_STATE_FILE" "$_AWS_SESSION_FILE"
      _aws_profiles prune-exported
    fi
    return
  fi

  _aws_split_flags "$@"
  set -- "${_AWS_REST[@]}"
  _aws_sync "${_AWS_INACTIVE_FLAG[@]}" || return 130
  _aws_choose "$@" || return
  _aws_assume_and_share new "${_AWS_ARGS[@]}"
}

# Abre o console conforme ROLEROSTER_CONSOLE_MODE (escolhido em `role browser`):
#   profile  um perfil fixo do navegador (--bp); padrao
#   account  um perfil por conta (padrao do Granted)
#   guest    janela isolada a cada abertura, sem deixar perfil
# O Firefox ignora o modo: o Granted abre cada conta num container.
_role_open_console() {
  local mode=${ROLEROSTER_CONSOLE_MODE:-profile}
  [ "${ROLEROSTER_BROWSER:-}" = firefox ] && mode=account
  case $mode in
    guest) _role_console_guest "$@" ;;
    account) source "$_AWS_ASSUME" -c "$@" ;;
    *) source "$_AWS_ASSUME" --bp "${ROLEROSTER_CONSOLE_PROFILE:-AWS}" -c "$@" ;;
  esac
}

# A URL do console vem do Granted (-u) e a janela e aberta pelo roleroster, que a recebe
# pelo stdin (ela leva credenciais temporarias; nao deve aparecer como argumento).
_role_console_guest() {
  local output url
  output=$(source "$_AWS_ASSUME" "$@" -u) || return
  url=${output##*$'\n'}  # a URL e a ultima linha; o assume pode imprimir avisos antes
  case $url in
    https://*) ;;
    *)
      _role_say fail "O Granted não devolveu a URL do console; use role browser para outro modo."
      return 1 ;;
  esac
  printf '%s\n' "$url" | _aws_profiles open-console
}

_role_console() {
  _aws_expand_short "$@"
  set -- "${_AWS_ARGV[@]}"
  if _aws_has_arg --squads "$@"; then
    _aws_profiles squads
    return
  fi
  if _aws_is_passthrough "$@"; then
    source "$_AWS_ASSUME" "$@"
    return
  fi
  _aws_split_flags "$@"
  set -- "${_AWS_REST[@]}"
  _aws_sync "${_AWS_INACTIVE_FLAG[@]}" || return 130
  _aws_choose "$@" || return
  _role_open_console "${_AWS_ARGS[@]}"
}

role() {
  local rc
  case ${1:-} in
    sso) shift; _role_sso "$@" ;;
    console) shift; _role_console "$@" ;;
    assume) shift; source "$_AWS_ASSUME" "$@" ;;
    *)
      command roleroster "$@"
      rc=$?
      # preferencias gravadas no .env passam a valer neste terminal na hora
      case ${1:-} in browser|notices|setup) _aws_load_files ;; esac
      return $rc ;;
  esac
}

# ---------------------------------------------------------------- antes de cada prompt

# O terminal segue o perfil compartilhado. Sem o arquivo (depois de `role sso --unset`
# em outro terminal), larga o perfil exportado.
_aws_follow_shared_profile() {
  local shared
  if [ ! -r "$_AWS_STATE_FILE" ]; then
    case ${AWS_PROFILE:-} in *"$_AWS_EXPORT_SUFFIX") unset AWS_PROFILE ;; esac
    return 0
  fi
  shared=$(<"$_AWS_STATE_FILE")
  if [ -n "$shared" ] && [ "${AWS_PROFILE:-}" != "$shared" ]; then
    export AWS_PROFILE="$shared"
  fi
}

# Trava velha, de um terminal que morreu no meio da renovacao (mais de 3 minutos).
_aws_lock_is_stale() {
  [ -n "$(find "$_AWS_RENEW_LOCK" -maxdepth 0 -mmin +3 2>/dev/null)" ]
}

# Renovacao automatica: um terminal por vez (trava) refaz o assume do perfil.
# Se o login SSO tiver vencido, o proprio assume abre o navegador.
# `role notices off` esconde so a linha de andamento; falhas e avisos continuam aparecendo.
_aws_renew_locked() {
  local profile=$1
  if ! mkdir "$_AWS_RENEW_LOCK" 2>/dev/null; then
    _aws_lock_is_stale && rmdir "$_AWS_RENEW_LOCK" 2>/dev/null
    return 0
  fi
  [ "${ROLEROSTER_RENEW_NOTICES:-1}" = 0 ] || _role_say note "Renovando as credenciais de $profile..."
  _AWS_NO_RENEW=
  if ! _aws_assume_and_share renewed "$profile"; then
    _role_say fail "Não consegui renovar as credenciais; rode role sso. Nova tentativa neste terminal em 5 min."
    _role_tick
    _AWS_RENEW_RETRY_AT=$(( _ROLE_NOW + _AWS_RENEW_BACKOFF ))
  fi
  rmdir "$_AWS_RENEW_LOCK" 2>/dev/null
}

# Se faltam menos de 10 min para a credencial vencer e a janela de 12 h do ultimo sso
# ainda vale, renova. So le um arquivo pequeno.
_aws_auto_renew() {
  [ -r "$_AWS_SESSION_FILE" ] || return 0
  local profile="" expires="" renew_until="" renew="" line
  while IFS= read -r line || [ -n "$line" ]; do
    case $line in
      profile=*) profile=${line#profile=} ;;
      expires=*) expires=${line#expires=} ;;
      renew_until=*) renew_until=${line#renew_until=} ;;
      renew=*) renew=${line#renew=} ;;
    esac
  done < "$_AWS_SESSION_FILE"
  [ "$renew" = 1 ] && [ -n "$profile" ] || return 0
  case $expires$renew_until in '' | *[!0-9]*) return 0 ;; esac
  [ -n "$expires" ] && [ -n "$renew_until" ] || return 0
  _role_tick
  (( _ROLE_NOW >= expires - _AWS_RENEW_MARGIN && _ROLE_NOW >= _AWS_RENEW_RETRY_AT )) || return 0

  if (( _ROLE_NOW >= renew_until )); then
    _role_say note "A renovação automática (12 h) terminou; rode role sso para continuar."
    _AWS_RENEW_RETRY_AT=$(( _ROLE_NOW + 3600 ))  # avisa no maximo uma vez por hora
    return 0
  fi
  _aws_renew_locked "$profile"
}

# ---------------------------------------------------------------- gancho do prompt

if [ -n "${ZSH_VERSION:-}" ]; then
  autoload -Uz add-zsh-hook
  add-zsh-hook precmd _aws_follow_shared_profile
  add-zsh-hook precmd _aws_auto_renew
else
  # Roda por ultimo e devolve o $? do comando do usuario, para nao atrapalhar o prompt dele.
  _role_prompt() {
    local rc=$?
    _aws_follow_shared_profile
    _aws_auto_renew
    return $rc
  }
  case "$(declare -p PROMPT_COMMAND 2>/dev/null)" in
    "declare -a"*)
      case " ${PROMPT_COMMAND[*]} " in *" _role_prompt "*) ;; *) PROMPT_COMMAND+=(_role_prompt) ;; esac ;;
    *)
      case ";${PROMPT_COMMAND:-};" in
        *";_role_prompt;"*) ;;
        *) PROMPT_COMMAND="${PROMPT_COMMAND:+$PROMPT_COMMAND;}_role_prompt" ;;
      esac ;;
  esac
fi
_aws_follow_shared_profile
