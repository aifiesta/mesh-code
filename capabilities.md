# Mesh Code — capabilities

What the app can do today, stated plainly. If something here stops being
true, that is a bug — file it against this document.

## The agent

- **Plans, then works.** Multi-step tasks get an explicit plan (visible in
  the transcript and the Plan panel) with per-step status the model updates
  as it goes. If a turn ends with the plan counter behind the actual work,
  the transcript says so rather than leaving a frozen "2/8".
- **Tools:** read/write files (writes shown as real diffs before landing),
  run shell commands, start background dev servers (auto port, live
  preview), search the web, ask the user structured questions
  (rendered inline in the chat, never a popup), maintain the plan, save
  repo memory.
- **Asks instead of assuming.** `ask_user` questions appear in the
  transcript with options + free text; answered questions collapse to a
  one-line record. Unanswered approvals time out as **denial**.
- **Long turns survive.** Deterministic history compaction (truncate then
  fold, no LLM calls, transcript preserved on disk), stall detection
  (nudge → renudge → stop on repeated identical tool batches), retry ladder
  with Retry-After respect, max_tokens overflow shrink-and-retry.

## Model control

- **Pinned model** — pick any model from the live catalog; fuzzy-searchable
  browser with per-1M pricing and context length.
- **Local first** (default) — the local model writes; a stuck turn
  escalates to the strong-model chain (Sonnet 5.5 → Opus 5.5 → GPT-6.1 Sol
  → Gemini 4 Argon, minus exclusions) on a repeated action, two failed tool
  batches, a refusal, or the Escalate button; hands back after 8 hops or
  300k tokens; "Harder" raises the effort one level for that escalation;
  sticky per turn or per session.
- **Gateway auto** — Mesh's Auto Router decides per prompt with a weight
  profile, never picking an excluded model.
- **Budget** — Settings › Budget: one monthly limit (default $10). Today's
  share is what was left of the month this morning divided by the days
  left. The control bar ring fills with today's share (amber at 80%, red
  when over); its popover shows Today (resets at midnight) and This month
  (resets on the 1st), the spend by tier, "Allow more today" when held, and
  "I raised the cap" after a Mesh HTTP 402. A held cloud turn
  ends with a notice; a held escalation hands back to the local model.
  Local hops are never counted.
- The bar is honest about both: with routing on it reads "Auto routing"
  until a hop has actually answered, then shows the real model with a
  `routed` flag.
- Model exclusions, fallback models, output style, reasoning effort — all
  in Settings (⌘,) and the ⌘K palette.

## Cost & usage

- Live token and cost readout in the composer bar that moves **during** a
  turn (per-hop status), not just at the end.
- Per-turn line in the transcript: model · hops · tokens · cost · time.
- Costs the gateway bills are shown as-is; when a model reports no cost the
  app computes from the catalog's own per-token rates and marks it `~`
  (estimate) — a guess is never presented as a billed figure.
- Session and lifetime totals in the Account panel.

## Permissions & safety

- Four modes, always visible next to the composer: **Ask every time**,
  **Accept edits**, **Auto**, **Bypass**.
- Hard gates outrank every mode: writes outside the project, sensitive
  paths (`~/.ssh` etc.), and dangerous command shapes come back as
  questions with the reason stated.
- The engine binds to loopback only, requires a per-launch token on every
  request and WebSocket, and checks Origin — a browser tab cannot drive it.

## Projects & files

- Multi-root, VS Code-style: several folders open at once, one **active**
  project; per-project chat that survives switching (switching asks first
  when a conversation is live, and the old one keeps running).
- Explorer with create/rename/delete (delete confirms; root is guarded),
  file preview, and click-to-mention — dropping a path into the composer.
- Sessions grouped by day in the sidebar; busy dot on running sessions.

## Panels

- **Plan** — live step list.
- **Preview** — dev servers the agent starts render in-app; stop from the
  RUNNING list.
- **Shell** — a real persistent shell (`$SHELL`, run non-interactively, so no
  rc/profile files; yours, not the agent's): arrow
  history, ⌃C, `cd` persists. Deliberately ungated — it is you typing.
  Being a pipe (not a PTY), full-screen programs like vim/top won't run.
- **Account** — key status (hint + source, never the key itself), models
  available, lifetime spend.
- All panels live in a resizable, hideable right rail; the sidebar is
  resizable too, and both clamp against the window so no layout can crush
  the transcript.

## Interface

- ⌘K palette: every setting expanded to one runnable action per option,
  grouped with sticky headers, matched against the engine's own help text
  fuzzy abbreviations (`wwga` → Who writes: Gateway auto).
- "/" in an empty composer opens the palette; ⏎ sends, ⇧⏎ newline; typing
  while the agent works queues the message for the next turn.
- Images paste/drop/attach into the composer (multimodal).
- Dark, light, or follow-system — full token coverage both ways.
- Guided tour: opt-in from the "Take the tour" link in the sidebar, the
  palette, or the account menu. Never auto-starts.

## Install & identity

- macOS/Linux: `curl -fsSL https://code.meshapi.ai/install.sh | sh`;
  Windows: `irm https://code.meshapi.ai/install.ps1 | iex`. Both verify the
  download against `SHA256SUMS`. (A directly-shared .dmg needs
  notarisation; see packaging/SHARING.md.)
- Sign in with a Mesh API key; it is stored in an owner-only file
  (`~/.mesh-code/credentials`, 0600 on macOS/Linux), never in config files, and the
  UI only ever sees a hint (`…a1b2`).

## Current limits (honest list)

- One agent turn at a time per session (queued input, no parallel turns).
- The shell tab is a pipe: no TTY programs, no ANSI cursor apps.
- Web search/fetch goes through the gateway's tools — no local browsing.
- Windows and Linux builds ship since 0.2.4 but are lightly tested; on
  Windows the Shell panel needs a POSIX `sh` on `PATH`.
- Models the gateway prices at zero and omits from the catalog show `$0` —
  the app cannot invent a rate for them.
