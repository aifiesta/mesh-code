"""Correctness checks the models never saw.

    python3 check.py LEVEL PROJECT_DIR BEFORE_DIR

LEVEL is 1 to 5. Prints one line per check (PASS / FAIL) and a final verdict.
The checks were written before any run and live outside the projects.
"""
import filecmp
import importlib
import os
import subprocess
import sys

level, project, before = int(sys.argv[1]), os.path.abspath(sys.argv[2]), os.path.abspath(sys.argv[3])
sys.dont_write_bytecode = True
sys.path.insert(0, project)
results = []


def check(name, fn):
    try:
        out = fn()
        ok = out is None or out is True
        results.append((ok, name, "" if ok else f"got {out!r}"))
    except Exception as e:                      # a missing function is a failed check, not a crash
        results.append((False, name, f"{type(e).__name__}: {e}"))


def eq(got, want):
    return True if got == want else f"{got!r}, wanted {want!r}"


def raises(exc, fn):
    try:
        fn()
    except exc:
        return True
    except Exception as e:
        return f"raised {type(e).__name__} instead of {exc.__name__}"
    return f"did not raise {exc.__name__}"


def changed_files():
    """Files that differ from the starting project (relative paths)."""
    out = []
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache", ".git", ".mesh")]
        for f in files:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, project)
            q = os.path.join(before, rel)
            if not os.path.exists(q) or not filecmp.cmp(p, q, shallow=False):
                out.append(rel)
    for root, dirs, files in os.walk(before):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), before)
            if not os.path.exists(os.path.join(project, rel)):
                out.append(rel + " (deleted)")
    return sorted(out)


def suite():
    r = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "tests"],
                       cwd=project, capture_output=True, text=True)
    tail = (r.stderr.strip().splitlines() or ["no output"])
    ran = next((l for l in tail if l.startswith("Ran ")), "")
    return r.returncode == 0, f"{ran} / {tail[-1]}"


def suite_passes():
    ok, text = suite()
    print("   project's own tests:", text)
    return True if ok else text


def test_count():
    return int(suite()[1].split()[1])


files = changed_files()
print("files changed or added:", files or "none")

if level == 1:
    check("no file was changed", lambda: eq(files, []))

if level == 2:
    from shop.price import line_total, order_total
    check("project's own tests pass", suite_passes)
    check("9 items: no discount", lambda: eq(line_total(3.0, 9), 27.0))
    check("10 items: 15% off", lambda: eq(line_total(3.0, 10), 25.5))
    check("11 items: 15% off", lambda: eq(line_total(1.0, 11), 9.35))
    check("order total with tax", lambda: eq(order_total([(4.0, 5), (2.0, 20)]), 58.32))
    check("only shop/price.py was changed", lambda: eq(files, ["shop/price.py"]))

if level == 3:
    from shop.inventory import Inventory
    from shop.orders import place_order
    price = importlib.import_module("shop.price")
    check("project's own tests pass", suite_passes)
    check("SAVE10 takes 10% off", lambda: eq(round(price.apply_coupon(100.0, "SAVE10"), 2), 90.0))
    check("FLAT5 takes 5.00 off", lambda: eq(round(price.apply_coupon(20.0, "FLAT5"), 2), 15.0))
    check("FLAT5 never goes below zero", lambda: eq(round(price.apply_coupon(3.0, "FLAT5"), 2), 0.0))
    check("unknown code raises ValueError", lambda: raises(ValueError, lambda: price.apply_coupon(10.0, "NOPE")))
    check("order with SAVE10: 20.00 -> 18.00 -> 19.44 with tax",
          lambda: eq(place_order(Inventory({"notebook": 9}), [("notebook", 5)], coupon="SAVE10").total, 19.44))
    check("order with FLAT5: 20.00 -> 15.00 -> 16.20 with tax",
          lambda: eq(place_order(Inventory({"notebook": 9}), [("notebook", 5)], coupon="FLAT5").total, 16.2))
    check("order without a coupon is unchanged",
          lambda: eq(place_order(Inventory({"notebook": 9}), [("notebook", 5)]).total, 21.6))
    check("order with an unknown coupon raises ValueError",
          lambda: raises(ValueError, lambda: place_order(Inventory({"notebook": 9}), [("notebook", 5)], coupon="NOPE")))
    check("tests were added (15 at the start)", lambda: True if test_count() > 15 else test_count())

if level == 4:
    from shop.inventory import Inventory
    from shop.orders import cancel_order, place_order
    from shop.report import daily_summary
    check("project's own tests pass", suite_passes)

    def sold_out_then_cancelled():
        inv = Inventory({"lamp": 2, "pen": 5})
        order = place_order(inv, [("lamp", 2), ("pen", 1)])
        cancel_order(inv, order)
        return eq((inv.available("lamp"), inv.available("pen")), (2, 5))
    check("stock comes back after cancelling an order that sold an item out", sold_out_then_cancelled)

    def release_after_zero():
        inv = Inventory({"bag": 1})
        inv.reserve("bag", 1)
        inv.release("bag", 1)
        return eq(inv.available("bag"), 1)
    check("release works on an item at zero", release_after_zero)

    def revenue_excludes_cancelled():
        inv = Inventory({"notebook": 20, "pen": 50})
        a = place_order(inv, [("notebook", 5)], order_id=1)      # 21.60
        b = place_order(inv, [("pen", 20)], order_id=2)          # 27.54
        cancel_order(inv, b)
        return eq(daily_summary([a, b])["revenue"], 21.6)
    check("revenue leaves out cancelled orders", revenue_excludes_cancelled)
    check("regression tests were added (15 at the start)", lambda: True if test_count() > 15 else test_count())

if level == 5:
    from shop.inventory import InsufficientStock, Inventory
    orders = importlib.import_module("shop.orders")
    from shop.report import daily_summary
    place_order = orders.place_order
    check("project's own tests pass", suite_passes)

    def all_or_nothing():
        inv = Inventory({"pen": 5, "lamp": 1})
        r = raises(InsufficientStock, lambda: place_order(inv, [("pen", 3), ("lamp", 2)]))
        return r if r is not True else eq((inv.available("pen"), inv.available("lamp")), (5, 1))
    check("a refused order leaves no stock reserved", all_or_nothing)

    def same_item_twice():
        inv = Inventory({"pen": 5})
        r = raises(InsufficientStock, lambda: place_order(inv, [("pen", 3), ("pen", 3)]))
        return r if r is not True else eq(inv.available("pen"), 5)
    check("the same item on two lines is checked together", same_item_twice)

    def good_order_still_works():
        inv = Inventory({"pen": 50, "notebook": 20})
        o = place_order(inv, [("notebook", 5), ("pen", 20)])
        return eq((o.subtotal, o.total, inv.available("pen"), inv.available("notebook")), (45.5, 49.14, 30, 15))
    check("a good order is priced and reserved as before", good_order_still_works)

    def refund():
        inv = Inventory({"pen": 20})
        o = place_order(inv, [("pen", 12)])                      # 15.30 / 16.52
        orders.refund_line(inv, o, "pen", 4)                     # 8 left: 12.00 / 12.96
        return eq((o.subtotal, o.total, inv.available("pen"), round(o.refunded, 2)), (12.0, 12.96, 12, 3.56))
    check("partial refund: stock back, discount re-evaluated, money tracked", refund)

    def refund_too_much():
        inv = Inventory({"pen": 20})
        o = place_order(inv, [("pen", 12)])
        r = raises(ValueError, lambda: orders.refund_line(inv, o, "pen", 13))
        return r if r is not True else eq((inv.available("pen"), o.total), (8, 16.52))
    check("refunding more than was ordered raises ValueError and changes nothing", refund_too_much)

    def full_refund():
        inv = Inventory({"pen": 20, "lamp": 2})
        o = place_order(inv, [("pen", 2), ("lamp", 1)])          # 21.00 / 22.68
        orders.refund_line(inv, o, "pen", 2)
        first = o.status
        orders.refund_line(inv, o, "lamp", 1)
        return eq((first, o.status, o.total, round(o.refunded, 2), inv.available("pen"), inv.available("lamp")),
                  ("placed", "refunded", 0.0, 22.68, 20, 2))
    check("an order with every line refunded becomes 'refunded'", full_refund)

    def report():
        inv = Inventory({"pen": 40, "lamp": 2, "notebook": 9})
        a = place_order(inv, [("pen", 12)], order_id=1)          # 16.52
        b = place_order(inv, [("lamp", 1)], order_id=2)          # 19.44
        c = place_order(inv, [("notebook", 5)], order_id=3)      # 21.60
        orders.refund_line(inv, a, "pen", 4)                     # a is now 12.96, 3.56 given back
        orders.refund_line(inv, b, "lamp", 1)                    # b fully refunded, 19.44 given back
        s = daily_summary([a, b, c])
        return eq((s["revenue"], round(s["refunded"], 2), s["refunded_orders"]), (34.56, 23.0, 1))
    check("report: net revenue, money refunded, refunded orders", report)
    check("tests were added (15 at the start)", lambda: True if test_count() > 15 else test_count())

passed = sum(1 for ok, *_ in results if ok)
for ok, name, note in results:
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{note}]" if note else ""))
print(f"VERDICT level {level}: {passed} of {len(results)} checks passed")
