"""Optional Telegram USER session (Telethon StringSession).

A bot token cannot read stories or private-channel history — Telegram restricts
those methods to real user accounts (verified live: ``BotMethodInvalidError``).
This module manages a *second*, optional MTProto client logged in as a user, so
the story downloader has an authenticated account to fetch from.

The session string is persisted to ``data/user_session.txt`` (chmod 600). It is
created interactively by the owner through the /tglogin admin flow — this module
only loads/holds the resulting client. If no session is stored, ``get_user()``
returns None and story downloads fail with a clear "owner must run /tglogin"
message rather than a crash.

Security: the session string is equivalent to a logged-in account. It is stored
with 0600 perms and never logged.
"""
import logging
import os
from typing import Optional

from bot import config

logger = logging.getLogger(__name__)

_SESSION_FILE = os.path.join(config.DATA_DIR, "user_session.txt")

_user_client = None  # telethon.TelegramClient, lazily started
_import_error: Optional[str] = None


def session_path() -> str:
    return _SESSION_FILE


def have_session() -> bool:
    """True if a stored user session exists (does not verify it still works)."""
    if os.getenv("TG_USER_SESSION"):
        return True
    return os.path.exists(_SESSION_FILE) and os.path.getsize(_SESSION_FILE) > 0


def _read_session() -> str:
    env = os.getenv("TG_USER_SESSION")
    if env:
        return env.strip()
    if os.path.exists(_SESSION_FILE):
        with open(_SESSION_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    return ""


def save_session(string: str) -> None:
    """Persist a StringSession and tighten its perms."""
    with open(_SESSION_FILE, "w", encoding="utf-8") as fh:
        fh.write(string)
    try:
        os.chmod(_SESSION_FILE, 0o600)
    except OSError:
        pass
    logger.info("user session saved (%d chars)", len(string))


def clear_session() -> bool:
    """Remove the stored session and drop the live client. Returns True if a
    session file was actually removed."""
    global _user_client
    _user_client = None
    removed = False
    if os.path.exists(_SESSION_FILE):
        try:
            os.remove(_SESSION_FILE)
            removed = True
        except OSError:
            pass
    return removed


async def get_user():
    """Return a connected, authorized user client, or None if unavailable.

    Never raises: any failure (no session, revoked session, import error) is
    logged and returns None so callers can show a friendly message.
    """
    global _user_client, _import_error
    if not have_session():
        return None
    if _user_client is not None and _user_client.is_connected():
        return _user_client
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession

        client = TelegramClient(
            StringSession(_read_session()),
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH,
        )
        await client.connect()
        if not await client.is_user_authorized():
            logger.warning("stored user session is no longer authorized")
            await client.disconnect()
            return None
        _user_client = client
        logger.info("user session client ready (stories enabled)")
        return _user_client
    except Exception as exc:  # pragma: no cover - defensive
        _import_error = str(exc)
        logger.error("user session client failed: %s", exc)
        return None


async def close_user() -> None:
    global _user_client
    if _user_client is not None:
        try:
            await _user_client.disconnect()
        except Exception:
            pass
        _user_client = None
