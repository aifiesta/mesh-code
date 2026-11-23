"""Smart routing on for existing installs, once (24 Nov 2026)."""
import json

from fastapi.testclient import TestClient

from meshharness.core import config as cfg_mod
from meshharness.server import Engine, create_app


def _write(conf: dict):
    cfg_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg_mod.CONFIG_FILE.write_text(json.dumps(conf))


def _saved():
    return json.loads(cfg_mod.CONFIG_FILE.read_text())


def test_an_existing_pinned_install_is_switched_once_and_told():
    _write({"route_mode": "off", "model": "x/y", "max_hops": 7})
    cfg = cfg_mod.load_config()
    assert cfg["route_mode"] == "smart" and cfg["_switched_to_smart"] is True
    saved = _saved()
    assert saved["route_mode"] == "smart" and saved["model"] == "x/y" and saved["max_hops"] == 7
    assert "_switched_to_smart" not in saved and "api_key" not in saved


def test_choosing_pinned_again_afterwards_is_kept():
    _write({"route_mode": "off"})
    cfg_mod.load_config()
    _write({"route_mode": "off"})                     # the user picks Pinned again
    cfg = cfg_mod.load_config()
    assert cfg["route_mode"] == "off" and "_switched_to_smart" not in cfg


def test_other_routing_choices_and_fresh_installs_are_left_alone():
    _write({"route_mode": "auto"})
    assert cfg_mod.load_config()["route_mode"] == "auto"
    cfg_mod.MIGRATIONS_FILE.unlink()
    cfg_mod.CONFIG_FILE.unlink()
    cfg = cfg_mod.load_config()                       # fresh install: smart by default
    assert cfg["route_mode"] == "smart" and "_switched_to_smart" not in cfg


def test_the_first_window_is_told_once(monkeypatch, gateway):
    monkeypatch.setenv("MESHAPI_BASE_URL", gateway.base_url)
    monkeypatch.setenv("MESHAPI_API_KEY", "rsk_test")
    _write({"route_mode": "off"})
    engine = Engine(token="t")
    try:
        assert engine.cfg["route_mode"] == "smart" and "_switched_to_smart" not in engine.cfg
        with TestClient(create_app(engine)) as client:
            seen = []
            for _ in range(2):
                with client.websocket_connect("/ws?token=t") as ws:
                    for _ in range(40):
                        ev = json.loads(ws.receive_text())
                        seen.append(ev)
                        if ev["type"] == "notice" and ev["data"].get("app"):
                            break
                        if ev["type"] == "engine.ready" and len([e for e in seen if e["type"] == "engine.ready"]) == 2:
                            break
            notes = [e for e in seen if e["type"] == "notice" and e["data"].get("app")]
            assert len(notes) == 1 and "Pinned" in notes[0]["data"]["message"]
    finally:
        engine.shutdown()
