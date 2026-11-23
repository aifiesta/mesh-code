import threading
import time

import pytest
from fake_hf import FakeFileHost

from meshharness.local import download


@pytest.fixture
def host():
    h = FakeFileHost()
    yield h
    h.close()


def test_happy_path_verifies_renames_and_writes_a_marker(tmp_path, host):
    dest = tmp_path / "m.gguf"
    seen = []
    download.download(host.url, dest, host.sha256, host.size, on_progress=seen.append)
    assert dest.read_bytes() == host.blob
    assert (tmp_path / "m.gguf.sha256").read_text().strip() == host.sha256
    assert not (tmp_path / "m.gguf.part").exists()
    assert seen[-1].phase == "done" and seen[-1].bytes == host.size
    assert any(p.phase == "verifying" for p in seen)


def test_resumes_after_a_mid_transfer_disconnect_with_a_range_request(tmp_path, host, monkeypatch):
    monkeypatch.setattr(download, "RETRY_DELAYS_S", (0, 0, 0))
    monkeypatch.setattr(download, "CHUNK", 64 * 1024)   # so some chunks land before the cut
    host.disconnect_at = 700_000
    dest = tmp_path / "m.gguf"
    download.download(host.url, dest, host.sha256, host.size)
    assert dest.read_bytes() == host.blob
    ranges = [r["range"] for r in host.requests]
    assert ranges[0] is None and len(ranges) == 2
    resumed_at = int(ranges[1].removeprefix("bytes=").rstrip("-"))
    assert 0 < resumed_at <= 700_000


def test_restarts_cleanly_when_the_server_ignores_range(tmp_path, host, monkeypatch):
    monkeypatch.setattr(download, "RETRY_DELAYS_S", (0, 0, 0))
    host.ignore_range = True
    part = tmp_path / "m.gguf.part"
    part.write_bytes(b"stale" * 1000)
    download.download(host.url, tmp_path / "m.gguf", host.sha256, host.size)
    assert (tmp_path / "m.gguf").read_bytes() == host.blob


def test_checksum_mismatch_deletes_the_part_and_raises(tmp_path, host):
    host.serve_wrong_bytes = True
    with pytest.raises(download.ChecksumMismatch):
        download.download(host.url, tmp_path / "m.gguf", host.sha256, host.size)
    assert not (tmp_path / "m.gguf.part").exists() and not (tmp_path / "m.gguf").exists()


def test_insufficient_disk_refuses_before_any_bytes_move(tmp_path, host, monkeypatch):
    monkeypatch.setattr(download, "DISK_HEADROOM", 10 ** 15)
    with pytest.raises(download.DiskFull):
        download.download(host.url, tmp_path / "m.gguf", host.sha256, host.size)
    assert host.requests == []


def test_cancel_keeps_the_part_file_for_a_later_resume(tmp_path, host):
    cancel = threading.Event()
    def stop_early(p):
        if p.phase == "downloading" and p.bytes > 0:
            cancel.set()
    import meshharness.local.download as d
    orig = d.CHUNK
    d.CHUNK = 64 * 1024
    try:
        with pytest.raises(download.Cancelled):
            download.download(host.url, tmp_path / "m.gguf", host.sha256, host.size,
                              on_progress=stop_early, cancel=cancel)
    finally:
        d.CHUNK = orig
    part = tmp_path / "m.gguf.part"
    assert part.exists() and 0 < part.stat().st_size < host.size


def test_manager_runs_jobs_in_order_and_reports_each_phase(tmp_path, host):
    events, done = [], []
    mgr = download.DownloadManager(events.append)
    for i in range(2):
        mgr.enqueue(kind="model", target=f"m{i}", url=host.url, dest=tmp_path / f"m{i}.gguf",
                    sha256=host.sha256, size=host.size,
                    on_done=lambda job, err: done.append((job.target, err)))
    deadline = time.time() + 20
    while len(done) < 2 and time.time() < deadline:
        time.sleep(0.05)
    assert done == [("m0", None), ("m1", None)]
    phases = [e["phase"] for e in events if e["target"] == "m0"]
    assert phases[0] == "queued" and phases[-1] == "done" and "downloading" in phases
    assert mgr.active() == []
    mgr.shutdown()


def test_manager_cancel_of_a_queued_job_never_starts_it(tmp_path, host):
    events = []
    mgr = download.DownloadManager(events.append)
    mgr._wake.clear()
    first = mgr.enqueue(kind="model", target="m0", url=host.url, dest=tmp_path / "a.gguf",
                        sha256=host.sha256, size=host.size, on_done=lambda *a: None)
    second = mgr.enqueue(kind="model", target="m1", url=host.url, dest=tmp_path / "b.gguf",
                         sha256=host.sha256, size=host.size, on_done=lambda *a: None)
    assert mgr.cancel(second.id)
    deadline = time.time() + 20
    while not (tmp_path / "a.gguf").exists() and time.time() < deadline:
        time.sleep(0.05)
    assert not (tmp_path / "b.gguf").exists() and not (tmp_path / "b.gguf.part").exists()
    assert [e["phase"] for e in events if e["target"] == "m1"][-1] == "cancelled"
    mgr.shutdown()


def test_a_4xx_is_not_retried(tmp_path, host, monkeypatch):
    import httpx
    monkeypatch.setattr(download, "RETRY_DELAYS_S", (0, 0, 0))
    host.status = 404
    with pytest.raises(httpx.HTTPStatusError):
        download.download(host.url, tmp_path / "m.gguf", host.sha256, host.size)
    assert len(host.requests) == 1


def test_a_complete_file_without_its_marker_is_verified_not_refetched(tmp_path, host):
    dest = tmp_path / "m.gguf"
    dest.write_bytes(host.blob)
    download.download(host.url, dest, host.sha256, host.size)
    assert host.requests == [] and (tmp_path / "m.gguf.sha256").exists()
    dest.write_bytes(b"corrupt")
    download.download(host.url, dest, host.sha256, host.size)
    assert len(host.requests) == 1 and dest.read_bytes() == host.blob
