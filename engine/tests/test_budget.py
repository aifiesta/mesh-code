import json

import pytest

from meshharness.core import budget as budget_mod
from meshharness.core.budget import Budget


class Clock:
    def __init__(self, t): self.t = t
    def __call__(self): return self.t


def _b(tmp_path, clock, **cfg):
    base = {"monthly_budget_usd": 10}
    base.update(cfg)
    return Budget(base, tmp_path / "budget.json", now=clock)


def _days_left(clock):
    import calendar
    from datetime import datetime
    d = datetime.fromtimestamp(clock()).date()
    return calendar.monthrange(d.year, d.month)[1] - d.day + 1


def test_todays_share_is_whats_left_over_the_days_left_and_holds_when_used_up(tmp_path):
    clock = Clock(1_792_000_000)
    b = _b(tmp_path, clock)
    share = b.daily_limit()
    assert share == pytest.approx(10 / _days_left(clock))
    b.record("escalate", round(share * 0.9, 2))
    assert b.daily_limit() == share, "today's own spend does not shrink today's share"
    assert b.check("escalate", share) .scope == "day"
    b.override_today()
    assert b.check("escalate", share) is None
    snap = b.snapshot()
    assert snap["day"]["pct"] in (89, 90) and snap["day"]["override"] is True
    clock.t += 86400
    if _days_left(clock) > 0 and b.snapshot()["month"]["spent"] > 0:
        assert b.daily_limit() == pytest.approx((10 - snap["month"]["spent"]) / _days_left(clock), abs=1e-3)


def test_monthly_limit_holds_everything_and_a_new_month_resets(tmp_path):
    clock = Clock(1_792_000_000)
    b = _b(tmp_path, clock)
    b.override_today()
    b.record("audit", 9.99)
    assert b.check("audit", 0.05).scope == "month"
    clock.t += 40 * 86400
    assert b.check("audit", 0.05) is None and b.snapshot()["month"]["spent"] == 0


def test_warnings_fire_once_per_threshold(tmp_path):
    b = _b(tmp_path, Clock(1_792_000_000))
    share = b.daily_limit()
    assert b.record("audit", share * 0.55) == [50]
    assert b.record("audit", share * 0.30) == [80]
    assert b.record("audit", share * 0.01) == []
    assert 100 in b.record("audit", share * 0.20)


def test_key_cap_holds_until_cleared_and_the_ledger_survives_a_reload(tmp_path):
    clock = Clock(1_792_000_000)
    b = _b(tmp_path, clock)
    b.record("pinned", 0.2)
    b.set_key_cap(True)
    assert b.check("pinned", 0.01).scope == "key_cap"
    again = _b(tmp_path, clock)
    assert again.snapshot()["key_cap"] is True and again.snapshot()["month"]["spent"] == 0.2
    again.set_key_cap(False)
    assert again.check("pinned", 0.01) is None
    assert json.loads((tmp_path / "budget.json").read_text())["spent_usd"] == 0.2


def test_estimate_uses_the_catalog_per_million_prices_and_the_discount():
    models = [{"id": "a/b", "pricing": {"prompt_usd_per_1m": 2, "completion_usd_per_1m": 10}},
              {"id": "c/d", "pricing": {"prompt_usd_per_1m": 4, "prompt_usd_per_1m_discounted": "3.6",
                                        "completion_usd_per_1m": 20}},
              {"id": "local/x", "local": True, "pricing": {"prompt_usd_per_1m": 0}}]
    assert budget_mod.estimate_hop_usd("a/b", 100_000, models, 2000) == 0.22
    assert budget_mod.estimate_hop_usd("c/d", 100_000, models, 2000) == 0.4
    assert budget_mod.estimate_hop_usd("local/x", 100_000, models) == 0.0
    assert budget_mod.estimate_hop_usd("nope/none", 100_000, models) == 0.0


def test_a_hold_from_the_estimate_alone_is_shown_and_can_be_lifted(tmp_path):
    clock = Clock(1_792_000_000)
    b = _b(tmp_path, clock)
    assert b.check("pinned", b.daily_limit() + 0.01).scope == "day"
    snap = b.snapshot()
    assert snap["day"]["pct"] == 0 and snap["held"] == "day"
    b.override_today()
    assert b.snapshot()["held"] is None and b.check("pinned", b.daily_limit() + 0.01) is None
    clock.t += 86400
    assert b.snapshot()["held"] is None


def test_a_tiny_monthly_limit_still_paces_the_day(tmp_path):
    b = _b(tmp_path, Clock(1_792_000_000), monthly_budget_usd=0.05)
    assert 0 < b.daily_limit() < 0.005
    assert b.check("pinned", 0.01).scope == "day"


def test_a_spent_month_reads_as_a_full_day_not_no_limit(tmp_path):
    clock = Clock(1_792_000_000)
    b = _b(tmp_path, clock, monthly_budget_usd=1)
    b.override_today()
    b.record("pinned", 1.0)
    clock.t += 86400
    day = b.snapshot()["day"]
    assert day["limit"] == 0 and day["pct"] == 100


@pytest.mark.parametrize("raw", [
    {"month": "2026-10", "spent_usd": None},
    {"month": "2026-10", "days": []},
    {"month": "2026-10", "days": {"2026-10-01": {"spent": "x"}}},
    {"month": "2026-10", "by_kind": {"pinned": "1"}},
    {"month": 7},
    {"month": "2026-10", "held": "day"},
    {"month": "2026-10", "override_day": {"x": 1}},
])
def test_a_malformed_ledger_loads_fresh_instead_of_breaking_the_snapshot(tmp_path, raw):
    (tmp_path / "budget.json").write_text(json.dumps(raw))
    snap = _b(tmp_path, Clock(1_792_000_000)).snapshot()
    assert snap["month"]["spent"] == 0


def test_a_passing_check_clears_the_hold(tmp_path):
    b = _b(tmp_path, Clock(1_792_000_000))
    assert b.check("pinned", 5).scope == "day" and b.snapshot()["held"] == "day"
    b.cfg["monthly_budget_usd"] = 1000
    assert b.check("pinned", 5) is None and b.snapshot()["held"] is None
