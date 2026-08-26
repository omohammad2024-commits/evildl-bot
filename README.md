# Downloader Bot (evildl-bot / mydlplus)

ربات دانلودر حرفه‌ای تلگرام برای ۱۳ پلتفرم، دو زبانه (فارسی/انگلیسی).

A bilingual (FA/EN) Telegram downloader bot covering 13 platforms, with an
admin panel, an optional web dashboard, MTProto large-file uploads, and
automatic iOS-playback repair for downloaded videos.

## Platforms

📸 Instagram · 🎬 YouTube · 📱 TikTok · 🎵 Spotify · 📌 Pinterest ·
🎧 SoundCloud · 🐦 Twitter/X · 🎥 Aparat · 🟠 Reddit · 🟣 Twitch ·
🔵 Facebook · ⚫ Rumble · 🔗 direct links

## Features

- 🌐 Bilingual UI (Persian / English)
- 🎨 Colored inline keyboards, quality picker
- 📊 Admin panel + FastAPI web dashboard
- 🔒 MTProto uploads up to 2 GB
- 📱 **iOS-playable output** — videos are normalised to H.264/AAC + faststart so
  they open on iPhone, not just Android (see below)
- 🤖 YouTube PO-token provider bundled (bypasses bot-detection)

## iOS playback repair

YouTube (and Instagram's adaptive tier) often serve VP9 video or Opus audio in
an `.mp4` container. Those play on Android and Telegram Desktop but **fail to
open on iPhone**. On every video the bot:

1. prefers H.264 + AAC at download time (yt-dlp format selection);
2. probes the real codecs with ffprobe;
3. if needed, remuxes (audio-only or faststart) or transcodes to H.264/AAC,
   8-bit 4:2:0, with the `moov` atom moved to the front.

See `bot/utils/media_handler.py:ensure_ios_compatible`. Tunable via the
`IOS_COMPAT*` env vars in `.env.example`.

---

## Deploy on Railway (recommended)

The repo ships a `Dockerfile` + `railway.toml`, so Railway builds an image that
already contains Python 3.11, **Node 22**, **ffmpeg**, and the **PO-token
server** — no manual setup.

1. **New Project → Deploy from GitHub repo** → pick this repo.
2. Railway detects the Dockerfile automatically (`railway.toml` forces it).
3. Add environment variables (Settings → Variables). Minimum:
   - `BOT_TOKEN` — from @BotFather
   - `BOT_USERNAME` — without the `@`
   - `ADMIN_IDS` — your numeric Telegram id (comma-separated for several)
   - `WEB_PASSWORD` — if you use the dashboard
   The full list with defaults is in `.env.example`. **Do not set** `VENV` /
   `NODE` / `POT_DIR` — the image sets those itself.
4. Deploy. The bot long-polls Telegram, so no public domain is required. Expose
   port `8080` only if you want the web dashboard.

Moving to another Railway account later is just: fork/import the repo there,
re-enter the same variables, deploy. Nothing is host-specific in the image.

## Run with Docker directly

```bash
docker build -t evildl-bot .
docker run -d --name evildl-bot \
  -e BOT_TOKEN=... -e BOT_USERNAME=... -e ADMIN_IDS=... \
  -v "$PWD/data:/app/data" \
  evildl-bot
```

Mount `/app/data` as a volume to persist the SQLite DB, cookies, and MTProto
session across restarts.

## Run on bare metal (VPS)

For a plain server without Docker, `run.sh` is a dependency-free supervisor
(no systemd/docker/cron) that runs the bot + POT server detached, restarts
either if it dies, and rotates logs.

```bash
python3 -m venv /opt/botvenv
/opt/botvenv/bin/pip install -r requirements.txt
cp .env.example .env      # then fill in BOT_TOKEN etc.
# build the POT server once (needs Node >=22) — see Dockerfile for the steps
./run.sh start            # start | stop | restart | status | logs
```

`run.sh` reads `VENV`, `NODE`, `POT_DIR`, `POT_PORT` from `.env`, so unattended
starts pick up the right paths.

## Configuration

Copy `.env.example` to `.env` and fill it in. `.env` is gitignored and must
never be committed — it holds the live bot token.

## License

MIT
