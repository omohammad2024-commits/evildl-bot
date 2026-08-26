"""Job queue + disk guard.

A public bot must never fall over because 200 people pasted links at once, and
it must never tell a user "you hit a limit". Both goals are handled here:

* a bounded worker pool runs downloads; everything else waits its turn
* per-user concurrency stops one person monopolising the workers
* heavy users get a small silent delay instead of a refusal
* the disk is checked before each job and swept afterwards
"""
import asyncio
import logging
import os
import shutil
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Optional

from bot import config

logger = logging.getLogger(__name__)


class DownloadQueue:
    def __init__(self) -> None:
        self._sem = asyncio.Semaphore(config.MAX_CONCURRENT)
        self._user_active: dict[int, int] = defaultdict(int)
        self._user_locks: dict[int, asyncio.Semaphore] = {}
        self.completed = 0
        self.failed = 0
        self.started_at = time.time()

    # ── introspection for the admin panel ─────────────────────────────
    @property
    def active(self) -> int:
        return sum(self._user_active.values())

    @property
    def waiting(self) -> int:
        # Semaphore has no public waiter count; derive from internals safely.
        waiters = getattr(self._sem, "_waiters", None)
        return len(waiters) if waiters else 0

    def user_active(self, user_id: int) -> int:
        return self._user_active.get(user_id, 0)

    def _user_sem(self, user_id: int) -> asyncio.Semaphore:
        sem = self._user_locks.get(user_id)
        if sem is None:
            sem = asyncio.Semaphore(config.PER_USER_CONCURRENT)
            self._user_locks[user_id] = sem
        return sem

    def _release_user_sem(self, user_id: int) -> None:
        """Drop a user's semaphore once they have no active jobs, so the dict
        cannot grow without bound on a long-running public bot. Only removes a
        fully-available (untaken) semaphore to avoid discarding one with
        in-flight waiters."""
        if self._user_active.get(user_id, 0) > 0:
            return
        sem = self._user_locks.get(user_id)
        # A brand-new semaphore has its full permit count and no waiters.
        if sem is not None and not sem.locked() and not getattr(sem, "_waiters", None):
            self._user_locks.pop(user_id, None)

    # ── the wrapper every download goes through ───────────────────────
    async def run(
        self,
        user_id: int,
        coro_factory: Callable[[], Awaitable[Any]],
        *,
        hourly_count: int = 0,
        on_queued: Optional[Callable[[], Awaitable[Any]]] = None,
    ) -> Any:
        """Execute ``coro_factory()`` under the queue's limits.

        ``on_queued`` is awaited only when the job actually has to wait, so the
        user sees a neutral "queued" note rather than a limit warning.
        """
        # Fair-use slowdown: invisible, and never a refusal. The soft cap is
        # live-editable from the admin panel via the 'lim_soft_hourly' setting;
        # fall back to the static config default when it's unset.
        soft = config.SOFT_HOURLY
        try:
            from bot.database import db as _db
            raw = await _db.get_setting("lim_soft_hourly", "")
            if raw and raw.isdigit():
                soft = int(raw)
        except Exception:
            pass
        if soft and hourly_count > soft:
            await asyncio.sleep(config.SOFT_DELAY)

        user_sem = self._user_sem(user_id)
        must_wait = self._sem.locked() or user_sem.locked()
        if must_wait and on_queued:
            try:
                await on_queued()
            except Exception:
                logger.debug("on_queued notifier failed", exc_info=True)

        async with user_sem:
            async with self._sem:
                self._user_active[user_id] += 1
                try:
                    ensure_disk_space()
                    result = await coro_factory()
                    self.completed += 1
                    return result
                except Exception:
                    self.failed += 1
                    raise
                finally:
                    self._user_active[user_id] -= 1
                    if self._user_active[user_id] <= 0:
                        self._user_active.pop(user_id, None)
                    sweep_temp()
        # Outside the semaphores: reclaim this user's lock if they're now idle.
        self._release_user_sem(user_id)


queue = DownloadQueue()


# ── disk guard ────────────────────────────────────────────────────────
# Even in a disk-panic force sweep, never delete files younger than this many
# seconds — they are almost certainly a concurrent worker's in-flight download.
FORCE_SWEEP_MIN_AGE = 30


class DiskFull(RuntimeError):
    pass


def free_bytes(path: str = "") -> int:
    try:
        return shutil.disk_usage(path or config.TEMP_DIR).free
    except Exception:
        return 0


def ensure_disk_space() -> None:
    """Sweep, then refuse to start if the disk is still critically low."""
    if free_bytes() >= config.MIN_FREE_DISK:
        return
    sweep_temp(force=True)
    remaining = free_bytes()
    if remaining < config.MIN_FREE_DISK:
        raise DiskFull(
            f"low disk: {remaining // (1024 * 1024)}MB free"
        )


def sweep_temp(force: bool = False) -> int:
    """Delete stale files from the temp dir. Returns bytes reclaimed.

    Downloads clean up after themselves in a ``finally`` block; this catches
    anything orphaned by a crash or a hard kill. ``force`` shortens the age
    grace period for the disk-panic path, but NEVER drops it to zero: with up
    to ``MAX_CONCURRENT`` workers writing simultaneously, deleting age-0 files
    would nuke another worker's in-flight download (and on Linux the open fd
    means the space isn't even reclaimed — we'd only corrupt a live job). A
    few-second floor keeps active transfers safe while still clearing orphans.
    """
    reclaimed = 0
    now = time.time()
    # Force mode still respects a minimum age so concurrent, actively-writing
    # downloads are never deleted out from under a worker.
    max_age = FORCE_SWEEP_MIN_AGE if force else config.TEMP_MAX_AGE
    try:
        for name in os.listdir(config.TEMP_DIR):
            path = os.path.join(config.TEMP_DIR, name)
            try:
                if not os.path.isfile(path):
                    continue
                if now - os.path.getmtime(path) < max_age:
                    continue
                size = os.path.getsize(path)
                os.remove(path)
                reclaimed += size
            except OSError:
                continue
    except FileNotFoundError:
        os.makedirs(config.TEMP_DIR, exist_ok=True)
    if reclaimed:
        logger.info("Swept %.1fMB from temp dir", reclaimed / 1024 / 1024)
    return reclaimed


def temp_usage() -> int:
    """Bytes currently held by the temp directory."""
    total = 0
    try:
        for name in os.listdir(config.TEMP_DIR):
            try:
                total += os.path.getsize(os.path.join(config.TEMP_DIR, name))
            except OSError:
                continue
    except FileNotFoundError:
        pass
    return total


def trim_logs() -> int:
    """Truncate oversized log files.

    Python's RotatingFileHandler covers bot.log, but the supervisor's captured
    stdout files are outside its control, so they are trimmed here.
    """
    reclaimed = 0
    log_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "logs"
    )
    log_dir = os.path.normpath(log_dir)
    limit = 10 * 1024 * 1024
    for directory in (log_dir, os.path.dirname(config.LOG_PATH)):
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if not name.endswith((".out", ".log", ".out.1", ".log.1")):
                continue
            path = os.path.join(directory, name)
            try:
                size = os.path.getsize(path)
                if size <= limit:
                    continue
                # Keep the tail: that is where the useful diagnostics are.
                with open(path, "rb") as fh:
                    fh.seek(-limit // 2, os.SEEK_END)
                    tail = fh.read()
                with open(path, "wb") as fh:
                    fh.write(b"[... truncated by housekeeping ...]\n")
                    fh.write(tail)
                reclaimed += size - os.path.getsize(path)
            except OSError:
                continue
    if reclaimed:
        logger.info("Trimmed %.1fMB of logs", reclaimed / 1024 / 1024)
    return reclaimed
