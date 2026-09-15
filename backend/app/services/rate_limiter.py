"""Minimal rate limiting (see docs/ARCHITECTURE.md — usage/account architecture).

A fixed-window counter per client, in memory. This is abuse/cost protection
for the raw API today, not the real free/premium usage-quota system — that
needs accounts and a database (Milestone 6/11) so limits can be per-user and
per-plan instead of per-IP. Kept behind this same interface so it can be
swapped for that later without touching the route handlers.
"""

import threading
import time
from dataclasses import dataclass
from typing import Dict


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: Dict[str, list] = {}
        self._lock = threading.Lock()

    def check(self, client_key: str) -> RateLimitResult:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(client_key, []) if now - t < self.window_seconds]
            if len(hits) >= self.max_requests:
                oldest = min(hits)
                retry_after = int(self.window_seconds - (now - oldest)) + 1
                self._hits[client_key] = hits
                return RateLimitResult(allowed=False, retry_after_seconds=retry_after)
            hits.append(now)
            self._hits[client_key] = hits
            return RateLimitResult(allowed=True)
