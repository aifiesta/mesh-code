"""A small inventory project used as the benchmark workspace."""
from pathlib import Path

FILES = {
    "README.md": "# inventory\n\nTiny stock tracker.\n\n    python -m inventory.cli stock.csv\n\nPrints a stock report.\n",
    "inventory/__init__.py": "",
    "inventory/models.py": '''from dataclasses import dataclass


@dataclass
class Item:
    sku: str
    name: str
    qty: int
    price: float


def total_value(items):
    return sum(i.qty * i.price for i in items)
''',
    "inventory/store.py": '''import csv

from .models import Item, total_value


class Store:
    def __init__(self):
        self.items = {}

    def add(self, item):
        if item.sku in self.items:
            self.items[item.sku].qty += item.qty
        else:
            self.items[item.sku] = item

    def remove(self, sku, qty):
        item = self.items[sku]
        if qty > item.qty:
            raise ValueError(f"only {item.qty} of {sku} in stock")
        item.qty -= qty

    def value(self):
        return total_value(self.items.values())

    @classmethod
    def from_csv(cls, path):
        store = cls()
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                store.add(Item(row["sku"], row["name"], int(row["qty"]), float(row["price"])))
        return store
''',
    "inventory/report.py": '''from .models import total_value


def average_price(items):
    items = list(items)
    return sum(i.price for i in items) / len(items)


def build_report(store, min_qty=0):
    lines = []
    lines.append("STOCK REPORT")
    lines.append("=" * 30)
    items = sorted(store.items.values(), key=lambda i: i.sku)
    shown = []
    for item in items:
        if item.qty < min_qty:
            continue
        shown.append(item)
        flag = ""
        if item.qty == 0:
            flag = " (OUT)"
        elif item.qty < 5:
            flag = " (LOW)"
        lines.append(f"{item.sku:<8}{item.name:<14}{item.qty:>5}{flag}")
    lines.append("-" * 30)
    lines.append(f"items: {len(shown)}")
    lines.append(f"value: {total_value(shown):.2f}")
    if shown:
        lines.append(f"avg price: {average_price(shown):.2f}")
    low = [i.sku for i in shown if 0 < i.qty < 5]
    out = [i.sku for i in shown if i.qty == 0]
    if low:
        lines.append("low: " + ", ".join(low))
    if out:
        lines.append("out: " + ", ".join(out))
    return "\\n".join(lines)
''',
    "inventory/cli.py": '''import argparse

from .report import build_report
from .store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(prog="inventory")
    parser.add_argument("csv")
    args = parser.parse_args(argv)
    store = Store.from_csv(args.csv)
    print(build_report(store))


if __name__ == "__main__":
    main()
''',
    "tests/__init__.py": "",
    "tests/conftest.py": "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n",
    "tests/test_store.py": '''import pytest

from inventory.models import Item, total_value
from inventory.store import Store


def make():
    s = Store()
    s.add(Item("A1", "bolt", 10, 0.5))
    s.add(Item("B2", "nut", 3, 0.25))
    return s


def test_add_merges_qty():
    s = make()
    s.add(Item("A1", "bolt", 5, 0.5))
    assert s.items["A1"].qty == 15


def test_remove_too_many():
    with pytest.raises(ValueError):
        make().remove("B2", 4)


def test_value():
    assert make().value() == pytest.approx(5.75)
    assert total_value([]) == 0
''',
    "tests/test_report.py": '''from inventory.models import Item
from inventory.report import build_report
from inventory.store import Store


def test_report_flags():
    s = Store()
    s.add(Item("A1", "bolt", 10, 0.5))
    s.add(Item("B2", "nut", 3, 0.25))
    s.add(Item("C3", "gear", 0, 4.0))
    r = build_report(s)
    assert "(LOW)" in r and "(OUT)" in r
    assert "low: B2" in r and "out: C3" in r
    assert "value: 5.75" in r
''',
    "stock.csv": "sku,name,qty,price\nA1,bolt,10,0.5\nB2,nut,3,0.25\nC3,gear,0,4.0\n",
}


def write(root: Path):
    for rel, text in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
