#!/usr/bin/env bash
# Check a built dmg the way a recipient will experience it.
#
#   ./packaging/verify-dist.sh packaging/out/Mesh-Code-arm64.dmg
#
# We shipped a build whose signature was invalid — every copy reported
# "damaged and can't be opened", and nothing in the build caught it. This is
# the gate that would have. Run it before uploading a release.
set -euo pipefail

DMG="${1:-packaging/out/Mesh-Code-arm64.dmg}"
APP_NAME="Mesh Code"
[ -f "$DMG" ] || { echo "error: no such dmg: $DMG" >&2; exit 1; }

ok()   { printf '\033[32m  ✓ %s\033[0m\n' "$*"; }
bad()  { printf '\033[31m  ✗ %s\033[0m\n' "$*" >&2; FAILED=1; }
info() { printf '\033[36m==> %s\033[0m\n' "$*"; }
FAILED=0

STAGE=$(mktemp -d)
MOUNTED=""
cleanup() {
  [ -n "$MOUNTED" ] && hdiutil detach "$MOUNTED" -quiet 2>/dev/null || true
  pkill -f "$STAGE/$APP_NAME.app" 2>/dev/null || true
  rm -rf "$STAGE"
}
trap cleanup EXIT INT TERM

info "mounting $DMG"
MOUNTED=$(hdiutil attach "$DMG" -nobrowse -mountrandom /tmp 2>/dev/null | tail -1 | awk '{print $NF}')
[ -n "$MOUNTED" ] && [ -d "$MOUNTED" ] || { bad "could not mount the dmg"; exit 1; }
[ -d "$MOUNTED/$APP_NAME.app" ] || { bad "dmg does not contain $APP_NAME.app"; exit 1; }

info "signature"
if codesign --verify --deep --strict "$MOUNTED/$APP_NAME.app" 2>/dev/null; then
  ok "valid (deep, strict)"
else
  bad "INVALID — recipients will see \"is damaged and can't be opened\""
  codesign --verify --deep --strict "$MOUNTED/$APP_NAME.app" 2>&1 | sed 's/^/    /' >&2
fi
IDENT=$(codesign -dv "$MOUNTED/$APP_NAME.app" 2>&1 | sed -n 's/^Identifier=//p')
[ "$IDENT" = "ai.meshapi.code" ] && ok "identifier $IDENT" \
  || bad "identifier is '$IDENT' (expected ai.meshapi.code)"

info "bundled payload"
for f in "Contents/Resources/engine/mesh-code-engine" "Contents/Resources/ui/index.html"; do
  [ -e "$MOUNTED/$APP_NAME.app/$f" ] && ok "$f" || bad "missing $f"
done
TABLE=$(find "$MOUNTED/$APP_NAME.app/Contents/Resources/engine" -name routing_table.json | wc -l | tr -d ' ')
[ "$TABLE" = "1" ] && ok "routing_table.json bundled" || bad "routing table missing (smart routing degrades silently)"

info "simulating a real transfer (quarantine, as a browser/AirDrop sets it)"
cp -R "$MOUNTED/$APP_NAME.app" "$STAGE/"
hdiutil detach "$MOUNTED" -quiet; MOUNTED=""
xattr -w com.apple.quarantine "0083;00000000;Verify;" "$STAGE/$APP_NAME.app"
codesign --verify --deep --strict "$STAGE/$APP_NAME.app" 2>/dev/null \
  && ok "signature survives transfer" || bad "signature broken after copy"
if spctl -a -t exec "$STAGE/$APP_NAME.app" >/dev/null 2>&1; then
  ok "Gatekeeper accepts it — notarised, opens by double-click"
else
  printf '\033[33m  ! not notarised: a double-clicked .dmg will be BLOCKED.\033[0m\n'
  printf '\033[33m    Recipients must install via the curl one-liner, which\033[0m\n'
  printf '\033[33m    does not set quarantine. See packaging/SHARING.md.\033[0m\n'
fi

info "launching (quarantine cleared, as the installer does)"
xattr -dr com.apple.quarantine "$STAGE/$APP_NAME.app"
open -a "$STAGE/$APP_NAME.app"
PORT=""
for _ in $(seq 1 30); do
  PORT=$(lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | grep -i mesh-code \
          | grep -o '127\.0\.0\.1:[0-9]*' | head -1 | cut -d: -f2 || true)
  [ -n "$PORT" ] && break
  sleep 1
done
if [ -n "$PORT" ] && curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  ok "engine came up on :$PORT and answered /health"
else
  bad "app did not start a working engine"
fi
pkill -f "$STAGE/$APP_NAME.app" 2>/dev/null || true

echo
if [ "$FAILED" = "1" ]; then
  printf '\033[31mNOT SHIPPABLE — fix the above before uploading.\033[0m\n'; exit 1
fi
printf '\033[32mShippable.\033[0m  Checksum line for the release:\n\n'
shasum -a 256 "$DMG"
