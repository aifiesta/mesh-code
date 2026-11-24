"""Deep work helpers (meshharness/helpers.py).

The lead session runs against the fake gateway as usual; its helpers run at
the same time, so the gateway answers by what each request says (the
`responder`) instead of from a fixed queue.
"""
import json
import threading
import time

import pytest
from fake_gateway import sse_turn

from meshharness import helpers as H
from meshharness.core.permissions import Mode
from meshharness.protocol import Decision, Event
from meshharness.session import Session

LEAD = "test/model"


def _arm_always(monkeypatch):
    """Turn the helpers on for the fake lead model (the real rule needs Smart
    routing to have picked Opus for a Deep work task; tested separately)."""
    def arm(self):
        self.state["_helpers_for"] = None
        self.state["_helpers_used"] = False
        if not self.helper:
            self.state["_helpers_for"] = self.cfg.get("model")
    monkeypatch.setattr(Session, "_arm_helpers", arm)


def _delegate_call(pieces, cid="d1"):
    return {"id": cid, "name": "delegate", "arguments": json.dumps({"pieces": pieces})}


TWO = [{"title": "Alpha", "files": ["a.py"], "brief": "Write a.py with def a(): return 1."},
       {"title": "Beta", "files": ["b.py"], "brief": "Write b.py with def b(): return 2."}]


def _tool_names(payload):
    return {t["function"]["name"] for t in payload.get("tools") or []}


def _last_user(payload):
    """The helper's brief (engine nudges may come after it as user messages)."""
    users = [m.get("content") for m in payload.get("messages") or [] if m.get("role") == "user"]
    users = [c if isinstance(c, str) else json.dumps(c) for c in users]
    return next((c for c in users if "YOUR PART" in c), users[-1] if users else "")


def _has_tool_result(payload):
    return any(m.get("role") == "tool" for m in payload.get("messages") or [])


def _responder(lead_turns, helper_fn):
    """Lead requests pop from `lead_turns`; helper requests go to helper_fn."""
    lock = threading.Lock()

    def respond(payload):
        if payload.get("model") == LEAD:
            with lock:
                return lead_turns.pop(0) if lead_turns else sse_turn("(lead: no script)")
        return helper_fn(payload)
    return respond


def _simple_helper(payload):
    part = _last_user(payload)
    name = "a.py" if "YOUR PART: Alpha" in part else "b.py"
    if not _has_tool_result(payload):
        return sse_turn(tool_calls=[{"id": f"w_{name}", "name": "write_file", "arguments":
                                     json.dumps({"path": name, "content": f"# {name}\n"})}],
                        cost=0.001)
    return sse_turn(f"Wrote {name}.", cost=0.001)


# ---------------------------------------------------------------------------
# when the tool is offered
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("level,pick,mode,offered", [
    ("max", "anthropic/claude-opus-5.5", "smart", True),
    ("xhigh", "anthropic/claude-opus-5.5", "smart", False),     # Complex task: unchanged
    ("max", "openai/gpt-6.1-sol", "smart", True),               # Deep work starts on Sol since 24 Nov
    ("max", "deepseek/deepseek-v4.1-flash-1017", "smart", False),  # never a helper leading helpers
    ("max", "anthropic/claude-opus-5.5", "off", False),         # Smart routing off
])
def test_offered_only_when_the_task_starts_as_deep_work(harness, level, pick, mode,
                                                                offered):
    h = harness(route_mode=mode, deep_work_helpers=True)
    s = h.session
    s.state.update(_turn_level=level, _smart_pick=pick, _on_local=False)
    s._arm_helpers()
    names = {t["function"]["name"] for t in s._tools()}
    assert ("delegate" in names) is offered


def test_off_by_default_deep_work_runs_alone(harness):
    """Since 25 Nov the helpers are off unless config.json turns them on: a
    Deep work task on Sol gets neither the tool nor the decision note."""
    from meshharness.core.config import DEFAULT_CONFIG
    assert DEFAULT_CONFIG["deep_work_helpers"] is False
    for h in (harness(route_mode="smart"),
              harness(route_mode="smart", deep_work_helpers=DEFAULT_CONFIG["deep_work_helpers"])):
        s = h.session
        s.state["messages"].append({"role": "user", "content": "rework the shop"})
        s.state.update(_turn_level="max", _smart_pick="openai/gpt-6.1-sol", _on_local=False)
        s._arm_helpers()
        assert "delegate" not in {t["function"]["name"] for t in s._tools()}
        assert s.state["messages"][-1]["content"] == "rework the shop"


def test_a_step_up_into_opus_mid_task_does_not_offer_it(harness):
    h = harness(route_mode="smart", deep_work_helpers=True)
    s = h.session
    s.state.update(_turn_level="xhigh", _smart_pick="openai/gpt-6.1-sol", _on_local=False)
    s._arm_helpers()
    s.state["_smart_pick"] = "anthropic/claude-opus-5.5"     # stepped up later in the task
    assert "delegate" not in {t["function"]["name"] for t in s._tools()}


def test_the_switch_turns_it_off(harness):
    h = harness(route_mode="smart", deep_work_helpers=False)
    s = h.session
    s.state.update(_turn_level="max", _smart_pick="anthropic/claude-opus-5.5", _on_local=False)
    s._arm_helpers()
    assert "delegate" not in {t["function"]["name"] for t in s._tools()}


def test_if_opus_is_dropped_mid_task_the_tool_goes_with_it(harness):
    h = harness(route_mode="smart", deep_work_helpers=True)
    s = h.session
    s.state.update(_turn_level="max", _smart_pick="anthropic/claude-opus-5.5", _on_local=False)
    s._arm_helpers()
    s.state["_smart_pick"] = None      # abandoned after an error: the pinned model rides
    assert "delegate" not in {t["function"]["name"] for t in s._tools()}


def test_helper_tools_leave_out_asking_servers_notes_and_delegating():
    names = {t["function"]["name"] for t in H.HELPER_TOOLS}
    assert {"read_file", "write_file", "edit_file", "run_bash"} <= names
    assert not names & {"ask_user", "start_server", "remember", "delegate"}


def test_helper_model_is_deepseek_flash_from_the_catalog():
    cat = [{"id": "anthropic/claude-opus-5.5"}, {"id": "deepseek/deepseek-v4.1-flash-1017"}]
    assert H.helper_model(cat) == "deepseek/deepseek-v4.1-flash-1017"
    assert H.helper_model([{"id": "deepseek/deepseek-v4.1-flash-1201"}]) == \
        "deepseek/deepseek-v4.1-flash-1201"
    assert H.helper_model(None) == H.HELPER_MODELS[0]


# ---------------------------------------------------------------------------
# checking the pieces
# ---------------------------------------------------------------------------
def test_pieces_must_be_two_to_four_on_separate_files(tmp_path):
    one = {"pieces": TWO[:1]}
    assert "2-4 pieces" in H.check_pieces(one, tmp_path)[1]
    five = {"pieces": [dict(TWO[0], files=[f"f{i}.py"]) for i in range(5)]}
    assert "2-4 pieces" in H.check_pieces(five, tmp_path)[1]
    shared = {"pieces": [TWO[0], dict(TWO[1], files=["b.py", "./a.py"])]}
    assert "one piece only" in H.check_pieces(shared, tmp_path)[1]
    outside = {"pieces": [TWO[0], dict(TWO[1], files=["../elsewhere.py"])]}
    assert "outside the project" in H.check_pieces(outside, tmp_path)[1]
    no_brief = {"pieces": [TWO[0], dict(TWO[1], brief="")]}
    assert "needs its files and a brief" in H.check_pieces(no_brief, tmp_path)[1]
    ok, err = H.check_pieces({"pieces": TWO}, tmp_path)
    assert err is None and [p["n"] for p in ok] == [1, 2]


# ---------------------------------------------------------------------------
# a whole run
# ---------------------------------------------------------------------------
def test_helpers_run_at_once_and_the_lead_reviews_with_their_reports(
        gateway, harness, tmp_path, monkeypatch):
    _arm_always(monkeypatch)
    started = []
    both_in = threading.Event()

    def helper(payload):
        part = _last_user(payload)
        if not _has_tool_result(payload):
            started.append(part)
            if len(started) >= 2:
                both_in.set()
            # Each helper's first step waits for the other's: proves they run at once.
            assert both_in.wait(5), "helpers did not run at the same time"
        return _simple_helper(payload)

    gateway.responder = _responder(
        [sse_turn(tool_calls=[_delegate_call(TWO)], cost=0.01),
         sse_turn("All joined.", cost=0.01)], helper)
    h = harness(mode=Mode.ACCEPT_EDITS)
    h.session.submit("build both")
    done = h.wait_for(Event.TURN_FINISHED, timeout=20)

    assert (tmp_path / "a.py").read_text() == "# a.py\n"
    assert (tmp_path / "b.py").read_text() == "# b.py\n"
    # The lead saw the tool, then got both reports back as its result.
    lead_reqs = [r for r in gateway.requests if r.get("model") == LEAD]
    assert "delegate" in _tool_names(lead_reqs[0])
    result = next(m for m in lead_reqs[1]["messages"] if m.get("role") == "tool")["content"]
    assert 'Piece 1 "Alpha"' in result and "Wrote a.py." in result
    assert 'Piece 2 "Beta"' in result and "Wrote b.py." in result
    assert "Run the full test suite once" in result
    # Helpers asked on DeepSeek at max thinking, without the lead's extra tools.
    helper_reqs = [r for r in gateway.requests if r.get("model") != LEAD]
    assert {r["model"] for r in helper_reqs} == {H.HELPER_MODELS[0]}
    assert all(r.get("reasoning_effort") == "max" or r.get("reasoning") for r in helper_reqs)
    assert not any("delegate" in _tool_names(r) or "ask_user" in _tool_names(r)
                   for r in helper_reqs)
    # Every helper step counts in the task's cost: 2 lead steps + 2 x 2 helper steps.
    assert done["data"]["cost"] == pytest.approx(0.01 * 2 + 0.001 * 4)
    assert [x["status"] for x in done["data"]["helpers"]] == ["done", "done"]
    last = h.of(Event.HELPERS)[-1]["data"]
    assert last["working"] == 0 and last["cost"] == pytest.approx(0.004)
    assert [r["steps"] for r in last["helpers"]] == [2, 2]
    assert all(r["secs"] < 5 for r in last["helpers"])
    assert h.of(Event.ASSISTANT_MESSAGE)[-1]["data"]["text"] == "All joined."


def test_helper_spend_counts_in_the_daily_ledger(gateway, harness, monkeypatch):
    _arm_always(monkeypatch)
    recorded = []

    class Ledger:
        def record(self, cost):
            recorded.append(cost)

        def capped(self):
            return False

        def past_hard_stop(self):
            return False

        def snapshot(self):
            return {"enabled": True}

        def enabled(self):
            return True

    gateway.responder = _responder(
        [sse_turn(tool_calls=[_delegate_call(TWO)], cost=0.01), sse_turn("ok", cost=0.01)],
        _simple_helper)
    h = harness(mode=Mode.ACCEPT_EDITS)
    h.session.cap = Ledger()
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED, timeout=20)
    assert sum(recorded) == pytest.approx(0.024)


def test_a_helper_may_not_edit_another_pieces_file(gateway, harness, tmp_path, monkeypatch):
    _arm_always(monkeypatch)
    (tmp_path / "b.py").write_text("original\n")

    def helper(payload):
        part = _last_user(payload)
        if "YOUR PART: Alpha" in part and not _has_tool_result(payload):
            return sse_turn(tool_calls=[{"id": "x", "name": "write_file", "arguments":
                                         json.dumps({"path": "b.py", "content": "stolen\n"})}])
        if "YOUR PART: Alpha" in part:
            tool_msg = [m for m in payload["messages"] if m.get("role") == "tool"][-1]
            return sse_turn("Alpha was refused: " + tool_msg["content"][:80])
        return sse_turn("Beta: nothing to do.")

    gateway.responder = _responder(
        [sse_turn(tool_calls=[_delegate_call(TWO)]), sse_turn("ok")], helper)
    h = harness(mode=Mode.BYPASS)
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED, timeout=20)
    assert (tmp_path / "b.py").read_text() == "original\n"
    result = [m for m in gateway.requests[-1]["messages"] if m.get("role") == "tool"][-1]
    assert "helper 2's files" in result["content"]


def test_a_helper_that_runs_out_of_time_is_reported_to_the_lead(
        gateway, harness, tmp_path, monkeypatch):
    _arm_always(monkeypatch)
    monkeypatch.setattr(H, "TIME_LIMIT", 1)

    def helper(payload):
        if "YOUR PART: Beta" in _last_user(payload):
            return ["__stall__:8"] + sse_turn("too late")
        return _simple_helper(payload)

    gateway.responder = _responder(
        [sse_turn(tool_calls=[_delegate_call(TWO)]), sse_turn("finished Beta myself")], helper)
    h = harness(mode=Mode.ACCEPT_EDITS)
    t0 = time.time()
    h.session.submit("go")
    done = h.wait_for(Event.TURN_FINISHED, timeout=20)
    assert time.time() - t0 < 7, "the lead must not wait out a stuck helper"
    assert [x["status"] for x in done["data"]["helpers"]] == ["done", "timed out"]
    rows = h.of(Event.HELPERS)[-1]["data"]["helpers"]
    assert rows[0]["secs"] < rows[1]["secs"], "a helper's time stops when it finishes"
    result = [m for m in gateway.requests[-1]["messages"] if m.get("role") == "tool"][-1]
    assert "timed out" in result["content"] and "finish this piece yourself" in result["content"]


def test_a_helper_over_its_cost_limit_is_stopped(gateway, harness, monkeypatch):
    _arm_always(monkeypatch)
    monkeypatch.setattr(H, "COST_LIMIT", 0.05)

    def helper(payload):
        if "YOUR PART: Beta" in _last_user(payload):
            return sse_turn(tool_calls=[{"id": f"r{time.time()}", "name": "read_file",
                                         "arguments": json.dumps({"path": "nothing.txt"})}],
                            cost=0.04)
        return _simple_helper(payload)

    gateway.responder = _responder(
        [sse_turn(tool_calls=[_delegate_call(TWO)]), sse_turn("ok")], helper)
    h = harness(mode=Mode.BYPASS)
    h.session.submit("go")
    done = h.wait_for(Event.TURN_FINISHED, timeout=20)
    assert done["data"]["helpers"][1]["status"] == "over its cost limit"


def test_a_helpers_approval_shows_in_the_lead_chat_and_the_click_reaches_it(
        gateway, harness, tmp_path, monkeypatch):
    _arm_always(monkeypatch)
    gateway.responder = _responder(
        [sse_turn(tool_calls=[_delegate_call(TWO)]), sse_turn("ok")], _simple_helper)
    h = harness(mode=Mode.DEFAULT)
    h.session.submit("go")
    for i in (1, 2):
        prop = h.wait_for(Event.TOOL_PROPOSED, timeout=10, min_count=i)
        assert prop["session"] == h.session.id
        assert prop["data"]["summary"].startswith("Helper ")
        assert h.session.respond_approval(prop["data"]["token"], Decision.ALLOW)
    h.wait_for(Event.TURN_FINISHED, timeout=20)
    assert (tmp_path / "a.py").exists() and (tmp_path / "b.py").exists()


def test_stop_ends_the_helpers_and_the_task(gateway, harness, monkeypatch):
    _arm_always(monkeypatch)

    def helper(payload):
        return ["__stall__:30"] + sse_turn("never")

    gateway.responder = _responder([sse_turn(tool_calls=[_delegate_call(TWO)])], helper)
    h = harness(mode=Mode.ACCEPT_EDITS)
    h.session.submit("go")
    h.wait_for(Event.HELPERS)
    time.sleep(0.3)
    t0 = time.time()
    h.session.interrupt()
    h.wait_for(Event.TURN_ABORTED, timeout=10)
    assert time.time() - t0 < 5
    assert [x["status"] for x in h.of(Event.HELPERS)[-1]["data"]["helpers"]] == \
        ["stopped", "stopped"]


def test_delegate_is_refused_when_not_offered_and_used_only_once(gateway, harness, monkeypatch):
    # Not offered (no Deep work on Opus): an error, the lead carries on.
    gateway.script(sse_turn(tool_calls=[_delegate_call(TWO)]), sse_turn("did it myself"))
    h = harness(mode=Mode.ACCEPT_EDITS)
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED, timeout=10)
    res = [e for e in h.of(Event.TOOL_RESULT) if e["data"]["name"] == "delegate"][0]
    assert not res["data"]["ok"] and "not available" in res["data"]["result"]
    assert not h.of(Event.HELPERS)


def test_a_second_delegate_in_one_task_is_refused(gateway, harness, monkeypatch):
    _arm_always(monkeypatch)
    gateway.responder = _responder(
        [sse_turn(tool_calls=[_delegate_call(TWO, "d1")]),
         sse_turn(tool_calls=[_delegate_call(TWO, "d2")]),
         sse_turn("done")], _simple_helper)
    h = harness(mode=Mode.ACCEPT_EDITS)
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED, timeout=20)
    res = [e for e in h.of(Event.TOOL_RESULT) if e["data"]["name"] == "delegate"]
    assert res[0]["data"]["ok"] and not res[1]["data"]["ok"]
    assert "already used" in res[1]["data"]["result"]


def test_the_decision_note_rides_with_the_message_but_not_in_the_chat(harness):
    h = harness(route_mode="smart", deep_work_helpers=True)
    s = h.session
    s.state["messages"].append({"role": "user", "content": "rework the shop"})
    s.state.update(_turn_level="max", _smart_pick="anthropic/claude-opus-5.5", _on_local=False)
    s._arm_helpers()
    assert s.state["messages"][-1]["content"].endswith(H.DECIDE_NOTE)
    shown = [m for m in s.history_payload() if m["kind"] == "user"]
    assert shown == [{"kind": "user", "text": "rework the shop"}]
    # Not on other tasks.
    s.state["messages"].append({"role": "user", "content": "small fix"})
    s.state.update(_turn_level="high", _smart_pick="deepseek/deepseek-v4.1-flash-1017")
    s._arm_helpers()
    assert s.state["messages"][-1]["content"] == "small fix"


def test_deep_work_steps_up_from_its_start_to_the_strongest_even_with_effort_set(
        harness, monkeypatch):
    """Deep work starts below the strongest model; its one step-up is part of
    Deep work, so it is taken with the Effort set to Deep work in Settings."""
    from meshharness.core import router
    h = harness(route_mode="smart", route_effort="max")
    s = h.session
    s.catalog = [{"id": "x"}]
    monkeypatch.setattr(router, "load_table", lambda: {"x": 1})
    seen = []

    def choose(cohort, difficulty, *a, **kw):
        seen.append((difficulty, kw.get("failed")))
        return {"model": "anthropic/claude-opus-5.5", "level": "xhigh", "effort": "xhigh",
                "scored": True}
    monkeypatch.setattr(router, "choose", choose)
    s._apply_pick({"model": "openai/gpt-6.1-sol", "level": "xhigh", "effort": "xhigh",
                   "scored": True, "deep_start": True}, "max")
    s.state["_smart_cohort"] = "agentic"
    assert s._smart_escalate("model is repeating the same action")
    assert s.state["_smart_pick"] == "anthropic/claude-opus-5.5"
    assert seen == [("max", ("openai/gpt-6.1-sol", "xhigh"))]
    # once on the strongest, the Effort the user set holds again
    assert not s._smart_escalate("still struggling")
