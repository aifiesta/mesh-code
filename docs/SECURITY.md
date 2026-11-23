# Security model

This app writes files and runs shell commands. That is its job. The design
question is not whether it can do dangerous things, but whether every
dangerous thing is either something the user chose or something they were
shown first.

## Threat model

| Adversary | Wants | Stopped by |
|---|---|---|
| A website in the user's browser | to reach the engine and run commands | Origin check + per-launch token |
| Another local process / another user on the box | same | Loopback-only bind + token |
| A prompt injection in a file, web page or command output | to make the agent exfiltrate secrets or write somewhere bad | System-prompt rule that external content is data; safety gates on paths and commands; sensitive-path denylist that applies even in bypass mode |
| A malicious or confused model | to write outside the project, read `~/.ssh`, `curl \| sh` | Same gates, plus the approval dialog showing the real diff/command |
| A shared terminal or screen | the API key or the engine token | Key stored `0600` (macOS/Linux); token on a private pipe, never a shared tty |

## The transport

`server.py` defends the socket three ways, and all three are load-bearing:

1. **Binds `127.0.0.1` only.** Never `0.0.0.0` — that would put a remote code
   execution endpoint on the user's LAN.
2. **Per-launch token.** Minted at startup, handed to the UI out of band via
   the Electron main process, required on every connection and on
   `/api/models`. Compared with `secrets.compare_digest`.
3. **Origin check.** Same-origin policy *does not apply to WebSockets*, so a
   tab on any website can open `ws://127.0.0.1:…`. Without this check a
   malicious page could try to talk to the engine; with it, a browser-supplied
   Origin must be one we recognise.

The port is ephemeral and the token is fresh each launch, so there is nothing
to guess and nothing to squat on.

## Approval and the safety gates

Permission modes decide what *skips* the dialog. The gates in `safety.py`
decide what comes *back* to it regardless of mode:

- writes resolving outside the session workspace (in accept-edits / auto)
- any path on the sensitive denylist — `~/.ssh`, `~/.aws`, credential files,
  key extensions — **including in bypass mode**
- destructive or exfiltrating command shapes: `rm -rf /`, `sudo`,
  `curl … | sh`, redirects whose target is a protected path
- reads of denylisted paths, because a read leaks the contents to the model
  provider

When a gate fires, the dialog says so explicitly — that is the most
security-relevant moment in the app, and it is styled to look like it.

An unanswered approval times out as a **denial**. "No answer" is not consent.

## The renderer

The window shows text the model produced, which may quote text from a file
or a web page. So:

- `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`
- the preload bridge exposes exactly two things: a folder picker and a menu
  subscription
- the markdown renderer never builds HTML from model output — every node is a
  React element with text as a child, so there is no `dangerouslySetInnerHTML`
  and no injection surface
- links are `http(s)`-only, so a `javascript:` or `data:` href in model
  output never becomes clickable, and they open in the real browser
- navigation away from the engine origin is blocked

## The local model

- `llama-server` binds `127.0.0.1` on a free port and requires a key minted
  per launch, written to a `0600` file (macOS/Linux) and passed with
  `--api-key-file`, so another user's process cannot borrow the model.
  Processes running as you can read the key, as they can read your files.
- Every runtime archive and model file is checked against a sha256 before
  use. A remote manifest may re-pin the download facts (repository,
  revision, file name, sha256, size, runtime tag), each model's ranking and
  sizing figures, and the model list;
  each is validated as a bare name on a trusted host (Hugging Face, GitHub).
  Server flags and URL templates always come from the bundled copy, so a
  tampered remote cannot inject server arguments or paths. Archives are
  extracted with path-escape and symlink checks.
- A server left behind by a crash is found by its pid file and stopped on
  the next launch, and only if its command line contains `llama-server`.

## Commands you type

A message that starts with `!` runs in the project folder without an
approval dialog, like the Shell tab: it is you typing, not the agent. Its
output is added to the conversation as data and is sent to whichever model
answers next, which may be a cloud one. Do not `!cat` a secret you would
not paste into a prompt.

## Secrets

- API key: `~/.mesh-code/credentials`, created `0600` (macOS/Linux) via `os.open` with
  the mode set — never write-then-chmod, which leaves a readable window.
- The key is never written to `config.json` (`save_config` strips it) and is
  sent only to the configured Mesh gateway.
- The spend ledger (`~/.mesh-code/budget.json`) is written `0600` (macOS/Linux); it holds
  amounts only, no prompts or keys.
- `base_url` must be `https://`, or `http://` for a genuinely local host —
  resolved through a URL parser, so `http://localhost.evil.com` is rejected
  rather than smuggled through as "local".

## Reporting

Found something? Please report privately rather than filing a public issue.
