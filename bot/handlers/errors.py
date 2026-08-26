"""Global error handler: log everything, tell the user something neutral, and
never let one bad update kill the bot.
"""
import html
import logging
import traceback

from telegram import Update
from telegram.error import Forbidden, NetworkError, RetryAfter, TimedOut
from telegram.ext import ContextTypes

from bot import config
from bot.i18n import get_text

logger = logging.getLogger(__name__)

# Errors that are routine in a public bot and should not be reported to admins.
_QUIET = (Forbidden, TimedOut, NetworkError, RetryAfter)


async def errors_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if error is None:
        return

    if isinstance(error, _QUIET):
        logger.warning("Transient error: %s: %s", type(error).__name__, error)
        return

    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    logger.error("Unhandled error: %s\n%s", error, tb)

    # Let the user know something failed, without leaking internals.
    if isinstance(update, Update) and update.effective_message:
        try:
            lang = "fa"
            if update.effective_user:
                from bot.database import db

                lang = await db.get_user_language(update.effective_user.id)
            await update.effective_message.reply_text(get_text("ERROR_GENERIC", lang))
        except Exception:
            pass

    # Send a trimmed report to the first admin so failures are visible.
    if config.ADMIN_IDS:
        try:
            snippet = html.escape(tb[-1200:])
            await context.bot.send_message(
                config.ADMIN_IDS[0],
                f"⚠️ <b>Error</b>\n<code>{html.escape(str(error))[:200]}</code>\n\n"
                f"<pre>{snippet}</pre>",
                parse_mode="HTML",
            )
        except Exception:
            logger.debug("could not notify admin about the error")
