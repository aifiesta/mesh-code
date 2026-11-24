"""Catalog lookup: the echoed id is rarely the catalog id.

The cost panel exists to move a run onto a cheaper model, so a hop with no
figure defeats it. Measured against the live gateway on 3 November 2026, one
request per model: asking for "bytedance-seed/seed-1.6-flash" echoes
"seed-1-6-flash-250715" (dots turned into dashes), asking for
"deepseek/deepseek-v4-flash" echoes "deepseek-ai/DeepSeek-V4-Flash" (case
differs), and both priced as nothing while their catalog rows carried full
prompt and completion rates. "anthropic/claude-sonnet-4.5" echoed unchanged
and priced normally.

Looser matching is the easy half. The hard half is that it must not reach a
wrong row: a blank panel is what users see today, a wrong price is money
stated wrongly. So every test below that asserts a hit is paired with one
that asserts a miss.
"""
import pytest

from meshharness.core.pricing import estimate_cost, find_model

USAGE = {"prompt_tokens": 1000, "completion_tokens": 500}

SEED = {"id": "bytedance-seed/seed-1.6-flash",
        "pricing": {"prompt_usd_per_1m": "0.15", "completion_usd_per_1m": "0.60"}}
DEEPSEEK = {"id": "deepseek/deepseek-v4-flash",
            "pricing": {"prompt_usd_per_1m": "0.20", "completion_usd_per_1m": "0.60"}}
SONNET = {"id": "anthropic/claude-sonnet-4.5",
          "pricing": {"prompt_usd_per_1m": "3.00", "completion_usd_per_1m": "15.00"}}
CATALOG = [SEED, DEEPSEEK, SONNET]


def test_separator_respelling_still_prices():
    """Dots in the catalog id, dashes in the echoed one."""
    assert find_model("seed-1-6-flash-250715", CATALOG) is SEED
    cost = estimate_cost("seed-1-6-flash-250715", USAGE, CATALOG)
    assert cost == pytest.approx(1000 * 0.15 / 1e6 + 500 * 0.60 / 1e6)


def test_case_respelling_still_prices():
    """Same name, another case, and a vendor prefix of its own."""
    assert find_model("deepseek-ai/DeepSeek-V4-Flash", CATALOG) is DEEPSEEK
    cost = estimate_cost("deepseek-ai/DeepSeek-V4-Flash", USAGE, CATALOG)
    assert cost == pytest.approx(1000 * 0.20 / 1e6 + 500 * 0.60 / 1e6)


def test_id_echoed_unchanged_still_prices():
    """The case that always worked must keep working."""
    assert find_model("anthropic/claude-sonnet-4.5", CATALOG) is SONNET
    assert estimate_cost("anthropic/claude-sonnet-4.5", USAGE, CATALOG) == pytest.approx(
        1000 * 3.00 / 1e6 + 500 * 15.00 / 1e6)


def test_build_suffix_does_not_reach_a_shorter_sibling():
    """gpt-4o must not be priced at gpt-4's rate, folded or not.

    This is the mis-billing guard the looser matching had to survive. Each
    spelling below is one a gateway has echoed.
    """
    gpt4 = {"id": "openai/gpt-4",
            "pricing": {"prompt_usd_per_1m": "30", "completion_usd_per_1m": "60"}}
    for echoed in ("gpt-4o-2024-08-06", "GPT-4o-2024-08-06", "gpt_4o_2024_08_06"):
        assert find_model(echoed, [gpt4]) is None, echoed
        assert estimate_cost(echoed, USAGE, [gpt4]) is None, echoed


def test_longest_segment_wins_over_a_prefix_of_itself():
    """gpt-4o-mini's build string must price as mini, not as gpt-4o."""
    mini = {"id": "openai/gpt-4o-mini",
            "pricing": {"prompt_usd_per_1m": "0.15", "completion_usd_per_1m": "0.60"}}
    full = {"id": "openai/gpt-4o",
            "pricing": {"prompt_usd_per_1m": "2.50", "completion_usd_per_1m": "10.00"}}
    for catalog in ([full, mini], [mini, full]):
        assert find_model("gpt-4o-mini-2024-07-18", catalog) is mini
    assert estimate_cost("gpt-4o-mini-2024-07-18", USAGE, [full, mini]) == pytest.approx(
        1000 * 0.15 / 1e6 + 500 * 0.60 / 1e6)


def test_exact_id_wins_over_a_folded_match_on_another_row():
    """A row that matches exactly is never beaten by a looser rung."""
    cheap = {"id": "vendor-b/model-x",
             "pricing": {"prompt_usd_per_1m": "1", "completion_usd_per_1m": "2"}}
    dear = {"id": "vendor-a/Model-X",
            "pricing": {"prompt_usd_per_1m": "90", "completion_usd_per_1m": "180"}}
    assert find_model("vendor-b/model-x", [dear, cheap]) is cheap


def test_whole_id_wins_over_a_bare_segment_on_another_vendor():
    """Rung 2 before rung 3: the vendor prefix is evidence, so it is used."""
    own = {"id": "deepseek-ai/deepseek-v4-flash",
           "pricing": {"prompt_usd_per_1m": "0.20", "completion_usd_per_1m": "0.60"}}
    other = {"id": "reseller/DeepSeek-V4-Flash",
             "pricing": {"prompt_usd_per_1m": "9.00", "completion_usd_per_1m": "18.00"}}
    assert find_model("DeepSeek-AI/DeepSeek-V4-Flash", [other, own]) is own


def test_two_ids_folding_to_one_key_report_rather_than_collapse():
    """A folded key is not unique, so a clash answers None, not catalog order.

    "seed-1.6-flash" and "seed-1-6-flash" are different catalog ids that fold
    to the same key. Indexing on that key would keep whichever was inserted
    last and price every request off it. Both orderings are asserted so that
    "last one wins" cannot pass this test.
    """
    dotted = {"id": "vendor/seed-1.6-flash",
              "pricing": {"prompt_usd_per_1m": "0.15", "completion_usd_per_1m": "0.60"}}
    dashed = {"id": "vendor/seed-1-6-flash",
              "pricing": {"prompt_usd_per_1m": "9.00", "completion_usd_per_1m": "18.00"}}
    for catalog in ([dotted, dashed], [dashed, dotted]):
        assert find_model("seed-1-6-flash", catalog) is None
        assert find_model("seed-1-6-flash-250715", catalog) is None
        assert estimate_cost("seed-1-6-flash-250715", USAGE, catalog) is None


def test_a_clash_that_prices_the_same_is_not_a_clash():
    """Two spellings at one rate still price: the figure is not in doubt."""
    dotted = {"id": "vendor/seed-1.6-flash",
              "pricing": {"prompt_usd_per_1m": "0.15", "completion_usd_per_1m": "0.6"}}
    dashed = {"id": "vendor/seed-1-6-flash",
              "pricing": {"prompt_usd_per_1m": "0.150", "completion_usd_per_1m": "0.60"}}
    assert estimate_cost("seed-1-6-flash-250715", USAGE, [dotted, dashed]) == pytest.approx(
        1000 * 0.15 / 1e6 + 500 * 0.60 / 1e6)


def test_a_model_with_no_catalog_row_returns_none():
    """No row means no figure. A confident $0.000000 would be a lie."""
    assert find_model("mystery/model-9", CATALOG) is None
    assert estimate_cost("mystery/model-9", USAGE, CATALOG) is None
    assert estimate_cost("seed-1-6-flash-250715", USAGE, []) is None
    assert estimate_cost("seed-1-6-flash-250715", USAGE, None) is None


def test_a_row_without_token_rates_returns_none():
    """A per-image row is not a chat cost, so it prices as nothing."""
    image = {"id": "vendor/image-1", "pricing": {"image_usd_per_unit": "0.04"}}
    assert find_model("image-1", [image]) is image
    assert estimate_cost("image-1", USAGE, [image]) is None
