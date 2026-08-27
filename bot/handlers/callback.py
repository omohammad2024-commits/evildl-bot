"""Callback query router.

Admin callbacks are silently ignored for non-admins: if someone forwards an
admin keyboard, pressing its buttons produces no alert and no reaction, so the
panel's existence is never confirmed.
"""
import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot import config
from bot.database import db
from bot.i18n import get_text, themed
from bot.keyboards.inline import (
    admin_back_keyboard,
    admin_keyboard,
    cache_keyboard,
    language_keyboard,
    main_keyboard,
)
from bot.utils.jobs import jobs
from bot.utils.url_parser import supported_list

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    user = query.from_user
    data = query.data
    lang = await db.get_user_language(user.id)

    # Admin namespace: absolute silence for everyone else.
    if data.startswith("a:") and not is_admin(user.id):
        logger.info("Ignored admin callback %s from non-admin %s", data, user.id)
        return

    try:
        if data == "x":
            await query.answer()
            await _delete(query)
            return

        if data.startswith("l:"):
            await _set_language(update, context, data.split(":", 1)[1])
            return

        if data.startswith("m:"):
            await _main_menu(update, context, data.split(":", 1)[1], lang)
            return

        if data.startswith("s:"):
            await _settings_action(update, context, data, lang)
            return

        if data.startswith("h:"):
            await _history_redownload(update, context, data.split(":", 1)[1], lang)
            return

        if data.startswith("j:"):
            await _check_join(update, context, lang)
            return

        if data.startswith("q:"):
            await _quality_chosen(update, context, data, lang)
            return

        if data.startswith("pl:"):
            await _playlist_chosen(update, context, data, lang)
            return

        if data.startswith("d:"):
            await _post_download(update, context, data, lang)
            return

        if data.startswith("g:"):
            from bot.handlers import gsettings as GS

            await GS.handle_callback(update, context, data.split(":", 1)[1], lang)
            return

        if data.startswith("c:") or data.startswith("a:"):
            await _admin_action(update, context, data, lang)
            return

        await query.answer()
    except Exception as exc:
        logger.error("callback %s failed: %s", data, exc, exc_info=True)
        try:
            await query.answer(get_text("ERROR_GENERIC", lang), show_alert=False)
        except Exception:
            pass


# ── language / menu ───────────────────────────────────────────────────
async def _set_language(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> None:
    query = update.callback_query
    user = query.from_user
    if lang not in {"fa", "en"}:
        lang = config.DEFAULT_LANG
    await db.add_user(user.id, user.username or "", user.first_name or "")
    await db.set_user_language(user.id, lang)
    await query.answer(get_text("LANG_SELECTED_FA" if lang == "fa" else "LANG_SELECTED_EN", lang))

    from bot.handlers.start import send_welcome

    await _delete(query)
    await send_welcome(update, context, lang)


async def _main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE,
                     action: str, lang: str) -> None:
    query = update.callback_query

    # Personal screens must not render in a shared chat. Old group messages may
    # still carry the private menu (sent before groups got their own card), and
    # any member could press those buttons to open the TAPPER's history,
    # favourites, stats or settings inside the group. Refuse there and point to
    # DM instead; only help and language are safe to show in a group.
    chat = update.effective_chat
    if (chat is not None and chat.type in ("group", "supergroup")
            and action in {"history", "favorites", "mystats", "stats",
                           "settings", "invite"}):
        await query.answer(get_text("GROUP_PM_ONLY", lang), show_alert=True)
        return

    await query.answer()
    if action == "help":
        await query.message.reply_text(
            await themed("HELP_MESSAGE", lang, platforms=supported_list()),
            parse_mode=ParseMode.HTML, disable_web_page_preview=True,
        )
    elif action == "lang":
        await query.message.reply_text(
            get_text("LANG_SELECT_MESSAGE", lang), reply_markup=language_keyboard()
        )
    elif action in ("stats", "mystats"):
        await _show_mystats(update, context, lang)
    elif action == "history":
        await _show_history(update, context, lang)
    elif action == "favorites":
        await _show_favorites(update, context, lang)
    elif action == "settings":
        await _show_settings(update, context, lang)
    elif action == "invite":
        await _show_invite(update, context, lang)


async def run_menu_action(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          action: str, lang: str) -> bool:
    """Run a main-menu action that arrived as a ``?start=go_<action>`` deep link.

    The menu screens were written against ``update.callback_query``; with the
    blue-button theme the same actions arrive as a plain /start message. Rather
    than duplicate every screen, wrap the message in a synthetic CallbackQuery
    (never answered — there is no real query to answer) and reuse them.
    """
    known = {"help", "lang", "mystats", "stats", "history", "favorites",
             "settings", "invite"}
    if action not in known:
        return False

    from telegram import CallbackQuery

    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return False

    shim = CallbackQuery(id="0", from_user=user, chat_instance="0",
                         message=message)
    shim.set_bot(context.bot)
    proxy = Update(update_id=update.update_id, callback_query=shim)

    if action == "help":
        await message.reply_text(
            await themed("HELP_MESSAGE", lang, platforms=supported_list()),
            parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    elif action == "lang":
        await message.reply_text(get_text("LANG_SELECT_MESSAGE", lang),
                                 reply_markup=language_keyboard())
    elif action in ("stats", "mystats"):
        await _show_mystats(proxy, context, lang)
    elif action == "history":
        await _show_history(proxy, context, lang)
    elif action == "favorites":
        await _show_favorites(proxy, context, lang)
    elif action == "settings":
        await _show_settings(proxy, context, lang)
    elif action == "invite":
        await _show_invite(proxy, context, lang)
    return True


async def _show_mystats(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        lang: str) -> None:
    """Personal stats card with a 7-day mini bar chart."""
    from bot.utils.media_handler import human_size
    from bot.utils.url_parser import platform_label

    query = update.callback_query
    user = query.from_user
    st = await db.get_personal_stats(user.id)
    row = await db.get_user(user.id) or {}
    joined = str(row.get("join_date") or "")[:10] or "—"

    # 7-day bar chart (oldest -> newest).
    from datetime import datetime, timedelta
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
    text = await themed(
        "MYSTATS", lang,
        total=st.get("total", 0), week=st.get("week", 0),
        bytes=human_size(st.get("bytes", 0)), top=top,
        joined=joined, refs=refs, chart=chart,
    )
    await query.message.reply_text(text, parse_mode=ParseMode.HTML)


async def _show_favorites(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          lang: str) -> None:
    from bot.keyboards.inline import favorites_keyboard
    from bot.utils.jobs import Job, jobs
    from bot.utils.url_parser import detect_platform

    query = update.callback_query
    user = query.from_user
    rows = await db.get_favorites(user.id, 12)
    if not rows:
        await query.message.reply_text(get_text("FAV_EMPTY", lang))
        return

    entries, tokens = [], []
    for r in rows:
        url = r.get("url") or ""
        if not url:
            continue
        platform = r.get("platform") or detect_platform(url)
        token = jobs.put(Job(url=url, platform=platform, user_id=user.id,
                             chat_id=query.message.chat_id))
        label = r.get("title") or url.split("//")[-1][:40]
        entries.append({"platform": platform, "url": label, "title": label})
        tokens.append(token)

    await query.message.reply_text(
        get_text("FAV_TITLE", lang, count=len(entries)),
        parse_mode=ParseMode.HTML,
        reply_markup=favorites_keyboard(entries, tokens, lang),
    )


async def _show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         lang: str) -> None:
    from bot.keyboards.inline import settings_keyboard

    query = update.callback_query
    prefs = await db.get_prefs(query.from_user.id)
    await query.message.reply_text(
        await themed("SETTINGS_USER_TITLE", lang),
        parse_mode=ParseMode.HTML,
        reply_markup=settings_keyboard(lang, prefs),
    )


async def _show_invite(update: Update, context: ContextTypes.DEFAULT_TYPE,
                       lang: str) -> None:
    query = update.callback_query
    user = query.from_user
    me = context.bot.username or ""
    link = f"https://t.me/{me}?start=ref_{user.id}"
    count = await db.count_referrals(user.id)
    await query.message.reply_text(
        await themed("INVITE_TEXT", lang, link=link, count=count),
        parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )


async def _settings_action(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           data: str, lang: str) -> None:
    """Apply a settings toggle and re-render the settings keyboard in place."""
    from bot.keyboards.inline import settings_keyboard

    query = update.callback_query
    user = query.from_user
    parts = data.split(":")  # s:<group>:<value>
    if len(parts) < 3:
        await query.answer()
        return
    group, value = parts[1], parts[2]

    if group == "q":
        await db.set_pref(user.id, "def_quality", value)
    elif group == "f":
        await db.set_pref(user.id, "def_format", value)
    elif group == "n":
        prefs = await db.get_prefs(user.id)
        await db.set_pref(user.id, "notify", 0 if prefs.get("notify", True) else 1)

    await query.answer(get_text("SET_SAVED", lang))
    prefs = await db.get_prefs(user.id)
    try:
        await query.message.edit_reply_markup(reply_markup=settings_keyboard(lang, prefs))
    except Exception:
        pass



async def _show_history(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        lang: str) -> None:
    """List the user's recent downloads with one-tap re-download buttons."""
    from bot.keyboards.inline import history_keyboard
    from bot.utils.jobs import Job, jobs
    from bot.utils.url_parser import detect_platform

    query = update.callback_query
    user = query.from_user
    rows = await db.get_user_recent(user.id, 8)
    if not rows:
        await query.message.reply_text(get_text("HISTORY_EMPTY", lang))
        return

    # Give each entry a short token so callback data stays tiny.
    entries = []
    tokens = []
    for r in rows:
        url = r.get("url") or ""
        if not url:
            continue
        platform = r.get("platform") or detect_platform(url)
        token = jobs.put(Job(url=url, platform=platform, user_id=user.id,
                             chat_id=query.message.chat_id))
        # Show the URL tail as a readable-ish label.
        label = url.split("//")[-1][:40]
        entries.append({"platform": platform, "url": label, "title": label})
        tokens.append(token)

    await query.message.reply_text(
        get_text("HISTORY_TITLE", lang, count=len(entries)),
        parse_mode=ParseMode.HTML,
        reply_markup=history_keyboard(entries, tokens, lang),
    )


async def _history_redownload(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              token: str, lang: str) -> None:
    """Re-run a download the user picked from their history."""
    from bot.handlers.download import run_download
    from bot.utils.jobs import jobs

    query = update.callback_query
    job = jobs.get(token)
    if job is None:
        await query.answer(get_text("HINT_RETRY", lang))
        return
    await query.answer()
    status = await query.message.reply_text(
        await themed("QUEUED", lang), parse_mode=ParseMode.HTML)
    await run_download(update, context, job.url, platform=job.platform,
                       quality=job.quality or "best", lang=lang, status=status)


async def _check_join(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> None:
    """Handle the "I joined" button.

    The cached result is dropped first, otherwise a user who just joined would
    keep seeing the gate until the cache expired.
    """
    from bot.utils import gate

    query = update.callback_query
    if not await gate.is_enabled():
        await query.answer()
        await _delete(query)
        return

    gate.cache_clear(query.from_user.id)
    missing = await gate.missing_channels(context.bot, query.from_user.id)
    if not missing:
        await query.answer(get_text("JOIN_OK", lang))
        await _delete(query)
        return

    # Name what is still missing instead of a generic failure.
    names = "، ".join(gate.channel_label(c) for c in missing)
    await query.answer(
        get_text("JOIN_STILL_MISSING", lang, channels=names)[:200],
        show_alert=True,
    )


# ── download callbacks ────────────────────────────────────────────────
async def _owns_job(query, job, lang: str) -> bool:
    """Guard a job's buttons so only the person who asked can drive them.

    In a group the quality/playlist keyboard is visible to everyone. Without
    this check any member could tap another member's menu — the download would
    run, be billed to the original requester, and the keyboard would vanish for
    them. The job's owner (and the bot admins) may proceed; anyone else gets a
    private toast and the message is left untouched.
    """
    if job is None:
        return False
    tapper = query.from_user.id if query.from_user else 0
    if not job.user_id or tapper == job.user_id or is_admin(tapper):
        return True
    await query.answer(get_text("NOT_YOUR_BUTTON", lang), show_alert=True)
    return False


async def _quality_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          data: str, lang: str) -> None:
    query = update.callback_query
    _, token, quality = data.split(":", 2)
    job = jobs.get(token)
    if job is None:
        await query.answer(get_text("HINT_RETRY", lang))
        return
    if not await _owns_job(query, job, lang):
        return
    await query.answer()

    from bot.handlers.download import run_download

    status = query.message
    try:
        await status.edit_text(await themed("QUEUED", lang),
                               parse_mode=ParseMode.HTML)
    except Exception:
        status = None

    await run_download(update, context, job.url, platform=job.platform,
                       quality=quality, lang=lang, status=status)


async def _playlist_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE,
                           data: str, lang: str) -> None:
    query = update.callback_query
    _, token, scope = data.split(":", 2)
    job = jobs.get(token)
    if job is None:
        await query.answer(get_text("HINT_RETRY", lang))
        return
    if not await _owns_job(query, job, lang):
        return
    await query.answer()

    from bot.handlers.download import run_download
    from bot.utils.url_parser import get_service

    entries = job.payload.get("entries") or []
    if scope == "1":
        entries = entries[:1]
    else:
        entries = entries[:config.MAX_PLAYLIST_ITEMS]

    try:
        await query.message.edit_text(
            get_text("PLAYLIST_STARTED", lang, count=len(entries))
        )
    except Exception:
        pass

    service = get_service(job.platform)
    for idx, entry in enumerate(entries, 1):
        note = await query.message.reply_text(
            get_text("PLAYLIST_ITEM", lang, current=idx, total=len(entries),
                     title=(entry.get("title") or "")[:60])
        )
        try:
            if job.platform == "spotify" and entry.get("track"):
                # Album/playlist tracks are resolved individually.
                result = await service.download_track(entry["track"])
                await _deliver_single(update, context, result, job.platform, note, lang)
            else:
                await run_download(update, context, entry.get("url") or "",
                                   platform=job.platform, lang=lang, status=note)
        except Exception as exc:
            logger.warning("playlist item %s failed: %s", idx, exc)
            try:
                await note.edit_text(get_text("DOWNLOAD_FAILED", lang,
                                              error=str(exc)[:150],
                                              hint=get_text("HINT_RETRY", lang)))
            except Exception:
                pass


async def _deliver_single(update, context, result, platform, note, lang) -> None:
    """Send one already-downloaded result (used by the Spotify track path)."""
    from bot.handlers.download import build_caption
    from bot.utils import sender
    from bot.utils.media_handler import delete_file, get_file_size

    if not result.items:
        return
    item = result.items[0]
    size = get_file_size(item.path)
    try:
        await sender.send_media(
            update.effective_message, item.path,
            caption=build_caption(result, platform=platform, size=size, lang=lang),
            kind=item.kind, filename=item.filename,
            performer=result.uploader, title=result.title,
        )
        await db.add_download(update.effective_user.id, platform, item.path, size, 1)
        try:
            await note.delete()
        except Exception:
            pass
    finally:
        await delete_file(item.path)


async def _post_download(update: Update, context: ContextTypes.DEFAULT_TYPE,
                         data: str, lang: str) -> None:
    query = update.callback_query
    _, action, token = data.split(":", 2)
    job = jobs.get(token)
    if job is None:
        await query.answer(get_text("HINT_RETRY", lang))
        return
    # 'fav' is personal (each user has their own favourites), so anyone may tap
    # it on any result. The actions that spend bandwidth are owner-only.
    if action != "fav" and not await _owns_job(query, job, lang):
        return
    await query.answer()

    from bot.handlers.download import convert_and_send_mp3, run_download, send_subtitle

    if action == "mp3":
        await convert_and_send_mp3(update, context, job.url, job.platform, lang)
    elif action == "sub":
        await send_subtitle(update, context, job.url, job.platform, lang)
    elif action == "fav":
        user_id = query.from_user.id
        if await db.is_favorite(user_id, job.url):
            await db.remove_favorite(user_id, job.url)
            await query.answer(get_text("FAV_REMOVED", lang))
            is_fav = False
        else:
            await db.add_favorite(user_id, job.url, job.platform, job.title or "")
            await query.answer(get_text("FAV_ADDED", lang))
            is_fav = True
        # Reflect the new state on the button without touching the media.
        from bot.keyboards.inline import after_download_keyboard
        try:
            await query.message.edit_reply_markup(
                reply_markup=after_download_keyboard(
                    token, lang,
                    show_mp3=(job.platform != "spotify"),
                    show_subtitle=(job.platform == "youtube"),
                    is_fav=is_fav,
                )
            )
        except Exception:
            pass
    elif action == "again":
        await run_download(update, context, job.url, platform=job.platform,
                           quality=job.quality or "best", lang=lang)


# ── admin callbacks ───────────────────────────────────────────────────
async def _admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE,
                        data: str, lang: str) -> None:
    query = update.callback_query
    if not is_admin(query.from_user.id):
        logger.info("Ignored admin callback from non-admin %s", query.from_user.id)
        return

    # The owner panel exposes user lists, the message inbox and bot-wide
    # settings. Even though only the owner can reach it, rendering it inside a
    # group would show all of that to every member — keep it DM-only.
    chat = update.effective_chat
    if chat is not None and chat.type in ("group", "supergroup"):
        await query.answer(get_text("GROUP_PM_ONLY", lang), show_alert=True)
        return

    from bot.handlers import admin as A
    # The inbox keyboard shows the live-feed state, so several branches below
    # need it; import once here instead of in each branch.
    from bot.utils import livefeed

    action = data.split(":", 1)[1]
    await query.answer()

    async def show(text: str, keyboard=None) -> None:
        try:
            await query.message.edit_text(
                text, parse_mode=ParseMode.HTML,
                reply_markup=keyboard or admin_back_keyboard(lang),
                disable_web_page_preview=True,
            )
        except Exception:
            await query.message.reply_text(
                text, parse_mode=ParseMode.HTML,
                reply_markup=keyboard or admin_back_keyboard(lang),
            )

    if action == "panel":
        maintenance = await db.get_setting("maintenance", "0") == "1"
        await show(await A.panel_text(lang), admin_keyboard(lang, maintenance))
    elif action == "stats":
        await show(await A.stats_text(lang))
    elif action == "charts":
        await show(await A.charts_text(lang))
    elif action == "services":
        await show(await A.services_text(lang))
    elif action == "top":
        await show(await A.top_text(lang))
    elif action == "health":
        await show(await A.health_text(lang))
    elif action == "errors":
        await show(await A.errors_text(lang))
    elif action == "users":
        await show(await A.users_text(lang))
    elif action == "finduser":
        context.user_data["await_finduser"] = True
        await query.message.reply_text(get_text("FINDUSER_PROMPT", lang))
    elif action == "platforms":
        states = {}
        for p in config.ALL_PLATFORMS:
            states[p] = await db.get_setting(f"pf_enabled_{p}", "1") != "0"
        from bot.keyboards.inline import platforms_keyboard
        await show(await A.platforms_status(lang), platforms_keyboard(lang, states))
    elif action.startswith("pf:"):
        platform = action.split(":", 1)[1]
        await A.toggle_platform(platform)
        states = {}
        for p in config.ALL_PLATFORMS:
            states[p] = await db.get_setting(f"pf_enabled_{p}", "1") != "0"
        from bot.keyboards.inline import platforms_keyboard
        await show(await A.platforms_status(lang), platforms_keyboard(lang, states))
    elif action == "limits":
        from bot.keyboards.inline import limits_keyboard
        await show(await A.limits_text(lang), limits_keyboard(lang))
    elif action.startswith("lim:"):
        try:
            delta = int(action.split(":", 1)[1])
        except ValueError:
            delta = 0
        if delta:
            await A.adjust_soft_hourly(delta)
        from bot.keyboards.inline import limits_keyboard
        await show(await A.limits_text(lang), limits_keyboard(lang))
    elif action == "cache":
        await show(await A.cache_text(lang), cache_keyboard(lang))
    elif action == "storage":
        await show(await A.storage_text(lang))
    elif action == "clean":
        from bot.utils.queue import sweep_temp, trim_logs
        from bot.utils.media_handler import human_size

        sweep_temp(force=True)
        trim_logs()
        report = await db.maintain(aggressive=True)
        saved = max(0, report["bytes_before"] - report["bytes_after"])
        await show(await A.storage_text(lang) +
                   f"\n\n🗑 -{report['downloads_pruned']} / -{report['cache_pruned']} rows"
                   f" · {human_size(saved)} freed")
    elif action == "cacheclear":
        count = await db.clear_cache()
        await show(get_text("CACHE_CLEARED", lang, count=count), cache_keyboard(lang))
    elif action == "banlist":
        await show(await A.banlist_text(lang))
    elif action == "ban":
        context.user_data["await_ban"] = True
        await query.message.reply_text(get_text("BAN_PROMPT", lang))
    elif action == "unban":
        context.user_data["await_unban"] = True
        await query.message.reply_text(get_text("UNBAN_PROMPT", lang))
    elif action == "bc":
        context.user_data["await_broadcast"] = True
        await query.message.reply_text(get_text("BROADCAST_PROMPT", lang))
    elif action == "bcgo":
        await A.run_broadcast(update, context)
    elif action == "bcall":
        await A.run_broadcast_targeted(update, context, "all")
    elif action == "bcactive":
        await A.run_broadcast_targeted(update, context, "active")
    elif action == "bcno":
        context.user_data.pop("broadcast_msg", None)
        await show(get_text("BROADCAST_CANCELLED", lang))
    elif action == "export":
        await A.send_export(update, lang)
    elif action == "backup":
        await A.send_db_backup(update, lang)
    elif action == "restart":
        from bot.keyboards.inline import restart_confirm_keyboard
        await show(get_text("RESTART_CONFIRM", lang), restart_confirm_keyboard(lang))
    elif action == "restartgo":
        await A.do_restart(update, lang)
    elif action == "maint":
        await A._toggle_maintenance(update, lang)
        maintenance = await db.get_setting("maintenance", "0") == "1"
        await show(await A.panel_text(lang), admin_keyboard(lang, maintenance))
    elif action == "settings":
        await show(get_text("SETTINGS_TITLE", lang) +
                   "\n\n<code>/setchannel @ch</code>\n<code>/setwelcome text</code>")
    elif action == "theme":
        await _theme_screen(query, context, lang, show)
    elif action.startswith("thflag:"):
        from bot.utils import theme as TH
        key = action.split(":", 1)[1]
        await TH.set_flag(key, not await TH.flag(key))
        await _theme_screen(query, context, lang, show)
    elif action.startswith("thpre:"):
        from bot.utils import theme as TH
        await TH.apply_preset(action.split(":", 1)[1])
        await _theme_screen(query, context, lang, show)
    elif action == "thpack":
        from bot.utils import theme as TH
        context.user_data["await_theme_pack"] = True
        await show(get_text("THEME_PACK_PROMPT", lang, div=await TH.divider()),
                   admin_back_keyboard(lang))
    elif action.startswith("grpp:"):
        from bot.keyboards.inline import admin_groups_keyboard

        page = max(0, int(action.split(":", 1)[1] or 0))
        stats = await db.group_stats()
        rows = await db.list_groups(10, page * 10)
        body = await themed("ADMIN_GROUPS_TITLE", lang) + "\n" + \
            await themed("ADMIN_GROUPS_BODY", lang, **stats)
        if not rows:
            body += "\n\n" + get_text("ADMIN_GROUPS_EMPTY", lang)
        await show(body, admin_groups_keyboard(lang, rows, page, stats["total"]))
    elif action.startswith("grp:"):
        from bot.handlers.gsettings import settings_text

        chat_id = int(action.split(":", 1)[1])
        await show(await themed("GSETTINGS_TITLE", lang) + "\n" +
                   await settings_text(chat_id, lang),
                   admin_back_keyboard(lang))
    elif action.startswith("inboxp:"):
        from bot.handlers import inbox as IB
        from bot.keyboards.inline import inbox_keyboard

        page = max(0, int(action.split(":", 1)[1] or 0))
        rows = await db.all_chats(IB.PAGE, page * IB.PAGE)
        await show(await IB.inbox_text(lang, page),
                   inbox_keyboard(lang, rows, page, await db.count_users_total(), await livefeed.enabled()))
    elif action.startswith("inboxt:"):
        from bot.handlers import inbox as IB
        from bot.keyboards.inline import thread_keyboard

        _, uid, page = action.split(":")
        uid, page = int(uid), max(0, int(page))
        # Opening a thread marks it read, the way any messenger behaves.
        await db.mark_seen(uid)
        await show(await IB.thread_text(uid, lang, page),
                   thread_keyboard(lang, uid, page,
                                   await db.count_conversation(uid)))
    elif action in ("feedoff", "feedon", "feedtoggle"):
        if action == "feedtoggle":
            new_state = not await livefeed.enabled()
        else:
            new_state = (action == "feedon")
        await db.set_setting("live_feed", "1" if new_state else "0")
        await query.answer(
            get_text("FEED_ENABLED" if new_state else "FEED_DISABLED", lang),
            show_alert=True)
    elif action.startswith("inboxg:"):
        from bot.handlers import inbox as IB
        from bot.keyboards.inline import gallery_keyboard

        uid = int(action.split(":", 1)[1])
        rows = await db.media_in_thread(uid, 30)
        await show(await IB.gallery_text(uid, lang),
                   gallery_keyboard(lang, uid, rows))
    elif action.startswith("inboxm:"):
        from bot.handlers import inbox as IB

        row_id = int(action.split(":", 1)[1])
        # The panel is DM-only (guarded above), so the owner's user id IS the
        # chat to deliver into — and it can't be None, unlike query.message.
        err = await IB.send_logged_media(context, query.from_user.id,
                                         row_id, lang)
        await query.answer(err or get_text("INBOX_MEDIA_SENT", lang),
                           show_alert=bool(err))
    elif action == "inboxstar":
        from bot.handlers import inbox as IB
        from bot.keyboards.inline import starred_keyboard

        rows = await db.starred_messages(30)
        await show(await IB.starred_text(lang), starred_keyboard(lang, rows))
    elif action == "inboxseenall":
        from bot.handlers import inbox as IB
        from bot.keyboards.inline import inbox_keyboard

        # Mark every conversation read in one pass, then redraw the list.
        for row in await db.recent_chats(500, 0):  # KEEP: only message-bearing rows can be unread
            await db.mark_seen(int(row["user_id"]))
        rows = await db.all_chats(IB.PAGE, 0)
        await query.answer(get_text("INBOX_READALL_OK", lang))
        await show(await IB.inbox_text(lang, 0),
                   inbox_keyboard(lang, rows, 0, await db.count_users_total(), await livefeed.enabled()))
    elif action.startswith("inboxr:"):
        from bot.handlers import inbox as IB

        uid = int(action.split(":", 1)[1])
        context.user_data["await_inbox_reply"] = uid
        profile = await db.get_user(uid) or {}
        who = profile.get("username") and f"@{profile['username']}" or \
            profile.get("first_name") or str(uid)
        await show(get_text("INBOX_REPLY_PROMPT", lang, who=who),
                   admin_back_keyboard(lang))
    elif action.startswith("inboxu:"):
        from bot.handlers import inbox as IB

        uid = int(action.split(":", 1)[1])
        await show(await IB.user_card(uid, lang), admin_back_keyboard(lang))
    elif action.startswith("inboxban:"):
        uid = int(action.split(":", 1)[1])
        await db.ban_user(uid, "from inbox")
        await query.answer(get_text("BAN_SUCCESS", lang, id=uid).replace("<code>","").replace("</code>",""), show_alert=True)
        from bot.handlers import inbox as IB
        from bot.keyboards.inline import inbox_keyboard

        rows = await db.all_chats(IB.PAGE, 0)
        await show(await IB.inbox_text(lang, 0),
                   inbox_keyboard(lang, rows, 0, await db.count_users_total(), await livefeed.enabled()))
    elif action == "inboxfind":
        context.user_data["await_inbox_search"] = True
        await show(get_text("INBOX_SEARCH_PROMPT", lang), admin_back_keyboard(lang))
    elif action.startswith("throle:"):
        from bot.utils import theme as TH
        await TH.cycle_role_style(action.split(":", 1)[1])
        await _theme_screen(query, context, lang, show)
    elif action.startswith("thsep:"):
        try:
            delta = int(action.split(":", 1)[1])
        except ValueError:
            delta = 0
        if delta:
            await A.adjust_sep_len(delta)
        await _theme_screen(query, context, lang, show)
    elif action.startswith("thslot:"):
        slot = action.split(":", 1)[1]
        context.user_data["await_theme_slot"] = slot
        await query.message.reply_text(
            get_text("THEME_SLOT_PROMPT", lang, slot=slot),
            parse_mode=ParseMode.HTML)
    elif action == "thexport":
        from bot.utils import theme as TH
        blob = await TH.export_json()
        await query.message.reply_text(
            get_text("THEME_EXPORT", lang, json=blob[:3500]),
            parse_mode=ParseMode.HTML)
    elif action == "thimport":
        context.user_data["await_theme_import"] = True
        await query.message.reply_text(get_text("THEME_IMPORT_PROMPT", lang),
                                       parse_mode=ParseMode.HTML)
    elif action == "threset":
        from bot.utils import theme as TH
        await TH.reset()
        await _theme_screen(query, context, lang, show)


async def _theme_screen(query, context, lang: str, show) -> None:
    """Render the theme editor with the live theme state on every button."""
    from bot.handlers import admin as A
    from bot.keyboards.inline import theme_keyboard
    from bot.utils import theme as TH

    roles = {r: await TH.role_style(r) for r in TH.STYLE_ROLES}
    await show(await A.theme_text(lang),
               theme_keyboard(lang, await TH.flag("premium"),
                              await TH.flag("styles"),
                              await A.theme_slot_values(),
                              await TH.current_preset(),
                              roles, await TH.flag("btn_icons")))


async def _delete(query) -> None:
    try:
        await query.message.delete()
    except Exception:
        pass
