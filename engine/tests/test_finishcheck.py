"""Finding a project's tests, and reading run_bash's result as pass/fail."""
import json

from meshharness.core import finishcheck as fc


def test_python_tests_are_found_by_their_files(tmp_path):
    assert fc.find_test_command(tmp_path) is None
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_price.py").write_text("def test_x(): pass\n")
    assert fc.find_test_command(tmp_path) == "python3 -m pytest -q -x"


def test_a_declared_npm_test_script_wins_but_the_placeholder_does_not(tmp_path):
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}}))
    assert fc.find_test_command(tmp_path) is None
    pkg.write_text(json.dumps({"scripts": {"test": "vitest run"}}))
    assert fc.find_test_command(tmp_path) == "npm test --silent"


def test_verdicts():
    assert fc.verdict("3 passed in 0.1s\n[exit 0]") is True
    assert fc.verdict("1 failed, 2 passed\n[exit 1]") is False
    assert fc.verdict("no tests ran in 0.01s\n[exit 5]") is None
    assert fc.verdict("/usr/bin/python3: No module named pytest\n[exit 1]") is None
    assert fc.verdict("User denied this tool call.") is None
    assert fc.verdict("Error: command timed out after 180s.") is None
    assert fc.verdict("") is None
