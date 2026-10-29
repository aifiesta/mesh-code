import io
import sys
import tarfile
import zipfile

import pytest

from meshharness.local import runtime


def _tar(path, members):
    with tarfile.open(path, "w:gz") as t:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


def test_tar_extract_keeps_only_the_server_and_libraries_and_refuses_escapes(tmp_path):
    archive = tmp_path / "llama.tar.gz"
    _tar(archive, {
        "llama-b1/llama-server": b"#!/bin/sh\necho server\n",
        "llama-b1/libggml.0.dylib": b"lib", "llama-b1/libllama.so.0.7.0": b"lib",
        "llama-b1/LICENSE": b"MIT", "llama-b1/llama-cli": b"not wanted",
        "../escape.dylib": b"evil", "/abs/llama-server": b"evil",
    })
    into = tmp_path / "rt"
    server = runtime.install_from_archive(archive, into)
    names = sorted(p.name for p in into.iterdir())
    assert server.name == runtime.SERVER
    assert names == sorted(["LICENSE", "libggml.0.dylib", "libllama.so.0.7.0", runtime.SERVER]) or \
        sys.platform == "win32"
    assert not (tmp_path / "escape.dylib").exists()


def test_zip_extract_works_the_same_way(tmp_path):
    archive = tmp_path / "llama.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("llama-server.exe" if sys.platform == "win32" else "llama-server", "bin")
        z.writestr("ggml.dll", "lib")
        z.writestr("llama-cli.exe", "no")
        z.writestr("../../evil.dll", "no")
    into = tmp_path / "rt"
    runtime.install_from_archive(archive, into)
    assert (into / runtime.SERVER).exists() and (into / "ggml.dll").exists()
    assert not (into / "llama-cli.exe").exists() and not (tmp_path / "evil.dll").exists()


def test_archive_without_a_server_is_an_error(tmp_path):
    archive = tmp_path / "x.tar.gz"
    _tar(archive, {"llama-b1/libggml.dylib": b"lib"})
    with pytest.raises(FileNotFoundError):
        runtime.install_from_archive(archive, tmp_path / "rt")


def test_locate_prefers_env_then_a_downloaded_variant_then_a_matching_bundle(tmp_path, monkeypatch):
    rt = tmp_path / "runtime"
    downloaded = rt / "b1" / "darwin-arm64-metal" / runtime.SERVER
    downloaded.parent.mkdir(parents=True)
    downloaded.write_text("x")
    monkeypatch.delenv("MESH_LLAMA_SERVER", raising=False)
    monkeypatch.delenv("MESH_LLAMA_DIR", raising=False)
    monkeypatch.delenv("MESH_LLAMA_VARIANT", raising=False)
    assert runtime.locate(rt, "b1", "darwin-arm64-metal") == downloaded

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / runtime.SERVER).write_text("x")
    monkeypatch.setenv("MESH_LLAMA_DIR", str(bundle))
    monkeypatch.setenv("MESH_LLAMA_VARIANT", "linux-x64-cpu")
    assert runtime.locate(rt, "b1", "linux-x64-cpu") == bundle / runtime.SERVER
    assert runtime.locate(rt, "b1", "linux-x64-vulkan") is None      # a CPU bundle never serves a GPU plan
    assert runtime.locate(rt, "b1", "darwin-arm64-metal") == downloaded   # downloaded beats bundled

    override = tmp_path / "custom-server"
    override.write_text("x")
    monkeypatch.setenv("MESH_LLAMA_SERVER", str(override))
    assert runtime.locate(rt, "b1", "darwin-arm64-metal") == override

    monkeypatch.setenv("MESH_LLAMA_SERVER", str(tmp_path / "missing"))
    monkeypatch.delenv("MESH_LLAMA_DIR")
    assert runtime.locate(rt, "b1", "linux-x64-cpu") is None
