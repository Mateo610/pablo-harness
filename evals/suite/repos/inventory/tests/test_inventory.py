from inventory.csvio import dump_csv, load_csv
from inventory.models import Item
from inventory.pricing import bulk_price, discount
from inventory.store import Store


def make() -> Store:
    store = Store()
    store.add(Item("B1", "Bolt", 25, 100))
    store.add(Item("N1", "Nut", 10, 40))
    return store


def test_store() -> None:
    store = make()
    store.remove("B1", 10)
    assert store.items["B1"].qty == 90
    assert [i.sku for i in store.find("Bo")] == ["B1"]


def test_pricing() -> None:
    assert discount(1000, 25) == 750
    assert bulk_price(100, 2) == 200
    assert bulk_price(100, 20) == 1800


def test_csv_round_trip() -> None:
    items = [Item("B1", "Bolt", 25, 100)]
    assert load_csv(dump_csv(items)) == items
