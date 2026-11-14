"""Token-budget defaults: the one-time config migration, the monthly tally,
and the router's budget steering."""
import json

from meshharness.core import config as cfg_mod
from meshharness.protocol import Event
from test_session import _agentic_catalog


def _write_config(data):
    cfg_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg_mod.CONFIG_FILE.write_text(json.dumps(data))


def test_migration_turns_on_caching_and_smart_routing_once():
    _write_config({"optimize": 0.0, "route_mode": "off", "model": "x/y"})
    cfg = cfg_mod.load_config()
    assert cfg["optimize"] == 0.1 and cfg["route_mode"] == "smart"
    assert json.loads(cfg_mod.CONFIG_FILE.read_text())["defaults_rev"] == 1

    # A user who switches back afterwards keeps their choice.
    cfg["route_mode"] = "off"
    cfg["optimize"] = 0.0
    cfg_mod.save_config(cfg)
    again = cfg_mod.load_config()
    assert again["route_mode"] == "off" and again["optimize"] == 0.0


def test_migration_keeps_non_default_choices():
    _write_config({"optimize": 0.3, "route_mode": "auto"})
    cfg = cfg_mod.load_config()
    assert cfg["optimize"] == 0.3 and cfg["route_mode"] == "auto"


def test_migration_never_rewrites_an_unreadable_config():
    cfg_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg_mod.CONFIG_FILE.write_text("{not json")
    cfg_mod.load_config()
    assert cfg_mod.CONFIG_FILE.read_text() == "{not json"


def test_month_cost_resets_when_the_month_rolls():
    assert cfg_mod.month_cost({"month": {"key": cfg_mod.month_key(), "cost": 4.5}}) == 4.5
    assert cfg_mod.month_cost({"month": {"key": "1999-01", "cost": 4.5}}) == 0.0
    assert cfg_mod.month_cost({}) == 0.0


def _spent(cost):
    cfg_mod.save_usage({"month": {"key": cfg_mod.month_key(), "cost": cost}})


def _smart(harness, **over):
    h = harness(route_mode="smart", route_effort="auto", monthly_budget=10, **over)
    h.session.catalog = _agentic_catalog()
    return h


def test_over_70_percent_routes_low_and_says_so_once(harness):
    _spent(8)
    h = _smart(harness)
    h.session._route_turn("refactor this module and fix the failing test")
    assert h.session.state["_smart_difficulty"] == "low"
    h.session._route_turn("now refactor the other module too")
    notices = [e for e in h.of(Event.NOTICE) if "budget" in e["data"]["message"]]
    assert len(notices) == 1


def test_under_budget_routing_is_unchanged(harness):
    _spent(1)
    h = _smart(harness)
    h.session._route_turn("refactor this module and fix the failing test")
    assert not [e for e in h.of(Event.NOTICE) if "budget" in e["data"]["message"]]


def _escalation_state(h):
    st = h.session.state
    st.update(_smart_cohort="agentic", _smart_difficulty="low",
              _smart_base_difficulty="low", _smart_last=None,
              _smart_escalations=0, _smart_clean_hops=0)


def test_over_90_percent_never_escalates(harness):
    _spent(9.5)
    h = _smart(harness)
    _escalation_state(h)
    assert h.session._smart_escalate("test") is False


def test_escalation_respects_never_use_list(harness):
    h = _smart(harness)
    _escalation_state(h)
    assert h.session._smart_escalate("test") is True
    first = h.session.state["_smart_pick"]

    h2 = _smart(harness, exclude_models=[first])
    _escalation_state(h2)
    h2.session._smart_escalate("test")
    assert h2.session.state.get("_smart_pick") != first


def test_tool_definitions_count_toward_the_cache_minimum():
    from meshharness.core.optimize import prepare
    msgs = [{"role": "system", "content": "x" * 6000},   # ~1.5k tokens alone
            {"role": "user", "content": "hi"}]
    out, _, _ = prepare(msgs, "anthropic/claude-sonnet-4.5", 0.1, True)
    assert "cache_control" not in out[0]
    out, _, _ = prepare(msgs, "anthropic/claude-sonnet-4.5", 0.1, True,
                        tools_tokens=1700)
    assert out[0]["cache_control"] == {"type": "ephemeral"}


def test_a_long_turn_escalates_and_does_not_settle_back(harness):
    h = _smart(harness)
    _escalation_state(h)
    h.session._escalate_long_turn(20)
    st = h.session.state
    assert st["_smart_escalations"] == 1
    climbed = st["_smart_difficulty"]
    assert climbed != "low" and st["_smart_base_difficulty"] == climbed

    st["_smart_clean_hops"] = 10
    h.session._smart_de_escalate()
    assert st["_smart_difficulty"] == climbed


def test_escalation_skips_rungs_that_pick_the_same_model(harness, monkeypatch):
    # Live table: low, mid and high all rank one cheap model first.
    from meshharness.core import router
    monkeypatch.setattr(router, "effective_weights", lambda w, d: d)
    monkeypatch.setattr(router, "pick", lambda cohort, weights, *a, **k:
                        {"model": "strong" if weights in ("xhigh", "max") else "cheap"})
    h = _smart(harness)
    _escalation_state(h)
    h.session.state.update(_smart_pick="cheap", _smart_difficulty="mid")
    assert h.session._smart_escalate("test") is True
    assert h.session.state["_smart_pick"] == "strong"
    assert h.session.state["_smart_difficulty"] == "xhigh"
