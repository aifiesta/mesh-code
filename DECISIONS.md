# Decisions

Short records of choices that are not obvious from the code.

## 2026-10-01 — source lives in this public repo
The installers were already served from here and `install.sh` points at
this repo's releases. Keeping source alongside them means free CI minutes,
no cross-repo token, and one place to look. Internal session notes
(`CHANGELOG.md`, `HANDOVER.md`, `docs/internal/`) stay gitignored.

## 2026-10-01 — installers are built by a CI matrix, never cross-built
The engine is a PyInstaller freeze and only runs on the platform that
built it. `release.yml` builds on macOS arm64, Ubuntu x64 and Windows x64
and attaches everything to a draft release; publishing is a human click.
`electron-builder` runs with `--publish never` so a `GH_TOKEN` in the
environment can never make it upload on its own.

## 2026-10-01 — a missing platform build is reported by name
Both installers read `SHA256SUMS` before downloading and list the builds
that exist when theirs is absent. The previous "check your network" on a
404 sent a Linux user chasing DNS.

## 2026-10-01 — lock files are generated with peer deps enforced
The authoring machine has `legacy-peer-deps=true` in `~/.npmrc`, which
drops electron-builder's platform peers (squirrel-windows, dmg-license)
from the lock; `npm ci` on a default-configured runner then refuses it.
Regenerate with `npx npm@10.9 install --package-lock-only
--legacy-peer-deps=false` when `desktop/package.json` changes.

## 2026-10-21 — tests run in ci on three oses and a frozen engine must boot before any release builds
`release.yml` used to build installers from whatever tree carried the tag;
the 64-test suite only ever ran on the authoring machine. `test.yml` now
runs the engine tests on macOS, Ubuntu and Windows, builds the UI, checks
that `protocol.py` and `types.ts` name the same events, and boots a
PyInstaller freeze through its WebSocket. The release workflow calls it
first. Windows cannot block yet because the suite still has POSIX-only
paths; it runs with `continue-on-error` until it is green.

## 2026-10-21 — the local runtime is a bundled llama-server; weights are downloaded, never shipped
`llama-server` (MIT, ~16–37 MB per platform) rides in the app bundle so a
fresh install can run a model offline once the weights exist. The weights
themselves are 3–30 GB and change monthly, so they are fetched on first
run — resumable, sha256-verified — into `~/.mesh-code/local/models/`.
Ollama would have meant a second install and no control over the chat
template flags tool calling depends on.

## 2026-10-21 — the hardware tier picks the default local model
The strongest manifest model that fits in RAM minus 4 GB (and within 75 %
of unified memory on Apple Silicon) at a 32k context wins; the context
then climbs while it still fits. A machine where nothing fits at 32k gets
the smallest model at 8k, marked slow, and the app keeps using the cloud
unless the user opts in. The rule is a table test, not a RAM lookup, so
new models slot in by editing the manifest.

## 2026-10-21 — the model manifest is data, refreshed remotely, every artifact hashed
`engine/meshharness/local/manifest.json` pins each model to a Hugging
Face repo, file, size and sha256, and each llama.cpp asset to a tag and
sha256 (llama.cpp publishes no checksums; we hash the release assets
ourselves). A remote copy on a rolling release tag can re-pin a model
without an app release. A remote copy may only re-pin download facts
(repo, file, size, sha256, runtime tag and assets) for https URLs on
huggingface.co or github.com; anything that becomes a filesystem path or a
llama-server argument comes from the bundled copy, which always validates.

## 2026-10-21 — tool calls printed as text are promoted, never guessed
Small models sometimes emit `<tool_call>` JSON, Qwen's `<function=…>`
XML or Cohere's action block as plain text. `core/textcalls.py` turns
those into structured calls only when the tool name is known and the
arguments parse; anything inside a code fence is treated as quoted
material, and unknown names are left for the model to retry.

## 2026-10-22 — a hop goes to a Target, and the config stays the cloud default
`client.py` takes an optional per-hop `Target` (base URL, key, model alias,
context, timeouts, request defaults) instead of learning about local
servers. The session resolves `local/*` ids through the local layer right
before each hop; a local id that cannot be served yet falls back to
`local_fallback_model` with one notice per reason, so a download in
progress never blocks a turn. Local hops cost $0 and are never priced.

## 2026-10-22 — small local models get a compact system prompt at request time
The full prompt is swapped for `local/prompt.py`'s short one only in the
request sent to a local target; history keeps the full prompt, so a session
can move between a local and a cloud model mid-conversation. Tool calls a
local model prints as text are promoted (never a cloud model's: quoted
text stays text); an unparseable one gets a single nudge per turn.

