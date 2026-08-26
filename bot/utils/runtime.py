"""Mutable runtime facts discovered after start-up.

``config`` holds static settings from the environment; this holds values we only
learn once connected to Telegram (the bot's own @username, for example) so any
module can read them without importing the Application.
"""

BOT_USERNAME: str = ""
BOT_ID: int = 0


def set_identity(username: str, bot_id: int = 0) -> None:
    global BOT_USERNAME, BOT_ID
    BOT_USERNAME = (username or "").lstrip("@")
    BOT_ID = bot_id or BOT_ID


def _u16_slice(text: str, offset: int, length: int) -> str:
    """Slice ``text`` using Telegram's UTF-16 code-unit offsets.

    Telegram reports entity offsets/lengths in UTF-16 code units, while Python
    indexes by code point. Any character outside the BMP (every emoji, and lots
    of symbols) counts as 2 units, so slicing the str directly drifts by one
    position per emoji — '🔥🔥 @Evildl_bot' sliced at the reported offset yields
    'vildl_bot '. Encoding to UTF-16-LE first makes the offsets exact.
    """
    try:
        buf = text.encode("utf-16-le")
        return buf[offset * 2:(offset + length) * 2].decode("utf-16-le", "ignore")
    except Exception:
        # Never let a malformed entity break mention detection.
        return text[offset:offset + length]


def is_bot_mentioned(message) -> bool:
    """Robustly decide whether THIS bot was @-mentioned/tagged in a message.

    Uses Telegram message entities instead of naive substring matching, so:
      - '@Evildl_bot_news' does NOT count as a mention of '@Evildl_bot'
        (substring matching gets this wrong);
      - a ``text_mention`` entity (tapping the bot's name, no '@') is honoured
        by matching the bot's numeric id;
      - both text and caption entities are checked;
      - entity offsets are read as UTF-16, so emoji before the tag don't shift
        the slice.

    Falls back to a word-boundary substring test when entities are absent
    (e.g. forwarded copies that dropped their offsets).
    """
    if message is None:
        return False
    text = message.text or message.caption or ""
    # Check BOTH lists: a media message with a caption can still carry entities
    # on either field depending on the client that sent it.
    entities = list(getattr(message, "entities", None) or ()) + \
        list(getattr(message, "caption_entities", None) or ())
    uname = BOT_USERNAME.lower()

    for ent in entities:
        if ent.type == "mention" and uname:
            frag = _u16_slice(text, ent.offset, ent.length).lstrip("@").lower()
            if frag == uname:
                return True
        elif ent.type == "text_mention" and ent.user and BOT_ID:
            if ent.user.id == BOT_ID:
                return True

    # Fallback: word-boundary match so '@evildl_bot' hits but '@evildl_bot_x'
    # does not. Telegram usernames are [A-Za-z0-9_], so the next char after the
    # handle must not be one of those.
    if uname:
        import re

        if re.search(rf"@{re.escape(uname)}(?![A-Za-z0-9_])", text, re.IGNORECASE):
            return True
    return False


def signature() -> str:
    """The credit line appended to every caption."""
    return f"@{BOT_USERNAME}" if BOT_USERNAME else ""
