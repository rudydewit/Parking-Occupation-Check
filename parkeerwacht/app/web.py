"""Webpagina via Ingress: status bekijken, vak tekenen, foto's van wisselingen."""
from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from parking import validate_zone

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
            if path == "/api/raw.jpg":
                return self._jpeg(shared.raw_jpeg)
            match = re.fullmatch(r"/api/history/(\d+)\.jpg", path)
            if match:
                return self._jpeg(shared.history_photo(match.group(1)))
            self._send(b"", "text/plain", 404)

        def do_POST(self):
            if self.client_address[0] not in allowed:
                return self._send(b"", "text/plain", 403)
            if self.path.split("?", 1)[0] != "/api/zone":
                return self._send(b"", "text/plain", 404)
            try:
                length = int(self.headers.get("Content-Length", 0))
                if length > 4096:
                    raise ValueError("te veel gegevens")
                shared.set_zone(validate_zone(json.loads(self.rfile.read(length))["points"]))
            except (ValueError, KeyError, TypeError) as err:
                return self._json({"error": str(err)}, 400)
            self._json({"ok": True})

    server = ThreadingHTTPServer((host, port), Handler)
    server.serve_forever()
