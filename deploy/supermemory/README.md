# Supermemory Codex deployment operations

This Compose project runs the pinned native Supermemory server behind the
existing Traefik instance and routes its LLM work to a private, dedicated Codex
bridge. It does not configure or modify Hermes/Hades.

## Prerequisites

- Run these commands on the deployment host from this repository checkout.
- Docker Engine, Docker Compose v2, `curl`, `getent`, and `openssl` must be
  installed.
- The external Docker network `traefik_default` must already exist, and its
  Traefik instance must have the `web`, `websecure`, and `le` certificate
  resolver configuration used by `compose.yaml`.
- DNS A `persephone.cc` must resolve to `162.19.229.31` before public TLS
  validation. No AAAA record should point to another host.
- Choose the BasicAuth password for username `titagram` at the bootstrap prompt
  and be ready to complete the Codex device authorization for the dedicated
  bridge volume.

Do not create deployment credentials by hand. `bootstrap.sh` creates
`deploy/supermemory/.env.runtime` with mode `0600`, generates the bridge key,
discovers the native Supermemory key, and stores the Compose-escaped BasicAuth
hash. The raw BasicAuth password is not stored. Deployment credentials belong
only in `.env.runtime`; Codex credentials belong only in the `codex_home`
volume. Never commit either location or copy their contents into logs, tickets,
or acceptance notes. A Platform OpenAI API key is neither required nor an
acceptable substitute for Codex device authentication.

## First deployment

Confirm DNS before starting:

```bash
getent ahostsv4 persephone.cc
```

Continue to the public TLS checks only when the first returned IPv4 address is
`162.19.229.31`. Also confirm that no AAAA record resolves the hostname to a
different host.

Run the operator workflow from a real interactive terminal:

```bash
cd deploy/supermemory
./scripts/bootstrap.sh
./scripts/smoke.sh --local
./scripts/smoke.sh --public
docker compose --env-file .env.runtime -f compose.yaml logs --tail=100
docker compose --env-file .env.runtime -f compose.yaml restart
```

The bootstrap prompts silently for the BasicAuth password, may pull/build the
pinned images, and opens the Codex device-auth flow. Do not paste either prompt
value into shell history. If bootstrap is interrupted after `.env.runtime` is
created, preserve that file and continue with:

```bash
./scripts/bootstrap.sh --resume
```

Do not run bootstrap again without `--resume` against an existing runtime file.
After a successful restart, wait until both health checks pass, repeat the
local smoke test, repeat the marker search described below, and run the
persisted Codex authentication check:

```bash
docker compose --env-file .env.runtime -f compose.yaml ps
./scripts/smoke.sh --local
docker compose --env-file .env.runtime -f compose.yaml exec -T codex-bridge codex login status
```

The last command must report `Logged in using ChatGPT`.

## Authentication and network checks

The routes intentionally have two separate authentication boundaries:

- `/`, `/v4/reference`, and `/v4/openapi` use Traefik BasicAuth as
  `titagram`. Traefik removes the Basic `Authorization` header and injects the
  native Supermemory Bearer credential for these browser routes.
- Generic `/v3`, `/v4`, and `/files` API routes preserve native Supermemory
  Bearer authentication. They must reject unauthenticated calls and must never
  treat the Basic credential as API authorization.

After `./scripts/smoke.sh --public` passes, use a browser to confirm that `/`
shows Traefik's BasicAuth prompt and then the built-in `supermemory · local`
UI, and that `/v4/reference` and `/v4/openapi` load after BasicAuth.

Both Compose services must have no published host bindings. Only
`supermemory-server` may join `traefik_default`; `codex-bridge` stays only on
the private `backend` network and must have no Traefik labels. Verify the live
containers without printing environment values:

```bash
docker compose --env-file .env.runtime -f compose.yaml ps -q
docker port "$(docker compose --env-file .env.runtime -f compose.yaml ps -q codex-bridge)"
docker port "$(docker compose --env-file .env.runtime -f compose.yaml ps -q supermemory-server)"
docker inspect --format '{{json .NetworkSettings.Networks}}' "$(docker compose --env-file .env.runtime -f compose.yaml ps -q codex-bridge)"
docker inspect --format '{{json .NetworkSettings.Networks}}' "$(docker compose --env-file .env.runtime -f compose.yaml ps -q supermemory-server)"
```

## Real ingestion, retrieval, and persistence acceptance

Open the deployed `/v4/openapi` reference and use the documented endpoints and
schemas from that deployed version; do not guess endpoint paths. Generate a
unique marker such as `persephone-memory-<UTC timestamp>` and a unique test
container tag. Add one short text document containing the marker with
`Authorization: Bearer $SUPERMEMORY_API_KEY` supplied without echoing the key
or writing it into shell history.

For the ingestion trace, retain only the bridge request ID, accepted request
field names/types, HTTP status, duration, and Codex error category. Never
retain the document sentence, model output, request/response bodies,
authorization headers, or keys. Poll the status endpoint named by the deployed
OpenAPI document for at most five minutes. Search by both the unique container
tag and marker, and require at least one returned document or extracted memory
to reference the marker.

Inspect sanitized bridge logs to confirm Codex handled the request. Inspect
only environment variable names and container wiring to confirm the bridge did
not receive `OPENAI_API_KEY` and no Hermes endpoint participated. If the trace
exposes a necessary unsupported Chat Completions field, stop acceptance, add a
failing parser test, implement the smallest explicit mapping, run the full
bridge suite, and retry. Never silently drop tools, images, streaming,
multiple choices, or an unknown response format.

Restart the Compose project, wait for both services to become healthy, repeat
the exact marker search, and require the same result. Then run `codex login
status` as shown above to prove the authentication survived in `codex_home`.

## Logs and backups

Normal bridge logs are sanitized, but still treat all deployment logs as
sensitive operational data. View a bounded tail with:

```bash
docker compose --env-file .env.runtime -f compose.yaml logs --tail=100
```

Backups must cover both named volumes:

- `supermemory_data` for graph data, generated Supermemory authentication
  state, and embedding cache;
- `codex_home` for the dedicated Codex authentication state.

Protect backups as credentials, test restores regularly, and take a fresh
backup before image or Compose changes. A backup is not complete unless both
volumes can be restored together.

## Rollback

Capture the current image IDs and back up both persistent volumes before a
change. To stop and remove the current containers and project network while
preserving memory and authentication, run:

```bash
docker compose --env-file .env.runtime -f compose.yaml down
```

Restore the previously recorded image or Git revision, then run
`./scripts/bootstrap.sh --resume` and repeat the local, public, retrieval, and
restart-persistence checks.

Never use `docker compose down -v`. The `-v` option destroys the
`supermemory_data` and `codex_home` volumes, erasing persistent memory and
Codex authentication; it is not part of rollback.

## Milestone 1 acceptance evidence

Milestone 1 remains open until every live check below is `PASS`. Keep this
record limited to statuses, artifact identity, DNS/TLS facts, restart outcome,
and a hashed or truncated marker. Do not add keys, htpasswd hashes, tokens,
document text, headers, or model output.

### 2026-08-02

| Check | Status | Non-secret evidence |
| --- | --- | --- |
| Local quality gates | PASS | `npm ci`; 84/84 tests; TypeScript build; image/config/syntax/diff gates |
| Server release image/tag/checksum | PASS | `hephaistos-supermemory-server:test`; image `sha256:344fc19c47c7177e52f96c1c015046e2bc8bfe965c27e29fadf315ccdf970a20`; `server-v0.0.6`; binary SHA-256 `bb1b7cee393818236873b8e2518a435e10d9195e27ea5608a3af48a733ef8ee8` |
| Deployment container image IDs | FAIL | — |
| DNS IPv4 | FAIL | `213.186.33.5`; required `162.19.229.31` |
| TLS issuer/expiry | FAIL | — |
| Built-in UI | FAIL | — |
| HTTPS and authentication boundary | FAIL | — |
| Bridge private | FAIL | — |
| Document added and Codex extraction observed | FAIL | marker `—` |
| Search retrieval | FAIL | marker `—` |
| Restart persistence and Codex auth | FAIL | — |
| Hermes/Hades untouched | PASS | Milestone 1 deployment scope only |
