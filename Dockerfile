# mydlplus / evildl-bot — single-image deploy (Railway, Fly, any Docker host).
#
# Bundles everything the bot needs at runtime:
#   * Python 3.11 + pinned deps
#   * Node 22  — the bgutil PO-token server AND yt-dlp's JS challenge solver
#   * ffmpeg   — merges DASH streams and runs the iOS codec repair
#   * the bgutil PO-token provider, built from source into the image
#
# Secrets are NEVER baked in: pass BOT_TOKEN, ADMIN_IDS, etc. as environment
# variables in the Railway dashboard (see .env.example for the full list).

FROM python:3.11-slim

# ── system packages ───────────────────────────────────────────────────
# ffmpeg for media, curl/xz to fetch Node, git for the POT provider clone.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg curl xz-utils ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# ── Node 22 (bgutil requires >=22; Debian slim ships older) ───────────
ENV NODE_VERSION=22.21.1
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) node_arch="x64" ;; \
        arm64) node_arch="arm64" ;; \
        *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" -o /tmp/node.tar.xz; \
    mkdir -p /opt/node; \
    tar -xJf /tmp/node.tar.xz -C /opt/node --strip-components=1; \
    rm /tmp/node.tar.xz; \
    ln -s /opt/node/bin/node /usr/local/bin/node; \
    ln -s /opt/node/bin/npm  /usr/local/bin/npm; \
    node --version && npm --version

# ── PO-token provider (built into the image) ──────────────────────────
ENV POT_DIR=/opt/bgutil-pot/server
RUN set -eux; \
    git clone --depth 1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil-pot; \
    cd "$POT_DIR"; \
    npm install --no-audit --no-fund; \
    npx --yes tsc; \
    test -f build/main.js

# ── Python deps ───────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── app source ────────────────────────────────────────────────────────
COPY . .

# Runtime paths inside the image (override via env if you move things).
ENV VENV=/usr/local \
    NODE=/usr/local/bin/node \
    POT_PORT=4416 \
    PYTHONUNBUFFERED=1 \
    IOS_COMPAT=1 \
    IOS_COMPAT_THREADS=4

# The bot polls Telegram (long-polling) and serves an optional web dashboard.
EXPOSE 8080

# start.sh brings up the POT server then the bot in the foreground, so the
# container's lifecycle is tied to the bot (Railway restarts on exit).
CMD ["bash", "start.sh"]
