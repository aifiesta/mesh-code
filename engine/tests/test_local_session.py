"""A session whose model is local/*: the hop goes to llama-server, costs
nothing, and small-model quirks (text tool calls, compact prompt) are
handled in the loop."""
import json

import pytest
from fake_gateway import FakeGateway, sse_turn

from meshharness.core.permissions import Mode

from meshharness.local.manager import LocalUnavailable
from meshharness.local.provider import Target
from meshharness.protocol import Event


class StubLocal:
    """What Session needs from LocalModels: a Target, or a reason it can't have one."""

    def __init__(self, llama: FakeGateway, *, ready=True, reason="Tiny is still downloading (42%)",
                 read_timeout=600.0, load_s=0.0, permanent=False):
        self.llama, self.ready, self.reason, self.permanent = llama, ready, reason, permanent
        self.read_timeout, self.load_s = read_timeout, load_s
        self.recovered = self.forced = 0

    def target_for(self, model_id, *, cancel=None, notify=None):
        if self.load_s and cancel is not None and cancel.wait(self.load_s):
            raise LocalUnavailable("cancelled while the model was loading")
        if not self.ready:
            raise LocalUnavailable(self.reason, permanent=self.permanent)
        port = int(self.llama.base_url.rsplit(":", 1)[1].split("/")[0])
        t = Target.for_local(port=port, api_key="local-key", alias="local/tiny", model_id="local/tiny",
                             ctx=32768, entry={"request_defaults": {"temperature": 0.2}})
        return Target(**{**t.__dict__, "read_timeout": self.read_timeout})

    def recover(self, *, force=False):
        self.recovered += 1
        self.forced += int(force)


@pytest.fixture
def llama():
    g = FakeGateway()
    yield g
    g.close()


def test_local_turn_hits_llama_server_not_the_gateway_and_costs_nothing(harness, gateway, llama):
    llama.script(sse_turn("hello from the laptop"))
    h = harness(model="local/auto", local=StubLocal(llama))
    h.session.submit("hi")
    fin = h.wait_for(Event.TURN_FINISHED)
    assert gateway.requests == [] and len(llama.requests) == 1
    body, headers = llama.requests[0], llama.headers[0]
    assert headers.get("authorization") == "Bearer local-key"
    assert body["model"] == "local/tiny" and body.get("temperature") == 0.2
    assert "models" not in body and "reasoning_effort" not in body
    assert fin["data"]["cost"] == 0 and fin["data"]["cost_estimated"] is False
    assert fin["data"]["local"] is True and fin["data"]["model"] == "local/tiny"
    status = h.of(Event.STATUS)[-1]["data"]
    assert status["turn_local"] is True and status["turn_cost"] == 0


def test_compact_prompt_goes_on_the_wire_but_history_keeps_the_full_one(harness, llama):
    llama.script(sse_turn("ok"))
    h = harness(model="local/auto", local=StubLocal(llama), system="Project rule: tabs.")
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    sent = llama.requests[0]["messages"][0]["content"]
    assert sent.startswith("You are Mesh Code") and "Project rule: tabs." in sent and len(sent) < 2500
    assert len(h.session.state["messages"][0]["content"]) > len(sent)


def test_text_tool_call_is_promoted_and_executed(harness, llama, tmp_path):
    (tmp_path / "a.txt").write_text("alpha")
    llama.script(sse_turn('<tool_call>{"name":"read_file","arguments":{"path":"a.txt"}}</tool_call>'))
    llama.script(sse_turn("it says alpha"))
    h = harness(mode=Mode.BYPASS, model="local/auto", local=StubLocal(llama))
    h.session.submit("read a.txt")
    h.wait_for(Event.TURN_FINISHED)
    repaired = h.of(Event.TOOL_REPAIRED)
    assert repaired and repaired[0]["data"]["kind"] == "text_parsed"
    assert h.of(Event.TOOL_RESULT)[0]["data"]["name"] == "read_file"
    assert "<tool_call>" not in (h.of(Event.ASSISTANT_MESSAGE)[0]["data"].get("text") or "")


def test_unparseable_text_call_nudges_once_then_counts_a_struggle(harness, llama):
    bad = '<tool_call>{"name":"read_file","arguments":{"path": }</tool_call>'
    llama.script(sse_turn(bad))
    llama.script(sse_turn("fine, done"))
    h = harness(model="local/auto", local=StubLocal(llama))
    h.session.submit("go")
    h.wait_for(Event.TURN_FINISHED)
    nudges = [m for m in llama.requests[1]["messages"] if "printed a tool call as text" in str(m.get("content"))]
    assert len(nudges) == 1 and nudges[0]["role"] == "user" and bad in nudges[0]["content"]
    roles = [m["role"] for m in h.session.state["messages"]]
    assert roles == ["system", "user", "user", "assistant"]      # never two assistants in a row
    assert h.of(Event.TURN_FINISHED)[0]["data"]["tokens"] > 0  # the nudged hop was still counted


def test_local_auto_falls_back_to_the_cloud_model_with_a_notice_while_unavailable(harness, gateway, llama):
    gateway.script(sse_turn("cloud here"))
    h = harness(model="local/auto", local=StubLocal(llama, ready=False),
                local_fallback_model="test/model")
    h.session.submit("hi")
    fin = h.wait_for(Event.TURN_FINISHED)
    assert llama.requests == [] and gateway.requests[0]["model"] == "test/model"
    notice = [e for e in h.of(Event.NOTICE) if "downloading" in e["data"]["message"]]
    assert notice and "test/model" in notice[0]["data"]["message"]
    assert fin["data"]["local"] is False and h.session.cfg["model"] == "local/auto"


def test_llama_server_dying_mid_stream_calls_recover_and_retries(harness, llama, monkeypatch):
    from meshharness.core import loopguard
    monkeypatch.setattr(loopguard, "backoff_delay", lambda n: 0.0)
    llama.script(["__drop__"])                      # server closes the socket mid-stream
    llama.script(sse_turn("recovered"))
    local = StubLocal(llama)
    h = harness(model="local/auto", local=local)
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED, timeout=20)
    assert local.recovered >= 1
    assert h.of(Event.ASSISTANT_MESSAGE)[-1]["data"]["text"] == "recovered"


def test_non_streaming_fallback_uses_the_local_target(harness, llama, monkeypatch):
    from meshharness.core import loopguard
    monkeypatch.setattr(loopguard, "backoff_delay", lambda n: 0.0)
    monkeypatch.setattr(loopguard, "MAX_STREAM_ATTEMPTS", 1)
    llama.script(["__status__:503"])                # streaming attempt refused
    llama.script(sse_turn("from blocking call"))     # served to the non-streaming request
    h = harness(model="local/auto", local=StubLocal(llama))
    h.session.submit("hi")
    fin = h.wait_for(Event.TURN_FINISHED, timeout=20)
    assert fin["data"]["model"] == "local/tiny" and fin["data"]["cost"] == 0
    assert any(r.get("stream") is False for r in llama.requests)


def test_context_limit_comes_from_the_local_target(harness, llama):
    llama.script(sse_turn("ok"))
    h = harness(model="local/auto", local=StubLocal(llama), auto_compact=True)
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED)
    assert h.session.state["_hop_ctx"] == 32768


def test_a_cloud_reply_quoting_a_tool_call_is_text_not_an_action(harness, gateway):
    quoted = 'In Hermes format you would write <tool_call>{"name":"run_bash","arguments":{"command":"rm -rf /"}}</tool_call>'
    gateway.script(sse_turn(quoted))
    h = harness(mode=Mode.BYPASS)                  # cloud model, pinned test/model
    h.session.submit("how do tool calls look?")
    h.wait_for(Event.TURN_FINISHED)
    assert h.of(Event.TOOL_REPAIRED) == [] and h.of(Event.TOOL_PROPOSED) == []
    assert h.of(Event.ASSISTANT_MESSAGE)[0]["data"]["text"] == quoted


def test_a_fallback_hop_is_priced_as_the_cloud_model_it_ran_on(harness, gateway, llama):
    gateway.script(sse_turn("cloud", usage={"prompt_tokens": 1000, "completion_tokens": 100,
                                             "total_tokens": 1100}, cost=0, model="upstream-echo-name"))
    h = harness(model="local/auto", local=StubLocal(llama, ready=False), local_fallback_model="test/model")
    h.session.catalog = [{"id": "local/auto", "local": True, "context_length": 32768,
                          "pricing": {"prompt_usd_per_1m": "0", "completion_usd_per_1m": "0"}},
                         {"id": "test/model", "context_length": 128000,
                          "pricing": {"prompt_usd_per_1m": 1.0, "completion_usd_per_1m": 3.0}}]
    h.session.submit("hi")
    fin = h.wait_for(Event.TURN_FINISHED)
    assert fin["data"]["local"] is False
    assert fin["data"]["cost"] > 0 and fin["data"]["cost_estimated"] is True
    assert h.session.state["_hop_ctx"] == 128000        # compaction sized for the cloud model


def test_stop_while_the_local_model_loads_aborts_instead_of_falling_back(harness, gateway, llama):
    gateway.script(sse_turn("should never be asked"))
    h = harness(model="local/auto", local=StubLocal(llama, load_s=5.0), local_fallback_model="test/model")
    h.session.submit("hi")
    h.wait_for(Event.HOP_STARTED)
    h.session.interrupt()
    h.wait_for(Event.TURN_ABORTED)
    assert gateway.requests == [] and llama.requests == []


def test_no_target_ends_the_turn_cleanly(harness, gateway, llama):
    h = harness(model="local/auto", local=StubLocal(llama, ready=False), local_fallback_model="")
    h.session.submit("hi")
    err = h.wait_for(Event.ERROR)
    assert err["data"]["fatal"] and "no cloud fallback" in err["data"]["message"]
    fin = h.wait_for(Event.TURN_FINISHED)
    assert fin["data"]["cost"] == 0 and gateway.requests == []


def test_a_silent_local_server_is_restarted_not_just_retried(harness, llama, monkeypatch):
    from meshharness.core import loopguard
    # The fake serves one request at a time, so the retry must wait out the
    # stalled handler: a 1 s backoff against a 0.8 s stall and a 0.5 s read.
    monkeypatch.setattr(loopguard, "backoff_delay", lambda n: 1.0)
    llama.script(["__stall__:0.8"])
    llama.script(sse_turn("back"))
    local = StubLocal(llama, read_timeout=0.5)
    h = harness(model="local/auto", local=local)
    h.session.submit("hi")
    h.wait_for(Event.TURN_FINISHED, timeout=30)
    assert local.forced >= 1
    assert h.of(Event.ASSISTANT_MESSAGE)[-1]["data"]["text"] == "back"


def test_a_local_pin_is_never_overridden_by_smart_routing(harness, llama):
    llama.script(sse_turn("local answer"))
    h = harness(model="local/auto", local=StubLocal(llama), route_mode="smart")
    h.session.submit("refactor the whole architecture of this large codebase")
    fin = h.wait_for(Event.TURN_FINISHED)
    assert fin["data"]["local"] is True and len(llama.requests) == 1


def test_a_permanent_reason_is_said_once_per_session_not_per_turn(harness, gateway, llama):
    gateway.script(sse_turn("one"))
    gateway.script(sse_turn("two"))
    h = harness(model="local/auto", local=StubLocal(llama, ready=False, permanent=True,
                                                    reason="local models are turned off in Settings"),
                local_fallback_model="test/model")
    h.session.submit("a")
    h.wait_for(Event.TURN_FINISHED)
    h.session.submit("b")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    notices = [e for e in h.of(Event.NOTICE) if "turned off" in e["data"]["message"]]
    assert len(notices) == 1 and "unavailable" in notices[0]["data"]["message"]
    assert len(gateway.requests) == 2
