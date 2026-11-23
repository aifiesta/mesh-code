"""An install from before the local layer moves to local-first exactly once."""
import json

from meshharness.core import config as cfg_mod


def _write(obj: dict) -> None:
    cfg_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg_mod.CONFIG_FILE.write_text(json.dumps(obj))


def test_an_upgraded_cloud_pin_becomes_the_fallback_and_is_persisted():
    _write({"model": "anthropic/claude-sonnet-4.5", "route_mode": "smart", "max_hops": 7})
    cfg = cfg_mod.load_config()
    assert cfg["model"] == "local/auto"
    assert cfg["local_fallback_model"] == "anthropic/claude-sonnet-4.5"
    assert cfg["_migrated_to_local"] == "anthropic/claude-sonnet-4.5"
    assert cfg["policy"] == "local-first" and "route_mode" not in cfg
    assert cfg["max_hops"] == 7                      # everything else survives
    on_disk = json.loads(cfg_mod.CONFIG_FILE.read_text())
    assert on_disk["model"] == "local/auto" and on_disk["local_fallback_model"] == "anthropic/claude-sonnet-4.5"
    assert "_migrated_to_local" not in on_disk
    again = cfg_mod.load_config()
    assert "_migrated_to_local" not in again         # second launch: no migration, no notice


def test_a_fresh_install_starts_local_with_no_notice():
    cfg = cfg_mod.load_config()
    assert cfg["model"] == "local/auto" and "_migrated_to_local" not in cfg
    assert cfg["local_fallback_model"] == cfg_mod.DEFAULT_CONFIG["local_fallback_model"]


def test_a_config_already_on_a_local_model_is_left_alone():
    _write({"model": "local/qwen3.5-9b-q4"})
    cfg = cfg_mod.load_config()
    assert cfg["model"] == "local/qwen3.5-9b-q4" and "_migrated_to_local" not in cfg


def test_an_empty_model_moves_to_local_with_the_default_fallback():
    _write({"model": ""})
    cfg = cfg_mod.load_config()
    assert cfg["model"] == "local/auto" and "_migrated_to_local" not in cfg
    assert cfg["local_fallback_model"] == cfg_mod.DEFAULT_CONFIG["local_fallback_model"]


def test_a_corrupt_config_is_never_overwritten_by_the_migration():
    cfg_mod.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    cfg_mod.CONFIG_FILE.write_text('{"model": "anthropic/claude-sonnet-4.5", "max_hops": ')   # truncated write
    before = cfg_mod.CONFIG_FILE.read_bytes()
    cfg = cfg_mod.load_config()
    assert cfg["model"] == "local/auto" and "_migrated_to_local" not in cfg   # defaults in memory only
    assert cfg_mod.CONFIG_FILE.read_bytes() == before                           # the file is left for the user to fix


def test_a_hand_edited_key_reaches_the_credentials_file_despite_the_migration():
    _write({"model": "anthropic/claude-sonnet-4.5", "api_key": "rsk_legacy_key"})
    cfg = cfg_mod.load_config()
    assert cfg["api_key"] == "rsk_legacy_key"
    assert cfg_mod.CREDENTIALS_FILE.read_text().strip() == "rsk_legacy_key"
    assert "api_key" not in json.loads(cfg_mod.CONFIG_FILE.read_text())


def test_a_local_config_with_the_dead_router_keys_loses_them_on_save():
    _write({"model": "local/qwen3.5-9b-q4", "route_mode": "smart", "optimize": 0.8,
            "route_weights": {"cost": 1}, "route_effort": "max", "local_fallback_model": "x/y"})
    cfg = cfg_mod.load_config()
    assert cfg["policy"] == "local-first" and "route_mode" not in cfg
    assert not {"optimize", "route_weights", "route_effort"} & set(cfg)
    cfg_mod.save_config(cfg)
    on_disk = json.loads(cfg_mod.CONFIG_FILE.read_text())
    assert not {"optimize", "route_weights", "route_effort", "route_mode"} & set(on_disk)
    assert on_disk["policy"] == "local-first"


def test_route_mode_auto_becomes_the_cloud_auto_policy_and_a_cloud_pin_stays_pinned():
    _write({"model": "local/auto", "route_mode": "auto", "local_fallback_model": "x/y"})
    assert cfg_mod.load_config()["policy"] == "cloud-auto"
    _write({"model": "anthropic/claude-sonnet-5.5", "route_mode": "off", "local_fallback_model": "x/y"})
    assert cfg_mod.load_config()["policy"] == "pinned"


def test_a_hand_picked_local_model_becomes_the_local_pick_under_local_first():
    _write({"model": "local/qwen3.5-9b-q4", "local_fallback_model": "x/y"})
    cfg = cfg_mod.load_config()
    assert cfg["policy"] == "local-first" and cfg["local_model"] == "local/qwen3.5-9b-q4"
