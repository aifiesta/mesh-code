"""How the thinking level reaches the gateway, and what happens when a model
refuses the level it was sent."""
import json

from fake_gateway import sse_turn

from meshharness import settings
from meshharness.core.client import build_payload, nearest_level, reasoning_refusal
from meshharness.protocol import Event

SONNET = "anthropic/claude-sonnet-5.5"

# the two refusals seen live on 2026-11-17, as the gateway sent them
OPENAI_REFUSAL = {
    "code": "invalid_request",
    "message": "Unsupported value: 'max' is not supported with the 'x' model. "
               "Supported values are: 'none', 'low', 'medium', and 'high'.",
    "upstream_error": {"param": "reasoning.effort", "code": "unsupported_value"}}
MISTRAL_REFUSAL = {
    "code": "invalid_request", "message": "Provider returned error",
    "upstream_error": {"metadata": {"raw": (
        "reasoning_effort none is not supported for this model, supported values: "
        "[<ReasoningEffort.high: 'high'>, <ReasoningEffort.low: 'low'>, "
        "<ReasoningEffort.medium: 'medium'>]")}}}


def _inband(err):
    return [json.dumps({"error": err})]


def _json_or_text(text):
    try:
        return json.loads(text)
    except ValueError:
        return text


# ---- the payload ------------------------------------------------------------------

def test_thinking_level_goes_out_in_the_gateways_current_field():
    p = build_payload([{"role": "user", "content": "hi"}],
                      {"model": "a/b", "reasoning_effort": "xhigh"})
    assert p["reasoning"] == {"effort": "xhigh"}
    assert "reasoning_effort" not in p      # the deprecated field refuses xhigh/max


def test_no_level_set_sends_no_thinking_field_at_all():
    p = build_payload([{"role": "user", "content": "hi"}],
                      {"model": "a/b", "reasoning_effort": None})
    assert "reasoning" not in p and "reasoning_effort" not in p


def test_off_is_a_real_level_and_is_sent():
    p = build_payload([{"role": "user", "content": "hi"}],
                      {"model": "a/b", "reasoning_effort": "none"})
    assert p["reasoning"] == {"effort": "none"}


def test_settings_offer_every_level_the_gateway_takes():
    for level in (None, "none", "low", "medium", "high", "xhigh", "max"):
        assert settings.coerce("reasoning_effort", level) == level
    try:
        settings.coerce("reasoning_effort", "ultra")
    except settings.InvalidSetting:
        pass
    else:
        raise AssertionError("an unknown level must be refused")


# ---- recognising a refusal ----------------------------------------------------------

def test_a_refusal_says_which_levels_the_model_does_take():
    assert reasoning_refusal(OPENAI_REFUSAL) == {"accepted": ["none", "low", "medium", "high"]}
    assert reasoning_refusal(MISTRAL_REFUSAL) == {"accepted": ["low", "medium", "high"]}
    # max_tokens must not be read as the level "max"
    assert reasoning_refusal("reasoning.effort invalid; supported values: low, high. "
                             "max_tokens too large") == {"accepted": ["low", "high"]}


def test_refusals_worded_other_ways_are_recognised():
    for text in ("thinking is not supported for this model",
                 "Unsupported parameter: 'effort'",
                 "thinking.budget_tokens must be less than max_tokens",
                 '{"detail":[{"loc":["body","reasoning","effort"],"msg":"Input should be \'high\' or \'low\'"}]}',
                 "this model does not support reasoning"):
        assert reasoning_refusal(_json_or_text(text)) is not None, text


def test_other_errors_are_not_mistaken_for_a_refused_level():
    for text in ("model x-ai/grok-4.1-fast-non-reasoning is not supported",
                 "rate limit exceeded", "context length exceeded", "model is not available",
                 {"code": "invalid_request", "message": "tool schema is not supported"}):
        assert reasoning_refusal(text) is None, text


def test_nearest_level_keeps_as_close_to_the_request_as_the_model_allows():
    assert nearest_level("max", ["none", "low", "medium", "high"]) == "high"
    assert nearest_level("none", ["low", "medium", "high"]) == "low"
    assert nearest_level("medium", ["low", "high"]) == "low"          # tie: the cheaper one
    assert nearest_level("max", ["high", "xhigh"], refused={"xhigh"}) == "high"
    assert nearest_level("max", []) is None and nearest_level("max", ["max"]) is None


# ---- a refusal inside a turn ----------------------------------------------------------

def test_a_refused_level_is_retried_without_it_and_remembered(gateway, harness):
    gateway.script(_inband(MISTRAL_REFUSAL), sse_turn("done"), sse_turn("again"))
    h = harness(reasoning_effort="none")
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    assert gateway.requests[0]["reasoning"] == {"effort": "none"}
    assert gateway.requests[1]["reasoning"] == {"effort": "low"}     # nearest it takes
    assert not h.of(Event.ERROR)
    h.session.submit("more")                       # remembered: no doomed request
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert len(gateway.requests) == 3 and gateway.requests[2]["reasoning"] == {"effort": "low"}


def test_refusing_one_level_does_not_switch_off_the_others(gateway, harness):
    gateway.script(_inband(MISTRAL_REFUSAL), sse_turn("done"), sse_turn("thought hard"))
    h = harness(reasoning_effort="none")
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    h.session.cfg["reasoning_effort"] = "high"
    h.session.submit("now think")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert gateway.requests[2]["reasoning"] == {"effort": "high"}


def test_max_on_a_model_that_stops_at_high_runs_at_high(gateway, harness):
    gateway.script(_inband(OPENAI_REFUSAL), sse_turn("done"), sse_turn("again"))
    h = harness(reasoning_effort="max")
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    assert gateway.requests[0]["reasoning"] == {"effort": "max"}
    assert gateway.requests[1]["reasoning"] == {"effort": "high"}
    assert any("using 'high' instead" in (e["data"].get("message") or "") for e in h.of(Event.NOTICE))


def test_if_the_replacement_is_refused_too_the_next_nearest_is_tried(gateway, harness):
    second = dict(OPENAI_REFUSAL, message="Unsupported value: 'high' is not supported. "
                                          "Supported values are: 'low' and 'medium'.")
    gateway.script(_inband(OPENAI_REFUSAL), _inband(second), sse_turn("done"))
    h = harness(reasoning_effort="max")
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    assert [r.get("reasoning", {}).get("effort") for r in gateway.requests] == ["max", "high", "medium"]
    assert not h.of(Event.ERROR)


def test_a_refusal_sent_as_an_http_error_is_retried_too(gateway, harness):
    body = ('{"error":{"code":"validation_error","details":[{"loc":["body","reasoning","effort"],'
            '"msg":"Input should be \'high\', \'medium\', \'low\' or \'none\'"}]}}')
    gateway.script([f"__status__:422:{body}"], sse_turn("done"))
    h = harness(reasoning_effort="xhigh")
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    assert gateway.requests[1]["reasoning"] == {"effort": "high"}
    assert not h.of(Event.ERROR)


def test_an_http_error_about_something_else_still_stops_the_turn(gateway, harness):
    gateway.script(['__status__:400:{"error":{"message":"bad tool schema"}}'])
    h = harness(reasoning_effort="high")
    h.session.submit("hi")
    err = h.wait_for(Event.ERROR)
    assert "400" in err["data"]["message"] and len(gateway.requests) == 1


def test_the_last_resort_request_does_not_resend_a_refused_level(gateway, harness):
    h = harness(reasoning_effort="none")
    h.session.state["_reasoning_swaps"] = {("test/model", "none"): None}
    h.session.policy.begin_turn()
    h.session._non_streaming([{"role": "user", "content": "hi"}])
    assert gateway.requests[-1]["stream"] is False
    assert "reasoning" not in gateway.requests[-1]


def test_a_refusal_on_an_escalated_hop_retries_there_instead_of_handing_back(gateway, harness):
    gateway.script(_inband(OPENAI_REFUSAL), sse_turn("strong answer"))
    h = harness(policy="local-first", local_fallback_model="test/model",
                escalation_effort="max")
    h.session.escalate()
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED)
    assert [r["model"] for r in gateway.requests] == [SONNET, SONNET]
    assert [r["reasoning"]["effort"] for r in gateway.requests] == ["max", "high"]
    assert not h.of(Event.ERROR)
