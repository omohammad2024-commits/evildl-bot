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
import re
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

    # ── shorts vs full videos ─────────────────────────────────────────
    # A Short is a different product from a normal upload: it is short, vertical,
    # and the viewer never picks a resolution. Asking "which quality?" for a 30s
    # clip is friction, so Shorts skip the menu and take the best rendition.
    # Full videos keep the menu because a 1080p hour-long upload is hundreds of
    # megabytes and the user should decide.
    SHORT_MAX_DURATION = 180

    @staticmethod
    def is_short_url(url: str) -> bool:
        """True when the LINK itself says Short — instant, no network call."""
        return bool(re.search(r"/shorts/", url, re.I))

    @classmethod
    def looks_like_short(cls, info: Dict[str, Any]) -> bool:
        """Decide from probed metadata, for links that hide their nature.

        A Short shared via ``youtu.be/<id>`` or ``watch?v=<id>`` carries no
        ``/shorts/`` marker, so the URL test alone would drop it into the menu
        path. Two signals together are reliable: short duration AND a portrait
        frame. Either one on its own is not — plenty of normal uploads are under
        three minutes, and vertical full videos exist too.
        """
        duration = int(info.get("duration") or 0)
        if not duration or duration > cls.SHORT_MAX_DURATION:
            return False
        w = int(info.get("width") or 0)
        h = int(info.get("height") or 0)
        return bool(w and h and h > w)

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
            # The frame size of the best video rendition — used to tell a Short
            # (portrait) from a normal upload when the URL doesn't say.
            best_v = None
            for f in formats:
                if f.get("vcodec") in (None, "none"):
                    continue
                if best_v is None or short_side(f) > short_side(best_v):
                    best_v = f
            top_w = int((best_v or {}).get("width") or info.get("width") or 0)
            top_h = int((best_v or {}).get("height") or info.get("height") or 0)

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

            # Smart menu, part 1: drop tiers this bot physically cannot deliver.
            # Offering "1080p · 1.4GB" when the upload ceiling is 2GB-but-really
            # 50MB without MTProto just produces a failure after a long wait.
            # A tier with no size estimate is kept — unknown is not too big.
            cap = config.MAX_UPLOAD
            usable = [o for o in options if not o["size"] or o["size"] <= cap]
            # Never present an empty menu: if every tier is over the cap, keep
            # the smallest so the user still has one honest choice.
            if options and not usable:
                usable = [min(options, key=lambda o: o["size"] or 0)]
            options = usable

            # Smart menu, part 2: mark the tier worth defaulting to — the highest
            # that still lands under the "comfortable" size. The keyboard renders
            # this with a ⭐ so the user has guidance instead of four bare numbers.
            comfort = min(cap, config.QUALITY_COMFORT_BYTES)
            best_pick = ""
            for o in options:
                if not o["size"] or o["size"] <= comfort:
                    best_pick = o["quality"]
            if not best_pick and options:
                best_pick = options[0]["quality"]
            for o in options:
                o["recommended"] = (o["quality"] == best_pick)

            audio_size = int(best_audio * 125 * duration) if (best_audio and duration) else 0
            top_short_side = min(top_w, top_h) if (top_w and top_h) else (top_h or top_w)
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
                "width": top_w,
                "height": top_h,
                # Highest real quality available, as a quality label. Shorts are
                # downloaded at exactly this instead of the generic "best".
                "top_quality": str(top_short_side) if top_short_side else "best",
                "recommended": best_pick,
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
