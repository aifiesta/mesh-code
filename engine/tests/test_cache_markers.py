from types import SimpleNamespace

from meshharness.core.client import build_payload

CFG = {"model": "anthropic/claude-sonnet-5.5", "base_url": "http://x", "api_key": "k"}
MSGS = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
    {"role": "tool", "tool_call_id": "c1", "content": "out"},
]


def test_cloud_requests_mark_the_system_prompt_and_the_newest_message():
    sent = build_payload(MSGS, CFG)["messages"]
    marked = [i for i, m in enumerate(sent) if "cache_control" in m]
    assert marked == [0, 3]
    assert sent[3]["cache_control"] == {"type": "ephemeral"}


def test_marking_leaves_the_session_history_untouched():
    build_payload(MSGS, CFG)
    assert not any("cache_control" in m for m in MSGS)


def test_local_requests_are_not_marked():
    target = SimpleNamespace(local=True, model="qwen", supports_reasoning_effort=False,
                             request_defaults={})
    sent = build_payload(MSGS, CFG, target=target)["messages"]
    assert not any("cache_control" in m for m in sent)
