"""Direct link fallback: anything the other services do not claim.

Two paths: let yt-dlp try (it supports well over a thousand sites, so a link
that is not one of our 12 named platforms often still works), and if that fails
download the raw file over HTTP.
"""
import logging
import os
from typing import Optional
from urllib.parse import unquote, urlparse

from bot import config
from bot.services.base import DownloadResult, MediaItem, YtDlpDownloader
from bot.utils.antiblock import with_retries
from bot.utils.media_handler import (
    download_file,
    guess_kind,
    head_info,
    sanitize_filename,
)

logger = logging.getLogger(__name__)

KNOWN_EXTS = {
    "mp4", "mkv", "avi", "mov", "webm", "m4v", "flv", "ts",
    "mp3", "m4a", "aac", "ogg", "opus", "wav", "flac",
    "jpg", "jpeg", "png", "webp", "gif", "bmp",
    "pdf", "zip", "rar", "7z", "apk", "exe", "dmg", "iso",
    "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "epub",
}


class DirectLinkDownloader(YtDlpDownloader):
    name = "direct"
    patterns = [r"^https?://"]

    # ── metadata ──────────────────────────────────────────────────────
    async def get_info(self, url: str) -> DownloadResult:
        # A raw file extension means there is nothing for yt-dlp to extract.
        if not self._looks_like_file(url):
            try:
                return await super().get_info(url)
            except Exception as exc:
                logger.info("direct yt-dlp info failed (%s), using HEAD", exc)

        meta = await head_info(url)
        name = meta.get("filename") or self._name_from_url(url)
        return DownloadResult(
            title=name or "file",
            platform=self.name,
            extra={
                "filesize": meta.get("size", 0),
                "content_type": meta.get("content_type", ""),
                "filename": name,
                "raw_file": True,
            },
        )

    async def download(self, url: str, quality: str = "best") -> DownloadResult:
        if not self._looks_like_file(url):
            try:
                return await super().download(url, quality)
            except Exception as exc:
                logger.info("direct yt-dlp download failed (%s), raw HTTP", exc)

        async def attempt(_: int) -> DownloadResult:
            meta = await head_info(url)
            size = int(meta.get("size") or 0)
            if size and size > config.MAX_DOWNLOAD:
                raise ValueError(
                    f"file is {size // 1024 // 1024}MB, above the "
                    f"{config.MAX_DOWNLOAD // 1024 // 1024}MB ceiling"
                )

            name = meta.get("filename") or self._name_from_url(url) or "file"
            ext = os.path.splitext(name)[1].lstrip(".").lower()
            if not ext:
                ext = self._ext_from_content_type(str(meta.get("content_type", ""))) or "bin"
                name = f"{name}.{ext}"

            path = self.temp_path(ext, prefix="dl")
            await download_file(
                str(meta.get("final_url") or url), path,
                max_size=config.MAX_DOWNLOAD,
            )
            kind = guess_kind(path, str(meta.get("content_type", "")))
            return DownloadResult(
                items=[MediaItem(path=path, url=url, kind=kind, filename=name)],
                title=name, platform=self.name,
                extra={"content_type": meta.get("content_type", ""), "raw_file": True},
            )

        return await with_retries(attempt, platform=self.name)

    # ── helpers ───────────────────────────────────────────────────────
    @staticmethod
    def _looks_like_file(url: str) -> bool:
        path = urlparse(url).path.lower()
        ext = os.path.splitext(path)[1].lstrip(".")
        return ext in KNOWN_EXTS

    @staticmethod
    def _name_from_url(url: str) -> str:
        base = os.path.basename(urlparse(url).path)
        return sanitize_filename(unquote(base)) if base else ""

    @staticmethod
    def _ext_from_content_type(ct: str) -> Optional[str]:
        mapping = {
            "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov",
            "video/x-matroska": "mkv",
            "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/ogg": "ogg",
            "audio/wav": "wav", "audio/flac": "flac",
            "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif",
            "image/webp": "webp",
            "application/pdf": "pdf", "application/zip": "zip",
            "application/x-rar-compressed": "rar",
            "application/vnd.android.package-archive": "apk",
        }
        return mapping.get(ct.split(";")[0].strip().lower())
