#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${AKIBA_APP_DIR:-/opt/akibabot}"
SERVICE_NAME="${AKIBA_SERVICE_NAME:-akibabot.service}"
PORT="${AKIBA_PORT:-8000}"
DEPLOY_STATE_DIR="${APP_DIR}/.deploy"
REQ_HASH_FILE="${DEPLOY_STATE_DIR}/requirements.sha256"

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required environment variable: ${name}" >&2
    exit 1
  fi
}

wait_for_service() {
  local deadline=$((SECONDS + 180))
  until systemctl is-active --quiet "$SERVICE_NAME"; do
    if (( SECONDS >= deadline )); then
      echo "Service ${SERVICE_NAME} did not reach active state in time" >&2
      systemctl status "$SERVICE_NAME" --no-pager || true
      journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true
      exit 1
    fi
    sleep 2
  done
}

wait_for_health() {
  local url="http://127.0.0.1:${PORT}/api/health"
  local deadline=$((SECONDS + 180))
  until curl -fsS "$url" >/dev/null; do
    if (( SECONDS >= deadline )); then
      echo "Health endpoint did not become ready: ${url}" >&2
      systemctl status "$SERVICE_NAME" --no-pager || true
      journalctl -u "$SERVICE_NAME" -n 50 --no-pager || true
      exit 1
    fi
    sleep 2
  done
}

require_env "BINANCE_API_KEY"
require_env "BINANCE_SECRET_KEY"
require_env "TELEGRAM_BOT_TOKEN"
require_env "TELEGRAM_CHAT_ID"

mkdir -p "$APP_DIR" "$APP_DIR/logs" "$APP_DIR/data" "$DEPLOY_STATE_DIR"
cd "$APP_DIR"

cat > .env <<EOF
BINANCE_API_KEY=${BINANCE_API_KEY}
BINANCE_SECRET_KEY=${BINANCE_SECRET_KEY}
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
TRADING_MODE=${TRADING_MODE:-paper}
LOG_LEVEL=${LOG_LEVEL:-INFO}
AKIBA_STRATEGY=${AKIBA_STRATEGY:-ensemble}
AKIBA_HOST=${AKIBA_HOST:-0.0.0.0}
AKIBA_PORT=${PORT}
AKIBA_CONFIG=${AKIBA_CONFIG:-config/config.json}
AKIBA_NO_API=${AKIBA_NO_API:-false}
AKIBA_DRY_RUN=${AKIBA_DRY_RUN:-false}
EOF
chmod 600 .env

if [[ ! -d venv ]]; then
  python3 -m venv venv
fi

if [[ ! -x venv/bin/pip ]]; then
  echo "Remote virtualenv is missing pip" >&2
  exit 1
fi

requirements_hash="$(sha256sum requirements.txt | awk '{print $1}')"
previous_hash="$(cat "$REQ_HASH_FILE" 2>/dev/null || true)"

if [[ "$requirements_hash" != "$previous_hash" ]]; then
  venv/bin/pip install --disable-pip-version-check -r requirements.txt
  printf '%s\n' "$requirements_hash" > "$REQ_HASH_FILE"
else
  echo "requirements.txt unchanged; skipping pip install"
fi

chmod 755 scripts/run_bot_service.sh
install -D -m 0644 deploy/systemd/akibabot.service /etc/systemd/system/akibabot.service
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

wait_for_service
wait_for_health

printf '%s\n' "${DEPLOY_SHA:-unknown}" > "${DEPLOY_STATE_DIR}/current_sha"

echo "--- HEALTH ---"
curl -fsS "http://127.0.0.1:${PORT}/api/health"
echo
echo "--- STATUS ---"
curl -fsS "http://127.0.0.1:${PORT}/api/status"
echo
