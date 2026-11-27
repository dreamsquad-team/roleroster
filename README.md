# Roleroster

Mantém os perfis de Switch Role do `~/.aws/config` sincronizados com as páginas do Confluence da empresa, e deixa o [Granted](https://docs.granted.dev) fazendo o acesso. Uma fonte só para as contas dos clientes.

## Estado

Versão 0.1 (alfa), ainda não publicada no PyPI. Funciona em macOS e Linux com **zsh** e **bash** (3.2 ou superior). Fish e Windows ainda não.

## Instalar

Requer Python 3.11 ou superior, o [Granted](https://docs.granted.dev/getting-started) e, opcionalmente, o `fzf` (melhora a busca de perfis). `role doctor` confere tudo isso e mostra o comando de instalação do que faltar.

```sh
uv tool install roleroster     # o uv baixa o Python sozinho se faltar
# ou: pipx install roleroster

role init                      # liga no rc do seu shell (zsh ou bash); os comandos ficam sempre atrás do `role`
role doctor                    # confere a instalação e diz como consertar o que faltar
```

Abra um novo terminal e rode `role sso`: na primeira vez o roleroster conduz a configuração em passos.

Para desfazer: `role init --uninstall` (o `.env` e o estado ficam). Para testar sem encostar nos seus arquivos, aponte tudo para uma pasta temporária com `export ROLEROSTER_HOME=/tmp/rr_teste`.

### Preset da sua empresa

Sem preset, o roleroster pergunta o que não dá para adivinhar: a URL do portal SSO, a conta e o role do perfil base e a pasta raiz do Confluence. Uma empresa pode distribuir um arquivo TOML (veja [examples/preset.example.toml](examples/preset.example.toml)) que responde essas perguntas de antemão:

```sh
role init --preset https://exemplo.com/roleroster.toml    # instala o preset e liga o shell
role preset                                               # mostra o preset instalado
role preset -r                                            # remove
```

O preset define só URLs, nomes e as convenções das páginas do Confluence (o título da seção com os perfis e o prefixo das pastas de projetos inativos). O roleroster mostra o conteúdo e pede confirmação antes de instalar; só aceite presets que a sua empresa publicou. O que você responder no onboarding vale mais do que o preset.

## Comandos

```
role sso [termos]       sincroniza, escolhe o perfil com busca e assume (renova sozinho por até 12 h)
role console [termos]   sincroniza, escolhe o perfil com busca e abre o console
role assume <perfil>    o assume do Granted, neste terminal
role sync | squads | setup | status | browser | token | notices | doctor | init
role granted <comando>  qualquer comando do Granted; o que não for do roleroster também vai para ele
role -h
```

### Token do Confluence

O token fica no cofre do sistema (Keychain no macOS, Credential Manager no Windows, Secret Service no Linux). Sem cofre disponível, ele cai no `.env` (permissão 600). `role token` troca o token; `role doctor --fix` move para o cofre um token que ainda esteja no `.env`.

### Avisos de renovação

`role notices off` esconde a linha "Renovando as credenciais..." que aparece quando a renovação automática roda. Falhas e o aviso de fim da janela de 12 h continuam aparecendo. `role notices on` volta ao normal.

### Navegador e console

Na primeira configuração (ou depois, com `role browser`) você escolhe o navegador do console e o do login SSO (Chrome, Brave, Firefox ou Edge) e como as contas abrem no console:

| Modo | O que faz |
|---|---|
| Perfil fixo (padrão) | todas as contas abrem num mesmo perfil do navegador (nome padrão `AWS`) |
| Visitante | cada `role console` abre uma janela isolada, num diretório de dados descartável: não cria perfil, e várias contas ficam abertas ao mesmo tempo (só Chrome, Brave e Edge) |
| Um perfil por conta | o padrão do Granted: cria um perfil do navegador para cada conta |

O Firefox ignora o modo: o Granted abre cada conta num container e precisa da [extensão do Granted](https://addons.mozilla.org/en-GB/firefox/addon/granted).

As janelas do modo visitante ficam em `ROLEROSTER_HOME/browser` e as com mais de 2 dias são apagadas na próxima abertura.

`sso` e `console` aceitam `-i` (inclui projetos inativos). `sso` aceita `--no-renew` e `--unset`.

## Onde ficam os arquivos

Tudo em `ROLEROSTER_HOME` (padrão `~/.config/roleroster`, ou `$XDG_CONFIG_HOME/roleroster`):

| Caminho     | Conteúdo                                                  |
| ----------- | ---------------------------------------------------------- |
| `.env`    | URL, e-mail, token e squads do Confluence (permissão 600) |
| `state/`  | sessão compartilhada entre terminais                      |
| `shell/`  | script que o `role init` liga no `.zshrc`              |
| `backup/` | a versão anterior do config da AWS e do `.zshrc`        |

Na primeira execução o roleroster conduz a configuração em passos (arquivos da AWS, acesso SSO, Confluence, squads) e só pergunta o que não dá para adivinhar. Os arquivos da AWS ficam isolados (`config_roleroster` e `credentials_roleroster`) a menos que você escolha outros. Para mudar os valores que usam o padrão (sessão e regiões do SSO, nome e role do perfil base, perfis SSO extras, pasta raiz do Confluence), use `role setup --advanced`.

## Desenvolvimento

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```
