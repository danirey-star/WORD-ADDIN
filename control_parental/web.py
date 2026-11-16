"""Servidor HTTP mínimo (solo biblioteca estándar) compartido por agente y servidor."""
from __future__ import annotations

import json
import logging
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("control_parental")
DASHBOARD = Path(__file__).parent / "web" / "dashboard.html"


class ApiError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    app = None  # se asigna con make_server

    def log_message(self, fmt, *args):
        log.debug("http: " + fmt, *args)

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _dispatch(self, method: str):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = None
        if method in ("POST", "PUT"):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 20_000_000:
                return self._json(413, {"error": "demasiado grande"})
            raw = self.rfile.read(n) if n else b""
            try:
                body = json.loads(raw or b"{}")
            except ValueError:
                return self._json(400, {"error": "JSON no válido"})
        try:
            if method == "GET" and url.path in ("/", "/index.html"):
                return self._send(200, DASHBOARD.read_bytes(), "text/html; charset=utf-8")
            result = self.app.handle(method, url.path, q, body, self)
            self._json(200, result)
        except ApiError as e:
            self._json(e.status, {"error": str(e)})
        except (KeyError, ValueError) as e:
            self._json(400, {"error": f"petición no válida: {e}"})
        except Exception as e:  # pragma: no cover
            log.exception("Error en %s %s", method, url.path)
            self._json(500, {"error": str(e)})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def is_local(self) -> bool:
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def make_server(app, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv


def parse_date(q: dict) -> date:
    return date.fromisoformat(q["date"]) if q.get("date") else date.today()
