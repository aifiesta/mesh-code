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

## 2026-10-22 — an upgraded install moves to local-first, keeping its cloud model as the fallback
On the first launch with the local layer, a config whose `model` is a
cloud id becomes `model: local/auto` with that cloud id as
`local_fallback_model`, and a one-time notice says so. The migrated config
is written back at once so it runs once; snapshots pinned to that old model
move to `local/auto` too; Smart routing is reset to Pinned because it never
applies to a local pin. Until the download finishes every turn still runs
on the old model, so nothing breaks. A fresh install starts at `local/auto`
with no notice.

## 2026-10-22 — the gateway catalog cache stays gateway-only; local rows are merged on the way out
`Engine._catalog` keeps its retry-when-empty rule; `merged_catalog()`
prepends the local layer's synthetic zero-price rows wherever a catalog is
handed to a session, the socket or `/api/models`.


## 2026-10-22 — the key gate can be passed with a local model only, and the UI remembers that choice

Once a local model is installed, the key gate offers "Continue with the
local model only". The engine never gated on a key (the client reads the
cloud key only for cloud targets), so the bypass is a per-machine UI flag
in the browser store, not a setting: a key saved later makes it moot, and
nothing about it belongs in the config file that `save_config` strips.

## 2026-10-22 — session-less notices never enter a transcript

Events with no session id (the upgrade notice, local readiness, a failed
download) go to `appNotices`, rendered above the transcript, in the empty
state, on the key gate and inside the settings sheet. They are kept out of
session messages because HISTORY skips a session that already has any,
so a notice appended during the replay window would hide the restored
conversation.

## 2026-10-22 — one baseline llama-server per platform is bundled; GPU variants download on demand

The installer carries the Metal build on macOS and the CPU build on Windows
and Linux (16–24 MB each), fetched at build time from the manifest's pin so
the bundled binary and the one the engine would download are the same
bytes. Vulkan and later CUDA builds are tens to hundreds of MB and only
pay off on some machines, so the engine downloads them when its plan asks
for one. A machine with weights on disk therefore works offline, and the
installer grows by one small archive.

## 2026-10-22 — runtime assets are hashed at pin time; models are pinned from Hugging Face LFS metadata

llama.cpp publishes no checksums, so `packaging/pin-local-manifest.py`
downloads each release asset once and writes its sha256 and size into the
manifest. Hugging Face exposes every LFS file's sha256 and size through its
tree API, so model pins are refreshed without downloading gigabytes. Either
can be re-pinned and shipped through the remote manifest without an app
build.

## 2026-10-22 — the local router and the token-savings dial are removed in favour of Mesh Auto Routing

The CLI's bundled routing table picked a model per prompt and climbed an
effort ladder within a turn; the optimize dial pruned re-sent context.
Neither has a gateway equivalent, both duplicated what Mesh does server-side
(Auto Routing with weight profiles and exclusions, provider prompt caching),
and the table went stale every month. `route_mode` stays as off | auto until
the policy PR replaces it with a policy, so settings keep validating in
between. The refusal and claimed-work checks live on in `core/verify.py`:
they are the cheapest audit triggers there are, so a weak reply is recorded
per turn now and acted on once the auditor exists. The old `route` key
(cheapest/fastest/balanced, pre-0.5.0) is still dropped on load.

## 2026-10-22 — the policy ladder is explicit: local writes, the cloud escalates on evidence, and hands back

`core/policy.py` holds one tier per turn: local, cloud fallback, gateway
auto, pinned, or escalated. Escalation happens on the same evidence the loop
already produces (a repeated action, two failed tool batches, a refusal or
claimed work) or on the Escalate button, at most once per turn when
automatic, and hands back after 8 hops or 300k tokens with a note so the
local model continues instead of restarting. The old router escalated on a
hidden difficulty ladder nobody could see; this one names its reason in the
transcript and the control bar every time.

## 2026-10-22 — model chains ship as data, picked by benchmark, with the user's choice first

`core/policy.json` carries the audit and escalation chains chosen from the
Artificial Analysis indices cross-checked against the Mesh catalog; the
user's own pick and fallbacks go in front, exclusions come out, and the rest
of the chain rides as the gateway's `models` field so a provider outage fails
over server-side. The file can be re-pinned without touching code.

## 2026-10-22 — Harder is one effort level above the configured one, for that escalation only

"Escalate harder" raises the reasoning effort by one step over the user's
escalation effort (capped at max) and applies to the escalation it was
pressed for; a session-sticky escalation continues at the configured effort.
A fixed "xhigh" could lower a user's "max", and a sticky Harder would quietly
bill every later turn at the top rate.

## 2026-10-22 — a failed escalated hop hands back the turn but keeps a session-sticky escalation

When the strong model cannot be reached (transport error, HTTP error or an
in-band gateway error) the current turn continues on the local model with
the pending nudge intact, and the turn is not summarised as escalated unless
an escalated hop actually completed. The user's "stay escalated this
session" choice survives one bad hop; only "Back to local" clears it.

## 2026-10-22 — spend is governed client-side against a monthly limit the user sets; local work never counts

`core/budget.py` keeps a ledger under `~/.mesh-code/budget.json`: one
monthly limit (default $10), today's share of it (what was left when the day
began divided by the days left, so a quiet week raises it and a heavy one
lowers it), and what each cloud tier cost by day and month. There is no
separate daily setting and no ration of automatic escalations: both were
extra numbers to tune for one decision, and the share already bounds them. Every cloud hop asks the ledger
before it starts and records what it cost after; local hops never touch it.
A cloud hop that is stopped or fails mid-stream may already be billed, so
its pre-flight estimate is recorded, marked as an estimate. Only one copy of
the app runs at a time, so two engines never overwrite the ledger; a dev
build beside the installed app is the one case left uncovered.
Past the daily share, or on the key's HTTP 402, the escalated tier hands
back for this turn (a session-sticky escalation tries again next turn) and
other cloud turns stop with a hold the user can lift for the day; past the month everything
cloud stops and local keeps working. The control bar shows the day as a
ring and the detail as two bars, because a number in dollars does not read
at a glance and a ring does. The gateway's own HTTP 402 is treated as the
key's cap: a hold until the user says they raised it.
