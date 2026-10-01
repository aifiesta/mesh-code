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
- **Smart routing (local)** — a bundled table classifies each prompt
  (cohort + difficulty) and picks per turn, zero extra tokens; the bar
  explains what it would pick and why before you spend anything.
- **Gateway auto** — Mesh's upstream Auto Router decides.
- The bar is honest about all three: with routing on it reads
  "Router picks"/"Gateway picks" until a hop has actually answered, then
  shows the real model with a `ROUTED` tag.
- Effort dial, model exclusions, fallback models, output style, reasoning
  effort — all in Settings (⌘,) and the ⌘K palette.

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
- **Shell** — a real persistent zsh (your shell, not the agent's): arrow
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
  (typing "cheap" finds token savings), fuzzy abbreviations (`rtsm` →
  Routing: Smart).
- "/" in an empty composer opens the palette; ⏎ sends, ⇧⏎ newline; typing
  while the agent works queues the message for the next turn.
- Images paste/drop/attach into the composer (multimodal).
- Dark, light, or follow-system — full token coverage both ways.
- Guided tour: opt-in from the "New here?" card in the sidebar, the
  palette, or the account menu. Never auto-starts.

## Install & identity

- macOS: `curl -fsSL https://code.meshapi.ai/install.sh | sh` — checksum-
  verified, no quarantine, no dialogs. (A directly-shared .dmg needs
  notarisation; see packaging/SHARING.md.)
- Sign in with a Mesh API key; it is stored in the system keychain, never
  in config files, and the UI only ever sees a hint (`…a1b2`).

## Current limits (honest list)

- One agent turn at a time per session (queued input, no parallel turns).
- The shell tab is a pipe: no TTY programs, no ANSI cursor apps.
- Web search/fetch goes through the gateway's tools — no local browsing.
- No Windows/Linux builds shipped yet (packaging targets exist, untested).
- Models the gateway prices at zero and omits from the catalog show `$0` —
  the app cannot invent a rate for them.
