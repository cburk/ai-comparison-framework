# Task: TokenBucket rate limiter

Implement the class `TokenBucket` in `bucket.py` (a stub is provided; keep the
module name and the public interface exactly as below). Standard library only.

## Interface

```python
class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float,
                 clock: Callable[[], float] = time.monotonic) -> None: ...
    def try_consume(self, n: int = 1) -> bool: ...
    def available(self) -> float: ...
    def time_until(self, n: int = 1) -> float: ...
```

`clock` returns the current time in seconds; it is injectable so tests can fake time.

## Acceptance criteria

1. A new bucket starts full (`available() == capacity`).
2. `try_consume(n)` returns `True` and removes `n` tokens if at least `n` are
   available; otherwise it returns `False` and removes nothing.
3. Tokens refill continuously at `refill_rate` tokens per second, measured with
   `clock`, and never exceed `capacity`.
4. `available()` returns the current token count as a float, including refill up to now.
5. `time_until(n)` returns the seconds until `n` tokens will be available
   (`0.0` if they already are). It raises `ValueError` if `n > capacity`.
6. `ValueError` is raised for `capacity <= 0` or `refill_rate <= 0` in the
   constructor, and for `n <= 0` in `try_consume` and `time_until`.
7. If `clock` ever goes backwards, no tokens are added or removed for that step
   (the bucket must not lose tokens or refill spuriously).
