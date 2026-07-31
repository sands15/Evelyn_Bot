from __future__ import annotations

import http.client
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents
    if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.vision_ingress_proxy import (  # noqa: E402
    VISION_INGRESS_SCHEMA,
    VISION_RUNTIME_HOST,
    VISION_RUNTIME_PORT,
    VisionIngressConfig,
    build_vision_ingress_server,
)


class RecordingUpstreamHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []
    response_body = b'{"ok":true,"source":"runtime"}'

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _respond(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Upstream-Secret", "must-not-pass")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        type(self).requests.append(
            {"method": "GET", "path": self.path, "headers": dict(self.headers)}
        )
        self._respond(type(self).response_body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length)
        type(self).requests.append(
            {
                "method": "POST",
                "path": self.path,
                "headers": dict(self.headers),
                "body": body,
            }
        )
        self._respond(body)


class VisionIngressProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        RecordingUpstreamHandler.requests = []
        RecordingUpstreamHandler.response_body = (
            b'{"ok":true,"source":"runtime"}'
        )
        self.upstream = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            RecordingUpstreamHandler,
        )
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever,
            daemon=True,
        )
        self.upstream_thread.start()
        self.gateway = build_vision_ingress_server(
            VisionIngressConfig(
                listen_host="127.0.0.1",
                listen_port=0,
                upstream_host="127.0.0.1",
                upstream_port=self.upstream.server_address[1],
                upstream_timeout_seconds=1.0,
                max_request_bytes=1024,
                max_response_bytes=1024,
                max_concurrent_requests=4,
            )
        )
        self.gateway_thread = threading.Thread(
            target=self.gateway.serve_forever,
            daemon=True,
        )
        self.gateway_thread.start()

    def tearDown(self) -> None:
        self.gateway.shutdown()
        self.gateway.server_close()
        self.gateway_thread.join(timeout=2)
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=2)

    def _connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(
            "127.0.0.1",
            self.gateway.server_address[1],
            timeout=2,
        )

    def test_health_is_proxied_without_upstream_private_headers(self) -> None:
        connection = self._connection()
        connection.request("GET", "/health")
        response = connection.getresponse()
        body = response.read()
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(body), {"ok": True, "source": "runtime"})
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertIsNone(response.getheader("X-Upstream-Secret"))
        self.assertEqual(len(RecordingUpstreamHandler.requests), 1)
        recorded = RecordingUpstreamHandler.requests[0]
        self.assertEqual(recorded["method"], "GET")
        self.assertEqual(recorded["path"], "/health")
        headers = {
            str(name).lower(): str(value)
            for name, value in dict(recorded["headers"]).items()
        }
        self.assertEqual(headers["accept-encoding"], "identity")
        self.assertNotIn("authorization", headers)

    def test_allowed_post_forwards_body_and_only_allowlisted_headers(self) -> None:
        payload = b'{"image_base64":"safe-fixture"}'
        connection = self._connection()
        connection.request(
            "POST",
            "/v1/vision/analyze",
            body=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer must-not-pass",
                "X-Forwarded-Host": "attacker.invalid",
            },
        )
        response = connection.getresponse()
        body = response.read()
        connection.close()

        self.assertEqual(response.status, 200)
        self.assertEqual(body, payload)
        recorded = RecordingUpstreamHandler.requests[0]
        headers = {
            str(name).lower(): str(value)
            for name, value in dict(recorded["headers"]).items()
        }
        self.assertEqual(recorded["path"], "/v1/vision/analyze")
        self.assertEqual(recorded["body"], payload)
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["accept"], "application/json")
        self.assertNotIn("authorization", headers)
        self.assertNotIn("x-forwarded-host", headers)

    def test_paths_queries_absolute_urls_and_methods_fail_closed(self) -> None:
        cases = (
            ("GET", "/"),
            ("GET", "/health?target=http://example.com"),
            ("GET", "http://example.com/health"),
            ("POST", "/health"),
            ("PUT", "/v1/vision/analyze"),
        )
        for method, path in cases:
            with self.subTest(method=method, path=path):
                connection = self._connection()
                connection.request(
                    method,
                    path,
                    body=b"{}" if method == "POST" else None,
                    headers={"Connection": "close"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                connection.close()
                self.assertIn(response.status, {404, 501})
                self.assertEqual(payload["schema"], VISION_INGRESS_SCHEMA)
                self.assertEqual(
                    payload["error"],
                    "vision_gateway_request_rejected",
                )
                self.assertTrue(payload["contentFree"])
        self.assertEqual(RecordingUpstreamHandler.requests, [])

    def test_oversized_request_is_rejected_before_body_read(self) -> None:
        connection = self._connection()
        connection.putrequest("POST", "/v1/vision/analyze")
        connection.putheader("Content-Length", "1025")
        connection.putheader("Connection", "close")
        connection.endheaders()
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()

        self.assertEqual(response.status, 413)
        self.assertEqual(payload["error"], "vision_gateway_request_too_large")
        self.assertEqual(RecordingUpstreamHandler.requests, [])

    def test_oversized_response_and_unavailable_upstream_are_content_free(
        self,
    ) -> None:
        RecordingUpstreamHandler.response_body = b"x" * 1025
        connection = self._connection()
        connection.request("GET", "/health")
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 502)
        self.assertEqual(payload["error"], "vision_gateway_response_too_large")

        unavailable = build_vision_ingress_server(
            VisionIngressConfig(
                listen_host="127.0.0.1",
                listen_port=0,
                upstream_host="127.0.0.1",
                upstream_port=1,
                upstream_timeout_seconds=0.1,
            )
        )
        thread = threading.Thread(target=unavailable.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                unavailable.server_address[1],
                timeout=2,
            )
            connection.request("GET", "/health")
            response = connection.getresponse()
            body = response.read()
            connection.close()
        finally:
            unavailable.shutdown()
            unavailable.server_close()
            thread.join(timeout=2)
        self.assertEqual(response.status, 502)
        self.assertEqual(
            json.loads(body),
            {
                "schema": VISION_INGRESS_SCHEMA,
                "ok": False,
                "error": "vision_upstream_unavailable",
                "contentFree": True,
            },
        )
        self.assertNotIn(b"127.0.0.1", body)

    def test_production_upstream_is_fixed_not_environment_driven(self) -> None:
        self.assertEqual(VISION_RUNTIME_HOST, "vision_runtime")
        self.assertEqual(VISION_RUNTIME_PORT, 8891)


if __name__ == "__main__":
    unittest.main()
