"""Global outgoing-keyboard theming hook.

Rather than teach ~40 call sites about the theme, patch the send/edit methods
once: every ``reply_markup`` that goes out is re-emitted through ``themed_kb``,
which attaches the Bot API 10.2 ``style`` colour and ``icon_custom_emoji_id``
premium icon that match the owner's theme.

Patching happens on the CLASS, not the instance: PTB's ``Bot``/``ExtBot`` inherit
``TelegramObject.__setattr__``, which raises
``AttributeError: Attribute 'send_message' of class 'ExtBot' can't be set!`` for
instance attributes. Class-level assignment sidesteps that guard, and the wrapper
takes ``self`` explicitly so bound calls still work.
"""
import functools
import inspect
import logging

logger = logging.getLogger(__name__)

# Bot methods that can carry an inline keyboard.
_METHODS = (
    "send_message",
    "edit_message_text",
    "edit_message_reply_markup",
    "edit_message_caption",
    "send_photo",
    "send_video",
    "send_audio",
    "send_document",
    "send_animation",
    "send_voice",
    "copy_message",
)

_FLAG = "_theme_hook_installed"


def _wrap(original):
    # Which positional parameters this method accepts, so positionally-passed
    # values can be normalised into kwargs. This matters because PTB's
    # convenience wrappers pass the payload POSITIONALLY:
    # ``Message.reply_text`` calls ``bot.send_message(chat_id, *args)``, so the
    # text never appears in kwargs and a kwargs-only hook silently skips it —
    # which is why panels (built with explicit kwargs) were premium while plain
    # replies were not.
    try:
        params = [p.name for p in inspect.signature(original).parameters.values()]
        if params and params[0] == "self":
            params = params[1:]
    except (TypeError, ValueError):  # pragma: no cover - builtins
        params = []

    @functools.wraps(original)
    async def call(self, *args, **kwargs):
        # Normalise positional arguments into kwargs so the hook can see them.
        if args and params:
            movable = min(len(args), len(params))
            for idx in range(movable):
                name = params[idx]
                if name not in kwargs:
                    kwargs[name] = args[idx]
            args = args[movable:]

        markup = kwargs.get("reply_markup")
        if markup is not None:
            try:
                from bot.keyboards.inline import themed_kb

                kwargs["reply_markup"] = await themed_kb(markup)
            except Exception as exc:  # never block a send over theming
                logger.debug("theme hook skipped: %s", exc)

        # Premiumize the message body too: every plain emoji becomes a premium
        # one. Only for HTML payloads, since <tg-emoji> is HTML-only.
        try:
            await _premiumize_kwargs(kwargs)
        except Exception as exc:
            logger.debug("premium emoji skipped: %s", exc)

        result = await original(self, *args, **kwargs)

        # Log the bot's own half of the conversation so the owner inbox shows a
        # real two-sided thread. After the call, because the returned Message is
        # the only reliable source of the file_id Telegram assigned.
        try:
            from bot.utils.outlog import log_send

            await log_send(original.__name__, kwargs, result)
        except Exception as exc:  # never block a send over logging
            logger.debug("outgoing log hook skipped: %s", exc)

        return result

    return call


def _escape_html(text: str) -> str:
    """Make plain text safe to send as HTML."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


async def _premiumize_kwargs(kwargs: dict) -> None:
    """Rewrite ``text``/``caption`` in place so their emoji render premium.

    ``<tg-emoji>`` only works in HTML mode, but most call sites send plain text
    with no ``parse_mode`` at all (over 100 of them here). Those messages could
    never show a premium emoji, which is why replies like "no link found" or the
    tag-to-act hints still looked plain while the panels were fully premium.

    So when no parse_mode is set we *promote* the message to HTML: escape the
    text first (making every ``<``/``&`` literal, so nothing the user typed can
    be interpreted as markup), then insert the ``<tg-emoji>`` tags, then declare
    HTML. Promotion only happens when the text actually gained a premium tag —
    otherwise the payload is left exactly as it was.
    """
    from bot.utils import premoji, theme

    if not await theme.flag("premium"):
        return
    # Entities and parse_mode are mutually exclusive; never touch entity sends.
    if kwargs.get("entities") or kwargs.get("caption_entities"):
        return

    parse_mode = kwargs.get("parse_mode")
    mode = str(getattr(parse_mode, "value", parse_mode) or "").lower()

    if mode == "html":
        for field in ("text", "caption"):
            value = kwargs.get(field)
            if isinstance(value, str) and value:
                kwargs[field] = await premoji.premiumize(value)
        return

    # Markdown payloads are left alone: escaping rules differ and mixing
    # <tg-emoji> into Markdown is not supported by Telegram.
    if mode:
        return

    # No parse_mode: promote to HTML only if premiumizing actually changed it.
    promoted = False
    updated = {}
    for field in ("text", "caption"):
        value = kwargs.get(field)
        if not isinstance(value, str) or not value:
            continue
        candidate = await premoji.premiumize(_escape_html(value))
        if "<tg-emoji" in candidate:
            updated[field] = candidate
            promoted = True
    if promoted:
        kwargs.update(updated)
        kwargs["parse_mode"] = "HTML"


def install(bot=None) -> int:
    """Patch the bot classes so outgoing keyboards get themed. Idempotent."""
    try:
        from telegram import Bot
        from telegram.ext import ExtBot

        targets = [ExtBot, Bot]
    except Exception:  # pragma: no cover - telegram always present in prod
        return 0

    wrapped = 0
    for cls in targets:
        if getattr(cls, _FLAG, False):
            continue
        for name in _METHODS:
            original = cls.__dict__.get(name)
            if original is None:
                continue
            setattr(cls, name, _wrap(original))
            wrapped += 1
        setattr(cls, _FLAG, True)

    if wrapped:
        logger.info("Theme hook installed on %d bot methods", wrapped)
    return wrapped
