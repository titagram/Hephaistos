#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
temp_dir="$(mktemp -d)"
fixture_dir="$temp_dir/deploy/supermemory"
fake_bin="$temp_dir/bin"
docker_log="$temp_dir/docker.log"
output_file="$temp_dir/bootstrap.output"

cleanup() { rm -rf -- "$temp_dir"; }
trap cleanup EXIT

mkdir -p "$fixture_dir/scripts" "$fake_bin"
cp "$repo_root/deploy/supermemory/scripts/bootstrap.sh" "$fixture_dir/scripts/bootstrap.sh"
printf '%s\n' \
  'CODEX_MODEL=gpt-5.6-sol' \
  'SUPERMEMORY_BASIC_AUTH_USERS=titagram:$$2y$$10$$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
  'SUPERMEMORY_API_KEY=sm_complete_test_key' \
  'SUPERMEMORY_BRIDGE_API_KEY=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' \
  >"$fixture_dir/.env.runtime"
chmod 600 "$fixture_dir/.env.runtime"

cat >"$fake_bin/getent" <<'GETENT'
#!/usr/bin/env bash
printf '%s\n' '162.19.229.31 STREAM persephone.cc'
GETENT

cat >"$fake_bin/docker" <<'DOCKER'
#!/usr/bin/env bash
set -euo pipefail
printf 'docker' >>"$BOOTSTRAP_DOCKER_LOG"
printf ' %s' "$@" >>"$BOOTSTRAP_DOCKER_LOG"
printf '\n' >>"$BOOTSTRAP_DOCKER_LOG"

case "${1:-}" in
  network) exit 0 ;;
  inspect)
    if [[ "$*" == *'.Config.Labels'* ]]; then
      printf '%s\n' 'titagram:$2y$10$aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    else
      printf '%s\n' healthy
    fi
    exit 0
    ;;
  compose)
    shift
    [[ "${1:-}" == version ]] && exit 0
    while [[ "${1:-}" == --env-file || "${1:-}" == -f ]]; do shift 2; done
    case "${1:-}" in
      run) printf '%s\n' 'Logged in using ChatGPT' ;;
      up) ;;
      ps) printf 'fixture-%s\n' "${3:-service}" ;;
      *) exit 94 ;;
    esac
    ;;
  *) exit 95 ;;
esac
DOCKER

chmod +x "$fake_bin/docker" "$fake_bin/getent"
: >"$docker_log"

if ! BOOTSTRAP_DOCKER_LOG="$docker_log" script -qefc \
  "PATH='$fake_bin:/usr/bin:/bin' bash '$fixture_dir/scripts/bootstrap.sh' --resume" \
  /dev/null >"$output_file" 2>&1; then
  sed -n '1,120p' "$output_file" >&2
  exit 1
fi

grep -Fq 'Resume complete.' "$output_file"
test "$(grep -Ec ' ps -q codex-bridge$' "$docker_log")" = 1
test "$(grep -Ec ' ps -q supermemory-server$' "$docker_log")" = 2

printf '%s\n' 'bootstrap complete-resume health assertions: PASS'
