"""Owner inbox: read the bot's private chats like a normal account.

The Bot API gives a bot no way to browse its own dialogs, so the bot records what
it receives (``db.log_message``) and this module renders that log as a familiar
two-level view:

* **Chat list** — one row per user, newest traffic first, with the last message.
* **Thread** — a single user's messages, oldest-last so it reads like a chat,
  with paging and a reply button.

The owner can answer straight from the thread: replies go out as a normal bot
message, so the user just sees the bot talking to them.
"""
import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot import config
from bot.database import db
from bot.i18n import get_text

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS

PAGE = 10          # chats per inbox page
THREAD_PAGE = 20   # messages per thread page

# What to log per message type. Text/captions are stored; media is noted by kind
# so the owner sees "sent a photo" without the bot hoarding files.
_KINDS = (
    ("text", "text"),
    ("photo", "photo"),
    ("video", "video"),
    ("animation", "animation"),
    ("audio", "audio"),
    ("voice", "voice"),
    ("video_note", "video_note"),
    ("document", "document"),
    ("sticker", "sticker"),
    ("contact", "contact"),
    ("location", "location"),
    ("poll", "poll"),
)

_KIND_ICON = {
    "text": "💬", "photo": "🖼", "video": "🎬", "animation": "🎞",
    "audio": "🎵", "voice": "🎤", "video_note": "📹", "document": "📎",
    "sticker": "🌟", "contact": "📞", "location": "📍", "poll": "📊",
    "command": "⌨️", "out": "📤",
}

# Media kinds whose file_id can be re-sent straight back out of the inbox.
# Mapped to the Bot API method that delivers each one.
_SENDABLE = {
    "photo": "send_photo",
    "video": "send_video",
    "animation": "send_animation",
    "audio": "send_audio",
    "voice": "send_voice",
    "video_note": "send_video_note",
    "document": "send_document",
    "sticker": "send_sticker",
}


def _file_ref(message, kind: str) -> tuple:
    """(file_id, file_type) for a media message, or ("", "") for text.

    Photos arrive as a size ladder; the last entry is the largest, which is what
    the owner wants when re-sending.
    """
    try:
        if kind == "photo" and message.photo:
            return message.photo[-1].file_id, "photo"
        obj = getattr(message, kind, None)
        if obj is not None and hasattr(obj, "file_id"):
            return obj.file_id, kind
    except Exception:
        pass
    return "", ""


def _classify(message) -> str:
    """The message kind, e.g. 'text', 'photo', 'command'."""
    text = message.text or ""
    if text.startswith("/"):
        return "command"
    for attr, kind in _KINDS:
        if getattr(message, attr, None):
            return kind
    return "other"


def _preview(message, kind: str) -> str:
    """The text stored for this message: real text, caption, or a media note."""
    if message.text:
        return message.text
    if message.caption:
        return message.caption
    if kind == "sticker":
        emoji = getattr(message.sticker, "emoji", "") or ""
        return f"[sticker {emoji}]".strip()
    return f"[{kind}]"


async def record(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log every incoming message. Registered in group -1 so it never blocks.

    Runs before the download handler and always returns without answering, so a
    failure here can never break an actual download. Media is logged with its
    file_id so the owner can pull the real file back out of the inbox later.
    """
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    # The owner's own messages are not interesting in their own inbox.
    if is_admin(user.id):
        return
    try:
        kind = _classify(message)
        file_id, file_type = _file_ref(message, kind)
        reply_to = 0
        try:
            if message.reply_to_message:
                reply_to = message.reply_to_message.message_id
        except Exception:
            pass
        await db.log_message(
            user_id=user.id, chat_id=chat.id, chat_type=chat.type,
            msg_id=message.message_id, kind=kind,
            text=_preview(message, kind), direction="in",
            file_id=file_id, file_type=file_type, reply_to=reply_to)
    except Exception as exc:  # logging must never break the bot
        logger.debug("inbox record skipped: %s", exc)


async def record_out(user_id: int, chat_id: int, text: str,
                     kind: str = "out", file_id: str = "",
                     file_type: str = "") -> None:
    """Log an owner reply so the thread shows both sides."""
    try:
        await db.log_message(user_id=user_id, chat_id=chat_id,
                             chat_type="private", msg_id=0, kind=kind,
                             text=text, direction="out",
                             file_id=file_id, file_type=file_type)
    except Exception as exc:
        logger.debug("inbox reply log skipped: %s", exc)


def _who(row: dict) -> str:
    """A display name for a chat row: @username, first name, or the id."""
    username = row.get("username")
    if username:
        return f"@{username}"
    name = row.get("first_name")
    if name:
        return str(name)
    return f"ID {row.get('user_id')}"


def _clip(text: str, limit: int = 60) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _esc(text: str) -> str:
    return (str(text or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _ago(stamp: str) -> str:
    """'2m', '5h', '3d' — relative time, the way a messenger shows it.

    ``created_at`` is a SQLite ``CURRENT_TIMESTAMP``, i.e. UTC without a zone
    marker, so it is parsed as UTC explicitly rather than as local time.
    """
    from datetime import datetime, timezone

    raw = str(stamp or "")[:19]
    if not raw:
        return ""
    try:
        when = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return raw[5:16]
    delta = (datetime.now(timezone.utc) - when).total_seconds()
    if delta < 60:
        return "now"
    if delta < 3600:
        return f"{int(delta // 60)}m"
    if delta < 86400:
        return f"{int(delta // 3600)}h"
    if delta < 604800:
        return f"{int(delta // 86400)}d"
    return raw[5:10]


async def inbox_text(lang: str, page: int = 0) -> str:
    """The chat-list screen: every private conversation, unread first-class."""
    from bot.utils import theme

    total = await db.count_chats()
    unread = await db.unread_total()
    rows = await db.recent_chats(PAGE, page * PAGE)
    div = await theme.divider()
    head = get_text("INBOX_TITLE", lang) + "\n" + div + "\n"
    if not rows:
        return head + get_text("INBOX_EMPTY", lang)

    lines = [head, get_text("INBOX_COUNT", lang, count=total)]
    if unread:
        lines.append(get_text("INBOX_UNREAD_LINE", lang, count=unread))
    lines.append("")
    for row in rows:
        icon = _KIND_ICON.get(row.get("last_kind") or "text", "💬")
        n_unread = int(row.get("unread") or 0)
        # An unread row is bolded and badged; a row whose last message is the
        # owner's own reply is marked so it's clear who spoke last.
        badge = f" <b>({n_unread})</b>" if n_unread else ""
        arrow = "↩️ " if (row.get("last_dir") == "out") else ""
        when = _ago(row.get("last_at") or "")
        lines.append(
            f"{icon} <b>{_esc(_who(row))}</b>{badge} · {row.get('total', 0)}"
            f" · <i>{when}</i>\n"
            f"   {arrow}<i>{_esc(_clip(row.get('last_text') or ''))}</i>")
    return "\n".join(lines)


async def thread_text(user_id: int, lang: str, page: int = 0) -> str:
    """One conversation, rendered oldest-last so it reads like a chat."""
    from bot.utils import theme

    total = await db.count_conversation(user_id)
    # Page 0 must show the NEWEST messages, so offset from the end.
    offset = max(0, total - THREAD_PAGE * (page + 1))
    limit = THREAD_PAGE if offset else max(0, total - THREAD_PAGE * page)
    rows = await db.conversation(user_id, limit or THREAD_PAGE, offset)
    profile = await db.get_user(user_id) or {}
    div = await theme.divider()

    who = _who({"username": profile.get("username"),
                "first_name": profile.get("first_name"),
                "user_id": user_id})
    head = (get_text("INBOX_THREAD_TITLE", lang, who=_esc(who)) + "\n" + div +
            "\n" + get_text("INBOX_THREAD_META", lang, id=user_id, count=total))
    if not rows:
        return head + "\n\n" + get_text("INBOX_EMPTY", lang)

    lines = [head, ""]
    for row in rows:
        when = _ago(row.get("created_at") or "")
        star = " ⭐" if row.get("starred") else ""
        body = _esc(row.get("text") or "")
        # Media rows advertise that the real file is one tap away; the star code
        # lets the owner pin any row. Both are answered by ``code_handler``.
        codes = []
        if row.get("file_id"):
            codes.append(f"{get_text('INBOX_TAP_MEDIA', lang)} <code>/m{row.get('id')}</code>")
        codes.append(f"<code>/s{row.get('id')}</code>")
        body += "\n   <i>" + " · ".join(codes) + "</i>"
        if row.get("direction") == "out":
            lines.append(f"↩️ <i>{when}</i>{star}\n   {body}")
        else:
            icon = _KIND_ICON.get(row.get("kind") or "text", "💬")
            lines.append(f"{icon} <i>{when}</i>{star}\n   {body}")
    return "\n".join(lines)


async def gallery_text(user_id: int, lang: str) -> str:
    """Every file a user sent, as a numbered list the owner can pull from."""
    from bot.utils import theme

    rows = await db.media_in_thread(user_id, 30)
    profile = await db.get_user(user_id) or {}
    who = _who({"username": profile.get("username"),
                "first_name": profile.get("first_name"),
                "user_id": user_id})
    div = await theme.divider()
    head = get_text("INBOX_GALLERY_TITLE", lang, who=_esc(who)) + "\n" + div
    if not rows:
        return head + "\n" + get_text("INBOX_GALLERY_EMPTY", lang)
    lines = [head, "", get_text("INBOX_GALLERY_HINT", lang), ""]
    for row in rows:
        icon = _KIND_ICON.get(row.get("kind") or "document", "📎")
        lines.append(
            f"{icon} <code>/m{row.get('id')}</code> · <i>{_ago(row.get('created_at') or '')}</i>"
            f"\n   {_esc(_clip(row.get('text') or '', 50))}")
    return "\n".join(lines)


async def starred_text(lang: str) -> str:
    """The owner's starred shortlist across every conversation."""
    from bot.utils import theme

    rows = await db.starred_messages(30)
    div = await theme.divider()
    head = get_text("INBOX_STARRED_TITLE", lang) + "\n" + div
    if not rows:
        return head + "\n" + get_text("INBOX_STARRED_EMPTY", lang)
    lines = [head, ""]
    for row in rows:
        icon = _KIND_ICON.get(row.get("kind") or "text", "💬")
        media = " 📎" if row.get("file_id") else ""
        # Only offer /m for rows that actually have a file; every row gets /s so
        # it can be un-starred from here.
        codes = ([f"<code>/m{row.get('id')}</code>"] if row.get("file_id") else [])
        codes.append(f"<code>/s{row.get('id')}</code>")
        lines.append(
            f"{icon} <b>{_esc(_who(row))}</b>{media} · <i>{_ago(row.get('created_at') or '')}</i>"
            f"\n   {_esc(_clip(row.get('text') or '', 70))}"
            f"\n   " + " · ".join(codes))
    return "\n".join(lines)


async def user_card(user_id: int, lang: str) -> str:
    """A compact profile for one user, shown from their thread."""
    from bot.utils import theme
    from bot.utils.media_handler import human_size

    profile = await db.get_user(user_id) or {}
    div = await theme.divider()
    who = _who({"username": profile.get("username"),
                "first_name": profile.get("first_name"),
                "user_id": user_id})
    total = await db.get_user_downloads(user_id)
    msgs = await db.count_conversation(user_id)
    banned = await db.is_banned(user_id)
    lines = [
        get_text("INBOX_THREAD_TITLE", lang, who=_esc(who)),
        div,
        get_text("INBOX_CARD", lang,
                 id=user_id,
                 joined=str(profile.get("created_at") or "—")[:16],
                 lang_code=profile.get("language") or "—",
                 downloads=total,
                 messages=msgs,
                 banned=get_text("YES" if banned else "NO", lang)),
    ]
    recent = await db.get_user_recent(user_id, 5)
    if recent:
        lines.append("")
        lines.append(get_text("INBOX_CARD_RECENT", lang))
        for row in recent:
            lines.append(f"• {_esc(_clip(row.get('url') or '', 46))}")
    return "\n".join(lines)


async def search_text(term: str, lang: str) -> str:
    """Results for a message search, with pull/star codes on each hit."""
    from bot.utils import theme

    rows = await db.search_messages(term, 20)
    div = await theme.divider()
    head = get_text("INBOX_SEARCH_TITLE", lang) + "\n" + div
    if not rows:
        return head + "\n" + get_text("INBOX_SEARCH_EMPTY", lang)
    lines = [head, ""]
    for row in rows:
        icon = _KIND_ICON.get(row.get("kind") or "text", "💬")
        codes = ([f"<code>/m{row.get('id')}</code>"] if row.get("file_id") else [])
        codes.append(f"<code>/s{row.get('id')}</code>")
        lines.append(
            f"{icon} <b>{_esc(_who(row))}</b> <i>{_ago(row.get('created_at') or '')}</i>\n"
            f"   {_esc(_clip(row.get('text') or '', 70))}\n"
            f"   " + " · ".join(codes))
    return "\n".join(lines)


async def send_logged_media(context: ContextTypes.DEFAULT_TYPE,
                            to_chat: int, msg_row_id: int, lang: str) -> str:
    """Re-send a logged message's media to the owner from its stored file_id.

    Telegram file_ids never expire, so the actual photo/video/voice a user sent
    is retrievable long after the fact without the bot storing the bytes. A
    file_id can still be rejected (deleted by the sender, or minted by a
    different bot token), so failure is reported instead of raising.
    """
    row = await db.message_by_id(msg_row_id)
    if row is None:
        return get_text("INBOX_MEDIA_MISSING", lang)
    file_id = row.get("file_id")
    if not file_id:
        return get_text("INBOX_MEDIA_NONE", lang)

    kind = row.get("file_type") or row.get("kind") or "document"
    method_name = _SENDABLE.get(kind, "send_document")
    method = getattr(context.bot, method_name, None)
    if method is None:
        return get_text("INBOX_MEDIA_NONE", lang)

    who = _who(row)
    caption = get_text("INBOX_MEDIA_CAPTION", lang, who=_esc(who),
                       when=_ago(row.get("created_at") or ""))
    # send_sticker and send_video_note take no caption.
    kwargs = {} if kind in ("sticker", "video_note") else {
        "caption": caption, "parse_mode": ParseMode.HTML}
    try:
        await method(to_chat, file_id, **kwargs)
    except Exception as exc:
        logger.info("inbox media re-send failed for row %s: %s", msg_row_id, exc)
        return get_text("INBOX_MEDIA_FAIL", lang, error=_esc(str(exc)[:120]))
    return ""


async def code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the ``/m<id>`` and ``/s<id>`` shortcuts shown in inbox screens.

    The thread and gallery views print codes like ``/m412`` next to each row.
    Those are only useful if something answers them, so this handler pulls the
    media (``/m``) or toggles the star (``/s``) for that row id. Owner-only and
    DM-only, matching every other inbox surface.
    """
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not is_admin(user.id):
        return
    chat = update.effective_chat
    if chat is not None and chat.type in ("group", "supergroup"):
        return

    raw = (message.text or "").strip()
    if len(raw) < 3 or raw[0] != "/" or raw[1] not in ("m", "s"):
        return
    try:
        row_id = int(raw[2:])
    except ValueError:
        return

    lang = await db.get_user_language(user.id)
    if raw[1] == "m":
        err = await send_logged_media(context, user.id, row_id, lang)
        if err:
            await message.reply_text(err, parse_mode=ParseMode.HTML)
        return

    row = await db.message_by_id(row_id)
    if row is None:
        await message.reply_text(get_text("INBOX_MEDIA_MISSING", lang),
                                 parse_mode=ParseMode.HTML)
        return
    now_starred = await db.toggle_star(row_id)
    await message.reply_text(
        get_text("INBOX_STAR_ON" if now_starred else "INBOX_STAR_OFF", lang),
        parse_mode=ParseMode.HTML)


async def do_reply(context: ContextTypes.DEFAULT_TYPE, user_id: int,
                   text: str, lang: str) -> str:
    """Send the owner's text reply to a user and log it."""
    try:
        await context.bot.send_message(user_id, text)
    except Exception as exc:
        return get_text("INBOX_REPLY_FAIL", lang, error=_esc(str(exc)[:120]))
    await record_out(user_id, user_id, text)
    return get_text("INBOX_REPLY_OK", lang)


async def do_reply_media(context: ContextTypes.DEFAULT_TYPE, user_id: int,
                         message, lang: str) -> str:
    """Forward whatever the owner just sent — any media type — to a user.

    The owner replying with a photo, voice note, sticker, or file is the whole
    point of a messenger, so the reply path accepts every kind ``_classify``
    recognises instead of text only. The media is passed by file_id, so nothing
    is downloaded or re-uploaded.
    """
    kind = _classify(message)
    if kind in ("text", "command"):
        return await do_reply(context, user_id, message.text or "", lang)

    file_id, file_type = _file_ref(message, kind)
    if not file_id:
        # Types with no file_id (contact, location, poll) are forwarded whole.
        try:
            await context.bot.copy_message(user_id, message.chat_id,
                                           message.message_id)
        except Exception as exc:
            return get_text("INBOX_REPLY_FAIL", lang, error=_esc(str(exc)[:120]))
        await record_out(user_id, user_id, f"[{kind}]", kind=kind)
        return get_text("INBOX_REPLY_OK", lang)

    method = getattr(context.bot, _SENDABLE.get(kind, "send_document"), None)
    if method is None:
        return get_text("INBOX_REPLY_FAIL", lang, error=kind)
    caption = message.caption or ""
    kwargs = {} if kind in ("sticker", "video_note") else {"caption": caption}
    try:
        await method(user_id, file_id, **kwargs)
    except Exception as exc:
        return get_text("INBOX_REPLY_FAIL", lang, error=_esc(str(exc)[:120]))
    await record_out(user_id, user_id, caption or f"[{kind}]",
                     kind=kind, file_id=file_id, file_type=file_type)
    return get_text("INBOX_REPLY_OK", lang)

# NOTE: no broadcast helper lives here on purpose. ``admin.run_broadcast``
# already does it properly — it filters banned users, reports live progress,
# uses copy_message so any media type carries over untouched, and records the
# run via ``db.log_broadcast``. The inbox links to that instead of shipping a
# second, weaker implementation.
