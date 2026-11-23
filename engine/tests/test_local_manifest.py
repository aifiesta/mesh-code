import copy
import json

import pytest

import meshharness
from meshharness.local import manifest


def _bundled():
    return json.loads(manifest.BUNDLED.read_text())


def test_bundled_manifest_validates_for_the_shipping_engine_version():
    m = _bundled()
    manifest.validate(m, meshharness.__version__, bundled=True)
    assert all(len(a["sha256"]) == 64 for a in m["runtime"]["assets"].values())
    assert all(len(x["sha256"]) == 64 and x["bytes"] > 0 for x in m["models"])
    assert manifest.load(manifest.BUNDLED.with_name("absent.json"), meshharness.__version__) == m


def _remote(updated="2999-01-01"):
    """A remote manifest: download facts only, no argv or URL fields."""
    m = _bundled()
    m["updated"] = updated
    rt = m["runtime"]
    m["runtime"] = {"tag": rt["tag"], "assets": {k: {f: v[f] for f in ("file", "sha256", "bytes")}
                                                  for k, v in rt["assets"].items()}}
    m["models"] = [{k: v for k, v in x.items()
                    if k not in ("extra_server_args", "chat_template_kwargs", "url", "request_defaults")}
                   for x in m["models"]]
    return m


def test_a_newer_remote_re_pins_download_facts_but_never_argv_or_urls(tmp_path):
    cache = tmp_path / "manifest.json"
    remote = _remote()
    remote["models"][0]["sha256"] = "a" * 64
    remote["models"][0]["bytes"] = 123
    remote["runtime"]["tag"] = "b99999"
    cache.write_text(json.dumps(remote))
    merged = manifest.load(cache, "0.3.0")
    bundled = _bundled()
    assert merged["models"][0]["sha256"] == "a" * 64 and merged["models"][0]["bytes"] == 123
    assert merged["models"][0].get("chat_template_kwargs") == bundled["models"][0].get("chat_template_kwargs")
    assert merged["runtime"]["tag"] == "b99999"
    assert merged["runtime"]["server_args"] == bundled["runtime"]["server_args"]
    assert merged["runtime"]["url_template"] == bundled["runtime"]["url_template"]


def test_an_older_or_broken_cache_is_ignored(tmp_path):
    cache = tmp_path / "manifest.json"
    cache.write_text(json.dumps(_remote(updated="2000-01-01")))
    assert manifest.load(cache, "0.3.0") == _bundled()
    cache.write_text("{not json")
    assert manifest.load(cache, "0.3.0") == _bundled()


@pytest.mark.parametrize("mutate,why", [
    (lambda m: m.update(manifest_version=99), "manifest_version"),
    (lambda m: m["models"][0].pop("sha256"), "missing"),
    (lambda m: m["models"][0].update(sha256="abc"), "no sha256"),
    (lambda m: m["models"][0].update(id="qwen"), "bad or duplicate"),
    (lambda m: m["models"][0].update(file="../../../.zshrc"), "bare filename"),
    (lambda m: m["models"][0].update(file="/etc/passwd"), "bare filename"),
    (lambda m: m["models"][0].update(repo="../x"), "unsafe repo"),
    (lambda m: m["models"][0].update(rev="../main"), "unsafe rev"),
    (lambda m: m["models"][0].update(bytes=0), "no size"),
    (lambda m: m["runtime"].update(tag="../b1"), "safe path segment"),
    (lambda m: m["runtime"]["assets"]["darwin-arm64-metal"].update(file="x/../../y.tar.gz"), "bare filename"),
    (lambda m: m["runtime"].update(assets={}), "runtime block"),
    (lambda m: m.update(updated=20261021), "updated must be"),
    (lambda m: m["models"][0].update(quality_rank="high"), "bad quality_rank"),
    (lambda m: m["models"][0].update(n_layer="many"), "bad n_layer"),
])
def test_invalid_manifests_are_rejected_whatever_their_source(mutate, why):
    for bundled, src in ((True, _bundled()), (False, _remote())):
        mutate(src)
        with pytest.raises(manifest.InvalidManifest, match=why):
            manifest.validate(src, "0.3.0", bundled=bundled)


@pytest.mark.parametrize("mutate,why", [
    (lambda m: m.update(min_app_version="9.9.0"), "needs app"),
    (lambda m: m["runtime"].update(server_args=["--host", "0.0.0.0"]), "bundled-only"),
    (lambda m: m["runtime"].update(url_template="https://evil.example/{tag}/{file}"), "bundled-only"),
    (lambda m: m["models"][0].update(url="https://huggingface.co/x/y"), "bundled-only"),
    (lambda m: m["models"][0].update(extra_server_args=["--api-key", "x"]), "bundled-only"),
    (lambda m: m["models"][0].update(chat_template_kwargs={"a": 1}), "bundled-only"),
])
def test_remote_manifests_may_not_carry_argv_url_or_version_overrides(mutate, why):
    src = _remote()
    mutate(src)
    with pytest.raises(manifest.InvalidManifest, match=why):
        manifest.validate(src, "0.3.0", bundled=False)


@pytest.mark.parametrize("mutate,why", [
    (lambda m: m["runtime"].update(server_args=["--host", "0.0.0.0"]), "may not set --host"),
    (lambda m: m["models"][0].update(extra_server_args=["--api-key", "x"]), "may not set --api-key"),
    (lambda m: m["runtime"].update(url_template="http://github.com/{tag}/{file}"), "not https"),
    (lambda m: m["runtime"].update(url_template="https://evil.example/{tag}/{file}"), "not trusted"),
    (lambda m: m["models"][0].update(url="https://evil.example/m.gguf"), "not trusted"),
])
def test_even_the_bundled_manifest_cannot_open_the_server_or_fetch_from_strangers(mutate, why):
    src = _bundled()
    mutate(src)
    with pytest.raises(manifest.InvalidManifest, match=why):
        manifest.validate(src, "0.3.0", bundled=True)


def test_bundled_manifest_is_exempt_from_the_min_version_gate_but_remote_is_not():
    m = _bundled()
    m["min_app_version"] = "9.9.0"
    manifest.validate(m, "0.1.0", bundled=True)
    with pytest.raises(manifest.InvalidManifest, match="needs app"):
        manifest.validate(_remote() | {"min_app_version": "9.9.0"}, "0.1.0", bundled=False)


def test_refresh_ignores_an_unusable_remote_and_keeps_the_cache(tmp_path, monkeypatch):
    cache = tmp_path / "manifest.json"
    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"manifest_version": 1}
    monkeypatch.setattr(manifest.httpx, "get", lambda *a, **k: _Resp())
    assert manifest.refresh(cache, "0.3.0", force=True) is False
    assert not cache.exists()


def test_refresh_writes_a_valid_remote_atomically(tmp_path, monkeypatch):
    cache = tmp_path / "sub" / "manifest.json"
    good = _remote()
    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return good
    monkeypatch.setattr(manifest.httpx, "get", lambda *a, **k: _Resp())
    assert manifest.refresh(cache, "0.3.0", force=True) is True
    assert json.loads(cache.read_text())["updated"] == "2999-01-01"
    assert not cache.with_suffix(".tmp").exists()


def test_urls_follow_the_manifest_templates():
    m = _bundled()
    assert manifest.runtime_url(m, "darwin-arm64-metal").endswith("/b11796/llama-b11796-bin-macos-arm64.tar.gz")
    assert manifest.model_url(m["models"][0]).startswith("https://huggingface.co/unsloth/")


def test_each_desktop_platform_has_a_bundled_runtime():
    """fetch-llama.sh bundles exactly these; a plan on any other variant
    downloads its build on demand."""
    assets = _bundled()["runtime"]["assets"]
    bundled = {k for k, a in assets.items() if a.get("bundled")}
    assert bundled == {"darwin-arm64-metal", "win32-x64-cpu", "linux-x64-cpu"}
    assert all(assets[k]["file"].startswith(f"llama-{_bundled()['runtime']['tag']}-bin-") for k in assets)
