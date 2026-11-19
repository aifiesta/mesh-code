"""Old tool results are sent whole on every step, whatever the dial says.

The "Token savings" dial used to cut every tool result older than the last
four messages to 400 characters, on every request, from 0.2 up. Measured
(docs/bench/caching_pruning): that changed text the provider had already
cached, so bills went up (8 to 9% on DeepSeek, 65% on Claude Haiku 5.5), and
with five or more files read in one step it cut files the model had not
seen yet, which it then read again.
"""
import copy
import json

import pytest

from fake_gateway import sse_turn
from meshharness.core import memory, optimize
from meshharness.core.client import stream_chat

BIG = "x = 1  # a line of a source file\n" * 200          # 6,600 characters
DIALS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.95)
MODELS = ("anthropic/claude-haiku-5.5", "deepseek/deepseek-v4.1-flash-1017", "openai/gpt-6.1-sol")


def _chat(parallel_reads=6, later_steps=3):
    """A chat where one step read several files at once, then went on."""
    msgs = [{"role": "system", "content": "the same for every chat"},
            {"role": "system", "content": "about this project"},
            {"role": "user", "content": "explain each module"}]
    calls = [{"id": f"c{i}", "type": "function",
              "function": {"name": "read_file", "arguments": json.dumps({"path": f"lib/m{i}.py"})}}
             for i in range(parallel_reads)]
    msgs.append({"role": "assistant", "content": None, "tool_calls": calls})
    msgs += [{"role": "tool", "tool_call_id": f"c{i}", "content": f"# m{i}.py\n" + BIG}
             for i in range(parallel_reads)]
    for step in range(later_steps):
        cid = f"s{step}"
        msgs.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": "run_bash", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": cid, "content": f"step {step}\n" + BIG})
    return msgs


def _texts(messages):
    return [m.get("content") for m in messages]


@pytest.mark.parametrize("dial", DIALS)
@pytest.mark.parametrize("model", MODELS)
def test_no_dial_value_changes_what_a_message_says(dial, model):
    before = _chat()
    snapshot = copy.deepcopy(before)
    out, _, plan = optimize.prepare(before, model, dial, has_tools=True)
    assert _texts(out) == _texts(snapshot)
    assert before == snapshot
    assert plan["tokens_pruned_est"] == 0
    assert "tool_result_pruning" not in plan["levers_applied"]


def test_files_read_together_in_one_step_all_reach_the_model_whole(gateway):
    """Six files read at once: the request that shows them to the model for
    the first time carries all six in full. (The old cut kept the last four
    messages, so the first two arrived as 400 characters.)"""
    gateway.script(sse_turn("ok"))
    chat = _chat(parallel_reads=6, later_steps=0)
    cfg = {"base_url": gateway.base_url, "api_key": "rsk_test", "model": "deepseek/deepseek-v4.1-flash-1017",
           "auto_route": False, "fallback_models": [], "reasoning_effort": None, "optimize": 0.3}
    list(stream_chat(chat, cfg))
    sent = [m for m in gateway.requests[0]["messages"] if m["role"] == "tool"]
    assert len(sent) == 6
    assert all(len(m["content"]) > 6000 for m in sent)


@pytest.mark.parametrize("model", MODELS)
def test_each_step_begins_with_exactly_what_the_step_before_sent(model):
    """That is what lets a provider read the step before from its cache."""
    strip = lambda ms: [{k: v for k, v in m.items() if k != "cache_control"} for m in ms]
    previous = None
    for later in range(0, 6):
        out, _, _ = optimize.prepare(_chat(later_steps=later), model, 0.3, has_tools=True)
        if previous is not None:
            assert strip(out)[:len(previous)] == previous
        previous = strip(out)


@pytest.mark.parametrize("dial", (0.0,) + DIALS)
def test_the_read_again_stub_is_honest_at_every_dial(dial):
    """memory.dedupe_read tells the model "that file is already earlier in
    this conversation". It relies on optimize.survives_pruning. This binds
    the two: whatever survives_pruning says about a size, prepare() must
    leave a tool result of that size whole when it is no longer recent."""
    for size in (100, 801, 5000, 60_000):
        chat = _chat(parallel_reads=1, later_steps=6)
        chat[4]["content"] = "r" * size
        out, _, _ = optimize.prepare(chat, "anthropic/claude-haiku-5.5", dial, has_tools=True)
        whole = out[4]["content"] == "r" * size
        if optimize.survives_pruning(size, dial):
            assert whole, f"survives_pruning said a {size}-char result is safe at dial {dial}, but prepare() changed it"


def test_a_big_file_read_twice_gets_the_stub_with_the_dial_on(tmp_path):
    """With the cut in place the stub was withheld for big files from dial
    0.2 up, because the first read might have been cut. Now it never is."""
    f = tmp_path / "big.py"
    f.write_text(BIG)
    state = {"messages": [{"role": "user", "content": "hi"},
                          {"role": "assistant", "content": None},
                          {"role": "tool", "content": BIG}]}
    memory.record_read(state, str(f), BIG, 2)
    stub = memory.dedupe_read(state, str(f), 0.3)
    assert stub and "already earlier in this conversation" in stub
