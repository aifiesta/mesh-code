#!/usr/bin/env python3
"""Pretends to be llama-server: answers /health with 503 for FAKE_LLAMA_LOAD_S
seconds then 200, checks the api key on POST, and exits on its own after
FAKE_LLAMA_CRASH_AFTER_S seconds when set."""
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


def main():
    args = sys.argv[1:]
    port = int(args[args.index("--port") + 1])
    key = open(args[args.index("--api-key-file") + 1]).read().strip()
    started = time.time()
    load_s = float(os.environ.get("FAKE_LLAMA_LOAD_S", "0"))
    crash_s = os.environ.get("FAKE_LLAMA_CRASH_AFTER_S")
    print("fake llama-server starting", flush=True)

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/health":
                if time.time() - started < load_s:
                    self._send(503, {"error": {"message": "Loading model"}})
                else:
                    self._send(200, {"status": "ok"})
            elif self.path == "/props":
                self._send(200, {"model_path": args[args.index("-m") + 1],
                                 "default_generation_settings": {"n_ctx": int(args[args.index("-c") + 1])}})
            else:
                self._send(404, {})

        def do_POST(self):
            if self.headers.get("Authorization") != f"Bearer {key}":
                self._send(401, {"error": {"message": "bad key"}})
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or b"{}") or b"{}")
            usage = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}
            if body.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for chunk in ({"choices": [{"delta": {"content": "hello from llama"}}]},
                              {"choices": [], "usage": usage, "timings": {"predicted_per_second": 42.0}}):
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                return
            self._send(200, {"choices": [{"message": {"role": "assistant", "content": "hello from llama"}}],
                             "usage": usage})

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = HTTPServer(("127.0.0.1", port), H)
    signal.signal(signal.SIGTERM, lambda *a: (srv.shutdown(), sys.exit(0)))
    if crash_s:
        threading.Timer(float(crash_s), lambda: os._exit(3)).start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    while True:
        time.sleep(0.2)


if __name__ == "__main__":
    main()
