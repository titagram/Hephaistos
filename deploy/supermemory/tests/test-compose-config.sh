#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
temp_dir="$(mktemp -d)"
env_file="$temp_dir/.env.runtime"
config_file="$temp_dir/compose.json"
cleanup() { rm -rf "$temp_dir"; }
trap cleanup EXIT

umask 077
cat >"$env_file" <<'ENV'
SUPERMEMORY_BASIC_AUTH_USERS=test-user:test-hash
SUPERMEMORY_API_KEY=supermemory-test-key
SUPERMEMORY_BRIDGE_API_KEY=bridge-test-key
ENV
chmod 600 "$env_file"

cd "$repo_root"
docker compose --env-file "$env_file" \
  -f deploy/supermemory/compose.yaml config --format json >"$config_file"

node - "$config_file" <<'NODE'
const assert = require("node:assert/strict");
const fs = require("node:fs");

const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const bridge = config.services["codex-bridge"];
const server = config.services["supermemory-server"];

assert.ok(bridge, "codex-bridge service must exist");
assert.ok(server, "supermemory-server service must exist");

const networkNames = (service) => Object.keys(service.networks ?? {}).sort();
assert.ok(!bridge.ports || bridge.ports.length === 0, "bridge must not publish host ports");
assert.deepEqual(networkNames(bridge), ["backend"], "bridge must join only backend");
assert.ok(!server.ports || server.ports.length === 0, "server must not publish host ports");
assert.deepEqual(
  networkNames(server),
  ["backend", "traefik_default"],
  "server must join backend and traefik_default",
);
assert.notEqual(config.networks.backend.internal, true, "backend must permit outbound Internet egress");
assert.equal(config.networks.traefik_default.external, true, "Traefik network must be external");

for (const [name, service] of Object.entries({ bridge, server })) {
  assert.equal(service.restart, "unless-stopped", `${name} restart policy`);
  assert.ok(service.healthcheck?.test?.length, `${name} healthcheck must be explicit`);
  assert.equal(service.logging?.driver, "json-file", `${name} logging driver`);
  assert.equal(service.logging?.options?.["max-size"], "10m", `${name} max-size`);
  assert.equal(service.logging?.options?.["max-file"], "3", `${name} max-file`);
}

assert.deepEqual(bridge.expose, ["8646"], "bridge must expose only 8646 internally");
assert.deepEqual(server.expose, ["6767"], "server must expose only 6767 internally");
assert.equal(bridge.read_only, true, "bridge root filesystem must be read-only");
assert.deepEqual(bridge.cap_drop, ["ALL"], "bridge must drop every Linux capability");
assert.ok(
  bridge.security_opt?.includes("no-new-privileges:true"),
  "bridge must disable privilege escalation",
);
assert.ok(bridge.tmpfs?.includes("/tmp"), "bridge must have a writable tmpfs at /tmp");

const bridgeVolumes = bridge.volumes ?? [];
assert.ok(
  bridgeVolumes.some((mount) => mount.type === "volume" && mount.source === "codex_home" && mount.target === "/var/lib/codex"),
  "bridge must mount codex_home",
);
assert.ok(
  bridgeVolumes.some((mount) => mount.type === "bind" && mount.target === "/var/lib/codex/config.toml" && mount.read_only === true),
  "bridge must mount codex-config.toml read-only",
);
assert.ok(
  (server.volumes ?? []).some((mount) => mount.type === "volume" && mount.source === "supermemory_data" && mount.target === "/var/lib/supermemory"),
  "server must mount supermemory_data",
);
assert.equal(server.depends_on?.["codex-bridge"]?.condition, "service_healthy");

assert.equal(server.environment.OPENAI_BASE_URL, "http://codex-bridge:8646/v1");
assert.equal(server.environment.OPENAI_API_KEY, "bridge-test-key");
for (const name of ["OPENAI_MODEL", "OPENAI_FAST_MODEL", "OPENAI_TEXT_MODEL"]) {
  assert.equal(server.environment[name], "supermemory-codex", `${name} must select the bridge model`);
}
assert.equal(server.environment.SUPERMEMORY_SKIP_EMBEDDING_PREWARM, "true");
assert.equal(
  server.environment.WORKFLOW_ENGINE,
  "direct",
  "server must process ingestion directly without the unavailable Rivet runtime",
);
assert.equal(bridge.environment.BRIDGE_API_KEY, "bridge-test-key");
assert.equal(bridge.environment.CODEX_MODEL, "gpt-5.6-sol");
assert.ok(!Object.hasOwn(bridge.environment, "OPENAI_API_KEY"), "bridge must not receive OPENAI_API_KEY");

const bridgeLabels = bridge.labels ?? {};
const labels = server.labels ?? {};
assert.equal(Object.keys(bridgeLabels).length, 0, "bridge must have no Traefik labels");
assert.equal(labels["traefik.enable"], "true");
assert.equal(labels["traefik.docker.network"], "traefik_default");

assert.equal(labels["traefik.http.routers.sm-http.rule"], "Host(`persephone.cc`)");
assert.equal(labels["traefik.http.routers.sm-http.entrypoints"], "web");
assert.equal(labels["traefik.http.routers.sm-http.middlewares"], "sm-https-redirect");
assert.equal(labels["traefik.http.middlewares.sm-https-redirect.redirectscheme.scheme"], "https");

const httpsRouters = {
  "sm-web": {
    priority: "10",
    rule: "Host(`persephone.cc`)",
    middlewares: "sm-basic-auth,sm-backend-key",
  },
  "sm-docs": {
    priority: "300",
    rule: "Host(`persephone.cc`) && (PathPrefix(`/v4/reference`) || PathPrefix(`/v4/openapi`))",
    middlewares: "sm-basic-auth,sm-backend-key",
  },
  "sm-api": {
    priority: "200",
    rule: "Host(`persephone.cc`) && (PathPrefix(`/v3`) || PathPrefix(`/v4`) || PathPrefix(`/files`))",
    middlewares: undefined,
  },
};

for (const [router, expected] of Object.entries(httpsRouters)) {
  const prefix = `traefik.http.routers.${router}`;
  assert.equal(labels[`${prefix}.entrypoints`], "websecure", `${router} entrypoint`);
  assert.equal(labels[`${prefix}.priority`], expected.priority, `${router} priority`);
  assert.equal(labels[`${prefix}.rule`], expected.rule, `${router} rule`);
  assert.equal(labels[`${prefix}.middlewares`], expected.middlewares, `${router} middleware split`);
  assert.equal(labels[`${prefix}.service`], "sm-service", `${router} service`);
  assert.equal(labels[`${prefix}.tls`], "true", `${router} TLS`);
  assert.equal(labels[`${prefix}.tls.certresolver`], "le", `${router} resolver`);
}

assert.equal(labels["traefik.http.middlewares.sm-basic-auth.basicauth.users"], "test-user:test-hash");
assert.equal(labels["traefik.http.middlewares.sm-basic-auth.basicauth.removeheader"], "true");
assert.equal(
  labels["traefik.http.middlewares.sm-backend-key.headers.customrequestheaders.Authorization"],
  "Bearer supermemory-test-key",
);
assert.equal(labels["traefik.http.services.sm-service.loadbalancer.server.port"], "6767");

assert.ok(config.volumes.codex_home, "codex_home volume must exist");
assert.ok(config.volumes.supermemory_data, "supermemory_data volume must exist");
NODE

echo "compose topology assertions: PASS"
