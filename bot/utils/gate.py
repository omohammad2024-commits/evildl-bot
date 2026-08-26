"""Forced-channel gate.

Upgraded from a single-channel check to a small subsystem, because the original
version had a fatal flaw for a public bot: when the bot was not an admin of the
target channel, ``get_chat_member`` raised "Member list is inaccessible", the
error was swallowed, and the gate silently let everyone through. It looked
configured and enforced nothing.

Design rules:

* **Verify at configuration time.** ``verify_channel`` is called by
  /setchannel, so a broken setup is reported the moment it is created rather
  than never.
* **Fail closed on a real answer, fail open on a broken setup.** A definitive
  "not a participant" blocks. A misconfiguration (bot not admin, channel gone)
  lets users through and warns the admin — a config mistake must never take the
  whole bot offline.
* **Multiple channels.** Membership in *all* configured channels is required.
* **Private channels supported.** A stored invite link is used for the button
  when the channel has no public @username.
* **Cache membership.** Without it, every download costs one API call per
  channel per user, which is how public bots hit rate limits.

Storage: the ``force_channels`` setting holds a JSON list of entries
``{"id": "-100...", "handle": "@name", "title": "...", "invite": "https://..."}``.
The legacy ``force_channel`` string is migrated automatically on first read.
"""
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from telegram import Bot
from telegram.error import BadRequest, Forbidden

from bot import config
from bot.database import db

logger = logging.getLogger(__name__)

# Statuses that count as "in the channel".
MEMBER_STATUSES = {"member", "administrator", "creator", "owner"}
# Statuses that are a definitive "no".
OUTSIDER_STATUSES = {"left", "kicked", "banned", "restricted"}

SETTING = "force_channels"
LEGACY_SETTING = "force_channel"

# (user_id, channel_id) -> (is_member, checked_at)
_cache: Dict[Tuple[int, str], Tuple[bool, float]] = {}
# A confirmed membership is trusted only briefly: if a user joins to unlock the
# bot and then immediately leaves the channel, the gate must notice quickly.
# A ChatMemberHandler also clears the cache the instant Telegram reports a leave
# (see bot.handlers.membership), so this TTL is just a backstop.
CACHE_TTL = 60
# A negative result (not a member) is cached even more briefly so pressing
# "I joined" right after joining feels instant.
NEG_CACHE_TTL = 20

# channel_id -> problem string, for the admin panel.
_problems: Dict[str, str] = {}
_warned_at: Dict[str, float] = {}


# ── configuration ─────────────────────────────────────────────────────
async def channels() -> List[Dict[str, Any]]:
    """Configured channels, migrating the legacy single-channel setting."""
    raw = await db.get_setting(SETTING, "")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [c for c in data if isinstance(c, dict) and c.get("id")]
        except (ValueError, TypeError):
            logger.warning("force_channels is corrupt; ignoring")

    legacy = await db.get_setting(LEGACY_SETTING, config.FORCE_CHANNEL)
    if legacy:
        return [{"id": legacy, "handle": legacy, "title": legacy, "invite": ""}]
    return []


async def save_channels(items: List[Dict[str, Any]]) -> None:
    await db.set_setting(SETTING, json.dumps(items, ensure_ascii=False))
    # Keep the legacy key roughly in sync so nothing reads a stale value.
    await db.set_setting(LEGACY_SETTING, items[0].get("handle", "") if items else "")
    cache_clear()


async def is_enabled() -> bool:
    return bool(await channels())


def channel_link(entry: Dict[str, Any]) -> str:
    """Best available join URL for a channel."""
    handle = (entry.get("handle") or "").lstrip("@")
    if handle and not handle.startswith("-"):
        return f"https://t.me/{handle}"
    return entry.get("invite") or ""


def channel_label(entry: Dict[str, Any]) -> str:
    return entry.get("title") or entry.get("handle") or entry.get("id", "")


# ── verification ──────────────────────────────────────────────────────
async def verify_channel(bot: Bot, target: str) -> Tuple[bool, Dict[str, Any], str]:
    """Check a channel is usable as a gate before storing it.

    Returns ``(usable, entry, problem)``. ``problem`` is a short machine-ish
    reason the caller turns into a message.
    """
    try:
        chat = await bot.get_chat(target)
    except Exception as exc:
        return False, {}, f"not_found:{exc}"

    entry: Dict[str, Any] = {
        "id": str(chat.id),
        "handle": f"@{chat.username}" if chat.username else str(chat.id),
        "title": chat.title or chat.username or str(chat.id),
        "invite": chat.invite_link or "",
    }

    me = await bot.get_me()
    try:
        member = await bot.get_chat_member(chat.id, me.id)
    except Exception as exc:
        # The classic failure: the bot is not an administrator, so Telegram
        # refuses to expose membership at all.
        return False, entry, f"cannot_read_members:{exc}"

    if member.status not in {"administrator", "creator", "owner"}:
        return False, entry, f"not_admin:{member.status}"

    # Private channels need an invite link for the button to work.
    if not chat.username and not entry["invite"]:
        try:
            link = await bot.export_chat_invite_link(chat.id)
            entry["invite"] = link
        except Exception:
            return False, entry, "no_invite_link"

    return True, entry, ""


# ── membership ────────────────────────────────────────────────────────
def _cache_get(user_id: int, chat_id: str) -> Optional[bool]:
    hit = _cache.get((user_id, chat_id))
    if not hit:
        return None
    ok, at = hit
    ttl = CACHE_TTL if ok else NEG_CACHE_TTL
    if time.time() - at > ttl:
        _cache.pop((user_id, chat_id), None)
        return None
    return ok


def cache_clear(user_id: Optional[int] = None) -> None:
    """Drop cached membership, e.g. when a user presses "I joined"."""
    if user_id is None:
        _cache.clear()
        return
    for key in [k for k in _cache if k[0] == user_id]:
        _cache.pop(key, None)


def note_membership(user_id: int, chat_id: str, is_member: bool) -> None:
    """Record a membership change pushed by Telegram (ChatMemberHandler).

    This makes leaves take effect immediately: when a user leaves a gated
    channel Telegram sends a chat_member update, and we drop them to a cached
    "not a member" so the very next download re-gates them without waiting for
    the TTL to expire.
    """
    _cache[(user_id, str(chat_id))] = (is_member, time.time())


async def _member_of(bot: Bot, entry: Dict[str, Any], user_id: int) -> bool:
    """Membership in one channel. True when the setup is broken."""
    chat_id = str(entry.get("id"))
    cached = _cache_get(user_id, chat_id)
    if cached is not None:
        return cached

    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except BadRequest as exc:
        text = str(exc).lower()
        # Definitive "this user is not in the channel" answers. Telegram phrases
        # this several ways; participant_id_invalid is what it returns for a user
        # it cannot resolve at all, which for gating purposes is still a "no".
        if any(tok in text for tok in (
            "user not found", "not participant", "user_not_participant",
            "participant_id_invalid", "member not found",
        )):
            _cache[(user_id, chat_id)] = (False, time.time())
            return False
        _record_problem(entry, str(exc))
        return True
    except Forbidden as exc:
        _record_problem(entry, str(exc))
        return True
    except Exception as exc:  # transient network issue
        logger.warning("membership check error for %s: %s", chat_id, exc)
        return True

    _problems.pop(chat_id, None)
    ok = member.status in MEMBER_STATUSES
    _cache[(user_id, chat_id)] = (ok, time.time())
    return ok


def _record_problem(entry: Dict[str, Any], reason: str) -> None:
    chat_id = str(entry.get("id"))
    _problems[chat_id] = reason
    now = time.time()
    if now - _warned_at.get(chat_id, 0) > 3600:
        _warned_at[chat_id] = now
        logger.error(
            "FORCED-CHANNEL GATE DISABLED for %s: %s — promote the bot to "
            "administrator there, otherwise nobody is being gated.",
            channel_label(entry), reason,
        )


async def missing_channels(bot: Bot, user_id: int) -> List[Dict[str, Any]]:
    """Channels the user still needs to join. Empty means allowed through."""
    result: List[Dict[str, Any]] = []
    for entry in await channels():
        if not await _member_of(bot, entry, user_id):
            result.append(entry)
    return result


async def is_member(bot: Bot, user_id: int) -> bool:
    """Whether the user satisfies every configured channel."""
    if not await is_enabled():
        return True
    return not await missing_channels(bot, user_id)


async def health(bot: Bot) -> List[Dict[str, Any]]:
    """Per-channel gate status for the admin panel."""
    out: List[Dict[str, Any]] = []
    for entry in await channels():
        usable, fresh, problem = await verify_channel(bot, entry["id"])
        out.append({
            "label": channel_label(entry),
            "handle": entry.get("handle", ""),
            "ok": usable,
            "problem": problem or _problems.get(str(entry.get("id")), ""),
            "members": fresh.get("title") and None,
        })
    return out
