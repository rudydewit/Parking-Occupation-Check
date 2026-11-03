"""Webpagina via Ingress: status bekijken, foto's van wisselingen."""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HTML = (Path(__file__).parent / "index.html").read_bytes()
INGRESS_PROXY = "172.30.32.2"


def serve(shared, port: int = 8099, allowed=(INGRESS_PROXY,), host: str = "0.0.0.0"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body: bytes, content_type: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200):
            self._send(json.dumps(data).encode(), "application/json", status)

        def _jpeg(self, data):
            if data is None:
                self._send(b"", "text/plain", 404)
            else:
                self._send(data, "image/jpeg")

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/health":
                return self._send(b"ok", "text/plain")
            if self.client_address[0] not in allowed:
                return self._send(b"", "text/plain", 403)
            if path == "/":
                return self._send(HTML, "text/html; charset=utf-8")
            if path == "/api/state":
                return self._json(shared.state_dict())
            if path == "/api/annotated.jpg":
                return self._jpeg(shared.annotated_jpeg)
            match = re.fullmatch(r"/api/history/(\d+)\.jpg", path)
            if match:
                return self._jpeg(shared.history_photo(match.group(1)))
            self._send(b"", "text/plain", 404)

    server = ThreadingHTTPServer((host, port), Handler)
    server.serve_forever()
