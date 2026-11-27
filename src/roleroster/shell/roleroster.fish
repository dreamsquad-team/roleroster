# roleroster no fish: a funcao `role` e a renovacao automatica das credenciais.
# Ligado pelo `role init`, que grava no config.fish um bloco com uma linha:
#   source "<pasta do roleroster>/shell/roleroster.fish"
#
# Mesmo comportamento do roleroster.sh (zsh e bash), escrito em fish. So ficam aqui as funcoes
# que precisam alterar o terminal atual (AWS_PROFILE); a logica de verdade esta no pacote Python.
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
#   role <qualquer outro>          vai para o `roleroster` e, se nao for dele, para o Granted
#
# Funciona no fish 3.2 ou superior.

if test -n "$ROLEROSTER_HOME"
    set -g _ROLE_HOME $ROLEROSTER_HOME
else if test -n "$XDG_CONFIG_HOME"
    set -g _ROLE_HOME $XDG_CONFIG_HOME/roleroster
else
    set -g _ROLE_HOME $HOME/.config/roleroster
end
set -g _AWS_STATE_FILE $_ROLE_HOME/state/current_profile
set -g _AWS_SESSION_FILE $_ROLE_HOME/state/session
set -g _AWS_RENEW_LOCK $_ROLE_HOME/state/renew.lock
set -g _AWS_EXPORT_SUFFIX -exported
set -g _AWS_RENEW_MARGIN 600 # renova quando faltam menos de 10 min para a credencial vencer
set -g _AWS_RENEW_BACKOFF 300 # depois de uma falha, este terminal espera 5 min para tentar de novo
set -g _AWS_RENEW_RETRY_AT 0
set -g _AWS_NO_RENEW ""
set -g _AWS_ARGV
set -g _AWS_REST
set -g _AWS_ARGS
set -g _AWS_INACTIVE_FLAG

# O role init instala o Granted em ~/.local/bin quando nao ha Homebrew; o assume chama o assumego pelo PATH.
if not contains -- $HOME/.local/bin $PATH; and test -x $HOME/.local/bin/assumego
    set -gx PATH $PATH $HOME/.local/bin
end

# Acha o assume.fish do Granted: no PATH, em ~/.local/bin (role init sem Homebrew) ou no Homebrew.
# Devolve 1 se nao achou. Roda de novo antes de cada uso, porque o Granted pode ter sido instalado
# depois que este terminal abriu.
function _aws_find_assume
    set -l prefix /opt/homebrew
    test -n "$HOMEBREW_PREFIX"; and set prefix $HOMEBREW_PREFIX
    for candidate in (command -s assume.fish) $HOME/.local/bin/assume.fish $prefix/bin/assume.fish
        if test -n "$candidate"; and test -r "$candidate"
            set -g _AWS_ASSUME $candidate
            return 0
        end
    end
    set -g _AWS_ASSUME $prefix/bin/assume.fish
    return 1
end
_aws_find_assume
# GRANTED_ALIAS_CONFIGURED evita que o Granted ofereca instalar o alias dele.
set -gx GRANTED_ALIAS_CONFIGURED true

# Mesmo padrao do restante do roleroster: ✔ feito, ✖ erro, › informacao (cinza).
function _role_say
    set -l kind $argv[1]
    set -l text $argv[2]
    set -l locale ""
    for candidate in $LC_ALL $LC_CTYPE $LANG
        if test -n "$candidate"
            set locale $candidate
            break
        end
    end
    set -l glyph
    set -l code 2
    if string match -qir 'utf-?8' -- $locale
        switch $kind
            case ok
                set glyph ✔
            case fail
                set glyph ✖
            case '*'
                set glyph ›
        end
    else
        switch $kind
            case ok
                set glyph +
            case fail
                set glyph x
            case '*'
                set glyph '>'
        end
    end
    switch $kind
        case ok
            set code 32
        case fail
            set code 31
    end
    if test -t 2; and test -z "$NO_COLOR"; and test "$TERM" != dumb
        printf '\033[%sm%s\033[0m %s\n' $code $glyph $text >&2
    else
        printf '%s %s\n' $glyph $text >&2
    end
end

function _aws_need_granted
    test -r "$_AWS_ASSUME"; and return 0
    _aws_find_assume; and return 0
    _role_say fail "O Granted não está instalado. Rode role init para instalar."
    return 1
end

function assume
    _aws_need_granted; or return 1
    source $_AWS_ASSUME $argv
end

function _aws_profiles
    command roleroster $argv
end

# Exporta AWS_CONFIG_FILE e AWS_SHARED_CREDENTIALS_FILE escolhidos no onboarding
# (ficam no .env), para a AWS CLI e o Granted lerem os mesmos arquivos do roleroster.
# O que ja estiver exportado no terminal tem prioridade e nao e sobrescrito.
# As preferencias (ROLEROSTER_BROWSER, _CONSOLE_MODE, _CONSOLE_PROFILE, _RENEW_NOTICES) so
# existem no .env: sao sempre relidas, para `role browser` e `role notices` valerem na hora.
function _aws_load_files
    set -l env_file $_ROLE_HOME/.env
    test -r $env_file; or return 0
    for line in (cat $env_file)
        set line (string trim --left -- $line)
        set line (string replace -r '^export ' '' -- $line)
        string match -q -- '*=*' $line; or continue
        set -l key (string replace -r '=.*$' '' -- $line)
        set -l value (string replace -r '^[^=]*=' '' -- $line)
        set value (string replace -a -r '["\']' '' -- $value)
        set value (string replace -r '^~' $HOME -- $value)
        switch $key
            case AWS_CONFIG_FILE
                test -n "$AWS_CONFIG_FILE"; or set -gx AWS_CONFIG_FILE $value
            case AWS_SHARED_CREDENTIALS_FILE
                test -n "$AWS_SHARED_CREDENTIALS_FILE"; or set -gx AWS_SHARED_CREDENTIALS_FILE $value
            case ROLEROSTER_BROWSER
                set -gx ROLEROSTER_BROWSER $value
            case ROLEROSTER_CONSOLE_MODE
                set -gx ROLEROSTER_CONSOLE_MODE $value
            case ROLEROSTER_CONSOLE_PROFILE
                set -gx ROLEROSTER_CONSOLE_PROFILE $value
            case ROLEROSTER_RENEW_NOTICES
                set -gx ROLEROSTER_RENEW_NOTICES $value
        end
    end
end
_aws_load_files

# Flags que nao precisam de perfil: vao direto ao assume, sem sync nem busca.
function _aws_is_passthrough
    for arg in $argv
        switch $arg
            case --unset --un --help -h --version -v
                return 0
        end
    end
    return 1
end

# _aws_has_arg <flag> <args...>: algum dos args e exatamente <flag>?
function _aws_has_arg
    set -l needle $argv[1]
    set -e argv[1]
    contains -- $needle $argv
end

# Sincroniza os perfis. So cancelar a configuracao (Ctrl-C, saida 130)
# interrompe o comando; qualquer outra falha so avisa e segue com o config atual.
function _aws_sync
    _aws_profiles sync $argv
    set -l rc $status
    _aws_load_files # o onboarding pode ter acabado de gravar os arquivos da AWS
    test $rc -ne 130
end

# Formas curtas das nossas flags, escolhidas para nao colidir com as do assume.
# Deixa a forma longa em _AWS_ARGV.
function _aws_expand_short
    set -g _AWS_ARGV
    for arg in $argv
        switch $arg
            case -S
                set -a _AWS_ARGV --squads
            case -B
                set -a _AWS_ARGV --setup
            case -T
                set -a _AWS_ARGV --status
            case -U
                set -a _AWS_ARGV --unset
            case -n
                set -a _AWS_ARGV --no-renew
            case '*'
                set -a _AWS_ARGV $arg
        end
    end
end

# -i/--inactives e --no-renew sao nossas, nao do assume: saem dos argumentos.
# Deixa _AWS_INACTIVE_FLAG (vazio ou --inactives), _AWS_NO_RENEW (vazio ou 1)
# e o restante em _AWS_REST.
function _aws_split_flags
    set -g _AWS_INACTIVE_FLAG
    set -g _AWS_NO_RENEW ""
    set -g _AWS_REST
    for arg in $argv
        switch $arg
            case -i --inactives
                set -g _AWS_INACTIVE_FLAG --inactives
            case --no-renew
                set -g _AWS_NO_RENEW 1
            case '*'
                set -a _AWS_REST $arg
        end
    end
end

# Os termos digitados (ate a primeira flag) viram a busca. Deixa em _AWS_ARGS
# o perfil escolhido seguido das flags. Sem fzf, repassa tudo ao seletor do Granted.
function _aws_choose
    set -l terms
    set -l rest $argv
    while test (count $rest) -gt 0
        string match -q -- '-*' $rest[1]; and break
        set -a terms $rest[1]
        set -e rest[1]
    end
    set -l profile (_aws_profiles pick $_AWS_INACTIVE_FLAG $terms)
    set -l rc $status
    switch $rc
        case 0
            set -g _AWS_ARGS $profile $rest
        case 3
            set -g _AWS_ARGS $terms $rest
        case '*'
            return $rc
    end
    return 0 # o `set` nao zera o $status: sem isto a funcao devolveria o do comando anterior
end

# Assume o perfil e compartilha a sessao com todos os terminais e agentes: eles
# passam a usar o perfil "<perfil>-exported". $1: "new" (sso) ou "renewed" (renovacao).
function _aws_assume_and_share
    set -l kind $argv[1]
    set -e argv[1]
    source $_AWS_ASSUME $argv
    test -n "$AWS_PROFILE"; or return 1

    set -l original (string replace -r -- "$_AWS_EXPORT_SUFFIX\$" '' $AWS_PROFILE)
    set -l exported $original$_AWS_EXPORT_SUFFIX
    mkdir -p $_ROLE_HOME/state # instalacao nova: a pasta so existiria depois do session-save
    printf '%s\n' $exported >$_AWS_STATE_FILE
    _aws_profiles prune-exported $exported

    set -l save session-save $original $AWS_CREDENTIAL_EXPIRATION
    if test "$kind" = renewed
        set -a save --renewed
    else if test -n "$_AWS_NO_RENEW"
        set -a save --no-renew
    end
    _aws_profiles $save

    for name in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_CREDENTIAL_EXPIRATION AWS_SESSION_EXPIRATION
        set -e $name
    end
    set -gx AWS_PROFILE $exported
    return 0 # o `set -e` de uma variavel que nao existe deixa $status 4, e o `set -gx` nao o zera
end

function _role_sso
    _aws_expand_short $argv
    set -l args $_AWS_ARGV
    if _aws_has_arg --squads $args
        _aws_profiles squads
        return
    end
    if _aws_has_arg --setup $args
        _aws_profiles setup
        return
    end
    if _aws_has_arg --status $args
        _aws_profiles status
        return
    end
    if _aws_is_passthrough $args
        _aws_need_granted; or return 1
        source $_AWS_ASSUME $args
        if _aws_has_arg --unset $args; or _aws_has_arg --un $args
            rm -f $_AWS_STATE_FILE $_AWS_SESSION_FILE
            _aws_profiles prune-exported
        end
        return
    end

    _aws_split_flags $args
    _aws_sync $_AWS_INACTIVE_FLAG; or return 130
    _aws_need_granted; or return 1
    _aws_choose $_AWS_REST; or return
    _aws_assume_and_share new $_AWS_ARGS
end

# Abre o console conforme ROLEROSTER_CONSOLE_MODE (escolhido em `role browser`):
#   profile  um perfil fixo do navegador (--bp); padrao
#   account  um perfil por conta (padrao do Granted)
#   guest    janela isolada a cada abertura, sem deixar perfil
# O Firefox ignora o modo: o Granted abre cada conta num container.
function _role_open_console
    set -l mode profile
    test -n "$ROLEROSTER_CONSOLE_MODE"; and set mode $ROLEROSTER_CONSOLE_MODE
    test "$ROLEROSTER_BROWSER" = firefox; and set mode account
    switch $mode
        case guest
            _role_console_guest $argv
        case account
            source $_AWS_ASSUME -c $argv
        case '*'
            set -l browser_profile AWS
            test -n "$ROLEROSTER_CONSOLE_PROFILE"; and set browser_profile $ROLEROSTER_CONSOLE_PROFILE
            source $_AWS_ASSUME --bp $browser_profile -c $argv
    end
end

# A URL do console vem do Granted (-u) e a janela e aberta pelo roleroster, que a recebe
# pelo stdin (ela leva credenciais temporarias; nao deve aparecer como argumento).
function _role_console_guest
    set -l output (source $_AWS_ASSUME $argv -u)
    or return
    set -l url $output[-1] # a URL e a ultima linha; o assume pode imprimir avisos antes
    if not string match -q -- 'https://*' $url
        _role_say fail "O Granted não devolveu a URL do console; use role browser para outro modo."
        return 1
    end
    printf '%s\n' $url | _aws_profiles open-console
end

function _role_console
    _aws_expand_short $argv
    set -l args $_AWS_ARGV
    if _aws_has_arg --squads $args
        _aws_profiles squads
        return
    end
    if _aws_is_passthrough $args
        _aws_need_granted; or return 1
        source $_AWS_ASSUME $args
        return
    end
    _aws_split_flags $args
    _aws_sync $_AWS_INACTIVE_FLAG; or return 130
    _aws_need_granted; or return 1
    _aws_choose $_AWS_REST; or return
    _role_open_console $_AWS_ARGS
end

function role
    set -l first ""
    test (count $argv) -gt 0; and set first $argv[1]
    set -l rest $argv[2..-1]
    switch $first
        case sso
            _role_sso $rest
        case console
            _role_console $rest
        case assume
            _aws_need_granted; or return 1
            source $_AWS_ASSUME $rest
        case '*'
            command roleroster $argv
            set -l rc $status
            # preferencias gravadas no .env passam a valer neste terminal na hora
            switch $first
                case browser notices setup
                    _aws_load_files
            end
            return $rc
    end
end

# ---------------------------------------------------------------- antes de cada prompt

# O terminal segue o perfil compartilhado. Sem o arquivo (depois de `role sso --unset`
# em outro terminal), larga o perfil exportado.
function _aws_follow_shared_profile
    if not test -r $_AWS_STATE_FILE
        if string match -q -- "*$_AWS_EXPORT_SUFFIX" "$AWS_PROFILE"
            set -e AWS_PROFILE
        end
        return 0
    end
    set -l shared (string trim -- (head -n 1 $_AWS_STATE_FILE))
    if test -n "$shared"; and test "$AWS_PROFILE" != "$shared"
        set -gx AWS_PROFILE $shared
    end
    return 0
end

function _role_tick
    set -g _ROLE_NOW (date +%s)
end

# Trava velha, de um terminal que morreu no meio da renovacao (mais de 3 minutos).
function _aws_lock_is_stale
    set -l old (find $_AWS_RENEW_LOCK -maxdepth 0 -mmin +3 2>/dev/null)
    test -n "$old"
end

# Renovacao automatica: um terminal por vez (trava) refaz o assume do perfil.
# Se o login SSO tiver vencido, o proprio assume abre o navegador.
# `role notices off` esconde so a linha de andamento; falhas e avisos continuam aparecendo.
function _aws_renew_locked
    set -l profile $argv[1]
    if not mkdir $_AWS_RENEW_LOCK 2>/dev/null
        _aws_lock_is_stale; and rmdir $_AWS_RENEW_LOCK 2>/dev/null
        return 0
    end
    if test "$ROLEROSTER_RENEW_NOTICES" != 0
        _role_say note "Renovando as credenciais de $profile..."
    end
    set -g _AWS_NO_RENEW ""
    if not _aws_assume_and_share renewed $profile
        _role_say fail "Não consegui renovar as credenciais; rode role sso. Nova tentativa neste terminal em 5 min."
        _role_tick
        set -g _AWS_RENEW_RETRY_AT (math $_ROLE_NOW + $_AWS_RENEW_BACKOFF)
    end
    rmdir $_AWS_RENEW_LOCK 2>/dev/null
end

# Se faltam menos de 10 min para a credencial vencer e a janela de 12 h do ultimo sso
# ainda vale, renova. So le um arquivo pequeno.
function _aws_auto_renew
    test -r $_AWS_SESSION_FILE; or return 0
    set -l profile ""
    set -l expires ""
    set -l renew_until ""
    set -l renew ""
    for line in (cat $_AWS_SESSION_FILE)
        switch $line
            case 'profile=*'
                set profile (string replace 'profile=' '' -- $line)
            case 'expires=*'
                set expires (string replace 'expires=' '' -- $line)
            case 'renew_until=*'
                set renew_until (string replace 'renew_until=' '' -- $line)
            case 'renew=*'
                set renew (string replace 'renew=' '' -- $line)
        end
    end
    test "$renew" = 1; and test -n "$profile"; or return 0
    string match -qr '^[0-9]+$' -- $expires; or return 0
    string match -qr '^[0-9]+$' -- $renew_until; or return 0
    _role_tick
    test $_ROLE_NOW -ge (math $expires - $_AWS_RENEW_MARGIN); and test $_ROLE_NOW -ge $_AWS_RENEW_RETRY_AT; or return 0

    if test $_ROLE_NOW -ge $renew_until
        _role_say note "A renovação automática (12 h) terminou; rode role sso para continuar."
        set -g _AWS_RENEW_RETRY_AT (math $_ROLE_NOW + 3600) # avisa no maximo uma vez por hora
        return 0
    end
    _aws_renew_locked $profile
end

# ---------------------------------------------------------------- gancho do prompt

function _role_prompt --on-event fish_prompt
    _aws_follow_shared_profile
    _aws_auto_renew
end
_aws_follow_shared_profile
