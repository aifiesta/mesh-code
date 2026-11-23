"""A hop to the local server: its address and key, no gateway fields, and
never a cache mark, whatever the model is called."""
from fake_gateway import FakeGateway, sse_turn

from meshharness.core.client import build_payload, stream_chat
from meshharness.local.provider import Target


def _target(g, alias):
    port = int(g.base_url.rsplit(":", 1)[1].split("/")[0])
    return Target.for_local(port=port, api_key="k", alias=alias, model_id="local/x", ctx=32768,
                            entry={"request_defaults": {"temperature": 0.6}})


def test_no_cache_marks_and_no_gateway_fields_on_a_local_hop():
    g = FakeGateway()
    try:
        g.script(sse_turn("hi"))
        cfg = {"base_url": "http://unused.invalid/v1", "api_key": "cloud-key",
               "model": "anthropic/claude-haiku-5.5", "fallback_models": ["a/b"],
               "reasoning_effort": "max"}
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        events = list(stream_chat(msgs, cfg, target=_target(g, "anthropic/claude-lookalike")))
        body = g.requests[0]
        assert all("cache_control" not in m for m in body["messages"])
        assert "models" not in body and "reasoning" not in body and body["temperature"] == 0.6
        assert g.headers[0]["authorization"] == "Bearer k"
        meta = events[-1]
        assert meta["local"] is True and meta["model"] == "local/x"
    finally:
        g.close()


def test_a_cloud_payload_is_unchanged_without_a_target():
    cfg = {"model": "x/y", "fallback_models": ["a/b"], "reasoning_effort": "high"}
    body = build_payload([{"role": "user", "content": "u"}], cfg)
    assert body["model"] == "x/y" and body["models"] == ["a/b"] and body["reasoning"] == {"effort": "high"}
