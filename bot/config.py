"""Central configuration. Everything is read from the environment."""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Core ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

BOT_NAME = os.getenv("BOT_NAME", "Downloader")

# ── Paths ─────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "bot.db")
TEMP_DIR = os.getenv("TEMP_DIR", "/tmp/dlbot")
LOG_PATH = os.path.join(BASE_DIR, "bot.log")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# ── Upload limits ─────────────────────────────────────────────────────
# Bot API caps uploads at 50MB. With MTProto (Telethon + bot token) we can
# push up to 2000MB, so the effective cap depends on what is configured.
BOT_API_LIMIT = 50 * 1024 * 1024
MTPROTO_LIMIT = 2000 * 1024 * 1024

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")

# Fallback to Telegram Desktop's own public api_id/api_hash. These ship in
# Telegram's open-source clients (github.com/telegramdesktop/tdesktop), are not
# secret, and let a bot log in over MTProto for 2GB uploads without anyone
# having to complete the buggy my.telegram.org form. Verified working: a 60MB
# file uploaded successfully with this pair. Override via .env if you get your
# own credentials.
if not (TELEGRAM_API_ID and TELEGRAM_API_HASH):
    TELEGRAM_API_ID = 2040
    TELEGRAM_API_HASH = "b18441a1ff607e10a989891a5462e627"

USE_MTPROTO = bool(TELEGRAM_API_ID and TELEGRAM_API_HASH)

# Local Bot API server (optional, raises the cap to 2000MB without MTProto)
LOCAL_BOT_API = os.getenv("LOCAL_BOT_API", "").rstrip("/")

# Hard ceiling on what we are willing to pull to disk at all.
MAX_DOWNLOAD = int(os.getenv("MAX_DOWNLOAD_MB", "2000")) * 1024 * 1024

# When no MTProto and no local Bot API server are configured, files over the
# 50MB Bot API cap are uploaded to a public file host and sent as a direct
# link instead of being refused or split. This is the credential-free path to
# an effectively unlimited upload size. Set HOST_LARGE_FILES=0 to disable and
# fall back to splitting into <50MB parts.
HOST_LARGE_FILES = os.getenv("HOST_LARGE_FILES", "1") == "1"

# Effective in-Telegram upload cap (native media). Beyond this we host + link.
_NATIVE_CAP = MTPROTO_LIMIT if (USE_MTPROTO or LOCAL_BOT_API) else BOT_API_LIMIT
# With file hosting on, there is no hard ceiling the user ever hits.
MAX_UPLOAD = MAX_DOWNLOAD if HOST_LARGE_FILES else _NATIVE_CAP

# Split oversized files into <=SPLIT_PART_SIZE chunks when no bypass exists.
SPLIT_PART_SIZE = 49 * 1024 * 1024
ALLOW_SPLIT = os.getenv("ALLOW_SPLIT", "1") == "1"

# ── Limits: invisible to users ────────────────────────────────────────
# Nothing here ever produces a "you hit a limit" message. Users are queued
# silently; heavy users are slowed down, never refused.
MAX_DURATION = 0            # 0 = unlimited video length
HOURLY_QUOTA = 0            # 0 = no hard per-user cap

# Global worker pool. Extra jobs wait in the queue instead of being rejected.
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT", "4"))
# Per-user concurrency so one person cannot occupy every worker.
PER_USER_CONCURRENT = int(os.getenv("PER_USER_CONCURRENT", "2"))
# Soft fair-use: past this many downloads per hour a user's jobs get a small
# artificial delay and drop to the back of the queue. They are never blocked.
SOFT_HOURLY = int(os.getenv("SOFT_HOURLY", "40"))
SOFT_DELAY = float(os.getenv("SOFT_DELAY", "3.0"))

MAX_LINKS_PER_MSG = int(os.getenv("MAX_LINKS_PER_MSG", "20"))
MAX_PLAYLIST_ITEMS = int(os.getenv("MAX_PLAYLIST_ITEMS", "50"))

# ── Disk guard ────────────────────────────────────────────────────────
# Public bots die when the disk fills. Refuse to start new downloads when the
# temp filesystem drops below this much free space, and always clean up.
# NOTE: this is measured against TEMP_DIR's filesystem, not /data. Keep the
# default modest — a 1500MB floor on a small volume would refuse every
# download. Override MIN_FREE_MB for large disks if you want a bigger safety
# margin.
MIN_FREE_DISK = int(os.getenv("MIN_FREE_MB", "400")) * 1024 * 1024
TEMP_MAX_AGE = int(os.getenv("TEMP_MAX_AGE", "1800"))  # seconds

# ── Database growth control ───────────────────────────────────────────
# The download log is a rolling window, not an archive: lifetime totals live in
# cheap counters so old rows can be dropped without losing statistics.
DB_RETENTION_DAYS = int(os.getenv("DB_RETENTION_DAYS", "14"))
# Hard cap on cached file_id rows. Least-recently-used entries are evicted.
CACHE_MAX_ROWS = int(os.getenv("CACHE_MAX_ROWS", "20000"))
# Cached file_ids do not expire on Telegram's side, but stale rows waste space.
CACHE_MAX_AGE_DAYS = int(os.getenv("CACHE_MAX_AGE_DAYS", "90"))
# Only a prefix of each URL is stored, for debugging; the rest is hashed.
URL_STORE_CHARS = int(os.getenv("URL_STORE_CHARS", "120"))
# Housekeeping cadence (seconds).
MAINTENANCE_INTERVAL = int(os.getenv("MAINTENANCE_INTERVAL", "3600"))
# Emergency threshold: below this much free space, prune aggressively.
DISK_PANIC = int(os.getenv("DISK_PANIC_MB", "150")) * 1024 * 1024

# ── Channel gate (optional, off by default) ───────────────────────────
FORCE_CHANNEL = os.getenv("FORCE_CHANNEL", "")

# ── Anti-block engine ─────────────────────────────────────────────────
# PO Token provider (bgutil) — required for reliable YouTube extraction.
POT_SERVER = os.getenv("POT_SERVER", "http://127.0.0.1:4416")
# JS runtime used to solve YouTube's JS challenges. Node >= 22 required.
JS_RUNTIME = os.getenv("JS_RUNTIME", "node")
NODE_PATH = os.getenv("NODE_PATH", "/opt/node22/bin/node")

# Ordered YouTube client fallback chain. Each entry is tried in order.
# `default` (yt-dlp's own client rotation) and `web` expose the full resolution
# ladder up to 4K; `mweb`/`tv`/`android_vr` frequently cap at 360p (format 18),
# which silently downgrades "best". Lead with the full-ladder clients so quality
# is right, keep the others as reachability fallbacks for when the leads block.
YT_CLIENT_CHAIN = [c.strip() for c in os.getenv(
    "YT_CLIENT_CHAIN", "default,web,mweb,tv,web_safari,android_vr,ios"
).split(",") if c.strip()]

# "best" resolution ceiling. 4K/8K sources otherwise produce >1GB files that are
# slow to fetch/upload and stream poorly on Telegram.
BEST_MAX_HEIGHT = int(os.getenv("BEST_MAX_HEIGHT", "1080"))

# ── iOS playback compatibility ────────────────────────────────────────
# iPhone/QuickTime plays H.264 video with AAC audio. YouTube serves VP9/AV1
# above 1080p and Opus audio on most modern streams; such a file is named .mp4,
# plays on Android and Telegram Desktop, and silently refuses to open on iOS.
# Format selection asks for H.264+AAC first; anything that still slips through is
# repaired by media_handler.ensure_ios_compatible.
IOS_COMPAT = os.getenv("IOS_COMPAT", "1") != "0"

# Codecs an iPhone can actually decode. hevc is included because iOS plays it
# natively (Apple ships the hardware decoder) — no need to re-encode those.
IOS_VIDEO_CODECS = {"h264", "avc1", "hevc", "h265"}
IOS_AUDIO_CODECS = {"aac", "mp4a", "mp3", "alac"}

# Pixel formats iOS decodes reliably. 8-bit 4:2:0 only — a 10-bit or 4:2:2/4:4:4
# stream carries the right codec name but still refuses to open on many iPhones,
# so it is re-encoded down to yuv420p.
IOS_PIX_FMTS = {"yuv420p", "yuvj420p", "nv12"}

# Bounds on the expensive full-video transcode. This host has 48 CPU cores but
# no usable GPU encoder (no nvidia-smi, no /dev/dri, h264_nvenc fails), so the
# fallback is libx264 on CPU. Past these limits the original file is sent
# unmodified rather than making the user wait many minutes.
IOS_COMPAT_MAX_PIXELS = int(os.getenv("IOS_COMPAT_MAX_PIXELS", str(1920 * 1080)))
IOS_COMPAT_MAX_DURATION = int(os.getenv("IOS_COMPAT_MAX_DURATION", "1800"))
IOS_COMPAT_PRESET = os.getenv("IOS_COMPAT_PRESET", "veryfast")
IOS_COMPAT_CRF = int(os.getenv("IOS_COMPAT_CRF", "23"))

# libx264 thread cap. MUST be bounded: `-threads 0` lets x264 spawn one thread
# per core (60 on this 48-core box) plus lookahead threads, each holding frame
# buffers for a 1080p frame. Inside the ~950MB cgroup that overshoots RAM and
# the kernel SIGKILLs ffmpeg (exit -9) before a single frame is written — which
# is exactly why "iOS repair failed (vp9->h264)" kept firing. 4 threads encode a
# 20s reel in a few seconds and stay well under the memory cap.
IOS_COMPAT_THREADS = int(os.getenv("IOS_COMPAT_THREADS", "4"))

# curl_cffi impersonation targets, rotated on retry.
IMPERSONATE_CHAIN = [c.strip() for c in os.getenv(
    "IMPERSONATE_CHAIN", "chrome-136:macos-15,safari-18.4:ios-18.4,chrome-131:android-14"
).split(",") if c.strip()]

# Optional cookies file (Netscape format) for gated content.
# Per-platform cookie jars live here (see bot/utils/cookies.py). A single
# global COOKIES_FILE is still honoured as a fallback.
COOKIES_DIR = os.getenv("COOKIES_DIR", os.path.join(BASE_DIR, "data", "cookies"))
COOKIES_FILE = os.getenv("COOKIES_FILE", "")
if COOKIES_FILE and not os.path.exists(COOKIES_FILE):
    COOKIES_FILE = ""

# Optional outbound proxy, e.g. socks5://user:pass@host:port or
# http://user:pass@host:port. A residential/mobile proxy is the reliable way to
# unblock YouTube + Instagram from a flagged datacenter IP.
#
# PROXY applies to every outbound request (yt-dlp AND the httpx services).
# To route only specific platforms through a (usually paid, metered) proxy and
# keep everything else direct, set PROXY_<PLATFORM>, e.g.:
#   PROXY_YOUTUBE=http://user:pass@host:port
#   PROXY_INSTAGRAM=http://user:pass@host:port
# A per-platform value overrides PROXY for that platform.
PROXY = os.getenv("PROXY", "")
PROXY_YOUTUBE = os.getenv("PROXY_YOUTUBE", "")
PROXY_INSTAGRAM = os.getenv("PROXY_INSTAGRAM", "")
PROXY_TIKTOK = os.getenv("PROXY_TIKTOK", "")
PROXY_TWITTER = os.getenv("PROXY_TWITTER", "")

# Retry / backoff behaviour
RETRY_ATTEMPTS = int(os.getenv("RETRY_ATTEMPTS", "4"))
RETRY_BASE_DELAY = float(os.getenv("RETRY_BASE_DELAY", "1.5"))
SLEEP_REQUESTS = float(os.getenv("SLEEP_REQUESTS", "0.4"))
SOCKET_TIMEOUT = int(os.getenv("SOCKET_TIMEOUT", "30"))
CONCURRENT_FRAGMENTS = int(os.getenv("CONCURRENT_FRAGMENTS", "8"))

# Cool-down: after N consecutive failures a platform is parked briefly so we
# stop hammering an endpoint that is actively rejecting us.
CIRCUIT_THRESHOLD = int(os.getenv("CIRCUIT_THRESHOLD", "5"))
CIRCUIT_COOLDOWN = int(os.getenv("CIRCUIT_COOLDOWN", "120"))

# ── Feature switches ──────────────────────────────────────────────────
CACHE_FILE_IDS = os.getenv("CACHE_FILE_IDS", "1") == "1"
SEND_THUMBNAILS = os.getenv("SEND_THUMBNAILS", "1") == "1"
DEFAULT_LANG = os.getenv("DEFAULT_LANG", "fa")

# Chat used to turn a freshly downloaded file into a reusable Bot-API file_id
# (upload once here, then reference the file_id everywhere, incl. inline edits).
# Defaults to the first admin's private chat.
STORAGE_CHAT = int(os.getenv("STORAGE_CHAT", str(ADMIN_IDS[0] if ADMIN_IDS else 0)) or 0)

# Inline mode: when True, a chosen inline result is replaced in-place with the
# real media (needs BotFather /setinlinefeedback = Enabled). When False, inline
# only offers a deep-link "open the bot" button.
INLINE_LIVE_MEDIA = os.getenv("INLINE_LIVE_MEDIA", "1") == "1"
# Only auto-fetch inline media up to this size, to stay fast and avoid abuse.
INLINE_MAX_MB = int(os.getenv("INLINE_MAX_MB", "50"))

# Group mode: when True, the bot downloads any supported link posted in a group
# it is a member of. When False (default) it stays quiet in groups unless the
# message @-mentions the bot, so it does not spam busy chats.
GROUP_AUTO_DOWNLOAD = os.getenv("GROUP_AUTO_DOWNLOAD", "0") == "1"

# ── Admin panel: dynamic settings ─────────────────────────────────────
# All video platforms the bot supports. The admin panel can toggle each on/off
# at runtime (stored in the settings table as pf_enabled_<name>); a disabled
# platform silently queues nothing and tells the user it's temporarily off.
ALL_PLATFORMS = [
    "youtube", "instagram", "tiktok", "twitter",
    "spotify", "soundcloud", "pinterest", "googleplay", "telegram",
]
# Cookie jars whose expiry the panel surfaces (name -> file under COOKIES_DIR).
COOKIE_PLATFORMS = ["youtube", "instagram"]
# Command used by the "safe restart" panel button. The supervisor relaunches
# the bot after it exits, so a clean exit(0) is enough; run.sh also works.
RESTART_CMD = os.getenv("RESTART_CMD", "")

# ── Web dashboard ─────────────────────────────────────────────────────
# A standalone FastAPI admin dashboard (separate process from the bot) that
# reads the same database and controls the same settings. It talks to the bot
# only through the shared SQLite DB + settings flags, so it can be restarted
# independently. Auth is a single admin password (set WEB_PASSWORD); sessions
# are signed cookies. Bind to 0.0.0.0 to expose it, 127.0.0.1 to keep it local.
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "")
# Secret for signing session cookies. Auto-derived from the bot token if unset
# so a fresh deploy still has a stable, non-guessable key.
WEB_SECRET = os.getenv("WEB_SECRET", "") or ("dlweb-" + (BOT_TOKEN[:16] if BOT_TOKEN else "insecure-dev-key"))
# Public bot username, used to render join links / the bot handle in the UI.
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
# Trust X-Forwarded-For for client IP (login rate-limit). Only enable when a
# real reverse proxy / tunnel sits in front, otherwise the header is spoofable
# and defeats the brute-force guard. The Cloudflare quick-tunnel terminates
# elsewhere so the socket peer is already the tunnel; keep this off (False) so
# the rate-limit keys on something an attacker can't rotate at will.
WEB_TRUST_PROXY = os.getenv("WEB_TRUST_PROXY", "0") == "1"
# Mark the session cookie Secure (HTTPS-only). On by default because the
# dashboard is served through an HTTPS tunnel; set 0 only for plain-HTTP local
# testing where a Secure cookie would never be sent back.
WEB_COOKIE_SECURE = os.getenv("WEB_COOKIE_SECURE", "1") == "1"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
]
