# Hindsight Control Plane deployment

Production deployment of the Hindsight 0.9.2 Control Plane for the existing bare-metal dataplane.

## Public endpoint

- UI: `https://persephone.cc/hindsight-ui`
- Dataplane remains at `https://persephone.cc/hindsight` and is not modified by this Compose project.

The UI is compiled with `NEXT_PUBLIC_BASE_PATH=/hindsight-ui`; do not replace the image with the stock root-path image without rebuilding it.

## Security and connectivity

- The container publishes no host port.
- Traefik is the only public ingress.
- The Control Plane uses its built-in access-key login (`HINDSIGHT_CP_ACCESS_KEY`).
- The dataplane tenant key (`HINDSIGHT_CP_DATAPLANE_API_KEY`) remains server-side.
- The container reaches the localhost-only API through the existing `172.18.0.1:18888` systemd socket proxy.
- PostgreSQL and `127.0.0.1:8888` remain unexposed.

Runtime secrets are stored outside the repository in:

```text
/home/ubuntu/.config/hindsight/control-plane.env
```

Expected variable names (never commit their values):

```text
HINDSIGHT_CP_ACCESS_KEY
HINDSIGHT_CP_DATAPLANE_API_KEY
```

The generated UI access key is also written with mode `0600` to:

```text
/home/ubuntu/.config/hindsight/control-plane-access-key
```

## Operations

```bash
cd /home/ubuntu/Hephaistos/deploy/hindsight-control-plane
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 control-plane
```

The build uses upstream release `v0.9.2`, pinned to commit `ebad478240d3171bb88201ececda5e8d9883d22d`, and target `cp-only`.

## Verification

```bash
curl -fsS https://persephone.cc/hindsight-ui/api/health
curl -I https://persephone.cc/hindsight-ui/
```

Expected behavior:

- `/api/health`: HTTP 200 and dataplane status `connected`;
- unauthenticated page request: redirect to `/hindsight-ui/login`;
- successful login sets an HttpOnly, Secure, SameSite=Lax session cookie;
- container has no direct host port binding.

If the `traefik_default` subnet changes, update both this Compose file's `extra_hosts` gateway and the existing Hindsight socket bridge binding.
