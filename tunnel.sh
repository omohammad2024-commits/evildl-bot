#!/usr/bin/env bash
# Supervisor for the Cloudflare quick-tunnel that exposes the web dashboard.
#
# A trycloudflare quick tunnel needs no account and no domain, but its hostname
# changes every time cloudflared restarts. So we:
#   1. run cloudflared pointed at the local dashboard,
#   2. scrape the assigned https://…trycloudflare.com URL from its output,
#   3. write it to run/tunnel_url.txt so the dashboard (and you) can read it,
#   4. restart it if it dies, refreshing the URL file each time.
#
#   ./tunnel.sh start | stop | restart | status | url
set -uo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CF="${CF:-/opt/cloudflared/cloudflared}"

RUN_DIR="$APP_DIR/run"
LOG_DIR="$APP_DIR/logs"
CF_PID="$RUN_DIR/cloudflared.pid"
SUP_PID="$RUN_DIR/tunnelsup.pid"
URL_FILE="$RUN_DIR/tunnel_url.txt"
CF_LOG="$LOG_DIR/cloudflared.out"
SUP_LOG="$LOG_DIR/tunnelsup.out"

[ -f "$APP_DIR/.env" ] && set -a && . "$APP_DIR/.env" && set +a
WEB_PORT="${WEB_PORT:-8080}"

mkdir -p "$RUN_DIR" "$LOG_DIR"

alive() { [ -n "${1:-}" ] && [ -d "/proc/$1" ]; }
pid_of() { [ -f "$1" ] && cat "$1" 2>/dev/null || echo ""; }

start_cf() {
  : > "$CF_LOG"
  setsid "$CF" tunnel --no-autoupdate --url "http://127.0.0.1:${WEB_PORT}" \
    >> "$CF_LOG" 2>&1 9>&- &
  echo $! > "$CF_PID"
}

scrape_url() {
  # cloudflared prints the URL once at startup; grab the first match.
  grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$CF_LOG" 2>/dev/null | head -1
}

wait_for_url() {
  local tries=0 url=""
  while [ "$tries" -lt 30 ]; do
    url="$(scrape_url)"
    [ -n "$url" ] && { echo "$url" > "$URL_FILE"; echo "$url"; return 0; }
    sleep 1; tries=$((tries + 1))
  done
  return 1
}

supervise() {
  echo "$$" > "$SUP_PID"
  trap 'stop_child; exit 0' TERM INT
  local backoff=3
  while true; do
    local cf; cf=$(pid_of "$CF_PID")
    if ! alive "$cf"; then
      echo "$(date '+%F %T') (re)starting cloudflared (backoff ${backoff}s)" >> "$SUP_LOG"
      sleep "$backoff"
      start_cf
      if url=$(wait_for_url); then
        echo "$(date '+%F %T') tunnel up: $url" >> "$SUP_LOG"
        backoff=3
      else
        echo "$(date '+%F %T') tunnel URL not found; will retry" >> "$SUP_LOG"
        backoff=$(( backoff * 2 )); [ "$backoff" -gt 60 ] && backoff=60
      fi
    fi
    sleep 5
  done
}

stop_child() {
  local cf; cf=$(pid_of "$CF_PID")
  alive "$cf" && kill "$cf" 2>/dev/null
  rm -f "$CF_PID"
}

case "${1:-}" in
  start)
    if alive "$(pid_of "$SUP_PID")"; then echo "already running (supervisor $(pid_of "$SUP_PID"))"; exit 0; fi
    [ -x "$CF" ] || { echo "! cloudflared not found at $CF"; exit 1; }
    echo "starting tunnel to local dashboard :$WEB_PORT ..."
    setsid bash "$APP_DIR/tunnel.sh" __supervise >> "$SUP_LOG" 2>&1 9>&- &
    # Give the supervisor time to bring cloudflared up and scrape the URL.
    for _ in $(seq 1 20); do
      [ -s "$URL_FILE" ] && break; sleep 1
    done
    if [ -s "$URL_FILE" ]; then echo "  tunnel URL: $(cat "$URL_FILE")"; else echo "  ! URL not ready yet — check $CF_LOG"; fi
    ;;
  __supervise) supervise ;;
  stop)
    sup=$(pid_of "$SUP_PID"); alive "$sup" && kill "$sup" 2>/dev/null
    stop_child
    rm -f "$SUP_PID" "$URL_FILE"
    echo "stopped"
    ;;
  restart) "$0" stop; sleep 1; "$0" start ;;
  status)
    sup=$(pid_of "$SUP_PID"); cf=$(pid_of "$CF_PID")
    alive "$sup" && echo "supervisor : running (pid $sup)" || echo "supervisor : stopped"
    alive "$cf"  && echo "cloudflared: running (pid $cf)"  || echo "cloudflared: stopped"
    [ -s "$URL_FILE" ] && echo "url        : $(cat "$URL_FILE")" || echo "url        : (none)"
    ;;
  url) [ -s "$URL_FILE" ] && cat "$URL_FILE" || { echo "no tunnel URL yet"; exit 1; } ;;
  *) echo "usage: $0 {start|stop|restart|status|url}"; exit 1 ;;
esac
