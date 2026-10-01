import sys, threading, time
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fake_gateway import FakeGateway            # noqa: E402
from meshharness.core.permissions import Mode   # noqa: E402
from meshharness.session import Session         # noqa: E402


@pytest.fixture(autouse=True)
def isolate_config_home(tmp_path_factory, monkeypatch):
    """Point the engine's config home at a tmp dir for EVERY test.

    Without this, any test that saves a setting writes to the developer's
    real ~/.mesh-code — which is how a test gateway's base_url and a
    `test/model` pin once ended up in a live config and broke a real launch.

    It gets its OWN temp directory rather than a subfolder of `tmp_path`:
    tests that use `tmp_path` as a workspace would otherwise find the config
    home sitting inside the project they are listing.
    """
    from meshharness.core import config as cfg
    home = tmp_path_factory.mktemp("config-home")
    monkeypatch.setattr(cfg, "CONFIG_DIR", home)
    monkeypatch.setattr(cfg, "CONFIG_FILE", home / "config.json")
    monkeypatch.setattr(cfg, "CREDENTIALS_FILE", home / "credentials")
    monkeypatch.setattr(cfg, "SERVERS_FILE", home / "servers.json")
    monkeypatch.setattr(cfg, "TRANSCRIPTS_DIR", home / "transcripts")
    monkeypatch.setattr(cfg, "CLI_CREDENTIALS_FILE", home / "no-cli-credentials")
    monkeypatch.setattr(cfg, "USAGE_FILE", home / "usage.json")
    monkeypatch.setattr(cfg, "LEGACY_CONFIG_DIR", home / "no-legacy")
    monkeypatch.setattr(cfg, "SESSIONS_DIR", home / "sessions")
    from meshharness.core import journal as jr
    monkeypatch.setattr(jr, "JOURNAL_DIR", home / "journal")
    yield home


@pytest.fixture
def gateway():
    g = FakeGateway()
    yield g
    g.close()


class Harness:
    """A Session plus a recorded event log, with helpers to wait on it."""

    def __init__(self, session, events, lock):
        self.session, self.events, self._lock = session, events, lock

    def of(self, type_):
        with self._lock:
            return [e for e in self.events if e["type"] == type_]

    def wait_for(self, type_, timeout=10.0, min_count=1):
        deadline = time.time() + timeout
        while time.time() < deadline:
            hits = self.of(type_)
            if len(hits) >= min_count:
                return hits[min_count - 1]
            time.sleep(0.01)
        raise AssertionError(
            f"timed out waiting for {type_!r}; saw: "
            f"{[e['type'] for e in self.events]}")

    def types(self):
        with self._lock:
            return [e["type"] for e in self.events]


@pytest.fixture
def harness(gateway, tmp_path):
    made = []

    def _make(mode=Mode.DEFAULT, **cfg_over):
        events, lock = [], threading.Lock()

        def emit(ev):
            with lock:
                events.append(ev)

        cfg = {
            "base_url": gateway.base_url,
            "api_key": "rsk_test",
            "model": "test/model",
            "system": "test",
            "auto_route": False, "repo_memory": False, "fallback_models": [],
            "reasoning_effort": None, "optimize": 0.0, "max_hops": 0,
            "auto_compact": False, "stall_policy": "pause", "route_mode": "off",
            "output_style": "default",
        }
        cfg.update(cfg_over)
        s = Session("t1", tmp_path, cfg, emit, mode=mode)
        s.start()
        made.append(s)
        return Harness(s, events, lock)

    yield _make
    for s in made:
        s.close()
