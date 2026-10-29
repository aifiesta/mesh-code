"""Tool calls that arrived as text instead of structured `tool_calls`.
Small models print the call in their chat template's own notation; the
common shapes are promoted, anything ambiguous or quoted is left alone."""
from __future__ import annotations

import json
import re

_HERMES = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_QWEN_XML = re.compile(r"<function=([\w\-]+)>(.*?)</function>", re.S)
_QWEN_PARAM = re.compile(r"<parameter=([\w\-]+)>(.*?)</parameter>", re.S)
_COHERE = re.compile(r"<\|START_ACTION\|>\s*(\[.*?\])\s*<\|END_ACTION\|>", re.S)
_FENCED_JSON = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)
_FENCE = re.compile(r"```.*?```", re.S)
_EMPTY_WRAPPER = re.compile(r"<tool_call>\s*</tool_call>")
_ANY_MARKER = re.compile(r"<tool_call>|<function=|<\|START_ACTION\|>", re.S)


def looks_like_tool_call(text: str) -> bool:
    if not text:
        return False
    quoted = _quoted_spans(text)
    if any(not _inside(m.start(), quoted) for m in _ANY_MARKER.finditer(text)):
        return True
    return any(_json_call_head(text[a:b]) for a, b in _fence_spans(text))


def extract(text: str, known_tools: set, *, schemas: dict | None = None,
            id_prefix: str = "text") -> tuple[str, list, str | None]:
    """Return (text with the calls removed, calls, reason-if-none).

    Calls are `{"id", "name", "arguments": <json string>}`, the shape the
    accumulator produces for structured calls. `schemas` maps a tool name
    to its JSON-schema `properties`, so XML parameters are typed by the
    tool, not guessed from their text."""
    calls: list = []
    clean = text
    quoted = _quoted_spans(text)
    for pattern, parse in ((_HERMES, _parse_json_obj), (_COHERE, _parse_cohere),
                           (_QWEN_XML, _parse_qwen_xml), (_FENCED_JSON, _parse_json_obj)):
        for m in pattern.finditer(text):
            if _inside(m.start(), quoted):
                continue
            got = parse(m, known_tools, schemas or {})
            if got:
                calls.extend(got)
                clean = clean.replace(m.group(0), "")
    if not calls:
        return text, [], "no recognisable tool call" if looks_like_tool_call(text) else None
    for i, c in enumerate(calls):
        c["id"] = f"{id_prefix}_{i}"
    clean = _EMPTY_WRAPPER.sub("", clean)
    return clean.strip(), calls, None


def _fence_spans(text: str) -> list:
    # One left-to-right pass, so a block's closing fence is never paired
    # with the next block's opening one.
    return [m.span() for m in _FENCE.finditer(text)]


def _quoted_spans(text: str) -> list:
    """Fences that are quoted material: everything except a ```json block
    whose object is a tool call."""
    return [(a, b) for a, b in _fence_spans(text) if not _json_call_head(text[a:b])]


def _json_call_head(block: str) -> bool:
    m = _FENCED_JSON.fullmatch(block)
    if not m:
        return False
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return False
    return isinstance(obj, dict) and "name" in obj


def _inside(pos: int, spans: list) -> bool:
    return any(a <= pos < b for a, b in spans)


def _parse_json_obj(m, known_tools, schemas):
    try:
        obj = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, dict):
        return []
    return _call(obj.get("name"), obj.get("arguments", obj.get("parameters", {})), known_tools)


def _parse_cohere(m, known_tools, schemas):
    try:
        items = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict):
            out += _call(it.get("tool_name") or it.get("name"),
                         it.get("parameters") or it.get("arguments") or {}, known_tools)
    return out


def _parse_qwen_xml(m, known_tools, schemas):
    props = schemas.get(m.group(1)) or {}
    args = {}
    for pm in _QWEN_PARAM.finditer(m.group(2)):
        raw = pm.group(2)
        raw = raw[1:] if raw.startswith("\n") else raw
        raw = raw[:-1] if raw.endswith("\n") else raw
        args[pm.group(1)] = _coerce(raw, (props.get(pm.group(1)) or {}).get("type"))
    return _call(m.group(1), args, known_tools)


def _call(name, args, known_tools) -> list:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return []
    if not isinstance(name, str) or name not in known_tools or not isinstance(args, dict):
        return []
    return [{"name": name, "arguments": json.dumps(args)}]


def _coerce(raw: str, declared: str | None):
    """Only a parameter the tool declares as non-string is interpreted;
    a string stays exactly as written, digits and all."""
    if declared in (None, "string"):
        return raw
    s = raw.strip()
    if declared == "boolean" and s in ("true", "false"):
        return s == "true"
    if declared in ("integer", "number") and re.fullmatch(r"-?\d+(\.\d+)?", s):
        return int(s) if "." not in s else float(s)
    if declared in ("object", "array") and s.startswith(("{", "[")):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
    return raw
