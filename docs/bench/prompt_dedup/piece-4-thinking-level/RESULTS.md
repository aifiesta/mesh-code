# Piece 4: does the thinking level move the bill? (19 November 2026)

**Measurement only. No product code was changed.** Run on the app window that has
the per-step bill split (commit `b0d2528`), so every figure below is the gateway's
own bill, split by part.

## What was run

- Two tasks from the level comparison, each with its hidden checks: a small fix
  (make failing tests pass, 6 checks) and the Standard task (add coupons and tests,
  10 checks).
- Model: DeepSeek V4.1 Flash 1017, the model Smart routing picks for both.
- Thinking level: low, high, and max (what Smart routing sends today), set through
  the app's Reasoning setting and put back after every run. Two runs per cell, all
  in the same window, levels interleaved. 12 runs, $0.034.
- "Off" was not run: with Reasoning off the router no longer picks DeepSeek (it picks
  MiniMax M3.1 Flash), so it would not be the same comparison.
- A third and fourth round were planned and dropped: see the first finding.

## Numbers (averages of two runs)

| Task | Thinking | Billed | Output | of which thinking | Thinking tokens | Steps | Seconds | Hidden checks |
|---|---|---|---|---|---|---|---|---|
| Small fix | low | $0.00112 | $0.00033 | $0.00008 | 178 | 5.5 | 20 | 6/6, 6/6 |
| | high | $0.00115 | $0.00036 | $0.00008 | 197 | 5.5 | 25 | 6/6, 6/6 |
| | max (today) | $0.00127 | $0.00043 | $0.00010 | 244 | 6.0 | 34 | 6/6, 6/6 |
| Standard task | low | $0.00449 | $0.00251 | $0.00095 | 2,249 | 18.5 | 132 | 10/10, 10/10 |
| | high | $0.00472 | $0.00272 | $0.00114 | 2,708 | 15.5 | 106 | 10/10, 10/10 |
| | max (today) | $0.00439 | $0.00259 | $0.00112 | 2,666 | 13.0 | 79 | 10/10, 10/10 |

## What the numbers say

1. **The thinking level is not a lever on this model.** Low, high and max produce
   about the same amount of thinking (2,249 to 2,708 tokens on the Standard task)
   and the same bill within a few percent. On the Standard task max was the
   cheapest and fastest of the three. Two runs per cell is thin, but the most a
   lower level could save is small either way: all of the thinking is about a
   quarter of a Standard bill and under a tenth of a small one.
2. **Quality is the same at every level tested:** all 12 runs pass every check.
3. **On a small task the bill is mostly the first step.** Step 1 costs $0.00051 on
   average: 44% of a small fix's bill, 12% of a Standard task's. Nothing is cached
   on step 1, so the whole prompt and tool list (about 3,400 tokens) are billed at
   the full price once per chat.

## Follow-on probe: can the first step be cached across chats?

Eight tiny requests ($0.03), two "chats" in two different project folders, a few
seconds apart, with today's prompt order and with the same text reordered so the
per-project paragraph (the working directory) comes last.

| Model | Prompt order | Chat 2, first step |
|---|---|---|
| DeepSeek | today's (working directory at character 49) | 0 tokens cached, $0.00047 |
| DeepSeek | stable text first | 1,024 of 3,357 cached, $0.00033 |
| GPT-6.1 Sol | today's | 0 cached, 2,572 written to cache, $0.0065 |
| GPT-6.1 Sol | stable text first | 0 cached, 2,572 written to cache, $0.0065 |

- **Today nothing is shared between chats:** the working directory is the second
  line of the prompt, so every project's prompt differs from the start.
- **DeepSeek caches whatever start two requests have in common.** Moving the
  per-project paragraph to the end of the prompt got the stable part of the prompt
  cached (1,024 tokens). The tool list was not, which means the provider places the
  tools after the system prompt; to share those too, the per-project text would have
  to ride in the first user message instead.
- **Sol does not share a start between different chats on its own.** It wrote the
  whole prompt to its cache again. It would need the request to mark where the
  stable part ends, which is the caching work in PR 14.
- Size on DeepSeek if the whole fixed text were cached on step 1: about $0.00046
  per new chat, which is about 40% of a small fix and 10% of a Standard task. It
  only applies when another request with the same start was made recently enough
  (the provider's cache lifetime was not measured).

## Where things are

- `low/`, `high/`, `auto/` (auto = the router's own choice, max): `projects/` as each
  run left them and `runs/` (per run: `recording.webm`, `bill-step-by-step.txt`,
  `what-the-model-changed.diff`, `correctness-check.txt`, `final-answer.md`,
  screenshots, `events.jsonl`).
- `numbers.json`: every run, printed by `scripts/analyse.py`.
- `settings-before-the-test.json`: saved settings before the test; byte-identical
  after it.
- Chats: in the Mesh Code window started for the bill split, projects named
  `low-…`, `high-…`, `auto-…`.

## Spend

$0.06 in total (12 runs $0.034, probes $0.03). Ceiling was $0.50.
