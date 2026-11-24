"""The finish check: when the local model says a change is done, run the
project's own tests before believing it.

A small model can end a turn with "all tests pass" over broken code (seen
live, 23 Nov 2026: 1 in 3 coupon runs on Qwen 3.5 9B). Nothing in the reply
gives that away, so the check is the tests themselves. Only a command the
project plainly declares is used; a project with no tests found is not
checked. Running it goes through the same approval as any run_bash call.
"""
from __future__ import annotations

import json
from pathlib import Path

# The test runner said there was nothing to run, or could not start at all:
# no verdict on the change. pytest: 4 usage error, 5 no tests collected.
NO_VERDICT_CODES = {4, 5}
_NO_VERDICT_TEXT = ("No module named pytest", "no tests ran", "command not found",
                    "Missing script", "no test files", "command timed out")
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}


def _python_tests(root: Path) -> bool:
    if (root / "pytest.ini").is_file() or (root / "conftest.py").is_file():
        return True
    for name in ("pyproject.toml", "setup.cfg", "tox.ini"):
        f = root / name
        try:
            if f.is_file() and "pytest" in f.read_text(errors="replace"):
                return True
        except OSError:
            pass
    # test_*.py or *_test.py at the top or one folder down (tests/, src/…)
    for base in [root] + [d for d in root.iterdir() if d.is_dir() and d.name not in _SKIP_DIRS]:
        try:
            for f in base.iterdir():
                if f.suffix == ".py" and (f.name.startswith("test_") or f.name.endswith("_test.py")):
                    return True
        except OSError:
            continue
    return False


def find_test_command(root: Path) -> "str | None":
    """The command that runs this project's tests, or None when none is
    plainly declared. Cheap: reads a few small files, runs nothing."""
    root = Path(root)
    try:
        pkg = root / "package.json"
        if pkg.is_file():
            script = ((json.loads(pkg.read_text(errors="replace")) or {}).get("scripts") or {}).get("test")
            if isinstance(script, str) and script.strip() and "no test specified" not in script:
                return "npm test --silent"
        if (root / "Cargo.toml").is_file():
            return "cargo test --quiet"
        if (root / "go.mod").is_file():
            return "go test ./..."
        if _python_tests(root):
            return "python3 -m pytest -q -x"
    except (OSError, ValueError):
        return None
    return None


def verdict(result: str) -> "bool | None":
    """True = the tests passed, False = they failed, None = no verdict
    (nothing ran, the runner is missing, or the user said no). `result` is
    the run_bash tool's text."""
    text = result or ""
    if text.startswith("User denied") or "interrupted" in text[:200]:
        return None
    if any(t in text for t in _NO_VERDICT_TEXT):
        return None
    code = _exit_code(text)
    if code is None:
        return None
    if code in NO_VERDICT_CODES:
        return None
    return code == 0


def _exit_code(text: str) -> "int | None":
    """run_bash ends its result with "[exit N]"; see tools.execute."""
    import re
    m = re.search(r"\[exit (-?\d+)\]\s*$", text)
    return int(m.group(1)) if m else None    # no exit code: the command never ran
