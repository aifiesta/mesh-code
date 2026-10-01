# Turning on signing + notarisation

Until this is done, a `.dmg` that reaches someone by AirDrop, Slack or a
browser download is **blocked by macOS** and shows *"is damaged and can't be
opened"* — with no way past it. That is Apple's rule for anything not
notarised, and no amount of ad-hoc signing changes it. The curl installer
sidesteps it (curl does not set the quarantine attribute); notarisation is
what fixes the double-click path.

Cost: an Apple Developer Program membership, $99/year.

## 1. Get a Developer ID Application certificate

developer.apple.com → Certificates → **+** → *Developer ID Application*.
Download it and double-click to add it to your login keychain. Confirm:

```sh
security find-identity -v -p codesigning
# → 1 valid identities found
#   … "Developer ID Application: Your Name (TEAMID)"
```

Today that command returns **0 valid identities**, which is why builds are
ad-hoc signed.

## 2. Create an app-specific password

appleid.apple.com → Sign-In and Security → App-Specific Passwords. This is
not your Apple ID password; notarisation will not accept that.

## 3. Restore the hardened runtime

`desktop/package.json` → `build.mac`. These were turned **off** deliberately:
hardened runtime without notarisation buys nothing and actively restricts the
frozen Python engine. With a real certificate they must come back, or
notarisation is rejected.

```json
"hardenedRuntime": true,
"entitlements": "../packaging/entitlements.mac.plist",
"entitlementsInherit": "../packaging/entitlements.mac.plist",
"identity": "Developer ID Application: Your Name (TEAMID)"
```

`packaging/entitlements.mac.plist` already grants what a frozen Python needs —
JIT, unsigned executable memory, library validation disabled. Do not trim it:
PyInstaller's bootloader will not start without those.

Leave `notarize` unset. electron-builder notarises automatically when the
credentials below are present; a hardcoded `false` would silently prevent it.

## 4. Build

```sh
export APPLE_ID="you@example.com"
export APPLE_APP_SPECIFIC_PASSWORD="xxxx-xxxx-xxxx-xxxx"
export APPLE_TEAM_ID="TEAMID"
./packaging/build.sh mac
```

`packaging/after-sign.js` detects the real identity (`CSC_NAME`/`CSC_LINK`)
and stands down, so the ad-hoc re-signing does not fight the real signature.
Notarisation adds roughly 2–5 minutes while Apple's service scans the upload.

## 5. Verify before sending it anywhere

```sh
./packaging/verify-dist.sh packaging/out/Mesh-Code-arm64.dmg
```

The line to look for changes from

```
! not notarised: a double-clicked .dmg will be BLOCKED.
```

to

```
✓ Gatekeeper accepts it — notarised, opens by double-click
```

Once that flips, `packaging/SHARING.md` and the dmg background can drop their
quarantine instructions, and the `xattr` step in `install.sh` becomes dead
code worth deleting.

## Intel builds

Notarisation does not fix architecture. The engine is a PyInstaller freeze
built for the **host** arch, so an x64 dmg must be produced on an Intel Mac
(or cross-built); building one here would ship an arm64 engine that cannot
start. See `build.mac.target` in `desktop/package.json`.
