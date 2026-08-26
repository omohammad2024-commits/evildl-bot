#!/usr/bin/env bash
# Supervisor for the Downloader web dashboard (separate from the bot).
#
# Runs the FastAPI dashboard under uvicorn, fully detached, and restarts it if
# it dies. Independent of run.sh so restarting the bot from the dashboard does
# not take the dashboard down with it.
#
#   ./web.sh start | stop | restart | status | logs
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${VENV:-/opt/botvenv}"
PY="$VENV/bin/python"
NODE="${NODE:-/opt/node22/bin/node}"

RUN_DIR="$APP_DIR/run"
LOG_DIR="$APP_DIR/logs"
WEB_PID="$RUN_DIR/web.pid"
SUP_PID="$RUN_DIR/websup.pid"
WEB_LOG="$LOG_DIR/web.out"
SUP_LOG="$LOG_DIR/websup.out"

# Load .env so WEB_PORT / WEB_HOST / WEB_PASSWORD are available here too.
[ -f "$APP_DIR/.env" ] && set -a && . "$APP_DIR/.env" && set +a
WEB_HOST="${WEB_HOST:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-8080}"

mkdir -p "$RUN_DIR" "$LOG_DIR"

alive() { [ -n "${1:-}" ] && [ -d "/proc/$1" ]; }
pid_of() { [ -f "$1" ] && cat "$1" 2>/dev/null || echo ""; }

rotate() {
  local f="$1" max=$((20 * 1024 * 1024))
  [ -f "$f" ] || return 0
  local size; size=$(stat -c%s "$f" 2>/dev/null || echo 0)
  [ "$size" -gt "$max" ] && { mv -f "$f" "$f.1"; : > "$f"; }
}

start_web() {
  rotate "$WEB_LOG"
  cd "$APP_DIR" || return 1
  PATH="$(dirname "$NODE"):$PATH" \
  PYTHONUNBUFFERED=1 \
  setsid "$VENV/bin/uvicorn" web.app:app --host "$WEB_HOST" --port "$WEB_PORT" \
    --no-access-log >> "$WEB_LOG" 2>&1 &
  echo $! > "$WEB_PID"
}

web_healthy() {
  curl -fsS --max-time 4 "http://127.0.0.1:$WEB_PORT/health" >/dev/null 2>&1
}

supervise() {
  echo "$$" > "$SUP_PID"
  trap 'stop_child; exit 0' TERM INT
  local backoff=2
  while true; do
    local web; web=$(pid_of "$WEB_PID")
    if ! alive "$web"; then
      echo "$(date '+%F %T') restarting web (backoff ${backoff}s)" >> "$SUP_LOG"
      sleep "$backoff"
      start_web
      backoff=$(( backoff * 2 )); [ "$backoff" -gt 60 ] && backoff=60
    else
      backoff=2
    fi
    sleep 5
  done
}

stop_child() {
  local web; web=$(pid_of "$WEB_PID")
  alive "$web" && kill "$web" 2>/dev/null
  rm -f "$WEB_PID"
}

case "${1:-}" in
  start)
    if alive "$(pid_of "$SUP_PID")"; then echo "already running (supervisor $(pid_of "$SUP_PID"))"; exit 0; fi
    echo "starting dashboard on ${WEB_HOST}:${WEB_PORT} ..."
    setsid bash "$APP_DIR/web.sh" __supervise >> "$SUP_LOG" 2>&1 &
    sleep 3
    if web_healthy; then echo "  dashboard online: http://${WEB_HOST}:${WEB_PORT}"; else echo "  ! not healthy yet — check $WEB_LOG"; fi
    ;;
  __supervise) supervise ;;
  stop)
    sup=$(pid_of "$SUP_PID"); alive "$sup" && kill "$sup" 2>/dev/null
    stop_child
    rm -f "$SUP_PID"
    echo "stopped"
    ;;
  restart) "$0" stop; sleep 1; "$0" start ;;
  status)
    sup=$(pid_of "$SUP_PID"); web=$(pid_of "$WEB_PID")
    alive "$sup" && echo "supervisor : running (pid $sup)" || echo "supervisor : stopped"
    if alive "$web"; then
      web_healthy && echo "dashboard  : running (pid $web) — healthy" || echo "dashboard  : running (pid $web) — NOT healthy"
    else echo "dashboard  : stopped"; fi
    ;;
  logs) tail -f "$WEB_LOG" ;;
  *) echo "usage: $0 {start|stop|restart|status|logs}"; exit 1 ;;
esac
