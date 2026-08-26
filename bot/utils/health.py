"""Live service-health probes for the admin panel.

Each probe answers a single question the admin actually cares about — "is the
thing that keeps this bot working still up?" — and returns a small dict the
panel renders with a 🟢/🟡/🔴 badge. Everything is best-effort and never raises:
a probe that can't run reports 🔴 with a short reason rather than blowing up the
panel.
"""
import asyncio
import logging
import os
import time
from typing import Any, Dict, List

from bot import config

logger = logging.getLogger(__name__)


async def _probe_pot() -> Dict[str, Any]:
    """The bgutil PO-token server — required for reliable YouTube."""
    url = config.POT_SERVER.rstrip("/") + "/ping"
    try:
        from bot.utils import net

        async with net.aclient(timeout=5.0) as c:
            r = await c.get(url)
        ok = r.status_code < 500
        return {"name": "POT server", "ok": ok, "icon": "🟢" if ok else "🔴",
                "detail": f"HTTP {r.status_code}"}
    except Exception as exc:
        return {"name": "POT server", "ok": False, "icon": "🔴",
                "detail": type(exc).__name__}


async def _probe_mtproto() -> Dict[str, Any]:
    """MTProto (Telethon) — the 2GB upload path."""
    if not config.USE_MTPROTO:
        return {"name": "MTProto", "ok": False, "icon": "🟡",
                "detail": "disabled"}
    try:
        from bot.utils import sender

        client = await sender._get_mtproto()
        ok = client is not None and client.is_connected()
        return {"name": "MTProto", "ok": ok, "icon": "🟢" if ok else "🔴",
                "detail": "2GB uploads" if ok else "not connected"}
    except Exception as exc:
        return {"name": "MTProto", "ok": False, "icon": "🔴",
                "detail": type(exc).__name__}


async def _probe_node() -> Dict[str, Any]:
    """Node runtime — yt-dlp needs it to solve YouTube's JS challenges."""
    path = config.NODE_PATH
    ok = bool(path) and os.path.exists(path) and os.access(path, os.X_OK)
    return {"name": "Node.js", "ok": ok, "icon": "🟢" if ok else "🔴",
            "detail": os.path.basename(path) if ok else "missing"}


def _probe_disk() -> Dict[str, Any]:
    from bot.utils.queue import free_bytes
    from bot.utils.media_handler import human_size

    free = free_bytes()
    if free < config.DISK_PANIC:
        icon, ok = "🔴", False
    elif free < config.MIN_FREE_DISK:
        icon, ok = "🟡", True
    else:
        icon, ok = "🟢", True
    return {"name": "Disk", "ok": ok, "icon": icon, "detail": human_size(free)}


def _probe_proxy() -> Dict[str, Any]:
    """Whether an outbound proxy is configured (not whether it's healthy)."""
    proxies = [p for p in (config.PROXY, config.PROXY_YOUTUBE,
                           config.PROXY_INSTAGRAM) if p]
    if proxies:
        return {"name": "Proxy", "ok": True, "icon": "🟢",
                "detail": f"{len(proxies)} configured"}
    return {"name": "Proxy", "ok": True, "icon": "🟡", "detail": "none (direct)"}


async def all_services() -> List[Dict[str, Any]]:
    """Run every probe concurrently and return their results."""
    async_probes = [_probe_pot(), _probe_mtproto(), _probe_node()]
    results = await asyncio.gather(*async_probes, return_exceptions=True)
    out: List[Dict[str, Any]] = []
    for r in results:
        if isinstance(r, dict):
            out.append(r)
        else:
            out.append({"name": "?", "ok": False, "icon": "🔴",
                        "detail": type(r).__name__})
    out.append(_probe_disk())
    out.append(_probe_proxy())
    return out


def cookie_status() -> List[Dict[str, Any]]:
    """Cookie expiry per platform, with a colour by days remaining."""
    from bot.utils import cookies

    raw = cookies.status()
    out: List[Dict[str, Any]] = []
    for platform in config.COOKIE_PLATFORMS:
        info = raw.get(platform, {})
        if not info.get("present"):
            out.append({"platform": platform, "present": False, "icon": "⚪️",
                        "detail": "not installed"})
            continue
        if not info.get("ok"):
            out.append({"platform": platform, "present": True, "icon": "🔴",
                        "detail": str(info.get("reason", "invalid"))})
            continue
        days = int(info.get("days_left", 0))
        if days <= 0:
            icon = "🔴"
        elif days <= 3:
            icon = "🟠"
        elif days <= 7:
            icon = "🟡"
        else:
            icon = "🟢"
        out.append({"platform": platform, "present": True, "icon": icon,
                    "days_left": days, "count": info.get("count", 0),
                    "detail": f"{days}d left"})
    return out
