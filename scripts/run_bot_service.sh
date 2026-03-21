#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/akibabot"

cd "$APP_DIR"
mkdir -p logs data

args=(
  "$APP_DIR/start_bot.py"
  "--config" "${AKIBA_CONFIG:-config/config.json}"
  "--strategy" "${AKIBA_STRATEGY:-ensemble}"
  "--host" "${AKIBA_HOST:-0.0.0.0}"
  "--port" "${AKIBA_PORT:-8000}"
)

if [[ "${AKIBA_NO_API:-false}" == "true" ]]; then
  args+=("--no-api")
fi

if [[ "${AKIBA_DRY_RUN:-false}" == "true" ]]; then
  args+=("--dry-run")
fi

if [[ "${LOG_LEVEL:-INFO}" == "DEBUG" ]]; then
  args+=("--debug")
fi

if [[ "${TRADING_MODE:-paper}" != "live" ]]; then
  args+=("--testnet")
fi

exec "$APP_DIR/venv/bin/python" "${args[@]}"
