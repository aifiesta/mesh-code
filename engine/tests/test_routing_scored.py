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

def test_each_rung_of_the_ladder_gets_the_entry_that_is_cheapest_to_finish_with():
    # the three levels wording can assign all start on the cheap model
    for rung in ("low", "mid", "high"):                      # Lookup, Small change, Standard task
        assert _id(_pick(rung)) == ("t/cheap", "high"), rung
    assert _pick("xhigh")["model"] == "t/mid", \
        "once a task has beaten a model, the mid one is tried before the strong one"
    deep = _pick("max")                                      # Deep work starts where Complex does
    assert deep["model"] == "t/mid" and deep["deep_start"]


def test_expected_cost_is_attempt_plus_wait_plus_failure_risk():
    top = _pick("xhigh")["ranked"][0]                        # judged on the hard score alone
    e = DOC["models"][top["model"]]["levels"][top["level"]]
    strongest = DOC["models"]["t/strong"]["levels"]["max"]
    rescue = strongest["cost_per_task"] + router.TIME_VALUE * strongest["minutes_per_task"]
    chance = (e["hard"]["score"] - e["hard"]["margin"]) / 100
    want = e["cost_per_task"] + router.TIME_VALUE * e["minutes_per_task"] + (1 - chance) * rescue
    assert abs(top["expected"] - want) < 0.011
    assert top["chance"] == round(chance * 100)


def test_a_model_that_cannot_do_hard_work_is_not_offered_it_however_cheap():
    free = copy.deepcopy(DOC)
    free["models"]["t/cheap"]["levels"]["high"]["cost_per_task"] = 0.0001
    got = _pick("xhigh", doc=free, weights={"cost": 0.98, "cap": 0.01, "speed": 0.01})
    assert got["model"] != "t/cheap"
    assert all(r["model"] != "t/cheap" for r in got["ranked"])


def test_deep_work_steps_up_to_the_strongest_whatever_it_costs():
    start = _pick("max")
    up = _pick("max", failed=(start["model"], start["level"]))
    assert _id(up) == ("t/strong", "max")
    cheap = {"cost": 0.98, "cap": 0.01, "speed": 0.01}
    assert _id(_pick("max", weights=cheap, failed=(start["model"], start["level"]))) == ("t/strong", "max")


def test_the_three_sliders_each_move_the_pick_their_own_way():
    base = _pick("xhigh")["ranked"][0]
    cheap = _pick("xhigh", weights={"cost": 0.9, "cap": 0.05, "speed": 0.05})["ranked"][0]
    quality = _pick("xhigh", weights={"cost": 0.05, "cap": 0.9, "speed": 0.05})["ranked"][0]
    fast = _pick("xhigh", weights={"cost": 0.05, "cap": 0.05, "speed": 0.9})["ranked"][0]
    assert cheap["cost"] <= base["cost"] and fast["minutes"] <= base["minutes"]
    assert quality["chance"] >= base["chance"]
    assert quality["chance"] > cheap["chance"] and fast["minutes"] < quality["minutes"]


# ---- stepping up after a failure ----------------------------------------------------

def test_after_a_failure_only_something_clearly_stronger_is_picked():
    # mid@medium has a full hard score of 41: mid@max (48-5=43) clears it, cheap never does
    got = _pick("xhigh", failed=("t/mid", "medium"))
    assert _id(got) == ("t/mid", "max")
    assert all((r["model"], r["level"]) != ("t/mid", "medium") for r in got["ranked"])
    # strong@high (52-5=47) clears mid@max's 48? No: only strong@max (55) does
    assert _id(_pick("xhigh", failed=("t/mid", "max"))) == ("t/strong", "max")


def test_when_nothing_is_clearly_stronger_the_strongest_left_is_used():
    got = _pick("xhigh", failed=("t/strong", "max"))
    assert _id(got) == ("t/strong", "high")


def test_a_failed_entry_that_has_left_the_list_does_not_break_the_pick():
    assert _pick("high", failed=("gone/model", "max")) is not None


# ---- staying put -----------------------------------------------------------------------

def test_the_serving_model_keeps_the_turn_while_it_is_close_to_the_best():
    near = copy.deepcopy(DOC)
    near["models"]["t/other"] = {"name": "Other", "levels": {"medium": _e(82, 41, 0.75, 4)}}
    cat = _cat() + [{"id": "t/other", "context_length": 200000}]
    assert _pick("xhigh", doc=near, catalog=cat)["model"] == "t/mid"
    kept = _pick("xhigh", doc=near, catalog=cat, incumbent="t/other")
    assert kept["model"] == "t/other" and kept["sticky"] is True


def test_it_does_not_keep_the_turn_when_it_is_clearly_worse():
    got = _pick("xhigh", incumbent="t/strong")
    assert got["model"] == "t/mid" and got["sticky"] is False


def test_more_cached_context_widens_how_close_counts():
    far = copy.deepcopy(DOC)
    far["models"]["t/other"] = {"name": "Other", "levels": {"medium": _e(82, 41, 2.6, 4)}}
    cat = _cat() + [{"id": "t/other", "context_length": 200000}]
    assert _pick("xhigh", doc=far, catalog=cat, incumbent="t/other")["model"] == "t/mid"
    assert _pick("xhigh", doc=far, catalog=cat, incumbent="t/other",
                 incumbent_bonus=12.0)["model"] == "t/other"


# ---- what is available ----------------------------------------------------------------

def test_only_models_the_gateway_offers_and_that_fit_the_job_are_considered():
    assert _pick("xhigh")["model"] == "t/mid"
    assert _pick("xhigh", catalog=_cat("t/cheap", "t/strong"))["model"] != "t/mid"
    assert _pick("xhigh", exclude={"t/mid"})["model"] != "t/mid"
    small = _cat("t/cheap", "t/strong") + [{"id": "t/mid", "context_length": 8000}]
    assert _pick("xhigh", catalog=small, needs_ctx=50000)["model"] != "t/mid"
    no_tools = _cat("t/cheap", "t/strong") + [{"id": "t/mid", "supports_tools": False}]
    assert _pick("xhigh", catalog=no_tools, needs_tools=True)["model"] != "t/mid"
    assert _pick("xhigh", catalog=_cat("not/scored")) is None


def test_a_price_change_at_the_gateway_moves_the_cost_with_it():
    before = _pick("xhigh")["ranked"][0]
    dear = [dict(r, pricing={"prompt_usd_per_1m": 8, "completion_usd_per_1m": 40})
            if r["id"] == "t/mid" else r for r in _cat()]            # t/mid now costs 4x
    after = next(r for r in _pick("xhigh", catalog=dear)["ranked"]
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


# ---- the five task levels -------------------------------------------------------------

# (level, prompt). The list agreed with the user on 2026-11-18; a change to the
# rules that moves any of these should be a deliberate one.
SAMPLES = [
    ("low", "In two sentences, what does price.py do? Do not change any files."),
    ("low", "what does the router do?"),
    ("low", "how does auth work in this repo?"),
    ("low", "why is the login test failing?"),
    ("low", "explain how routing works in this repo"),
    ("mid", "The tests in tests/test_price.py are failing. Find what is wrong in price.py, "
            "fix it, and run the tests to confirm they pass."),
    ("mid", "rename the function line_total to compute_line_total in price.py"),
    ("mid", "fix the typo in README.md"),
    ("mid", "add a health endpoint"),
    ("mid", "can you fix the failing test?"),
    ("mid", "bump the version to 0.2.5"),
    ("mid", "add a health endpoint and run the tests"),
    ("mid", "write unit tests for utils.py"),
    ("mid", "make the button blue"),
    ("mid", "optimize the slow query in reports.py"),
    ("mid", "please review the diff and tell me if anything looks wrong"),
    ("high", "Refactor price.py for production use: fix the failing tests, handle edge cases "
             "such as zero or negative quantities with a clear error, add tests for them, "
             "and run the full test suite."),
    ("high", "update the copyright year in every file"),
    ("high", "Add dark mode: add a theme toggle to the settings page, store the choice, "
             "update the CSS variables and write tests"),
    ("high", "migrate the database layer from sqlite to postgres across the whole project"),
    ("high", "implement pagination in api/users.py, api/orders.py and api/items.py"),
    ("high", "1. add a login page\n2. add a signup page\n3. wire both to the API\n4. write tests"),
    ("high", "set up CI with GitHub Actions to run lint, tests and build on every push"),
    ("high", "design a scalable architecture for a distributed job queue"),
    ("high", "there's a race condition somewhere in the upload worker, find it and fix it"),
    ("high", "the app crashes sometimes on startup, figure out why"),
]


def test_the_agreed_sample_prompts_land_on_their_levels():
    wrong = [(want, router.task_level(text)[0], text[:50]) for want, text in SAMPLES
             if router.task_level(text)[0] != want]
    assert not wrong, wrong


def test_each_rule_names_itself_in_plain_words():
    assert router.task_level("don't modify anything, just tell me what main.py does")[1] \
        == "it says not to change anything"
    assert "question" in router.task_level("where is the config loaded?")[1]
    assert "3 files named" in router.task_level("rename foo in a.py, b.py and c.py")[1]
    assert "whole project" in router.task_level("rename foo everywhere")[1]
    assert "pasted code" in router.task_level("fix this\n```\nx = 1\n```")[1]
    assert "listed items" in router.task_level("- add a\n- add b\n- add c")[1]
    assert "unknown cause" in router.task_level("debug the flaky upload")[1]
    assert router.task_level("fix the typo")[1] == "one or two actions in one or two files"


def test_wording_never_assigns_the_top_two_levels():
    scary = ("Design a rigorous, production-grade, distributed, concurrent, scalable "
             "architecture end-to-end: refactor, migrate, optimise and benchmark every "
             "module in the entire codebase, handle all edge cases, formal proof included. " * 5)
    assert router.task_level(scary)[0] == "high"
    assert all(router.task_level(text)[0] in ("low", "mid", "high") for _, text in SAMPLES)


def test_a_request_phrased_as_a_question_is_still_a_request():
    assert router.task_level("can you rename foo to bar in utils.py?")[0] == "mid"
    assert router.task_level("please explain and then fix the bug, add a test and run it")[0] == "high"


def test_each_level_blends_the_two_scores_more_hard_the_higher_it_is():
    e = {"everyday": {"score": 80, "margin": 0}, "hard": {"score": 40, "margin": 0}}
    got = [round(router._chance(e, rung), 3) for rung in ("low", "mid", "high", "xhigh", "max")]
    assert got == [0.8, 0.7, 0.6, 0.4, 0.4]


# ---- small tasks are priced by the token, not by the benchmark task -----------------------

# Two models whose benchmark tasks cost the same, one with tokens twenty times cheaper.
SAME_BENCH = {"models": {
    "t/cheap-tokens": {"name": "A", "price_in_per_1m": 0.1, "price_out_per_1m": 0.5,
                       "levels": {"max": _e(80, 30, 0.60, 15, em=5, hm=5)}},
    "t/dear-tokens": {"name": "B", "price_in_per_1m": 2, "price_out_per_1m": 10,
                      "levels": {"medium": _e(80, 30, 0.60, 15, em=5, hm=5)}},
}}


def _same_cat():
    return [{"id": m, "context_length": 200000,
             "pricing": {"prompt_usd_per_1m": str(v["price_in_per_1m"]),
                         "completion_usd_per_1m": str(v["price_out_per_1m"])}}
            for m, v in SAME_BENCH["models"].items()]


def test_a_small_change_goes_to_the_model_with_the_cheaper_tokens():
    for rung in ("low", "mid", "high"):
        got = router.pick_scored(rung, None, SAME_BENCH, _same_cat())
        assert got["model"] == "t/cheap-tokens" and got["priced_as"] == "token prices"
        a, b = got["ranked"][0], got["ranked"][1]
        assert b["cost"] > 15 * a["cost"], "the token price gap must show in the cost"


def test_levels_reached_only_by_step_up_keep_the_benchmark_cost():
    got = router.pick_scored("xhigh", None, SAME_BENCH, _same_cat())
    assert got["priced_as"] == "benchmark task"
    assert {r["cost"] for r in got["ranked"]} == {0.6}


def test_a_higher_thinking_level_costs_more_on_a_small_task_too():
    got = router.pick_scored("mid", None, DOC, _cat("t/mid"))
    cost = {r["level"]: r["cost"] for r in got["ranked"]}
    assert cost["low"] < cost["medium"] < cost["max"]
    assert cost["max"] < 6 * cost["low"], \
        "input dominates a small task, so the gap is narrower than on a benchmark task (6x)"


def test_an_unknown_price_keeps_every_entry_on_the_benchmark_figures():
    cat = _same_cat()
    cat[0] = {"id": cat[0]["id"], "context_length": 200000}        # no pricing row
    got = router.pick_scored("mid", None, SAME_BENCH, cat)
    assert got["priced_as"] == "benchmark task", "never a mix of the two scales"


def test_the_bundled_scores_send_small_work_cheap_and_the_top_to_the_strongest():
    doc = scores.load()
    cat = [{"id": m, "context_length": 200000,
            "pricing": {"prompt_usd_per_1m": str(v["price_in_per_1m"]),
                        "completion_usd_per_1m": str(v["price_out_per_1m"])}}
           for m, v in doc["models"].items()]
    price = {m: v["price_in_per_1m"] for m, v in doc["models"].items()}
    deep = router.pick_scored("max", None, doc, cat)
    for rung in ("low", "mid", "high"):
        start = router.pick_scored(rung, None, doc, cat)
        assert price[start["model"]] <= 0.25, f"{rung} should not start on a dear model"
        assert price[start["model"]] < price[deep["model"]]
    # Deep work starts on the Complex pick, thinking at DEEP_WORK_LEVEL when it
    # was tested there; its step-up reaches the model of the strongest entry,
    # at that level too.
    ents = scores.entries(doc)
    complex_pick = router.pick_scored("xhigh", None, doc, cat)
    assert deep["model"] == complex_pick["model"] and deep["deep_start"]
    tested = {e["level"] for e in ents if e["model"] == deep["model"]}
    assert deep["level"] == (router.DEEP_WORK_LEVEL if router.DEEP_WORK_LEVEL in tested
                             else complex_pick["level"])
    up = router.pick_scored("max", None, doc, cat, failed=(deep["model"], deep["level"]))
    best = max(ents, key=lambda e: e["hard"]["score"] - e["hard"]["margin"])
    assert up["model"] == best["model"]
    tested = {e["level"] for e in ents if e["model"] == best["model"]}
    assert up["level"] == (router.DEEP_WORK_LEVEL if router.DEEP_WORK_LEVEL in tested
                           else best["level"])


# ---- the table serving a coding turn ------------------------------------------------------

def test_without_scored_models_the_table_still_gets_its_old_three_way_difficulty(monkeypatch):
    seen = []
    real = router.effective_weights
    monkeypatch.setattr(router, "effective_weights", lambda w, lvl: seen.append(lvl) or real(w, lvl))
    table = router.load_table()
    cat = [{"id": m, "context_length": 200000} for m in table["frontiers"]["agentic"][:6]]
    for rung in ("low", "mid", "high", "xhigh", "max"):
        router.choose("agentic", rung, None, table, cat, scores_doc=DOC)
    assert seen == ["low", "low", "mid", "high", "max"]


# ---- in the session -------------------------------------------------------------------------

def test_the_route_note_names_the_task_level_and_why(gateway, harness, monkeypatch):
    gateway.script(sse_turn("done"))
    h = _scored_session(harness, monkeypatch)
    h.session.submit("rename line_total to compute_line_total in price.py")
    h.wait_for(Event.TURN_FINISHED)
    routed = h.of(Event.ROUTED)[0]["data"]
    assert routed["task"] == "Small change" and routed["difficulty"] == "mid"
    assert routed["why"] == "one or two actions in one or two files"


def test_a_prompt_full_of_hard_words_starts_no_higher_than_a_standard_task(gateway, harness, monkeypatch):
    gateway.script(sse_turn("done"))
    h = _scored_session(harness, monkeypatch)
    h.session.submit("Refactor price.py for production use: fix the failing tests, handle edge "
                     "cases, add tests for them, and run the full test suite.")
    h.wait_for(Event.TURN_FINISHED)
    routed = h.of(Event.ROUTED)[0]["data"]
    assert routed["task"] == "Standard task"
    assert routed["model"] == "t/cheap", "a standard task starts on the cheap model"
    # ...and only climbs when the model visibly struggles
    assert h.session._smart_escalate("test") is True
    assert h.session.state["_smart_difficulty"] == "xhigh"
    assert h.session._serving()[0] in ("t/mid", "t/strong")


def test_the_level_the_user_forces_is_the_level_used(gateway, harness, monkeypatch):
    gateway.script(sse_turn("done"))
    h = _scored_session(harness, monkeypatch, route_effort="max")
    h.session.submit("fix the typo in README.md")
    h.wait_for(Event.TURN_FINISHED)
    routed = h.of(Event.ROUTED)[0]["data"]
    assert routed["task"] == "Deep work" and routed["why"] == "you set it in Settings"
    # Deep work starts on the Complex pick (router.DEEP_WORK_LEVEL)
    assert routed["model"] == "t/mid"


def test_explaining_a_route_gives_the_level_and_the_rule_behind_it(harness, monkeypatch):
    h = _scored_session(harness, monkeypatch)
    out = h.session.explain_route("what does price.py do?")
    assert out["task"] == "Lookup" and "question" in out["why"]


def test_settings_name_the_five_levels():
    from meshharness import settings
    labels = [o["label"] for o in settings.BY_KEY["route_effort"]["options"]]
    assert labels == ["Auto", "Lookup", "Small change", "Standard task", "Complex task", "Deep work"]


def test_the_wait_shrinks_with_the_cost_entry_by_entry():
    ents = router._live_entries(DOC, _cat(), None, False, 0, None)
    for e, p in zip(ents, router._at_token_prices(ents, _cat(), "mid")):
        assert abs(p["minutes_per_task"] / e["minutes_per_task"]
                   - p["cost_per_task"] / e["cost_per_task"]) < 1e-9


def test_a_bigger_level_is_priced_as_a_bigger_task():
    ents = router._live_entries(DOC, _cat(), None, False, 0, None)
    cost = {rung: router._at_token_prices(ents, _cat(), rung)[0]["cost_per_task"]
            for rung in ("low", "mid", "high")}
    assert cost["low"] < cost["mid"] < cost["high"]


# ---- a failure is priced as one step up, not a jump to the strongest ----------------------

def _k():
    return router._slider_scale(None)


def test_just_under_the_top_a_failure_costs_the_strongest_entry():
    ents = router._live_entries(DOC, _cat(), None, False, 0, None)
    strongest = DOC["models"]["t/strong"]["levels"]["max"]
    assert abs(router._step_up_price(ents, "xhigh", _k())
               - (strongest["cost_per_task"] + router.TIME_VALUE * strongest["minutes_per_task"])) < 1e-9


def test_lower_down_a_failure_costs_less_because_it_climbs_one_level_at_a_time():
    ents = router._live_entries(DOC, _cat(), None, False, 0, None)
    prices = [router._step_up_price(ents, rung, _k()) for rung in ("low", "mid", "high", "xhigh")]
    assert prices == sorted(prices) and prices[0] < prices[-1]
    # one level up from "high" is the best attempt at "xhigh", itself backed by the strongest
    top = prices[-1]
    best_xhigh = min(router._expected(e, "xhigh", _k(), top)["total"] for e in ents
                     if router._chance(e, "xhigh") >= 0.6 * max(router._chance(x, "xhigh") for x in ents))
    assert abs(prices[2] - best_xhigh) < 1e-9


# A cheap model, and one fifteen times dearer that is 14 points likelier to finish.
DEAR_BUT_BETTER = {"models": {
    "t/cheap": {"name": "Cheap", "price_in_per_1m": 0.2, "price_out_per_1m": 0.6,
                "levels": {"max": _e(84, 31, 0.59, 15)}},
    "t/dear": {"name": "Dear", "price_in_per_1m": 3.0, "price_out_per_1m": 15.0,
               "levels": {"high": _e(90, 52, 7.2, 24)}},
    "t/top": {"name": "Top", "price_in_per_1m": 4.0, "price_out_per_1m": 20.0,
              "levels": {"max": _e(89, 54, 20.0, 25)}},
}}


def _dear_cat():
    return [{"id": m, "context_length": 200000,
             "pricing": {"prompt_usd_per_1m": str(v["price_in_per_1m"]),
                         "completion_usd_per_1m": str(v["price_out_per_1m"])}}
            for m, v in DEAR_BUT_BETTER["models"].items()]


def test_a_much_dearer_model_does_not_start_a_task_for_a_few_points_of_chance():
    for rung in ("low", "mid", "high"):
        got = router.pick_scored(rung, None, DEAR_BUT_BETTER, _dear_cat())
        assert got["model"] == "t/cheap", rung
    # the user can still ask for it
    assert router.pick_scored("high", {"cost": 0.02, "cap": 0.96, "speed": 0.02},
                              DEAR_BUT_BETTER, _dear_cat())["model"] != "t/cheap"


# ---- too close to call: the one that is cheapest to try ---------------------------------------

def _two(cost_b, hard_b=41):
    return {"models": {
        "t/a": {"name": "A", "levels": {"medium": _e(82, 41, 1.00, 4)}},
        "t/b": {"name": "B", "levels": {"medium": _e(82, hard_b, cost_b, 4)}},
        "t/strong": DOC["models"]["t/strong"]}}


def _plain(*ids):
    return [{"id": m, "context_length": 200000} for m in ids]      # no prices: benchmark figures


def test_between_two_entries_too_close_to_call_the_cheaper_attempt_wins():
    # B is five points likelier to finish and its total is a shade lower, but it costs 50% more to try
    got = router.pick_scored("xhigh", None, _two(1.5, hard_b=46), _plain("t/a", "t/b", "t/strong"))
    totals = {r["model"]: r["expected"] for r in got["ranked"]}
    assert totals["t/b"] < totals["t/a"] < totals["t/b"] * 1.10
    assert got["model"] == "t/a"


def test_a_clear_difference_is_not_treated_as_a_tie():
    got = router.pick_scored("xhigh", None, _two(1.5, hard_b=52), _plain("t/a", "t/b", "t/strong"))
    totals = {r["model"]: r["expected"] for r in got["ranked"]}
    assert totals["t/a"] > totals["t/b"] * 1.10
    assert got["model"] == "t/b"


# ---- the estimate follows how the app is really billed ------------------------------------------

def _cache_cat(model, cache_price="0.02"):
    return [{"id": model, "context_length": 200000,
             "pricing": {"prompt_usd_per_1m": "1.0", "completion_usd_per_1m": "4.0",
                         "cache_read_input_usd_per_1m": cache_price}}]


def _priced(model, **kw):
    doc = {"models": {model: {"name": "M", "levels": {"high": _e(80, 40, 1.0, 5)}}}}
    cat = kw.pop("catalog", None) or _cache_cat(model)
    ents = router._live_entries(doc, cat, None, False, 0, None)
    return router._at_token_prices(ents, cat, "mid", **kw)[0]


def test_resent_input_is_counted_at_the_cached_price_where_it_is_billed_that_way():
    sent, written = router.TOKEN_PRICED["mid"]
    e = _priced("deepseek/x")
    want = (sent * 0.25 * 1.0 + sent * 0.75 * 0.02 + written * 4.0) / 1e6
    assert abs(e["cost_per_task"] - want) < 1e-9
    listed = (sent * 1.0 + written * 4.0) / 1e6
    assert e["cost_per_task"] < 0.4 * listed


def test_a_model_with_no_cached_price_is_counted_in_full():
    sent, written = router.TOKEN_PRICED["mid"]
    cat = [{"id": "deepseek/x", "context_length": 200000,
            "pricing": {"prompt_usd_per_1m": "1.0", "completion_usd_per_1m": "4.0"}}]
    assert abs(_priced("deepseek/x", catalog=cat)["cost_per_task"]
               - (sent * 1.0 + written * 4.0) / 1e6) < 1e-9


def test_anthropic_models_are_counted_at_full_price():
    """The app asks Claude models to cache, but the router does not count
    on it: the estimate would leave out the cache write price and Claude's
    larger token counts, and would move small tasks off DeepSeek on that."""
    sent, written = router.TOKEN_PRICED["mid"]
    assert abs(_priced("anthropic/claude-x")["cost_per_task"]
               - (sent * 1.0 + written * 4.0) / 1e6) < 1e-9
    assert _priced("deepseek/x")["cost_per_task"] < 0.4 * _priced("anthropic/claude-x")["cost_per_task"]


def test_caching_makes_a_task_cheaper_not_faster():
    assert _priced("deepseek/x")["minutes_per_task"] == \
        _priced("anthropic/claude-x")["minutes_per_task"]


def test_deep_work_starts_on_the_complex_model_at_extra_high_and_steps_up_at_extra_high():
    """Deep work starts on the model Complex would get, thinking at extra high
    when it was tested there (router.DEEP_WORK_LEVEL); a step-up goes to the
    strongest model, also at extra high when tested there. A level the user
    fixed skips both rules."""
    doc = copy.deepcopy(DOC)
    doc["models"]["t/mid"]["levels"]["xhigh"] = _e(84, 46, 1.4, 9)
    doc["models"]["t/strong"]["levels"]["xhigh"] = _e(90, 58, 12.0, 16)
    start = _pick("max", doc=doc)
    assert _id(start) == ("t/mid", "xhigh") and start["effort"] == "xhigh" and start["deep_start"]
    assert _pick("xhigh", doc=doc)["model"] == "t/mid", "Complex keeps its own level"
    up = _pick("max", doc=doc, failed=("t/mid", "xhigh"))
    assert _id(up) == ("t/strong", "xhigh") and not up.get("deep_start")
    assert _id(_pick("max", doc=doc, level="max")) == ("t/strong", "max"), "the user's level wins"
    # no extra-high entry anywhere: the Complex pick's own level, then strongest at max
    assert _pick("max")["model"] == "t/mid"
