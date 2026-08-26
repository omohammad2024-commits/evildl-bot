"""Google Play / APK downloader.

Google Play itself has no anonymous download API — the store only streams an APK
to a signed-in Android device. The practical, no-account route (the one every
public APK site uses) is APKCombo, which mirrors Play apps and hands out a
time-limited Cloudflare-R2 link per build. Verified reachable from this host's
egress IP directly (APKPure/APKMirror return 403 here; APKCombo returns 200).

Flow, all scraped, no key:
  1. A Google Play URL carries only the *package id* (``?id=com.foo``). APKCombo
     resolves a build purely from the package id — the URL slug is cosmetic, so
     any slug works (``/x/<pkg>/download/apk``).
  2. ``/checkin`` returns an ``fp=...&ip=...`` fingerprint token tied to our IP.
  3. The download page, fetched with that token, contains ``/r2?u=<signed>``
     links (one per variant: phone APK, or a multi-split ``.xapk`` bundle).
  4. ``https://apkcombo.com/r2?u=...`` 302-redirects to the real R2 object; a
     plain follow-redirects GET streams the file. Range requests work (206).

Delivered as a document (``.apk`` or ``.xapk``). ``.xapk`` is a zip of the base
APK plus split configs/OBB — Telegram hosts it fine and APK installers accept it.
"""
import logging
import os
import re
import urllib.parse
from typing import List, Optional, Tuple

from bot import config
from bot.services.base import DownloadResult, MediaItem, BaseDownloader
from bot.utils import net
from bot.utils.antiblock import with_retries
from bot.utils.media_handler import download_file, sanitize_filename

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_BASE = "https://apkcombo.com"

# A Play package id: dotted segments, lowercase-ish, e.g. com.whatsapp,
# org.telegram.messenger, com.king.candycrushsaga.
_PKG_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+")


class GooglePlayDownloader(BaseDownloader):
    name = "googleplay"
    patterns = [
        r"(?:^|\.|//)play\.google\.com/store/apps/details",
        r"(?:^|\.|//)play\.google\.com/store/apps/details\?.*id=",
        r"(?:^|\.|//)apkcombo\.com/",
        # Bare "market://details?id=" deep links.
        r"^market://details\?id=",
    ]

    # ── package resolution ────────────────────────────────────────────
    @staticmethod
    def _package_from_url(url: str) -> str:
        """Pull the app package id out of a Play / APKCombo / market URL."""
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("id"):
            cand = qs["id"][0]
            if _PKG_RE.fullmatch(cand):
                return cand
        if qs.get("package"):
            cand = qs["package"][0]
            if _PKG_RE.fullmatch(cand):
                return cand
        # APKCombo path form: /<slug>/<package>/... — the package is the segment
        # that looks like a dotted id.
        for seg in parsed.path.split("/"):
            if _PKG_RE.fullmatch(seg):
                return seg
        # Fragment form: /downloader/#package=com.foo
        frag = urllib.parse.parse_qs(parsed.fragment)
        if frag.get("package") and _PKG_RE.fullmatch(frag["package"][0]):
            return frag["package"][0]
        raise ValueError("no package id in URL")

    # ── apkcombo scraping ─────────────────────────────────────────────
    async def _checkin(self, client) -> str:
        """Fetch the per-IP fingerprint token the download page needs."""
        try:
            r = await client.get(f"{_BASE}/checkin",
                                  headers={"Referer": f"{_BASE}/"})
            token = r.text.strip()
            # Response is "fp=...&ip=..."; keep only well-formed tokens.
            if token.startswith("fp="):
                return token
        except Exception as exc:
            logger.debug("apkcombo checkin failed: %s", exc)
        return ""

    async def _variants(self, package: str) -> Tuple[str, List[Tuple[str, str, bool]]]:
        """Return (title, [(download_path, filename, is_xapk), ...]) for a package.

        The slug in the path is cosmetic — APKCombo resolves the build from the
        package id alone, so a fixed placeholder slug is used.
        """
        async with net.aclient(platform=self.name, follow_redirects=True,
                               headers={"User-Agent": _UA}) as client:
            token = await self._checkin(client)
            page_url = f"{_BASE}/a/{package}/download/apk"
            if token:
                page_url += f"?{token}"
            r = await client.get(
                page_url, headers={"Referer": f"{_BASE}/a/{package}/"})
            html = r.text

        title = ""
        m = re.search(r"<title>([^<]+)</title>", html)
        if m:
            raw = m.group(1).strip()
            # Page titles look like "AppName APK (Android App) - Free Download"
            # or "Download AppName APK - APKCombo"; strip the boilerplate both
            # ways round so the caption shows just the app name.
            raw = re.sub(r"\s+APK\b.*$", "", raw)
            raw = re.sub(r"^Download\s+", "", raw, flags=re.I)
            raw = re.sub(r"\s*[-|]\s*APKCombo.*$", "", raw, flags=re.I)
            title = raw.strip()

        variants: List[Tuple[str, str, bool]] = []
        seen: set = set()
        for path in re.findall(r'/r2\?u=[^"\'\s]+', html):
            if path in seen:
                continue
            seen.add(path)
            decoded = urllib.parse.unquote(path)
            fn_match = re.search(r'filename%3D%22([^%"]+)', path) or \
                re.search(r'filename="?([^"&]+)', decoded)
            filename = fn_match.group(1) if fn_match else f"{package}.apk"
            filename = sanitize_filename(filename)
            is_xapk = ".xapk" in filename.lower() or ".xapk" in decoded.lower()
            variants.append((path, filename, is_xapk))
        return title or package, variants

    @staticmethod
    def _prefer(variants: List[Tuple[str, str, bool]]) -> Tuple[str, str, bool]:
        """Pick the best single build: a plain APK beats a multi-split XAPK."""
        for path, fn, is_xapk in variants:
            if not is_xapk:
                return path, fn, is_xapk
        return variants[0]

    # ── contract ──────────────────────────────────────────────────────
    async def get_info(self, url: str) -> DownloadResult:
        package = self._package_from_url(url)

        async def attempt(_: int) -> DownloadResult:
            title, variants = await self._variants(package)
            if not variants:
                raise ValueError("no downloadable build found on APKCombo")
            _, filename, is_xapk = self._prefer(variants)
            return DownloadResult(
                title=title,
                uploader="Google Play",
                platform=self.name,
                extra={
                    "package": package,
                    "filename": filename,
                    "is_xapk": is_xapk,
                    "variant_count": len(variants),
                    "raw_file": True,
                },
            )

        return await with_retries(attempt, platform=self.name)

    async def download(self, url: str, quality: str = "best") -> DownloadResult:
        package = self._package_from_url(url)

        async def attempt(_: int) -> DownloadResult:
            title, variants = await self._variants(package)
            if not variants:
                raise ValueError("no downloadable build found on APKCombo")
            path, filename, is_xapk = self._prefer(variants)
            ext = "xapk" if is_xapk else "apk"
            dest = self.temp_path(ext, prefix="apk")
            await download_file(
                f"{_BASE}{path}", dest,
                referer=f"{_BASE}/a/{package}/download/apk",
                headers={"User-Agent": _UA},
                max_size=config.MAX_DOWNLOAD,
            )
            # A valid APK/XAPK is a zip: it must start with the PK signature.
            # A captcha/HTML page would not, so this catches a silent block.
            if not self._looks_like_zip(dest):
                os.remove(dest) if os.path.exists(dest) else None
                raise ValueError("APKCombo returned a non-APK payload")
            return DownloadResult(
                items=[MediaItem(path=dest, url=url, kind="document",
                                 filename=filename)],
                title=title,
                uploader="Google Play",
                platform=self.name,
                extra={"package": package, "is_xapk": is_xapk, "raw_file": True},
            )

        return await with_retries(attempt, platform=self.name)

    @staticmethod
    def _looks_like_zip(path: str) -> bool:
        try:
            with open(path, "rb") as fh:
                return fh.read(2) == b"PK"
        except OSError:
            return False
