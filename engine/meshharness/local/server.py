"""The llama-server process: start on demand, keep alive, stop on quit."""
from __future__ import annotations

import os
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

STOP_GRACE_S = 5
RESTART_DELAYS_S = (1, 4, 16)
RESTART_WINDOW_S = 600
WATCH_INTERVAL_S = 1.0
LOG_MAX_BYTES = 5 * 1024 * 1024
_LOAD_FLOOR_S = 60
_LOAD_BYTES_PER_S = 100 * 1024 * 1024   # a slow external disk paging weights in


@dataclass
class ServerInfo:
    port: int
    api_key: str
    alias: str
    ctx: int
    model_id: str


class LocalServerError(RuntimeError):
    pass


def load_timeout_s(model_bytes: int) -> float:
    return _LOAD_FLOOR_S + model_bytes / _LOAD_BYTES_PER_S


class LocalServer:
    def __init__(self, local_dir: Path, on_state=None):
        self.local_dir = local_dir
        self.log_path = local_dir / "logs" / "llama-server.log"
        self.pid_file = local_dir / "llama-server.pid"
        self.key_file = local_dir / "llama.key"
        self._on_state = on_state or (lambda *_: None)
        self._proc: subprocess.Popen | None = None
        self._info: ServerInfo | None = None
        self._argv: list[str] = []
        self._bin: Path | None = None
        self._model_bytes = 0
        self._lock = threading.RLock()
        self._stopping = False
        self._restarting = False
        self._restarts: list[float] = []
        self.state = "stopped"
        self.error: str | None = None
        self.started_at: float | None = None
        self._kill_orphan()

    def ensure(self, server_bin: Path, argv: list[str], *, model_id: str, alias: str, ctx: int,
               model_bytes: int = 0, cancel: threading.Event | None = None, notify=None,
               unless_stopping: bool = False) -> ServerInfo:
        """Return a ready server running exactly this argv, starting or
        swapping as needed. Blocks until /health answers 200."""
        with self._lock:
            if unless_stopping and self._stopping:
                raise LocalServerError("stopped before the restart")
            same = (self._info and self._proc and self._proc.poll() is None and self._argv == argv)
            if same and self.state == "ready":
                return self._info  # type: ignore[return-value]
            if not same:
                self._stop_locked(quiet=self._restarting)
                self._bin, self._argv, self._model_bytes = server_bin, list(argv), model_bytes
                port = _free_port()
                key = secrets.token_urlsafe(24)
                self._write_key(key)
                full = [str(server_bin)] + [a.replace("{port}", str(port)).replace("{key_file}", str(self.key_file))
                                            for a in argv]
                self._spawn(full)
                self._info = ServerInfo(port=port, api_key=key, alias=alias, ctx=ctx, model_id=model_id)
                self._set("starting")
                if notify:
                    notify(f"Starting the local model ({model_id})…")
            info = self._info
        self._wait_ready(info, cancel)
        return info  # type: ignore[return-value]

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def recover(self) -> None:
        """After a request to the server failed: if it died, forget it so
        the next ensure() starts a fresh one."""
        with self._lock:
            if self._proc and self._proc.poll() is not None:
                self._mark_crashed()

    def describe(self) -> dict:
        p = self._proc
        return {"state": self.state, "model": self._info.model_id if self._info else None,
                "port": self._info.port if self._info and self.state == "ready" else None,
                "ctx": self._info.ctx if self._info else None, "pid": p.pid if p else None,
                "restarts": len(self._restarts), "error": self.error, "started_at": self.started_at}

    def log_tail(self, lines: int = 40) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    def _spawn(self, argv: list[str]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.log_path.exists() and self.log_path.stat().st_size > LOG_MAX_BYTES:
            os.replace(self.log_path, self.log_path.with_suffix(".log.1"))
        kw: dict = {"stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL,
                    "cwd": str(Path(argv[0]).parent), "env": _clean_env()}
        if sys.platform == "win32":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kw["start_new_session"] = True
        try:
            with open(self.log_path, "ab") as log:
                self._proc = subprocess.Popen(argv, stdout=log, **kw)
        except OSError as e:
            raise LocalServerError(f"could not start {argv[0]}: {e}") from e
        self.pid_file.write_text(str(self._proc.pid))
        self._stopping = False
        self.error, self.started_at = None, time.time()
        threading.Thread(target=self._watch, args=(self._proc,), daemon=True,
                         name="mesh-local-watchdog").start()

    def _wait_ready(self, info: ServerInfo, cancel: threading.Event | None) -> None:
        deadline = time.monotonic() + load_timeout_s(self._model_bytes)
        url = f"http://127.0.0.1:{info.port}/health"
        while time.monotonic() < deadline:
            if cancel and cancel.is_set():
                raise LocalServerError("cancelled while the model was loading")
            with self._lock:
                proc, current = self._proc, self._info
            if current is not info:
                raise LocalServerError("the local server was restarted while loading")
            if proc is None or proc.poll() is not None:
                raise LocalServerError("llama-server exited while loading:\n" + self.log_tail())
            if self.state == "ready":
                return
            try:
                if httpx.get(url, timeout=2).status_code == 200:
                    with self._lock:
                        if self._info is info:
                            self._set("ready")
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        self.stop()
        raise LocalServerError(f"llama-server did not become ready within "
                               f"{load_timeout_s(self._model_bytes):.0f}s:\n" + self.log_tail())

    def _watch(self, proc: subprocess.Popen) -> None:
        while proc.poll() is None:
            time.sleep(WATCH_INTERVAL_S)
        with self._lock:
            if proc is not self._proc or self._stopping:
                return
            self._mark_crashed()
            now = time.monotonic()
            self._restarts = [t for t in self._restarts if now - t < RESTART_WINDOW_S]
            if len(self._restarts) >= len(RESTART_DELAYS_S):
                self._set("failed", f"llama-server keeps crashing (exit {proc.returncode}):\n" + self.log_tail())
                return
            delay = RESTART_DELAYS_S[len(self._restarts)]
            self._restarts.append(now)
            self._restarting = True
            self._set("restarting")
            server_bin, argv, info, model_bytes = self._bin, list(self._argv), self._info, self._model_bytes
        time.sleep(delay)
        try:
            self.ensure(server_bin, argv, model_id=info.model_id, alias=info.alias, ctx=info.ctx,
                        model_bytes=model_bytes, unless_stopping=True)
        except Exception as e:  # the watchdog thread must never die silently
            with self._lock:
                if not self._stopping:       # a user stop during the restart wins
                    self._set("failed", str(e))
        finally:
            self._restarting = False

    def _mark_crashed(self) -> None:
        self._set("crashed", f"llama-server exited (code {self._proc.returncode if self._proc else '?'})")
        self._proc = None
        self.pid_file.unlink(missing_ok=True)

    def _stop_locked(self, *, quiet: bool = False) -> None:
        """`quiet` is the restart path replacing its own dead process; a user
        stop always announces itself and cancels any pending restart."""
        self._stopping = True
        p = self._proc
        self._proc, self._info = None, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(STOP_GRACE_S)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(STOP_GRACE_S)
        self.pid_file.unlink(missing_ok=True)
        if self.state != "stopped" and not quiet:
            self._set("stopped")

    def _kill_orphan(self) -> None:
        """A previous engine that died hard leaves its llama-server running."""
        try:
            pid = int(self.pid_file.read_text().strip())
        except (OSError, ValueError):
            return
        if "llama-server" in _cmdline(pid):
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
                else:
                    os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        self.pid_file.unlink(missing_ok=True)

    def _write_key(self, key: str) -> None:
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(key + "\n")
            if os.name == "posix":
                os.chmod(self.key_file, 0o600)
        except OSError as e:
            raise LocalServerError(f"could not write the local server key: {e}") from e

    def _set(self, state: str, error: str | None = None) -> None:
        self.state, self.error = state, error
        try:
            self._on_state(self.describe())
        except Exception:
            pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _clean_env() -> dict:
    """PyInstaller prepends its own lib dir to the loader path; the child
    must see the user's original one or it loads the wrong libstdc++."""
    env = dict(os.environ)
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        orig = env.pop(f"{var}_ORIG", None)
        if orig is not None:
            env[var] = orig
        elif getattr(sys, "frozen", False):
            env.pop(var, None)
    return env


def _cmdline(pid: int) -> str:
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=5).stdout
            return out if str(pid) in out else ""
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""
