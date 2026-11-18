# Prompt dedup, piece 1: shell, memory and server rules said once (18 November 2026)

**The change** (commit `ed6e5f6` on `claude/prompt-dedup-46af16`, not merged): three
paragraphs of the system prompt repeated what a tool definition already says. They
were removed or cut down, and the one clause the tool lacked moved into the tool.

| Rule | Before | After |
|---|---|---|
| Shell (no questions, 120 s limit) | prompt paragraph + `run_bash` definition (the two disagreed on slow commands) | `run_bash` definition only |
| Memory (when to save a note) | prompt paragraph + `remember` definition | `remember` definition only, with "when to call it" added |
| Servers | prompt paragraph + `start_server` definition + the tool's result | definition + result; the prompt keeps two sentences no tool has (update the plan first, no `nohup`/`timeout` workarounds) |

Fixed text sent on every step: 15,201 → 13,866 characters.

**How it was tested.** Two Mesh Code windows, same settings, Smart routing on Auto,
approvals on Auto. "Before" runs engine commit `1cb78fe`, "after" runs `ed6e5f6`.
Every run picked DeepSeek V4.1 Flash 1017 at max thinking. Each run is a fresh copy
of the project, driven in the real app and recorded. Billed for this test: $0.016
(ceiling was $0.60).

## Numbers

| Case | Engine | Steps | Tokens | Billed | Made a plan | Hidden checks |
|---|---|---|---|---|---|---|
| 1 Normal: add coupons, write tests | before | 18 | 142,479 | $0.005602 | yes | 10/10 |
| | after | 14 | 112,119 | $0.004765 | yes | 10/10 |
| 2 Edge: add a page, start a server | before, 3 runs | 6, 6, 6 | 29,380 / 30,076 / 29,907 | $0.001262 / $0.001356 / $0.001353 | no, no, no | 10/10 each |
| | after, 3 runs | 9, 10, 9 | 44,834 / 50,329 / 45,661 | $0.001603 / $0.001574 / $0.001649 | yes, yes, yes | 10/10 each |
| 3 Edge: a command that asks a question, a 130 s command, a lasting preference | before | 6 | 29,251 | $0.001158 | no | 7/7 |
| | after | 6 | 28,340 | $0.001221 | yes | 7/7 |

The "before" run of case 1 was not paid for again: it is the Standard task recorded
earlier the same day on the same engine (see
`before/runs/before-1-normal/WHERE-THIS-RUN-CAME-FROM.txt`).

## What the numbers say

1. **The saving itself is about 305 tokens per step, and it is exact.** Step 1 of a
   run is the fixed text plus the user's prompt. Case 2: 3,892 / 3,894 / 3,891 tokens
   before, 3,588 / 3,582 / 3,586 after. Case 3: 3,909 → 3,593.
2. **Output quality did not change.** Every run passed every hidden check. Every
   trimmed rule was still followed by the cheap model in every run:
   - server started with the server tool, never through the shell; no call after
     the start (no curl, no re-reading); the address is in the final answer;
   - the reset command had `--yes` on the first try; the 130 s command was given
     a longer limit on the first try (400 s before, 300 s after); nothing timed out;
   - the lasting preference was saved once, as one sentence.
3. **CORRECTED. The first six runs looked like a side effect: the changed engine made
   a plan on small tasks and the old one did not (4 of 4 against 0 of 4). That was
   chance, not the change.** See "Diagnosis" below: over 26 runs of the server case
   the model planned about half the time whichever prompt it got.
4. **On the normal task the totals moved the other way** (18 → 14 steps, −21% tokens,
   −15% cost), but that is one run each and the gap is the step count, not the trim.
5. **In dollars the trim alone is tiny on this model**: about 305 tokens × steps at
   the cached price. Whether the model happens to plan moves the bill far more than
   the trim does.

## Diagnosis: does the trim make the model plan? No.

A third window was started whose engine takes its prompt from files, so the same
window could run the old prompt, the new prompt, and the old prompt with only one of
the three removals. The old and new variants were checked to be byte-identical to
what the two real engines send. All runs are the server case (case 2).

| Prompt | Runs | Made a plan | Average steps | Average tokens | Average billed |
|---|---|---|---|---|---|
| Old (first window 4 runs, variant window 6) | 10 | 4 | 7.0 | 35,663 | $0.001493 |
| New (second window 4 runs, variant window 6) | 10 | 5 | 8.1 | 39,942 | $0.001484 |
| Old with only the memory paragraph removed | 2 | 1 | | | |
| Old with only the shell paragraph removed | 2 | 1 | | | |
| Old with only the servers paragraph cut | 2 | 1 | | | |

- **Cause: chance.** The model makes a plan for this task about half the time on any
  of the prompts (12 of 26). The old prompt planned in 4 of 6 runs in the variant
  window. The first three pairs happened to fall 0 of 3 against 3 of 3.
- **Nothing in the trim needs fixing**, so the engine change is unchanged (`ed6e5f6`).
- **What a plan costs on a small task, on either prompt:** 6.2 → 9.4 steps,
  30,258 → 48,642 tokens, $0.00139 → $0.00161. Two of the added steps do nothing but
  plan bookkeeping (one that only creates the plan, one that only ticks the last
  item), which the prompt already tells the model not to do. This existed before the
  change and belongs to the plan rule, not to this piece.
- **Not explained:** the first window never planned (0 of 4) although the same prompt
  planned 4 of 6 times in the variant window. Not chased.
- **Rules still followed:** all 26 runs started the server with the server tool. One
  run ran a file check (`grep`) after the server had started: the old prompt with the
  servers paragraph cut, 1 of its 2 runs. The 10 runs on the new prompt and the 10
  on the old prompt ran nothing after the start.
- **The test script hung once.** In the last pair (run 4 in the first two windows)
  the script stopped answering; one chat waited about 12 minutes on its server
  dialog until it was approved by hand. Those two runs' times are meaningless; their
  steps, tokens and cost are real. A fifth pair was not run.
- Billed for the diagnosis: $0.030. With the first test: $0.046 in total.

Every run: `diagnosis/server-case-all-runs.json`, printed by `scripts/plan_table.py`.
Variant prompts: `diagnosis/variants/`. Chats: the third test window, projects named
`old-…`, `new-…`, `only-…`.

## Not tested

- Other models (Seed 1.6 Flash, GPT-6.1 Sol, Claude). Only the model Smart routing
  picked was run.
- Cases 1 and 3 were run once per engine.

## Where things are

- `before/` and `after/`: `projects/` (each project as the run left it) and `runs/`
  (per run: `recording.webm`, `conversation.md`, `step-by-step.txt`,
  `what-the-model-changed.diff`, `correctness-check.txt`, `final-answer.md`,
  screenshots, `events.jsonl`).
- `0-projects-before/`: the three projects as they were before any run.
- `checks/`: the checks the models never saw. `scripts/`: how the app was driven.
- `numbers.json`: the table above, per run.
- `settings-before-the-test.json`: saved settings before the test; they are
  byte-identical after it.
- The chats are in Mesh Code: "before-…" projects in the window on the older engine,
  "after-…" projects in the window started for this test, the diagnosis projects in
  a third one. All are still open.
- Every server-case run left its small test server running (about 27 of them, each a
  `python3 -m http.server` serving one test project). They stop when stopped in the
  app or when their window is closed.
