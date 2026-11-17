"""Token-saving file tools: line-range reads, generated-file notes, targeted
edits and trimmed shell output.

The pure behaviour is tested straight against core/tools.py; anything that
touches approvals, the read dedupe or the conversation runs the real engine
against the fake gateway, like test_session.py.
"""
import json
import shutil
import subprocess

import pytest
from fake_gateway import sse_turn

from meshharness.core import tools
from meshharness.core.permissions import Mode
from meshharness.protocol import Decision, Event


def _numbered(n: int) -> str:
    return "".join(f"line {i}\n" for i in range(1, n + 1))


def _tool_results(gateway) -> list:
    """Tool results as the model was last sent them."""
    return [str(m.get("content")) for m in gateway.requests[-1]["messages"]
            if m.get("role") == "tool"]


def _call(name: str, call_id: str = "c1", **args) -> list:
    return sse_turn(tool_calls=[{"id": call_id, "name": name,
                                 "arguments": json.dumps(args)}])


# ---------------------------------------------------------------------------
# read_file: line ranges
# ---------------------------------------------------------------------------

def test_small_file_without_a_range_is_returned_whole_and_unchanged(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    detail = tools.read_file_detail({"path": "a.py"}, cwd=tmp_path)
    assert detail["text"] == "x = 1\ny = 2\n"
    assert detail["whole"] is True and detail["shown"] is True
    # execute() is the same path.
    assert tools.execute("read_file", {"path": "a.py"}, cwd=tmp_path) == "x = 1\ny = 2\n"


def test_ranged_read_numbers_lines_and_says_how_to_continue(tmp_path):
    (tmp_path / "big.txt").write_text(_numbered(843))
    out = tools.execute("read_file", {"path": "big.txt", "offset": 120, "limit": 3},
                        cwd=tmp_path)
    assert out.splitlines() == [
        "120\tline 120",
        "121\tline 121",
        "122\tline 122",
        "[lines 120-122 of 843. For more, call again with offset=123, "
        "or any other range.]",
    ]


def test_ranged_read_is_never_reported_as_the_whole_file(tmp_path):
    (tmp_path / "f.txt").write_text(_numbered(10))
    detail = tools.read_file_detail({"path": "f.txt", "offset": 1, "limit": 10},
                                    cwd=tmp_path)
    assert detail["whole"] is False and detail["shown"] is True
    assert detail["text"].endswith("[lines 1-10 of 10 — end of file.]")


def test_ranged_read_to_the_end_of_the_file(tmp_path):
    (tmp_path / "f.txt").write_text(_numbered(10))
    out = tools.execute("read_file", {"path": "f.txt", "offset": 9}, cwd=tmp_path)
    assert out == "9\tline 9\n10\tline 10\n[lines 9-10 of 10 — end of file.]"


def test_ranged_read_past_the_end_says_how_long_the_file_is(tmp_path):
    (tmp_path / "f.txt").write_text(_numbered(10))
    out = tools.execute("read_file", {"path": "f.txt", "offset": 50, "limit": 5},
                        cwd=tmp_path)
    assert "has 10 lines" in out and "offset 50 is past the end" in out
    assert not out.startswith("Error:")


def test_offset_and_limit_are_read_leniently(tmp_path):
    """"120", 120.0 and an offset of 0 must not cost the model a round trip."""
    (tmp_path / "f.txt").write_text(_numbered(10))
    out = tools.execute("read_file", {"path": "f.txt", "offset": "2", "limit": 1.0},
                        cwd=tmp_path)
    assert out.startswith("2\tline 2\n[lines 2-2 of 10.")
    out = tools.execute("read_file", {"path": "f.txt", "offset": 0, "limit": 1},
                        cwd=tmp_path)
    assert out.startswith("1\tline 1\n")


def test_a_non_numeric_range_is_rejected_before_it_runs():
    err = tools.validate_call("read_file", {"path": "f", "offset": "top"})
    assert err and "`offset`" in err
    assert tools.validate_call("read_file", {"path": "f", "limit": True})
    assert tools.validate_call("read_file", {"path": "f", "offset": 3, "limit": 4}) is None


def test_oversized_file_returns_a_first_chunk_cut_at_a_line_boundary(
        tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "READ_LIMIT", 200)
    (tmp_path / "big.txt").write_text(_numbered(100))
    detail = tools.read_file_detail({"path": "big.txt"}, cwd=tmp_path)
    lines = detail["text"].splitlines()
    assert detail["whole"] is False
    assert lines[0] == "1\tline 1"
    # Every content line is complete — never half a line.
    last_shown = int(lines[-2].split("\t")[0])
    assert lines[-2] == f"{last_shown}\tline {last_shown}"
    assert lines[-1] == (f"[lines 1-{last_shown} of 100. For more, call again "
                         f"with offset={last_shown + 1}, or any other range.]")
    assert len(detail["text"]) < 400


def test_reading_can_continue_from_the_footer_until_the_end(tmp_path, monkeypatch):
    """The user's requirement: the model is never stuck with the first range."""
    monkeypatch.setattr(tools, "READ_LIMIT", 300)
    (tmp_path / "big.txt").write_text(_numbered(120))
    seen, offset, calls = [], None, 0
    while True:
        args = {"path": "big.txt"} if offset is None else {"path": "big.txt",
                                                           "offset": offset}
        out = tools.execute("read_file", args, cwd=tmp_path)
        calls += 1
        *body, footer = out.splitlines()
        seen += [row.split("\t", 1)[1] for row in body]
        if "end of file" in footer:
            break
        offset = int(footer.split("offset=")[1].split(",")[0])
        assert calls < 50
    assert seen == [f"line {i}" for i in range(1, 121)]


def test_one_enormous_line_is_cut_to_the_budget_and_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "READ_LIMIT", 100)
    (tmp_path / "blob.txt").write_text("a" * 5000 + "\nsecond\n")
    out = tools.execute("read_file", {"path": "blob.txt", "offset": 1, "limit": 1},
                        cwd=tmp_path)
    assert len(out) < 400
    assert "Line 1 is 5000 chars; only its first 100 are shown." in out
    assert "offset=2" in out


def test_empty_file_and_missing_file(tmp_path):
    (tmp_path / "empty.txt").write_text("")
    assert tools.execute("read_file", {"path": "empty.txt"}, cwd=tmp_path) == ""
    assert "is empty" in tools.execute(
        "read_file", {"path": "empty.txt", "offset": 1}, cwd=tmp_path)
    assert tools.execute("read_file", {"path": "nope.txt"}, cwd=tmp_path).startswith("Error:")


def test_read_summary_shows_the_range():
    assert tools.summarize_call("read_file", {"path": "a.py"}) == "read_file: a.py"
    assert tools.summarize_call(
        "read_file", {"path": "a.py", "offset": 120, "limit": 80}
    ) == "read_file: a.py (lines 120-199)"
    assert tools.summarize_call(
        "read_file", {"path": "a.py", "offset": 120}) == "read_file: a.py (from line 120)"


# ---------------------------------------------------------------------------
# read_file: a long file read without a range returns a map, not the file
# ---------------------------------------------------------------------------

def _long_module(functions: int = 60) -> str:
    out = ["import os", "", ""]
    for i in range(1, functions + 1):
        out += [f"def handler_{i}(request):", f"    value = request.get('k{i}')",
                "    if value is None:", "        return None",
                f"    return value * {i}", "", ""]
    return "\n".join(out) + "\n"


def test_long_file_without_a_range_returns_a_map_of_its_definitions(
        tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "WHOLE_READ_LIMIT", 2_000)
    src = _long_module()
    (tmp_path / "views.py").write_text(src)
    detail = tools.read_file_detail({"path": "views.py"}, cwd=tmp_path)
    text = detail["text"]
    assert detail["whole"] is False and detail["shown"] is False
    assert text.startswith("[views.py is long — ")
    assert f"{len(src)} chars" in text and "not returned whole" in text
    # Each definition is listed with the line it really starts on.
    assert "4: def handler_1\n" in text
    line_of_30 = src[:src.index("def handler_30(")].count("\n") + 1
    assert f"{line_of_30}: def handler_30\n" in text
    assert "offset and limit" in text and "offset=1" in text
    assert len(text) < len(src) / 3


def test_the_map_is_capped_and_says_how_many_definitions_it_left_out(
        tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "WHOLE_READ_LIMIT", 2_000)
    monkeypatch.setattr(tools, "LONG_FILE_MAP_CHARS", 300)
    (tmp_path / "views.py").write_text(_long_module(80))
    text = tools.execute("read_file", {"path": "views.py"}, cwd=tmp_path)
    assert "more definitions" in text
    listed = [row for row in text.splitlines() if ": def handler_" in row]
    left_out = int(text.split("… and ")[1].split(" more")[0])
    assert 0 < len(listed) < 80 and len(listed) + left_out == 80


def test_the_whole_long_file_is_one_ranged_call_away(tmp_path, monkeypatch):
    """The map must never be a dead end: offset=1 returns all of it."""
    monkeypatch.setattr(tools, "WHOLE_READ_LIMIT", 2_000)
    src = _long_module()
    (tmp_path / "views.py").write_text(src)
    out = tools.execute("read_file", {"path": "views.py", "offset": 1}, cwd=tmp_path)
    *body, footer = out.splitlines()
    assert [row.split("\t", 1)[1] for row in body] == src.splitlines()
    assert footer.endswith("— end of file.]")


def test_long_file_in_a_language_without_patterns_shows_its_first_lines(
        tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "WHOLE_READ_LIMIT", 2_000)
    (tmp_path / "notes.txt").write_text(_numbered(900))
    text = tools.execute("read_file", {"path": "notes.txt"}, cwd=tmp_path)
    assert "Its first 40 lines:]" in text
    assert "\n1\tline 1\n" in text and "\n40\tline 40\n" in text
    assert "41\tline 41" not in text


def test_a_file_at_the_whole_read_limit_still_comes_back_whole(tmp_path, monkeypatch):
    src = _long_module(10)
    monkeypatch.setattr(tools, "WHOLE_READ_LIMIT", len(src))
    (tmp_path / "views.py").write_text(src)
    detail = tools.read_file_detail({"path": "views.py"}, cwd=tmp_path)
    assert detail["text"] == src and detail["whole"] is True


def test_a_file_map_is_not_recorded_as_the_file(gateway, harness, tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "WHOLE_READ_LIMIT", 2_000)
    (tmp_path / "views.py").write_text(_long_module())
    gateway.script(
        _call("read_file", "c1", path="views.py"),
        _call("read_file", "c2", path="views.py"),
        sse_turn("done"))
    h = harness(mode=Mode.BYPASS, optimize=0.0)
    h.session.submit("read views.py")
    h.wait_for(Event.TURN_FINISHED)

    first, second = _tool_results(gateway)
    assert first == second and first.startswith("[views.py is long")
    assert h.session.state["session_reads"] == {}


# ---------------------------------------------------------------------------
# read_file: lockfiles, minified and generated files
# ---------------------------------------------------------------------------

def test_whole_read_of_a_lockfile_returns_a_one_line_note(tmp_path):
    (tmp_path / "package-lock.json").write_text('{"a": 1}\n' * 2000)
    detail = tools.read_file_detail({"path": "package-lock.json"}, cwd=tmp_path)
    assert detail["whole"] is False and detail["shown"] is False
    assert "\n" not in detail["text"]
    assert "lockfile" in detail["text"] and "2000 lines" in detail["text"]
    assert "offset and limit" in detail["text"]
    assert not detail["text"].startswith("Error:")


def test_a_ranged_read_of_a_lockfile_always_goes_through(tmp_path):
    (tmp_path / "yarn.lock").write_text(_numbered(2000))
    out = tools.execute("read_file", {"path": "yarn.lock", "offset": 5, "limit": 2},
                        cwd=tmp_path)
    assert out.startswith("5\tline 5\n6\tline 6\n")


def test_a_tiny_lockfile_is_just_returned(tmp_path):
    (tmp_path / "go.sum").write_text("example.com/x v1.0.0 h1:abc=\n")
    assert tools.execute("read_file", {"path": "go.sum"}, cwd=tmp_path).startswith(
        "example.com/x")


@pytest.mark.parametrize("rel, expect", [
    ("static/app.min.js", "minified"),
    ("static/app.js.map", "minified"),
    ("node_modules/left-pad/index.js", "node_modules"),
    ("web/.next/server/page.js", ".next"),
    ("pkg/__pycache__/mod.cpython-312.pyc.txt", "__pycache__"),
])
def test_generated_paths_are_not_read_whole(tmp_path, rel, expect):
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    target.write_text(_numbered(400))
    out = tools.execute("read_file", {"path": rel}, cwd=tmp_path)
    assert out.startswith(f"[{target.name} not shown:") and expect in out


def test_very_long_lines_mark_a_file_as_generated(tmp_path):
    (tmp_path / "data.js").write_text("var a=" + "x" * 6000 + ";\n")
    out = tools.execute("read_file", {"path": "data.js"}, cwd=tmp_path)
    assert "very long lines" in out


def test_ordinary_source_is_never_mistaken_for_generated(tmp_path):
    src = tmp_path / "src" / "build" / "tool.py"
    src.parent.mkdir(parents=True)
    src.write_text(_numbered(400))
    # `build/` holds real source here and nothing says git ignores it.
    assert tools.execute("read_file", {"path": "src/build/tool.py"},
                         cwd=tmp_path) == _numbered(400)


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_build_folders_are_skipped_only_when_git_ignores_them(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text("dist/\n")
    for folder in ("dist", "vendor"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "bundle.js").write_text(_numbered(400))
    ignored = tools.execute("read_file", {"path": "dist/bundle.js"}, cwd=tmp_path)
    assert "which git ignores" in ignored
    kept = tools.execute("read_file", {"path": "vendor/bundle.js"}, cwd=tmp_path)
    assert kept == _numbered(400)


def test_a_workspace_that_itself_lives_in_a_build_folder_is_not_hidden(tmp_path):
    """Only folders INSIDE the project count — a project checked out under
    ~/build/ must stay readable."""
    root = tmp_path / "build" / "node_modules_free" / "proj"
    root.mkdir(parents=True)
    (root / "main.py").write_text(_numbered(400))
    assert tools.execute("read_file", {"path": "main.py"}, cwd=root) == _numbered(400)


# ---------------------------------------------------------------------------
# read_file through the engine: what gets recorded as "in the conversation"
# ---------------------------------------------------------------------------

def test_a_ranged_read_is_not_recorded_as_the_full_file(gateway, harness, tmp_path):
    (tmp_path / "big.py").write_text(_numbered(500))
    gateway.script(
        _call("read_file", "c1", path="big.py", offset=10, limit=5),
        _call("read_file", "c2", path="big.py"),
        sse_turn("done"))
    h = harness(mode=Mode.BYPASS, optimize=0.0)
    h.session.submit("look at big.py")
    h.wait_for(Event.TURN_FINISHED)

    first, second = _tool_results(gateway)
    assert first.startswith("10\tline 10\n") and "of 500" in first
    # The whole-file read after it must return the file, not "already in
    # your context" — the range never put the whole file there.
    assert second == _numbered(500)
    assert not any(e["data"].get("deduped") for e in h.of(Event.TOOL_RESULT))


def test_a_whole_read_is_still_deduped_but_a_range_after_it_is_answered(
        gateway, harness, tmp_path):
    (tmp_path / "mod.py").write_text(_numbered(200))
    gateway.script(
        _call("read_file", "c1", path="mod.py"),
        _call("read_file", "c2", path="mod.py"),
        _call("read_file", "c3", path="mod.py", offset=50, limit=2),
        sse_turn("done"))
    h = harness(mode=Mode.BYPASS, optimize=0.0)
    h.session.submit("read it")
    h.wait_for(Event.TURN_FINISHED)

    whole, repeat, ranged = _tool_results(gateway)
    assert whole == _numbered(200)
    assert "unchanged since you last read it" in repeat
    assert ranged.startswith("50\tline 50\n51\tline 51\n[lines 50-51 of 200.")


def test_a_generated_file_note_is_not_recorded_as_the_file(gateway, harness, tmp_path):
    (tmp_path / "package-lock.json").write_text('{"a": 1}\n' * 2000)
    gateway.script(
        _call("read_file", "c1", path="package-lock.json"),
        _call("read_file", "c2", path="package-lock.json"),
        sse_turn("done"))
    h = harness(mode=Mode.BYPASS, optimize=0.0)
    h.session.submit("check the lockfile")
    h.wait_for(Event.TURN_FINISHED)

    first, second = _tool_results(gateway)
    assert first == second and "not shown" in first
    assert h.session.state["session_reads"] == {}


def test_compaction_summary_marks_a_partial_read():
    from meshharness.core import compact
    tc = {"id": "c1", "function": {"name": "read_file", "arguments":
                                   json.dumps({"path": "a.py", "offset": 5})}}
    assert compact._describe_action(tc, {}) == "read part of a.py"
    tc["function"]["arguments"] = json.dumps({"path": "a.py"})
    assert compact._describe_action(tc, {}) == "read a.py"
