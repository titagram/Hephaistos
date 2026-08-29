# Traefik deployment recipe

This reference captures the reusable pattern from a real deployment of a Node/Wrangler application behind a shared Traefik container.

## Discovery checklist

```bash
git clone https://github.com/OWNER/REPO.git /srv/APP
sed -n '1,240p' /srv/APP/site/README.md
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'
docker inspect traefik --format '{{json .Config.Cmd}}'
docker inspect traefik --format '{{json .NetworkSettings.Networks}}'
docker network inspect traefik_default
getent ahostsv4 app.example.com
```

Look for the existing entrypoints (`web`, `websecure`), ACME resolver name, and external network name instead of guessing them.

## Compose override template

```yaml
services:
  app:
    ports: !reset []
    labels:
      traefik.enable: "true"
      traefik.docker.network: traefik_default
      traefik.http.middlewares.app-auth.basicauth.users: "USER:$$2y$$12$$BCRYPT_HASH"
      traefik.http.middlewares.app-auth.basicauth.removeheader: "true"
      traefik.http.middlewares.app-https-redirect.redirectscheme.scheme: https
      traefik.http.middlewares.app-https-redirect.redirectscheme.permanent: "true"
      traefik.http.routers.app-http.entrypoints: web
      traefik.http.routers.app-http.rule: Host(`app.example.com`)
      traefik.http.routers.app-http.middlewares: app-https-redirect
      traefik.http.routers.app-https.entrypoints: websecure
      traefik.http.routers.app-https.rule: Host(`app.example.com`)
      traefik.http.routers.app-https.middlewares: app-auth
      traefik.http.routers.app-https.tls: "true"
      traefik.http.routers.app-https.tls.certresolver: le
      traefik.http.services.app.loadbalancer.server.port: "3000"
    networks:
      - default
      - traefik

networks:
  traefik:
    external: true
    name: traefik_default
```

Replace `app`, hostname, resolver, and backend port. Use `htpasswd -nbB USER 'PASSWORD'` to generate the hash, then double each `$` in the Compose value.

## Deployment and verification matrix

```bash
docker compose -f compose.yaml -f compose.traefik.yaml config >/tmp/app-compose-rendered.yaml
docker compose -f compose.yaml -f compose.traefik.yaml up -d --build
docker compose -f compose.yaml -f compose.traefik.yaml ps

docker inspect app-container --format '{{json .NetworkSettings.Ports}}'
curl -sSIk --resolve app.example.com:80:127.0.0.1 http://app.example.com/
curl -sSI --resolve app.example.com:443:127.0.0.1 https://app.example.com/
curl -sS --resolve app.example.com:443:127.0.0.1 \
  -u 'USER:PASSWORD' https://app.example.com/health
```

Expected results:

| Check | Expected |
|---|---|
| Merged Compose config | exits 0 |
| Container status | running |
| Container ports | no host binding; published map is null/absent |
| HTTP request | 301/308 redirect to HTTPS |
| HTTPS without credentials | 401 and `WWW-Authenticate: Basic` |
| HTTPS with credentials | 200 from the app or health endpoint |
| TLS verification | succeeds without `-k` |
| DNS | hostname resolves to the deployment host |

If the application needs a persistent database or local state, preserve its named volume and follow the README's single-instance guidance. Do not use `docker compose down -v` during routine redeployments.
