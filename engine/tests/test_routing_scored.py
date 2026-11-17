"""Smart routing over the per-level scores: the pick is a model AND a
thinking level, chosen by lowest expected cost to finish."""
import copy
import json

from fake_gateway import sse_turn

from meshharness.core import router, scores
from meshharness.protocol import Event


def _e(everyday, hard, cost, minutes, em=5, hm=5):
    return {"sendable": True, "everyday": {"score": everyday, "margin": em},
            "hard": {"score": hard, "margin": hm}, "cost_per_task": cost,
            "minutes_per_task": minutes}


# Three models, the shape of the real list: a cheap one that is fine on easy
# work and weak on hard work, a mid one, and a strong, expensive one.
DOC = {"models": {
    "t/cheap": {"name": "Cheap", "price_in_per_1m": 0.1, "price_out_per_1m": 0.4, "levels": {
        "high": _e(85, 15, 0.30, 10)}},
    "t/mid": {"name": "Mid", "price_in_per_1m": 2, "price_out_per_1m": 10, "levels": {
        "low": _e(75, 25, 0.40, 2), "medium": _e(82, 41, 0.70, 4), "max": _e(86, 48, 2.40, 20)}},
    "t/strong": {"name": "Strong", "price_in_per_1m": 15, "price_out_per_1m": 75, "levels": {
        "high": _e(90, 52, 7.0, 24), "max": _e(90, 60, 20.0, 25)}},
}}


def _cat(*ids, **over):
    rows = []
    for m in ids or DOC["models"]:
        f = DOC["models"].get(m, {})
        rows.append({"id": m, "context_length": 200000, "supports_tools": True,
                     "pricing": {"prompt_usd_per_1m": f.get("price_in_per_1m", 1),
                                 "completion_usd_per_1m": f.get("price_out_per_1m", 1)}, **over})
    return rows


def _pick(difficulty, **kw):
    return router.pick_scored(difficulty, kw.pop("weights", None), kw.pop("doc", DOC),
                              kw.pop("catalog", _cat()), **kw)


def _id(got):
    return (got["model"], got["level"])


# ---- the pick -------------------------------------------------------------------

def test_easy_work_goes_cheap_and_harder_work_goes_to_the_middle_model():
    assert _id(_pick("low")) == ("t/cheap", "high")
    assert _id(_pick("mid")) == ("t/mid", "medium")
    assert _pick("high")["model"] == "t/mid", \
        "trying the mid model first beats paying for the strong one up front"


def test_expected_cost_is_attempt_plus_wait_plus_failure_risk():
    top = _pick("high")["ranked"][0]
    e = DOC["models"][top["model"]]["levels"][top["level"]]
    strongest = DOC["models"]["t/strong"]["levels"]["max"]
    rescue = strongest["cost_per_task"] + 0.05 * strongest["minutes_per_task"]
    chance = (e["hard"]["score"] - e["hard"]["margin"]) / 100
    want = e["cost_per_task"] + 0.05 * e["minutes_per_task"] + (1 - chance) * rescue
    assert abs(top["expected"] - want) < 0.011
    assert top["chance"] == round(chance * 100)


def test_a_model_that_cannot_do_hard_work_is_not_offered_it_however_cheap():
    free = copy.deepcopy(DOC)
    free["models"]["t/cheap"]["levels"]["high"]["cost_per_task"] = 0.0001
    got = _pick("high", doc=free, weights={"cost": 0.98, "cap": 0.01, "speed": 0.01})
    assert got["model"] != "t/cheap"
    assert all(r["model"] != "t/cheap" for r in got["ranked"])


def test_max_means_the_strongest_whatever_it_costs():
    assert _id(_pick("max")) == ("t/strong", "max")
    assert _id(_pick("max", weights={"cost": 0.98, "cap": 0.01, "speed": 0.01})) == ("t/strong", "max")


def test_the_three_sliders_each_move_the_pick_their_own_way():
    assert _pick("mid", weights={"cost": 0.9, "cap": 0.05, "speed": 0.05})["model"] in ("t/cheap", "t/mid")
    quality = _pick("mid", weights={"cost": 0.1, "cap": 0.8, "speed": 0.1})
    cheap = _pick("mid", weights={"cost": 0.9, "cap": 0.05, "speed": 0.05})
    assert quality["ranked"][0]["chance"] >= cheap["ranked"][0]["chance"]
    fast = _pick("low", weights={"cost": 0.1, "cap": 0.1, "speed": 0.8})
    assert _pick("low")["model"] == "t/cheap" and fast["model"] == "t/mid", \
        "when speed leads, the 10-minute cheap entry loses to a model that answers in a few"


# ---- stepping up after a failure ----------------------------------------------------

def test_after_a_failure_only_something_clearly_stronger_is_picked():
    # mid@medium has a full hard score of 41: mid@max (48-5=43) clears it, cheap never does
    got = _pick("high", failed=("t/mid", "medium"))
    assert _id(got) == ("t/mid", "max")
    assert all((r["model"], r["level"]) != ("t/mid", "medium") for r in got["ranked"])
    # strong@high (52-5=47) clears mid@max's 48? No: only strong@max (55) does
    assert _id(_pick("high", failed=("t/mid", "max"))) == ("t/strong", "max")


def test_when_nothing_is_clearly_stronger_the_strongest_left_is_used():
    got = _pick("high", failed=("t/strong", "max"))
    assert _id(got) == ("t/strong", "high")


def test_a_failed_entry_that_has_left_the_list_does_not_break_the_pick():
    assert _pick("high", failed=("gone/model", "max")) is not None


# ---- staying put -----------------------------------------------------------------------

def test_the_serving_model_keeps_the_turn_while_it_is_close_to_the_best():
    near = copy.deepcopy(DOC)
    near["models"]["t/other"] = {"name": "Other", "levels": {"medium": _e(82, 41, 0.75, 4)}}
    cat = _cat() + [{"id": "t/other", "context_length": 200000}]
    assert _pick("mid", doc=near, catalog=cat)["model"] == "t/mid"
    kept = _pick("mid", doc=near, catalog=cat, incumbent="t/other")
    assert kept["model"] == "t/other" and kept["sticky"] is True


def test_it_does_not_keep_the_turn_when_it_is_clearly_worse():
    got = _pick("mid", incumbent="t/strong")
    assert got["model"] == "t/mid" and got["sticky"] is False


def test_more_cached_context_widens_how_close_counts():
    far = copy.deepcopy(DOC)
    far["models"]["t/other"] = {"name": "Other", "levels": {"medium": _e(82, 41, 2.6, 4)}}
    cat = _cat() + [{"id": "t/other", "context_length": 200000}]
    assert _pick("mid", doc=far, catalog=cat, incumbent="t/other")["model"] == "t/mid"
    assert _pick("mid", doc=far, catalog=cat, incumbent="t/other",
                 incumbent_bonus=12.0)["model"] == "t/other"


# ---- what is available ----------------------------------------------------------------

def test_only_models_the_gateway_offers_and_that_fit_the_job_are_considered():
    assert _pick("mid", catalog=_cat("t/cheap", "t/strong"))["model"] != "t/mid"
    assert _pick("mid", exclude={"t/mid"})["model"] != "t/mid"
    small = _cat("t/cheap", "t/strong") + [{"id": "t/mid", "context_length": 8000}]
    assert _pick("mid", catalog=small, needs_ctx=50000)["model"] != "t/mid"
    no_tools = _cat("t/cheap", "t/strong") + [{"id": "t/mid", "supports_tools": False}]
    assert _pick("mid", catalog=no_tools, needs_tools=True)["model"] != "t/mid"
    assert _pick("mid", catalog=_cat("not/scored")) is None


def test_a_price_change_at_the_gateway_moves_the_cost_with_it():
    before = _pick("mid")["ranked"][0]
    dear = [dict(r, pricing={"prompt_usd_per_1m": 8, "completion_usd_per_1m": 40})
            if r["id"] == "t/mid" else r for r in _cat()]            # t/mid now costs 4x
    after = next(r for r in _pick("mid", catalog=dear)["ranked"]
                 if (r["model"], r["level"]) == (before["model"], before["level"]))
    assert abs(after["cost"] - 4 * before["cost"]) < 0.02


def test_a_level_the_user_fixed_is_the_level_that_is_sent():
    got = _pick("high", level="low")
    assert got["effort"] == "low" and got["level"] == "low"
    # judged by each model's entry nearest that level
    assert got["model"] in DOC["models"]


def test_the_pick_does_not_change_its_inputs():
    doc, cat = copy.deepcopy(DOC), _cat()
    before = (json.dumps(doc, sort_keys=True), json.dumps(cat, sort_keys=True))
    _pick("mid", doc=doc, catalog=cat, failed=("t/mid", "medium"), level="high")
    assert (json.dumps(doc, sort_keys=True), json.dumps(cat, sort_keys=True)) == before


def test_the_bundled_scores_pick_something_at_every_difficulty():
    doc = scores.load()
    cat = [{"id": m, "context_length": 200000} for m in doc["models"]]
    for d in ("low", "mid", "medium", "high", "xhigh", "max"):
        got = router.pick_scored(d, None, doc, cat)
        assert got and got["scored"] and got["effort"] in (None, "none", "low", "medium", "high", "xhigh", "max")


# ---- choose(): scores for coding work, the table for the rest ---------------------------

def test_coding_work_uses_the_scores_and_other_work_uses_the_table():
    table = router.load_table()
    got = router.choose("agentic", "mid", None, table, _cat(), scores_doc=DOC)
    assert got["scored"] and got["cohort"] == "agentic" and got["level"]
    frontier = table["frontiers"]["writing"]
    old = router.choose("writing", "mid", None, table, [{"id": m} for m in frontier[:6]] + _cat(),
                        scores_doc=DOC)
    assert old and not old.get("scored") and old["model"] in frontier


def test_an_account_with_none_of_the_scored_models_falls_back_to_the_table():
    table = router.load_table()
    frontier = table["frontiers"]["agentic"]
    got = router.choose("agentic", "mid", None, table,
                        [{"id": m, "context_length": 200000} for m in frontier[:6]], scores_doc=DOC)
    assert got and not got.get("scored") and got["model"] in frontier


# ---- in the session ---------------------------------------------------------------------

def _scored_session(harness, monkeypatch, **cfg):
    monkeypatch.setattr(scores, "load", lambda path=None: DOC)
    h = harness(route_mode="smart", **cfg)
    h.session.catalog = _cat()
    return h


def test_a_smart_turn_sends_the_picked_model_and_its_thinking_level(gateway, harness, monkeypatch):
    gateway.script(sse_turn("done"))
    h = _scored_session(harness, monkeypatch)
    h.session.submit("fix the failing test in price.py and run the tests")
    h.wait_for(Event.TURN_FINISHED)
    routed = h.of(Event.ROUTED)[0]["data"]
    sent = gateway.requests[0]
    assert sent["model"] == routed["model"] and sent["model"].startswith("t/")
    assert sent["reasoning"] == {"effort": routed["thinking"]}
    assert "level" not in routed, "the notice level must stay free for info/warn"
    assert h.session.cfg["model"] == "test/model" and h.session.cfg["reasoning_effort"] is None


def test_a_level_the_user_set_wins_over_the_routers(gateway, harness, monkeypatch):
    gateway.script(sse_turn("done"))
    h = _scored_session(harness, monkeypatch, reasoning_effort="low")
    h.session.submit("fix the failing test in price.py and run the tests")
    h.wait_for(Event.TURN_FINISHED)
    assert gateway.requests[0]["reasoning"] == {"effort": "low"}


def test_stepping_up_moves_to_a_clearly_stronger_entry_and_the_level_follows(harness, monkeypatch):
    h = _scored_session(harness, monkeypatch)
    st = h.session.state
    st["_smart_cohort"], st["_smart_base_difficulty"] = "agentic", "mid"
    h.session._apply_pick({"model": "t/mid", "level": "medium", "effort": "medium", "scored": True}, "mid")
    assert h.session._smart_escalate("test") is True
    assert h.session._serving() == ("t/mid", "max")
    assert h.session._effective_cfg()["reasoning_effort"] == "max"
    assert any("mid · max" in (e["data"].get("message") or "") for e in h.of(Event.NOTICE))
    assert h.session._smart_escalate("test") is True
    assert h.session._serving() == ("t/strong", "max")


def test_with_routing_off_no_level_is_added(gateway, harness, monkeypatch):
    monkeypatch.setattr(scores, "load", lambda path=None: DOC)
    gateway.script(sse_turn("done"))
    h = harness(route_mode="off")
    h.session.catalog = _cat()
    h.session.submit("fix the failing test in price.py")
    h.wait_for(Event.TURN_FINISHED)
    assert gateway.requests[0]["model"] == "test/model" and "reasoning" not in gateway.requests[0]


def test_an_abandoned_pick_takes_its_level_with_it(harness, monkeypatch):
    h = _scored_session(harness, monkeypatch)
    h.session._apply_pick({"model": "t/mid", "level": "max", "effort": "max", "scored": True}, "mid")
    assert h.session._effective_cfg()["reasoning_effort"] == "max"
    assert h.session._smart_abandon("returned a gateway error") is True
    cfg = h.session._effective_cfg()
    assert cfg["model"] == "test/model" and "reasoning_effort" not in cfg


def test_explaining_a_route_names_the_thinking_level(harness, monkeypatch):
    h = _scored_session(harness, monkeypatch)
    out = h.session.explain_route("fix the failing test in price.py and run the tests")
    assert out["available"] and out["pick"].startswith("t/") and out["thinking"]
