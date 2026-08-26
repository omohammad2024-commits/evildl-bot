"""Group behaviour: registration, per-group settings, gates and flood control.

The bot is public, so it gets added to groups it knows nothing about. This
module is the single place that decides *whether the bot should act in a group
at all*, keeping that policy out of the download path.

Design notes:

* **A group's own admins own its settings.** Defaults are deliberately quiet
  (tag-only) so a busy chat is never flooded on the bot's first day; the group's
  admins can opt into auto-download.
* **Settings survive a kick.** Leaving marks the row inactive rather than
  deleting it, so re-adding the bot restores the previous configuration.
* **Flood control is per-chat**, on top of the existing per-user limits. One
  large group cannot exhaust the bot for everyone else.
"""
import logging
import time
from typing import Dict, Optional, Tuple

from telegram import Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from bot import config
from bot.database import db
from bot.i18n import get_text

logger = logging.getLogger(__name__)

GROUP_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP)

# Per-chat flood window: at most N triggers per WINDOW seconds in one group.
FLOOD_MAX = 6
FLOOD_WINDOW = 60

# chat_id -> list of recent trigger timestamps
_hits: Dict[int, list] = {}

# (chat_id, user_id) -> (is_admin, expires_at). Group admin lists change rarely;
# caching avoids a get_chat_member round trip on every single message.
_admin_cache: Dict[Tuple[int, int], Tuple[bool, float]] = {}
_ADMIN_TTL = 300


def is_group(chat) -> bool:
    return bool(chat) and chat.type in GROUP_TYPES


async def settings(chat_id: int) -> Dict[str, object]:
    """Effective settings for a group, with defaults for unknown chats.

    A group row exists as soon as the bot *sees* the chat, which is separate from
    a group admin having *chosen* anything. Until ``configured`` is set, the
    owner's global ``GROUP_AUTO_DOWNLOAD`` default still applies — otherwise
    merely registering a group would flip auto-download off behind the owner's
    back (the table default is 0), and link auto-detection would silently stop
    working the moment the group got tracked.
    """
    row = await db.get_group(chat_id)
    default_auto = 1 if config.GROUP_AUTO_DOWNLOAD else 0
    if not row:
        return {
            "auto_download": default_auto,
            "admins_only": 0,
            "clean_mode": 1,
            "def_quality": "",
            "lang": "",
            "configured": 0,
            "known": False,
        }
    row["known"] = True
    if not row.get("configured"):
        row["auto_download"] = default_auto
    return row


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track add/remove of the bot itself (``my_chat_member`` updates).

    Without this the bot has no idea which groups it lives in — there is no Bot
    API call to list them, so the only chance to learn is the moment membership
    changes.
    """
    cm = update.my_chat_member
    if cm is None or not is_group(cm.chat):
        return
    status = cm.new_chat_member.status
    chat = cm.chat
    actor = cm.from_user.id if cm.from_user else 0

    if status in ("member", "administrator"):
        count = 0
        try:
            count = await context.bot.get_chat_member_count(chat.id)
        except Exception:
            pass
        existing = await db.get_group(chat.id)
        await db.upsert_group(chat.id, chat.title or "", chat.username or "",
                              chat.type, actor, count)
        logger.info("added to group %s (%s) by %s, members=%s",
                    chat.id, chat.title, actor, count)
        # Greet only on a genuinely new join, not on a promote-to-admin event.
        if existing is None:
            await _greet(context, chat.id, actor)
    elif status in ("left", "kicked"):
        await db.mark_group_left(chat.id)
        logger.info("removed from group %s (%s)", chat.id, chat.title)


async def _greet(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                 actor: int) -> None:
    """Introduce the bot once, when it is first added to a group."""
    from bot.i18n import themed
    from bot.keyboards.inline import group_welcome_keyboard
    from bot.utils.runtime import BOT_USERNAME

    lang = await db.get_user_language(actor) if actor else "fa"
    try:
        await context.bot.send_message(
            chat_id,
            await themed("GROUP_WELCOME", lang, bot=BOT_USERNAME or "bot"),
            parse_mode=ParseMode.HTML,
            reply_markup=await group_welcome_keyboard(lang, BOT_USERNAME),
        )
    except Exception as exc:
        logger.debug("group greet skipped for %s: %s", chat_id, exc)


async def touch(chat) -> None:
    """Keep a group's row fresh when it is seen in traffic (title renames etc)."""
    if not is_group(chat):
        return
    try:
        await db.upsert_group(chat.id, chat.title or "", chat.username or "",
                              chat.type)
    except Exception as exc:
        logger.debug("group touch failed for %s: %s", chat.id, exc)


async def is_group_admin(context: ContextTypes.DEFAULT_TYPE, chat_id: int,
                         user_id: int) -> bool:
    """Whether a user administers this group, cached briefly."""
    key = (chat_id, user_id)
    hit = _admin_cache.get(key)
    now = time.time()
    if hit and hit[1] > now:
        return hit[0]
    ok = False
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        ok = member.status in ("creator", "administrator")
    except Exception as exc:
        logger.debug("get_chat_member failed (%s/%s): %s", chat_id, user_id, exc)
    _admin_cache[key] = (ok, now + _ADMIN_TTL)
    return ok


def flood_ok(chat_id: int) -> bool:
    """True when this group is under its per-chat trigger budget.

    Silent by design: over-budget triggers are dropped without a message, so a
    busy group never sees "slow down" spam from the bot.
    """
    now = time.time()
    window = _hits.setdefault(chat_id, [])
    cutoff = now - FLOOD_WINDOW
    window[:] = [t for t in window if t > cutoff]
    if len(window) >= FLOOD_MAX:
        return False
    window.append(now)
    return True


async def may_act(update: Update, context: ContextTypes.DEFAULT_TYPE,
                  mentioned: bool) -> bool:
    """The single gate for "should the bot act on this group message?".

    Order matters: an explicit @mention bypasses auto-download (that is the
    universal escape hatch), but never bypasses the admins-only rule or flood
    control.
    """
    chat = update.effective_chat
    user = update.effective_user
    if not is_group(chat) or not user:
        return True

    conf = await settings(chat.id)
    if not conf.get("known"):
        # First traffic from a group the bot never saw a join event for (added
        # while the bot was offline).
        await touch(chat)
        conf = await settings(chat.id)

    if conf.get("admins_only"):
        from bot.handlers.admin import is_admin as is_bot_admin

        if not is_bot_admin(user.id) and \
                not await is_group_admin(context, chat.id, user.id):
            return False

    if not mentioned and not conf.get("auto_download"):
        return False

    if not flood_ok(chat.id):
        logger.info("group %s over flood budget — dropping trigger", chat.id)
        return False
    return True


async def group_lang(chat_id: int, user_id: int) -> str:
    """A group's forced language, else the triggering user's own preference."""
    conf = await settings(chat_id)
    forced = (conf.get("lang") or "").strip()
    if forced in ("fa", "en"):
        return forced
    return await db.get_user_language(user_id)
