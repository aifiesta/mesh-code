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

---

# Smart routing on the new scores (17 November 2026)

For coding work Smart routing now picks a model and a thinking level together
from `model_scores.json`, by lowest expected cost to finish. Tested in the real
desktop app with Smart routing on, the previous engine (commit 4215a24) against
this one, same three tasks, same starting project, all recorded.

- Six runs, **$0.14 billed** (ceiling was $1). 206 unit tests pass (24 new).
- Evidence: `~/Desktop/mesh-code-model-scoring/4-smart-routing-in-app/`, one
  folder per run with `recording.webm`, `conversation.md`,
  `what-the-model-changed.diff` and the project after the run.
- Run it again with `run_smart_compare.sh`.

| Task | Engine | Router's pick | Result | Billed | Time |
|---|---|---|---|---|---|
| Easy: explain price.py | old | Seed 1.6 Flash, no level | correct | $0.0008 | 6 s |
| | new | DeepSeek V4.1 Flash 1017 at max | correct, and pointed out the discount bug | $0.0008 | 6 s |
| Bug fix: make the tests pass | old | Seed 1.6 Flash, no level | fixed, 4 of 4 pass | $0.0014 | 16 s |
| | new | DeepSeek V4.1 Flash 1017 at max | fixed, 4 of 4 pass | $0.0014 | 30 s |
| "Hard": refactor for production, edge cases, tests | old | DeepSeek V4 Flash, no level | done as asked, 6 tests pass | $0.0027 | 39 s |
| | new | GPT-6.1 Sol at extra high | done as asked and more, 47 tests pass | $0.1334 | 205 s |

What this shows:

- **The wiring works.** The pick, the thinking level in the request, the note
  in the app ("Routed to … at max thinking") and the fall-back to the old table
  for non-coding prompts all behave as designed. The relay log confirms the
  level sent on every request.
- **On easy and ordinary prompts nothing got cheaper or dearer.** Both routers
  send them to a very cheap model. The new one asks for max thinking, which
  doubled the wait on the bug fix.
- **On the prompt labelled hard, the new router cost 49 times more and took 5
  times longer for an outcome the old pick also reached.** GPT-6.1 Sol's result
  is more thorough (input validation, 47 tests) but the task did not require
  it. The scratch project is small, so this says the label was wrong, not that
  the scores are.

Two things behind that:

- **The difficulty label is a keyword guess.** "Refactor", "production" and
  "edge cases" made a small task "hard". The bug-fix prompt was labelled easy.
  Under the old router a wrong label cost almost nothing, because every level
  went to a cheap model. Now it decides between a $0.003 run and a $0.13 run.
- **The scores say cheap models fail hard work.** DeepSeek V4.1 Flash is rated
  about 27% on hard tasks against 40% for GPT-6.1 Sol. That is measured on
  genuinely hard benchmark tasks; it does not describe a small "refactor".

Not covered live: step-up after a failure, sticking with the serving model, and
a user-fixed thinking level under Smart routing. All three are unit-tested.

Status: committed on `claude/model-scoring-763885`, **not merged** into
`token-optimisation` until the cost on hard-labelled prompts is decided.

---

# Five task levels and small-task pricing (18 November 2026)

Coding prompts are now sorted by the shape of the request onto five levels
(Lookup, Small change, Standard task, Complex task, Deep work). Wording never
assigns the top two. The two lowest levels are priced as a small task at the
gateway's live token prices; the rest keep the benchmark cost per task.

- Three recorded runs in the real app on the new engine, **$0.06 billed**
  (ceiling was $0.50). The old-engine runs from the section above are reused,
  not re-run. 222 unit tests pass, including the 26 agreed sample prompts.
- Evidence: `~/Desktop/mesh-code-model-scoring/5-task-levels-in-app/`.
  Run it again with `run_levels.sh`.

| Prompt | Old engine | First version of the new pick | Now |
|---|---|---|---|
| Explain price.py, change nothing | Seed 1.6 Flash, $0.0008, 6 s | DeepSeek V4.1 Flash 1017 at max, $0.0008, 6 s | **Lookup**: DeepSeek V4.1 Flash 1017 at max, $0.0008, 8 s |
| Tests fail, fix price.py, run tests | Seed 1.6 Flash, $0.0014, 16 s | DeepSeek V4.1 Flash 1017 at max, $0.0014, 30 s | **Small change**: DeepSeek V4.1 Flash 1017 at max, $0.0015, 22 s |
| Refactor for production, edge cases, add tests, run suite | DeepSeek V4 Flash, $0.0027, 39 s, 6 tests | GPT-6.1 Sol at extra high, $0.1334, 205 s, 47 tests | **Standard task**: GPT-6.1 Sol at medium, $0.0580, 83 s, 23 tests |

All nine runs did what was asked. The app showed the level and the rule behind
it, for example "Routed to gpt-6.1-sol at medium thinking — Standard task (6
kinds of action)".

What this shows:

- **The levels and the guard work.** The prompt full of hard-sounding words
  starts as a Standard task, not at the top. Its cost fell from $0.133 to
  $0.058 and its time from 205 s to 83 s.
- **Small changes stay on the cheap model.** Without the small-task pricing,
  level 2 would have gone to GPT-6.1 Sol.
- **A Standard task still costs about 20 times the old pick on this project**
  ($0.058 against $0.0027), for a result the old pick also reached. At this
  level the scores rate the cheap and the mid model about equal (53% and 52%);
  the mid model wins on the benchmark's wait time, and the benchmark cost hides
  the token price gap, exactly as it did for small changes.

Simulated, not built: pricing a Standard task by the token as well (about
100,000 tokens in, 3,000 out, from the three recorded runs at this level).
The pick then depends on how long such a task is assumed to take: DeepSeek V4.1
Flash 1017 or Claude Haiku 5.5 at max when it is short, GPT-6.1 Terra at high
when it is a third of a benchmark task. A step-up from there goes to GPT-6.1
Sol at extra high either way.

Not covered live: step-up, sticking with the serving model, a forced level.
Unit-tested only.

Status: committed on `claude/model-scoring-763885`, not merged into
`token-optimisation`.
