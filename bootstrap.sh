#!/usr/bin/env bash
# Rebuild every runtime dependency the bot needs.
#
# Why this exists: the bot's code and database live on the persistent /data
# volume, but its interpreter, Node, and the PO-token server live under /opt on
# a throwaway overlay filesystem. If the container is recreated, /opt is empty
# and the bot cannot start. This script restores it unattended.
#
#   bash bootstrap.sh
#
# Idempotent: each step is skipped when already satisfied.
set -uo pipefail

# Resolve APP_DIR to wherever this script actually lives, so the portable
# tarball works when extracted to any path (not just /data/workspace).
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${VENV:-/opt/botvenv}"
NODE_DIR="${NODE_DIR:-/opt/node22}"
NODE_VER="${NODE_VER:-v22.21.1}"
POT_DIR="${POT_DIR:-/opt/bgutil-pot}"
WARP_DIR="${WARP_DIR:-/data/warp}"

say() { echo "==> $*"; }

# ── ffmpeg (needed to merge video+audio and to build thumbnails) ───────
if ! command -v ffmpeg >/dev/null 2>&1; then
  say "installing ffmpeg"
  apt-get update -q 2>&1 | tail -1
  apt-get install -y -q ffmpeg 2>&1 | tail -1
fi

# ── Python venv ───────────────────────────────────────────────────────
if [ ! -x "$VENV/bin/python" ]; then
  say "creating venv at $VENV"
  python3 -m venv "$VENV"
fi
say "installing python deps"
"$VENV/bin/pip" install -q -U pip 2>&1 | tail -1
"$VENV/bin/pip" install -q -U \
  "python-telegram-bot[job-queue]==20.7" \
  "yt-dlp[default,curl-cffi]" \
  bgutil-ytdlp-pot-provider \
  aiosqlite httpx python-dotenv telethon Pillow beautifulsoup4 lxml aiofiles \
  fastapi "uvicorn[standard]" jinja2 python-multipart itsdangerous 2>&1 | tail -2

# ── Node 22 (required by yt-dlp to solve YouTube JS challenges) ────────
if [ ! -x "$NODE_DIR/bin/node" ]; then
  say "installing Node $NODE_VER"
  mkdir -p "$NODE_DIR"
  curl -sSL -m 600 -o /tmp/node.tar.xz \
    "https://nodejs.org/dist/$NODE_VER/node-$NODE_VER-linux-x64.tar.xz"
  tar -xJf /tmp/node.tar.xz -C "$NODE_DIR" --strip-components=1
  rm -f /tmp/node.tar.xz
fi

# ── cloudflared (public https tunnel for the web dashboard) ───────────
if [ ! -x /opt/cloudflared/cloudflared ]; then
  say "installing cloudflared"
  mkdir -p /opt/cloudflared
  curl -sSL -m 120 -o /opt/cloudflared/cloudflared \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" \
    && chmod +x /opt/cloudflared/cloudflared
fi

# ── WARP userspace proxy (clean egress IP for YouTube/Instagram) ──────
# Binaries + WARP account live under /data/warp (persistent), so this only
# fires if that directory was wiped. wgcf registers a free WARP account and
# wireproxy exposes it as a local SOCKS5 the bot points PROXY_YOUTUBE /
# PROXY_INSTAGRAM at. Needs no root and no /dev/net/tun.
if [ ! -x "$WARP_DIR/wireproxy" ] || [ ! -x "$WARP_DIR/wgcf" ]; then
  say "installing WARP userspace proxy (wgcf + wireproxy)"
  mkdir -p "$WARP_DIR"
  [ -x "$WARP_DIR/wgcf" ] || { curl -sSL -m 120 -o "$WARP_DIR/wgcf" \
    "https://github.com/ViRb3/wgcf/releases/download/v2.2.32/wgcf_2.2.32_linux_amd64" \
    && chmod +x "$WARP_DIR/wgcf"; }
  if [ ! -x "$WARP_DIR/wireproxy" ]; then
    curl -sSL -m 120 -o "$WARP_DIR/wp.tgz" \
      "https://github.com/windtf/wireproxy/releases/download/v1.1.3/wireproxy_linux_amd64.tar.gz" \
      && tar xzf "$WARP_DIR/wp.tgz" -C "$WARP_DIR" && chmod +x "$WARP_DIR/wireproxy" \
      && rm -f "$WARP_DIR/wp.tgz"
  fi
fi

# ── bgutil PO-token provider (required for YouTube) ───────────────────
if [ ! -f "$POT_DIR/server/build/main.js" ]; then
  say "building bgutil POT provider"
  rm -rf "$POT_DIR"
  git clone -q --depth 1 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git "$POT_DIR"
  cd "$POT_DIR/server" || exit 1
  PATH="$NODE_DIR/bin:$PATH" npm install -q --no-audit --no-fund 2>&1 | tail -2
  PATH="$NODE_DIR/bin:$PATH" npx --yes tsc 2>&1 | tail -2
  cd "$APP_DIR" || exit 1
fi

# ── verify ────────────────────────────────────────────────────────────
say "verification"
printf "  python  : %s\n" "$("$VENV/bin/python" --version 2>&1)"
printf "  yt-dlp  : %s\n" "$("$VENV/bin/yt-dlp" --version 2>&1)"
printf "  node    : %s\n" "$("$NODE_DIR/bin/node" --version 2>&1)"
printf "  ffmpeg  : %s\n" "$(command -v ffmpeg || echo MISSING)"
printf "  pot srv : %s\n" "$([ -f "$POT_DIR/server/build/main.js" ] && echo built || echo MISSING)"

say "done — start with: cd $APP_DIR && ./run.sh start"
