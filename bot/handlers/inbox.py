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
    failure here can never break an actual download.
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
        await db.log_message(
            user_id=user.id, chat_id=chat.id, chat_type=chat.type,
            msg_id=message.message_id, kind=kind,
            text=_preview(message, kind), direction="in")
    except Exception as exc:  # logging must never break the bot
        logger.debug("inbox record skipped: %s", exc)


async def record_out(user_id: int, chat_id: int, text: str) -> None:
    """Log an owner reply so the thread shows both sides."""
    try:
        await db.log_message(user_id=user_id, chat_id=chat_id,
                             chat_type="private", msg_id=0, kind="out",
                             text=text, direction="out")
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


async def inbox_text(lang: str, page: int = 0) -> str:
    """The chat-list screen."""
    from bot.utils import theme

    total = await db.count_chats()
    rows = await db.recent_chats(PAGE, page * PAGE)
    div = await theme.divider()
    head = get_text("INBOX_TITLE", lang) + "\n" + div + "\n"
    if not rows:
        return head + get_text("INBOX_EMPTY", lang)

    lines = [head, get_text("INBOX_COUNT", lang, count=total), ""]
    for row in rows:
        icon = _KIND_ICON.get(row.get("last_kind") or "text", "💬")
        lines.append(
            f"{icon} <b>{_esc(_who(row))}</b> · {row.get('total', 0)}\n"
            f"   <i>{_esc(_clip(row.get('last_text') or ''))}</i>")
    return "\n".join(lines)


async def thread_text(user_id: int, lang: str, page: int = 0) -> str:
    """One conversation, rendered oldest-last."""
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
        stamp = str(row.get("created_at") or "")[5:16]
        if row.get("direction") == "out":
            lines.append(f"↩️ <i>{stamp}</i>\n   {_esc(row.get('text') or '')}")
        else:
            icon = _KIND_ICON.get(row.get("kind") or "text", "💬")
            lines.append(f"{icon} <i>{stamp}</i>\n   {_esc(row.get('text') or '')}")
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
    """Results for a message search."""
    from bot.utils import theme

    rows = await db.search_messages(term, 20)
    div = await theme.divider()
    head = get_text("INBOX_SEARCH_TITLE", lang) + "\n" + div
    if not rows:
        return head + "\n" + get_text("INBOX_SEARCH_EMPTY", lang)
    lines = [head, ""]
    for row in rows:
        icon = _KIND_ICON.get(row.get("kind") or "text", "💬")
        stamp = str(row.get("created_at") or "")[5:16]
        lines.append(
            f"{icon} <b>{_esc(_who(row))}</b> <i>{stamp}</i>\n"
            f"   {_esc(_clip(row.get('text') or '', 70))}")
    return "\n".join(lines)


async def do_reply(context: ContextTypes.DEFAULT_TYPE, user_id: int,
                   text: str, lang: str) -> str:
    """Send the owner's reply to a user and log it."""
    try:
        await context.bot.send_message(user_id, text)
    except Exception as exc:
        return get_text("INBOX_REPLY_FAIL", lang, error=_esc(str(exc)[:120]))
    await record_out(user_id, user_id, text)
    return get_text("INBOX_REPLY_OK", lang)
