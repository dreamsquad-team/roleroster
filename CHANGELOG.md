# Changelog

Segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/) e o [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [Não lançado]

## [0.4.0] - 2026-11-27

### Adicionado
- O preset aceita perfis SSO diretos (`[[aws.profiles]]` com `name`, `account_id`, `role` e `region` opcional), para a empresa entregar contas como Sandbox e Production a todo mundo. O sync grava esses perfis num bloco próprio do config da AWS (`# >>> preset ... <<<`) e o reescreve a cada uso, então mudar o preset muda os perfis de quem o instalou e remover um perfil do preset o remove do config. Um perfil escrito à mão com o mesmo nome vence, e um perfil do Confluence com o mesmo nome cede ao do preset. `role preset` mostra os perfis antes de pedir a confirmação.

## [0.3.0] - 2026-11-27

### Adicionado
- Suporte ao fish 3.2 ou superior, no macOS e no Linux. O `role init` detecta o fish (ou use `--shell fish`), grava o bloco em `~/.config/fish/config.fish` e copia o `roleroster.fish`, que faz o mesmo que o `roleroster.sh`: `role sso`, `console` e `assume`, o aviso de Granted ausente e a renovação automática antes de cada prompt. Testado no fish 3.6 e no 4.0.
- `role init` e `role doctor --fix` oferecem instalar a AWS CLI (opcional). No macOS com Homebrew usam `brew install awscli`. No Linux baixam o instalador oficial da versão 2.42.5, conferem o SHA-256 fixado no pacote e instalam em `~/.local/aws-cli`, sem `sudo`. `--aws-cli` instala sem perguntar, `--no-aws-cli` não oferece, e `--yes` sozinho não a instala.

### Alterado
- A mensagem "precisa do shell" também cita o `source` do fish.
- CI: `actions/checkout` e `actions/setup-python` na versão 7, `actions/upload-artifact` na 7 e `actions/download-artifact` na 8. O CI instala o fish para rodar os testes dele.

## [0.2.1] - 2026-11-27

### Corrigido
- Sem o Granted, `role sso`, `role console` e `role assume` terminavam com `no such file or directory: .../assume`. Agora avisam "O Granted não está instalado. Rode role init para instalar." e `sso` e `console` param logo depois do sync, antes de pedir o perfil.
- `role sync`, `role squads` e `role setup` (portanto o primeiro `role sso`) oferecem instalar o Granted quando ele falta, e não só o `role init`.
- Um Granted instalado depois que o terminal abriu é reconhecido no uso seguinte, sem abrir outro terminal.
- O navegador escolhido no roleroster (que fica no `.env`) é regravado no config do Granted quando o config não o tem, por exemplo se o Granted foi instalado depois da escolha ou o `~/.granted` foi apagado. Antes, o Granted fazia as perguntas de primeira execução dele ("Use Firefox as default Granted browser?") no meio do `role sso`. O `role doctor` avisa e o `--fix` regrava.

## [0.2.0] - 2026-11-27

### Adicionado
- `role init` e `role doctor --fix` instalam o Granted quando ele falta, sempre com confirmação (`role init --yes` aceita sem perguntar). No macOS com Homebrew usam o `brew`. No Linux, e no macOS sem Homebrew, baixam o binário da release 0.40.0, conferem o SHA-256 fixado no pacote e copiam `granted`, `assume` e `assumego` para `~/.local/bin`, sem `sudo`.
- O script de shell reconhece o Granted em `~/.local/bin` e acrescenta essa pasta ao `PATH` quando precisa.

### Alterado
- README reescrito com as formas de instalação (uv, pipx, pip em ambiente virtual, GitHub e código-fonte), instalação do Granted por sistema, atualização, remoção e problemas comuns.
- O aviso de Granted ausente mostra o comando de instalação do seu sistema e aponta para `role init`.

## [0.1.0] - 2026-11-27

### Adicionado
- Comando `role` (e `roleroster`): `sso`, `console`, `assume`, `sync`, `squads`, `setup`, `status`, `browser`, `notices`, `token`, `doctor`, `preset` e `init`. O que não é do roleroster vai para o Granted.
- Sincronização dos perfis de Switch Role das páginas do Confluence para o `~/.aws/config` (bloco gerado, seção manual preservada).
- Onboarding em passos, com perguntas só do que não dá para adivinhar.
- Preset de empresa (TOML) para dispensar perguntas que a empresa já sabe: `role preset` e `role init --preset`.
- Navegador do console e do login SSO (Chrome, Brave, Firefox, Edge) e três modos de abrir as contas: perfil fixo, visitante (janela isolada) e um perfil por conta.
- Renovação automática das credenciais, com `role notices on|off` para silenciar a linha de andamento.
- Token do Confluence no cofre do sistema (Keychain, Credential Manager, Secret Service), com `.env` como alternativa.
- `role doctor` com `--fix` e `--online`.
- Suporte a zsh e bash (3.2 ou superior) em macOS e Linux.
