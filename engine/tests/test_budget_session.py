"""The governor inside a turn: holds end a cloud turn, hand an escalation
back, count every cloud hop, and treat a 402 as the key's own cap."""
import json
import time

from fake_gateway import sse_turn

from meshharness.core.budget import Budget
from meshharness.protocol import Event

CATALOG = [{"id": "test/model", "pricing": {"prompt_usd_per_1m": 1, "completion_usd_per_1m": 5}},
           {"id": "anthropic/claude-sonnet-5.5", "pricing": {"prompt_usd_per_1m": 2, "completion_usd_per_1m": 10}}]


def _budget(tmp_path, **cfg):
    base = {"monthly_budget_usd": 10}
    base.update(cfg)
    return Budget(base, tmp_path / "ledger.json")


def test_a_cloud_hop_is_counted_and_the_snapshot_rides_on_status(gateway, harness, tmp_path):
    gateway.script(sse_turn("hi"))
    b = _budget(tmp_path)
    h = harness(policy="pinned", budget=b)
    h.session.catalog = CATALOG
    h.session.submit("hello")
    h.wait_for(Event.TURN_FINISHED)
    snap = b.snapshot()
    assert snap["month"]["spent"] > 0 and snap["day"]["by_kind"].get("pinned", 0) > 0
    assert h.of(Event.BUDGET_UPDATED)
    assert h.of(Event.STATUS)[-1]["data"]["budget"]["day"]["limit"] == round(b.daily_limit(), 4) > 0


def test_the_daily_hold_ends_a_pinned_turn_before_it_spends(gateway, harness, tmp_path):
    gateway.script(sse_turn("never sent"))
    b = _budget(tmp_path)
    b.record("pinned", b.daily_limit())
    h = harness(policy="pinned", budget=b)
    h.session.catalog = CATALOG
    h.session.submit("hello")
    h.wait_for(Event.TURN_FINISHED)
    assert gateway.requests == []
    hold = h.of(Event.BUDGET_HOLD)
    assert hold and hold[0]["data"]["scope"] == "day"
    b.override_today()
    gateway.script(sse_turn("now it runs"))
    h.session.submit("again")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert len(gateway.requests) == 1


def test_a_held_escalation_hands_back_to_the_local_model(gateway, harness, tmp_path):
    gateway.script(sse_turn("fallback answer"))
    b = _budget(tmp_path)
    b.record("escalate", b.daily_limit() - 0.001)   # room for a free fallback hop, not a Sonnet one
    h = harness(policy="local-first", local_fallback_model="test/model", budget=b)
    h.session.catalog = [{"id": "test/model", "pricing": {"prompt_usd_per_1m": 0, "completion_usd_per_1m": 0}},
                         CATALOG[1]]
    h.session.escalate()
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED)
    models = [r["model"] for r in gateway.requests]
    assert models == ["test/model"], models
    back = [e for e in h.of(Event.POLICY_CHANGED) if "budget hold" in e["data"]["reason"]]
    assert back
    assert h.of(Event.TURN_FINISHED)[-1]["data"]["escalated"] is None
    assert h.of(Event.STATUS)[-1]["data"]["policy"]["tier"] != "escalated"


def test_a_402_sets_the_key_cap_hold_until_cleared(gateway, harness, tmp_path):
    gateway.script(["__status__:402"])
    b = _budget(tmp_path)
    h = harness(policy="pinned", budget=b)
    h.session.catalog = CATALOG
    h.session.submit("hello")
    h.wait_for(Event.TURN_FINISHED)
    assert not h.of(Event.ERROR)
    hold = h.of(Event.BUDGET_HOLD)
    assert hold and hold[-1]["data"]["scope"] == "key_cap" and "spend cap" in hold[-1]["data"]["message"]
    assert b.snapshot()["key_cap"] is True
    gateway.script(sse_turn("never"))
    h.session.submit("again")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert h.of(Event.BUDGET_HOLD)[-1]["data"]["scope"] == "key_cap"
    b.set_key_cap(False)
    h.session.submit("third")
    h.wait_for(Event.TURN_FINISHED, min_count=3)
    assert len(gateway.requests) == 2      # the 402 attempt and the cleared retry; the held turn sent nothing


def test_a_402_on_the_escalated_hop_hands_back_instead_of_ending_the_turn(gateway, harness, tmp_path):
    gateway.script(["__status__:402"], sse_turn("never sent"))
    b = _budget(tmp_path)
    h = harness(policy="local-first", local_fallback_model="test/model", budget=b)
    h.session.catalog = CATALOG
    h.session.escalate()
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED)
    assert [r["model"] for r in gateway.requests] == ["anthropic/claude-sonnet-5.5"]
    assert any("402" in e["data"]["reason"] for e in h.of(Event.POLICY_CHANGED))
    assert not h.of(Event.ERROR) and b.snapshot()["key_cap"] is True
    assert h.of(Event.BUDGET_HOLD)[-1]["data"]["scope"] == "key_cap"   # the cloud fallback is capped too
    assert h.of(Event.TURN_FINISHED)[-1]["data"]["escalated"] is None


def test_the_hop_that_ends_an_escalation_is_counted_as_escalation(gateway, harness, tmp_path):
    gateway.script(sse_turn("done"))
    b = _budget(tmp_path)
    h = harness(policy="local-first", local_fallback_model="test/model", budget=b)
    h.session.catalog = CATALOG
    doc = h.session.policy.doc
    h.session.policy.doc = {**doc, "escalation": {**doc["escalation"], "max_hops": 1}}
    h.session.escalate()
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED)
    by_kind = b.snapshot()["month"]["by_kind"]
    assert by_kind.get("escalate", 0) > 0 and "fallback" not in by_kind, by_kind


def test_a_hop_that_fails_in_band_still_counts_its_estimate(gateway, harness, tmp_path):
    gateway.script(sse_turn("par", error="upstream died"))
    b = _budget(tmp_path)
    h = harness(policy="pinned", budget=b)
    h.session.catalog = CATALOG
    h.session.submit("hello")
    h.wait_for(Event.TURN_FINISHED)
    snap = b.snapshot()
    assert snap["month"]["spent"] > 0 and snap["estimated_usd"] == snap["month"]["spent"]


def test_a_stopped_cloud_hop_still_counts_its_estimate(gateway, harness, tmp_path):
    gateway.script(["__stall__:5"])
    b = _budget(tmp_path)
    h = harness(policy="pinned", budget=b)
    h.session.catalog = CATALOG
    h.session.submit("hello")
    h.wait_for(Event.HOP_STARTED)
    deadline = time.time() + 5
    while not gateway.requests and time.time() < deadline:
        time.sleep(0.02)
    h.session.interrupt()
    h.wait_for(Event.TURN_ABORTED)
    assert b.snapshot()["month"]["spent"] > 0
