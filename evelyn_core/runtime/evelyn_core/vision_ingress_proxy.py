from __future__ import annotations

import http.client
import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit


VISION_INGRESS_SCHEMA = "vision.ingress.v1"
VISION_RUNTIME_HOST = "vision_runtime"
VISION_RUNTIME_PORT = 8891
VISION_INGRESS_HOST = "0.0.0.0"
VISION_INGRESS_PORT = 8891
MAX_REQUEST_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
UPSTREAM_TIMEOUT_SECONDS = 180.0
MAX_CONCURRENT_REQUESTS = 32
_ALLOWED_METHODS_BY_PATH = {
    "/health": frozenset({"GET"}),
    "/v1/vision/describe": frozenset({"POST"}),
    "/v1/vision/ocr": frozenset({"POST"}),
    "/v1/vision/analyze": frozenset({"POST"}),
    "/v1/vision/ocr/unload": frozenset({"POST"}),
}
_REQUEST_HEADERS = frozenset({"accept", "content-type"})
_RESPONSE_HEADERS = frozenset({"content-type"})


@dataclass(frozen=True)
class VisionIngressConfig:
    listen_host: str = VISION_INGRESS_HOST
    listen_port: int = VISION_INGRESS_PORT
    upstream_host: str = VISION_RUNTIME_HOST
    upstream_port: int = VISION_RUNTIME_PORT
    upstream_timeout_seconds: float = UPSTREAM_TIMEOUT_SECONDS
    max_request_bytes: int = MAX_REQUEST_BYTES
    max_response_bytes: int = MAX_RESPONSE_BYTES
    max_concurrent_requests: int = MAX_CONCURRENT_REQUESTS


class VisionIngressServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        config: VisionIngressConfig,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.vision_config = config
        self._request_slots = threading.BoundedSemaphore(
            max(1, config.max_concurrent_requests)
        )

    def process_request(
        self,
        request: Any,
        client_address: Any,
    ) -> None:
        if not self._request_slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._request_slots.release()
            raise

    def process_request_thread(
        self,
        request: Any,
        client_address: Any,
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()


class VisionIngressHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "EvelynVisionIngress/1"
    sys_version = ""

    @property
    def config(self) -> VisionIngressConfig:
        return self.server.vision_config  # type: ignore[attr-defined]

    def log_message(self, _format: str, *args: Any) -> None:
        status = str(args[1]) if len(args) > 1 else "unknown"
        print(f"[VISION INGRESS] request_complete status={status}", flush=True)

    def send_error(
        self,
        code: int,
        message: str | None = None,
        explain: str | None = None,
    ) -> None:
        del message, explain
        self._write_json(code, "vision_gateway_request_rejected")

    def _write_json(self, status: int, code: str) -> None:
        body = json.dumps(
            {
                "schema": VISION_INGRESS_SCHEMA,
                "ok": False,
                "error": code,
                "contentFree": True,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _validated_path(self) -> str:
        parsed = urlsplit(self.path)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
            return ""
        methods = _ALLOWED_METHODS_BY_PATH.get(parsed.path)
        if methods is None or self.command not in methods:
            return ""
        return parsed.path

    def _read_body(self) -> bytes | None:
        transfer_encoding = str(self.headers.get("Transfer-Encoding") or "")
        if transfer_encoding:
            self._write_json(400, "vision_gateway_request_rejected")
            return None
        raw_length = self.headers.get("Content-Length")
        if self.command == "GET":
            if raw_length not in {None, "", "0"}:
                self._write_json(400, "vision_gateway_request_rejected")
                return None
            return b""
        try:
            length = int(str(raw_length or ""))
        except ValueError:
            self._write_json(411, "vision_gateway_request_rejected")
            return None
        if length < 0 or length > self.config.max_request_bytes:
            self._write_json(413, "vision_gateway_request_too_large")
            return None
        body = self.rfile.read(length)
        if len(body) != length:
            self._write_json(400, "vision_gateway_request_rejected")
            return None
        return body

    def _request_headers(self) -> dict[str, str]:
        return {
            name: value
            for name, value in self.headers.items()
            if name.lower() in _REQUEST_HEADERS
        }

    def _proxy(self) -> None:
        path = self._validated_path()
        if not path:
            self._write_json(404, "vision_gateway_request_rejected")
            return
        body = self._read_body()
        if body is None:
            return
        connection = http.client.HTTPConnection(
            self.config.upstream_host,
            self.config.upstream_port,
            timeout=self.config.upstream_timeout_seconds,
        )
        try:
            connection.request(
                self.command,
                path,
                body=body if self.command == "POST" else None,
                headers=self._request_headers(),
            )
            response = connection.getresponse()
            response_body = response.read(self.config.max_response_bytes + 1)
            if len(response_body) > self.config.max_response_bytes:
                self._write_json(502, "vision_gateway_response_too_large")
                return
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() in _RESPONSE_HEADERS:
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(response_body)
        except (OSError, TimeoutError, http.client.HTTPException):
            self._write_json(502, "vision_upstream_unavailable")
        finally:
            connection.close()

    def do_GET(self) -> None:
        self._proxy()

    def do_POST(self) -> None:
        self._proxy()


def build_vision_ingress_server(
    config: VisionIngressConfig | None = None,
) -> VisionIngressServer:
    selected = config or VisionIngressConfig()
    return VisionIngressServer(
        (selected.listen_host, selected.listen_port),
        VisionIngressHandler,
        config=selected,
    )


def main() -> None:
    server = build_vision_ingress_server()
    print(
        "[VISION INGRESS] ready "
        f"listen={VISION_INGRESS_HOST}:{VISION_INGRESS_PORT} "
        f"upstream={VISION_RUNTIME_HOST}:{VISION_RUNTIME_PORT}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
