"""The engine keeps the plan's ticks; the model only says which step it is on.

Before: the model ticked steps with its own tool, and whole round trips went
on nothing else (a response that only ticks re-sends the entire conversation).
Now there is no tick tool. The work tools take an optional `step`, the plan
follows it, and the last step is ticked when the turn ends with an answer.
"""
import json

from fake_gateway import sse_turn
from meshharness.core import tools
from meshharness.core.permissions import Mode
from meshharness.core.plan import Plan
from meshharness.protocol import Event


def _statuses(plan):
    return [s.status for s in plan.steps]


def _call(name, call_id="c1", **args):
    return {"id": call_id, "name": name, "arguments": json.dumps(args)}


# -- the plan itself --------------------------------------------------------

def test_a_new_plan_starts_on_its_first_step():
    plan = Plan(["a", "b", "c"])
    plan.begin()
    assert _statuses(plan) == ["in_progress", "pending", "pending"]
    assert plan.current()[0] == 1


def test_following_a_step_ticks_everything_before_it():
    plan = Plan(["a", "b", "c", "d"])
    plan.begin()
    assert plan.follow(1) is False                 # already there: nothing changes
    assert plan.follow(3) is True
    assert _statuses(plan) == ["completed", "completed", "in_progress", "pending"]


def test_naming_an_earlier_step_again_does_not_untick_it():
    plan = Plan(["a", "b", "c"])
    plan.begin()
    plan.follow(3)
    assert plan.follow(2) is False
    assert _statuses(plan) == ["completed", "completed", "in_progress"]


def test_step_numbers_arrive_in_many_shapes():
    plan = Plan(["a", "b", "c"])
    plan.begin()
    assert plan.follow("2") is True
    assert plan.follow(3.0) is True
    for junk in (None, True, "two", 2.5, 0, 4, -1, [2], float("nan"), float("inf")):
        assert plan.follow(junk) is None, junk
    assert _statuses(plan) == ["completed", "completed", "in_progress"]


def test_finish_ticks_the_last_step_only_when_it_is_the_one_in_progress():
    done = Plan(["a", "b"])
    done.begin()
    done.follow(2)
    assert done.finish() is True and done.is_complete()

    early = Plan(["a", "b", "c"])
    early.begin()
    early.follow(2)
    assert early.finish() is False, "step 3 was never started: nothing is claimed"
    assert _statuses(early) == ["completed", "in_progress", "pending"]


def test_a_revised_plan_keeps_the_ticks_of_steps_it_carries_over():
    old = Plan(["scaffold", "write tests", "ship"])
    old.begin()
    old.follow(2)
    new = Plan(["scaffold", "write more tests", "ship"], previous=old)
    new.begin()
    assert _statuses(new) == ["completed", "in_progress", "pending"]


def test_a_restored_plan_keeps_its_saved_statuses():
    plan = Plan(["a", "b"])
    plan.begin()
    plan.follow(2)
    again = Plan.from_dict(plan.to_dict())
    assert _statuses(again) == ["completed", "in_progress"]


# -- what the model is offered ----------------------------------------------

def test_there_is_no_tick_tool_and_the_work_tools_take_a_step():
    names = [t["function"]["name"] for t in tools.TOOLS]
    assert "update_step" not in names
    assert "create_plan" in names
    props = {t["function"]["name"]: t["function"]["parameters"] for t in tools.TOOLS}
    for name in ("read_file", "write_file", "edit_file", "run_bash", "start_server"):
        assert props[name]["properties"]["step"]["type"] == "integer", name
        assert "step" not in props[name].get("required", []), name
    for name in ("create_plan", "ask_user", "remember", "web_search"):
        assert "step" not in props[name]["properties"], name


def test_the_plan_tool_explains_the_step_argument_once():
    desc = next(t["function"]["description"] for t in tools.TOOLS
                if t["function"]["name"] == "create_plan")
    assert "`step`" in desc and "no tool for ticking" in desc
    assert "Step 1 is in progress already" in desc
    assert "Never make a call only to report progress" in desc
    assert "update_step" not in json.dumps(tools.TOOLS)
    prompt = tools.build_system_prompt({"repo_memory": False}, "/tmp")
    assert "update_step" not in prompt and "create_plan" not in prompt
    # What only the prompt says: finish what you planned.
    assert "while any plan step is still undone" in prompt


# -- through the engine -----------------------------------------------------

def _plans(h):
    return [e["data"] for e in h.of(Event.PLAN_UPDATED)]


def test_tool_calls_move_the_checklist_and_the_end_of_the_turn_ticks_the_last(
        gateway, harness, tmp_path):
    (tmp_path / "a.txt").write_text("one\n")
    gateway.script(
        sse_turn(tool_calls=[_call("create_plan", steps=["look", "write", "check"])]),
        sse_turn(tool_calls=[_call("read_file", path="a.txt", step=1)]),
        sse_turn(tool_calls=[_call("write_file", path="b.txt", content="two\n", step=2)]),
        sse_turn(tool_calls=[_call("run_bash", command="cat b.txt", step=3)]),
        sse_turn("All three steps are done."))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("do the three things")
    done = h.wait_for(Event.TURN_FINISHED)

    seen = [(p["done"], [s["status"] for s in p["steps"]]) for p in _plans(h)]
    assert seen[0] == (0, ["in_progress", "pending", "pending"]), "the engine starts step 1"
    assert (1, ["completed", "in_progress", "pending"]) in seen
    assert (2, ["completed", "completed", "in_progress"]) in seen
    assert seen[-1] == (3, ["completed", "completed", "completed"])
    assert done["data"]["plan"]["complete"] is True
    # Five responses, none of them plan bookkeeping after the plan itself.
    assert len(gateway.requests) == 5
    assert (tmp_path / "b.txt").read_text() == "two\n"


def test_step_never_reaches_the_tool_or_the_screen(gateway, harness, tmp_path):
    gateway.script(
        sse_turn(tool_calls=[_call("create_plan", steps=["write", "check"])]),
        sse_turn(tool_calls=[_call("write_file", path="b.txt", content="x\n", step=1)]),
        sse_turn("done"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED)
    started = [e["data"] for e in h.of(Event.TOOL_STARTED) if e["data"]["name"] == "write_file"]
    assert started and "step" not in (started[0].get("args") or {})
    # The model still sees its own call exactly as it sent it.
    sent = [tc for m in gateway.requests[-1]["messages"] for tc in (m.get("tool_calls") or [])
            if tc["function"]["name"] == "write_file"]
    assert json.loads(sent[0]["function"]["arguments"])["step"] == 1


def test_a_turn_that_stops_early_does_not_claim_the_rest(gateway, harness, tmp_path):
    gateway.script(
        sse_turn(tool_calls=[_call("create_plan", steps=["write", "check", "ship"])]),
        sse_turn(tool_calls=[_call("write_file", path="b.txt", content="x\n", step=1)]),
        sse_turn("I wrote the file but could not check or ship it."))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("go")
    done = h.wait_for(Event.TURN_FINISHED)
    plan = done["data"]["plan"]
    assert plan["complete"] is False and plan["done"] == 0
    assert [s["status"] for s in plan["steps"]] == ["in_progress", "pending", "pending"]


def test_work_that_never_names_a_step_gets_one_reminder_after_three_batches(
        gateway, harness, tmp_path):
    gateway.script(
        sse_turn(tool_calls=[_call("create_plan", steps=["write", "check"])]),
        sse_turn(tool_calls=[_call("write_file", "c1", path="b.txt", content="x\n")]),
        sse_turn(tool_calls=[_call("run_bash", "c2", command="cat b.txt")]),
        sse_turn(tool_calls=[_call("run_bash", "c3", command="wc -l b.txt")]),
        sse_turn(tool_calls=[_call("run_bash", "c4", command="ls")]),
        sse_turn("done"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED)
    results = [m["content"] for m in gateway.requests[-1]["messages"] if m.get("role") == "tool"]
    nudged = [i for i, r in enumerate(results) if "[plan] The checklist still shows step 1 of 2 (write)" in r]
    assert nudged == [3], "on the third quiet batch (the plan result is index 0), and only once"
    assert "put the current step's number in `step`" in results[3]


def test_no_reminder_without_a_plan_or_when_steps_are_named(gateway, harness, tmp_path):
    gateway.script(
        sse_turn(tool_calls=[_call("write_file", "c0", path="a.txt", content="x\n")]),
        sse_turn(tool_calls=[_call("run_bash", "c1", command="ls")]),
        sse_turn(tool_calls=[_call("run_bash", "c2", command="ls")]),
        sse_turn(tool_calls=[_call("create_plan", "c3", steps=["write", "check"])]),
        sse_turn(tool_calls=[_call("write_file", "c4", path="b.txt", content="x\n")]),
        sse_turn(tool_calls=[_call("run_bash", "c5", command="cat b.txt")]),
        sse_turn(tool_calls=[_call("run_bash", "c6", command="cat b.txt", step=2)]),
        sse_turn(tool_calls=[_call("run_bash", "c7", command="ls")]),
        sse_turn("done"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("go")
    done = h.wait_for(Event.TURN_FINISHED)
    results = [m["content"] for m in gateway.requests[-1]["messages"] if m.get("role") == "tool"]
    assert not any("[plan]" in r for r in results)
    assert done["data"]["plan"]["complete"] is True


def test_an_edit_that_changes_nothing_sent_to_announce_a_step_is_not_a_broken_call(
        gateway, harness, tmp_path):
    """Seen live: after making a plan the model sent edit_file with identical
    old and new text and step=1. Counted as malformed, it made the router
    step up to a model ten times the price."""
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    gateway.script(
        sse_turn(tool_calls=[_call("create_plan", steps=["change f", "check"])]),
        sse_turn(tool_calls=[_call("edit_file", "c1", path="a.py", old_text="def f():",
                                   new_text="def f():", step=2)]),
        sse_turn("ok"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED)
    skipped = h.of(Event.TOOL_SKIPPED)
    assert [e["data"]["kind"] for e in skipped] == ["noop"]
    assert h.session.state.get("doom_streak", {}).get("edit_file") is None
    result = [m["content"] for m in gateway.requests[-1]["messages"] if m.get("role") == "tool"][-1]
    assert result.startswith("Nothing changed") and "no need to announce a plan step" in result
    assert [s["status"] for s in h.of(Event.PLAN_UPDATED)[-1]["data"]["steps"]] == ["completed", "completed"]
    assert (tmp_path / "a.py").read_text() == "def f():\n    return 1\n"


def test_the_same_edit_without_a_step_is_still_an_error(gateway, harness, tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    gateway.script(
        sse_turn(tool_calls=[_call("edit_file", "c1", path="a.py", old_text="def f():", new_text="def f():")]),
        sse_turn("ok"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED)
    assert [e["data"]["kind"] for e in h.of(Event.TOOL_SKIPPED)] == ["invalid"]


def test_the_plan_result_does_not_ask_for_a_tick(gateway, harness):
    gateway.script(
        sse_turn(tool_calls=[_call("create_plan", steps=["a", "b"])]),
        sse_turn("planned"))
    h = harness()
    h.session.submit("plan it")
    h.wait_for(Event.TURN_FINISHED)
    result = [m["content"] for m in gateway.requests[-1]["messages"] if m.get("role") == "tool"][-1]
    assert result == "Plan created with 2 step(s); step 1 is in progress. Start the work now."


def test_an_old_chat_that_still_calls_the_tick_tool_is_answered(gateway, harness):
    gateway.script(
        sse_turn(tool_calls=[_call("create_plan", steps=["a", "b"])]),
        sse_turn(tool_calls=[_call("update_step", "c2", index=1, status="completed")]),
        sse_turn("ok"))
    h = harness()
    h.session.submit("plan it")
    h.wait_for(Event.TURN_FINISHED)
    assert not h.of(Event.TOOL_PROPOSED), "still bookkeeping, never gated"
    result = [m["content"] for m in gateway.requests[-1]["messages"] if m.get("role") == "tool"][-1]
    assert result.startswith("Step 1 → completed.")
    assert "put `step` on your tool calls" in result


def _make_plan_then_work(steps, step_done):
    return [sse_turn(tool_calls=[_call("create_plan", steps=steps)]),
            sse_turn(tool_calls=[_call("run_bash", command="true", step=step_done)])]


def test_a_plan_revised_in_the_same_turn_keeps_its_ticks(gateway, harness):
    gateway.script(
        *_make_plan_then_work(["read the code", "fix the bug", "run the tests"], 2),
        sse_turn(tool_calls=[_call("create_plan", steps=["read the code", "fix the bug properly", "run the tests"])]),
        sse_turn("Stopping here for now."))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("fix the bug")
    h.wait_for(Event.TURN_FINISHED)
    assert _statuses(h.session.state["plan"]) == ["completed", "in_progress", "pending"]


def test_a_plan_made_in_a_later_turn_does_not_inherit_ticks(gateway, harness):
    gateway.script(
        *_make_plan_then_work(["read the code", "fix the bug", "run the tests"], 3),
        sse_turn("Fixed, and the tests pass."),
        # Turn 2, a new task whose plan reuses two finished titles. The model
        # does step 2 and stops: "run the tests" was never run in this turn.
        *_make_plan_then_work(["read the code", "add the feature", "run the tests"], 2),
        sse_turn("Added the feature."))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("fix the bug")
    first = h.wait_for(Event.TURN_FINISHED)
    assert first["data"]["plan"]["complete"]
    h.session.submit("now add the feature")
    second = h.wait_for(Event.TURN_FINISHED, min_count=2)

    new_plan = [p for p in _plans(h) if "add the feature" in [s["title"] for s in p["steps"]]][0]
    assert [s["status"] for s in new_plan["steps"]] == ["in_progress", "pending", "pending"], \
        "a new task's plan starts clean, whatever the old plan's titles"
    assert [s["status"] for s in second["data"]["plan"]["steps"]] == ["completed", "in_progress", "pending"]
    assert not second["data"]["plan"]["complete"], "the turn must not claim the tests were run"
