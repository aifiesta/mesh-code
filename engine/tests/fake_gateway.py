"""A minimal stand-in for the Mesh gateway, speaking real SSE.

Tests drive the engine through httpx against this, rather than monkey-
patching stream_chat. That keeps client.py — the tool-call accumulator, the
in-band error path, the usage/cost tail — inside the tested surface, which
is where the subtle bugs actually live.

Queue up scripted turns with `server.script(...)`; each POST to
/v1/chat/completions pops the next one.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def sse_turn(text: str = "", tool_calls: list | None = None,
             usage: dict | None = None, cost: float | None = None,
             model: str = "test/model", error: str | None = None) -> list[str]:
    """Build the SSE data lines for one assistant turn."""
    lines: list[str] = []
    if error:
        lines.append(json.dumps({"error": {"message": error}}))
        return lines
    for ch in _chunks(text):
        lines.append(json.dumps(
            {"model": model, "choices": [{"delta": {"content": ch}}]}))
    for i, tc in enumerate(tool_calls or []):
        # Stream arguments in fragments, the way real providers do.
        lines.append(json.dumps({"model": model, "choices": [{"delta": {
            "tool_calls": [{"index": i, "id": tc.get("id", f"call_{i}"),
                            "function": {"name": tc["name"], "arguments": ""}}]}}]}))
        for frag in _chunks(tc["arguments"], 24):
            lines.append(json.dumps({"choices": [{"delta": {
                "tool_calls": [{"index": i, "function": {"arguments": frag}}]}}]}))
    lines.append(json.dumps({
        "model": model,
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5,
                           "total_tokens": 15},
        "cost": 0.000123 if cost is None else cost}))
    return lines


def _chunks(s: str, n: int = 8):
    return [s[i:i + n] for i in range(0, len(s), n)] if s else []


class FakeGateway:
    def __init__(self):
        self._turns: list[list[str]] = []
        self.requests: list[dict] = []
        # Billed cost the gateway would report for a request id via
        # GET /usage/requests/{id}. Empty by default so tests that do not
        # care get a 404 and the engine falls through to the SSE `cost`
        # tail / catalog estimate, exactly as it does when offline.
        self.request_costs: dict[str, float] = {}
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_GET(self):
                # /models is what key verification calls, and what the model
                # catalog loads from. Without it the fake could only ever
                # exercise the failure path.
                if "/usage/requests/" in self.path:
                    rid = self.path.rsplit("/", 1)[-1]
                    with outer._lock:
                        cost = outer.request_costs.get(rid)
                    if cost is None:
                        self.send_error(404)
                        return
                    body = json.dumps({
                        "request_id": rid, "outcome": "success",
                        "event": {"request_id": rid,
                                  "cost_usd": f"{cost:.8f}"}}).encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path.endswith("/models"):
                    body = json.dumps({"data": [
                        {"id": "test/model", "context_length": 128000,
                         "pricing": {"prompt_usd_per_1m": 1.0,
                                     "completion_usd_per_1m": 3.0}},
                        {"id": "test/cheap", "context_length": 32000,
                         "pricing": {"prompt_usd_per_1m": 0.1,
                                     "completion_usd_per_1m": 0.2}},
                    ]}).encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length", 0)))
                try:
                    payload = json.loads(body or b"{}")
                except json.JSONDecodeError:
                    payload = {}
                with outer._lock:
                    outer.requests.append(payload)
                    lines = outer._turns.pop(0) if outer._turns else sse_turn("(no script)")
                if self.path.endswith("/web/search"):
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"results": []}).encode())
                    return
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("x-request-id", "req_test_1")
                self.end_headers()
                for line in lines:
                    # "__stall__:N" freezes the stream for N seconds with
                    # NOTHING on the wire — a model thinking silently. The
                    # client must close the socket to end it early.
                    if line.startswith("__stall__:"):
                        deadline = time.time() + float(line.split(":", 1)[1])
                        while time.time() < deadline:
                            time.sleep(0.05)
                            try:
                                self.wfile.write(b"")  # noop
                            except Exception:
                                return
                        continue
                    try:
                        self.wfile.write(f"data: {line}\n\n".encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self._httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def script(self, *turns: list[str]) -> "FakeGateway":
        with self._lock:
            self._turns.extend(turns)
        return self

    def close(self):
        self._httpd.shutdown()
        self._httpd.server_close()
