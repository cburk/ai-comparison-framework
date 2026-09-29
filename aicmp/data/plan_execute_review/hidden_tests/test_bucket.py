import pytest

from bucket import TokenBucket


class FakeClock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make(capacity=10, rate=2.0):
    c = FakeClock()
    return TokenBucket(capacity, rate, clock=c), c


def test_starts_full():  # AC1
    b, _ = make()
    assert b.available() == 10


def test_consume_success_deducts():  # AC2
    b, _ = make()
    assert b.try_consume(3) is True
    assert b.available() == pytest.approx(7)


def test_consume_default_is_one():  # AC2
    b, _ = make()
    assert b.try_consume() is True
    assert b.available() == pytest.approx(9)


def test_consume_failure_deducts_nothing():  # AC2
    b, _ = make(capacity=5)
    assert b.try_consume(4) is True
    assert b.try_consume(2) is False
    assert b.available() == pytest.approx(1)


def test_consume_exact_amount():  # AC2
    b, _ = make(capacity=5)
    assert b.try_consume(5) is True
    assert b.available() == pytest.approx(0)


def test_refill_over_time():  # AC3
    b, c = make(capacity=10, rate=2.0)
    b.try_consume(10)
    c.advance(1.5)
    assert b.available() == pytest.approx(3.0)


def test_refill_capped_at_capacity():  # AC3
    b, c = make(capacity=10, rate=2.0)
    b.try_consume(4)
    c.advance(1000)
    assert b.available() == pytest.approx(10)


def test_refill_is_incremental_across_calls():  # AC3
    b, c = make(capacity=10, rate=1.0)
    b.try_consume(10)
    c.advance(0.5)
    assert b.try_consume(1) is False
    c.advance(0.5)
    assert b.try_consume(1) is True
    assert b.available() == pytest.approx(0)


def test_available_returns_float():  # AC4
    b, c = make()
    b.try_consume(1)
    c.advance(0.25)
    assert isinstance(b.available(), float)


def test_time_until_zero_when_available():  # AC5
    b, _ = make()
    assert b.time_until(5) == 0.0


def test_time_until_positive():  # AC5
    b, _ = make(capacity=10, rate=2.0)
    b.try_consume(10)
    assert b.time_until(4) == pytest.approx(2.0)


def test_time_until_accounts_for_elapsed_time():  # AC5
    b, c = make(capacity=10, rate=2.0)
    b.try_consume(10)
    c.advance(1.0)
    assert b.time_until(4) == pytest.approx(1.0)


def test_time_until_exceeding_capacity_raises():  # AC5
    b, _ = make(capacity=10)
    with pytest.raises(ValueError):
        b.time_until(11)


@pytest.mark.parametrize("cap,rate", [(0, 1.0), (-1, 1.0), (5, 0), (5, -2.0)])
def test_invalid_constructor_args(cap, rate):  # AC6
    with pytest.raises(ValueError):
        TokenBucket(cap, rate)


@pytest.mark.parametrize("n", [0, -1])
def test_invalid_n(n):  # AC6
    b, _ = make()
    with pytest.raises(ValueError):
        b.try_consume(n)
    with pytest.raises(ValueError):
        b.time_until(n)


def test_clock_backwards_does_not_refill_or_lose_tokens():  # AC7
    b, c = make(capacity=10, rate=2.0)
    b.try_consume(6)
    c.advance(-50)
    assert b.available() == pytest.approx(4)
    assert b.try_consume(4) is True
    assert b.available() == pytest.approx(0)
