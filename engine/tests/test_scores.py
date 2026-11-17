"""The scoring file that Smart routing will pick from, and how the thinking
level reaches the gateway."""
from fake_gateway import sse_turn

from meshharness import settings
from meshharness.core import scores
from meshharness.core.client import build_payload, is_reasoning_rejection
from meshharness.protocol import Event


# ---- the scoring file ---------------------------------------------------------

def test_bundled_scoring_file_loads_and_every_entry_is_complete():
    doc = scores.load()
    assert doc and len(doc["models"]) >= 15
    ents = scores.entries(doc)
    assert len(ents) >= 60
    for e in ents:
        assert e["level"] in scores.LEVELS
        assert 0 <= e["everyday"]["score"] <= 100 and e["everyday"]["margin"] > 0
        assert 0 <= e["hard"]["score"] <= 100 and e["hard"]["margin"] > 0
        assert e["cost_per_task"] > 0
        # what to send is the level itself, or nothing for an untuned model
        assert e["effort"] == (None if e["level"] == "default" else e["level"])


def test_entries_measured_with_thinking_off_are_dropped_where_mesh_keeps_it_on():
    doc = scores.load()
    assert "none" in doc["models"]["deepseek/deepseek-v4-pro-0813"]["levels"]
    assert "none" not in scores.levels(doc, "deepseek/deepseek-v4-pro-0813")
    # a model whose off switch works keeps its entry
    assert "none" in scores.levels(doc, "openai/gpt-6-luna")


def test_levels_come_back_weakest_first_and_include_the_top_ones():
    assert scores.levels(scores.load(), "openai/gpt-6.1-sol") == [
        "low", "medium", "high", "xhigh", "max"]


def test_entries_follow_the_live_catalog_and_the_never_pick_list():
    doc = scores.load()
    only = scores.entries(doc, available={"openai/gpt-6.1-sol", "not/listed"})
    assert {e["model"] for e in only} == {"openai/gpt-6.1-sol"}
    assert not scores.entries(doc, exclude={m for m in doc["models"]})


def test_excluded_models_are_not_in_the_file():
    doc = scores.load()
    assert not any("fable" in m for m in doc["models"])


def test_a_missing_or_broken_file_is_none_not_a_crash(tmp_path):
    assert scores.load(str(tmp_path / "nope.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert scores.load(str(bad)) is None
    assert scores.entries(None) == []


def test_an_entry_missing_a_number_is_left_out(tmp_path):
    import json
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"models": {"a/b": {"name": "B", "levels": {
        "high": {"sendable": True, "everyday": {"score": 80, "margin": 5},
                 "hard": {"score": 40, "margin": 6}, "cost_per_task": 1.0,
                 "minutes_per_task": 4},
        "low": {"sendable": True, "everyday": {"score": 70, "margin": 5},
                "hard": {"score": 30, "margin": 6}, "cost_per_task": None},
        "max": {"sendable": False, "everyday": {"score": 90, "margin": 5},
                "hard": {"score": 50, "margin": 6}, "cost_per_task": 3.0}}}}}))
    assert scores.levels(scores.load(str(f)), "a/b") == ["high"]


# ---- how the thinking level is sent ---------------------------------------------

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


def test_refusals_are_recognised_from_the_real_error_shapes():
    # the two shapes seen live on 2026-11-17 (docs/bench/model_scoring)
    openai = {"code": "invalid_request",
              "message": "Unsupported value: 'minimal' is not supported with the 'x' model.",
              "upstream_error": {"param": "reasoning.effort", "code": "unsupported_value"}}
    mistral = {"code": "invalid_request", "message": "Provider returned error",
               "upstream_error": {"metadata": {"raw": "reasoning_effort none is not supported for this model"}}}
    assert is_reasoning_rejection(openai) and is_reasoning_rejection(mistral)
    assert not is_reasoning_rejection({"message": "model is not available"})
    assert not is_reasoning_rejection("rate limited")


def _refusal():
    import json
    return [json.dumps({"error": {
        "code": "invalid_request", "message": "Provider returned error",
        "upstream_error": {"metadata": {"raw": "reasoning_effort none is not supported"}}}})]


def test_a_refused_level_is_retried_without_it_and_remembered(gateway, harness):
    gateway.script(_refusal(), sse_turn("done"), sse_turn("again"))
    h = harness(reasoning_effort="none")
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    assert gateway.requests[0]["reasoning"] == {"effort": "none"}
    assert "reasoning" not in gateway.requests[1]            # the retry
    assert any("'none'" in (e["data"].get("message") or "") for e in h.of(Event.NOTICE))
    assert not h.of(Event.ERROR)
    # same model, same level, next turn: not sent again, no doomed request
    h.session.submit("more")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert len(gateway.requests) == 3 and "reasoning" not in gateway.requests[2]


def test_refusing_one_level_does_not_switch_off_the_others(gateway, harness):
    gateway.script(_refusal(), sse_turn("done"), sse_turn("thought hard"))
    h = harness(reasoning_effort="none")
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    h.session.cfg["reasoning_effort"] = "high"
    h.session.submit("now think")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert gateway.requests[2]["reasoning"] == {"effort": "high"}


# ---- review fixes: refusals -----------------------------------------------------

from meshharness.core.client import nearest_level, reasoning_refusal  # noqa: E402

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
        assert reasoning_refusal(json_or_text(text)) is not None, text


def json_or_text(text):
    import json
    try:
        return json.loads(text)
    except ValueError:
        return text


def test_other_errors_are_not_mistaken_for_a_refused_level():
    for text in ("model x-ai/grok-4.1-fast-non-reasoning is not supported",
                 "rate limit exceeded", "context length exceeded",
                 {"code": "invalid_request", "message": "tool schema is not supported"}):
        assert reasoning_refusal(text) is None, text


def test_nearest_level_keeps_as_close_to_the_request_as_the_model_allows():
    assert nearest_level("max", ["none", "low", "medium", "high"]) == "high"
    assert nearest_level("none", ["low", "medium", "high"]) == "low"
    assert nearest_level("medium", ["low", "high"]) == "low"          # tie: the cheaper one
    assert nearest_level("max", ["high", "xhigh"], refused={"xhigh"}) == "high"
    assert nearest_level("max", []) is None and nearest_level("max", ["max"]) is None


def _inband(err):
    import json
    return [json.dumps({"error": err})]


def test_max_on_a_model_that_stops_at_high_runs_at_high(gateway, harness):
    gateway.script(_inband(OPENAI_REFUSAL), sse_turn("done"), sse_turn("again"))
    h = harness(reasoning_effort="max")
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    assert gateway.requests[0]["reasoning"] == {"effort": "max"}
    assert gateway.requests[1]["reasoning"] == {"effort": "high"}
    assert any("using 'high' instead" in (e["data"].get("message") or "") for e in h.of(Event.NOTICE))
    h.session.submit("more")                       # remembered: straight to high
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert len(gateway.requests) == 3
    assert gateway.requests[2]["reasoning"] == {"effort": "high"}


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
    h.session._non_streaming([{"role": "user", "content": "hi"}])
    assert gateway.requests[-1]["stream"] is False
    assert "reasoning" not in gateway.requests[-1]


def test_the_last_resort_request_goes_to_the_model_serving_the_turn(gateway, harness):
    h = harness(reasoning_effort="high")
    h.session._effective_model = lambda: "test/cheap"       # the live smart pick
    h.session._non_streaming([{"role": "user", "content": "hi"}])
    assert gateway.requests[-1]["model"] == "test/cheap"
    assert gateway.requests[-1]["reasoning"] == {"effort": "high"}


# ---- review fixes: the loader -----------------------------------------------------

def _entry(**over):
    e = {"sendable": True, "everyday": {"score": 80, "margin": 5},
         "hard": {"score": 40, "margin": 6}, "cost_per_task": 1.0, "minutes_per_task": 4}
    e.update(over)
    return e


def test_entries_without_a_margin_or_a_wait_are_left_out(tmp_path):
    import json
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"models": {"a/b": {"name": "B", "levels": {
        "low": _entry(everyday={"score": 70}),                 # no margin
        "medium": _entry(minutes_per_task=None),               # no wait
        "high": _entry(hard={"score": 140, "margin": 6}),      # not a share of tasks
        "max": _entry()}}}}))
    assert scores.levels(scores.load(str(f)), "a/b") == ["max"]


def test_changing_an_entry_does_not_change_the_loaded_file():
    doc = scores.load()
    first = scores.entries(doc)[0]
    before = first["hard"]["score"]
    first["hard"]["score"] = -1
    first["everyday"]["margin"] = 99
    again = scores.entries(scores.load())[0]
    assert again["hard"]["score"] == before and again["everyday"]["margin"] != 99
