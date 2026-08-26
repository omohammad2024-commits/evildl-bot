#!/usr/bin/env bash
# Supervisor for the Downloader bot.
#
# Runs the bot and the PO-token provider fully detached from whatever shell
# started them, restarts either one if it dies, and keeps a rotating log.
# Deliberately does not depend on systemd, docker, or cron.
#
#   ./run.sh start     start both services (detached)
#   ./run.sh stop      stop both
#   ./run.sh restart   stop + start
#   ./run.sh status    show state, pids, uptime, last log lines
#   ./run.sh logs      follow the bot log
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load runtime paths from .env so an unattended start (cron, @reboot, a bare
# ./run.sh start) picks up this host's real locations. Without this the
# defaults below win and the bot comes up with no PO-token provider, which
# makes every YouTube link fail on bot-detection.
# Only path/port vars are exported here; the bot reads its own secrets from
# .env via python-dotenv.
if [ -f "$APP_DIR/.env" ]; then
  while IFS='=' read -r _k _v; do
    case "$_k" in
      VENV|NODE|POT_DIR|POT_PORT)
        [ -n "${_v}" ] && export "$_k=$_v"
        ;;
    esac
  done < <(grep -E '^(VENV|NODE|POT_DIR|POT_PORT)=' "$APP_DIR/.env" 2>/dev/null || true)
fi

VENV="${VENV:-/opt/botvenv}"
PY="$VENV/bin/python"
NODE="${NODE:-/opt/node22/bin/node}"
POT_DIR="${POT_DIR:-/opt/bgutil-pot/server}"
POT_PORT="${POT_PORT:-4416}"

RUN_DIR="$APP_DIR/run"
LOG_DIR="$APP_DIR/logs"
BOT_PID="$RUN_DIR/bot.pid"
POT_PID="$RUN_DIR/pot.pid"
SUP_PID="$RUN_DIR/supervisor.pid"
BOT_LOG="$LOG_DIR/bot.out"
POT_LOG="$LOG_DIR/pot.out"
SUP_LOG="$LOG_DIR/supervisor.out"

mkdir -p "$RUN_DIR" "$LOG_DIR"

alive() { [ -n "${1:-}" ] && [ -d "/proc/$1" ]; }
pid_of() { [ -f "$1" ] && cat "$1" 2>/dev/null || echo ""; }

rotate() {  # keep logs from growing without bound
  local f="$1" max=$((20 * 1024 * 1024))
  [ -f "$f" ] || return 0
  local size; size=$(stat -c%s "$f" 2>/dev/null || echo 0)
  if [ "$size" -gt "$max" ]; then
    mv -f "$f" "$f.1"
    : > "$f"
  fi
}

start_pot() {
  [ -f "$POT_DIR/build/main.js" ] || { echo "  ! POT provider not built at $POT_DIR"; return 1; }
  rotate "$POT_LOG"
  cd "$POT_DIR" || return 1
  setsid "$NODE" build/main.js --port "$POT_PORT" >> "$POT_LOG" 2>&1 &
  echo $! > "$POT_PID"
  cd "$APP_DIR" || return 1
}

start_bot() {
  rotate "$BOT_LOG"
  cd "$APP_DIR" || return 1
  # PATH carries node so yt-dlp's JS challenge solver can find it.
  PATH="$(dirname "$NODE"):$PATH" \
  PYTHONUNBUFFERED=1 \
  setsid "$PY" -m bot.main >> "$BOT_LOG" 2>&1 &
  echo $! > "$BOT_PID"
}

pot_healthy() {
  curl -fsS --max-time 4 "http://127.0.0.1:$POT_PORT/ping" >/dev/null 2>&1
}

supervise() {
  echo "$$" > "$SUP_PID"
  trap 'stop_children; exit 0' TERM INT
  local backoff=2
  while true; do
    local pot bot
    pot=$(pid_of "$POT_PID"); bot=$(pid_of "$BOT_PID")

    if ! alive "$pot" || ! pot_healthy; then
      alive "$pot" && kill "$pot" 2>/dev/null
      echo "$(date '+%F %T') restarting POT provider" >> "$SUP_LOG"
      start_pot
      sleep 3
    fi

    if ! alive "$bot"; then
      echo "$(date '+%F %T') restarting bot (backoff ${backoff}s)" >> "$SUP_LOG"
      sleep "$backoff"
      start_bot
      # Escalating backoff prevents a crash-loop from hammering Telegram.
      backoff=$(( backoff * 2 )); [ "$backoff" -gt 60 ] && backoff=60
    else
      backoff=2
    fi

    sleep 15
  done
}

stop_children() {
  for f in "$BOT_PID" "$POT_PID"; do
    local p; p=$(pid_of "$f")
    if alive "$p"; then
      kill "$p" 2>/dev/null
      for _ in 1 2 3 4 5 6 7 8 9 10; do alive "$p" || break; sleep 0.5; done
      alive "$p" && kill -9 "$p" 2>/dev/null
    fi
    rm -f "$f"
  done
}

cmd_start() {
  local sup; sup=$(pid_of "$SUP_PID")
  if alive "$sup"; then
    echo "already running (supervisor pid $sup)"
    return 0
  fi
  [ -f "$APP_DIR/.env" ] || { echo "! .env is missing — copy .env.example and fill BOT_TOKEN"; exit 1; }
  rotate "$SUP_LOG"
  echo "starting supervisor..."
  setsid "$0" __supervise >> "$SUP_LOG" 2>&1 &
  sleep 6
  cmd_status
}

cmd_stop() {
  local sup; sup=$(pid_of "$SUP_PID")
  if alive "$sup"; then
    kill "$sup" 2>/dev/null
    for _ in $(seq 1 20); do alive "$sup" || break; sleep 0.5; done
    alive "$sup" && kill -9 "$sup" 2>/dev/null
  fi
  rm -f "$SUP_PID"
  stop_children
  echo "stopped"
}

cmd_status() {
  local sup bot pot
  sup=$(pid_of "$SUP_PID"); bot=$(pid_of "$BOT_PID"); pot=$(pid_of "$POT_PID")
  printf "supervisor : %s\n" "$(alive "$sup" && echo "running (pid $sup)" || echo "stopped")"
  printf "bot        : %s\n" "$(alive "$bot" && echo "running (pid $bot, up $(ps -o etime= -p "$bot" 2>/dev/null | tr -d ' '))" || echo "stopped")"
  printf "pot server : %s" "$(alive "$pot" && echo "running (pid $pot)" || echo "stopped")"
  pot_healthy && printf " — healthy\n" || printf " — NOT responding\n"
  if [ -f "$BOT_LOG" ]; then
    echo "--- last log lines ---"
    tail -n 12 "$BOT_LOG"
  fi
}

case "${1:-status}" in
  start)       cmd_start ;;
  stop)        cmd_stop ;;
  restart)     cmd_stop; sleep 2; cmd_start ;;
  status)      cmd_status ;;
  logs)        tail -f "$BOT_LOG" ;;
  __supervise) supervise ;;
  *)           echo "usage: $0 {start|stop|restart|status|logs}"; exit 1 ;;
esac
