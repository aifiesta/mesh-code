"""The whole local layer end to end: manifest → download → verify →
llama-server → a Target a hop can use. Real threads, real HTTP, fake model."""
import json
import stat
import sys
import time
from pathlib import Path

import httpx
import pytest
from fake_hf import FakeFileHost

from meshharness.local import manager as manager_mod
from meshharness.local.hardware import Gpu, HardwareProfile

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake binary is a shebang script")

FAKE_BIN = Path(__file__).with_name("fake_llama_bin.py")


@pytest.fixture
def host():
    h = FakeFileHost()
    yield h
    h.close()


@pytest.fixture
def layer(tmp_path, host, monkeypatch):
    """A LocalModels whose only model is the fake host's blob and whose
    llama-server is the fake binary."""
    bin_ = tmp_path / "llama-server"
    bin_.write_text(FAKE_BIN.read_text())
    bin_.chmod(bin_.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("MESH_LLAMA_SERVER", str(bin_))

    manifest = {
        "manifest_version": 1, "updated": "2026-10-21", "min_app_version": "0.3.0",
        "runtime": {"tag": "test", "url_template": "http://127.0.0.1:9/{tag}/{file}", "server_args": ["--jinja"],
                    "assets": {"darwin-arm64-metal": {"file": "x.tar.gz", "sha256": "0" * 64, "bytes": 1},
                               "linux-x64-cpu": {"file": "x.tar.gz", "sha256": "0" * 64, "bytes": 1}}},
        "models": [{"id": "local/tiny", "label": "Tiny", "repo": "x/y", "file": "tiny.gguf", "url": host.url,
                    "sha256": host.sha256, "bytes": host.size, "ctx_max": 32768,
                    "kv_bytes_per_token": 10, "quality_rank": 10, "n_layer": 4}],
    }
    bundled = tmp_path / "bundled.json"
    bundled.write_text(json.dumps(manifest))
    monkeypatch.setattr(manager_mod.manifest_mod, "BUNDLED", bundled)
    monkeypatch.setattr(manager_mod.manifest_mod, "INSECURE_LOOPBACK_OK", True)
    monkeypatch.setattr(manager_mod.manifest_mod, "refresh", lambda *a, **k: False)
    monkeypatch.setattr(manager_mod, "probe",
                        lambda *_: HardwareProfile(os="darwin", arch="arm64", ram_gb=16, avail_gb=10,
                                                   cores=8, gpu=Gpu(kind="metal")))
    events = []
    layer = manager_mod.LocalModels({"local_enabled": True, "local_auto_setup": True},
                                    local_dir=tmp_path / "local", app_version="0.3.0",
                                    emit=lambda t, d: events.append((t, d)),
                                    on_catalog_change=lambda: events.append(("catalog", None)))
    layer.events = events
    yield layer
    layer.shutdown()


def _wait(pred, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_boot_auto_downloads_the_recommended_model_and_serves_it(layer):
    layer.boot()
    assert _wait(lambda: any(t == "local.ready" for t, _ in layer.events)), [t for t, _ in layer.events]
    assert layer.installed_ids() == {"local/tiny"}
    phases = [d["phase"] for t, d in layer.events if t == "local.progress"]
    assert phases[0] == "queued" and "downloading" in phases and phases[-1] == "done"
    rows = layer.catalog_rows()
    assert rows[0]["id"] == "local/auto" and rows[0]["resolves_to"] == "local/tiny"
    assert all(r["pricing"]["prompt_usd_per_1m"] == "0" for r in rows)

    target = layer.target_for("local/auto")
    assert target.local and target.display_id == "local/tiny" and target.context_limit == 32768
    r = httpx.post(f"{target.base_url}/chat/completions",
                   headers={"Authorization": f"Bearer {target.api_key}"}, json={})
    assert r.status_code == 200
    assert layer.status()["server"]["state"] == "ready"
    assert layer.status()["probe_failed"] is False
    assert layer.target_for("local/tiny") == target        # same server, no restart


def test_target_is_unavailable_until_the_download_finishes(layer, host):
    host.disconnect_at = 10  # first attempt fails fast, retries wait → still downloading
    import meshharness.local.download as dl
    layer.cfg["local_auto_setup"] = False
    layer.boot()
    with pytest.raises(manager_mod.LocalUnavailable, match="not downloaded"):
        layer.target_for("local/auto")
    layer.install("local/tiny")
    with pytest.raises(manager_mod.LocalUnavailable, match="downloading|not downloaded"):
        layer.target_for("local/auto")
    assert _wait(lambda: "local/tiny" in layer.installed_ids(), timeout=40)


def test_delete_refuses_the_running_model_and_decline_stops_auto_setup(layer, tmp_path):
    layer.boot()
    assert _wait(lambda: "local/tiny" in layer.installed_ids())
    layer.target_for("local/tiny")
    with pytest.raises(RuntimeError, match="stop the local server"):
        layer.delete("local/tiny")
    layer.server_action("stop")
    layer.delete("local/tiny")
    assert layer.installed_ids() == set()
    assert not (tmp_path / "local" / "models" / "tiny.gguf").exists()

    layer.decline()
    fresh = manager_mod.LocalModels({"local_enabled": True, "local_auto_setup": True},
                                    local_dir=tmp_path / "local", app_version="0.3.0",
                                    emit=lambda *a: None, on_catalog_change=lambda: None)
    fresh.boot()
    assert fresh.state["declined"] and fresh.downloads.active() == []
    fresh.shutdown()


def test_disabled_layer_never_starts_anything(layer):
    layer.cfg["local_enabled"] = False
    layer.boot()
    with pytest.raises(manager_mod.LocalUnavailable, match="turned off"):
        layer.target_for("local/auto")
    assert layer.status()["server"]["state"] == "stopped"


def test_a_slow_plan_is_not_auto_installed_and_says_why(layer, monkeypatch):
    monkeypatch.setattr(manager_mod, "probe",
                        lambda *_: HardwareProfile(os="linux", arch="x64", ram_gb=16, avail_gb=10,
                                                   cores=8, gpu=Gpu(kind="none")))
    layer.boot()
    assert layer.downloads.active() == [] and layer.installed_ids() == set()
    st = layer.status()
    assert st["recommended"]["slow"] is True and st["needs_confirm"]["reason"] == "slow"
    layer.cfg["local_use_when_slow"] = True
    layer.install("local/tiny")
    assert _wait(lambda: "local/tiny" in layer.installed_ids())


def test_refresh_force_re_pins_from_a_newer_remote(layer, host, monkeypatch, tmp_path):
    layer.boot()
    assert _wait(lambda: "local/tiny" in layer.installed_ids())
    newer = json.loads(Path(manager_mod.manifest_mod.BUNDLED).read_text())
    newer["updated"] = "2999-01-01"
    newer["models"][0]["rev"] = "abc123"
    newer["models"][0]["quality_rank"] = 99        # not a download fact: ignored
    for m in newer["models"]:
        for k in ("url", "chat_template_kwargs", "extra_server_args", "request_defaults"):
            m.pop(k, None)
    newer["runtime"] = {"tag": "test", "assets": {k: {f: v[f] for f in ("file", "sha256", "bytes")}
                                                  for k, v in newer["runtime"]["assets"].items()}}

    def fake_refresh(cache_path, app_version, *, url=None, force=False, timeout=10.0):
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(newer))
        return True
    monkeypatch.setattr(manager_mod.manifest_mod, "refresh", fake_refresh)
    assert layer.refresh(force=True) is True
    assert layer.manifest["models"][0]["rev"] == "abc123"
    assert layer.manifest["models"][0]["quality_rank"] != 99
    assert layer.manifest["models"][0]["url"] == host.url          # bundled-only fields survive the overlay
    assert "local/tiny" in layer.installed_ids()


def test_declined_or_blocked_downloads_are_a_permanent_reason(layer, monkeypatch):
    layer.cfg["local_auto_setup"] = False
    layer.boot()
    with pytest.raises(manager_mod.LocalUnavailable) as e:
        layer.target_for("local/auto")
    assert e.value.permanent is False                       # a download may still be started
    layer.decline()
    with pytest.raises(manager_mod.LocalUnavailable) as e:
        layer.target_for("local/auto")
    assert e.value.permanent is True


def test_opting_in_to_slow_starts_the_install_the_gate_was_blocking(layer, monkeypatch):
    monkeypatch.setattr(manager_mod, "probe",
                        lambda *_: HardwareProfile(os="linux", arch="x64", ram_gb=16, avail_gb=10,
                                                   cores=8, gpu=Gpu(kind="none")))
    layer.boot()
    assert layer.downloads.active() == []
    layer.on_setting("local_use_when_slow", True)
    assert _wait(lambda: "local/tiny" in layer.installed_ids())


def test_a_failed_boot_is_a_permanent_reason(layer, monkeypatch):
    monkeypatch.setattr(manager_mod, "probe", lambda *_: (_ for _ in ()).throw(RuntimeError("no sysctl")))
    layer.boot()
    assert layer.booted.is_set() and "no sysctl" in layer.state["boot_error"]
    with pytest.raises(manager_mod.LocalUnavailable) as e:
        layer.target_for("local/auto")
    assert e.value.permanent is True and "failed to start" in str(e.value)


def test_a_comfortable_installed_model_beats_downloading_the_recommendation(layer, monkeypatch):
    from types import SimpleNamespace as NS
    plans = {"local/big": NS(model={"id": "local/big", "quality_rank": 30}, tight=False, slow=False),
             "local/mid": NS(model={"id": "local/mid", "quality_rank": 20}, tight=False, slow=False),
             "local/huge": NS(model={"id": "local/huge", "quality_rank": 40}, tight=True, slow=False)}
    monkeypatch.setattr(layer, "_recommend", lambda: plans["local/big"])
    monkeypatch.setattr(layer, "_plan_for", lambda mid: plans.get(mid))
    layer.state["installed"] = {"local/huge": "x"}
    assert layer.default_id() == "local/big", "a tight install does not stand in for the recommendation"
    layer.state["installed"] = {"local/huge": "x", "local/mid": "y"}
    assert layer.default_id() == "local/mid"


def test_ready_target_never_blocks_and_loads_in_the_background(layer):
    """While the model is downloaded but not loaded, Smart routing gets None
    (the turn goes to the cloud) and the load starts behind it."""
    layer.boot()
    assert _wait(lambda: "local/tiny" in layer.installed_ids())
    assert layer.server.state == "stopped"
    t0 = time.time()
    assert layer.ready_target() is None
    assert time.time() - t0 < 1.0
    assert _wait(lambda: layer.server.state == "ready")
    target = layer.ready_target()
    assert target is not None and target.local and target.context_limit == 32768
    assert layer.context_window() == 32768


def test_a_stop_from_the_app_holds_and_start_loads_again(layer):
    layer.boot()
    assert _wait(lambda: "local/tiny" in layer.installed_ids())
    layer.target_for("local/tiny")
    layer.server_action("stop")
    assert layer.server.state == "stopped" and layer.status()["server"]["held"]
    assert layer.ready_target() is None
    time.sleep(0.5)
    assert layer.server.state == "stopped", "a stop must stay a stop"
    layer.server_action("start")
    assert layer.server.state == "ready" and not layer.server.held


def test_the_idle_unload_frees_the_process_and_the_next_use_reloads(layer, monkeypatch):
    layer.boot()
    assert _wait(lambda: "local/tiny" in layer.installed_ids())
    layer.target_for("local/tiny")
    proc = layer.server._proc
    layer.cfg["local_idle_unload_min"] = 1
    layer.server.last_used -= 120
    assert layer.server.unload_if_idle(60)
    assert proc.poll() is not None and layer.server.state == "stopped" and not layer.server.held
    assert layer.ready_target() is None                      # loads again in the background
    assert _wait(lambda: layer.server.state == "ready")


def test_no_download_without_a_yes_and_none_when_off(layer):
    layer.cfg["local_auto_setup"] = False
    layer.boot()
    st = layer.status()
    assert st["offer"] and st["offer"]["model"] == "local/tiny" and st["jobs"] == []
    layer.on_setting("local_enabled", False)
    assert layer.status()["offer"] is None
    with pytest.raises(ValueError, match="turned off"):
        layer.install("local/tiny")


def test_a_download_that_would_fill_the_disk_is_refused(layer, monkeypatch):
    layer.cfg["local_auto_setup"] = False
    layer.boot()
    from collections import namedtuple
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(manager_mod.shutil, "disk_usage", lambda p: Usage(10, 10, 100))
    with pytest.raises(ValueError, match="free some space"):
        layer.install("local/tiny")


def test_quitting_while_the_model_loads_in_the_background_leaves_nothing_running(layer, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_LOAD_S", "2")
    layer.boot()
    assert _wait(lambda: "local/tiny" in layer.installed_ids())
    assert layer.ready_target() is None                      # starts the background load
    assert _wait(lambda: layer.server._proc is not None, 5)
    proc = layer.server._proc
    layer.shutdown()
    time.sleep(1.0)
    assert proc.poll() is not None and layer.server._proc is None
    assert layer.ready_target() is None and layer.server._proc is None


def test_a_model_too_big_for_this_machine_says_so_not_that_it_is_missing(layer, monkeypatch):
    layer.cfg["local_auto_setup"] = False
    layer.boot()
    monkeypatch.setattr(layer, "_plan_for", lambda mid: None)   # does not fit here
    with pytest.raises(manager_mod.LocalUnavailable, match="does not fit in memory on this machine"):
        layer.target_for("local/tiny")
