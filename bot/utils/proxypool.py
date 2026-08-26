"""A tiny rotating pool of free public HTTP proxies.

Why this exists: Instagram's metadata endpoints (web_profile_info, users/info)
hard-rate-limit this server's datacenter IP with 429s, so we can't read the HD
profile-picture URL directly. But the image CDN itself is NOT blocked from our
IP. So the trick is: make the small JSON metadata request through a throwaway
free proxy, extract the signed HD URL, then download the actual image from our
own IP (fast, unproxied).

Free proxies are individually unreliable and short-lived, so we fetch a large
list, race many at once, and cache whichever one just worked for a short while.
This is best-effort: if nothing works we return None and the caller falls back
to the low-res thumbnail.
"""
import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from bot.utils import net

logger = logging.getLogger(__name__)

_SOURCES = [
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
]
_IPRE = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5})")

# module-level caches
_proxy_list: List[str] = []
_proxy_list_ts: float = 0.0
_good_proxy: str = ""          # last proxy that returned valid JSON
_good_proxy_ts: float = 0.0

_LIST_TTL = 1800   # refresh the raw list every 30 min
_GOOD_TTL = 1800   # trust a known-good proxy for 30 min (was 10) — a proxy that
                   # just served IG metadata usually keeps working, and reusing
                   # it turns a ~seconds-long race into a single instant request.

# Remember a few recently-good proxies, not just one, so if the single best one
# has died we try the runners-up before falling back to the full race.
_good_proxies: List[str] = []
_GOOD_KEEP = 5


async def _fetch_list() -> List[str]:
    global _proxy_list, _proxy_list_ts
    if _proxy_list and (time.time() - _proxy_list_ts) < _LIST_TTL:
        return _proxy_list
    found: List[str] = []
    seen = set()
    for src in _SOURCES:
        try:
            async with net.aclient(timeout=15.0) as c:
                r = await c.get(src)
            for m in _IPRE.finditer(r.text):
                p = m.group(1)
                if p not in seen:
                    seen.add(p)
                    found.append(p)
        except Exception as exc:
            logger.debug("proxy source failed %s: %s", src, exc)
    if found:
        _proxy_list = found
        _proxy_list_ts = time.time()
    return _proxy_list


async def _try_one(proxy: str, url: str, headers: Dict[str, str],
                   want_key: str, sem: asyncio.Semaphore) -> Optional[Dict[str, Any]]:
    async with sem:
        try:
            async with httpx.AsyncClient(proxies=f"http://{proxy}", timeout=10.0,
                                         follow_redirects=True) as c:
                r = await c.get(url, headers=headers)
            if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
                data = r.json()
                # cheap sanity check: the wanted key must appear somewhere.
                if want_key in r.text:
                    global _good_proxy, _good_proxy_ts
                    _good_proxy = proxy
                    _good_proxy_ts = time.time()
                    # promote to the front of the recently-good list
                    if proxy in _good_proxies:
                        _good_proxies.remove(proxy)
                    _good_proxies.insert(0, proxy)
                    del _good_proxies[_GOOD_KEEP:]
                    return data
        except Exception:
            return None
    return None


async def fetch_json(url: str, headers: Dict[str, str], want_key: str,
                     *, max_proxies: int = 800, concurrency: int = 100,
                     deadline: float = 45.0) -> Optional[Dict[str, Any]]:
    """Return parsed JSON from ``url`` fetched through some working free proxy.

    ``want_key`` is a substring that must be present in a valid response body
    (used to reject error/placeholder JSON). Returns None if nothing works
    within ``deadline`` seconds.
    """
    # 1. Reuse recently-good proxies first — usually instant. Try the whole
    #    short list concurrently so one dead entry doesn't add latency.
    fresh = _good_proxies if (time.time() - _good_proxy_ts) < _GOOD_TTL else []
    if fresh:
        sem = asyncio.Semaphore(len(fresh))
        tasks = [asyncio.create_task(_try_one(p, url, headers, want_key, sem))
                 for p in fresh]
        for coro in asyncio.as_completed(tasks):
            res = await coro
            if res is not None:
                for t in tasks:
                    t.cancel()
                return res

    proxies = await _fetch_list()
    if not proxies:
        return None

    sem = asyncio.Semaphore(concurrency)
    t0 = time.time()
    # Race in batches; stop at the first proxy that returns valid JSON.
    batch = concurrency * 3
    for i in range(0, min(len(proxies), max_proxies), batch):
        if time.time() - t0 > deadline:
            break
        chunk = proxies[i:i + batch]
        tasks = [asyncio.create_task(_try_one(p, url, headers, want_key, sem))
                 for p in chunk]
        try:
            for coro in asyncio.as_completed(tasks, timeout=max(1.0, deadline - (time.time() - t0))):
                res = await coro
                if res is not None:
                    for t in tasks:
                        t.cancel()
                    return res
        except (asyncio.TimeoutError, asyncio.CancelledError):
            for t in tasks:
                t.cancel()
            break
    return None
