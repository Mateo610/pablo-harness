from inventory.models import Item


class Store:
    def __init__(self) -> None:
        self.items: dict[str, Item] = {}

    def add(self, item: Item) -> None:
        self.items[item.sku] = item

    def remove(self, sku: str, qty: int) -> None:
        item = self.items[sku]
        item.qty -= qty

    def find(self, text: str) -> list[Item]:
        return [i for i in self.items.values() if text in i.name]

    def value_cents(self) -> int:
        return sum(i.price_cents for i in self.items.values())
