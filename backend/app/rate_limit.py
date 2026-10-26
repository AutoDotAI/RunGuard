"""Small, single-API-process public-demo rate limits.

The public Compose topology runs exactly one API process. Limits reset on
restart; they are an abuse brake, not an account lockout or billing quota.
"""

import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable

from .store import Problem


class RateLimited(Problem):
    def __init__(self, retry_after: int):
        super().__init__(429, "RATE_LIMITED", "Too many requests; try again later")
        self.retry_after = retry_after


class RateLimiter:
    def __init__(self, *, enabled: bool | None = None, clock: Callable[[], float] = time.monotonic):
        self.enabled = (os.environ.get("RUNGUARD_PUBLIC_MODE") == "1" or
                        os.environ.get("RUNGUARD_RATE_LIMIT_ENABLED") == "1") if enabled is None else enabled
        self.clock = clock
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], deque[float]] = {}

    def enforce(self, action: str, key: str) -> None:
        if not self.enabled:
            return
        limit, period = {"register": (5, 300), "login": (10, 300),
                         "run": (10, 60)}[action]
        now = self.clock()
        with self._lock:
            bucket = self._buckets.setdefault((action, key), deque())
            while bucket and now - bucket[0] >= period:
                bucket.popleft()
            if len(bucket) >= limit:
                raise RateLimited(max(1, math.ceil(period - (now - bucket[0]))))
            bucket.append(now)
