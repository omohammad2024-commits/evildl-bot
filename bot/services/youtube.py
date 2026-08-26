"""YouTube: quality menu, MP3, Shorts, playlists, music.

Reliability notes learned from live testing:
* a Node >= 22 runtime is required to solve YouTube's JS challenges
* a PO token provider (bgutil, see config.POT_SERVER) is required for most
  clients; without it extraction silently loses formats
* client rotation (mweb -> tv -> web_safari -> android_vr -> ios) recovers from
  per-client blocks, and is handled by the anti-block engine
"""
import logging
import os
from typing import Any, Dict, List, Optional

import yt_dlp

from bot import config
from bot.services.base import DownloadResult, MediaItem, YtDlpDownloader
from bot.utils import antiblock
from bot.utils.antiblock import with_retries

logger = logging.getLogger(__name__)

# Quality buttons offered for YouTube links.
STANDARD_HEIGHTS = [360, 480, 720, 1080]


class YouTubeDownloader(YtDlpDownloader):
    name = "youtube"
    patterns = [
        r"(?:^|\.|//)(?:www\.|m\.|music\.)?youtube\.com/(?:watch\?|shorts/|live/|playlist\?|embed/)",
        r"(?:^|\.|//)youtu\.be/",
    ]
    qualities = ["360", "480", "720", "1080", "mp3"]

    def format_selector(self, quality: str) -> str:
        if quality == "mp3":
            return "bestaudio[ext=m4a]/bestaudio/best"
        if quality.isdigit():
            return antiblock.format_for_height(int(quality))
        # "best" stays capped at the class ceiling (1080p). Above that YouTube
        # publishes VP9/AV1 only, which an iPhone cannot decode, so an uncapped
        # pick would either arrive unplayable or force an expensive transcode.
        return antiblock.format_for_height(self.best_max_height)

    # ── quality discovery ─────────────────────────────────────────────
    async def available_qualities(self, url: str) -> Dict[str, Any]:
        """Probe once and report which buttons make sense, with size estimates."""
        def attempt(n: int) -> Dict[str, Any]:
            opts = antiblock.youtube_ydl_opts(n)
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = [e for e in info["entries"] if e][0]

            formats = info.get("formats") or []
            duration = int(info.get("duration") or 0)
            best_audio = 0
            for f in formats:
                if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none"):
                    best_audio = max(best_audio, f.get("tbr") or 0)

            options: List[Dict[str, Any]] = []
            # A quality label refers to the SHORT side of the frame, so a
            # 1080x1920 Short counts as 1080p, not 1920p. Using min(w, h) makes
            # the menu correct for both landscape and portrait sources.
            def short_side(f: Dict[str, Any]) -> int:
                w, h = f.get("width") or 0, f.get("height") or 0
                if w and h:
                    return min(w, h)
                return h or w or 0

            labels = sorted({short_side(f) for f in formats if short_side(f)})
            for h in STANDARD_HEIGHTS:
                if not any(lbl >= h for lbl in labels):
                    continue
                # Pick the best video stream at this tier for the estimate.
                cands = [
                    f for f in formats
                    if short_side(f) == h and f.get("vcodec") not in (None, "none")
                ]
                size = 0
                for f in cands:
                    s = f.get("filesize") or f.get("filesize_approx") or 0
                    if not s and f.get("tbr") and duration:
                        s = int(f["tbr"] * 125 * duration)
                    size = max(size, s or 0)
                if size and best_audio and duration:
                    size += int(best_audio * 125 * duration)
                options.append({"quality": str(h), "height": h, "size": size})

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
        result = await super().download(url, quality)
        if quality == "mp3" and result.items:
            result.items[0].kind = "audio"
        return result

    @staticmethod
    def is_playlist(url: str) -> bool:
        return "list=" in url and "watch?v=" not in url

    # ── subtitles ─────────────────────────────────────────────────────
    async def fetch_subtitle(self, url: str, lang_pref: str = "") -> "Optional[str]":
        """Download the best available subtitle track to a .srt file.

        Prefers manual subtitles, then auto-generated captions. Returns the
        path to the .srt, or None when the video has no captions at all.
        """
        import glob

        def attempt(n: int) -> "Optional[str]":
            opts = antiblock.youtube_ydl_opts(n)
            outtmpl = self.outtmpl(prefix="sub")
            opts.update({
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitlesformat": "srt/best",
                "subtitleslangs": [lang_pref] if lang_pref else ["en", "fa", "en-orig"],
                "outtmpl": outtmpl,
                "convertsubtitles": "srt",
                "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}],
            })
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
            stem = outtmpl.rsplit(".%(ext)s", 1)[0]
            hits = glob.glob(f"{stem}*.srt") or glob.glob(f"{stem}*.vtt")
            return hits[0] if hits else None

        return await with_retries(attempt, platform=self.name)


class YouTubeMusicDownloader(YouTubeDownloader):
    """music.youtube.com links default to audio."""
    name = "youtube"
    patterns = [r"music\.youtube\.com/"]

    def format_selector(self, quality: str) -> str:
        if quality in {"", "best"}:
            return "bestaudio[ext=m4a]/bestaudio/best"
        return super().format_selector(quality)
