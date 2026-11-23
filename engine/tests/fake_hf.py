"""A stand-in for a Hugging Face file URL: a deterministic blob with Range
support, plus knobs for the failure modes a resumable downloader must survive."""
from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeFileHost:
    def __init__(self, size: int = 2 * 1024 * 1024):
        self.blob = bytes(range(256)) * (size // 256) + bytes(size % 256)
        self.sha256 = hashlib.sha256(self.blob).hexdigest()
        self.size = len(self.blob)
        self.disconnect_at: int | None = None   # close after this many bytes, once
        self.ignore_range = False               # always answer 200 from byte 0
        self.serve_wrong_bytes = False
        self.status: int | None = None      # force this status on every request
        self.requests: list[dict] = []
        self._lock = threading.Lock()
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                data = host.blob if not host.serve_wrong_bytes else bytes(reversed(host.blob))
                rng = self.headers.get("Range")
                with host._lock:
                    host.requests.append({"range": rng})
                    cut = host.disconnect_at
                    host.disconnect_at = None
                if host.status:
                    self.send_response(host.status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = 0
                if rng and not host.ignore_range:
                    start = int(rng.split("=")[1].split("-")[0])
                    if start >= len(data):
                        self.send_response(416)
                        self.end_headers()
                        return
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
                else:
                    self.send_response(200)
                body = data[start:]
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if cut is not None:
                    self.wfile.write(body[:max(0, cut - start)])
                    self.wfile.flush()
                    self.connection.close()
                    return
                self.wfile.write(body)

        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self._srv.server_address[1]}/model.gguf"

    def close(self):
        self._srv.shutdown()
        self._srv.server_close()
