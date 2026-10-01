"""End-to-end tests for the headless loop.

Everything here runs the real engine against a local SSE gateway: real
httpx, real streaming, real tool-call accumulation, real filesystem writes
into a tmp workspace. The only fake is the model's opinion.
"""
import json
import time

import pytest
from fake_gateway import sse_turn

from meshharness.core.permissions import Mode
from meshharness.protocol import Decision, Event


def test_plain_reply_streams_and_settles(gateway, harness):
    gateway.script(sse_turn("Hello from the harness."))
    h = harness()
    h.session.submit("hi")

    done = h.wait_for(Event.TURN_FINISHED)
    deltas = "".join(e["data"]["text"] for e in h.of(Event.ASSISTANT_DELTA))
    assert deltas == "Hello from the harness."
    assert h.of(Event.ASSISTANT_MESSAGE)[0]["data"]["text"] == "Hello from the harness."
    assert done["data"]["cost"] == pytest.approx(0.000123)
    assert done["data"]["tokens"] == 15


def test_tool_call_requires_approval_and_writes_the_file(gateway, harness, tmp_path):
    """The load-bearing round trip: propose -> park -> click -> execute."""
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "write_file", "arguments":
                              json.dumps({"path": "hello.py", "content": "print('hi')\n"})}]),
        sse_turn("Wrote it."))
    h = harness(mode=Mode.DEFAULT)
    h.session.submit("write hello.py")

    proposal = h.wait_for(Event.TOOL_PROPOSED)
    assert proposal["data"]["name"] == "write_file"
    # The dialog gets a real diff, not just the raw arguments.
    preview = proposal["data"]["preview"]
    assert preview["kind"] == "diff" and preview["new"] == "print('hi')\n"
    assert not (tmp_path / "hello.py").exists(), "must not write before approval"

    h.session.respond_approval(proposal["data"]["token"], Decision.ALLOW)

    h.wait_for(Event.TURN_FINISHED)
    assert (tmp_path / "hello.py").read_text() == "print('hi')\n"
    assert h.of(Event.FILE_CHANGED)[0]["data"]["path"].endswith("hello.py")


def test_denial_never_touches_the_disk(gateway, harness, tmp_path):
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "write_file", "arguments":
                              json.dumps({"path": "nope.py", "content": "x"})}]),
        sse_turn("Understood."))
    h = harness(mode=Mode.DEFAULT)
    h.session.submit("write nope.py")

    proposal = h.wait_for(Event.TOOL_PROPOSED)
    h.session.respond_approval(proposal["data"]["token"], Decision.DENY)
    h.wait_for(Event.TURN_FINISHED)

    assert not (tmp_path / "nope.py").exists()
    # The model is told plainly, so it can adapt rather than retry blindly.
    sent = gateway.requests[-1]["messages"]
    assert any(m.get("role") == "tool" and "denied" in str(m.get("content"))
               for m in sent)


def test_accept_edits_mode_skips_the_dialog(gateway, harness, tmp_path):
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "write_file", "arguments":
                              json.dumps({"path": "auto.txt", "content": "ok"})}]),
        sse_turn("done"))
    h = harness(mode=Mode.ACCEPT_EDITS)
    h.session.submit("write auto.txt")
    h.wait_for(Event.TURN_FINISHED)

    assert (tmp_path / "auto.txt").read_text() == "ok"
    assert not h.of(Event.TOOL_PROPOSED), "accept-edits should not prompt"
    assert h.of(Event.TOOL_DECIDED)[0]["data"]["auto"] is True


def test_auto_approval_still_falls_back_to_asking_outside_the_workspace(
        gateway, harness, tmp_path):
    """The safety gate must survive the port to a per-session workspace."""
    outside = tmp_path.parent / "outside.txt"
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "write_file", "arguments":
                              json.dumps({"path": str(outside), "content": "x"})}]),
        sse_turn("ok"))
    h = harness(mode=Mode.ACCEPT_EDITS)
    h.session.submit("write outside")

    proposal = h.wait_for(Event.TOOL_PROPOSED)
    assert "outside the current project directory" in proposal["data"]["blocked_reason"]
    h.session.respond_approval(proposal["data"]["token"], Decision.DENY)
    h.wait_for(Event.TURN_FINISHED)
    assert not outside.exists()


def test_relative_paths_resolve_against_the_session_workspace(
        gateway, harness, tmp_path):
    """Not against os.getcwd() — the whole point of threading `root` through."""
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "write_file", "arguments":
                              json.dumps({"path": "sub/dir/f.txt", "content": "scoped"})}]),
        sse_turn("ok"))
    h = harness(mode=Mode.ACCEPT_EDITS)
    h.session.submit("write nested")
    h.wait_for(Event.TURN_FINISHED)
    assert (tmp_path / "sub" / "dir" / "f.txt").read_text() == "scoped"


def test_always_allow_persists_for_the_session(gateway, harness, tmp_path):
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "write_file", "arguments":
                              json.dumps({"path": "a.txt", "content": "1"})}]),
        sse_turn(tool_calls=[{"id": "c2", "name": "write_file", "arguments":
                              json.dumps({"path": "b.txt", "content": "2"})}]),
        sse_turn("both done"))
    h = harness(mode=Mode.DEFAULT)
    h.session.submit("write two files")

    first = h.wait_for(Event.TOOL_PROPOSED)
    h.session.respond_approval(first["data"]["token"], Decision.ALWAYS)
    h.wait_for(Event.TURN_FINISHED)

    assert (tmp_path / "a.txt").read_text() == "1"
    assert (tmp_path / "b.txt").read_text() == "2"
    assert len(h.of(Event.TOOL_PROPOSED)) == 1, "second write should not re-prompt"


def test_malformed_arguments_are_skipped_not_executed(gateway, harness):
    """Truncated JSON must never be fabricated into a call."""
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "write_file",
                              "arguments": '{"path": "x.py", "content": "unclosed'}]),
        sse_turn("I'll try again."))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("write x.py")

    skipped = h.wait_for(Event.TOOL_SKIPPED)
    assert skipped["data"]["kind"] in ("truncated", "unparseable")
    h.wait_for(Event.TURN_FINISHED)
    assert not h.of(Event.TOOL_PROPOSED)
    # And the model is replayed "{}" rather than its own broken JSON.
    replayed = [m for m in gateway.requests[-1]["messages"]
                if m.get("role") == "assistant" and m.get("tool_calls")]
    assert replayed[-1]["tool_calls"][0]["function"]["arguments"] == "{}"


def test_plan_tool_emits_a_structured_plan(gateway, harness):
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "create_plan", "arguments":
                              json.dumps({"steps": ["scaffold", "test", "ship"]})}]),
        sse_turn("planned"))
    h = harness()
    h.session.submit("plan it")

    plan = h.wait_for(Event.PLAN_UPDATED)
    assert plan["data"]["total"] == 3
    assert [s["title"] for s in plan["data"]["steps"]] == ["scaffold", "test", "ship"]
    assert not h.of(Event.TOOL_PROPOSED), "plan tools are bookkeeping, never gated"


def test_ask_user_blocks_the_turn_until_answered(gateway, harness):
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "ask_user", "arguments": json.dumps(
            {"questions": [{"question": "Which stack?", "header": "Stack",
                            "options": [{"label": "Vite"}, {"label": "Next"}]}]})}]),
        sse_turn("Vite it is."))
    h = harness()
    h.session.submit("build an app")

    ask = h.wait_for(Event.ASK_USER)
    assert ask["data"]["questions"][0]["question"] == "Which stack?"
    time.sleep(0.1)
    assert not h.of(Event.TURN_FINISHED), "turn must be parked on the question"

    h.session.respond_ask(ask["data"]["token"], ["Vite"])
    h.wait_for(Event.TURN_FINISHED)
    answer = [m for m in gateway.requests[-1]["messages"] if m.get("role") == "tool"][-1]
    assert "Vite" in answer["content"]


def test_interrupt_unparks_a_waiting_approval(gateway, harness, tmp_path):
    """A stop click while a dialog is open must not strand the worker."""
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "run_bash",
                              "arguments": json.dumps({"command": "echo hi"})}]),
        sse_turn("ok"))
    h = harness(mode=Mode.DEFAULT)
    h.session.submit("run something")
    h.wait_for(Event.TOOL_PROPOSED)

    h.session.interrupt()
    h.wait_for(Event.TURN_ABORTED, timeout=10)
    assert len(h.session.pending) == 0, "no slot may be left dangling"


def test_stop_lands_while_the_stream_is_silent(gateway, harness):
    """A model thinking with nothing on the wire used to hold the Stop
    click hostage: the interrupt was only checked per chunk, so it landed
    when the NEXT chunk arrived. It must land within a fraction of a
    second, and the connection must be torn down, not drained to the end."""
    turn = sse_turn("partial ")
    tail = turn.pop()                       # usage/cost line
    gateway.script(turn + ["__stall__:20"] + [json.dumps(
        {"model": "test/model", "choices": [{"delta": {"content": "never seen"}}]}), tail])
    h = harness()
    h.session.submit("think hard")
    h.wait_for(Event.ASSISTANT_DELTA)       # stream is open and now silent

    t0 = time.time()
    h.session.interrupt()
    done = h.wait_for(Event.TURN_ABORTED, timeout=10)
    assert time.time() - t0 < 1.5, "stop must not wait for the next chunk"
    assert done["data"]["hops"] == 1
    assert "never seen" not in "".join(
        e["data"]["text"] for e in h.of(Event.ASSISTANT_DELTA))


def test_stop_kills_a_running_shell_command(gateway, harness):
    """Stop during `sleep 30` used to wait for the command (or its 120s
    timeout). The tree is killed and the turn aborts within ~1s."""
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "run_bash", "arguments":
                              json.dumps({"command": "sleep 30; echo late"})}]),
        sse_turn("unreachable"))
    h = harness(mode=Mode.AUTO)
    h.session.submit("run it")
    h.wait_for(Event.TOOL_STARTED)

    t0 = time.time()
    h.session.interrupt()
    h.wait_for(Event.TURN_ABORTED, timeout=10)
    assert time.time() - t0 < 2.0, "stop must kill the command, not wait on it"
    res = h.of(Event.TOOL_RESULT)
    assert res and "interrupted" in res[-1]["data"]["result"].lower()
    assert "late" not in res[-1]["data"]["result"]


def test_in_band_gateway_error_surfaces_instead_of_hanging(gateway, harness):
    gateway.script(sse_turn(error="model is not available"))
    h = harness()
    h.session.submit("hi")
    err = h.wait_for(Event.ERROR)
    assert "not available" in err["data"]["message"]


def test_bash_runs_inside_the_workspace(gateway, harness, tmp_path):
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "run_bash",
                              "arguments": json.dumps({"command": "pwd > where.txt"})}]),
        sse_turn("ok"))
    h = harness(mode=Mode.AUTO)
    h.session.submit("where am i")
    h.wait_for(Event.TURN_FINISHED)
    assert (tmp_path / "where.txt").read_text().strip() == str(tmp_path.resolve())


def test_smart_routing_actually_picks_a_model(gateway, harness, monkeypatch):
    """Regression: classify() returns (cohort, confidence).

    Passing that tuple straight into router.pick() made every frontier
    lookup miss, so smart routing quietly did nothing — and because routing
    is designed to fail OPEN, no error ever surfaced. The only way to catch
    it is to assert a pick actually happens.
    """
    from meshharness.core import router

    table = router.load_table()
    assert table, "the routing table should be bundled"
    cohort, confidence = router.classify("refactor this module", has_tools=True)
    assert isinstance(cohort, str) and 0 <= confidence <= 1

    # A catalog containing exactly the models the table's frontier names.
    frontier = (table.get("frontiers") or {}).get(cohort) or []
    assert frontier, f"no frontier for cohort {cohort!r}"
    catalog = [{"id": m} for m in frontier[:6]]

    gateway.script(sse_turn("routed reply"))
    h = harness(route_mode="smart")
    h.session.catalog = catalog
    h.session.submit("refactor this module and fix the failing test")
    h.wait_for(Event.TURN_FINISHED)

    routed = h.of(Event.ROUTED)
    assert routed, "smart routing produced no pick at all"
    assert isinstance(routed[0]["data"]["cohort"], str)
    assert routed[0]["data"]["model"] in [m["id"] for m in catalog]


def test_start_server_failure_reports_a_result(gateway, harness, tmp_path):
    """Regression: the tool card said "running…" forever.

    TOOL_STARTED creates the card in a running state; start_server returned
    its outcome as a string but never emitted TOOL_RESULT, so a server that
    failed to start still looked like it was starting — observed live on a
    `npm run dev` with no node_modules.
    """
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "start_server", "arguments":
                              json.dumps({"command": "exit 1"})}]),
        sse_turn("that failed"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("start it")
    h.wait_for(Event.TURN_FINISHED, timeout=90)

    results = [e for e in h.of(Event.TOOL_RESULT) if e["data"]["name"] == "start_server"]
    assert results, "start_server must report a result, pass or fail"
    assert results[0]["data"]["ok"] is False
    assert "Error" in results[0]["data"]["result"]


def test_cost_falls_back_to_catalog_pricing(gateway, harness):
    """Some upstreams bill tokens and report cost 0. Showing $0.000000 for a
    186k-token turn is worse than showing an estimate and saying so."""
    gateway.script(sse_turn(
        "done",
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000,
               "total_tokens": 2_000_000},
        cost=0.0, model="test/model"))
    h = harness()
    # test/model in the fake catalog: $1.00/1M in, $3.00/1M out
    h.session.catalog = [{"id": "test/model", "context_length": 128000,
                          "pricing": {"prompt_usd_per_1m": "1.00",
                                      "completion_usd_per_1m": "3.00"}}]
    h.session.submit("hi")
    done = h.wait_for(Event.TURN_FINISHED)

    assert done["data"]["cost"] > 0, "a billed turn must not report $0"
    assert done["data"]["cost_estimated"] is True, "an estimate must be labelled"


def test_cost_estimate_uses_the_requested_model_when_the_echo_differs(gateway, harness):
    """The gateway echoes the upstream's own name ("deepseek-flash") for the
    catalog id we asked for ("deepseek/deepseek-v4.1-flash"). Pricing by the
    echo matched nothing and 1.3M tokens showed as $0.0000 — the model we
    REQUESTED is the catalog id, so price that first."""
    gateway.script(sse_turn(
        "done",
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 100_000,
               "total_tokens": 1_100_000},
        cost=0.0, model="deepseek-flash"))
    h = harness()
    h.session.cfg["model"] = "deepseek/deepseek-v4.1-flash"
    h.session.catalog = [{"id": "deepseek/deepseek-v4.1-flash", "context_length": 128000,
                          "pricing": {"prompt_usd_per_1m": "0.30",
                                      "completion_usd_per_1m": "1.20"}}]
    h.session.submit("hi")
    done = h.wait_for(Event.TURN_FINISHED)

    assert done["data"]["cost"] == pytest.approx(0.30 + 0.12)
    assert done["data"]["cost_estimated"] is True


def test_cost_is_the_gateways_billed_figure_when_the_request_settles(gateway, harness):
    """The chat completion body carries no cost on any API version, but the
    gateway bills every request and answers GET /usage/requests/{id} with
    `cost_usd` ~250ms after the stream closes. That figure beats both the SSE
    tail and the catalog estimate — and it needs no catalog match, so the
    "deepseek-flash" echo that defeated the estimator is irrelevant."""
    gateway.request_costs["req_test_1"] = 0.0421
    gateway.script(sse_turn(
        "done",
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 100_000,
               "total_tokens": 1_100_000},
        cost=0.0, model="deepseek-flash"))
    h = harness()
    h.session.cfg["model"] = "deepseek/deepseek-v4.1-flash"
    h.session.catalog = []          # nothing to estimate from — must not matter
    h.session.submit("hi")
    done = h.wait_for(Event.TURN_FINISHED)

    assert done["data"]["cost"] == pytest.approx(0.0421)
    assert done["data"]["cost_estimated"] is False, "a billed figure is not an estimate"
    assert done["data"]["tokens"] == 1_100_000


def test_reopening_a_workspace_keeps_the_conversation(gateway, harness, tmp_path):
    """Switching projects must not orphan the transcript."""
    gateway.script(sse_turn("first answer"))
    h = harness()
    h.session.submit("hello")
    h.wait_for(Event.TURN_FINISHED)
    before = len(h.session.state["messages"])
    assert before > 1

    h.session.start_again()          # what reopening the project now does
    assert len(h.session.state["messages"]) == before, "history must survive"
    resumed = [e for e in h.of(Event.SESSION_STARTED) if e["data"].get("resumed")]
    assert resumed, "the client needs to know this is a resume, not a new session"


# ---- audit round 2026-08-25: regression tests for the fixes -------------

def test_compaction_fold_phase_is_reachable():
    """The fold phase used to be dead: every caller passed neither limit nor
    aggressive, so compact_history only ever truncated and the second pass
    exhausted itself, making the next context error fatal."""
    from meshharness.core import compact
    msgs = [{"role": "system", "content": "sys"}]
    for i in range(30):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": None,
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "read_file",
                                                  "arguments": '{"path":"x"}'}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}",
                     "content": "y" * 2000})
    state = {"messages": list(msgs), "session_reads": {}}
    rep = compact.compact_history(state, limit=8000, aggressive=False)
    assert rep.get("folded", 0) > 0, "fold must run when a limit is supplied"


def test_credential_dirs_are_denied_in_auto_modes():
    """~/.mesh-code (this app's own key) must never auto-read/redirect."""
    from meshharness.core import safety
    from meshharness.core.permissions import Mode
    ok, _ = safety.is_path_safe_for_auto_read("~/.mesh-code/credentials", Mode.BYPASS)
    assert not ok, "BYPASS must still refuse the app's own credential dir"
    ok, _ = safety.is_command_safe_for_auto("cat ~/.mesh-code/credentials", Mode.AUTO)
    assert not ok, "AUTO must refuse reading the credential dir"
    ok, _ = safety.is_command_safe_for_auto(
        "echo x >> ~/.mesh-code/config.json", Mode.AUTO)
    assert not ok, "AUTO must refuse a redirect into the config dir"


def test_pricing_suffix_match_respects_word_boundary():
    """gpt-4o must not fall back to gpt-4's pricing."""
    from meshharness.core.pricing import find_model
    assert find_model("gpt-4o-2024-08-06", [{"id": "openai/gpt-4"}]) is None
    hit = find_model("gpt-4o-mini-2024-07-18", [{"id": "openai/gpt-4o-mini"}])
    assert hit and hit["id"] == "openai/gpt-4o-mini"


def test_terminal_stdin_command_does_not_report_premature_done():
    """A command that reads stdin used to swallow the completion marker."""
    import time
    from meshharness.terminal import Terminal
    events = []
    t = Terminal("/tmp", lambda **d: events.append(d))
    t.start()
    try:
        t.run("head -1")
        deadline = time.time() + 4
        while time.time() < deadline:
            done = [e for e in events if e.get("done")]
            if done:
                assert done[0]["exit_code"] == "0", "clean EOF, not a garbage code"
                break
            time.sleep(0.05)
        else:
            raise AssertionError("head -1 hung (marker swallowed)")
    finally:
        t.close()


def test_deleting_a_symlink_removes_the_link_not_its_target(tmp_path):
    """delete('current') must unlink the symlink, never rmtree its target."""
    import os
    from meshharness import files
    real = tmp_path / "releases"
    real.mkdir()
    (real / "keep.txt").write_text("precious")
    link = tmp_path / "current"
    os.symlink(real, link)
    files.delete(tmp_path, "current")
    assert not link.exists() and not link.is_symlink(), "link is gone"
    assert (real / "keep.txt").read_text() == "precious", "target survives"


# ---- smart-routing cascade (within-turn escalate / settle) --------------

def _agentic_catalog():
    """A catalog spanning the agentic cohort's frontier, so a pick at any
    effort resolves to a real model."""
    from meshharness.core import router
    table = router.load_table()
    frontier = (table.get("frontiers") or {}).get("agentic") or []
    if not frontier:
        # fall back to any cohort with a frontier
        for c, ms in (table.get("frontiers") or {}).items():
            if ms:
                frontier = ms
                break
    return [{"id": m, "context_length": 200000} for m in frontier[:8]]


def test_stall_escalates_the_smart_pick_mid_turn(gateway, harness):
    """A repeated identical tool batch is a struggle signal — the cascade
    must climb the effort ladder instead of just nudging the same model."""
    same = [{"id": "c1", "name": "run_bash",
             "arguments": json.dumps({"command": "ls"})}]
    # Four identical batches trip the stall detector (period-1, nudge at 3),
    # then a clean finish.
    gateway.script(
        sse_turn(tool_calls=same),
        sse_turn(tool_calls=same),
        sse_turn(tool_calls=same),
        sse_turn(tool_calls=same),
        sse_turn("done"),
    )
    h = harness(mode=Mode.AUTO, route_mode="smart", route_effort="auto")
    h.session.catalog = _agentic_catalog()
    h.session.submit("run the build repeatedly")
    h.wait_for(Event.TURN_FINISHED)

    assert h.session.state.get("_smart_escalations", 0) >= 1, \
        "a stall under smart routing must escalate the pick"


def test_escalate_then_de_escalate_walks_difficulty_up_and_back(harness):
    """Method-level: escalate raises the effort tier and re-picks; enough
    clean hops settle it back toward the turn's original floor."""
    h = harness(route_mode="smart", route_effort="auto")
    h.session.catalog = _agentic_catalog()
    st = h.session.state
    st["_smart_cohort"] = "agentic"
    st["_smart_difficulty"] = "low"
    st["_smart_base_difficulty"] = "low"
    st["_smart_last"] = None
    st["_smart_escalations"] = 0
    st["_smart_clean_hops"] = 0

    assert h.session._smart_escalate("test") is True
    assert st["_smart_difficulty"] == "mid", "escalate moves one tier up"
    assert st["_smart_escalations"] == 1

    # Not enough clean hops yet — stays put.
    st["_smart_clean_hops"] = 1
    h.session._smart_de_escalate()
    assert st["_smart_difficulty"] == "mid"

    # Enough clean hops — settles back down toward the floor.
    st["_smart_clean_hops"] = 3
    h.session._smart_de_escalate()
    assert st["_smart_difficulty"] == "low", "settles back to the base floor"
    assert st["_smart_escalations"] == 0


def test_route_pin_is_never_clobbered_by_the_cascade(gateway, harness):
    """The user's pinned model (cfg['model']) must survive routing — the pick
    lives in _smart_pick, so the pin can always be fallen back to."""
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "run_bash",
                              "arguments": json.dumps({"command": "ls"})}]),
        sse_turn("done"))
    h = harness(mode=Mode.AUTO, route_mode="smart")
    h.session.catalog = _agentic_catalog()
    h.session.submit("do a thing")
    h.wait_for(Event.TURN_FINISHED)
    assert h.session.cfg["model"] == "test/model", \
        "the pinned model must not be overwritten by a smart pick"


# ---- start_server reliability (ported from the CLI's hardened version) ---

def test_start_server_honours_the_port_in_the_command(gateway, harness):
    """`http.server 8123` binds 8123 — waiting on an auto-picked port
    instead is the exact live failure the CLI fixed."""
    import urllib.request
    h = harness()
    r = h.session._start_server({"command": "python3 -m http.server 18123"}, "c1")
    assert "http://localhost:18123" in r, r
    assert urllib.request.urlopen("http://localhost:18123", timeout=3).status == 200


def test_start_server_refuses_to_restart_its_own_server(gateway, harness):
    h = harness()
    r1 = h.session._start_server({"command": "python3 -m http.server 18124"}, "c1")
    assert "Server started" in r1
    r2 = h.session._start_server({"command": "python3 -m http.server 18124"}, "c2")
    assert "YOUR OWN server" in r2, "restart must be refused with guidance, not looped"


def test_start_server_survives_chatty_prebind_output(gateway, harness):
    """>64KB of output before binding used to fill the unread pipe and
    deadlock the server, timing out a working command."""
    import time as _t
    chatty = ("python3 -c \"import sys;print('x'*200000);sys.stdout.flush();"
              "import http.server,socketserver;"
              "socketserver.TCPServer(('127.0.0.1',18125),"
              "http.server.SimpleHTTPRequestHandler).serve_forever()\"")
    h = harness()
    t0 = _t.time()
    r = h.session._start_server({"command": chatty, "port": 18125}, "c1")
    assert "Server started" in r, r
    assert _t.time() - t0 < 10, "must not stall on a full pipe"


def test_start_server_timeout_names_run_bash_for_portless_commands(gateway, harness):
    """A GUI app / worker never opens a port — the error must steer the
    model to run_bash instead of reading as a mysterious failure."""
    h = harness()
    r = h.session._start_server({"command": "sleep 60", "wait_seconds": 5}, "c1")
    assert "run_bash instead" in r, r


# ---- workspace containment for auto-approved bash ------------------------

def test_auto_mode_asks_before_bash_writes_outside_the_workspace(tmp_path):
    """`ln -sfn <ws> /tmp/x` sailed through AUTO with no question — no
    dangerous pattern, no redirect, yet it writes somewhere the user never
    opened. AUTO now applies the same containment write_file already has."""
    from meshharness.core import safety
    from meshharness.core.permissions import Mode
    ok, why = safety.is_command_safe_for_auto(
        f'ln -sfn "{tmp_path}" /tmp/nimbus-crm', Mode.AUTO, tmp_path)
    assert not ok and "outside the open project" in why
    ok, _ = safety.is_command_safe_for_auto(
        "echo hi > /tmp/log.txt", Mode.AUTO, tmp_path)
    assert not ok, "AUTO redirects outside the workspace must ask"
    # Ordinary in-project work still flows.
    for cmd in ("npm run build", "mkdir -p src/components", "echo x > out.log",
                "cp /etc/hosts ./backup.txt", f"touch {tmp_path}/a.txt"):
        ok, why = safety.is_command_safe_for_auto(cmd, Mode.AUTO, tmp_path)
        assert ok, f"{cmd!r} should auto-approve in AUTO: {why}"
    # BYPASS keeps its contract: outside-workspace allowed, denylist refused.
    ok, _ = safety.is_command_safe_for_auto(
        "ln -sfn x /tmp/nimbus-crm", Mode.BYPASS, tmp_path)
    assert ok, "BYPASS explicitly waives containment"


def test_single_file_rm_and_heredoc_scripts_auto_approve_but_recursive_rm_asks():
    """`rm -f tmp.html` inside a Python heredoc tripped the destructive-rm
    rule on 12 of 51 commands in one session, each one a modal in BYPASS.
    Only a RECURSIVE rm is the shape worth a question."""
    from meshharness.core import safety
    from meshharness.core.permissions import Mode
    heredoc = ("cd /ws && /usr/bin/python3 - <<'PY'\nimport io, os\n"
               "html = io.open('index.html').read()\n"
               "os.system('rm -f test-out.html')\nPY")
    for cmd in ("rm -f build/tmp.html", "rm build/a.txt build/b.txt", heredoc,
                "git rm --cached x.log"):
        for mode in (Mode.AUTO, Mode.BYPASS):
            ok, why = safety.is_command_safe_for_auto(cmd, mode)
            assert ok, f"{cmd[:40]!r} should auto-approve in {mode}: {why}"
    for cmd in ("rm -rf node_modules", "rm -fr dist", "rm -Rf build", "rm -r tmp",
                "rm -f /", "rm -f ~/x", "python3 -c \"import os; os.system('rm -rf /tmp/x')\""):
        ok, why = safety.is_command_safe_for_auto(cmd, Mode.BYPASS)
        assert not ok, f"{cmd!r} must ask even in BYPASS"


def test_images_from_earlier_turns_are_retired_when_a_new_turn_starts(gateway, harness):
    """A 640KB pasted screenshot was re-sent on every one of ~25 hops after
    it arrived. It stays for its own turn and becomes a one-line note after."""
    gateway.script(sse_turn("saw it"), sse_turn("ok"))
    h = harness()
    big = "data:image/png;base64," + ("A" * 20_000)
    h.session.submit("look", [{"name": "shot.png", "data_url": big}])
    h.wait_for(Event.TURN_FINISHED)
    sent = gateway.requests[0]["messages"]
    assert any(isinstance(m.get("content"), list)
               and any(p.get("type") == "image_url" for p in m["content"])
               for m in sent), "the image must reach the model in its own turn"

    h.session.submit("now change the heading")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    sent = gateway.requests[1]["messages"]
    assert not any(isinstance(m.get("content"), list)
                   and any(p.get("type") == "image_url" for p in m["content"])
                   for m in sent), "old images must not ride along"
    first_user = next(m for m in sent if m["role"] == "user")
    assert "attached here earlier" in json.dumps(first_user["content"])
    before = len(json.dumps(gateway.requests[0]["messages"]))
    assert len(json.dumps(sent)) < before - 15_000, "the 20KB image must be gone"


# ---- persistence, new chat, journal (the restart-amnesia fixes) ----------

def test_session_survives_a_restart_with_its_conversation(gateway, harness, tmp_path):
    """Snapshot + restore: the whole reason the app stopped feeling broken."""
    from meshharness.session import Session
    from meshharness.core import config as cfg
    gateway.script(sse_turn("the answer is 42"))
    h = harness()
    h.session.submit("what's the answer?")
    h.wait_for(Event.TURN_FINISHED)
    h.session.save_state()

    snap = json.loads((cfg.SESSIONS_DIR / f"{h.session.id}.json").read_text())
    assert snap["workspace"] == str(h.session.workspace)

    events = []
    restored = Session.restore(snap, dict(h.session.cfg), lambda ev: events.append(ev))
    try:
        texts = [m.get("content") for m in restored.state["messages"]]
        assert any("the answer is 42" in str(t) for t in texts), \
            "the conversation must survive the restart"
        hist = restored.history_payload()
        assert any(m["kind"] == "user" for m in hist)
        assert any(m["kind"] == "assistant" and "42" in m["text"] for m in hist)
    finally:
        restored.close()


def test_new_chat_resets_conversation_but_keeps_the_project(gateway, harness):
    gateway.script(sse_turn("first thing"), sse_turn("fresh start"))
    h = harness()
    h.session.submit("hello")
    h.wait_for(Event.TURN_FINISHED)
    before = len(h.session.state["messages"])
    assert before > 1

    assert h.session.new_chat() is True
    assert len(h.session.state["messages"]) == 1, "only the system prompt remains"
    assert any(e["type"] == Event.CHAT_CLEARED for e in h.events)

    h.session.submit("again")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert any("fresh start" in str(m.get("content"))
               for m in h.session.state["messages"])


def test_journal_records_turns_and_feeds_the_next_session(gateway, harness, tmp_path):
    """The PM notebook: a turn's servers/plan land in the journal, and a NEW
    session's system prompt carries them — so it runs instead of rebuilds."""
    from meshharness.core import journal
    journal.record_turn(
        tmp_path,
        plan={"steps": [{"title": "Build the dashboard", "status": "completed"},
                        {"title": "Wire the API", "status": "pending"}]},
        servers=[{"cmd": "cd frontend && npm run dev", "url": "http://localhost:4000"}],
        files_written=["a.tsx"], model="test/model", ok=True)
    journal.note(tmp_path, "vite ignores PORT env; frontend always runs on 4000")

    text = journal.load(tmp_path)
    assert "cd frontend && npm run dev" in text
    assert "[x] Build the dashboard" in text
    assert "[ ] Wire the API" in text
    assert "vite ignores PORT env" in text

    gateway.script(sse_turn("ok"))
    h = harness(repo_memory=True)   # same tmp_path workspace
    system = h.session.state["messages"][0]["content"]
    assert "PROJECT JOURNAL" in system
    assert "npm run dev" in system, "the next session must open knowing how to run it"


def test_session_cost_and_tokens_survive_the_full_persistence_chain(gateway, harness):
    """Cost is money — it must survive snapshot → restore → describe, and a
    post-restore turn must ADD to the restored total, not restart from 0."""
    from meshharness.session import Session
    from meshharness.core import config as cfg
    gateway.script(sse_turn("one"), sse_turn("two"))
    h = harness()
    h.session.submit("first")
    h.wait_for(Event.TURN_FINISHED)
    cost1 = h.session.session_cost
    tok1 = h.session.session_tokens
    assert cost1 > 0 and tok1 > 0, "the fake gateway reports usage+cost"
    h.session.save_state()

    snap = json.loads((cfg.SESSIONS_DIR / f"{h.session.id}.json").read_text())
    assert snap["session_cost"] == cost1
    assert snap["session_tokens"] == tok1

    events = []
    restored = Session.restore(snap, dict(h.session.cfg), lambda ev: events.append(ev))
    try:
        assert restored.session_cost == cost1
        assert restored.session_tokens == tok1
        desc = restored.describe()
        assert desc["session_cost"] == round(cost1, 6)
        assert desc["session_tokens"] == tok1, \
            "describe() must carry tokens or the UI shows 0 after restart"

        restored.submit("second")
        deadline = __import__("time").time() + 10
        while __import__("time").time() < deadline:
            if any(e["type"] == Event.TURN_FINISHED for e in events):
                break
            __import__("time").sleep(0.02)
        assert restored.session_cost > cost1, "post-restore spend must ACCUMULATE"
        assert restored.session_tokens > tok1
    finally:
        restored.close()
