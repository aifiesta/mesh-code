"""The engine owns the local layer: boot, status over the socket, install →
progress → ready → merged catalog, settings, and a session that runs on it."""
import json
import stat
import sys
import time
from pathlib import Path

import pytest
from fake_gateway import sse_turn
from fake_hf import FakeFileHost
from fastapi.testclient import TestClient

from meshharness.local import manager as manager_mod
from meshharness.local.hardware import Gpu, HardwareProfile
from meshharness.protocol import Event, Request
from meshharness.server import Engine, create_app

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake binary is a shebang script")

FAKE_BIN = Path(__file__).with_name("fake_llama_bin.py")


@pytest.fixture
def host():
    h = FakeFileHost()
    yield h
    h.close()


@pytest.fixture
def local_world(tmp_path, host, monkeypatch):
    """A manifest whose only model is the fake host's blob, served by the fake binary."""
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
    monkeypatch.setattr(manager_mod, "probe",
                        lambda *_: HardwareProfile(os="darwin", arch="arm64", ram_gb=16, avail_gb=10,
                                                   cores=8, gpu=Gpu(kind="metal")))
    return manifest


@pytest.fixture
def app_client(gateway, local_world, monkeypatch):
    monkeypatch.setenv("MESHAPI_BASE_URL", gateway.base_url)
    monkeypatch.setenv("MESHAPI_API_KEY", "rsk_test")
    engine = Engine(token="secret-token")
    engine.cfg["auto_compact"] = False
    engine.cfg["repo_memory"] = False
    engine.cfg["local_auto_setup"] = True
    with TestClient(create_app(engine)) as client:
        yield client, engine
    engine.local.shutdown()


def _drain(ws, want, limit=600, pred=None):
    seen = []
    for _ in range(limit):
        ev = json.loads(ws.receive_text())
        seen.append(ev.get("type"))
        if ev.get("type") == want and (pred is None or pred(ev)):
            return ev
    raise AssertionError(f"never saw {want}; saw {seen[-30:]}")


def _installed(engine, timeout=20):
    deadline = time.time() + timeout
    while "local/tiny" not in engine.local.installed_ids() and time.time() < deadline:
        time.sleep(0.05)
    return "local/tiny" in engine.local.installed_ids()


def test_engine_boots_the_local_layer_and_installs_the_recommended_model(app_client):
    client, engine = app_client
    assert _installed(engine)                      # boot → auto-download → verify, no socket needed
    with client.websocket_connect("/ws?token=secret-token") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "engine.ready"
        local = hello["data"]["local"]
        assert local["enabled"] is True and local["models"][0]["installed"] is True
        assert local["default_model"] == "local/tiny" and local["recommended"]["model"] == "local/tiny"
        # The local model is a choice inside Smart routing, not a model to pin.
        assert all(not str(m.get("id", "")).startswith("local/") for m in hello["data"]["catalog"])


def test_a_lookup_with_smart_routing_starts_on_the_local_server(app_client, gateway, tmp_path):
    client, engine = app_client
    assert _installed(engine)
    engine.local.target_for("local/tiny")          # loaded: the turn can start local
    gateway.script(sse_turn("checked on the cloud"))
    with client.websocket_connect("/ws?token=secret-token") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": Request.OPEN_WORKSPACE, "workspace": str(tmp_path)}))
        sid = _drain(ws, Event.SESSION_STARTED)["session"]
        engine.sessions[sid].cfg["route_mode"] = "smart"
        ws.send_text(json.dumps({"type": Request.PROMPT, "session": sid,
                                 "text": "In two sentences, what does price.py do? Do not change any files."}))
        routed = _drain(ws, Event.ROUTED, pred=lambda e: e["data"].get("local"))
        stepped = _drain(ws, Event.ROUTED, pred=lambda e: e["data"].get("from_local"))
        fin = _drain(ws, Event.TURN_FINISHED)
    # The fake server answers "hello from llama" without reading a file: a
    # guess, so the turn steps up to Smart routing's cloud pick.
    assert routed["data"]["model"] == "local/tiny"
    assert "without reading" in stepped["data"]["reason"]
    assert fin["data"]["local_steps"] == 1 and fin["data"]["stepped_up_from_local"]
    assert len([r for r in gateway.requests if "messages" in r]) == 1


def test_nothing_downloads_until_the_user_says_yes(gateway, local_world, monkeypatch, host):
    monkeypatch.setenv("MESHAPI_BASE_URL", gateway.base_url)
    monkeypatch.setenv("MESHAPI_API_KEY", "rsk_test")
    engine = Engine(token="secret-token")           # local_auto_setup is off by default
    try:
        engine.local.boot()
        st = engine.local.status()
        assert st["enabled"] and st["offer"]["model"] == "local/tiny" and st["jobs"] == []
        time.sleep(0.3)
        assert engine.local.installed_ids() == set()
        engine.local.decline()
        assert engine.local.status()["offer"] is None
    finally:
        engine.shutdown()


def test_a_disabled_layer_refuses_to_download(gateway, local_world, monkeypatch):
    monkeypatch.setenv("MESHAPI_BASE_URL", gateway.base_url)
    monkeypatch.setenv("MESHAPI_API_KEY", "rsk_test")
    engine = Engine(token="secret-token")
    try:
        engine.local.boot()
        engine.local.on_setting("local_enabled", False)
        with pytest.raises(ValueError, match="turned off"):
            engine.local.install("local/tiny")
        assert engine.local.status()["offer"] is None
    finally:
        engine.shutdown()


def test_local_requests_round_trip_over_the_socket(app_client):
    client, engine = app_client
    assert _installed(engine)
    with client.websocket_connect("/ws?token=secret-token") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": Request.LOCAL_STATUS}))
        st = _drain(ws, Event.LOCAL_STATUS)
        assert st["data"]["models"][0]["installed"] is True
        ws.send_text(json.dumps({"type": Request.LOCAL_SERVER, "action": "stop"}))
        st = _drain(ws, Event.LOCAL_STATUS, pred=lambda e: e["data"]["server"]["state"] == "stopped")
        assert st["data"]["server"]["held"] is True        # a stop stays a stop
        assert engine.local.ready_target() is None and engine.local.server.state == "stopped"
        ws.send_text(json.dumps({"type": Request.LOCAL_DELETE, "model": "local/tiny"}))
        st = _drain(ws, Event.LOCAL_STATUS, pred=lambda e: not e["data"]["models"][0]["installed"])
        assert engine.local.installed_ids() == set()
        ws.send_text(json.dumps({"type": Request.LOCAL_DECLINE}))
        st = _drain(ws, Event.LOCAL_STATUS, pred=lambda e: e["data"]["declined"])
        assert st["data"]["declined"] is True


def test_local_settings_coerce_and_reach_the_layer(app_client):
    client, engine = app_client
    with client.websocket_connect("/ws?token=secret-token") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": Request.SET_CONFIG, "key": "local_context", "value": "64k"}))
        st = _drain(ws, Event.SETTINGS)
        values = {s["key"]: s["value"] for s in st["data"]["settings"]}
        assert values["local_context"] == "64k" and engine.local.cfg["local_context"] == "64k"
        ws.send_text(json.dumps({"type": Request.SET_CONFIG, "key": "local_keep_free_gb", "value": 99}))
        err = _drain(ws, Event.CONFIG_ERROR)
        assert "between" in err["data"]["message"]
        ws.send_text(json.dumps({"type": Request.SET_CONFIG, "key": "local_enabled", "value": False}))
        _drain(ws, Event.SETTINGS)
        assert engine.local.cfg["local_enabled"] is False
    from meshharness.core import config as config_mod
    saved = json.loads(config_mod.CONFIG_FILE.read_text())
    assert saved["local_context"] == "64k" and saved["local_enabled"] is False


def test_a_disabled_layer_hides_its_rows_stops_the_server_and_never_probes(app_client, monkeypatch):
    client, engine = app_client
    assert _installed(engine)
    engine.local.target_for("local/tiny")
    assert engine.local.server.state == "ready"
    with client.websocket_connect("/ws?token=secret-token") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": Request.SET_CONFIG, "key": "local_enabled", "value": False}))
        _drain(ws, Event.SETTINGS)
    deadline = time.time() + 5
    while engine.local.server.state != "stopped" and time.time() < deadline:
        time.sleep(0.05)
    assert engine.local.server.state == "stopped"
    probed = []
    monkeypatch.setattr(manager_mod, "probe", lambda *_: probed.append(1))
    fresh = manager_mod.LocalModels({"local_enabled": False}, local_dir=engine.local.local_dir / "x",
                                    app_version="0.3.0", emit=lambda *a: None, on_catalog_change=lambda: None)
    fresh.boot()
    assert probed == [] and fresh.booted.is_set() and fresh.catalog_rows() == []
    fresh.shutdown()


def test_local_model_setting_only_accepts_an_installed_model(app_client):
    client, engine = app_client
    assert _installed(engine)
    with client.websocket_connect("/ws?token=secret-token") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": Request.SET_CONFIG, "key": "local_model", "value": "local/nope"}))
        err = _drain(ws, Event.CONFIG_ERROR)
        assert "not downloaded" in err["data"]["message"] and engine.cfg.get("local_model", "") == ""
        ws.send_text(json.dumps({"type": Request.SET_CONFIG, "key": "local_model", "value": "local/tiny"}))
        _drain(ws, Event.SETTINGS)
        assert engine.cfg["local_model"] == "local/tiny"


def test_a_fresh_install_starts_with_smart_routing_on(gateway, local_world, monkeypatch):
    """Local models live inside Smart routing; with routing pinned by
    default a new user would never reach them."""
    from meshharness import settings as settings_mod
    from meshharness.core import config as config_mod
    monkeypatch.setenv("MESHAPI_BASE_URL", gateway.base_url)
    monkeypatch.setenv("MESHAPI_API_KEY", "rsk_test")
    assert not config_mod.CONFIG_FILE.exists()
    engine = Engine(token="secret-token")
    try:
        assert engine.cfg["route_mode"] == "smart" and engine.cfg["local_enabled"] is True
        assert json.loads(config_mod.CONFIG_FILE.read_text())["route_mode"] == "smart"
        assert settings_mod.BY_KEY["route_mode"]["default"] == "smart"
    finally:
        engine.shutdown()
