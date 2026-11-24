"""The local model as one more choice inside Smart routing: Lookup and Small
change start on it when it is loaded, a step-up goes to Smart routing's own
pick for the level, and the extra local signals (no file read, two failed
tool rounds, a step limit, failing tests) use the same step-up path."""
import json
import time

import pytest
from fake_gateway import FakeGateway, sse_turn

from meshharness.core import finishcheck, scores
from meshharness.core.permissions import Mode
from meshharness.local.provider import Target
from meshharness.protocol import Event


def _e(everyday, hard, cost, minutes, em=5, hm=5):
    return {"sendable": True, "everyday": {"score": everyday, "margin": em},
            "hard": {"score": hard, "margin": hm}, "cost_per_task": cost,
            "minutes_per_task": minutes}


DOC = {"models": {
    "t/cheap": {"name": "Cheap", "price_in_per_1m": 0.1, "price_out_per_1m": 0.4, "levels": {
        "high": _e(85, 15, 0.30, 10)}},
    "t/mid": {"name": "Mid", "price_in_per_1m": 2, "price_out_per_1m": 10, "levels": {
        "low": _e(75, 25, 0.40, 2), "medium": _e(82, 41, 0.70, 4), "max": _e(86, 48, 2.40, 20)}},
    "t/strong": {"name": "Strong", "price_in_per_1m": 15, "price_out_per_1m": 75, "levels": {
        "high": _e(90, 52, 7.0, 24), "max": _e(90, 60, 20.0, 25)}},
}}
CATALOG = [{"id": m, "context_length": 200000, "supports_tools": True,
            "pricing": {"prompt_usd_per_1m": f["price_in_per_1m"],
                        "completion_usd_per_1m": f["price_out_per_1m"]}}
           for m, f in DOC["models"].items()]

LOOKUP = "In two sentences, what does price.py do? Do not change any files."
SMALL = "fix the typo in README.md"
STANDARD = ("Refactor price.py for production use: fix the failing tests, handle edge cases "
            "such as zero or negative quantities with a clear error, add tests for them, "
            "and run the full test suite.")


class _Server:
    def __init__(self):
        self.in_use = 0
        self.uses = 0

    def begin_use(self):
        self.in_use += 1
        self.uses += 1

    def end_use(self):
        self.in_use -= 1


class StubLocal:
    """What Session needs from LocalModels."""

    def __init__(self, llama: FakeGateway, *, ready=True, window=32768):
        self.llama, self.ready, self.window = llama, ready, window
        self.server = _Server()
        self.asked = 0
        self.recovered = 0

    def ready_target(self):
        self.asked += 1
        if not self.ready:
            return None
        port = int(self.llama.base_url.rsplit(":", 1)[1].split("/")[0])
        return Target.for_local(port=port, api_key="local-key", alias="local/tiny",
                                model_id="local/tiny", ctx=self.window,
                                entry={"request_defaults": {"temperature": 0.2}})

    def context_window(self):
        return self.window

    def target_for(self, model_id, *, cancel=None, notify=None):
        """A model picked in the model list: blocks while loading."""
        from meshharness.local.manager import LocalUnavailable
        self.picked = model_id
        t = self.ready_target()
        if t is None:
            raise LocalUnavailable("Tiny is not downloaded yet")
        return t

    def recover(self, *, force=False):
        self.recovered += 1


@pytest.fixture
def llama():
    g = FakeGateway()
    yield g
    g.close()


def _read(path, cid="r1"):
    return sse_turn(tool_calls=[{"id": cid, "name": "read_file", "arguments": json.dumps({"path": path})}])


def _smart(harness, monkeypatch, llama, *, mode=Mode.BYPASS, **kw):
    monkeypatch.setattr(scores, "load", lambda path=None: DOC)
    local = kw.pop("local", None) or StubLocal(llama)
    kw.setdefault("route_mode", "smart")
    h = harness(mode=mode, local=local, **kw)
    h.session.catalog = CATALOG
    return h, local


def _chat_requests(g):
    return [r for r in g.requests if "messages" in r]


def test_a_lookup_starts_on_the_local_model_and_costs_nothing(harness, gateway, llama, monkeypatch, tmp_path):
    (tmp_path / "price.py").write_text("def line_total(q, p):\n    return q * p\n")
    llama.script(_read("price.py"), sse_turn("It multiplies quantity by price."))
    h, local = _smart(harness, monkeypatch, llama)
    h.session.submit(LOOKUP)
    fin = h.wait_for(Event.TURN_FINISHED)
    assert _chat_requests(gateway) == [] and len(llama.requests) == 2
    assert llama.headers[0].get("authorization") == "Bearer local-key"
    routed = h.of(Event.ROUTED)
    assert routed[-1]["data"]["local"] is True and routed[-1]["data"]["task"] == "Lookup"
    assert fin["data"]["cost"] == 0 and fin["data"]["local"] is True
    assert fin["data"]["local_steps"] == 2 and not fin["data"]["stepped_up_from_local"]
    usage = h.of(Event.HOP_USAGE)
    assert usage and all(u["data"]["local"] and u["data"]["cost"] == 0 for u in usage)
    assert all(v == 0 for u in usage for k, v in u["data"]["bill"].items() if k.endswith("_usd"))
    assert sum(u["data"]["bill"]["output_tokens"] for u in usage) > 0   # tokens are real, dollars $0
    assert local.server.in_use == 0 and local.server.uses == 2


def test_what_goes_to_the_local_server(harness, llama, monkeypatch, tmp_path):
    """One short system prompt (not main's two), no cache marks, no gateway
    fields, no plan-tick tool named."""
    (tmp_path / "README.md").write_text("helo\n")
    llama.script(_read("README.md"), sse_turn("The typo is 'helo'."))
    h, _ = _smart(harness, monkeypatch, llama, system="Project rule: tabs.")
    h.session.submit(SMALL)
    h.wait_for(Event.TURN_FINISHED)
    body = llama.requests[0]
    systems = [m for m in body["messages"] if m["role"] == "system"]
    assert len(systems) == 1 and body["messages"][0]["role"] == "system"
    assert "Project rule: tabs." in systems[0]["content"] and "update_step" not in systems[0]["content"]
    assert len(systems[0]["content"]) < 2500
    assert all("cache_control" not in m for m in body["messages"])
    assert body["model"] == "local/tiny" and body["temperature"] == 0.2
    assert "models" not in body and "reasoning" not in body
    assert body["stream_options"] == {"include_usage": True}
    stored = [m for m in h.session.state["messages"] if m["role"] == "system"]
    assert len(stored) == 2, "the stored chat keeps main's prompt for a step-up"


def test_a_standard_task_is_routed_as_before(harness, gateway, llama, monkeypatch):
    gateway.script(sse_turn("done"))
    h, local = _smart(harness, monkeypatch, llama)
    h.session.submit(STANDARD)
    h.wait_for(Event.TURN_FINISHED)
    assert llama.requests == [] and len(_chat_requests(gateway)) == 1
    assert not any(e["data"].get("local") for e in h.of(Event.ROUTED))


def test_with_smart_routing_off_the_local_model_is_never_used(harness, gateway, llama, monkeypatch):
    gateway.script(sse_turn("done"))
    h, _ = _smart(harness, monkeypatch, llama, route_mode="off")
    h.session.submit(LOOKUP)
    h.wait_for(Event.TURN_FINISHED)
    assert llama.requests == [] and _chat_requests(gateway)[0]["model"] == "test/model"


def test_while_it_downloads_or_loads_the_cloud_pick_answers(harness, gateway, llama, monkeypatch):
    gateway.script(sse_turn("cloud answer"))
    h, local = _smart(harness, monkeypatch, llama, local=StubLocal(llama, ready=False))
    h.session.submit(LOOKUP)
    h.wait_for(Event.TURN_FINISHED)
    assert llama.requests == [] and local.asked >= 1
    assert _chat_requests(gateway)[0]["model"] == h.of(Event.ROUTED)[0]["data"]["model"]


def test_an_answer_with_no_file_read_steps_up_to_the_pick_for_the_level(harness, gateway, llama, monkeypatch):
    llama.script(sse_turn("It applies the discount after tax."))      # a guess
    gateway.script(sse_turn("checked: before tax"))
    h, _ = _smart(harness, monkeypatch, llama)
    h.session.submit(LOOKUP)
    fin = h.wait_for(Event.TURN_FINISHED)
    first = h.of(Event.ROUTED)[0]["data"]["model"]                     # the cloud pick for Lookup
    step = [e["data"] for e in h.of(Event.ROUTED) if e["data"].get("from_local")]
    assert step and step[0]["model"] == first, "the step-up is the level's own pick, not a stronger one"
    assert _chat_requests(gateway)[0]["model"] == first
    assert fin["data"]["stepped_up_from_local"] and fin["data"]["local_steps"] == 1
    texts = [e["data"]["text"] for e in h.of(Event.ASSISTANT_MESSAGE)]
    assert texts == ["checked: before tax"], "the guess is not shown as the answer"
    # the cloud model gets the whole chat with main's prompt
    assert sum(1 for m in _chat_requests(gateway)[0]["messages"] if m["role"] == "system") == 2


def test_two_failed_tool_rounds_in_a_row_step_up(harness, gateway, llama, monkeypatch):
    llama.script(_read("nope1.py", "a"), _read("nope2.py", "b"), sse_turn("never sent"))
    gateway.script(sse_turn("done on cloud"))
    h, _ = _smart(harness, monkeypatch, llama)
    h.session.submit(SMALL)
    h.wait_for(Event.TURN_FINISHED)
    assert len(llama.requests) == 2
    step = [e["data"] for e in h.of(Event.ROUTED) if e["data"].get("from_local")]
    assert step and "failed tool rounds" in step[0]["reason"]


def test_the_step_limit_moves_a_long_local_run_to_the_cloud(harness, gateway, llama, monkeypatch, tmp_path):
    from meshharness import session as session_mod
    monkeypatch.setattr(session_mod, "LOCAL_STEP_LIMIT", 3)
    for i in range(3):
        (tmp_path / f"f{i}.py").write_text(f"x = {i}\n")
    llama.script(*[_read(f"f{i}.py", f"c{i}") for i in range(3)])
    gateway.script(sse_turn("finished on cloud"))
    h, _ = _smart(harness, monkeypatch, llama)
    h.session.submit(SMALL)
    h.wait_for(Event.TURN_FINISHED)
    assert len(llama.requests) == 3 and len(_chat_requests(gateway)) == 1
    step = [e["data"] for e in h.of(Event.ROUTED) if e["data"].get("from_local")]
    assert step and "3 steps" in step[0]["reason"]


def test_the_finish_check_steps_up_when_the_projects_tests_fail(harness, gateway, llama, monkeypatch):
    monkeypatch.setattr(finishcheck, "find_test_command",
                        lambda root: "echo '1 failed: test_price'; exit 1")
    llama.script(sse_turn(tool_calls=[{"id": "w", "name": "write_file",
                                       "arguments": json.dumps({"path": "README.md", "content": "hello\n"})}]),
                 sse_turn("Fixed. All tests pass."))
    gateway.script(sse_turn("fixed for real"))
    h, _ = _smart(harness, monkeypatch, llama)
    h.session.submit(SMALL)
    fin = h.wait_for(Event.TURN_FINISHED)
    sent = _chat_requests(gateway)[0]["messages"]
    assert any("1 failed: test_price" in str(m.get("content")) for m in sent if m["role"] == "user")
    assert fin["data"]["stepped_up_from_local"]
    assert any("tests fail" in (e["data"].get("reason") or "") for e in h.of(Event.ROUTED))


def test_passing_tests_keep_the_local_answer(harness, gateway, llama, monkeypatch):
    monkeypatch.setattr(finishcheck, "find_test_command", lambda root: "echo ok; exit 0")
    llama.script(sse_turn(tool_calls=[{"id": "w", "name": "write_file",
                                       "arguments": json.dumps({"path": "README.md", "content": "hello\n"})}]),
                 sse_turn("Fixed the typo."))
    h, _ = _smart(harness, monkeypatch, llama)
    h.session.submit(SMALL)
    fin = h.wait_for(Event.TURN_FINISHED)
    assert _chat_requests(gateway) == [] and fin["data"]["cost"] == 0
    assert not fin["data"]["stepped_up_from_local"]


def test_the_finish_check_asks_like_any_command_in_default_mode(harness, gateway, llama, monkeypatch):
    monkeypatch.setattr(finishcheck, "find_test_command", lambda root: "exit 1")
    llama.script(sse_turn(tool_calls=[{"id": "w", "name": "write_file",
                                       "arguments": json.dumps({"path": "README.md", "content": "hi\n"})}]),
                 sse_turn("Done."))
    h, _ = _smart(harness, monkeypatch, llama, mode=Mode.ACCEPT_EDITS)
    h.session.submit(SMALL)
    prop = h.wait_for(Event.TOOL_PROPOSED)
    assert prop["data"]["name"] == "run_bash" and prop["data"]["args"]["command"] == "exit 1"
    h.session.respond_approval(prop["data"]["token"], "deny")
    fin = h.wait_for(Event.TURN_FINISHED)
    assert not fin["data"]["stepped_up_from_local"] and _chat_requests(gateway) == []


def test_a_failing_local_server_hands_the_hop_to_the_cloud(harness, gateway, llama, monkeypatch):
    llama.script(["__drop__"])
    gateway.script(sse_turn("cloud took over"))
    h, local = _smart(harness, monkeypatch, llama)
    h.session.submit(LOOKUP)
    h.wait_for(Event.TURN_FINISHED, timeout=20)
    assert local.recovered >= 1 and len(llama.requests) == 1
    assert h.of(Event.ASSISTANT_MESSAGE)[-1]["data"]["text"] == "cloud took over"
    assert local.server.in_use == 0


def test_after_a_step_up_the_turn_stays_on_cloud_and_the_next_message_starts_local(
        harness, gateway, llama, monkeypatch, tmp_path):
    (tmp_path / "price.py").write_text("x = 1\n")
    llama.script(sse_turn("guess"),                                    # turn 1: steps up
                 _read("price.py"), sse_turn("x is one"))              # turn 2: local again
    gateway.script(_read("price.py"), sse_turn("x is 1"))              # turn 1 on cloud: 2 hops
    h, _ = _smart(harness, monkeypatch, llama)
    h.session.submit(LOOKUP)
    h.wait_for(Event.TURN_FINISHED)
    assert len(llama.requests) == 1 and len(_chat_requests(gateway)) == 2
    h.session.submit("In one sentence, what is x in price.py? Do not change any files.")
    fin = h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert len(llama.requests) == 3 and len(_chat_requests(gateway)) == 2
    assert fin["data"]["local"] is True


def test_text_tool_calls_become_calls_with_ids_unique_across_turns(harness, llama, monkeypatch, tmp_path):
    (tmp_path / "a.txt").write_text("alpha")
    call = '<tool_call>{"name":"read_file","arguments":{"path":"a.txt"}}</tool_call>'
    llama.script(sse_turn(call), sse_turn("it says alpha"), sse_turn(call), sse_turn("still alpha"))
    h, _ = _smart(harness, monkeypatch, llama)
    h.session.submit("In one sentence, what does a.txt say? Do not change any files.")
    h.wait_for(Event.TURN_FINISHED)
    h.session.submit("In one sentence, what does a.txt say now? Do not change any files.")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    ids = [tc["id"] for m in h.session.state["messages"] if m["role"] == "assistant"
           for tc in m.get("tool_calls") or []]
    assert len(ids) == 2 and len(set(ids)) == 2
    assert all(e["data"]["kind"] == "text_parsed" for e in h.of(Event.TOOL_REPAIRED))


def test_escalate_goes_to_the_pick_and_harder_one_step_beyond(harness, llama, monkeypatch):
    h, _ = _smart(harness, monkeypatch, llama)
    s = h.session
    s.state["messages"].append({"role": "user", "content": SMALL})
    s._route_turn(SMALL)
    assert s.state["_on_local"]
    pick = s._serving()
    s.escalate()
    s._take_route_request()
    assert not s.state["_on_local"] and s._serving() == pick
    s._route_turn(SMALL)
    s.escalate(harder=True)
    s._take_route_request()
    assert not s.state["_on_local"] and s._serving() != pick
    assert s.state["_smart_escalations"] == 1


def test_back_to_local_is_refused_for_a_chat_over_half_the_window(harness, llama, monkeypatch):
    h, _ = _smart(harness, monkeypatch, llama, local=StubLocal(llama, window=4096))
    h.session.state["messages"].append({"role": "user", "content": "x " * 6000})
    h.session.deescalate()
    note = [e["data"]["message"] for e in h.of(Event.NOTICE)]
    assert any("can't go back to the local model" in n and "half" in n for n in note)
    assert h.session._route_request is None


def test_a_long_chat_starts_on_the_cloud_not_the_local_model(harness, gateway, llama, monkeypatch):
    gateway.script(sse_turn("cloud"))
    h, _ = _smart(harness, monkeypatch, llama, local=StubLocal(llama, window=4096))
    h.session.state["messages"].append({"role": "user", "content": "x " * 6000})
    h.session.state["messages"].append({"role": "assistant", "content": "ok"})
    h.session.submit(LOOKUP)
    h.wait_for(Event.TURN_FINISHED)
    assert llama.requests == []



# ---- a local model picked in the model list, like any other model ----------------

def test_a_picked_local_model_runs_every_step_and_never_steps_up(harness, gateway, llama, monkeypatch):
    monkeypatch.setattr(finishcheck, "find_test_command", lambda root: "exit 1")
    llama.script(sse_turn(tool_calls=[{"id": "w", "name": "write_file",
                                       "arguments": json.dumps({"path": "README.md", "content": "hi\n"})}]),
                 sse_turn("Done."))
    local = StubLocal(llama)
    h = harness(mode=Mode.BYPASS, model="local/tiny", route_mode="off", local=local)
    h.session.catalog = CATALOG
    h.session.submit(STANDARD)                  # any level: the user picked it
    fin = h.wait_for(Event.TURN_FINISHED)
    assert _chat_requests(gateway) == [] and len(llama.requests) == 2
    assert local.picked == "local/tiny"
    assert fin["data"]["cost"] == 0 and fin["data"]["local_steps"] == 2
    assert not fin["data"]["stepped_up_from_local"] and h.of(Event.ROUTED) == []
    assert h.of(Event.TOOL_PROPOSED) == [] and len(h.of(Event.TOOL_STARTED)) == 1, \
        "no finish check or step-up: the picked model is the one the user wants"
    sent = llama.requests[0]
    assert sent["model"] == "local/tiny" and sum(1 for m in sent["messages"] if m["role"] == "system") == 1


def test_a_picked_local_model_that_is_not_ready_ends_the_turn_with_the_reason(harness, gateway, llama):
    h = harness(model="local/tiny", route_mode="off", local=StubLocal(llama, ready=False))
    h.session.submit("hi")
    h.session.respond_ask(h.wait_for(Event.ASK_USER)["data"]["token"], ["Stop"])
    err = h.wait_for(Event.ERROR)
    assert err["data"]["fatal"] and "not downloaded" in err["data"]["message"]
    assert _chat_requests(gateway) == [] and llama.requests == []


def test_a_picked_local_model_with_local_turned_off_says_so(harness, gateway, llama):
    h = harness(model="local/tiny", route_mode="off", local=StubLocal(llama), local_enabled=False)
    h.session.submit("hi")
    h.session.respond_ask(h.wait_for(Event.ASK_USER)["data"]["token"], ["Stop"])
    err = h.wait_for(Event.ERROR)
    assert "turned off" in err["data"]["message"] and _chat_requests(gateway) == []


# ---- a picked local model and its small window ------------------------------

LOCAL_ROW = {"id": "local/tiny", "context_length": 16384, "supports_tools": True, "local": True,
             "pricing": {"prompt_usd_per_1m": "0", "completion_usd_per_1m": "0"}}
TOO_LONG = json.dumps({"error": {
    "code": 400, "message": "the request exceeds the available context size, try increasing it",
    "type": "exceed_context_size_error", "n_prompt_tokens": 17000, "n_ctx": 16384}})


def _bash(i):
    return sse_turn(tool_calls=[{"id": f"b{i}", "name": "run_bash", "arguments": json.dumps(
        {"command": f"python3 -c \"print('x' * 9000)\"  # {i}"})}])


def _picked(harness, llama, **kw):
    h = harness(mode=Mode.BYPASS, model="local/tiny", route_mode="off",
                local=StubLocal(llama, window=16384), auto_compact=True, **kw)
    h.session.catalog = [LOCAL_ROW] + CATALOG
    return h


def test_a_picked_local_model_is_trimmed_at_its_own_window(harness, gateway, llama):
    llama.script(*[_bash(i) for i in range(16)], sse_turn("Done."))
    h = _picked(harness, llama)
    h.session.submit("print a lot")
    h.wait_for(Event.TURN_FINISHED, timeout=60)
    assert h.of(Event.COMPACTED), "a 16k local window must be trimmed long before 128k"
    sent = [len(json.dumps(r["messages"])) // 4 for r in llama.requests]
    assert max(sent) < 16384 * 0.8, sent


def test_when_the_local_runner_says_too_long_the_chat_is_trimmed_and_retried(harness, gateway, llama):
    llama.script(*[_bash(i) for i in range(6)], [f"__status__:400:{TOO_LONG}"], sse_turn("Done."))
    h = _picked(harness, llama)
    h.session.submit("print a lot")
    fin = h.wait_for(Event.TURN_FINISHED, timeout=60)
    assert h.of(Event.ERROR) == []
    assert [c["data"]["reason"] for c in h.of(Event.COMPACTED)] == \
        ["the chat outgrew the local model's window"]
    assert len(llama.requests) == 8 and len(llama.requests[-1]["messages"]) < len(llama.requests[-2]["messages"])
    assert fin["data"]["cost"] == 0


def test_a_chat_that_cannot_be_trimmed_to_fit_says_so_plainly(harness, gateway, llama):
    llama.script([f"__status__:400:{TOO_LONG}"])
    h = _picked(harness, llama)
    h.session.submit("hi")
    err = h.wait_for(Event.ERROR)
    msg = err["data"]["message"]
    assert err["data"]["fatal"] and "16k window" in msg and "gateway" not in msg
    assert _chat_requests(gateway) == []


def test_other_local_errors_name_the_local_model_not_the_gateway(harness, gateway, llama):
    llama.script(['__status__:400:{"error": {"message": "bad request"}}'])
    h = _picked(harness, llama)
    h.session.submit("hi")
    err = h.wait_for(Event.ERROR)
    assert err["data"]["message"] == "the local model returned HTTP 400"

# ---- a picked local model that cannot run: warn, ask, then the cloud ----------

def _picked_not_ready(harness, llama, monkeypatch):
    monkeypatch.setattr(scores, "load", lambda path=None: DOC)
    h = harness(mode=Mode.BYPASS, model="local/tiny", route_mode="off",
                local=StubLocal(llama, ready=False))
    h.session.catalog = CATALOG
    return h


def test_a_picked_local_model_that_cannot_run_warns_and_asks_before_the_cloud(harness, gateway, llama, monkeypatch):
    gateway.script(sse_turn("The bulk discount is applied before tax."))
    h = _picked_not_ready(harness, llama, monkeypatch)
    h.session.submit(LOOKUP)
    ask = h.wait_for(Event.ASK_USER)
    q = ask["data"]["questions"][0]
    assert "can't run on this computer" in q["question"] and "cost money" in q["question"]
    assert [o["label"] for o in q["options"]] == ["Use the cloud for this task", "Stop"]
    assert ask["data"]["from_app"] is True, "no \"Let the model decide\" on a question from the app"
    assert any(n["data"]["level"] == "warn" and "can't run" in n["data"]["message"]
               for n in h.of(Event.NOTICE))
    time.sleep(0.1)
    assert _chat_requests(gateway) == [], "nothing is spent before the user says yes"

    h.session.respond_ask(ask["data"]["token"], ["Use the cloud for this task"])
    fin = h.wait_for(Event.TURN_FINISHED)
    sent = _chat_requests(gateway)
    assert len(sent) == 1 and sent[0]["model"] in DOC["models"] and llama.requests == []
    assert h.of(Event.ROUTED)[-1]["data"]["model"] == sent[0]["model"]
    assert fin["data"]["cost"] > 0, "a cloud step is billed like any other"
    assert h.session.cfg["model"] == "local/tiny" and h.session.cfg["route_mode"] == "off", \
        "the user's choice of model and routing is not changed"


def test_saying_stop_ends_the_task_with_the_reason_and_spends_nothing(harness, gateway, llama, monkeypatch):
    h = _picked_not_ready(harness, llama, monkeypatch)
    h.session.submit(LOOKUP)
    ask = h.wait_for(Event.ASK_USER)
    h.session.respond_ask(ask["data"]["token"], ["Stop"])
    err = h.wait_for(Event.ERROR)
    assert "not downloaded" in err["data"]["message"] and _chat_requests(gateway) == []


def test_a_yes_covers_one_task_and_the_next_message_asks_again(harness, gateway, llama, monkeypatch):
    gateway.script(sse_turn("First."), sse_turn("Second."))
    h = _picked_not_ready(harness, llama, monkeypatch)
    h.session.submit(LOOKUP)
    h.session.respond_ask(h.wait_for(Event.ASK_USER)["data"]["token"], ["Use the cloud for this task"])
    h.wait_for(Event.TURN_FINISHED)
    h.session.submit(SMALL)
    second = h.wait_for(Event.ASK_USER, min_count=2)
    h.session.respond_ask(h.of(Event.ASK_USER)[-1]["data"]["token"], ["Use the cloud for this task"])
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert len(h.of(Event.ASK_USER)) == 2 and len(_chat_requests(gateway)) == 2
