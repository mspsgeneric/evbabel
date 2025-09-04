# evtranslator/relay/backoff.py
from __future__ import annotations
import random, time
from dataclasses import dataclass

@dataclass
class BackoffCfg:
    attempts: int = 3
    base: float = 0.3
    factor: float = 2.0
    max_delay: float = 2.0
    jitter_ms: int = 150

class ExponentialBackoff:
    def __init__(self, cfg: BackoffCfg):
        self.cfg = cfg
        self.try_n = 0
    def next_delay(self) -> float:
        d = min(self.cfg.base * (self.cfg.factor ** self.try_n), self.cfg.max_delay)
        j = random.uniform(0, self.cfg.jitter_ms / 1000.0)
        self.try_n += 1
        return d + j

class CircuitBreaker:
    def __init__(self, fail_threshold: int = 6, cooldown_sec: float = 30.0):
        self.fail_threshold = fail_threshold
        self.cooldown_sec = cooldown_sec
        self.fail_count = 0
        self.open_until = 0.0
    @property
    def is_open(self) -> bool:
        return time.monotonic() < self.open_until
    def on_success(self):
        self.fail_count = 0
    def on_failure(self):
        self.fail_count += 1
        if self.fail_count >= self.fail_threshold:
            self.open_until = time.monotonic() + self.cooldown_sec
            self.fail_count = 0
