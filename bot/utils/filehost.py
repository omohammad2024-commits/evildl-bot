"""External file-host upload for files past Telegram's 50MB Bot API cap.

Telegram's only in-app ways past 50MB (MTProto, local Bot API server) both need
api_id/api_hash, which we don't have. This module needs no credentials: it
uploads the oversized file to a temporary public host and returns a direct link,
so the user always gets their file even without MTProto.

Hosts are tried in order and the first success wins. Each was verified live from
this server (2026-08): gofile.io stores the file and echoes back the exact byte
size; litterbox/0x0.st were unreliable from this IP and sit lower in the chain.
"""
import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

from bot import config

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"


@dataclass
class HostedFile:
    url: str          # page the user opens
    direct: str       # direct-download link when the host exposes one
    host: str
    size: int
    expires: str = ""  # human note about lifetime, "" = permanent-ish


async def _gofile(path: str, size: int) -> Optional[HostedFile]:
    """gofile.io — free, unlimited size, no account. Verified working."""
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True,
                                 headers={"User-Agent": UA}) as c:
        srv = await c.get("https://api.gofile.io/servers")
        srv.raise_for_status()
        servers = srv.json().get("data", {}).get("servers") or []
        if not servers:
            raise RuntimeError("gofile returned no servers")
        server = servers[0]["name"]

    # Long timeout: a large file over a slow uplink can take minutes.
    async with httpx.AsyncClient(timeout=None, follow_redirects=True,
                                 headers={"User-Agent": UA}) as c:
        with open(path, "rb") as fh:
            r = await c.post(
                f"https://{server}.gofile.io/contents/uploadfile",
                files={"file": (os.path.basename(path), fh, "application/octet-stream")},
            )
        r.raise_for_status()
        data = r.json().get("data", {})

    reported = int(data.get("size") or 0)
    if reported and abs(reported - size) > 4096:
        # A mismatch means the upload was truncated; treat as failure.
        raise RuntimeError(f"gofile size mismatch: got {reported}, sent {size}")
    page = data.get("downloadPage")
    if not page:
        raise RuntimeError("gofile returned no download page")
    return HostedFile(url=page, direct=data.get("directLink") or "",
                      host="gofile.io", size=size)


async def _litterbox(path: str, size: int) -> Optional[HostedFile]:
    """litterbox (catbox) — up to 1GB, temporary. Fallback only."""
    async with httpx.AsyncClient(timeout=None, follow_redirects=True,
                                 headers={"User-Agent": UA}) as c:
        with open(path, "rb") as fh:
            r = await c.post(
                "https://litterbox.catbox.moe/resources/internals/api.php",
                data={"reqtype": "fileupload", "time": "72h"},
                files={"fileToUpload": (os.path.basename(path), fh)},
            )
        r.raise_for_status()
        link = r.text.strip()
        if not link.startswith("http"):
            raise RuntimeError(f"litterbox: {link[:80]}")
    return HostedFile(url=link, direct=link, host="litterbox",
                      size=size, expires="72h")


async def _zerox(path: str, size: int) -> Optional[HostedFile]:
    """0x0.st — up to 512MB. Fallback only."""
    async with httpx.AsyncClient(timeout=None, follow_redirects=True,
                                 headers={"User-Agent": "curl/8.5.0"}) as c:
        with open(path, "rb") as fh:
            r = await c.post("https://0x0.st",
                             files={"file": (os.path.basename(path), fh)})
        r.raise_for_status()
        link = r.text.strip()
        if not link.startswith("http"):
            raise RuntimeError(f"0x0.st: {link[:80]}")
    return HostedFile(url=link, direct=link, host="0x0.st",
                      size=size, expires="~1 year")


# Order matters: most reliable first.
_HOSTS = [("gofile", _gofile), ("litterbox", _litterbox), ("0x0", _zerox)]


async def upload(path: str) -> Optional[HostedFile]:
    """Upload an oversized file to the first host that accepts it.

    Returns None only when every host fails, so the caller can fall back to
    splitting the file into Bot-API-sized parts.
    """
    size = os.path.getsize(path)
    for name, fn in _HOSTS:
        try:
            result = await fn(path, size)
            if result:
                logger.info("hosted %s (%.1fMB) on %s", os.path.basename(path),
                            size / 1024 / 1024, result.host)
                return result
        except Exception as exc:
            logger.warning("file host %s failed: %s", name, str(exc)[:120])
    logger.error("all file hosts failed for %s", path)
    return None
