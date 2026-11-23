# Piece 2: the engine ticks the plan (18 November 2026)

**The change** (commits `ee36340` and `671cfbf` on `claude/prompt-dedup-46af16`, not
merged, not pushed):

- The `update_step` tool is gone, so a step that only ticks the plan cannot happen.
- `read_file`, `write_file`, `edit_file`, `run_bash` and `start_server` take an
  optional `step`: the plan step the call works on. The engine takes it off the call
  and moves the checklist. Reaching step N ticks every earlier step.
- Making a plan starts step 1. A turn that ends with an answer ticks the last step if
  that was the one in progress. Steps nobody started stay unticked.
- The plan and server tools' results no longer ask for a tick.
- Plan pair of the dedup: how to plan is said once, in the plan tool. The prompt keeps
  when to plan and "do not say done while steps are undone".
- Fixed text per step: 13,866 → 13,295 characters (15,201 before any of this work).

**How it was tested.** Real app, Smart routing, approvals on Auto, a fresh copy of the
project per run, every run recorded. "Old" is the window on commit `ed6e5f6` (piece 1,
still with the tick tool). "New" is a window on `671cfbf`. Old and new runs alternate.
Billed: $0.38 (ceiling $1.00).

## Numbers

| Task, model | Engine | Runs | Steps | Tokens | Billed | Tick-only steps | Checklist at end | Hidden checks |
|---|---|---|---|---|---|---|---|---|
| Complex task, GPT-6.1 Sol | old | 1 today (+1 recorded earlier) | 23 (18) | 166,191 (140,884) | $0.1056 ($0.0893) | 8 (8) | 4/4 | 5/5 |
| | new | 2 | 11, 11 | 76,730 / 68,023 | $0.0976 / $0.0583 | 0, 0 | 4/4, 3/3 | 5/5, 5/5 |
| Standard task, DeepSeek V4.1 Flash | old | 6 | 15.2 avg (13 to 19) | 121,879 avg | $0.00508 avg | 3 in 6 runs | complete in 6 of 6 | 10/10 each |
| | new | 7 | 16.1 avg (10 to 27) | 137,905 avg | $0.00508 avg | 0 | complete in 5 of 5 that planned | 10/10 each |

## What the numbers say

1. **On GPT-6.1 Sol the change halves the work.** Averages, two runs a side: 20.5 → 11
   steps, 153,538 → 72,376 tokens (−53%), about 75 → 51 seconds, $0.0975 → $0.0779
   (−20%). The bill falls less than the tokens because Sol's bill is mostly its own
   thinking output, and it swings run to run ($0.058 to $0.106 here), so treat the
   dollar figure as rough. Sol ticked in 8 separate steps in both old runs; it has no
   way to do that now.
2. **On DeepSeek nothing changes in cost.** Same average bill to the fourth decimal.
   DeepSeek mostly sent its ticks together with real work already (3 tick-only steps
   in 6 old runs), so there was little to save. Step counts swing 10 to 27 run to run
   on the same prompt, which is why this needed 13 runs to say.
3. **Quality is unchanged.** All 16 runs in the table pass every hidden check.
4. **The checklist still works.** In every run that made a plan it ended fully ticked,
   and the journal's "What's built" filled in. Sol put `step` on every call (18/18,
   19/19). DeepSeek put it on some (6/9, 9/14, 10/13, 15/20, and once only 1/11), which
   is enough: naming a later step ticks the earlier ones. The reminder for work that
   never names a step did not fire in any run.

## What went wrong on the way (first version, `ee36340`)

- In its first Standard run, right after making a plan, DeepSeek sent an edit with
  identical old and new text and `step: 1`: a call whose only purpose was to say which
  step it was on. The engine counted it as a broken call, the router stepped up to
  GPT-6.1 Sol for three steps, and the run cost $0.0387 instead of about $0.005.
- Fixed in `671cfbf`: step 1 needs no number and the tools say so; no call should be
  made only to report progress; an edit that changes nothing and carries a step is
  answered plainly and not counted as broken. Not seen again in 9 runs on the fix.
- The first version's plan paragraph also had shorter trigger wording. The fix puts
  the original wording back so how often a model plans is not shifted.

## Not tested, and left as it is

- **Claude Opus 5.5 and other models.** One Opus run costs about $0.90. Its recorded
  run had one tick-only step (13% of that bill).
- **A model that never names a step** (checklist would stop at step 1 and the app
  shows its "plan still at 1/N" notice), **a plan revised halfway, and a plan that
  runs over several turns.** These are covered by unit tests only.
- **The step that only creates the plan** is still there (about one per planned run).
- **How often a model plans:** the new engine planned in 5 of 7 DeepSeek runs, the old
  in 6 of 6. Too few runs to call it a difference.
- **The server case** was run on the first version only (5 old, 4 new) and is not in
  the table: about 30 small test servers left running by earlier tests now take the
  usual ports, so 3 of those 9 runs had to retry the server start and fail the check
  for that reason. One new run's last step waited over six minutes at the gateway
  and finished after the script gave up; the chat is complete in the app.

## Found in passing (not changed)

- **One broken tool call is enough for the router to step up** to a model more than
  ten times the price ("repeated malformed tool calls" fires on a single all-broken
  batch). That is what turned one stray call into a $0.039 run.

## Where things are

- `old/`, `new/` (first version) and `new2/` (the fix): `projects/` as each run left
  them and `runs/` (per run: `recording.webm`, `conversation.md`, `step-by-step.txt`,
  `what-the-model-changed.diff`, `correctness-check.txt`, `final-answer.md`,
  screenshots, `events.jsonl`).
- `numbers.json`: every run, printed by `scripts/analyse.py`.
- `settings-before-the-test.json`: saved settings before the test; byte-identical
  after it (the Effort setting was set to Complex task for the Sol runs and put back).
- Chats: `old-…` projects in the window on the older engine, `new-…` and `new2-…`
  in the two windows started for this piece. All windows are still open.
