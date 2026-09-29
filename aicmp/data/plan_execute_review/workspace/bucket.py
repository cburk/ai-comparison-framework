"""Token bucket rate limiter. See TASK.md."""

import time
from typing import Callable


class TokenBucket:
    def __init__(
        self,
        capacity: int,
        refill_rate: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        raise NotImplementedError

    def try_consume(self, n: int = 1) -> bool:
        raise NotImplementedError

    def available(self) -> float:
        raise NotImplementedError

    def time_until(self, n: int = 1) -> float:
        raise NotImplementedError
