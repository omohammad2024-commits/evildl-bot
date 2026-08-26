#!/usr/bin/env bash
# Container entrypoint for Railway / Docker.
#
# Unlike run.sh (the bare-metal supervisor that daemonises with setsid), a
# container wants ONE foreground process whose exit ends the container, so the
# platform can restart it. So: launch the PO-token server in the background,
# wait for it to answer, then exec the bot in the foreground as PID 1's child.
set -euo pipefail

POT_DIR="${POT_DIR:-/opt/bgutil-pot/server}"
POT_PORT="${POT_PORT:-4416}"
NODE="${NODE:-/usr/local/bin/node}"

# ── PO-token provider ─────────────────────────────────────────────────
if [ -f "$POT_DIR/build/main.js" ]; then
  echo "[start] launching POT provider on 127.0.0.1:${POT_PORT}"
  ( cd "$POT_DIR" && "$NODE" build/main.js --port "$POT_PORT" ) &
  POT_PID=$!

  # Wait up to ~20s for it to answer, but never block the bot forever: YouTube
  # simply degrades without it, everything else works regardless.
  for _ in $(seq 1 20); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${POT_PORT}/ping" >/dev/null 2>&1; then
      echo "[start] POT provider healthy"
      break
    fi
    sleep 1
  done
else
  echo "[start] WARNING: POT provider not built at $POT_DIR — YouTube may hit bot-detection"
fi

# ── the bot (foreground; its exit stops the container) ────────────────
echo "[start] launching bot"
export PATH="$(dirname "$NODE"):$PATH"
exec python -m bot.main
