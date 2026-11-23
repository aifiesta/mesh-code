"""Old tool results are sent whole on every step.

The "Token savings" setting (since removed) used to cut every tool result
older than the last four messages to 400 characters, on every request.
Measured (docs/bench/caching_pruning): that changed text the provider had
already cached, so bills went up (8 to 9% on DeepSeek, 65% on Claude Haiku
5.5), and with five or more files read in one step it cut files the model
had not seen yet, which it then read again.
"""
import json

import pytest

from fake_gateway import sse_turn
from meshharness.core import memory
from meshharness.core.client import stream_chat

BIG = "x = 1  # a line of a source file\n" * 200          # 6,600 characters


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


@pytest.mark.parametrize("model", ("deepseek/deepseek-v4.1-flash-1017", "anthropic/claude-haiku-5.5"))
def test_files_read_together_in_one_step_all_reach_the_model_whole(gateway, model):
    """Six files read at once: the request that shows them to the model for
    the first time carries all six in full. (The old cut kept the last four
    messages, so the first two arrived as 400 characters.)"""
    gateway.script(sse_turn("ok", model=model))
    chat = _chat(parallel_reads=6, later_steps=0)
    cfg = {"base_url": gateway.base_url, "api_key": "rsk_test", "model": model,
           "auto_route": False, "fallback_models": [], "reasoning_effort": None}
    list(stream_chat(chat, cfg))
    sent = [m for m in gateway.requests[0]["messages"] if m["role"] == "tool"]
    assert len(sent) == 6
    assert all(len(m["content"]) > 6000 for m in sent)


def test_a_big_file_read_twice_gets_the_stub(tmp_path):
    """With the cut in place the stub was withheld for big files, because
    the first read might have been cut. Nothing cuts a read now, so the
    "already earlier in this conversation" stub is honest at any size."""
    f = tmp_path / "big.py"
    f.write_text(BIG)
    state = {"messages": [{"role": "user", "content": "hi"},
                          {"role": "assistant", "content": None},
                          {"role": "tool", "content": BIG}]}
    memory.record_read(state, str(f), BIG, 2)
    stub = memory.dedupe_read(state, str(f))
    assert stub and "already earlier in this conversation" in stub
