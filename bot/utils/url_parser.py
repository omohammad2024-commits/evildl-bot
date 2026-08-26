"""Platform detection and service registry.

Detection is driven by each service's own patterns, so adding a platform means
adding one service class and registering it here — the parser needs no edits.
"""
import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"'\\]+", re.I)

# Human-facing labels and emoji, shared by keyboards, captions, and help text.
PLATFORM_LABELS: Dict[str, Tuple[str, str]] = {
    "instagram": ("📸", "Instagram"),
    "youtube": ("🎬", "YouTube"),
    "tiktok": ("📱", "TikTok"),
    "spotify": ("🎵", "Spotify"),
    "pinterest": ("📌", "Pinterest"),
    "soundcloud": ("🎧", "SoundCloud"),
    "twitter": ("🐦", "Twitter / X"),
    "googleplay": ("📲", "Google Play"),
    "telegram": ("✈️", "Telegram"),
    "direct": ("🔗", "Direct link"),
}

_REGISTRY: Dict[str, type] = {}
_ORDER: List[str] = [
    "youtube", "instagram", "tiktok", "twitter", "spotify", "soundcloud",
    "pinterest", "googleplay", "telegram", "direct",
]


def _registry() -> Dict[str, type]:
    """Import services lazily so a broken extractor cannot stop the bot booting."""
    global _REGISTRY
    if _REGISTRY:
        return _REGISTRY

    from bot.services.direct_link import DirectLinkDownloader
    from bot.services.googleplay import GooglePlayDownloader
    from bot.services.instagram import InstagramDownloader
    from bot.services.pinterest import PinterestDownloader
    from bot.services.soundcloud import SoundCloudDownloader
    from bot.services.spotify import SpotifyDownloader
    from bot.services.telegram import TelegramDownloader
    from bot.services.tiktok import TikTokDownloader
    from bot.services.twitter import TwitterDownloader
    from bot.services.youtube import YouTubeDownloader

    _REGISTRY = {
        "youtube": YouTubeDownloader,
        "instagram": InstagramDownloader,
        "tiktok": TikTokDownloader,
        "twitter": TwitterDownloader,
        "spotify": SpotifyDownloader,
        "soundcloud": SoundCloudDownloader,
        "pinterest": PinterestDownloader,
        "googleplay": GooglePlayDownloader,
        "telegram": TelegramDownloader,
        "direct": DirectLinkDownloader,
    }
    return _REGISTRY


def detect_platform(url: str) -> str:
    """Return the platform key for a URL, or 'direct' as the fallback."""
    reg = _registry()
    for key in _ORDER:
        if key == "direct":
            continue
        cls = reg.get(key)
        if cls and cls().detect(url):
            return key
    return "direct"


def get_service(platform: str):
    reg = _registry()
    cls = reg.get(platform) or reg["direct"]
    return cls()


def service_for_url(url: str):
    return get_service(detect_platform(url))


def extract_urls(text: str, limit: int = 20) -> List[str]:
    """Pull every URL out of a message, de-duplicated, order preserved."""
    found = URL_RE.findall(text or "")
    seen, out = set(), []
    for u in found:
        u = u.rstrip(").,;:!?\u060c\u061f")
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= limit:
            break
    return out


def platform_label(platform: str) -> str:
    emoji, name = PLATFORM_LABELS.get(platform, ("🔗", platform.title()))
    return f"{emoji} {name}"


def platform_emoji(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, ("🔗", ""))[0]


def supported_list() -> str:
    """Multi-line list used in /start and /help."""
    lines = []
    for key in _ORDER:
        emoji, name = PLATFORM_LABELS[key]
        lines.append(f"{emoji} {name}")
    return "\n".join(lines)
