"""In-memory rate limiting: per-IP sliding hourly window + a global daily plan cap.

Every uncached plan costs ~6-14 paid Google calls, so a public deployment needs a
wallet guard. In-memory state is fine for a single instance; swap for Redis/KV if the
backend ever scales out.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, per_ip_per_hour: int, daily_cap: int):
        self._per_ip = per_ip_per_hour
        self._daily_cap = daily_cap
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._day: str | None = None
        self._day_count = 0

    def check(self, ip: str) -> str | None:
        """Record one plan attempt. Returns an error code, or None when allowed."""
        now = time.time()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        if day != self._day:
            self._day = day
            self._day_count = 0
            self._hits.clear()  # also bounds memory: at most one day of IPs
        if self._day_count >= self._daily_cap:
            return "DAILY_CAP"
        window = self._hits[ip]
        while window and now - window[0] > 3600:
            window.popleft()
        if len(window) >= self._per_ip:
            return "RATE_LIMITED"
        window.append(now)
        self._day_count += 1
        return None
