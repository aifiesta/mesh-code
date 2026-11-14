# Cost benchmark

Checks that the token-saving defaults (prompt caching, Smart routing, the
80k-token compaction cap, 24k-char reads, monthly budget steering) keep a
$10/month plan workable without breaking tasks.

**Pass bar:** new defaults average **≤ $0.05 per turn**, and fail **at most one**
task that the old setup solved.

## Setups

| | Routing | Token savings | Model |
|---|---|---|---|
| **A — old** | Pinned | 0 | anthropic/claude-sonnet-4.5 |
| **B — new** | Smart | 0.1 | (router picks) |

Set them from ⌘, in the app. Set Monthly budget to 0 for both runs so budget
steering doesn't skew the comparison.

## Procedure

1. Copy a small real project (a few thousand lines, with tests) to a scratch folder,
   once per setup, so each run starts from the same files.
2. For each task below: start a **new conversation**, paste the prompt, let it finish.
3. Record the turn's cost (shown in the app; also the `cost` delta in
   `~/.mesh-code/usage.json`) and whether the result is correct.

## Tasks

1. Explain what the main module does and how a request flows through it.
2. Run the test suite and summarise any failures.
3. Fix one failing test (break one first with a one-line change).
4. Add a small function with a unit test (e.g. input validation helper).
5. Rename a function used in 3+ files and update all callers.
6. Find and fix a bug from a stack trace you paste in.
7. Add a CLI flag or config option end to end, including docs.
8. Refactor one long function into smaller ones without changing behaviour; run tests.

## Results

| Task | A cost | A ok | B cost | B ok | B model(s) |
|---|---|---|---|---|---|
| 1 | $0.080 | yes | $0.0011 | yes | seed-1.6-flash |
| 2 | $0.097 | yes | $0.0012 | yes | seed-1.6-flash |
| 3 | $0.121 | yes | $0.0016 | yes | seed-1.6-flash |
| 4 | $0.106 | yes | $0.0017 | yes | seed-1.6-flash |
| 5 | $0.133 | yes | $0.0023 | yes | seed-1.6-flash |
| 6 | $0.048 | yes | $0.0007 | yes | deepseek-v4-flash |
| 7 | $0.242 | yes | $0.0019 | yes | seed-1.6-flash |
| 8 | $0.154 | yes | $0.0018 | yes | deepseek-v4-flash |
| **Avg** | **$0.123** | 8/8 | **$0.0015** | 8/8 | |

Run 2026-11-14, headless through the engine, on a ~150-line Python project with
pytest. Correctness was checked by script (tests pass, hidden checks, files left
untouched where asked). Monthly budget off for both. B passes the bar.
Limit: a small, easy codebase. Large repos and harder tasks are still unmeasured.
Rerun: `cd engine && PATH=$PWD/.venv/bin:$PATH .venv/bin/python ../docs/bench/bench.py B /tmp/runB` (A for the old setup).

## Bigger repo: the Mesh Code engine (~8.6k lines, 73 tests)

| Task | A cost | A ok | B cost | B ok | B model |
|---|---|---|---|---|---|
| 1 Explain the request flow | $0.667 | yes | $0.0032 | yes | seed-1.6-flash |
| 2 Summarise failing tests | $0.286 | yes | $0.0007 | yes | seed-1.6-flash |
| 3 Fix the failing code | $0.954 | yes | $0.0049 | yes | seed-1.6-flash |
| 4 Add a setting end to end | $2.496 | yes | $0.0588 | **no** | seed-1.6-flash |
| 5 Rename a helper + callers | $0.468 | yes | $0.0052 | yes | deepseek-v4-flash |
| 6 Fix a bug from a traceback | $0.114 | yes | $0.0015 | yes | deepseek-v4-flash |
| 7 Refactor `prepare` | $0.196 | yes | $0.0037 | yes | deepseek-v4-flash |
| **Avg** | **$0.740** | 7/7 | **$0.0111** | 6/7 | |

B passes the bar (one failure). Task 4 on B ran 60 steps and 2M tokens without
moving up to a stronger model, and left the test suite hanging. Rerun with
`docs/bench/big.py A|B <clean engine copy> <outdir>`.

## Reference numbers (one small 3–4 step turn, 2026-11-14)

| Setup | Cost |
|---|---|
| Sonnet 4.5, no caching | $0.035 |
| Sonnet 4.5, caching (0.1) | $0.019 |
| Smart routing (picked bytedance-seed/seed-1.6-flash) | $0.0008 |
