#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
temp_dir="$(mktemp -d)"
fixture_dir="$temp_dir/deploy"
fake_bin="$temp_dir/bin"
url_log="$temp_dir/urls.log"
output_file="$temp_dir/smoke.output"

cleanup() { rm -rf -- "$temp_dir"; }
trap cleanup EXIT

mkdir -p "$fixture_dir/scripts" "$fake_bin"
cp "$repo_root/deploy/supermemory/scripts/smoke.sh" "$fixture_dir/scripts/smoke.sh"
printf '%s\n' 'SUPERMEMORY_API_KEY=sm_test_key' >"$fixture_dir/.env.runtime"
chmod 600 "$fixture_dir/.env.runtime"

cat >"$fake_bin/curl" <<'CURL'
#!/usr/bin/env bash
set -euo pipefail

body_file=''
header_file=''
url=''
auth_kind=none

while (($#)); do
  case "$1" in
    --config)
      [[ "${2:-}" == - ]] || exit 90
      IFS= read -r config_line || true
      case "$config_line" in
        user\ =*) auth_kind=basic ;;
        header\ =*) auth_kind=bearer ;;
        *) exit 93 ;;
      esac
      shift 2
      ;;
    --output) body_file="$2"; shift 2 ;;
    --dump-header) header_file="$2"; shift 2 ;;
    --write-out|--max-time|--max-filesize) shift 2 ;;
    --silent|--show-error) shift ;;
    http://*|https://*) url="$1"; shift ;;
    *) exit 91 ;;
  esac
done

printf '%s\n' "$url" >>"$SMOKE_URL_LOG"
: >"$body_file"
: >"$header_file"

case "$url:$auth_kind" in
  http://persephone.cc/:none)
    printf 'HTTP/1.1 301 Moved Permanently\r\nLocation: https://persephone.cc/\r\n\r\n' >"$header_file"
    printf 301
    ;;
  https://persephone.cc/:none|https://persephone.cc/v4/reference:none)
    printf 'HTTP/2 401\r\nWWW-Authenticate: Basic realm="supermemory"\r\n\r\n' >"$header_file"
    printf 401
    ;;
  https://persephone.cc/v3/settings:none|https://persephone.cc/v3/settings:basic)
    printf 'HTTP/2 401\r\n\r\n' >"$header_file"
    printf 401
    ;;
  https://persephone.cc/:basic)
    printf 'HTTP/2 200\r\n\r\n' >"$header_file"
    printf '%s\n' 'supermemory · local' >"$body_file"
    printf 200
    ;;
  https://persephone.cc/v3/settings:bearer)
    printf 'HTTP/2 200\r\n\r\n' >"$header_file"
    printf '%s\n' '{}' >"$body_file"
    printf 200
    ;;
  *)
    printf 'HTTP/2 404\r\n\r\n' >"$header_file"
    printf 404
    ;;
esac
CURL

cat >"$fake_bin/openssl" <<'OPENSSL'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  s_client) printf '%s\n' 'fake certificate' ;;
  x509) cat >/dev/null ;;
  *) exit 92 ;;
esac
OPENSSL

chmod +x "$fake_bin/curl" "$fake_bin/openssl"
: >"$url_log"

if ! printf '%s\n' 'test-password' |
  SMOKE_URL_LOG="$url_log" script -qefc \
    "PATH='$fake_bin:/usr/bin:/bin' bash '$fixture_dir/scripts/smoke.sh' --public" \
    /dev/null >"$output_file" 2>&1; then
  sed -n '1,120p' "$output_file" >&2
  exit 1
fi

grep -Fq 'public smoke: PASS' "$output_file"
expected_urls="$temp_dir/expected-urls.log"
cat >"$expected_urls" <<'URLS'
http://persephone.cc/
https://persephone.cc/
https://persephone.cc/v4/reference
https://persephone.cc/v3/settings
https://persephone.cc/
https://persephone.cc/v3/settings
https://persephone.cc/v3/settings
URLS
cmp "$expected_urls" "$url_log"

printf '%s\n' 'public smoke route assertions: PASS'
