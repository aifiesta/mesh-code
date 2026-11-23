from meshharness.core import policy
from meshharness.core.policy import PolicyController, Tier


def _pc(**cfg):
    base = {"policy": "local-first", "model": "local/auto", "exclude_models": [], "auto_escalate": True}
    base.update(cfg)
    return PolicyController(base)


def test_bundled_policy_has_both_chains():
    doc = policy.load()
    assert doc["escalation"]["models"][0] == "anthropic/claude-sonnet-5.5"
    assert doc["audit"]["models"][0] == "deepseek/deepseek-v4.1-flash"


def test_chain_puts_the_users_pick_first_and_honours_exclusions():
    doc = policy.load()
    cfg = {"escalation_model": "openai/gpt-6.1-sol", "escalation_fallbacks": ["anthropic/claude-opus-5.5"],
           "exclude_models": ["anthropic/claude-sonnet-5.5"]}
    got = policy.chain(doc, "escalation", cfg)
    assert got[:2] == ["openai/gpt-6.1-sol", "anthropic/claude-opus-5.5"]
    assert "anthropic/claude-sonnet-5.5" not in got and len(got) == len(set(got))


def test_each_policy_shapes_the_hop_cfg():
    pc = _pc(); pc.begin_turn()
    assert pc.tier is Tier.LOCAL and pc.hop_cfg({})["model"] == "local/auto"
    pc = _pc(policy="cloud-auto", weight_profile="cost_first"); pc.begin_turn()
    cfg = pc.hop_cfg({})
    assert pc.tier is Tier.CLOUD_AUTO and cfg["model"] == "auto" and cfg["auto_route"] and cfg["weight_profile"] == "cost_first"
    pc = _pc(policy="pinned", model="anthropic/claude-sonnet-5.5"); pc.begin_turn()
    assert pc.tier is Tier.PINNED and pc.hop_cfg({})["model"] == "anthropic/claude-sonnet-5.5"


def test_escalation_runs_the_chain_with_fallbacks_then_hands_back():
    pc = _pc(); pc.begin_turn()
    assert pc.request_escalation("stalled") is True
    cfg = pc.hop_cfg({"reasoning_effort": None})
    assert pc.tier is Tier.ESCALATED and cfg["model"] == "anthropic/claude-sonnet-5.5"
    assert cfg["models"] == ["anthropic/claude-opus-5.5", "openai/gpt-6.1-sol", "google/gemini-4-argon"]
    assert cfg["reasoning_effort"] == "high"
    assert pc.request_escalation("again") is False          # once per turn, and already escalated
    for _ in range(7):
        assert pc.after_hop(1000) is None
    assert pc.after_hop(1000) == "after 8 hops" and pc.tier is Tier.LOCAL
    pc.end_turn(); pc.begin_turn()
    assert pc.tier is Tier.LOCAL


def test_implicit_escalation_respects_auto_escalate_but_explicit_does_not():
    pc = _pc(auto_escalate=False); pc.begin_turn()
    assert pc.request_escalation("stalled") is False
    assert pc.request_escalation("button", explicit=True, harder=True) is True
    assert pc.hop_cfg({})["reasoning_effort"] == "xhigh"


def test_escalate_next_turn_and_sticky_session():
    pc = _pc(escalation_sticky="session")
    assert pc.escalate_next_turn() == "anthropic/claude-sonnet-5.5"
    assert pc.describe()["escalation_pending"] is True
    assert pc.begin_turn() is Tier.ESCALATED
    pc.end_turn()
    assert pc.begin_turn() is Tier.ESCALATED, "sticky for the session"
    assert pc.deescalate() is True
    pc.end_turn()
    assert pc.begin_turn() is Tier.LOCAL


def test_other_policies_never_escalate():
    pc = _pc(policy="pinned", model="x/y"); pc.begin_turn()
    assert pc.request_escalation("stalled", explicit=True) is False
    assert pc.escalate_next_turn() is None


def test_harder_is_one_level_above_the_configured_effort_and_never_sticks():
    pc = _pc(escalation_effort="max", escalation_sticky="session")
    assert pc.effort(harder=True) == "max"
    pc = _pc(escalation_effort="medium", escalation_sticky="session")
    pc.escalate_next_turn(harder=True)
    pc.begin_turn()
    assert pc.hop_cfg({})["reasoning_effort"] == "high"
    pc.end_turn(); pc.begin_turn()
    assert pc.hop_cfg({})["reasoning_effort"] == "medium", "the next sticky turn is back to the configured effort"


def test_switching_policy_drops_a_pending_or_sticky_escalation():
    pc = _pc(escalation_sticky="session")
    pc.escalate_next_turn()
    pc.cfg["policy"] = "pinned"
    assert pc.begin_turn() is Tier.PINNED and pc.describe()["escalation_pending"] is False
    pc.cfg["policy"] = "local-first"
    pc.end_turn()
    assert pc.begin_turn() is Tier.LOCAL


def test_a_one_model_chain_sends_an_empty_models_list_not_the_users_fallbacks():
    from meshharness.core.client import build_payload
    pc = _pc(exclude_models=["anthropic/claude-opus-5.5", "openai/gpt-6.1-sol", "google/gemini-4-argon"])
    pc.begin_turn(); pc.request_escalation("x")
    cfg = pc.hop_cfg({"fallback_models": ["cheap/model"], "base_url": "http://x", "api_key": "k"})
    assert cfg["models"] == []
    assert "models" not in build_payload([{"role": "user", "content": "hi"}], cfg)
