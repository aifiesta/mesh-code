<div align="center">

<img src="docs/icon.png" width="110" alt="Mesh Code icon" />

# Mesh Code

**An agentic coding app for your desktop. A coding model runs on your own
machine for free; a strong cloud model steps in when it gets stuck.**

It plans, writes files, runs commands and starts dev servers inside a project
folder you choose. Every step is either auto-approved by a mode you picked,
or shown to you as a real diff before it happens.

[![release](https://img.shields.io/github/v/release/aifiesta/mesh-code?label=release&color=6f5af5)](https://github.com/aifiesta/mesh-code/releases/latest)
[![downloads](https://img.shields.io/github/downloads/aifiesta/mesh-code/total?color=2ea043)](https://github.com/aifiesta/mesh-code/releases)
![platform](https://img.shields.io/badge/macOS%20%7C%20Windows%20%7C%20Linux-111)
![models](https://img.shields.io/badge/models-1%2C000%2B%20via%20one%20key-6f5af5)

```bash
curl -fsSL https://code.meshapi.ai/install.sh | sh
```

<img src="docs/hero.png" width="920" alt="Mesh Code running a task: plan, diffs, test run and live cost in one window" />

</div>

---

## Why Mesh Code

Most coding agents make you choose: a terminal you can script, or a GUI you
can trust. Mesh Code refuses the trade. It is the
[`meshapi` CLI](https://github.com/aifiesta/meshapi-code)'s agent loop — the
same streaming client, repo memory and safety gates — lifted
into a window where you can *see* what the agent is doing and stop it with
your eyes open:

- **Plans you can watch.** Multi-step work gets an explicit checklist the
  model updates as it goes — in the transcript and in a live side panel.
- **Diffs before writes.** Every file change is a real diff you approve,
  not a notification you discover.
- **Real execution.** It runs your commands and test suites for real, starts
  dev servers with port detection, and renders them in a built-in preview.
- **Live cost, during the turn.** Token and dollar readouts move while the
  model works, hop by hop — not a surprise at the end of the month.

## A coding model on your own machine

New in 0.3.0. On first launch Mesh Code reads this machine's memory and GPU
and picks the strongest open coding model that leaves room for everything
else you run. When that model is 8 GB or less and runs on the GPU it
downloads straight away (also on the CPU if you turned on Use even when
slow); otherwise the app asks first. The download is
resumable and checked against a pinned sha256. From then on the model on
your machine writes the code: free, offline, and private while it is the
one answering. The `llama-server` runtime that serves it ships inside the
app on macOS and in the CPU builds; Windows and Linux machines with a GPU
download a GPU build of it on first use.

| This Mac's memory | Model it picks | Download | Context |
|---|---|---|---|
| 8 GB | Qwen3.5 4B · Q4 (slow; the cloud answers unless you opt in) | 2.7 GB | 8k |
| 16–18 GB | Qwen3.5 9B · Q4 | 5.7 GB | 32k |
| 24 GB | Qwen3.5 9B · Q8 | 9.5 GB | 64k |
| 32 GB | North Mini Code 1.0 (30B-A3B) · Q4 | 19 GB | 64k |
| 36 GB and up | Qwen3.8 27B · Q6 | 22 GB | 32k–256k |

Anything over 8 GB, or a model that would run without a GPU, asks before
downloading, and Settings › Local model lets
you pick a different one, delete one, or turn the local model off. Until a
model is ready, or if it cannot start, your chosen cloud model answers.

When the local model gets stuck — it repeats the same action, fails two
batches of tool calls, refuses, or claims work it never did — a strong
cloud model takes the rest of that turn and hands back after 8 steps or
300k tokens. Whatever it takes over, including command output, is sent to
that cloud model. You
can also press **Escalate** yourself. The strong models are a chain picked
from the Artificial Analysis coding-agent benchmarks: Claude Sonnet 5.5,
then Opus 5.5, GPT-6.1 Sol and Gemini 4 Argon, so one provider's outage
does not end the turn.

## One key. Every model that matters.

<div align="center">
<img src="docs/models.png" width="880" alt="The model browser: 1,000+ models with context windows and per-million pricing" />
</div>

Mesh Code talks to the Mesh gateway, which fronts **1,000+ models** from
Anthropic, OpenAI, Google, Meta, DeepSeek, Mistral, Qwen, xAI
and dozens more — through one API key and one bill. The app makes that
catalog usable instead of overwhelming:

- **Pin a model** — fuzzy-search the browser (`son45` finds Sonnet 4.5),
  compare context windows and per-1M pricing, filter to free or text-only.
- **Local first** — the default policy: the model on this machine writes
  the code for free, and a strong cloud model (Sonnet 5.5, then Opus 5.5,
  GPT-6.1 Sol, Gemini 4 Argon) takes over a stuck turn on evidence or when
  you press **Escalate**, then hands back after its share of the turn.
- **A budget you set** — one monthly cloud limit ($10 by default); today's
  share is what is left divided by the days left. The bar shows today as a
  ring, the popover shows Today and This month with what each tier cost.
  Past the share, cloud work pauses until midnight unless you allow more;
  past the month, the cloud stops and the local model keeps working. Work
  on this machine is never counted.
- **Gateway auto** — or let Mesh's Auto Router pick per prompt, honouring
  the models you have excluded.
- **Fallback order** — if the primary model fails or is rate-limited, the
  gateway tries your fallbacks in order. A long run survives one provider's
  bad day.
- **Exclusions** — mark a model "Never" once, and no mode of routing will
  touch it again.
- **Your own commands** — start a message with `!` and it runs in the
  project folder; the output lands in the chat and whichever model answers
  next reads it, which may be a cloud one.

Honesty is a feature: with routing on, the bar reads "Auto routing" until a
hop has actually answered, then shows the real model with a `routed` flag.
When a model reports no cost, the app computes an estimate from the
catalog's own rates and marks it `~` — a guess is never presented as a bill.

## An agent on a leash you choose

<div align="center">
<img src="docs/approval.png" width="880" alt="A write approval: the exact diff, approve / always allow / reject" />
</div>

Four permission modes sit next to the composer, always visible: **Ask every
time → Accept edits → Auto → Bypass**. And some gates outrank every mode —
a write outside your project, a path like `~/.ssh/`, or a command shaped
like `rm -rf /` comes back as a question with the reason stated, even with
auto-approval on.

The engine itself binds to loopback only, requires a per-launch token on
every request and WebSocket, and checks Origin — a browser tab cannot drive
it. Your API key lives in an owner-only file (`~/.mesh-code/credentials`,
0600 on macOS/Linux); the UI only ever sees a hint (`…a1b2`).

## Every dial, findable

<div align="center">
<img src="docs/palette.png" width="880" alt="The command palette: every setting as a runnable action" />
</div>

CLI agents hide two dozen slash commands behind `/help`. Here the same power
is layered: clickable pills for what you change often, **⌘K** for everything
by name *or* by what it does (`wwga` matches Who writes: Gateway auto), and
**⌘,** for grouped settings. A guided tour is
offered — never forced.

## Built for long sessions

- **Restarts lose nothing.** Conversations snapshot to disk and restore on
  launch — transcript, plan, cost totals intact.
- **Many projects, many chats.** Each open folder is its own session with
  its own history, plan and servers; every project keeps a list of past
  conversations you can switch in place.
- **A project journal.** A per-project notebook the agent maintains — proven
  run commands, what's built, learnings — injected into every new session so
  a fresh session runs the project instead of re-discovering it.
- **Long turns survive.** Deterministic history compaction, stall detection,
  a retry ladder that respects `Retry-After`, and overflow shrink-and-retry.

## Standalone by design

Mesh Code needs **no Python, no Node, no CLI** preinstalled. The installer
drops a single app that carries its own runtimes: the engine is frozen into
a ~28 MB binary with Python inside it, Electron brings its own Node, and
`llama-server` (~16–24 MB) is bundled for the local model (a GPU build for
Windows or Linux is fetched on first use). The model weights are the one
large download. If
you do use the `meshapi` CLI and are signed in, your key is picked up
automatically — read once, never written.

```
┌─────────────────────────── Mesh Code.app ───────────────────────────┐
│                                                                     │
│  Electron shell ──── spawns ────▶  engine (frozen binary)           │
│   window, menus                     agent loop · policy · budget    │
│        │                            loopback only · token-gated     │
│        └── loads UI from ──────────▶│            │                  │
│                                     ▼            ▼                  │
│                    llama-server (this machine)   Mesh gateway       │
│                    the local model, free         1,000+ models      │
└─────────────────────────────────────────────────────────────────────┘
```

## Install

**macOS (Apple Silicon):**

```bash
curl -fsSL https://code.meshapi.ai/install.sh | sh
```

The installer downloads the current release, verifies it against the
published checksums, installs it to /Applications, and launches it. Re-run
any time to upgrade. Prefer to read before you pipe? The script is short and
inspectable: [`install.sh`](install.sh).

You can also grab the `.dmg` directly from
[Releases](https://github.com/aifiesta/mesh-code/releases) — note that a
directly-downloaded dmg is quarantined by macOS and needs
`xattr -dr com.apple.quarantine "/Applications/Mesh Code.app"` after copying,
until the build is notarised. The curl installer handles this for you.

**Windows (x64):**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://code.meshapi.ai/install.ps1 | iex"
```

**Linux (x86_64):** the same `curl` command installs an AppImage to
`~/.local/bin/mesh-code` with a desktop entry. AppImages need `libfuse2`
(`libfuse2t64` on Ubuntu 24.04+).

Windows and Linux builds are new as of 0.2.4 and lightly tested; each
release ships all three installers plus `SHA256SUMS`.

## First launch

The local model downloads straight away when it is 8 GB or less and runs on
the GPU; otherwise the app asks first. Once it is installed you can use the
app with no key at all. For the cloud (escalation, other
models), add a Mesh API key ([app.meshapi.ai](https://app.meshapi.ai)).
It is stored in an owner-only file (`~/.mesh-code/credentials`, 0600 on
macOS/Linux) and sent nowhere but the Mesh gateway. If you already use the
`meshapi` CLI, your existing key is picked up automatically.

## Honest limits

Mesh Code is in **beta**, and things might break.
Today's known edges, stated plainly:

- One agent turn at a time per session (typed input queues for the next turn).
- The Shell tab is a pipe, not a PTY — no vim, no top.
- Web search goes through the gateway's tools; there is no local browsing.
- Windows and Linux builds are lightly tested; on Windows the Shell panel
  needs a POSIX `sh` on `PATH`.
- A small local model is not a frontier model. Expect it to need the
  escalation now and then on hard, multi-file work.
- Without a GPU the local model is slow, so on such machines the cloud
  answers unless you turn on Settings › Local model › Use even when slow.
- If the whole window flickers while the local model runs, quit the app
  and relaunch it with `open --env MESH_OPAQUE=1 -a "Mesh Code"` for a
  window without the macOS vibrancy layer.

Found a rough edge — or something you love? The **Send feedback** link in
the sidebar opens a 15-second form; the engine relays your rating and the
two optional text boxes to a Google Form, and nothing else.

## What's in this repository

The whole app: `engine/` (the Python agent engine and its tests), `ui/`
(React), `desktop/` (Electron shell), `packaging/` (freeze, sign, build
scripts) and the install scripts. Releases are built by CI from a tag;
see `DECISIONS.md` for the choices behind that.
