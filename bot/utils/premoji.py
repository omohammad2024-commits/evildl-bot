"""Premium (custom) emoji resolution for arbitrary unicode emoji.

The owner wants EVERY emoji in the bot rendered as a premium custom emoji, not
just the handful stored in theme slots. Doing that by hand would mean pasting
hundreds of ids, so this module builds the mapping automatically:

1. Read the emoji packs the owner points at (``getStickerSet``). Each pack entry
   carries the plain unicode ``emoji`` it stands for plus its
   ``custom_emoji_id`` — exactly the lookup table needed.
2. Cache ``emoji -> id`` in the ``settings`` table so later renders cost nothing
   and a Telegram outage cannot break message rendering.
3. ``premiumize(text)`` then rewrites any unicode emoji found in a message into
   ``<tg-emoji emoji-id="...">X</tg-emoji>``.

Emoji not present in the configured packs are left as plain unicode — there is no
id to use, and inventing one would make Telegram reject the whole message.
"""
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from bot.database import db

logger = logging.getLogger(__name__)

# Packs used to resolve emoji, in priority order. The first pack that provides a
# given emoji wins, so listing a favourite pack first shapes the whole look.
# RestrictedEmoji is the widest general-purpose custom_emoji pack (997 entries),
# so it backfills whatever the smaller themed packs do not cover — verified with
# getStickerSet, and every name here really is sticker_type=custom_emoji.
DEFAULT_PACKS = ("NewsEmoji", "AnimatedEmoji", "RestrictedEmoji", "CirclesEmoji",
                 "SignsEmoji", "ToolsEmoji", "OfficeEmoji", "BoxEmoji",
                 "ArrowsEmoji", "HeartsEmoji", "FlagsEmoji", "DuckEmoji",
                 "StatusEmoji", "TechEmoji", "MoneyEmoji", "EmojiPremium")

# Fallback for emoji no pack provides: swap the glyph for a near-equivalent that
# IS covered, so the owner never sees a plain unicode emoji next to premium ones.
# Only applied when ``strict`` mode is on (the default), and only as a last
# resort after every configured pack has been tried.
ALIASES: Dict[str, str] = {
    "☆": "⭐️", "♻️": "🔄", "🆔": "🆕", "🎛": "⚙️", "🎧": "🎵",
    "💠": "💎", "💾": "📥", "📄": "📝", "📅": "📆", "📋": "📝",
    "📛": "🎯", "📢": "📣", "📦": "📥", "📸": "🖼", "🔁": "🔄",
    "🔓": "🔒", "🔙": "⬅️", "🔢": "🧮", "🔧": "⚙️", "🔹": "🔵",
    "🕐": "⌛", "🕓": "⏰", "🗄": "📂", "🧹": "🗑",
    # Preset glyphs no pack covers, mapped to a covered look-alike. Every target
    # here is verified present in the packs (see the alias self-test).
    "⏱": "⌛", "🕰": "⏰", "⏬": "⬇️", "⏫": "📤", "📡": "🌐",
    "🟩": "✅", "🟥": "❌", "🔋": "⚡️", "🔆": "🌟", "🕹": "🎮",
    "📗": "📚", "📕": "📚", "📔": "📚", "📜": "📝", "🌀": "🔄",
    "🍃": "🌿", "🫧": "💧", "▪️": "🔵", "◇": "🔵", "▣": "🔵",
    "▤": "📝", "◷": "⌛", "✓": "✅", "✕": "❌", "↑": "📤",
    "↓": "⬇️", "•": "🔵", "◦": "🔵", "⌘": "⚙️", "?": "❓",
    "🖊": "✏️", "🧾": "📝", "🌙": "✨", "🧩": "🎯", "🖼": "🖼",
    "↩️": "⬅️", "⌨️": "⚙️", "📹": "🎬", "🎞": "🎬", "📍": "🌐",
    "🎚": "⚙️", "🎛": "⚙️", "⬆️": "⬇️",
}

_SETTING_MAP = "premium_emoji_map"
_SETTING_PACKS = "premium_emoji_packs"

_cache: Optional[Dict[str, str]] = None

# Emoji matcher: pictographic ranges plus optional variation selector / ZWJ
# sequences, longest-first so a multi-codepoint emoji is not split apart.
_EMOJI_RE = re.compile(
    "("
    "(?:[\U0001F1E6-\U0001F1FF]{2})"                  # flags
    "|(?:[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]"
    "(?:\uFE0F)?(?:\u200D[\U0001F300-\U0001FAFF\U00002600-\U000027BF]"
    "(?:\uFE0F)?)*)"
    "|(?:[\u2190-\u21FF\u2300-\u23FF\u24C2\u25A0-\u25FF\u2600-\u27BF"
    "\u2B00-\u2BFF\u3030\u303D\u3297\u3299]\uFE0F?)"
    ")"
)

# Never premiumize these: they are structural, and swapping them for animated
# emoji makes progress bars and dividers jitter.
_SKIP = set("▰▱█░▒▓◉◎▮▯─━═·⌁➖")


async def configured_packs() -> List[str]:
    raw = await db.get_setting(_SETTING_PACKS, "")
    if raw:
        names = [n.strip() for n in raw.split(",") if n.strip()]
        if names:
            return names
    return list(DEFAULT_PACKS)


async def set_packs(names: List[str]) -> None:
    await db.set_setting(_SETTING_PACKS, ",".join(n.strip() for n in names if n.strip()))
    await clear_cache()


async def clear_cache() -> None:
    global _cache
    _cache = None
    await db.set_setting(_SETTING_MAP, "")


async def load_map() -> Dict[str, str]:
    """The cached ``emoji -> custom_emoji_id`` table."""
    global _cache
    if _cache is not None:
        return _cache
    raw = await db.get_setting(_SETTING_MAP, "")
    data: Dict[str, str] = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data = {str(k): str(v) for k, v in parsed.items()}
        except (ValueError, TypeError):
            logger.warning("premium emoji map is corrupt; rebuilding on demand")
    _cache = data
    return data


async def rebuild(bot) -> Tuple[int, List[str]]:
    """Fetch the configured packs and rebuild the map. Returns (count, packs).

    The raw HTTP endpoint is used rather than ``bot.get_sticker_set`` because
    PTB 20.7's ``StickerSet`` model still requires ``is_animated``/``is_video``,
    fields Telegram removed — the typed call raises before any data is seen.
    """
    packs = await configured_packs()
    token = getattr(bot, "token", "") or ""
    mapping: Dict[str, str] = {}
    used: List[str] = []

    from bot.utils import net

    async with net.aclient() as client:
        for name in packs:
            try:
                resp = await client.get(
                    f"https://api.telegram.org/bot{token}/getStickerSet",
                    params={"name": name}, timeout=25)
                payload = resp.json()
            except Exception as exc:
                logger.warning("emoji pack %s unavailable: %s", name, exc)
                continue
            if not payload.get("ok"):
                logger.warning("emoji pack %s rejected: %s", name,
                               payload.get("description"))
                continue
            added = 0
            for sticker in payload.get("result", {}).get("stickers", []):
                emoji = sticker.get("emoji") or ""
                emoji_id = sticker.get("custom_emoji_id") or ""
                if not emoji or not emoji_id:
                    continue
                # First pack wins, and keep both the bare and VS16 forms so text
                # written either way still matches.
                for key in {emoji, emoji.replace("\uFE0F", ""), emoji + "\uFE0F"}:
                    if key and key not in mapping:
                        mapping[key] = emoji_id
                        added += 1
            if added:
                used.append(name)

    global _cache
    _cache = mapping
    await db.set_setting(_SETTING_MAP, json.dumps(mapping, ensure_ascii=False))
    logger.info("premium emoji map rebuilt: %d entries from %s", len(mapping), used)
    return len(mapping), used


def _lookup(mapping: Dict[str, str], glyph: str, strict: bool = True) -> Tuple[str, str]:
    """(custom_emoji_id, glyph_to_show) for one emoji.

    Tries the glyph as written, then without the VS16 selector, then — in strict
    mode — a near-equivalent alias so no plain emoji survives next to premium
    ones. Returns ('', glyph) when nothing resolves.
    """
    bare = glyph.replace("\uFE0F", "")
    emoji_id = mapping.get(glyph) or mapping.get(bare)
    if emoji_id:
        return emoji_id, glyph
    if strict:
        alias = ALIASES.get(glyph) or ALIASES.get(bare)
        if alias:
            alias_id = mapping.get(alias) or mapping.get(alias.replace("\uFE0F", ""))
            if alias_id:
                return alias_id, alias
    return "", glyph


async def premiumize(text: str, strict: bool = True) -> str:
    """Rewrite every resolvable unicode emoji in ``text`` as a premium emoji.

    HTML tags are preserved: the scan skips anything inside ``<...>`` and inside
    an existing ``<tg-emoji>`` element, so calling this twice is safe.
    """
    if not text:
        return text
    mapping = await load_map()
    if not mapping:
        return text

    out = []
    i = 0
    length = len(text)
    # Telegram forbids nested entities inside code/pre, so emoji there stay plain.
    literal_depth = 0
    while i < length:
        ch = text[i]
        # Copy HTML tags verbatim.
        if ch == "<":
            end = text.find(">", i)
            if end == -1:
                out.append(text[i:])
                break
            tag = text[i:end + 1]
            out.append(tag)
            i = end + 1
            low = tag.lower()
            if low.startswith("<code") or low.startswith("<pre"):
                literal_depth += 1
            elif low in ("</code>", "</pre>"):
                literal_depth = max(0, literal_depth - 1)
            # Skip the body of an existing tg-emoji so it is not double-wrapped.
            elif tag.startswith("<tg-emoji"):
                close = text.find("</tg-emoji>", i)
                if close != -1:
                    out.append(text[i:close + len("</tg-emoji>")])
                    i = close + len("</tg-emoji>")
            continue
        match = _EMOJI_RE.match(text, i)
        if match:
            glyph = match.group(0)
            if glyph in _SKIP or literal_depth:
                out.append(glyph)
            else:
                emoji_id, shown = _lookup(mapping, glyph, strict)
                if emoji_id:
                    out.append(f'<tg-emoji emoji-id="{emoji_id}">{shown}</tg-emoji>')
                else:
                    out.append(glyph)
            i = match.end()
            continue
        out.append(ch)
        i += 1
    return "".join(out)


async def resolve(glyph: str, strict: bool = True) -> str:
    """The custom_emoji_id for one glyph, or '' when nothing covers it."""
    mapping = await load_map()
    return _lookup(mapping, glyph, strict)[0]


async def stats() -> dict:
    mapping = await load_map()
    return {"entries": len(mapping), "packs": await configured_packs()}
