from meshharness.local import hardware


def test_linux_meminfo_is_parsed_from_kib(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16318860 kB\nMemFree:  123 kB\nMemAvailable:   8000000 kB\nSwapTotal: 0 kB\n")
    total, avail = hardware._linux_memory(meminfo)
    assert total == 16318860 * 1024 and avail == 8000000 * 1024


def test_a_failed_memory_probe_degrades_to_zero_and_flags_the_profile(monkeypatch):
    monkeypatch.setattr(hardware, "_run", lambda cmd: (_ for _ in ()).throw(OSError("no sysctl")))
    monkeypatch.setattr(hardware, "MEMINFO", hardware.Path("/nonexistent/meminfo"))
    assert hardware._memory("darwin") == (0, 0)
    assert hardware._memory("linux") == (0, 0)
    monkeypatch.setattr(hardware, "_gpu", lambda *a: hardware.Gpu())
    assert hardware.probe().probe_failed is True


def test_nvidia_smi_csv_is_parsed_and_absence_means_no_cuda(monkeypatch):
    monkeypatch.setattr(hardware.shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(hardware, "_run", lambda cmd: "NVIDIA GeForce RTX 4070, 12282, 560.35.03\n")
    gpu = hardware._nvidia()
    assert (gpu.kind, gpu.name, gpu.vram_gb, gpu.driver) == ("cuda", "NVIDIA GeForce RTX 4070", 12.0, "560.35.03")
    monkeypatch.setattr(hardware.shutil, "which", lambda name: None)
    assert hardware._nvidia() is None


def test_gpu_falls_back_to_vulkan_then_none(monkeypatch):
    monkeypatch.setattr(hardware, "_nvidia", lambda: None)
    monkeypatch.setattr(hardware, "_vulkan_loader_present", lambda os_name: True)
    assert hardware._gpu("linux", "x64").kind == "vulkan"
    monkeypatch.setattr(hardware, "_vulkan_loader_present", lambda os_name: False)
    assert hardware._gpu("linux", "x64").kind == "none"
    assert hardware._gpu("darwin", "arm64").kind == "metal"


def test_disk_probe_walks_up_to_an_existing_parent_and_never_raises(tmp_path):
    assert hardware._disk_free(tmp_path / "not" / "yet" / "created") > 0
    assert hardware._disk_free(None) > 0
