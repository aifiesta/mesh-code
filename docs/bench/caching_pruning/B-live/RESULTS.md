# Piece B: old tool results are no longer cut on every step (19 November 2026)

**The change** (commit `b92db04` on `claude/caching-pruning-9bafd2`). With the
"Token savings" setting at 0.2 or higher, the engine cut every tool result older
than the last four messages to 400 characters, on every request. That is removed.
The setting is off (0) by default, so **a chat on default settings is billed the
same as before**; this fixes what happened when someone turned the setting up.

Two things were wrong with the cut:

1. **It broke provider caching.** It changed text the provider had already cached,
   so the cache was not read on the next step.
2. **It hid files the model had not read yet.** Only the last four messages were
   kept whole. When a model read six files in one step, the first two reached it as
   400 characters. This one was found in this test, not at the gateway.

## How it was tested

The real app, the one open window, "Token savings" at 0.3 for each test chat.
"Before" chats ran while the window's engine was on the old code; the window was
then restarted on the new code for the "after" chats. Model pinned per chat, a
fresh copy of the project for every run, every bill read from the gateway, every
chat screen-recorded. Billed: **$0.064** (ceiling $0.40). Saved settings were put
back after every run and match the backup.

The main task ("explain"): six real source files of 3,500 to 7,600 characters in
`lib/`, and the prompt "Explain what each module in lib/ does ... name the function
you would rewrite first ... Do not change any file."

## Results

| Run | Before: steps | Before: bill | Before: files read again | After: steps | After: bill | After: files read again |
|---|---|---|---|---|---|---|
| Claude Haiku 5.5, explain, 1 | 12 | $0.013459 | 6 of 6 | 3 | $0.003297 | 0 |
| Claude Haiku 5.5, explain, 2 | 16 | $0.017773 | 6 of 6 | 3 | $0.002828 | 0 |
| DeepSeek V4.1 Flash, explain, 1 | 10 | $0.005912 | 6 of 6 | 3 | $0.002286 | 0 |
| DeepSeek V4.1 Flash, explain, 2 | 6 | $0.003078 | 2 of 6 | 5 | $0.001913 | 0 |
| DeepSeek V4.1 Flash, explain, 3 | | | | 4 | $0.002132 | 0 |
| Claude Haiku 5.5, coupon task | 12 | $0.007085 | 1 | 7 | $0.003607 | 0 |

Averages on the explain task: Claude Haiku 5.5 **$0.0156 to $0.0031 (-80%)**;
DeepSeek V4.1 Flash **$0.0045 to $0.0021 (-53%)**.

What happened in "Claude Haiku 5.5, explain, 1" before the change:

| Step | What the model did | Read from cache | Written to cache |
|---|---|---|---|
| 2 | asked for all six files at once | 4,712 | 550 |
| 3 | got two of them cut to 400 characters; asked for those two again | 5,262 | 8,253 |
| 4 to 9 | kept re-reading files as each earlier read was cut | 5,262 every step | 6,656 to 12,525 every step |
| 12 | answered | 4,443 | 12,760 |

After the change the same chat is three steps: list the folder, read six files,
answer. The six files are written to the cache once, on step 3.

## What the numbers say

1. **With the cut, the model re-read files in 4 of 4 explain chats** (19 and 36 file
   reads on Haiku where 6 were needed). **Without it, in 0 of 5.**
2. **Step counts do not overlap on Claude Haiku 5.5** (12 and 16 against 3 and 3).
   On DeepSeek they nearly do once (6 against 5), and two or three runs per side is
   a small sample, but the cause is exact and visible in every "before" chat: the
   repeated reads.
3. **The cache was stuck with the cut.** In the Haiku chat above, steps 3 to 9 each
   read the same 5,262 tokens and wrote 6,700 to 12,500 at the write price.
4. **Answers were right on both sides:** every answer covers all six modules and no
   file was changed; the coupon task passes 10 of 10 hidden checks both times.
5. **The coupon task has one run per side** (12 steps against 7). Its step count
   moves by chance (7 and 8 in the piece A test), so that pair alone proves little.

## What changes for the user

- **Default settings (setting at 0): nothing.** The cut never ran there.
- **Setting at 0.2 or higher: chats that read several files stop paying for
  repeated reads.**
- **The setting now does one thing at every value above 0:** it limits how long one
  reply can be (4,096 or 8,192 tokens) and adds cache marks for non-Claude models,
  which had no effect on DeepSeek at the gateway. Its description in the app says so.
- A big file read twice now gets the short "already in this conversation" reply with
  the setting on, as it always did with it off.

## Limits of this test

- "Before" and "after" ran one after the other in the same window, not alternating.
- One DeepSeek "after" chat (run 2) had a step that took over 4 minutes at the
  provider. It finished and is counted; the delay was the provider's, with steps
  before it taking 1 to 3 seconds.
- A first "before" chat with a different prompt ("which functions have no
  docstring") is in the folder but not in the table: the model answered it with a
  shell script, no big tool result appeared, and the cut had nothing to do.
- GPT-6.1 Sol was not run.

## What is in this folder

- `cut/` (before) and `nocut/` (after): for every run, the project the model worked
  in and under `runs/`: `recording.webm`, screenshots, `events.jsonl`,
  `bill-step-by-step.txt`, `files-read.txt`, `final-answer.md`,
  `what-the-model-changed.diff`, and `correctness-check.txt` for the coupon task.
- `0-projects-before/`, `numbers.json`, `prompts.json`, `scripts/`,
  `settings-before-the-test.json`.
