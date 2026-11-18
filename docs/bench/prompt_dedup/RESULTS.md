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
3. **A side effect: after the change the model plans on small tasks, and before it
   did not.** 4 of 4 "after" runs of the two edge cases made a plan; 0 of 4 "before"
   runs did. In the server case that adds 3 to 4 steps, two of which do nothing but
   plan bookkeeping (a step that only creates the plan, a step that only ticks the
   last item). Averages for the server case: 6 → 9.3 steps, 29,788 → 46,941 tokens
   (+58%), $0.001324 → $0.001609 (+22%).
   - The prompt's plan rule says to plan for anything over about 3 tool calls, so
     the "after" behaviour is what the prompt asks for; the "before" engine was
     skipping it. But on a short task it costs more than the trim saves.
   - Why it happens was not tested. My guess: with less text around it, the plan
     rule carries more weight, and the sentence that is now last in the prompt is
     about the plan.
4. **On the normal task the totals moved the other way** (18 → 14 steps, −21% tokens,
   −15% cost), but that is one run each and the gap is mostly the step count, not
   the trim. Both engines planned here.
5. **In dollars the trim alone is tiny on this model**: about 305 tokens × steps at
   the cached price. The plan side effect is larger than it in both directions.

## Not tested

- Other models (Seed 1.6 Flash, GPT-6.1 Sol, Claude). Only the model Smart routing
  picked was run.
- Cases 1 and 3 were run once per engine.
- Which of the three removals causes the extra planning.

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
  "after-…" projects in the window started for this test. Both windows are still
  open. The test servers started in case 2 are still running (ports 5173, 5174,
  5175, 5176, 8000, 8080 as picked by the runs).
