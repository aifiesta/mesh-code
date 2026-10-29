#!/usr/bin/env bash
# Fetch the llama-server build the installer bundles: ./packaging/fetch-llama.sh mac|win|linux
# The target must match the host (the extractor keeps the host's binary name and
# the build is run). Tag, file and sha256 come from the engine's manifest.
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET="${1:-}"
case "$TARGET" in
  mac)   VARIANT="darwin-arm64-metal" ;;
  win)   VARIANT="win32-x64-cpu" ;;
  linux) VARIANT="linux-x64-cpu" ;;
  *) echo "usage: $0 mac|win|linux" >&2; exit 2 ;;
esac

PY=python3; command -v python3 >/dev/null 2>&1 || PY=python
if [ -x .build-venv/bin/python ]; then PY=.build-venv/bin/python
elif [ -x .build-venv/Scripts/python.exe ]; then PY=.build-venv/Scripts/python.exe; fi

MANIFEST="engine/meshharness/local/manifest.json"
read -r TAG FILE SHA URL < <("$PY" - "$MANIFEST" "$VARIANT" <<'PYEOF'
import json, sys
m = json.load(open(sys.argv[1], encoding="utf-8"))
rt = m["runtime"]; a = rt["assets"][sys.argv[2]]
assert a.get("bundled"), f"{sys.argv[2]} is not marked bundled in the manifest"
print(rt["tag"], a["file"], a["sha256"], rt["url_template"].format(tag=rt["tag"], file=a["file"]))
PYEOF
)

CACHE="packaging/llama-cache"; DIST="packaging/llama-dist"
mkdir -p "$CACHE"
ARCHIVE="$CACHE/$FILE"
if [ ! -f "$ARCHIVE" ]; then
  echo "==> downloading llama.cpp $TAG ($VARIANT)"
  curl -fL --retry 3 --retry-delay 2 -o "$ARCHIVE.part" "$URL"
  mv "$ARCHIVE.part" "$ARCHIVE"
fi

echo "==> verifying and extracting into $DIST"
rm -rf "$DIST"
PYTHONPATH=engine "$PY" - "$ARCHIVE" "$SHA" "$DIST" "$VARIANT" "$TAG" <<'PYEOF'
import hashlib, pathlib, sys
archive, want, dist, variant, tag = sys.argv[1:]
h = hashlib.sha256()
with open(archive, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
if h.hexdigest() != want:
    pathlib.Path(archive).unlink()
    sys.exit(f"sha256 mismatch for {archive}: got {h.hexdigest()}, manifest says {want}")
from meshharness.local import runtime
server = runtime.install_from_archive(pathlib.Path(archive), pathlib.Path(dist))
(pathlib.Path(dist) / "variant").write_text(variant + "\n", encoding="utf-8")
(pathlib.Path(dist) / "tag").write_text(tag + "\n", encoding="utf-8")
print(f"    {server.name} -> {dist} ({variant})")
PYEOF

BIN="$DIST/llama-server"; [ "$TARGET" = win ] && BIN="$BIN.exe"
if ! "$BIN" --version >/dev/null 2>&1; then
  echo "error: the bundled llama-server does not run:" >&2
  "$BIN" --version 2>&1 | head -3 >&2 || true
  exit 1
fi
echo "==> $("$BIN" --version 2>&1 | head -1)"
