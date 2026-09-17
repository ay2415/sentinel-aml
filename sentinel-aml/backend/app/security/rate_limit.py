"""Sliding-window rate limiter.

Local: in-process (correct for a single replica). Production: the same interface backed by
Azure Cache for Redis (sorted sets / INCR+EXPIRE), because in-memory state is per-replica.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class InMemoryRateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, 0
            q.append(now)
            return True, self.limit - len(q)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
