# Dashboard Auth And Exposure Matrix

This matrix covers the dashboard/API surface in this repository. The executable
source of truth is `hermes_cli/web_server.py` plus
`hermes_cli/dashboard_auth/public_paths.py`; this document records the operator
contract and the review checklist.

## Bind Modes

| Bind mode | Auth boundary | Operator guidance |
| --- | --- | --- |
| `127.0.0.1`, `localhost`, `::1` | Loopback plus ephemeral `_SESSION_TOKEN` on non-public `/api/*` routes | Default local mode. Use an SSH tunnel for remote access. |
| Non-loopback host, including `0.0.0.0` | Dashboard auth provider is mandatory; `--insecure` does not disable auth | Configure OAuth or `dashboard.basic_auth.*` before binding publicly. |
| Reverse proxy | Dashboard auth provider plus Host/Origin checks and `dashboard.public_url` | Terminate TLS at the proxy, forward only intended hosts, and keep auth enabled. |
| WebSocket routes (`/api/pty`, `/api/ws`, `/api/pub`, `/api/events`) | Loopback token in local mode; single-use dashboard-auth ticket or internal server credential in gated mode | Do not expose WebSocket endpoints without the same dashboard auth boundary. |

## Public API Allowlist

Only these `/api/*` routes bypass the dashboard cookie/session gate. All entries
must be safe for unauthenticated uptime probes and pre-login dashboard bootstrap.

| Route | Method | Auth dependency | Reason |
| --- | --- | --- | --- |
| `/api/status` | `GET` | Public read-only liveness; gated mode redacts host details | Portal and monitors need a no-cookie health probe. |
| `/api/config/defaults` | `GET` | Public read-only bootstrap | The config UI can render default/schema state before login. |
| `/api/config/schema` | `GET` | Public read-only bootstrap | The config UI can render field metadata before login. |
| `/api/model/info` | `GET` | Public read-only metadata | Model context/capability metadata contains no local secrets. |
| `/api/dashboard/themes` | `GET` | Public read-only metadata | The shell needs themes before login. |
| `/api/dashboard/plugins` | `GET` | Public read-only metadata | The shell needs plugin manifests before login. |
| `/api/cron/fire` | `POST` | Self-authenticated NAS JWT, not cookie auth | Managed cron callbacks are bearer-only service calls. |

`/api/cron/fire` is the only mutating public-path exception. It is public only
to bypass the browser cookie gate; the route must verify its short-lived
purpose-scoped JWT before doing work.

## Protected API Classes

The following classes must remain behind session, loopback token, or registered
bearer-token auth:

| Class | Examples | Required boundary |
| --- | --- | --- |
| File, git, PTY, and terminal operations | `/api/files/*`, `/api/git/*`, `/api/pty` | Authenticated dashboard session or loopback token/WebSocket ticket |
| Config, model, tool, skill, and profile mutation | `PUT /api/config/raw`, `POST /api/model/set`, `PUT /api/tools/*`, `POST /api/profiles` | Authenticated dashboard session or loopback token |
| Credential and OAuth flows after bootstrap | `/api/auth/me`, `/api/auth/ws-ticket`, provider callbacks after login start | Authenticated session or provider-owned proof |
| Plugin install/update/enable/disable | `/api/dashboard/agent-plugins/*`, `/api/plugins/*` | Authenticated dashboard session; plugin routes inherit dashboard middleware |
| Backend Hades local state | `hades backend *` CLI and TUI RPC status | Local profile secrets and backend bearer token; never publish tokens in logs |

## Docker Exposure Profiles

The default `docker-compose.yml` uses host networking for runtime compatibility,
but the dashboard command binds to `127.0.0.1`. Treat that as localhost-only
even inside Docker:

```bash
ssh -L 9119:localhost:9119 user@host
```

For reverse-proxy exposure:

1. Keep dashboard auth enabled with OAuth or `dashboard.basic_auth.*`.
2. Set `dashboard.public_url` to the external HTTPS origin.
3. Forward only the dashboard port and preserve Host/Origin headers.
4. Keep API-server exposure separate; if `API_SERVER_HOST=0.0.0.0`, set
   `API_SERVER_KEY` and document the allowed callers.

For egress isolation, start from
[`network-egress-isolation.md`](network-egress-isolation.md). Whole-process
network policy is the production boundary; in-process allowlists are review
aids, not containment.

## Review Checklist

When adding a dashboard/API route:

1. If the route mutates files, config, git, credentials, tools, plugins, jobs,
   or local process state, keep it out of `PUBLIC_API_PATHS`.
2. If a service callback cannot use cookies, register a dedicated token/JWT
   proof and document it as self-authenticating.
3. If the route is public read-only bootstrap, add it to the table above and to
   `PUBLIC_API_PATHS`.
4. Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/hermes_cli/test_dashboard_auth_inventory.py
```
