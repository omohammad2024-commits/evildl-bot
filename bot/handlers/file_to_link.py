"""Public "send a file, get a link" feature.

Any user can forward/upload a file (document, video, audio, photo) and the bot
hosts it and replies with a direct download link. This turns Telegram into a
quick file-sharing front end and works for files far larger than a normal chat
share, because incoming media is pulled over MTProto (Telethon), which is not
subject to the Bot API's 20MB getFile download cap.

Design decisions:
- Public, so it runs behind the same invisible queue and forced-channel gate as
  link downloads — no visible limits, no refusal messages.
- The cookie-upload handler (admin .txt files) runs first in main.py, so a small
  .txt from an admin is still treated as a cookie, not hosted.
- Files are streamed to /tmp and always removed in finally.
"""
import logging
import os
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot import config
from bot.database import db
from bot.i18n import get_text
from bot.utils import filehost
from bot.utils import media_handler as mh

logger = logging.getLogger(__name__)


def _pick_media(message):
    """Return (telethon-fetchable, filename, size, kind) for any media type."""
    if message.document:
        d = message.document
        return d, d.file_name or "file", d.file_size or 0, "document"
    if message.video:
        v = message.video
        return v, (v.file_name or "video.mp4"), v.file_size or 0, "video"
    if message.audio:
        a = message.audio
        return a, (a.file_name or "audio.mp3"), a.file_size or 0, "audio"
    if message.voice:
        return message.voice, "voice.ogg", message.voice.file_size or 0, "voice"
    if message.video_note:
        return message.video_note, "video_note.mp4", message.video_note.file_size or 0, "video"
    if message.photo:
        p = message.photo[-1]
        return p, "photo.jpg", p.file_size or 0, "photo"
    if message.animation:
        an = message.animation
        return an, (an.file_name or "animation.mp4"), an.file_size or 0, "video"
    return None, "", 0, ""


async def _download_incoming(context, message, dest: str, size: int) -> bool:
    """Download the message's media to ``dest``.

    Small files (<=20MB) use the Bot API. Larger files must come over MTProto,
    because the Bot API's getFile rejects anything above 20MB.
    """
    # Bot API path for small files.
    if size and size <= 19 * 1024 * 1024:
        try:
            media, _, _, _ = _pick_media(message)
            tg_file = await media.get_file()
            await tg_file.download_to_drive(dest)
            return os.path.exists(dest) and os.path.getsize(dest) > 0
        except Exception as exc:
            logger.info("Bot API download failed (%s); trying MTProto", exc)

    # MTProto path — pulls files of any size via the message id.
    from bot.utils import sender

    client = await sender._get_mtproto()
    if client is None:
        return False
    try:
        fetched = await client.get_messages(message.chat_id, ids=message.message_id)
        if not fetched or not fetched.media:
            return False
        out = await fetched.download_media(file=dest)
        return bool(out) and os.path.exists(out) and os.path.getsize(out) > 0
    except Exception as exc:
        logger.warning("MTProto incoming download failed: %s", exc)
        return False


async def file_to_link_handler(update: Update,
                               context: ContextTypes.DEFAULT_TYPE) -> None:
    """Host any uploaded file and reply with a direct download link."""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    media, fname, size, kind = _pick_media(message)
    if media is None:
        return

    # An admin answering a prompt with media (e.g. a caption-less photo for a
    # broadcast) must be handled by the admin flow, NOT hosted. download_handler
    # only fires on TEXT|CAPTION, so a caption-less photo would never reach
    # consume_pending_input — do it here where all media types arrive. If it
    # consumes the message, stop (don't host it).
    from bot.handlers.admin import consume_pending_input

    if await consume_pending_input(update, context):
        return

    # Group mode: NEVER auto-host files in a group. The bot must ignore every
    # photo/video/document/sticker posted there — including replies to the bot,
    # since people reply to the bot's own messages constantly. A file is hosted
    # in a group ONLY when its caption explicitly @-mentions the bot. (Links,
    # unlike files, still auto-download in groups — that logic is in download.py
    # and is unaffected.)
    if message.chat.type in ("group", "supergroup"):
        from bot.handlers import groups as G
        from bot.utils.runtime import is_bot_mentioned

        if not is_bot_mentioned(message):
            return
        # The mention is necessary but not sufficient: the group's admins-only
        # rule and its flood budget still apply.
        if not await G.may_act(update, context, True):
            return

    lang = await db.get_user_language(user.id)
    await host_message(update, context, message, user, lang)


async def host_message(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       message, user, lang: str) -> None:
    """Host the media carried by ``message`` and reply with a download link.

    Shared by the normal file-to-link handler and the group "reply to a file +
    @mention the bot" path in download.py, so both behave identically (queue,
    gates, MTProto large-file pull, filehost fallback).
    """
    media, fname, size, kind = _pick_media(message)
    if media is None:
        return

    await db.add_user(user.id, user.username or "", user.first_name or "")

    if await db.is_banned(user.id):
        return
    # Same maintenance + forced-channel gates as link downloads.
    from bot.handlers.download import _maintenance_blocked, _channel_ok

    if await _maintenance_blocked(user.id, message, lang):
        return
    if not await _channel_ok(update, context, lang):
        return

    # Refuse only what we physically cannot handle (over the download ceiling).
    if size and size > config.MAX_DOWNLOAD:
        await message.reply_text(
            get_text("F2L_TOO_BIG", lang,
                     limit=config.MAX_DOWNLOAD // 1024 // 1024))
        return

    note = await message.reply_text(get_text("F2L_WORKING", lang))
    dest = os.path.join(config.TEMP_DIR, f"f2l_{user.id}_{int(time.time())}_{fname}")
    os.makedirs(config.TEMP_DIR, exist_ok=True)

    async def _job():
        await context.bot.send_chat_action(message.chat_id, ChatAction.UPLOAD_DOCUMENT)
        if not await _download_incoming(context, message, dest, size):
            raise RuntimeError("download failed")
        hosted = await filehost.upload(dest)
        if hosted is None:
            raise RuntimeError("all file hosts failed")
        return hosted

    try:
        from bot.utils.queue import queue

        hourly = await db.get_user_hourly(user.id)
        hosted = await queue.run(user.id, _job, hourly_count=hourly)
    except Exception as exc:
        logger.warning("file-to-link failed for %s: %s", user.id, exc)
        try:
            await note.edit_text(get_text("F2L_FAILED", lang))
        except Exception:
            pass
        return
    finally:
        await mh.delete_file(dest)

    real_size = mh.human_size(hosted.size)
    expiry = f"\n⏳ {hosted.expires}" if hosted.expires else ""
    link = hosted.direct or hosted.url

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    kb = InlineKeyboardMarkup(
        [[InlineKeyboardButton(get_text("F2L_BUTTON", lang), url=link)]]
    )
    try:
        await note.edit_text(
            get_text("F2L_DONE", lang, name=fname[:60], size=real_size,
                     url=hosted.url) + expiry,
            reply_markup=kb, disable_web_page_preview=False)
    except Exception:
        await message.reply_text(
            get_text("F2L_DONE", lang, name=fname[:60], size=real_size,
                     url=hosted.url) + expiry, reply_markup=kb)

    await db.add_download(user.id, "filehost", fname, hosted.size, 1)
    logger.info("file-to-link: %s hosted %s (%s)", user.id, fname, real_size)
