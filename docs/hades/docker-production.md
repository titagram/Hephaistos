# Hades Docker Production Profile

Use `docker-compose.production.yml` for self-hosted Hades Agent deployments
where the public backend is not used or where the operator wants local control
over dashboard exposure, auth, updates, and backups.

The default `docker-compose.yml` remains a compatibility profile and uses
`network_mode: host`. The production profile is the supported safe default for
new self-hosted installs.

## Start

Create the data directory and choose credentials:

```bash
mkdir -p ~/.hermes
export HERMES_UID="$(id -u)"
export HERMES_GID="$(id -g)"
export HERMES_DASHBOARD_BASIC_AUTH_USERNAME="admin"
export HERMES_DASHBOARD_BASIC_AUTH_SECRET="$(openssl rand -hex 32)"
```

Create a dashboard password hash from a checkout or an already built image:

```bash
.venv/bin/python -c "from plugins.dashboard_auth.basic import hash_password; print(hash_password('choose-a-strong-password'))"
```

Then export it and start:

```bash
export HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH="<scrypt-hash>"
docker compose -f docker-compose.production.yml up -d
```

The dashboard binds to `0.0.0.0` inside the container so Docker can publish it,
but Docker publishes it only on host loopback:

```text
127.0.0.1:9119 -> container:9119
```

Reach it locally at `http://127.0.0.1:9119` or through an SSH tunnel:

```bash
ssh -L 9119:127.0.0.1:9119 user@host
```

## Auth

The production compose file requires:

- `HERMES_DASHBOARD_BASIC_AUTH_USERNAME`
- `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH`
- `HERMES_DASHBOARD_BASIC_AUTH_SECRET`

If any of these are missing, `docker compose` fails before starting the
container. For internet-facing deployments, prefer OAuth or self-hosted OIDC
behind a TLS reverse proxy instead of the bundled username/password provider.
Set `HERMES_DASHBOARD_PUBLIC_URL` to the external HTTPS URL when using a proxy.

## Egress

The production profile avoids host networking, but the Hades container still
needs outbound access for model providers, messaging platforms, updates, and
tooling. For strict egress control, put an allowlisting HTTP proxy on the same
Docker network and route Hades through it:

```yaml
services:
  hades:
    environment:
      HTTP_PROXY: "http://egress-proxy:3128"
      HTTPS_PROXY: "http://egress-proxy:3128"
      NO_PROXY: "127.0.0.1,localhost,hades"

  egress-proxy:
    image: ubuntu/squid:6.10-24.04_edge
    restart: unless-stopped
    networks:
      - hades_egress
    volumes:
      - ./config/squid-allowlist.conf:/etc/squid/conf.d/allowlist.conf:ro
```

Keep the allowlist limited to the model provider, messaging platform, update
source, and backend URLs the deployment actually uses. The broader network
threat model and validation commands are in
[`../security/network-egress-isolation.md`](../security/network-egress-isolation.md).

## Dashboard Exposure

Safe defaults:

- Publish dashboard only on `127.0.0.1:9119`.
- Use SSH tunneling, VPN, or a TLS reverse proxy for remote access.
- Keep the API server disabled unless a specific client needs it.
- If the OpenAI-compatible API server is enabled, set `API_SERVER_KEY` and bind
  it behind the same private access path.

Reverse proxy example:

```yaml
services:
  hades:
    environment:
      HERMES_DASHBOARD_PUBLIC_URL: "https://hades.example.com"
    ports:
      - "127.0.0.1:9119:9119"
```

The proxy should terminate TLS, forward `X-Forwarded-Host`,
`X-Forwarded-Proto`, and `X-Forwarded-Prefix` when used, and restrict access
with OAuth/OIDC for public deployments.

## Backup

All mutable state is under the host data directory mounted at `/opt/data`.
Back up `~/.hermes` while the container is stopped or after taking a filesystem
snapshot:

```bash
docker compose -f docker-compose.production.yml down
tar --numeric-owner -czf hades-backup-$(date +%Y%m%d%H%M%S).tgz -C "$HOME" .hermes
docker compose -f docker-compose.production.yml up -d
```

Do not paste backup archives into support tickets. They contain `.env`, memory,
sessions, logs, skills, plugins, and profile state.

## Restore

Restore onto a host with the same UID/GID or start once with the correct
`HERMES_UID` and `HERMES_GID` so the container remap fixes ownership:

```bash
docker compose -f docker-compose.production.yml down
mv ~/.hermes ~/.hermes.before-restore.$(date +%Y%m%d%H%M%S)
tar -xzf hades-backup-YYYYMMDDHHMMSS.tgz -C "$HOME"
export HERMES_UID="$(id -u)"
export HERMES_GID="$(id -g)"
docker compose -f docker-compose.production.yml up -d
hades doctor
```

If you restore on a new public URL, rotate dashboard auth secrets and update
`HERMES_DASHBOARD_PUBLIC_URL`.

## Update And Rollback

Update:

```bash
docker compose -f docker-compose.production.yml pull
docker compose -f docker-compose.production.yml up -d
hades doctor
```

For source checkouts that build locally, replace `pull` with:

```bash
docker compose -f docker-compose.production.yml build --pull
docker compose -f docker-compose.production.yml up -d
```

Rollback:

```bash
docker compose -f docker-compose.production.yml down
docker image ls hermes-agent
docker compose -f docker-compose.production.yml up -d
```

If the issue is state-related rather than image-related, restore the last known
good backup instead of editing SQLite files manually.

## Break Glass

Use the compatibility `docker-compose.yml` host-network profile only when a
specific adapter or local network dependency cannot work through bridge
networking. Before switching:

1. Confirm the dashboard remains loopback-only or auth-gated.
2. Confirm `API_SERVER_KEY` is set if the API server is enabled.
3. Record why bridge networking failed and how to return to the production
   profile.
4. Prefer a temporary override file over editing `docker-compose.production.yml`.
