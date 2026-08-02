#!/usr/bin/env bash
set +x
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$DEPLOY_DIR/.env.runtime"
MODE=''
DEBUG_SAFE=false
TEMP_DIR=''
SUPERMEMORY_API_KEY=''
basic_auth_password=''

cleanup() {
  unset SUPERMEMORY_API_KEY basic_auth_password curl_password
  if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
    rm -rf -- "$TEMP_DIR"
  fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

die() {
  printf 'ERROR: %s\n' "$1" >&2
  exit 1
}

usage() {
  printf 'Usage: %s (--local|--public) [--debug-safe]\n' "${0##*/}" >&2
}

while (($#)); do
  case "$1" in
    --local|--public)
      [[ -z "$MODE" ]] || { usage; exit 2; }
      MODE="$1"
      ;;
    --debug-safe) DEBUG_SAFE=true ;;
    *) usage; exit 2 ;;
  esac
  shift
done
[[ -n "$MODE" ]] || { usage; exit 2; }

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || die '.env.runtime must be a regular file, not a symlink'
[[ "$(stat -c '%a' "$ENV_FILE")" == 600 ]] || die '.env.runtime must have mode 600'

api_key_count=0
while IFS= read -r runtime_line || [[ -n "$runtime_line" ]]; do
  case "$runtime_line" in
    SUPERMEMORY_API_KEY=*)
      SUPERMEMORY_API_KEY="${runtime_line#SUPERMEMORY_API_KEY=}"
      api_key_count=$((api_key_count + 1))
      ;;
  esac
done <"$ENV_FILE"
unset runtime_line
[[ "$api_key_count" == 1 ]] || die '.env.runtime must contain exactly one SUPERMEMORY_API_KEY line'
unset api_key_count
[[ "$SUPERMEMORY_API_KEY" =~ ^sm_[A-Za-z0-9_-]+$ && "$SUPERMEMORY_API_KEY" != sm_bootstrap_pending ]] || \
  die '.env.runtime does not contain a discovered Supermemory API key'

TEMP_DIR="$(mktemp -d /tmp/supermemory-smoke.XXXXXX)"
BODY_FILE="$TEMP_DIR/response.body"
HEADER_FILE="$TEMP_DIR/response.headers"

compose() {
  docker compose --env-file "$ENV_FILE" -f "$DEPLOY_DIR/compose.yaml" "$@"
}

assert_healthy() {
  local service="$1" container_id='' health=''
  container_id="$(compose ps -q "$service")"
  [[ -n "$container_id" ]] || die "$service has no running container"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id")"
  [[ "$health" == healthy ]] || die "$service health is $health"
}

request_failure() {
  local label="$1" status="${2:-curl-error}"
  printf 'ERROR: %s failed (HTTP %s); response body redacted\n' "$label" "$status" >&2
  if [[ "$DEBUG_SAFE" == true && -s "$BODY_FILE" ]]; then
    printf '%s\n' '--- debug-safe response body ---' >&2
    sed -n '1,120p' "$BODY_FILE" >&2
  fi
  exit 1
}

plain_request() {
  local label="$1" url="$2" status=''
  : >"$BODY_FILE"
  : >"$HEADER_FILE"
  if ! status="$(curl --silent --show-error --max-time 20 --max-filesize 1048576 \
    --output "$BODY_FILE" --dump-header "$HEADER_FILE" --write-out '%{http_code}' "$url")"; then
    request_failure "$label" curl-error
  fi
  HTTP_STATUS="$status"
}

basic_request() {
  local label="$1" url="$2" status=''
  curl_password="${basic_auth_password//\\/\\\\}"
  curl_password="${curl_password//\"/\\\"}"
  : >"$BODY_FILE"
  : >"$HEADER_FILE"
  if ! status="$(
    printf 'user = "titagram:%s"\n' "$curl_password" |
      curl --config - --silent --show-error --max-time 20 --max-filesize 1048576 \
        --output "$BODY_FILE" --dump-header "$HEADER_FILE" --write-out '%{http_code}' "$url"
  )"; then
    unset curl_password basic_auth_password
    request_failure "$label" curl-error
  fi
  unset curl_password basic_auth_password
  HTTP_STATUS="$status"
}

bearer_request() {
  local label="$1" url="$2" status=''
  : >"$BODY_FILE"
  : >"$HEADER_FILE"
  if ! status="$(
    printf 'header = "Authorization: Bearer %s"\n' "$SUPERMEMORY_API_KEY" |
      curl --config - --silent --show-error --max-time 20 --max-filesize 1048576 \
        --output "$BODY_FILE" --dump-header "$HEADER_FILE" --write-out '%{http_code}' "$url"
  )"; then
    request_failure "$label" curl-error
  fi
  HTTP_STATUS="$status"
}

header_value() {
  local wanted="${1,,}" file="$2" line='' name='' value=''
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    name="${line%%:*}"
    if [[ "${name,,}" == "$wanted" && "$line" == *:* ]]; then
      value="${line#*:}"
      while [[ "$value" == ' '* || "$value" == $'\t'* ]]; do value="${value:1}"; done
      printf '%s\n' "$value"
      return 0
    fi
  done <"$file"
  return 1
}

case "$MODE" in
  --local)
    command -v docker >/dev/null 2>&1 || die 'Docker is required'
    docker compose version >/dev/null 2>&1 || die 'Docker Compose v2 is required'
    assert_healthy codex-bridge
    assert_healthy supermemory-server
    compose exec -T supermemory-server node -e \
      "require('http').get('http://127.0.0.1:6767/',r=>{let b='';r.on('data',c=>b+=c);r.on('end',()=>process.exit(r.statusCode===200&&b.includes('supermemory · local')?0:1))}).on('error',()=>process.exit(1))" \
      >/dev/null 2>&1 || die 'Supermemory root HTML did not contain the expected local marker'

    bridge_container_id="$(compose ps -q codex-bridge)"
    port_bindings="$(docker inspect --format '{{json .HostConfig.PortBindings}}' "$bridge_container_id")"
    [[ "$port_bindings" == '{}' || "$port_bindings" == null ]] || die 'codex-bridge publishes a host port'
    bridge_labels="$(docker inspect --format '{{json .Config.Labels}}' "$bridge_container_id")"
    [[ "$bridge_labels" != *'"traefik.'* ]] || die 'codex-bridge has a Traefik label'
    unset bridge_container_id port_bindings bridge_labels
    printf '%s\n' 'local smoke: PASS'
    ;;

  --public)
    [[ -t 0 && -t 1 ]] || die 'public smoke requires an interactive TTY for the BasicAuth password prompt'
    command -v curl >/dev/null 2>&1 || die 'curl is required'
    command -v openssl >/dev/null 2>&1 || die 'openssl is required'

    plain_request 'HTTP to HTTPS redirect' 'http://persephone.cc/'
    [[ "$HTTP_STATUS" =~ ^30[1278]$ ]] || request_failure 'HTTP to HTTPS redirect' "$HTTP_STATUS"
    redirect_location="$(header_value location "$HEADER_FILE" || true)"
    [[ "$redirect_location" == https://persephone.cc/* ]] || request_failure 'HTTP to HTTPS redirect target' "$HTTP_STATUS"

    plain_request 'unauthenticated UI' 'https://persephone.cc/'
    [[ "$HTTP_STATUS" == 401 ]] || request_failure 'unauthenticated UI' "$HTTP_STATUS"
    root_challenge="$(header_value www-authenticate "$HEADER_FILE" || true)"
    [[ "${root_challenge,,}" == basic* ]] || request_failure 'unauthenticated UI Basic challenge' "$HTTP_STATUS"

    plain_request 'unauthenticated reference docs' 'https://persephone.cc/v4/reference'
    [[ "$HTTP_STATUS" == 401 ]] || request_failure 'unauthenticated reference docs' "$HTTP_STATUS"
    docs_challenge="$(header_value www-authenticate "$HEADER_FILE" || true)"
    [[ "$docs_challenge" == "$root_challenge" ]] || request_failure 'reference docs Basic challenge' "$HTTP_STATUS"

    plain_request 'unauthenticated API' 'https://persephone.cc/v4/memories'
    [[ "$HTTP_STATUS" == 401 || "$HTTP_STATUS" == 403 ]] || request_failure 'unauthenticated API' "$HTTP_STATUS"
    api_challenge="$(header_value www-authenticate "$HEADER_FILE" || true)"
    [[ "${api_challenge,,}" != basic* ]] || request_failure 'API challenge isolation' "$HTTP_STATUS"

    IFS= read -r -s -p 'BasicAuth password for titagram: ' basic_auth_password
    printf '\n'
    [[ -n "$basic_auth_password" ]] || die 'BasicAuth password must not be empty'
    basic_request 'authenticated UI' 'https://persephone.cc/'
    [[ "$HTTP_STATUS" == 200 ]] || request_failure 'authenticated UI' "$HTTP_STATUS"
    grep -Fq 'supermemory · local' "$BODY_FILE" || request_failure 'authenticated UI marker' "$HTTP_STATUS"

    bearer_request 'authenticated API' 'https://persephone.cc/v4/memories'
    [[ "$HTTP_STATUS" =~ ^2[0-9][0-9]$ ]] || request_failure 'authenticated API' "$HTTP_STATUS"
    unset SUPERMEMORY_API_KEY

    if ! openssl s_client -connect persephone.cc:443 -servername persephone.cc </dev/null 2>/dev/null |
      openssl x509 -noout -checkhost persephone.cc >/dev/null; then
      die 'TLS certificate does not cover persephone.cc'
    fi
    unset redirect_location root_challenge docs_challenge api_challenge
    printf '%s\n' 'public smoke: PASS'
    ;;
esac
