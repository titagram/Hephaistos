# Traefik on `162.19.229.31`

This is the host-level runbook for the shared Traefik reverse proxy. It lives outside every application repository because one proxy routes several independent Docker projects.

## Critical ownership rule

The host-managed Compose project in `/home/ubuntu/traefik` is shared infrastructure. Hades, Angela, GuestInk, Rocket, Mindful Flow, and other application Compose projects may attach containers to its network and publish Docker labels, but none of those projects owns the proxy lifecycle.

Do not add this container to `/home/ubuntu/dev-sandbox` or run a Hades cleanup command that removes it. Stopping Traefik makes every routed domain unavailable.

## Verified current state (2026-07-15)

- Container: `traefik`
- Compose file: `/home/ubuntu/traefik/compose.yaml`
- Image reference: `traefik@sha256:82d3d16dde0474a51fef00b28de143d48b67f7a27453224d5e7b5aaefff26a97`
- Running Traefik version: `3.6.6`, linux/amd64
- Compose project: `traefik`
- Restart policy: `unless-stopped`
- Docker network: `traefik_default` (`bridge`, local scope)
- Published ports: `80:80`, `443:443`; port 8080 is not published
- Docker socket mount: `/var/run/docker.sock:/var/run/docker.sock:ro`
- ACME mount: `/home/ubuntu/acme.json:/acme.json` (currently read/write)
- ACME file: root-owned, mode `0600`
- Docker provider: enabled; containers are ignored unless `traefik.enable=true`
- Certificate resolver: `le`
- ACME challenge: HTTP-01 through entrypoint `web`
- Entrypoints: `web` on port 80 and `websecure` on port 443
- Dashboard/API feature: disabled; no dashboard router is managed on this host
- Outbound version and anonymous-usage checks: disabled
- Log level: `INFO`
- Container log driver: `json-file`, rotated at `10m`, five retained compressed files

The image digest is intentionally pinned to the verified v3.6.6 runtime. Upgrade the digest only in a dedicated maintenance change with pre/post checks for every hosted domain.

## Effective startup arguments

```text
--global.checknewversion=false
--global.sendanonymoususage=false
--providers.docker=true
--providers.docker.exposedbydefault=false
--entrypoints.web.address=:80
--entrypoints.websecure.address=:443
--certificatesresolvers.le.acme.email=gabriele.tita@gmail.com
--certificatesresolvers.le.acme.storage=/acme.json
--certificatesresolvers.le.acme.httpchallenge=true
--certificatesresolvers.le.acme.httpchallenge.entrypoint=web
--log.level=INFO
```

## How routing works

Traefik watches Docker through the socket. An application container is routable only when it:

1. has `traefik.enable=true`;
2. declares router and service labels;
3. shares a Docker network with Traefik;
4. sets `traefik.docker.network=traefik_default` when it has multiple networks.

The `traefik_default` network is intentionally external to application Compose projects. They may join it, but they must not create or remove it.

Currently observed routed projects include Hades (`home-sweet-home.cloud`), Rocket (`gtsystems.tech`), Angela (`gtsystem-test.xyz`), Mindful Flow (`api.laurasanti.it`), and GuestInk (`guestink.it` / `www.guestink.it`). Inspect live labels before assuming this list is complete.

## Routine inspection

```bash
cd /home/ubuntu/traefik
docker compose config --quiet
docker compose ps
docker inspect traefik --format 'status={{.State.Status}} image={{.Config.Image}} restart={{.HostConfig.RestartPolicy.Name}}'
docker port traefik
docker network inspect traefik_default
docker compose logs --tail=200 traefik
docker exec traefik traefik version
docker inspect traefik --format 'log={{.HostConfig.LogConfig.Type}} options={{json .HostConfig.LogConfig.Config}}'
stat -c '%a %U:%G %s %n' /home/ubuntu/acme.json
```

List containers opted into Traefik without printing Basic Auth hashes:

```bash
docker inspect $(docker ps -q) | jq -r '
  .[] as $container
  | ($container.Config.Labels // {}) as $labels
  | select($labels["traefik.enable"] == "true")
  | [($container.Name | ltrimstr("/")), ($labels["com.docker.compose.project"] // "standalone")]
  | @tsv
'
```

Never dump every Docker label into shared logs or chat: middleware labels may contain Basic Auth hashes.

## Safe restart

A restart causes a short outage for every routed domain:

```bash
cd /home/ubuntu/traefik
docker compose config --quiet
docker compose restart traefik
docker compose logs --since=2m traefik
```

Then validate at least:

```bash
curl -fsSI http://home-sweet-home.cloud/ | head
curl -fsS https://home-sweet-home.cloud/api/hades/v1/health
```

The protected Hades dashboard root returns `401` without Basic Auth; that is expected. Validate the other hosted domains appropriate to the maintenance window as well.

## Disaster recovery prerequisites

Before reconstruction:

1. Back up `/home/ubuntu/acme.json` without changing its content or permissions.
2. Record `docker inspect traefik` to a mode-0600 maintenance file; do not paste it into chat.
3. Confirm ports 80 and 443 are free.
4. Confirm the `traefik_default` network exists, or recreate only that network.
5. Confirm all application containers still publish their own labels.

Backup example:

```bash
sudo install -d -m 700 /home/ubuntu/backups/traefik
sudo cp --preserve=mode,ownership,timestamps /home/ubuntu/acme.json \
  /home/ubuntu/backups/traefik/acme.json.$(date -u +%Y%m%dT%H%M%SZ)
docker inspect traefik > \
  /home/ubuntu/backups/traefik/traefik.inspect.$(date -u +%Y%m%dT%H%M%SZ).json
chmod 600 /home/ubuntu/backups/traefik/traefik.inspect.*.json
```

Do not delete or truncate `acme.json`. Losing it discards certificate/account state and can trigger ACME rate limits during recovery.

## Reconstruction and rollback

The canonical topology is already encoded in `/home/ubuntu/traefik/compose.yaml`. Do not reconstruct it with an unpinned `docker run` command.

```bash
docker network inspect traefik_default >/dev/null 2>&1 || docker network create traefik_default
cd /home/ubuntu/traefik
docker compose config --quiet
docker compose up -d
docker compose ps
```

If a change fails, restore the last known-good `compose.yaml` and run `docker compose up -d`. Restore `acme.json` only if its content was actually damaged; preserve root ownership and mode `0600`. Timestamped snapshots are under `/home/ubuntu/backups/traefik`. After rollback, rerun the complete domain baseline rather than validating Hades alone.

The retired standalone `devboard-auth-provider` container was redundant with the `devboard-basic-auth` middleware owned by `devboard-app-1`. Its final inspect snapshot is retained under `/home/ubuntu/backups/traefik`; do not recreate it unless the Hades routing contract is deliberately redesigned.

## Hades-specific pointer

The development repository lives at `/home/ubuntu/dev-sandbox`. Its routers and
middleware are defined in `docker-compose.devboard.traefik.yaml`; its
agent-facing instructions are in `AGENTS.md`; its integration runbook is
`docs/runbooks/traefik-integration.md`.

The active production source checkout is intentionally separate:

```text
/home/ubuntu/devboard-logbook-release
```

It is a regular Git checkout of `main`, currently deployed at
`a31473f8332a77006510e9981be65f3c83cfbaa3`. Do not deploy from
`/home/ubuntu/dev-sandbox` while it is on a task branch or has local changes.
The application containers need a real `.git` directory because their startup
invokes Composer; do not substitute a Git worktree unless its Git metadata is
also visible inside the container.

Deploy Hades with both the base Compose file and the Traefik overlay, reusing
the ignored environment file from the development repository:

```bash
RELEASE_DIR=/home/ubuntu/devboard-logbook-release
ENV_FILE=/home/ubuntu/dev-sandbox/.env
docker compose --env-file "$ENV_FILE" -p devboard \
  -f "$RELEASE_DIR/docker-compose.devboard.yaml" \
  -f "$RELEASE_DIR/docker-compose.devboard.traefik.yaml" \
  up -d --build --wait
```

The overlay restores the `traefik_default` network and the Hades/API/frontend
router labels. It must not own, recreate, or restart the standalone Traefik
container.
