"""Reference solution used only to validate the hidden tests."""

import time
from typing import Callable


class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float, clock: Callable[[], float] = time.monotonic) -> None:
        if capacity <= 0 or refill_rate <= 0:
            raise ValueError("capacity and refill_rate must be positive")
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._clock = clock
        self._tokens = float(capacity)
        self._last = clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        self._last = now
        if elapsed > 0:
            self._tokens = min(float(self.capacity), self._tokens + elapsed * self.refill_rate)

    def try_consume(self, n: int = 1) -> bool:
        if n <= 0:
            raise ValueError("n must be positive")
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def available(self) -> float:
        self._refill()
        return self._tokens

    def time_until(self, n: int = 1) -> float:
        if n <= 0 or n > self.capacity:
            raise ValueError("invalid n")
        self._refill()
        return max(0.0, (n - self._tokens) / self.refill_rate)
