"""Upload layer.

Responsibilities, in order of preference:

1. reuse a cached ``file_id`` — instant, no upload at all
2. send via the Bot API (files up to 50MB)
3. for bigger files, send via MTProto (Telethon + the same bot token, up to 2GB)
   when TELEGRAM_API_ID / TELEGRAM_API_HASH are configured
4. otherwise split into <50MB parts, or fall back to a direct link

Everything returns the ``file_id`` of what was sent so the caller can cache it.
"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from telegram import (
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter, TimedOut

from bot import config
from bot.utils import media_handler as mh

logger = logging.getLogger(__name__)


# ── MTProto (large files) ─────────────────────────────────────────────
_mt_client = None
_mt_lock = asyncio.Lock()


async def _get_mtproto():
    """Lazily start a Telethon client logged in with the bot token."""
    global _mt_client
    if not config.USE_MTPROTO:
        return None
    async with _mt_lock:
        if _mt_client is not None and _mt_client.is_connected():
            return _mt_client
        try:
            from telethon import TelegramClient

            session = os.path.join(config.DATA_DIR, "mtproto_bot")
            client = TelegramClient(
                session, config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH
            )
            await client.start(bot_token=config.BOT_TOKEN)
            _mt_client = client
            logger.info("MTProto client ready (large uploads enabled)")
            return _mt_client
        except Exception as exc:
            logger.error("MTProto init failed: %s", exc)
            return None


async def close_mtproto() -> None:
    global _mt_client
    if _mt_client is not None:
        try:
            await _mt_client.disconnect()
        except Exception:
            pass
        _mt_client = None


async def _send_large_via_mtproto(
    chat_id: int,
    path: str,
    *,
    caption: str,
    kind: str,
    meta: Dict[str, int],
    thumb: Optional[str] = None,
    reply_to: Optional[int] = None,
) -> Optional[str]:
    client = await _get_mtproto()
    if client is None:
        return None
    try:
        from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

        attributes = []
        if kind == "video":
            attributes.append(
                DocumentAttributeVideo(
                    duration=meta.get("duration", 0) or 0,
                    w=meta.get("width", 0) or 0,
                    h=meta.get("height", 0) or 0,
                    supports_streaming=True,
                )
            )
        elif kind == "audio":
            attributes.append(
                DocumentAttributeAudio(duration=meta.get("duration", 0) or 0)
            )

        msg = await client.send_file(
            chat_id,
            path,
            caption=caption[:1024],
            parse_mode="html",
            thumb=thumb,
            attributes=attributes or None,
            supports_streaming=(kind == "video"),
            force_document=(kind == "document"),
            reply_to=reply_to,
        )
        # Convert the MTProto document into a Bot-API-compatible file_id.
        try:
            from telethon.utils import pack_bot_file_id

            return pack_bot_file_id(msg.media)
        except Exception:
            return None
    except Exception as exc:
        logger.error("MTProto upload failed: %s", exc)
        return None


# ── main entry point ──────────────────────────────────────────────────
async def send_media(
    message: Message,
    path: str,
    *,
    caption: str = "",
    kind: str = "",
    keyboard: Optional[InlineKeyboardMarkup] = None,
    filename: str = "",
    performer: str = "",
    title: str = "",
) -> Dict[str, Any]:
    """Send one local file with the richest presentation Telegram allows.

    Returns ``{"file_id", "file_type", "size", "meta", "message"}``.
    """
    size = mh.get_file_size(path)
    if size == 0:
        raise ValueError("downloaded file is empty")

    kind = kind or mh.guess_kind(path)
    meta: Dict[str, int] = {}
    thumb_path: Optional[str] = None

    if kind == "video":
        # Repair iOS-unplayable codecs BEFORE probing and thumbnailing: a
        # transcode changes the file (and its path), so metadata must describe
        # what actually gets uploaded. This is the single choke point every
        # platform's video passes through, so one call covers all 13 of them.
        path = await mh.ensure_ios_compatible(path)
        size = mh.get_file_size(path) or size
        meta = await mh.probe_media(path)
        thumb_path = await mh.make_thumbnail(path)

    # Route to MTProto when the file is too large for the Bot API.
    if size > config.BOT_API_LIMIT and config.USE_MTPROTO:
        file_id = await _send_large_via_mtproto(
            message.chat_id, path,
            caption=caption, kind=kind, meta=meta, thumb=thumb_path,
            reply_to=message.message_id,
        )
        if file_id is not None:
            if thumb_path:
                await mh.delete_file(thumb_path)
            # Attach the action keyboard as a follow-up: MTProto messages
            # cannot carry Bot API inline keyboards.
            if keyboard is not None:
                try:
                    await message.reply_text("⬇️", reply_markup=keyboard)
                except Exception:
                    pass
            return {
                "file_id": file_id, "file_type": kind, "size": size,
                "meta": meta, "message": None,
            }
        # MTProto failed — fall through to hosting/splitting rather than
        # leaving the user with nothing.
        logger.warning("MTProto upload failed; falling back for %s", path)

    if size > config.BOT_API_LIMIT:
        # No MTProto and no local Bot API server. Two credential-free routes
        # remain; prefer a real link over chopping the file into parts.
        if config.HOST_LARGE_FILES:
            hosted = await _send_hosted_link(
                message, path, caption=caption, kind=kind, meta=meta,
                thumb=thumb_path, keyboard=keyboard,
            )
            if hosted is not None:
                if thumb_path:
                    await mh.delete_file(thumb_path)
                return hosted
        # Last resort: split into Bot-API-sized parts.
        if thumb_path:
            await mh.delete_file(thumb_path)
        return await _send_split(message, path, caption=caption, keyboard=keyboard)

    common = {
        "caption": caption[:1024] or None,
        "parse_mode": ParseMode.HTML,
        "reply_markup": keyboard,
        "read_timeout": 300,
        "write_timeout": 600,
        "connect_timeout": 60,
        "pool_timeout": 60,
    }

    async def _do_send() -> Message:
        with open(path, "rb") as fh:
            if kind == "video":
                thumb_fh = open(thumb_path, "rb") if thumb_path else None
                try:
                    return await message.reply_video(
                        video=fh,
                        duration=meta.get("duration") or None,
                        width=meta.get("width") or None,
                        height=meta.get("height") or None,
                        thumbnail=thumb_fh,
                        supports_streaming=True,
                        filename=filename or None,
                        **common,
                    )
                finally:
                    if thumb_fh:
                        thumb_fh.close()
            if kind == "audio":
                ameta = await mh.probe_media(path)
                return await message.reply_audio(
                    audio=fh,
                    duration=ameta.get("duration") or None,
                    performer=performer or None,
                    title=title or None,
                    filename=filename or None,
                    **common,
                )
            if kind == "photo":
                return await message.reply_photo(photo=fh, **common)
            if kind == "animation":
                return await message.reply_animation(animation=fh, **common)
            return await message.reply_document(
                document=fh, filename=filename or None, **common
            )

    sent = await _with_flood_retry(_do_send)
    if thumb_path:
        await mh.delete_file(thumb_path)

    return {
        "file_id": extract_file_id(sent),
        "file_type": kind,
        "size": size,
        "meta": meta,
        "message": sent,
    }


async def send_cached(
    message: Message,
    cached: Dict[str, Any],
    *,
    caption: str = "",
    keyboard: Optional[InlineKeyboardMarkup] = None,
) -> Optional[Message]:
    """Re-send a previously uploaded file by its file_id (no download)."""
    kind = cached.get("file_type") or "document"
    file_id = cached["file_id"]
    common = {
        "caption": caption[:1024] or None,
        "parse_mode": ParseMode.HTML,
        "reply_markup": keyboard,
    }
    try:
        if kind == "video":
            return await message.reply_video(
                video=file_id,
                duration=cached.get("duration") or None,
                width=cached.get("width") or None,
                height=cached.get("height") or None,
                supports_streaming=True,
                **common,
            )
        if kind == "audio":
            return await message.reply_audio(audio=file_id, **common)
        if kind == "photo":
            return await message.reply_photo(photo=file_id, **common)
        if kind == "animation":
            return await message.reply_animation(animation=file_id, **common)
        return await message.reply_document(document=file_id, **common)
    except BadRequest as exc:
        # A stale file_id means the cache row is dead; the caller re-downloads.
        logger.info("cached file_id rejected (%s) — will re-download", exc)
        return None


async def send_album(
    message: Message,
    paths: List[str],
    *,
    caption: str = "",
) -> List[str]:
    """Send up to 10 items as one media group (Instagram carousels, tweets)."""
    file_ids: List[str] = []
    # Album videos bypass send_media, so they need the same iOS codec repair.
    paths = [
        await mh.ensure_ios_compatible(p) if mh.guess_kind(p) == "video" else p
        for p in paths
    ]
    for batch_start in range(0, len(paths), 10):
        batch = paths[batch_start:batch_start + 10]
        media: List[Any] = []
        handles = []
        try:
            for idx, p in enumerate(batch):
                fh = open(p, "rb")
                handles.append(fh)
                cap = caption[:1024] if (batch_start == 0 and idx == 0) else None
                if mh.guess_kind(p) == "video":
                    media.append(
                        InputMediaVideo(
                            media=fh, caption=cap, parse_mode=ParseMode.HTML,
                            supports_streaming=True,
                        )
                    )
                else:
                    media.append(
                        InputMediaPhoto(media=fh, caption=cap, parse_mode=ParseMode.HTML)
                    )
            sent = await _with_flood_retry(
                lambda: message.reply_media_group(
                    media=media, read_timeout=300, write_timeout=600
                )
            )
            for m in sent:
                fid = extract_file_id(m)
                if fid:
                    file_ids.append(fid)
        finally:
            for fh in handles:
                try:
                    fh.close()
                except Exception:
                    pass
    return file_ids


async def _send_hosted_link(
    message: Message,
    path: str,
    *,
    caption: str = "",
    kind: str = "",
    meta: Optional[Dict[str, int]] = None,
    thumb: Optional[str] = None,
    keyboard: Optional[InlineKeyboardMarkup] = None,
) -> Optional[Dict[str, Any]]:
    """Upload an oversized file to a public host and post the link + a preview.

    The user gets a tappable thumbnail (so the message still looks like media)
    plus a button that opens the direct download. Returns the standard result
    dict, or None if hosting failed so the caller can fall back to splitting.
    """
    from bot.utils import filehost

    hosted = await filehost.upload(path)
    if hosted is None:
        return None

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup as IKM

    size_txt = mh.human_size(hosted.size)
    expiry = f" · {hosted.expires}" if hosted.expires else ""
    body = (
        f"{caption}\n\n"
        f"📦 <b>{size_txt}</b> — از سقف تلگرام بزرگ‌تر است، پس لینک مستقیم:\n"
        f"🔗 {hosted.url}{expiry}"
    ).strip()

    link_target = hosted.direct or hosted.url
    buttons = [[InlineKeyboardButton("⬇️ دانلود فایل", url=link_target)]]
    if keyboard is not None:
        buttons.extend(keyboard.inline_keyboard)
    markup = IKM(buttons)

    sent = None
    if thumb and os.path.exists(thumb):
        try:
            with open(thumb, "rb") as fh:
                sent = await _with_flood_retry(
                    lambda fh=fh: message.reply_photo(
                        photo=fh, caption=body[:1024],
                        parse_mode=ParseMode.HTML, reply_markup=markup,
                    )
                )
        except Exception:
            logger.debug("hosted-link thumbnail send failed", exc_info=True)
    if sent is None:
        sent = await _with_flood_retry(
            lambda: message.reply_text(
                body[:4096], parse_mode=ParseMode.HTML,
                reply_markup=markup, disable_web_page_preview=False,
            )
        )

    return {
        "file_id": None, "file_type": kind or "document", "size": hosted.size,
        "meta": meta or {}, "message": sent, "hosted": hosted.host,
    }


async def _send_split(
    message: Message,
    path: str,
    *,
    caption: str = "",
    keyboard: Optional[InlineKeyboardMarkup] = None,
) -> Dict[str, Any]:
    """Fallback for oversized files without MTProto: send numbered parts."""
    if not config.ALLOW_SPLIT:
        raise ValueError("file too large and splitting is disabled")
    parts = await mh.split_file(path)
    total = len(parts)
    base = os.path.basename(path)
    try:
        for i, part in enumerate(parts, 1):
            cap = f"{caption}\n\n📦 <b>{i}/{total}</b>" if i == 1 else f"📦 <b>{i}/{total}</b>"
            with open(part, "rb") as fh:
                await _with_flood_retry(
                    lambda fh=fh, cap=cap, i=i: message.reply_document(
                        document=fh,
                        filename=f"{base}.part{i:03d}",
                        caption=cap[:1024],
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard if i == total else None,
                        read_timeout=300,
                        write_timeout=600,
                    )
                )
    finally:
        await mh.delete_file(*[p for p in parts if p != path])
    return {"file_id": None, "file_type": "document", "size": mh.get_file_size(path),
            "meta": {}, "message": None, "split": total}


# ── helpers ───────────────────────────────────────────────────────────
async def _with_flood_retry(factory, attempts: int = 4):
    """Honour Telegram's RetryAfter instead of hammering and getting limited."""
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            result = factory()
            if asyncio.iscoroutine(result):
                return await result
            return result
        except RetryAfter as exc:
            last = exc
            wait = float(getattr(exc, "retry_after", 3)) + 1
            logger.warning("Telegram flood wait %.0fs", wait)
            await asyncio.sleep(wait)
        except TimedOut as exc:
            last = exc
            await asyncio.sleep(2 * (attempt + 1))
    assert last is not None
    raise last


def extract_file_id(msg: Optional[Message]) -> Optional[str]:
    if msg is None:
        return None
    if msg.video:
        return msg.video.file_id
    if msg.audio:
        return msg.audio.file_id
    if msg.document:
        return msg.document.file_id
    if msg.animation:
        return msg.animation.file_id
    if msg.photo:
        return msg.photo[-1].file_id
    if msg.voice:
        return msg.voice.file_id
    return None
