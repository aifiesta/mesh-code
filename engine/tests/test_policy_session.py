"""The policy ladder inside a turn: a cloud-only engine runs local-first on
the fallback model, escalates on evidence or on request, and hands back."""
import json

from fake_gateway import sse_turn

from meshharness.core.permissions import Mode
from meshharness.protocol import Event

SONNET = "anthropic/claude-sonnet-5.5"


def _local_first(harness, **over):
    return harness(policy="local-first", local_fallback_model="test/model", **over)


def test_a_weak_reply_escalates_once_and_the_strong_model_answers(gateway, harness):
    gateway.script(sse_turn("I've created the file for you."))     # claimed, no tool call
    gateway.script(sse_turn("Done: wrote hello.py"))
    h = _local_first(harness)
    h.session.submit("create hello.py printing hi")
    h.wait_for(Event.TURN_FINISHED)
    models = [r["model"] for r in gateway.requests]
    assert models == ["test/model", SONNET]
    assert gateway.requests[-1]["models"][0] == "anthropic/claude-opus-5.5"
    assert gateway.requests[-1]["reasoning_effort"] == "high"
    changed = h.of(Event.POLICY_CHANGED)
    assert changed and changed[0]["data"]["model"] == SONNET
    fin = h.of(Event.TURN_FINISHED)[-1]["data"]
    assert fin["escalated"] == SONNET


def test_auto_escalate_off_leaves_the_weak_reply_alone(gateway, harness):
    gateway.script(sse_turn("I've created the file for you."))
    h = _local_first(harness, auto_escalate=False)
    h.session.submit("create hello.py printing hi")
    h.wait_for(Event.TURN_FINISHED)
    assert [r["model"] for r in gateway.requests] == ["test/model"]
    assert not h.of(Event.POLICY_CHANGED)


def test_escalate_while_idle_runs_the_next_turn_on_the_strong_model_then_returns(gateway, harness):
    gateway.script(sse_turn("strong answer"))
    gateway.script(sse_turn("local again"))
    h = _local_first(harness)
    h.session.escalate()
    h.session.submit("hard thing")
    h.wait_for(Event.TURN_FINISHED)
    h.session.submit("easy thing")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert [r["model"] for r in gateway.requests] == [SONNET, "test/model"]


def test_sticky_session_keeps_escalating_until_deescalated(gateway, harness):
    for _ in range(3):
        gateway.script(sse_turn("ok"))
    h = _local_first(harness, escalation_sticky="session")
    h.session.escalate()
    for n in (1, 2):
        h.session.submit("go")
        h.wait_for(Event.TURN_FINISHED, min_count=n)
    h.session.deescalate()
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED, min_count=3)
    assert [r["model"] for r in gateway.requests] == [SONNET, SONNET, "test/model"]


def test_escalation_hands_back_after_its_hop_budget(gateway, harness):
    for i in range(4):
        gateway.script(sse_turn(tool_calls=[{"id": f"c{i}", "name": "run_bash",
                                             "arguments": json.dumps({"command": f"echo {i}"})}]))
    gateway.script(sse_turn("finished"))
    h = _local_first(harness, mode=Mode.AUTO)
    h.session.policy.doc["escalation"]["max_hops"] = 2
    h.session.escalate()
    h.session.submit("loop a bit")
    h.wait_for(Event.TURN_FINISHED)
    models = [r["model"] for r in gateway.requests]
    assert models[:2] == [SONNET, SONNET] and set(models[2:]) == {"test/model"}
    back = [e for e in h.of(Event.POLICY_CHANGED) if "handed back" in e["data"]["reason"]]
    assert back, "the hand-back must be announced"
    nudges = [m for m in gateway.requests[2]["messages"] if m.get("role") == "user"]
    assert any("stronger model worked" in (m.get("content") or "") for m in nudges)


def test_pinned_never_escalates(gateway, harness):
    gateway.script(sse_turn("I've created the file for you."))
    h = harness(policy="pinned")
    h.session.escalate()
    h.session.submit("create hello.py")
    h.wait_for(Event.TURN_FINISHED)
    assert [r["model"] for r in gateway.requests] == ["test/model"]


def test_a_stall_escalates_before_the_nudge(gateway, harness):
    same = {"id": "c", "name": "run_bash", "arguments": json.dumps({"command": "echo same"})}
    for i in range(3):
        gateway.script(sse_turn(tool_calls=[{**same, "id": f"c{i}"}]))
    gateway.script(sse_turn("strong model takes over"))
    h = _local_first(harness, mode=Mode.AUTO)
    h.session.submit("do the thing")
    h.wait_for(Event.TURN_FINISHED)
    models = [r["model"] for r in gateway.requests]
    assert models[:3] == ["test/model"] * 3 and models[3] == SONNET
    assert any("repeating" in e["data"]["reason"] for e in h.of(Event.POLICY_CHANGED))


def test_two_failed_tool_batches_escalate(gateway, harness):
    broken = {"id": "b", "name": "run_bash", "arguments": "{not json"}
    gateway.script(sse_turn(tool_calls=[{**broken, "id": "b1"}]))
    gateway.script(sse_turn(tool_calls=[{**broken, "id": "b2"}]))
    gateway.script(sse_turn("fixed by the strong model"))
    h = _local_first(harness, mode=Mode.AUTO)
    h.session.submit("do the thing")
    h.wait_for(Event.TURN_FINISHED)
    assert [r["model"] for r in gateway.requests][-1] == SONNET
    assert any("failed tool calls" in e["data"]["reason"] for e in h.of(Event.POLICY_CHANGED))


def test_escalate_while_busy_moves_the_next_hop_and_harder_raises_the_effort(gateway, harness):
    gateway.script(sse_turn(tool_calls=[{"id": "c1", "name": "run_bash",
                                         "arguments": json.dumps({"command": "sleep 1"})}]))
    gateway.script(sse_turn("strong"))
    h = _local_first(harness, mode=Mode.AUTO, escalation_effort="high")
    h.session.submit("slow thing")
    h.wait_for(Event.TOOL_STARTED)          # press while the local hop's tool runs
    h.session.escalate(harder=True)
    h.wait_for(Event.TURN_FINISHED)
    last = gateway.requests[-1]
    assert last["model"] == SONNET and last["reasoning_effort"] == "xhigh"


def test_a_failed_escalated_hop_hands_back_and_the_turn_goes_on(gateway, harness):
    gateway.script(["__status__:401"])
    gateway.script(sse_turn("local finished it"))
    h = _local_first(harness)
    h.session.escalate()
    h.session.submit("go")
    fin = h.wait_for(Event.TURN_FINISHED)
    assert [r["model"] for r in gateway.requests] == [SONNET, "test/model"]
    assert not h.of(Event.ERROR)
    assert any("could not be reached" in e["data"]["reason"] for e in h.of(Event.POLICY_CHANGED))
    assert fin["data"]["escalated"] is None, "no escalated hop completed"


def test_without_a_key_nothing_escalates_and_the_reply_is_kept(gateway, harness):
    gateway.script(sse_turn("I've created the file for you."))
    h = _local_first(harness, api_key="")
    h.session.submit("create hello.py")
    h.wait_for(Event.TURN_FINISHED)
    assert [r["model"] for r in gateway.requests] == ["test/model"]
    assert h.of(Event.ASSISTANT_MESSAGE), "the local reply is shown, not discarded"
    assert any("API key" in e["data"]["message"] for e in h.of(Event.NOTICE))
    h.session.escalate()
    assert any("API key" in e["data"]["message"] for e in h.of(Event.NOTICE)[-1:])


def test_a_failed_hop_keeps_a_session_sticky_escalation_for_the_next_turn(gateway, harness):
    gateway.script(["__status__:401"])
    gateway.script(sse_turn("local finished it"))
    gateway.script(sse_turn("strong is back"))
    h = _local_first(harness, escalation_sticky="session")
    h.session.escalate()
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED)
    h.session.submit("again")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert [r["model"] for r in gateway.requests] == [SONNET, "test/model", SONNET]


def test_an_escalate_press_in_the_last_hop_does_not_leak_into_the_next_turn(gateway, harness, monkeypatch):
    gateway.script(sse_turn("plain reply"))
    gateway.script(sse_turn("second plain reply"))
    h = _local_first(harness)
    # A press that lands after the last hop took the mailbox: nobody consumes it this turn.
    monkeypatch.setattr(h.session, "_take_escalate_request", lambda: False)
    h.session._escalate_request = ("requested", False)
    h.session.submit("one")
    h.wait_for(Event.TURN_FINISHED)
    monkeypatch.undo()
    assert h.session._escalate_request is None
    assert any("ended before the escalation" in e["data"]["message"] for e in h.of(Event.NOTICE))
    h.session.submit("two")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert [r["model"] for r in gateway.requests] == ["test/model", "test/model"]
