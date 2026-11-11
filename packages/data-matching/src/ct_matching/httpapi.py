"""Loopback-only JSON HTTP API (stdlib ``http.server``). Not a production server.

Safety posture: binds 127.0.0.1 only, validates the Host header (DNS-rebinding defence),
sends no CORS headers, caps body size, accepts only ``application/json`` and never makes
outbound requests.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from .schema import SchemaError
from .service import API_VERSION, RequestError, Service

MAX_BODY_BYTES = 48 * 1024 * 1024
LOOPBACK = "127.0.0.1"


def make_server(service: Service, port: int = 0) -> ThreadingHTTPServer:
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "ct-matching/" + API_VERSION
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: Any) -> None:  # no request logging: bodies may hold profile data
            return

        def _send(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            if status >= 400:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            self.wfile.write(body)

        def _fail(self, status: int, code: str, message: str) -> None:
            self._send(status, {"error": {"code": code, "message": message}})

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
            return host in ("127.0.0.1", "localhost", "::1")

        def _body(self) -> Any:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                raise RequestError("unsupported-media-type", "Content-Type must be application/json", 415)
            try:
                length = int(self.headers.get("Content-Length") or "")
            except ValueError:
                raise RequestError("length-required", "Content-Length header required", 411) from None
            if length < 0 or length > MAX_BODY_BYTES:
                raise RequestError("payload-too-large", f"body exceeds {MAX_BODY_BYTES} bytes", 413)
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
                raise RequestError("malformed-json", f"body is not valid UTF-8 JSON: {type(exc).__name__}") from exc

        def _dispatch(self, method: str) -> None:
            try:
                if not self._host_ok():
                    raise RequestError("forbidden-host", "Host header must be a loopback name", 403)
                parts = urlsplit(self.path)
                pairs = parse_qsl(parts.query, keep_blank_values=True)
                params = dict(pairs)
                if len(params) != len(pairs):
                    raise RequestError("bad-parameter", "repeated query parameter")
                segments = [unquote(s) for s in parts.path.split("/") if s]
                if len(segments) < 2 or segments[0] != "v1":
                    raise RequestError("not-found", "unknown route", 404)
                route, rest = segments[1], segments[2:]
                with lock:
                    if method == "GET" and route == "health" and not rest:
                        out = service.health()
                    elif method == "GET" and route == "jobs" and not rest:
                        out = service.query(params)
                    elif method == "GET" and route == "jobs" and len(rest) == 1:
                        out = service.job(rest[0], params.get("as_of"))
                    elif method == "GET" and route == "provenance" and len(rest) == 1:
                        out = service.provenance(rest[0])
                    elif method == "GET" and route == "skill-vectors" and not rest:
                        out = service.skill_vectors(params.get("as_of"))
                    elif method == "POST" and route == "import" and not rest:
                        out = service.import_doc(self._body())
                    elif method == "POST" and route == "rank" and not rest:
                        out = service.rank(self._body())
                    elif route in ("health", "jobs", "provenance", "skill-vectors", "import", "rank"):
                        raise RequestError("method-not-allowed", f"{method} not allowed here", 405)
                    else:
                        raise RequestError("not-found", "unknown route", 404)
                self._send(200, out)
            except RequestError as exc:
                self._fail(exc.status, exc.code, str(exc))
            except SchemaError as exc:
                self._fail(500, "internal-contract-violation", str(exc)[:300])
            except Exception as exc:  # last resort: never leak a traceback or crash the server thread
                self._fail(500, "internal-error", type(exc).__name__)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802
            self._fail(405, "method-not-allowed", "PUT not allowed")

        do_DELETE = do_PATCH = do_PUT  # noqa: N815

    return ThreadingHTTPServer((LOOPBACK, port), Handler)
