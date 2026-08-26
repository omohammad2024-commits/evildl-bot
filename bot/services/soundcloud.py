"""SoundCloud: tracks and podcasts as MP3.

SoundCloud has no public API key for anonymous clients, so yt-dlp scrapes a
``client_id`` out of the site's JavaScript and **caches it on disk**. When
SoundCloud rotates that id the cached copy keeps being used and every request
comes back ``404 Unable to download JSON metadata`` — the extractor looks broken
while the network and the URL are fine.

This module therefore treats the cache as disposable: on a metadata failure it
purges the cached id and retries once, which forces a fresh scrape.
"""
import logging
import os
import shutil
from typing import List, Optional

from bot.services.base import DownloadResult, YtDlpDownloader

logger = logging.getLogger(__name__)

# Errors that mean "the cached client_id went stale", not "this track is gone".
_STALE_MARKERS = (
    "unable to download json metadata",
    "unable to download api page",
    "http error 401",
    "http error 403",
    "http error 404",
)

# Errors worth surfacing verbatim: retrying cannot help.
_DRM_MARKERS = ("drm protected", "drm-protected")


def _cache_dirs() -> List[str]:
    """Every location yt-dlp might have written the SoundCloud client_id to."""
    seen: List[str] = []
    roots = [
        os.environ.get("XDG_CACHE_HOME"),
        os.path.expanduser("~/.cache"),
        "/root/.cache",
        "/data/.cache",
    ]
    for root in roots:
        if not root:
            continue
        path = os.path.join(root, "yt-dlp", "soundcloud")
        if path not in seen:
            seen.append(path)
    return seen


def purge_client_id_cache() -> int:
    """Drop the cached client_id so the next call re-scrapes it. Returns count."""
    removed = 0
    for path in _cache_dirs():
        if os.path.isdir(path):
            try:
                shutil.rmtree(path)
                removed += 1
                logger.info("cleared stale SoundCloud client_id cache: %s", path)
            except OSError as exc:
                logger.debug("could not clear %s: %s", path, exc)
    return removed


class SoundCloudDownloader(YtDlpDownloader):
    name = "soundcloud"
    patterns = [
        r"(?:^|\.|//)(?:www\.|m\.)?soundcloud\.com/",
        r"(?:^|\.|//)on\.soundcloud\.com/",
        r"(?:^|\.|//)snd\.sc/",
    ]

    def format_selector(self, quality: str) -> str:
        # Prefer a progressive MP3 when SoundCloud offers one: no HLS assembly
        # and no transcode needed. Fall back to any audio stream.
        return ("bestaudio[protocol^=http][ext=mp3]/bestaudio[ext=mp3]/"
                "bestaudio[ext=m4a]/bestaudio/best")

    def ydl_opts(self, attempt: int, quality: str, *, download: bool):
        opts = super().ydl_opts(attempt, quality, download=download)
        # Always deliver MP3: SoundCloud serves HLS/Opus which Telegram will not
        # play as an audio message.
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }]
        opts.pop("merge_output_format", None)
        # Ask for the whole set only when the user actually linked a set.
        opts.setdefault("noplaylist", True)
        return opts

    @staticmethod
    def _is_stale(exc: Exception) -> bool:
        msg = str(exc).lower()
        if any(m in msg for m in _DRM_MARKERS):
            return False
        return any(m in msg for m in _STALE_MARKERS)

    async def _with_fresh_id(self, coro_factory, what: str):
        """Run an extractor call, refreshing the client_id once if it looks stale."""
        try:
            return await coro_factory()
        except Exception as exc:
            if not self._is_stale(exc):
                raise
            logger.warning("soundcloud %s failed (%s) — refreshing client_id",
                           what, str(exc)[:120])
            if not purge_client_id_cache():
                raise
            return await coro_factory()

    async def get_info(self, url: str) -> DownloadResult:
        return await self._with_fresh_id(
            lambda: super(SoundCloudDownloader, self).get_info(url), "get_info")

    async def download(self, url: str, quality: str = "best") -> DownloadResult:
        result = await self._with_fresh_id(
            lambda: super(SoundCloudDownloader, self).download(url, quality),
            "download")
        for item in result.items:
            item.kind = "audio"
        return result
