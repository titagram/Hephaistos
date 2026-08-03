# Supermemory Codex Bridge Design

**Date:** 2026-08-02
**Status:** Approved for implementation planning

## Objective

Complete a first milestone in which a self-hosted `supermemory-server` runs on
the current VPS, uses a dedicated Codex adapter for its LLM work, and exposes
the Supermemory API over HTTPS at `persephone.cc`. Hermes/Hades must remain
unchanged during this milestone.

The second milestone will update the existing Hermes/Hades Supermemory memory
provider so its SDK and conversations requests target this self-hosted API.

## Constraints and decisions

- Use the official prebuilt `supermemory-server`; rebuilding it is not required.
- Pin the server release and verify its published checksum during image build.
- Keep the Codex bridge independent of Hermes, `AIAgent`, Hermes memory, and
  Hermes provider selection.
- Give the bridge its own Codex runtime configuration and credential storage.
- Keep local embeddings inside Supermemory. Codex handles only LLM-backed work
  such as summaries, contextual chunking, and memory extraction.
- Do not commit passwords, API keys, Codex tokens, generated Supermemory keys,
  or htpasswd hashes that are specific to the deployment.
- Use the existing external Docker network `traefik_default` and the existing
  Traefik ACME resolver `le`.
- Point the DNS A record for `persephone.cc` at `162.19.229.31` before the TLS
  acceptance test. At design time, the domain still resolves to `213.186.33.5`.

## Architecture

```text
Internet
   |
   v
Traefik :443
   |-- web and reference routes   -> supermemory-server
   |   (BasicAuth, then backend Bearer injection)
   |
   `-- /v3, /v4, /files          -> supermemory-server
       (Supermemory Bearer auth)
                                      |
                               private Docker network
                                      |
                                      v
                                 codex-bridge
                                      |
                                      v
                              Codex SDK/app-server
```

The deployment is a dedicated Compose project with two services:

1. `supermemory-server`, built around the pinned official native binary.
2. `codex-bridge`, a standalone Node/TypeScript OpenAI-compatibility service.

Only the Supermemory-facing web service joins `traefik_default`. The bridge
joins only the private project network and publishes no host port. Supermemory
addresses it by Docker service name.

The runtime probe of official release `server-v0.0.6` confirmed that `/` serves
a complete `supermemory · local` HTML interface with status, SDK examples, API
key guidance, and links to `/v4/reference` and `/v4/openapi`. The Linux x64
binary matched the upstream SHA-256
`bb1b7cee393818236873b8e2518a435e10d9195e27ea5608a3af48a733ef8ee8`.
No replacement landing service is needed.

## Traefik routing and authentication

Traefik defines a higher-priority API router for `Host(persephone.cc)` and
the paths `/v3`, `/v4`, and `/files`. These paths do not use Traefik BasicAuth;
they retain the native Supermemory Bearer authentication expected by its SDKs.

A lower-priority fallback web router for the same host uses Traefik BasicAuth
with username `titagram`. The password is converted to an htpasswd hash outside
version control and supplied through deployment-only configuration. Traefik
strips the Basic `Authorization` header, then injects the generated Supermemory
Bearer credential before forwarding web and reference requests. The
higher-priority API router does not inject or replace `Authorization`, so SDK
clients retain native Supermemory Bearer authentication.

HTTP redirects permanently to HTTPS. HTTPS uses the existing `le` certificate
resolver. The fallback router forwards non-API web routes to the built-in UI;
the API-reference routes `/v4/reference` and `/v4/openapi` receive dedicated
higher-priority BasicAuth routers so they remain usable in a browser without
weakening authentication for the rest of `/v4`.

This split is required because HTTP Basic and Supermemory Bearer authentication
both use the `Authorization` request header and cannot coexist on the same API
request.

## Codex bridge contract

The bridge exposes an internal OpenAI-compatible base URL with at least:

- `GET /healthz` for container health checks;
- `POST /v1/chat/completions` for Supermemory inference.

Supermemory is configured with the bridge URL, a private shared bridge key, and
a fixed public model alias such as `supermemory-codex`. The bridge maps that
alias to one deployment-configured Codex model. Callers cannot select arbitrary
Codex models.

The bridge uses a long-lived Codex SDK/app-server process for runtime reuse, but
starts a fresh Codex thread for every HTTP request. This prevents documents or
ingestion jobs from sharing conversational context. It enables no tools,
Hermes memory, workspace mutation, or agent loop.

The initial implementation supports only the request surface observed from
Supermemory. During the first ingestion trace, capture field names and shapes
without logging document content or secrets. In particular, validate messages,
token limits, streaming flags, and `response_format`/JSON Schema behavior.
Unsupported fields or modes return an explicit client error instead of silently
falling back to a different provider.

The bridge returns a conventional non-streaming Chat Completions response and
maps Codex usage/error information where available. It enforces request timeout,
body-size, and concurrency limits so background ingestion cannot exhaust the
VPS or the Codex account.

## Persistence and secrets

Use distinct persistent storage for:

- Supermemory graph data, generated authentication state, and embedding cache;
- the bridge's dedicated Codex authentication state.

Deployment secrets live outside Git in a permission-restricted environment or
secret file. This includes the BasicAuth hash, bridge shared key, generated
Supermemory API key, and Codex credentials. Logs must redact authorization
headers and must not include raw document contents or model responses at normal
log levels.

## Error handling and operations

- Bridge startup fails if its Codex model, credential location, or shared key is
  missing.
- Supermemory and Traefik must not report ready until their local dependencies
  pass health checks.
- Codex authentication, rate-limit, timeout, structured-output, and upstream
  errors map to stable JSON HTTP errors and are logged without secrets.
- Requests are not automatically rerouted through Hermes or another provider.
- Containers use restart policies, bounded logs, and pinned artifacts.
- Restarting or recreating containers must retain Supermemory data and Codex
  authentication through their persistent volumes.

## Milestone 1 validation

The milestone is complete only when all of the following pass:

1. Reconfirm that the pinned official binary serves the observed built-in UI at
   `/` inside the deployed container.
2. Build and start the persistent Compose deployment.
3. Confirm `persephone.cc` resolves to `162.19.229.31` and presents a valid TLS
   certificate.
4. Confirm unauthenticated web access receives a BasicAuth challenge and the
   configured user can access the web response.
5. Confirm API requests without a Supermemory Bearer key are rejected.
6. Confirm the bridge has no public Traefik router or host port.
7. Add a unique text document through the public Supermemory API.
8. Observe successful extraction through the dedicated Codex bridge, without an
   OpenAI Platform API key or Hermes involvement.
9. Search for the unique concept and retrieve the ingested document or memory.
10. Restart the deployment and repeat the search successfully.

## Milestone 2 boundary

Milestone 2 changes the existing `plugins/memory/supermemory` provider to accept
a configurable Supermemory base URL. Both the SDK client and the currently
hardcoded conversations endpoint must derive from the same configured URL.
Tests must cover cloud defaults and self-hosted URLs. No milestone 2 code is
included in the bridge deployment work.

## Out of scope

- Rebuilding or reverse-engineering the native Supermemory server.
- Modifying Hermes model-provider selection.
- Exposing the Codex bridge publicly.
- Hosting Supermemory connectors or other platform-only features.
- Building a custom Supermemory dashboard or replacement landing page.
