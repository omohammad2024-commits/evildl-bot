"""TikTok: no-watermark video, slideshows, short links."""
import asyncio
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from bot.utils import net

from bot.services.base import DownloadResult, MediaItem, YtDlpDownloader
from bot.utils.antiblock import random_ua, with_retries
from bot.utils.media_handler import download_file

logger = logging.getLogger(__name__)


class TikTokDownloader(YtDlpDownloader):
    name = "tiktok"
    patterns = [
        r"(?:^|\.|//)(?:www\.|m\.)?tiktok\.com/",
        r"(?:^|\.|//)(?:vm|vt)\.tiktok\.com/",
    ]
    qualities = ["video", "mp3"]

    def format_selector(self, quality: str) -> str:
        if quality == "mp3":
            return "bestaudio/best"
        # download_addr formats carry no watermark on most videos.
        return "b[ext=mp4]/b"

    async def _tikwm(self, url: str) -> Optional[Dict[str, Any]]:
        """Public no-watermark API fallback, also handles image slideshows."""
        try:
            async with net.aclient(platform="tiktok", timeout=30.0, follow_redirects=True,
                                         headers={"User-Agent": random_ua()}) as c:
                r = await c.get("https://www.tikwm.com/api/",
                                params={"url": url, "hd": "1"})
                if r.status_code != 200:
                    return None
                data = r.json()
                if data.get("code") != 0 or not data.get("data"):
                    return None
                return data["data"]
        except Exception as exc:
            logger.debug("tikwm failed: %s", exc)
            return None

    async def download(self, url: str, quality: str = "best") -> DownloadResult:
        try:
            return await super().download(url, quality)
        except Exception as exc:
            logger.info("tiktok yt-dlp failed (%s), trying tikwm", exc)

        async def attempt(_: int) -> DownloadResult:
            data = await self._tikwm(url)
            if not data:
                raise ValueError("TikTok unavailable via all sources")

            items: List[MediaItem] = []
            author = (data.get("author") or {}).get("unique_id", "")
            title = data.get("title") or "TikTok"

            images = data.get("images") or []
            if images and quality != "mp3":
                tasks = []
                for i, img in enumerate(images[:10]):
                    path = self.temp_path("jpg", prefix=f"tt_p{i}")
                    items.append(MediaItem(path=path, url=img, kind="photo",
                                           filename=os.path.basename(path)))
                    tasks.append(download_file(img, path, referer="https://www.tiktok.com/"))
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                src = data.get("hdplay") or data.get("play") or data.get("wmplay")
                if quality == "mp3":
                    src = data.get("music") or src
                if not src:
                    raise ValueError("no media URL from TikTok")
                if src.startswith("/"):
                    src = "https://www.tikwm.com" + src
                ext = "mp3" if quality == "mp3" else "mp4"
                path = self.temp_path(ext, prefix="tt")
                await download_file(src, path, referer="https://www.tiktok.com/")
                items.append(MediaItem(path=path, kind="audio" if quality == "mp3" else "video",
                                       url=src, filename=os.path.basename(path)))

            good = [i for i in items if os.path.exists(i.path) and os.path.getsize(i.path) > 0]
            if not good:
                raise ValueError("TikTok media download failed")

            return DownloadResult(
                items=good, title=title[:120], uploader=author, text=title,
                duration=int(data.get("duration") or 0),
                like_count=int(data.get("digg_count") or 0),
                view_count=int(data.get("play_count") or 0),
                platform=self.name,
                thumbnail=data.get("cover") or "",
                extra={"slideshow": bool(images)},
            )

        return await with_retries(attempt, platform=self.name)
