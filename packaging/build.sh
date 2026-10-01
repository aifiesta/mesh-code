#!/usr/bin/env bash
# One command from a clean checkout to an installer.
#
#   ./packaging/build.sh            # current platform
#   ./packaging/build.sh mac        # or win / linux
#
# Three stages, in order — each depends on the one before it:
#   1. the UI compiles to static assets
#   2. the engine freezes into a binary that carries its own Python
#   3. electron-builder wraps both into a native installer
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET="${1:-}"

echo "==> 1/3  building the UI"
( cd ui && npm ci --silent && npm run build )

echo "==> 2/3  freezing the engine (this is what makes the app standalone)"
PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
"$PY" -m venv .build-venv
# Windows venvs put activate under Scripts/.
# shellcheck disable=SC1091
if [ -f .build-venv/Scripts/activate ]; then source .build-venv/Scripts/activate
else source .build-venv/bin/activate; fi
pip install --quiet --upgrade pip
pip install --quiet ./engine "pyinstaller>=6.6"
rm -rf packaging/engine-dist packaging/build
pyinstaller packaging/engine.spec \
  --distpath packaging/engine-dist \
  --workpath packaging/build \
  --noconfirm
deactivate

# electron-builder copies packaging/engine-dist -> Resources/engine, so flatten
# PyInstaller's extra directory level.
if [ -d packaging/engine-dist/mesh-code-engine ]; then
  mv packaging/engine-dist/mesh-code-engine packaging/engine-dist/_flat
  find packaging/engine-dist -maxdepth 1 -mindepth 1 ! -name _flat -exec rm -rf {} +
  mv packaging/engine-dist/_flat/* packaging/engine-dist/
  rmdir packaging/engine-dist/_flat
fi

# Sanity-check the freeze before wrapping it: a broken engine inside a
# signed installer is discovered by users, not by us.
echo "==> verifying the frozen engine starts"
ENGINE="packaging/engine-dist/mesh-code-engine"
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) ENGINE="$ENGINE.exe" ;; esac
[ -x "$ENGINE" ] || { echo "error: $ENGINE is missing or not executable"; exit 1; }
"$ENGINE" --port 0 > /tmp/mesh-code-freeze-check.json 2>&1 &
CHECK_PID=$!
# Poll rather than sleep a flat 8s. The FIRST launch of a freshly frozen
# binary unpacks _internal and gets a full Gatekeeper signature scan, which
# on a cold cache runs past 8s — the build then failed on a working engine.
READY=""
for _ in $(seq 1 40); do
  if grep -q '"ready": true' /tmp/mesh-code-freeze-check.json 2>/dev/null; then READY=1; break; fi
  kill -0 "$CHECK_PID" 2>/dev/null || break   # it died; stop waiting on a corpse
  sleep 1
done
if [ -n "$READY" ]; then
  echo "    engine OK"
else
  echo "error: the frozen engine did not report ready:"; cat /tmp/mesh-code-freeze-check.json
  kill "$CHECK_PID" 2>/dev/null || true; exit 1
fi
kill "$CHECK_PID" 2>/dev/null || true

echo "==> 3/3  packaging the app"
( cd desktop
  npm ci --silent
  # --publish never: with GH_TOKEN in the environment electron-builder would
  # otherwise upload straight to a release on its own.
  case "$TARGET" in
    mac)   npm run dist:mac -- --publish never ;;
    win)   npm run dist:win -- --publish never ;;
    linux) npm run dist:linux -- --publish never ;;
    *)     npm run dist -- --publish never ;;
  esac )

# Checksums the installers verify against. Without this file they warn and
# continue; with it, a corrupted or swapped download is refused.
if ls packaging/out/Mesh-Code-* >/dev/null 2>&1; then
  echo "==> writing SHA256SUMS"
  ( cd packaging/out
    if command -v shasum >/dev/null 2>&1; then shasum -a 256 Mesh-Code-*
    else sha256sum Mesh-Code-*; fi ) > packaging/out/SHA256SUMS
  cat packaging/out/SHA256SUMS
fi

echo
echo "Done. Installers are in packaging/out/"
echo "Upload them plus SHA256SUMS to the release the installers point at."
