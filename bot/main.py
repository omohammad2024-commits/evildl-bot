"""Entry point.

Run with:  python -m bot.main

Two command scopes are registered so ordinary users only ever see the five
public commands, while the admin's own chat gets the full list.
"""
import asyncio
import logging
import logging.handlers
import os
import sys

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from bot import config
from bot.database import db
from bot.handlers.admin import (
    admin_command,
    ban_command,
    banlist_command,
    broadcast_command,
    cache_command,
    errors_command,
    inbox_command,
    export_command,
    health_command,
    maintenance_command,
    setchannel_command,
    setwelcome_command,
    stats_command,
    storage_command,
    top_command,
    unban_command,
    user_command,
)
from bot.handlers.callback import callback_handler
from bot.handlers.cookies import cookie_document_handler, cookies_command
from bot.handlers.download import download_handler
from bot.handlers.file_to_link import file_to_link_handler
from bot.handlers.errors import errors_handler
from bot.handlers.start import (
    cancel_command,
    favorites_command,
    help_command,
    invite_command,
    language_command,
    me_command,
    mystats_command,
    settings_command,
    start_command,
)

logger = logging.getLogger(__name__)

PUBLIC_COMMANDS = [
    BotCommand("start", "شروع / Start"),
    BotCommand("help", "راهنما / Help"),
    BotCommand("mystats", "آمار من / My stats"),
    BotCommand("favorites", "علاقه‌مندی‌ها / Favorites"),
    BotCommand("settings", "تنظیمات / Settings"),
    BotCommand("invite", "دعوت دوستان / Invite"),
    BotCommand("lang", "تغییر زبان / Language"),
    BotCommand("cancel", "لغو / Cancel"),
]

ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand("admin", "پنل مدیریت"),
    BotCommand("stats", "آمار کامل"),
    BotCommand("top", "پلتفرم‌های محبوب"),
    BotCommand("health", "سلامت پلتفرم‌ها"),
    BotCommand("errors", "آخرین خطاها"),
    BotCommand("cache", "وضعیت کش"),
    BotCommand("storage", "وضعیت فضا"),
    BotCommand("user", "اطلاعات کاربر"),
    BotCommand("ban", "بن کاربر"),
    BotCommand("unban", "رفع بن"),
    BotCommand("banlist", "لیست بن‌ها"),
    BotCommand("broadcast", "ارسال همگانی"),
    BotCommand("export", "خروجی CSV"),
    BotCommand("setchannel", "کانال اجباری"),
    BotCommand("setwelcome", "پیام خوشامد"),
    BotCommand("maintenance", "حالت تعمیر"),
    BotCommand("cookies", "وضعیت کوکی‌ها"),
]


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    # Rotate so a public bot cannot fill the disk with logs.
    file_handler = logging.handlers.RotatingFileHandler(
        config.LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.Application").setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)


async def post_init(application) -> None:
    await db.connect()

    # Theme every outgoing keyboard (Bot API 10.2 styles + premium button icons)
    # from one place, so new handlers are themed without extra wiring.
    from bot.utils import premoji, theme_hook

    theme_hook.install(application.bot)

    # Warm the premium-emoji map once at boot: the first render then costs
    # nothing, and a Telegram hiccup later cannot strip the look.
    try:
        if not await premoji.load_map():
            count, packs = await premoji.rebuild(application.bot)
            logger.info("premium emoji ready: %d glyphs from %d packs",
                        count, len(packs))
    except Exception as exc:
        logger.warning("premium emoji map unavailable: %s", exc)

    # Public scope: five harmless commands.
    await application.bot.set_my_commands(PUBLIC_COMMANDS, scope=BotCommandScopeDefault())
    # Admin scope: per-chat, so nobody else can enumerate them.
    for admin_id in config.ADMIN_IDS:
        try:
            await application.bot.set_my_commands(
                ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id)
            )
        except Exception as exc:
            logger.warning("could not set admin commands for %s: %s", admin_id, exc)

    me = await application.bot.get_me()

    # Record the bot's own handle so captions can credit it.
    from bot.utils.runtime import set_identity

    set_identity(me.username or "", me.id)

    logger.info(
        "Bot @%s online | admins=%s | mtproto=%s | upload_cap=%dMB | temp=%s",
        me.username, config.ADMIN_IDS, config.USE_MTPROTO,
        config.MAX_UPLOAD // 1024 // 1024, config.TEMP_DIR,
    )


async def post_shutdown(application) -> None:
    from bot.utils.sender import close_mtproto
    from bot.utils.userclient import close_user

    await close_mtproto()
    await close_user()
    await db.close()
    logger.info("Shutdown complete")


async def periodic_sweep(context) -> None:
    """Frequent, cheap housekeeping: orphaned temp files only."""
    from bot.utils.queue import sweep_temp

    await asyncio.to_thread(sweep_temp)


async def periodic_maintenance(context) -> None:
    """Hourly housekeeping so nothing grows without bound.

    Prunes the rolling download log, evicts least-used cache rows, trims logs,
    and VACUUMs. If free space is critically low it prunes aggressively and
    warns the admin instead of silently dying when the disk fills.
    """
    from bot.utils.queue import free_bytes, sweep_temp, trim_logs

    free = free_bytes()
    panic = free < config.DISK_PANIC

    await asyncio.to_thread(sweep_temp, panic)
    await asyncio.to_thread(trim_logs)
    report = await db.maintain(aggressive=panic)

    # Keep the owner inbox log bounded — /data is a small volume.
    try:
        dropped = await db.prune_messages(2000 if panic else 5000)
        if dropped:
            logger.info("inbox log pruned: -%s rows", dropped)
    except Exception as exc:
        logger.debug("inbox prune failed: %s", exc)

    # Lift any expired temporary bans.
    try:
        freed = await db.expire_temp_bans()
        if freed:
            logger.info("temp-ban sweep: unbanned %s user(s)", freed)
    except Exception as exc:
        logger.debug("temp-ban sweep failed: %s", exc)

    if panic:
        logger.warning("Disk low (%.0fMB free) — aggressive prune done", free / 1024 / 1024)
        if config.ADMIN_IDS:
            try:
                await context.bot.send_message(
                    config.ADMIN_IDS[0],
                    f"⚠️ فضای دیسک کم است: {free // 1024 // 1024}MB آزاد\n"
                    f"پاک‌سازی اضطراری انجام شد: "
                    f"-{report['downloads_pruned']} لاگ، -{report['cache_pruned']} کش",
                )
            except Exception:
                pass


async def daily_watchdog(context) -> None:
    """Once a day: warn the admin about cookie expiry and unhealthy platforms.

    Runs quietly — it only messages the admin when something actually needs
    attention, so it doubles as a "cookie about to die / platform down" alert
    without spamming.
    """
    if not config.ADMIN_IDS:
        return
    admin = config.ADMIN_IDS[0]
    alerts: List[str] = []

    # 1. Cookie expiry: warn a week ahead so there is time to re-export.
    try:
        from bot.utils import cookies as cookie_jar

        for platform, info in (cookie_jar.status() or {}).items():
            days = info.get("days_left")
            if isinstance(days, (int, float)) and 0 <= days <= 7:
                alerts.append(f"🍪 کوکی {platform} تا {int(days)} روز دیگر منقضی می‌شود — دوباره ارسال کن")
            elif isinstance(days, (int, float)) and days < 0:
                alerts.append(f"🔴 کوکی {platform} منقضی شده است — دوباره ارسال کن")
    except Exception as exc:
        logger.debug("cookie expiry check skipped: %s", exc)

    # 2. Platform health: flag any platform whose 24h success rate is poor with
    #    a meaningful number of attempts.
    try:
        for row in await db.get_platform_health():
            ok = row.get("ok") or 0
            bad = row.get("bad") or 0
            total = ok + bad
            if total >= 5 and (ok / total) < 0.5:
                from bot.utils.url_parser import platform_label

                alerts.append(
                    f"🔴 {platform_label(row['platform'])}: "
                    f"{round(ok / total * 100)}% موفقیت ({ok}/{total}) در ۲۴ ساعت"
                )
    except Exception as exc:
        logger.debug("platform health check skipped: %s", exc)

    if alerts:
        try:
            await context.bot.send_message(
                admin, "⚠️ <b>گزارش روزانه</b>\n\n" + "\n".join(alerts),
                parse_mode="HTML",
            )
        except Exception:
            pass


def build_application():
    request = HTTPXRequest(
        connection_pool_size=16,
        read_timeout=300.0,
        write_timeout=600.0,
        connect_timeout=30.0,
        pool_timeout=60.0,
    )
    builder = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .request(request)
        .concurrent_updates(True)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
    )
    if config.LOCAL_BOT_API:
        builder = builder.base_url(f"{config.LOCAL_BOT_API}/bot").local_mode(True)
    app = builder.build()

    # Inbox recorder: group -1 runs before everything, block=False so it can
    # never delay or swallow a download.
    from bot.handlers.inbox import record as inbox_record

    app.add_handler(MessageHandler(filters.ALL, inbox_record, block=False), group=-1)

    # public
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler(["lang", "language"], language_command))
    app.add_handler(CommandHandler("me", me_command))
    app.add_handler(CommandHandler("mystats", mystats_command))
    app.add_handler(CommandHandler("favorites", favorites_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("invite", invite_command))
    app.add_handler(CommandHandler("cancel", cancel_command))

    # admin (silent for everyone else)
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("top", top_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("errors", errors_command))
    app.add_handler(CommandHandler("cache", cache_command))
    app.add_handler(CommandHandler(["storage", "disk"], storage_command))
    app.add_handler(CommandHandler("user", user_command))
    app.add_handler(CommandHandler("ban", ban_command))
    app.add_handler(CommandHandler("unban", unban_command))
    app.add_handler(CommandHandler("banlist", banlist_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("setchannel", setchannel_command))
    app.add_handler(CommandHandler("setwelcome", setwelcome_command))
    app.add_handler(CommandHandler("maintenance", maintenance_command))
    app.add_handler(CommandHandler("cookies", cookies_command))
    app.add_handler(CommandHandler("inbox", inbox_command))
    # The inbox prints /m<id> and /s<id> codes next to each row (pull that
    # message's media / toggle its star). A CommandHandler matches on an exact
    # name, so these variable-suffix codes need a regex MessageHandler. Owner
    # and DM checks live inside code_handler.
    from bot.handlers.inbox import code_handler

    app.add_handler(MessageHandler(
        filters.Regex(r"^/[ms]\d+$") & filters.ChatType.PRIVATE, code_handler))
    from bot.handlers.tglogin import (
        tglogin_command, tglogout_command, tglogin_text,
    )
    app.add_handler(CommandHandler("tglogin", tglogin_command))
    app.add_handler(CommandHandler(["tglogout", "tglogout"], tglogout_command))

    # Per-group settings, owned by that group's admins (not the bot owner).
    from bot.handlers.gsettings import gsettings_command

    app.add_handler(CommandHandler(["gsettings", "groupsettings"], gsettings_command))

    app.add_handler(CallbackQueryHandler(callback_handler))
    # Real-time channel membership tracking for the forced-channel gate, so a
    # user who leaves a gated channel is re-gated immediately (not after the
    # cache TTL). Requires "chat_member" in allowed_updates + bot as admin.
    from telegram.ext import ChatMemberHandler
    from bot.handlers.membership import chat_member_handler

    from bot.handlers.groups import register as group_register

    app.add_handler(ChatMemberHandler(group_register,
                                      ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(chat_member_handler,
                                      ChatMemberHandler.CHAT_MEMBER))
    # Inline mode: @Bot <link> in any chat.
    from telegram.ext import ChosenInlineResultHandler, InlineQueryHandler
    from bot.handlers.inline import chosen_inline_handler, inline_query_handler

    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(ChosenInlineResultHandler(chosen_inline_handler))
    # Admin cookie uploads (.txt documents) are checked first (group 0). When a
    # document is claimed as a cookie the handler raises ApplicationHandlerStop
    # so the public file-to-link handler in group 1 does not also host it.
    app.add_handler(MessageHandler(filters.Document.ALL, cookie_document_handler))
    # Public "send a file, get a link": any uploaded media that is not a
    # command. Group 1 so it runs after the cookie check; for ordinary files
    # the cookie handler returns without stopping, and this fires.
    app.add_handler(
        MessageHandler(
            (filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.VOICE
             | filters.PHOTO | filters.VIDEO_NOTE | filters.ANIMATION)
            & ~filters.COMMAND,
            file_to_link_handler,
        ),
        group=1,
    )
    # Any text or captioned media that is not a command may contain links.
    # First, the interactive user-login flow (admin+DM) claims phone/code/
    # password input in its OWN group (-2, before the inbox observer in -1) so
    # a phone number or login code is never treated as a link. It raises
    # ApplicationHandlerStop when it owns the input, halting all later groups.
    from bot.handlers.tglogin import tglogin_text
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
                       tglogin_text),
        group=-2,
    )
    app.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, download_handler
        )
    )
    app.add_error_handler(errors_handler)

    if app.job_queue:
        app.job_queue.run_repeating(periodic_sweep, interval=600, first=120)
        app.job_queue.run_repeating(
            periodic_maintenance, interval=config.MAINTENANCE_INTERVAL, first=300
        )
        # Daily cookie-expiry + platform-health alert (fires ~24h after boot).
        app.job_queue.run_repeating(daily_watchdog, interval=86400, first=1800)

    return app


def main() -> None:
    setup_logging()
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN is not set — create a .env file (see .env.example)")
        sys.exit(1)
    if not config.ADMIN_IDS:
        logger.warning("ADMIN_IDS is empty — the admin panel will be unreachable")

    app = build_application()
    logger.info("Starting polling...")
    app.run_polling(drop_pending_updates=True,
                    allowed_updates=["message", "callback_query",
                                     "inline_query", "chosen_inline_result",
                                     "chat_member", "my_chat_member"])


if __name__ == "__main__":
    main()
