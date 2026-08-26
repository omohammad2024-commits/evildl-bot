"""Short-lived job store.

Callback data is limited to 64 bytes, so a URL cannot be embedded in a button.
Instead each pending job gets a short token and the real payload lives here.
Entries expire so a long-running bot does not leak memory.
"""
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

TTL = 3600  # seconds a token stays valid


@dataclass
class Job:
    url: str
    platform: str
    user_id: int
    chat_id: int = 0
    quality: str = "best"
    title: str = ""
    created_at: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)


class JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}

    def put(self, job: Job) -> str:
        self._prune()
        token = secrets.token_urlsafe(6)[:8]
        self._jobs[token] = job
        return token

    def get(self, token: str) -> Optional[Job]:
        self._prune()
        return self._jobs.get(token)

    def drop(self, token: str) -> None:
        self._jobs.pop(token, None)

    def _prune(self) -> None:
        if len(self._jobs) < 64:
            return
        cutoff = time.time() - TTL
        for token in [t for t, j in self._jobs.items() if j.created_at < cutoff]:
            self._jobs.pop(token, None)

    def __len__(self) -> int:
        return len(self._jobs)


jobs = JobStore()
