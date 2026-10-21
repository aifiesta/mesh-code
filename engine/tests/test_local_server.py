import os
import stat
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from meshharness.local import server as srv_mod

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="fake binary is a shebang script")

FAKE = Path(__file__).with_name("fake_llama_bin.py")
ARGV = ["-m", "{model_path}", "-a", "local/test", "--host", "127.0.0.1", "--port", "{port}",
        "--api-key-file", "{key_file}", "-c", "8192", "-ngl", "0"]


@pytest.fixture
def fake_bin(tmp_path):
    b = tmp_path / "llama-server"
    b.write_text(FAKE.read_text())
    b.chmod(b.stat().st_mode | stat.S_IXUSR)
    return b


@pytest.fixture
def local_dir(tmp_path):
    d = tmp_path / "local"
    d.mkdir()
    return d


def _ensure(s, fake_bin, **kw):
    return s.ensure(fake_bin, ARGV, model_id="local/test", alias="local/test", ctx=8192, **kw)


def test_starts_waits_through_loading_and_answers_with_the_key(local_dir, fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_LOAD_S", "1.5")
    states = []
    s = srv_mod.LocalServer(local_dir, on_state=lambda d: states.append(d["state"]))
    t0 = time.time()
    info = _ensure(s, fake_bin)
    assert time.time() - t0 >= 1.4 and s.state == "ready"
    assert states[:2] == ["starting", "ready"]
    r = httpx.post(f"http://127.0.0.1:{info.port}/v1/chat/completions",
                   headers={"Authorization": f"Bearer {info.api_key}"}, json={})
    assert r.status_code == 200
    assert httpx.post(f"http://127.0.0.1:{info.port}/v1/chat/completions", json={}).status_code == 401
    assert oct(s.key_file.stat().st_mode & 0o777) == "0o600"
    assert _ensure(s, fake_bin) is info          # same argv → same process
    s.stop()
    assert s.state == "stopped" and not s.pid_file.exists()


def test_two_callers_during_one_load_both_get_a_ready_server(local_dir, fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_LOAD_S", "1.5")
    s = srv_mod.LocalServer(local_dir)
    results, errors = [], []

    def go():
        try:
            info = _ensure(s, fake_bin)
            results.append((info.port, s.state, httpx.get(f"http://127.0.0.1:{info.port}/health").status_code))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
        time.sleep(0.2)
    for t in threads:
        t.join(timeout=30)
    assert not errors and len(results) == 2
    assert results[0][0] == results[1][0]            # one process, not two
    assert all(state == "ready" and code == 200 for _, state, code in results)
    s.stop()


def test_ensure_after_a_cancelled_load_waits_for_the_same_process(local_dir, fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_LOAD_S", "1.2")
    s = srv_mod.LocalServer(local_dir)
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    with pytest.raises(srv_mod.LocalServerError, match="cancelled"):
        _ensure(s, fake_bin, cancel=cancel)
    pid = s._proc.pid
    info = _ensure(s, fake_bin)
    assert s.state == "ready" and s._proc.pid == pid
    assert httpx.get(f"http://127.0.0.1:{info.port}/health").status_code == 200
    s.stop()


def test_crash_triggers_a_restart_then_gives_up_after_the_budget(local_dir, fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_CRASH_AFTER_S", "0.8")
    monkeypatch.setattr(srv_mod, "RESTART_DELAYS_S", (0.1, 0.1))
    monkeypatch.setattr(srv_mod, "WATCH_INTERVAL_S", 0.1)
    states = []
    s = srv_mod.LocalServer(local_dir, on_state=lambda d: states.append(d["state"]))
    _ensure(s, fake_bin)
    deadline = time.time() + 15
    while s.state != "failed" and time.time() < deadline:
        time.sleep(0.1)
    assert s.state == "failed", states
    assert states.count("restarting") == 2 and "keeps crashing" in (s.error or "")
    assert "stopped" not in states[:-1]           # restarts never announce a stop
    s.stop()


def test_stop_during_a_restart_is_not_blocked_by_the_load(local_dir, fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_CRASH_AFTER_S", "0.5")
    monkeypatch.setenv("FAKE_LLAMA_LOAD_S", "0")
    monkeypatch.setattr(srv_mod, "RESTART_DELAYS_S", (0.2, 0.2))
    monkeypatch.setattr(srv_mod, "WATCH_INTERVAL_S", 0.1)
    s = srv_mod.LocalServer(local_dir)
    _ensure(s, fake_bin)
    deadline = time.time() + 5
    while s.state != "restarting" and time.time() < deadline:
        time.sleep(0.02)
    monkeypatch.setenv("FAKE_LLAMA_LOAD_S", "20")   # the restarted process will load slowly
    t0 = time.time()
    s.stop()
    assert time.time() - t0 < 3
    time.sleep(0.8)
    assert s.state == "stopped" and s._proc is None


def test_recover_forgets_a_dead_process_so_ensure_restarts(local_dir, fake_bin, monkeypatch):
    monkeypatch.setattr(srv_mod, "WATCH_INTERVAL_S", 30)   # keep the watchdog out of this test
    s = srv_mod.LocalServer(local_dir)
    info = _ensure(s, fake_bin)
    os.kill(s._proc.pid, 9)
    time.sleep(0.3)
    s.recover()
    assert s.state == "crashed"
    info2 = _ensure(s, fake_bin)
    assert s.state == "ready" and info2.port != info.port
    s.stop()


def test_boot_kills_an_orphan_named_in_the_pidfile(local_dir, fake_bin):
    s = srv_mod.LocalServer(local_dir)
    _ensure(s, fake_bin)
    pid = s._proc.pid
    s._proc = None            # simulate the engine dying without stop()
    assert local_dir.joinpath("llama-server.pid").read_text() == str(pid)
    srv_mod.LocalServer(local_dir)
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            break
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_cancel_during_load_leaves_the_process_loading_for_the_next_caller(local_dir, fake_bin, monkeypatch):
    monkeypatch.setenv("FAKE_LLAMA_LOAD_S", "30")
    s = srv_mod.LocalServer(local_dir)
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    with pytest.raises(srv_mod.LocalServerError, match="cancelled"):
        _ensure(s, fake_bin, cancel=cancel)
    assert s.state == "starting"
    s.stop()
    assert s._proc is None and s.state == "stopped"


def test_a_binary_that_cannot_start_is_a_local_server_error(local_dir, tmp_path):
    not_exec = tmp_path / "llama-server"
    not_exec.write_text("not a program")
    s = srv_mod.LocalServer(local_dir)
    with pytest.raises(srv_mod.LocalServerError, match="could not start"):
        _ensure(s, not_exec)
    assert s.state == "stopped"


def test_load_timeout_scales_with_model_size():
    assert srv_mod.load_timeout_s(0) == 60
    assert srv_mod.load_timeout_s(32 * 1024 ** 3) > 300


def test_child_env_restores_the_original_loader_path(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/pyinstaller/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
    env = srv_mod._clean_env()
    assert env["LD_LIBRARY_PATH"] == "/usr/lib" and "LD_LIBRARY_PATH_ORIG" not in env
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert "LD_LIBRARY_PATH" not in srv_mod._clean_env()   # frozen with no ORIG: drop it
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert srv_mod._clean_env()["LD_LIBRARY_PATH"] == "/pyinstaller/_internal"   # dev: leave it
