"""Resumable, checksum-verified downloads for multi-gigabyte model files.
Nothing is renamed into place until every byte's sha256 matches."""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

CHUNK = 1024 * 1024
PROGRESS_INTERVAL_S = 0.25
DISK_HEADROOM = 500 * 1024 * 1024
RETRY_DELAYS_S = (1, 4, 16)


class Cancelled(Exception):
    pass


class DiskFull(Exception):
    pass


class ChecksumMismatch(Exception):
    pass


@dataclass
class Progress:
    phase: str                  # downloading | verifying | done
    bytes: int
    total: int
    rate_bps: float = 0.0
    eta_s: float | None = None


def download(url: str, dest: Path, sha256: str, size: int, *,
             on_progress: Callable[[Progress], None] | None = None,
             cancel: threading.Event | None = None,
             client: httpx.Client | None = None) -> Path:
    part = dest.with_name(dest.name + ".part")
    marker = dest.with_name(dest.name + ".sha256")
    dest.parent.mkdir(parents=True, exist_ok=True)
    cancel = cancel or threading.Event()
    if dest.exists() and not marker.exists():
        # A crash between rename and marker: verify what is there instead
        # of fetching gigabytes again.
        try:
            _verify(dest, sha256, size, on_progress, cancel)
            marker.write_text(sha256 + "\n")
            _emit(on_progress, Progress("done", size, size))
            return dest
        except ChecksumMismatch:
            pass
    own_client = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0))
    try:
        for attempt, delay in enumerate((0,) + RETRY_DELAYS_S):
            if delay:
                _sleep(delay, cancel)
            try:
                _fetch(client, url, part, size, on_progress, cancel)
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code < 500 or attempt == len(RETRY_DELAYS_S):
                    raise        # a 404 or 401 will not get better by waiting
            except (httpx.HTTPError, OSError) as e:
                if isinstance(e, (DiskFull, Cancelled)) or attempt == len(RETRY_DELAYS_S):
                    raise
    finally:
        if own_client:
            client.close()
    _verify(part, sha256, size, on_progress, cancel)
    os.replace(part, dest)
    marker.write_text(sha256 + "\n")
    _emit(on_progress, Progress("done", size, size))
    return dest


def _fetch(client, url, part, size, on_progress, cancel) -> None:
    have = part.stat().st_size if part.exists() else 0
    if have >= size:
        return
    _check_disk(part.parent, size - have)
    headers = {"Accept-Encoding": "identity"}
    if have:
        headers["Range"] = f"bytes={have}-"
    with client.stream("GET", url, headers=headers) as r:
        if r.status_code == 416:            # server says we already have it all
            return
        r.raise_for_status()
        mode = "ab" if (r.status_code == 206 and have) else "wb"
        if mode == "wb":
            have = 0
        last_emit, last_bytes = 0.0, have
        with open(part, mode) as f:
            for chunk in r.iter_bytes(CHUNK):
                if cancel.is_set():
                    raise Cancelled()
                f.write(chunk)
                have += len(chunk)
                now = time.monotonic()
                if now - last_emit >= PROGRESS_INTERVAL_S:
                    rate = (have - last_bytes) / max(now - last_emit, 1e-6)
                    eta = (size - have) / rate if rate > 0 else None
                    _emit(on_progress, Progress("downloading", have, size, rate, eta))
                    last_emit, last_bytes = now, have
    if have < size:
        raise httpx.TransportError("connection closed before the file was complete")


def _verify(path, sha256, size, on_progress, cancel) -> None:
    h = hashlib.sha256()
    done = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK * 8), b""):
            if cancel.is_set():
                raise Cancelled()
            h.update(chunk)
            done += len(chunk)
            _emit(on_progress, Progress("verifying", done, size))
    if done != size or h.hexdigest() != sha256:
        path.unlink(missing_ok=True)
        raise ChecksumMismatch(f"{path.name}: got {h.hexdigest()[:12]}…, size {done}")


def _check_disk(folder: Path, needed: int) -> None:
    free = shutil.disk_usage(folder).free
    if free < needed * 1.05 + DISK_HEADROOM:
        raise DiskFull(f"need {needed / 1e9:.1f} GB free in {folder}, have {free / 1e9:.1f} GB")


def _sleep(seconds: float, cancel: threading.Event) -> None:
    if cancel.wait(seconds):
        raise Cancelled()


def _emit(cb, p: Progress) -> None:
    if cb:
        try:
            cb(p)
        except Exception:
            pass


@dataclass
class Job:
    id: str
    kind: str                   # model | runtime
    target: str                 # model id or runtime variant
    url: str
    dest: Path
    sha256: str
    size: int
    on_done: Callable[["Job", Exception | None], None]
    phase: str = "queued"
    bytes: int = 0
    rate_bps: float = 0.0
    eta_s: float | None = None
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def describe(self) -> dict:
        return {"id": self.id, "kind": self.kind, "target": self.target, "phase": self.phase,
                "bytes": self.bytes, "total": self.size, "rate_bps": round(self.rate_bps),
                "eta_s": None if self.eta_s is None else round(self.eta_s), "error": self.error}


class DownloadManager:
    """One worker, FIFO. Big files on a laptop link are best served one at
    a time, and one queue keeps progress reporting trivially ordered."""

    def __init__(self, emit: Callable[[dict], None]):
        self._emit = emit
        self._jobs: list[Job] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="mesh-local-downloads", daemon=True)
        self._thread.start()

    def enqueue(self, **kw) -> Job:
        job = Job(id=uuid.uuid4().hex[:8], **kw)
        with self._lock:
            self._jobs.append(job)
        self._emit(job.describe())
        self._wake.set()
        return job

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = next((j for j in self._jobs if j.id == job_id), None)
            queued = bool(job and job.phase == "queued")
            if job:
                job.cancel.set()
                if queued:
                    job.phase = "cancelled"
        if not job:
            return False
        if queued:                      # the worker never claimed it, so finish it here
            self._finish(job, Cancelled())
        return True

    def active(self) -> list[dict]:
        with self._lock:
            return [j.describe() for j in self._jobs]

    def shutdown(self) -> None:
        self._stop.set()
        with self._lock:
            for j in self._jobs:
                j.cancel.set()
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            while True:
                with self._lock:
                    job = next((j for j in self._jobs if j.phase == "queued"), None)
                    if job is not None:
                        job.phase = "downloading"   # claimed: cancel() now leaves finishing to us
                if job is None or self._stop.is_set():
                    break
                self._work(job)

    def _work(self, job: Job) -> None:
        def progress(p: Progress):
            job.phase, job.bytes, job.rate_bps, job.eta_s = p.phase, p.bytes, p.rate_bps, p.eta_s
            self._emit(job.describe())
        try:
            if job.cancel.is_set():
                raise Cancelled()
            download(job.url, job.dest, job.sha256, job.size, on_progress=progress, cancel=job.cancel)
            self._finish(job, None)
        except Exception as e:
            self._finish(job, e)

    def _finish(self, job: Job, err: Exception | None) -> None:
        job.phase = "cancelled" if isinstance(err, Cancelled) else "error" if err else "done"
        job.error = None if not err or isinstance(err, Cancelled) else f"{type(err).__name__}: {err}"
        with self._lock:
            self._jobs = [j for j in self._jobs if j is not job]
        self._emit(job.describe())
        try:
            job.on_done(job, err)
        except Exception:
            pass
