# evtranslator/relay/ratelimit.py
from __future__ import annotations
import asyncio, time

class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float):
        self.rate = float(rate_per_sec)
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.last = time.perf_counter()
    async def acquire(self):
        while True:
            now = time.perf_counter()
            elapsed = now - self.last
            self.last = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return
            await asyncio.sleep(max(0.0, (1.0 - self.tokens) / self.rate))
