#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/home/lekim/Projects/akibaCryptobot}"
SSH_KEY="${SSH_KEY:-/home/lekim/.ssh/akibabot_vultr}"
REMOTE_HOST="${REMOTE_HOST:-66.42.32.233}"
REMOTE_USER="${REMOTE_USER:-root}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/opt/akibabot}"
REMOTE_SERVICE="${REMOTE_SERVICE:-akibabot.service}"
REMOTE_PORT="${REMOTE_PORT:-8000}"
DEPLOY_SHA="${DEPLOY_SHA:-$(git -C "$REPO_ROOT" rev-parse HEAD)}"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/akiba_deploy_${DEPLOY_SHA:0:7}_XXXXXX")"

cleanup() {
  rm -rf "$TMP_DIR"
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
  --exclude '.deploy/' \
  "$TMP_DIR"/ \
  "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_APP_DIR}/"

ssh -T -i "$SSH_KEY" -o StrictHostKeyChecking=no "${REMOTE_USER}@${REMOTE_HOST}" bash -s <<EOF
set -euo pipefail

mkdir -p "${REMOTE_APP_DIR}/.deploy"
printf '%s\n' "${DEPLOY_SHA}" > "${REMOTE_APP_DIR}/.deploy/current_sha"
systemctl restart "${REMOTE_SERVICE}"

deadline=\$((SECONDS + 180))
until systemctl is-active --quiet "${REMOTE_SERVICE}"; do
  if (( SECONDS >= deadline )); then
    systemctl status "${REMOTE_SERVICE}" --no-pager || true
    journalctl -u "${REMOTE_SERVICE}" -n 50 --no-pager || true
    exit 1
  fi
  sleep 2
done

until curl -fsS "http://127.0.0.1:${REMOTE_PORT}/api/health" >/dev/null; do
  if (( SECONDS >= deadline )); then
    systemctl status "${REMOTE_SERVICE}" --no-pager || true
    journalctl -u "${REMOTE_SERVICE}" -n 50 --no-pager || true
    exit 1
  fi
  sleep 2
done

echo "--- HEALTH ---"
curl -fsS "http://127.0.0.1:${REMOTE_PORT}/api/health"
echo
echo "--- STATUS ---"
curl -fsS "http://127.0.0.1:${REMOTE_PORT}/api/status"
echo
EOF
