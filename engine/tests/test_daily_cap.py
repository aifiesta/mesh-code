"""The daily share of a monthly plan: the rule (with a fake clock), the
ledger, and the gate at the start of a task.

The rule: today's share = (plan - spent this period before today) / days left
in the period, counting today, fixed when the day starts. Past it, cloud
models are off until tomorrow and work carries on, free, on the local model.
"""
import json
from datetime import date, datetime

import pytest
from fake_gateway import sse_turn
from test_local_session import LOOKUP, SMALL, StubLocal, _chat_requests, _read, _smart, llama  # noqa: F401

from meshharness.core.dailycap import DailyCap, FixedPlan, period_for
from meshharness.protocol import Event


class Clock:
    def __init__(self, y, m, d, hour=10):
        self.t = datetime(y, m, d, hour).timestamp()

    def __call__(self):
        return self.t

    def to(self, y, m, d, hour=10):
        self.t = datetime(y, m, d, hour).timestamp()


def _cap(tmp_path, plan=30, reset_day=1, clock=None, name="cap.json"):
    clock = clock or Clock(2026, 11, 1)
    cfg = {"monthly_plan_usd": plan, "plan_reset_day": reset_day}
    return DailyCap(cfg, tmp_path / name, now=clock), cfg, clock


# ---- the rule ---------------------------------------------------------------

def test_the_first_day_gets_an_even_share_and_spending_it_caps_the_day(tmp_path):
    cap, _, _ = _cap(tmp_path, plan=30)                 # November: 30 days
    assert cap.share() == pytest.approx(1.0)
    cap.record(0.4)
    snap = cap.snapshot()
    assert snap["used_pct"] == 40.0 and snap["left_pct"] == 60.0 and not snap["capped"]
    assert not cap.capped()
    cap.record(0.6)
    assert cap.capped() and cap.snapshot()["capped"] and cap.snapshot()["left_pct"] == 0.0


def test_the_share_is_fixed_for_the_day_while_you_spend(tmp_path):
    cap, _, _ = _cap(tmp_path, plan=30)
    before = cap.share()
    cap.record(0.9)
    assert cap.share() == before


def test_a_quiet_day_raises_tomorrows_share(tmp_path):
    cap, _, clock = _cap(tmp_path, plan=30)
    clock.to(2026, 11, 2)
    assert cap.share() == pytest.approx(30 / 29)
    assert cap.used_today() == 0 and not cap.capped()


def test_a_heavy_day_lowers_tomorrows_share(tmp_path):
    cap, _, clock = _cap(tmp_path, plan=30)
    cap.record(2.0)
    clock.to(2026, 11, 2)
    assert cap.share() == pytest.approx(28 / 29)
    assert cap.used_today() == 0 and not cap.capped()      # a new day starts fresh


def test_days_left_counts_today_up_to_the_reset_day(tmp_path):
    cap, _, _ = _cap(tmp_path, plan=10, reset_day=15, clock=Clock(2026, 11, 10))
    # Period Oct 15 – Nov 15: Nov 10..14 left, five days.
    assert cap.share() == pytest.approx(2.0)
    assert cap.snapshot()["month_resets_on"] == "2026-11-15"


def test_the_reset_day_starts_a_new_period_with_nothing_spent(tmp_path):
    cap, _, clock = _cap(tmp_path, plan=10, reset_day=15, clock=Clock(2026, 11, 14))
    cap.record(9.0)
    clock.to(2026, 11, 15)
    assert cap.used_today() == 0
    assert cap.share() == pytest.approx(10 / 30)            # Nov 15 – Dec 15


def test_periods_across_year_ends_and_short_months():
    assert period_for(date(2027, 1, 5), 10) == (date(2026, 12, 10), date(2027, 1, 10))
    assert period_for(date(2027, 2, 28), 28) == (date(2027, 2, 28), date(2027, 3, 28))
    assert period_for(date(2028, 2, 29), 28) == (date(2028, 2, 28), date(2028, 3, 28))
    assert period_for(date(2026, 12, 31), 1) == (date(2026, 12, 1), date(2027, 1, 1))
    # Out-of-range reset days are clamped to 1..28.
    assert period_for(date(2027, 2, 20), 31) == (date(2027, 1, 28), date(2027, 2, 28))
    assert period_for(date(2027, 2, 20), 0)[0] == date(2027, 2, 1)


def test_a_plan_change_mid_day_resets_todays_share_but_keeps_todays_spend(tmp_path):
    cap, cfg, clock = _cap(tmp_path, plan=30)
    cap.record(1.0)
    clock.to(2026, 11, 2)
    cap.record(0.5)
    cfg["monthly_plan_usd"] = 59
    # (59 - 1.0 spent before today) / 29 days = 2.0; today's 0.5 still counts.
    assert cap.share() == pytest.approx(2.0)
    assert cap.used_today() == pytest.approx(0.5)
    assert cap.snapshot()["used_pct"] == 25.0


def test_a_used_up_plan_keeps_the_cloud_off_for_the_rest_of_the_period(tmp_path):
    cap, _, clock = _cap(tmp_path, plan=10)
    cap.record(10.0)
    clock.to(2026, 11, 20)
    assert cap.share() == 0 and cap.capped() and cap.snapshot()["left_pct"] == 0.0


def test_the_hard_stop_is_at_twice_the_share(tmp_path):
    cap, _, _ = _cap(tmp_path, plan=30)
    cap.record(1.5)
    assert cap.capped() and not cap.past_hard_stop()
    cap.record(0.5)
    assert cap.past_hard_stop()


def test_off_when_the_plan_is_zero(tmp_path):
    cap, _, _ = _cap(tmp_path, plan=0)
    cap.record(5.0)
    assert not cap.enabled() and not cap.capped() and not cap.past_hard_stop()
    assert cap.snapshot() == {"enabled": False}


def test_what_the_app_shows_has_no_money_in_it(tmp_path):
    cap, _, _ = _cap(tmp_path, plan=30)
    cap.record(0.25)
    snap = cap.snapshot()
    assert set(snap) == {"enabled", "used_pct", "left_pct", "capped", "next_share_on",
                         "month_resets_on"}
    assert snap["next_share_on"] == "2026-11-02"


def test_two_windows_share_one_ledger(tmp_path):
    clock = Clock(2026, 11, 1)
    a, _, _ = _cap(tmp_path, plan=30, clock=clock)
    b, _, _ = _cap(tmp_path, plan=30, clock=clock)
    a.share(), b.share()
    a.record(0.6)
    b.record(0.6)
    assert a.used_today() == pytest.approx(1.2) and b.capped()


def test_a_broken_ledger_starts_over_instead_of_failing(tmp_path):
    (tmp_path / "cap.json").write_text("{not json")
    cap, _, _ = _cap(tmp_path, plan=30)
    assert cap.share() == pytest.approx(1.0) and not cap.capped()
    assert json.loads((tmp_path / "cap.json").read_text())["day"] == "2026-11-01"


def test_the_fixed_plan_comes_from_the_environment_then_the_plan_file(tmp_path, monkeypatch):
    monkeypatch.delenv("MESH_CODE_MONTHLY_PLAN_USD", raising=False)
    monkeypatch.delenv("MESH_CODE_PLAN_RESET_DAY", raising=False)
    src = FixedPlan(tmp_path / "plan.json")
    cap = DailyCap(src, tmp_path / "cap.json", now=Clock(2026, 11, 10))
    assert not cap.enabled()                                  # no plan: off
    (tmp_path / "plan.json").write_text(json.dumps({"monthly_plan_usd": 21, "plan_reset_day": 15}))
    assert cap.enabled() and cap.share() == pytest.approx(21 / 5)
    monkeypatch.setenv("MESH_CODE_MONTHLY_PLAN_USD", "42")
    assert cap.share() == pytest.approx(42 / 5)               # the environment wins
    (tmp_path / "plan.json").write_text("{broken")
    monkeypatch.delenv("MESH_CODE_MONTHLY_PLAN_USD")
    assert not cap.enabled()                                  # a broken file never blocks work


# ---- the gate in a chat ------------------------------------------------------

def _used_up(tmp_path, plan=30):
    cap, _, _ = _cap(tmp_path, plan=plan, name="used.json")
    cap.record(cap.share())
    return cap


def test_local_steps_are_never_counted(harness, gateway, llama, monkeypatch, tmp_path):
    (tmp_path / "price.py").write_text("def line_total(q, p):\n    return q * p\n")
    llama.script(_read("price.py"), sse_turn("It multiplies quantity by price."))
    h, _ = _smart(harness, monkeypatch, llama)
    cap, _, _ = _cap(tmp_path)
    h.session.cap = cap
    h.session.submit(LOOKUP)
    h.wait_for(Event.TURN_FINISHED)
    assert len(llama.requests) == 2 and _chat_requests(gateway) == []
    assert cap.used_today() == 0


def test_cloud_steps_are_counted(harness, gateway, tmp_path):
    gateway.script(sse_turn("done", cost=0.25))
    h = harness()
    cap, _, _ = _cap(tmp_path)
    h.session.cap = cap
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    assert cap.used_today() == pytest.approx(0.25)
    st = [e for e in h.of(Event.STATUS) if e["data"].get("daily_cap", {}).get("enabled")]
    assert st and st[-1]["data"]["daily_cap"]["used_pct"] == 25.0


def test_once_used_up_a_task_on_a_pinned_cloud_model_runs_on_the_local_model(
        harness, gateway, llama, monkeypatch, tmp_path):
    (tmp_path / "price.py").write_text("x = 1\n")
    llama.script(_read("price.py"), sse_turn("x is one"))
    h, _ = _smart(harness, monkeypatch, llama, route_mode="off")
    h.session.cap = _used_up(tmp_path)
    h.session.submit("Refactor price.py and add tests.")
    h.wait_for(Event.TURN_FINISHED)
    assert _chat_requests(gateway) == [] and len(llama.requests) == 2
    routed = h.of(Event.ROUTED)
    assert routed and routed[0]["data"]["local"] is True
    assert any("free local model" in e["data"]["message"] for e in h.of(Event.NOTICE))


def test_once_used_up_a_hard_task_under_smart_routing_runs_locally_too(
        harness, gateway, llama, monkeypatch, tmp_path):
    from test_local_session import STANDARD
    (tmp_path / "price.py").write_text("x = 1\n")
    llama.script(_read("price.py"), sse_turn("done"))
    h, _ = _smart(harness, monkeypatch, llama)
    h.session.cap = _used_up(tmp_path)
    h.session.submit(STANDARD)
    h.wait_for(Event.TURN_FINISHED)
    assert _chat_requests(gateway) == [] and len(llama.requests) == 2
    assert all(e["data"].get("local") for e in h.of(Event.ROUTED))   # no cloud pick named


def test_with_no_local_model_the_task_ends_plainly_and_nothing_goes_to_the_cloud(
        harness, gateway, tmp_path):
    gateway.script(sse_turn("should not be sent"))
    h = harness()
    h.session.cap = _used_up(tmp_path)
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    assert _chat_requests(gateway) == []
    fin = h.of(Event.TURN_FINISHED)[-1]["data"]
    assert fin["hops"] == 0 and not fin["model"]
    err = h.of(Event.ERROR)[-1]["data"]
    assert err["fatal"] and "Today's share of your plan is used" in err["message"]
    assert "until tomorrow" in err["message"]


def test_with_no_local_model_downloaded_the_task_says_why(harness, gateway, llama, monkeypatch, tmp_path):
    h, _ = _smart(harness, monkeypatch, llama, local=StubLocal(llama, ready=False))
    h.session.cap = _used_up(tmp_path)
    h.session.submit(LOOKUP)
    h.wait_for(Event.TURN_FINISHED)
    assert _chat_requests(gateway) == [] and llama.requests == []
    msg = h.of(Event.ERROR)[-1]["data"]["message"]
    assert "the free local model can't run here (Tiny is not downloaded yet)" in msg


def test_no_step_up_to_the_cloud_while_used_up(harness, gateway, llama, monkeypatch, tmp_path):
    # Normally an answer with no file read behind it steps up to the cloud.
    llama.script(sse_turn("a guess"))
    h, _ = _smart(harness, monkeypatch, llama)
    h.session.cap = _used_up(tmp_path)
    h.session.submit(LOOKUP)
    h.wait_for(Event.TURN_FINISHED)
    assert _chat_requests(gateway) == [] and len(llama.requests) == 1
    assert h.of(Event.ASSISTANT_MESSAGE)[-1]["data"]["text"] == "a guess"


def test_the_escalate_button_is_refused_while_used_up(harness, llama, monkeypatch, tmp_path):
    h, _ = _smart(harness, monkeypatch, llama)
    s = h.session
    s.cap = _used_up(tmp_path)
    s.state["messages"].append({"role": "user", "content": SMALL})
    s._route_turn(SMALL)
    assert s._apply_daily_cap() is None and s.state["_on_local"]
    assert not h.of(Event.ROUTED)[0]["data"].get("escalated")
    s.escalate(harder=True)
    s._take_route_request()
    assert s.state["_on_local"] and not s.state.get("_smart_escalations")
    assert any("not moving to the cloud" in e["data"]["message"] for e in h.of(Event.NOTICE))


def test_a_failing_local_server_ends_the_task_instead_of_going_to_the_cloud(
        harness, gateway, llama, monkeypatch, tmp_path):
    llama.script(["__drop__"])
    gateway.script(sse_turn("should not be sent"))
    h, local = _smart(harness, monkeypatch, llama)
    h.session.cap = _used_up(tmp_path)
    h.session.submit(LOOKUP)
    h.wait_for(Event.TURN_FINISHED, timeout=20)
    assert _chat_requests(gateway) == [] and local.recovered >= 1
    assert "the local model failed" in h.of(Event.ERROR)[-1]["data"]["message"]


def test_a_cloud_task_that_crosses_the_line_goes_on_but_stops_at_twice_the_share(
        harness, gateway, tmp_path):
    from meshharness.core.permissions import Mode
    (tmp_path / "a.txt").write_text("alpha")

    def step(n):
        return sse_turn(tool_calls=[{"id": f"r{n}", "name": "read_file",
                                     "arguments": json.dumps({"path": "a.txt"})}], cost=0.08)
    gateway.script(step(1), step(2), step(3), sse_turn("done", cost=0.08))
    h = harness(mode=Mode.BYPASS)
    cap, _, _ = _cap(tmp_path, plan=3)              # share 0.10 on Nov 1
    h.session.cap = cap
    h.session.submit("read a.txt three times")
    h.wait_for(Event.TURN_FINISHED)
    # 0.08 → 0.16 (past the line: the task goes on) → 0.24 (past twice the
    # share: it stops before the fourth step).
    assert len(_chat_requests(gateway)) == 3
    assert any("twice today's share" in e["data"]["message"] for e in h.of(Event.NOTICE))
    assert cap.used_today() == pytest.approx(0.24)


def test_a_task_started_under_the_line_is_not_moved_to_local(harness, gateway, llama, monkeypatch, tmp_path):
    gateway.script(sse_turn("cloud answer"))
    h, _ = _smart(harness, monkeypatch, llama, route_mode="off")
    cap, _, _ = _cap(tmp_path, plan=30)
    cap.record(0.5)
    h.session.cap = cap
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    assert llama.requests == [] and len(_chat_requests(gateway)) == 1


def test_once_used_up_a_sleeping_local_model_is_woken_and_waited_for(
        harness, gateway, llama, monkeypatch, tmp_path):
    (tmp_path / "price.py").write_text("x = 1\n")
    llama.script(_read("price.py"), sse_turn("x is one"))
    local = StubLocal(llama, ready=False)             # asleep: loads only when asked

    def wake(model_id, cancel=None, notify=None):
        local.ready = True
        return local.ready_target()
    local.target_for = wake
    h, _ = _smart(harness, monkeypatch, llama, route_mode="off", local=local)
    h.session.cap = _used_up(tmp_path)
    h.session.submit("What is x in price.py?")
    h.wait_for(Event.TURN_FINISHED)
    assert _chat_requests(gateway) == [] and len(llama.requests) >= 1


def test_once_used_up_a_local_reply_cut_off_empty_ends_the_task_and_stays_off_the_cloud(
        harness, gateway, llama, monkeypatch, tmp_path):
    cut = dict(finish_reason="length",
               usage={"prompt_tokens": 900, "completion_tokens": 4096, "total_tokens": 4996})
    llama.script(sse_turn("", **cut), sse_turn("", **cut))
    gateway.script(sse_turn("should not be sent"))
    h, _ = _smart(harness, monkeypatch, llama)
    h.session.cap = _used_up(tmp_path)
    h.session.submit(LOOKUP)
    h.wait_for(Event.TURN_FINISHED, timeout=20)
    assert _chat_requests(gateway) == [] and len(llama.requests) == 2
    msg = h.of(Event.ERROR)[-1]["data"]["message"]
    assert "cloud models are off" in msg and "cut off at 4096 tokens" in msg
