"""A plain shell for the user — no model involved.

Sometimes you just want to run the command yourself: `npm install`, `git
status`, poke at why the dev server will not start. Routing that through the
agent is slow, costs tokens, and puts an approval dialog in front of your own
shell.

One persistent shell process per session, so `cd` and exported variables
survive between commands the way a real terminal does. Output streams back
line by line.

HONEST LIMITATION: this is a pipe, not a PTY. Programs that demand a terminal
— vim, top, anything drawing with ANSI cursor control, anything prompting for
a password — will not behave. Commands and their output work fine, which is
what this is for. A real PTY needs a native module in the Electron process
and a rebuild against its ABI; that is a bigger change than the need
justifies today.

Nothing here is gated by permission modes: this is the user typing into their
own shell, exactly as they could in Terminal.app. The agent's tools are the
thing that needs gating, not this.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import uuid


def _pick_shell() -> str:
    """Prefer the user's own shell — their aliases and PATH live there."""
    env_shell = os.environ.get("SHELL")
    if env_shell and os.path.exists(env_shell):
        return env_shell
    for candidate in ("/bin/zsh", "/bin/bash", "/bin/sh"):
        if os.path.exists(candidate):
            return candidate
    return shutil.which("sh") or "/bin/sh"


class Terminal:
    """One long-lived shell, fed commands on stdin."""

    # Printed after every command so we can detect completion and exit code
    # without a PTY. Random per session so nothing in real output collides.
    def __init__(self, cwd, emit) -> None:
        self.cwd = str(cwd)
        self._emit = emit
        self._marker = f"__mesh_done_{uuid.uuid4().hex[:12]}__"
        self._shell = _pick_shell()
        self._proc: subprocess.Popen | None = None
        self._pump: threading.Thread | None = None
        self._alive = threading.Event()

    # -- lifecycle ------------------------------------------------------
    def start(self) -> dict:
        if self._proc and self._proc.poll() is None:
            return self.describe()
        env = dict(os.environ, TERM="dumb", PAGER="cat", GIT_PAGER="cat",
                   PYTHONUNBUFFERED="1")
        self._proc = subprocess.Popen(
            [self._shell, "-s"],
            cwd=self.cwd, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
            start_new_session=True,
        )
        self._alive.set()
        self._pump = threading.Thread(target=self._read_loop, daemon=True,
                                      name="terminal-read")
        self._pump.start()
        return self.describe()

    def describe(self) -> dict:
        return {"shell": self._shell, "cwd": self.cwd,
                "running": bool(self._proc and self._proc.poll() is None)}

    def _read_loop(self) -> None:
        import re
        # The marker sits at the START of its own line ("<marker> <code>").
        # Anchoring here (not a substring test) stops a command that happens
        # to PRINT the marker string, or a `set -x` trace of the printf, from
        # being misread as completion; the strict exit-code regex stops a
        # stray partial line yielding a garbage code.
        pat = re.compile(r"^" + re.escape(self._marker) + r"\s+(\d+)\s*$")
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            if not self._alive.is_set():
                break
            m = pat.match(line)
            if m:
                self._safe_emit(done=True, exit_code=m.group(1))
                continue
            self._safe_emit(text=line.rstrip("\n"))
        self._safe_emit(closed=True)

    def _safe_emit(self, **data) -> None:
        try:
            self._emit(**data)
        except Exception:
            pass  # a transport hiccup must not kill the read thread

    # -- input ----------------------------------------------------------
    def run(self, command: str) -> None:
        """Send one command. Echoes it first, the way a shell prompt would."""
        if not (self._proc and self._proc.poll() is None):
            self.start()
        assert self._proc and self._proc.stdin
        cmd = (command or "").rstrip("\n")
        self._safe_emit(text=f"$ {cmd}", echo=True)
        # Wrap the command in a brace group whose stdin is /dev/null, then
        # print the marker on its OWN line. Two bugs this closes:
        #   - a command that reads stdin (`head`, `cat`, a bare `python`) used
        #     to consume the printf line itself → premature "done" with a
        #     garbage exit code, and the real completion never arrived. With
        #     </dev/null it gets immediate EOF instead (a line pipe can't
        #     deliver interactive input or ^D anyway, so this only removes a
        #     hang); `echo x | cat` still works — the pipeline has its own stdin.
        #   - `printf` on its own line (not fused after the command) means a
        #     trailing `&&`/`}` errors and still reaches the marker rather than
        #     swallowing it into a continuation.
        # `;` not `&&` so the marker prints even when the command fails.
        payload = (
            "{\n"
            f"{cmd}\n"
            "} </dev/null\n"
            f" printf '%s %s\\n' '{self._marker}' \"$?\"\n"
        )
        try:
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            self._safe_emit(text=f"shell is not accepting input ({e})", done=True,
                            exit_code="1")

    def interrupt(self) -> None:
        """Ctrl-C the foreground command without killing the shell."""
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(os.getpgid(self._proc.pid), 2)  # SIGINT
            except (OSError, ProcessLookupError):
                pass

    def close(self) -> None:
        self._alive.clear()
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM the group
            except (OSError, ProcessLookupError):
                pass
            # Reap it — an unwaited killed shell lingers as a zombie, one per
            # closed terminal.
            try:
                proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    os.killpg(os.getpgid(proc.pid), 9)
                    proc.wait(timeout=1)
                except Exception:
                    pass
        self._proc = None
