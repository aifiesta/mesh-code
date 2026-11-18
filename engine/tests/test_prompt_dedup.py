"""A rule about one tool is said once: in that tool's definition.

The system prompt and every tool definition are re-sent on every step, so a
rule written in both places is paid for twice per step. These tests pin the
three pairs that were merged (shell, memory, servers): the prompt copy is
gone, the surviving copy still says everything the prompt copy said, and the
few sentences no tool definition carries are still in the prompt.
"""
import inspect

from meshharness.core import tools
from meshharness.session import Session


def _prompt() -> str:
    return tools.build_system_prompt({"repo_memory": False}, "/tmp")


def _desc(name: str) -> str:
    return next(t["function"]["description"] for t in tools.TOOLS
                if t["function"]["name"] == name)


# -- shell ------------------------------------------------------------------

def test_shell_rules_are_only_in_the_shell_tool():
    prompt = _prompt()
    assert "/dev/null" not in prompt
    assert "--no-input" not in prompt
    # The prompt copy said "break it into smaller pieces" while the tool
    # said "pass `timeout`": with one copy there is nothing to contradict.
    assert "shell timeout is 120s" not in prompt

    d = _desc("run_bash")
    assert "non-interactive" in d
    assert "stdin is /dev/null" in d
    assert "--yes" in d and "--no-input" in d
    assert f"{tools.BASH_TIMEOUT}s" in d and "`timeout`" in d


# -- memory -----------------------------------------------------------------

def test_memory_rules_are_only_in_the_remember_tool():
    prompt = _prompt()
    assert "remember" not in prompt
    assert "MEMORY —" not in prompt

    d = _desc("remember")
    # When to call it (this half lived only in the prompt before).
    assert "user states a lasting preference" in d
    assert "durable fact" in d
    # What a note is, and what must never go in one.
    assert "One factual sentence per call" in d
    for banned in ("secrets", "file contents", "transient task state"):
        assert banned in d


def test_repo_memory_block_still_introduces_itself():
    """The prompt paragraph also announced the REPO MEMORY section; the
    block's own header does that, so nothing is lost when notes exist."""
    from meshharness.core import memory
    block = memory.format_warm_start({}, "- uses pnpm", {})
    assert block.startswith("REPO MEMORY —")
    assert "previous" in block


# -- servers ----------------------------------------------------------------

def test_server_rules_are_in_the_tool_and_its_result():
    prompt = _prompt()
    assert "start_server" not in prompt
    assert "run_bash" not in prompt
    assert "END THE TURN" not in prompt
    assert "curl the URL" not in prompt

    d = _desc("start_server")
    assert "Do NOT use run_bash" in d
    assert "120s" in d
    assert "npm run dev" in d and "http.server" in d
    assert "return the URL" in d

    # What to do AFTER the start is said when it happens, in the result.
    result_text = inspect.getsource(Session._start_server)
    assert "Do " in result_text and "not curl it" in result_text
    assert "end your turn" in result_text
    assert "plan step completed" in result_text


def test_prompt_keeps_what_no_tool_definition_says():
    prompt = _prompt()
    assert "BEFORE starting a long-running server, bring the plan up to date" in prompt
    for workaround in ("nohup", "disown", "setsid", "timeout N npm run dev"):
        assert workaround in prompt
    # Untouched neighbours.
    assert "PLAN BEFORE ACTING" in prompt
    assert "ASK WHEN GENUINELY FORKED" in prompt
    assert "User messages may include images" in prompt


# -- size -------------------------------------------------------------------

def test_fixed_text_per_step_shrank():
    """6,988 chars of prompt before this change (cwd /tmp, no base prompt);
    the tool block grew by the one clause moved into `remember`."""
    import json
    assert len(_prompt()) < 5_750
    assert len(json.dumps(tools.TOOLS)) < 8_300
