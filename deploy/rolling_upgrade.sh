#!/usr/bin/env bash
set -euo pipefail

# Blue/green helper.  It starts a candidate on an alternate port, waits for
# /health, atomically replaces the configured nginx upstream, then drains the
# old PID.  A failed health check leaves the old process serving traffic.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANDIDATE_ROOT="${1:-$ROOT}"
OLD_PORT="${EXCELMANUS_BACKEND_PORT:-8000}"
NEW_PORT="${EXCELMANUS_ROLLING_PORT:-8001}"
NGINX_CONF="${EXCELMANUS_NGINX_CONF:-$ROOT/deploy/nginx.conf}"
PYTHON_BIN="${EXCELMANUS_PYTHON:-$ROOT/.venv/bin/python}"
OLD_PID="${EXCELMANUS_OLD_PID:-}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi
if [[ ! -f "$NGINX_CONF" ]]; then
  echo "nginx 配置不存在: $NGINX_CONF" >&2
  exit 2
fi
if [[ "$OLD_PORT" == "$NEW_PORT" ]]; then
  echo "新旧端口必须不同" >&2
  exit 2
fi

backup="$(mktemp "${NGINX_CONF}.rolling.XXXXXX")"
cp "$NGINX_CONF" "$backup"
candidate_pid=""
switched=false
cleanup() {
  if [[ -n "$candidate_pid" ]] && kill -0 "$candidate_pid" 2>/dev/null; then
    kill "$candidate_pid" 2>/dev/null || true
  fi
  if [[ "$switched" != true ]] && [[ -f "$backup" ]]; then
    cp "$backup" "$NGINX_CONF" 2>/dev/null || true
  fi
  rm -f "$backup"
}
trap cleanup EXIT

EXCELMANUS_API_PORT="$NEW_PORT" \
EXCELMANUS_WORKERS="${EXCELMANUS_WEB_WORKERS:-2}" \
"$PYTHON_BIN" -m excelmanus.api --host 127.0.0.1 --port "$NEW_PORT" \
  --workers "${EXCELMANUS_WEB_WORKERS:-2}" >"${ROOT}/rolling-candidate.log" 2>&1 &
candidate_pid=$!

ready=false
for _ in $(seq 1 "${EXCELMANUS_ROLLING_TIMEOUT:-60}"); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${NEW_PORT}/api/v1/health" >/dev/null; then
    ready=true
    break
  fi
  if ! kill -0 "$candidate_pid" 2>/dev/null; then
    break
  fi
  sleep 1
done
if [[ "$ready" != true ]]; then
  echo "候选实例健康检查失败，保持旧实例" >&2
  exit 1
fi

# Only change the exact backend listener line; nginx -t must pass before reload.
sed "s#127\\.0\\.0\\.1:${OLD_PORT};#127.0.0.1:${NEW_PORT};#" "$backup" > "$NGINX_CONF"
if ! nginx -t -c "$NGINX_CONF"; then
  cp "$backup" "$NGINX_CONF"
  echo "nginx 配置检查失败，保持旧实例" >&2
  exit 1
fi
nginx -s reload -c "$NGINX_CONF"
switched=true

if [[ -n "$OLD_PID" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
  kill -TERM "$OLD_PID" 2>/dev/null || true
fi
echo "rolling upgrade complete: ${OLD_PORT} -> ${NEW_PORT}"
trap - EXIT
rm -f "$backup"
