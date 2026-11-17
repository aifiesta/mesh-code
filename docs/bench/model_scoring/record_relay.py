"""A local relay that records what the app actually sends to Mesh.

The app is pointed at this relay (MESHAPI_BASE_URL=http://127.0.0.1:PORT/v1);
every request is forwarded unchanged to the real gateway and the reply is
streamed straight back. One line per model request goes to LOG: the model,
the thinking fields in the request, and the token counts in the reply. The
API key is passed through and never written down.

    python record_relay.py PORT LOG.jsonl [UPSTREAM]

Listens on 127.0.0.1 only."""
import json, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

PORT, LOG = int(sys.argv[1]), sys.argv[2]
UPSTREAM = (sys.argv[3] if len(sys.argv) > 3 else "https://api.meshapi.ai").rstrip("/")
lock = threading.Lock()
client = httpx.Client(timeout=httpx.Timeout(600, connect=20))


def record(row):
    with lock, open(LOG, "a") as f:
        f.write(json.dumps(row) + "\n")


class Relay(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _forward(self, method):
        body = self.rfile.read(int(self.headers.get("content-length") or 0)) if method == "POST" else None
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "content-length", "connection", "accept-encoding")}
        row = None
        if method == "POST" and self.path.endswith("/chat/completions"):
            try:
                req = json.loads(body or b"{}")
            except json.JSONDecodeError:
                req = {}
            row = {"at": time.strftime("%H:%M:%S"), "model": req.get("model"),
                   "sent_reasoning": req.get("reasoning"),
                   "sent_reasoning_effort": req.get("reasoning_effort", "(field absent)"),
                   "messages": len(req.get("messages") or []), "tools": len(req.get("tools") or []),
                   "stream": req.get("stream")}
        started = time.time()
        try:
            with client.stream(method, UPSTREAM + self.path, content=body, headers=headers) as r:
                self.send_response(r.status_code)
                for k, v in r.headers.items():
                    if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding"):
                        self.send_header(k, v)
                self.send_header("connection", "close")
                self.end_headers()
                tail = b""
                for chunk in r.iter_bytes():
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    if row is not None:
                        tail = (tail + chunk)[-200000:]
                if row is not None:
                    row.update(status=r.status_code, request_id=r.headers.get("x-request-id"),
                               seconds=round(time.time() - started, 1))
                    text = tail.decode("utf-8", "replace")
                    thinking = 0
                    for line in text.splitlines():
                        if not line.startswith("data:") or "[DONE]" in line:
                            continue
                        try:
                            ch = json.loads(line[5:])
                        except json.JSONDecodeError:
                            continue
                        if ch.get("error"):
                            row["error"] = json.dumps(ch["error"])[:500]
                        if ch.get("usage"):
                            u = ch["usage"]
                            row.update(input_tokens=u.get("prompt_tokens"),
                                       output_tokens=u.get("completion_tokens"),
                                       reasoning_tokens=(u.get("completion_tokens_details") or {}).get("reasoning_tokens"))
                        for c in ch.get("choices") or []:
                            d = c.get("delta") or {}
                            thinking += len(d.get("reasoning_content") or d.get("reasoning") or "")
                    row["thinking_text_chars"] = thinking
        except Exception as e:                       # the app sees a dropped connection
            if row is not None:
                row["relay_error"] = f"{type(e).__name__}: {e}"[:300]
        finally:
            if row is not None:
                record(row)
            self.close_connection = True

    def do_GET(self):
        self._forward("GET")

    def do_POST(self):
        self._forward("POST")


print(f"relay on http://127.0.0.1:{PORT} -> {UPSTREAM}", flush=True)
ThreadingHTTPServer(("127.0.0.1", PORT), Relay).serve_forever()
