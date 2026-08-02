#!/usr/bin/env bash
set +x
set -euo pipefail

EXPECTED_DNS_ADDRESS=162.19.229.31
ENV_FILE_NAME=.env.runtime
DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$DEPLOY_DIR/../.." && pwd)"
ENV_FILE="$DEPLOY_DIR/$ENV_FILE_NAME"
RUNTIME_TEMP=''
basic_auth_password=''
basic_auth_entry=''
escaped_basic_auth_entry=''
bridge_api_key=''
supermemory_logs=''
supermemory_api_key=''

cleanup() {
  unset basic_auth_password basic_auth_entry escaped_basic_auth_entry
  unset bridge_api_key supermemory_logs supermemory_api_key
  if [[ -n "$RUNTIME_TEMP" && -e "$RUNTIME_TEMP" ]]; then
    rm -f -- "$RUNTIME_TEMP"
  fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

usage() {
  printf 'Usage: %s [--resume]\n' "${0##*/}" >&2
}

resume=false
case "$#" in
  0) ;;
  1)
    [[ "$1" == --resume ]] || { usage; exit 2; }
    resume=true
    ;;
  *) usage; exit 2 ;;
esac

[[ -t 0 && -t 1 ]] || die 'bootstrap requires an interactive TTY on stdin and stdout'
command -v docker >/dev/null 2>&1 || die 'Docker is required'
docker compose version >/dev/null 2>&1 || die 'Docker Compose v2 is required'
docker network inspect traefik_default >/dev/null 2>&1 || \
  die 'required external Docker network traefik_default was not found'
command -v getent >/dev/null 2>&1 || die 'getent is required for the DNS preflight'

if [[ -e "$ENV_FILE" ]]; then
  [[ "$resume" == true ]] || die "$ENV_FILE_NAME already exists; use --resume to preserve its credentials"
  [[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || die "$ENV_FILE_NAME must be a regular file, not a symlink"
  [[ "$(stat -c '%a' "$ENV_FILE")" == 600 ]] || die "$ENV_FILE_NAME must have mode 600"
elif [[ "$resume" == true ]]; then
  die "$ENV_FILE_NAME does not exist; run bootstrap without --resume first"
fi

dns_output=''
observed_address='unresolved'
if dns_output="$(getent ahostsv4 persephone.cc 2>/dev/null)" && [[ -n "$dns_output" ]]; then
  read -r observed_address _ <<<"$dns_output"
fi
printf 'DNS: persephone.cc -> %s\n' "$observed_address"
if [[ "$observed_address" != "$EXPECTED_DNS_ADDRESS" ]]; then
  printf 'WARNING: persephone.cc does not currently resolve to %s; continuing so DNS can propagate.\n' \
    "$EXPECTED_DNS_ADDRESS" >&2
fi
unset dns_output observed_address

compose() {
  docker compose --env-file "$ENV_FILE" -f "$DEPLOY_DIR/compose.yaml" "$@"
}

codex_status_is_authenticated() {
  local status_output=''
  if ! status_output="$(compose run --rm --entrypoint codex codex-bridge login status 2>&1)"; then
    unset status_output
    die 'Codex login status failed for the dedicated codex_home volume'
  fi
  if [[ "${status_output,,}" != *'logged in using chatgpt'* ]]; then
    unset status_output
    die 'the dedicated codex_home volume is not authenticated'
  fi
  unset status_output
}

wait_for_health() {
  local service="$1" container_id='' health=''
  local attempt
  for attempt in {1..60}; do
    container_id="$(compose ps -q "$service")"
    if [[ -n "$container_id" ]]; then
      health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
      [[ "$health" == healthy ]] && return 0
      [[ "$health" == unhealthy || "$health" == exited || "$health" == dead ]] && break
    fi
    sleep 2
  done
  die "$service did not become healthy"
}

verify_basic_auth_label() {
  local escaped_entry='' expected_entry='' actual_entry='' container_id='' runtime_line=''
  local entry_count=0
  while IFS= read -r runtime_line || [[ -n "$runtime_line" ]]; do
    case "$runtime_line" in
      SUPERMEMORY_BASIC_AUTH_USERS=*)
        escaped_entry="${runtime_line#SUPERMEMORY_BASIC_AUTH_USERS=}"
        entry_count=$((entry_count + 1))
        ;;
    esac
  done <"$ENV_FILE"
  [[ "$entry_count" == 1 ]] || die "$ENV_FILE_NAME must contain exactly one SUPERMEMORY_BASIC_AUTH_USERS line"
  expected_entry="${escaped_entry//\$\$/\$}"
  [[ "$expected_entry" =~ ^titagram:\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$ ]] || \
    die "$ENV_FILE_NAME does not contain a valid Compose-escaped bcrypt entry"

  container_id="$(compose ps -q supermemory-server)"
  [[ -n "$container_id" ]] || die 'supermemory-server has no container for label verification'
  actual_entry="$(docker inspect --format '{{index .Config.Labels "traefik.http.middlewares.sm-basic-auth.basicauth.users"}}' "$container_id")"
  [[ "$actual_entry" == "$expected_entry" ]] || \
    die 'the rendered Traefik BasicAuth label does not contain the intended bcrypt entry'
}

if [[ "$resume" == true ]]; then
  codex_status_is_authenticated
  compose up -d --build
  verify_basic_auth_label
  printf 'Resume complete. Run: %s --local\n' "$DEPLOY_DIR/scripts/smoke.sh"
  exit 0
fi

command -v openssl >/dev/null 2>&1 || die 'openssl is required'

IFS= read -r -s -p 'Choose the BasicAuth password for titagram: ' basic_auth_password
printf '\n'
[[ -n "$basic_auth_password" ]] || die 'BasicAuth password must not be empty'
if ! basic_auth_entry="$(
  printf '%s\n' "$basic_auth_password" |
    docker run --rm -i httpd:2.4-alpine htpasswd -niB titagram
)"; then
  unset basic_auth_password
  die 'failed to generate the BasicAuth bcrypt entry'
fi
unset basic_auth_password
[[ "$basic_auth_entry" =~ ^titagram:\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}$ ]] || \
  die 'htpasswd returned an invalid bcrypt entry'
escaped_basic_auth_entry="${basic_auth_entry//\$/\$\$}"
unset basic_auth_entry

bridge_api_key="$(openssl rand -hex 32)"
[[ "$bridge_api_key" =~ ^[0-9a-f]{64}$ ]] || die 'openssl returned an invalid bridge key'

umask 077
RUNTIME_TEMP="$(mktemp "$DEPLOY_DIR/.env.runtime.tmp.XXXXXX")"
chmod 600 "$RUNTIME_TEMP"
{
  printf '%s\n' 'CODEX_MODEL=gpt-5.3-codex'
  printf 'SUPERMEMORY_BASIC_AUTH_USERS=%s\n' "$escaped_basic_auth_entry"
  printf '%s\n' 'SUPERMEMORY_API_KEY=sm_bootstrap_pending'
  printf 'SUPERMEMORY_BRIDGE_API_KEY=%s\n' "$bridge_api_key"
} >"$RUNTIME_TEMP"
chmod 600 "$RUNTIME_TEMP"
mv -f -- "$RUNTIME_TEMP" "$ENV_FILE"
RUNTIME_TEMP=''
unset escaped_basic_auth_entry bridge_api_key

printf '%s\n' 'Running image and Compose configuration tests...'
(
  cd "$REPO_ROOT"
  bash deploy/supermemory/tests/test-server-image.sh
  bash deploy/supermemory/tests/test-compose-config.sh
)

printf '%s\n' 'Building deployment images...'
compose build codex-bridge supermemory-server

printf '%s\n' 'Authenticate the dedicated Codex volume using device authentication.'
compose run --rm --entrypoint codex codex-bridge login --device-auth
codex_status_is_authenticated

compose up -d codex-bridge
wait_for_health codex-bridge
compose up -d supermemory-server

key_count=0
for attempt in {1..60}; do
  if ! supermemory_logs="$(compose logs --no-color supermemory-server 2>&1)"; then
    unset supermemory_logs
    die 'failed to read Supermemory logs for API key discovery'
  fi
  mapfile -t key_candidates < <(
    printf '%s\n' "$supermemory_logs" | grep -oE 'sm_[A-Za-z0-9_-]+' | sort -u
  )
  key_count="${#key_candidates[@]}"
  if ((key_count > 1)); then
    unset supermemory_logs key_candidates key_count
    die 'multiple distinct Supermemory API key candidates were found in captured logs'
  fi
  if ((key_count == 1)); then
    supermemory_api_key="${key_candidates[0]}"
    break
  fi
  unset key_candidates
  sleep 2
done
unset supermemory_logs key_candidates key_count
[[ -n "$supermemory_api_key" ]] || die 'no Supermemory API key candidate was found in captured logs'

RUNTIME_TEMP="$(mktemp "$DEPLOY_DIR/.env.runtime.tmp.XXXXXX")"
chmod 600 "$RUNTIME_TEMP"
replacement_count=0
while IFS= read -r runtime_line || [[ -n "$runtime_line" ]]; do
  case "$runtime_line" in
    SUPERMEMORY_API_KEY=*)
      printf 'SUPERMEMORY_API_KEY=%s\n' "$supermemory_api_key"
      replacement_count=$((replacement_count + 1))
      ;;
    *) printf '%s\n' "$runtime_line" ;;
  esac
done <"$ENV_FILE" >"$RUNTIME_TEMP"
unset runtime_line supermemory_api_key
[[ "$replacement_count" == 1 ]] || die "$ENV_FILE_NAME must contain exactly one SUPERMEMORY_API_KEY line"
unset replacement_count
chmod 600 "$RUNTIME_TEMP"
mv -f -- "$RUNTIME_TEMP" "$ENV_FILE"
RUNTIME_TEMP=''

compose up -d --force-recreate supermemory-server
wait_for_health supermemory-server
verify_basic_auth_label

printf 'Bootstrap complete. Run local acceptance with: %s --local\n' "$DEPLOY_DIR/scripts/smoke.sh"
printf 'After DNS and TLS are ready, run: %s --public\n' "$DEPLOY_DIR/scripts/smoke.sh"
