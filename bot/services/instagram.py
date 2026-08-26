"""Instagram: reels, posts, carousels, stories, IGTV.

Live-testing notes that matter:
* sending a custom User-Agent makes Instagram return an empty media response,
  so the anti-block engine deliberately strips it for this platform
* yt-dlp handles single video posts well; multi-image carousels need the
  embed/GraphQL path, so both are attempted
"""
import asyncio
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import httpx

from bot.utils import net

from bot.services.base import DownloadResult, MediaItem, YtDlpDownloader
from bot.utils.antiblock import with_retries
from bot.utils.media_handler import download_file

logger = logging.getLogger(__name__)

SHORTCODE_RE = re.compile(r"instagram\.com/(?:[^/]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", re.I)
STORY_RE = re.compile(r"instagram\.com/stories/([A-Za-z0-9_.]+)(?:/(\d+))?", re.I)
# A bare profile URL: instagram.com/<username> with no post/reel/story path.
# The negative lookahead keeps reserved paths (p, reel, stories, ...) out.
PROFILE_RE = re.compile(
    r"instagram\.com/(?!p/|reel/|reels/|tv/|stories/|explore/|share/|accounts/|"
    r"about/|developer/|legal/|directory/)([A-Za-z0-9_.]+)/?(?:\?|$)", re.I,
)

# Instagram's public web app id — required header for the web API endpoints.
IG_APP_ID = "936619743392459"


class InstagramDownloader(YtDlpDownloader):
    name = "instagram"
    # Instagram publishes H.264/AAC only, so the highest rendition it offers is
    # already iOS-playable — no reason to cap it. Uncapped means a reel arrives
    # at full source quality instead of being sorted down to a 1080p ceiling.
    best_max_height = None
    patterns = [
        r"(?:^|\.|//)(?:www\.)?instagram\.com/(?:p|reel|reels|tv|stories)/",
        r"(?:^|\.|//)instagr\.am/",
        r"(?:^|\.|//)(?:www\.)?instagram\.com/share/",
        # Bare profile URL (profile-picture download). Checked last so post,
        # reel, and story links keep matching their own patterns first.
        r"(?:^|\.|//)(?:www\.)?instagram\.com/(?!p/|reel/|reels/|tv/|stories/|"
        r"explore/|share/|accounts/|about/|developer/|legal/|directory/)"
        r"[A-Za-z0-9_.]+/?(?:\?|$)",
    ]

    @staticmethod
    def shortcode(url: str) -> Optional[str]:
        m = SHORTCODE_RE.search(url)
        return m.group(1) if m else None

    @staticmethod
    def story_target(url: str) -> Optional[tuple]:
        """Return (username, story_id_or_None) for a /stories/ URL."""
        m = STORY_RE.search(url)
        return (m.group(1), m.group(2)) if m else None

    @staticmethod
    def profile_username(url: str) -> Optional[str]:
        m = PROFILE_RE.search(url)
        return m.group(1) if m else None

    # ── web API helpers ───────────────────────────────────────────────
    async def _web_profile_info(self, username: str) -> Dict[str, Any]:
        """Public profile JSON: profile_pic_url_hd, user id, name, bio.

        Uses the same endpoint instagram.com's own web app calls. A logged-in
        cookie (if the admin installed one) makes it far more reliable.
        """
        from bot.utils import cookies as cookie_jar

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/136.0.0.0 Safari/537.36",
            "X-IG-App-ID": IG_APP_ID,
            "Accept": "application/json",
            "Referer": f"https://www.instagram.com/{username}/",
        }
        ck = cookie_jar.cookie_header("instagram")
        if ck:
            headers["Cookie"] = ck
        url = ("https://www.instagram.com/api/v1/users/web_profile_info/"
               f"?username={username}")
        async with net.aclient(platform="instagram", timeout=30.0,
                               follow_redirects=True, headers=headers) as c:
            r = await c.get(url)
            r.raise_for_status()
            # The API returns JSON; a walled IP returns an HTML shell instead.
            ctype = r.headers.get("content-type", "")
            if "application/json" not in ctype:
                raise ValueError("instagram web API returned HTML (login/blocked)")
            return r.json()["data"]["user"]

    async def _download_profile_pic(self, username: str) -> DownloadResult:
        """Fetch a user's profile picture at the best resolution available.

        Resolution ladder (best first):
          1. web_profile_info via a free proxy -> profile_pic_url_hd (320px+,
             the real HD source). Our datacenter IP gets 429'd on this endpoint,
             but the image CDN itself is reachable, so we read the signed HD URL
             through a throwaway proxy and then download the image from our own
             IP.
          2. direct web_profile_info (works when the IP isn't currently limited)
          3. topsearch (always up, but only a 150px thumbnail)
          4. og:image scrape
        The 150px thumbnail is upscaled with Pillow as a last resort so it looks
        less pixelated on Telegram.
        """
        pic = ""
        full_name = ""
        upscale = False

        # 1. HD via free proxy (best quality).
        try:
            hd, name = await self._hd_via_proxy(username)
            if hd:
                pic, full_name = hd, name
        except Exception as exc:
            logger.info("ig HD-via-proxy failed for %s: %s", username, exc)

        # 2. direct web_profile_info (HD when the IP isn't rate-limited).
        if not pic:
            try:
                user = await self._web_profile_info(username)
                pic = user.get("profile_pic_url_hd") or user.get("profile_pic_url") or ""
                full_name = user.get("full_name") or ""
            except Exception as exc:
                logger.info("ig direct web API failed for %s (%s)", username, exc)

        # 3. topsearch / __a=1 / app API (topsearch is 150px -> upscale).
        if not pic:
            pic, full_name = await self._profile_pic_fallbacks(username)
            if pic and "s150x150" in pic:
                upscale = True

        # 4. og:image scrape.
        if not pic:
            pic = await self._scrape_profile_og(username)
        # 5. Anonymous IG viewer sites (no IG auth) as a last resort.
        if not pic:
            pic, nm = await self._pic_via_viewers(username)
            if pic and not full_name:
                full_name = nm
        if not pic:
            # Every IG-auth path failed. On this host that almost always means
            # the installed cookie is checkpointed (challenge_required) or the
            # egress IP is rate-limited — not that the profile is missing. Say so
            # so the owner knows to refresh the cookie via /cookies rather than
            # chasing a phantom bug.
            raise ValueError(
                "could not fetch this profile's picture — the Instagram login "
                "cookie looks expired or checkpointed. Refresh it with /cookies."
            )

        path = self.temp_path("jpg", prefix=f"ig_pfp_{username}")
        await download_file(pic, path, referer="https://www.instagram.com/")
        if not (os.path.exists(path) and os.path.getsize(path) > 0):
            raise ValueError("profile picture download failed")

        if upscale:
            self._upscale(path)

        return DownloadResult(
            items=[MediaItem(path=path, kind="photo",
                             filename=os.path.basename(path))],
            title=f"@{username}" + (f" — {full_name}" if full_name else ""),
            uploader=username, text="",
            platform=self.name, extra={"profile_pic": True},
        )

    async def _hd_via_proxy(self, username: str) -> tuple:
        """Read profile_pic_url_hd through a free proxy (our IP is 429'd here)."""
        from bot.utils import cookies as cookie_jar
        from bot.utils import proxypool

        ck = cookie_jar.cookie_header("instagram")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/136.0.0.0 Safari/537.36",
            "X-IG-App-ID": IG_APP_ID, "Accept": "*/*",
            "Referer": f"https://www.instagram.com/{username}/",
        }
        if ck:
            headers["Cookie"] = ck
        url = ("https://www.instagram.com/api/v1/users/web_profile_info/"
               f"?username={username}")
        data = await proxypool.fetch_json(url, headers, "profile_pic_url_hd")
        if not data:
            return "", ""
        u = (data.get("data") or {}).get("user") or {}
        return (u.get("profile_pic_url_hd") or u.get("profile_pic_url") or "",
                u.get("full_name") or "")

    @staticmethod
    def _upscale(path: str, factor: int = 3) -> None:
        """Enlarge a small thumbnail with a high-quality resample + mild sharpen.

        Not true HD, but makes a 150px avatar look far less blocky on Telegram.
        Silently no-ops if Pillow is unavailable or the image can't be read.
        """
        try:
            from PIL import Image, ImageFilter

            with Image.open(path) as im:
                im = im.convert("RGB")
                w, h = im.size
                big = im.resize((w * factor, h * factor), Image.LANCZOS)
                big = big.filter(ImageFilter.UnsharpMask(radius=2, percent=110, threshold=3))
                big.save(path, "JPEG", quality=92)
        except Exception as exc:
            logger.debug("profile-pic upscale skipped: %s", exc)

    async def _profile_pic_fallbacks(self, username: str) -> tuple:
        """Alternate JSON endpoints for the profile picture + display name."""
        from bot.utils import cookies as cookie_jar

        ck = cookie_jar.cookie_header("instagram")
        base_headers = {"X-IG-App-ID": IG_APP_ID, "Accept": "application/json"}
        if ck:
            base_headers["Cookie"] = ck

        # 1. Authenticated topsearch — the most reliable path from a datacenter
        #    IP. The web_profile_info endpoint 429s aggressively, but topsearch
        #    keeps working with a logged-in cookie and returns the pic + id.
        if ck:
            try:
                csrf = ""
                m = re.search(r"csrftoken=([^;]+)", ck)
                if m:
                    csrf = m.group(1)
                h = dict(base_headers)
                h["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/136.0.0.0 Safari/537.36")
                h["X-CSRFToken"] = csrf
                h["X-Requested-With"] = "XMLHttpRequest"
                h["Referer"] = f"https://www.instagram.com/{username}/"
                async with net.aclient(platform="instagram", timeout=30.0,
                                       follow_redirects=True, headers=h) as c:
                    r = await c.get("https://www.instagram.com/web/search/topsearch/"
                                    f"?context=blended&query={username}&count=5")
                if "application/json" in r.headers.get("content-type", ""):
                    for entry in (r.json().get("users") or []):
                        u = entry.get("user", {})
                        if (u.get("username") or "").lower() == username.lower():
                            pic = u.get("profile_pic_url") or ""
                            if pic:
                                return pic, u.get("full_name") or ""
            except Exception as exc:
                logger.debug("ig topsearch fallback failed: %s", exc)

        # 2. The legacy ?__a=1&__d=dis JSON view of the profile page.
        try:
            h = dict(base_headers)
            h["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/136.0.0.0 Safari/537.36")
            async with net.aclient(platform="instagram", timeout=30.0,
                                   follow_redirects=True, headers=h) as c:
                r = await c.get(f"https://www.instagram.com/{username}/?__a=1&__d=dis")
            if "application/json" in r.headers.get("content-type", ""):
                data = r.json()
                u = (data.get("graphql") or {}).get("user") or data.get("user") or {}
                pic = u.get("profile_pic_url_hd") or u.get("profile_pic_url") or ""
                if pic:
                    return pic, u.get("full_name") or ""
        except Exception as exc:
            logger.debug("ig ?__a=1 fallback failed: %s", exc)

        # 3. The mobile app API (different host, often not rate-limited together).
        try:
            h = dict(base_headers)
            h["User-Agent"] = "Instagram 219.0.0.12.117 Android"
            async with net.aclient(platform="instagram", timeout=30.0,
                                   follow_redirects=True, headers=h) as c:
                r = await c.get("https://i.instagram.com/api/v1/users/"
                                f"web_profile_info/?username={username}")
            if "application/json" in r.headers.get("content-type", ""):
                u = r.json()["data"]["user"]
                pic = u.get("profile_pic_url_hd") or u.get("profile_pic_url") or ""
                if pic:
                    return pic, u.get("full_name") or ""
        except Exception as exc:
            logger.debug("ig i.instagram fallback failed: %s", exc)

        return "", ""

    async def _scrape_profile_og(self, username: str) -> str:
        from bot.utils import cookies as cookie_jar

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) "
                                 "Chrome/136.0.0.0 Safari/537.36"}
        ck = cookie_jar.cookie_header("instagram")
        if ck:
            headers["Cookie"] = ck
        async with net.aclient(platform="instagram", timeout=30.0,
                               follow_redirects=True, headers=headers) as c:
            r = await c.get(f"https://www.instagram.com/{username}/")
            html = r.text
        m = (re.search(r'"profile_pic_url_hd":"(.*?)"', html)
             or re.search(r'"profile_pic_url":"(.*?)"', html)
             or re.search(r'<meta property="og:image" content="([^"]+)"', html))
        return _unescape(m.group(1)) if m else ""

    async def _pic_via_viewers(self, username: str) -> tuple:
        """Last resort: anonymous IG viewer mirrors that need no IG auth.

        These sites are flaky and rate-limit datacenter IPs, so each is wrapped
        in its own try/except and we take the first that yields a real CDN image
        URL. Returns (pic_url, full_name).
        """
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36")
        headers = {"User-Agent": ua, "Referer": "https://www.google.com/",
                   "Accept-Language": "en-US,en;q=0.9"}
        # (name, url, list-of-regexes) — regexes must capture a usable image URL.
        sources = [
            ("imginn", f"https://imginn.com/{username}/",
             [r'<img[^>]+class="[^"]*avatar[^"]*"[^>]+src="([^"]+)"',
              r'"profile_pic_url_hd":"([^"]+)"']),
            ("dumpor", f"https://dumpor.io/v/{username}",
             [r'(https://[^"\']*(?:cdninstagram|fbcdn|scontent)[^"\']*\.jpg[^"\']*)']),
        ]
        for name, url, pats in sources:
            try:
                async with net.aclient(platform="instagram", timeout=25.0,
                                       follow_redirects=True, headers=headers) as c:
                    r = await c.get(url)
                if r.status_code != 200:
                    continue
                for pat in pats:
                    m = re.search(pat, r.text)
                    if m:
                        pic = _unescape(m.group(1))
                        if "cdninstagram" in pic or "fbcdn" in pic or "scontent" in pic:
                            logger.info("ig pic via viewer %s for %s", name, username)
                            return pic, ""
            except Exception as exc:
                logger.debug("ig viewer %s failed for %s: %s", name, username, exc)
        return "", ""

    async def _download_story(self, url: str) -> DownloadResult:
        """Download a story item (or a user's active-story tray).

        Stories are almost always login-gated, so this leans on yt-dlp with the
        installed cookie jar first, then the web story-feed API. If no cookie is
        installed it raises a clear, translatable error.
        """
        target = self.story_target(url)
        if not target:
            raise ValueError("unrecognised story link")
        username, story_id = target

        # yt-dlp handles a specific story item well when cookies are present.
        try:
            return await super().download(url, "best")
        except Exception as exc:
            logger.info("ig story via yt-dlp failed (%s), trying web API", exc)

        # Web story-feed API: needs the numeric user id + a logged-in cookie.
        from bot.utils import cookies as cookie_jar
        ck = cookie_jar.cookie_header("instagram")
        if not ck:
            raise ValueError("STORY_NEEDS_COOKIE")

        user = await self._web_profile_info(username)
        uid = user.get("id") or user.get("pk")
        if not uid:
            raise ValueError("could not resolve the story owner")

        headers = {
            "User-Agent": "Instagram 219.0.0.12.117 Android",
            "X-IG-App-ID": IG_APP_ID,
            "Cookie": ck,
            "Accept": "application/json",
        }
        api = f"https://i.instagram.com/api/v1/feed/user/{uid}/story/"
        async with net.aclient(platform="instagram", timeout=30.0,
                               follow_redirects=True, headers=headers) as c:
            r = await c.get(api)
            r.raise_for_status()
            reel = (r.json() or {}).get("reel") or {}
        items_json = reel.get("items") or []
        if story_id:
            items_json = [it for it in items_json if str(it.get("pk")) == str(story_id)] or items_json

        media_items: List[MediaItem] = []
        tasks = []
        for i, it in enumerate(items_json[:15]):
            vids = it.get("video_versions") or []
            if vids:
                src = vids[0]["url"]
                path = self.temp_path("mp4", prefix=f"ig_story_{username}_{i}")
                kind = "video"
            else:
                cands = ((it.get("image_versions2") or {}).get("candidates") or [])
                if not cands:
                    continue
                src = cands[0]["url"]
                path = self.temp_path("jpg", prefix=f"ig_story_{username}_{i}")
                kind = "photo"
            media_items.append(MediaItem(path=path, url=src, kind=kind,
                                         filename=os.path.basename(path)))
            tasks.append(download_file(src, path, referer="https://www.instagram.com/"))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        good = [it for it, res in zip(media_items, results)
                if not isinstance(res, Exception)
                and os.path.exists(it.path) and os.path.getsize(it.path) > 0]
        if not good:
            raise ValueError("no active stories found for this user")
        return DownloadResult(
            items=good, title=f"@{username} stories", uploader=username,
            platform=self.name, extra={"story": True, "item_count": len(good)},
        )

    # ── carousel support via the embed page ───────────────────────────
    async def _embed_media(self, shortcode: str) -> Dict[str, Any]:
        """Scrape the public embed page for every item in a post.

        No login and no custom User-Agent: both make Instagram return nothing.
        """
        url = f"https://www.instagram.com/p/{shortcode}/embed/captioned/"
        headers = {}
        from bot.utils import cookies as cookie_jar

        ck = cookie_jar.cookie_header("instagram")
        if ck:
            headers["Cookie"] = ck
        async with net.aclient(platform="instagram", timeout=30.0,
                               follow_redirects=True, headers=headers or None) as c:
            r = await c.get(url)
            r.raise_for_status()
            html = r.text

        videos: List[str] = []
        photos: List[str] = []
        caption = ""
        username = ""

        # The embed page ships a JSON blob with the full media list.
        for m in re.finditer(r'\\"video_url\\":\\"(.*?)\\"', html):
            videos.append(_unescape(m.group(1)))
        for m in re.finditer(r'"video_url":"(.*?)"', html):
            videos.append(_unescape(m.group(1)))
        for m in re.finditer(r'\\"display_url\\":\\"(.*?)\\"', html):
            photos.append(_unescape(m.group(1)))
        for m in re.finditer(r'"display_url":"(.*?)"', html):
            photos.append(_unescape(m.group(1)))

        cm = re.search(r'"edge_media_to_caption":\{"edges":\[\{"node":\{"text":"(.*?)"\}', html)
        if cm:
            caption = _unescape(cm.group(1))
        if not caption:
            cm = re.search(r'class="Caption".*?>(.*?)</div>', html, re.S)
            if cm:
                caption = re.sub(r"<[^>]+>", "", cm.group(1)).strip()[:900]

        # Newer embed format: the main media is a plain <img class="EmbeddedMediaImage">
        # (or a <video>), not the old JSON blob. Use these when the JSON keys above
        # returned nothing, so single-image/carousel posts still work.
        if not videos:
            for m in re.finditer(r'<video[^>]+src="([^"]+)"', html):
                videos.append(_html_unescape(m.group(1)))
        if not photos:
            for m in re.finditer(r'class="EmbeddedMediaImage"[^>]*\ssrc="([^"]+)"', html):
                photos.append(_html_unescape(m.group(1)))
            # Some layouts put the class after src; try the reverse order too.
            if not photos:
                for m in re.finditer(r'<img[^>]+src="([^"]+)"[^>]*class="EmbeddedMediaImage"', html):
                    photos.append(_html_unescape(m.group(1)))
        um = re.search(r'"owner":\{"username":"(.*?)"', html) or \
            re.search(r'class="UsernameText">(.*?)<', html)
        if um:
            username = um.group(1)

        # De-duplicate while preserving order; a video's poster frame also shows
        # up as a display_url, so drop photos that pair with a video.
        videos = _dedup(videos)
        photos = _dedup(photos)
        if videos:
            photos = photos[len(videos):] if len(photos) > len(videos) else []

        return {"videos": videos, "photos": photos, "caption": caption,
                "username": username}

    async def get_info(self, url: str) -> DownloadResult:
        try:
            return await super().get_info(url)
        except Exception as exc:
            logger.info("instagram yt-dlp info failed (%s), trying embed", exc)
            sc = self.shortcode(url)
            if not sc:
                raise
            data = await self._embed_media(sc)
            count = len(data["videos"]) + len(data["photos"])
            if not count:
                raise
            return DownloadResult(
                title=(data["caption"] or "Instagram")[:120],
                uploader=data["username"], text=data["caption"],
                platform=self.name,
                extra={"item_count": count, "carousel": count > 1},
            )

    async def download(self, url: str, quality: str = "best") -> DownloadResult:
        # Story links: dedicated path (login-gated, needs cookie/web API).
        if self.story_target(url):
            return await self._download_story(url)

        sc = self.shortcode(url)

        # A bare profile URL with no post/story path -> profile picture.
        if not sc and not self.story_target(url):
            uname = self.profile_username(url)
            if uname:
                return await self._download_profile_pic(uname)

        # A carousel must go through the embed path to get every item.
        if sc:
            try:
                data = await self._embed_media(sc)
                total = len(data["videos"]) + len(data["photos"])
                if total > 1:
                    return await self._download_embed_items(sc, data)
            except Exception as exc:
                logger.debug("carousel probe failed: %s", exc)

        try:
            return await super().download(url, quality)
        except Exception as exc:
            logger.info("instagram yt-dlp download failed (%s), trying embed", exc)
            if not sc:
                raise
            data = await self._embed_media(sc)
            if not (data["videos"] or data["photos"]):
                raise
            return await self._download_embed_items(sc, data)

    async def _download_embed_items(self, shortcode: str, data: Dict[str, Any]) -> DownloadResult:
        items: List[MediaItem] = []
        tasks = []
        for i, v in enumerate(data["videos"]):
            path = self.temp_path("mp4", prefix=f"ig_{shortcode}_v{i}")
            items.append(MediaItem(path=path, url=v, kind="video",
                                   filename=os.path.basename(path)))
            tasks.append(download_file(v, path, referer="https://www.instagram.com/"))
        for i, p in enumerate(data["photos"]):
            path = self.temp_path("jpg", prefix=f"ig_{shortcode}_p{i}")
            items.append(MediaItem(path=path, url=p, kind="photo",
                                   filename=os.path.basename(path)))
            tasks.append(download_file(p, path, referer="https://www.instagram.com/"))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        good: List[MediaItem] = []
        for item, res in zip(items, results):
            if isinstance(res, Exception):
                logger.warning("instagram item failed: %s", res)
                continue
            if os.path.exists(item.path) and os.path.getsize(item.path) > 0:
                good.append(item)
        if not good:
            raise ValueError("Instagram returned no downloadable media")

        return DownloadResult(
            items=good,
            title=(data["caption"] or "Instagram")[:120],
            uploader=data["username"],
            text=data["caption"],
            platform=self.name,
            extra={"carousel": len(good) > 1, "item_count": len(good)},
        )


def _unescape(s: str) -> str:
    out = s.replace("\\/", "/").replace("\\u0026", "&").replace("\\\\", "\\")
    try:
        out = json.loads(f'"{out}"')
    except Exception:
        pass
    return out


def _html_unescape(s: str) -> str:
    """Decode HTML entities in a plain attribute value (e.g. &amp; -> &)."""
    import html as _html

    return _html.unescape(s)


def _dedup(seq: List[str]) -> List[str]:
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out
