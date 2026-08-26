"""Anti-block engine: retries, rotation, circuit breaker, yt-dlp option builder.

Every network-facing service routes through here so that blocking behaviour is
handled in exactly one place.
"""
import asyncio
import logging
import random
import time
from typing import Any, Callable, Dict, List, Optional

from bot import config

logger = logging.getLogger(__name__)


# ── Circuit breaker ───────────────────────────────────────────────────
class _Circuit:
    """Per-platform failure tracker.

    After CIRCUIT_THRESHOLD consecutive failures a platform is parked for
    CIRCUIT_COOLDOWN seconds. This keeps us from hammering an endpoint that is
    actively rejecting us, which is what usually escalates a soft block into a
    hard IP ban.
    """

    def __init__(self) -> None:
        self._fails: Dict[str, int] = {}
        self._until: Dict[str, float] = {}

    def record_failure(self, platform: str) -> None:
        n = self._fails.get(platform, 0) + 1
        self._fails[platform] = n
        if n >= config.CIRCUIT_THRESHOLD:
            self._until[platform] = time.time() + config.CIRCUIT_COOLDOWN
            self._fails[platform] = 0
            logger.warning(
                "Circuit opened for %s — cooling down %ss",
                platform, config.CIRCUIT_COOLDOWN,
            )

    def record_success(self, platform: str) -> None:
        self._fails.pop(platform, None)
        self._until.pop(platform, None)

    def cooldown_remaining(self, platform: str) -> int:
        until = self._until.get(platform, 0)
        remaining = until - time.time()
        return int(remaining) if remaining > 0 else 0


circuit = _Circuit()


# ── Rotation helpers ──────────────────────────────────────────────────
def random_ua() -> str:
    return random.choice(config.USER_AGENTS)


def impersonate_for_attempt(attempt: int) -> Optional[str]:
    if not config.IMPERSONATE_CHAIN:
        return None
    return config.IMPERSONATE_CHAIN[attempt % len(config.IMPERSONATE_CHAIN)]


def yt_client_for_attempt(attempt: int) -> str:
    chain = config.YT_CLIENT_CHAIN or ["mweb"]
    return chain[attempt % len(chain)]


# Audio-only pulls (the Spotify bridge) need a different client order than
# video. Measured on this host: `android_vr` serves audio-only formats reliably
# while `mweb`/`tv` return an EMPTY search result and `web_safari` reports
# "requested format is not available". For video the opposite holds — `mweb`
# reaches 720p where `android_vr` caps at 360p (format 18). So video keeps the
# default chain and audio leads with android_vr.
# Download chain for audio-only pulls, ordered by measured success on this host
# (2 videos x 3 clients, real downloads):
#   default    2/2  <- yt-dlp's own client rotation, most robust
#   mweb       1/2  (403 on some music videos)
#   android_vr 0/2  ("requested format is not available" for audio-only)
# Leading with `default` lets yt-dlp pick internally instead of us pinning a
# client that may not carry an audio stream for that particular video.
YT_AUDIO_CHAIN = ("default", "mweb", "web_safari", "android_vr", "tv")

# Chain used purely to RESOLVE a search query to a video id. Different from the
# download chain: `android_vr` answers searches that `mweb` returns empty for,
# so search and download must be allowed to use different clients.
YT_SEARCH_CHAIN = ("android_vr", "mweb", "default", "web_safari", "tv")


def yt_audio_client_for_attempt(attempt: int) -> str:
    return YT_AUDIO_CHAIN[attempt % len(YT_AUDIO_CHAIN)]


# ── Retry wrapper ─────────────────────────────────────────────────────
# Transient = worth retrying (rate limits, timeouts, flaky network, 5xx).
_TRANSIENT = (
    "429", "too many requests", "timed out", "timeout", "temporarily",
    "connection reset", "connection aborted", "read timeout", "5xx",
    "500", "502", "503", "504", "empty media response", "unable to download",
    "fragment", "incomplete",
)

# Hard = deterministic failure that retrying with the same request will NEVER
# fix (dead/invalid cookies, private/removed content, unsupported URL, no
# media in the post). These short-circuit after the FIRST attempt so the user
# gets an answer in ~1s instead of waiting through 4 pointless backoff rounds
# (1.5+3+6+12 ≈ 22s). Checked before _TRANSIENT so it always wins on overlap.
#
# NOTE on "requested format is not available": this looks deterministic but is
# NOT. YouTube exposes a different format list to each player client, so the
# error means "this client has no matching format", not "this video has none".
# Because each retry rotates the client (mweb → tv → web_safari → android_vr),
# aborting here skipped the clients that do work — that single mis-classification
# is what made YouTube and Spotify look permanently broken. It now belongs to
# _CLIENT_SPECIFIC below, which keeps the rotation going.
_HARD = (
    "sign in to confirm", "not a bot", "confirm you're not a bot",
    "login required", "login_required",
    "no video could be found", "unsupported url", "this video is private",
    "video unavailable", "account has been", "has been removed",
    "does not exist", "age-restricted", "age restricted",
    "members-only", "members only",
)

# Failures caused by the specific client/format combination we just tried.
# Worth retrying immediately because the next attempt uses a different client.
_CLIENT_SPECIFIC = (
    "requested format is not available",
    "no such format",
    "only images are available",
    "requested format not available",
    # A search that yielded nothing usable, or a download whose entry was
    # dropped because THIS client could not serve it. Both are fixed by the
    # next client in the rotation, never by waiting.
    "produced no file",
    "no usable search result",
)


def _is_client_specific(exc: BaseException) -> bool:
    """Failure tied to the player client we just used, not to the video."""
    msg = str(exc).lower()
    return any(tok in msg for tok in _CLIENT_SPECIFIC)


def _is_transient(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if _is_client_specific(exc):
        return True
    return any(tok in msg for tok in _TRANSIENT)


def _is_hard(exc: BaseException) -> bool:
    """Deterministic failure — no point retrying the identical request."""
    msg = str(exc).lower()
    return any(tok in msg for tok in _HARD)


async def with_retries(
    fn: Callable[[int], Any],
    *,
    platform: str = "generic",
    attempts: Optional[int] = None,
    on_retry: Optional[Callable[[int, BaseException], Any]] = None,
) -> Any:
    """Run ``fn(attempt)`` with exponential backoff + jitter.

    ``fn`` receives the zero-based attempt number so it can rotate clients,
    user agents, or impersonation targets itself.
    """
    total = attempts or config.RETRY_ATTEMPTS
    wait = circuit.cooldown_remaining(platform)
    if wait:
        raise RuntimeError(f"COOLDOWN:{wait}")

    last: Optional[BaseException] = None
    for attempt in range(total):
        try:
            result = fn(attempt)
            if asyncio.iscoroutine(result):
                result = await result
            circuit.record_success(platform)
            return result
        except Exception as exc:  # noqa: BLE001 - we re-raise below
            last = exc
            hard = _is_hard(exc)
            transient = (not hard) and _is_transient(exc)
            logger.warning(
                "%s attempt %d/%d failed (%s): %s",
                platform, attempt + 1, total,
                "hard" if hard else ("transient" if transient else "fatal"), exc,
            )
            if on_retry:
                try:
                    maybe = on_retry(attempt, exc)
                    if asyncio.iscoroutine(maybe):
                        await maybe
                except Exception:  # pragma: no cover - notifier must not break retries
                    logger.debug("on_retry callback failed", exc_info=True)
            # Stop immediately on the last attempt, on hard (deterministic)
            # failures, and on non-transient errors — only genuine transient
            # errors are worth the exponential backoff.
            if attempt == total - 1 or hard or not transient:
                break
            # A client-specific format failure is fixed by the NEXT client, not
            # by waiting — rotate straight away instead of sleeping.
            if _is_client_specific(exc):
                continue
            delay = config.RETRY_BASE_DELAY * (2 ** attempt)
            delay += random.uniform(0, delay * 0.4)  # jitter
            await asyncio.sleep(min(delay, 30))

    # Only count failures that indicate the *platform* is rejecting us toward
    # the circuit breaker. A "hard" error (private/removed/age-restricted/no
    # media) is the user's specific link, not the platform blocking the bot —
    # counting those would let a few bad links park the platform for everyone.
    if last is not None and not _is_hard(last):
        circuit.record_failure(platform)
    assert last is not None
    raise last


# ── yt-dlp option builder ─────────────────────────────────────────────
def base_ydl_opts(attempt: int = 0, *, outtmpl: Optional[str] = None,
                  platform: str = "") -> Dict[str, Any]:
    """Baseline yt-dlp options with anti-block settings applied.

    ``platform`` selects the per-platform cookie jar; omit it for generic use.
    """
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": config.SOCKET_TIMEOUT,
        "retries": 3,
        "fragment_retries": 5,
        "extractor_retries": 2,
        "concurrent_fragment_downloads": config.CONCURRENT_FRAGMENTS,
        "sleep_requests": config.SLEEP_REQUESTS,
        "http_headers": {
            "User-Agent": random_ua(),
            "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
        },
        "nocheckcertificate": True,
        "geo_bypass": True,
        "ignoreerrors": False,
        "noplaylist": True,
        "restrictfilenames": True,
    }
    if outtmpl:
        opts["outtmpl"] = outtmpl
    # Per-platform cookie jar (falls back to the global COOKIES_FILE).
    if platform:
        from bot.utils import cookies as cookie_jar

        jar = cookie_jar.get(platform)
        if jar:
            opts["cookiefile"] = jar
    elif config.COOKIES_FILE:
        opts["cookiefile"] = config.COOKIES_FILE
    # Proxy: per-platform override, then the global proxy.
    from bot.utils import net

    proxy = net.proxy_for(platform)
    if proxy:
        opts["proxy"] = proxy

    target = impersonate_for_attempt(attempt)
    if target:
        try:
            from yt_dlp.networking.impersonate import ImpersonateTarget

            opts["impersonate"] = ImpersonateTarget.from_str(target)
        except Exception:  # curl_cffi missing -> plain requests
            logger.debug("impersonation unavailable", exc_info=True)
    return opts


def youtube_ydl_opts(attempt: int = 0, *, outtmpl: Optional[str] = None,
                     audio_only: bool = False) -> Dict[str, Any]:
    """YouTube-specific options: JS runtime, PO token provider, client rotation.

    ``audio_only`` switches to the audio-tuned client chain (see YT_AUDIO_CHAIN):
    the clients that serve video best are not the ones that serve audio-only
    formats, so the Spotify bridge would otherwise burn every attempt on clients
    that cannot give it an audio stream.
    """
    opts = base_ydl_opts(attempt, outtmpl=outtmpl, platform="youtube")
    client = (yt_audio_client_for_attempt(attempt) if audio_only
              else yt_client_for_attempt(attempt))
    opts["js_runtimes"] = {config.JS_RUNTIME: {"path": config.NODE_PATH}}
    # PO token wiring. Two things matter here, both learned the hard way:
    #   1. The legacy 'youtube:getpot_bgutil_baseurl' key is deprecated and is
    #      silently ignored, so the token never reaches yt-dlp.
    #   2. Recent nightlies also accept a 'youtubepot' namespace that names the
    #      provider explicitly. Sending both keys is harmless and makes the
    #      config work across versions.
    opts["extractor_args"] = {
        "youtube": {"player_client": [client]},
        "youtubepot": {"providers": ["bgutilhttp"]},
        "youtubepot-bgutilhttp": {"base_url": [config.POT_SERVER]},
    }
    # YouTube throttles aggressive clients; a small pause keeps us under radar.
    opts["sleep_interval"] = 0
    opts["max_sleep_interval"] = 2
    return opts


def platform_ydl_opts(platform: str, attempt: int = 0, *,
                      outtmpl: Optional[str] = None,
                      audio_only: bool = False) -> Dict[str, Any]:
    """Dispatch to the right option builder for a platform."""
    if platform in {"youtube", "spotify"}:
        return youtube_ydl_opts(attempt, outtmpl=outtmpl, audio_only=audio_only)
    opts = base_ydl_opts(attempt, outtmpl=outtmpl, platform=platform)
    if platform == "instagram":
        # A custom User-Agent makes Instagram return an empty media response.
        opts["http_headers"].pop("User-Agent", None)
        # When we egress through a clean proxy (WARP), Instagram serves public
        # posts anonymously. A STALE cookie jar, on the other hand, makes the
        # signed-in API path 400 — so if a proxy is configured for Instagram,
        # drop the cookie and let the clean-IP anonymous path work. (Cookies
        # are only needed for private content / stories, which fail anyway
        # without a live session.)
        from bot.utils import net

        if net.proxy_for("instagram"):
            opts.pop("cookiefile", None)
    return opts


def format_for_height(height: Optional[int]) -> str:
    """Build a yt-dlp format selector for a quality label.

    Important subtlety: yt-dlp's ``height`` field is the real pixel height, so
    filtering with ``height<=720`` breaks vertical videos — a 1080x1920 Short
    has height 1920 and gets excluded from every tier, which surfaces as
    "Requested format is not available".

    Quality labels refer to the SHORT side of the frame, so selection is done
    with a sort (``-S res:N``) instead of a filter. yt-dlp's ``res`` sort key
    uses the smaller dimension and picks the closest match at or below the
    target, which is correct for both landscape and portrait sources.

    iOS compatibility drives the preference order. iPhone/QuickTime decodes
    H.264 video + AAC audio and nothing else that YouTube serves: a VP9 or AV1
    stream, or an Opus audio track, produces a file that is named ``.mp4`` and
    simply refuses to open on an iPhone. So ask for avc1+mp4a first and only
    fall back to "anything" when the source genuinely has no H.264 rendition.
    Whatever survives that fallback is repaired afterwards by
    ``media_handler.ensure_ios_compatible``.
    """
    return (
        "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/"   # ideal: H.264 + AAC
        "bv*[vcodec^=avc1]+ba/"                 # H.264 video, any audio (audio is cheap to fix)
        "b[vcodec^=avc1]/"                      # pre-muxed H.264
        "bv*+ba/b"                              # last resort: anything (gets transcoded)
    )


def sort_for_height(height: Optional[int]) -> List[str]:
    """Sort keys that pair with :func:`format_for_height`.

    Resolution comes first so a quality tier means what it says: putting
    ``codec:avc1`` ahead of ``res`` would silently cap a request at whatever low
    resolution still has an H.264 stream (often 360p), because YouTube only
    publishes H.264 up to 1080p and serves VP9/AV1 above that.

    Codec preference is expressed as ``codec:avc1:m4a`` — video AND audio in one
    key — so that among streams of equal resolution the iOS-playable pair wins.
    Ranking only ``avc1`` left the audio choice to chance, which is how
    H.264+Opus files (unplayable on iPhone) were getting through.
    """
    keys: List[str] = []
    if height:
        # Target a specific tier: closest match at or below the requested height.
        keys.append(f"res:{int(height)}")
    else:
        # "best": highest resolution wins outright.
        keys.append("res")
    keys.extend(["codec:avc1:m4a", "ext:mp4:m4a", "size"])
    return keys
