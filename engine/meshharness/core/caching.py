"""Ask for prompt caching on the models that only cache when asked.

Every step of a chat sends the whole conversation again. DeepSeek and OpenAI
models bill that repeated part at their cached price by themselves. Anthropic
models bill it in full unless the request carries a mark: a `cache_control`
field on a message, which asks the provider to cache everything up to and
including that message.

Two marks go on every request to such a model:

  - the first system message. It is the same text for every chat (see
    tools.build_system_messages), so a new chat reads it from the entry an
    earlier chat wrote.
  - the last message. Each step then reads everything the step before sent
    and writes only what is new.

Measured at the gateway on 19 November 2026 (docs/bench/caching_pruning):
the same eight steps billed $0.00693 with no marks and $0.00223 with the
mark on the last message (Claude Haiku 5.5); $0.178 against $0.0571 on
Claude Opus 5.5. The mark has to be a key on the message itself: inside a
content part of a tool message the gateway ignores it without an error.

This does not depend on the "Token savings" setting, and it never changes
what the model is sent: only the two marks are added, on copies.
"""

MARK = {"type": "ephemeral"}


def needs_marks(model: str) -> bool:
    """Does this model cache only when the request asks for it?"""
    return (model or "").lower().startswith("anthropic/")


def asked_for(cfg: dict) -> bool:
    """Is caching asked for in this session? On unless `prompt_caching` is
    set to false in the config file (there is no switch for it in the app)."""
    return cfg.get("prompt_caching", True) is not False


def _has_content(message: dict) -> bool:
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    return bool(content)


def mark(messages: list) -> list:
    """The same messages with the two marks added. The list passed in, and
    every message in it, is left as it was: the chat's stored history must
    never carry a mark. Returns the list itself when there is nothing to do
    (no messages, or the caller placed marks of its own)."""
    if not messages or any("cache_control" in m for m in messages):
        return messages
    targets = set()
    if messages[0].get("role") == "system" and _has_content(messages[0]):
        targets.add(0)
    # A provider refuses a mark on an empty message, so the mark goes on the
    # last message that has something in it.
    for i in range(len(messages) - 1, -1, -1):
        if _has_content(messages[i]):
            targets.add(i)
            break
    if not targets:
        return messages
    return [{**m, "cache_control": dict(MARK)} if i in targets else m
            for i, m in enumerate(messages)]
