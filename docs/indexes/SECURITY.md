# Security Index

## Boundary Principale

Fonte: `SECURITY.md`.

Regola verificata: il solo boundary contro un LLM avversario e l'isolamento a
livello sistema operativo. Approval gate, redaction, scanner, allowlist tool e
Skills Guard sono euristiche o review aid, non contenimento.

## Credenziali

Fonti:
- `SECURITY.md`
- `.env.example`
- `hermes_constants.py`
- `agent/secret_scope.py`
- `tools/environments/local.py`
- `tools/code_execution_tool.py`

Regole osservate:
- Segreti in `$HERMES_HOME/.env`.
- Config comportamentale in `$HERMES_HOME/config.yaml`.
- Multiplex profile secrets passano da `agent/secret_scope.py`.
- Subprocess env viene scrub-bato; non copiare raw `os.environ`.
- `.env.example` contiene molte chiavi commentate; non trattarlo come config
  runtime attiva.

## Network Exposure

Fonte: `SECURITY.md`, `docker-compose.yml`, `hermes_cli/web_server.py`.

Regole osservate:
- Network-exposed adapters richiedono allowlist.
- Dashboard compose default: host `127.0.0.1`.
- Esposizione `0.0.0.0` e flags insecure sono break-glass operator decisions.

## Tool / Execution Risk

Fonti:
- `toolsets.py`
- `tools/registry.py`
- `tools/environments/`
- `tools/code_execution_tool.py`

Rischi:
- Tool terminal/file possono raggiungere cio che il backend espone.
- `execute_code` e MCP subprocess non sono contenuti dal solo terminal-backend
  isolation; `SECURITY.md` richiede whole-process wrapping per contenimento
  completo.
- Nuovi core tools aumentano lo schema inviato a ogni API call.

## Supply Chain

Fonti:
- `pyproject.toml`
- `uv.lock`
- `.github/workflows/supply-chain-audit.yml`
- `.github/workflows/osv-scanner.yml`

Regole osservate:
- Non allentare pin o bounds senza razionale scritto.
- Rigenerare lockfile solo quando richiesto dal task.
- Plugin/skill di terze parti vanno revisionati prima di installazione.

## Comandi / Controlli

```bash
ruff check .
python scripts/check-windows-footguns.py --all
python3 scripts/docs_audit.py
rg -n 'inherit_credentials=True|os.environ.copy\\(|get_secret\\(' .
```

## Da Completare

- Mappa completa endpoint dashboard -> auth dependency.
- Mappa plugin esterni installati dall'utente sotto `$HERMES_HOME/plugins/`
  non visibile dal checkout.
