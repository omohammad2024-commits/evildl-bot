"""Live activity feed — the owner sees what users do, as it happens.

The inbox is a pull surface: the owner has to open it to learn anything. This is
the push half. Every meaningful user action turns into a message in the owner's
chat within seconds, with buttons to act on it.

Why this is a queue and not a plain ``send_message`` at each event site:

* **Flood protection.** Telegram throttles a bot to roughly one message per
  second per chat. A burst of 30 users hitting /start would either get the feed
  rate-limited into oblivion or bury real messages. Events are collected for a
  short window and delivered as ONE grouped message.
* **Never slow a download.** ``notify`` only appends to a deque and returns, so
  no user-facing path ever waits on a feed send.
* **Never break the bot.** Every failure is swallowed; a broken feed must not
  stop a download.

The feed deliberately carries no media — only text and buttons. ``/data`` is a
434MB volume, so re-sending files here is not an option; the buttons link back
into the inbox, where the stored ``file_id`` can pull the real file on demand.

**Private chats only.** Group traffic never reaches the feed. The bot sits in
groups where people talk all day, and relaying that would bury the owner in
noise. Group messages are still written to the inbox log, so history stays
searchable — only the push is suppressed. The gate lives at each notify site
(``inbox.record`` checks ``chat.type``, ``db.add_download`` takes a ``chat_type``
argument) rather than here, because this module never sees the update.
"""
import asyncio
import logging
import time
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

# Pending events, oldest first. Bounded: if the bot is so busy that the flusher
# falls behind, dropping the oldest feed lines is strictly better than growing
# without limit.
_queue: deque = deque(maxlen=200)
_task: Optional[asyncio.Task] = None
_bot = None
_dropped = 0

# Coalescing window. Long enough to group a burst into one message, short enough
# that "same moment" still feels true.
_WINDOW = 2.5

# Hard ceiling on lines per delivered message, so one burst cannot produce a
# message over Telegram's 4096-character limit.
_MAX_LINES = 18

_ICON = {
    "new_user": "🆕",
    "message": "💬",
    "download": "✅",
    "failed": "❌",
    "media": "📎",
    "inline": "⚡",
}


def attach(bot) -> None:
    """Remember the bot instance so events can be delivered without a context."""
    global _bot
    _bot = bot


def notify(kind: str, *, user_id: int, name: str = "", username: str = "",
           detail: str = "", platform: str = "") -> None:
    """Record one activity event. Cheap, synchronous, never raises.

    Deliberately NOT async: it is called from hot paths (every incoming message,
    every finished download) and must not introduce an await point there.
    """
    global _dropped
    try:
        # The owner's own activity is not news to the owner. Without this every
        # download the owner runs would notify them about themselves — noisy
        # enough to make the feed useless.
        from bot.config import ADMIN_IDS

        if user_id in ADMIN_IDS:
            return
        if len(_queue) == _queue.maxlen:
            _dropped += 1
        _queue.append({
            "kind": kind, "user_id": user_id, "name": name or "",
            "username": username or "", "detail": detail or "",
            "platform": platform or "", "at": time.time(),
        })
        _ensure_task()
    except Exception as exc:  # a feed bug must never touch the caller
        logger.debug("livefeed notify skipped: %s", exc)


def _ensure_task() -> None:
    """Start the flusher lazily, inside whatever loop is running."""
    global _task
    if _task is not None and not _task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop yet (import time) — the next notify will start it
    _task = loop.create_task(_flusher())


def _esc(text: str) -> str:
    return (str(text or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _clip(text: str, limit: int = 70) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _who(event: dict) -> str:
    if event.get("username"):
        return f"@{event['username']}"
    return event.get("name") or str(event.get("user_id"))


async def _flusher() -> None:
    """Drain the queue in coalesced batches until it stays empty."""
    while True:
        await asyncio.sleep(_WINDOW)
        if not _queue:
            # Nothing pending: end the task and let the next notify restart it,
            # so an idle bot has no ticking timer.
            return
        try:
            if not await enabled():
                _queue.clear()
                continue
            batch = []
            while _queue and len(batch) < _MAX_LINES:
                batch.append(_queue.popleft())
            if not batch:
                continue
            target = await feed_chat()
            if not target:
                continue
            await _deliver(target, batch)
        except Exception as exc:
            logger.debug("livefeed flush failed: %s", exc)


async def enabled() -> bool:
    """Runtime switch, owner-toggleable from the panel."""
    try:
        from bot.database import db

        value = await db.get_setting("live_feed", "")
        if value == "":
            from bot import config

            return config.LIVE_FEED_DEFAULT
        return value == "1"
    except Exception:
        return False


async def feed_chat() -> int:
    """Where the feed goes: an explicit chat if configured, else the owner DM."""
    try:
        from bot import config

        if config.FEED_CHAT:
            return int(config.FEED_CHAT)
        return int(config.ADMIN_IDS[0]) if config.ADMIN_IDS else 0
    except Exception:
        return 0


async def _deliver(chat_id: int, batch: list) -> None:
    """Render and send one grouped feed message."""
    global _dropped
    if _bot is None:
        return

    from bot.i18n import get_text
    from bot.database import db

    lang = "fa"
    try:
        lang = await db.get_user_language(chat_id)
    except Exception:
        pass

    lines = [get_text("FEED_TITLE", lang)]
    for event in batch:
        icon = _ICON.get(event["kind"], "•")
        who = _esc(_who(event))
        detail = _esc(_clip(event["detail"]))
        platform = f" · {_esc(event['platform'])}" if event.get("platform") else ""
        lines.append(
            f"{icon} <b>{who}</b>{platform}\n"
            f"   🆔 <code>{event['user_id']}</code>"
            + (f"\n   {detail}" if detail else ""))

    if _dropped:
        lines.append(get_text("FEED_DROPPED", lang, count=_dropped))
        _dropped = 0

    text = "\n".join(lines)

    # Buttons only make sense for a single subject; a mixed batch links to the
    # inbox instead of guessing which user the owner meant.
    users = {e["user_id"] for e in batch}
    markup = None
    try:
        from bot.keyboards.inline import feed_keyboard

        markup = feed_keyboard(lang, next(iter(users)) if len(users) == 1 else 0)
    except Exception as exc:
        logger.debug("feed keyboard skipped: %s", exc)

    try:
        from telegram.constants import ParseMode

        await _bot.send_message(chat_id, text, parse_mode=ParseMode.HTML,
                                reply_markup=markup,
                                disable_web_page_preview=True)
    except Exception as exc:
        logger.debug("livefeed send failed: %s", exc)
