#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
image="hephaistos-supermemory-codex-bridge:test"

cd "$repo_root"

docker build --platform linux/amd64 \
  -f services/supermemory-codex-bridge/Dockerfile \
  -t "$image" \
  services/supermemory-codex-bridge

docker run --rm --entrypoint sh "$image" -c '
  test "$(id -un)" = node
  test -r /etc/ssl/certs/ca-certificates.crt
  test -s /etc/ssl/certs/ca-certificates.crt
'
