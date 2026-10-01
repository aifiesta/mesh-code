"""Round-trip primitive: let the sync agent loop wait on a UI answer.

The engine's turn logic is synchronous — `stream_chat` blocks on an httpx
stream, tools block on subprocesses. That is worth preserving exactly as
the CLI wrote it, because it is the part that has been debugged against
real providers for months. So the harness runs each session's loop on its
own worker thread and keeps the transport async, rather than rewriting
6k lines of engine as coroutines.

That leaves one problem: two events (a tool approval, an ask_user
question) need an answer BEFORE the loop can continue, and the answer
arrives on the event loop thread. `Pending` is the handoff — the worker
parks on a threading.Event, the transport resolves it by id, the worker
wakes up with the value.

Interrupt-safe by construction: `resolve_all` unblocks every waiter with a
fallback answer, so aborting a turn or dropping a websocket can never
strand a thread waiting for a click that will never come.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any


class Cancelled(Exception):
    """The wait was abandoned — turn interrupted or client disconnected."""


class _Slot:
    __slots__ = ("event", "value", "cancelled")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: Any = None
        self.cancelled = False


class Pending:
    """Registry of in-flight questions awaiting a frontend answer."""

    def __init__(self) -> None:
        self._slots: dict[str, _Slot] = {}
        self._lock = threading.Lock()

    def open(self) -> str:
        """Reserve a correlation id. Call BEFORE emitting the event, so an
        answer that races back cannot arrive before the slot exists."""
        token = uuid.uuid4().hex[:16]
        with self._lock:
            self._slots[token] = _Slot()
        return token

    def wait(self, token: str, timeout: float | None = None) -> Any:
        """Block the calling (worker) thread until the answer lands.

        Raises Cancelled if the wait was released without a real answer, or
        if `timeout` elapses — the caller turns that into a denial, which is
        the safe default for an approval.
        """
        with self._lock:
            slot = self._slots.get(token)
        if slot is None:
            raise Cancelled(f"no pending slot {token!r}")
        got = slot.event.wait(timeout)
        with self._lock:
            self._slots.pop(token, None)
        if not got or slot.cancelled:
            raise Cancelled(token)
        return slot.value

    def resolve(self, token: str, value: Any) -> bool:
        """Deliver an answer. False if nobody was waiting (stale click)."""
        with self._lock:
            slot = self._slots.get(token)
            if slot is None:
                return False
            slot.value = value
            slot.event.set()
        return True

    def cancel(self, token: str) -> None:
        with self._lock:
            slot = self._slots.get(token)
            if slot is not None:
                slot.cancelled = True
                slot.event.set()

    def resolve_all(self, value: Any = None, *, cancel: bool = True) -> int:
        """Release every waiter at once — interrupt, disconnect, shutdown."""
        with self._lock:
            slots = list(self._slots.values())
            for slot in slots:
                slot.cancelled = cancel
                slot.value = value
                slot.event.set()
        return len(slots)

    def __len__(self) -> int:
        with self._lock:
            return len(self._slots)
