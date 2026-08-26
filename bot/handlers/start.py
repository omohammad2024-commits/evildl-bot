"""/start, /help, /lang, /me, /cancel."""
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from bot import config
from bot.database import db
from bot.i18n import get_text, themed
from bot.keyboards.inline import (language_keyboard, main_keyboard,
                                  main_keyboard_async)
from bot.utils.url_parser import supported_list

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        return

    known = await db.get_user(user.id) is not None
    await db.add_user(user.id, user.username or "", user.first_name or "")

    if await db.is_banned(user.id):
        lang = await db.get_user_language(user.id)
        await message.reply_text(get_text("BANNED_MESSAGE", lang))
        return

    # Referral deep link: t.me/Bot?start=ref_<inviter_id>. Only credited on the
    # user's first ever contact, so it cannot be farmed by re-pressing start.
    if context.args and context.args[0].startswith("ref_") and not known:
        try:
            referrer_id = int(context.args[0][4:])
            await db.add_referral(user.id, referrer_id)
        except (ValueError, TypeError):
            pass

    # Deep link from inline mode: t.me/Bot?start=dl_<base64url> reruns the
    # download in this private chat with the full quality menu.
    if context.args and context.args[0].startswith("dl_"):
        import base64

        token = context.args[0][3:]
        try:
            pad = "=" * (-len(token) % 4)
            url = base64.urlsafe_b64decode(token + pad).decode()
        except Exception:
            url = ""
        if url:
            lang = await db.get_user_language(user.id)
            from bot.handlers.download import process_url

            await process_url(update, context, url, lang=lang)
            return

    # Menu deep link: t.me/Bot?start=go_<action>. Only used when the owner turns
    # on "blue buttons" — Telegram paints url buttons blue, so menu entries are
    # rendered as deep links back into the bot and dispatched here.
    if context.args and context.args[0].startswith("go_"):
        action = context.args[0][3:]
        lang = await db.get_user_language(user.id)
        from bot.handlers.callback import run_menu_action

        if await run_menu_action(update, context, action, lang):
            return

    # First contact: ask for a language before anything else.
    if not known:
        await message.reply_text(
            get_text("LANG_SELECT_MESSAGE", config.DEFAULT_LANG),
            reply_markup=language_keyboard(),
        )
        return

    lang = await db.get_user_language(user.id)
    await send_welcome(update, context, lang)


async def send_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> None:
    user = update.effective_user
    message = update.effective_message
    chat = update.effective_chat

    # Groups get their own card. The private menu must not be sent here: its
    # entries (history, favourites, my stats, settings, language, admin panel)
    # are per-user screens attached to a message the whole group can press, so
    # any member tapping them would act on the tapper's own account inside a
    # shared chat. Group-scoped actions stay inline; personal ones link to DM.
    if chat is not None and chat.type in ("group", "supergroup"):
        from bot.handlers import groups as G
        from bot.keyboards.inline import group_menu_keyboard

        conf = await G.settings(chat.id)
        glang = (conf.get("lang") or "").strip() or lang
        mode = get_text("GROUP_MODE_AUTO" if conf.get("auto_download")
                        else "GROUP_MODE_TAG", glang)
        if conf.get("admins_only"):
            mode += get_text("GROUP_MODE_ADMINS", glang)
        await message.reply_text(
            await themed("GROUP_START", glang, mode=mode),
            parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=await group_menu_keyboard(
                glang, context.bot.username or "",
                await G.is_group_admin(context, chat.id, user.id) if user else False),
        )
        return

    custom = await db.get_welcome_message()
    if custom:
        text = custom.replace("{user_name}", user.first_name or "") \
                     .replace("{date}", datetime.now().strftime("%Y-%m-%d"))
    else:
        text = await themed("WELCOME_MESSAGE", lang,
                            user_name=user.first_name or "", platforms=supported_list())
    await message.reply_text(
        text, parse_mode="HTML", disable_web_page_preview=True,
        reply_markup=await main_keyboard_async(
            lang, is_admin(user.id), context.bot.username or ""),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    lang = await db.get_user_language(user.id) if user else config.DEFAULT_LANG
    chat = update.effective_chat
    # In a group the full private help card is noise — answer with the short
    # group guide (how to tag, how to send files, where the settings are).
    if chat and chat.type in ("group", "supergroup"):
        await update.effective_message.reply_text(
            await themed("GROUP_HELP", lang),
            parse_mode="HTML", disable_web_page_preview=True,
        )
        return
    await update.effective_message.reply_text(
        await themed("HELP_MESSAGE", lang, platforms=supported_list()),
        parse_mode="HTML", disable_web_page_preview=True,
    )


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await db.get_user_language(update.effective_user.id)
    await update.effective_message.reply_text(
        get_text("LANG_SELECT_MESSAGE", lang), reply_markup=language_keyboard()
    )


async def me_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    lang = await db.get_user_language(user.id)
    row = await db.get_user(user.id) or {}
    joined = str(row.get("join_date") or "")[:10]
    await update.effective_message.reply_text(
        await themed("USER_STATS", lang,
                 downloads=await db.get_user_downloads(user.id),
                 joined=joined or "—",
                 ulang="فارسی" if lang == "fa" else "English"),
        parse_mode="HTML",
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = await db.get_user_language(update.effective_user.id)
    pending = [k for k in list(context.user_data.keys()) if k.startswith("await_")]
    for key in pending:
        context.user_data.pop(key, None)
    context.user_data.pop("broadcast_msg", None)
    await update.effective_message.reply_text(
        get_text("CANCELLED" if pending else "NOTHING_TO_CANCEL", lang)
    )


# ── user features: settings / favorites / mystats / invite ────────────
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.keyboards.inline import settings_keyboard

    user = update.effective_user
    lang = await db.get_user_language(user.id)
    prefs = await db.get_prefs(user.id)
    await update.effective_message.reply_text(
        await themed("SETTINGS_USER_TITLE", lang),
        parse_mode="HTML",
        reply_markup=settings_keyboard(lang, prefs),
    )


async def favorites_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from bot.keyboards.inline import favorites_keyboard
    from bot.utils.jobs import Job, jobs
    from bot.utils.url_parser import detect_platform

    user = update.effective_user
    message = update.effective_message
    lang = await db.get_user_language(user.id)
    rows = await db.get_favorites(user.id, 12)
    if not rows:
        await message.reply_text(get_text("FAV_EMPTY", lang))
        return
    entries, tokens = [], []
    for r in rows:
        url = r.get("url") or ""
        if not url:
            continue
        platform = r.get("platform") or detect_platform(url)
        token = jobs.put(Job(url=url, platform=platform, user_id=user.id,
                             chat_id=message.chat_id))
        label = r.get("title") or url.split("//")[-1][:40]
        entries.append({"platform": platform, "url": label, "title": label})
        tokens.append(token)
    await message.reply_text(
        get_text("FAV_TITLE", lang, count=len(entries)),
        parse_mode="HTML",
        reply_markup=favorites_keyboard(entries, tokens, lang),
    )


async def mystats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from datetime import datetime, timedelta

    from bot.utils.media_handler import human_size
    from bot.utils.url_parser import platform_label

    user = update.effective_user
    lang = await db.get_user_language(user.id)
    st = await db.get_personal_stats(user.id)
    row = await db.get_user(user.id) or {}
    joined = str(row.get("join_date") or "")[:10] or "—"

    today = datetime.utcnow().date()
    days = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
    byday = st.get("byday", {})
    counts = [byday.get(d.isoformat(), 0) for d in days]
    peak = max(counts) if counts else 0
    lines = []
    for d, c in zip(days, counts):
        filled = 0 if peak == 0 else round(c / peak * 8)
        bar = "▰" * filled + "▱" * (8 - filled)
        lines.append(f"<code>{d.strftime('%m-%d')} {bar}</code> {c}")
    chart = "\n".join(lines)

    top = platform_label(st.get("top_platform", "")) if st.get("top_platform") else "—"
    refs = await db.count_referrals(user.id)
    await update.effective_message.reply_text(
        await themed("MYSTATS", lang,
                 total=st.get("total", 0), week=st.get("week", 0),
                 bytes=human_size(st.get("bytes", 0)), top=top,
                 joined=joined, refs=refs, chart=chart),
        parse_mode="HTML",
    )


async def invite_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    lang = await db.get_user_language(user.id)
    me = context.bot.username or ""
    link = f"https://t.me/{me}?start=ref_{user.id}"
    count = await db.count_referrals(user.id)
    await update.effective_message.reply_text(
        await themed("INVITE_TEXT", lang, link=link, count=count),
        parse_mode="HTML", disable_web_page_preview=True,
    )
