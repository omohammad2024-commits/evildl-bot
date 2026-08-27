"""Pinterest: pins (image or video), including pin.it short links.

Extraction strategy, in order of reliability (all three verified live):

1. **PinResource API** — the same JSON endpoint pinterest.com's own web app
   calls. Returns the original-quality asset plus video variants. This is the
   primary path.
2. **yt-dlp** — handles some video pins directly.
3. **HTML scrape** — last resort.

Why the API comes first: the pin page no longer carries ``og:image`` or a
usable ``__PWS_DATA__`` blob (verified — zero media nodes in the embedded
state), so scraping the HTML finds only CSS sprites and thumbnails. The old
scrape-only implementation failed with "no media found on the pin page" for
exactly this reason.
"""
import json
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

import httpx

from bot.utils import net

from bot.services.base import DownloadResult, MediaItem, YtDlpDownloader
from bot.utils.antiblock import random_ua, with_retries
from bot.utils.media_handler import download_file

logger = logging.getLogger(__name__)

API = "https://www.pinterest.com/resource/PinResource/get/"
REFERER = "https://www.pinterest.com/"

# A desktop UA is required: mobile UAs get a stripped page and the API 403s.
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


class PinterestDownloader(YtDlpDownloader):
    name = "pinterest"
    patterns = [
        r"(?:^|\.|//)(?:[a-z]{2}\.)?pinterest\.[a-z.]+/pin/",
        r"(?:^|\.|//)pin\.it/",
    ]

    # ── url handling ──────────────────────────────────────────────────
    async def _resolve(self, url: str) -> str:
        """Follow pin.it short links to the canonical /pin/<id>/ URL."""
        if "pin.it" not in url:
            return url
        async with net.aclient(platform="pinterest", timeout=20.0, follow_redirects=True,
                                     headers={"User-Agent": DESKTOP_UA}) as c:
            r = await c.get(url)
            return str(r.url)

    @staticmethod
    def _pin_id(url: str) -> Optional[str]:
        m = re.search(r"/pin/(\d+)", url)
        return m.group(1) if m else None

    # ── primary path: the web app's own API ───────────────────────────
    async def _api_fetch(self, pin_id: str, source_url: str) -> Dict[str, Any]:
        params = {
            "source_url": f"/pin/{pin_id}/",
            "data": json.dumps(
                {"options": {"id": pin_id, "field_set_key": "detailed"},
                 "context": {}}
            ),
        }
        headers = {
            "User-Agent": DESKTOP_UA,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "X-Pinterest-PWS-Handler": "www/pin/[id].js",
            "Referer": source_url,
        }
        async with net.aclient(platform="pinterest", timeout=30.0, follow_redirects=True,
                                     headers=headers) as c:
            r = await c.get(API, params=params)
            r.raise_for_status()
            return r.json()["resource_response"]["data"]

    @staticmethod
    def _best_video(data: Dict[str, Any]) -> Optional[str]:
        """Highest-bitrate variant across the pin and any story pages."""
        best_url, best_score = None, -1

        def consider(videos: Any) -> None:
            nonlocal best_url, best_score
            if not isinstance(videos, dict):
                return
            for variant in (videos.get("video_list") or {}).values():
                if not isinstance(variant, dict):
                    continue
                url = variant.get("url")
                if not url:
                    continue
                score = (variant.get("width") or 0) * (variant.get("height") or 0)
                if score >= best_score:
                    best_url, best_score = url, score

        consider(data.get("videos"))
        # Story pins keep their media in a page list instead.
        for block in (data.get("story_pin_data") or {}).get("pages") or []:
            for b in block.get("blocks") or []:
                consider(b.get("video"))
        return best_url

    @staticmethod
    def _best_image(data: Dict[str, Any]) -> Optional[str]:
        """Highest-resolution still in the payload, scored by real pixel count.

        The old version returned ``images['orig']`` whenever it existed. That is
        usually the largest, but not always: on some pins ``orig`` is a cropped
        or re-encoded rendition while a numbered key (or a story page's
        ``originals``) carries more pixels. Verified on live pins — the numbered
        keys and ``orig`` frequently report identical dimensions, so guessing by
        name alone throws away information that is right there in the payload.

        Scoring every candidate by ``width * height`` and keeping the winner is
        strictly better, and it also reaches story pins, whose stills live under
        ``story_pin_data.pages[].image.images`` and were previously unreachable
        for image-only story pins.
        """
        best_url, best_score = None, -1

        def consider(images: Any) -> None:
            nonlocal best_url, best_score
            if not isinstance(images, dict):
                return
            for key, variant in images.items():
                if not isinstance(variant, dict):
                    continue
                url = variant.get("url")
                if not url:
                    continue
                w = variant.get("width") or 0
                h = variant.get("height") or 0
                score = w * h
                if not score:
                    # No dimensions given: fall back to the number in the key
                    # ("736x" -> 736) so an unlabelled variant still ranks.
                    m = re.match(r"(\d+)x", str(key))
                    score = int(m.group(1)) if m else 0
                # Prefer /originals/ on a tie: it is the untouched upload.
                if score > best_score or (score == best_score
                                          and "/originals/" in url):
                    best_url, best_score = url, score

        consider(data.get("images"))
        for page in (data.get("story_pin_data") or {}).get("pages") or []:
            for key in ("image", "image_adjusted"):
                consider((page.get(key) or {}).get("images"))
            for block in page.get("blocks") or []:
                consider((block.get("image") or {}).get("images"))
        return best_url

    async def _via_api(self, url: str) -> DownloadResult:
        pin_id = self._pin_id(url)
        if not pin_id:
            raise ValueError("could not read the pin id from the link")

        data = await self._api_fetch(pin_id, url)
        title = (data.get("title") or data.get("grid_title")
                 or (data.get("rich_summary") or {}).get("display_name") or "")
        text = (data.get("description") or "").strip()
        uploader = ((data.get("pinner") or {}).get("full_name")
                    or (data.get("pinner") or {}).get("username") or "")

        video = self._best_video(data)
        if video:
            path = self.temp_path("mp4", prefix="pin")
            await download_file(video, path, referer=REFERER)
            kind = "video"
        else:
            image = self._best_image(data)
            if not image:
                raise ValueError("the pin has no downloadable media")
            ext = "png" if ".png" in image.lower() else "jpg"
            path = self.temp_path(ext, prefix="pin")
            await download_file(image, path, referer=REFERER)
            kind = "photo"

        return DownloadResult(
            items=[MediaItem(path=path, kind=kind,
                             filename=os.path.basename(path))],
            title=(title or "Pinterest").strip()[:120],
            text=text[:900],
            uploader=uploader,
            platform=self.name,
        )

    # ── PNG delivery ──────────────────────────────────────────────────
    async def original_image(self, url: str, dest_dir: str = "") -> Tuple[str, int, int]:
        """Download the pin's highest-resolution still and return it as a PNG.

        Backs the "🖼 PNG file" button. Two things make this higher quality than
        the photo the bot normally sends:

        * Telegram re-encodes anything sent with ``send_photo`` to JPEG and caps
          the long side at 2560px. Sending the same pixels as a *document*
          bypasses both, so the user gets the untouched original resolution.
        * PNG is lossless, so no second generation of JPEG artefacts is added on
          top of whatever Pinterest already stored.

        Returns ``(png_path, width, height)``. Raises on failure — the caller
        turns that into a user-facing message.
        """
        url = await self._resolve(url)
        pin_id = self._pin_id(url)
        if not pin_id:
            raise ValueError("could not read the pin id from the link")

        data = await self._api_fetch(pin_id, url)
        image = self._best_image(data)
        if not image:
            raise ValueError("the pin has no still image")

        # Fetch to its native extension first; converting from the real bytes is
        # safer than trusting the URL's suffix.
        src_ext = "png" if ".png" in image.lower() else "jpg"
        src = self.temp_path(src_ext, prefix="pinorig")
        await download_file(image, src, referer=REFERER)

        from bot.utils.media_handler import to_png

        return await to_png(src)

    # ── last resort: scrape the page ──────────────────────────────────
    async def _scrape(self, url: str) -> DownloadResult:
        async with net.aclient(platform="pinterest", timeout=30.0, follow_redirects=True,
                                     headers={"User-Agent": DESKTOP_UA}) as c:
            r = await c.get(url)
            r.raise_for_status()
            html = r.text

        video = None
        vm = re.search(r"(https://v\d*\.pinimg\.com/videos/[^\"'\\ ]+?\.mp4)", html)
        if vm:
            video = vm.group(1).replace("\\/", "/")

        image = None
        for pat in (r"(https://i\.pinimg\.com/originals/[^\"'\\ ]+?\.(?:jpg|jpeg|png|gif))",
                    r'<meta property="og:image" content="([^"]+)"'):
            im = re.search(pat, html)
            if im:
                image = im.group(1).replace("\\/", "/")
                break

        title = ""
        tm = re.search(r"<title>(.*?)</title>", html, re.S)
        if tm:
            title = re.sub(r"\s+", " ", tm.group(1)).strip()
            title = re.split(r"\s*\|\s*", title)[0]

        if video:
            path = self.temp_path("mp4", prefix="pin")
            await download_file(video, path, referer=REFERER)
            kind = "video"
        elif image:
            ext = "png" if ".png" in image.lower() else "jpg"
            path = self.temp_path(ext, prefix="pin")
            await download_file(image, path, referer=REFERER)
            kind = "photo"
        else:
            raise ValueError("no media found on the pin page")

        return DownloadResult(
            items=[MediaItem(path=path, kind=kind,
                             filename=os.path.basename(path))],
            title=title[:120] or "Pinterest", platform=self.name,
        )

    # ── entry point ───────────────────────────────────────────────────
    async def download(self, url: str, quality: str = "best") -> DownloadResult:
        url = await self._resolve(url)

        async def attempt(_: int) -> DownloadResult:
            try:
                return await self._via_api(url)
            except Exception as exc:
                logger.info("pinterest API path failed (%s), trying yt-dlp", exc)
            try:
                return await super(PinterestDownloader, self).download(url, quality)
            except Exception as exc:
                logger.info("pinterest yt-dlp failed (%s), scraping", exc)
            return await self._scrape(url)

        return await with_retries(attempt, platform=self.name)
