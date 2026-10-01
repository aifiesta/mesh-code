# Sharing this build

**Send people the install command, not the `.dmg`.**

```sh
curl -fsSL https://code.meshapi.ai/install.sh | sh
```

That is the whole thing. It downloads the current build, checks it against
the published `SHA256SUMS`, installs to `/Applications`, and launches it —
with no security dialog at any point.

## Why not just send the .dmg?

Because macOS blocks it, and there is no way around that for free.

Gatekeeper enforces notarisation on any app carrying the **quarantine**
attribute, which macOS attaches based on *how the file arrived*. AirDrop,
Slack, Messages and browser downloads all set it. `curl` does not.

| How it arrives | Quarantined | Result on macOS 15+ |
|---|---|---|
| `curl` (the installer) | no | **opens normally** |
| AirDrop / Slack / browser | yes | *"is damaged and can't be opened"* |

The second dialog offers only **Move to Bin**. Sequoia removed the
right-click → Open override that used to get past it, so there is no click
path out — only the Terminal command below, or notarisation.

This is also why the `meshapi` CLI never had this problem: a command-line
binary never becomes a `.app`, so the app-launch check never runs. Mesh Code
is a GUI app, which puts it under the same rules as VS Code and Cursor —
both of which are notarised.

## If someone already has the .dmg

They can clear the flag once, in Terminal:

```sh
xattr -dr com.apple.quarantine "/Applications/Mesh Code.app"
```

Then open it normally. Only run that on software you trust the source of —
it is the step that tells macOS to stop checking. The dmg window carries
this instruction on its background so you do not have to send it separately.

## Requirements

- **Apple Silicon Mac** (M1 or later). This build will not run on Intel: the
  engine inside it is compiled for arm64, and an Intel build has to be
  produced on an Intel machine.
- macOS 11 or later.
- Nothing else. Python and Node are inside the app.
- A Mesh API key from https://app.meshapi.ai — the app asks on first launch,
  and a guided tour runs automatically.

## Before you publish a build

```sh
./packaging/verify-dist.sh packaging/out/Mesh-Code-arm64.dmg
```

It verifies the signature, simulates a quarantined transfer, launches the app
and checks the engine answers `/health`. **A build that fails this must not
be uploaded** — we shipped one whose signature was invalid, every recipient
saw "damaged", and nothing in the build caught it.

## Making all of this unnecessary

Sign and notarise with an Apple Developer ID ($99/yr) and the `.dmg` works
however it arrives — no Terminal, no instructions.
See [NOTARIZING.md](NOTARIZING.md) for the exact steps; the build config is
already staged for it.
