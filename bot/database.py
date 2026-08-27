"""Async SQLite layer. Also holds the file_id cache that makes repeat
downloads instant, and the counters the admin panel reads.

Storage discipline (this bot runs on a ~434MB partition):
* download URLs are hashed, not stored in full, beyond a short prefix
* the ``downloads`` table is a rolling window — rows older than
  ``config.DB_RETENTION_DAYS`` are pruned, and lifetime totals are kept as
  cheap counters in ``settings`` so statistics survive the pruning
* the ``file_cache`` table is capped by row count, evicting least-used entries
* ``VACUUM`` runs after a prune so the file actually shrinks
"""
import logging
import time
from typing import Any, Dict, List, Optional

import aiosqlite

from bot import config

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY,
    username        TEXT,
    first_name      TEXT,
    language        TEXT DEFAULT 'fa',
    join_date       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_banned       INTEGER DEFAULT 0,
    total_downloads INTEGER DEFAULT 0,
    ban_reason      TEXT DEFAULT NULL
);
CREATE TABLE IF NOT EXISTS downloads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    platform    TEXT,
    url         TEXT,
    file_size   INTEGER DEFAULT 0,
    timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    success     INTEGER DEFAULT 1,
    error       TEXT DEFAULT NULL,
    duration_ms INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS banned_users (
    user_id   INTEGER PRIMARY KEY,
    ban_date  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason    TEXT DEFAULT NULL,
    banned_by INTEGER
);
CREATE TABLE IF NOT EXISTS broadcasts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id     INTEGER,
    message_text TEXT,
    sent_count   INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    timestamp    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS file_cache (
    cache_key  TEXT PRIMARY KEY,
    file_id    TEXT NOT NULL,
    file_type  TEXT NOT NULL,
    platform   TEXT,
    title      TEXT,
    file_size  INTEGER DEFAULT 0,
    duration   INTEGER DEFAULT 0,
    width      INTEGER DEFAULT 0,
    height     INTEGER DEFAULT 0,
    hits       INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dl_user ON downloads(user_id);
CREATE INDEX IF NOT EXISTS idx_dl_time ON downloads(timestamp);
CREATE INDEX IF NOT EXISTS idx_dl_platform ON downloads(platform);
-- Hot path: per-user success stats + history filter on (user_id, success).
CREATE INDEX IF NOT EXISTS idx_dl_user_success ON downloads(user_id, success);
-- file_cache eviction sorts by (hits, created_at); index it so the LRU sweep
-- doesn't scan the whole table on a tight-disk host.
CREATE INDEX IF NOT EXISTS idx_cache_evict ON file_cache(hits, created_at);
CREATE TABLE IF NOT EXISTS user_prefs (
    user_id       INTEGER PRIMARY KEY,
    def_quality   TEXT DEFAULT 'ask',
    def_format    TEXT DEFAULT 'video',
    notify        INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS favorites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    url        TEXT,
    platform   TEXT,
    title      TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, url)
);
CREATE TABLE IF NOT EXISTS referrals (
    referred_id INTEGER PRIMARY KEY,
    referrer_id INTEGER,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Live feed of what users send the bot, so the owner can read their private
-- chats with it like a normal account. Text is capped and old rows are pruned.
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    chat_id    INTEGER,
    chat_type  TEXT,
    msg_id     INTEGER,
    direction  TEXT DEFAULT 'in',
    kind       TEXT,
    text       TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fav_user ON favorites(user_id);
CREATE INDEX IF NOT EXISTS idx_ref_referrer ON referrals(referrer_id);
CREATE INDEX IF NOT EXISTS idx_msg_user ON messages(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_msg_time ON messages(id DESC);
-- Groups the bot has been added to, plus per-group behaviour chosen by that
-- group's own admins. Settings live here (not in the global `settings` table)
-- so one group's choices never leak into another's.
CREATE TABLE IF NOT EXISTS groups (
    chat_id     INTEGER PRIMARY KEY,
    title       TEXT,
    username    TEXT,
    chat_type   TEXT,
    added_by    INTEGER,
    member_count INTEGER DEFAULT 0,
    -- 1 = react to any link, 0 = only when the bot is @-mentioned
    auto_download INTEGER DEFAULT 0,
    -- 1 = only group admins may trigger downloads
    admins_only INTEGER DEFAULT 0,
    -- 1 = delete the bot's progress notes after delivering
    clean_mode  INTEGER DEFAULT 1,
    def_quality TEXT DEFAULT '',
    lang        TEXT DEFAULT '',
    -- 1 = a group admin has explicitly chosen settings for this chat. Until
    -- then the owner's global GROUP_AUTO_DOWNLOAD default applies, so merely
    -- REGISTERING a group must not silently turn auto-download off.
    configured  INTEGER DEFAULT 0,
    downloads   INTEGER DEFAULT 0,
    active      INTEGER DEFAULT 1,
    joined_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_groups_active ON groups(active, last_seen DESC);
"""


class Database:
    """Single shared connection. aiosqlite serialises access internally."""

    _instance: Optional["Database"] = None

    def __new__(cls, *args, **kwargs):
        # A singleton keeps handlers from each opening their own connection.
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialised = False
        return cls._instance

    def __init__(self, path: str = config.DB_PATH):
        if getattr(self, "_initialised", False):
            return
        self.path = path
        self.conn: Optional[aiosqlite.Connection] = None
        self._initialised = True

    # ── lifecycle ─────────────────────────────────────────────────────
    async def connect(self) -> None:
        if self.conn is not None:
            return
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA synchronous=NORMAL")
        # Wait up to 5s for a lock instead of erroring immediately. The bot and
        # the standalone web dashboard both open this file, so brief write
        # contention is normal; a busy timeout turns "database is locked"
        # exceptions into a short transparent wait.
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.conn.executescript(SCHEMA)
        await self._migrate()
        await self.conn.commit()
        logger.info("Database ready at %s", self.path)

    # Columns added after a table shipped. ``CREATE TABLE IF NOT EXISTS`` does
    # nothing for an existing table, so new columns must be added explicitly or
    # every query touching them fails on an already-deployed database.
    _ADDED_COLUMNS = (
        ("groups", "configured", "INTEGER DEFAULT 0"),
        # Inbox messenger upgrade. The original log stored only a text preview,
        # so the owner could see "[photo]" but never the photo itself. Keeping
        # the file_id means media can be re-sent on demand without the bot
        # hoarding files on disk (file_ids stay valid indefinitely).
        ("messages", "file_id", "TEXT"),
        ("messages", "file_type", "TEXT"),
        ("messages", "reply_to", "INTEGER"),
        ("messages", "seen", "INTEGER DEFAULT 0"),
        ("messages", "starred", "INTEGER DEFAULT 0"),
    )

    async def _migrate(self) -> None:
        """Add columns missing from an older on-disk schema. Idempotent."""
        for table, column, decl in self._ADDED_COLUMNS:
            try:
                cur = await self.conn.execute(f"PRAGMA table_info({table})")
                cols = {row[1] for row in await cur.fetchall()}
                await cur.close()
            except Exception as exc:
                logger.debug("migration probe failed for %s: %s", table, exc)
                continue
            if not cols or column in cols:
                continue
            try:
                await self.conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                logger.info("migrated: added %s.%s", table, column)
            except Exception as exc:
                logger.warning("could not add %s.%s: %s", table, column, exc)

    async def init_db(self) -> None:
        await self.connect()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    async def _ensure(self) -> aiosqlite.Connection:
        if self.conn is None:
            await self.connect()
        assert self.conn is not None
        return self.conn

    # ── users ─────────────────────────────────────────────────────────
    async def add_user(self, user_id: int, username: str = "", first_name: str = "") -> None:
        db = await self._ensure()
        await db.execute(
            "INSERT INTO users (user_id, username, first_name) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, "
            "first_name=excluded.first_name, last_active=CURRENT_TIMESTAMP",
            (user_id, username or "", first_name or ""),
        )
        await db.commit()

    async def update_user_activity(self, user_id: int) -> None:
        db = await self._ensure()
        await db.execute(
            "UPDATE users SET last_active=CURRENT_TIMESTAMP WHERE user_id=?", (user_id,)
        )
        await db.commit()

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        db = await self._ensure()
        async with db.execute("SELECT * FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def get_user_language(self, user_id: int) -> str:
        db = await self._ensure()
        async with db.execute(
            "SELECT language FROM users WHERE user_id=?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return (row["language"] if row and row["language"] else config.DEFAULT_LANG)

    async def set_user_language(self, user_id: int, language: str) -> None:
        db = await self._ensure()
        await db.execute(
            "INSERT INTO users (user_id, language) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET language=excluded.language",
            (user_id, language),
        )
        await db.commit()

    async def has_language(self, user_id: int) -> bool:
        db = await self._ensure()
        async with db.execute(
            "SELECT language FROM users WHERE user_id=? AND language IS NOT NULL", (user_id,)
        ) as cur:
            return await cur.fetchone() is not None

    # ── user preferences ──────────────────────────────────────────────
    async def get_prefs(self, user_id: int) -> Dict[str, Any]:
        """A user's download preferences, with sane defaults if unset."""
        db = await self._ensure()
        async with db.execute(
            "SELECT def_quality, def_format, notify FROM user_prefs WHERE user_id=?",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if row:
            return {"def_quality": row["def_quality"], "def_format": row["def_format"],
                    "notify": bool(row["notify"])}
        return {"def_quality": "ask", "def_format": "video", "notify": True}

    async def set_pref(self, user_id: int, key: str, value: Any) -> None:
        if key not in {"def_quality", "def_format", "notify"}:
            return
        db = await self._ensure()
        await db.execute(
            f"INSERT INTO user_prefs (user_id, {key}) VALUES (?, ?) "
            f"ON CONFLICT(user_id) DO UPDATE SET {key}=excluded.{key}",
            (user_id, value),
        )
        await db.commit()

    # ── favorites ─────────────────────────────────────────────────────
    async def add_favorite(self, user_id: int, url: str, platform: str, title: str = "") -> bool:
        """Save a link. Returns False if it was already saved."""
        db = await self._ensure()
        try:
            await db.execute(
                "INSERT INTO favorites (user_id, url, platform, title) VALUES (?, ?, ?, ?)",
                (user_id, url, platform, (title or "")[:120]),
            )
            await db.commit()
            return True
        except Exception:
            return False

    async def remove_favorite(self, user_id: int, url: str) -> None:
        db = await self._ensure()
        await db.execute("DELETE FROM favorites WHERE user_id=? AND url=?", (user_id, url))
        await db.commit()

    async def is_favorite(self, user_id: int, url: str) -> bool:
        db = await self._ensure()
        async with db.execute(
            "SELECT 1 FROM favorites WHERE user_id=? AND url=?", (user_id, url)
        ) as cur:
            return await cur.fetchone() is not None

    async def get_favorites(self, user_id: int, limit: int = 12) -> List[Dict[str, Any]]:
        db = await self._ensure()
        async with db.execute(
            "SELECT url, platform, title FROM favorites WHERE user_id=? "
            "ORDER BY created_at DESC LIMIT ?", (user_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ── referrals ─────────────────────────────────────────────────────
    async def add_referral(self, referred_id: int, referrer_id: int) -> bool:
        """Record who invited a new user. First writer wins; self-refs ignored.

        Guards against farming: a user cannot credit themselves, and the
        referrer must already exist as a real user (blocks fabricated ids in
        a ``?start=ref_<madeup>`` deeplink).
        """
        if referred_id == referrer_id or referrer_id <= 0:
            return False
        db = await self._ensure()
        async with db.execute(
            "SELECT 1 FROM users WHERE user_id=?", (referrer_id,)
        ) as cur:
            if await cur.fetchone() is None:
                return False
        try:
            await db.execute(
                "INSERT INTO referrals (referred_id, referrer_id) VALUES (?, ?)",
                (referred_id, referrer_id),
            )
            await db.commit()
            return True
        except Exception:
            return False

    async def count_referrals(self, referrer_id: int) -> int:
        db = await self._ensure()
        async with db.execute(
            "SELECT COUNT(*) AS n FROM referrals WHERE referrer_id=?", (referrer_id,)
        ) as cur:
            row = await cur.fetchone()
        return row["n"] if row else 0

    async def get_personal_stats(self, user_id: int) -> Dict[str, Any]:
        """Totals + this-week + top platform + last-7-day daily counts."""
        db = await self._ensure()
        out: Dict[str, Any] = {}
        async with db.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(file_size),0) AS bytes "
            "FROM downloads WHERE user_id=? AND success=1", (user_id,)
        ) as cur:
            r = await cur.fetchone()
            out["total"] = r["total"] if r else 0
            out["bytes"] = r["bytes"] if r else 0
        async with db.execute(
            "SELECT COUNT(*) AS n FROM downloads WHERE user_id=? AND success=1 "
            "AND timestamp >= datetime('now','-7 days')", (user_id,)
        ) as cur:
            r = await cur.fetchone()
            out["week"] = r["n"] if r else 0
        async with db.execute(
            "SELECT platform, COUNT(*) AS n FROM downloads WHERE user_id=? AND success=1 "
            "GROUP BY platform ORDER BY n DESC LIMIT 1", (user_id,)
        ) as cur:
            r = await cur.fetchone()
            out["top_platform"] = r["platform"] if r else ""
        # Daily counts for the last 7 days (oldest first).
        async with db.execute(
            "SELECT date(timestamp) AS d, COUNT(*) AS n FROM downloads "
            "WHERE user_id=? AND success=1 AND timestamp >= datetime('now','-6 days') "
            "GROUP BY d ORDER BY d", (user_id,)
        ) as cur:
            byday = {row["d"]: row["n"] for row in await cur.fetchall()}
        out["byday"] = byday
        return out

    # ── bans ──────────────────────────────────────────────────────────
    async def is_banned(self, user_id: int) -> bool:
        db = await self._ensure()
        async with db.execute(
            "SELECT 1 FROM banned_users WHERE user_id=? "
            "UNION SELECT 1 FROM users WHERE user_id=? AND is_banned=1",
            (user_id, user_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def ban_user(self, user_id: int, reason: str = "", banned_by: int = 0) -> None:
        db = await self._ensure()
        await db.execute(
            "INSERT INTO banned_users (user_id, reason, banned_by) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET reason=excluded.reason, "
            "banned_by=excluded.banned_by, ban_date=CURRENT_TIMESTAMP",
            (user_id, reason or None, banned_by),
        )
        await db.execute(
            "UPDATE users SET is_banned=1, ban_reason=? WHERE user_id=?",
            (reason or None, user_id),
        )
        await db.commit()

    async def unban_user(self, user_id: int) -> None:
        db = await self._ensure()
        await db.execute("DELETE FROM banned_users WHERE user_id=?", (user_id,))
        await db.execute(
            "UPDATE users SET is_banned=0, ban_reason=NULL WHERE user_id=?", (user_id,)
        )
        await db.commit()

    async def get_banned_users(self, limit: int = 50) -> List[Dict[str, Any]]:
        db = await self._ensure()
        async with db.execute(
            "SELECT * FROM banned_users ORDER BY ban_date DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ── downloads ─────────────────────────────────────────────────────
    async def add_download(
        self,
        user_id: int,
        platform: str,
        url: str,
        file_size: int = 0,
        success: int = 1,
        error: str = "",
        duration_ms: int = 0,
    ) -> None:
        db = await self._ensure()
        # Store only a short URL prefix; the full string is not worth the space.
        await db.execute(
            "INSERT INTO downloads (user_id, platform, url, file_size, success, error, duration_ms) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, platform, url[:config.URL_STORE_CHARS], file_size, success,
             error[:200] or None, duration_ms),
        )
        if success:
            await db.execute(
                "UPDATE users SET total_downloads = total_downloads + 1 WHERE user_id=?",
                (user_id,),
            )
        # Lifetime counters live in settings so pruning old rows never loses them.
        await self._bump("lt_attempts", 1, conn=db)
        if success:
            await self._bump("lt_downloads", 1, conn=db)
            await self._bump("lt_bytes", file_size, conn=db)
            await self._bump(f"lt_pf_{platform}", 1, conn=db)
        else:
            await self._bump("lt_failed", 1, conn=db)
        await db.commit()

    async def _bump(self, key: str, amount: int, conn=None) -> None:
        """Increment a counter stored in the settings table."""
        if not amount:
            return
        db = conn or await self._ensure()
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = CAST(CAST(settings.value AS INTEGER) + ? AS TEXT), "
            "updated_at = CURRENT_TIMESTAMP",
            (key, str(amount), amount),
        )

    async def _counter(self, key: str) -> int:
        raw = await self.get_setting(key, "0")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    async def get_user_downloads(self, user_id: int) -> int:
        db = await self._ensure()
        async with db.execute(
            "SELECT COUNT(*) c FROM downloads WHERE user_id=? AND success=1", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    async def get_user_recent(self, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """The user's most recent successful downloads, newest first.

        Feeds the "my history / re-download" feature. Only successful rows with
        a stored URL are returned, so every entry is actually re-downloadable.
        """
        db = await self._ensure()
        async with db.execute(
            "SELECT platform, url, file_size, timestamp FROM downloads "
            "WHERE user_id=? AND success=1 AND url IS NOT NULL AND url != '' "
            "AND url LIKE 'http%' AND platform NOT IN ('filehost') "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_user_hourly(self, user_id: int) -> int:
        """Downloads in the last hour — feeds the invisible soft throttle."""
        db = await self._ensure()
        async with db.execute(
            "SELECT COUNT(*) c FROM downloads WHERE user_id=? AND success=1 "
            "AND timestamp > datetime('now', '-1 hour')",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    async def get_total_downloads(self) -> int:
        """Lifetime total, read from the counter so pruning cannot shrink it."""
        return await self._counter("lt_downloads")

    async def get_failed_downloads(self) -> int:
        return await self._counter("lt_failed")

    async def get_total_users(self) -> int:
        db = await self._ensure()
        async with db.execute("SELECT COUNT(*) c FROM users") as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    async def get_active_users(self, hours: int = 24) -> int:
        db = await self._ensure()
        async with db.execute(
            f"SELECT COUNT(*) c FROM users WHERE last_active > datetime('now', '-{int(hours)} hours')"
        ) as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    async def get_new_users(self, hours: int = 24) -> int:
        db = await self._ensure()
        async with db.execute(
            f"SELECT COUNT(*) c FROM users WHERE join_date > datetime('now', '-{int(hours)} hours')"
        ) as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    async def get_today_downloads(self) -> int:
        db = await self._ensure()
        async with db.execute(
            "SELECT COUNT(*) c FROM downloads WHERE success=1 AND date(timestamp)=date('now')"
        ) as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    async def get_week_downloads(self) -> int:
        db = await self._ensure()
        async with db.execute(
            "SELECT COUNT(*) c FROM downloads WHERE success=1 "
            "AND timestamp > datetime('now', '-7 days')"
        ) as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    async def get_total_bytes(self) -> int:
        return await self._counter("lt_bytes")

    async def get_top_platforms(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Lifetime ranking from counters, so pruning does not distort it."""
        db = await self._ensure()
        async with db.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'lt_pf_%'"
        ) as cur:
            rows = await cur.fetchall()
        stats = []
        for r in rows:
            try:
                stats.append({"platform": r["key"][6:], "count": int(r["value"])})
            except (TypeError, ValueError):
                continue
        stats.sort(key=lambda x: x["count"], reverse=True)
        return stats[:limit]

    async def get_platform_health(self) -> List[Dict[str, Any]]:
        """Success ratio per platform in the last 24h — shows what is broken."""
        db = await self._ensure()
        async with db.execute(
            "SELECT platform, "
            "SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) ok, "
            "SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) bad "
            "FROM downloads WHERE timestamp > datetime('now','-24 hours') "
            "GROUP BY platform ORDER BY (ok+bad) DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_recent_errors(self, limit: int = 10) -> List[Dict[str, Any]]:
        db = await self._ensure()
        async with db.execute(
            "SELECT platform, error, url, timestamp FROM downloads "
            "WHERE success=0 AND error IS NOT NULL ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_top_users(self, limit: int = 5) -> List[Dict[str, Any]]:
        db = await self._ensure()
        async with db.execute(
            "SELECT user_id, username, first_name, total_downloads FROM users "
            "ORDER BY total_downloads DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ── analytics (admin panel) ───────────────────────────────────────
    async def get_daily_downloads(self, days: int = 7) -> List[Dict[str, Any]]:
        """Successful downloads per day for the last ``days`` days, oldest first.

        Returns one entry per calendar day (filling gaps with 0) so the chart
        has a fixed width regardless of activity.
        """
        db = await self._ensure()
        async with db.execute(
            "SELECT date(timestamp) d, COUNT(*) c FROM downloads "
            "WHERE success=1 AND timestamp > datetime('now', ?) "
            "GROUP BY date(timestamp)",
            (f"-{int(days)} days",),
        ) as cur:
            got = {r["d"]: r["c"] for r in await cur.fetchall()}
        from datetime import datetime, timedelta

        today = datetime.utcnow().date()
        out = []
        for i in range(days - 1, -1, -1):
            day = today - timedelta(days=i)
            key = day.isoformat()
            out.append({"day": key, "label": day.strftime("%m-%d"),
                        "count": got.get(key, 0)})
        return out

    async def get_daily_new_users(self, days: int = 7) -> List[Dict[str, Any]]:
        """New user signups per day for the last ``days`` days, oldest first."""
        db = await self._ensure()
        async with db.execute(
            "SELECT date(join_date) d, COUNT(*) c FROM users "
            "WHERE join_date > datetime('now', ?) GROUP BY date(join_date)",
            (f"-{int(days)} days",),
        ) as cur:
            got = {r["d"]: r["c"] for r in await cur.fetchall()}
        from datetime import datetime, timedelta

        today = datetime.utcnow().date()
        out = []
        for i in range(days - 1, -1, -1):
            day = today - timedelta(days=i)
            key = day.isoformat()
            out.append({"day": key, "label": day.strftime("%m-%d"),
                        "count": got.get(key, 0)})
        return out

    async def get_peak_hours(self) -> List[Dict[str, Any]]:
        """Download count grouped by hour-of-day over the retained window."""
        db = await self._ensure()
        async with db.execute(
            "SELECT CAST(strftime('%H', timestamp) AS INTEGER) h, COUNT(*) c "
            "FROM downloads WHERE success=1 GROUP BY h"
        ) as cur:
            got = {r["h"]: r["c"] for r in await cur.fetchall()}
        return [{"hour": h, "count": got.get(h, 0)} for h in range(24)]

    async def get_platform_avg_time(self) -> List[Dict[str, Any]]:
        """Average successful download time (ms) per platform, last 24h."""
        db = await self._ensure()
        async with db.execute(
            "SELECT platform, AVG(duration_ms) avg_ms, COUNT(*) c FROM downloads "
            "WHERE success=1 AND duration_ms > 0 "
            "AND timestamp > datetime('now','-24 hours') "
            "GROUP BY platform ORDER BY c DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    async def get_active_user_ids(self, hours: int = 168) -> List[int]:
        """User ids active within the window (for targeted broadcast)."""
        db = await self._ensure()
        async with db.execute(
            f"SELECT user_id FROM users WHERE is_banned=0 "
            f"AND last_active > datetime('now', '-{int(hours)} hours')"
        ) as cur:
            return [r["user_id"] for r in await cur.fetchall()]

    # ── temporary bans ────────────────────────────────────────────────
    async def ban_user_until(self, user_id: int, until_ts: int, reason: str = "",
                             banned_by: int = 0) -> None:
        """Ban a user until a unix timestamp. Stored as a setting so it needs
        no schema change and is auto-checked by ``is_banned``."""
        await self.ban_user(user_id, reason, banned_by)
        await self.set_setting(f"tempban_{user_id}", str(int(until_ts)))

    async def get_temp_ban_expiry(self, user_id: int) -> int:
        return await self._counter(f"tempban_{user_id}")

    async def expire_temp_bans(self) -> int:
        """Unban any temp-banned user whose window has passed. Returns count."""
        db = await self._ensure()
        now = int(time.time())
        async with db.execute(
            "SELECT key, value FROM settings WHERE key LIKE 'tempban_%'"
        ) as cur:
            rows = await cur.fetchall()
        freed = 0
        for r in rows:
            try:
                uid = int(r["key"][8:])
                until = int(r["value"])
            except (TypeError, ValueError):
                continue
            if until and until <= now:
                await self.unban_user(uid)
                await db.execute("DELETE FROM settings WHERE key=?", (r["key"],))
                freed += 1
        if freed:
            await db.commit()
        return freed

    async def get_all_user_ids(self) -> List[int]:
        db = await self._ensure()
        async with db.execute(
            "SELECT user_id FROM users WHERE is_banned=0"
        ) as cur:
            return [r["user_id"] for r in await cur.fetchall()]

    async def export_users_csv(self) -> str:
        """Return a UTF-8-BOM CSV so Excel renders Persian correctly."""
        db = await self._ensure()
        async with db.execute(
            "SELECT user_id, username, first_name, language, join_date, "
            "last_active, is_banned, total_downloads FROM users ORDER BY user_id"
        ) as cur:
            rows = await cur.fetchall()
        lines = ["user_id,username,first_name,language,join_date,last_active,is_banned,total_downloads"]
        for r in rows:
            vals = []
            for v in tuple(r):
                s = "" if v is None else str(v)
                s = s.replace('"', '""')
                vals.append(f'"{s}"' if ("," in s or '"' in s) else s)
            lines.append(",".join(vals))
        return "\ufeff" + "\n".join(lines)

    # ── settings ──────────────────────────────────────────────────────
    async def get_setting(self, key: str, default: str = "") -> str:
        db = await self._ensure()
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
        return row["value"] if row and row["value"] is not None else default

    async def set_setting(self, key: str, value: str) -> None:
        db = await self._ensure()
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=CURRENT_TIMESTAMP",
            (key, value),
        )
        await db.commit()

    async def get_welcome_message(self) -> str:
        return await self.get_setting("welcome_message", "")

    async def set_welcome_message(self, text: str) -> None:
        await self.set_setting("welcome_message", text)

    # ── file_id cache ─────────────────────────────────────────────────
    @staticmethod
    def cache_key(url: str, quality: str = "best") -> str:
        import hashlib
        import re

        # Normalise so the same video shared via different link shapes hits the
        # same cache row.
        u = url.strip().lower()
        u = re.sub(r"[?&](utm_[^&]*|igshid|si|feature|fbclid|s|t)=[^&]*", "", u)
        u = u.rstrip("/?&")
        return hashlib.sha256(f"{u}|{quality}".encode()).hexdigest()[:32]

    async def get_cached(self, url: str, quality: str = "best") -> Optional[Dict[str, Any]]:
        if not config.CACHE_FILE_IDS:
            return None
        db = await self._ensure()
        key = self.cache_key(url, quality)
        async with db.execute("SELECT * FROM file_cache WHERE cache_key=?", (key,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        await db.execute("UPDATE file_cache SET hits=hits+1 WHERE cache_key=?", (key,))
        await db.commit()
        return dict(row)

    async def put_cached(
        self,
        url: str,
        quality: str,
        file_id: str,
        file_type: str,
        *,
        platform: str = "",
        title: str = "",
        file_size: int = 0,
        duration: int = 0,
        width: int = 0,
        height: int = 0,
    ) -> None:
        if not config.CACHE_FILE_IDS or not file_id:
            return
        db = await self._ensure()
        await db.execute(
            "INSERT INTO file_cache (cache_key, file_id, file_type, platform, title, "
            "file_size, duration, width, height) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET file_id=excluded.file_id",
            (
                self.cache_key(url, quality), file_id, file_type, platform,
                title[:150], file_size, duration, width, height,
            ),
        )
        await db.commit()

    async def cache_stats(self) -> Dict[str, Any]:
        db = await self._ensure()
        async with db.execute(
            "SELECT COUNT(*) rows, COALESCE(SUM(hits),0) hits, "
            "COALESCE(SUM(file_size),0) bytes FROM file_cache"
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else {"rows": 0, "hits": 0, "bytes": 0}

    async def clear_cache(self) -> int:
        db = await self._ensure()
        async with db.execute("SELECT COUNT(*) c FROM file_cache") as cur:
            row = await cur.fetchone()
        n = row["c"] if row else 0
        await db.execute("DELETE FROM file_cache")
        await db.commit()
        return n

    # ── housekeeping ──────────────────────────────────────────────────
    async def maintain(self, aggressive: bool = False) -> Dict[str, int]:
        """Prune the database so it cannot grow without bound.

        Called on a timer and whenever free disk gets low. Lifetime statistics
        are unaffected because they live in counters, not in the pruned rows.
        """
        db = await self._ensure()
        report = {"downloads_pruned": 0, "cache_pruned": 0, "bytes_before": 0,
                  "bytes_after": 0}
        report["bytes_before"] = await self.file_size()

        async def run(sql: str, params: tuple = ()) -> int:
            """Execute and close the cursor.

            Leaving cursors open blocks VACUUM with
            'cannot VACUUM - SQL statements in progress'.
            """
            cur = await db.execute(sql, params)
            count = cur.rowcount or 0
            await cur.close()
            return count

        days = 2 if aggressive else config.DB_RETENTION_DAYS
        report["downloads_pruned"] = await run(
            f"DELETE FROM downloads WHERE timestamp < datetime('now', '-{int(days)} days')"
        )

        # Drop resolved errors: only the most recent are ever displayed.
        await run(
            "UPDATE downloads SET error=NULL WHERE error IS NOT NULL AND id NOT IN "
            "(SELECT id FROM downloads WHERE success=0 ORDER BY id DESC LIMIT 50)"
        )

        # Age out unused cache rows, then enforce the row cap by evicting the
        # least-used entries first.
        report["cache_pruned"] = await run(
            f"DELETE FROM file_cache WHERE created_at < "
            f"datetime('now', '-{int(config.CACHE_MAX_AGE_DAYS)} days') AND hits = 0"
        )

        max_rows = 500 if aggressive else config.CACHE_MAX_ROWS
        async with db.execute("SELECT COUNT(*) c FROM file_cache") as c:
            row = await c.fetchone()
        excess = (row["c"] if row else 0) - max_rows
        if excess > 0:
            report["cache_pruned"] += await run(
                "DELETE FROM file_cache WHERE cache_key IN ("
                "  SELECT cache_key FROM file_cache ORDER BY hits ASC, created_at ASC LIMIT ?"
                ")",
                (excess,),
            )

        # Keep only recent broadcast records.
        await run(
            "DELETE FROM broadcasts WHERE id NOT IN "
            "(SELECT id FROM broadcasts ORDER BY id DESC LIMIT 50)"
        )

        await db.commit()

        if report["downloads_pruned"] or report["cache_pruned"] or aggressive:
            await self._compact()

        report["bytes_after"] = await self.file_size()
        if report["downloads_pruned"] or report["cache_pruned"]:
            logger.info(
                "DB maintenance: -%d downloads, -%d cache rows, %.1fKB -> %.1fKB",
                report["downloads_pruned"], report["cache_pruned"],
                report["bytes_before"] / 1024, report["bytes_after"] / 1024,
            )
        return report

    async def _compact(self) -> None:
        """Checkpoint the WAL and VACUUM so the file actually shrinks.

        VACUUM cannot run inside a transaction, and aiosqlite's connection is
        pinned to its own worker thread, so this uses a short-lived plain
        connection in a thread instead of touching the shared one.
        """
        db = await self._ensure()
        await db.commit()

        def _vacuum(path: str) -> None:
            import sqlite3

            conn = sqlite3.connect(path, isolation_level=None, timeout=30)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.execute("VACUUM")
            finally:
                conn.close()

        try:
            import asyncio

            await asyncio.to_thread(_vacuum, self.path)
        except Exception as exc:
            logger.warning("VACUUM skipped: %s", exc)

    async def file_size(self) -> int:
        """Total on-disk size including the WAL sidecar files."""
        import os

        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(self.path + suffix)
            except OSError:
                pass
        return total

    async def storage_report(self) -> Dict[str, Any]:
        """Row counts per table, for the admin panel."""
        db = await self._ensure()
        out: Dict[str, Any] = {"bytes": await self.file_size()}
        for table in ("users", "downloads", "file_cache", "banned_users",
                      "broadcasts", "settings"):
            try:
                async with db.execute(f"SELECT COUNT(*) c FROM {table}") as cur:
                    row = await cur.fetchone()
                out[table] = row["c"] if row else 0
            except Exception:
                out[table] = 0
        return out

    # ── broadcasts ────────────────────────────────────────────────────
    async def log_broadcast(self, admin_id: int, text: str, sent: int, failed: int) -> None:
        db = await self._ensure()
        await db.execute(
            "INSERT INTO broadcasts (admin_id, message_text, sent_count, failed_count) "
            "VALUES (?, ?, ?, ?)",
            (admin_id, (text or "")[:1000], sent, failed),
        )
        await db.commit()


    # ── message log (owner-only inbox) ────────────────────────────────
    async def log_message(self, *, user_id: int, chat_id: int, chat_type: str,
                          msg_id: int, kind: str, text: str,
                          direction: str = "in",
                          file_id: str = "", file_type: str = "",
                          reply_to: int = 0) -> None:
        """Record one message so the owner can read the bot's private chats.

        ``file_id`` is stored for media so the owner can pull the actual photo,
        video, or voice note back out of the inbox later. Telegram file_ids do
        not expire, so this costs one text column instead of disk space.
        """
        db = await self._ensure()
        await db.execute(
            "INSERT INTO messages (user_id, chat_id, chat_type, msg_id, "
            "direction, kind, text, file_id, file_type, reply_to, seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, chat_id, chat_type, msg_id, direction, kind,
             (text or "")[:600], file_id or None, file_type or None,
             reply_to or None, 1 if direction == "out" else 0),
        )
        await db.commit()

    async def mark_seen(self, user_id: int) -> int:
        """Mark a user's incoming messages as read. Returns rows touched."""
        db = await self._ensure()
        cur = await db.execute(
            "UPDATE messages SET seen = 1 "
            "WHERE user_id = ? AND direction = 'in' AND COALESCE(seen, 0) = 0",
            (user_id,),
        )
        await db.commit()
        return cur.rowcount or 0

    async def unread_total(self) -> int:
        """Unread incoming messages across every private chat."""
        db = await self._ensure()
        async with db.execute(
            "SELECT COUNT(*) c FROM messages WHERE direction = 'in' "
            "AND chat_type = 'private' AND COALESCE(seen, 0) = 0"
        ) as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    async def toggle_star(self, msg_row_id: int) -> bool:
        """Flip the star on one logged message. Returns the new state."""
        db = await self._ensure()
        async with db.execute(
            "SELECT COALESCE(starred, 0) s FROM messages WHERE id = ?",
            (msg_row_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        new = 0 if row["s"] else 1
        await db.execute("UPDATE messages SET starred = ? WHERE id = ?",
                         (new, msg_row_id))
        await db.commit()
        return bool(new)

    async def starred_messages(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Every starred message, newest first — the owner's pinned shortlist."""
        db = await self._ensure()
        async with db.execute(
            "SELECT m.*, u.username, u.first_name FROM messages m "
            "LEFT JOIN users u ON u.user_id = m.user_id "
            "WHERE COALESCE(m.starred, 0) = 1 ORDER BY m.id DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def message_by_id(self, msg_row_id: int) -> Optional[Dict[str, Any]]:
        db = await self._ensure()
        async with db.execute(
            "SELECT m.*, u.username, u.first_name FROM messages m "
            "LEFT JOIN users u ON u.user_id = m.user_id WHERE m.id = ?",
            (msg_row_id,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def media_in_thread(self, user_id: int, limit: int = 30) -> List[Dict[str, Any]]:
        """Only the media a user sent, newest first — the thread's gallery."""
        db = await self._ensure()
        async with db.execute(
            "SELECT * FROM messages WHERE user_id = ? "
            "AND file_id IS NOT NULL AND file_id != '' "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def recent_chats(self, limit: int = 12, offset: int = 0) -> List[Dict[str, Any]]:
        """Users with recent traffic, newest first — the inbox list.

        Carries unread count, last direction, and whether the last message had
        media, so the list can render like a real messenger without N+1 queries.
        """
        db = await self._ensure()
        sql = (
            "SELECT m.user_id, MAX(m.id) AS last_id, COUNT(*) AS total, "
            "       MAX(m.created_at) AS last_at, "
            "       SUM(CASE WHEN m.direction = 'in' AND COALESCE(m.seen,0) = 0 "
            "                THEN 1 ELSE 0 END) AS unread, "
            "       (SELECT text FROM messages x WHERE x.user_id = m.user_id "
            "        ORDER BY x.id DESC LIMIT 1) AS last_text, "
            "       (SELECT kind FROM messages x WHERE x.user_id = m.user_id "
            "        ORDER BY x.id DESC LIMIT 1) AS last_kind, "
            "       (SELECT direction FROM messages x WHERE x.user_id = m.user_id "
            "        ORDER BY x.id DESC LIMIT 1) AS last_dir, "
            "       u.username, u.first_name "
            "FROM messages m LEFT JOIN users u ON u.user_id = m.user_id "
            "WHERE m.chat_type = 'private' "
            "GROUP BY m.user_id ORDER BY last_id DESC LIMIT ? OFFSET ?"
        )
        async with db.execute(sql, (limit, offset)) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def count_chats(self) -> int:
        db = await self._ensure()
        async with db.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM messages "
            "WHERE chat_type = 'private'"
        ) as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    async def conversation(self, user_id: int, limit: int = 20,
                           offset: int = 0) -> List[Dict[str, Any]]:
        """One user's thread, oldest-last so it reads like a chat."""
        db = await self._ensure()
        async with db.execute(
            "SELECT * FROM messages WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in reversed(rows)]

    async def count_conversation(self, user_id: int) -> int:
        db = await self._ensure()
        async with db.execute(
            "SELECT COUNT(*) c FROM messages WHERE user_id = ?", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return row["c"] if row else 0

    async def search_messages(self, term: str, limit: int = 20) -> List[Dict[str, Any]]:
        db = await self._ensure()
        async with db.execute(
            "SELECT m.*, u.username, u.first_name FROM messages m "
            "LEFT JOIN users u ON u.user_id = m.user_id "
            "WHERE m.text LIKE ? ORDER BY m.id DESC LIMIT ?",
            (f"%{term}%", limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def prune_messages(self, keep: int = 5000) -> int:
        """Keep the log bounded — /data is small. Returns rows deleted."""
        db = await self._ensure()
        async with db.execute("SELECT COUNT(*) c FROM messages") as cur:
            row = await cur.fetchone()
        total = row["c"] if row else 0
        if total <= keep:
            return 0
        await db.execute(
            "DELETE FROM messages WHERE id <= "
            "(SELECT id FROM messages ORDER BY id DESC LIMIT 1 OFFSET ?)",
            (keep,),
        )
        await db.commit()
        return total - keep


    # ── groups ────────────────────────────────────────────────────────
    async def upsert_group(self, chat_id: int, title: str = "", username: str = "",
                           chat_type: str = "group", added_by: int = 0,
                           member_count: int = 0) -> None:
        """Register or refresh a group. Never clobbers the group's own settings."""
        db = await self._ensure()
        await db.execute(
            "INSERT INTO groups (chat_id, title, username, chat_type, added_by, "
            "member_count) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "  title=excluded.title, username=excluded.username, "
            "  chat_type=excluded.chat_type, active=1, "
            "  member_count=CASE WHEN excluded.member_count > 0 "
            "                    THEN excluded.member_count ELSE groups.member_count END, "
            "  last_seen=CURRENT_TIMESTAMP",
            (chat_id, title[:120], (username or "")[:64], chat_type, added_by,
             member_count),
        )
        await db.commit()

    async def mark_group_left(self, chat_id: int) -> None:
        """Flag a group inactive instead of deleting it, so settings survive a re-add."""
        db = await self._ensure()
        await db.execute(
            "UPDATE groups SET active=0, last_seen=CURRENT_TIMESTAMP WHERE chat_id=?",
            (chat_id,))
        await db.commit()

    async def get_group(self, chat_id: int) -> Optional[Dict[str, Any]]:
        db = await self._ensure()
        async with db.execute("SELECT * FROM groups WHERE chat_id=?", (chat_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def set_group_flag(self, chat_id: int, field: str, value: Any) -> None:
        """Update one group setting. ``field`` is whitelisted, never interpolated raw.

        Any explicit change also marks the group ``configured``, which is what
        makes the group's own choice win over the owner's global default.
        """
        allowed = {"auto_download", "admins_only", "clean_mode", "def_quality",
                   "lang", "member_count", "configured"}
        if field not in allowed:
            raise ValueError(f"illegal group field: {field!r}")
        db = await self._ensure()
        mark = field in {"auto_download", "admins_only", "clean_mode",
                         "def_quality", "lang"}
        extra = ", configured=1" if mark else ""
        await db.execute(
            f"UPDATE groups SET {field}=?{extra}, "
            f"last_seen=CURRENT_TIMESTAMP WHERE chat_id=?",
            (value, chat_id))
        await db.commit()

    async def bump_group_downloads(self, chat_id: int) -> None:
        db = await self._ensure()
        await db.execute(
            "UPDATE groups SET downloads=downloads+1, last_seen=CURRENT_TIMESTAMP "
            "WHERE chat_id=?", (chat_id,))
        await db.commit()

    async def list_groups(self, limit: int = 20, offset: int = 0,
                          active_only: bool = True) -> List[Dict[str, Any]]:
        db = await self._ensure()
        sql = "SELECT * FROM groups"
        if active_only:
            sql += " WHERE active=1"
        sql += " ORDER BY last_seen DESC LIMIT ? OFFSET ?"
        async with db.execute(sql, (limit, offset)) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def group_stats(self) -> Dict[str, int]:
        db = await self._ensure()
        out = {"total": 0, "active": 0, "downloads": 0, "members": 0}
        async with db.execute(
            "SELECT COUNT(*) total, "
            "       SUM(CASE WHEN active=1 THEN 1 ELSE 0 END) active, "
            "       COALESCE(SUM(downloads),0) downloads, "
            "       COALESCE(SUM(member_count),0) members FROM groups"
        ) as cur:
            row = await cur.fetchone()
        if row:
            out = {"total": row["total"] or 0, "active": row["active"] or 0,
                   "downloads": row["downloads"] or 0, "members": row["members"] or 0}
        return out


db = Database()
