# Piece 5: a chat's first step is now cached (19 November 2026)

**The change** (commit `f4d3a73` on `claude/prompt-dedup-46af16`, not merged, not
pushed). No prompt text was added or removed; it is sent in a different shape.

- **Before:** one system message, with the working directory on its second line. No
  two projects' prompts began alike, so the provider could never reuse one chat's
  cached opening for another chat. Step 1 of every chat was billed in full.
- **After:** two system messages. The first is identical for every chat and project
  (your base prompt and the standing rules); the provider puts the tool list right
  after it. The second holds what differs: working directory, repo memory, the
  journal, the output style.
- Chats saved before the change keep their single message.

## How it was tested

Real app, Smart routing (DeepSeek V4.1 Flash 1017 on every run), a fresh copy of the
project for every run, runs alternating between two windows: "old" on commit
`b0d2528` and "new" on `f4d3a73`. Every figure is the gateway's own bill from the
per-step split. Billed: $0.028 for the runs, about $0.03 for probe requests.

## Step 1 of each chat, in the order the runs happened

| Minute | Run | Step 1 input cached | Step 1 billed | Whole run | Hidden checks |
|---|---|---|---|---|---|
| 0.0 | old, small fix 1 | 0 of 3,397 | $0.000523 | $0.001166 | 6/6 |
| 1.0 | new, small fix 1 | 0 of 3,396 | $0.000511 | $0.001030 | 6/6 |
| 1.7 | old, small fix 2 | 0 of 3,397 | $0.000528 | $0.001192 | 6/6 |
| 2.6 | new, small fix 2 | 3,200 of 3,396 | $0.000079 | $0.000687 | 6/6 |
| 3.4 | old, small fix 3 | 0 of 3,397 | $0.000507 | $0.001128 | 6/6 |
| 4.1 | new, small fix 3 | 3,200 of 3,396 | $0.000083 | $0.000697 | 6/6 |
| 4.9 | old, small fix 4 | 0 of 3,397 | $0.000517 | $0.001041 | 6/6 |
| 6.0 | old, Standard 1 | 0 of 3,453 | $0.000531 | $0.005673 | 10/10 |
| 8.7 | new, Standard 1 | 3,200 of 3,452 | $0.000105 | $0.004398 | 10/10 |
| 10.8 | old, Standard 2 | 0 of 3,453 | $0.000534 | $0.005094 | 10/10 |
| 12.9 | new, Standard 2 | 3,200 of 3,452 | $0.000106 | $0.004216 | 10/10 |
| 19.8 | new, small fix 5 (after 5 idle minutes) | 3,200 of 3,396 | $0.000079 | $0.000902 | 6/6 |
| 35.7 | new, small fix 6 (after 15 idle minutes) | 3,200 of 3,396 | $0.000087 | $0.000597 | 6/6 |

## What the numbers say

1. **Step 1 is cached in every new-engine run after the first.** 3,200 of about
   3,400 input tokens, in a different project folder each time. Step 1 falls from
   $0.00052 to $0.00008 (84% less). The old engine cached nothing on step 1 in any
   of its 6 runs.
2. **The very first run on the new engine was not cached,** as expected: nobody had
   sent that opening yet.
3. **A small fix costs about a third less.** Old engine, 4 runs: $0.00113 on
   average. New engine with step 1 cached, 4 runs: $0.00072 (36% less).
4. **On the Standard task the gain is the same $0.0004, about 8% of the bill.** The
   two-run averages ($0.0054 old, $0.0043 new) differ by more than that, but the
   rest is the step count (18 against 15), which swings by chance.
5. **The cache outlived 15 idle minutes.** Longer gaps were not tried, and each
   gap was tried once.
6. **Quality is unchanged:** all 13 runs pass every hidden check, and every file
   was written in the right project, so the model still reads the working
   directory from the second message.

## Other models (probe requests, not app runs)

- **GPT-6.1 Sol** accepts the two messages but gains nothing yet: it does not reuse
  a cache between different chats on its own (it charged $0.0065 to write the whole
  opening again). It needs the request to mark where the stable part ends, which is
  the caching work in PR 14. The stable message is now the natural place for that
  mark.
- **Claude Haiku 5.5** accepts the two messages. Claude models cache nothing unless
  asked, so no gain there yet either.
- Models other than these three were not tried with two system messages.

## Not tested

- How long the cache lasts beyond 15 minutes.
- A chat that is compacted (covered by a unit test only).
- One new-engine run (small fix 4) was never sent: the test script timed out
  clicking the approval mode. Nothing was billed for it.

## Where things are

- `old/` and `new/`: `projects/` as each run left them and `runs/` (per run:
  `recording.webm`, `bill-step-by-step.txt`, `what-the-model-changed.diff`,
  `correctness-check.txt`, `final-answer.md`, screenshots, `events.jsonl`).
- `numbers.json`: every run, printed by `scripts/analyse.py`.
- `settings-before-the-test.json`: saved settings before the test; byte-identical
  after it.
- Chats: `old-…` projects in the window started for the bill split, `new-…` in the
  window started for this piece. All windows are still open.
