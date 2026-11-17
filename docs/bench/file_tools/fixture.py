"""Deterministic scratch project for live runs: one large module, a small
package with tests, and a big lockfile."""
import json
from pathlib import Path

KINDS = ["invoice", "refund", "shipment", "coupon", "ledger", "audit", "tax", "stock"]


def big_module() -> str:
    out = ['"""Order processing rules."""', "from dataclasses import dataclass", "", "",
           "@dataclass", "class Order:", "    total: float", "    days_late: int = 0",
           "    region: str = \"eu\"", "    items: int = 1", "", ""]
    for i in range(1, 181):
        kind = KINDS[i % len(KINDS)]
        if i == 117:
            out += [
                "def compute_late_fee(order: Order) -> float:",
                '    """Fee charged when an order is paid after its due date."""',
                "    if order.days_late <= 0:",
                "        return 0.0",
                "    rate = 0.015 if order.region == \"eu\" else 0.02",
                "    fee = order.total * rate * order.days_late",
                "    # the fee can never exceed a quarter of the order",
                "    return round(min(fee, order.total * 0.25), 2)",
                "", ""]
            continue
        out += [
            f"def {kind}_rule_{i}(order: Order) -> float:",
            f'    """Rule {i}: adjust the {kind} amount for an order."""',
            f"    base = order.total * {round(0.01 * (i % 17 + 1), 2)}",
            f"    if order.items > {i % 9 + 1}:",
            f"        base += {i % 13} * order.items",
            f"    if order.region == \"{'eu' if i % 2 else 'us'}\":",
            f"        base *= {round(1 + (i % 7) / 100, 2)}",
            f"    return round(base, 2)",
            "", ""]
    return "\n".join(out)


def lockfile() -> str:
    pkgs = {"": {"name": "shop", "version": "1.0.0"}}
    for i in range(900):
        name = "lodash" if i == 611 else f"pkg-{i:04d}"
        pkgs[f"node_modules/{name}"] = {
            "version": "4.17.21" if name == "lodash" else f"{i % 9}.{i % 23}.{i % 7}",
            "resolved": f"https://registry.npmjs.org/{name}/-/{name}-{i}.tgz",
            "integrity": "sha512-" + ("ab12cd34ef" * 8)[: 80] + f"{i:04d}==",
            "license": "MIT",
        }
    return json.dumps({"name": "shop", "lockfileVersion": 3, "packages": pkgs}, indent=2) + "\n"


def write(ws: Path) -> None:
    (ws / "orders").mkdir(parents=True, exist_ok=True)
    (ws / "tests").mkdir(exist_ok=True)
    (ws / "orders" / "__init__.py").write_text("")
    (ws / "orders" / "engine.py").write_text(big_module())
    (ws / "tests" / "test_fee.py").write_text(
        "from orders.engine import Order, compute_late_fee\n\n\n"
        "def test_no_fee_when_on_time():\n    assert compute_late_fee(Order(100.0, 0)) == 0.0\n\n\n"
        "def test_fee_is_capped():\n    assert compute_late_fee(Order(100.0, 400)) == 25.0\n\n\n"
        "def test_eu_rate():\n    assert compute_late_fee(Order(200.0, 2)) == 6.0\n")
    (ws / "package.json").write_text(json.dumps(
        {"name": "shop", "version": "1.0.0", "dependencies": {"lodash": "^4.17.0"}}, indent=2) + "\n")
    (ws / "package-lock.json").write_text(lockfile())
    (ws / "README.md").write_text("# shop\n\nOrder rules live in orders/engine.py. Run tests with `python -m pytest -q`.\n")


if __name__ == "__main__":
    import sys
    write(Path(sys.argv[1]))


def noisy_tests(ws: Path) -> None:
    """300 passing tests that print a line each, then one real failure whose
    details land at the very end of the output."""
    (ws / "tests" / "test_bulk.py").write_text(
        "import pytest\nfrom orders.engine import Order, compute_late_fee\n\n\n"
        "@pytest.mark.parametrize('days', range(1, 301))\n"
        "def test_fee_is_never_negative_for_late_orders(days):\n"
        "    assert compute_late_fee(Order(50.0, days)) >= 0\n")
    p = ws / "orders" / "engine.py"
    p.write_text(p.read_text().replace("order.total * 0.25), 2)", "order.total * 0.30), 2)"))
