"""Token-saving file tools: line-range reads, generated-file notes, targeted
edits and trimmed shell output.

The pure behaviour is tested straight against core/tools.py; anything that
touches approvals, the read dedupe or the conversation runs the real engine
against the fake gateway, like test_session.py.
"""
import json
import shutil
import subprocess
import time

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
    h = harness(mode=Mode.BYPASS)
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
    h = harness(mode=Mode.BYPASS)
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
    h = harness(mode=Mode.BYPASS)
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
    h = harness(mode=Mode.BYPASS)
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


# ---------------------------------------------------------------------------
# read approvals: reads inside the project in accept-edits and auto modes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", [Mode.ACCEPT_EDITS, Mode.AUTO])
def test_reads_inside_the_project_do_not_prompt(gateway, harness, tmp_path, mode):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')\n")
    gateway.script(_call("read_file", path="src/app.py"), sse_turn("done"))
    h = harness(mode=mode)
    h.session.submit("read it")
    h.wait_for(Event.TURN_FINISHED)

    assert not h.of(Event.TOOL_PROPOSED), "a project read should not prompt"
    assert h.of(Event.TOOL_DECIDED)[0]["data"]["auto"] is True
    assert _tool_results(gateway) == ["print('hi')\n"]


def test_default_mode_still_asks_before_every_read(gateway, harness, tmp_path):
    (tmp_path / "app.py").write_text("print('hi')\n")
    gateway.script(_call("read_file", path="app.py"), sse_turn("done"))
    h = harness(mode=Mode.DEFAULT)
    h.session.submit("read it")
    proposal = h.wait_for(Event.TOOL_PROPOSED)
    assert proposal["data"]["name"] == "read_file"
    h.session.respond_approval(proposal["data"]["token"], Decision.DENY)
    h.wait_for(Event.TURN_FINISHED)
    assert _tool_results(gateway) == ["User denied this tool call."]


@pytest.mark.parametrize("mode", [Mode.ACCEPT_EDITS, Mode.AUTO])
def test_reads_outside_the_project_still_ask(gateway, harness, tmp_path, mode):
    outside = tmp_path.parent / f"outside-{mode.value}.txt"
    outside.write_text("not yours\n")
    gateway.script(_call("read_file", path=str(outside)), sse_turn("ok"))
    h = harness(mode=mode)
    h.session.submit("read outside")
    proposal = h.wait_for(Event.TOOL_PROPOSED)
    assert "outside the current project directory" in proposal["data"]["blocked_reason"]
    h.session.respond_approval(proposal["data"]["token"], Decision.DENY)
    h.wait_for(Event.TURN_FINISHED)
    assert "not yours" not in "".join(_tool_results(gateway))


@pytest.mark.parametrize("name", [".env", ".env.local", "prod.env", ".envrc"])
def test_environment_files_still_ask_in_auto_modes(gateway, harness, tmp_path, name):
    (tmp_path / name).write_text("API_KEY=sk-live-secret\n")
    gateway.script(_call("read_file", path=name), sse_turn("ok"))
    h = harness(mode=Mode.AUTO)
    h.session.submit("read env")
    proposal = h.wait_for(Event.TOOL_PROPOSED)
    assert "environment files" in proposal["data"]["blocked_reason"]
    h.session.respond_approval(proposal["data"]["token"], Decision.DENY)
    h.wait_for(Event.TURN_FINISHED)
    assert "sk-live-secret" not in json.dumps(gateway.requests[-1]["messages"])


def test_env_file_detection_spares_templates_and_code():
    from meshharness.core import safety
    for name in (".env", ".ENV", ".env.production", "staging.env", ".envrc"):
        assert safety.is_env_file(name), name
    for name in (".env.example", ".env.sample", ".env.template", "env.py",
                 "environment.ts", "vite-env.d.ts", "README.md"):
        assert not safety.is_env_file(name), name


def test_always_allow_for_reads_keeps_its_wider_reach(gateway, harness, tmp_path):
    """The user's own "Always allow read file" click is not narrowed to the
    project by this change — only the mode's silent approval is."""
    outside = tmp_path.parent / "lib-source.py"
    outside.write_text("library code\n")
    (tmp_path / "a.py").write_text("a\n")
    gateway.script(
        _call("read_file", "c1", path="a.py"),
        _call("read_file", "c2", path=str(outside)),
        sse_turn("done"))
    h = harness(mode=Mode.DEFAULT)
    h.session.submit("read two files")
    first = h.wait_for(Event.TOOL_PROPOSED)
    h.session.respond_approval(first["data"]["token"], Decision.ALWAYS)
    h.wait_for(Event.TURN_FINISHED)
    assert len(h.of(Event.TOOL_PROPOSED)) == 1
    assert _tool_results(gateway) == ["a\n", "library code\n"]


def test_bypass_reads_are_unchanged_and_credentials_still_ask():
    from meshharness.core import safety
    ok, _ = safety.is_path_safe_for_auto_read("/usr/share/dict/words", Mode.BYPASS)
    assert ok, "bypass keeps reading outside the project without asking"
    ok, why = safety.is_path_safe_for_auto_read(
        "~/.ssh/id_rsa", Mode.AUTO, workspace_only=True)
    assert not ok and "denylist" in why


# ---------------------------------------------------------------------------
# edit_file: exact-text replacement
# ---------------------------------------------------------------------------

SAMPLE = (
    "def area(w, h):\n"
    "    return w * h\n"
    "\n"
    "def perimeter(w, h):\n"
    "    total = 2 * (w + h)\n"
    "    return total\n"
    "\n"
    "def label(w, h):\n"
    "    return f\"{w}x{h}\"\n"
)


def _edit(tmp_path, **args):
    return tools.execute("edit_file", {"path": "shapes.py", **args}, cwd=tmp_path)


def test_edit_replaces_exactly_the_matched_text(tmp_path):
    f = tmp_path / "shapes.py"
    f.write_text(SAMPLE)
    out = _edit(tmp_path, old_text="    total = 2 * (w + h)\n    return total",
                new_text="    return 2 * (w + h)")
    assert out.startswith("OK — replaced 1 occurrence in ")
    assert out.endswith("(now lines 5-5)")
    assert f.read_text() == SAMPLE.replace(
        "    total = 2 * (w + h)\n    return total", "    return 2 * (w + h)")


def test_edit_can_delete_text(tmp_path):
    f = tmp_path / "shapes.py"
    f.write_text(SAMPLE)
    assert _edit(tmp_path, old_text="    total = 2 * (w + h)\n", new_text="").startswith("OK")
    assert "total = 2" not in f.read_text()


def test_ambiguous_edit_changes_nothing_and_lists_the_lines(tmp_path):
    f = tmp_path / "shapes.py"
    f.write_text(SAMPLE)
    out = _edit(tmp_path, old_text="(w, h):", new_text="(width, height):")
    assert out.startswith("Error: old_text matches 3 places in shapes.py (lines 1, 4, 8).")
    assert "replace_all" in out and "neighbouring line" in out
    assert f.read_text() == SAMPLE


def test_replace_all_changes_every_match(tmp_path):
    f = tmp_path / "shapes.py"
    f.write_text(SAMPLE)
    out = _edit(tmp_path, old_text="(w, h):", new_text="(w, h=1):", replace_all=True)
    assert out.startswith("OK — replaced 3 occurrences in ") and "first at line 1" in out
    assert f.read_text().count("(w, h=1):") == 3


def test_edit_never_creates_a_file(tmp_path):
    out = _edit(tmp_path, old_text="a", new_text="b")
    assert out.startswith("Error: shapes.py does not exist.")
    assert "write it whole" in out
    assert not (tmp_path / "shapes.py").exists()


@pytest.mark.parametrize("args, expect", [
    ({"old_text": "x", "new_text": "y"}, "`path`"),
    ({"path": "f", "new_text": "y"}, "non-empty `old_text`"),
    ({"path": "f", "old_text": "", "new_text": "y"}, "non-empty `old_text`"),
    ({"path": "f", "old_text": "x"}, "`new_text`"),
    ({"path": "f", "old_text": "x", "new_text": "x"}, "identical"),
])
def test_edit_arguments_are_validated(args, expect):
    err = tools.validate_call("edit_file", args)
    assert err and err.startswith("Error:") and expect in err


def test_edit_keeps_windows_line_endings(tmp_path):
    f = tmp_path / "shapes.py"
    f.write_bytes(SAMPLE.replace("\n", "\r\n").encode())
    # The model sends plain \n, as it saw the text.
    out = _edit(tmp_path, old_text="    total = 2 * (w + h)\n    return total",
                new_text="    return 2 * (w + h)\n    # done")
    assert out.startswith("OK")
    raw = f.read_bytes().decode()
    assert "    return 2 * (w + h)\r\n    # done\r\n" in raw
    assert "\n" not in raw.replace("\r\n", ""), "no bare \\n may be introduced"


def test_edit_leaves_unix_line_endings_alone(tmp_path):
    f = tmp_path / "shapes.py"
    f.write_text(SAMPLE)
    _edit(tmp_path, old_text="return w * h", new_text="return h * w")
    assert b"\r" not in f.read_bytes()


# ---- when the text is not there: the message has to be enough to recover ----

def test_miss_with_wrong_indentation_points_at_the_lines(tmp_path):
    f = tmp_path / "shapes.py"
    f.write_text(SAMPLE)
    out = _edit(tmp_path, old_text="total = 2 * (w + h)\nreturn total", new_text="x")
    assert out.startswith("Error: old_text was not found in shapes.py: the "
                          "indentation or spacing differs")
    assert "Lines 5-6 hold the same text" in out
    assert "Read lines 5-6" in out
    # Not shown to the model before, so the file's text is not quoted.
    assert "    total" not in out
    assert f.read_text() == SAMPLE


def test_miss_with_wrong_indentation_quotes_the_file_when_allowed(tmp_path):
    (tmp_path / "shapes.py").write_text(SAMPLE)
    plan = tools.plan_edit(tmp_path / "shapes.py",
                           "total = 2 * (w + h)\nreturn total", "x",
                           quote=True, shown="shapes.py")
    assert plan["error"].endswith(
        "copy it character for character:\n    total = 2 * (w + h)\n    return total")


def test_miss_caused_by_copied_line_numbers_says_so(tmp_path):
    (tmp_path / "shapes.py").write_text(SAMPLE)
    out = _edit(tmp_path, old_text="5\t    total = 2 * (w + h)\n6\t    return total",
                new_text="    return 2 * (w + h)")
    assert "line-number prefixes" in out and "without them" in out


def test_miss_where_only_the_first_line_matches(tmp_path):
    (tmp_path / "shapes.py").write_text(SAMPLE)
    out = _edit(tmp_path, old_text="def perimeter(w, h):\n    return 2 * w + 2 * h",
                new_text="x")
    assert "Its first line is at line 4, but the lines after it are not what you sent." in out
    assert "Read lines 4-5" in out


def test_miss_with_nothing_recognisable(tmp_path):
    (tmp_path / "shapes.py").write_text(SAMPLE)
    out = _edit(tmp_path, old_text="def volume(w, h, d):", new_text="x")
    assert "not even its first line is in the file" in out and "grep -n" in out


def test_a_near_match_is_reported_never_applied(tmp_path):
    f = tmp_path / "shapes.py"
    f.write_text(SAMPLE)
    for old in ("return w*h", "RETURN w * h", "return w * h ;"):
        assert _edit(tmp_path, old_text=old, new_text="return 0").startswith("Error:")
    assert f.read_text() == SAMPLE


def test_edit_summary_and_schema_hint():
    assert tools.summarize_call("edit_file", {
        "path": "a.py", "old_text": "a\nb\nc", "new_text": "a\nb"}) == "edit_file: a.py (3 → 2 lines)"
    assert tools.summarize_call("edit_file", {
        "path": "a.py", "old_text": "a", "new_text": "", "replace_all": True,
    }) == "edit_file: a.py (1 → 0 lines, every match)"
    assert tools.summarize_call("edit_file", {}) == "edit_file: (missing path)"
    hint = tools.schema_hint("edit_file")
    assert '"old_text": "..."' in hint and "optional: replace_all" in hint


def test_compaction_summary_names_an_edit():
    from meshharness.core import compact
    tc = {"id": "c1", "function": {"name": "edit_file", "arguments": json.dumps(
        {"path": "a.py", "old_text": "x", "new_text": "y"})}}
    assert compact._describe_action(tc, {}) == "edited a.py"


def test_system_prompt_stays_tool_name_free_and_drops_whole_file_rewrites():
    prompt = tools.build_system_prompt({"repo_memory": False}, "/tmp")
    for name in ("read_file", "write_file", "edit_file"):
        assert name not in prompt, f"{name} must not be named in the prompt"
    assert "always write the whole file" not in prompt
    assert "replace only the lines that need to change" in prompt
    # Still forbids placeholder code.
    assert "Never leave placeholder comments" in prompt
    assert "rest of the code remains the same" in prompt


# ---------------------------------------------------------------------------
# edit_file through the engine
# ---------------------------------------------------------------------------

def test_edit_asks_with_a_diff_of_the_changed_lines(gateway, harness, tmp_path):
    f = tmp_path / "shapes.py"
    f.write_text("# header\n" * 20 + SAMPLE)
    gateway.script(
        _call("edit_file", path="shapes.py",
              old_text="    total = 2 * (w + h)\n    return total",
              new_text="    return 2 * (w + h)"),
        sse_turn("Simplified it."))
    h = harness(mode=Mode.DEFAULT)
    h.session.submit("simplify perimeter")

    proposal = h.wait_for(Event.TOOL_PROPOSED)
    assert proposal["data"]["name"] == "edit_file"
    preview = proposal["data"]["preview"]
    assert preview["kind"] == "diff" and preview["exists"] is True
    # A window around the change, numbered as in the file — not 30 lines of file.
    assert preview["start_line"] == 22
    assert preview["old"].splitlines()[3:5] == ["    total = 2 * (w + h)", "    return total"]
    assert "    return 2 * (w + h)" in preview["new"].splitlines()
    assert "# header" not in preview["old"]
    assert f.read_text().count("total") == 2, "must not edit before approval"

    h.session.respond_approval(proposal["data"]["token"], Decision.ALLOW)
    h.wait_for(Event.TURN_FINISHED)
    assert "total" not in f.read_text()
    changed = h.of(Event.FILE_CHANGED)[0]["data"]
    assert changed["path"].endswith("shapes.py") and changed["action"] == "edit"
    assert _tool_results(gateway)[0].startswith("OK — replaced 1 occurrence")


def test_a_doomed_edit_is_answered_without_asking_the_user(gateway, harness, tmp_path):
    (tmp_path / "shapes.py").write_text(SAMPLE)
    gateway.script(
        _call("edit_file", path="shapes.py", old_text="def volume():", new_text="x"),
        sse_turn("I'll look again."))
    h = harness(mode=Mode.DEFAULT)
    h.session.submit("edit")
    h.wait_for(Event.TURN_FINISHED)

    assert not h.of(Event.TOOL_PROPOSED), "nothing to approve: the edit cannot apply"
    failed = [e for e in h.of(Event.TOOL_RESULT) if e["data"]["name"] == "edit_file"]
    assert failed and failed[0]["data"]["ok"] is False
    assert _tool_results(gateway)[0].startswith("Error: old_text was not found in shapes.py")
    assert (tmp_path / "shapes.py").read_text() == SAMPLE


@pytest.mark.parametrize("mode", [Mode.ACCEPT_EDITS, Mode.AUTO, Mode.BYPASS])
def test_edit_is_auto_approved_wherever_a_write_is(gateway, harness, tmp_path, mode):
    f = tmp_path / "shapes.py"
    f.write_text(SAMPLE)
    gateway.script(
        _call("edit_file", path="shapes.py", old_text="return w * h", new_text="return h * w"),
        sse_turn("done"))
    h = harness(mode=mode)
    h.session.submit("edit")
    h.wait_for(Event.TURN_FINISHED)
    assert not h.of(Event.TOOL_PROPOSED)
    assert "return h * w" in f.read_text()


def test_edit_outside_the_project_asks_like_a_write_does(gateway, harness, tmp_path):
    outside = tmp_path.parent / "outside-edit.py"
    outside.write_text("x = 1\n")
    gateway.script(
        _call("edit_file", path=str(outside), old_text="x = 1", new_text="x = 2"),
        sse_turn("ok"))
    h = harness(mode=Mode.ACCEPT_EDITS)
    h.session.submit("edit outside")
    proposal = h.wait_for(Event.TOOL_PROPOSED)
    assert "outside the current project directory" in proposal["data"]["blocked_reason"]
    h.session.respond_approval(proposal["data"]["token"], Decision.DENY)
    h.wait_for(Event.TURN_FINISHED)
    assert outside.read_text() == "x = 1\n"


def test_a_missed_edit_outside_the_project_reveals_nothing_before_approval(
        gateway, harness, tmp_path):
    """No pre-check there: even a match count is information about a file
    the user has not agreed to open."""
    outside = tmp_path.parent / "outside-secret.py"
    outside.write_text("token = 'abc'\n")
    gateway.script(
        _call("edit_file", path=str(outside), old_text="nope", new_text="x"),
        sse_turn("ok"))
    h = harness(mode=Mode.DEFAULT)
    h.session.submit("probe")
    proposal = h.wait_for(Event.TOOL_PROPOSED)
    assert proposal["data"]["preview"]["old"] == "nope"
    h.session.respond_approval(proposal["data"]["token"], Decision.DENY)
    h.wait_for(Event.TURN_FINISHED)
    assert _tool_results(gateway) == ["User denied this tool call."]


def test_a_failed_edit_quotes_the_file_only_after_it_was_read(gateway, harness, tmp_path):
    (tmp_path / "shapes.py").write_text(SAMPLE)
    wrong = {"path": "shapes.py", "old_text": "total = 2 * (w + h)\nreturn total",
             "new_text": "return 2 * (w + h)"}
    gateway.script(
        _call("edit_file", "c1", **wrong),
        _call("read_file", "c2", path="shapes.py", offset=4, limit=3),
        _call("edit_file", "c3", **wrong),
        sse_turn("done"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("edit")
    h.wait_for(Event.TURN_FINISHED)

    before, _read, after = _tool_results(gateway)
    assert "Read lines 5-6" in before and "    total = 2" not in before
    assert "copy it character for character:\n    total = 2 * (w + h)\n    return total" in after
    # Second miss in a row on the same file: told to stop guessing.
    assert "2 failed edits of this file in a row" in after
    assert "2 failed edits" not in before


def test_after_an_edit_the_file_is_not_claimed_to_be_in_context(gateway, harness, tmp_path):
    f = tmp_path / "mod.py"
    f.write_text(_numbered(200))
    gateway.script(
        _call("read_file", "c1", path="mod.py"),
        _call("edit_file", "c2", path="mod.py", old_text="line 50\n", new_text="line fifty\n"),
        _call("read_file", "c3", path="mod.py"),
        sse_turn("done"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("edit then re-read")
    h.wait_for(Event.TURN_FINISHED)

    _first, edit, reread = _tool_results(gateway)
    assert edit.startswith("OK")
    # The re-read returns the NEW content in full, not "already in your context".
    assert reread == f.read_text() and "line fifty" in reread


def test_edit_drops_the_dedupe_entry_even_when_bytes_are_restored(
        gateway, harness, tmp_path):
    """Edit away and back: the bytes match the recorded read again, but the
    record must be gone — the entry is dropped on every successful edit."""
    (tmp_path / "mod.py").write_text(_numbered(200))
    gateway.script(
        _call("read_file", "c1", path="mod.py"),
        _call("edit_file", "c2", path="mod.py", old_text="line 50\n", new_text="line fifty\n"),
        _call("edit_file", "c3", path="mod.py", old_text="line fifty\n", new_text="line 50\n"),
        sse_turn("done"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("edit twice")
    h.wait_for(Event.TURN_FINISHED)
    assert h.session.state["session_reads"] == {}


def test_placeholder_guard_covers_what_an_edit_writes(gateway, harness, tmp_path):
    (tmp_path / "game.js").write_text("function start() {\n  run();\n}\n")
    gateway.script(
        _call("edit_file", path="game.js", old_text="  run();",
              new_text="  // TODO: add game logic here"),
        sse_turn("done"), sse_turn("ok"), sse_turn("ok"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("make the game")
    h.wait_for(Event.TURN_FINISHED)
    notices = [e["data"] for e in h.of(Event.NOTICE)
               if "scaffolding" in e["data"].get("message", "")]
    assert notices and notices[0]["detail"][0].startswith("line 2:")


def test_placeholder_guard_ignores_todos_the_edit_did_not_write(gateway, harness, tmp_path):
    (tmp_path / "game.js").write_text(
        "// TODO: tidy this file one day\nfunction start() {\n  run();\n}\n")
    gateway.script(
        _call("edit_file", path="game.js", old_text="  run();", new_text="  runGame();"),
        sse_turn("done"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("rename the call")
    h.wait_for(Event.TURN_FINISHED)
    assert h.session.state["stub_files"] == {}
    assert not [e for e in h.of(Event.NOTICE)
                if "scaffolding" in e["data"].get("message", "")]


def test_truncated_edit_arguments_take_the_malformed_call_path(gateway, harness, tmp_path):
    (tmp_path / "shapes.py").write_text(SAMPLE)
    gateway.script(
        sse_turn(tool_calls=[{"id": "c1", "name": "edit_file", "arguments":
                              '{"path": "shapes.py", "old_text": "return w * h", "new_text": "retu'}]),
        sse_turn("Trying again."))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("edit")
    skipped = h.wait_for(Event.TOOL_SKIPPED)
    assert skipped["data"]["name"] == "edit_file"
    assert skipped["data"]["kind"] in ("truncated", "unparseable")
    h.wait_for(Event.TURN_FINISHED)
    feedback = _tool_results(gateway)[0]
    assert "cut off" in feedback and "edit_file expects" in feedback
    assert (tmp_path / "shapes.py").read_text() == SAMPLE


def test_edit_missing_a_required_argument_is_skipped_with_the_schema(gateway, harness, tmp_path):
    (tmp_path / "shapes.py").write_text(SAMPLE)
    gateway.script(
        _call("edit_file", path="shapes.py", new_text="x"),
        sse_turn("ok"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("edit")
    h.wait_for(Event.TURN_FINISHED)
    feedback = _tool_results(gateway)[0]
    assert "non-empty `old_text`" in feedback and "edit_file expects" in feedback
    assert h.of(Event.TOOL_SKIPPED)[0]["data"]["kind"] == "invalid"


def test_repeating_the_same_failed_edit_trips_the_stall_detector(gateway, harness, tmp_path):
    """A model stuck re-sending one failing edit is nudged after 3 identical
    hops and stopped by the 9th — the existing loop guard covers the new tool."""
    (tmp_path / "shapes.py").write_text(SAMPLE)
    miss = _call("edit_file", path="shapes.py", old_text="def volume():", new_text="x")
    gateway.script(*([miss] * 14), sse_turn("giving up"))
    h = harness(mode=Mode.BYPASS)
    h.session.submit("edit")
    end = time.time() + 40
    while time.time() < end and not (h.of(Event.TURN_FINISHED) or h.of(Event.TURN_ABORTED)):
        time.sleep(0.05)
    assert h.of(Event.TURN_FINISHED) or h.of(Event.TURN_ABORTED)

    edits = [e for e in h.of(Event.TOOL_RESULT) if e["data"]["name"] == "edit_file"]
    assert 3 <= len(edits) <= 9, "the loop guard must stop it before the script runs out"
    sent = json.dumps([r["messages"] for r in gateway.requests])
    assert "repeating the same action" in sent, "the model must be told it is looping"
    assert (tmp_path / "shapes.py").read_text() == SAMPLE


# ---------------------------------------------------------------------------
# run_bash: long output keeps its failures and its end
# ---------------------------------------------------------------------------

@pytest.fixture
def log_dir(tmp_path_factory, monkeypatch):
    folder = tmp_path_factory.mktemp("logs") / "mesh-code-logs"
    monkeypatch.setattr(tools, "shell_log_dir", lambda: folder)
    return folder


def _noisy(passing: int = 400) -> str:
    rows = [f"tests/test_bulk.py::test_case_{i} PASSED" for i in range(1, passing + 1)]
    rows[249] = "tests/test_bulk.py::test_case_250 FAILED"
    rows += ["", "=== FAILURES ===", "___ test_case_250 ___",
             "    assert compute(250) == 1", "E   AssertionError: assert 7 == 1",
             "=== 1 failed, 399 passed in 2.31s ==="]
    return "\n".join(rows) + "\n"


def test_short_output_is_returned_as_before(tmp_path, log_dir):
    out = tools.execute("run_bash", {"command": "printf 'one\\ntwo\\n'"}, cwd=tmp_path)
    assert out == "one\ntwo\n\n[exit 0]"
    assert not log_dir.exists(), "nothing is saved for output that fits"


def test_long_output_keeps_head_failures_tail_and_exit_code(tmp_path, log_dir):
    (tmp_path / "noisy.txt").write_text(_noisy())
    out = tools.execute("run_bash", {"command": "cat noisy.txt; exit 3"}, cwd=tmp_path)

    assert len(out) <= tools.OUTPUT_LIMIT + 20
    assert out.startswith("[output trimmed: 406 lines, ")
    assert "test_case_1 PASSED" in out                       # head
    assert "250: tests/test_bulk.py::test_case_250 FAILED" in out   # failure, with its line number
    assert "E   AssertionError: assert 7 == 1" in out        # tail: the reason
    assert "=== 1 failed, 399 passed in 2.31s ===" in out    # tail: the summary
    assert out.endswith("\n[exit 3]")
    # The old cut kept only the first 8,000 chars: none of the above after the head.
    assert "test_case_250 FAILED" not in _noisy()[:tools.OUTPUT_LIMIT]


def test_the_full_output_is_saved_and_can_be_read_by_range(tmp_path, log_dir):
    (tmp_path / "noisy.txt").write_text(_noisy())
    out = tools.execute("run_bash", {"command": "cat noisy.txt"}, cwd=tmp_path)
    logs = list(log_dir.iterdir())
    assert len(logs) == 1 and str(logs[0]) in out
    assert logs[0].read_text() == _noisy()
    assert (logs[0].stat().st_mode & 0o777) == 0o600
    assert (log_dir.stat().st_mode & 0o777) == 0o700

    part = tools.execute("read_file", {"path": str(logs[0]), "offset": 249, "limit": 3},
                         cwd=tmp_path)
    assert part.splitlines()[1] == "250\ttests/test_bulk.py::test_case_250 FAILED"


def test_old_logs_are_pruned(tmp_path, log_dir):
    import os
    log_dir.mkdir(mode=0o700)
    stale, fresh = log_dir / "cmd-old.log", log_dir / "cmd-recent.log"
    stale.write_text("old")
    fresh.write_text("recent")
    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(stale, (two_days_ago, two_days_ago))
    (tmp_path / "noisy.txt").write_text(_noisy())
    tools.execute("run_bash", {"command": "cat noisy.txt"}, cwd=tmp_path)
    assert not stale.exists() and fresh.exists()


def test_output_is_still_returned_when_the_log_cannot_be_saved(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setattr(tools, "shell_log_dir", lambda: blocker)
    (tmp_path / "noisy.txt").write_text(_noisy())
    out = tools.execute("run_bash", {"command": "cat noisy.txt"}, cwd=tmp_path)
    assert "could not be saved" in out
    assert "=== 1 failed, 399 passed in 2.31s ===" in out and out.endswith("[exit 0]")


def test_a_symlinked_log_folder_is_refused(tmp_path, monkeypatch):
    real = tmp_path / "elsewhere"
    real.mkdir()
    link = tmp_path / "logs-link"
    link.symlink_to(real)
    monkeypatch.setattr(tools, "shell_log_dir", lambda: link)
    assert tools._save_log("secret output") is None
    assert list(real.iterdir()) == []


def test_colour_codes_and_progress_redraws_are_dropped():
    raw = "\x1b[32mPASSED\x1b[0m ok\r\n10%\r50%\r100% done\n\x1b]0;title\x07end\n"
    assert tools.clean_output(raw) == "PASSED ok\n100% done\nend\n"


def test_one_enormous_output_line_still_fits_the_limit(tmp_path, log_dir):
    out = tools.execute("run_bash", {"command": "python3 -c \"print('x' * 50000)\""},
                        cwd=tmp_path)
    assert len(out) < tools.OUTPUT_LIMIT and out.endswith("[exit 0]")
    assert "output trimmed: 1 lines, 50001 chars" in out


def test_compaction_still_reads_the_exit_code_of_trimmed_output(tmp_path, log_dir):
    from meshharness.core import compact
    (tmp_path / "noisy.txt").write_text(_noisy())
    out = tools.execute("run_bash", {"command": "cat noisy.txt; exit 2"}, cwd=tmp_path)
    tc = {"id": "c1", "function": {"name": "run_bash", "arguments":
                                   json.dumps({"command": "cat noisy.txt; exit 2"})}}
    assert compact._describe_action(tc, {"c1": out}).endswith("→ exit 2")


def test_the_saved_log_reads_without_a_prompt_in_auto_mode(
        gateway, harness, tmp_path, log_dir):
    (tmp_path / "noisy.txt").write_text(_noisy())
    gateway.script(_call("run_bash", "c1", command="cat noisy.txt"), sse_turn("ok"))
    h = harness(mode=Mode.AUTO)
    h.session.submit("run it")
    h.wait_for(Event.TURN_FINISHED)
    log = next(log_dir.iterdir())

    gateway.script(_call("read_file", "c2", path=str(log), offset=250, limit=1),
                   sse_turn("done"))
    h.session.submit("read the log")
    h.wait_for(Event.TURN_FINISHED, min_count=2)
    assert not h.of(Event.TOOL_PROPOSED), "the engine's own log must not prompt"
    assert _tool_results(gateway)[-1].startswith(
        "250\ttests/test_bulk.py::test_case_250 FAILED")
