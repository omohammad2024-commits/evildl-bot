"""Service base classes.

``BaseDownloader`` defines the contract. ``YtDlpDownloader`` implements it once
on top of yt-dlp with the anti-block engine wired in, so a platform that yt-dlp
already handles well only needs to declare its patterns.
"""
import logging
import os
import re
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yt_dlp

from bot import config
from bot.utils import antiblock
from bot.utils.antiblock import with_retries
from bot.utils.media_handler import sanitize_filename

logger = logging.getLogger(__name__)


@dataclass
class MediaItem:
    """One downloadable artefact."""
    path: str = ""
    url: str = ""
    kind: str = "video"          # video | audio | photo | document | animation
    filename: str = ""


@dataclass
class DownloadResult:
    """What a service hands back to the handler."""
    items: List[MediaItem] = field(default_factory=list)
    title: str = ""
    uploader: str = ""
    text: str = ""               # post/tweet caption
    duration: int = 0
    width: int = 0
    height: int = 0
    like_count: int = 0
    view_count: int = 0
    platform: str = ""
    thumbnail: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_album(self) -> bool:
        return len(self.items) > 1


class BaseDownloader(ABC):
    """Contract for every platform service."""

    name = "base"
    patterns: List[str] = []
    # Which quality buttons to offer. Empty means "no quality menu".
    qualities: List[str] = []

    def __init__(self, temp_dir: str = config.TEMP_DIR):
        self.temp_dir = temp_dir
        os.makedirs(temp_dir, exist_ok=True)

    # ── detection ─────────────────────────────────────────────────────
    def detect(self, url: str) -> bool:
        return any(re.search(p, url, re.I) for p in self.patterns)

    # ── contract ──────────────────────────────────────────────────────
    @abstractmethod
    async def get_info(self, url: str) -> DownloadResult:
        """Metadata only, no bytes on disk."""

    @abstractmethod
    async def download(self, url: str, quality: str = "best") -> DownloadResult:
        """Fetch media to ``self.temp_dir`` and return the populated result."""

    # ── helpers ───────────────────────────────────────────────────────
    def temp_path(self, ext: str = "bin", prefix: str = "") -> str:
        stem = f"{prefix or self.name}_{uuid.uuid4().hex[:10]}"
        return os.path.join(self.temp_dir, f"{sanitize_filename(stem)}.{ext.lstrip('.')}")

    def outtmpl(self, prefix: str = "") -> str:
        stem = f"{prefix or self.name}_{uuid.uuid4().hex[:10]}"
        return os.path.join(self.temp_dir, f"{stem}.%(ext)s")


class YtDlpDownloader(BaseDownloader):
    """Generic yt-dlp implementation used by most platforms.

    Subclasses usually only override ``name``, ``patterns``, and sometimes
    ``format_selector`` or ``qualities``.
    """

    name = "generic"
    default_format = "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/bv*+ba/b[ext=mp4]/b"

    # Resolution ceiling applied when no explicit tier was requested ("best").
    # ``None`` means uncapped — take the highest the source offers. Platforms
    # that only ever publish H.264/AAC (Instagram, TikTok) can safely go
    # uncapped because their top rendition is already iOS-playable; YouTube
    # keeps the cap because everything above 1080p there is VP9/AV1 and would
    # need an expensive transcode.
    best_max_height: Optional[int] = config.BEST_MAX_HEIGHT

    def format_selector(self, quality: str) -> str:
        if quality in {"", "best", "video"}:
            return self.default_format
        if quality == "mp3":
            return "bestaudio/best"
        if quality.isdigit():
            return antiblock.format_for_height(int(quality))
        return self.default_format

    def ydl_opts(self, attempt: int, quality: str, *, download: bool) -> Dict[str, Any]:
        # An mp3 request is an audio-only pull, which needs the audio-tuned
        # YouTube client chain — the video clients often have no audio-only
        # format and would waste every retry.
        opts = antiblock.platform_ydl_opts(
            self.name, attempt, outtmpl=self.outtmpl() if download else None,
            audio_only=(quality == "mp3"),
        )
        opts["format"] = self.format_selector(quality)
        # Quality tiers are chosen by sort key, not by a height filter, so
        # vertical videos resolve correctly. See antiblock.format_for_height.
        if quality.isdigit():
            opts["format_sort"] = antiblock.sort_for_height(int(quality))
        elif quality != "mp3":
            # "best" honours the platform's own ceiling. sort_for_height(None)
            # ranks by raw resolution, so an uncapped platform takes the top
            # rendition available.
            opts["format_sort"] = antiblock.sort_for_height(self.best_max_height)
        if quality == "mp3":
            opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320",
                }
            ]
        else:
            opts["merge_output_format"] = "mp4"
        return opts

    # ── extraction ────────────────────────────────────────────────────
    def _extract(self, url: str, opts: Dict[str, Any], *, download: bool) -> Dict[str, Any]:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=download)
            if info is None:
                raise ValueError("extractor returned no data")
            if info.get("_type") == "playlist" and info.get("entries"):
                entries = [e for e in info["entries"] if e]
                if not entries:
                    raise ValueError("playlist is empty")
                info = entries[0]
            if download:
                info["__resolved_path"] = self._resolved_path(ydl, info)
            return info

    @staticmethod
    def _resolved_path(ydl: "yt_dlp.YoutubeDL", info: Dict[str, Any]) -> str:
        """Find what yt-dlp actually wrote, post-processing included."""
        downloads = info.get("requested_downloads") or []
        for d in downloads:
            for key in ("filepath", "_filename"):
                p = d.get(key)
                if p and os.path.exists(p):
                    return p
        path = ydl.prepare_filename(info)
        if os.path.exists(path):
            return path
        # Post-processors change the extension (e.g. .webm -> .mp3).
        stem = os.path.splitext(path)[0]
        for ext in ("mp4", "mp3", "mkv", "webm", "m4a", "jpg", "gif"):
            cand = f"{stem}.{ext}"
            if os.path.exists(cand):
                return cand
        raise FileNotFoundError(f"yt-dlp produced no file for {info.get('id')}")

    def _result_from_info(self, info: Dict[str, Any]) -> DownloadResult:
        return DownloadResult(
            title=info.get("title") or info.get("id") or self.name,
            uploader=info.get("uploader") or info.get("channel") or info.get("uploader_id") or "",
            text=(info.get("description") or "")[:900],
            duration=int(info.get("duration") or 0),
            width=int(info.get("width") or 0),
            height=int(info.get("height") or 0),
            like_count=int(info.get("like_count") or 0),
            view_count=int(info.get("view_count") or 0),
            platform=self.name,
            thumbnail=info.get("thumbnail") or "",
            extra={
                "id": info.get("id"),
                "ext": info.get("ext"),
                "filesize": info.get("filesize") or info.get("filesize_approx") or 0,
                "heights": sorted({
                    f["height"] for f in (info.get("formats") or [])
                    if f.get("height")
                }),
                "is_live": bool(info.get("is_live")),
            },
        )

    async def get_info(self, url: str) -> DownloadResult:
        def attempt(n: int) -> DownloadResult:
            info = self._extract(url, self.ydl_opts(n, "best", download=False), download=False)
            return self._result_from_info(info)

        return await with_retries(attempt, platform=self.name)

    # ── quality discovery ─────────────────────────────────────────────
    # Short side of a frame: a 1080x1920 vertical video is "1080p", not 1920p.
    STANDARD_HEIGHTS = [360, 480, 720, 1080]

    async def available_qualities(self, url: str) -> Dict[str, Any]:
        """Probe a link and report which quality buttons make sense.

        Generic implementation shared by every yt-dlp platform (TikTok,
        Twitter, Instagram, ...). YouTube overrides this with its own
        PO-token-aware probe. Returns an empty ``options`` list when the source
        has only one usable resolution, so the caller falls back to a direct
        download instead of a pointless single-button menu.
        """
        def _short_side(f: Dict[str, Any]) -> int:
            w, h = f.get("width") or 0, f.get("height") or 0
            if w and h:
                return min(w, h)
            return h or w or 0

        def attempt(n: int) -> Dict[str, Any]:
            opts = antiblock.platform_ydl_opts(self.name, n)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = [e for e in info["entries"] if e][0]

            formats = info.get("formats") or []
            duration = int(info.get("duration") or 0)
            best_audio = 0.0
            for f in formats:
                if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none"):
                    best_audio = max(best_audio, f.get("tbr") or 0)

            labels = sorted({_short_side(f) for f in formats if _short_side(f)})
            options: List[Dict[str, Any]] = []
            for h in self.STANDARD_HEIGHTS:
                if not any(lbl >= h for lbl in labels):
                    continue
                cands = [f for f in formats
                         if _short_side(f) == h and f.get("vcodec") not in (None, "none")]
                size = 0
                for f in cands:
                    s = f.get("filesize") or f.get("filesize_approx") or 0
                    if not s and f.get("tbr") and duration:
                        s = int(f["tbr"] * 125 * duration)
                    size = max(size, s or 0)
                if size and best_audio and duration:
                    size += int(best_audio * 125 * duration)
                options.append({"quality": str(h), "height": h, "size": size})

            # A menu with fewer than 2 real choices is noise.
            if len(options) < 2:
                options = []

            audio_size = int(best_audio * 125 * duration) if (best_audio and duration) else 0
            return {
                "title": info.get("title") or "",
                "uploader": info.get("uploader") or info.get("channel") or "",
                "duration": duration,
                "view_count": int(info.get("view_count") or 0),
                "like_count": int(info.get("like_count") or 0),
                "thumbnail": info.get("thumbnail") or "",
                "is_live": bool(info.get("is_live")),
                "options": options,
                "audio_size": audio_size,
                "heights": labels,
            }

        return await with_retries(attempt, platform=self.name)

    async def download(self, url: str, quality: str = "best") -> DownloadResult:
        def attempt(n: int) -> DownloadResult:
            info = self._extract(url, self.ydl_opts(n, quality, download=True), download=True)
            result = self._result_from_info(info)
            path = info["__resolved_path"]
            kind = "audio" if quality == "mp3" else self._kind_for(path)
            result.items = [MediaItem(path=path, kind=kind,
                                      filename=os.path.basename(path))]
            return result

        return await with_retries(attempt, platform=self.name)

    @staticmethod
    def _kind_for(path: str) -> str:
        from bot.utils.media_handler import guess_kind

        return guess_kind(path)

    # ── playlists ─────────────────────────────────────────────────────
    async def playlist_entries(self, url: str, limit: int = 0) -> List[Dict[str, Any]]:
        """Flat playlist listing, used to ask the user before bulk-downloading."""
        limit = limit or config.MAX_PLAYLIST_ITEMS

        def attempt(n: int) -> List[Dict[str, Any]]:
            opts = antiblock.platform_ydl_opts(self.name, n)
            opts.update({"extract_flat": "in_playlist", "noplaylist": False,
                         "playlistend": limit})
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            entries = [e for e in (info.get("entries") or []) if e]
            return [
                {
                    "url": e.get("url") or e.get("webpage_url") or "",
                    "title": e.get("title") or "",
                    "duration": e.get("duration") or 0,
                }
                for e in entries
            ]

        return await with_retries(attempt, platform=self.name)
