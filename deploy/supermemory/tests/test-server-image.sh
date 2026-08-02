#!/usr/bin/env bash
set -euo pipefail

image="hephaistos-supermemory-server:test"
container="hephaistos-supermemory-server-test"
volume="hephaistos_supermemory_server_test"
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; docker volume rm "$volume" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

grep -Fx 'ARG SUPERMEMORY_PLATFORM=linux/amd64' deploy/supermemory/server.Dockerfile
test "$(grep -Fc 'FROM --platform=$SUPERMEMORY_PLATFORM node:22-bookworm-slim@sha256:7af03b14a13c8cdd38e45058fd957bf00a72bbe17feac43b1c15a689c029c732' deploy/supermemory/server.Dockerfile)" = "2"
docker build --platform linux/amd64 -f deploy/supermemory/server.Dockerfile -t "$image" .
test "$(docker image inspect -f '{{.Os}}/{{.Architecture}}' "$image")" = "linux/amd64"
docker volume create "$volume" >/dev/null
docker run -d --name "$container" -p 127.0.0.1:16767:6767 \
  -e SUPERMEMORY_DATA_DIR=/var/lib/supermemory \
  -e SUPERMEMORY_SKIP_EMBEDDING_PREWARM=true \
  -e OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  -e OPENAI_API_KEY=test-only \
  -e OPENAI_MODEL=supermemory-codex \
  -v "$volume:/var/lib/supermemory" "$image"

for _ in $(seq 1 60); do
  html="$(curl -fsS http://127.0.0.1:16767/ 2>/dev/null || true)"
  [[ "$html" == *"supermemory · local"* ]] && break
  sleep 1
done
[[ "$html" == *"supermemory · local"* ]]
[[ "$html" == *"/v4/reference"* ]]
[[ "$html" == *"/v4/openapi"* ]]
test "$(docker inspect -f '{{.Config.User}}' "$container")" = "node"
