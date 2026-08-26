"""Owner flow to log a USER account into the bot (for story downloads).

Bot tokens cannot read Telegram stories, so the story downloader needs a real
user session. This module drives an interactive, DM-only, admin-only login:

    /tglogin           → ask for the phone number
    <phone>            → send the login code, ask for it
    <code>             → sign in (or ask for the 2FA password)
    <password>         → sign in with 2FA

On success the StringSession is persisted via bot.utils.userclient and the live
client is cached. /tglogout clears it. All steps are DM + admin only; a
non-admin gets silence. The transient login client and phone_code_hash live in
module state keyed by admin id, never persisted until sign-in succeeds.

Security: the code is sent to the account being logged in; the session string is
account-equivalent and stored 0600. Nothing sensitive is logged.
"""
import logging
from typing import Dict

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot import config
from bot.i18n import get_text
from bot.utils import userclient

logger = logging.getLogger(__name__)

# admin_id -> {"client":TelegramClient, "phone":str, "hash":str, "step":str}
_pending: Dict[int, dict] = {}


def _is_admin(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id in config.ADMIN_IDS)


def _dm(update: Update) -> bool:
    c = update.effective_chat
    return bool(c and c.type == "private")


async def _new_client():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(), config.TELEGRAM_API_ID,
                            config.TELEGRAM_API_HASH)
    await client.connect()
    return client


async def tglogin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start (or restart) the user-login flow. Admin + DM only."""
    if not _is_admin(update):
        return
    lang = context.user_data.get("lang") or config.DEFAULT_LANG
    message = update.effective_message
    if not _dm(update):
        # Never run an account login inside a group.
        return

    if userclient.have_session():
        client = await userclient.get_user()
        if client is not None:
            try:
                me = await client.get_me()
                who = f"@{me.username}" if me and me.username else (me.first_name if me else "?")
            except Exception:
                who = "?"
            await message.reply_text(
                get_text("TGLOGIN_ALREADY", lang, who=who), parse_mode=ParseMode.HTML)
            return

    # Clean up any half-finished attempt.
    await _abort(update.effective_user.id)
    context.user_data["tglogin_step"] = "phone"
    await message.reply_text(get_text("TGLOGIN_ASK_PHONE", lang),
                             parse_mode=ParseMode.HTML)


async def tglogout_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update) or not _dm(update):
        return
    lang = context.user_data.get("lang") or config.DEFAULT_LANG
    await userclient.close_user()
    removed = userclient.clear_session()
    await update.effective_message.reply_text(
        get_text("TGLOGOUT_DONE" if removed else "TGLOGIN_NONE", lang))


async def _abort(admin_id: int) -> None:
    st = _pending.pop(admin_id, None)
    if st and st.get("client"):
        try:
            await st["client"].disconnect()
        except Exception:
            pass


async def tglogin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Consume the phone / code / password steps. Registered before the
    download handler; raises ApplicationHandlerStop when it owns the input so a
    phone number or login code is never treated as a link."""
    if not _is_admin(update) or not _dm(update):
        return
    step = context.user_data.get("tglogin_step")
    if not step:
        return  # not in a login flow — let normal handlers run

    lang = context.user_data.get("lang") or config.DEFAULT_LANG
    message = update.effective_message
    text = (message.text or "").strip()
    admin_id = update.effective_user.id

    if text.lower() in ("/cancel", "cancel", "لغو"):
        context.user_data.pop("tglogin_step", None)
        await _abort(admin_id)
        await message.reply_text(get_text("TGLOGIN_CANCELLED", lang))
        raise ApplicationHandlerStop

    try:
        if step == "phone":
            await _handle_phone(update, context, text, lang)
        elif step == "code":
            await _handle_code(update, context, text, lang)
        elif step == "password":
            await _handle_password(update, context, text, lang)
    except ApplicationHandlerStop:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("tglogin step %s failed: %s", step, exc)
        context.user_data.pop("tglogin_step", None)
        await _abort(admin_id)
        await message.reply_text(get_text("TGLOGIN_ERROR", lang, error=str(exc)[:120]))
    raise ApplicationHandlerStop


async def _handle_phone(update, context, phone, lang) -> None:
    admin_id = update.effective_user.id
    message = update.effective_message
    client = await _new_client()
    try:
        sent = await client.send_code_request(phone)
    except Exception as exc:
        await client.disconnect()
        raise ValueError(f"could not send code: {exc}") from exc
    _pending[admin_id] = {"client": client, "phone": phone,
                          "hash": sent.phone_code_hash, "step": "code"}
    context.user_data["tglogin_step"] = "code"
    # Ask the user to space out the digits so Telegram doesn't auto-expire the
    # code when it detects it in the chat (a known anti-abuse behaviour).
    await message.reply_text(get_text("TGLOGIN_ASK_CODE", lang),
                             parse_mode=ParseMode.HTML)


async def _handle_code(update, context, code, lang) -> None:
    admin_id = update.effective_user.id
    message = update.effective_message
    st = _pending.get(admin_id)
    if not st:
        context.user_data.pop("tglogin_step", None)
        await message.reply_text(get_text("TGLOGIN_ERROR", lang, error="session expired"))
        return
    client = st["client"]
    digits = code.replace(" ", "").replace("-", "")
    from telethon.errors import SessionPasswordNeededError

    try:
        await client.sign_in(phone=st["phone"], code=digits,
                             phone_code_hash=st["hash"])
    except SessionPasswordNeededError:
        context.user_data["tglogin_step"] = "password"
        st["step"] = "password"
        await message.reply_text(get_text("TGLOGIN_ASK_PASSWORD", lang),
                                 parse_mode=ParseMode.HTML)
        return
    await _finish(update, context, lang)


async def _handle_password(update, context, password, lang) -> None:
    admin_id = update.effective_user.id
    st = _pending.get(admin_id)
    if not st:
        context.user_data.pop("tglogin_step", None)
        await update.effective_message.reply_text(
            get_text("TGLOGIN_ERROR", lang, error="session expired"))
        return
    await st["client"].sign_in(password=password)
    await _finish(update, context, lang)


async def _finish(update, context, lang) -> None:
    admin_id = update.effective_user.id
    message = update.effective_message
    st = _pending.pop(admin_id, None)
    context.user_data.pop("tglogin_step", None)
    if not st:
        return
    client = st["client"]
    from telethon.sessions import StringSession

    session_str = client.session.save()
    userclient.save_session(session_str)
    try:
        me = await client.get_me()
        who = f"@{me.username}" if me and me.username else (me.first_name if me else "?")
    except Exception:
        who = "?"
    # Hand the live, authorized client over to userclient so the first story
    # download doesn't have to reconnect.
    await client.disconnect()
    await message.reply_text(get_text("TGLOGIN_DONE", lang, who=who),
                             parse_mode=ParseMode.HTML)
    logger.info("user session established by admin %s (%s)", admin_id, who)
