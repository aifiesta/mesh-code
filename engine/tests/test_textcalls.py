import json

import pytest

from meshharness.core import textcalls

KNOWN = {"read_file", "write_file", "run_bash", "update_step"}


def _calls(text):
    clean, calls, why = textcalls.extract(text, KNOWN)
    return clean, [(c["name"], json.loads(c["arguments"])) for c in calls], why


@pytest.mark.parametrize("text,expected", [
    ('Let me look.\n<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n</tool_call>',
     [("read_file", {"path": "a.py"})]),
    ('<tool_call>{"name":"read_file","arguments":{"path":"a"}}</tool_call>'
     '<tool_call>{"name":"run_bash","arguments":{"command":"ls"}}</tool_call>',
     [("read_file", {"path": "a"}), ("run_bash", {"command": "ls"})]),
    ('<tool_call><function=write_file><parameter=path>x.py</parameter>'
     '<parameter=content>print(1)\nprint(2)</parameter></function></tool_call>',
     [("write_file", {"path": "x.py", "content": "print(1)\nprint(2)"})]),
    ('<|START_ACTION|>[{"tool_name": "run_bash", "parameters": {"command": "pytest -q"}}]<|END_ACTION|>',
     [("run_bash", {"command": "pytest -q"})]),
    ('Running it:\n```json\n{"name": "run_bash", "arguments": {"command": "ls"}}\n```',
     [("run_bash", {"command": "ls"})]),
    ('<tool_call>{"name":"update_step","arguments":"{\\"index\\": 2, \\"status\\": \\"completed\\"}"}</tool_call>',
     [("update_step", {"index": 2, "status": "completed"})]),
])
def test_recognised_shapes_become_structured_calls(text, expected):
    clean, calls, why = _calls(text)
    assert calls == expected and why is None
    assert "<tool_call>" not in clean and "<function=" not in clean


def test_prose_that_merely_mentions_tools_is_left_alone():
    text = "You could call read_file on a.py, or run_bash to list files."
    assert not textcalls.looks_like_tool_call(text)
    assert _calls(text) == (text, [], None)


def test_unknown_tool_names_and_bad_json_are_not_executed():
    clean, calls, why = _calls('<tool_call>{"name":"delete_everything","arguments":{}}</tool_call>')
    assert calls == [] and why == "no recognisable tool call"
    clean, calls, why = _calls('<tool_call>{"name": "run_bash", "arguments": {"command": }</tool_call>')
    assert calls == [] and why == "no recognisable tool call"


@pytest.mark.parametrize("text", [
    '<|START_ACTION|>["foo"]<|END_ACTION|>',
    '<|START_ACTION|>{"tool_name": "run_bash"}<|END_ACTION|>',
    '<tool_call>{"name": ["x"], "arguments": {}}</tool_call>',
    '<tool_call>["not", "an", "object"]</tool_call>',
    '<tool_call>{"name": "run_bash", "arguments": "not json"}</tool_call>',
    '<tool_call>{"name": "run_bash", "arguments": ["ls"]}</tool_call>',
])
def test_malformed_output_never_raises(text):
    clean, calls, why = _calls(text)
    assert calls == [] and why == "no recognisable tool call" and clean == text


def test_anything_inside_a_plain_code_fence_is_quoted_material_not_a_call():
    echoed = ('Here is the file:\n```\n<tool_call>{"name":"read_file","arguments":{"path":"x"}}</tool_call>\n```')
    assert _calls(echoed) == (echoed, [], None)
    logged = 'Last call was:\n```\n{"name":"run_bash","arguments":{"command":"rm -rf build"}}\n```'
    assert _calls(logged) == (logged, [], None)
    fenced_json_only = 'see:\n```text\n{"name":"run_bash","arguments":{"command":"ls"}}\n```'
    assert _calls(fenced_json_only) == (fenced_json_only, [], None)
    mixed = ("```py\nprint('<tool_call>')\n```\n"
             '<tool_call>{"name":"run_bash","arguments":{"command":"ls"}}</tool_call>')
    clean, calls, why = _calls(mixed)
    assert calls == [("run_bash", {"command": "ls"})] and clean.startswith("```py")


def test_a_fenced_json_call_keeps_its_own_closing_fence():
    one_then_prose = ('```json\n{"name":"run_bash","arguments":{"command":"ls"}}\n```\n\nThen:\n```py\nprint(1)\n```')
    clean, calls, why = _calls(one_then_prose)
    assert calls == [("run_bash", {"command": "ls"})] and clean.endswith("```py\nprint(1)\n```")
    two = ('```json\n{"name":"read_file","arguments":{"path":"a"}}\n```\n'
           '```json\n{"name":"run_bash","arguments":{"command":"ls"}}\n```')
    assert _calls(two)[1] == [("read_file", {"path": "a"}), ("run_bash", {"command": "ls"})]
    swapped = '```json\n{"arguments":{"command":"ls"},"name":"run_bash"}\n```'
    assert _calls(swapped)[1] == [("run_bash", {"command": "ls"})]


def test_a_fence_inside_a_calls_own_arguments_survives_intact():
    content = "# T\n```py\nprint(1)\n```\n"
    text = ('Writing it.\n<tool_call>' + json.dumps({"name": "write_file", "arguments": {"path": "a.md", "content": content}})
            + '</tool_call>\nAnd later:\n```\nquoted\n```')
    clean, calls, why = _calls(text)
    assert calls == [("write_file", {"path": "a.md", "content": content})]
    assert "<tool_call>" not in clean and clean.endswith("```\nquoted\n```")
