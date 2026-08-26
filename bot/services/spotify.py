"""Spotify: metadata from the public embed page, audio sourced from YouTube.

Spotify itself is DRM-protected, so the only workable path is: read the track
metadata, find the matching YouTube upload, download that as MP3, then attach
the real Spotify cover art.
"""
import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from bot.utils import net
import yt_dlp

from bot import config
from bot.services.base import DownloadResult, MediaItem, YtDlpDownloader
from bot.utils import antiblock
from bot.utils.antiblock import random_ua, with_retries
from bot.utils.media_handler import convert_to_mp3, download_file, embed_cover

logger = logging.getLogger(__name__)

SPOTIFY_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist)/([A-Za-z0-9]+)", re.I
)


class SpotifyDownloader(YtDlpDownloader):
    name = "spotify"
    patterns = [
        r"(?:^|\.|//)open\.spotify\.com/",
        r"(?:^|\.|//)spotify\.link/",
    ]

    @staticmethod
    def parse(url: str) -> Optional[tuple]:
        m = SPOTIFY_RE.search(url)
        return (m.group(1), m.group(2)) if m else None

    async def _resolve_short(self, url: str) -> str:
        if "spotify.link" not in url:
            return url
        try:
            async with net.aclient(timeout=20.0, follow_redirects=True,
                                         headers={"User-Agent": random_ua()}) as c:
                r = await c.get(url)
                return str(r.url)
        except Exception:
            return url

    async def _embed_metadata(self, kind: str, sid: str) -> Dict[str, Any]:
        """Read track/album/playlist data from Spotify's public embed page."""
        url = f"https://open.spotify.com/embed/{kind}/{sid}"
        async with net.aclient(timeout=30.0, follow_redirects=True,
                                     headers={"User-Agent": random_ua()}) as c:
            r = await c.get(url)
            r.raise_for_status()
            html = r.text

        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                      html, re.S)
        if not m:
            raise ValueError("Spotify embed page changed shape")
        data = json.loads(m.group(1))
        entity = (
            data.get("props", {}).get("pageProps", {}).get("state", {})
            .get("data", {}).get("entity", {})
        )
        if not entity:
            raise ValueError("no entity in Spotify embed data")

        def cover_of(e: Dict[str, Any]) -> str:
            imgs = e.get("visualIdentity", {}).get("image") or e.get("coverArt", {}).get("sources") or []
            best = ""
            best_w = -1
            for i in imgs:
                w = i.get("maxWidth") or i.get("width") or 0
                if w > best_w:
                    best_w, best = w, i.get("url", "")
            return best

        def artists_of(e: Dict[str, Any]) -> str:
            arts = e.get("artists") or []
            names = [a.get("name", "") for a in arts if a.get("name")]
            return ", ".join(names) or e.get("subtitle", "")

        tracks: List[Dict[str, Any]] = []
        if kind == "track":
            tracks.append({
                "title": entity.get("title") or entity.get("name") or "",
                "artist": artists_of(entity),
                "duration": int(entity.get("duration", 0) or 0) // 1000,
                "cover": cover_of(entity),
            })
        else:
            for t in (entity.get("trackList") or []):
                tracks.append({
                    "title": t.get("title") or "",
                    "artist": t.get("subtitle") or artists_of(entity),
                    "duration": int(t.get("duration", 0) or 0) // 1000,
                    "cover": cover_of(entity),
                })

        return {
            "kind": kind,
            "name": entity.get("title") or entity.get("name") or "",
            "cover": cover_of(entity),
            "artist": artists_of(entity),
            "tracks": [t for t in tracks if t["title"]],
        }

    # ── YouTube bridge ────────────────────────────────────────────────
    def _search_video_id(self, query: str) -> str:
        """Resolve a query to a YouTube video id, trying clients until one answers.

        Search and download must be separated. Measured on this host, no single
        player client does both for every track: `android_vr` searches fine but
        has no audio-only format for some videos, while `mweb` has the audio
        formats but returns an EMPTY search result. Doing them in one call meant
        a track only worked if the same client happened to be good at both.
        """
        last = ""
        for client in antiblock.YT_SEARCH_CHAIN:
            opts = antiblock.youtube_ydl_opts(0, audio_only=True)
            opts["extractor_args"]["youtube"]["player_client"] = [client]
            opts.update({"noplaylist": True, "default_search": "ytsearch1",
                         "skip_download": True, "extract_flat": "in_playlist"})
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(f"ytsearch1:{query}", download=False)
                entries = info.get("entries") or []
                if entries and entries[0].get("id"):
                    return entries[0]["id"]
                last = f"empty result via {client}"
            except Exception as exc:
                last = f"{client}: {str(exc)[:80]}"
                logger.debug("spotify search via %s failed: %s", client, exc)
        raise ValueError(f"no YouTube match for {query!r} ({last})")

    async def _download_from_youtube(self, query: str, cover_url: str,
                                     meta: Dict[str, Any]) -> MediaItem:
        # Phase 1: find the video once, outside the retry loop.
        video_id = await asyncio.to_thread(self._search_video_id, query)
        url = f"https://www.youtube.com/watch?v={video_id}"

        # Phase 2: download that exact video, rotating clients AND loosening the
        # format each attempt so a client with no audio-only stream can still
        # deliver (audio is extracted from the muxed file by the postprocessor).
        formats = (
            "bestaudio[ext=m4a]/bestaudio",
            "bestaudio/best",
            "best",
            "worst",
        )

        def attempt(n: int) -> str:
            opts = antiblock.youtube_ydl_opts(n, outtmpl=self.outtmpl(prefix="sp"),
                                              audio_only=True)
            opts.update({
                "format": formats[min(n, len(formats) - 1)],
                "noplaylist": True,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320",
                }],
            })
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info is None:
                    raise RuntimeError(f"no data for {video_id} (client rejected it)")
                return self._resolved_path(ydl, info)

        path = await with_retries(attempt, platform="youtube")

        # Attach the genuine Spotify cover instead of the YouTube thumbnail.
        if cover_url:
            cover_path = self.temp_path("jpg", prefix="cover")
            try:
                await download_file(cover_url, cover_path)
                with_art = await embed_cover(path, cover_path)
                if with_art:
                    os.remove(path)
                    path = with_art
            except Exception as exc:
                logger.debug("cover embed skipped: %s", exc)
            finally:
                if os.path.exists(cover_path):
                    os.remove(cover_path)

        return MediaItem(path=path, kind="audio", filename=os.path.basename(path))

    # ── contract ──────────────────────────────────────────────────────
    async def get_info(self, url: str) -> DownloadResult:
        url = await self._resolve_short(url)
        parsed = self.parse(url)
        if not parsed:
            raise ValueError("unrecognised Spotify link")
        kind, sid = parsed
        meta = await self._embed_metadata(kind, sid)
        first = meta["tracks"][0] if meta["tracks"] else {}
        return DownloadResult(
            title=meta["name"] or first.get("title", ""),
            uploader=meta["artist"] or first.get("artist", ""),
            duration=first.get("duration", 0),
            platform=self.name,
            thumbnail=meta["cover"],
            extra={
                "kind": kind,
                "track_count": len(meta["tracks"]),
                "is_collection": kind in {"album", "playlist"},
                "tracks": meta["tracks"],
            },
        )

    async def download(self, url: str, quality: str = "best") -> DownloadResult:
        url = await self._resolve_short(url)
        parsed = self.parse(url)
        if not parsed:
            raise ValueError("unrecognised Spotify link")
        kind, sid = parsed
        meta = await self._embed_metadata(kind, sid)
        if not meta["tracks"]:
            raise ValueError("no tracks found for this Spotify link")

        track = meta["tracks"][0]
        query = f"{track['artist']} - {track['title']} audio"
        item = await self._download_from_youtube(query, track.get("cover") or meta["cover"], track)

        return DownloadResult(
            items=[item],
            title=track["title"],
            uploader=track["artist"],
            duration=track.get("duration", 0),
            platform=self.name,
            thumbnail=track.get("cover") or meta["cover"],
            extra={"album": meta["name"], "kind": kind,
                   "track_count": len(meta["tracks"]), "tracks": meta["tracks"]},
        )

    async def download_track(self, track: Dict[str, Any]) -> DownloadResult:
        """Download one entry from an album/playlist listing."""
        query = f"{track.get('artist','')} - {track.get('title','')} audio"
        item = await self._download_from_youtube(query, track.get("cover", ""), track)
        return DownloadResult(
            items=[item], title=track.get("title", ""), uploader=track.get("artist", ""),
            duration=track.get("duration", 0), platform=self.name,
            thumbnail=track.get("cover", ""),
        )
