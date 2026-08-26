"""/gsettings — per-group configuration, driven by that group's own admins.

Kept separate from the owner admin panel: these settings belong to whoever runs
the group, not to the bot owner. The bot owner can still open it (useful for
support), but a group admin does not gain any bot-wide power from it.
"""
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.database import db
from bot.i18n import get_text, themed

logger = logging.getLogger(__name__)

# Quality values the per-group default cycles through.
QUALITY_CYCLE = ("", "360p", "480p", "720p", "1080p", "mp3")
LANG_CYCLE = ("", "fa", "en")


async def _allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Only group admins (or the bot owner) may change a group's settings."""
    from bot.handlers.admin import is_admin as is_bot_admin
    from bot.handlers.groups import is_group, is_group_admin

    chat = update.effective_chat
    user = update.effective_user
    if not is_group(chat):
        return False
    if not user:
        return False
    if is_bot_admin(user.id):
        return True
    return await is_group_admin(context, chat.id, user.id)


async def settings_text(chat_id: int, lang: str) -> str:
    """Render the current group configuration."""
    from bot.handlers.groups import settings

    conf = await settings(chat_id)
    row = await db.get_group(chat_id) or {}
    quality = (conf.get("def_quality") or "").strip()
    glang = (conf.get("lang") or "").strip()
    return await themed(
        "GSETTINGS_BODY", lang,
        title=(row.get("title") or str(chat_id))[:60],
        members=row.get("member_count") or 0,
        downloads=row.get("downloads") or 0,
        mode=get_text("GMODE_AUTO" if conf.get("auto_download") else "GMODE_TAG", lang),
        admins_only=get_text("YES" if conf.get("admins_only") else "NO", lang),
        clean=get_text("YES" if conf.get("clean_mode") else "NO", lang),
        quality=quality or get_text("GQUALITY_ASK", lang),
        lang_name=glang or get_text("GLANG_USER", lang),
    )


async def gsettings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/gsettings inside a group."""
    from bot.handlers.groups import is_group, touch
    from bot.keyboards.inline import group_settings_keyboard

    chat = update.effective_chat
    message = update.effective_message
    user = update.effective_user
    if not is_group(chat):
        lang = await db.get_user_language(user.id) if user else "fa"
        await message.reply_text(get_text("GSETTINGS_ONLY_GROUP", lang))
        return

    lang = await db.get_user_language(user.id) if user else "fa"
    if not await _allowed(update, context):
        await message.reply_text(get_text("GSETTINGS_ONLY_ADMIN", lang))
        return

    await touch(chat)
    # Refresh the member count while we're here — it drives the panel display.
    try:
        count = await context.bot.get_chat_member_count(chat.id)
        await db.set_group_flag(chat.id, "member_count", count)
    except Exception:
        pass

    await message.reply_text(
        await themed("GSETTINGS_TITLE", lang) + "\n" +
        await settings_text(chat.id, lang),
        parse_mode=ParseMode.HTML,
        reply_markup=await group_settings_keyboard(lang, chat.id,
                                                   await _conf(chat.id)))


async def _conf(chat_id: int) -> dict:
    from bot.handlers.groups import settings

    return dict(await settings(chat_id))


async def group_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help inside a group answers with the short group guide."""
    user = update.effective_user
    lang = await db.get_user_language(user.id) if user else "fa"
    await update.effective_message.reply_text(
        await themed("GROUP_HELP", lang), parse_mode=ParseMode.HTML)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          action: str, lang: str) -> None:
    """Toggle handlers for the g: callback namespace."""
    from bot.keyboards.inline import group_settings_keyboard

    query = update.callback_query
    chat = update.effective_chat

    # 'help' is the only action any member may use: it prints the short group
    # guide as a fresh message, so it cannot disturb the shared card.
    if action == "help":
        await query.answer()
        await query.message.reply_text(await themed("GROUP_HELP", lang),
                                       parse_mode=ParseMode.HTML,
                                       disable_web_page_preview=True)
        return

    if not await _allowed(update, context):
        await query.answer(get_text("GSETTINGS_ONLY_ADMIN", lang), show_alert=True)
        return

    # 'panel' opens the settings card from the group /start menu.
    if action == "panel":
        await query.answer()
        await query.message.reply_text(
            await themed("GSETTINGS_TITLE", lang) + "\n" +
            await settings_text(chat.id, lang),
            parse_mode=ParseMode.HTML,
            reply_markup=await group_settings_keyboard(lang, chat.id,
                                                       await _conf(chat.id)))
        return

    conf = await _conf(chat.id)
    if action == "mode":
        await db.set_group_flag(chat.id, "auto_download",
                                0 if conf.get("auto_download") else 1)
    elif action == "admins":
        await db.set_group_flag(chat.id, "admins_only",
                                0 if conf.get("admins_only") else 1)
    elif action == "clean":
        await db.set_group_flag(chat.id, "clean_mode",
                                0 if conf.get("clean_mode") else 1)
    elif action == "quality":
        cur = (conf.get("def_quality") or "").strip()
        nxt = QUALITY_CYCLE[(QUALITY_CYCLE.index(cur) + 1) % len(QUALITY_CYCLE)] \
            if cur in QUALITY_CYCLE else QUALITY_CYCLE[1]
        await db.set_group_flag(chat.id, "def_quality", nxt)
    elif action == "lang":
        cur = (conf.get("lang") or "").strip()
        nxt = LANG_CYCLE[(LANG_CYCLE.index(cur) + 1) % len(LANG_CYCLE)] \
            if cur in LANG_CYCLE else LANG_CYCLE[1]
        await db.set_group_flag(chat.id, "lang", nxt)

    await query.answer()
    try:
        await query.message.edit_text(
            await themed("GSETTINGS_TITLE", lang) + "\n" +
            await settings_text(chat.id, lang),
            parse_mode=ParseMode.HTML,
            reply_markup=await group_settings_keyboard(lang, chat.id,
                                                       await _conf(chat.id)))
    except Exception as exc:
        logger.debug("gsettings redraw skipped: %s", exc)
