"""Inline mode: use the bot inside ANY chat by typing @Bot <link>.

Two paths, chosen by config.INLINE_LIVE_MEDIA:

* LIVE (default): the result carries an inline keyboard so Telegram gives us an
  ``inline_message_id``. When the user picks it we download in the background,
  upload once to the storage chat to mint a Bot-API file_id, and edit the sent
  message IN PLACE into the real video/photo — so the media appears right in the
  chat the user is typing in, no private-chat hop.
* Cached results are offered as ready-to-send media instantly.
* If live media is off or the file is too big, we fall back to a deep-link
  button that opens the bot and runs the normal download with a quality menu.
"""
import asyncio
import logging
import os
from typing import List

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultVideo,
    InlineQueryResultPhoto,
    InputMediaPhoto,
    InputMediaVideo,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot import config
from bot.database import db
from bot.i18n import get_text
from bot.utils.media_handler import delete_file, get_file_size
from bot.utils.url_parser import detect_platform, extract_urls, get_service, platform_label

logger = logging.getLogger(__name__)

# Cache the bot username: get_me() is a network round trip and inline queries
# fire on every keystroke, so calling it each time makes the box spin.
_BOT_USERNAME: str = ""


async def _username(context) -> str:
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        me = await context.bot.get_me()
        _BOT_USERNAME = me.username
    return _BOT_USERNAME


def _deeplink(username: str, url: str) -> str:
    """Deep link that reruns the download in the bot's private chat.

    The URL is stashed in a short token so it fits Telegram's start-param
    limit (64 chars, base64-ish only).
    """
    import base64

    token = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"https://t.me/{username}?start=dl_{token}"


async def inline_query_handler(update: Update,
                               context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query
    if query is None:
        return
    user = update.effective_user
    text = (query.query or "").strip()
    username = await _username(context)

    urls = extract_urls(text, 1)
    if not urls:
        # Guide the user instead of an empty box. No DB/network work here so the
        # box never spins while the user is still typing.
        lang = "fa"
        await query.answer(
            [
                InlineQueryResultArticle(
                    id="help",
                    title=get_text("INLINE_HELP_TITLE", lang),
                    description=get_text("INLINE_HELP_DESC", lang),
                    input_message_content=InputTextMessageContent(
                        get_text("INLINE_HELP_MSG", lang, username=username),
                        parse_mode=ParseMode.HTML,
                    ),
                )
            ],
            cache_time=30, is_personal=False,
        )
        return

    url = urls[0]
    platform = detect_platform(url)
    label = platform_label(platform)
    lang = "fa"
    results: List = []

    # Best case: we already have this file cached -> send it inline instantly.
    cached = await db.get_cached(url, "best")
    if cached and cached.get("file_id"):
        kind = cached.get("file_type")
        title = cached.get("title") or label
        try:
            if kind == "video":
                results.append(
                    InlineQueryResultVideo(
                        id="cached_v",
                        video_file_id=cached["file_id"],
                        title=title[:60],
                        caption=f"{title[:200]}\n\n@{username}",
                    )
                )
            elif kind == "photo":
                results.append(
                    InlineQueryResultPhoto(
                        id="cached_p",
                        photo_file_id=cached["file_id"],
                        caption=f"{title[:200]}\n\n@{username}",
                    )
                )
        except Exception as exc:
            logger.debug("inline cached result build failed: %s", exc)

    # Live media path: attach an inline keyboard so Telegram returns an
    # inline_message_id we can later edit in place with the real file.
    if config.INLINE_LIVE_MEDIA:
        import base64

        token = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton(get_text("INLINE_WORKING_BTN", lang),
                                   callback_data="noop")]]
        )
        results.append(
            InlineQueryResultArticle(
                id=f"live_{token[:48]}",
                title=get_text("INLINE_FETCH_TITLE", lang, platform=label),
                description=get_text("INLINE_LIVE_DESC", lang),
                input_message_content=InputTextMessageContent(
                    get_text("INLINE_LIVE_MSG", lang, platform=label),
                    parse_mode=ParseMode.HTML,
                ),
                reply_markup=kb,
            )
        )
    else:
        # Deep-link fallback: open the bot and download there.
        results.append(
            InlineQueryResultArticle(
                id="fetch",
                title=get_text("INLINE_FETCH_TITLE", lang, platform=label),
                description=get_text("INLINE_FETCH_DESC", lang),
                input_message_content=InputTextMessageContent(
                    get_text("INLINE_FETCH_MSG", lang, url=url, username=username),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                ),
            )
        )

    await query.answer(results, cache_time=0, is_personal=True)


async def chosen_inline_handler(update: Update,
                                context: ContextTypes.DEFAULT_TYPE) -> None:
    """After the user picks a live result: download, then edit media in place.

    Telegram only gives us the inline_message_id here (not a chat), so we edit
    the already-sent message. To get a Bot-API file_id for a fresh download we
    upload it once to the storage chat, then reuse that file_id in the edit.
    """
    chosen = update.chosen_inline_result
    if chosen is None or not config.INLINE_LIVE_MEDIA:
        return
    imid = chosen.inline_message_id
    logger.info("chosen_inline_result fired: result_id=%s imid=%s query=%s",
                chosen.result_id, bool(imid), (chosen.query or "")[:60])
    if not imid or not chosen.result_id.startswith("live_"):
        return

    urls = extract_urls(chosen.query or "", 1)
    if not urls:
        return
    url = urls[0]
    platform = detect_platform(url)
    lang = "fa"

    async def _edit_text(key: str, **kw):
        try:
            await context.bot.edit_message_text(
                get_text(key, lang, **kw), inline_message_id=imid,
                parse_mode=ParseMode.HTML)
        except Exception:
            pass

    # 1. Cache hit -> edit straight to the media, no download.
    cached = await db.get_cached(url, "best")
    if cached and cached.get("file_id"):
        await _edit_media_from_file_id(context, imid, cached["file_id"],
                                       cached.get("file_type", "video"),
                                       cached.get("title") or platform_label(platform))
        return

    service = get_service(platform)
    if service is None:
        await _edit_text("INLINE_LIVE_FAIL")
        return

    paths: List[str] = []
    try:
        result = await service.download(url, "best")
        if not result.items:
            # Text-only post (e.g. a tweet with no media): show the text
            # instead of a failure message.
            if result.extra.get("text_only") and result.text:
                await _edit_text("TEXT_ONLY_POST", text=result.text[:900])
            else:
                await _edit_text("INLINE_LIVE_FAIL")
            return
        item = result.items[0]
        paths = [it.path for it in result.items]
        size = get_file_size(item.path)
        if size == 0 or size > config.INLINE_MAX_MB * 1024 * 1024:
            # Too big for the fast inline path — point to the bot instead.
            username = await _username(context)
            import base64
            tok = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton(get_text("INLINE_OPEN_BTN", lang),
                                       url=f"https://t.me/{username}?start=dl_{tok}")]]
            )
            try:
                await context.bot.edit_message_text(
                    get_text("INLINE_TOO_BIG", lang), inline_message_id=imid,
                    parse_mode=ParseMode.HTML, reply_markup=kb)
            except Exception:
                pass
            return

        # 2. Upload once to the storage chat to mint a reusable file_id.
        title = result.title or platform_label(platform)
        caption = f"{title[:180]}"
        file_id, kind = await _mint_file_id(context, item.path, item.kind, caption)
        if not file_id:
            await _edit_text("INLINE_LIVE_FAIL")
            return

        # 3. Cache and edit the inline message into the real media.
        await db.put_cached(url, "best", file_id, kind, platform=platform,
                            title=title, file_size=size)
        await _edit_media_from_file_id(context, imid, file_id, kind, title)
    except Exception as exc:
        logger.warning("inline live fetch failed for %s: %s", url, exc)
        await _edit_text("INLINE_LIVE_FAIL")
    finally:
        if paths:
            await delete_file(*paths)


async def _mint_file_id(context, path: str, kind: str, caption: str):
    """Upload the file once to the storage chat and return its Bot-API file_id."""
    with open(path, "rb") as fh:
        if kind == "video":
            m = await context.bot.send_video(config.STORAGE_CHAT, video=fh,
                                             caption=caption, supports_streaming=True)
            return (m.video.file_id if m.video else None), "video"
        if kind == "photo":
            m = await context.bot.send_photo(config.STORAGE_CHAT, photo=fh,
                                             caption=caption)
            return (m.photo[-1].file_id if m.photo else None), "photo"
        if kind == "audio":
            m = await context.bot.send_audio(config.STORAGE_CHAT, audio=fh,
                                             caption=caption)
            return (m.audio.file_id if m.audio else None), "audio"
        m = await context.bot.send_document(config.STORAGE_CHAT, document=fh,
                                            caption=caption)
        return (m.document.file_id if m.document else None), "document"


async def _edit_media_from_file_id(context, imid: str, file_id: str,
                                   kind: str, title: str) -> None:
    username = await _username(context)
    caption = f"{title[:180]}\n\n@{username}"
    media = None
    if kind == "video":
        media = InputMediaVideo(file_id, caption=caption)
    elif kind == "photo":
        media = InputMediaPhoto(file_id, caption=caption)
    if media is None:
        try:
            await context.bot.edit_message_text(
                f"{title}\n\n@{username}", inline_message_id=imid)
        except Exception:
            pass
        return
    try:
        await context.bot.edit_message_media(media=media, inline_message_id=imid)
    except Exception as exc:
        logger.debug("edit_message_media failed: %s", exc)
