# Piece A: Claude models are asked for caching on every request (19 November 2026)

**The change** (commits `493f3c9` and `2ef18c5` on `claude/caching-pruning-9bafd2`).
A Claude model bills the whole conversation at full price on every step unless the
request carries a **mark**: a `cache_control` field on a message, which asks the
provider to cache everything up to and including that message. Marks were only sent
with the "Token savings" setting on, and it is off by default.

Now every request to a Claude model carries two marks, whatever that setting says:
one on the first system message (the same text in every chat) and one on the last
message. Nothing the model reads is changed, and the saved chat never carries a mark.

## How it was tested

The real app, two windows running the same code. One was started with
`MESH_CODE_PROMPT_CACHING=0` ("caching off": today's behaviour), the other normally
("caching on"). The model was pinned per chat, a fresh copy of the project was used
for every run, and runs alternated between the windows. Every figure is the
gateway's own bill, read step by step. Each run has a screen recording.

Billed: **$0.152** (ceiling $0.50). The saved settings were put back after every run
and match the backup taken before the test.

## Results, in the order the runs happened

| Run | Steps | Input billed | Whole bill | Against off | Step 1 read from cache | Hidden checks |
|---|---|---|---|---|---|---|
| Haiku 5.5, small fix 1, off | 4 | $0.002023 | $0.002424 | | 0 of 4,702 | 6/6 |
| Haiku 5.5, small fix 1, **on** | 4 | $0.000863 | $0.001272 | -48% | 0 of 4,702 | 6/6 |
| Haiku 5.5, small fix 2, off | 4 | $0.002023 | $0.002436 | | 0 of 4,702 | 6/6 |
| Haiku 5.5, small fix 2, **on** | 4 | $0.000400 | $0.000788 | -68% | 4,443 of 4,702 | 6/6 |
| Haiku 5.5, small fix 3, off | 3 | $0.001491 | $0.001915 | | 0 of 4,702 | 6/6 |
| Haiku 5.5, small fix 3, **on** | 3 | $0.000387 | $0.000949 | -50% | 4,443 of 4,702 | 6/6 |
| Haiku 5.5, coupon task, off | 7 | $0.005465 | $0.008120 | | 0 of 4,786 | 10/10 |
| Haiku 5.5, coupon task, **on** | 8 | $0.001516 | $0.003794 | -53% | 4,443 of 4,786 | 10/10 |
| Opus 5.5, small fix 1, off | 3 | $0.058075 | $0.069271 | | 0 of 4,701 | 6/6 |
| Opus 5.5, small fix 1, **on** | 3 | $0.028822 | $0.040414 | -42% | 0 of 4,701 | 6/6 |
| Opus 5.5, small fix 2, **on** | 3 | $0.009605 | $0.020747 | -70% | 4,443 of 4,701 | 6/6 |

One chat step by step (Opus 5.5, small fix 2, on):

| Step | Billed | At full price | Read from cache | Written to cache | Output |
|---|---|---|---|---|---|
| 1 | $0.003595 | 4 | 4,443 | 254 | 91 |
| 2 | $0.009736 | 2 | 4,697 | 738 | 309 |
| 3 | $0.007416 | 2 | 5,435 | 553 | 219 |

## What the numbers say

1. **It works in the app as it did at the gateway.** With caching off, no run read a
   single token from cache. With it on, every step after the first read everything
   the step before had read or written, in all 6 runs.
2. **The input bill falls by 50% to 83%.** Haiku small fix: $0.0020 to $0.0004 once
   the opening is cached. Opus small fix: $0.058 to $0.0096.
3. **The whole bill falls by 42% to 70%,** less than the input bill because the
   model's own output costs the same either way. On these short chats output is a
   fifth to a half of the bill.
4. **A new chat reads the opening from an earlier chat.** 4,443 tokens on step 1 in
   4 of 4 chats that followed another chat on the same model, each in a different
   project folder. The first chat on each model had nothing to read and paid the
   write price on step 1 (Opus: $0.0228 against $0.0186), then saved from step 2.
5. **Quality is unchanged:** all 11 runs pass every hidden check.
6. **Longer chats save more.** The 8-step run with caching on cost less than half
   the 7-step run with it off.

## Limits of this test

- One run per side on Opus with a cold start, plus one warm run. Three per side on
  Haiku for the small fix, one per side for the coupon task. The step counts match
  within each pair except the coupon task (7 against 8).
- Chats of 3 to 8 steps. The recorded $0.90 Opus task was 11 steps; no chat that long
  was run here.
- Not tried in the app: a chat left idle for more than 5 minutes, a chat whose last
  message is an image, the gateway's own model pick (`auto`, which gets no marks).

## What is in this folder

- `off/` and `on/`: for every run, the project the model worked in (`projects/`) and
  under `runs/`: `recording.webm` (the window, recorded), screenshots, `events.jsonl`
  (everything the engine told the window), `bill-step-by-step.txt`,
  `what-the-model-changed.diff`, `correctness-check.txt`, `final-answer.md`.
- `0-projects-before/`: the projects as they were before each run.
- `numbers.json`: one row per run. `scripts/`: the driver and the analysis.
- `settings-before-the-test.json`: the app's settings as they were.
