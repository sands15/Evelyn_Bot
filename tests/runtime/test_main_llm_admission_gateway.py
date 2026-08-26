from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core import main_inference_contract as admission  # noqa: E402
from evelyn_core import fast_control_api as fast_api  # noqa: E402
from evelyn_core.main_inference_contract import (  # noqa: E402
    MAIN_ADMISSION_KIND_HEADER,
    MAIN_ADMISSION_QUEUE_MS_HEADER,
    MAIN_ADMISSION_RECEIPT_HEADER,
    MAIN_ADMISSION_RECEIPT_VALUE,
    MAIN_ADMISSION_REQUEST_ID_HEADER,
    MAIN_ADMISSION_UPSTREAM_HEADERS_MS_HEADER,
    MAIN_ADMISSION_UPSTREAM_WRITE_MS_HEADER,
    MainAdmissionLease,
    MainRequestKind,
    admitted_main_request,
    main_admission_headers,
    main_request_kind_from_header,
)
from evelyn_core.main_llm_admission_gateway import build_app  # noqa: E402
from evelyn_core.observability_metrics import (  # noqa: E402
    VOICE_LATENCY_TRACE_METRICS_KEY,
    VoiceLatencyTrace,
)
from evelyn_core.voice_route_execution import (  # noqa: E402
    _mark_voice_main_admission,
)


def _headers(kind: MainRequestKind) -> dict[str, str]:
    return main_admission_headers(kind)


class MainAdmissionHeaderTests(unittest.IsolatedAsyncioTestCase):
    def test_request_kind_header_has_one_canonical_wire_form(self) -> None:
        for kind in MainRequestKind:
            headers = main_admission_headers(kind)
            self.assertEqual(main_request_kind_from_header(headers[MAIN_ADMISSION_KIND_HEADER]), kind)
        for invalid in (None, "", "REALTIME", " realtime", "realtime ", "0", "voice"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "kind_invalid"):
                    main_request_kind_from_header(invalid)

    async def test_gateway_client_mode_bypasses_process_lock_and_recovers_admit_time(self) -> None:
        class Response:
            headers = {
                MAIN_ADMISSION_RECEIPT_HEADER: MAIN_ADMISSION_RECEIPT_VALUE,
                MAIN_ADMISSION_REQUEST_ID_HEADER: "a" * 24,
                MAIN_ADMISSION_QUEUE_MS_HEADER: "12.5",
                MAIN_ADMISSION_UPSTREAM_HEADERS_MS_HEADER: "25.0",
                MAIN_ADMISSION_UPSTREAM_WRITE_MS_HEADER: "5.0",
            }

        class RequestContext:
            async def __aenter__(self):
                return Response()

            async def __aexit__(self, *_args):
                return None

        leases = []
        before = asyncio.get_running_loop().time()
        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": "http://gateway/v1/chat/completions",
        }
        with patch.dict("os.environ", env, clear=False), patch.object(
            admission,
            "_acquire_process_lock",
            side_effect=AssertionError("gateway clients must not take the process lock"),
        ):
            async with admitted_main_request(
                RequestContext,
                kind=MainRequestKind.REALTIME,
                on_acquired=leases.append,
            ):
                pass
        after = asyncio.get_running_loop().time()

        self.assertEqual(len(leases), 1)
        self.assertEqual(leases[0].request_id, "a" * 24)
        self.assertEqual(leases[0].queue_ms, 12.5)
        self.assertLessEqual(before - 0.030, leases[0].admitted_at)
        self.assertLessEqual(leases[0].admitted_at, after)
        self.assertAlmostEqual(
            leases[0].raw_request_written_at - leases[0].admitted_at,
            0.005,
            places=6,
        )

    async def test_gateway_mode_rejects_direct_or_malformed_admission_response(self) -> None:
        class RequestContext:
            def __init__(self, headers: dict[str, str]) -> None:
                self.response = type("Response", (), {"headers": headers})()

            async def __aenter__(self):
                return self.response

            async def __aexit__(self, *_args):
                return None

        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": "http://gateway/v1/chat/completions",
        }
        valid = {
            MAIN_ADMISSION_RECEIPT_HEADER: MAIN_ADMISSION_RECEIPT_VALUE,
            MAIN_ADMISSION_REQUEST_ID_HEADER: "b" * 24,
            MAIN_ADMISSION_QUEUE_MS_HEADER: "0",
            MAIN_ADMISSION_UPSTREAM_HEADERS_MS_HEADER: "10",
            MAIN_ADMISSION_UPSTREAM_WRITE_MS_HEADER: "5",
        }
        missing_write = dict(valid)
        missing_write.pop(MAIN_ADMISSION_UPSTREAM_WRITE_MS_HEADER)
        write_after_headers = {
            **valid,
            MAIN_ADMISSION_UPSTREAM_WRITE_MS_HEADER: "11",
        }
        unbounded_write = {
            **valid,
            MAIN_ADMISSION_UPSTREAM_WRITE_MS_HEADER: "nan",
        }
        with patch.dict("os.environ", env, clear=False):
            for headers in (
                {},
                missing_write,
                write_after_headers,
                unbounded_write,
            ):
                with self.subTest(headers=headers):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "main_llm_admission_receipt_invalid",
                    ):
                        async with admitted_main_request(
                            lambda headers=headers: RequestContext(headers),
                            kind=MainRequestKind.REALTIME,
                        ):
                            self.fail("an unattested response must never be yielded")

    async def test_gateway_mode_fast_markers_use_reconstructed_raw_write_time(self) -> None:
        lease = MainAdmissionLease(
            request_id="c" * 24,
            kind=MainRequestKind.REALTIME,
            queue_ms=3.0,
            admitted_at=100.0,
            _task=None,
            raw_request_written_at=100.005,
        )
        trace = VoiceLatencyTrace()
        token = fast_api.FAST_MAIN_LATENCY_TRACE.set(trace)
        try:
            config = fast_api._main_llm_http_trace_config()
            env = {
                "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
                "MAIN_LLM_ADMISSION_GATEWAY_URL": (
                    "http://gateway/v1/chat/completions"
                ),
            }
            with patch.dict("os.environ", env, clear=False):
                await config.on_request_chunk_sent[0]()
                self.assertNotIn(
                    "main_request_written",
                    trace.public_summary()["markers_ms"],
                )
                fast_api.mark_fast_main_admission(lease)
        finally:
            fast_api.FAST_MAIN_LATENCY_TRACE.reset(token)

        markers = trace.public_summary()["markers_ms"]
        self.assertEqual(markers["main_slot_acquired"], 0.0)
        self.assertEqual(markers["main_request_written"], 5.0)

        core_trace = VoiceLatencyTrace()
        _mark_voice_main_admission(
            {VOICE_LATENCY_TRACE_METRICS_KEY: core_trace},
            lease,
        )
        core_markers = core_trace.public_summary()["markers_ms"]
        self.assertEqual(core_markers["main_slot_acquired"], 0.0)
        self.assertEqual(core_markers["main_request_written"], 5.0)

        local_trace = VoiceLatencyTrace()
        local_token = fast_api.FAST_MAIN_LATENCY_TRACE.set(local_trace)
        try:
            with patch.dict(
                "os.environ",
                {"MAIN_LLM_ADMISSION_CLIENT_MODE": "local"},
                clear=False,
            ):
                await config.on_request_chunk_sent[0]()
        finally:
            fast_api.FAST_MAIN_LATENCY_TRACE.reset(local_token)
        self.assertIn(
            "main_request_written",
            local_trace.public_summary()["markers_ms"],
        )


class MainLlmAdmissionGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.order: list[str] = []
        self.release_holder = asyncio.Event()

        async def health(_request: web.Request) -> web.Response:
            return web.json_response({"status": "ok"})

        async def models(_request: web.Request) -> web.Response:
            return web.json_response({"data": [{"id": "main"}]})

        async def chat(request: web.Request) -> web.StreamResponse:
            payload = await request.json()
            label = str(payload.get("label") or "stream")
            self.order.append(label)
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            if label == "holder":
                await self.release_holder.wait()
            await response.write(f"data: {label}-a\n\n".encode())
            await response.write(f"data: {label}-b\n\n".encode())
            await response.write_eof()
            return response

        upstream_app = web.Application()
        upstream_app.router.add_get("/health", health)
        upstream_app.router.add_get("/v1/models", models)
        upstream_app.router.add_post("/v1/chat/completions", chat)
        self.upstream = TestServer(upstream_app)
        await self.upstream.start_server()

        upstream_url = str(self.upstream.make_url("/v1/chat/completions"))
        self.client = TestClient(
            TestServer(
                build_app(
                    upstream_url=upstream_url,
                    backend_epoch_provider=lambda: "test-epoch",
                )
            )
        )
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        self.release_holder.set()
        await self.client.close()
        await self.upstream.close()

    async def test_rejects_missing_duplicate_and_noncanonical_kind_headers(self) -> None:
        cases = (
            None,
            {MAIN_ADMISSION_KIND_HEADER: "REALTIME"},
            [(MAIN_ADMISSION_KIND_HEADER, "realtime"), (MAIN_ADMISSION_KIND_HEADER, "background")],
        )
        for headers in cases:
            with self.subTest(headers=headers):
                response = await self.client.post(
                    "/v1/chat/completions",
                    json={"label": "must-not-reach-upstream"},
                    headers=headers,
                )
                self.assertEqual(response.status, 400)
                await response.read()
        self.assertEqual(self.order, [])

    async def test_gateway_client_mode_blocks_a_direct_raw_upstream_url(self) -> None:
        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": str(
                self.client.make_url("/v1/chat/completions")
            ),
        }
        async with aiohttp.ClientSession() as session:
            with patch.dict("os.environ", env, clear=False):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "main_llm_admission_receipt_invalid",
                ):
                    async with admitted_main_request(
                        lambda: session.post(
                            self.upstream.make_url("/v1/chat/completions"),
                            json={"label": "direct-raw"},
                        ),
                        kind=MainRequestKind.REALTIME,
                    ):
                        self.fail("raw Main must not bypass the gateway")

    async def test_streams_upstream_bytes_and_exposes_content_free_admission_timing(self) -> None:
        response = await self.client.post(
            "/v1/chat/completions",
            json={"label": "stream"},
            headers=_headers(MainRequestKind.REALTIME),
        )
        body = await response.read()

        self.assertEqual(response.status, 200)
        self.assertEqual(body, b"data: stream-a\n\ndata: stream-b\n\n")
        self.assertEqual(response.headers["Content-Type"], "text/event-stream")
        self.assertTrue(response.headers[MAIN_ADMISSION_REQUEST_ID_HEADER])
        self.assertEqual(
            response.headers[MAIN_ADMISSION_RECEIPT_HEADER],
            MAIN_ADMISSION_RECEIPT_VALUE,
        )
        self.assertGreaterEqual(float(response.headers[MAIN_ADMISSION_QUEUE_MS_HEADER]), 0.0)
        self.assertGreaterEqual(
            float(response.headers[MAIN_ADMISSION_UPSTREAM_HEADERS_MS_HEADER]),
            0.0,
        )
        write_ms = float(
            response.headers[MAIN_ADMISSION_UPSTREAM_WRITE_MS_HEADER]
        )
        headers_ms = float(
            response.headers[MAIN_ADMISSION_UPSTREAM_HEADERS_MS_HEADER]
        )
        self.assertGreaterEqual(write_ms, 0.0)
        self.assertLessEqual(write_ms, headers_ms)

    async def test_realtime_overtakes_already_queued_background_globally(self) -> None:
        holder = await self.client.post(
            "/v1/chat/completions",
            json={"label": "holder"},
            headers=_headers(MainRequestKind.INTERACTIVE),
        )
        background_task = asyncio.create_task(
            self.client.post(
                "/v1/chat/completions",
                json={"label": "background"},
                headers=_headers(MainRequestKind.BACKGROUND),
            )
        )
        await asyncio.sleep(0.02)
        realtime_task = asyncio.create_task(
            self.client.post(
                "/v1/chat/completions",
                json={"label": "realtime"},
                headers=_headers(MainRequestKind.REALTIME),
            )
        )
        await asyncio.sleep(0.02)
        self.release_holder.set()
        await holder.read()
        realtime = await asyncio.wait_for(realtime_task, timeout=1)
        await realtime.read()
        background = await asyncio.wait_for(background_task, timeout=1)
        await background.read()

        self.assertEqual(self.order, ["holder", "realtime", "background"])

    async def test_disconnected_client_releases_lane_while_upstream_is_idle(self) -> None:
        holder = await self.client.post(
            "/v1/chat/completions",
            json={"label": "holder"},
            headers=_headers(MainRequestKind.INTERACTIVE),
        )
        holder.close()

        following = await asyncio.wait_for(
            self.client.post(
                "/v1/chat/completions",
                json={"label": "after-disconnect"},
                headers=_headers(MainRequestKind.REALTIME),
            ),
            timeout=1,
        )
        self.assertEqual(following.status, 200)
        await following.read()
        self.assertEqual(self.order[:2], ["holder", "after-disconnect"])

    async def test_upstream_header_timeout_releases_lane_with_receipt(self) -> None:
        release = asyncio.Event()

        async def health(_request: web.Request) -> web.Response:
            return web.json_response({"status": "ok"})

        async def models(_request: web.Request) -> web.Response:
            return web.json_response({"data": []})

        async def chat(request: web.Request) -> web.Response:
            payload = await request.json()
            if payload.get("label") == "hung-before-headers":
                await release.wait()
            return web.json_response({"ok": True})

        upstream_app = web.Application()
        upstream_app.router.add_get("/health", health)
        upstream_app.router.add_get("/v1/models", models)
        upstream_app.router.add_post("/v1/chat/completions", chat)
        upstream = TestServer(upstream_app)
        await upstream.start_server()
        client = TestClient(
            TestServer(
                build_app(
                    upstream_url=str(
                        upstream.make_url("/v1/chat/completions")
                    ),
                    upstream_headers_timeout_sec=0.05,
                    upstream_stream_idle_timeout_sec=0.05,
                    upstream_total_timeout_sec=0.1,
                    client_disconnect_poll_sec=0.01,
                )
            )
        )
        await client.start_server()
        try:
            timed_out = await client.post(
                "/v1/chat/completions",
                json={"label": "hung-before-headers"},
                headers=_headers(MainRequestKind.INTERACTIVE),
            )
            self.assertEqual(timed_out.status, 504)
            self.assertEqual(
                timed_out.headers[MAIN_ADMISSION_RECEIPT_HEADER],
                MAIN_ADMISSION_RECEIPT_VALUE,
            )
            await timed_out.read()

            following = await asyncio.wait_for(
                client.post(
                    "/v1/chat/completions",
                    json={"label": "after-timeout"},
                    headers=_headers(MainRequestKind.REALTIME),
                ),
                timeout=1,
            )
            self.assertEqual(following.status, 200)
            await following.read()
        finally:
            release.set()
            await client.close()
            await upstream.close()

    async def test_body_limit_is_enforced_before_upstream(self) -> None:
        small_client = TestClient(
            TestServer(
                build_app(
                    upstream_url=str(
                        self.upstream.make_url("/v1/chat/completions")
                    ),
                    max_body_bytes=32,
                )
            )
        )
        await small_client.start_server()
        try:
            response = await small_client.post(
                "/v1/chat/completions",
                data=b'{' + b'"x":"' + (b"a" * 64) + b'"}',
                headers={
                    **_headers(MainRequestKind.INTERACTIVE),
                    "Content-Type": "application/json",
                },
            )
            self.assertEqual(response.status, 413)
            await response.read()
        finally:
            await small_client.close()
        self.assertEqual(self.order, [])

    async def test_health_and_models_are_proxied_without_admission(self) -> None:
        health = await self.client.get("/health")
        self.assertEqual(health.status, 200)
        self.assertTrue((await health.json())["upstreamReady"])
        models = await self.client.get("/v1/models")
        self.assertEqual(models.status, 200)
        self.assertEqual((await models.json())["data"][0]["id"], "main")

    async def test_health_fails_closed_when_backend_epoch_is_unavailable(self) -> None:
        client = TestClient(
            TestServer(
                build_app(
                    upstream_url=str(
                        self.upstream.make_url("/v1/chat/completions")
                    ),
                    backend_epoch_provider=lambda: None,
                )
            )
        )
        await client.start_server()
        try:
            response = await client.get("/health")
            payload = await response.json()
            self.assertEqual(response.status, 503)
            self.assertIs(payload["upstreamReady"], True)
            self.assertIs(payload["reservationReady"], False)
            self.assertIs(payload["ok"], False)
        finally:
            await client.close()

    async def test_post_admission_upstream_failure_keeps_gateway_receipt(self) -> None:
        client = TestClient(
            TestServer(
                build_app(
                    upstream_url="http://127.0.0.1:1/v1/chat/completions"
                )
            )
        )
        await client.start_server()
        try:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "main"},
                headers=_headers(MainRequestKind.REALTIME),
            )
            self.assertEqual(response.status, 502)
            self.assertEqual(
                response.headers[MAIN_ADMISSION_RECEIPT_HEADER],
                MAIN_ADMISSION_RECEIPT_VALUE,
            )
            self.assertRegex(
                response.headers[MAIN_ADMISSION_REQUEST_ID_HEADER],
                r"^[a-f0-9]{24}$",
            )
            await response.read()
        finally:
            await client.close()


class MainLlmGatewayComposeContractTests(unittest.TestCase):
    def test_only_gateway_is_reachable_by_runtime_clients(self) -> None:
        source = (REPO_ROOT / "docker-compose.fast-control.yml").read_text(
            encoding="utf-8"
        )
        gateway = source.split("\n  main_llm_gateway:\n", 1)[1].split(
            "\n  tts:", 1
        )[0]
        main = source.split("\n  main_llm:\n", 1)[1].split(
            "\n  router_llm:", 1
        )[0]
        clients = source.split("\n  main_llm:\n", 1)[0]

        self.assertEqual(
            clients.count(
                'LLM_SERVER_URL: "http://main_llm_gateway:9819/v1/chat/completions"'
            ),
            3,
        )
        self.assertEqual(
            clients.count('MAIN_LLM_ADMISSION_CLIENT_MODE: "gateway"'),
            3,
        )
        self.assertNotIn("MAIN_LLM_ADMISSION_LOCK_FILE", clients)
        self.assertIn('expose:\n      - "9819"', gateway)
        self.assertNotIn("ports:", gateway)
        self.assertIn(
            'MAIN_LLM_EPOCH_FILE: "/main-llm-epoch/epoch"',
            gateway,
        )
        self.assertIn(
            'MAIN_LLM_FOREGROUND_RESERVATION_TTL_MS: "900"',
            gateway,
        )
        self.assertIn("- main_llm_epoch:/main-llm-epoch:ro", gateway)
        self.assertIn("payload.get('reservationReady') is True", gateway)
        self.assertIn("payload.get('reservationTtlMs') == 900", gateway)
        self.assertIn(
            'MAIN_LLM_GATEWAY_UPSTREAM_HEADERS_TIMEOUT_SEC: "30"',
            gateway,
        )
        self.assertIn(
            'MAIN_LLM_GATEWAY_STREAM_IDLE_TIMEOUT_SEC: "30"',
            gateway,
        )
        self.assertIn(
            'MAIN_LLM_GATEWAY_UPSTREAM_TOTAL_TIMEOUT_SEC: "300"',
            gateway,
        )
        self.assertIn("- main_llm_admission", gateway)
        self.assertNotIn("- default", gateway)
        self.assertIn("- main_llm_internal", gateway)
        self.assertIn("- main_llm_internal", main)
        self.assertNotIn("ports:", main)
        self.assertEqual(source.count("- main_llm_admission"), 4)
        self.assertIn("main_llm_admission:\n    internal: true", source)
        self.assertIn("main_llm_internal:\n    internal: true", source)


if __name__ == "__main__":
    unittest.main()
