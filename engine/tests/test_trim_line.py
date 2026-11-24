"""When to trim, per model: the window, a price step, and what re-reading
the cached chat costs."""
import json

from fake_gateway import sse_turn

from meshharness.core import compact
from meshharness.core.permissions import Mode
from meshharness.protocol import Event


def _row(mid, ctx=1_000_000, cached=None, prompt=1.0, step=None):
    return {"id": mid, "context_length": ctx, "supports_tools": True,
            "standard_context_threshold": step,
            "pricing": {"prompt_usd_per_1m": str(prompt), "completion_usd_per_1m": "1",
                        "cache_read_input_usd_per_1m": None if cached is None else str(cached)}}


CAT = [
    _row("anthropic/claude-sonnet-5.5", cached=0.1, prompt=2.0),
    _row("deepseek/deepseek-v4.1-flash-1017", ctx=1_048_576, cached=0.006, prompt=0.2),
    _row("anthropic/claude-haiku-5.5", cached=0.01, prompt=0.1, step=100_000),
    _row("openai/gpt-6.1-sol", ctx=1_050_000, cached=0.1, prompt=2.0, step=272_000),
    _row("small/cloud-32k", ctx=32_768, cached=0.1, prompt=2.0),
    {"id": "local/tiny", "context_length": 16384, "local": True,
     "pricing": {"prompt_usd_per_1m": "0", "completion_usd_per_1m": "0"}},
]


def test_the_trim_point_follows_price_and_window():
    line = {m["id"]: compact.trim_line(m["id"], CAT) for m in CAT}
    assert line["anthropic/claude-sonnet-5.5"] == 100_000       # dear cache reads
    assert line["openai/gpt-6.1-sol"] == 100_000
    assert line["deepseek/deepseek-v4.1-flash-1017"] == 200_000  # cheap cache reads
    assert line["anthropic/claude-haiku-5.5"] == 90_000         # just under its 5x price step
    assert line["small/cloud-32k"] == int(32_768 * 0.7)         # the window binds
    assert line["local/tiny"] == int(16384 * 0.7)               # free: the window alone
    assert compact.trim_line("not/listed", CAT) == int(compact.DEFAULT_CONTEXT_TOKENS * 0.7)


def _tool_run(i, size):
    call = {"id": f"c{i}", "type": "function",
            "function": {"name": "run_bash", "arguments": json.dumps({"command": f"cat f{i}"})}}
    return [{"role": "assistant", "content": None, "tool_calls": [call]},
            {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * size}]


def test_a_trim_folds_well_below_the_line_so_it_does_not_fire_again_soon():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    for i in range(30):
        msgs += _tool_run(i, 16_000)          # ~4k tokens each, ~120k in all
    state = {"messages": msgs}
    rep = compact.compact_history(state, line=100_000)
    assert rep
    assert compact.est_history_tokens(state["messages"]) < 50_000


def test_on_a_small_window_the_untouched_recent_messages_shrink_to_fit():
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
    for i in range(4):
        msgs += _tool_run(i, 12_000)          # 4 big reads, all inside the last 8 messages
    state = {"messages": msgs}
    before = compact.est_history_tokens(msgs)
    rep = compact.compact_history(state, line=int(16384 * 0.7))
    assert rep and rep["truncated"] >= 1, "the last-8 window must not shield a full 16k window"
    assert compact.est_history_tokens(state["messages"]) < int(16384 * 0.7) * 0.6 < before


# ---- in a session: the real count from the provider decides ------------------

def _step(i, prompt_tokens):
    return sse_turn(tool_calls=[{"id": f"b{i}", "name": "run_bash",
                                 "arguments": json.dumps({"command": f"echo {'y' * 3000} # {i}"})}],
                    usage={"prompt_tokens": prompt_tokens, "completion_tokens": 20,
                           "total_tokens": prompt_tokens + 20})


def _run(harness, gateway, model, prompt_tokens):
    gateway.script(*[_step(i, prompt_tokens) for i in range(7)], sse_turn("Done."))
    h = harness(mode=Mode.BYPASS, model=model, auto_compact=True)
    h.session.catalog = CAT
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED, timeout=30)
    return h


def test_a_dear_model_is_trimmed_at_100k_real_tokens_though_the_guess_is_small(harness, gateway):
    h = _run(harness, gateway, "anthropic/claude-sonnet-5.5", 120_000)
    reasons = [c["data"]["reason"] for c in h.of(Event.COMPACTED)]
    assert reasons and reasons[0] == "approaching this model's trim point"


def test_a_cheap_model_carries_the_same_chat_untrimmed(harness, gateway):
    h = _run(harness, gateway, "deepseek/deepseek-v4.1-flash-1017", 120_000)
    assert h.of(Event.COMPACTED) == []


def test_a_count_that_leaves_cached_tokens_out_cannot_hide_a_big_chat(harness, gateway):
    gateway.script(*[sse_turn(tool_calls=[{"id": f"b{i}", "name": "run_bash", "arguments": json.dumps(
        {"command": f"python3 -c \"print('x' * 9000)\" # {i}"})}],
        usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}) for i in range(24)],
        sse_turn("Done."))
    h = harness(mode=Mode.BYPASS, model="small/cloud-32k", auto_compact=True)
    h.session.catalog = CAT
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED, timeout=60)
    assert h.of(Event.COMPACTED), "the chars/4 guess still trims when the reported count is too low"
