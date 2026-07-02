# Testing

## Python

Runner canonico:

```bash
scripts/run_tests.sh
```

Per task mirati:

```bash
scripts/run_tests.sh tests/agent/test_prompt_caching.py -q
scripts/run_tests.sh tests/gateway/
```

Evidenza: `scripts/run_tests.sh` dice di usarlo al posto di `pytest` diretto.
Il runner:
- attiva `.venv`, `venv` o `$HOME/.hermes/hades-agent/venv`;
- usa `scripts/run_tests_parallel.py`;
- esegue un subprocess pytest per file;
- imposta `TZ=UTC`, `LANG=C.UTF-8`, `PYTHONHASHSEED=0`;
- pulisce l'ambiente per ridurre leak di credenziali.

CI test:

```bash
uv sync --locked --python 3.11 --extra all --extra dev
scripts/run_tests.sh --files '<slice>'
python -m pytest tests/e2e/ -v --tb=short
```

## Python Lint / Guardrail

Da `.github/workflows/lint.yml`:

```bash
ruff check .
python scripts/check-windows-footguns.py --all
```

`ruff` blocca le regole selezionate in `pyproject.toml`; al momento la regola
enforced osservata e `PLW1514`.

## TypeScript

Da `.github/workflows/typecheck.yml`:

```bash
npm ci --ignore-scripts
npm run --prefix ui-tui typecheck
npm run --prefix web typecheck
npm run --prefix apps/bootstrap-installer typecheck
npm run --prefix apps/desktop typecheck
npm run --prefix apps/shared typecheck
```

Build desktop CI:

```bash
npm ci
npm run --prefix apps/desktop build
```

Script workspace osservati:

```bash
npm run --prefix ui-tui test
npm run --prefix web test
npm run --prefix apps/desktop test:ui
npm run --prefix apps/desktop test:desktop:platforms
```

## Verifica Minima Per Tipo Di Task

- Docs/manuale: `python3 scripts/docs_audit.py`.
- Python core/tool/session: `scripts/run_tests.sh <test mirato>` piu
  `ruff check .` se hai cambiato file Python.
- Windows/subprocess/file I/O: `python scripts/check-windows-footguns.py --all`
  o scan mirato se disponibile.
- Desktop: `npm run --prefix apps/desktop typecheck` e test vicino al file.
- Web dashboard: `npm run --prefix web typecheck`; aggiungi `npm run --prefix web test`
  se tocchi logica testata.
- TUI: `npm run --prefix ui-tui typecheck` e `npm run --prefix ui-tui test`.
- Docker/packaging: leggere `.github/workflows/docker*.yml`, `Dockerfile` e
  `docker-compose.yml`; non buildare immagini pesanti senza conferma.

## Gate Di Release

La matrice completa dei gate di produzione vive in `docs/RELEASE_GATES.md`.
Usala quando un task tocca backend MVP, PyPI, Docker, website, desktop package
o update flow. In sintesi:

- `CI / All required checks pass` e' il gate aggregato da branch protection.
- Docker non e' nel gate aggregato oggi; per una release che pubblica immagini
  va richiesto il workflow `.github/workflows/docker.yml` o un signoff esplicito.
- Il backend MVP richiede anche lo smoke no-network
  `tests/hermes_cli/test_hades_backend_mvp_smoke.py` e uno smoke staging con
  `HERMES_HOME` usa-e-getta.

## Copertura Da Verificare

- Non e stata prodotta una mappa completa della copertura per ogni plugin.
- Alcuni test e2e richiedono servizi esterni o credenziali; verificare marker
  `integration` in `pyproject.toml` prima di eseguirli.
