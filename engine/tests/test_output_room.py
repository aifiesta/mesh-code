"""Reply limits: Anthropic models get room to answer, and a reply cut off
empty is asked again with more room, then reported, never ended in silence."""
import pytest
from fake_gateway import sse_turn

from meshharness.core.client import build_payload, cut_off_at, output_room
from meshharness.protocol import Event

CUT = {"prompt_tokens": 100, "completion_tokens": 4096, "total_tokens": 4196}


def test_anthropic_models_get_room_others_are_left_alone():
    base = {"reasoning_effort": None}
    assert build_payload([], dict(base, model="anthropic/claude-opus-5.5"))["max_tokens"] == 32_000
    assert "max_tokens" not in build_payload([], dict(base, model="deepseek/deepseek-v4.1-flash"))
    assert "max_tokens" not in build_payload([], dict(base, model="x", auto_route=True))
    assert output_room("anthropic/claude-haiku-5.5") == 32_000


def test_cut_off_is_read_from_the_finish_reason_or_a_default_limit():
    assert cut_off_at({"finish_reason": "length", "usage": {"completion_tokens": 900}}) == 900
    assert cut_off_at({"usage": {"completion_tokens": 4096}}) == 4096
    assert cut_off_at({"finish_reason": "stop", "usage": {"completion_tokens": 50}}) is None
    assert cut_off_at({}) is None


def test_a_cut_off_empty_reply_is_asked_again_with_more_room(gateway, harness):
    gateway.script(sse_turn("", usage=CUT, finish_reason="length"), sse_turn("Done."))
    h = harness()
    h.session.submit("write a big module")
    h.wait_for(Event.TURN_FINISHED)
    assert len(gateway.requests) == 2, "one retry, not the three empty-reply retries"
    assert gateway.requests[1]["max_tokens"] == 32_000
    assert h.of(Event.ASSISTANT_MESSAGE)[-1]["data"]["text"] == "Done."
    assert any("cut off at 4096" in e["data"]["message"] for e in h.of(Event.NOTICE))


def test_cut_off_twice_says_so_instead_of_ending_silently(gateway, harness):
    big = dict(CUT, completion_tokens=32_000)
    gateway.script(sse_turn("", usage=CUT, finish_reason="length"),
                   sse_turn("", usage=big, finish_reason="length"),
                   sse_turn("should not be asked"))
    h = harness()
    h.session.submit("write a big module")
    h.wait_for(Event.TURN_FINISHED)
    errors = [e for e in h.of(Event.ERROR) if e["data"].get("fatal")]
    assert errors and "cut off" in errors[0]["data"]["message"]
    assert len(gateway.requests) == 2 and gateway.requests[1]["max_tokens"] == 32_000
