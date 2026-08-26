"""Telegram: download media from public channel/group posts via bot MTProto.

Reuses the same Telethon bot-token session the uploader already runs on
(``config.TELEGRAM_API_ID/HASH`` + ``BOT_TOKEN``), so no user login is needed.

What a bot token CAN do over MTProto (verified live on this host):
* resolve a public entity by @username or t.me link
* ``get_messages(peer, ids=[...])`` for specific post ids — the media downloads
  fine (photo/video/document up to 2GB)

What a bot token CANNOT do (Telegram restricts these to user sessions):
* ``iter_messages`` / history scraping (raises BotMethodInvalidError)
* stories (``stories.GetPeerStories`` → BotMethodInvalidError)
* private chats/channels the bot is not a member of

Link forms handled:
  https://t.me/<channel>/<id>            public post
  https://t.me/<channel>/<topic>/<id>    forum-topic post (last number is msg id)
  https://t.me/s/<channel>/<id>          web-preview form
  https://t.me/c/<internal>/<id>         private — only works if the bot is in it
"""
import logging
import os
import re
from typing import List, Optional, Tuple

from bot import config
from bot.services.base import DownloadResult, MediaItem, BaseDownloader

logger = logging.getLogger(__name__)


class TelegramDownloader(BaseDownloader):
    name = "telegram"
    patterns = [
        r"(?:^|\.|//)t\.me/",
        r"(?:^|\.|//)telegram\.me/",
    ]

    # ── link parsing ──────────────────────────────────────────────────
    @staticmethod
    def _is_story(url: str) -> Optional[Tuple[str, int]]:
        """Return (username, story_id) for a story link, else None.

        Story links are t.me/<channel>/s/<story_id>. The `s` sits in the SECOND
        segment — distinct from the web-preview form t.me/s/<channel> where `s`
        is FIRST.
        """
        m = re.search(r"t(?:elegram)?\.me/(.+)$", url, re.I)
        if not m:
            return None
        parts = [p for p in m.group(1).split("?")[0].split("/") if p]
        if len(parts) >= 3 and parts[1] == "s" and parts[2].isdigit():
            return parts[0], int(parts[2])
        return None

    @staticmethod
    def _parse(url: str) -> Tuple[str, int, bool]:
        """Return (peer, message_id, is_private).

        ``peer`` is a @username for public links or an int channel id (as a
        string) for /c/ links. Raises ValueError when no message id is present
        (a bare channel link is not a downloadable post).
        """
        m = re.search(r"t(?:elegram)?\.me/(.+)$", url, re.I)
        if not m:
            raise ValueError("not a Telegram link")
        parts = [p for p in m.group(1).split("?")[0].split("/") if p]

        # Channel STORY: t.me/<channel>/s/<story_id> — the `s` is the SECOND
        # segment (a channel web-preview is t.me/s/<channel>, `s` FIRST). Stories
        # need a USER session (bot tokens are API-restricted); download() routes
        # these to the user client and raises a clear error if none is set up.
        if len(parts) >= 3 and parts[1] == "s" and parts[2].isdigit():
            return parts[0], int(parts[2]), False

        # Private channel: t.me/c/<internal_id>/<msg_id>
        if parts and parts[0] == "c":
            if len(parts) < 3:
                raise ValueError("private Telegram link needs a message id")
            internal = parts[1]
            msg_id = int(parts[-1])
            # MTProto wants -100<internal> for channel peers.
            return f"-100{internal}", msg_id, True

        # Web-preview form: t.me/s/<channel>/<id>
        if parts and parts[0] == "s":
            parts = parts[1:]

        if len(parts) < 2:
            raise ValueError("Telegram link has no message id — send a link to a specific post")

        channel = parts[0]
        # The message id is the final numeric segment (handles forum topics:
        # t.me/<chan>/<topic_id>/<msg_id> where the last number is the message).
        nums = [p for p in parts[1:] if p.isdigit()]
        if not nums:
            raise ValueError("Telegram link has no message id")
        return channel, int(nums[-1]), False

    # ── contract ──────────────────────────────────────────────────────
    async def get_info(self, url: str) -> DownloadResult:
        peer, msg_id, is_private = self._parse(url)
        from bot.utils import sender

        client = await sender._get_mtproto()
        if client is None:
            raise ValueError("Telegram download backend is unavailable")

        msg = await self._one_message(client, peer, msg_id, is_private)
        text = msg.message or ""
        has_media = bool(msg.media and (msg.photo or msg.video or msg.document))
        return DownloadResult(
            title=(text[:80] or f"Telegram post {msg_id}"),
            text=text,
            uploader=self._peer_name(peer),
            platform=self.name,
            extra={"has_media": has_media, "message_id": msg_id},
        )

    async def download(self, url: str, quality: str = "best") -> DownloadResult:
        story = self._is_story(url)
        if story:
            return await self._download_story(*story, url)

        peer, msg_id, is_private = self._parse(url)
        from bot.utils import sender

        client = await sender._get_mtproto()
        if client is None:
            raise ValueError("Telegram download backend is unavailable")

        msg = await self._one_message(client, peer, msg_id, is_private)
        text = msg.message or ""

        if not (msg.media and (msg.photo or msg.video or msg.document)):
            # A text-only post: hand the text back so the caller can post it.
            if text:
                return DownloadResult(
                    title=text[:80], text=text, uploader=self._peer_name(peer),
                    platform=self.name, extra={"text_only": True},
                )
            raise ValueError("this Telegram post has no downloadable media")

        stem = self.temp_path("bin", prefix="tg").rsplit(".", 1)[0]
        path = await msg.download_media(file=stem)
        if not path or not os.path.exists(path):
            raise ValueError("Telegram media download produced no file")

        kind = self._kind_for(msg, path)
        return DownloadResult(
            items=[MediaItem(path=path, url=url, kind=kind,
                             filename=os.path.basename(path))],
            title=(text[:80] or f"Telegram post {msg_id}"),
            text=text,
            uploader=self._peer_name(peer),
            platform=self.name,
            extra={"message_id": msg_id},
        )

    # ── helpers ───────────────────────────────────────────────────────
    async def _download_story(self, username: str, story_id: int, url: str) -> DownloadResult:
        """Download a channel/user story via the optional USER session.

        Stories are API-restricted for bot tokens (verified: BotMethodInvalidError),
        so this needs a logged-in user account managed by bot.utils.userclient.
        If none is set up, raise a clear marker the handler turns into a
        "run /tglogin" message.
        """
        from bot.utils import userclient
        from telethon.tl import functions

        client = await userclient.get_user()
        if client is None:
            raise ValueError("__STORY_NEEDS_LOGIN__")

        try:
            peer = await client.get_entity(username)
            res = await client(functions.stories.GetStoriesByIDRequest(
                peer=peer, id=[story_id]))
        except Exception as exc:
            low = str(exc).lower()
            if "not found" in low or "empty" in low or "invalid" in low:
                raise ValueError(
                    "that story is gone — stories expire after 24h (or the "
                    "channel has no active story with that id)"
                ) from exc
            raise

        stories = getattr(res, "stories", None) or []
        if not stories:
            raise ValueError(
                "that story is gone — stories expire after 24h (or the "
                "channel has no active story with that id)"
            )
        story = stories[0]
        media = getattr(story, "media", None)
        if media is None:
            raise ValueError("this story has no downloadable media")

        stem = self.temp_path("bin", prefix="tgstory").rsplit(".", 1)[0]
        # Telethon downloads a MessageMedia (photo or document) directly.
        path = await client.download_media(media, file=stem)
        if not path or not os.path.exists(path):
            raise ValueError("Telegram story download produced no file")

        ext = os.path.splitext(path)[1].lower()
        if ext in (".jpg", ".jpeg", ".png", ".webp"):
            kind = "photo"
        elif ext in (".mp4", ".mov", ".webm"):
            kind = "video"
        elif ext in (".gif",):
            kind = "animation"
        else:
            kind = "document"

        caption = getattr(story, "caption", "") or ""
        return DownloadResult(
            items=[MediaItem(path=path, url=url, kind=kind,
                             filename=os.path.basename(path))],
            title=f"@{username} story {story_id}",
            text=caption,
            uploader=username,
            platform=self.name,
            extra={"story_id": story_id},
        )

    @staticmethod
    async def _one_message(client, peer, msg_id: int, is_private: bool):
        """Fetch a single post by id. Bot tokens can do this; iter_messages they
        cannot, so never fall back to history scraping."""
        target = int(peer) if is_private else peer
        try:
            msgs = await client.get_messages(target, ids=[msg_id])
        except Exception as exc:
            low = str(exc).lower()
            if "private" in low or "invalid" in low or "cannot find" in low:
                raise ValueError(
                    "can't open this Telegram post — it's private or the bot "
                    "isn't a member of that channel"
                ) from exc
            raise
        msg = msgs[0] if msgs else None
        if msg is None:
            raise ValueError("that Telegram post doesn't exist or was deleted")
        return msg

    @staticmethod
    def _kind_for(msg, path: str) -> str:
        if msg.photo:
            return "photo"
        if msg.video or (msg.document and (msg.file and (msg.file.mime_type or "").startswith("video/"))):
            ext = os.path.splitext(path)[1].lower()
            if ext in (".gif",):
                return "animation"
            return "video"
        if msg.document and msg.file and (msg.file.mime_type or "").startswith("audio/"):
            return "audio"
        return "document"

    @staticmethod
    def _peer_name(peer) -> str:
        p = str(peer)
        return "" if p.startswith("-100") else p
