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
