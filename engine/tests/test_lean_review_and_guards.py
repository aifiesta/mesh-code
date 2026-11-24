"""24 Nov fixes around Deep work helpers.

- the lead's join step is told to review lean (run the suite, read the code once)
- Auto reads through a folder inside the project that links elsewhere
  (a linked node_modules) without asking; the denylist still bites
- a tool that does not exist is refused at once, never sent for approval
- a helper never asks the user for something the safety check blocked
"""
import json
from pathlib import Path

import pytest
from fake_gateway import sse_turn

from meshharness import helpers as H
from meshharness.core import safety
from meshharness.core.permissions import Mode
from meshharness.protocol import Decision, Event

from test_deep_helpers import LEAD, TWO, _arm_always, _delegate_call, _has_tool_result, \
    _last_user, _responder, _simple_helper


# ---------------------------------------------------------------------------
# lean review wording
# ---------------------------------------------------------------------------
def test_review_ask_keeps_the_join_step_lean():
    ask = H.REVIEW_ASK
    assert "Run the full test suite once" in ask
    assert "once, whole and in one go" in ask
    assert "Do not read the test files unless a test fails" in ask
    assert "Do not tidy" in ask
    assert "run the full suite once more" in ask


def test_delegate_tool_puts_every_changed_file_in_a_piece():
    desc = H.DELEGATE_TOOL["function"]["description"]
    assert "README and docs included, belongs to one piece" in desc
    assert "read the changed code once" in desc


# ---------------------------------------------------------------------------
# reads through a linked folder inside the project
# ---------------------------------------------------------------------------
@pytest.fixture
def linked(tmp_path_factory):
    ws = tmp_path_factory.mktemp("ws")
    shared = tmp_path_factory.mktemp("shared-node-modules")
    (shared / "pkg").mkdir()
    (shared / "pkg" / "index.js").write_text("module.exports = 1\n")
    (ws / "node_modules").symlink_to(shared, target_is_directory=True)
    return ws, shared


def _auto_read(path, ws):
    return safety.is_path_safe_for_auto_read(path, Mode.AUTO, ws, workspace_only=True)


def test_auto_reads_through_a_linked_folder_inside_the_project(linked):
    ws, _ = linked
    ok, why = _auto_read("node_modules/pkg/index.js", ws)
    assert ok, why
    ok, why = _auto_read(str(ws / "node_modules" / "pkg" / "index.js"), ws)
    assert ok, why


def test_the_real_outside_folder_still_asks(linked):
    ws, shared = linked
    ok, why = _auto_read(str(shared / "pkg" / "index.js"), ws)
    assert not ok and "outside the current project" in why


def test_dot_dot_out_of_the_project_still_asks(linked, tmp_path_factory):
    ws, _ = linked
    other = tmp_path_factory.mktemp("outside")
    (other / "secret.txt").write_text("x")
    for p in (f"node_modules/../../{other.name}/secret.txt",
              f"x/../../{other.name}/secret.txt",
              f"../{other.name}/secret.txt"):
        ok, why = _auto_read(p, ws)
        assert not ok, p


def test_a_link_into_a_denylisted_place_still_asks(tmp_path):
    (tmp_path / "keys").symlink_to(Path.home() / ".ssh", target_is_directory=True)
    ok, why = _auto_read("keys/id_ed25519", tmp_path)
    assert not ok and "denylist" in why


def test_a_link_to_an_env_file_still_asks(tmp_path, tmp_path_factory):
    other = tmp_path_factory.mktemp("conf")
    (other / ".env").write_text("KEY=1\n")
    (tmp_path / "conf").symlink_to(other, target_is_directory=True)
    ok, why = _auto_read("conf/.env", tmp_path)
    assert not ok and "environment" in why


def test_writes_through_the_link_still_ask(linked):
    ws, _ = linked
    ok, why = safety.is_path_safe_for_auto_write("node_modules/pkg/index.js", Mode.AUTO, ws)
    assert not ok and "outside" in why


def test_session_auto_reads_a_linked_node_modules_without_asking(
        gateway, harness, tmp_path, tmp_path_factory):
    shared = tmp_path_factory.mktemp("nm")
    (shared / "lib.js").write_text("// lib\n")
    (tmp_path / "node_modules").symlink_to(shared, target_is_directory=True)
    gateway.script(
        sse_turn(tool_calls=[{"id": "r1", "name": "read_file",
                              "arguments": json.dumps({"path": "node_modules/lib.js"})}]),
        sse_turn("read it"))
    h = harness(mode=Mode.AUTO)
    h.session.submit("look")
    h.wait_for(Event.TURN_FINISHED, timeout=10)
    assert not h.of(Event.TOOL_PROPOSED)
    res = h.of(Event.TOOL_RESULT)[0]["data"]
    assert res["ok"] and "// lib" in res["result"]


# ---------------------------------------------------------------------------
# a tool that does not exist
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,hint", [("bash", "Use run_bash."),
                                       ("frobnicate", "The tools are:")])
def test_an_unknown_tool_is_refused_without_asking(gateway, harness, name, hint):
    gateway.script(
        sse_turn(tool_calls=[{"id": "u1", "name": name,
                              "arguments": json.dumps({"command": "ls"})}]),
        sse_turn("ok"))
    h = harness(mode=Mode.DEFAULT)
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED, timeout=10)
    assert not h.of(Event.TOOL_PROPOSED), "no approval card for a call that cannot run"
    started = h.of(Event.TOOL_STARTED)[0]["data"]
    assert started["call_id"] == "u1" and started["name"] == name
    res = h.of(Event.TOOL_RESULT)[0]["data"]
    assert not res["ok"] and f"no tool named {name!r}" in res["result"] and hint in res["result"]
    tool_msg = [m for m in gateway.requests[-1]["messages"] if m.get("role") == "tool"][-1]
    assert hint in tool_msg["content"]


# ---------------------------------------------------------------------------
# helpers never ask for what the safety check blocked
# ---------------------------------------------------------------------------
def _alpha_does(call):
    """Alpha's first step makes `call`; then it reports what it got back."""
    def helper(payload):
        part = _last_user(payload)
        if "YOUR PART: Alpha" in part:
            if not _has_tool_result(payload):
                return sse_turn(tool_calls=[call])
            tool_msg = [m for m in payload["messages"] if m.get("role") == "tool"][-1]
            return sse_turn("Alpha got: " + tool_msg["content"])
        return _simple_helper(payload)
    return helper


def test_a_helper_writing_outside_the_project_is_told_no_without_asking(
        gateway, harness, tmp_path_factory, monkeypatch):
    _arm_always(monkeypatch)
    scratch = tmp_path_factory.mktemp("scratch") / "notes.txt"
    gateway.responder = _responder(
        [sse_turn(tool_calls=[_delegate_call(TWO)]), sse_turn("joined")],
        _alpha_does({"id": "w", "name": "write_file",
                     "arguments": json.dumps({"path": str(scratch), "content": "x"})}))
    h = harness(mode=Mode.AUTO)
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED, timeout=20)
    assert not h.of(Event.TOOL_PROPOSED)
    assert not scratch.exists()
    result = [m for m in gateway.requests[-1]["messages"] if m.get("role") == "tool"][-1]
    assert "not allowed for a helper" in result["content"]
    assert "inside the project folder" in result["content"]


def test_a_helper_calling_a_tool_it_lacks_is_refused_without_asking(
        gateway, harness, monkeypatch):
    _arm_always(monkeypatch)
    gateway.responder = _responder(
        [sse_turn(tool_calls=[_delegate_call(TWO)]), sse_turn("joined")],
        _alpha_does({"id": "s", "name": "start_server",
                     "arguments": json.dumps({"command": "npm start"})}))
    h = harness(mode=Mode.AUTO)
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED, timeout=20)
    assert not h.of(Event.TOOL_PROPOSED)
    result = [m for m in gateway.requests[-1]["messages"] if m.get("role") == "tool"][-1]
    assert "no tool named 'start_server'" in result["content"]


def test_the_lead_still_asks_for_a_blocked_write(gateway, harness, tmp_path_factory):
    """Only helpers are told no: the lead's blocked call still reaches the user."""
    scratch = tmp_path_factory.mktemp("scratch2") / "notes.txt"
    gateway.script(
        sse_turn(tool_calls=[{"id": "w", "name": "write_file",
                              "arguments": json.dumps({"path": str(scratch), "content": "x"})}]),
        sse_turn("ok"))
    h = harness(mode=Mode.AUTO)
    h.session.submit("go")
    prop = h.wait_for(Event.TOOL_PROPOSED, timeout=10)
    assert prop["data"]["name"] == "write_file"
    assert h.session.respond_approval(prop["data"]["token"], Decision.DENY)
    h.wait_for(Event.TURN_FINISHED, timeout=10)
    assert not scratch.exists()
