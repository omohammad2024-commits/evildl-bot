"""Dashboard API routes.

Registered onto the FastAPI app by ``web.app`` so it can reuse the auth
dependency. Everything talks to the same singleton ``db`` the bot uses (shared
SQLite file, WAL mode, so concurrent readers are fine) and the same ``settings``
table the bot reads for runtime toggles, so a change here takes effect in the
bot without a restart.

Actions that need Telegram (broadcast, channel verification) build a transient
``telegram.Bot`` from the shared token; they never import the running bot.
"""
import asyncio
import io
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List

from fastapi import Depends, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from bot import config
from bot.database import db

logger = logging.getLogger("web.api")

_START = time.time()


def _uptime() -> str:
    s = int(time.time() - _START)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def register(app: FastAPI, require_auth) -> None:
    """Attach all API routes. ``require_auth`` is the session dependency."""
    auth = Depends(require_auth)

    # ── overview ──────────────────────────────────────────────────────
    @app.get("/api/overview")
    async def overview(_: bool = auth) -> Dict[str, Any]:
        from bot.utils.media_handler import human_size
        from bot.utils.queue import free_bytes, queue

        cache = await db.cache_stats()
        q = queue
        return {
            "users": await db.get_total_users(),
            "new_today": await db.get_new_users(24),
            "active_24h": await db.get_active_users(24),
            "downloads_total": await db.get_total_downloads(),
            "downloads_today": await db.get_today_downloads(),
            "downloads_week": await db.get_week_downloads(),
            "failed_total": await db.get_failed_downloads(),
            "total_bytes": human_size(await db.get_total_bytes()),
            "cache_rows": cache.get("rows", 0),
            "cache_hits": cache.get("hits", 0),
            "disk_free": human_size(free_bytes()),
            "running": getattr(q, "active", 0),
            "waiting": getattr(q, "waiting", 0),
            "uptime": _uptime(),
            "bot_username": config.BOT_USERNAME or "",
            "tunnel_url": _tunnel_url(),
        }

    # ── charts ────────────────────────────────────────────────────────
    @app.get("/api/charts")
    async def charts(_: bool = auth) -> Dict[str, Any]:
        daily = await db.get_daily_downloads(14)
        newusers = await db.get_daily_new_users(14)
        peak = await db.get_peak_hours()
        platforms = await db.get_top_platforms(10)
        return {
            "daily": daily,
            "new_users": newusers,
            "peak_hours": peak,
            "platforms": platforms,
        }

    # ── services / health ─────────────────────────────────────────────
    @app.get("/api/services")
    async def services(_: bool = auth) -> Dict[str, Any]:
        from bot.utils import health

        svc = await health.all_services()
        cookies = health.cookie_status()
        return {"services": svc, "cookies": cookies}

    @app.get("/api/platform-health")
    async def platform_health(_: bool = auth) -> Dict[str, Any]:
        rows = await db.get_platform_health()
        return {"platforms": rows}

    # ── users ─────────────────────────────────────────────────────────
    @app.get("/api/users")
    async def users(_: bool = auth, limit: int = 25) -> Dict[str, Any]:
        top = await db.get_top_users(min(limit, 100))
        return {"users": top}

    @app.get("/api/user/{user_id}")
    async def user_detail(user_id: int, _: bool = auth) -> Dict[str, Any]:
        row = await db.get_user(user_id)
        if not row:
            raise HTTPException(status_code=404, detail="user not found")
        st = await db.get_personal_stats(user_id)
        recent = await db.get_user_recent(user_id, 10)
        banned = await db.is_banned(user_id)
        return {"user": row, "stats": {k: v for k, v in st.items() if k != "byday"},
                "recent": recent, "banned": banned,
                "referrals": await db.count_referrals(user_id)}

    @app.post("/api/user/{user_id}/ban")
    async def ban(user_id: int, request: Request, _: bool = auth):
        body = await _json(request)
        reason = (body.get("reason") or "via dashboard")[:200]
        hours = int(body.get("hours") or 0)
        if hours > 0:
            await db.ban_user_until(user_id, int(time.time()) + hours * 3600, reason, 0)
        else:
            await db.ban_user(user_id, reason, 0)
        return {"ok": True, "banned": True}

    @app.post("/api/user/{user_id}/unban")
    async def unban(user_id: int, _: bool = auth):
        await db.unban_user(user_id)
        return {"ok": True, "banned": False}

    @app.get("/api/banlist")
    async def banlist(_: bool = auth) -> Dict[str, Any]:
        return {"banned": await db.get_banned_users(100)}

    # ── platform on/off ───────────────────────────────────────────────
    @app.get("/api/platforms")
    async def platforms(_: bool = auth) -> Dict[str, Any]:
        out = []
        for p in config.ALL_PLATFORMS:
            enabled = await db.get_setting(f"pf_enabled_{p}", "1") != "0"
            out.append({"platform": p, "enabled": enabled})
        return {"platforms": out}

    @app.post("/api/platforms/{platform}/toggle")
    async def toggle_platform(platform: str, _: bool = auth):
        if platform not in config.ALL_PLATFORMS:
            raise HTTPException(status_code=404, detail="unknown platform")
        key = f"pf_enabled_{platform}"
        current = await db.get_setting(key, "1") != "0"
        await db.set_setting(key, "0" if current else "1")
        return {"ok": True, "platform": platform, "enabled": not current}

    # ── limits (read-only + live edit of soft caps via settings) ───────
    @app.get("/api/limits")
    async def limits(_: bool = auth) -> Dict[str, Any]:
        return {
            "concurrent": config.MAX_CONCURRENT,
            "per_user": config.PER_USER_CONCURRENT,
            "soft_hourly": config.SOFT_HOURLY,
            "maintenance": await db.get_setting("maintenance", "0") == "1",
        }

    @app.post("/api/maintenance/toggle")
    async def maintenance_toggle(_: bool = auth):
        current = await db.get_setting("maintenance", "0") == "1"
        await db.set_setting("maintenance", "0" if current else "1")
        return {"ok": True, "maintenance": not current}

    # ── cookies ───────────────────────────────────────────────────────
    @app.get("/api/cookies")
    async def cookies_status(_: bool = auth) -> Dict[str, Any]:
        from bot.utils import cookies as ck

        return {"cookies": ck.status()}

    @app.post("/api/cookies/{platform}")
    async def cookies_upload(platform: str, _: bool = auth, file: UploadFile = None):
        from bot.utils import cookies as ck

        if platform not in ck.KNOWN_PLATFORMS:
            raise HTTPException(status_code=404, detail="unknown platform")
        if file is None:
            raise HTTPException(status_code=400, detail="no file")
        raw = (await file.read()).decode("utf-8", errors="replace")
        ok, reason, details = ck.save(raw, platform)
        if not ok:
            return JSONResponse({"ok": False, "error": reason, "details": _clean(details)},
                                status_code=400)
        return {"ok": True, "details": _clean(details)}

    @app.delete("/api/cookies/{platform}")
    async def cookies_delete(platform: str, _: bool = auth):
        from bot.utils import cookies as ck

        if platform not in ck.KNOWN_PLATFORMS:
            raise HTTPException(status_code=404, detail="unknown platform")
        return {"ok": ck.remove(platform)}

    # ── forced channels (gate) ────────────────────────────────────────
    @app.get("/api/channels")
    async def channels(_: bool = auth) -> Dict[str, Any]:
        from bot.utils import gate

        return {"channels": await gate.channels(),
                "enabled": await gate.is_enabled()}

    @app.post("/api/channels")
    async def channels_add(request: Request, _: bool = auth):
        from bot.utils import gate

        body = await _json(request)
        target = (body.get("channel") or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="channel required")
        bot = await _get_bot()
        try:
            async with bot:
                usable, entry, problem = await gate.verify_channel(bot, target)
        finally:
            pass
        if not usable:
            return JSONResponse({"ok": False, "error": problem, "entry": entry},
                                status_code=400)
        items = await gate.channels()
        if any(str(c.get("id")) == str(entry.get("id")) for c in items):
            return JSONResponse({"ok": False, "error": "already added"}, status_code=400)
        items.append(entry)
        await gate.save_channels(items)
        return {"ok": True, "entry": entry}

    @app.delete("/api/channels/{channel_id}")
    async def channels_remove(channel_id: str, _: bool = auth):
        from bot.utils import gate

        items = [c for c in await gate.channels() if str(c.get("id")) != str(channel_id)]
        await gate.save_channels(items)
        return {"ok": True, "remaining": len(items)}

    # ── broadcast ─────────────────────────────────────────────────────
    @app.post("/api/broadcast")
    async def broadcast(request: Request, _: bool = auth):
        body = await _json(request)
        text = (body.get("text") or "").strip()
        audience = body.get("audience") or "all"
        if not text:
            raise HTTPException(status_code=400, detail="text required")
        if audience == "active":
            user_ids = await db.get_active_user_ids(168)
        else:
            user_ids = await db.get_all_user_ids()

        # Fire the broadcast in the background so the request returns at once.
        asyncio.create_task(_do_broadcast(text, user_ids))
        return {"ok": True, "queued": len(user_ids)}

    # ── logs ──────────────────────────────────────────────────────────
    @app.get("/api/logs")
    async def logs(_: bool = auth, lines: int = 120):
        path = os.path.join(config.BASE_DIR, "logs", "bot.out")
        if not os.path.exists(path):
            path = config.LOG_PATH
        tail = _tail(path, min(lines, 500))
        return {"lines": tail}

    # ── control ───────────────────────────────────────────────────────
    @app.post("/api/restart-bot")
    async def restart_bot(_: bool = auth):
        """Ask the bot to restart by touching a flag file the supervisor watches.

        We do NOT kill the bot from here (different process tree). Instead we
        drop a sentinel the bot's own maintenance loop picks up, or the admin
        can use the Telegram panel. As a reliable fallback we run run.sh restart.
        """
        script = os.path.join(config.BASE_DIR, "run.sh")
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", script, "restart",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
            await asyncio.wait_for(proc.communicate(), timeout=30)
            return {"ok": True}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=500)

    @app.post("/api/cache/clear")
    async def cache_clear(_: bool = auth):
        n = await db.clear_cache()
        return {"ok": True, "cleared": n}

    @app.post("/api/cleanup")
    async def cleanup(_: bool = auth):
        from bot.utils.queue import sweep_temp, trim_logs

        await asyncio.to_thread(sweep_temp, True)
        await asyncio.to_thread(trim_logs)
        report = await db.maintain(aggressive=True)
        return {"ok": True, "report": {k: report[k] for k in
                ("downloads_pruned", "cache_pruned")}}

    @app.get("/api/export/users")
    async def export_users(_: bool = auth):
        csv = await db.export_users_csv()
        buf = io.BytesIO(csv.encode("utf-8"))
        return StreamingResponse(
            buf, media_type="text/csv",
            headers={"Content-Disposition":
                     f"attachment; filename=users_{datetime.now():%Y%m%d}.csv"})


# ── helpers ───────────────────────────────────────────────────────────
async def _json(request: Request) -> Dict[str, Any]:
    try:
        return await request.json()
    except Exception:
        return {}


def _clean(details: Dict[str, Any]) -> Dict[str, Any]:
    """Strip anything sensitive/bulky before returning cookie details."""
    return {k: v for k, v in (details or {}).items() if k in
            ("count", "days_left", "soonest_expiry")}


def _tail(path: str, n: int) -> List[str]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            block = min(size, n * 400)
            fh.seek(size - block)
            data = fh.read().decode("utf-8", errors="replace")
        return data.splitlines()[-n:]
    except Exception:
        return []


async def _get_bot():
    from telegram import Bot

    return Bot(config.BOT_TOKEN)


def _tunnel_url() -> str:
    """The current public tunnel URL, written by tunnel.sh. Empty if no tunnel."""
    try:
        with open(os.path.join(config.BASE_DIR, "run", "tunnel_url.txt"), encoding="utf-8") as fh:
            return fh.read().strip()
    except Exception:
        return ""


async def _do_broadcast(text: str, user_ids: List[int]) -> None:
    """Send a plain-text broadcast to a list of users, slowly, logging results."""
    bot = await _get_bot()
    sent = failed = 0
    async with bot:
        for i, uid in enumerate(user_ids, 1):
            try:
                await bot.send_message(uid, text)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05 if i % 25 else 1.0)
    try:
        await db.log_broadcast(0, text, sent, failed)
    except Exception:
        pass
    logger.info("dashboard broadcast done: sent=%s failed=%s", sent, failed)
