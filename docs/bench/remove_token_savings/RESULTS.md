# Token savings setting removed: live check (23 November 2026)

A separate Mesh Code window was started from this branch, with its own
settings folder holding a config file saved by an older version: the old
`optimize` key at 0.5 ("Token savings" turned up). `drive.js` attached to that
window, recorded it, and ran the checks below. Spending ceiling: $0.25 for
the whole check; $0.05 per run. Spent: about $0.003.

## The setting is gone

| Check | Result |
|---|---|
| Settings the engine sends the window | no `optimize`, no slider-type setting |
| Spend pop-up (where the slider was) | no Token savings |
| Settings sheet, every category | no Token savings |
| ⌘K "token savings" | 0 results |
| ⌘K "spend" | finds Routing (Pinned, Smart, Gateway auto) |
| Old config file with `optimize: 0.5` | the window started normally; after a settings save the key is gone from the file and every other setting is kept |

## A short task still works, and Claude still reads from cache

Task: "Read calc.py and util.py and tell me in two sentences what score()
returns. Do not change any file." Model pinned per chat. File reads approved,
anything else turned down.

| Model | Steps | Cost | Step 1 input: fresh / cached / written | Step 2 input: fresh / cached / written | Answer |
|---|---|---|---|---|---|
| DeepSeek V4.1 Flash | 2 | $0.000416 | 332 / 3,072 / 0 | 236 / 3,328 / 0 | right |
| Claude Haiku 5.5 | 2 | $0.000262 | 4 / 4,443 / 290 | 2 / 4,733 / 339 | right |

On Claude the first step already read the shared system prompt from the
cache written by the window's earlier chat, and the second step read
everything the first one sent. Neither request carried a reply-length cap.

A first attempt turned down every approval, file reads included, so both
models answered that they could not read the files (DeepSeek $0.001229,
Claude Haiku $0.000716, the latter writing 4,731 tokens to the cache on step 1
and reading them on step 2). The script was fixed to approve file reads and
run again.
