from dataclasses import dataclass


@dataclass
class Item:
    sku: str
    name: str
    price_cents: int
    qty: int = 0
