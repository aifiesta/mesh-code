"""Two cheap checks on a no-tool reply: a refusal, or work claimed without a
tool call. They name a weak answer; the policy ladder decides what to do."""
import re

_REFUSAL_RE = re.compile(
    r"\b(?:i (?:don'?t|do not) have (?:the )?(?:tools?|access|ability|capability)"
    r"|i'?m (?:sorry|afraid)[, ].{0,40}\b(?:can'?t|cannot|unable)"
    r"|i (?:can'?t|cannot) (?:help with|assist with|do) that"
    r"|as an ai(?: language)? model[, ]"
    r"|i (?:am|'m) (?:unable|not able) to)\b", re.I)

# "I created/wrote/updated the file" — a claim that MUST be backed by a tool
# call. Said without one, the model is hallucinating work it never did.
_CLAIM_RE = re.compile(
    r"\b(?:i(?:'?ve| have)?\s+(?:created|wrote|written|added|updated|saved|"
    r"generated|implemented|installed|ran|executed)"
    r"|(?:file|script|module|directory|folder)\s+(?:has been|was)\s+"
    r"(?:created|written|saved|updated))\b", re.I)

_ACTION_ASK_RE = re.compile(
    r"\b(?:create|write|make|build|add|implement|run|execute|install|fix|"
    r"update|delete|rename|refactor|generate)\b", re.I)


def verify_reply(reply: str, *, tool_calls_this_turn: int,
                 user_asked_action: bool) -> "str | None":
    """Return a short reason when the reply looks like a routing failure.

    Two clear-cut signals only:
      refusal    — the model declined or claimed it lacks tools it was given
      hallucination — it claims to have DONE work while calling no tool
    Anything else returns None (accept the answer).
    """
    text = (reply or "").strip()
    if not text:
        return None                      # empty is handled by the retry ladder
    if _REFUSAL_RE.search(text):
        return "the model refused or claimed it lacks tools"
    if (user_asked_action and tool_calls_this_turn == 0
            and _CLAIM_RE.search(text)):
        return "the model claimed work it never performed"
    return None


def asks_for_action(text: str) -> bool:
    """True when the user's message requests real work (not a question)."""
    return bool(_ACTION_ASK_RE.search(text or ""))
