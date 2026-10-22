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
    assert cfg["route_mode"] == "off"               # smart never applies to a local pin
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
