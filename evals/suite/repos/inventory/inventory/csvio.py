import csv
import io

from inventory.models import Item


def load_csv(text: str) -> list[Item]:
    rows = csv.DictReader(io.StringIO(text))
    return [Item(r["sku"], r["name"], int(r["price_cents"]), int(r["qty"])) for r in rows]


def dump_csv(items: list[Item]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["sku", "name", "price_cents", "qty"])
    for i in items:
        writer.writerow([i.sku, i.name, i.price_cents, i.qty])
    return out.getvalue()
