"""Download flow.

Presentation matches the reference bot the user asked for: a neutral
"⏳ در صف دانلود...." status message, then the media as a streamable video with
a thumbnail, a caption carrying the post text, and an audio button underneath.
"""
import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

from telegram import Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import ContextTypes

from bot import config
from bot.database import db
from bot.i18n import get_text, themed
from bot.keyboards.inline import (
    after_download_keyboard,
    playlist_keyboard,
    quality_keyboard,
)
from bot.utils import sender
from bot.utils.antiblock import circuit
from bot.utils.jobs import Job, jobs
from bot.utils.media_handler import (
    convert_to_mp3,
    delete_file,
    get_file_size,
    human_duration,
    human_size,
)
from bot.utils.queue import DiskFull, queue
from bot.utils.url_parser import (
    detect_platform,
    extract_urls,
    get_service,
    platform_label,
    supported_list,
)

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


# ── error translation ─────────────────────────────────────────────────
def _hint_for(error: str, lang: str) -> str:
    """Pick advice that is actually true for the failure.

    Ordering matters: restriction and rate-limit checks come before the generic
    "retry" fallback, because telling a user to retry a permanently restricted
    post just makes them retry forever.
    """
    e = error.lower()
    # Instagram stories need a logged-in cookie to fetch at all.
    if "story_needs_cookie" in e:
        return get_text("HINT_STORY_COOKIE", lang)
    # Audience/age restricted or region-locked to a subset of viewers. yt-dlp
    # reports these as "isn't available to everyone" / "certain audiences".
    if any(k in e for k in ("certain audiences", "available to everyone",
                            "age-restricted", "age restricted", "inappropriate",
                            "confirm your age")):
        return get_text("HINT_RESTRICTED", lang)
    # Datacenter IP blocked by the platform (not the user's fault, not fixable
    # by retrying immediately).
    if any(k in e for k in ("not a bot", "empty media response", "403 blocked",
                            "too many requests", "429", "rate limit")):
        return get_text("HINT_BLOCKED", lang)
    if any(k in e for k in ("private", "login", "sign in", "authentication", "cookies")):
        return get_text("HINT_LOGIN", lang)
    if any(k in e for k in ("not available in your country", "geo", "blocked in")):
        return get_text("HINT_GEO", lang)
    if any(k in e for k in ("unavailable", "deleted", "removed", "404", "not found")):
        return get_text("HINT_PRIVATE", lang)
    if "live" in e:
        return get_text("HINT_LIVE", lang)
    return get_text("HINT_RETRY", lang)


def _clean_error(exc: BaseException) -> str:
    msg = str(exc)
    # yt-dlp prefixes are noise for end users.
    for prefix in ("ERROR: ", "[youtube] ", "[instagram] ", "[generic] "):
        msg = msg.replace(prefix, "")
    msg = msg.split(";")[0].strip()
    return msg[:200] or exc.__class__.__name__


# ── caption ───────────────────────────────────────────────────────────
def build_caption(result, *, platform: str, size: int = 0, cached: bool = False,
                  lang: str = "fa") -> str:
    """Caption = the original post text, credited to the bot.

    No stats line: the user asked for the real caption plus the bot's handle.
    """
    from html import escape

    from bot.utils.runtime import signature

    sig = signature()
    # Pinterest titles/descriptions are SEO boilerplate, not a real post
    # caption — the user asked to drop them, leaving only the bot handle.
    # Google Play "apps" have no post text either; show the app name + handle.
    if platform == "pinterest":
        return sig[:1024]
    if platform == "googleplay":
        from html import escape as _esc

        name = (getattr(result, "title", "") or "").strip()
        if name and sig:
            return f"{_esc(name)}\n\n{sig}"[:1024]
        return (sig or _esc(name))[:1024]

    body = (result.text or "").strip()
    if not body:
        title = (result.title or "").strip()
        # yt-dlp invents titles like "Video by <user>" when a post has no
        # caption; those carry no information, so drop them.
        if title and not _is_placeholder_title(title, result.uploader):
            body = title

    if len(body) > 900:
        body = body[:900].rstrip() + "…"

    sig = signature()
    if body and sig:
        return f"{escape(body)}\n\n{sig}"[:1024]
    return (escape(body) or sig)[:1024]


_PLACEHOLDER_PATTERNS = (
    "video by ", "photo by ", "post by ", "reel by ",
)


def _message_has_media(msg) -> bool:
    """True if a message carries any hostable media attachment."""
    if msg is None:
        return False
    return bool(
        msg.document or msg.video or msg.audio or msg.voice
        or msg.video_note or msg.photo or msg.animation
    )


def _is_placeholder_title(title: str, uploader: str = "") -> bool:
    low = title.lower()
    if any(low.startswith(p) for p in _PLACEHOLDER_PATTERNS):
        return True
    # A title that is just the uploader's name adds nothing either.
    return bool(uploader) and low.strip() == uploader.lower().strip()


# ── entry point ───────────────────────────────────────────────────────
async def download_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any non-command text message that contains links."""
    message = update.effective_message
    if message is None or not (message.text or message.caption):
        return
    user = update.effective_user
    if user is None:
        return

    # Admin prompts (ban id, broadcast body, ...) take precedence. When one is
    # consumed we must STOP propagation: a broadcast sent as captioned media
    # also matches the group-1 file-to-link handler, which would otherwise
    # host it publicly in addition to queuing the broadcast.
    from bot.handlers.admin import consume_pending_input
    from telegram.ext import ApplicationHandlerStop

    if await consume_pending_input(update, context):
        raise ApplicationHandlerStop

    text = message.text or message.caption or ""
    urls = extract_urls(text, config.MAX_LINKS_PER_MSG)
    lang = await db.get_user_language(user.id)

    from bot.utils.runtime import is_bot_mentioned

    in_group = message.chat.type in ("group", "supergroup")
    mentioned = is_bot_mentioned(message)
    reply_msg = message.reply_to_message

    # Tag-to-act (works in groups AND private): a user replies to a message and
    # @-mentions the bot ("@bot download this"). When the trigger message has
    # no link of its own, pull links — or media — from the message it replies
    # to. An explicit @mention is always required, so a bare reply-to-the-bot
    # never triggers this.
    if mentioned and not urls and reply_msg is not None:
        rt = reply_msg.text or reply_msg.caption or ""
        urls = extract_urls(rt, config.MAX_LINKS_PER_MSG)
        if not urls and _message_has_media(reply_msg):
            from bot.handlers.file_to_link import host_message

            await host_message(update, context, reply_msg, user, lang)
            return

    if not urls:
        # Silent for group chatter; only answer in private chats. But if the bot
        # was tagged with no usable link anywhere, tell the user what to do
        # instead of staying mute.
        if message.chat.type == "private":
            await message.reply_text(get_text("NO_LINK_FOUND", lang))
        elif mentioned:
            await message.reply_text(get_text("NO_LINK_FOUND", lang))
        return

    # Group mode: a single gate decides whether the bot may act here — it
    # covers the admins-only rule, auto-download vs tag-only, and per-chat flood
    # control. Never trigger on a bare reply-to-the-bot: people reply to the bot
    # constantly, which would be spam.
    if in_group:
        from bot.handlers import groups as G

        if not await G.may_act(update, context, mentioned):
            return
        lang = await G.group_lang(message.chat.id, user.id)

    await db.add_user(user.id, user.username or "", user.first_name or "")
    if await db.is_banned(user.id):
        await message.reply_text(get_text("BANNED_MESSAGE", lang))
        return

    if await _maintenance_blocked(user.id, message, lang):
        return
    if not await _channel_ok(update, context, lang):
        return

    if len(urls) == 1:
        await process_url(update, context, urls[0], lang=lang)
        return

    # Batch: report progress, then a summary.
    ok = failed = 0
    total = len(urls)
    for idx, url in enumerate(urls, 1):
        note = await message.reply_text(
            get_text("BATCH_PROGRESS", lang, current=idx, total=total)
        )
        try:
            success = await process_url(update, context, url, lang=lang, status=note)
            ok += 1 if success else 0
            failed += 0 if success else 1
        except Exception as exc:
            logger.error("batch item failed: %s", exc)
            failed += 1
    await message.reply_text(
        get_text("BATCH_SUMMARY", lang, ok=ok, failed=failed, total=total)
    )


async def process_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    *,
    lang: str = "fa",
    quality: str = "",
    status: Optional[Message] = None,
) -> bool:
    """Resolve one URL: cache hit, quality menu, playlist prompt, or download."""
    message = update.effective_message
    user = update.effective_user
    platform = detect_platform(url)

    # Admin kill-switch: a platform disabled from the panel silently tells the
    # user it's temporarily off instead of attempting (and failing) a download.
    if platform in config.ALL_PLATFORMS:
        if await db.get_setting(f"pf_enabled_{platform}", "1") == "0":
            await message.reply_text(get_text("PLATFORM_DISABLED", lang))
            return False

    # Serve from cache before doing anything expensive.
    # Profile pictures and stories are volatile (they change / expire) and an
    # early miss could have cached a placeholder — never serve them from cache.
    volatile = platform == "instagram" and (
        service_hint_volatile(url)
    )

    cache_quality = quality or "best"
    cached = None if volatile else await db.get_cached(url, cache_quality)
    if cached:
        sent = await sender.send_cached(
            message, cached,
            caption=_cached_caption(cached, platform, lang),
            keyboard=after_download_keyboard(
                jobs.put(Job(url=url, platform=platform, user_id=user.id,
                             chat_id=message.chat_id, quality=cache_quality)),
                lang,
                show_mp3=cached.get("file_type") == "video",
                # Cache hits get the PNG button too: the original is re-fetched
                # from Pinterest on demand, so a cached JPEG file_id does not
                # prevent delivering full-resolution pixels.
                show_png=(platform == "pinterest"
                          and cached.get("file_type") == "photo"),
            ),
        )
        if sent is not None:
            if status:
                await _safe_delete(status)
            await db.add_download(user.id, platform, url, cached.get("file_size", 0), 1,
                                  chat_type=message.chat.type)
            # Group bookkeeping applies to cache hits too — otherwise a group's
            # counter only moves on cold downloads and clean-mode leaves the
            # trigger message behind whenever the file was already cached.
            if message.chat.type in ("group", "supergroup"):
                from bot.handlers import groups as G

                await db.bump_group_downloads(message.chat_id)
                conf = await G.settings(message.chat_id)
                if conf.get("clean_mode") and update.callback_query is None:
                    await _safe_delete(message)
            return True

    service = get_service(platform)

    # Honour the user's saved default: skip the quality menu when they picked a
    # fixed quality, and route to audio when they default to audio-only.
    if not quality:
        prefs = await db.get_prefs(user.id)
        # In a group, the group's own default wins over each member's personal
        # preference — otherwise the same link behaves differently per member and
        # the quality menu keeps appearing in a busy chat.
        if message.chat.type in ("group", "supergroup"):
            from bot.handlers.groups import settings as group_settings

            gconf = await group_settings(message.chat.id)
            gq = (gconf.get("def_quality") or "").strip()
            if gq:
                return await run_download(
                    update, context, url, platform=platform, quality=gq,
                    lang=lang, status=status,
                )
        if prefs.get("def_format") == "audio" and platform in (
                "youtube", "tiktok", "instagram", "twitter", "soundcloud"):
            return await run_download(
                update, context, url, platform=platform, quality="mp3",
                lang=lang, status=status,
            )
        pref_q = prefs.get("def_quality", "ask")
        if pref_q not in ("ask", "", None):
            return await run_download(
                update, context, url, platform=platform, quality=pref_q,
                lang=lang, status=status,
            )

    # YouTube Shorts: no quality menu. A Short is a vertical clip of at most a
    # few minutes and the viewer never picks a resolution on YouTube itself, so
    # asking here is pure friction — send the best rendition straight away.
    # A normal upload keeps the menu (see _offer_quality) because 1080p of an
    # hour-long video is hundreds of megabytes and that must stay the user's
    # decision.
    if (not quality and platform == "youtube"
            and getattr(service, "is_short_url", None)
            and service.is_short_url(url)):
        return await run_download(
            update, context, url, platform=platform, quality="best",
            lang=lang, status=status,
        )

    # Offer a quality menu for any platform that declares qualities and can
    # probe them. YouTube and TikTok qualify; platforms with a single usable
    # resolution return no options and fall through to a direct download.
    if (not quality and getattr(service, "qualities", None)
            and hasattr(service, "available_qualities")):
        shown = await _offer_quality(update, context, url, platform, service, lang, status)
        if shown:
            return True

    # Playlists / albums ask before pulling many items.
    if not quality and await _offer_playlist(update, context, url, platform, service, lang, status):
        return True

    return await run_download(
        update, context, url, platform=platform, quality=quality or "best",
        lang=lang, status=status,
    )


# ── quality + playlist prompts ────────────────────────────────────────
async def _offer_quality(update, context, url, platform, service, lang, status) -> bool:
    message = update.effective_message
    user = update.effective_user
    note = status or await message.reply_text(
        await themed("PROCESSING", lang), parse_mode="HTML")
    try:
        info = await service.available_qualities(url)
    except Exception as exc:
        logger.info("quality probe failed for %s: %s", url, exc)
        await _safe_delete(note)
        return False

    if info.get("is_live"):
        await _edit(note, get_text("DOWNLOAD_FAILED", lang,
                                   error="live stream", hint=get_text("HINT_LIVE", lang)))
        return True
    if not info.get("options"):
        await _safe_delete(note)
        return False

    # A Short shared as youtu.be/<id> or watch?v=<id> carries no /shorts/ marker,
    # so the URL check upstream could not catch it. Now that the probe is done we
    # know its duration and frame shape: if it is really a Short, skip the menu
    # and take the highest quality the probe actually found. `top_quality` is a
    # concrete tier (e.g. "1080") rather than the generic "best", so the pick is
    # the real ceiling of THIS video, not the platform default.
    if (platform == "youtube" and getattr(service, "looks_like_short", None)
            and service.looks_like_short(info)):
        logger.info("youtube: %s detected as a Short (%ss, %sx%s) — skipping menu",
                    url, info.get("duration"), info.get("width"), info.get("height"))
        await _safe_delete(note)
        return await run_download(
            update, context, url, platform=platform,
            quality=info.get("top_quality") or "best", lang=lang,
        )

    token = jobs.put(Job(url=url, platform=platform, user_id=user.id,
                         chat_id=message.chat_id, title=info.get("title", ""),
                         payload={"info": info}))
    await _edit(
        note,
        get_text("QUALITY_PROMPT", lang,
                 title=info.get("title", "")[:90],
                 uploader=info.get("uploader", ""),
                 duration=human_duration(info.get("duration", 0)) or "—",
                 views=f"{info.get('view_count', 0):,}"),
        keyboard=quality_keyboard(token, info["options"], lang,
                                  audio_size=info.get("audio_size", 0)),
    )
    return True


async def _offer_playlist(update, context, url, platform, service, lang, status) -> bool:
    """Ask before downloading a playlist/album; single items skip this."""
    message = update.effective_message
    user = update.effective_user

    is_collection = False
    entries: List[Dict[str, Any]] = []
    title = ""

    if platform == "spotify":
        try:
            info = await service.get_info(url)
            if info.extra.get("is_collection") and info.extra.get("track_count", 0) > 1:
                is_collection = True
                title = info.title
                entries = [{"title": t["title"], "track": t}
                           for t in info.extra.get("tracks", [])]
        except Exception as exc:
            logger.debug("spotify collection probe failed: %s", exc)
    elif platform == "youtube" and "list=" in url and "watch?v=" not in url:
        try:
            entries = await service.playlist_entries(url)
            is_collection = len(entries) > 1
            title = "YouTube playlist"
        except Exception as exc:
            logger.debug("playlist probe failed: %s", exc)

    if not is_collection or len(entries) < 2:
        return False

    token = jobs.put(Job(url=url, platform=platform, user_id=user.id,
                         chat_id=message.chat_id, title=title,
                         payload={"entries": entries}))
    body = get_text("PLAYLIST_FOUND", lang, title=title[:90], count=len(entries))
    keyboard = playlist_keyboard(token, len(entries), lang)
    if status:
        await _edit(status, body, keyboard=keyboard)
    else:
        await message.reply_text(body, parse_mode="HTML", reply_markup=keyboard)
    return True


# ── the actual download ───────────────────────────────────────────────
def service_hint_volatile(url: str) -> bool:
    """True for Instagram profile-pic and story links.

    These are volatile: a profile picture changes, stories expire in 24h, and a
    rate-limited early attempt might have cached a 150px placeholder. So we
    always re-fetch them and never cache the result.
    """
    from bot.services.instagram import InstagramDownloader as _IG

    ig = _IG()
    if ig.story_target(url):
        return True
    # A bare profile URL (no /p/, /reel/, /stories/) means profile picture.
    return bool(ig.profile_username(url)) and not ig.shortcode(url)


async def run_download(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    *,
    platform: str,
    quality: str = "best",
    lang: str = "fa",
    status: Optional[Message] = None,
    silent: bool = False,
) -> bool:
    message = update.effective_message
    user = update.effective_user
    service = get_service(platform)
    started = time.time()
    # A download triggered by tapping an inline button (quality/audio/again/…)
    # arrives as a callback, and update.effective_message is then the message
    # the BUTTON lives on — i.e. the already-delivered media, not a fresh link.
    # Clean-mode must never delete that; it may only remove a genuine trigger
    # link message. So remember whether this run is button-originated.
    from_button = update.callback_query is not None

    note = status
    if note is None and not silent:
        # Start at "checking" — QUEUED is only shown if the job really waits,
        # so an instant download never claims it was queued.
        note = await message.reply_text(await themed("PROCESSING", lang),
                                       parse_mode="HTML")

    waited = {"yes": False}

    async def announce_queued() -> None:
        waited["yes"] = True
        if note is not None:
            await _edit(note, await themed("QUEUED", lang))

    hourly = await db.get_user_hourly(user.id)

    async def job() -> Any:
        if note is not None:
            await _edit(note, await themed("DOWNLOADING", lang))
        await _send_action(context, message.chat_id, platform)
        return await service.download(url, quality)

    result = None
    try:
        result = await queue.run(user.id, job, hourly_count=hourly,
                                 on_queued=announce_queued)
    except DiskFull as exc:
        logger.error("disk full: %s", exc)
        if note is not None:
            await _edit(note, get_text("ERROR_GENERIC", lang))
        await db.add_download(user.id, platform, url, 0, 0, str(exc),
                              chat_type=message.chat.type)
        return False
    except Exception as exc:
        msg = _clean_error(exc)
        if msg.startswith("COOLDOWN:"):
            msg = "platform temporarily unavailable"
        # Telegram story links need a user session (bot tokens are API-
        # restricted from stories). Tell the owner to run /tglogin.
        if msg == "__STORY_NEEDS_LOGIN__":
            if note is not None:
                await _edit(note, get_text("STORY_NEEDS_LOGIN", lang))
            await db.add_download(user.id, platform, url, 0, 0,
                                  "telegram story: no user session",
                                  chat_type=message.chat.type)
            return False
        logger.warning("download failed [%s] %s: %s", platform, url[:80], msg)
        await db.add_download(user.id, platform, url, 0, 0, msg,
                              int((time.time() - started) * 1000),
                              chat_type=message.chat.type)
        if note is not None:
            await _edit(note, get_text("DOWNLOAD_FAILED", lang, error=msg,
                                       hint=_hint_for(msg, lang)))
        return False

    # Text-only posts (e.g. a tweet with no media).
    if not result.items:
        if result.extra.get("text_only") and result.text:
            body = get_text("TEXT_ONLY_POST", lang, text=result.text[:900])
            if note is not None:
                await _edit(note, body)
            else:
                await message.reply_text(body, parse_mode="HTML")
            await db.add_download(user.id, platform, url, 0, 1,
                                  chat_type=message.chat.type)
            return True
        if note is not None:
            await _edit(note, get_text("DOWNLOAD_FAILED", lang, error="no media",
                                       hint=get_text("HINT_PRIVATE", lang)))
        return False

    paths = [item.path for item in result.items]
    total_size = sum(get_file_size(p) for p in paths)

    try:
        if note is not None:
            await _edit(note, await themed("UPLOADING", lang))
        await _send_action(context, message.chat_id,
                           "audio" if result.items[0].kind == "audio" else platform)

        token = jobs.put(Job(url=url, platform=platform, user_id=user.id,
                             chat_id=message.chat_id, quality=quality,
                             title=result.title))
        caption = build_caption(result, platform=platform, size=total_size, lang=lang)

        if result.is_album:
            file_ids = await sender.send_album(message, paths, caption=caption)
            # NOTE: albums are intentionally NOT file_id-cached. The cache row
            # holds a single file_id, so re-sending a cached album used to
            # deliver only its first item. Correctness beats the minor speed
            # win here; multi-item posts re-download (still fast for photos).
        else:
            item = result.items[0]
            is_fav = await db.is_favorite(user.id, url)
            sent = await sender.send_media(
                message, item.path,
                caption=caption,
                kind=item.kind,
                filename=item.filename,
                performer=result.uploader,
                title=result.title,
                keyboard=after_download_keyboard(
                    token, lang,
                    show_mp3=(item.kind == "video"),
                    show_subtitle=(platform == "youtube" and item.kind == "video"),
                    is_fav=is_fav,
                    # A Pinterest still can be re-delivered as a full-resolution
                    # PNG document, bypassing Telegram's JPEG re-encode.
                    show_png=(platform == "pinterest" and item.kind == "photo"),
                ),
            )
            if sent.get("file_id") and not service_hint_volatile(url):
                meta = sent.get("meta") or {}
                await db.put_cached(
                    url, quality, sent["file_id"], sent["file_type"],
                    platform=platform, title=result.title, file_size=sent["size"],
                    duration=meta.get("duration", result.duration),
                    width=meta.get("width", result.width),
                    height=meta.get("height", result.height),
                )

        await db.add_download(user.id, platform, url, total_size, 1,
                              "", int((time.time() - started) * 1000),
                              chat_type=message.chat.type)
        # Group bookkeeping + noise control: count the download against the group
        # and, when clean mode is on, remove the trigger message too so the chat
        # is left with just the delivered file.
        if message.chat.type in ("group", "supergroup"):
            try:
                await db.bump_group_downloads(message.chat_id)
                from bot.handlers.groups import settings as group_settings

                # Only delete a genuine trigger link — never the delivered media
                # a button was attached to (audio/quality/again re-runs).
                if (not from_button
                        and (await group_settings(message.chat_id)).get("clean_mode")):
                    await _safe_delete(message)
            except Exception:
                logger.debug("group cleanup skipped", exc_info=True)
        if note is not None:
            await _safe_delete(note)
        # Completion ping: only when the user actually waited in a queue AND has
        # result alerts enabled. The status note is edited/deleted in place, so
        # a user who tabbed away never sees it finish — a fresh reply pings them.
        # Never ping in a group: it is noise for everyone else.
        if waited["yes"] and message.chat.type == "private":
            try:
                prefs = await db.get_prefs(user.id)
                if prefs.get("notify", True):
                    await message.reply_text(get_text("NOTIFY_READY", lang))
            except Exception:
                logger.debug("completion ping failed", exc_info=True)
        return True

    except Exception as exc:
        msg = _clean_error(exc)
        logger.error("upload failed [%s]: %s", platform, msg)
        await db.add_download(user.id, platform, url, total_size, 0, msg,
                              chat_type=message.chat.type)
        if note is not None:
            await _edit(note, get_text("DOWNLOAD_FAILED", lang, error=msg,
                                       hint=get_text("HINT_RETRY", lang)))
        return False
    finally:
        # Temp files always go, even on a failed upload.
        await delete_file(*paths)


async def convert_and_send_mp3(update, context, url: str, platform: str, lang: str) -> bool:
    """The '🎵 audio' button: fetch audio for a link already delivered."""
    message = update.effective_message
    user = update.effective_user
    cached = await db.get_cached(url, "mp3")
    if cached:
        sent = await sender.send_cached(message, cached,
                                       caption=_cached_caption(cached, platform, lang))
        if sent is not None:
            return True
    return await run_download(update, context, url, platform=platform,
                              quality="mp3", lang=lang)


async def send_original_png(update, context, url: str, platform: str,
                            lang: str) -> bool:
    """The '🖼 PNG' button: deliver the pin's original pixels as a document.

    Sent with ``reply_document`` on purpose. ``send_photo`` would re-encode to
    JPEG and cap the long side at 2560px, which is exactly the quality loss this
    button exists to avoid.

    The file is always removed afterwards — /data is a 434MB volume, so a PNG
    (which is much larger than the source JPEG) must never be left behind, even
    when the upload fails.
    """
    message = update.effective_message
    service = get_service(platform)
    if not hasattr(service, "original_image"):
        await message.reply_text(get_text("PNG_UNAVAILABLE", lang))
        return False

    note = await message.reply_text(get_text("PNG_WORKING", lang))
    path = None
    try:
        path, width, height = await service.original_image(url)
        size = get_file_size(path)
        if size == 0:
            raise ValueError("conversion produced an empty file")
        if size > config.BOT_API_LIMIT:
            # A lossless PNG of a big pin can exceed the Bot API's 50MB ceiling.
            # Say so plainly instead of failing with a raw API error.
            await _edit(note, get_text("PNG_TOO_BIG", lang,
                                       size=f"{size / 1048576:.1f}"))
            return False

        with open(path, "rb") as fh:
            await message.reply_document(
                document=fh,
                filename=os.path.basename(path),
                caption=get_text("PNG_READY", lang, width=width, height=height,
                                 size=f"{size / 1048576:.1f}"),
                parse_mode=ParseMode.HTML,
            )
        await _safe_delete(note)
        return True
    except Exception as exc:
        logger.info("PNG delivery failed for %s: %s", url, exc)
        await _edit(note, get_text("PNG_FAILED", lang))
        return False
    finally:
        if path:
            await delete_file(path)


async def send_subtitle(update, context, url: str, platform: str, lang: str) -> bool:
    """The '📝 subtitle' button: fetch and send the video's captions as .srt."""
    message = update.effective_message
    service = get_service(platform)
    if not hasattr(service, "fetch_subtitle"):
        await message.reply_text(get_text("SUB_NONE", lang))
        return False

    note = await message.reply_text(get_text("SUB_FETCHING", lang))
    path = None
    try:
        path = await service.fetch_subtitle(url)
        if not path or not os.path.exists(path):
            await _edit(note, get_text("SUB_NONE", lang))
            return False
        with open(path, "rb") as fh:
            await message.reply_document(document=fh, filename=os.path.basename(path),
                                         caption=get_text("SUB_READY", lang))
        await _safe_delete(note)
        return True
    except Exception as exc:
        logger.info("subtitle fetch failed for %s: %s", url, exc)
        await _edit(note, get_text("SUB_NONE", lang))
        return False
    finally:
        if path:
            await delete_file(path)


# ── small helpers ─────────────────────────────────────────────────────
def _cached_caption(cached: Dict[str, Any], platform: str, lang: str) -> str:
    """Cached re-sends carry the same caption shape as a fresh download."""
    from html import escape

    from bot.utils.runtime import signature

    sig = signature()
    # Pinterest: drop the SEO title on cached re-sends too — bot handle only.
    if platform == "pinterest":
        return sig[:1024]

    title = (cached.get("title") or "").strip()
    if title and _is_placeholder_title(title):
        title = ""
    if title and sig:
        return f"{escape(title)}\n\n{sig}"[:1024]
    return (escape(title) or sig)[:1024]


async def _send_action(context, chat_id: int, platform: str) -> None:
    action = ChatAction.UPLOAD_VIDEO
    if platform == "audio":
        action = ChatAction.UPLOAD_VOICE
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=action)
    except Exception:
        pass


async def _edit(message: Message, text: str, keyboard=None) -> None:
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=keyboard,
                                disable_web_page_preview=True)
    except Exception as exc:
        logger.debug("edit failed: %s", exc)


async def _safe_delete(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def _maintenance_blocked(user_id: int, message: Message, lang: str) -> bool:
    if is_admin(user_id):
        return False
    if await db.get_setting("maintenance", "0") != "1":
        return False
    await message.reply_text(get_text("MAINTENANCE_NOTICE", lang))
    return True


async def _channel_ok(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> bool:
    """Enforce channel membership when an admin has configured channels.

    Membership logic lives in bot.utils.gate so the download handler, the
    "I joined" button, and /setchannel all behave identically.
    """
    from bot.utils import gate

    user = update.effective_user
    if is_admin(user.id) or not await gate.is_enabled():
        return True

    missing = await gate.missing_channels(context.bot, user.id)
    if not missing:
        return True

    from bot.keyboards.inline import join_keyboard

    names = "\n".join(f"• {gate.channel_label(c)}" for c in missing)
    await update.effective_message.reply_text(
        get_text("JOIN_CHANNEL", lang, channel=names),
        parse_mode="HTML", reply_markup=join_keyboard(missing, lang),
    )
    return False
