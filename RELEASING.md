# Como publicar uma versão

A publicação é feita pelo GitHub Actions (`.github/workflows/release.yml`) com *Trusted Publishing*: nenhum token fica guardado. Uma versão publicada no PyPI **não pode ser apagada nem substituída**, então o caminho é sempre TestPyPI primeiro.

## Uma vez só (preparação)

1. **GitHub, Settings, Environments:** crie `testpypi` e `pypi`. No `pypi`, ative *Required reviewers* (você): é o portão manual antes de publicar de verdade.
2. **GitHub, Settings, Code security:** ative *Private vulnerability reporting* (o `SECURITY.md` aponta para ele).
3. **TestPyPI e PyPI** (duas contas separadas, com 2FA): em *Account settings, Publishing*, adicione um *pending publisher* em cada um:
   - projeto `roleroster`, dono e repositório deste repo, workflow `release.yml`;
   - ambiente `testpypi` no TestPyPI e `pypi` no PyPI (os nomes precisam bater exatamente).
4. **Branch `main` protegida:** pull request obrigatório, com os checks `lint`, `tests` e `package` verdes.

## Cada versão

1. Atualize `__version__` em `src/roleroster/__init__.py` e passe o `CHANGELOG.md` de "Não lançado" para a nova versão, com a data.
2. Confira localmente: `ruff check .`, `pytest --cov`, `python -m build` e `twine check dist/*`.
3. Abra o pull request e espere o CI (Ubuntu e macOS, Python 3.11 e 3.14).
4. **Ensaio:** em *Actions, release, Run workflow* (na `main`). Ele constrói e publica só no TestPyPI.
5. Instale do TestPyPI numa máquina ou contêiner limpo e teste:
   ```sh
   pipx install --index-url https://test.pypi.org/simple/ \
     --pip-args="--extra-index-url https://pypi.org/simple/" roleroster
   role --version && role doctor
   ```
6. **Publicação:** crie a tag `vX.Y.Z` (igual a `__version__`, o workflow confere) e envie. O workflow passa pelo TestPyPI (versão repetida é ignorada) e para no ambiente `pypi` até você aprovar.
7. Confira a página do projeto no PyPI (README e metadados) e instale em ambiente limpo: `pipx install roleroster`.
8. Crie a *GitHub Release* a partir da tag, com as notas do `CHANGELOG.md`.

## Se algo der errado

- Não dá para apagar nem reenviar a mesma versão. Marque a versão como **yanked** no PyPI (ela sai das instalações novas, sem quebrar quem já a fixou) e publique a seguinte (`X.Y.Z+1`).
- Falha antes do passo 6: nada foi publicado de verdade; corrija e repita.
