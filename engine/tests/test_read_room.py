"""File reads sized to the serving model's window (tokens, not characters)."""
import hashlib
import json

from fake_gateway import sse_turn

from meshharness.core import tools
from meshharness.core.permissions import Mode
from meshharness.protocol import Event
from test_local_session import CATALOG, StubLocal, llama  # noqa: F401


def _hexlist(n=150):
    return "".join(f"{hashlib.sha256(str(i).encode()).hexdigest()}  src/module_{i:03d}.py\n" for i in range(n))


def _code(n=24):
    return "\n".join(f"def step_{i:02d}(record, factor=1.0):\n    out = dict(record)\n"
                     f"    out['value'] = record.get('value', 0) * factor + {i}\n    return out\n"
                     for i in range(n))


def test_dense_text_is_not_undercounted():
    hexes, code = _hexlist(), _code()
    assert tools.est_tokens(hexes) > len(hexes) / 2, "hex runs ~1.3 chars a token, not 4"
    assert len(code) / 6 < tools.est_tokens(code) < len(code) / 2


def test_without_a_limit_reads_are_unchanged(tmp_path):
    (tmp_path / "v1.txt").write_text(_hexlist())
    d = tools.read_file_detail({"path": "v1.txt"}, cwd=tmp_path)
    assert d["whole"] and d["text"] == _hexlist()


def test_a_file_over_the_room_comes_back_as_a_map_not_whole(tmp_path):
    (tmp_path / "v1.txt").write_text(_hexlist())
    d = tools.read_file_detail({"path": "v1.txt"}, cwd=tmp_path, max_tokens=3000)
    assert not d["whole"] and "not returned whole" in d["text"]
    assert tools.est_tokens(d["text"]) < 3000


def test_a_range_is_cut_to_the_room_and_says_where_to_go_on(tmp_path):
    (tmp_path / "v1.txt").write_text(_hexlist())
    d = tools.read_file_detail({"path": "v1.txt", "offset": 1}, cwd=tmp_path, max_tokens=2000)
    assert tools.est_tokens(d["text"]) < 2600 and "call again with offset=" in d["text"]


# ---- in a session -------------------------------------------------------------

LOCAL_ROW = {"id": "local/tiny", "context_length": 16384, "supports_tools": True, "local": True,
             "pricing": {"prompt_usd_per_1m": "0", "completion_usd_per_1m": "0"}}


def _batch_of_reads(names):
    return sse_turn(tool_calls=[{"id": f"r{i}", "name": "read_file", "arguments": json.dumps({"path": n})}
                                for i, n in enumerate(names)])


def _results(h):
    return [e["data"]["result"] for e in h.of(Event.TOOL_RESULT)]


def test_on_a_16k_local_model_one_step_of_reads_cannot_fill_the_window(harness, gateway, llama, tmp_path):
    names = [f"v{i}.txt" for i in range(1, 4)]
    llama.script(_batch_of_reads(names), sse_turn("Done."))
    h = harness(mode=Mode.BYPASS, model="local/tiny", route_mode="off",
                local=StubLocal(llama, window=16384))
    for n in names:
        (h.session.workspace / n).write_text(_hexlist())
    h.session.catalog = [LOCAL_ROW] + CATALOG
    h.session.submit("compare the three lists")
    h.wait_for(Event.TURN_FINISHED, timeout=30)
    out = _results(h)
    assert sum(tools.est_tokens(r) for r in out) <= 16384 * 0.4 + 50
    assert all(tools.est_tokens(r) <= 16384 * 0.2 + 50 for r in out)
    assert any("not read in this step" in r for r in out) or all("not returned whole" in r for r in out)


def test_on_a_big_cloud_window_the_same_reads_come_back_whole(harness, gateway, tmp_path):
    names = [f"v{i}.txt" for i in range(1, 4)]
    gateway.script(_batch_of_reads(names), sse_turn("Done."))
    h = harness(mode=Mode.BYPASS, model="t/cheap")
    for n in names:
        (h.session.workspace / n).write_text(_hexlist())
    h.session.catalog = [{**CATALOG[0], "context_length": 1_000_000}]
    h.session.submit("compare the three lists")
    h.wait_for(Event.TURN_FINISHED, timeout=30)
    sent = [m["content"] for m in gateway.requests[-1]["messages"] if m.get("role") == "tool"]
    assert sent == [_hexlist()] * 3, "a 1M-token window reads these whole, as before"
