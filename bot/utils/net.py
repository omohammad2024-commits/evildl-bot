"""Central HTTP client factory so every request honours the configured proxy.

Before this existed, ``config.PROXY`` only reached yt-dlp; the httpx-based
services (Instagram, Twitter, TikTok, Pinterest, Spotify) and the file
downloader all opened their own un-proxied clients. A residential proxy is the
one lever that unblocks YouTube AND Instagram from a flagged datacenter IP, so
it must apply to every outbound request, not just yt-dlp.

Usage: replace ``httpx.AsyncClient(...)`` with ``net.aclient(...)`` and
``httpx.Client(...)`` with ``net.client(...)``. Both inject the proxy unless a
call explicitly passes ``proxy=``.

Per-platform proxying is supported: set PROXY for everything, or
PROXY_YOUTUBE / PROXY_INSTAGRAM / ... to route only certain platforms through
the (usually paid) proxy while everything else stays direct.
"""
from typing import Optional

import httpx

from bot import config


def proxy_for(platform: str = "") -> str:
    """Resolve the proxy URL for a platform, falling back to the global one."""
    if platform:
        specific = getattr(config, f"PROXY_{platform.upper()}", "")
        if specific:
            return specific
    return config.PROXY or ""


def _merge(kwargs: dict, platform: str) -> dict:
    # An explicit proxy in the call always wins; otherwise inject ours.
    # httpx 0.25.x (installed here) uses the ``proxies=`` kwarg, not ``proxy=``.
    if "proxy" not in kwargs and "proxies" not in kwargs and "mounts" not in kwargs:
        p = proxy_for(platform)
        if p:
            kwargs["proxies"] = p
    return kwargs


def aclient(*, platform: str = "", **kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(**_merge(kwargs, platform))


def client(*, platform: str = "", **kwargs) -> httpx.Client:
    return httpx.Client(**_merge(kwargs, platform))
