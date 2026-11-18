#!/bin/sh
# The three task-level prompts on this checkout's engine, Smart routing on, in the real app, recorded.
#   sh run_levels.sh EVIDENCE_DIR PROJECT_BEFORE_DIR
# Needs PLAYWRIGHT and ELECTRON set (see app_run.js). Makes billed calls.
E="$1"; BEFORE="$2"; HERE="$(cd "$(dirname "$0")" && pwd)"
"$HERE/../../../engine/.venv/bin/python" "$HERE/record_relay.py" 8899 "$E/what-the-app-sent-to-mesh.jsonl" >/dev/null 2>&1 &
RELAY=$!; sleep 2; export RELAY_URL=http://127.0.0.1:8899/v1
run() { mkdir -p "$E/$1"; cp -R "$BEFORE" "$E/$1/project"
        node "$HERE/app_run.js" "$E/$1" smart "Model default" "$E/$1/project" "$2" 2>&1 | tail -1; }
run 1-lookup "In two sentences, what does price.py do? Do not change any files."
run 2-small-change "The tests in tests/test_price.py are failing. Find what is wrong in price.py, fix it, and run the tests to confirm they pass."
run 3-standard-task "Refactor price.py for production use: fix the failing tests, handle edge cases such as zero or negative quantities with a clear error, add tests for them, and run the full test suite."
kill $RELAY
