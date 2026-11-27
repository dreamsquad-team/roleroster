# Contribuindo

## Ambiente

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

Requer Python 3.11 ou superior, e zsh e bash instalados para os testes de shell (eles pulam o que não existir).

## Regras que os testes protegem

- **Nenhum teste toca no seu computador.** `tests/conftest.py` troca o `HOME`, a pasta do roleroster e o cofre por versões temporárias. Não chame o Granted, o Keychain ou a rede de dentro de um teste sem isso.
- **Nenhum dado de empresa no pacote.** O teste `NoCompanyDataInThePackageTest` falha se um nome de empresa, URL ou ID entrar em `src/`. Valores de uma empresa vão em um preset, fora deste repositório.
- **Um script de shell só** (`src/roleroster/shell/roleroster.sh`) para zsh e bash 3.2+. Use só o que os dois entendem; o que é específico fica nos blocos por shell. Os testes de shell rodam nos dois.
- **Mensagens:** português, sem travessão, com `✔` (feito), `!` (aviso), `✖` (erro) e `›` (informação).

## Fluxo

1. Abra uma issue descrevendo o problema ou a ideia.
2. Faça a mudança com testes. `ruff check .` precisa passar, e a cobertura não pode cair de 85% (`pytest --cov`).
3. Atualize o `CHANGELOG.md`.
4. Abra o pull request.
