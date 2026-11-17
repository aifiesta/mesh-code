# Thinking-level check through Mesh (17 November 2026)

Which thinking levels can Mesh Code actually request for the 19 models in the
new scoring file? One small question was sent per model, level and request
form, shaped like an engine turn (streaming, one tool defined).

- 228 requests, 223 answered, **$0.33 billed** (ceiling was $2).
- Script: `level_check.py` in this folder. Raw replies: `calls.jsonl`. One row
  per request: `levels.csv`. Five extra non-streaming requests:
  `non-streaming-checks.jsonl`. All three sit in
  `~/Desktop/mesh-code-model-scoring/1-thinking-level-check/`.

## What was found

1. **The field the engine sends today stops at high.** `reasoning_effort`
   accepts only none, low, medium and high; anything else is refused before it
   reaches a model. Mesh marks this field as deprecated.
2. **Mesh's current field accepts every level.** `reasoning: {"effort": ...}`
   accepted none, low, medium, high, extra high and max for all 19 models. To
   use extra high or max the engine has to send this field.
3. **Two exceptions.** Mistral Large 4 refuses "none" in the new field. The
   four GPT-6 models refuse "minimal" when a tool is defined (the scoring file
   does not use that level).
4. **Eleven models keep thinking when asked for none.** For three of them the
   scoring file had an entry measured with thinking off (DeepSeek V4 Pro 0813,
   GLM 5.3 Flash, GPT-6.1 Terra). Those three entries are now marked not
   requestable, because Mesh would serve something else.
5. **Accepted is not the same as honoured.** Mesh never reported dropping a
   setting, in streaming or non-streaming replies. On this small question the
   amount of thinking clearly rose with the level for GPT-6 Astra and GPT-6.1
   Sol, and at max for DeepSeek V4.1 Flash. For the other models one small
   question cannot show whether a higher level changes anything.

## Per model

"Thinking by level" is thinking tokens at low / medium / high / extra high /
max in the new field (for Claude models, estimated from the thinking text,
because Mesh reports their thinking tokens as 0). One sample each, so small
differences mean nothing.

| Model | Levels accepted (new field) | No level sent | "none" switches thinking off | Thinking by level | Billed |
|---|---|---|---|---|---|
| anthropic/claude-opus-5.5 | all 6 | no thinking | yes | 89 / 79 / 70 / 75 / 83 | $0.043 |
| anthropic/claude-sonnet-5.5 | all 6 | no thinking | yes | 0 / 0 / 79 / 77 / 77 | $0.030 |
| google/gemini-4-carbon | all 6 | no thinking | yes | 469 / 273 / 653 / 0 / 0 | $0.020 |
| openai/gpt-6-astra | all 6 | thinks | no, still thinks | 30 / 30 / 146 / 140 / 142 | $0.039 |
| moonshotai/kimi-k3.1-pro | all 6 | thinks | no, still thinks | 87 / 149 / 137 / 143 / 153 | $0.025 |
| openai/gpt-6.1-sol | all 6 | thinks | no, still thinks | 33 / 112 / 43 / 142 / 178 | $0.008 |
| openai/gpt-6.1-terra | all 6 | thinks | no, still thinks | 139 / 155 / 147 / 153 / 142 | $0.005 |
| google/gemini-4-hydrogen | all 6 | thinks | no, still thinks | 471 / 986 / 849 / 947 / 883 | $0.073 |
| google/gemini-4-argon | all 6 | no thinking | yes | 0 / 0 / 370 / 0 / 0 | $0.010 |
| anthropic/claude-haiku-5.5 | all 6 | no thinking | yes | 103 / 129 / 151 / 122 / 125 | $0.002 |
| deepseek/deepseek-v4.1-flash-1017 | all 6 | no thinking | yes | 160 / 228 / 187 / 156 / 164 | $0.001 |
| deepseek/deepseek-v4.1-flash | all 6 | no thinking | yes | 165 / 170 / 187 / 190 / 350 | $0.002 |
| deepseek/deepseek-v4-pro-0813 | all 6 | thinks | no, still thinks | 397 / 562 / 811 / 479 / 317 | $0.016 |
| xiaomi/mimo-v2.6-pro | all 6 | thinks | no, still thinks | 347 / 184 / 340 / 247 / 214 | $0.005 |
| xiaomi/mimo-v2.6-flash | all 6 | thinks | no, still thinks | 255 / 155 / 286 / 151 / 187 | $0.001 |
| z-ai/glm-5.3-flash | all 6 | thinks | no, still thinks | 79 / 120 / 124 / 136 / 156 | $0.001 |
| openai/gpt-6-luna | all 6 | no thinking | yes | 136 / 157 / 159 / 148 / 148 | $0.001 |
| mistralai/mistral-large-4 | all but none (new field) | thinks | no, still thinks | 594 / 684 / 624 / 898 / 926 | $0.036 |
| minimax/minimax-m3.1-flash | all 6 | thinks | no, still thinks | 439 / 143 / 274 / 148 / 530 | $0.004 |

Gemini 4 Carbon and Argon showed no thinking at extra high and max in the run
above, but did think at those levels in the non-streaming follow-up (316 and
368 tokens). Their thinking is decided per request, so the zeros are not proof
that the level was ignored.

## What this changes

- The scoring file now treats 65 of its 68 entries as requestable (was 48).
- With extra high and max available, the simulated picks become: easy tasks
  DeepSeek V4.1 Flash 1017 at max, medium tasks GPT-6.1 Sol at medium, hard
  tasks GPT-6.1 Sol at extra high, last resort Claude Opus 5.5 at max.
- The engine must send the new field, and must send a level per turn rather
  than one global setting.

## Not covered

- Whether each level changes real task results. That would need real tasks per
  level, which the user has ruled out; the scores rely on third-party tests.
- No app run and no screen recording: this check talks to the gateway directly
  and changes nothing in the app.

---

# Thinking levels in the app (17 November 2026)

The engine now sends the thinking level in Mesh's current field, and Settings
offers Extra high and Max. Tested in the real Mesh Code desktop app, driven by
`app_run.js` and recorded on video. A local relay (`record_relay.py`) logged
what the app actually sent to Mesh.

- Three runs, **$0.10 billed** (ceiling was $1). 170 unit tests pass.
- Everything is in `~/Desktop/mesh-code-model-scoring/2-thinking-levels-in-app/`:
  per run a `recording.webm` (opens in Chrome), three screenshots,
  `conversation.md`, `what-the-model-changed.diff` and the project after the
  run. `0-project-before/` is the starting project.
  `what-the-app-sent-to-mesh.jsonl` is the relay's log.

Task for runs 1 and 2: a small pricing module with two planted bugs and three
failing tests. "Find what is wrong in price.py, fix it, and run the tests."

| Run | Reasoning set in Settings | What the app sent | Requests | Thinking tokens | Result | Billed | Time |
|---|---|---|---|---|---|---|---|
| 1 | GPT-6.1 Sol, Low | `reasoning: {"effort": "low"}` on all 13 | 13 | 23 | both bugs fixed, 4 of 4 tests pass | $0.032 | 37 s |
| 2 | GPT-6.1 Sol, Max | `reasoning: {"effort": "max"}` on all 14 | 14 | 1464 | both bugs fixed, 4 of 4 tests pass | $0.055 | 84 s |
| 3 | Mistral Large 4, Off | `none` once, refused; then 3 without a level | 4 | 273 | answered correctly, no files changed | $0.013 | 15 s |

What this shows:

- **Max reaches the model.** Before this change the engine's field stopped at
  High. On the same task Max used 1464 thinking tokens against 23 at Low,
  cost 1.7 times as much and took 2.3 times as long. Both levels solved
  this small task, so it shows the level takes effect, not that Max is better.
- **A refused level no longer ends the turn.** Mistral Large 4 refuses "none".
  The app showed "this model does not take the 'none' reasoning level,
  retrying without it" and answered. The refusal is remembered per model and
  level, so other levels on that model still go out.
- **The old field is gone from requests.** `reasoning_effort` was absent in all
  31 requests.

Not covered:

- No old-engine comparison run: the old engine cannot send Max at all, and at
  Low to High only the field name differs.
- The scoring file is bundled and loaded (`core/scores.py`) but the router does
  not read it yet. That is the next piece.
- One task, one run per level. This is a wiring check, not a quality test.

Seen in passing: in Auto mode both GPT-6.1 Sol runs created a Python
environment under `/tmp` to get pytest, without the app asking, although Auto
is described as asking before writing outside the project. Not changed here.

---

# Review fixes (17 November 2026)

A review of the change above found six things worth fixing before Smart
routing builds on it. All six are fixed; 182 unit tests pass (12 new).

- **A refused level now falls back to the nearest accepted one.** The refusal
  usually lists the levels the model takes. Max on a model that stops at High
  runs at High; before, it ran with no level at all.
- **More refusal wordings are recognised,** and a model id such as
  `…-non-reasoning` in an unrelated error is not mistaken for one.
- **A refusal sent as an HTTP error is retried** like one sent inside a reply.
- **The last-resort non-streaming request** uses the same model and level as
  the streaming attempt. It used to re-send a refused level, to the pinned
  model.
- **The score loader** leaves out entries missing a margin or a wait time, and
  hands out copies so a caller cannot alter the loaded file.

Live check in the real app, recorded
(`~/Desktop/mesh-code-model-scoring/3-review-fixes-in-app/`): Mistral Large 4
with Reasoning set to Off. "none" was refused once; the app showed "using
'low' instead" and sent `low` on the next three requests.

| | Before the fix | After |
|---|---|---|
| Level used after the refusal | none sent (model default) | low |
| Thinking tokens | 273 | 51 |
| Billed | $0.013 | $0.007 |

Left for later: tests that assert today's model list, the unused
`reasoning_rejected_models` setting, the app driver approving every dialog,
and the relay log reading only the last 200 KB of a reply.
