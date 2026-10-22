"""The one object the engine talks to: what is installed, what is
recommended, what is downloading, and a ready Target when a hop needs one."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable

from . import download, manifest as manifest_mod, runtime, tiers
from .hardware import HardwareProfile, probe
from .provider import AUTO_ID, Target, catalog_rows
from .server import LocalServer, LocalServerError

AUTO_DOWNLOAD_LIMIT_GB = 8
_GB = 1024 ** 3


class LocalUnavailable(RuntimeError):
    """The hop cannot run locally; the message is user-readable. `permanent`
    means waiting will not help (disabled, nothing fits), so the caller
    should say so once per session rather than once per turn."""

    def __init__(self, message: str, *, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


class LocalModels:
    def __init__(self, cfg: dict, *, local_dir: Path, app_version: str,
                 emit: Callable[[str, dict], None], on_catalog_change: Callable[[], None]):
        self.cfg = cfg
        self.local_dir = local_dir
        self.models_dir = local_dir / "models"
        self.runtime_dir = local_dir / "runtime"
        self.state_file = local_dir / "state.json"
        self.app_version = app_version
        self._emit = emit
        self._on_catalog_change = on_catalog_change
        self._lock = threading.RLock()
        self.hw: HardwareProfile | None = None
        self.manifest: dict | None = None
        self.plan: tiers.TierPlan | None = None
        self.state: dict = self._load_state()
        self.downloads = download.DownloadManager(lambda job: self._emit("local.progress", job))
        self.server = LocalServer(local_dir, on_state=lambda _: self._emit_status())
        self.booted = threading.Event()
        self._booting = threading.Lock()   # one boot at a time

    def boot(self) -> None:
        """Runs on a thread at engine start; never raises. A disabled layer
        touches nothing: no probe, no manifest fetch, no download."""
        if not self._booting.acquire(blocking=False):
            return
        try:
            self._boot()
        finally:
            self._booting.release()

    def _boot(self) -> None:
        if not self.cfg.get("local_enabled", True):
            self.booted.set()
            self._emit_status()
            return
        try:
            hw = probe(self.models_dir)
            manifest = manifest_mod.load(self.local_dir / "manifest.json", self.app_version)
            with self._lock:
                self.hw, self.manifest = hw, manifest
                self._reconcile()
                self.plan = self._plan_for(self.default_id())
            threading.Thread(target=self._refresh_manifest, daemon=True).start()
            self._auto_install()
        except Exception as e:  # a local-model problem must never block the engine
            with self._lock:
                self.state["boot_error"] = f"{type(e).__name__}: {e}"
        finally:
            self.booted.set()
            self._emit_status()

    def shutdown(self) -> None:
        self.downloads.shutdown()
        self.server.stop()

    def default_id(self) -> str | None:
        chosen = self.cfg.get("local_model") or self.state.get("default")
        if chosen and self.manifest and manifest_mod.model(self.manifest, chosen):
            return chosen
        plan = self._recommend()
        return plan.model["id"] if plan else None

    def installed_ids(self) -> set:
        with self._lock:
            return set(self.state.get("installed", {}))

    def catalog_rows(self) -> list[dict]:
        with self._lock:
            manifest = self.manifest
            if not manifest or not self.cfg.get("local_enabled", True):
                return []
            running = {}
            srv = self.server.describe()
            if srv["state"] == "ready" and srv.get("model"):
                running[srv["model"]] = srv["ctx"]
            planned = {m["id"]: p.ctx for m in manifest["models"] if (p := self._plan_for(m["id"]))}
            return catalog_rows(manifest, installed=self.installed_ids(), default_id=self.default_id(),
                                running_ctx=running, planned_ctx=planned)

    def status(self) -> dict:
        with self._lock:
            plan = self.plan
            needs_confirm = None
            blocked = self._blocked(plan) if plan else None
            if (plan and blocked and plan.model["id"] not in self.installed_ids()
                    and not self._job_for(plan.model["id"]) and not self.state.get("declined")):
                needs_confirm = {"model": plan.model["id"], "bytes": plan.model["bytes"], "reason": blocked}
            return {
                "enabled": bool(self.cfg.get("local_enabled", True)),
                "booted": self.booted.is_set(),
                "hardware": self.hw.to_dict() if self.hw else None,
                "probe_failed": bool(self.hw and self.hw.probe_failed),
                "recommended": ({"model": plan.model["id"], "ctx": plan.ctx, "reason": plan.reason,
                                 "slow": plan.slow} if plan else None),
                "runtime": self._runtime_status(),
                "models": [self._model_row(m) for m in (self.manifest or {}).get("models", [])],
                "default_model": self.default_id(),
                "server": self.server.describe(),
                "jobs": self.downloads.active(),
                "needs_confirm": needs_confirm,
                "declined": bool(self.state.get("declined")),
                "restart_needed": bool(self.state.get("restart_needed")),
                "boot_error": self.state.get("boot_error"),
            }

    def install(self, model_id: str) -> None:
        entry = manifest_mod.model(self.manifest or {"models": []}, model_id)
        if not entry:
            raise ValueError(f"unknown local model {model_id}")
        if model_id in self.installed_ids() or self._job_for(model_id):
            return
        self._ensure_runtime_download()
        dest = self.models_dir / entry["file"]
        self.downloads.enqueue(kind="model", target=model_id, url=manifest_mod.model_url(entry),
                               dest=dest, sha256=entry["sha256"], size=int(entry["bytes"]),
                               on_done=lambda job, err: self._model_done(model_id, dest, err))
        self._emit_status()

    def cancel(self, job_id: str) -> bool:
        ok = self.downloads.cancel(job_id)
        self._emit_status()
        return ok

    def delete(self, model_id: str) -> None:
        srv = self.server.describe()
        if srv.get("model") == model_id and srv["state"] != "stopped":
            raise RuntimeError("stop the local server before deleting the model it is running")
        with self._lock:
            path = self.state.get("installed", {}).pop(model_id, None)
            if path:
                for p in (Path(path), Path(path + ".sha256")):
                    p.unlink(missing_ok=True)
            self._save_state()
        self._on_catalog_change()
        self._emit_status()

    def set_default(self, model_id: str) -> None:
        if not manifest_mod.model(self.manifest or {"models": []}, model_id):
            raise ValueError(f"unknown local model {model_id}")
        with self._lock:
            self.state["default"] = model_id
            self.plan = self._plan_for(model_id)
            self._save_state()
        self._on_catalog_change()
        self._emit_status()

    def decline(self) -> None:
        with self._lock:
            self.state["declined"] = True
            self._save_state()
        self._emit_status()

    def server_action(self, action: str) -> None:
        if action == "stop":
            self.server.stop()
        elif action == "restart":
            self.server.stop()
            if self.plan and self.plan.model["id"] in self.installed_ids():
                self.target_for(self.plan.model["id"])
        self._emit_status()

    def refresh(self, *, force: bool = False) -> bool:
        """Fetch the remote manifest; True when it changed what we know."""
        if not manifest_mod.refresh(self.local_dir / "manifest.json", self.app_version, force=force):
            return False
        with self._lock:
            self.manifest = manifest_mod.load(self.local_dir / "manifest.json", self.app_version)
            self._reconcile()          # a re-pinned sha un-installs a stale file
            self.plan = self._plan_for(self.default_id())
        self._on_catalog_change()
        self._emit_status()
        return True

    def validate_setting(self, key: str, value) -> None:
        """Raise ValueError for a value the layer cannot honour."""
        if key == "local_model" and value and value not in self.installed_ids():
            raise ValueError(f"{value} is not downloaded yet; install it under Settings › Local model first")

    def recover(self, *, force: bool = False) -> None:
        """`force` stops a server that is alive but not answering."""
        if force:
            self.server.stop()
        else:
            self.server.recover()

    def on_setting(self, key: str, value) -> None:
        with self._lock:
            self.cfg[key] = value
            if key in ("local_model", "local_context", "local_gpu", "local_keep_free_gb"):
                self.plan = self._plan_for(self.default_id())
            if key in ("local_context", "local_gpu", "local_keep_free_gb"):
                self.state["restart_needed"] = self.server.state == "ready"
        if key == "local_enabled":
            if value:
                if self.hw is None and self.booted.is_set():   # booted while disabled: probe now
                    self.booted.clear()
                    threading.Thread(target=self.boot, daemon=True).start()
            else:
                # Stopping waits on the process; never do that on the event loop.
                threading.Thread(target=self._stop_and_report, daemon=True).start()
        elif key == "local_use_when_slow" and value:
            self._auto_install()          # the gate that blocked the download just opened
        self._on_catalog_change()
        self._emit_status()

    def _stop_and_report(self) -> None:
        self.server.stop()
        self._emit_status()

    def _auto_install(self) -> None:
        if (self.cfg.get("local_auto_setup", True) and not self.state.get("declined")
                and not self.installed_ids() and self.plan and not self._blocked(self.plan)):
            self.install(self.plan.model["id"])

    def target_for(self, model_id: str, *, cancel: threading.Event | None = None,
                   notify: Callable[[str], None] | None = None) -> Target:
        if not self.cfg.get("local_enabled", True):
            raise LocalUnavailable("local models are turned off in Settings", permanent=True)
        if not self.booted.wait(timeout=30) or not self.manifest or not self.hw:
            if self.booted.is_set() and self.state.get("boot_error"):
                raise LocalUnavailable(f"the local model layer failed to start ({self.state['boot_error']})",
                                       permanent=True)
            raise LocalUnavailable("the local model layer is still starting")
        if self.hw.probe_failed:
            raise LocalUnavailable("could not read this machine's memory, so no local model was chosen",
                                   permanent=True)
        resolved = self.default_id() if model_id == AUTO_ID else model_id
        if not resolved:
            raise LocalUnavailable("no local model fits this machine", permanent=True)
        entry = manifest_mod.model(self.manifest, resolved)
        if not entry:
            raise LocalUnavailable(f"unknown local model {resolved}")
        plan = self._plan_for(resolved)
        path = self.state.get("installed", {}).get(resolved)
        if not path or not Path(path).exists():
            job = self._job_for(resolved)
            if job:
                pct = int(100 * job["bytes"] / max(job["total"], 1))
                raise LocalUnavailable(f"{entry['label']} is still downloading ({pct}%)")
            # Nothing is coming on its own: the user declined, or the plan needs
            # a click. Say so once, not every turn.
            settled = bool(self.state.get("declined")) or bool(plan and self._blocked(plan))
            raise LocalUnavailable(f"{entry['label']} is not downloaded yet", permanent=settled)
        if plan is None:
            raise LocalUnavailable(f"{entry['label']} does not fit in memory on this machine", permanent=True)
        if plan.slow and not self.cfg.get("local_use_when_slow", False):
            raise LocalUnavailable(f"{entry['label']} would run slowly here; enable 'Use even when slow' to try it",
                                   permanent=True)
        server_bin = runtime.locate(self.runtime_dir, self.manifest["runtime"]["tag"], plan.variant)
        if not server_bin:
            if not self._ensure_runtime_download(plan.variant):
                raise LocalUnavailable(f"no llama-server build is pinned for {plan.variant}", permanent=True)
            raise LocalUnavailable("the local runtime is still downloading")
        idle = int(self.cfg.get("local_idle_unload_min", 20) or 0) * 60
        argv = [a.replace("{model_path}", path)
                for a in tiers.server_args(plan, self.manifest, alias=resolved, idle_unload_s=idle)]
        try:
            info = self.server.ensure(server_bin, argv, model_id=resolved, alias=resolved, ctx=plan.ctx,
                                      model_bytes=int(entry["bytes"]), cancel=cancel, notify=notify)
        except LocalServerError as e:
            raise LocalUnavailable(str(e)) from e
        with self._lock:
            self.state.pop("restart_needed", None)
        return Target.for_local(port=info.port, api_key=info.api_key, alias=info.alias,
                               model_id=resolved, ctx=info.ctx, entry=entry)

    def _recommend(self) -> tiers.TierPlan | None:
        if not (self.manifest and self.hw):
            return None
        return tiers.choose(self.manifest, self.hw, **self._tier_kw())

    def _plan_for(self, model_id: str | None) -> tiers.TierPlan | None:
        if not (self.manifest and self.hw and model_id):
            return None
        entry = manifest_mod.model(self.manifest, model_id)
        if not entry:
            return None
        return tiers.choose({"runtime": self.manifest["runtime"], "models": [entry]}, self.hw, **self._tier_kw())

    def _blocked(self, plan: tiers.TierPlan) -> str | None:
        """Why this plan is not auto-installed: too big to fetch unasked, or
        too slow to be used with the current settings."""
        if plan.model["bytes"] > AUTO_DOWNLOAD_LIMIT_GB * _GB:
            return "size"
        if plan.slow and not self.cfg.get("local_use_when_slow", False):
            return "slow"
        return None

    def _tier_kw(self) -> dict:
        return {"keep_free_gb": float(self.cfg.get("local_keep_free_gb", 4) or 4),
                "ctx_override": _ctx_setting(self.cfg.get("local_context")),
                "gpu_setting": str(self.cfg.get("local_gpu", "auto"))}

    def _reconcile(self) -> None:
        installed = {}
        for m in self.manifest["models"]:
            dest = self.models_dir / m["file"]
            marker = self.models_dir / (m["file"] + ".sha256")
            if dest.exists() and marker.exists() and marker.read_text().strip() == m["sha256"]:
                installed[m["id"]] = str(dest)
        self.state["installed"] = installed
        self._save_state()

    def _ensure_runtime_download(self, variant: str | None = None) -> bool:
        """True when a build for the variant exists or is on its way."""
        if not (self.manifest and self.hw):
            return False
        variant = variant or (self.plan.variant if self.plan else None)
        if not variant:
            return False
        tag = self.manifest["runtime"]["tag"]
        if runtime.locate(self.runtime_dir, tag, variant) or self._job_for(variant):
            return True
        asset = self.manifest["runtime"]["assets"].get(variant)
        if not asset:
            return False
        dest = self.runtime_dir / "downloads" / asset["file"]
        self.downloads.enqueue(kind="runtime", target=variant, url=manifest_mod.runtime_url(self.manifest, variant),
                               dest=dest, sha256=asset["sha256"], size=int(asset["bytes"]),
                               on_done=lambda job, err: self._runtime_done(tag, variant, dest, err))
        return True

    def _runtime_done(self, tag: str, variant: str, archive: Path, err) -> None:
        error = str(err) if err else None
        if not err:
            try:                      # extraction runs unlocked; it can take a while
                runtime.install_from_archive(archive, self.runtime_dir / tag / variant)
                archive.unlink(missing_ok=True)
            except Exception as e:
                error = f"{type(e).__name__}: {e}"
        with self._lock:
            if error:
                self.state["runtime_error"] = error
            else:
                self.state.pop("runtime_error", None)
            self._save_state()
        self._emit_status()

    def _model_done(self, model_id: str, dest: Path, err) -> None:
        if not err:
            with self._lock:
                self.state.setdefault("installed", {})[model_id] = str(dest)
                first = not self.state.get("adopted_default")
                self.state["adopted_default"] = True
                self._save_state()
            self._on_catalog_change()
            self._emit("local.ready", {"model": model_id, "first": first})
        self._emit_status()

    def _refresh_manifest(self) -> None:
        self.refresh(force=False)

    def _runtime_status(self) -> dict:
        if not (self.manifest and self.plan):
            return {"tag": None, "variant": None, "source": "missing"}
        tag, variant = self.manifest["runtime"]["tag"], self.plan.variant
        found = runtime.locate(self.runtime_dir, tag, variant)
        source = "missing"
        if found:
            bundled = os.environ.get("MESH_LLAMA_DIR")
            source = ("env" if os.environ.get("MESH_LLAMA_SERVER")
                      else "bundled" if bundled and str(found).startswith(bundled)
                      else "downloaded")
        elif variant not in self.manifest["runtime"]["assets"]:
            source = "unpinned"
        return {"tag": tag, "variant": variant, "source": source, "path": str(found) if found else None,
                "error": self.state.get("runtime_error")}

    def _model_row(self, m: dict) -> dict:
        plan = self._plan_for(m["id"])
        return {"id": m["id"], "label": m["label"], "bytes": m["bytes"], "ctx_max": m["ctx_max"],
                "ctx": plan.ctx if plan else None,
                "installed": m["id"] in self.installed_ids(), "fits": plan is not None,
                "slow": bool(plan and plan.slow), "recommended": m["id"] == self.default_id(),
                "family": m.get("family"), "license": m.get("license"),
                "job": self._job_for(m["id"])}

    def _job_for(self, target: str) -> dict | None:
        return next((j for j in self.downloads.active() if j["target"] == target), None)

    def _emit_status(self) -> None:
        try:
            self._emit("local.status", self.status())
        except Exception:
            pass

    def _load_state(self) -> dict:
        try:
            data = json.loads(self.state_file.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        with self._lock:
            self.local_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state, indent=2))
            os.replace(tmp, self.state_file)
            try:
                self.state_file.chmod(0o600)
            except OSError:
                pass


def _ctx_setting(value) -> int | None:
    table = {"16k": 16384, "32k": 32768, "64k": 65536, "128k": 131072, "256k": 262144}
    return table.get(str(value or "auto"))
