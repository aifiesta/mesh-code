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
