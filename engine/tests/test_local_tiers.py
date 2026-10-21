import json
from pathlib import Path

import pytest

from meshharness.local import tiers
from meshharness.local.hardware import Gpu, HardwareProfile

GB = 1024 ** 3


def _m(id_, bytes_gb, rank, kv=80_000, layers=36, ctx_max=262144):
    return {"id": id_, "label": id_, "bytes": int(bytes_gb * GB), "quality_rank": rank,
            "kv_bytes_per_token": kv, "n_layer": layers, "ctx_max": ctx_max}


MANIFEST = {"runtime": {"server_args": ["--jinja"]},
            "models": [_m("local/small", 2.7, 30), _m("local/mid", 5.7, 50),
                       _m("local/big", 19.2, 70, kv=56_000, layers=48)]}


def _hw(ram, gpu="metal", vram=0.0, driver="", os_="darwin"):
    return HardwareProfile(os=os_, arch="arm64" if gpu == "metal" else "x64", ram_gb=ram,
                           avail_gb=ram, cores=8, gpu=Gpu(kind=gpu, vram_gb=vram, driver=driver))


@pytest.mark.parametrize("ram,expect_model,expect_ctx,expect_slow", [
    (8, "local/small", 8192, True),       # nothing fits at 32k: smallest, 8k, marked slow
    (12, "local/small", 32768, False),
    (16, "local/mid", 32768, False),
    (18, "local/mid", 65536, False),
    (32, "local/big", 32768, False),
    (64, "local/big", 262144, False),
])
def test_tier_table_on_apple_silicon(ram, expect_model, expect_ctx, expect_slow):
    plan = tiers.choose(MANIFEST, _hw(ram))
    assert plan is not None
    assert (plan.model["id"], plan.ctx, plan.slow) == (expect_model, expect_ctx, expect_slow)
    assert plan.ngl == (0 if expect_slow else 99)


def test_pick_keeps_four_gb_free_and_respects_the_metal_working_set():
    hw = _hw(16)
    plan = tiers.choose(MANIFEST, hw)
    used = tiers.footprint(plan.model, plan.ctx)
    assert hw.ram_gb * GB - used >= 4 * GB
    assert used <= 0.75 * hw.ram_gb * GB


def test_cpu_only_box_is_slow_and_offloads_nothing():
    plan = tiers.choose(MANIFEST, _hw(16, gpu="none", os_="linux"))
    assert plan.model["id"] == "local/mid" and plan.slow and plan.ngl == 0
    assert plan.variant == "linux-x64-cpu"


def test_small_discrete_gpu_offloads_a_share_of_layers_on_the_vulkan_build():
    plan = tiers.choose(MANIFEST, _hw(16, gpu="cuda", vram=8, driver="560.1", os_="win32"))
    assert plan.model["id"] == "local/mid"
    assert 0 < plan.ngl < 99 and plan.slow
    assert plan.variant == "win32-x64-vulkan"


def test_big_discrete_gpu_takes_every_layer_and_is_not_slow():
    plan = tiers.choose(MANIFEST, _hw(32, gpu="cuda", vram=32, os_="linux"))
    assert plan.ngl == 99 and not plan.slow and plan.variant == "linux-x64-vulkan"


def test_vulkan_with_unknown_vram_stays_on_cpu_unless_forced():
    plan = tiers.choose(MANIFEST, _hw(16, gpu="vulkan", os_="win32"))
    assert plan.ngl == 0 and plan.slow and plan.variant == "win32-x64-vulkan"
    forced = tiers.choose(MANIFEST, _hw(16, gpu="vulkan", os_="win32"), gpu_setting="on")
    assert forced.ngl == 99 and not forced.slow


def test_gpu_off_means_the_cpu_build_and_no_offload():
    plan = tiers.choose(MANIFEST, _hw(16, gpu="cuda", vram=8, os_="win32"), gpu_setting="off")
    assert plan.variant == "win32-x64-cpu" and plan.ngl == 0


def test_user_context_override_is_clamped_to_what_fits():
    plan = tiers.choose(MANIFEST, _hw(18), ctx_override=16384)
    assert plan.ctx == 32768                       # never below the minimum
    plan = tiers.choose(MANIFEST, _hw(64), ctx_override=65536)
    assert plan.ctx == 65536                       # honoured when it fits


def test_server_args_carry_the_flags_tool_calling_depends_on():
    plan = tiers.choose(MANIFEST, _hw(16))
    plan.model["chat_template_kwargs"] = {"enable_thinking": False}
    args = tiers.server_args(plan, MANIFEST, alias="local/mid", idle_unload_s=600)
    joined = " ".join(args)
    for needed in ("--jinja", "--api-key-file {key_file}", "--port {port}", "-m {model_path}",
                   "--host 127.0.0.1", "-c 32768", "-ngl 99", "--sleep-idle-seconds 600",
                   '--chat-template-kwargs {"enable_thinking":false}'):
        assert needed in joined


def test_bundled_manifest_numbers_give_sane_defaults():
    real = json.loads((Path(tiers.__file__).parent / "manifest.json").read_text())
    plan = tiers.choose(real, _hw(16))
    assert plan.model["id"] == "local/qwen3.5-9b-q4" and plan.ctx >= 32768
    assert tiers.choose(real, _hw(32)).model["id"] == "local/north-mini-code-1.0-q4"
    assert tiers.choose(real, _hw(48)).model["id"] == "local/qwen3.8-27b-q6"
