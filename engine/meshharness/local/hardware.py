"""What this machine can run: RAM, GPU, disk. Every probe degrades to
"unknown" rather than raising — a failed probe must never block a launch."""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_GB = 1024 ** 3
MEMINFO = Path("/proc/meminfo")


@dataclass
class Gpu:
    kind: str = "none"          # metal | cuda | vulkan | none
    name: str = ""
    vram_gb: float = 0.0
    driver: str = ""


@dataclass
class HardwareProfile:
    os: str
    arch: str
    ram_gb: float
    avail_gb: float
    cores: int
    gpu: Gpu = field(default_factory=Gpu)
    disk_free_gb: float = 0.0
    probe_failed: bool = False   # RAM could not be read; nothing will "fit"

    def to_dict(self) -> dict:
        return asdict(self)


def probe(models_dir: Path | None = None) -> HardwareProfile:
    os_name = {"darwin": "darwin", "win32": "win32"}.get(sys.platform, "linux")
    arch = _arch()
    total, avail = _memory(os_name)
    return HardwareProfile(
        os=os_name, arch=arch,
        ram_gb=round(total / _GB, 1), avail_gb=round(avail / _GB, 1),
        cores=os.cpu_count() or 1,
        gpu=_gpu(os_name, arch),
        disk_free_gb=_disk_free(models_dir),
        probe_failed=total <= 0,
    )


def _arch() -> str:
    m = platform.machine().lower()
    if m in ("arm64", "aarch64"):
        return "arm64"
    if m in ("x86_64", "amd64"):
        return "x64"
    return m or "unknown"


def _memory(os_name: str) -> tuple[int, int]:
    try:
        if os_name == "darwin":
            total = int(_run(["sysctl", "-n", "hw.memsize"]))
            return total, _darwin_available(total)
        if os_name == "win32":
            return _windows_memory()
        return _linux_memory(MEMINFO)
    except Exception:
        return 0, 0


def _linux_memory(path: Path) -> tuple[int, int]:
    total = avail = 0
    for line in path.read_text().splitlines():
        key, _, rest = line.partition(":")
        fields = rest.split()
        kb = int(fields[0]) if fields else 0
        if key == "MemTotal":
            total = kb * 1024
        elif key == "MemAvailable":
            avail = kb * 1024
    return total, avail


def _darwin_available(total: int) -> int:
    # vm_stat's free + inactive pages is the closest thing to MemAvailable.
    try:
        out = _run(["vm_stat"])
        page = 16384 if "16384" in out.splitlines()[0] else 4096
        pages = {}
        for line in out.splitlines()[1:]:
            k, _, v = line.partition(":")
            pages[k.strip()] = int(v.strip().rstrip("."))
        return (pages.get("Pages free", 0) + pages.get("Pages inactive", 0)) * page
    except Exception:
        return total // 2


def _windows_memory() -> tuple[int, int]:
    class _Status(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    st = _Status()
    st.dwLength = ctypes.sizeof(_Status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))  # type: ignore[attr-defined]
    return int(st.ullTotalPhys), int(st.ullAvailPhys)


def _gpu(os_name: str, arch: str) -> Gpu:
    if os_name == "darwin" and arch == "arm64":
        return Gpu(kind="metal", name="Apple Silicon")
    nv = _nvidia()
    if nv:
        return nv
    if _vulkan_loader_present(os_name):
        return Gpu(kind="vulkan")
    return Gpu()


def _nvidia() -> Gpu | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader,nounits"])
        name, mem_mib, driver = [p.strip() for p in out.splitlines()[0].split(",")[:3]]
        return Gpu(kind="cuda", name=name, vram_gb=round(int(float(mem_mib)) / 1024, 1), driver=driver)
    except Exception:
        return None


def _vulkan_loader_present(os_name: str) -> bool:
    if os_name == "win32":
        root = os.environ.get("SystemRoot", r"C:\Windows")
        return Path(root, "System32", "vulkan-1.dll").exists()
    return bool(ctypes.util.find_library("vulkan"))


def _disk_free(path: Path | None) -> float:
    try:
        target = path or Path.home()
        while not target.exists():
            target = target.parent
        return round(shutil.disk_usage(target).free / _GB, 1)
    except Exception:
        return 0.0


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=True).stdout.strip()
