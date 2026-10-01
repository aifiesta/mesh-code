# Architecture

## The problem this solves

The `meshapi` CLI is ~10k lines. About 6k of that is an agent engine worth
keeping: a streaming client that repairs malformed tool-call deltas from
misbehaving providers, a local routing table, repo memory, context
compaction, safety gates. The other ~4k is a terminal — `prompt_toolkit`
key bindings, `rich` live regions, a REPL.

The two were interleaved. `cli.py` alone is 3,073 lines in which the agent
loop, the drawing and the approval prompts are the same code:
`handle_tool_calls` decides whether a call is safe **and** prints the diff
**and** blocks on `input()` for a `y/n`. There was no seam to build a second
frontend against — which is exactly why "make a desktop version" would
otherwise mean forking the loop and maintaining two of them forever.

So the conversion is a **separation**, not a port. Same engine, new edges.

## Layers

```
┌──────────────────────────────────────────────────────────┐
│ desktop/        Electron: window, menus, engine lifecycle │
│                 spawns the engine, reads {port, token}    │
└────────────────────────┬─────────────────────────────────┘
                         │ loads http://127.0.0.1:<port>
┌────────────────────────▼─────────────────────────────────┐
│ ui/             React. Renders events, sends requests.    │
│                 Knows nothing about agents.               │
└────────────────────────┬─────────────────────────────────┘
                         │ WebSocket, token-authenticated
┌────────────────────────▼─────────────────────────────────┐
│ engine/server.py    transport + static assets             │
│ engine/session.py   the agent loop, one thread/session    │
│ engine/protocol.py  the contract                          │
│ engine/core/        vendored CLI engine (unchanged)       │
└──────────────────────────────────────────────────────────┘
```

## The contract

Two directions, defined in `protocol.py`:

- **Event** (engine → frontend): a fact. `assistant.delta`, `tool.result`,
  `plan.updated`, `turn.finished`. Fire and forget.
- **Request** (frontend → engine): a command, or a reply to one of the two
  events that block.

Every event carries a monotonic `seq`, so a reconnecting client can tell
whether it missed anything.

### The two blocking events

This is the part that a GUI makes genuinely harder than a terminal, and the
part worth understanding before changing anything.

A terminal agent asks `y/n` on stdin and reads the answer inline. A window
cannot: the answer arrives later, on a different thread, as a click. So
approval is a round trip:

```
worker thread                    event loop                  UI
─────────────                    ──────────                  ──
pending.open() → token
emit TOOL_PROPOSED ─────────────────────────────────────────▶ dialog opens
pending.wait(token)  ◀── parked
                                          ◀───────────────── click "Approve"
                                 pending.resolve(token, …)
wakes with "allow"
executes the tool
```

`bridge.py` is the whole mechanism: a `threading.Event` per outstanding
question, resolved by id. `ask_user` — the model's own mid-task question —
uses the identical shape.

The safety property that matters: **`resolve_all()` releases every waiter at
once**, so an interrupt, a closed window or a dropped socket can never leave
a worker thread parked on a click that will never come. And an unanswered
approval times out as a *denial*, because "no answer" is not consent.

Claude Code solves the same problem the same way — the Agent SDK's
`canUseTool` callback and the CLI's `--permission-prompt-tool` are both this
round trip, in a different language.

### Threading

The engine loop is **synchronous** and stays that way. `stream_chat` blocks
on an httpx stream; tools block on subprocesses. That code has been debugged
against real providers for months, and rewriting 6k lines of it as coroutines
would be a large risk for no user-visible gain.

So: one worker thread per session, async transport, `Pending` as the bridge.
Sessions run concurrently and independently; the transport never blocks.

## What changed in the vendored engine

Kept deliberately small, so fixes can still flow between the CLI and here:

| Module | Change | Why |
|---|---|---|
| `safety.py` | gates and path resolution take a `root` | The CLI could ask `os.getcwd()` because one process served one directory. The harness serves many workspaces from one engine, so "relative to what?" has a per-session answer. |
| `tools.py` | `execute()` / `build_system_prompt()` take `cwd` | Same reason. Also: prompt text that described a terminal now describes a window. |
| `config.py` | own config home; reads the CLI's credentials; raises instead of `sys.exit()` | This process serves every open window and must not die over one bad setting. |
| `plan.py` | `to_dict()` replaces `render()` | The plan used to draw itself with `rich` markup — precisely the coupling that made a second frontend impossible. |

Everything else — `client.py`, `router.py`, `memory.py`, `compact.py`,
`optimize.py`, `loopguard.py`, `pricing.py`, `styles.py` — is byte-for-byte
the CLI's.

## What was rebuilt rather than ported

`cli.py`'s terminal-shaped parts had no headless equivalent and were
rewritten as `session.py`:

- the hop loop and its retry ladder (network errors, retryable statuses,
  in-band errors, context-limit compaction, the non-streaming fallback)
- `handle_tool_calls` → `_handle_tool_calls`, with events in place of prints
  and a round trip in place of `confirm_tool_call`
- `_prepare_call` and `_doom_feedback`, carried over in substance: the model
  is never replayed its own malformed JSON, because doing so few-shot-primes
  it into repeating the mistake
- `start_server`, reduced to port discovery, readiness polling and an event

## The settings surface

The CLI exposes 24 slash commands. They are excellent and almost entirely
undiscoverable: you learn they exist by typing `/help` and reading a wall of
text, and you learn what they *trade off* by reading the source.

Hand-wiring each one into a GUI would describe every setting three times —
in the widget, in the validator, and in the help text — which is exactly how
a settings screen drifts away from what the engine actually does. So instead
`settings.py` declares them once:

```python
{
  "key": "route_mode", "type": "enum", "label": "Routing",
  "options": [...],
  "help": "Who chooses the model for each prompt.",
  "why":  "Smart routing spends less on easy prompts and reaches for a "
          "stronger model on hard ones, deciding locally in microseconds.",
}
```

`describe()` ships that to the frontend inside `engine.ready`; `coerce()`
validates every write. Three surfaces render the same list:

| Surface | Shows | Opens with |
|---|---|---|
| Control bar | model, routing, cost — what you change mid-session | always visible |
| Command palette | every setting, each enum expanded to one action per option | `⌘K` |
| Settings sheet | everything, grouped, with the `why` text and the model browser | `⌘,` |

Two details that matter more than they look:

- **`why` is not filler.** Most of these dials are only worth touching if you
  know what they cost you. That sentence is the difference between a
  discoverable feature and a mystery toggle.
- **Unmet dependencies are dimmed, not hidden.** The routing weights only
  apply in smart mode, but hiding them makes the feature invisible again —
  which is the problem the whole screen exists to solve.

`route.preview` answers "which model would you pick, and why?" *before* the
turn runs. The CLI could only answer that after the fact with `/route why`.

## Why the engine is a frozen binary

Mesh Code must install on a machine with no Python, no Node and no `meshapi`
CLI. Electron brings its own Node; `packaging/engine.spec` freezes the engine
— interpreter, dependencies and the routing table — into one ~28 MB directory
that the app bundle carries in `Resources/engine`.

Two things about that freeze are easy to get wrong and were both caught by
running it rather than reading it:

- **`__main__.py` must use an ABSOLUTE import.** PyInstaller runs the entry
  script with no parent package, so `from .server import main` raises
  "attempted relative import with no known parent package" — the engine dies
  before printing its handshake and the shell reports only that it never
  became ready.
- **The routing table is DATA.** Import analysis cannot see a `.json`, so it
  has to be listed in `datas` explicitly. Without it smart routing degrades
  silently to the pinned model, because routing is designed to fail open —
  nothing would ever surface the loss.

In development `main.js` spawns `python3 -m meshharness.server` instead, so a
contributor does not re-freeze on every change.

## Adding a frontend

Nothing above is desktop-specific. A web or IDE frontend needs to:

1. connect to `ws://127.0.0.1:<port>/ws?token=…`
2. render events
3. answer `tool.proposed` and `ask.user` with their tokens

That is the entire integration surface.
