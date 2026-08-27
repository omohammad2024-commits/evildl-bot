"""Inline keyboards. Callback data is kept short and namespaced with ':'."""
from typing import Any, Dict, List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from bot.i18n import get_text
from bot.utils.media_handler import human_size

# Callback namespaces:
#   l:<lang>            language choice
#   q:<token>:<quality> quality choice for a pending job
#   pl:<token>:<all|1>  playlist scope
#   a:<action>          admin action
#   d:<action>:<token>  post-download action
#   x                   no-op / close


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🇮🇷 فارسی", callback_data="l:fa"),
        InlineKeyboardButton("🇬🇧 English", callback_data="l:en"),
    ]])


async def tbutton(label: str, *, role: str = "", slot: str = "",
                  callback_data: str = None, url: str = None) -> InlineKeyboardButton:
    """Build a themed button: native colour style + premium icon on the button.

    Bot API 10.2 added ``style`` ("primary"/"success"/"danger") and
    ``icon_custom_emoji_id``; PTB 20.7 does not know either field, so they are
    passed through ``api_kwargs``.
    """
    from bot.utils import theme

    extras = await theme.button_extras(role=role, slot=slot)
    kwargs = {"api_kwargs": extras} if extras else {}
    if url:
        return InlineKeyboardButton(label, url=url, **kwargs)
    return InlineKeyboardButton(label, callback_data=callback_data or "x", **kwargs)


# callback_data prefix -> (role, theme slot). Longest prefix wins, so a specific
# rule like "a:restart" beats the generic "a:". This is what lets EVERY keyboard
# in the bot pick up colours and premium button icons without touching the ~95
# individual button constructions.
_ROLE_RULES = (
    ("a:threset", ("cancel", "fail")),
    ("a:restartgo", ("cancel", "fail")),
    ("a:restart", ("cancel", "fail")),
    ("a:maint", ("cancel", "settings")),
    ("a:cacheclear", ("cancel", "fail")),
    ("a:clean", ("cancel", "fail")),
    ("a:ban", ("cancel", "fail")),
    ("a:unban", ("confirm", "ok")),
    ("a:bcall", ("confirm", "ok")),
    ("a:bcactive", ("confirm", "ok")),
    ("a:bcgo", ("confirm", "ok")),
    ("a:bcno", ("cancel", "fail")),
    ("a:thpre", ("menu", "brand")),
    ("a:thflag", ("menu", "settings")),
    ("a:thslot", ("nav", "")),
    ("a:thsep", ("nav", "")),
    ("a:throle", ("menu", "settings")),
    ("a:thexport", ("action", "upload")),
    ("a:thimport", ("action", "download")),
    ("a:theme", ("menu", "brand")),
    ("g:mode", ("menu", "link")),
    ("g:admins", ("admin", "admin")),
    ("g:clean", ("menu", "cache")),
    ("g:quality", ("quality", "quality")),
    ("g:lang", ("menu", "lang")),
    ("a:grpp:0", ("menu", "users")),
    ("a:grpp", ("nav", "users")),
    ("a:grp", ("menu", "users")),
    # NOTE: the second element must be a real theme SLOT name (see
    # bot.utils.theme.SLOTS) or the premium icon lookup silently does nothing.
    ("a:gatedel", ("cancel", "fail")),
    ("a:gateoff", ("cancel", "fail")),
    ("a:gateadd", ("action", "invite")),
    ("a:gate", ("menu", "sub")),
    ("a:inboxban", ("cancel", "fail")),
    ("a:inboxr", ("action", "invite")),
    ("a:inboxfind", ("menu", "search")),
    ("a:inboxstar", ("menu", "star")),
    ("a:inboxseenall", ("action", "ok")),
    ("a:inboxg", ("menu", "media")),
    ("a:inboxm", ("action", "")),
    ("a:inboxu", ("menu", "stats")),
    ("a:inboxt", ("nav", "")),
    ("a:inboxp", ("nav", "")),
    ("a:inbox", ("menu", "invite")),
    ("a:panel", ("nav", "admin")),
    ("a:stats", ("menu", "stats")),
    ("a:charts", ("menu", "stats")),
    ("a:health", ("menu", "ok")),
    ("a:services", ("menu", "settings")),
    ("a:errors", ("cancel", "fail")),
    ("a:top", ("menu", "star")),
    ("a:users", ("menu", "stats")),
    ("a:finduser", ("menu", "search")),
    ("a:platforms", ("menu", "settings")),
    ("a:limits", ("menu", "settings")),
    ("a:cache", ("menu", "cache")),
    ("a:storage", ("menu", "settings")),
    ("a:export", ("action", "upload")),
    ("a:backup", ("action", "upload")),
    ("a:banlist", ("menu", "fail")),
    ("a:bc", ("menu", "invite")),
    ("a:pf:", ("nav", "")),
    ("a:lim:", ("nav", "")),
    ("a:", ("menu", "")),
    ("m:history", ("menu", "history")),
    ("m:favorites", ("menu", "star")),
    ("m:mystats", ("menu", "stats")),
    ("m:stats", ("menu", "stats")),
    ("m:settings", ("menu", "settings")),
    ("m:invite", ("menu", "invite")),
    ("m:help", ("menu", "help")),
    ("m:lang", ("menu", "lang")),
    ("m:", ("menu", "")),
    ("q:", ("quality", "")),
    ("d:mp3", ("action", "audio")),
    ("d:png", ("action", "upload")),
    ("d:sub", ("action", "sub")),
    ("d:fav", ("action", "star")),
    ("d:again", ("action", "download")),
    ("d:", ("action", "")),
    ("h:", ("menu", "history")),
    ("s:", ("menu", "settings")),
    ("pl:", ("confirm", "ok")),
    ("j:", ("confirm", "ok")),
    ("l:", ("menu", "lang")),
    ("c:yes", ("confirm", "ok")),
    ("c:no", ("cancel", "fail")),
    ("x", ("cancel", "fail")),
)


def _role_for(callback_data: str) -> tuple:
    best = ("", "")
    best_len = -1
    for prefix, pair in _ROLE_RULES:
        if callback_data.startswith(prefix) and len(prefix) > best_len:
            best, best_len = pair, len(prefix)
    return best


def _lead_emoji(text: str) -> str:
    """The first emoji in a button label, or '' when it starts with plain text."""
    from bot.utils.premoji import _EMOJI_RE

    match = _EMOJI_RE.search(text or "")
    if not match:
        return ""
    # Only treat it as an icon when it leads the label (within the first chars),
    # so an emoji buried mid-sentence is not hoisted onto the button icon.
    return match.group(0) if match.start() <= 2 else ""


def _strip_lead_emoji(text: str) -> str:
    """Drop the leading emoji once it has been promoted to the button icon.

    Telegram renders ``icon_custom_emoji_id`` as a separate glyph BEFORE the
    label, so leaving the same emoji in the text shows it twice (the 🟢🟢 bug).
    """
    lead = _lead_emoji(text)
    if not lead:
        return text
    rest = text[text.index(lead) + len(lead):]
    stripped = rest.lstrip(" \u200f\u200e")
    # Never return an empty label: Telegram rejects buttons with no text.
    return stripped or text


# Buttons whose leading glyph IS the information (a live preview or a state
# indicator). Promoting it to the icon and stripping the text would either hide
# what the owner is inspecting or silently swap it for an alias glyph.
# Nothing is exempt any more: every leading glyph becomes a premium icon. Kept as
# a hook in case a future screen needs the raw glyph in its label.
_KEEP_LABEL_EMOJI: tuple = ()


async def themed_kb(markup):
    """Re-emit a keyboard with themed colours and premium button icons.

    Any keyboard built the plain way passes through here on its way out, so the
    owner's theme reaches every screen — admin panel included — without each
    builder having to know about the theme.
    """
    if markup is None or not isinstance(markup, InlineKeyboardMarkup):
        return markup
    from bot.utils import theme

    if not (await theme.flag("styles") or await theme.flag("btn_icons")):
        return markup

    rows = []
    for row in markup.inline_keyboard:
        out = []
        for b in row:
            data = b.callback_data or ""
            role, slot = _role_for(data) if data else ("nav", "")
            extras = await theme.button_extras(role=role, slot=slot)
            label = b.text
            keep = data.startswith(_KEEP_LABEL_EMOJI)
            if await theme.flag("btn_icons") and not keep:
                lead = _lead_emoji(label)
                # A slot icon already covers this button; otherwise resolve the
                # button's own leading emoji so EVERY button gets a premium icon.
                if "icon_custom_emoji_id" not in extras and lead:
                    from bot.utils import premoji

                    emoji_id = await premoji.resolve(lead)
                    if emoji_id:
                        extras["icon_custom_emoji_id"] = emoji_id
                # The icon renders before the label, so remove the now-duplicate
                # glyph from the text.
                if "icon_custom_emoji_id" in extras and lead:
                    label = _strip_lead_emoji(label)
            elif keep:
                # Keep the preview glyph in the text; no icon, no duplicate.
                extras.pop("icon_custom_emoji_id", None)
            if not extras:
                out.append(b)
                continue
            d = b.to_dict()
            d.pop("text", None)
            # to_dict() surfaces api_kwargs fields, which the constructor does not
            # accept — carry them in extras instead of passing them twice.
            for extra_field in ("style", "icon_custom_emoji_id"):
                value = d.pop(extra_field, None)
                if value and extra_field not in extras:
                    extras[extra_field] = value
            # Rebuild the same button, adding the 10.2 fields via api_kwargs.
            out.append(InlineKeyboardButton(label, api_kwargs=extras, **d))
        rows.append(out)
    return InlineKeyboardMarkup(rows)


async def main_keyboard_async(lang: str, is_admin: bool = False,
                             bot_username: str = "") -> InlineKeyboardMarkup:
    """Theme-aware main menu.

    Buttons carry the owner's chosen colour style (Bot API 10.2) and, when the
    slot holds a premium emoji, that emoji is rendered ON the button itself via
    ``icon_custom_emoji_id``. The legacy ``blue_buttons`` flag is still honoured
    for owners who prefer url deep links, but native styles supersede it.
    """
    from bot.utils import theme

    blue = await theme.flag("blue_buttons") and bool(bot_username)

    async def btn(label_key: str, action: str) -> InlineKeyboardButton:
        slot = _MENU_SLOTS.get(action)
        icon = (await theme.plain(slot)) if slot else ""
        label = get_text(label_key, lang)
        if icon:
            # Replace the leading hardcoded emoji with the themed one.
            parts = label.split(" ", 1)
            label = f"{icon} {parts[1]}" if len(parts) == 2 else f"{icon} {label}"
        if blue:
            return await tbutton(
                label, role="menu", slot=slot,
                url=f"https://t.me/{bot_username}?start=go_{action}")
        return await tbutton(label, role="menu", slot=slot,
                             callback_data=f"m:{action}")

    rows = [
        [await btn("BTN_HISTORY", "history"), await btn("BTN_FAVORITES", "favorites")],
        [await btn("BTN_STATS", "mystats"), await btn("BTN_SETTINGS_USER", "settings")],
        [await btn("BTN_INVITE", "invite"), await btn("BTN_HELP", "help")],
        [await btn("BTN_LANGUAGE", "lang")],
    ]
    if is_admin:
        # Admin stays a callback button: it must never leave the chat.
        label = (f"{await theme.plain('admin')} "
                 f"{get_text('BTN_ADMIN', lang).split(' ', 1)[-1]}")
        rows.append([await tbutton(label, role="admin", slot="admin",
                                   callback_data="a:panel")])
    return InlineKeyboardMarkup(rows)


# Menu action -> theme slot used for its button icon.
_MENU_SLOTS = {
    "history": "history",
    "favorites": "star",
    "mystats": "stats",
    "settings": "settings",
    "invite": "invite",
    "help": "help",
    "lang": "lang",
}


def main_keyboard(lang: str, is_admin: bool = False) -> InlineKeyboardMarkup:
    """Main menu. Grouped by purpose: my data, personalization, then admin."""
    rows = [
        [
            InlineKeyboardButton(get_text("BTN_HISTORY", lang), callback_data="m:history"),
            InlineKeyboardButton(get_text("BTN_FAVORITES", lang), callback_data="m:favorites"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_STATS", lang), callback_data="m:mystats"),
            InlineKeyboardButton(get_text("BTN_SETTINGS_USER", lang), callback_data="m:settings"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_INVITE", lang), callback_data="m:invite"),
            InlineKeyboardButton(get_text("BTN_HELP", lang), callback_data="m:help"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_LANGUAGE", lang), callback_data="m:lang"),
        ],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(get_text("BTN_ADMIN", lang), callback_data="a:panel")])
    return InlineKeyboardMarkup(rows)


def settings_keyboard(lang: str, prefs: Dict[str, Any]) -> InlineKeyboardMarkup:
    """User settings: default quality, default format, result notifications.

    Selection is shown by the button COLOUR (green = chosen), not a 🟢/⚪️ glyph:
    a plain marker would be the only non-premium emoji on screen.
    """
    q = prefs.get("def_quality", "ask")
    fmt = prefs.get("def_format", "video")
    notify = prefs.get("notify", True)

    def pick(label: str, cur: str, val: str, data: str) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            label, callback_data=data,
            api_kwargs={"style": "success"} if cur == val else {})

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text("SET_QUALITY_LABEL", lang), callback_data="x")],
        [
            pick(get_text("SET_Q_ASK", lang), q, "ask", "s:q:ask"),
            pick(get_text("SET_Q_BEST", lang), q, "best", "s:q:best"),
        ],
        [
            pick("720p", q, "720", "s:q:720"),
            pick("480p", q, "480", "s:q:480"),
            pick("360p", q, "360", "s:q:360"),
        ],
        [InlineKeyboardButton(get_text("SET_FORMAT_LABEL", lang), callback_data="x")],
        [
            pick(get_text("SET_F_VIDEO", lang), fmt, "video", "s:f:video"),
            pick(get_text("SET_F_AUDIO", lang), fmt, "audio", "s:f:audio"),
        ],
        [InlineKeyboardButton(
            get_text("SET_NOTIFY", lang), callback_data="s:n:toggle",
            api_kwargs={"style": "success" if notify else "danger"})],
        [InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x")],
    ])


def favorites_keyboard(entries: List[Dict[str, Any]], tokens: List[str],
                       lang: str) -> InlineKeyboardMarkup:
    """One re-download button per saved link, plus a close button."""
    from bot.utils.url_parser import platform_emoji

    rows: List[List[InlineKeyboardButton]] = []
    for entry, token in zip(entries, tokens):
        title = (entry.get("title") or entry.get("url") or "").strip()
        label = f"{platform_emoji(entry.get('platform', ''))} {title[:38]}"
        rows.append([InlineKeyboardButton(label or "—", callback_data=f"h:{token}")])
    rows.append([InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x")])
    return InlineKeyboardMarkup(rows)


def history_keyboard(entries: List[Dict[str, Any]], tokens: List[str],
                     lang: str) -> InlineKeyboardMarkup:
    """One re-download button per recent item, plus a close button.

    ``tokens`` are short job tokens (parallel to ``entries``) so the callback
    data stays tiny and the URL is looked up server-side.
    """
    from bot.utils.url_parser import platform_emoji

    rows: List[List[InlineKeyboardButton]] = []
    for entry, token in zip(entries, tokens):
        title = (entry.get("title") or entry.get("url") or "").strip()
        label = f"{platform_emoji(entry.get('platform', ''))} {title[:38]}"
        rows.append([InlineKeyboardButton(label or "—", callback_data=f"h:{token}")])
    rows.append([InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x")])
    return InlineKeyboardMarkup(rows)


def quality_keyboard(token: str, options: List[Dict[str, Any]], lang: str,
                     audio_size: int = 0) -> InlineKeyboardMarkup:
    """Quality picker with real size estimates, two buttons per row.

    Options are pre-filtered by the service so nothing unsendable is shown, and
    one entry may carry ``recommended`` — that one gets a ⭐ so the user has a
    steer instead of four bare numbers. The star is the only difference; every
    listed tier remains selectable.
    """
    labels = {360: "QUALITY_360", 480: "QUALITY_480", 720: "QUALITY_720",
              1080: "QUALITY_1080"}
    row: List[InlineKeyboardButton] = []
    rows: List[List[InlineKeyboardButton]] = []
    for opt in options:
        h = opt.get("height") or 0
        key = labels.get(h)
        label = get_text(key, lang) if key else f"{h}p"
        if opt.get("size"):
            label = f"{label} · {human_size(opt['size'])}"
        if opt.get("recommended"):
            label = f"⭐ {label}"
        row.append(InlineKeyboardButton(label, callback_data=f"q:{token}:{opt['quality']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    mp3_label = get_text("QUALITY_MP3", lang)
    if audio_size:
        mp3_label = f"{mp3_label} · {human_size(audio_size)}"
    rows.append([InlineKeyboardButton(mp3_label, callback_data=f"q:{token}:mp3")])
    rows.append([InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x")])
    return InlineKeyboardMarkup(rows)


def playlist_keyboard(token: str, count: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(get_text("BTN_ALL_ITEMS", lang, count=count),
                                 callback_data=f"pl:{token}:all"),
            InlineKeyboardButton(get_text("BTN_FIRST_ONLY", lang),
                                 callback_data=f"pl:{token}:1"),
        ],
        [InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x")],
    ])


def after_download_keyboard(token: str, lang: str, *,
                            show_mp3: bool = True,
                            show_subtitle: bool = False,
                            is_fav: bool = False,
                            show_png: bool = False,
                            direct_url: str = "") -> InlineKeyboardMarkup:
    """Actions under a delivered file, mirroring the reference bot layout."""
    rows: List[List[InlineKeyboardButton]] = []
    top: List[InlineKeyboardButton] = []
    if show_mp3:
        top.append(InlineKeyboardButton(get_text("BTN_MP3", lang),
                                        callback_data=f"d:mp3:{token}"))
    if show_subtitle:
        top.append(InlineKeyboardButton(get_text("BTN_SUBTITLE", lang),
                                        callback_data=f"d:sub:{token}"))
    if top:
        rows.append(top)
    # Pinterest stills: offer the untouched original as a PNG document. Its own
    # row because it is the primary action for an image pin, not a side option.
    if show_png:
        rows.append([InlineKeyboardButton(get_text("BTN_PNG", lang),
                                         callback_data=f"d:png:{token}")])
    row = [InlineKeyboardButton(get_text("BTN_REDOWNLOAD", lang),
                                callback_data=f"d:again:{token}")]
    fav_key = "BTN_FAV_ON" if is_fav else "BTN_FAV_OFF"
    row.append(InlineKeyboardButton(get_text(fav_key, lang),
                                    callback_data=f"d:fav:{token}"))
    rows.append(row)
    if direct_url:
        rows.append([InlineKeyboardButton(get_text("BTN_LINK", lang), url=direct_url)])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(lang: str, yes: str = "c:yes", no: str = "c:no") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(get_text("BTN_CONFIRM_YES", lang), callback_data=yes),
        InlineKeyboardButton(get_text("BTN_CONFIRM_NO", lang), callback_data=no),
    ]])


def join_keyboard(channels: list, lang: str) -> InlineKeyboardMarkup:
    """One button per channel the user still needs, then a re-check button.

    ``channels`` is a list of gate entries. A channel with no public username
    and no invite link is skipped rather than rendered as a dead button.
    """
    from bot.utils.gate import channel_label, channel_link

    rows = []
    for entry in channels:
        url = channel_link(entry)
        if not url:
            continue
        label = channel_label(entry)
        rows.append([InlineKeyboardButton(f"📢 {label}"[:60], url=url)])
    rows.append([InlineKeyboardButton(get_text("BTN_JOINED", lang),
                                      callback_data="j:check")])
    return InlineKeyboardMarkup(rows)


# ── admin ─────────────────────────────────────────────────────────────
def admin_keyboard(lang: str, maintenance: bool = False) -> InlineKeyboardMarkup:
    maint = get_text("BTN_ADMIN_MAINTENANCE", lang) + (" ✅" if maintenance else "")
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(get_text("BTN_ADMIN_STATS", lang), callback_data="a:stats"),
            InlineKeyboardButton(get_text("BTN_ADMIN_CHARTS", lang), callback_data="a:charts"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_ADMIN_HEALTH", lang), callback_data="a:health"),
            InlineKeyboardButton(get_text("BTN_ADMIN_SERVICES", lang), callback_data="a:services"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_ADMIN_TOP", lang), callback_data="a:top"),
            InlineKeyboardButton(get_text("BTN_ADMIN_ERRORS", lang), callback_data="a:errors"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_ADMIN_USERS", lang), callback_data="a:users"),
            InlineKeyboardButton(get_text("BTN_ADMIN_FINDUSER", lang), callback_data="a:finduser"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_ADMIN_PLATFORMS", lang), callback_data="a:platforms"),
            InlineKeyboardButton(get_text("BTN_ADMIN_LIMITS", lang), callback_data="a:limits"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_ADMIN_CACHE", lang), callback_data="a:cache"),
            InlineKeyboardButton(get_text("BTN_ADMIN_STORAGE", lang), callback_data="a:storage"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_ADMIN_BROADCAST", lang), callback_data="a:bc"),
            InlineKeyboardButton(get_text("BTN_ADMIN_EXPORT", lang), callback_data="a:export"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_ADMIN_BAN", lang), callback_data="a:ban"),
            InlineKeyboardButton(get_text("BTN_ADMIN_UNBAN", lang), callback_data="a:unban"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_ADMIN_BANLIST", lang), callback_data="a:banlist"),
            InlineKeyboardButton(get_text("BTN_ADMIN_BACKUP", lang), callback_data="a:backup"),
        ],
        [
            InlineKeyboardButton(maint, callback_data="a:maint"),
            InlineKeyboardButton(get_text("BTN_ADMIN_RESTART", lang), callback_data="a:restart"),
        ],
        [
            InlineKeyboardButton(get_text("INBOX_BTN_ADMIN", lang), callback_data="a:inboxp:0"),
            InlineKeyboardButton(get_text("BTN_ADMIN_GROUPS", lang), callback_data="a:grpp:0"),
            InlineKeyboardButton(get_text("BTN_ADMIN_THEME", lang), callback_data="a:theme"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_ADMIN_GATE", lang), callback_data="a:gate"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_ADMIN_REFRESH", lang), callback_data="a:panel"),
            InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x"),
        ],
    ])


def gate_keyboard(lang: str, items: list) -> InlineKeyboardMarkup:
    """Forced-join manager: add, remove per channel, and a global off switch.

    Each configured channel gets its own remove button keyed by channel id, so
    the owner never has to type a handle. Callback data carries the id, which for
    a private channel is a ``-100…`` number — well inside the 64-byte budget.
    """
    rows: list = []
    for entry in items:
        label = entry.get("title") or entry.get("handle") or entry.get("id", "")
        if len(label) > 22:
            label = label[:21] + "…"
        rows.append([InlineKeyboardButton(
            f"➖ {label}",
            callback_data=f"a:gatedel:{entry.get('id')}")])
    rows.append([
        InlineKeyboardButton(get_text("GATE_BTN_ADD", lang),
                             callback_data="a:gateadd"),
    ])
    if items:
        rows.append([
            InlineKeyboardButton(get_text("GATE_BTN_OFF", lang),
                                 callback_data="a:gateoff"),
        ])
    rows.append([
        InlineKeyboardButton(get_text("BTN_REFRESH", lang), callback_data="a:gate"),
        InlineKeyboardButton(get_text("BTN_BACK", lang), callback_data="a:panel"),
    ])
    rows.append([
        InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x"),
    ])
    return InlineKeyboardMarkup(rows)


def theme_keyboard(lang: str, premium: bool, blue: bool,
                   values: dict = None, preset: str = "",
                   roles: dict = None, icons: bool = True) -> InlineKeyboardMarkup:
    """Theme editor: presets, toggles, per-role colours, then the emoji slots.

    ``values`` maps slot -> the plain glyph currently in effect and ``roles``
    maps button-role -> its Bot API style, so every control previews live state.
    """
    from bot.utils.theme import PRESETS, SLOTS

    values = values or {}
    roles = roles or {}
    # Toggle state is shown by the button COLOUR, not a plain 🟢/🔴 glyph.
    prem = get_text("THEME_BTN_PREMIUM", lang)
    blu = get_text("THEME_BTN_STYLES", lang)

    rows = []
    # Preset picker, two per row, the active one marked.
    names = list(PRESETS.keys())
    for i in range(0, len(names), 2):
        row = []
        for n in names[i:i + 2]:
            label = str(PRESETS[n].get("_label") or n)
            if n == preset:
                label = f"• {label} •"
            row.append(InlineKeyboardButton(label, callback_data=f"a:thpre:{n}"))
        rows.append(row)

    rows.append([InlineKeyboardButton(
        prem, callback_data="a:thflag:premium",
        api_kwargs={"style": "success" if premium else "danger"})])
    rows.append([InlineKeyboardButton(
        blu, callback_data="a:thflag:styles",
        api_kwargs={"style": "success" if blue else "danger"})])
    rows.append([InlineKeyboardButton(
        get_text("THEME_BTN_ICONS", lang), callback_data="a:thflag:btn_icons",
        api_kwargs={"style": "success" if icons else "danger"})])
    rows.append([InlineKeyboardButton(get_text("THEME_BTN_PACK", lang),
                                     callback_data="a:thpack")])
    rows.append([
        InlineKeyboardButton(get_text("THEME_BTN_SEP_MINUS", lang), callback_data="a:thsep:-2"),
        InlineKeyboardButton(get_text("THEME_BTN_SEP_PLUS", lang), callback_data="a:thsep:2"),
    ])
    # One button per button-role: tap to cycle grey -> blue -> green -> red.
    if roles:
        for i in range(0, len(roles), 2):
            chunk = list(roles.items())[i:i + 2]
            # Each role button wears the colour it controls: the button IS the
            # preview, so no plain colour-dot emoji is needed.
            rows.append([
                InlineKeyboardButton(
                    get_text("THEME_ROLE_" + role.upper(), lang),
                    callback_data=f"a:throle:{role}",
                    api_kwargs={"style": style} if style else {})
                for role, style in chunk
            ])

    # Slot buttons, three per row, labelled with the live glyph.
    slots = list(SLOTS.keys())
    for i in range(0, len(slots), 3):
        rows.append([
            InlineKeyboardButton(f"{values.get(s, SLOTS[s])} {s}",
                                 callback_data=f"a:thslot:{s}")  # glyph -> premium icon
            for s in slots[i:i + 3]
        ])
    rows.append([
        InlineKeyboardButton(get_text("THEME_BTN_EXPORT", lang), callback_data="a:thexport"),
        InlineKeyboardButton(get_text("THEME_BTN_IMPORT", lang), callback_data="a:thimport"),
    ])
    rows.append([
        InlineKeyboardButton(get_text("THEME_BTN_RESET", lang), callback_data="a:threset"),
    ])
    rows.append([
        InlineKeyboardButton(get_text("BTN_BACK", lang), callback_data="a:panel"),
        InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x"),
    ])
    return InlineKeyboardMarkup(rows)


def platforms_keyboard(lang: str, states: dict) -> InlineKeyboardMarkup:
    """A toggle button per platform, two per row.

    State is carried by the button COLOUR (green = on, red = off) rather than a
    dot in the label: the themed icon already renders before the text, so a dot
    would read as a duplicate glyph.
    """
    from bot.utils.url_parser import platform_label

    rows: list = []
    row: list = []
    for p, on in states.items():
        # platform_label() already carries the platform emoji; themed_kb promotes
        # it to a premium icon, so do not prefix another one here.
        row.append(InlineKeyboardButton(
            platform_label(p),
            callback_data=f"a:pf:{p}",
            api_kwargs={"style": "success" if on else "danger"}))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton(get_text("BTN_BACK", lang), callback_data="a:panel"),
        InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x"),
    ])
    return InlineKeyboardMarkup(rows)


def feed_keyboard(lang: str, user_id: int = 0) -> InlineKeyboardMarkup:
    """Buttons under a live activity feed message.

    With a single subject the owner can jump straight into that user's thread,
    reply, or ban. A mixed batch only offers the inbox, since guessing which of
    several users the buttons refer to would be worse than not offering them.
    """
    rows: list = []
    if user_id:
        rows.append([
            InlineKeyboardButton(get_text("FEED_BTN_OPEN", lang),
                                 callback_data=f"a:inbox:{user_id}"),
            InlineKeyboardButton(get_text("INBOX_BTN_REPLY", lang),
                                 callback_data=f"a:inboxr:{user_id}"),
        ])
        rows.append([
            InlineKeyboardButton(get_text("INBOX_BTN_BAN", lang),
                                 callback_data=f"a:inboxban:{user_id}"),
            InlineKeyboardButton(get_text("FEED_BTN_MUTE", lang),
                                 callback_data="a:feedoff"),
        ])
    else:
        rows.append([
            InlineKeyboardButton(get_text("INBOX_BTN_ADMIN", lang),
                                 callback_data="a:inboxp:0"),
            InlineKeyboardButton(get_text("FEED_BTN_MUTE", lang),
                                 callback_data="a:feedoff"),
        ])
    return InlineKeyboardMarkup(rows)


def inbox_keyboard(lang: str, rows: list, page: int, total: int,
                   feed_on: bool = True) -> InlineKeyboardMarkup:
    """Chat list: one button per user, then paging and global actions.

    The button mirrors the screenshot layout the owner asked for: name, message
    count, and an unread badge — tapping it opens that user's DM history.
    ``feed_on`` only decides which label the live-feed toggle shows; it is passed
    in rather than read here so this stays a pure, synchronous builder.
    """
    from bot.handlers.inbox import PAGE, _clip, _who

    buttons: list = []
    for row in rows:
        unread = int(row.get("unread") or 0)
        badge = f" 🔴{unread}" if unread else ""
        count = int(row.get("total") or 0)
        banned = "🚫 " if row.get("is_banned") else "👤 "
        label = f"{banned}{_clip(_who(row), 20)}"
        if count:
            label += f" · {count}"
        buttons.append([InlineKeyboardButton(
            f"{label}{badge}", callback_data=f"a:inbox:{row['user_id']}")])
    nav: list = []
    if page > 0:
        nav.append(InlineKeyboardButton(get_text("BTN_PREV", lang),
                                        callback_data=f"a:inboxp:{page - 1}"))
    if (page + 1) * PAGE < total:
        nav.append(InlineKeyboardButton(get_text("BTN_NEXT", lang),
                                        callback_data=f"a:inboxp:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([
        InlineKeyboardButton(get_text("INBOX_BTN_SEARCH", lang),
                             callback_data="a:inboxfind"),
        InlineKeyboardButton(get_text("INBOX_BTN_STARRED", lang),
                             callback_data="a:inboxstar"),
    ])
    buttons.append([
        InlineKeyboardButton(get_text("INBOX_BTN_READALL", lang),
                             callback_data="a:inboxseenall"),
        InlineKeyboardButton(get_text("BTN_REFRESH", lang),
                             callback_data=f"a:inboxp:{page}"),
    ])
    buttons.append([
        InlineKeyboardButton(get_text("INBOX_BTN_BROADCAST", lang),
                             callback_data="a:bc"),
        InlineKeyboardButton(
            get_text("FEED_BTN_TOGGLE_ON" if feed_on
                     else "FEED_BTN_TOGGLE_OFF", lang),
            callback_data="a:feedtoggle"),
    ])
    buttons.append([
        InlineKeyboardButton(get_text("BTN_BACK", lang), callback_data="a:panel"),
        InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x"),
    ])
    return InlineKeyboardMarkup(buttons)


def thread_keyboard(lang: str, user_id: int, page: int,
                    total: int) -> InlineKeyboardMarkup:
    """One conversation: reply, media gallery, paging, profile, ban, back."""
    from bot.handlers.inbox import THREAD_PAGE

    rows: list = [[
        InlineKeyboardButton(get_text("INBOX_BTN_REPLY", lang),
                             callback_data=f"a:inboxr:{user_id}"),
        InlineKeyboardButton(get_text("INBOX_BTN_GALLERY", lang),
                             callback_data=f"a:inboxg:{user_id}"),
    ]]
    nav: list = []
    if (page + 1) * THREAD_PAGE < total:
        nav.append(InlineKeyboardButton(get_text("INBOX_BTN_OLDER", lang),
                                        callback_data=f"a:inboxt:{user_id}:{page + 1}"))
    if page > 0:
        nav.append(InlineKeyboardButton(get_text("INBOX_BTN_NEWER", lang),
                                        callback_data=f"a:inboxt:{user_id}:{page - 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(get_text("INBOX_BTN_STATS", lang),
                             callback_data=f"a:inboxu:{user_id}"),
        InlineKeyboardButton(get_text("INBOX_BTN_BAN", lang),
                             callback_data=f"a:inboxban:{user_id}"),
    ])
    rows.append([
        InlineKeyboardButton(get_text("BTN_BACK", lang), callback_data="a:inboxp:0"),
        InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x"),
    ])
    return InlineKeyboardMarkup(rows)


def gallery_keyboard(lang: str, user_id: int, rows: list) -> InlineKeyboardMarkup:
    """Media gallery: a button per file that re-sends it to the owner.

    Buttons are laid out three per row and labelled with the kind icon plus the
    row id, matching the ``/m<id>`` codes shown in the message body.
    """
    from bot.handlers.inbox import _KIND_ICON

    buttons: list = []
    line: list = []
    for row in rows:
        icon = _KIND_ICON.get(row.get("kind") or "document", "📎")
        line.append(InlineKeyboardButton(
            f"{icon} {row.get('id')}",
            callback_data=f"a:inboxm:{row.get('id')}"))
        if len(line) == 3:
            buttons.append(line)
            line = []
    if line:
        buttons.append(line)
    buttons.append([
        InlineKeyboardButton(get_text("BTN_BACK", lang),
                             callback_data=f"a:inboxt:{user_id}:0"),
        InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x"),
    ])
    return InlineKeyboardMarkup(buttons)


def starred_keyboard(lang: str, rows: list) -> InlineKeyboardMarkup:
    """Starred list: jump to each message's thread, or pull its media."""
    buttons: list = []
    line: list = []
    for row in rows:
        if row.get("file_id"):
            line.append(InlineKeyboardButton(
                f"📎 {row.get('id')}",
                callback_data=f"a:inboxm:{row.get('id')}"))
            if len(line) == 3:
                buttons.append(line)
                line = []
    if line:
        buttons.append(line)
    buttons.append([
        InlineKeyboardButton(get_text("BTN_BACK", lang), callback_data="a:inboxp:0"),
        InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x"),
    ])
    return InlineKeyboardMarkup(buttons)


async def group_welcome_keyboard(lang: str, bot_username: str) -> InlineKeyboardMarkup:
    """Shown once when the bot joins a group: add-elsewhere + help."""
    rows = [[
        await tbutton(get_text("GBTN_ADD", lang), role="menu", slot="invite",
                      url=f"https://t.me/{bot_username}?startgroup=true"),
    ], [
        await tbutton(get_text("GBTN_HELP", lang), role="nav", slot="help",
                      url=f"https://t.me/{bot_username}?start=help"),
    ]]
    return InlineKeyboardMarkup(rows)


async def group_menu_keyboard(lang: str, bot_username: str,
                              is_group_admin: bool = False) -> InlineKeyboardMarkup:
    """The /start menu for GROUPS — deliberately not the private menu.

    Personal screens (history, favourites, my stats, user settings, language)
    are per-user data and must never render inside a shared chat: whoever taps
    would see, or overwrite, the tapper's own data on a message everyone can
    press. So the group card links out to the private chat for those, and keeps
    only chat-scoped actions inline. The admin panel is omitted entirely.
    """
    dm = f"https://t.me/{bot_username}" if bot_username else ""
    rows = [
        [await tbutton(get_text("GBTN_HELP", lang), role="nav", slot="help",
                       callback_data="g:help")],
    ]
    if dm:
        rows.append([
            await tbutton(get_text("GBTN_OPEN_PM", lang), role="menu",
                          slot="brand", url=dm),
            await tbutton(get_text("GBTN_ADD", lang), role="menu", slot="invite",
                          url=f"{dm}?startgroup=true"),
        ])
    if is_group_admin:
        rows.append([await tbutton(get_text("GBTN_SETTINGS", lang), role="admin",
                                   slot="settings", callback_data="g:panel")])
    rows.append([await tbutton(get_text("BTN_CLOSE", lang), role="cancel",
                               slot="fail", callback_data="x")])
    return InlineKeyboardMarkup(rows)


async def group_settings_keyboard(lang: str, chat_id: int,
                                  conf: dict) -> InlineKeyboardMarkup:
    """Per-group settings. State is the button COLOUR, never a dot glyph."""
    auto = bool(conf.get("auto_download"))
    admins = bool(conf.get("admins_only"))
    clean = bool(conf.get("clean_mode"))
    quality = (conf.get("def_quality") or "").strip()
    glang = (conf.get("lang") or "").strip()

    rows = [
        [await tbutton(get_text("GBTN_MODE", lang),
                       role="confirm" if auto else "cancel", slot="link",
                       callback_data="g:mode")],
        [await tbutton(get_text("GBTN_ADMINS", lang),
                       role="confirm" if admins else "cancel", slot="admin",
                       callback_data="g:admins")],
        [await tbutton(get_text("GBTN_CLEAN", lang),
                       role="confirm" if clean else "cancel", slot="cache",
                       callback_data="g:clean")],
        [await tbutton(f"{get_text('GBTN_QUALITY', lang)}: "
                       f"{quality or get_text('GQUALITY_ASK', lang)}",
                       role="quality", slot="quality",
                       callback_data="g:quality")],
        [await tbutton(f"{get_text('GBTN_LANG', lang)}: "
                       f"{glang or get_text('GLANG_USER', lang)}",
                       role="menu", slot="lang", callback_data="g:lang")],
        [await tbutton(get_text("BTN_CLOSE", lang), role="cancel", slot="fail",
                       callback_data="x")],
    ]
    return InlineKeyboardMarkup(rows)


def admin_groups_keyboard(lang: str, rows: list, page: int,
                          total: int) -> InlineKeyboardMarkup:
    """Owner view: the groups the bot lives in."""
    buttons: list = []
    for row in rows[:10]:
        title = (row.get("title") or str(row.get("chat_id")))[:24]
        buttons.append([InlineKeyboardButton(
            f"{title} · {row.get('downloads', 0)}",
            callback_data=f"a:grp:{row.get('chat_id')}")])
    nav: list = []
    if page > 0:
        nav.append(InlineKeyboardButton(get_text("BTN_PREV", lang),
                                        callback_data=f"a:grpp:{page - 1}"))
    if (page + 1) * 10 < total:
        nav.append(InlineKeyboardButton(get_text("BTN_NEXT", lang),
                                        callback_data=f"a:grpp:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([
        InlineKeyboardButton(get_text("BTN_BACK", lang), callback_data="a:panel"),
        InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x"),
    ])
    return InlineKeyboardMarkup(buttons)


def broadcast_audience_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Choose who a pending broadcast goes to."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text("BTN_BC_ALL", lang), callback_data="a:bcall")],
        [InlineKeyboardButton(get_text("BTN_BC_ACTIVE", lang), callback_data="a:bcactive")],
        [InlineKeyboardButton(get_text("BTN_CONFIRM_NO", lang), callback_data="a:bcno")],
    ])


def restart_confirm_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Two-step confirmation for the restart button."""
    return InlineKeyboardMarkup([
        # BTN_CONFIRM_YES already carries an emoji; a second prefix would leave a
        # plain glyph in the label once the first becomes the premium icon.
        [InlineKeyboardButton(get_text("BTN_CONFIRM_YES", lang),
                              callback_data="a:restartgo")],
        [InlineKeyboardButton(get_text("BTN_BACK", lang), callback_data="a:panel")],
    ])


def admin_back_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(get_text("BTN_BACK", lang), callback_data="a:panel"),
        InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x"),
    ]])


def cache_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(get_text("BTN_ADMIN_CACHE_CLEAR", lang),
                              callback_data="a:cacheclear")],
        [
            InlineKeyboardButton(get_text("BTN_BACK", lang), callback_data="a:panel"),
            InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x"),
        ],
    ])


def limits_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Live-adjust the soft hourly cap; the only runtime-editable limit."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➖ 5", callback_data="a:lim:-5"),
            InlineKeyboardButton("➕ 5", callback_data="a:lim:+5"),
        ],
        [
            InlineKeyboardButton("➖ 20", callback_data="a:lim:-20"),
            InlineKeyboardButton("➕ 20", callback_data="a:lim:+20"),
        ],
        [
            InlineKeyboardButton(get_text("BTN_BACK", lang), callback_data="a:panel"),
            InlineKeyboardButton(get_text("BTN_CLOSE", lang), callback_data="x"),
        ],
    ])
