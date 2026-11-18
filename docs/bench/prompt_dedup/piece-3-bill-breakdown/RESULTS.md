# Piece 3: every step reports where its bill went (19 November 2026)

**The change** (commit `b0d2528` on `claude/prompt-dedup-46af16`, not merged, not
pushed). It changes nothing about what is sent to a model; it only reports.

- **Engine:** for every step, the split of tokens and dollars into fresh input,
  cached input, cache written, and output, with thinking shown as a share of output.
  Read from the gateway's own bill for the request, so the parts add up to the
  billed figure. If the gateway sends no breakdown, the split is worked out from
  catalog prices and marked as estimated.
- **Events:** `hop.usage` per step; `turn_bill` and `session_bill` on `status`;
  `bill` on `turn.finished` and `turn.aborted`. The session's split is saved with
  the chat.
- **App:** one line under each finished turn, and a table in the Tokens and Spend
  popovers.

## What the gateway reports (checked with live requests)

| Model | Fresh input | Cached input | Cache written | Output | Account discount |
|---|---|---|---|---|---|
| DeepSeek V4.1 Flash 1017 | $0.20 / 1M | $0.006 / 1M | not charged | $0.60 / 1M | 30% |
| GPT-6.1 Sol | $2.00 | $0.10 | $2.50 | $10.00 | none |
| Claude Haiku 5.5 | $0.10 | none without asking | none | $0.50 | 10% |

- DeepSeek and Sol cache by themselves: the second request with the same start was
  billed 99% as cached input.
- Sol charges 125% once for everything it has not seen before (the cache write).
- The Claude model cached nothing on the second identical request: it bills in full
  unless the request asks for caching.
- Thinking tokens are reported separately from other output on all three.

## Live run in the app

Standard task (add coupons, write tests), DeepSeek V4.1 Flash 1017 at max thinking,
12 steps, 10 of 10 hidden checks. Billed $0.004532.

| Part | Tokens | Share of tokens | Billed | Share of the bill |
|---|---|---|---|---|
| Fresh input | 9,903 | 11% | $0.001387 | 31% |
| Cached input | 72,832 | 81% | $0.000306 | 7% |
| Output | 6,759 | 8% | $0.002839 | 63% |
| of which thinking | 3,157 | | $0.001326 | 29% |

- **The split adds up:** tokens in the split 89,494, reported 89,494; dollars in the
  split $0.004532, billed $0.004532; the 12 steps add up to the same.
- **Re-sent history is 81% of the tokens and 7% of the money.** That is why trimming
  the prompt and removing plan ticks moved tokens but not the DeepSeek bill.
- **Output is 8% of the tokens and 63% of the money,** and almost half of that is
  thinking. This is one run, so the exact shares will move; the shape will not.
- **Step 1 alone is 12% of the bill** ($0.000545): nothing is cached yet, so the
  whole prompt and tool list are billed in full once per chat.

Per step: `new/runs/new-standard-run1/bill-step-by-step.txt`. Screenshots of the app:
`3-split-under-the-turn.png`, `4-tokens-popover.png`, `5-spend-popover.png` in the
same folder, with the recording and the event log.

## Not tested

- A live run on a model whose bill has no breakdown (the estimated path is covered
  by unit tests only).
- A chat restored after an app restart (unit test only).
- Chats made before this change show totals without a split.

## Spend

About $0.02: eight tiny probe requests ($0.016) and the one run ($0.0045). Ceiling
was $0.05. Saved settings are byte-identical to the backup.
