# Política de segurança

## Reportar uma vulnerabilidade

Não abra uma issue pública. Use o recurso de [relato privado de vulnerabilidades](https://github.com/dreamsquad-team/roleroster/security/advisories/new) do GitHub neste repositório, com os passos para reproduzir e a versão afetada.

Respondemos em até 7 dias.

## O que o roleroster guarda

- **Token de API do Confluence:** no cofre do sistema (Keychain, Credential Manager ou Secret Service). Onde não há cofre, no `.env` com permissão 600.
- **Credenciais temporárias da AWS:** quem as gera é o Granted. O roleroster só exporta o perfil `<perfil>-exported` para o arquivo de credenciais que você escolheu.
- **Presets:** só URLs e nomes, nunca segredos. Um preset aponta o portal de login, então o roleroster mostra o conteúdo e pede confirmação antes de instalar. Só instale presets que a sua empresa publicou.
- **Janelas do modo visitante:** a URL do console, que carrega credenciais temporárias, é passada pelo stdin (nunca como argumento de processo) e a janela usa um diretório de dados descartável, apagado depois de 2 dias.

O roleroster não envia telemetria.
