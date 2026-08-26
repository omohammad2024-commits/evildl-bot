"""Twitter / X: video, all photos, and the tweet text.

Primary source is the public FxTwitter API, verified working: it returns the
tweet text, author, like/retweet counts, every photo at original quality, and
direct MP4 URLs. vxTwitter is the fallback, and yt-dlp is the last resort for
video-only cases.
"""
import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from bot.utils import net

from bot import config
from bot.services.base import DownloadResult, MediaItem, YtDlpDownloader
from bot.utils.antiblock import random_ua, with_retries
from bot.utils.media_handler import download_file

logger = logging.getLogger(__name__)

STATUS_RE = re.compile(r"(?:twitter\.com|x\.com)/[^/]+/status(?:es)?/(\d+)", re.I)


class TwitterDownloader(YtDlpDownloader):
    name = "twitter"
    patterns = [
        r"(?:^|\.|//)(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/[^/]+/status",
        r"(?:^|\.|//)t\.co/",
    ]

    # ── helpers ───────────────────────────────────────────────────────
    @staticmethod
    def tweet_id(url: str) -> Optional[str]:
        m = STATUS_RE.search(url)
        return m.group(1) if m else None

    async def _fetch_api(self, tweet_id: str) -> Dict[str, Any]:
        """Resolve a tweet through a cascade of free public sources.

        Order reflects live 2026 reliability from this host: vxTwitter is
        currently the most dependable, FxTwitter is a good but flakier second
        (404s on some video IDs), and the Twitter syndication endpoint is a
        mirror-independent third source that returns full media JSON for
        photos and most tweets. First non-empty normalised result wins.
        """
        headers = {"User-Agent": random_ua(), "Accept": "application/json"}
        async with net.aclient(platform="twitter", timeout=25.0, headers=headers,
                               follow_redirects=True) as c:
            # 1. vxTwitter (most reliable today)
            try:
                r = await c.get(f"https://api.vxtwitter.com/Twitter/status/{tweet_id}")
                if r.status_code == 200:
                    data = self._normalise_vx(r.json())
                    if data["photos"] or data["videos"] or data["text"]:
                        return data
            except Exception as exc:
                logger.debug("vxtwitter failed: %s", exc)
            # 2. FxTwitter
            try:
                r = await c.get(f"https://api.fxtwitter.com/status/{tweet_id}")
                if r.status_code == 200:
                    data = r.json()
                    if data.get("code") == 200 and data.get("tweet"):
                        norm = self._normalise_fx(data["tweet"])
                        if norm["photos"] or norm["videos"] or norm["text"]:
                            return norm
            except Exception as exc:
                logger.debug("fxtwitter failed: %s", exc)
            # 3. Twitter syndication (mirror-independent; photos + most tweets)
            try:
                norm = await self._fetch_syndication(c, tweet_id)
                if norm and (norm["photos"] or norm["videos"] or norm["text"]):
                    return norm
            except Exception as exc:
                logger.debug("syndication failed: %s", exc)
        raise ValueError("tweet not reachable via public APIs")

    @classmethod
    async def _fetch_syndication(cls, client, tweet_id: str) -> Optional[Dict[str, Any]]:
        """cdn.syndication.twimg.com/tweet-result — no auth, returns mediaDetails.

        The endpoint needs a ``token`` param; any value works for public tweets
        but Twitter derives a lightweight hash from the id, so we compute the
        documented one to maximise hit rate on newer tweets.
        """
        token = cls._syndication_token(tweet_id)
        url = ("https://cdn.syndication.twimg.com/tweet-result"
               f"?id={tweet_id}&token={token}&lang=en")
        r = await client.get(url, headers={"Accept": "application/json"})
        if r.status_code != 200:
            return None
        t = r.json()
        if not t or t.get("__typename") == "TweetTombstone":
            return None
        photos, videos = [], []
        for m in t.get("mediaDetails") or []:
            mtype = m.get("type")
            if mtype == "photo" and m.get("media_url_https"):
                photos.append(m["media_url_https"] + "?name=orig")
            elif mtype in {"video", "animated_gif"}:
                variants = [v for v in (m.get("video_info", {}).get("variants") or [])
                            if v.get("content_type") == "video/mp4" and v.get("url")]
                if variants:
                    best = max(variants, key=lambda v: v.get("bitrate", 0))
                    oi = m.get("original_info") or {}
                    videos.append({
                        "url": best["url"],
                        "width": oi.get("width") or 0,
                        "height": oi.get("height") or 0,
                        "duration": int((m.get("video_info", {})
                                         .get("duration_millis", 0)) or 0) // 1000,
                        "thumbnail": m.get("media_url_https") or "",
                    })
        user = t.get("user") or {}
        fav = t.get("favorite_count")
        return {
            "text": t.get("text") or "",
            "author": user.get("screen_name") or "",
            "author_name": user.get("name") or "",
            "likes": int(fav) if isinstance(fav, int) else 0,
            "retweets": 0,
            "views": 0,
            "photos": photos,
            "videos": videos,
        }

    @staticmethod
    def _syndication_token(tweet_id: str) -> str:
        """Reproduce the base-36 token the Twitter embed JS derives from the id.

        token = ((id / 1e15) * PI) base-36, with trailing zeros / dot stripped.
        A best-effort match; any value still returns public tweets, but this
        raises the hit rate on recent ids.
        """
        try:
            n = (int(tweet_id) / 1e15) * 3.141592653589793
            # base-36 of the integer+fraction, mimicking JS Number.toString(36)
            whole = int(n)
            digits = "0123456789abcdefghijklmnopqrstuvwxyz"
            out = ""
            w = whole
            if w == 0:
                out = "0"
            while w > 0:
                out = digits[w % 36] + out
                w //= 36
            frac = n - whole
            out += "."
            for _ in range(12):
                frac *= 36
                d = int(frac)
                out += digits[d]
                frac -= d
            return out.replace(".", "").replace("0", "") or "x"
        except Exception:
            return "x"

    @staticmethod
    def _normalise_fx(t: Dict[str, Any]) -> Dict[str, Any]:
        media = t.get("media") or {}
        photos = [p["url"] for p in (media.get("photos") or []) if p.get("url")]
        videos = []
        for v in media.get("videos") or []:
            if v.get("url"):
                videos.append({
                    "url": v["url"],
                    "width": v.get("width") or 0,
                    "height": v.get("height") or 0,
                    "duration": int(v.get("duration") or 0),
                    "thumbnail": v.get("thumbnail_url") or "",
                })
        author = t.get("author") or {}
        return {
            "text": t.get("text") or "",
            "author": author.get("screen_name") or "",
            "author_name": author.get("name") or "",
            "likes": int(t.get("likes") or 0),
            "retweets": int(t.get("retweets") or 0),
            "views": int(t.get("views") or 0) if str(t.get("views") or "").isdigit() else 0,
            "photos": photos,
            "videos": videos,
        }

    @staticmethod
    def _normalise_vx(t: Dict[str, Any]) -> Dict[str, Any]:
        photos, videos = [], []
        for m in t.get("media_extended") or []:
            if m.get("type") == "image" and m.get("url"):
                photos.append(m["url"])
            elif m.get("type") in {"video", "gif"} and m.get("url"):
                size = m.get("size") or {}
                videos.append({
                    "url": m["url"],
                    "width": size.get("width") or 0,
                    "height": size.get("height") or 0,
                    "duration": int(m.get("duration_millis", 0) or 0) // 1000,
                    "thumbnail": m.get("thumbnail_url") or "",
                })
        return {
            "text": t.get("text") or "",
            "author": (t.get("user_screen_name") or ""),
            "author_name": (t.get("user_name") or ""),
            "likes": int(t.get("likes") or 0),
            "retweets": int(t.get("retweets") or 0),
            "views": 0,
            "photos": photos,
            "videos": videos,
        }

    # ── contract ──────────────────────────────────────────────────────
    async def get_info(self, url: str) -> DownloadResult:
        tid = self.tweet_id(url)
        if not tid:
            return await super().get_info(url)

        async def attempt(_: int) -> DownloadResult:
            data = await self._fetch_api(tid)
            vid = data["videos"][0] if data["videos"] else {}
            return DownloadResult(
                title=(data["text"] or "Tweet")[:120],
                uploader=data["author"],
                text=data["text"],
                duration=vid.get("duration", 0),
                width=vid.get("width", 0),
                height=vid.get("height", 0),
                like_count=data["likes"],
                view_count=data["views"],
                platform=self.name,
                thumbnail=vid.get("thumbnail", "") or (data["photos"][0] if data["photos"] else ""),
                extra={
                    "photo_count": len(data["photos"]),
                    "video_count": len(data["videos"]),
                    "author_name": data["author_name"],
                    "retweets": data["retweets"],
                },
            )

        return await with_retries(attempt, platform=self.name)

    async def download(self, url: str, quality: str = "best") -> DownloadResult:
        tid = self.tweet_id(url)
        if not tid:
            return await super().download(url, quality)

        async def attempt(_: int) -> DownloadResult:
            data = await self._fetch_api(tid)
            photos, videos = data["photos"], data["videos"]
            if not photos and not videos:
                # Text-only tweet: hand the text back with no media.
                return DownloadResult(
                    title=(data["text"] or "Tweet")[:120],
                    uploader=data["author"], text=data["text"],
                    like_count=data["likes"], view_count=data["views"],
                    platform=self.name,
                    extra={"text_only": True, "author_name": data["author_name"],
                           "retweets": data["retweets"]},
                )

            items: List[MediaItem] = []
            tasks = []
            # Videos first so a mixed tweet leads with motion.
            for i, v in enumerate(videos):
                path = self.temp_path("mp4", prefix=f"tw_{tid}_v{i}")
                items.append(MediaItem(path=path, url=v["url"], kind="video",
                                       filename=os.path.basename(path)))
                tasks.append(download_file(v["url"], path, referer="https://x.com/"))
            if quality != "video_only":
                for i, p in enumerate(photos):
                    ext = "png" if ".png" in p.lower() else "jpg"
                    path = self.temp_path(ext, prefix=f"tw_{tid}_p{i}")
                    items.append(MediaItem(path=path, url=p, kind="photo",
                                           filename=os.path.basename(path)))
                    tasks.append(download_file(p, path, referer="https://x.com/"))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            good: List[MediaItem] = []
            for item, res in zip(items, results):
                if isinstance(res, Exception):
                    logger.warning("twitter media failed (%s): %s", item.url[:60], res)
                    continue
                if os.path.exists(item.path) and os.path.getsize(item.path) > 0:
                    good.append(item)
            if not good:
                raise ValueError("all tweet media failed to download")

            vid = videos[0] if videos else {}
            return DownloadResult(
                items=good,
                title=(data["text"] or "Tweet")[:120],
                uploader=data["author"],
                text=data["text"],
                duration=vid.get("duration", 0),
                width=vid.get("width", 0),
                height=vid.get("height", 0),
                like_count=data["likes"],
                view_count=data["views"],
                platform=self.name,
                thumbnail=vid.get("thumbnail", ""),
                extra={"author_name": data["author_name"], "retweets": data["retweets"],
                       "photo_count": len(photos), "video_count": len(videos)},
            )

        return await with_retries(attempt, platform=self.name)
