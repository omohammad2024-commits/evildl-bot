"""Owner-editable UI theme: emoji slots, premium (custom) emoji, button style.

Everything lives in one JSON blob in the ``settings`` table (key ``theme``), so
adding a slot never needs a migration. The blob is cached in memory and
invalidated on every write, so an edit takes effect on the next message with no
restart.

Slot values accept two shapes:

    "🎬"                    plain emoji
    "🎬:5368324170671202286"  premium emoji: fallback + custom_emoji_id

For message TEXT the premium form renders as
``<tg-emoji emoji-id="...">🎬</tg-emoji>`` which Telegram shows as the animated
custom emoji. For BUTTON labels Telegram accepts no HTML at all, so the plain
fallback is used there — that is a platform limit, not a bug.

Premium emoji only render for real when the bot has a Fragment username linked
(otherwise clients fall back to the plain glyph). ``premium`` gates the whole
feature so the owner can turn it off if their client shows the fallback.
"""
import json
import logging
import re
from typing import Dict, Optional

from bot.database import db

logger = logging.getLogger(__name__)

# Slot name -> (default value, human label key). The label keys live in the
# locales so the editor screen is translated.
SLOTS: Dict[str, str] = {
    "brand": "🎬",        # welcome/brand header
    "download": "⬇️",     # downloading state
    "upload": "📤",       # uploading state
    "queue": "⏳",        # queued state
    "search": "🔍",       # checking link
    "ok": "✅",           # success
    "fail": "❌",         # failure
    "cache": "⚡️",        # served from cache
    "star": "⭐️",         # favorites
    "stats": "📊",        # stats
    "settings": "⚙️",     # settings
    "history": "🕓",      # history
    "invite": "🎁",       # invite
    "help": "📖",         # help
    "lang": "🌐",         # language
    "admin": "🛡",        # admin panel
    "link": "🔗",         # direct link
    "audio": "🎵",        # audio/mp3
    "sub": "📝",          # subtitle
    "bar_full": "▰",      # progress bar filled cell
    "bar_empty": "▱",     # progress bar empty cell
    "sep": "➖",          # separator character
    # Admin panel / stats card icons.
    "users": "👥",
    "new": "🆕",
    "hot": "🔥",
    "date": "📅",
    "disk": "💾",
    "clock": "⏱",
    "chart": "📈",
    "warn": "⚠️",
    "quality": "🎚",     # quality / resolution picker
}

# Non-emoji knobs.
DEFAULTS = {
    "premium": False,      # allow <tg-emoji> premium rendering in text
    "blue_buttons": False,  # legacy: url deep links to force blue buttons
    "styles": True,        # send native Bot API 10.2 button styles
    "btn_icons": True,     # put premium emoji ON buttons (icon_custom_emoji_id)
    "sep_len": 9,          # how many separator chars in a divider line
}

# Bot API 10.2 button styles. "" means "let the client pick" (the old grey).
STYLE_VALUES = ("", "primary", "success", "danger")

# Button role -> default style. Roles keep the mapping semantic, so changing
# "menu" recolours every menu button at once instead of hunting call sites.
STYLE_ROLES: Dict[str, str] = {
    "menu": "primary",      # main-menu entries
    "admin": "danger",      # admin panel entry
    "quality": "primary",   # quality picker
    "confirm": "success",   # yes / go ahead
    "cancel": "danger",     # no / close
    "action": "success",    # post-download actions (mp3, subtitle, favorite)
    "nav": "",              # back / refresh / paging
}

# Ready-made looks. Each preset only lists the slots it changes; everything
# else falls back to the built-in default, so a preset stays small and readable.
PRESETS: Dict[str, Dict[str, object]] = {
    "classic": {
        "_label": "🎬 کلاسیک",
        "slots": {},
        "sep_len": 9,
    },
    "minimal": {
        "_label": "▫️ مینیمال",
        "slots": {
            "brand": "▪️", "download": "↓", "upload": "↑", "queue": "•",
            "search": "◦", "ok": "✓", "fail": "✕", "cache": "⚡",
            "star": "☆", "stats": "▤", "settings": "⚙", "history": "◷",
            "invite": "◇", "help": "?", "lang": "⌘", "admin": "▣",
            "bar_full": "▓", "bar_empty": "░", "sep": "─",
        },
        "sep_len": 18,
    },
    "neon": {
        "_label": "🌈 نئون",
        "slots": {
            "brand": "🎆", "download": "🔽", "upload": "🔼", "queue": "🕐",
            "search": "🔎", "ok": "💚", "fail": "💔", "cache": "💫",
            "star": "💖", "stats": "📈", "settings": "🎛", "history": "🌀",
            "invite": "🎉", "help": "💡", "lang": "🌍", "admin": "🔮",
            "bar_full": "█", "bar_empty": "▒", "sep": "═",
        },
        "sep_len": 14,
    },
    "gold": {
        "_label": "👑 طلایی",
        "slots": {
            "brand": "👑", "download": "📥", "upload": "📦", "queue": "⌛️",
            "search": "🔍", "ok": "🏆", "fail": "⚠️", "cache": "✨",
            "star": "🌟", "stats": "📊", "settings": "🗝", "history": "📜",
            "invite": "🎁", "help": "📕", "lang": "🧭", "admin": "🛡",
            "bar_full": "▮", "bar_empty": "▯", "sep": "━",
        },
        "sep_len": 13,
    },
    "cyber": {
        "_label": "🤖 سایبری",
        "slots": {
            "brand": "🤖", "download": "⏬", "upload": "⏫", "queue": "⏸",
            "search": "📡", "ok": "🟩", "fail": "🟥", "cache": "🔋",
            "star": "🔆", "stats": "🧮", "settings": "🔧", "history": "💾",
            "invite": "🔗", "help": "📗", "lang": "🛰", "admin": "🕹",
            "bar_full": "▰", "bar_empty": "▱", "sep": "⌁",
        },
        "sep_len": 16,
    },
    "pastel": {
        "_label": "🌸 پاستل",
        "slots": {
            "brand": "🌸", "download": "🫧", "upload": "🎀", "queue": "🕰",
            "search": "🔍", "ok": "🌷", "fail": "🥀", "cache": "🍃",
            "star": "🤍", "stats": "🧁", "settings": "🪄", "history": "🕊",
            "invite": "💌", "help": "📔", "lang": "🌤", "admin": "🪷",
            "bar_full": "◉", "bar_empty": "◎", "sep": "·",
        },
        "sep_len": 20,
    },
}

_cache: Optional[dict] = None

_PREMIUM_RE = re.compile(r"^(.*?):(\d{5,25})$")


def _blank() -> dict:
    data = dict(DEFAULTS)
    data["slots"] = {}
    return data


async def load(force: bool = False) -> dict:
    """Return the current theme dict (cached)."""
    global _cache
    if _cache is not None and not force:
        return _cache
    raw = await db.get_setting("theme", "")
    data = _blank()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data.update({k: v for k, v in parsed.items() if k != "slots"})
                slots = parsed.get("slots")
                if isinstance(slots, dict):
                    data["slots"] = {k: str(v) for k, v in slots.items()
                                     if k in SLOTS and str(v).strip()}
        except (ValueError, TypeError):
            logger.warning("theme JSON is corrupt; using defaults")
    _cache = data
    return data


async def save(data: dict) -> None:
    global _cache
    await db.set_setting("theme", json.dumps(data, ensure_ascii=False))
    _cache = data


def invalidate() -> None:
    global _cache
    _cache = None


async def set_slot(slot: str, value: str) -> bool:
    """Set one emoji slot. Empty value resets it to the default."""
    if slot not in SLOTS:
        return False
    data = await load(force=True)
    value = (value or "").strip()
    if value:
        data["slots"][slot] = value[:64]
    else:
        data["slots"].pop(slot, None)
    await save(data)
    return True


async def set_flag(key: str, value) -> bool:
    if key not in DEFAULTS:
        return False
    data = await load(force=True)
    data[key] = value
    await save(data)
    return True


async def reset() -> None:
    await save(_blank())


async def apply_preset(name: str) -> bool:
    """Load a ready-made look, keeping the owner's feature flags intact."""
    preset = PRESETS.get(name)
    if not preset:
        return False
    data = await load(force=True)
    fresh = _blank()
    # Feature flags are the owner's choice, not part of the look.
    fresh["premium"] = data.get("premium", DEFAULTS["premium"])
    fresh["blue_buttons"] = data.get("blue_buttons", DEFAULTS["blue_buttons"])
    fresh["slots"] = dict(preset.get("slots") or {})
    fresh["sep_len"] = preset.get("sep_len", DEFAULTS["sep_len"])
    fresh["preset"] = name
    await save(fresh)
    return True


async def current_preset() -> str:
    data = await load()
    return str(data.get("preset") or "")


async def export_json() -> str:
    """The whole theme as pretty JSON, for backup or sharing."""
    data = await load(force=True)
    return json.dumps(data, ensure_ascii=False, indent=2)


async def import_json(raw: str) -> bool:
    """Load a theme from pasted JSON. Unknown slots and keys are dropped."""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if not isinstance(parsed, dict):
        return False
    fresh = _blank()
    for key in DEFAULTS:
        if key in parsed:
            fresh[key] = parsed[key]
    slots = parsed.get("slots")
    if isinstance(slots, dict):
        fresh["slots"] = {k: str(v)[:64] for k, v in slots.items()
                          if k in SLOTS and str(v).strip()}
    if parsed.get("preset"):
        fresh["preset"] = str(parsed["preset"])[:32]
    await save(fresh)
    return True


def _parse(value: str):
    """Split a slot value into (fallback_emoji, custom_emoji_id_or_empty)."""
    m = _PREMIUM_RE.match(value or "")
    if m:
        return m.group(1) or "•", m.group(2)
    return value, ""


async def raw(slot: str) -> str:
    """The stored value for a slot (may include the :id premium suffix)."""
    data = await load()
    return data["slots"].get(slot, SLOTS.get(slot, ""))


async def _auto_id(glyph: str) -> str:
    """Resolve a plain glyph to a premium id via the emoji-pack map.

    This is what makes every preset premium without hardcoding ids: a slot may
    hold just "👑" and still render as premium, because the pack map supplies the
    matching ``custom_emoji_id``.
    """
    try:
        from bot.utils import premoji

        return await premoji.resolve(glyph)
    except Exception:  # pragma: no cover - never break rendering over this
        return ""


async def icon(slot: str) -> str:
    """Slot rendered for message TEXT — premium markup when enabled."""
    data = await load()
    value = data["slots"].get(slot, SLOTS.get(slot, ""))
    fallback, emoji_id = _parse(value)
    if not data.get("premium"):
        return fallback
    if not emoji_id:
        emoji_id = await _auto_id(fallback)
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback


async def plain(slot: str) -> str:
    """Slot rendered for BUTTON labels — always the plain fallback glyph."""
    data = await load()
    value = data["slots"].get(slot, SLOTS.get(slot, ""))
    return _parse(value)[0]


async def divider() -> str:
    """A separator line built from the sep slot, e.g. ➖➖➖➖➖➖➖➖➖."""
    data = await load()
    ch = _parse(data["slots"].get("sep", SLOTS["sep"]))[0] or "➖"
    try:
        n = int(data.get("sep_len", DEFAULTS["sep_len"]))
    except (TypeError, ValueError):
        n = DEFAULTS["sep_len"]
    n = max(1, min(n, 30))
    return ch * n


async def bar(filled: int, total: int = 10) -> str:
    """A progress bar using the themed cells, e.g. ▰▰▰▰▱▱▱▱▱▱."""
    data = await load()
    full = _parse(data["slots"].get("bar_full", SLOTS["bar_full"]))[0] or "▰"
    empty = _parse(data["slots"].get("bar_empty", SLOTS["bar_empty"]))[0] or "▱"
    filled = max(0, min(filled, total))
    return full * filled + empty * (total - filled)


async def flag(key: str) -> bool:
    data = await load()
    return bool(data.get(key, DEFAULTS.get(key)))


async def role_style(role: str) -> str:
    """The Bot API style ('', 'primary', 'success', 'danger') for a button role."""
    data = await load()
    if not data.get("styles", DEFAULTS["styles"]):
        return ""
    overrides = data.get("role_styles") or {}
    value = overrides.get(role, STYLE_ROLES.get(role, ""))
    return value if value in STYLE_VALUES else ""


async def set_role_style(role: str, style: str) -> bool:
    if role not in STYLE_ROLES or style not in STYLE_VALUES:
        return False
    data = await load(force=True)
    overrides = dict(data.get("role_styles") or {})
    overrides[role] = style
    data["role_styles"] = overrides
    await save(data)
    return True


async def cycle_role_style(role: str) -> str:
    """Advance a role to the next style; returns the new value."""
    current = await role_style(role)
    order = list(STYLE_VALUES)
    nxt = order[(order.index(current) + 1) % len(order)] if current in order else order[1]
    await set_role_style(role, nxt)
    return nxt


async def button_extras(role: str = "", slot: str = "") -> dict:
    """The api_kwargs for one button: native style + premium icon.

    PTB 20.7 predates Bot API 10.2 and silently drops ``style`` /
    ``icon_custom_emoji_id`` if they are passed as normal arguments, so they must
    ride along in ``api_kwargs`` — verified to survive into to_dict().
    """
    extras = {}
    style = await role_style(role) if role else ""
    if style:
        extras["style"] = style
    if slot and await flag("btn_icons"):
        value = await raw(slot)
        fallback, emoji_id = _parse(value)
        # Fall back to the pack map so a preset slot holding a plain glyph still
        # gets a premium icon on the button.
        if not emoji_id:
            emoji_id = await _auto_id(fallback)
        if emoji_id:
            extras["icon_custom_emoji_id"] = emoji_id
    return extras


async def summary_lines() -> list:
    """One display line per slot for the editor screen."""
    data = await load()
    out = []
    for slot, default in SLOTS.items():
        value = data["slots"].get(slot, default)
        fallback, emoji_id = _parse(value)
        mark = " 💎" if emoji_id else ""
        custom = "" if slot not in data["slots"] else " ✏️"
        out.append(f"{fallback}{mark}{custom} <code>{slot}</code>")
    return out
