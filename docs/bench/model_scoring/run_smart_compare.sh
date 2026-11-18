#!/bin/sh
# Smart routing, previous engine against this one, in the real app, recorded.
#   sh run_smart_compare.sh EVIDENCE_DIR PROJECT_BEFORE_DIR OLD_APP_DIR
# OLD_APP_DIR holds the earlier engine: `git archive <rev> engine desktop | tar -x -C OLD_APP_DIR`
# plus a built ui/dist. Needs PLAYWRIGHT and ELECTRON set (see app_run.js). Makes billed calls.
E="$1"; BEFORE="$2"; OLD="$3"; HERE="$(cd "$(dirname "$0")" && pwd)"
"$HERE/../../../engine/.venv/bin/python" "$HERE/record_relay.py" 8899 "$E/what-the-app-sent-to-mesh.jsonl" >/dev/null 2>&1 &
RELAY=$!; sleep 2; export RELAY_URL=http://127.0.0.1:8899/v1
run() { # name, repo ("" = this checkout), prompt
  mkdir -p "$E/$1"; cp -R "$BEFORE" "$E/$1/project"
  APP_REPO="$2" node "$HERE/app_run.js" "$E/$1" smart "Model default" "$E/$1/project" "$3" 2>&1 | tail -1
}
EASY="In two sentences, what does price.py do? Do not change any files."
FIX="The tests in tests/test_price.py are failing. Find what is wrong in price.py, fix it, and run the tests to confirm they pass."
HARD="Refactor price.py for production use: fix the failing tests, handle edge cases such as zero or negative quantities with a clear error, add tests for them, and run the full test suite."
run 1-easy-old "$OLD" "$EASY";  run 1-easy-new "" "$EASY"
run 2-fix-old  "$OLD" "$FIX";   run 2-fix-new  "" "$FIX"
run 3-hard-old "$OLD" "$HARD";  run 3-hard-new "" "$HARD"
kill $RELAY
