"""Find or install the llama-server build for this machine."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

SERVER = "llama-server.exe" if sys.platform == "win32" else "llama-server"
_LIB_SUFFIXES = (".dylib", ".so", ".dll", ".metallib")


def locate(runtime_dir: Path, tag: str, variant: str) -> Path | None:
    """env override → a downloaded build of this tag+variant → the build
    Electron bundled, only when its tag and variant match (a re-pinned tag
    downloads; a CPU bundle never serves a GPU plan)."""
    override = os.environ.get("MESH_LLAMA_SERVER")
    if override and Path(override).is_file():
        return Path(override)
    downloaded = runtime_dir / tag / variant / SERVER
    if downloaded.is_file():
        return downloaded
    bundled_dir = os.environ.get("MESH_LLAMA_DIR")
    if (bundled_dir and os.environ.get("MESH_LLAMA_VARIANT") == variant
            and os.environ.get("MESH_LLAMA_TAG") == tag):
        bundled = Path(bundled_dir) / SERVER
        if bundled.is_file():
            return bundled
    return None


def install_from_archive(archive: Path, into: Path) -> Path:
    """Extract only the server and its shared libraries, refusing anything
    that would land outside `into`. Returns the server path."""
    into.mkdir(parents=True, exist_ok=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                name = Path(info.filename).name
                if info.is_dir() or not _wanted(name) or _escapes(info.filename):
                    continue
                with z.open(info) as src, open(into / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    else:
        links: list[tuple[str, str]] = []
        with tarfile.open(archive, "r:gz") as t:
            for member in t.getmembers():
                name = Path(member.name).name
                if not _wanted(name) or _escapes(member.name):
                    continue
                # The loader opens libllama.0.dylib, a symlink onto the versioned file.
                if member.issym() and member.linkname == Path(member.linkname).name \
                        and member.linkname not in ("", ".", "..", name):
                    links.append((name, member.linkname))
                    continue
                if not member.isfile():
                    continue
                src = t.extractfile(member)
                if src is None:
                    continue
                with src, open(into / name, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        _relink(into, links)
    server = into / SERVER
    if not server.exists():
        raise FileNotFoundError(f"{archive.name} contains no {SERVER}")
    for f in into.iterdir():
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IRUSR)
    if sys.platform == "darwin":
        subprocess.run(["xattr", "-dr", "com.apple.quarantine", str(into)],
                       capture_output=True, check=False)
    return server


def version(server: Path) -> str:
    try:
        out = subprocess.run([str(server), "--version"], capture_output=True, text=True,
                             timeout=15, check=False)
        text = out.stdout or out.stderr
        return text.strip().splitlines()[0] if text.strip() else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _wanted(name: str) -> bool:
    return name == SERVER or name.endswith(_LIB_SUFFIXES) or ".so." in name or name.startswith("LICENSE")


def _relink(into: Path, links: list) -> None:
    """Recreate same-directory symlinks whose target exists, chains included."""
    pending = list(links)
    while pending:
        done = [(n, target) for n, target in pending if (into / target).exists()]
        if not done:
            return
        for name, target in done:
            (into / name).unlink(missing_ok=True)
            os.symlink(target, into / name)
        pending = [l for l in pending if l not in done]


def _escapes(member: str) -> bool:
    p = Path(member)
    return p.is_absolute() or ".." in p.parts
