# Contribuindo

## Ambiente

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Requer Python 3.11 ou superior, e zsh, bash e fish instalados para os testes de shell (eles pulam o que não existir). O fish mínimo é o 3.2.

## Regras que os testes protegem

- **Nenhum teste toca no seu computador.** `tests/conftest.py` troca o `HOME`, a pasta do roleroster e o cofre por versões temporárias. Não chame o Granted, o Keychain ou a rede de dentro de um teste sem isso.
- **Nenhum dado de empresa no pacote.** O teste `NoCompanyDataInThePackageTest` falha se um nome de empresa, URL ou ID entrar em `src/`. Valores de uma empresa vão em um preset, fora deste repositório.
- **Um script de shell para zsh e bash** (`src/roleroster/shell/roleroster.sh`, bash 3.2+) e outro para o fish (`roleroster.fish`). No primeiro, use só o que os dois shells entendem; o que é específico fica nos blocos por shell. Uma mudança de comportamento vale para os dois arquivos, e as mesmas classes de teste em `tests/test_shell.py` rodam em zsh, bash e fish.
- **Pegadinha do fish:** `set` e `set -e` não zeram o `$status`. Uma função que termina neles devolve o erro de um comando anterior, então termine com `return 0` quando o status importar.
- **Mensagens:** português, sem travessão, com `✔` (feito), `!` (aviso), `✖` (erro) e `›` (informação).

## Subir a versão do Granted

O roleroster instala o Granted 0.40.0 com o SHA-256 fixado em `src/roleroster/granted_install.py`. Para testar outra versão, atualize `VERSION` e os 4 valores de `SHA256` com os hashes do `checksums.txt` da release (`granted_<versão>_<sistema>_<arquitetura>.tar.gz`), rode os testes e confirme o download real num contêiner Linux. O `role doctor` compara a versão instalada com essa constante.

## Subir a versão da AWS CLI

A AWS CLI instalada no Linux tem versão e SHA-256 fixados em `src/roleroster/awscli_install.py`. Para testar outra versão, atualize `VERSION` e os 2 valores de `SHA256` (baixe `awscli-exe-linux-x86_64-<versão>.zip` e `awscli-exe-linux-aarch64-<versão>.zip` em awscli.amazonaws.com e rode `shasum -a 256`), rode os testes e confirme o `role init --aws-cli` num contêiner Linux.

## Fluxo

1. Abra uma issue descrevendo o problema ou a ideia.
2. Faça a mudança com testes. `ruff check .` precisa passar, e a cobertura não pode cair de 85% (`pytest --cov`).
3. Atualize o `CHANGELOG.md`.
4. Abra o pull request.
