"""The wire contract exists twice: protocol.py and ui/src/types.ts.

A name added on one side and not the other is the most common way the
two drift apart, and nothing else catches it before a user clicks.
"""
import re
from pathlib import Path

from meshharness.protocol import Event, Request

TYPES_TS = Path(__file__).resolve().parents[2] / "ui" / "src" / "types.ts"


def _python_names(cls) -> set:
    return {v for k, v in vars(cls).items()
            if not k.startswith("_") and isinstance(v, str)}


def _ts_names(block: str) -> set:
    src = TYPES_TS.read_text(encoding="utf-8")
    m = re.search(rf"export const {block} = \{{(.*?)\}} as const", src, re.S)
    assert m, f"{block} table not found in {TYPES_TS}"
    return set(re.findall(r":\s*'([^']+)'", m.group(1)))


def test_every_event_is_mirrored_in_types_ts():
    assert _python_names(Event) == _ts_names("Ev")


def test_every_request_is_mirrored_in_types_ts():
    assert _python_names(Request) == _ts_names("Req")
