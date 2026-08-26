"""Translation lookup with graceful fallbacks."""
import logging
from typing import Any

from bot import config
from bot.locales import en as _en
from bot.locales import fa as _fa

logger = logging.getLogger(__name__)

LOCALES = {"fa": _fa, "en": _en}


def get_text(key: str, lang: str = "", /, **kwargs: Any) -> str:
    """Return a localised string, formatted with ``kwargs``.

    Falls back to the default language, then to the key itself, so a missing
    translation degrades instead of raising in a handler.

    ``key`` and ``lang`` are positional-only on purpose. Twice now a template
    placeholder named ``{lang}`` collided with this function's own ``lang``
    parameter and raised "got multiple values for argument 'lang'" at runtime,
    inside a handler, where it surfaced to the user as a generic error. With
    ``/`` the placeholder name is free to be anything, including ``lang``.
    """
    lang = (lang or config.DEFAULT_LANG).lower()
    module = LOCALES.get(lang) or LOCALES[config.DEFAULT_LANG]
    text = getattr(module, key, None)
    if text is None:
        text = getattr(LOCALES[config.DEFAULT_LANG], key, None)
    if text is None:
        logger.warning("Missing translation key: %s", key)
        return key
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError) as exc:
        logger.warning("Bad format args for %s: %s", key, exc)
        return text


def available_languages() -> list:
    return list(LOCALES.keys())


async def themed(key: str, lang: str = "", /, **fmt: Any) -> str:
    """``get_text`` plus live theme substitution.

    Locale strings may reference theme slots as ``{i_<slot>}`` (icon — premium
    markup allowed), ``{div}`` (separator line) and ``{bar_<n>}`` (progress bar
    with n cells filled). Theme tokens are resolved BEFORE ``str.format`` runs,
    so a themed string can still carry ordinary placeholders.
    """
    from bot.utils import theme

    lang = (lang or config.DEFAULT_LANG).lower()
    module = LOCALES.get(lang) or LOCALES[config.DEFAULT_LANG]
    text = getattr(module, key, None)
    if text is None:
        text = getattr(LOCALES[config.DEFAULT_LANG], key, None)
    if text is None:
        logger.warning("Missing translation key: %s", key)
        return key

    if "{i_" in text:
        for slot in theme.SLOTS:
            token = "{i_%s}" % slot
            if token in text:
                text = text.replace(token, await theme.icon(slot))
    if "{div}" in text:
        text = text.replace("{div}", await theme.divider())
    if "{bar_" in text:
        for n in range(10, -1, -1):
            token = "{bar_%d}" % n
            if token in text:
                text = text.replace(token, await theme.bar(n))

    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except (KeyError, IndexError, ValueError) as exc:
        logger.warning("Bad format args for %s: %s", key, exc)
        return text


def missing_keys() -> dict:
    """Diagnostic helper: which keys exist in one locale but not the other."""
    fa_keys = {k for k in dir(_fa) if k.isupper()}
    en_keys = {k for k in dir(_en) if k.isupper()}
    return {"missing_in_en": sorted(fa_keys - en_keys),
            "missing_in_fa": sorted(en_keys - fa_keys)}
