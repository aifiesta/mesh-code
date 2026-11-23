# Probe: caching and pruning, measured at the gateway (19 November 2026)

No product code was changed. One fixed conversation (the real system prompt and
tool list, then made-up tool rounds that read the engine's own files) was replayed
step by step through the engine's real request path (`stream_chat`), and the
gateway's own bill was read for every request. Replies were capped at 16 or 64
tokens, so these bills are almost all input. `scripts/probe.py`, records in `raw/`.

Billed in total: **$0.30** (ceiling $1). The probe stopped early: the API key has a
$5 spend cap and it was reached ($5.013 across all sessions), so the gateway now
answers every request with `spend_limit_exceeded`. What that cut off is listed at
the end.

Words used here:
- **mark**: the `cache_control` field on a message, which asks the provider to
  cache everything up to and including that message.
- **dial**: the app's "Token savings" setting. 0 = off (today's default). 0.1 = marks
  on the first system message and the second-to-last message. 0.2 and above = the
  same marks, and every tool result older than the last 4 messages is cut to 400
  characters on every request.

## A. Claude models

Claude Haiku 5.5, 8 steps, the same 76.4k input tokens in every variant that cuts
nothing. Two runs each; the two runs agree to the last digit shown.

| What is sent | Read from cache | Billed | Against today |
|---|---|---|---|
| Today: no marks | 0 | $0.00693 | |
| Dial 0.1: marks on system + second-to-last | 44.6k | $0.00361 | -48% |
| **Mark on the last message** | 62.1k | **$0.00223** | **-68%** |
| Marks on last system message + last message (1 run) | 62.1k | $0.00223 | -68% |
| Dial 0.3: marks + cutting every step | **0** | $0.00597 | -14% |

Claude Opus 5.5, 6 steps, mark on the last message (1 run): billed **$0.0571**.
The same 49,005 input tokens at Opus's full price are $0.178, so **-68%**. Opus
cached from step 1 (4,651 tokens), so its minimum size is not in the way.

Other facts:
- **Where the mark goes matters.** With it on the second-to-last message the newest
  message is billed in full on one step and written to the cache on the next.
- **The mark must be on the message itself.** The same mark inside a content part of
  a tool message is ignored without an error: 0 read from cache in 7 steps.
- **A second mark on the system prompt adds nothing inside a chat.**
- **It does help across chats.** With a mark on the first system message, a new chat
  in a different project read 4,443 of 4,644 tokens from cache on its first step:
  $0.00053 -> $0.00007 on Haiku (3 chats, 2 of 2 follow-on chats hit). Not run on Opus;
  by price it would be about $0.021 -> $0.003 per new chat started while the cache
  is alive.
- **The one-hour lifetime is accepted** (`"ttl": "1h"`); a write then costs 1.6 times
  the normal write.
- **Lifetime:** an entry was still read at 200 s and at 240 s. The 330 s, 600 s and
  "does a read restart the clock" checks were cut off by the spend cap.
- The account pays 90% of list price on Claude models (Opus: write $4.50, not $5).

## B. Cutting old tool results every step (dial 0.2 and above)

| Model | Not cut | Cut every step (dial 0.3) | What happened |
|---|---|---|---|
| Claude Haiku 5.5 (marks on, 2 runs each) | $0.00361 | $0.00597 (+65%) | Nothing was read from cache in 16 of 16 steps. Every step paid the write price for an entry no later step could read. |
| DeepSeek V4.1 Flash 1017 (no marks needed, 2 runs each) | $0.00171, $0.00184 | $0.00185, $0.00200 (+8%, +9%) | 23% fewer tokens sent, a bigger bill: cached tokens fell from 43.4k to 29.7k and fresh tokens rose. |
| GPT-6.1 Sol (3 runs each) | $0.031, $0.036, $0.044* | $0.026, $0.026, $0.021* | Cutting sent the cache back to the system prompt on steps 4 and 6 in 3 of 3 runs. The dollars cannot be compared, see below. |

\* 5 steps, the sixth was refused by the spend cap.

- **Marks do nothing on DeepSeek**, good or bad: $0.001708 with, $0.001711 without.
- **GPT-6.1 Sol's own cache is unreliable without any cutting.** In the three runs
  that cut nothing, the step before was fully read from cache on only about 4 of 13
  follow-on steps; on 4 it read nothing and rewrote the whole conversation at the
  write price. That is why its "not cut" runs cost more than its "cut" runs. Cause
  not found (it is not the pruning). Two of the first Sol runs also returned empty
  replies with the 16-token cap; the later runs used 64 and had none.

## What this says

1. Claude models bill about three times what they need to today. One mark on the
   last message fixes it; the dial's own placement gets two thirds of that.
2. Cutting every step loses money on every model where it could be measured, and
   on Claude it switches caching off completely while still paying for it.
3. Old tool results cost almost nothing once cached (5% of the fresh price on
   Claude, 3% on DeepSeek), so there is little left for per-step cutting to save.

## Not measured (cut off by the spend cap)

- Claude cache lifetime past 240 s, and whether a read restarts the clock.
- DeepSeek cache lifetime at 10 and 30 minutes (alive at 15 minutes in an earlier test).
- Cross-chat sharing on Opus.
- Whether a mark makes GPT-6.1 Sol's cache reliable.
- Nothing here ran in the app; this is the gateway only. Each piece that gets built
  is to be tested live in the app.
