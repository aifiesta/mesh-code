"""Where a bill goes: fresh input, cached input, cache writes, output, thinking.

Total tokens and total dollars do not move together (cached input costs a few
percent of fresh input; output costs several times input), so every step
reports the split. The shapes below are the gateway's real ones, copied from
live requests on 19 November 2026.
"""
import json

import pytest
from fake_gateway import sse_turn
from meshharness.core import pricing
from meshharness.core.permissions import Mode
from meshharness.protocol import Event

# DeepSeek V4.1 Flash, second request with the same prefix: almost all cached,
# and the account has a 30% discount.
DEEPSEEK_USAGE = {
    "prompt_tokens": 3359, "completion_tokens": 13, "total_tokens": 3372,
    "prompt_tokens_details": {"cached_tokens": 3328, "cache_write_tokens": 0,
                              "cache_write_1h_tokens": 0, "audio_tokens": 0},
    "completion_tokens_details": {"reasoning_tokens": 10, "audio_tokens": 0}}
DEEPSEEK_EVENT = {"cost_usd": "0.00002380", "cost_breakdown": {
    "lines": [
        {"kind": "input", "tokens": 31, "rate_per_1m_usd": "0.2", "amount_usd": "0.0000062", "charged": True},
        {"kind": "cache_read", "tokens": 3328, "rate_per_1m_usd": "0.006", "amount_usd": "0.00002", "charged": True},
        {"kind": "output", "tokens": 13, "rate_per_1m_usd": "0.6", "amount_usd": "0.0000078", "charged": True}],
    "subtotal_usd": "0.000034", "discount_pct": "30", "discount_usd": "0.0000102", "cost_usd": "0.0000238"}}

# GPT-6.1 Sol, first sight of a prefix: it is WRITTEN to the cache at 125%.
SOL_USAGE = {
    "prompt_tokens": 2588, "completion_tokens": 16, "total_tokens": 2604,
    "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 2585},
    "completion_tokens_details": {"reasoning_tokens": 9}}
SOL_EVENT = {"cost_usd": "0.00662850", "cost_breakdown": {
    "lines": [
        {"kind": "input", "tokens": 3, "rate_per_1m_usd": "2", "amount_usd": "0.000006", "charged": True},
        {"kind": "cache_write", "tokens": 2585, "rate_per_1m_usd": "2.5", "amount_usd": "0.0064625", "charged": True},
        {"kind": "output", "tokens": 16, "rate_per_1m_usd": "10", "amount_usd": "0.00016", "charged": True}],
    "subtotal_usd": "0.0066285", "discount_pct": None, "discount_usd": "0", "cost_usd": "0.0066285"}}


# -- token counts -------------------------------------------------------------

def test_cached_written_and_thinking_tokens_are_parts_not_extras():
    t = pricing.usage_tokens(DEEPSEEK_USAGE)
    assert t == {"fresh": 31, "cached": 3328, "cache_write": 0, "output": 13, "thinking": 10}
    assert t["fresh"] + t["cached"] + t["cache_write"] == DEEPSEEK_USAGE["prompt_tokens"]
    t = pricing.usage_tokens(SOL_USAGE)
    assert t == {"fresh": 3, "cached": 0, "cache_write": 2585, "output": 16, "thinking": 9}


def test_odd_usage_never_goes_negative():
    assert pricing.usage_tokens(None) == {"fresh": 0, "cached": 0, "cache_write": 0, "output": 0, "thinking": 0}
    t = pricing.usage_tokens({"prompt_tokens": 10, "completion_tokens": 2,
                              "prompt_tokens_details": {"cached_tokens": 50, "cache_write_tokens": 7},
                              "completion_tokens_details": {"reasoning_tokens": 9}})
    assert t == {"fresh": 0, "cached": 10, "cache_write": 0, "output": 2, "thinking": 2}


# -- the split as billed ------------------------------------------------------

def test_the_split_is_read_from_the_bill_and_adds_up_to_it():
    bill = pricing.bill_from_events([DEEPSEEK_EVENT], DEEPSEEK_USAGE)
    assert (bill["fresh_tokens"], bill["cached_tokens"], bill["output_tokens"]) == (31, 3328, 13)
    assert pricing.bill_total(bill) == pytest.approx(0.0000238, abs=1e-12)
    assert bill["estimated"] is False
    # The 30% account discount is spread over the lines in proportion.
    assert bill["cached_usd"] == pytest.approx(0.00002 * 0.7)
    assert bill["fresh_usd"] == pytest.approx(0.0000062 * 0.7)
    # 99% of the input tokens, but well under two thirds of the money.
    assert bill["cached_tokens"] / 3359 > 0.99
    assert bill["cached_usd"] / pricing.bill_total(bill) < 0.6


def test_thinking_is_a_share_of_output_not_added_on_top():
    bill = pricing.bill_from_events([DEEPSEEK_EVENT], DEEPSEEK_USAGE)
    assert bill["thinking_tokens"] == 10
    assert bill["thinking_usd"] == pytest.approx(bill["output_usd"] * 10 / 13)
    assert pricing.bill_total(bill) == pytest.approx(
        bill["fresh_usd"] + bill["cached_usd"] + bill["cache_write_usd"] + bill["output_usd"])


def test_a_cache_write_is_its_own_part():
    bill = pricing.bill_from_events([SOL_EVENT], SOL_USAGE)
    assert bill["cache_write_tokens"] == 2585 and bill["cached_tokens"] == 0
    assert bill["cache_write_usd"] == pytest.approx(0.0064625)
    assert pricing.bill_total(bill) == pytest.approx(0.0066285)


def test_several_requests_in_one_step_add_up():
    bill = pricing.bill_from_events([DEEPSEEK_EVENT, SOL_EVENT], SOL_USAGE)
    assert pricing.bill_total(bill) == pytest.approx(0.0000238 + 0.0066285)
    assert bill["output_tokens"] == 13 + 16


def test_a_line_the_engine_does_not_know_is_kept_as_other():
    event = {"cost_usd": "0.0105", "cost_breakdown": {"lines": [
        {"kind": "input", "tokens": 100, "amount_usd": "0.0001", "charged": True},
        {"kind": "web_search_call", "tokens": None, "amount_usd": "0.01", "charged": True},
        {"kind": "output", "tokens": 40, "amount_usd": "0.0004", "charged": True},
        {"kind": "output", "tokens": 999, "amount_usd": "9.99", "charged": False}]}}
    bill = pricing.bill_from_events([event], {})
    assert bill["other_usd"] == pytest.approx(0.01)
    assert bill["output_tokens"] == 40, "a line that was not charged is not counted"
    assert pricing.bill_total(bill) == pytest.approx(0.0105)


def test_no_breakdown_means_no_billed_split():
    assert pricing.bill_from_events([{"cost_usd": "0.01"}], DEEPSEEK_USAGE) is None
    assert pricing.bill_from_events([], DEEPSEEK_USAGE) is None


# -- the split when the gateway sent none --------------------------------------

CATALOG = [{"id": "x/model", "pricing": {"prompt_usd_per_1m": "1.0", "completion_usd_per_1m": "3.0",
                                         "cache_read_input_usd_per_1m": "0.1"}}]


def test_without_a_bill_the_split_is_estimated_from_the_catalog_and_says_so():
    usage = {"prompt_tokens": 1000, "completion_tokens": 100,
             "prompt_tokens_details": {"cached_tokens": 800},
             "completion_tokens_details": {"reasoning_tokens": 60}}
    bill = pricing.bill_from_usage("x/model", usage, CATALOG)
    assert bill["estimated"] is True
    assert (bill["fresh_tokens"], bill["cached_tokens"], bill["output_tokens"], bill["thinking_tokens"]) == (200, 800, 100, 60)
    assert bill["fresh_usd"] == pytest.approx(200 * 1.0 / 1e6)
    assert bill["cached_usd"] == pytest.approx(800 * 0.1 / 1e6)
    assert bill["output_usd"] == pytest.approx(100 * 3.0 / 1e6)
    assert pricing.bill_total(bill) == pytest.approx(pricing.estimate_cost("x/model", usage, CATALOG))


def test_an_estimated_split_is_scaled_to_the_total_that_is_known():
    usage = {"prompt_tokens": 1000, "completion_tokens": 100}
    bill = pricing.bill_from_usage("x/model", usage, CATALOG, cost=0.0026)     # catalog says 0.0013
    assert pricing.bill_total(bill) == pytest.approx(0.0026)
    assert bill["output_usd"] == pytest.approx(2 * 100 * 3.0 / 1e6)


def test_a_model_the_catalog_cannot_price_still_reports_its_tokens():
    usage = {"prompt_tokens": 1000, "completion_tokens": 100}
    bill = pricing.bill_from_usage("nobody/knows", usage, CATALOG, cost=0.5)
    assert bill["fresh_tokens"] == 1000 and bill["output_tokens"] == 100
    assert bill["other_usd"] == pytest.approx(0.5) and pricing.bill_total(bill) == pytest.approx(0.5)


def test_bills_add_up_key_by_key():
    a = pricing.bill_from_events([DEEPSEEK_EVENT], DEEPSEEK_USAGE)
    b = pricing.bill_from_usage("x/model", {"prompt_tokens": 10, "completion_tokens": 1}, CATALOG)
    total = pricing.add_bill(pricing.add_bill(pricing.empty_bill(), a), b)
    assert total["fresh_tokens"] == 31 + 10
    assert total["estimated"] is True, "one estimated step marks the sum"
    assert pricing.bill_total(total) == pytest.approx(pricing.bill_total(a) + pricing.bill_total(b))
    rounded = pricing.round_bill(total)
    assert isinstance(rounded["fresh_tokens"], int) and rounded["estimated"] is True
    json.dumps(rounded)


# -- through the engine -------------------------------------------------------

def _usage(prompt, cached, completion, thinking):
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": cached},
            "completion_tokens_details": {"reasoning_tokens": thinking}}


def test_every_step_reports_its_split_and_the_turn_adds_them_up(gateway, harness, tmp_path):
    (tmp_path / "a.txt").write_text("one\n")
    gateway.request_events["req_test_1"] = DEEPSEEK_EVENT       # the fake gives every request this id
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "read_file", "arguments": json.dumps({"path": "a.txt"})}],
                 usage=DEEPSEEK_USAGE),
        sse_turn("It says one.", usage=DEEPSEEK_USAGE))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("what does a.txt say?")
    done = h.wait_for(Event.TURN_FINISHED)

    steps = [e["data"] for e in h.of(Event.HOP_USAGE)]
    assert [s["hop"] for s in steps] == [1, 2]
    for s in steps:
        assert s["cost"] == pytest.approx(0.0000238)
        assert s["bill"]["cached_tokens"] == 3328 and s["bill"]["thinking_tokens"] == 10
        assert pricing.bill_total(s["bill"]) == pytest.approx(s["cost"], abs=1e-9)
        assert s["bill"]["estimated"] is False

    bill = done["data"]["bill"]
    assert bill["cached_tokens"] == 2 * 3328 and bill["fresh_tokens"] == 2 * 31
    assert pricing.bill_total(bill) == pytest.approx(done["data"]["cost"], abs=1e-6)
    # The running total rides on status while the turn is going, and the
    # session keeps it after the turn's own figures are cleared.
    assert any(e["data"].get("turn_bill", {}).get("cached_tokens") == 3328 for e in h.of(Event.STATUS))
    last = h.of(Event.STATUS)[-1]["data"]
    assert last["session_bill"]["cached_tokens"] == 2 * 3328
    assert last["turn_bill"]["cached_tokens"] == 0


def test_with_no_bill_from_the_gateway_the_split_is_estimated(gateway, harness):
    gateway.script(sse_turn("hello", usage=_usage(1000, 600, 50, 20), cost=0.0))
    h = harness()
    h.session.catalog = [{"id": "test/model", "pricing": {
        "prompt_usd_per_1m": "1.0", "completion_usd_per_1m": "3.0", "cache_read_input_usd_per_1m": "0.1"}}]
    h.session.submit("hi")
    done = h.wait_for(Event.TURN_FINISHED)
    bill = done["data"]["bill"]
    assert bill["estimated"] is True and done["data"]["cost_estimated"] is True
    assert (bill["fresh_tokens"], bill["cached_tokens"], bill["output_tokens"], bill["thinking_tokens"]) == (400, 600, 50, 20)
    assert pricing.bill_total(bill) == pytest.approx(done["data"]["cost"], abs=1e-6)


def test_the_session_split_survives_a_restart(gateway, harness):
    gateway.request_events["req_test_1"] = DEEPSEEK_EVENT
    gateway.script(sse_turn("hello", usage=DEEPSEEK_USAGE))
    h = harness()
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    h.session.save_state()
    from meshharness.core import config
    saved = json.loads((config.SESSIONS_DIR / f"{h.session.id}.json").read_text())
    assert saved["session_bill"]["cached_tokens"] == 3328
    from meshharness.session import Session
    again = Session.restore(saved, dict(h.session.cfg), lambda ev: None)
    assert again.session_bill["cached_tokens"] == 3328
    assert pricing.bill_total(again.session_bill) == pytest.approx(0.0000238)
