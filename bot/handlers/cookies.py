"""Admin-only cookie management: /cookies, and uploading a cookies.txt file.

Cookies are the one lever that restores YouTube and Instagram from a flagged
datacenter IP, so the flow is built to be usable from a phone: send the file to
the bot, it validates and installs it, and the message is deleted immediately
because the file contains live session credentials.
"""
import logging
import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot import config
from bot.i18n import get_text
from bot.utils import cookies as cookie_jar

logger = logging.getLogger(__name__)

SUPPORTED = ("youtube", "instagram", "tiktok", "twitter")

# Guessed from the filename when the admin does not say which platform it is.
FILENAME_HINTS = {
    "youtube": ("youtube", "yt", "google"),
    "instagram": ("instagram", "insta", "ig"),
    "tiktok": ("tiktok", "tt"),
    "twitter": ("twitter", "x.com", "x_"),
}

REASONS = {
    "no_cookies": "COOKIE_ERR_FORMAT",
    "wrong_domain": "COOKIE_ERR_DOMAIN",
    "missing_session": "COOKIE_ERR_SESSION",
    "expired": "COOKIE_ERR_EXPIRED",
}


def _is_admin(update: Update) -> bool:
    user = update.effective_user
    return bool(user and user.id in config.ADMIN_IDS)


def _platform_from_name(name: str) -> str:
    low = (name or "").lower()
    for platform, hints in FILENAME_HINTS.items():
        if any(h in low for h in hints):
            return platform
    return ""


async def cookies_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show cookie status, or remove a jar with /cookies del <platform>."""
    if not _is_admin(update):
        return  # silence for non-admins, like every other admin command
    lang = context.user_data.get("lang") or config.DEFAULT_LANG
    args = context.args or []

    if args and args[0].lower() in {"del", "delete", "remove", "rm"}:
        if len(args) < 2 or args[1].lower() not in SUPPORTED:
            await update.effective_message.reply_text(
                get_text("COOKIE_USAGE", lang, platforms=", ".join(SUPPORTED)))
            return
        platform = args[1].lower()
        removed = cookie_jar.remove(platform)
        await update.effective_message.reply_text(
            get_text("COOKIE_REMOVED" if removed else "COOKIE_NONE", lang,
                     platform=platform))
        return

    status = cookie_jar.status()
    lines = [get_text("COOKIE_TITLE", lang)]
    for platform in SUPPORTED:
        info = status.get(platform, {})
        if not info.get("present"):
            lines.append(get_text("COOKIE_ROW_NONE", lang, platform=platform))
            continue
        if info.get("ok"):
            age_days = int((time.time() - info.get("mtime", 0)) / 86400)
            lines.append(get_text("COOKIE_ROW_OK", lang, platform=platform,
                                  count=info.get("count", 0),
                                  days=info.get("days_left", 0), age=age_days))
        else:
            lines.append(get_text("COOKIE_ROW_BAD", lang, platform=platform,
                                  reason=info.get("reason", "?")))
    lines.append("")
    lines.append(get_text("COOKIE_HELP", lang))
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML,
        disable_web_page_preview=True)


async def cookie_document_handler(update: Update,
                                  context: ContextTypes.DEFAULT_TYPE) -> None:
    """Install an uploaded cookies.txt. Admin-only, and the upload is deleted."""
    if not _is_admin(update):
        return
    message = update.effective_message
    doc = message.document
    if not doc:
        return

    lang = context.user_data.get("lang") or config.DEFAULT_LANG
    name = doc.file_name or "cookies.txt"

    # Only touch plausible cookie files so ordinary document sends fall through
    # to the public file-to-link handler in the next group.
    if not (name.endswith(".txt") or "cookie" in name.lower()):
        return
    if doc.file_size and doc.file_size > 512 * 1024:
        # Too big to be a cookie jar; let file-to-link host it instead.
        return

    platform = ""
    if context.args:
        platform = (context.args[0] or "").lower()
    if platform not in SUPPORTED:
        platform = _platform_from_name(name)
    if not platform and message.caption:
        platform = _platform_from_name(message.caption)

    try:
        tg_file = await doc.get_file()
        raw = await tg_file.download_as_bytearray()
        text = bytes(raw).decode("utf-8", "replace")
    except Exception as exc:
        logger.warning("cookie upload download failed: %s", exc)
        await message.reply_text(get_text("COOKIE_ERR_FORMAT", lang))
        return

    # Without a hint, detect the platform from the cookie domains themselves.
    if not platform:
        parsed = cookie_jar.parse(text)
        domains = " ".join(c["domain"] for c in parsed)
        for cand, hints in FILENAME_HINTS.items():
            if any(h in domains for h in hints):
                platform = cand
                break
    if not platform:
        # Not a cookie file after all — let the public file-to-link handler
        # (next group) host it instead of consuming the update here.
        return

    ok, reason, details = cookie_jar.save(text, platform)

    # The uploaded file is a live credential; do not leave it in chat history.
    try:
        await message.delete()
    except Exception:
        logger.debug("could not delete cookie upload message", exc_info=True)

    if ok:
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=get_text("COOKIE_SAVED", lang, platform=platform,
                          count=details.get("count", 0),
                          days=details.get("days_left", 0)))
        logger.info("admin installed cookies for %s", platform)
    else:
        key = REASONS.get(reason, "COOKIE_ERR_FORMAT")
        extra = ""
        if reason == "wrong_domain" and details.get("found"):
            extra = " (" + ", ".join(details["found"]) + ")"
        elif reason == "missing_session" and details.get("missing"):
            extra = " (" + ", ".join(details["missing"]) + ")"
        await context.bot.send_message(
            chat_id=message.chat_id,
            text=get_text(key, lang, platform=platform) + extra)

    # We claimed this document as a cookie upload; stop so the file-to-link
    # handler in the next group does not also process it.
    raise ApplicationHandlerStop
