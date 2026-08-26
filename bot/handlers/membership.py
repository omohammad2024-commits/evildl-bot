"""React to channel membership changes in real time.

The forced-channel gate normally learns about a leave only when its short cache
entry expires. That leaves a window where a user can join to unlock the bot,
leave the channel, and keep downloading. Telegram can push a ``chat_member``
update the instant someone's status in a gated channel changes — if the bot is
an administrator there — so we subscribe to it and update the gate cache
immediately.

For this to fire, ``chat_member`` must be in ``allowed_updates`` (set in
main.py) and the bot must be an admin of the channel (which the gate already
requires anyway).
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

_MEMBER = {"member", "administrator", "creator", "owner"}


async def chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Push membership changes for gated channels into the gate cache."""
    cm = update.chat_member
    if cm is None:
        return

    from bot.utils import gate

    chat_id = str(cm.chat.id)
    # Only care about channels the gate actually enforces.
    try:
        gated_ids = {str(c.get("id")) for c in await gate.channels()}
    except Exception:
        return
    if chat_id not in gated_ids:
        return

    user_id = cm.new_chat_member.user.id
    new_status = cm.new_chat_member.status
    is_member = new_status in _MEMBER
    gate.note_membership(user_id, chat_id, is_member)
    logger.info("chat_member: user %s in %s -> %s (member=%s)",
                user_id, chat_id, new_status, is_member)
