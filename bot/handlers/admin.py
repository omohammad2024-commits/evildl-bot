"""Admin panel.

Two rules govern everything here:

* non-admins get **silence**, never "you are not an admin" — that would reveal
  that the command exists. Attempts are logged server-side only.
* every command is also reachable from the inline panel, so the panel is a full
  replacement for typing commands.
"""
import io
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot import config
from bot.database import db
from bot.i18n import get_text, themed
from bot.keyboards.inline import (
    admin_back_keyboard,
    admin_keyboard,
    cache_keyboard,
    confirm_keyboard,
)
from bot.utils.antiblock import circuit
from bot.utils.media_handler import human_size
from bot.utils.queue import free_bytes, queue
from bot.utils.url_parser import platform_label

logger = logging.getLogger(__name__)

BOT_STARTED_AT = time.time()


# ── small rendering helpers ───────────────────────────────────────────
_BAR_FULL = "▰"
_BAR_EMPTY = "▱"


def _bar(value: int, peak: int, width: int = 10) -> str:
    """A compact text progress bar scaled to ``peak``."""
    if peak <= 0:
        return _BAR_EMPTY * width
    filled = round(value / peak * width)
    filled = max(0, min(width, filled))
    return _BAR_FULL * filled + _BAR_EMPTY * (width - filled)


def platform_enabled(platform: str, cache: dict) -> bool:
    """Read a platform's on/off flag from a pre-fetched settings cache."""
    return cache.get(f"pf_enabled_{platform}", "1") != "0"


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def _guard(update: Update) -> Optional[str]:
    """Return the admin's language, or None when the caller is not an admin.

    A non-admin gets no reply at all: from their side the command does not exist.
    Owner commands are also refused in groups — the panel lists users, the
    message inbox and bot-wide settings, none of which should be printed into a
    shared chat even for the owner. Silent there too, so the admin surface stays
    invisible to ordinary members.
    """
    user = update.effective_user
    if user is None or not is_admin(user.id):
        if user is not None:
            logger.info("Blocked admin command from non-admin %s (%s)",
                        user.id, update.effective_message.text if update.effective_message else "")
        return None
    chat = update.effective_chat
    if chat is not None and chat.type in ("group", "supergroup"):
        logger.info("Refused owner command in group %s", chat.id)
        return None
    return await db.get_user_language(user.id)


def _uptime() -> str:
    seconds = int(time.time() - BOT_STARTED_AT)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


# ── panel ─────────────────────────────────────────────────────────────
async def panel_text(lang: str) -> str:
    cache = await db.cache_stats()
    # A quick one-line service pulse in the header, so the admin sees trouble
    # without opening the services page.
    try:
        from bot.utils import health

        svc = await health.all_services()
        pulse = " ".join(s["icon"] for s in svc)
    except Exception:
        pulse = "—"
    return await themed(
        "ADMIN_PANEL", lang,
        users=await db.get_total_users(),
        new_today=await db.get_new_users(24),
        downloads=await db.get_total_downloads(),
        today=await db.get_today_downloads(),
        active=await db.get_active_users(24),
        cache_rows=cache.get("rows", 0),
        cache_hits=cache.get("hits", 0),
        running=queue.active,
        waiting=queue.waiting,
        disk=human_size(free_bytes()),
        uptime=_uptime(),
        pulse=pulse,
    )


async def do_restart(update: Update, lang: str) -> None:
    """Exit the process cleanly; the supervisor relaunches it.

    We reply first, then schedule the exit a moment later so the message
    actually goes out before the process dies.
    """
    import asyncio

    await update.effective_message.reply_text(get_text("RESTART_NOW", lang))

    async def _bye():
        await asyncio.sleep(1.5)
        logger.warning("admin-triggered restart: exiting for supervisor relaunch")
        os._exit(0)

    asyncio.create_task(_bye())


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    maintenance = await db.get_setting("maintenance", "0") == "1"
    await update.effective_message.reply_text(
        await panel_text(lang), parse_mode=ParseMode.HTML,
        reply_markup=admin_keyboard(lang, maintenance),
    )


# ── statistics ────────────────────────────────────────────────────────
async def stats_text(lang: str) -> str:
    total = await db.get_total_downloads()
    failed = await db.get_failed_downloads()
    attempts = total + failed
    rate = round(total / attempts * 100, 1) if attempts else 100.0
    top = await db.get_top_platforms(1)
    top_users = await db.get_top_users(1)
    top_name = ""
    if top_users:
        u = top_users[0]
        top_name = (u.get("first_name") or u.get("username") or str(u.get("user_id")))
        top_name = f"{top_name} ({u.get('total_downloads', 0)})"

    lines = [
        get_text("STATS_TITLE", lang), "",
        get_text("STATS_USERS", lang, count=await db.get_total_users()),
        get_text("STATS_DOWNLOADS", lang, count=total),
        get_text("STATS_TODAY", lang, count=await db.get_today_downloads()),
        get_text("STATS_WEEK", lang, count=await db.get_week_downloads()),
        get_text("STATS_FAILED", lang, count=failed),
        get_text("STATS_SUCCESS_RATE", lang, rate=rate),
        get_text("STATS_TRAFFIC", lang, size=human_size(await db.get_total_bytes())),
        get_text("STATS_TOP", lang,
                 platform=platform_label(top[0]["platform"]) if top else "—"),
        get_text("STATS_ACTIVE", lang, name=top_name or "—"),
    ]
    return "\n".join(lines)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    await update.effective_message.reply_text(
        await stats_text(lang), parse_mode=ParseMode.HTML,
        reply_markup=admin_back_keyboard(lang),
    )


async def top_text(lang: str) -> str:
    rows = await db.get_top_platforms(10)
    if not rows:
        return get_text("HEALTH_EMPTY", lang)
    lines = [get_text("TOP_TITLE", lang), ""]
    for i, row in enumerate(rows, 1):
        lines.append(get_text("TOP_FORMAT", lang, rank=i,
                              platform=platform_label(row["platform"]),
                              count=row["count"]))
    return "\n".join(lines)


async def top_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    await update.effective_message.reply_text(
        await top_text(lang), parse_mode=ParseMode.HTML,
        reply_markup=admin_back_keyboard(lang),
    )


async def health_text(lang: str) -> str:
    """Per-platform success ratio — this is what tells you what is broken."""
    rows = await db.get_platform_health()
    if not rows:
        return get_text("HEALTH_TITLE", lang) + "\n\n" + get_text("HEALTH_EMPTY", lang)
    lines = [get_text("HEALTH_TITLE", lang), ""]
    for row in rows:
        ok = row.get("ok") or 0
        bad = row.get("bad") or 0
        total = ok + bad
        rate = round(ok / total * 100) if total else 0
        emoji = "🟢" if rate >= 90 else ("🟡" if rate >= 60 else "🔴")
        lines.append(get_text("HEALTH_FORMAT", lang, emoji=emoji,
                              platform=platform_label(row["platform"]),
                              rate=rate, ok=ok, total=total))
        cooldown = circuit.cooldown_remaining(row["platform"])
        if cooldown:
            lines.append(f"    ⏸ cooling down {cooldown}s")
    return "\n".join(lines)


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    await update.effective_message.reply_text(
        await health_text(lang), parse_mode=ParseMode.HTML,
        reply_markup=admin_back_keyboard(lang),
    )


async def errors_text(lang: str) -> str:
    from html import escape

    rows = await db.get_recent_errors(10)
    if not rows:
        return get_text("ERRORS_TITLE", lang) + "\n\n" + get_text("ERRORS_EMPTY", lang)
    lines = [get_text("ERRORS_TITLE", lang), ""]
    for row in rows:
        when = str(row.get("timestamp") or "")[5:16]
        lines.append(
            f"{platform_label(row.get('platform') or '')} <code>{when}</code>\n"
            f"  {escape((row.get('error') or '')[:120])}"
        )
    return "\n".join(lines)


async def errors_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    await update.effective_message.reply_text(
        await errors_text(lang), parse_mode=ParseMode.HTML,
        reply_markup=admin_back_keyboard(lang),
    )


async def users_text(lang: str) -> str:
    from html import escape

    rows = await db.get_top_users(10)
    lines = ["👥 <b>Top users</b>", ""]
    for i, u in enumerate(rows, 1):
        name = escape(u.get("first_name") or u.get("username") or "")
        lines.append(f"{i}. {name} — <code>{u['user_id']}</code> · "
                     f"{u.get('total_downloads', 0)}")
    return "\n".join(lines) if rows else "—"


# ── cache ─────────────────────────────────────────────────────────────
async def cache_text(lang: str) -> str:
    stats = await db.cache_stats()
    saved = (stats.get("bytes") or 0) * max(0, stats.get("hits") or 0)
    return get_text("CACHE_STATS", lang, rows=stats.get("rows", 0),
                    hits=stats.get("hits", 0), saved=human_size(saved))


async def cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    args = context.args or []
    if args and args[0].lower() in {"clear", "clean", "reset"}:
        count = await db.clear_cache()
        await update.effective_message.reply_text(
            get_text("CACHE_CLEARED", lang, count=count)
        )
        return
    await update.effective_message.reply_text(
        await cache_text(lang), parse_mode=ParseMode.HTML,
        reply_markup=cache_keyboard(lang),
    )


# ── storage ───────────────────────────────────────────────────────────
async def storage_text(lang: str) -> str:
    """What the bot is actually holding on disk, and where."""
    from bot.utils.queue import temp_usage

    rep = await db.storage_report()
    free = free_bytes()
    lines = [
        "💾 <b>وضعیت فضا</b>" if lang == "fa" else "💾 <b>Storage</b>", "",
        f"🗄 دیتابیس: <b>{human_size(rep.get('bytes', 0))}</b>" if lang == "fa"
        else f"🗄 Database: <b>{human_size(rep.get('bytes', 0))}</b>",
        f"📂 فایل‌های موقت: <b>{human_size(temp_usage())}</b>" if lang == "fa"
        else f"📂 Temp files: <b>{human_size(temp_usage())}</b>",
        f"💿 دیسک آزاد: <b>{human_size(free)}</b>" if lang == "fa"
        else f"💿 Free disk: <b>{human_size(free)}</b>",
        "",
        f"👥 users: {rep.get('users', 0)}",
        f"⬇️ downloads (rolling {config.DB_RETENTION_DAYS}d): {rep.get('downloads', 0)}",
        f"⚡️ file_cache: {rep.get('file_cache', 0)} / {config.CACHE_MAX_ROWS}",
        f"🚫 banned: {rep.get('banned_users', 0)}",
    ]
    if free < config.DISK_PANIC:
        lines.append("")
        lines.append("⚠️ فضا کم است!" if lang == "fa" else "⚠️ Disk space is low!")
    return "\n".join(lines)


async def storage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    args = context.args or []
    if args and args[0].lower() in {"clean", "prune", "vacuum"}:
        from bot.utils.queue import sweep_temp, trim_logs

        sweep_temp(force=True)
        trim_logs()
        report = await db.maintain(aggressive=True)
        saved = report["bytes_before"] - report["bytes_after"]
        await update.effective_message.reply_text(
            f"🗑 پاک‌سازی انجام شد\n\n"
            f"-{report['downloads_pruned']} رکورد لاگ\n"
            f"-{report['cache_pruned']} رکورد کش\n"
            f"💾 آزاد شد: {human_size(max(0, saved))}",
            parse_mode=ParseMode.HTML,
        )
        return
    await update.effective_message.reply_text(
        await storage_text(lang), parse_mode=ParseMode.HTML,
        reply_markup=admin_back_keyboard(lang),
    )


# ── user lookup / bans ────────────────────────────────────────────────
async def user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    args = context.args or []
    if not args or not args[0].strip().lstrip("-").isdigit():
        await update.effective_message.reply_text(get_text("INVALID_ID", lang))
        return
    await _show_user(update, int(args[0]), lang)


async def _show_user(update: Update, user_id: int, lang: str) -> None:
    from html import escape

    row = await db.get_user(user_id)
    if not row:
        await update.effective_message.reply_text(get_text("USER_NOT_FOUND", lang))
        return
    banned = await db.is_banned(user_id)
    await update.effective_message.reply_text(
        get_text("USER_LOOKUP", lang,
                 id=user_id,
                 name=escape(row.get("first_name") or "—"),
                 username=f"@{escape(row['username'])}" if row.get("username") else "",
                 ulang=row.get("language") or "—",
                 downloads=row.get("total_downloads") or 0,
                 joined=str(row.get("join_date") or "")[:16],
                 last=str(row.get("last_active") or "")[:16],
                 banned="✅" if banned else "❌"),
        parse_mode=ParseMode.HTML, reply_markup=admin_back_keyboard(lang),
    )


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    args = context.args or []
    if not args:
        context.user_data["await_ban"] = True
        await update.effective_message.reply_text(get_text("BAN_PROMPT", lang))
        return
    await _do_ban(update, context, args[0], " ".join(args[1:]), lang)


async def _do_ban(update, context, raw_id: str, reason: str, lang: str) -> None:
    if not raw_id.strip().lstrip("-").isdigit():
        await update.effective_message.reply_text(get_text("INVALID_ID", lang))
        return
    target = int(raw_id)
    if is_admin(target):
        await update.effective_message.reply_text(get_text("BAN_SELF", lang))
        return
    await db.ban_user(target, reason, update.effective_user.id)
    await update.effective_message.reply_text(
        get_text("BAN_SUCCESS", lang, id=target), parse_mode=ParseMode.HTML
    )
    # Tell the banned user once, so they are not left guessing.
    try:
        target_lang = await db.get_user_language(target)
        await context.bot.send_message(target, get_text("BANNED_MESSAGE", target_lang))
    except Exception:
        pass


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    args = context.args or []
    if not args:
        context.user_data["await_unban"] = True
        await update.effective_message.reply_text(get_text("UNBAN_PROMPT", lang))
        return
    await _do_unban(update, args[0], lang)


async def _do_unban(update, raw_id: str, lang: str) -> None:
    if not raw_id.strip().lstrip("-").isdigit():
        await update.effective_message.reply_text(get_text("INVALID_ID", lang))
        return
    target = int(raw_id)
    await db.unban_user(target)
    await update.effective_message.reply_text(
        get_text("UNBAN_SUCCESS", lang, id=target), parse_mode=ParseMode.HTML
    )


async def banlist_text(lang: str) -> str:
    rows = await db.get_banned_users(30)
    if not rows:
        return get_text("BAN_LIST_TITLE", lang) + "\n\n" + get_text("BAN_LIST_EMPTY", lang)
    lines = [get_text("BAN_LIST_TITLE", lang), ""]
    for r in rows:
        reason = (r.get("reason") or "—")[:40]
        lines.append(f"<code>{r['user_id']}</code> — {reason}")
    return "\n".join(lines)


async def banlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    await update.effective_message.reply_text(
        await banlist_text(lang), parse_mode=ParseMode.HTML,
        reply_markup=admin_back_keyboard(lang),
    )


# ── broadcast ─────────────────────────────────────────────────────────
async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    context.user_data["await_broadcast"] = True
    await update.effective_message.reply_text(get_text("BROADCAST_PROMPT", lang))


async def run_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Copy the stored message to every user, with progress and a report."""
    lang = await db.get_user_language(update.effective_user.id)
    stored = context.user_data.get("broadcast_msg") or {}
    chat_id = stored.get("chat_id")
    message_id = stored.get("message_id")
    if not chat_id or not message_id:
        await update.effective_message.reply_text(get_text("BROADCAST_CANCELLED", lang))
        return

    user_ids = await db.get_all_user_ids()
    total = len(user_ids)
    progress = await update.effective_message.reply_text(
        get_text("BROADCAST_SENDING", lang, current=0, total=total)
    )

    sent = failed = 0
    import asyncio

    for i, uid in enumerate(user_ids, 1):
        try:
            # copy_message preserves media without re-uploading it.
            await context.bot.copy_message(chat_id=uid, from_chat_id=chat_id,
                                           message_id=message_id)
            sent += 1
        except Exception:
            failed += 1
        # Telegram tolerates roughly 30 messages/second; stay well under.
        if i % 25 == 0:
            try:
                await progress.edit_text(
                    get_text("BROADCAST_SENDING", lang, current=i, total=total)
                )
            except Exception:
                pass
            await asyncio.sleep(1)
        else:
            await asyncio.sleep(0.05)

    await db.log_broadcast(update.effective_user.id, str(stored.get("text", "")), sent, failed)
    context.user_data.pop("broadcast_msg", None)
    try:
        await progress.edit_text(
            get_text("BROADCAST_SENT", lang, count=sent) +
            (("\n" + get_text("BROADCAST_FAILED", lang, count=failed)) if failed else "")
        )
    except Exception:
        pass


# ── settings ──────────────────────────────────────────────────────────
async def setwelcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    text = " ".join(context.args or []).strip()
    if not text:
        context.user_data["await_welcome"] = True
        await update.effective_message.reply_text(
            "📝 " + get_text("SETTINGS_TITLE", lang), parse_mode=ParseMode.HTML
        )
        return
    await db.set_welcome_message(text)
    await update.effective_message.reply_text(get_text("WELCOME_SET", lang))


async def setchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manage the forced-channel gate.

    Every add is verified against Telegram before being stored, so a broken
    setup (bot not promoted to admin) is reported immediately instead of
    silently letting everyone through.

        /setchannel                 show status
        /setchannel @name           add a channel
        /setchannel -@name          remove a channel
        /setchannel off             disable the gate entirely
    """
    lang = await _guard(update)
    if lang is None:
        return

    from bot.utils import gate

    msg = update.effective_message
    args = context.args or []

    if not args:
        await msg.reply_text(await _gate_status_text(context.bot, lang),
                             parse_mode=ParseMode.HTML)
        return

    value = args[0].strip()

    if value.lower() in {"off", "none", "0", "-", "disable"}:
        await gate.save_channels([])
        await msg.reply_text(get_text("CHANNEL_OFF", lang))
        return

    # Removal.
    if value.startswith("-") and len(value) > 1:
        target = value[1:].lstrip("@").lower()
        items = await gate.channels()
        kept = [
            c for c in items
            if target not in {
                str(c.get("id", "")).lower(),
                str(c.get("handle", "")).lstrip("@").lower(),
            }
        ]
        if len(kept) == len(items):
            await msg.reply_text(get_text("CHANNEL_NOT_FOUND", lang, channel=value[1:]))
            return
        await gate.save_channels(kept)
        await msg.reply_text(get_text("CHANNEL_REMOVED", lang, channel=value[1:]))
        return

    # Addition — verified before storing.
    target = value if value.startswith(("@", "-100")) else "@" + value.lstrip("@")
    usable, entry, problem = await gate.verify_channel(context.bot, target)

    if not usable:
        await msg.reply_text(_gate_problem_text(problem, target, lang),
                             parse_mode=ParseMode.HTML)
        return

    items = await gate.channels()
    if any(str(c.get("id")) == entry["id"] for c in items):
        await msg.reply_text(get_text("CHANNEL_EXISTS", lang,
                                      channel=gate.channel_label(entry)))
        return

    items.append(entry)
    await gate.save_channels(items)
    await msg.reply_text(
        get_text("CHANNEL_SET", lang, channel=gate.channel_label(entry)),
        parse_mode=ParseMode.HTML,
    )


def _gate_problem_text(problem: str, target: str, lang: str) -> str:
    """Turn a verification failure into an actionable instruction."""
    kind = problem.split(":", 1)[0]
    detail = problem.split(":", 1)[1] if ":" in problem else ""
    if kind == "not_found":
        return get_text("CHANNEL_ERR_NOT_FOUND", lang, channel=target)
    if kind in {"cannot_read_members", "not_admin"}:
        return get_text("CHANNEL_ERR_NOT_ADMIN", lang, channel=target)
    if kind == "no_invite_link":
        return get_text("CHANNEL_ERR_NO_INVITE", lang, channel=target)
    return get_text("CHANNEL_ERR_GENERIC", lang, channel=target,
                    error=detail[:120] or kind)


async def _gate_status_text(bot, lang: str) -> str:
    """Current gate state, including whether it is actually enforcing."""
    from bot.utils import gate

    items = await gate.channels()
    if not items:
        return get_text("CHANNEL_STATUS_OFF", lang)

    lines = [get_text("CHANNEL_STATUS_TITLE", lang), ""]
    for entry in items:
        usable, _fresh, problem = await gate.verify_channel(bot, entry["id"])
        icon = "🟢" if usable else "🔴"
        lines.append(f"{icon} <b>{gate.channel_label(entry)}</b>")
        if entry.get("handle", "").startswith("@"):
            lines.append(f"   {entry['handle']}")
        if not usable:
            lines.append("   " + get_text("CHANNEL_STATUS_BROKEN", lang))
    lines.append("")
    lines.append(get_text("CHANNEL_STATUS_HELP", lang))
    return "\n".join(lines)


async def maintenance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    await _toggle_maintenance(update, lang)


async def _toggle_maintenance(update: Update, lang: str) -> bool:
    current = await db.get_setting("maintenance", "0") == "1"
    new = not current
    await db.set_setting("maintenance", "1" if new else "0")
    await update.effective_message.reply_text(
        get_text("MAINTENANCE_ON" if new else "MAINTENANCE_OFF", lang)
    )
    return new


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await _guard(update)
    if lang is None:
        return
    await send_export(update, lang)


async def send_export(update: Update, lang: str) -> None:
    csv = await db.export_users_csv()
    count = max(0, csv.count("\n"))
    buf = io.BytesIO(csv.encode("utf-8"))
    buf.name = f"users_{datetime.now():%Y%m%d_%H%M}.csv"
    await update.effective_message.reply_document(
        document=buf, filename=buf.name,
        caption=get_text("EXPORT_READY", lang, count=count),
    )


# ── analytics: charts ─────────────────────────────────────────────────
async def charts_text(lang: str) -> str:
    """7-day download + signup bar charts, and peak hours."""
    dl = await db.get_daily_downloads(7)
    nu = await db.get_daily_new_users(7)
    hours = await db.get_peak_hours()

    dl_peak = max((d["count"] for d in dl), default=0)
    nu_peak = max((d["count"] for d in nu), default=0)

    lines = [get_text("CHART_TITLE", lang), ""]
    lines.append(get_text("CHART_DOWNLOADS", lang))
    for d in dl:
        lines.append(f"<code>{d['label']} {_bar(d['count'], dl_peak)}</code> {d['count']}")
    lines.append("")
    lines.append(get_text("CHART_NEWUSERS", lang))
    for d in nu:
        lines.append(f"<code>{d['label']} {_bar(d['count'], nu_peak)}</code> {d['count']}")

    # Peak hours: show the 3 busiest hours only, to stay compact.
    busy = sorted(hours, key=lambda x: x["count"], reverse=True)[:3]
    busy = [h for h in busy if h["count"] > 0]
    if busy:
        lines.append("")
        lines.append(get_text("CHART_PEAK", lang))
        for h in busy:
            lines.append(f"  🕐 {h['hour']:02d}:00 — <b>{h['count']}</b>")
    return "\n".join(lines)


# ── service health ────────────────────────────────────────────────────
async def services_text(lang: str) -> str:
    from bot.utils import health

    services = await health.all_services()
    cookies_ = health.cookie_status()

    lines = [get_text("SERVICES_TITLE", lang), ""]
    for s in services:
        lines.append(f"{s['icon']} <b>{s['name']}</b> — {s['detail']}")
    lines.append("")
    lines.append(get_text("SERVICES_COOKIES", lang))
    for c in cookies_:
        name = platform_label(c["platform"])
        lines.append(f"{c['icon']} {name} — {c['detail']}")
    return "\n".join(lines)


# ── platform on/off control ───────────────────────────────────────────
async def platforms_status(lang: str) -> str:
    cache = {}
    for p in config.ALL_PLATFORMS:
        cache[f"pf_enabled_{p}"] = await db.get_setting(f"pf_enabled_{p}", "1")
    lines = [get_text("PLATFORMS_TITLE", lang), ""]
    for p in config.ALL_PLATFORMS:
        on = platform_enabled(p, cache)
        icon = "🟢" if on else "🔴"
        state = get_text("PLATFORM_ON", lang) if on else get_text("PLATFORM_OFF", lang)
        lines.append(f"{icon} {platform_label(p)} — {state}")
    lines.append("")
    lines.append(get_text("PLATFORMS_HELP", lang))
    return "\n".join(lines)


async def toggle_platform(platform: str) -> bool:
    """Flip a platform's enabled flag; returns the new state (True=on)."""
    key = f"pf_enabled_{platform}"
    current = await db.get_setting(key, "1") != "0"
    new = not current
    await db.set_setting(key, "1" if new else "0")
    return new


# ── limits control ────────────────────────────────────────────────────
async def limits_text(lang: str) -> str:
    # concurrent / per_user are fixed at process start (the queue builds its
    # semaphores from config), so show the REAL live values, not unwritten
    # settings. Only the soft hourly cap is live-editable at runtime.
    concurrent = config.MAX_CONCURRENT
    per_user = config.PER_USER_CONCURRENT
    soft_raw = await db.get_setting("lim_soft_hourly", "")
    soft = soft_raw if (soft_raw and soft_raw.isdigit()) else str(config.SOFT_HOURLY)
    return get_text("LIMITS_TITLE", lang) + "\n\n" + get_text(
        "LIMITS_BODY", lang, concurrent=concurrent, per_user=per_user, soft=soft)


async def adjust_soft_hourly(delta: int) -> int:
    """Nudge the live soft hourly cap and persist it. Returns the new value."""
    cur_raw = await db.get_setting("lim_soft_hourly", "")
    cur = int(cur_raw) if (cur_raw and cur_raw.isdigit()) else config.SOFT_HOURLY
    new = max(0, cur + delta)
    await db.set_setting("lim_soft_hourly", str(new))
    return new


# ── targeted broadcast ────────────────────────────────────────────────
async def run_broadcast_targeted(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                 audience: str) -> None:
    """Broadcast to a subset of users. ``audience`` is 'all' or 'active'."""
    lang = await db.get_user_language(update.effective_user.id)
    stored = context.user_data.get("broadcast_msg") or {}
    chat_id = stored.get("chat_id")
    message_id = stored.get("message_id")
    if not chat_id or not message_id:
        await update.effective_message.reply_text(get_text("BROADCAST_CANCELLED", lang))
        return

    if audience == "active":
        user_ids = await db.get_active_user_ids(168)
    else:
        user_ids = await db.get_all_user_ids()
    total = len(user_ids)
    progress = await update.effective_message.reply_text(
        get_text("BROADCAST_SENDING", lang, current=0, total=total)
    )

    import asyncio

    sent = failed = 0
    for i, uid in enumerate(user_ids, 1):
        try:
            await context.bot.copy_message(chat_id=uid, from_chat_id=chat_id,
                                           message_id=message_id)
            sent += 1
        except Exception:
            failed += 1
        if i % 25 == 0:
            try:
                await progress.edit_text(
                    get_text("BROADCAST_SENDING", lang, current=i, total=total))
            except Exception:
                pass
            await asyncio.sleep(1)
        else:
            await asyncio.sleep(0.05)

    await db.log_broadcast(update.effective_user.id, str(stored.get("text", "")), sent, failed)
    context.user_data.pop("broadcast_msg", None)
    try:
        await progress.edit_text(
            get_text("BROADCAST_SENT", lang, count=sent) +
            (("\n" + get_text("BROADCAST_FAILED", lang, count=failed)) if failed else "")
        )
    except Exception:
        pass


# ── database backup ───────────────────────────────────────────────────
async def send_db_backup(update: Update, lang: str) -> None:
    """Send the SQLite database file as a document."""
    import shutil
    import tempfile

    src = config.DB_PATH
    try:
        # Copy first so a concurrent write can't corrupt the sent file.
        tmp = os.path.join(tempfile.gettempdir(),
                           f"bot_backup_{datetime.now():%Y%m%d_%H%M}.db")
        await __import__("asyncio").to_thread(shutil.copy2, src, tmp)
        with open(tmp, "rb") as fh:
            buf = io.BytesIO(fh.read())
        buf.name = os.path.basename(tmp)
        await update.effective_message.reply_document(
            document=buf, filename=buf.name,
            caption=get_text("BACKUP_READY", lang,
                             size=human_size(os.path.getsize(tmp))),
        )
        try:
            os.remove(tmp)
        except OSError:
            pass
    except Exception as exc:
        logger.error("db backup failed: %s", exc)
        await update.effective_message.reply_text(
            get_text("BACKUP_FAILED", lang, error=str(exc)[:100]))


# ── theme / UI personalization ────────────────────────────────────────
async def pack_from_message(message) -> List[str]:
    """Pack names the owner just sent: addemoji links, names, or a premium emoji.

    Sending one premium emoji is the easiest path — its ``set_name`` identifies
    the whole pack, so the owner can forward an emoji instead of typing a link.
    """
    text = (message.text or message.caption or "").strip()
    names: List[str] = []

    # 1. t.me/addemoji/<name> links (any number, in one message).
    for match in re.finditer(r"(?:t\.me|telegram\.me)/addemoji/([A-Za-z0-9_]+)", text):
        names.append(match.group(1))

    # 2. Premium emoji in the message: resolve their pack via the Bot API.
    ids = []
    # PTB 20.7 exposes entities as TUPLES; mixing tuple + list raises TypeError.
    for ent in list(message.entities or ()) + list(message.caption_entities or ()):
        if getattr(ent, "type", "") == "custom_emoji" and getattr(ent, "custom_emoji_id", ""):
            ids.append(ent.custom_emoji_id)
    if ids:
        try:
            stickers = await message.get_bot().get_custom_emoji_stickers(ids[:20])
            for sticker in stickers:
                name = getattr(sticker, "set_name", "") or ""
                if name:
                    names.append(name)
        except Exception as exc:
            logger.warning("could not resolve emoji pack: %s", exc)

    # 3. Bare pack names, e.g. "NewsEmoji, DuckEmoji".
    if not names:
        for chunk in re.split(r"[\s,]+", text):
            if re.fullmatch(r"[A-Za-z0-9_]{3,64}", chunk or ""):
                names.append(chunk)

    # De-duplicate, preserving the order the owner listed them in.
    seen = set()
    ordered = []
    for name in names:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


async def apply_theme_pack(message, lang: str) -> str:
    """Install the emoji packs from the owner's message and rebuild the map."""
    from bot.utils import premoji, theme

    names = await pack_from_message(message)
    if not names:
        return get_text("THEME_PACK_BAD", lang)

    # Keep the built-ins as a safety net behind the owner's picks, so a small
    # pack cannot shrink coverage and leave plain emoji on screen.
    merged = names + [p for p in premoji.DEFAULT_PACKS if p not in names]
    await premoji.set_packs(merged)
    count, used = await premoji.rebuild(message.get_bot())
    if not used:
        # Nothing resolved: restore the previous defaults rather than leave the
        # bot with an empty map.
        await premoji.set_packs(list(premoji.DEFAULT_PACKS))
        await premoji.rebuild(message.get_bot())
        return get_text("THEME_PACK_FAIL", lang, packs=", ".join(names))
    theme.invalidate()
    rejected = [n for n in names if n not in used]
    text = get_text("THEME_PACK_OK", lang, count=count, packs=", ".join(used))
    if rejected:
        text += "\n" + get_text("THEME_PACK_SKIPPED", lang, packs=", ".join(rejected))
    return text


async def inbox_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/inbox — the owner's view of what users send the bot. Silent for others."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    # DM-only: the inbox contains other users' messages.
    chat = update.effective_chat
    if chat is not None and chat.type in ("group", "supergroup"):
        return
    from bot.handlers import inbox as IB
    from bot.keyboards.inline import inbox_keyboard

    lang = await db.get_user_language(user.id)
    rows = await db.recent_chats(IB.PAGE, 0)
    await update.effective_message.reply_text(
        await IB.inbox_text(lang, 0), parse_mode=ParseMode.HTML,
        reply_markup=inbox_keyboard(lang, rows, 0, await db.count_chats()))


async def theme_text(lang: str) -> str:
    """The theme editor screen: live preview + current state."""
    from bot.utils import theme

    premium = await theme.flag("premium")
    styles = await theme.flag("styles")
    icons = await theme.flag("btn_icons")
    div = await theme.divider()
    # Colour names, not colour-dot emoji: the role buttons below already wear
    # their own colour, and a plain 🟢/🔴 here would be the only non-premium glyph.
    names = {"": "خاکستری", "primary": "آبی", "success": "سبز", "danger": "قرمز"}
    if lang != "fa":
        names = {"": "grey", "primary": "blue", "success": "green", "danger": "red"}
    role_bits = []
    for r in theme.STYLE_ROLES:
        style = await theme.role_style(r)
        role_bits.append(
            f"{get_text('THEME_ROLE_' + r.upper(), lang)}={names.get(style, names[''])}")
    rolebar = " · ".join(role_bits)
    preset = await theme.current_preset()
    preset_label = str((theme.PRESETS.get(preset) or {}).get("_label") or "—")
    data = await theme.load()
    edited = len(data.get("slots") or {})

    # Live preview built with the themed icons, so the owner sees the result.
    preview = "\n".join([
        f"{await theme.icon('brand')} <b>{get_text('THEME_PREVIEW', lang)}</b>",
        div,
        f"{await theme.icon('search')} {get_text('PROCESSING_LABEL', lang)}"
        f"  <code>{await theme.bar(2)}</code>",
        f"{await theme.icon('download')} {get_text('DOWNLOADING_LABEL', lang)}"
        f"  <code>{await theme.bar(6)}</code>",
        f"{await theme.icon('upload')} {get_text('UPLOADING_LABEL', lang)}"
        f"  <code>{await theme.bar(9)}</code>",
        f"{await theme.icon('ok')} {get_text('DOWNLOAD_SUCCESS_LABEL', lang)}"
        f"   {await theme.icon('cache')} {get_text('FROM_CACHE_LABEL', lang)}",
    ])
    lines = [
        await themed("THEME_TITLE", lang),
        "",
        preview,
        "",
        get_text("THEME_STATE", lang,
                 premium=get_text("ON" if premium else "OFF", lang),
                 blue=get_text("ON" if styles else "OFF", lang),
                 icons=get_text("ON" if icons else "OFF", lang),
                 rolebar=rolebar,
                 preset=preset_label,
                 edited=edited,
                 total=len(theme.SLOTS)),
        "",
        get_text("THEME_HELP", lang),
    ]
    return "\n".join(lines)


async def theme_slot_values(plain: bool = True) -> dict:
    """slot -> glyph currently in effect, for previewing on buttons."""
    from bot.utils import theme

    out = {}
    for slot in theme.SLOTS:
        out[slot] = await theme.plain(slot)
    return out


async def apply_theme_slot(slot: str, value: str, lang: str) -> str:
    """Store one slot from raw admin input; returns the reply text."""
    from bot.utils import theme

    value = (value or "").strip()
    if value in {"-", "حذف", "reset", "پیش‌فرض", "default"}:
        await theme.set_slot(slot, "")
        return get_text("THEME_SLOT_RESET", lang, slot=slot)
    ok = await theme.set_slot(slot, value)
    if not ok:
        return get_text("THEME_SLOT_BAD", lang)
    shown = await theme.plain(slot)
    return get_text("THEME_SLOT_SAVED", lang, slot=slot, value=shown)


async def adjust_sep_len(delta: int) -> int:
    """Nudge the separator width and persist it. Returns the new value."""
    from bot.utils import theme

    data = await theme.load(force=True)
    try:
        cur = int(data.get("sep_len", theme.DEFAULTS["sep_len"]))
    except (TypeError, ValueError):
        cur = theme.DEFAULTS["sep_len"]
    new = max(1, min(cur + delta, 30))
    await theme.set_flag("sep_len", new)
    return new


def premium_emoji_from_message(message) -> str:
    """Extract 'fallback:custom_emoji_id' when the admin sends a premium emoji.

    A premium emoji arrives as a ``custom_emoji`` entity over the plain glyph,
    so this reads the entity rather than the text — the only reliable way to
    learn the id without asking the owner to paste numbers by hand.
    """
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    for ent in entities:
        if ent.type == "custom_emoji" and ent.custom_emoji_id:
            glyph = text[ent.offset:ent.offset + ent.length] or "•"
            return f"{glyph}:{ent.custom_emoji_id}"
    return text.strip()


# ── pending text input ────────────────────────────────────────────────
async def consume_pending_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle a reply that answers an admin prompt. Returns True if consumed."""
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None or not is_admin(user.id):
        return False
    lang = await db.get_user_language(user.id)

    if context.user_data.pop("await_ban", False):
        parts = (message.text or "").split(maxsplit=1)
        await _do_ban(update, context, parts[0] if parts else "",
                      parts[1] if len(parts) > 1 else "", lang)
        return True

    if context.user_data.pop("await_unban", False):
        await _do_unban(update, (message.text or "").strip(), lang)
        return True

    uid = context.user_data.pop("await_inbox_reply", None)
    if uid:
        from bot.handlers import inbox as IB
        from bot.keyboards.inline import thread_keyboard

        reply = await IB.do_reply(context, int(uid), message.text or "", lang)
        await message.reply_text(
            reply, parse_mode=ParseMode.HTML,
            reply_markup=thread_keyboard(lang, int(uid), 0,
                                         await db.count_conversation(int(uid))))
        return True

    if context.user_data.pop("await_inbox_search", False):
        from bot.handlers import inbox as IB
        from bot.keyboards.inline import admin_back_keyboard

        await message.reply_text(
            await IB.search_text((message.text or "").strip(), lang),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_back_keyboard(lang))
        return True

    if context.user_data.pop("await_theme_pack", False):
        reply = await apply_theme_pack(message, lang)
        from bot.keyboards.inline import theme_keyboard
        from bot.utils import theme

        await message.reply_text(
            reply, parse_mode=ParseMode.HTML,
            reply_markup=theme_keyboard(
                lang, await theme.flag("premium"),
                await theme.flag("styles"), await theme_slot_values(),
                await theme.current_preset(),
                {r: await theme.role_style(r) for r in theme.STYLE_ROLES},
                await theme.flag("btn_icons")),
        )
        return True

    slot = context.user_data.pop("await_theme_slot", None)
    if slot:
        raw = premium_emoji_from_message(message)
        reply = await apply_theme_slot(slot, raw, lang)
        from bot.keyboards.inline import theme_keyboard
        from bot.utils import theme

        await message.reply_text(
            reply, parse_mode=ParseMode.HTML,
            reply_markup=theme_keyboard(
                lang, await theme.flag("premium"),
                await theme.flag("styles"), await theme_slot_values(),
                await theme.current_preset(),
                {r: await theme.role_style(r) for r in theme.STYLE_ROLES},
                await theme.flag("btn_icons")),
        )
        return True

    if context.user_data.pop("await_theme_import", False):
        from bot.keyboards.inline import theme_keyboard
        from bot.utils import theme

        ok = await theme.import_json(message.text or "")
        await message.reply_text(
            get_text("THEME_IMPORT_OK" if ok else "THEME_IMPORT_BAD", lang),
            parse_mode=ParseMode.HTML,
            reply_markup=theme_keyboard(
                lang, await theme.flag("premium"),
                await theme.flag("styles"), await theme_slot_values(),
                await theme.current_preset(),
                {r: await theme.role_style(r) for r in theme.STYLE_ROLES},
                await theme.flag("btn_icons")),
        )
        return True

    if context.user_data.pop("await_welcome", False):
        await db.set_welcome_message(message.text or "")
        await message.reply_text(get_text("WELCOME_SET", lang))
        return True

    if context.user_data.pop("await_finduser", False):
        raw = (message.text or "").strip()
        if raw.lstrip("-").isdigit():
            await _show_user(update, int(raw), lang)
        else:
            await message.reply_text(get_text("INVALID_ID", lang))
        return True

    if context.user_data.pop("await_broadcast", False):
        context.user_data["broadcast_msg"] = {
            "chat_id": message.chat_id,
            "message_id": message.message_id,
            "text": message.text or message.caption or "",
        }
        from bot.keyboards.inline import broadcast_audience_keyboard

        count = len(await db.get_all_user_ids())
        await message.reply_text(
            get_text("BROADCAST_CONFIRM", lang, count=count),
            parse_mode=ParseMode.HTML,
            reply_markup=broadcast_audience_keyboard(lang),
        )
        return True

    return False
