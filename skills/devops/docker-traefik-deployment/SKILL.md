---
name: docker-traefik-deployment
description: "Use when deploying a Dockerized or localhost-only bare-metal service behind an existing Traefik instance with HTTPS, authentication, and outside-in verification."
version: 1.1.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [docker, traefik, reverse-proxy, https, letsencrypt, basic-auth, deployment]
    related_skills: []
---

# Docker Application Deployment Behind Traefik

Use this skill when a user asks to clone or deploy a web project on a Docker host and expose it through an already-running Traefik reverse proxy, especially with a custom hostname, TLS, and authentication.

## Workflow

1. **Inspect before changing anything.** Confirm the repository URL and destination, check whether the destination already exists, read the project README, inspect its Dockerfile/Compose file, and inventory the running Traefik container, its command-line providers, entrypoints, certificate resolver, and attached Docker network.
2. **Confirm DNS and prerequisites.** Resolve the requested hostname and verify it points to the host's public address. Confirm Docker Compose is available and that the external Traefik network exists. Do not recreate or remove the shared Traefik service as part of the application deployment.
3. **Prefer a Compose override.** Keep the upstream application's base Compose file intact when possible. Add a deployment-specific override with Traefik labels and attach the app to both its normal network and the external Traefik network. Set `traefik.docker.network` explicitly when the container has multiple networks.
4. **Route HTTPS and redirect HTTP.** Create an HTTP router that permanently redirects to HTTPS and an HTTPS router with the known entrypoint, `tls=true`, and the configured ACME resolver. Define the backend port explicitly with `traefik.http.services.<name>.loadbalancer.server.port`.
5. **Protect the whole application when requested.** Define a Basic Auth middleware and attach it to the HTTPS application router. Generate an htpasswd-compatible hash rather than putting the plaintext password in Traefik labels. Escape `$` as `$$` in Compose YAML so the hash survives Compose interpolation. Never echo the plaintext password in the final report or store it in a committed file unless the user explicitly accepts that risk.
6. **Avoid accidental direct exposure.** If Traefik is the intended entrypoint, remove host port publishing in the override. With Docker Compose, `ports: !reset []` can reset ports inherited from the base file; generic YAML linters may reject `!reset`, but Docker Compose understands it. Verify the resulting container has `{"3000/tcp": null}` (or equivalent) rather than a host binding.
7. **Validate secrets and deploy.** For every ignored runtime env file (for example `.env.mcp`), verify it exists, is non-empty, has restrictive permissions, and contains the required *variable names* — never print its values. Render the merged configuration with `docker compose -f base -f override config`, then run `docker compose ... up -d --build`. Keep the app's documented persistent volume and single-instance topology. Check container status and logs for readiness. A restart cannot restore a service that is absent from the active Compose configuration: first confirm the expected service is rendered by `docker compose ... config --services`.
8. **Verify from the outside-in.** Test HTTP redirect, unauthenticated HTTPS (`401` plus `WWW-Authenticate`), authenticated HTTPS (`200`), and an application health/API endpoint. For an authenticated Streamable HTTP MCP, verify `/health`, assert that an unauthenticated `POST /mcp` is `401`, then perform authenticated `initialize` plus `tools/list` without logging the token. A public `404` with valid DNS/TLS usually means Traefik has no matching router or its backend container is absent; inspect the active Compose service list, container labels, and shared Traefik network before debugging client operating systems or credentials. Verify the TLS certificate without `-k` where possible, DNS resolution, the Traefik network attachment, and absence of a direct host port. Report the exact URL, deployment path, and verification results.

## Publishing a localhost-only bare-metal service

When Traefik runs in Docker but the backend is a bare-metal service intentionally bound to `127.0.0.1`, do not widen the backend to `0.0.0.0` merely so the container can reach it. A verified isolation pattern is:

1. Keep the application on `127.0.0.1:<backend-port>`.
2. Create a systemd socket bound only to the external Traefik network gateway (discover it with `docker network inspect <network>`), for example `172.18.0.1:<bridge-port>`.
3. Pair it with `systemd-socket-proxyd 127.0.0.1:<backend-port>` and enable the socket unit. This adds no package dependency and keeps the bridge socket off public interfaces.
4. Run a minimal edge container on the Traefik network. Point its upstream at the network gateway/bridge port and attach normal Traefik labels. If using `host.docker.internal`, set the explicit Traefik-network gateway address: Linux `host-gateway` may resolve to the default `docker0` gateway instead of the external Traefik network.
5. For MCP/SSE or streaming APIs, disable response and request buffering in the edge proxy and use generous read/send timeouts.
6. Verify all boundaries: the backend still listens only on localhost; the bridge listens only on the Traefik gateway; the edge container has no host port binding; public unauthenticated requests are rejected by the backend; authenticated REST/MCP requests succeed; TLS validates without `-k`.
7. Treat the gateway address as an operational dependency. If the shared Docker network is recreated with a different subnet, update both the systemd socket and edge-container host alias.

## Security and operational pitfalls

- Do not expose a stateful unauthenticated wiki or admin application directly to the Internet; enforce the requested proxy authentication and preserve the application's persistent volume.
- Do not use a plaintext password in a final response or Compose label. The user may provide it, but the deployed configuration should contain only a hash.
- Do not assume a hostname is ready merely because DNS resolves: wait for ACME issuance and test the actual router with the hostname in the `Host`/SNI path.
- Do not attach the app only to its private Compose network; Traefik must share the external network.
- Do not claim success based only on a successful image build. Verify router behavior and the application endpoint.

## Reference

For a concise, reusable deployment recipe and verification matrix, see `references/traefik-docker-deployment.md`.
