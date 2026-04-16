#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/akibabot_vultr}"
REMOTE_HOST="${REMOTE_HOST:-66.42.32.233}"
REMOTE_USER="${REMOTE_USER:-root}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/opt/akibabot}"
REMOTE_SERVICE="${REMOTE_SERVICE:-akibabot.service}"
REMOTE_PORT="${REMOTE_PORT:-8000}"
EXPECTED_CONFIGURED_DEPLOYMENT_PROFILE="${EXPECTED_CONFIGURED_DEPLOYMENT_PROFILE:-}"
EXPECTED_EFFECTIVE_RUNTIME_MODE="${EXPECTED_EFFECTIVE_RUNTIME_MODE:-}"
DEPLOY_SHA="${DEPLOY_SHA:-}"
TMP_DIR=""

usage() {
  cat <<'EOF'
Usage: deploy_snapshot.sh [--expected-profile PROFILE] [--expected-effective-mode MODE]

Optional verification flags:
  --expected-profile PROFILE           Assert configured_deployment_profile after deploy
  --expected-effective-mode MODE       Assert effective_runtime_mode after deploy
EOF
}

parse_args() {
  while (($#)); do
    case "$1" in
      --expected-profile)
        if (($# < 2)); then
          echo "Missing value for --expected-profile" >&2
          exit 2
        fi
        EXPECTED_CONFIGURED_DEPLOYMENT_PROFILE="$2"
        shift 2
        ;;
      --expected-profile=*)
        EXPECTED_CONFIGURED_DEPLOYMENT_PROFILE="${1#*=}"
        shift
        ;;
      --expected-effective-mode)
        if (($# < 2)); then
          echo "Missing value for --expected-effective-mode" >&2
          exit 2
        fi
        EXPECTED_EFFECTIVE_RUNTIME_MODE="$2"
        shift 2
        ;;
      --expected-effective-mode=*)
        EXPECTED_EFFECTIVE_RUNTIME_MODE="${1#*=}"
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done
}

verify_status_payload() {
  local status_json="$1"
  local expected_profile="${2:-}"
  local expected_effective_mode="${3:-}"

  if [[ -z "$expected_profile" && -z "$expected_effective_mode" ]]; then
    return 0
  fi

  STATUS_JSON="$status_json" \
  EXPECTED_CONFIGURED_DEPLOYMENT_PROFILE="$expected_profile" \
  EXPECTED_EFFECTIVE_RUNTIME_MODE="$expected_effective_mode" \
  python3 - <<'PY'
import json
import os
import sys

status = json.loads(os.environ["STATUS_JSON"])
if not isinstance(status, dict):
    print("Status verification failed:", file=sys.stderr)
    print("- status payload was not a JSON object", file=sys.stderr)
    raise SystemExit(1)

expected_profile = os.environ.get("EXPECTED_CONFIGURED_DEPLOYMENT_PROFILE", "")
expected_mode = os.environ.get("EXPECTED_EFFECTIVE_RUNTIME_MODE", "")
actual_profile = status.get("configured_deployment_profile")
actual_mode = status.get("effective_runtime_mode")
errors: list[str] = []

if expected_profile and actual_profile != expected_profile:
    errors.append(
        "configured_deployment_profile mismatch: "
        f"expected {expected_profile!r}, got {actual_profile!r}"
    )

if expected_mode and actual_mode != expected_mode:
    errors.append(
        "effective_runtime_mode mismatch: "
        f"expected {expected_mode!r}, got {actual_mode!r}"
    )

if errors:
    print("Status verification failed:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)
PY
}

main() {
  parse_args "$@"

  if [[ -z "$DEPLOY_SHA" ]]; then
    DEPLOY_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  fi

  TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/akiba_deploy_${DEPLOY_SHA:0:7}_XXXXXX")"

  cleanup() {
    if [[ -n "${TMP_DIR:-}" ]]; then
      rm -rf "$TMP_DIR"
    fi
  }

  trap cleanup EXIT

  git -C "$REPO_ROOT" archive --format=tar "$DEPLOY_SHA" | tar -xf - -C "$TMP_DIR"

  rsync -a --delete \
    -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=no" \
    --exclude '.env' \
    --exclude 'venv/' \
    --exclude '.venv/' \
    --exclude 'logs/' \
    --exclude 'data/' \
    --exclude 'models/' \
    --exclude '.deploy/' \
    "$TMP_DIR"/ \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_APP_DIR}/"

  local remote_env
  remote_env="DEPLOY_SHA=$(printf '%q' "$DEPLOY_SHA") REMOTE_APP_DIR=$(printf '%q' "$REMOTE_APP_DIR") REMOTE_SERVICE=$(printf '%q' "$REMOTE_SERVICE") REMOTE_PORT=$(printf '%q' "$REMOTE_PORT") EXPECTED_CONFIGURED_DEPLOYMENT_PROFILE=$(printf '%q' "$EXPECTED_CONFIGURED_DEPLOYMENT_PROFILE") EXPECTED_EFFECTIVE_RUNTIME_MODE=$(printf '%q' "$EXPECTED_EFFECTIVE_RUNTIME_MODE")"

  ssh -T -i "$SSH_KEY" -o StrictHostKeyChecking=no "${REMOTE_USER}@${REMOTE_HOST}" "${remote_env} bash -s" <<'EOF'
set -euo pipefail

wait_for_json() {
  local label="$1"
  local url="$2"
  local deadline=$((SECONDS + 180))
  local response_file

  response_file="$(mktemp)"
  while true; do
    if curl -fsS "$url" -o "$response_file" >/dev/null 2>&1; then
      cat "$response_file"
      rm -f "$response_file"
      return 0
    fi

    if (( SECONDS >= deadline )); then
      echo "${label} endpoint did not become ready in time: ${url}" >&2
      systemctl status "$REMOTE_SERVICE" --no-pager || true
      journalctl -u "$REMOTE_SERVICE" -n 50 --no-pager || true
      rm -f "$response_file"
      return 1
    fi

    sleep 2
  done
}

verify_status_payload() {
  local status_json="$1"
  local expected_profile="${2:-}"
  local expected_effective_mode="${3:-}"

  if [[ -z "$expected_profile" && -z "$expected_effective_mode" ]]; then
    return 0
  fi

  STATUS_JSON="$status_json" \
  EXPECTED_CONFIGURED_DEPLOYMENT_PROFILE="$expected_profile" \
  EXPECTED_EFFECTIVE_RUNTIME_MODE="$expected_effective_mode" \
  python3 - <<'PY'
import json
import os
import sys

status = json.loads(os.environ["STATUS_JSON"])
if not isinstance(status, dict):
    print("Status verification failed:", file=sys.stderr)
    print("- status payload was not a JSON object", file=sys.stderr)
    raise SystemExit(1)

expected_profile = os.environ.get("EXPECTED_CONFIGURED_DEPLOYMENT_PROFILE", "")
expected_mode = os.environ.get("EXPECTED_EFFECTIVE_RUNTIME_MODE", "")
actual_profile = status.get("configured_deployment_profile")
actual_mode = status.get("effective_runtime_mode")
errors: list[str] = []

if expected_profile and actual_profile != expected_profile:
    errors.append(
        "configured_deployment_profile mismatch: "
        f"expected {expected_profile!r}, got {actual_profile!r}"
    )

if expected_mode and actual_mode != expected_mode:
    errors.append(
        "effective_runtime_mode mismatch: "
        f"expected {expected_mode!r}, got {actual_mode!r}"
    )

if errors:
    print("Status verification failed:", file=sys.stderr)
    for error in errors:
        print(f"- {error}", file=sys.stderr)
    raise SystemExit(1)
PY
}

mkdir -p "$REMOTE_APP_DIR/.deploy"
echo "Deploy SHA: $DEPLOY_SHA"
printf '%s\n' "$DEPLOY_SHA" > "$REMOTE_APP_DIR/.deploy/current_sha"
systemctl restart "$REMOTE_SERVICE"

deadline=$((SECONDS + 180))
until systemctl is-active --quiet "$REMOTE_SERVICE"; do
  if (( SECONDS >= deadline )); then
    systemctl status "$REMOTE_SERVICE" --no-pager || true
    journalctl -u "$REMOTE_SERVICE" -n 50 --no-pager || true
    exit 1
  fi
  sleep 2
done

health_json="$(wait_for_json "health" "http://127.0.0.1:$REMOTE_PORT/api/health")"
status_json="$(wait_for_json "status" "http://127.0.0.1:$REMOTE_PORT/api/status")"
verify_status_payload \
  "$status_json" \
  "$EXPECTED_CONFIGURED_DEPLOYMENT_PROFILE" \
  "$EXPECTED_EFFECTIVE_RUNTIME_MODE"

echo "--- HEALTH ---"
printf '%s\n' "$health_json"
echo
echo "--- STATUS ---"
printf '%s\n' "$status_json"
echo
EOF
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
