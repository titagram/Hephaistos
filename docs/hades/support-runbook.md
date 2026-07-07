# Hades Support Runbook

This runbook is for beta support and incident response. It turns common launch
failures into safe commands, expected evidence, recovery action, and escalation.

## Safe Support Bundle

Ask users for command output, not raw state files:

```bash
hades doctor
hades backend support-report --json
hades logs --level WARNING --session latest
```

Use `hades backend status --json` for local debugging. For support tickets,
prefer `hades backend support-report --json`: it keeps setup, sync, awareness,
and action fields, but removes local absolute paths and secrets.

If the backend is configured and the user explicitly agrees, ask them to submit
the compact backend report:

```bash
hades doctor --report-backend
```

Do not ask users to send:

- `.env`, profile secret files, bootstrap tokens, derived backend agent tokens,
  API keys, or bearer headers.
- Raw job payloads, raw source files, generated artifact JSON, SQLite databases,
  or full `config.yaml`.
- Screenshots or logs that show local absolute paths, private repository URLs,
  tokenized install commands, or query-string credentials.

## Recovery Matrix

| Symptom | Command | Expected evidence | Recovery | Escalate when |
| --- | --- | --- | --- | --- |
| Backend unreachable | `hades backend support-report --json`; `hades doctor`; `hades logs --level WARNING --session latest` | `degraded=true`, sync action, `sync.error` or `sync.client_error` warning | Confirm network/DNS/TLS/backend URL, then rerun `hades backend sync` | Health fails for multiple users or backend dashboard also cannot reach `/api/hades/v1/health` |
| Token expired or revoked | `hades doctor`; `hades backend support-report --json` | Missing token warning, 401/403/422 setup or sync error with token redacted | Generate a new dashboard one-liner or rerun `hades backend bootstrap --url <backend-url> --project-id <project-id> --project-token <new-bootstrap-token> --workspace "$PWD" --non-interactive` | A freshly generated token fails or the backend cannot revoke the old token |
| Failed bootstrap | `hades backend support-report --json`; `hades doctor` | Backend not configured, no linked workspace, or bootstrap command failed before initial sync | Rerun the dashboard-generated one-liner from the workspace root; local Hades still works without shared backend memory | Repeated bootstrap creates duplicate backend agents or never reaches agent registration |
| Workspace already linked | `hades backend profiles --json`; `hades backend support-report --json` | Existing linked binding for the same workspace fingerprint or backend says workspace conflict | If it is the same project, keep the existing link and run `hades backend sync`; otherwise `hades project unlink <project>` and relink the intended project | Backend shows an active binding the user cannot see or unlink locally |
| Job waiting confirmation | `hades backend jobs` | `waiting_confirmation` jobs and status action telling the user to review work | `hades backend approve-job <job_id>` for expected work, or `hades backend refuse-job <job_id> --reason "too broad"` | Job repeatedly returns to waiting after refusal or has unclear capability/payload |
| Proposal refused or conflicted | `hades backend proposals`; `hades backend status --json` | Proposal status `refused` or `conflicted` with backend reason | Review reason, then `hades backend ack-proposal <proposal_id>` to silence local review state | Backend refused a proposal that should be accepted according to project policy |
| Artifact too large or truncated | `hades backend sync`; `hades logs --level WARNING --session latest` | `artifact.uploaded` with truncation/redaction counts, or upload failure warning | Lower requested scope or rerun with smaller backend job budgets; do not ask for raw source upload | Backend needs a schema or budget change to ingest normal project size |
| Evidence rejected by safety policy | `hades logs --level WARNING --session latest`; backend response error code | `unredacted_secret_detected`, `evidence_payload_too_large`, `source_slice_too_large`, or `diagnosis_payload_too_large` | Redact tokens/cookies/passwords/private keys, reduce payload size, or fetch a smaller source slice; do not ask the user to share the raw rejected payload | Redacted bounded payload is still rejected or the rejection blocks an otherwise valid diagnosis workflow |
| Docker permissions | `docker compose ps`; `docker compose logs --tail=100 dashboard`; `hades doctor` inside the container | Files in `~/.hermes` owned by the wrong UID/GID or dashboard cannot write logs/state | Start with `HERMES_UID=$(id -u) HERMES_GID=$(id -g) docker compose up -d`; keep dashboard bound to `127.0.0.1` unless auth is configured | State ownership remains broken after UID/GID remap or operator needs public reverse-proxy support |
| Windows PATH issue | PowerShell: `where.exe hades`; `hades doctor` | `hades` not found or doctor reports missing/wrong command link | Open a new terminal after install; rerun the PowerShell installer; check `%LOCALAPPDATA%\hermes` remains on PATH | Native Windows install cannot create or repair the command shim |
| Desktop/backend version mismatch | `hades version`; desktop About/version screen; `hades doctor` | Desktop starts an older backend command, `serve` fallback, or mismatched CLI/package version | Run `hades update`, restart the desktop app, and rerun `hades doctor` | Desktop still launches an old managed runtime after update/restart |

## Incident Steps

1. Identify whether the failure is local-only, backend-wide, or release-wide.
2. Preserve only safe evidence: doctor output, support report JSON, warning
   logs, and backend report ID if submitted.
3. Revoke or rotate affected bootstrap/project tokens before asking the user to
   retry a setup flow.
4. Prefer recovery commands over manual SQLite edits. Manual DB edits are a last
   resort for maintainers with a backup.
5. Close the incident with the exact command that recovered the user, the
   affected version, and whether docs/tests need a follow-up.
