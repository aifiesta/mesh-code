"""The system prompt is two messages: one that is identical for every chat,
then one for this project.

Providers that cache by themselves bill the opening of a request at a few
percent of the price when an earlier request began with the same text. The
first step of a chat can only match ANOTHER chat's opening, so nothing that
differs by project, chat or day may sit in the first message. Measured live:
another project's first step went from 0 to 3,200 of 3,389 input tokens
cached.
"""
import json

from fake_gateway import sse_turn
from meshharness.core import compact, journal, memory, tools
from meshharness.core.permissions import Mode
from meshharness.protocol import Event

CFG = {"system": "You are a helpful coding assistant. Be concise.", "repo_memory": False}


def test_the_first_message_is_the_same_for_every_project(tmp_path):
    a = tools.build_system_messages(CFG, tmp_path / "alpha")
    b = tools.build_system_messages(CFG, tmp_path / "beta")
    assert [m["role"] for m in a] == ["system", "system"]
    assert a[0]["content"] == b[0]["content"]
    assert a[1]["content"] != b[1]["content"]
    assert "alpha" not in a[0]["content"] and str(tmp_path) not in a[0]["content"]


def test_everything_about_the_project_is_in_the_second_message(tmp_path):
    root = tmp_path / "alpha"
    root.mkdir()
    memory.append_note(root, "uses pnpm, not npm")
    journal.note(root, "the API base path is /v2")
    cfg = dict(CFG, repo_memory=True, output_style="concise")
    stable, project = (m["content"] for m in tools.build_system_messages(
        cfg, root, extra=journal.prompt_block(root)))
    for text in (f"Working directory: {root.resolve()}", "REPO MEMORY", "uses pnpm",
                 "PROJECT JOURNAL", "the API base path is /v2"):
        assert text in project, text
        assert text not in stable, text
    # The output style still comes last of all: it is about prose, and
    # recency is what cheap models follow.
    from meshharness.core import styles
    assert project.rstrip().endswith(styles.block("concise").rstrip())
    assert styles.block("concise") not in stable


def test_the_stable_message_holds_the_standing_rules_and_the_users_base_prompt(tmp_path):
    stable = tools.stable_system_prompt(dict(CFG, system="Speak like a pirate."))
    assert stable.startswith("Speak like a pirate.")
    for rule in ("ASK WHEN GENUINELY FORKED", "PLAN BEFORE ACTING", "COMPLETENESS", "SECURITY"):
        assert rule in stable
    assert "Working directory" not in stable


def test_nothing_was_lost_in_the_split(tmp_path):
    """The two messages together are the old single prompt's text, reordered."""
    stable, project = (m["content"] for m in tools.build_system_messages(CFG, tmp_path))
    assert tools.build_system_prompt(CFG, tmp_path) == stable + "\n\n" + project
    assert project.startswith(f"Working directory: {tmp_path.resolve()}\n")
    assert "do not ask the user to run commands." in project


def test_a_chat_opens_with_both_messages_and_sends_them_first(gateway, harness, tmp_path):
    gateway.script(sse_turn("hello"))
    h = harness()
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    sent = gateway.requests[-1]["messages"]
    assert [m["role"] for m in sent[:3]] == ["system", "system", "user"]
    assert sent[0]["content"] == tools.stable_system_prompt(h.session.cfg)
    assert sent[1]["content"].startswith(f"Working directory: {tmp_path.resolve()}")
    assert sent[2]["content"] == "hi"


def test_two_sessions_in_different_projects_send_the_same_opening(gateway, harness, tmp_path):
    from meshharness.session import Session
    gateway.script(sse_turn("one"), sse_turn("two"))
    h = harness()
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    other = tmp_path / "elsewhere"
    other.mkdir()
    s2 = Session("t2", other, dict(h.session.cfg), lambda ev: None)
    s2.start()
    first, second = gateway.requests[-1], s2.state["messages"]
    assert first["messages"][0] == second[0], "byte for byte, so a provider can cache it"
    assert first["messages"][1] != second[1]
    assert json.dumps(first["tools"]) == json.dumps(tools.TOOLS)
    s2.close()


def test_new_chat_and_a_fresh_restore_rebuild_both_messages(gateway, harness, tmp_path):
    from meshharness.session import Session
    gateway.script(sse_turn("hello"))
    h = harness()
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    assert h.session.new_chat()
    assert [m["role"] for m in h.session.state["messages"]] == ["system", "system"]
    again = Session.restore({"id": "t9", "workspace": str(tmp_path), "messages": []},
                            dict(h.session.cfg), lambda ev: None)
    assert [m["role"] for m in again.state["messages"]] == ["system", "system"]


def test_a_chat_saved_with_the_old_single_message_is_left_as_it_was(harness, tmp_path):
    from meshharness.session import Session
    h = harness()
    old = [{"role": "system", "content": "the old single prompt"},
           {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    again = Session.restore({"id": "t8", "workspace": str(tmp_path), "messages": old},
                            dict(h.session.cfg), lambda ev: None)
    assert again.state["messages"] == old


def test_compaction_keeps_both_system_messages(tmp_path):
    messages = tools.build_system_messages(CFG, tmp_path) + [{"role": "user", "content": "go"}]
    for i in range(30):
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "run_bash", "arguments": json.dumps({"command": "ls"})}}]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 4000 + "\n[exit 0]"})
    state = {"messages": messages, "session_reads": {}}
    stable, project = messages[0]["content"], messages[1]["content"]
    done = compact.compact_history(state, limit=2000)
    assert done and done["folded"] >= 1
    assert state["messages"][0] == {"role": "system", "content": stable}
    assert state["messages"][1] == {"role": "system", "content": project}
    assert state["messages"][2] == {"role": "user", "content": "go"}
