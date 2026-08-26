#!/usr/bin/env bash
# Watchdog: guarantees the bot is running, no matter what killed it.
#
# Safe to run as often as you like — it is idempotent and exits quickly when
# everything is healthy. Intended to be driven by system cron every minute and
# at @reboot, and by a Hermes cron job as a second, independent trigger.
#
# Responsibilities, in order:
#   1. rebuild missing runtime dependencies (they live on a throwaway overlay)
#   2. make sure the detached supervisor is alive
#   3. make sure the bot process itself is alive and polling
#   4. never leave two supervisors running
set -uo pipefail

APP_DIR="/data/workspace/mydlplus-bot"
LOG="$APP_DIR/logs/watchdog.log"
LOCK="$APP_DIR/run/watchdog.lock"

mkdir -p "$APP_DIR/logs" "$APP_DIR/run"

log() { echo "$(date '+%F %T') | $*" >> "$LOG"; }

# Keep the watchdog log small; it runs every minute forever.
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 2097152 ]; then
  tail -n 500 "$LOG" > "$LOG.tmp" 2>/dev/null && mv -f "$LOG.tmp" "$LOG"
fi

# Only one watchdog at a time. flock releases automatically on exit.
#
# Critical detail: every long-lived process spawned below MUST close fd 9
# (9>&-). A child that inherits the locked descriptor keeps holding the lock
# for its whole lifetime, which would make every future watchdog run exit here
# and silently do nothing.
exec 9>"$LOCK"
flock -n 9 || exit 0

cd "$APP_DIR" || { log "FATAL: $APP_DIR missing"; exit 1; }

# ── 1. dependencies ───────────────────────────────────────────────────
# /opt lives on a throwaway overlay: if the container was recreated these are
# gone and the bot cannot start until they are rebuilt.
if [ ! -x /opt/botvenv/bin/python ] || [ ! -x /opt/node22/bin/node ] \
   || [ ! -f /opt/bgutil-pot/server/build/main.js ]; then
  if [ -f "$APP_DIR/bootstrap.sh" ]; then
    log "dependencies missing — running bootstrap"
    bash "$APP_DIR/bootstrap.sh" >> "$APP_DIR/logs/bootstrap.log" 2>&1
  else
    log "FATAL: dependencies missing and no bootstrap.sh"
    exit 1
  fi
fi

# ── 2. supervisor ─────────────────────────────────────────────────────
sup="$(cat "$APP_DIR/run/supervisor.pid" 2>/dev/null || echo '')"
if [ -n "$sup" ] && [ -d "/proc/$sup" ]; then
  # Supervisor is up. Verify it is actually the supervisor and not a recycled
  # pid belonging to something else.
  if ! tr '\0' ' ' < "/proc/$sup/cmdline" 2>/dev/null | grep -q "__supervise"; then
    log "stale supervisor pid $sup (pid reused) — restarting"
    rm -f "$APP_DIR/run/supervisor.pid"
    setsid ./run.sh __supervise >> "$APP_DIR/logs/supervisor.out" 2>&1 9>&- &
    log "supervisor restarted"
  fi
else
  log "supervisor down — starting"
  setsid ./run.sh __supervise >> "$APP_DIR/logs/supervisor.out" 2>&1 9>&- &
  # The supervisor needs a moment to fork the bot and write its pid file.
  sleep 12
  newsup="$(cat "$APP_DIR/run/supervisor.pid" 2>/dev/null || echo '')"
  if [ -n "$newsup" ] && [ -d "/proc/$newsup" ]; then
    log "supervisor started (pid $newsup)"
  else
    log "ERROR: supervisor failed to start"
  fi
fi

# ── 3. bot liveness ───────────────────────────────────────────────────
# The supervisor handles crashes, but a hung process (alive yet not polling)
# needs catching too. Telegram's own log line is the health signal.
bot="$(cat "$APP_DIR/run/bot.pid" 2>/dev/null || echo '')"
if [ -n "$bot" ] && [ -d "/proc/$bot" ]; then
  : # supervisor owns it from here
else
  log "bot not running (supervisor will respawn within 15s)"
fi

# ── 4. web dashboard ──────────────────────────────────────────────────
# The dashboard runs under its own supervisor (web.sh). Keep it alive the same
# way, independently of the bot so a bot restart never takes the panel down.
websup="$(cat "$APP_DIR/run/websup.pid" 2>/dev/null || echo '')"
if [ -n "$websup" ] && [ -d "/proc/$websup" ]; then
  if ! tr '\0' ' ' < "/proc/$websup/cmdline" 2>/dev/null | grep -q "__supervise"; then
    log "stale web supervisor pid $websup — restarting"
    rm -f "$APP_DIR/run/websup.pid"
    setsid bash "$APP_DIR/web.sh" __supervise >> "$APP_DIR/logs/websup.out" 2>&1 9>&- &
    log "web supervisor restarted"
  fi
else
  log "web dashboard down — starting"
  setsid bash "$APP_DIR/web.sh" __supervise >> "$APP_DIR/logs/websup.out" 2>&1 9>&- &
fi

# ── 5. cloudflare tunnel ──────────────────────────────────────────────
# Exposes the dashboard over a public https URL. Its own supervisor scrapes and
# refreshes run/tunnel_url.txt; keep that supervisor alive here. Only runs when
# cloudflared is actually installed.
if [ -x /opt/cloudflared/cloudflared ]; then
  tunsup="$(cat "$APP_DIR/run/tunnelsup.pid" 2>/dev/null || echo '')"
  if [ -n "$tunsup" ] && [ -d "/proc/$tunsup" ]; then
    if ! tr '\0' ' ' < "/proc/$tunsup/cmdline" 2>/dev/null | grep -q "__supervise"; then
      log "stale tunnel supervisor pid $tunsup — restarting"
      rm -f "$APP_DIR/run/tunnelsup.pid"
      setsid bash "$APP_DIR/tunnel.sh" __supervise >> "$APP_DIR/logs/tunnelsup.out" 2>&1 9>&- &
      log "tunnel supervisor restarted"
    fi
  else
    log "cloudflare tunnel down — starting"
    setsid bash "$APP_DIR/tunnel.sh" __supervise >> "$APP_DIR/logs/tunnelsup.out" 2>&1 9>&- &
  fi
fi

# ── 6. WARP userspace SOCKS proxy ─────────────────────────────────────
# Gives the bot a clean Cloudflare egress IP so YouTube/Instagram download
# without cookies. Its own supervisor (warp.sh) health-checks egress and
# rotates WARP endpoints; keep that supervisor alive here. Only runs when the
# wireproxy binary is present under /data/warp (persistent, survives overlay
# wipes).
WARP_DIR="/data/warp"
if [ -x "$WARP_DIR/wireproxy" ] && [ -f "$WARP_DIR/warp.sh" ]; then
  warpsup="$(cat "$WARP_DIR/run/warpsup.pid" 2>/dev/null || echo '')"
  if [ -n "$warpsup" ] && [ -d "/proc/$warpsup" ]; then
    if ! tr '\0' ' ' < "/proc/$warpsup/cmdline" 2>/dev/null | grep -q "__supervise"; then
      log "stale warp supervisor pid $warpsup — restarting"
      rm -f "$WARP_DIR/run/warpsup.pid"
      setsid bash "$WARP_DIR/warp.sh" __supervise >> "$WARP_DIR/logs/warpsup.out" 2>&1 9>&- &
      log "warp supervisor restarted"
    fi
  else
    log "warp proxy down — starting"
    setsid bash "$WARP_DIR/warp.sh" __supervise >> "$WARP_DIR/logs/warpsup.out" 2>&1 9>&- &
  fi
fi

exit 0
