"""Global outgoing-message logger — the bot's own half of every conversation.

The inbox could only ever show what users SENT. Everything the bot said back was
invisible, because ``record_out`` was called from exactly one place (the owner's
manual reply), while the bot answers users from ~40 different call sites:
download progress, quality menus, errors, /start, gate prompts, delivered media.
A thread that shows only one side is not a conversation.

Rather than teach 40 call sites to log, this hooks the send methods once — the
same trick ``theme_hook`` uses for keyboards, and it piggybacks on that wrapper
so there is a single patch point on the Bot classes.

Design constraints that shaped this:

* **Never break a send.** Every failure path swallows its exception. A logging
  bug must not stop a user getting their video.
* **Private user chats only.** Group traffic, the storage/log channel, and the
  owner's own DM are skipped: the point is per-user DM history, and logging the
  storage chat would record every file the bot mints a ``file_id`` for.
* **file_id is captured, bytes are not.** Same rule as incoming: ``/data`` is a
  434MB volume, so the log stores Telegram's handle, never the media.
* **Bounded.** Text is clipped; ``prune_messages`` already caps total rows.
"""
import logging

logger = logging.getLogger(__name__)

# Methods worth logging, mapped to the message kind they produce. edit_* is
# deliberately absent: progress bars edit the same message dozens of times and
# would bury the thread in noise.
_LOGGED = {
    "send_message": "text",
    "send_photo": "photo",
    "send_video": "video",
    "send_audio": "audio",
    "send_document": "document",
    "send_animation": "animation",
    "send_voice": "voice",
    "send_video_note": "video_note",
    "send_sticker": "sticker",
    "copy_message": "copy",
}

# Message attributes that can carry a file, in the order worth probing.
_FILE_ATTRS = ("video", "audio", "document", "animation", "voice",
               "video_note", "sticker", "photo")

# The payload kwarg that carries the file for each media send.
_FILE_ARG = {
    "send_photo": "photo",
    "send_video": "video",
    "send_audio": "audio",
    "send_document": "document",
    "send_animation": "animation",
    "send_voice": "voice",
    "send_video_note": "video_note",
    "send_sticker": "sticker",
}


def _clip(text: str, limit: int = 400) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _file_id_of(value) -> str:
    """A resendable file_id, or "" when the payload is raw bytes/a path.

    Media sent from disk has no file_id until Telegram answers, so the return
    Message is the reliable source; this only handles the case where the caller
    already passed a file_id string.
    """
    if isinstance(value, str) and len(value) > 30 and "/" not in value:
        return value
    return ""


def _file_id_from_result(message, kind: str) -> tuple:
    """Pull (file_id, file_type) off the Message the API returned.

    This is what makes bot-sent media retrievable: whether the caller passed a
    path, bytes, or a file_id, the response always carries the file_id Telegram
    assigned.

    Text sends are excluded up front. A real ``send_message`` response has all
    media attributes set to None so scanning them is harmless in production, but
    keying off the kind makes the intent explicit and stops a text row from ever
    being tagged with a file it does not have.
    """
    if kind == "text":
        return "", ""
    try:
        if kind in ("photo", "copy") and getattr(message, "photo", None):
            return message.photo[-1].file_id, "photo"
        # Prefer the attribute matching this send method, then fall back to a
        # scan for copy_message, whose kind is not known ahead of time.
        names = ([kind] if kind in _FILE_ATTRS else []) + [
            n for n in _FILE_ATTRS if n != kind]
        for attr in names:
            obj = getattr(message, attr, None)
            if obj is not None and hasattr(obj, "file_id"):
                return obj.file_id, attr
    except Exception:
        pass
    return "", ""


async def log_send(method_name: str, kwargs: dict, result) -> None:
    """Record one outgoing message. Must never raise."""
    try:
        kind = _LOGGED.get(method_name)
        if kind is None:
            return

        chat_id = kwargs.get("chat_id")
        if chat_id is None:
            return
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            return  # @channelusername strings are never a user DM

        # Negative ids are groups/channels — including the storage chat.
        if chat_id <= 0:
            return

        from bot.config import ADMIN_IDS

        # The owner's own DM is the panel itself; logging it would record every
        # inbox screen the owner opens into the inbox.
        if chat_id in ADMIN_IDS:
            return

        from bot.database import db

        # Only log for users the bot actually knows, so a stray send to an
        # arbitrary id cannot create phantom threads.
        if not await db.user_exists(chat_id):
            return

        text = kwargs.get("text") or kwargs.get("caption") or ""
        file_id, file_type = _file_id_from_result(result, kind)
        if not file_id:
            file_id = _file_id_of(kwargs.get(_FILE_ARG.get(method_name, "")))
            file_type = kind if file_id else ""
        if kind == "copy":
            # copy_message returns a bare MessageId, not a Message, so there is
            # no media to inspect. Label it generically rather than guessing
            # "document" — broadcasts go out this way and would otherwise show
            # up as a document in every user's thread.
            kind = file_type or "out"

        msg_id = 0
        try:
            msg_id = int(getattr(result, "message_id", 0) or 0)
        except Exception:
            pass

        await db.log_message(
            user_id=chat_id, chat_id=chat_id, chat_type="private",
            msg_id=msg_id, kind=kind,
            text=_clip(text) or f"[{kind}]", direction="out",
            file_id=file_id, file_type=file_type)
    except Exception as exc:  # logging must never break a send
        logger.debug("outgoing log skipped for %s: %s", method_name, exc)
