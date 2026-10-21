"""Where one hop goes: the Mesh gateway by default, or a local llama-server."""
from __future__ import annotations

from dataclasses import dataclass, field

LOCAL_PREFIX = "local/"
AUTO_ID = "local/auto"


@dataclass(frozen=True)
class Target:
    base_url: str
    api_key: str
    model: str                      # what goes in the request body
    display_id: str                 # what the UI and the catalog call it
    local: bool = False
    context_limit: int | None = None
    read_timeout: float = 120.0
    request_defaults: dict = field(default_factory=dict)
    supports_reasoning_effort: bool = True

    @classmethod
    def from_cfg(cls, cfg: dict) -> "Target":
        return cls(base_url=cfg["base_url"], api_key=cfg["api_key"],
                   model=cfg["model"], display_id=cfg["model"])

    @classmethod
    def for_local(cls, *, port: int, api_key: str, alias: str, model_id: str,
                  ctx: int, entry: dict) -> "Target":
        return cls(base_url=f"http://127.0.0.1:{port}/v1", api_key=api_key,
                   model=alias, display_id=model_id, local=True, context_limit=ctx,
                   read_timeout=600.0,
                   request_defaults=dict(entry.get("request_defaults") or {}),
                   supports_reasoning_effort=False)


def is_local(model_id: str) -> bool:
    return str(model_id or "").startswith(LOCAL_PREFIX)


def catalog_rows(manifest: dict, *, installed: set, default_id: str | None,
                 running_ctx: dict, planned_ctx: dict) -> list[dict]:
    """Synthetic catalog rows so the picker, pricing and context limits treat
    local models like any other. `context_length` is what the server will
    actually run at, never the architectural maximum."""
    rows = []
    for m in manifest["models"]:
        ctx = running_ctx.get(m["id"]) or planned_ctx.get(m["id"]) or m["ctx_max"]
        rows.append({
            "id": m["id"], "name": m["label"], "brand": "local", "model_type": "text",
            "context_length": ctx, "is_free": True, "local": True,
            "installed": m["id"] in installed, "size_bytes": m["bytes"],
            "recommended": m["id"] == default_id, "supports_tools": True,
            "pricing": {"prompt_usd_per_1m": "0", "completion_usd_per_1m": "0"},
        })
    if default_id:
        resolved = next((r for r in rows if r["id"] == default_id), None)
        if resolved:
            rows.insert(0, {**resolved, "id": AUTO_ID, "name": "Local (recommended for this machine)",
                            "resolves_to": default_id})
    return rows
