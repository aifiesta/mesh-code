#!/bin/sh
# Mesh Code installer  —  macOS & Linux
#
#   curl -fsSL https://code.meshapi.ai/install.sh | sh
#
# Downloads the current release for your platform and installs it. Nothing
# needs to be preinstalled — the app carries its own Python and its own Node.
# Re-run any time to upgrade.
#
# Inspect before piping to a shell. This file is short on purpose.
set -eu

REPO="aifiesta/mesh-code"
BASE="${MESH_CODE_BASE_URL:-https://github.com/${REPO}/releases/latest/download}"
APP="Mesh Code"

info() { printf '\033[36m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

command -v curl >/dev/null 2>&1 || die "curl is required (install it, then re-run)."

os=$(uname -s)
arch=$(uname -m)
case "$arch" in
  arm64|aarch64) arch="arm64" ;;
  x86_64|amd64)  arch="x64" ;;
  *) die "unsupported architecture: $arch" ;;
esac

tmp=$(mktemp -d) || die "couldn't create a temp directory."
cleanup() { [ -n "${mounted:-}" ] && hdiutil detach "$mounted" -quiet 2>/dev/null || true; rm -rf "$tmp"; }
trap cleanup EXIT INT TERM

# Download THEN act — never `curl … | sh`. POSIX sh has no pipefail, so a
# failed download inside a pipe still exits 0 and the failure gets
# misdiagnosed further down.
fetch() {
  info "Downloading $1…"
  curl -fL --progress-bar "$1" -o "$2" \
    || die "download failed — check your network, or grab it manually from
  https://github.com/${REPO}/releases/latest"
}

# The checksum list doubles as the asset index: a platform with no build
# published yet gets a precise message instead of a 404 blamed on the network.
available() {
  curl -fsSL "${BASE}/SHA256SUMS" -o "$tmp/SHA256SUMS" 2>/dev/null || return 0
  if ! grep -q " $1\$" "$tmp/SHA256SUMS"; then
    have=$(awk '{print "  - " $2}' "$tmp/SHA256SUMS" | grep -v blockmap | tr '\n' '\n')
    die "no build is published yet for $os/$arch ($1).
  Builds in the current release:
$have
  Track releases at https://github.com/${REPO}/releases"
  fi
}

# Verify against the release checksums when they are published. A missing
# SHA256SUMS is a loud warning, not a silent pass.
# Sets VERIFIED=1 only when a published checksum actually MATCHED. Callers
# must not claim verification (or strip Gatekeeper on that basis) unless this
# is set — a missing SHA256SUMS is a skip, not a pass.
VERIFIED=0
verify() {
  file=$1; name=$2
  VERIFIED=0
  if ! curl -fsSL "${BASE}/SHA256SUMS" -o "$tmp/SHA256SUMS" 2>/dev/null; then
    warn "No SHA256SUMS published for this release — skipping verification."
    return 0
  fi
  want=$(grep " $name\$" "$tmp/SHA256SUMS" 2>/dev/null | awk '{print $1}' || true)
  [ -n "$want" ] || { warn "No checksum listed for $name — skipping verification."; return 0; }
  if command -v shasum >/dev/null 2>&1; then got=$(shasum -a 256 "$file" | awk '{print $1}')
  elif command -v sha256sum >/dev/null 2>&1; then got=$(sha256sum "$file" | awk '{print $1}')
  else warn "No shasum/sha256sum available — skipping verification."; return 0; fi
  [ "$want" = "$got" ] || die "checksum mismatch for $name — refusing to install.
  expected $want
  got      $got"
  VERIFIED=1
  info "Checksum verified."
}

case "$os" in
  Darwin)
    dmg="Mesh-Code-${arch}.dmg"
    available "$dmg"
    fetch "${BASE}/${dmg}" "$tmp/$dmg"
    verify "$tmp/$dmg" "$dmg"

    info "Mounting…"
    # Take the LAST FIELD of the last line. Grepping for /tmp/... matched
    # nothing when hdiutil formatted its output differently and silently
    # produced an empty mount point, which then failed further down as a
    # confusing "didn't contain the app".
    mounted=$(hdiutil attach "$tmp/$dmg" -nobrowse -mountrandom /tmp 2>/dev/null \
      | tail -1 | awk '{print $NF}')
    [ -n "$mounted" ] && [ -d "$mounted" ] || die "couldn't mount the disk image."
    src="$mounted/$APP.app"
    [ -d "$src" ] || die "the disk image didn't contain $APP.app."

    dest="/Applications"
    [ -w "$dest" ] || dest="$HOME/Applications"
    mkdir -p "$dest"
    rm -rf "$dest/$APP.app"
    cp -R "$src" "$dest/" || die "couldn't copy into $dest."
    hdiutil detach "$mounted" -quiet; mounted=""

    # Gatekeeper only enforces notarisation on QUARANTINED apps. curl does
    # not set that attribute, so the normal path here needs no warning and
    # no user action at all — and the old `spctl` check was worse than
    # useless: it always rejects an ad-hoc signature, so it fired on every
    # successful install, and it pointed at an "Open Anyway" button macOS
    # no longer offers for ad-hoc builds.
    if xattr -p com.apple.quarantine "$dest/$APP.app" >/dev/null 2>&1; then
      # Unusual — the image reached us already quarantined (a browser
      # download handed to this script, say). Stripping Gatekeeper is only
      # defensible when we ACTUALLY verified the artifact against a published
      # checksum; otherwise leave it in place and tell the user how to clear
      # it themselves, rather than silently disarming Gatekeeper on an
      # unverified download and claiming a check that never ran.
      if [ "$VERIFIED" = "1" ]; then
        info "Clearing the download quarantine flag (checksum verified above)…"
        xattr -dr com.apple.quarantine "$dest/$APP.app" 2>/dev/null || true
      else
        warn "The app arrived quarantined and could not be checksum-verified.
  Leaving Gatekeeper in place. If you trust this download, clear it with:
      xattr -dr com.apple.quarantine \"$dest/$APP.app\""
      fi
    fi
    info "Installed to $dest/$APP.app"
    open "$dest/$APP.app" 2>/dev/null || true
    ;;

  Linux)
    img="Mesh-Code-${arch}.AppImage"
    available "$img"
    fetch "${BASE}/${img}" "$tmp/$img"
    verify "$tmp/$img" "$img"

    bindir="$HOME/.local/bin"
    mkdir -p "$bindir"
    install -m 0755 "$tmp/$img" "$bindir/mesh-code" \
      || die "couldn't install into $bindir."

    apps="$HOME/.local/share/applications"
    mkdir -p "$apps"
    cat > "$apps/mesh-code.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Mesh Code
Comment=Agentic coding, with every step under your control
Exec=$bindir/mesh-code %U
Terminal=false
Categories=Development;IDE;
DESKTOP

    case ":$PATH:" in
      *":$bindir:"*) ;;
      *) warn "$bindir is not on your PATH — add it, or run $bindir/mesh-code directly." ;;
    esac
    info "Installed to $bindir/mesh-code"
    ;;

  *) die "unsupported OS: $os (Windows: use the PowerShell installer)" ;;
esac

info "Done. Launch $APP and paste your Mesh API key — get one at https://app.meshapi.ai"
