"""Pick the strongest manifest model that fits this machine, and the largest
context that still leaves `keep_free_gb` of RAM untouched."""
from __future__ import annotations

import json
from dataclasses import dataclass

from .hardware import HardwareProfile

_GB = 1024 ** 3
CTX_LADDER = (8192, 16384, 32768, 65536, 131072, 262144)
CTX_MIN = 32768   # an agent loop below this re-compacts constantly
# Metal keeps weights in unified memory; past ~75 % the OS starts paging.
METAL_WORKING_SET = 0.75
VRAM_USABLE = 0.9
# The recommended tier leaves this share of RAM (up to the cap) to the OS and
# the user's other apps; a 9.5 GB model on an 18 GB laptop swaps otherwise.
DEFAULT_HEADROOM = 0.375
DEFAULT_HEADROOM_CAP_GB = 8.0


@dataclass
class TierPlan:
    model: dict
    ctx: int
    ngl: int
    variant: str
    slow: bool
    reason: str
    # Fits only by the hard rule: an explicit pick that leaves little for the rest of the machine.
    tight: bool = False


def kv_bytes(model: dict, ctx: int) -> int:
    return int(model["kv_bytes_per_token"]) * ctx


def overhead_bytes(ctx: int) -> int:
    # llama-server's own allocations plus the compute buffer, which grows
    # with the batch of context being processed.
    return int((1.0 + 0.25 * ctx / 16384) * _GB)


def footprint(model: dict, ctx: int) -> int:
    return int(model["bytes"]) + kv_bytes(model, ctx) + overhead_bytes(ctx)


def headroom_gb(hw: HardwareProfile, keep_free_gb: float, *, conservative: bool) -> float:
    if not conservative:
        return keep_free_gb
    return max(keep_free_gb, min(hw.ram_gb * DEFAULT_HEADROOM, DEFAULT_HEADROOM_CAP_GB))


def fits(model: dict, ctx: int, hw: HardwareProfile, keep_free_gb: float, *,
         conservative: bool = False) -> bool:
    need = footprint(model, ctx)
    budget = hw.ram_gb * _GB - headroom_gb(hw, keep_free_gb, conservative=conservative) * _GB
    if hw.gpu.kind == "metal":
        budget = min(budget, hw.ram_gb * _GB * METAL_WORKING_SET)
    return need <= budget


def gpu_layers(model: dict, ctx: int, hw: HardwareProfile, gpu_setting: str) -> int:
    """How many layers to offload. Metal takes everything; a discrete GPU
    with known VRAM takes what fits; Vulkan with unknown VRAM stays on the
    CPU unless the user forces the GPU on."""
    if gpu_setting == "off" or hw.gpu.kind == "none":
        return 0
    if hw.gpu.kind == "metal" or gpu_setting == "on":
        return 99
    if hw.gpu.vram_gb <= 0:
        return 0
    usable = hw.gpu.vram_gb * _GB * VRAM_USABLE
    need = int(model["bytes"]) + kv_bytes(model, ctx)
    if need <= usable:
        return 99
    layers = int(model.get("n_layer") or 0)
    return max(0, int(layers * usable / need)) if layers else 0


def choose(manifest: dict, hw: HardwareProfile, *, keep_free_gb: float = 4.0,
           ctx_override: int | None = None, gpu_setting: str = "auto",
           conservative: bool = True) -> TierPlan | None:
    """The recommendation is conservative; an explicit pick (`conservative=False`)
    may use the hard rule and is then marked tight at the minimum context."""
    candidates = sorted(
        (m for m in manifest["models"] if m.get("tool_calls", "native") != "none"),
        key=lambda m: m.get("quality_rank", 0), reverse=True)
    for model in candidates:
        tight = not fits(model, CTX_MIN, hw, keep_free_gb, conservative=True)
        if tight and (conservative or not fits(model, CTX_MIN, hw, keep_free_gb)):
            continue
        ctx = CTX_MIN
        for step in CTX_LADDER:
            if tight or step < CTX_MIN:
                continue
            if step > int(model.get("ctx_max") or step):
                break
            if ctx_override and step > ctx_override:
                break
            if fits(model, step, hw, keep_free_gb, conservative=True):
                ctx = step
        ngl = gpu_layers(model, ctx, hw, gpu_setting)
        note = " (tight: little memory left for other apps)" if tight else ""
        return TierPlan(
            model=model, ctx=ctx, ngl=ngl,
            variant=runtime_variant(hw, gpu_setting),
            slow=ngl < 99, tight=tight,
            reason=f"{hw.ram_gb:g} GB RAM, {hw.gpu.kind} → {model['label']} at {ctx // 1024}k context{note}",
        )
    # Nothing fits at the minimum context: offer the smallest model at the
    # smallest context and let the user decide, marked slow.
    smallest = min(candidates, key=lambda m: int(m["bytes"]), default=None)
    if smallest and fits(smallest, CTX_LADDER[0], hw, keep_free_gb=max(2.0, keep_free_gb - 2)):
        return TierPlan(model=smallest, ctx=CTX_LADDER[0], ngl=0,
                        variant=runtime_variant(hw, gpu_setting), slow=True,
                        reason=f"{hw.ram_gb:g} GB RAM is tight; {smallest['label']} at 8k, CPU only")
    return None


def runtime_variant(hw: HardwareProfile, gpu_setting: str = "auto") -> str:
    if hw.os == "darwin":
        return "darwin-arm64-metal"
    base = "win32-x64" if hw.os == "win32" else "linux-x64"
    if gpu_setting != "off" and hw.gpu.kind in ("cuda", "vulkan"):
        return f"{base}-vulkan"   # CUDA builds are not pinned yet; Vulkan drives NVIDIA too
    return f"{base}-cpu"


def server_args(plan: TierPlan, manifest: dict, *, alias: str, idle_unload_s: int) -> list[str]:
    """argv after the binary. `{model_path}`, `{port}` and `{key_file}` are
    filled in by whoever launches it; the server owns its port and key."""
    model = plan.model
    args = ["-m", "{model_path}", "-a", alias, "--host", "127.0.0.1", "--port", "{port}",
            "--api-key-file", "{key_file}", "-c", str(plan.ctx), "-ngl", str(plan.ngl)]
    args += list(manifest["runtime"].get("server_args", []))
    if idle_unload_s > 0:
        args += ["--sleep-idle-seconds", str(idle_unload_s)]
    kwargs = model.get("chat_template_kwargs")
    if kwargs:
        args += ["--chat-template-kwargs", json.dumps(kwargs, separators=(",", ":"))]
    args += list(model.get("extra_server_args", []))
    return args
