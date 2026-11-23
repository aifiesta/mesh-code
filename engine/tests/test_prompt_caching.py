"""Caching is asked for on the models that only cache when asked.

An Anthropic model bills the whole conversation in full on every step unless
the request carries a mark (`cache_control` on a message). Measured at the
gateway (docs/bench/caching_pruning): the same eight steps billed $0.00693
with no marks and $0.00223 with a mark on the last message.
"""
import copy
import json

from fake_gateway import sse_turn
from meshharness.core import caching
from meshharness.core.client import build_payload, stream_chat
from meshharness.core.permissions import Mode
from meshharness.protocol import Event

MARK = {"type": "ephemeral"}
CLAUDE = "anthropic/claude-opus-5.5"


def _marked(messages):
    return [i for i, m in enumerate(messages) if "cache_control" in m]


def _chat():
    return [
        {"role": "system", "content": "the same for every chat"},
        {"role": "system", "content": "about this project"},
        {"role": "user", "content": "what does a.txt say?"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "one"},
    ]


# -- which models, and the switch ----------------------------------------------

def test_only_models_that_cache_when_asked_get_marks():
    assert caching.needs_marks("anthropic/claude-opus-5.5")
    assert caching.needs_marks("Anthropic/claude-haiku-5.5")
    for model in ("deepseek/deepseek-v4.1-flash", "openai/gpt-6.1-sol", "auto", "", None):
        assert not caching.needs_marks(model), model


def test_it_is_on_unless_the_app_was_started_with_it_off(monkeypatch):
    monkeypatch.delenv(caching.OFF_SWITCH, raising=False)
    assert caching.asked_for()
    for value in ("0", "false", "off"):
        monkeypatch.setenv(caching.OFF_SWITCH, value)
        assert not caching.asked_for(), value
    monkeypatch.setenv(caching.OFF_SWITCH, "1")
    assert caching.asked_for()


# -- where the marks go --------------------------------------------------------

def test_the_marks_go_on_the_first_system_message_and_the_last_message():
    out = caching.mark(_chat())
    assert _marked(out) == [0, 4]
    assert out[0]["cache_control"] == MARK and out[4]["cache_control"] == MARK


def test_the_mark_is_a_key_on_the_message_and_the_text_is_untouched():
    """Inside a content part of a tool message the gateway ignores the mark
    without an error, so it must stay a key on the message itself."""
    before = _chat()
    out = caching.mark(before)
    for sent, kept in zip(out, before):
        assert {k: v for k, v in sent.items() if k != "cache_control"} == kept


def test_the_history_passed_in_is_never_changed():
    before = _chat()
    snapshot = copy.deepcopy(before)
    caching.mark(before)
    assert before == snapshot


def test_a_first_step_marks_the_opening_and_the_users_message():
    out = caching.mark(_chat()[:3])
    assert _marked(out) == [0, 2]


def test_an_empty_last_message_is_skipped_for_the_one_before_it():
    chat = _chat() + [{"role": "tool", "tool_call_id": "c2", "content": "  "}]
    assert _marked(caching.mark(chat)) == [0, 4]


def test_a_chat_with_no_system_message_marks_only_the_last_message():
    assert _marked(caching.mark([{"role": "user", "content": "hi"}])) == [0]
    chat = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "and?"}]
    assert _marked(caching.mark(chat)) == [2]


def test_marks_the_caller_placed_are_left_alone():
    chat = _chat()
    chat[2]["cache_control"] = MARK
    assert caching.mark(chat) is chat


def test_nothing_to_mark_returns_the_same_list():
    assert caching.mark([]) == []
    empty = [{"role": "assistant", "content": None}]
    assert caching.mark(empty) is empty


# -- on the wire ---------------------------------------------------------------

def _cfg(gateway, **over):
    cfg = {"base_url": gateway.base_url, "api_key": "rsk_test", "model": CLAUDE,
           "auto_route": False, "fallback_models": [], "reasoning_effort": None}
    cfg.update(over)
    return cfg


def _send(gateway, messages, **over):
    return [x for x in stream_chat(messages, _cfg(gateway, **over))]


def test_a_claude_request_carries_the_marks(gateway):
    gateway.script(sse_turn("ok", model=CLAUDE))
    chat = _chat()
    snapshot = copy.deepcopy(chat)
    _send(gateway, chat)
    sent = gateway.requests[0]
    assert _marked(sent["messages"]) == [0, 4]
    assert "max_tokens" not in sent, "caching must never cap the reply"
    assert chat == snapshot, "the stored history must never carry a mark"


def test_other_models_are_sent_as_before(gateway):
    gateway.script(sse_turn("ok"))
    chat = _chat()
    _send(gateway, chat, model="deepseek/deepseek-v4.1-flash")
    assert gateway.requests[0] == json.loads(json.dumps(
        build_payload(chat, _cfg(gateway, model="deepseek/deepseek-v4.1-flash"))))


def test_the_gateways_own_pick_is_sent_without_marks(gateway):
    gateway.script(sse_turn("ok"))
    _send(gateway, _chat(), auto_route=True)
    assert gateway.requests[0]["model"] == "auto"
    assert _marked(gateway.requests[0]["messages"]) == []


def test_started_with_it_off_sends_no_marks(gateway, monkeypatch):
    monkeypatch.setenv(caching.OFF_SWITCH, "0")
    gateway.script(sse_turn("ok", model=CLAUDE))
    _send(gateway, _chat())
    assert _marked(gateway.requests[0]["messages"]) == []


def test_a_refused_marked_request_is_sent_again_without_marks(gateway):
    gateway.script(['__status__:400:{"error": {"message": "cache_control not allowed"}}'],
                   sse_turn("ok", model=CLAUDE))
    out = _send(gateway, _chat())
    assert "ok" in "".join(x for x in out if isinstance(x, str))
    assert _marked(gateway.requests[0]["messages"]) == [0, 4]
    assert _marked(gateway.requests[1]["messages"]) == []


# -- in a chat -----------------------------------------------------------------

def test_every_step_of_a_claude_chat_is_marked_and_the_saved_chat_is_not(gateway, harness, tmp_path):
    (tmp_path / "a.txt").write_text("one\n")
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "read_file",
                              "arguments": json.dumps({"path": "a.txt"})}], model=CLAUDE),
        sse_turn("It says one.", model=CLAUDE))
    h = harness(mode=Mode.BYPASS, model=CLAUDE)
    h.session.submit("what does a.txt say?")
    h.wait_for(Event.TURN_FINISHED)

    first, second = (r["messages"] for r in gateway.requests[:2])
    assert _marked(first) == [0, len(first) - 1] and first[-1]["role"] == "user"
    assert _marked(second) == [0, len(second) - 1] and second[-1]["role"] == "tool"
    # What step 1 sent is the opening of what step 2 sent, mark aside: that
    # is what lets step 2 read step 1's entry.
    strip = lambda m: {k: v for k, v in m.items() if k != "cache_control"}
    assert [strip(m) for m in second[:len(first)]] == [strip(m) for m in first]
    assert _marked(h.session.state["messages"]) == []
