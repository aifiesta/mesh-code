"""Workspace file browsing for the explorer.

This is a NEW capability and worth being precise about: it lets the window
read the user's disk without the agent, the model, or an approval dialog
being involved. That is exactly what an editor's file tree does, and it is
fine — but only because every path is resolved and then checked to be
inside the session's workspace before anything is opened.

Two rules, both enforced here rather than trusted from the frontend:

  1. Everything resolves under the session root. `..`, absolute paths and
     symlinks that escape are rejected AFTER resolution, so a link pointing
     at ~/.ssh cannot be walked into by asking nicely.
  2. The sensitive denylist still applies. A workspace that happens to
     contain a credentials file does not get to preview it into a window.

Listings are lazy — one directory per request — so opening a tree never
walks a huge repo, and `node_modules` costs nothing until expanded.
"""
from __future__ import annotations

import os
from pathlib import Path

from .core import safety

# Previewing is for reading code, not for loading a database dump into a
# renderer. Anything larger is reported with its size instead.
PREVIEW_LIMIT = 512_000
# Directories that are almost never what someone is looking for. They are
# still listed — hiding a folder that exists is its own confusion — but
# they sort last and are flagged so the UI can dim them.
NOISY = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist",
         "build", ".next", ".turbo", "target", ".pytest_cache", ".mypy_cache"}


class OutsideWorkspace(ValueError):
    """The path resolved outside the session's workspace."""


class FileOpError(ValueError):
    """A create/rename/delete could not be carried out."""


def resolve_leaf(root: Path, rel: str) -> Path:
    """Like resolve(), but does NOT follow a trailing symlink.

    For DELETE/RENAME the target of the operation is the entry itself, not
    what it points at. resolve() follows the link, so deleting a symlink
    `current -> releases/v2` used to rmtree releases/v2 and leave the dangling
    link. Here the PARENT is resolved (and containment/safety-checked), and
    the leaf name is re-attached unresolved so the op lands on the link.
    """
    rel = (rel or "").strip("/")
    if not rel:
        # The root itself — let the caller's _guard_not_root reject it.
        return resolve(root, rel)
    parent_rel, _, leaf = rel.rpartition("/")
    parent = resolve(root, parent_rel)  # containment + denylist on the parent
    target = parent / leaf
    # Re-check the (unresolved) target against the denylist by name.
    ok, why = safety.is_path_safe_for_auto_read(str(target), safety.Mode.AUTO, Path(root).expanduser().resolve())
    if not ok:
        raise OutsideWorkspace(why or "path is not writable")
    return target


def resolve(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, or raise. The check is post-resolution."""
    root = Path(root).expanduser().resolve()
    target = (root / (rel or "")).expanduser()
    try:
        target = target.resolve()
    except (OSError, RuntimeError) as e:
        raise OutsideWorkspace(f"could not resolve path: {e}")
    if target != root and root not in target.parents:
        raise OutsideWorkspace("path is outside the workspace")
    ok, why = safety.is_path_safe_for_auto_read(str(target), safety.Mode.AUTO, root)
    if not ok:
        raise OutsideWorkspace(why or "path is not readable")
    return target


def list_dir(root: Path, rel: str = "") -> dict:
    """One directory's children. Directories first, then files, each A-Z."""
    target = resolve(root, rel)
    if not target.is_dir():
        raise OutsideWorkspace("not a directory")

    entries = []
    try:
        with os.scandir(target) as it:
            for e in it:
                try:
                    is_dir = e.is_dir(follow_symlinks=False)
                    size = 0 if is_dir else e.stat(follow_symlinks=False).st_size
                except OSError:
                    continue  # vanished mid-scan, or unreadable — skip it
                entries.append({
                    "name": e.name,
                    "dir": is_dir,
                    "size": size,
                    "noisy": is_dir and e.name in NOISY,
                    "hidden": e.name.startswith("."),
                    "path": str(Path(rel or "") / e.name),
                })
    except PermissionError:
        raise OutsideWorkspace("permission denied")

    entries.sort(key=lambda x: (not x["dir"], x["noisy"], x["name"].lower()))
    return {"path": rel or "", "entries": entries}


INDEX_LIMIT = 6000


def index(root: Path, limit: int = INDEX_LIMIT) -> dict:
    """Every file under `root` as a flat, sorted list of relative paths —
    what the composer's @ picker fuzzy-matches against.

    Noisy and hidden DIRECTORIES are pruned (node_modules alone can be
    40k entries), hidden files are kept (people do mention .env.example).
    Capped so a monorepo can't stall the engine; `truncated` tells the UI
    the list is partial so it can say so rather than silently miss files.
    """
    root = Path(root).expanduser().resolve()
    out: list[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in NOISY and not d.startswith("."))
        rel_dir = os.path.relpath(dirpath, root)
        for name in sorted(filenames):
            out.append(name if rel_dir == "." else f"{rel_dir}/{name}")
            if len(out) >= limit:
                truncated = True
                break
        if truncated:
            break
    return {"files": out, "truncated": truncated}


def read_preview(root: Path, rel: str) -> dict:
    """File contents for the viewer. Never guesses at binary as text."""
    target = resolve(root, rel)
    if target.is_dir():
        raise OutsideWorkspace("that is a directory")
    try:
        size = target.stat().st_size
    except OSError as e:
        raise OutsideWorkspace(str(e))

    if size > PREVIEW_LIMIT:
        return {"path": rel, "size": size, "too_large": True, "content": ""}
    try:
        raw = target.read_bytes()
    except OSError as e:
        raise OutsideWorkspace(str(e))
    # A NUL in the first block is the cheap, reliable binary test — better
    # than an extension list, which is always missing something.
    if b"\x00" in raw[:8000]:
        return {"path": rel, "size": size, "binary": True, "content": ""}
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    return {"path": rel, "size": size, "content": text,
            "lines": text.count("\n") + 1}


# ---------------------------------------------------------------------------
# Mutations
#
# These are the user's own edits to their own project — no model, no approval
# dialog. They still route through resolve(), so the workspace boundary and
# the sensitive denylist apply exactly as they do to reads. Three extra rules
# that only matter once you can WRITE:
#
#   - the workspace root itself can never be renamed or deleted
#   - creating never clobbers: an existing name is an error, not an overwrite
#   - deleting is permanent and says so; nothing here pretends to be a trash
#     can it cannot actually provide from a frozen binary
# ---------------------------------------------------------------------------

def _guard_not_root(root: Path, target: Path) -> None:
    if target == Path(root).expanduser().resolve():
        raise FileOpError("the project folder itself cannot be changed here")


def create(root: Path, rel: str, kind: str = "file") -> dict:
    """Create an empty file or a directory. Never overwrites."""
    name = Path(rel or "").name
    if not name or name in (".", ".."):
        raise FileOpError("give it a name")
    if "/" in name or "\\" in name:
        raise FileOpError("a name cannot contain a slash")

    target = resolve(root, rel)
    if target.exists():
        raise FileOpError(f"“{name}” already exists")
    try:
        if kind == "dir":
            target.mkdir(parents=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
    except OSError as e:
        raise FileOpError(str(e))
    return {"path": rel, "kind": kind}


def rename(root: Path, rel: str, new_name: str) -> dict:
    """Rename in place. The new name stays in the same directory."""
    new_name = (new_name or "").strip()
    if not new_name or new_name in (".", ".."):
        raise FileOpError("give it a name")
    if "/" in new_name or "\\" in new_name:
        raise FileOpError("a name cannot contain a slash")

    target = resolve_leaf(root, rel)
    _guard_not_root(root, target)
    if not (target.exists() or target.is_symlink()):
        raise FileOpError("that no longer exists")
    dest = target.parent / new_name
    if dest.exists() or dest.is_symlink():
        raise FileOpError(f"“{new_name}” already exists")
    try:
        target.rename(dest)
    except OSError as e:
        raise FileOpError(str(e))
    return {"path": str(Path(rel).parent / new_name), "was": rel}


def delete(root: Path, rel: str) -> dict:
    """Delete permanently. The caller is expected to have confirmed."""
    import shutil

    target = resolve_leaf(root, rel)
    _guard_not_root(root, target)
    if not (target.exists() or target.is_symlink()):
        raise FileOpError("that no longer exists")
    try:
        # is_symlink() FIRST: a symlink to a directory must be unlinked (drop
        # the link), never rmtree'd (which would wipe the link's target).
        if target.is_symlink():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    except OSError as e:
        raise FileOpError(str(e))
    return {"path": rel, "deleted": True}


def describe_for_delete(root: Path, rel: str) -> dict:
    """What the confirmation dialog needs: is it a folder, and how big?

    Counting is capped — a confirm dialog must not walk a 40k-file
    node_modules to tell you it is large.
    """
    target = resolve(root, rel)
    _guard_not_root(root, target)
    if not target.is_dir():
        try:
            return {"path": rel, "dir": False, "size": target.stat().st_size}
        except OSError:
            return {"path": rel, "dir": False, "size": 0}
    count, capped = 0, False
    for _root, dirs, filenames in os.walk(target):
        count += len(dirs) + len(filenames)
        if count > 500:
            capped = True
            break
    return {"path": rel, "dir": True, "items": count, "capped": capped}
