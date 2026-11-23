#!/usr/bin/env python3
"""Re-pin the local-model manifest without an app build.

  packaging/pin-local-manifest.py --runtime b11796     # hash llama.cpp's assets
  packaging/pin-local-manifest.py --models             # refresh GGUF sizes/sha256 from Hugging Face

llama.cpp publishes no checksums, so the runtime assets are downloaded and
hashed here; Hugging Face exposes each LFS file's sha256 and size through its
tree API, so models are pinned without downloading them.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

MANIFEST = Path(__file__).resolve().parents[1] / "engine/meshharness/local/manifest.json"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mesh-code-pin/1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _sha256_of_url(url: str) -> tuple[str, int]:
    h, size = hashlib.sha256(), 0
    req = urllib.request.Request(url, headers={"User-Agent": "mesh-code-pin/1"})
    with urllib.request.urlopen(req, timeout=120) as r:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            h.update(chunk); size += len(chunk)
    return h.hexdigest(), size


def pin_runtime(m: dict, tag: str) -> None:
    rt = m["runtime"]
    rt["tag"] = tag
    for variant, asset in rt["assets"].items():
        asset["file"] = asset["file"].replace(_tag_in(asset["file"]), tag)
        url = rt["url_template"].format(tag=tag, file=asset["file"])
        print(f"  {variant}: hashing {url}", file=sys.stderr)
        asset["sha256"], asset["bytes"] = _sha256_of_url(url)


def _tag_in(file: str) -> str:
    # llama-<tag>-bin-<platform>.<ext>
    return file.split("-")[1]


def pin_models(m: dict) -> None:
    for entry in m["models"]:
        repo, rev, file = entry["repo"], entry.get("rev") or "main", entry["file"]
        tree = json.loads(_get(f"https://huggingface.co/api/models/{repo}/tree/{rev}/{Path(file).parent}"
                               .rstrip("/.")))
        hit = next((t for t in tree if t.get("path") == file), None)
        if not hit or "lfs" not in hit:
            sys.exit(f"{entry['id']}: {file} not found as an LFS file in {repo}@{rev}")
        entry["sha256"], entry["bytes"] = hit["lfs"]["oid"], int(hit["lfs"]["size"])
        print(f"  {entry['id']}: {entry['bytes']} bytes {entry['sha256'][:12]}…", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runtime", metavar="TAG", help="llama.cpp release tag to pin")
    ap.add_argument("--models", action="store_true", help="refresh every model's size and sha256")
    ap.add_argument("--updated", help="value for the manifest's `updated` date (default: today)")
    args = ap.parse_args()
    if not args.runtime and not args.models:
        ap.error("nothing to do: pass --runtime TAG and/or --models")
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if args.runtime:
        pin_runtime(m, args.runtime)
    if args.models:
        pin_models(m)
    m["updated"] = args.updated or datetime.date.today().isoformat()
    fd, tmp = tempfile.mkstemp(dir=MANIFEST.parent, suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(m, indent=1) + "\n")
    os.replace(tmp, MANIFEST)
    os.chmod(MANIFEST, 0o644)
    print(f"wrote {MANIFEST}", file=sys.stderr)


if __name__ == "__main__":
    main()
