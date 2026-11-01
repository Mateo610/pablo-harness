"""Prices are integer cents."""


def discount(price_cents: int, percent: int) -> int:
    return price_cents - price_cents * percent // 100


def bulk_price(price_cents: int, qty: int) -> int:
    """10% off each item when buying 10 or more."""
    if qty > 10:
        return discount(price_cents, 10) * qty
    return price_cents * qty
