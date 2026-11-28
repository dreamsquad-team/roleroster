# Roleroster

Mantém os perfis de Switch Role do `~/.aws/config` sincronizados com as páginas do Confluence da empresa e usa o [Granted](https://docs.granted.dev) para o acesso. Em vez de copiar à mão o perfil de cada cliente, você roda `role sso`, escolhe a conta na busca e já está logado, com a credencial renovando sozinha por até 12 h.

Versão 0.3 (alfa). Funciona em macOS e Linux, com zsh, bash 3.2 ou superior e fish 3.2 ou superior. O Windows ainda não.

## Em 30 segundos

```sh
uv tool install roleroster     # ou pipx install roleroster
role init                      # liga o shell e oferece instalar o Granted se faltar
# abra um terminal novo
role sso                       # primeira vez: configuração em passos, depois escolha o perfil
```

O resto deste documento traz cada passo com as variações por sistema e por forma de instalação.

## O que você precisa

| Item | Obrigatório | Observação |
|---|---|---|
| macOS ou Linux (x86_64 ou arm64) | sim | com zsh, bash ou fish. O Windows ainda não |
| Python 3.11 ou superior | sim | o `uv` baixa o Python sozinho se faltar |
| Granted 0.40 | sim | o `role init` instala, com a sua confirmação. Veja a seção Instalar o Granted |
| Token de API do Atlassian | sim | o roleroster abre a página para gerar e guarda o token no cofre do sistema |
| fzf | não | melhora a busca de perfis. Sem ele, a busca usa o seletor do Granted |
| AWS CLI | não | o roleroster não a usa. Você a usa depois do login, com os perfis que ele criou. O `role init` oferece instalar |

O comando `role doctor` confere tudo isso e mostra o que falta.

## Instalar o roleroster

Escolha uma forma. Todas instalam o mesmo pacote e os comandos `role` e `roleroster`.

### Com uv (recomendado)

```sh
uv tool install roleroster                  # instala
uv tool install roleroster==0.4.1           # uma versão específica
uv tool upgrade roleroster                  # atualiza
uv tool uninstall roleroster                # remove
```

Sem o `uv`, instale pelo [guia oficial](https://docs.astral.sh/uv/getting-started/installation/). No macOS com Homebrew, `brew install uv`.

### Com pipx

```sh
pipx install roleroster
pipx upgrade roleroster
pipx uninstall roleroster
```

Sem o `pipx`, no macOS use `brew install pipx && pipx ensurepath`. No Linux, use o gerenciador da distribuição (`sudo apt install pipx`) ou `python3 -m pip install --user pipx`. Depois do `pipx ensurepath`, abra um terminal novo.

### Com pip, num ambiente virtual

Para quem não usa `uv` nem `pipx`. O `pip install` direto no Python do sistema costuma ser bloqueado em macOS e Linux recentes (PEP 668), então use um ambiente próprio:

```sh
python3 -m venv ~/.roleroster-venv
~/.roleroster-venv/bin/pip install roleroster
mkdir -p ~/.local/bin
ln -sf ~/.roleroster-venv/bin/role ~/.local/bin/role
ln -sf ~/.roleroster-venv/bin/roleroster ~/.local/bin/roleroster
```

Confira que `~/.local/bin` está no seu `PATH`. Para atualizar, rode `~/.roleroster-venv/bin/pip install -U roleroster`. Para remover, apague `~/.roleroster-venv` e os dois links.

### Direto do GitHub

Para testar uma versão que ainda não saiu no PyPI, ou uma branch:

```sh
uv tool install git+https://github.com/dreamsquad-team/roleroster@v0.4.1
pipx install git+https://github.com/dreamsquad-team/roleroster@main
```

### A partir do código-fonte

```sh
git clone https://github.com/dreamsquad-team/roleroster.git
cd roleroster
uv tool install .          # ou: pipx install .
```

Para desenvolver, veja a seção Desenvolvimento.

## Instalar o Granted

O roleroster mantém os perfis, e o Granted faz o login SSO e o `assume`. Ele não está no PyPI, então o `pip`, o `uv` e o `pipx` não o instalam.

O jeito mais curto é deixar o roleroster instalar:

```sh
role init            # pergunta antes de instalar
role init --yes      # instala sem perguntar
role doctor --fix    # também instala, se o Granted estiver faltando
```

O roleroster escolhe o caminho pelo sistema.

| Sistema | O que ele faz |
|---|---|
| macOS com Homebrew | `brew tap fwdcloudsec/granted` e `brew install fwdcloudsec/granted/granted` |
| macOS sem Homebrew | baixa o binário da release 0.40.0 no GitHub e copia para `~/.local/bin` |
| Linux (x86_64 ou arm64) | baixa o binário da release 0.40.0 no GitHub e copia para `~/.local/bin`, sem `sudo` |

No download, o roleroster confere o SHA-256 do arquivo contra o valor fixado dentro do pacote. Se o hash não bater, nada é instalado. Sem terminal para perguntar, ele não instala sozinho e mostra o que fazer.

### Instalar o Granted à mão

macOS:

```sh
brew tap fwdcloudsec/granted
brew install fwdcloudsec/granted/granted
```

Linux, sem `sudo`, no mesmo formato que o roleroster usa:

```sh
V=0.40.0
ARCH=$(uname -m); [ "$ARCH" = aarch64 ] && ARCH=arm64      # x86_64 ou arm64
curl -fsSL -o /tmp/granted.tar.gz \
  "https://github.com/fwdcloudsec/granted/releases/download/v${V}/granted_${V}_linux_${ARCH}.tar.gz"
mkdir -p ~/.local/bin
tar -xzf /tmp/granted.tar.gz -C ~/.local/bin granted assume assume.fish
ln -sf granted ~/.local/bin/assumego
```

Se preferir pacote do sistema (`.deb`, `.rpm`), veja as instruções do [Granted](https://docs.granted.dev/getting-started). O roleroster funciona com qualquer um deles, desde que `granted` e `assume` estejam no `PATH` ou em `~/.local/bin`.

Para conferir: `granted --version` deve mostrar a versão. O roleroster foi testado com a 0.40.0. Em outra versão o `role doctor` mostra um aviso, e a versão é o primeiro suspeito se algo estranhar.

## Instalar a AWS CLI (opcional)

O roleroster não usa a AWS CLI. Ela serve para você trabalhar com os perfis depois do login. O `role init` pergunta se quer instalar, e a resposta padrão é sim.

```sh
role init                 # pergunta "Instalar a AWS CLI agora?"
role init --aws-cli       # instala sem perguntar
role init --no-aws-cli    # não oferece
role doctor --fix         # também instala, se ela estiver faltando
```

O `--yes` sozinho não instala a AWS CLI, porque ela é opcional. Sem terminal para perguntar, o roleroster só avisa que o `--aws-cli` existe.

| Sistema | O que ele faz |
|---|---|
| macOS com Homebrew | `brew install awscli` |
| Linux (x86_64 ou arm64) | baixa o instalador oficial da versão 2.42.5, confere o SHA-256 fixado no pacote e roda o `aws/install` dele em `~/.local/aws-cli`, com o link em `~/.local/bin`, sem `sudo` |
| macOS sem Homebrew | mostra o [guia da AWS](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |

Instalação manual:

```sh
brew install awscli          # macOS
```

No Linux, siga o [guia da AWS](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).

## Instalar o fzf (opcional)

O fzf melhora a busca de perfis. Sem ele, a busca usa o seletor do Granted.

```sh
brew install fzf             # macOS
sudo apt install fzf         # Debian e Ubuntu
sudo dnf install fzf         # Fedora
```

## Ligar o shell e configurar

```sh
role init
```

O `role init` grava um bloco no arquivo de configuração do seu shell, depois de guardar uma cópia em `ROLEROSTER_HOME/backup`. O arquivo depende do shell e do sistema.

| Shell | macOS | Linux |
|---|---|---|
| zsh | `~/.zshrc` | `~/.zshrc` |
| bash | `~/.bash_profile` | `~/.bashrc` |
| fish | `~/.config/fish/config.fish` | `~/.config/fish/config.fish` |

Use `--shell bash`, `--shell zsh` ou `--shell fish` para escolher, `--rc <arquivo>` para outro arquivo e `--dry-run` para só ver o bloco. O fish usa um script próprio, `roleroster.fish`, que faz o mesmo que o `roleroster.sh` do zsh e do bash. Abra um terminal novo (ou rode `source ~/.zshrc`, `source ~/.config/fish/config.fish` no fish) e execute:

```sh
role sso
```

Na primeira vez o roleroster conduz a configuração em 5 passos e leva cerca de 2 minutos: onde gravar os perfis, acesso SSO, navegador, Confluence e squads. Ele só pergunta o que não dá para adivinhar. Os arquivos da AWS ficam isolados (`config_roleroster` e `credentials_roleroster`) a menos que você escolha outros. Para mudar valores que usam o padrão, rode `role setup --advanced`.

Os comandos `sso`, `console` e `assume` mudam o terminal atual, por isso vivem numa função de shell. Eles só existem depois do `role init` e de um terminal novo.

## Preset da sua empresa

Sem preset, o roleroster pergunta a URL do portal SSO, a conta e o role do perfil base e a pasta raiz do Confluence. Uma empresa pode distribuir um arquivo TOML que responde essas perguntas de antemão. Veja o modelo em [examples/preset.example.toml](https://github.com/dreamsquad-team/roleroster/blob/main/examples/preset.example.toml).

```sh
role init --preset ~/Downloads/empresa.toml               # arquivo local
role init --preset https://exemplo.com/roleroster.toml    # ou uma URL https
role preset                                               # mostra o preset instalado
role preset -r                                            # remove
```

O preset define só URLs, nomes e as convenções das páginas do Confluence, como o título da seção com os perfis e o prefixo das pastas de projetos inativos. Ele também pode trazer perfis SSO diretos, como Sandbox e Production, com uma seção `[[aws.profiles]]` por perfil (`name`, `account_id`, `role` e, opcional, `region`). Esses perfis entram num bloco próprio do arquivo de config da AWS, que o sync reescreve a cada uso: ao mudar o preset, quem já o instalou recebe a mudança no próximo `role sso`. Um perfil escrito à mão com o mesmo nome vence. O roleroster mostra o conteúdo e pede confirmação antes de instalar, e `--yes` pula a pergunta. Instale só presets que a sua empresa publicou. O que você responde no onboarding vale mais do que o preset.

## Comandos

```
role sso [termos]       sincroniza, escolhe o perfil com busca e assume (renova sozinho por até 12 h)
role console [termos]   sincroniza, escolhe o perfil com busca e abre o console
role assume <perfil>    o assume do Granted, neste terminal
role sync | squads | setup | status | browser | token | notices | doctor | preset | init
role granted <comando>  qualquer comando do Granted; o que não for do roleroster também vai para ele
role -h
```

`sso` e `console` aceitam `-i` (inclui projetos inativos). `sso` aceita ainda `--no-renew` e `--unset`.

### Token do Confluence

O token fica no cofre do sistema: Keychain no macOS, Secret Service no Linux e Credential Manager no Windows. Sem cofre disponível, cai no `.env` com permissão 600. O comando `role token` troca o token, e `role doctor --fix` move para o cofre um token que ainda esteja no `.env`.

O e-mail precisa ser o da conta Atlassian. Se o Confluence recusar o token, o roleroster pergunta o e-mail de novo antes da próxima tentativa, porque um erro de digitação ali é a causa mais comum.

### Avisos de renovação

`role notices off` esconde a linha "Renovando as credenciais..." que aparece quando a renovação automática roda. Falhas e o aviso de fim da janela de 12 h continuam aparecendo. `role notices on` volta ao normal.

### Navegador e console

Na primeira configuração, ou depois com `role browser`, você escolhe o navegador do console e o do login SSO (Chrome, Brave, Firefox ou Edge) e como as contas abrem no console.

| Modo | O que faz |
|---|---|
| Perfil fixo (padrão) | todas as contas abrem num mesmo perfil do navegador, de nome `AWS` |
| Visitante | cada `role console` abre uma janela isolada, num diretório de dados descartável. Não cria perfil, e várias contas ficam abertas ao mesmo tempo. Só Chrome, Brave e Edge |
| Um perfil por conta | o padrão do Granted, que cria um perfil do navegador para cada conta |

O Firefox ignora o modo. O Granted abre cada conta num container e precisa da [extensão do Granted](https://addons.mozilla.org/en-GB/firefox/addon/granted). As janelas do modo visitante ficam em `ROLEROSTER_HOME/browser`, e as com mais de 2 dias são apagadas na próxima abertura.

## Onde ficam os arquivos

Tudo fica em `ROLEROSTER_HOME`, que por padrão é `~/.config/roleroster` (ou `$XDG_CONFIG_HOME/roleroster`).

| Caminho | Conteúdo |
|---|---|
| `.env` | URL, e-mail e squads do Confluence, e o token se não houver cofre (permissão 600) |
| `preset.toml` | o preset da empresa, se você instalou um |
| `state/` | sessão compartilhada entre terminais |
| `shell/` | script que o `role init` liga no rc do shell |
| `backup/` | a versão anterior do rc do shell, guardada pelo `role init` |
| `browser/` | janelas do modo visitante |

Para testar sem encostar nos seus arquivos, aponte tudo para uma pasta temporária com `export ROLEROSTER_HOME=/tmp/rr_teste`.

## Atualizar

Atualize o pacote pelo mesmo gerenciador que instalou (`uv tool upgrade roleroster` ou `pipx upgrade roleroster`). Na primeira execução depois do upgrade, o roleroster atualiza sozinho o script de shell e avisa para abrir um terminal novo.

## Remover

1. Tire o roleroster do shell: `role init --uninstall`. O `.env` e o estado ficam.
2. Desinstale o pacote: `uv tool uninstall roleroster`, `pipx uninstall roleroster` ou apague o ambiente virtual.
3. Apague os arquivos, se quiser começar do zero: `rm -rf ~/.config/roleroster ~/.aws/config_roleroster ~/.aws/credentials_roleroster`.
4. Apague o token do cofre.

   macOS: `security delete-generic-password -s roleroster -a confluence-token`

   Linux: `secret-tool clear service roleroster username confluence-token`

O roleroster não remove o Granted nem a AWS CLI. Para o Granted instalado por ele em `~/.local/bin`, apague `granted`, `assume`, `assume.fish` e `assumego` dessa pasta. Para a AWS CLI instalada por ele no Linux, apague `~/.local/aws-cli` e os links `aws` e `aws_completer` em `~/.local/bin`. Com Homebrew, `brew uninstall granted awscli`.

## Problemas comuns

`role sso precisa do shell do roleroster`. O terminal onde você rodou ainda não carregou o bloco do `role init`. Abra um terminal novo ou rode `source ~/.zshrc` (`source ~/.bash_profile` no bash do macOS, `source ~/.config/fish/config.fish` no fish). Se você usa um terminal que restaura a sessão, abra uma aba criada depois do `role init`.

`O Confluence recusou o token`. Confira o e-mail da conta Atlassian e se o token foi copiado inteiro. O token novo se gera em https://id.atlassian.com/manage-profile/security/api-tokens, com "Create API token" e sem scopes.

`Granted não está instalado`. Rode `role init` (ou `role doctor --fix`) para instalar, ou veja a seção Instalar o Granted à mão. Se o `role doctor` achar o Granted mas o `granted` não abre no terminal, falta `~/.local/bin` no `PATH`. Acrescente `export PATH="$PATH:$HOME/.local/bin"` ao rc do shell.

O `pipx` ou o `uv` instalou uma versão antiga logo depois de um release. O índice do PyPI e o cache do `pip` levam alguns minutos para mostrar a versão nova. Instale sem cache, ou fixe a versão:

```sh
pipx install --pip-args="--no-cache-dir" roleroster==0.4.1
uv tool install --no-cache roleroster==0.4.1
```

Se já instalou a antiga, use `pipx install --force ...` ou `uv tool install --reinstall ...` com os mesmos argumentos. Confira com `role --version`.

`role: command not found`. Depois de `pipx install` ou `uv tool install`, o `~/.local/bin` precisa estar no `PATH`. Rode `pipx ensurepath` ou `uv tool update-shell` e abra um terminal novo.

Qualquer outro caso: rode `role doctor`. Ele confere a instalação, a configuração e o Confluence, e `role doctor --fix` aplica os consertos automáticos. Com `--online`, ele também testa o token no Confluence.

## Segurança

O roleroster não envia dados para lugar nenhum além do seu Confluence e dos downloads do Granted (GitHub) e da AWS CLI (awscli.amazonaws.com). O token fica no cofre do sistema ou no `.env` com permissão 600. A instalação do Granted e da AWS CLI só acontece com a sua confirmação, e cada arquivo baixado é conferido pelo SHA-256 fixado no pacote. Um preset é mostrado por inteiro antes de entrar. Para relatar uma vulnerabilidade, use o [relato privado do GitHub](https://github.com/dreamsquad-team/roleroster/security/advisories/new).

## Desenvolvimento

```sh
git clone https://github.com/dreamsquad-team/roleroster.git
cd roleroster
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/ruff check .
.venv/bin/python -m pytest --cov
```

Os testes não tocam nos seus arquivos: rodam com um `HOME` temporário. Veja [CONTRIBUTING.md](https://github.com/dreamsquad-team/roleroster/blob/main/CONTRIBUTING.md) e o [CHANGELOG](https://github.com/dreamsquad-team/roleroster/blob/main/CHANGELOG.md).

## Licença

MIT. Veja [LICENSE](https://github.com/dreamsquad-team/roleroster/blob/main/LICENSE).
