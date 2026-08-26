# Mesh Code

The Mesh agent as a desktop app. It plans, writes files, runs commands and
starts dev servers inside a project folder you choose — with every step
either auto-approved by a mode you picked, or shown to you as a diff before
it happens.

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

Windows and Linux builds are not published yet; `install.ps1` is here for
when they are.

## First launch

You'll be asked for a Mesh API key ([app.meshapi.ai](https://app.meshapi.ai)).
It is stored locally, owner-readable only, and sent nowhere but the Mesh
gateway. If you already use the `meshapi` CLI, your existing key is picked up
automatically.

## What's in this repository

Just the installers. This repo hosts the install scripts and the release
artifacts — the application source is not published here.
