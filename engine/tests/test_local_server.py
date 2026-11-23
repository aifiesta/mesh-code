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


def test_a_process_that_exits_on_its_own_is_not_restarted(local_dir, fake_bin, monkeypatch):
    """A crash, or the user killing it to get the memory back: it stays
    stopped until Start, instead of being started again at once."""
    monkeypatch.setattr(srv_mod, "WATCH_INTERVAL_S", 0.1)
    states = []
    s = srv_mod.LocalServer(local_dir, on_state=lambda d: states.append(d["state"]))
    _ensure(s, fake_bin)
    os.kill(s._proc.pid, 9)
    deadline = time.time() + 5
    while s.state != "crashed" and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(0.5)
    assert s.state == "crashed" and s._proc is None and s.held
    assert "starting" not in states[states.index("crashed"):]
    with pytest.raises(srv_mod.LocalServerError, match="Start"):
        _ensure(s, fake_bin)
    s.release()
    _ensure(s, fake_bin)
    assert s.state == "ready"
    s.stop()


def test_a_user_stop_holds_until_start_but_an_idle_unload_does_not(local_dir, fake_bin):
    s = srv_mod.LocalServer(local_dir)
    _ensure(s, fake_bin)
    s.stop()
    assert s.state == "stopped" and s.held
    with pytest.raises(srv_mod.LocalServerError):
        _ensure(s, fake_bin)
    s.release()
    _ensure(s, fake_bin)
    s.stop(hold=False)
    assert not s.held
    _ensure(s, fake_bin)                  # the next use loads it again
    assert s.state == "ready"
    s.stop()


def test_idle_unload_stops_the_process_and_frees_it(local_dir, fake_bin):
    s = srv_mod.LocalServer(local_dir)
    _ensure(s, fake_bin)
    proc = s._proc
    assert not s.unload_if_idle(3600)                 # used just now
    s.begin_use()
    s.last_used -= 10
    assert not s.unload_if_idle(5)                    # a hop is in flight
    s.end_use()
    s.last_used -= 10
    assert s.unload_if_idle(5)
    assert s.state == "stopped" and not s.held and proc.poll() is not None
    assert not s.unload_if_idle(5)                    # nothing left to unload


def test_recover_forgets_a_dead_process_and_holds_it(local_dir, fake_bin, monkeypatch):
    monkeypatch.setattr(srv_mod, "WATCH_INTERVAL_S", 30)   # keep the watchdog out of this test
    s = srv_mod.LocalServer(local_dir)
    info = _ensure(s, fake_bin)
    os.kill(s._proc.pid, 9)
    time.sleep(0.3)
    s.recover()
    assert s.state == "crashed" and s.held
    s.release()
    info2 = _ensure(s, fake_bin)
    assert s.state == "ready" and info2.port != info.port
    s.stop()


def test_boot_kills_an_orphan_named_in_the_pidfile(local_dir, fake_bin):
    s = srv_mod.LocalServer(local_dir)
    _ensure(s, fake_bin)
    proc = s._proc
    s._proc = None            # simulate the engine dying without stop()
    assert local_dir.joinpath("llama-server.pid").read_text() == str(proc.pid)
    srv_mod.LocalServer(local_dir)
    # Reaping through the test's own handle: on Linux an unreaped child still answers kill(pid, 0).
    assert proc.wait(timeout=5) == 0     # the fake exits 0 on SIGTERM


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
