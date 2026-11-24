import json

from meshharness.core import loopguard


def test_the_local_runners_too_long_error_is_recognised():
    body = json.dumps({"error": {"code": 400, "type": "exceed_context_size_error",
                                 "message": "the request exceeds the available context size, try increasing it",
                                 "n_prompt_tokens": 17000, "n_ctx": 16384}})
    assert loopguard.parse_local_context_overflow(body) == {"prompt_tokens": 17000, "context_limit": 16384}


def test_older_runner_wording_and_plain_text_are_recognised_without_numbers():
    got = loopguard.parse_local_context_overflow("input is larger than the max context size. skipping")
    assert got == {"prompt_tokens": None, "context_limit": None}


def test_other_errors_are_not_mistaken_for_a_full_window():
    assert loopguard.parse_local_context_overflow('{"error": {"message": "bad request"}}') is None
    assert loopguard.parse_local_context_overflow("") is None
    assert loopguard.parse_local_context_overflow(
        "input length and `max_tokens` exceed context limit: 190000 + 32000 > 200000") is None
