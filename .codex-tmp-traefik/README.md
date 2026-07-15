# Shared Traefik proxy

This directory owns the host-level Traefik process shared by all routed Docker projects. It is intentionally separate from every application repository, including Hades in `/home/ubuntu/dev-sandbox`.

## Canonical commands

Run commands from `/home/ubuntu/traefik`:

```bash
docker compose config --quiet
docker compose up -d
docker compose ps
docker compose logs --tail=200 traefik
docker compose restart traefik
```

The service uses the external `traefik_default` network and the host ACME state at `/home/ubuntu/acme.json`. Do not remove either resource during normal maintenance.

## Backups and rollback

Before replacing or reconstructing the proxy, back up both the ACME file and `docker inspect traefik` under `/home/ubuntu/backups/traefik`. Keep ACME permissions and ownership unchanged.

The detailed inventory, validation checklist, and rollback procedure are in `/home/ubuntu/traefik-readme.md`. A rollback must preserve `/home/ubuntu/acme.json`, ports 80/443, and the external `traefik_default` network.
