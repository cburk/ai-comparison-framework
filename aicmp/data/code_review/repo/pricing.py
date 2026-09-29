"""Pricing helpers for the storefront."""


def apply_discount(price: float, percent: float) -> float:
    """Return `price` reduced by `percent` percent (e.g. 20 -> 20% off)."""
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")
    return price - price * percent


def add_tax(price: float, rate: float) -> float:
    """Return `price` with tax `rate` (e.g. 0.08 for 8%) applied, rounded to cents."""
    return round(price * (1 + rate), 2)


def cart_total(items: list[dict]) -> float:
    """Sum price * quantity for each item in the cart."""
    return round(sum(i["price"] * i["quantity"] for i in items), 2)
