"""Which models and which llama-server build this app knows how to run.

The bundled manifest is trusted; a remote one may only re-pin download
facts (repo, file, sha256, size) per model and the runtime tag/assets.
Anything that reaches a filesystem path or the server argv stays bundled."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

MANIFEST_VERSION = 1
MANIFEST_URL = "https://github.com/aifiesta/mesh-code/releases/download/local-manifest/local-manifest.json"
REMOTE_TTL_S = 24 * 3600
BUNDLED = Path(__file__).with_name("manifest.json")

TRUSTED_HOSTS = frozenset({
    "huggingface.co", "hf.co",
    "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com",
})
INSECURE_LOOPBACK_OK = False   # tests flip this to serve files from 127.0.0.1 over http

_SAFE_ID = re.compile(r"^local/[a-z0-9][a-z0-9._-]{0,63}$")
_SAFE_TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_SAFE_REPO = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

_MODEL_REQUIRED = ("id", "label", "repo", "file", "sha256", "bytes", "ctx_max",
                   "kv_bytes_per_token", "quality_rank")
_ASSET_REQUIRED = ("file", "sha256", "bytes")
# Fields a remote manifest may carry per model; everything else is bundled-only.
_REMOTE_MODEL_FIELDS = frozenset(_MODEL_REQUIRED) | {"rev", "n_layer", "params_b", "active_params_b",
                                                    "family", "license", "tool_calls"}
_ARGV_FIELDS = ("server_args", "extra_server_args", "chat_template_kwargs", "url", "url_template")


class InvalidManifest(ValueError):
    pass


def load(cache_path: Path, app_version: str) -> dict:
    """The bundled manifest, overlaid with a newer validated remote copy."""
    bundled = _read(BUNDLED)
    validate(bundled, app_version, bundled=True)
    try:
        remote = _read(cache_path)
        validate(remote, app_version, bundled=False)
        if remote.get("updated", "") > bundled.get("updated", ""):
            return merge(bundled, remote)
    except Exception:          # a bad cache must never take the bundled copy down
        pass
    return bundled


def refresh(cache_path: Path, app_version: str, *, url: str = MANIFEST_URL,
            force: bool = False, timeout: float = 10.0) -> bool:
    """Fetch the remote manifest into the cache. False when nothing changed
    or the remote was unusable; never raises."""
    try:
        if not force and cache_path.exists() and time.time() - cache_path.stat().st_mtime < REMOTE_TTL_S:
            return False
        r = httpx.get(url, timeout=timeout, follow_redirects=True)
        r.raise_for_status()
        remote = r.json()
        validate(remote, app_version, bundled=False)
    except Exception:
        return False
    tmp = cache_path.with_suffix(".tmp")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(remote, indent=2))
    os.replace(tmp, cache_path)
    return True


def merge(bundled: dict, remote: dict) -> dict:
    """Overlay remote download facts onto the bundled manifest. Argv and
    URL-shaping fields always come from the bundled copy."""
    out = json.loads(json.dumps(bundled))
    out["updated"] = remote.get("updated", out.get("updated"))
    rt, rrt = out["runtime"], remote["runtime"]
    rt["tag"] = rrt["tag"]
    rt["assets"] = {k: {f: v[f] for f in _ASSET_REQUIRED} for k, v in rrt["assets"].items()}
    by_id = {m["id"]: m for m in out["models"]}
    merged = []
    for rm in remote["models"]:
        base = dict(by_id.get(rm["id"], {}))
        base.update({k: v for k, v in rm.items() if k in _REMOTE_MODEL_FIELDS})
        merged.append(base)
    out["models"] = merged
    return out


def validate(m: dict, app_version: str, *, bundled: bool) -> None:
    if not isinstance(m, dict) or m.get("manifest_version") != MANIFEST_VERSION:
        raise InvalidManifest("unsupported manifest_version")
    if not isinstance(m.get("updated", ""), str):
        raise InvalidManifest("updated must be a string")
    if not bundled and _version(m.get("min_app_version", "0")) > _version(app_version):
        raise InvalidManifest(f"needs app {m['min_app_version']}+")
    runtime = m.get("runtime") or {}
    if not isinstance(runtime.get("assets"), dict) or not runtime["assets"]:
        raise InvalidManifest("runtime block incomplete")
    if not _SAFE_TAG.match(str(runtime.get("tag", ""))):
        raise InvalidManifest("runtime tag is not a safe path segment")
    if bundled:
        if not runtime.get("url_template"):
            raise InvalidManifest("runtime block incomplete")
        _check_url(runtime["url_template"].format(tag="t", file="f"), "runtime url_template")
        _check_args(runtime.get("server_args"), "runtime server_args")
    else:
        _no_argv_fields(runtime, "runtime")
    for name, asset in runtime["assets"].items():
        _require(asset, _ASSET_REQUIRED, f"runtime asset {name}")
        _check_file(asset["file"], f"runtime asset {name}")
        _check_hash(asset["sha256"], f"runtime asset {name}")
        _check_size(asset["bytes"], f"runtime asset {name}")
    models = m.get("models") or []
    if not isinstance(models, list) or not models:
        raise InvalidManifest("no models")
    seen = set()
    for model in models:
        what = f"model {model.get('id', '?')}"
        _require(model, _MODEL_REQUIRED, what)
        if not _SAFE_ID.match(str(model["id"])) or model["id"] in seen:
            raise InvalidManifest(f"bad or duplicate model id {model['id']!r}")
        seen.add(model["id"])
        if not _SAFE_REPO.match(str(model["repo"])):
            raise InvalidManifest(f"{what} has an unsafe repo")
        if model.get("rev") is not None and not _SAFE_TAG.match(str(model["rev"])):
            raise InvalidManifest(f"{what} has an unsafe rev")
        _check_file(model["file"], what)
        _check_hash(model["sha256"], what)
        _check_size(model["bytes"], what)
        for key in ("ctx_max", "kv_bytes_per_token"):
            if not isinstance(model[key], int) or model[key] <= 0:
                raise InvalidManifest(f"{what} has a bad {key}")
        for key in ("quality_rank", "n_layer"):
            if key in model and not isinstance(model[key], int):
                raise InvalidManifest(f"{what} has a bad {key}")
        if bundled:
            if model.get("url"):
                _check_url(model["url"], what)
            _check_args(model.get("extra_server_args"), f"{what} extra_server_args")
        else:
            _no_argv_fields(model, what)


def model(m: dict, model_id: str) -> dict | None:
    return next((x for x in m["models"] if x["id"] == model_id), None)


def runtime_url(m: dict, variant: str) -> str:
    asset = m["runtime"]["assets"][variant]
    return m["runtime"]["url_template"].format(tag=m["runtime"]["tag"], file=asset["file"])


def model_url(entry: dict) -> str:
    if entry.get("url"):            # bundled-only: a mirror, or a test host
        return entry["url"]
    rev = entry.get("rev") or "main"
    return f"https://huggingface.co/{entry['repo']}/resolve/{rev}/{entry['file']}"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(obj, keys: tuple, what: str) -> None:
    if not isinstance(obj, dict):
        raise InvalidManifest(f"{what} is not an object")
    missing = [k for k in keys if obj.get(k) in (None, "")]
    if missing:
        raise InvalidManifest(f"{what} missing {missing}")


def _check_file(name, what: str) -> None:
    s = str(name)
    if not s or Path(s).name != s or s in (".", "..") or "/" in s or "\\" in s:
        raise InvalidManifest(f"{what} file is not a bare filename")


def _check_hash(value, what: str) -> None:
    if not (isinstance(value, str) and _HEX64.match(value)):
        raise InvalidManifest(f"{what} has no sha256")


def _check_size(value, what: str) -> None:
    if not isinstance(value, int) or value <= 0:
        raise InvalidManifest(f"{what} has no size")


def _check_url(url: str, what: str) -> None:
    u = urlsplit(str(url))
    host = (u.hostname or "").lower()
    if u.scheme == "http" and INSECURE_LOOPBACK_OK and host in ("127.0.0.1", "localhost"):
        return
    if u.scheme != "https":
        raise InvalidManifest(f"{what} url is not https")
    if host not in TRUSTED_HOSTS and not host.endswith(".huggingface.co") and not host.endswith(".hf.co"):
        raise InvalidManifest(f"{what} url host {host!r} is not trusted")


def _check_args(args, what: str) -> None:
    if args is None:
        return
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise InvalidManifest(f"{what} must be a list of strings")
    for a in args:
        if a in ("--host", "--port", "--api-key", "--api-key-file", "-m", "--model", "--path"):
            raise InvalidManifest(f"{what} may not set {a}")


def _no_argv_fields(obj: dict, what: str) -> None:
    present = [k for k in _ARGV_FIELDS if k in obj]
    if present:
        raise InvalidManifest(f"{what} carries bundled-only fields {present}")


def _version(v: str) -> tuple:
    out = []
    for part in str(v).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits or 0))
    return tuple(out)
