#!/usr/bin/env bash
set -euo pipefail

test_dir="$(mktemp -d /tmp/hephaistos-supermemory-server-test.XXXXXX)"
suffix="${test_dir##*.}"
image="hephaistos-supermemory-server-test:${suffix}"
container="hephaistos-supermemory-server-test-${suffix}"
volume="hephaistos_supermemory_server_test_${suffix}"
container_created=false
volume_created=false
image_built=false
cleanup() {
  if "$container_created"; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
  if "$volume_created"; then docker volume rm "$volume" >/dev/null 2>&1 || true; fi
  if "$image_built"; then docker image rm "$image" >/dev/null 2>&1 || true; fi
  rmdir "$test_dir" >/dev/null 2>&1 || true
}
trap cleanup EXIT

grep -Fx 'ARG SUPERMEMORY_PLATFORM=linux/amd64' deploy/supermemory/server.Dockerfile
test "$(grep -Fc 'FROM --platform=$SUPERMEMORY_PLATFORM node:22-bookworm-slim@sha256:7af03b14a13c8cdd38e45058fd957bf00a72bbe17feac43b1c15a689c029c732' deploy/supermemory/server.Dockerfile)" = "2"
docker build --platform linux/amd64 -f deploy/supermemory/server.Dockerfile -t "$image" .
image_built=true
test "$(docker image inspect -f '{{.Os}}/{{.Architecture}}' "$image")" = "linux/amd64"
docker volume create "$volume" >/dev/null
volume_created=true
docker create --name "$container" -p 127.0.0.1::6767 \
  -e SUPERMEMORY_DATA_DIR=/var/lib/supermemory \
  -e SUPERMEMORY_SKIP_EMBEDDING_PREWARM=true \
  -e OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  -e OPENAI_API_KEY=test-only \
  -e OPENAI_MODEL=supermemory-codex \
  -v "$volume:/var/lib/supermemory" "$image" >/dev/null
container_created=true
docker start "$container" >/dev/null

host_port="$(docker port "$container" 6767/tcp | sed -n 's/.*://p')"
test -n "$host_port"

for _ in $(seq 1 60); do
  html="$(curl -fsS "http://127.0.0.1:${host_port}/" 2>/dev/null || true)"
  [[ "$html" == *"supermemory · local"* ]] && break
  sleep 1
done
[[ "$html" == *"supermemory · local"* ]]
[[ "$html" == *"/v4/reference"* ]]
[[ "$html" == *"/v4/openapi"* ]]
test "$(docker inspect -f '{{.Config.User}}' "$container")" = "node"
