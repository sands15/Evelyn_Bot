from __future__ import annotations

import asyncio
import sys
import time
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

from evelyn_core.main_inference_contract import (  # noqa: E402
    MAIN_FOREGROUND_BACKEND_EPOCH_HEADER,
    MAIN_FOREGROUND_CAPTURE_GENERATION_HEADER,
    MAIN_FOREGROUND_RESERVATION_ID_HEADER,
    MainForegroundReservationBinding,
    MainForegroundReservationRejected,
    MainInferenceLane,
    MainRequestKind,
    admitted_main_request,
    bind_main_foreground_reservation,
    bind_main_realtime_pre_admission,
    cancel_main_foreground,
    main_admission_headers,
    main_foreground_reservation_headers,
    main_foreground_reservation_from_wire,
    main_foreground_reservation_to_wire,
    main_foreground_reservation_ttl_ms,
    reserve_main_foreground,
)
from evelyn_core.main_llm_admission_gateway import build_app  # noqa: E402


class MainForegroundReservationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.backend_epoch = "backend-epoch-1"
        self.order: list[str] = []
        self.release_holder = asyncio.Event()

        async def health(_request: web.Request) -> web.Response:
            return web.json_response({"status": "ok"})

        async def models(_request: web.Request) -> web.Response:
            return web.json_response({"data": [{"id": "main"}]})

        async def chat(request: web.Request) -> web.StreamResponse:
            payload = await request.json()
            label = str(payload.get("label") or "request")
            self.order.append(label)
            response = web.StreamResponse(
                headers={"Content-Type": "text/event-stream"}
            )
            await response.prepare(request)
            if label == "holder":
                await self.release_holder.wait()
            await response.write(f"data: {label}\n\n".encode())
            await response.write_eof()
            return response

        upstream_app = web.Application()
        upstream_app.router.add_get("/health", health)
        upstream_app.router.add_get("/v1/models", models)
        upstream_app.router.add_post("/v1/chat/completions", chat)
        self.upstream = TestServer(upstream_app)
        await self.upstream.start_server()

        self.lane = MainInferenceLane(use_process_lock=False)
        self.gateway = TestClient(
            TestServer(
                build_app(
                    upstream_url=str(
                        self.upstream.make_url("/v1/chat/completions")
                    ),
                    lane=self.lane,
                    backend_epoch_provider=lambda: self.backend_epoch,
                ),
                handler_cancellation=True,
            )
        )
        await self.gateway.start_server()
        self.chat_url = str(self.gateway.make_url("/v1/chat/completions"))
        self.session = aiohttp.ClientSession()

    async def asyncTearDown(self) -> None:
        self.release_holder.set()
        await self.session.close()
        await self.gateway.close()
        await self.upstream.close()

    async def _reserve(self, generation: int):
        return await reserve_main_foreground(
            self.session,
            capture_generation=generation,
            backend_epoch=self.backend_epoch,
            gateway_url=self.chat_url,
        )

    async def _redeem(
        self,
        reservation: MainForegroundReservationBinding,
        label: str,
    ) -> tuple[int, bytes]:
        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": self.chat_url,
        }
        with patch.dict("os.environ", env, clear=False):
            async with admitted_main_request(
                lambda: self.session.post(
                    self.chat_url,
                    json={"label": label},
                    headers=main_foreground_reservation_headers(reservation),
                ),
                kind=MainRequestKind.REALTIME,
                reservation=reservation,
            ) as response:
                return response.status, await response.read()

    async def test_default_ttl_and_strict_content_free_client_contract(self) -> None:
        health = await self.session.get(self.gateway.make_url("/health"))
        health_payload = await health.json()
        self.assertIs(health_payload["reservationReady"], True)
        self.assertEqual(health_payload["reservationTtlMs"], 900)
        reservation = await self._reserve(7)

        self.assertEqual(reservation.ttl_ms, 900)
        self.assertRegex(reservation.reservation_id, r"^[a-f0-9]{32}$")
        self.assertEqual(reservation.capture_generation, 7)
        self.assertEqual(reservation.backend_epoch, self.backend_epoch)
        headers = main_foreground_reservation_headers(reservation)
        self.assertEqual(
            headers[MAIN_FOREGROUND_RESERVATION_ID_HEADER],
            reservation.reservation_id,
        )
        self.assertEqual(headers[MAIN_FOREGROUND_CAPTURE_GENERATION_HEADER], "7")
        self.assertEqual(
            headers[MAIN_FOREGROUND_BACKEND_EPOCH_HEADER],
            self.backend_epoch,
        )
        wire = main_foreground_reservation_to_wire(reservation)
        self.assertEqual(main_foreground_reservation_from_wire(wire), reservation)
        for malformed in (
            {**wire, "text": "private"},
            {**wire, "schema": "wrong"},
            {**wire, "ttlMs": True},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                main_foreground_reservation_from_wire(malformed)
        await cancel_main_foreground(
            self.session,
            reservation,
            gateway_url=self.chat_url,
        )

        self.assertEqual(main_foreground_reservation_ttl_ms(500), 500)
        self.assertEqual(main_foreground_reservation_ttl_ms(1000), 1000)

        for ttl in (499, 1001, True):
            with self.subTest(ttl=ttl), self.assertRaisesRegex(
                ValueError,
                "reservation_ttl_invalid",
            ):
                build_app(
                    upstream_url=str(
                        self.upstream.make_url("/v1/chat/completions")
                    ),
                    lane=MainInferenceLane(use_process_lock=False),
                    reservation_ttl_ms=ttl,
                    backend_epoch_provider=lambda: self.backend_epoch,
                )

    async def test_forgery_binding_mismatch_and_replay_fail_closed(self) -> None:
        reservation = await self._reserve(11)
        forged = MainForegroundReservationBinding(
            reservation_id="f" * 32,
            capture_generation=11,
            backend_epoch=self.backend_epoch,
        )
        with self.assertRaises(MainForegroundReservationRejected):
            await self._redeem(forged, "forged")
        mismatched = MainForegroundReservationBinding(
            reservation_id=reservation.reservation_id,
            capture_generation=12,
            backend_epoch=self.backend_epoch,
        )
        with self.assertRaises(MainForegroundReservationRejected):
            await self._redeem(mismatched, "mismatched")
        self.assertEqual(self.order, [])

        status, body = await self._redeem(reservation, "redeemed")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"data: redeemed\n\n")
        with self.assertRaises(MainForegroundReservationRejected):
            await self._redeem(reservation, "replay")
        self.assertEqual(self.order, ["redeemed"])

    async def test_cancel_and_stale_epoch_reject_then_plain_realtime_fallback(self) -> None:
        cancelled = await self._reserve(20)
        await cancel_main_foreground(
            self.session,
            cancelled,
            gateway_url=self.chat_url,
        )
        with self.assertRaises(MainForegroundReservationRejected):
            await self._redeem(cancelled, "cancelled")

        stale = await self._reserve(21)
        self.backend_epoch = "backend-epoch-2"
        with self.assertRaises(MainForegroundReservationRejected):
            await self._redeem(stale, "stale")

        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": self.chat_url,
        }
        with patch.dict("os.environ", env, clear=False):
            async with admitted_main_request(
                lambda: self.session.post(
                    self.chat_url,
                    json={"label": "fallback"},
                    headers=main_admission_headers(MainRequestKind.REALTIME),
                ),
                kind=MainRequestKind.REALTIME,
            ) as response:
                self.assertEqual(response.status, 200)
                await response.read()
        self.assertEqual(self.order, ["fallback"])

    async def test_context_bound_reservation_redeems_and_typed_rejection_falls_back_once(self) -> None:
        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": self.chat_url,
        }
        redeemed = await self._reserve(22)
        with patch.dict("os.environ", env, clear=False):
            with bind_main_foreground_reservation(redeemed) as use:
                async with admitted_main_request(
                    lambda: self.session.post(
                        self.chat_url,
                        json={"label": "context-redeemed"},
                        headers=main_admission_headers(MainRequestKind.REALTIME),
                    ),
                    kind=MainRequestKind.REALTIME,
                ) as response:
                    self.assertEqual(response.status, 200)
                    await response.read()
                self.assertTrue(use.redeemed)
                self.assertFalse(use.fallback_used)
                async with admitted_main_request(
                    lambda: self.session.post(
                        self.chat_url,
                        json={"label": "context-after-redeem"},
                        headers=main_admission_headers(MainRequestKind.REALTIME),
                    ),
                    kind=MainRequestKind.REALTIME,
                ) as response:
                    self.assertEqual(response.status, 200)
                    await response.read()

        cancelled = await self._reserve(23)
        await cancel_main_foreground(
            self.session,
            cancelled,
            gateway_url=self.chat_url,
        )
        with patch.dict("os.environ", env, clear=False):
            with bind_main_foreground_reservation(cancelled) as use:
                async with admitted_main_request(
                    lambda: self.session.post(
                        self.chat_url,
                        json={"label": "context-fallback"},
                        headers=main_admission_headers(MainRequestKind.REALTIME),
                    ),
                    kind=MainRequestKind.REALTIME,
                ) as response:
                    self.assertEqual(response.status, 200)
                    await response.read()
                self.assertFalse(use.redeemed)
                self.assertTrue(use.fallback_used)

        self.assertEqual(
            self.order,
            [
                "context-redeemed",
                "context-after-redeem",
                "context-fallback",
            ],
        )

    async def test_realtime_pre_admission_activates_once_at_first_main_call(self) -> None:
        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": self.chat_url,
        }
        activation_calls: list[str] = []

        async def activate():
            activation_calls.append("activated")
            return await self._reserve(231)

        async def request(label: str, kind: MainRequestKind) -> None:
            async with admitted_main_request(
                lambda: self.session.post(
                    self.chat_url,
                    json={"label": label},
                    headers=main_admission_headers(kind),
                ),
                kind=kind,
            ) as response:
                self.assertEqual(response.status, 200)
                await response.read()

        with patch.dict("os.environ", env, clear=False):
            with bind_main_realtime_pre_admission(activate) as activation:
                await request("pre-background", MainRequestKind.BACKGROUND)
                self.assertEqual(activation_calls, [])
                await request("pre-redeemed", MainRequestKind.REALTIME)
                await request("pre-after-redeem", MainRequestKind.REALTIME)

        self.assertTrue(activation.attempted)
        self.assertEqual(activation_calls, ["activated"])
        self.assertEqual(
            self.order,
            ["pre-background", "pre-redeemed", "pre-after-redeem"],
        )

    async def test_realtime_pre_admission_none_and_rejection_fall_back_plain(self) -> None:
        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": self.chat_url,
        }

        async def request(label: str) -> None:
            async with admitted_main_request(
                lambda: self.session.post(
                    self.chat_url,
                    json={"label": label},
                    headers=main_admission_headers(MainRequestKind.REALTIME),
                ),
                kind=MainRequestKind.REALTIME,
            ) as response:
                self.assertEqual(response.status, 200)
                await response.read()

        none_calls = 0

        async def activate_none():
            nonlocal none_calls
            none_calls += 1
            return None

        cancelled = await self._reserve(232)
        await cancel_main_foreground(
            self.session,
            cancelled,
            gateway_url=self.chat_url,
        )
        rejected_calls = 0

        async def activate_cancelled():
            nonlocal rejected_calls
            rejected_calls += 1
            return cancelled

        with patch.dict("os.environ", env, clear=False):
            with bind_main_realtime_pre_admission(activate_none):
                await request("pre-none")
            with bind_main_realtime_pre_admission(activate_cancelled):
                await request("pre-rejected")

        self.assertEqual(none_calls, 1)
        self.assertEqual(rejected_calls, 1)
        self.assertEqual(self.order, ["pre-none", "pre-rejected"])

    async def test_realtime_pre_admission_activation_error_fails_before_http(self) -> None:
        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": self.chat_url,
        }

        async def fail_activation():
            raise RuntimeError("activation_transport_failed")

        factory_called = False

        def request_factory():
            nonlocal factory_called
            factory_called = True
            raise AssertionError("HTTP must not open after activation failure")

        with patch.dict("os.environ", env, clear=False):
            with bind_main_realtime_pre_admission(fail_activation):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "activation_transport_failed",
                ):
                    async with admitted_main_request(
                        request_factory,
                        kind=MainRequestKind.REALTIME,
                    ):
                        pass
                with self.assertRaisesRegex(
                    RuntimeError,
                    "main_llm_pre_admission_failed",
                ):
                    async with admitted_main_request(
                        request_factory,
                        kind=MainRequestKind.REALTIME,
                    ):
                        pass

        self.assertFalse(factory_called)

    async def test_realtime_pre_admission_malformed_receipt_poisons_retry(self) -> None:
        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": self.chat_url,
        }
        reservation = await self._reserve(233)
        factory_calls = 0

        async def activate():
            return reservation

        def malformed_factory():
            nonlocal factory_calls
            factory_calls += 1
            return self.session.post(
                self.upstream.make_url("/v1/chat/completions"),
                json={"label": "pre-malformed"},
            )

        with patch.dict("os.environ", env, clear=False):
            with bind_main_realtime_pre_admission(activate):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "main_llm_admission_receipt_invalid",
                ):
                    async with admitted_main_request(
                        malformed_factory,
                        kind=MainRequestKind.REALTIME,
                    ):
                        pass
                with self.assertRaisesRegex(
                    RuntimeError,
                    "main_llm_pre_admission_failed",
                ):
                    async with admitted_main_request(
                        malformed_factory,
                        kind=MainRequestKind.REALTIME,
                    ):
                        pass

        await cancel_main_foreground(
            self.session,
            reservation,
            gateway_url=self.chat_url,
        )
        self.assertEqual(factory_calls, 1)
        self.assertEqual(self.order, ["pre-malformed"])

    async def test_realtime_pre_admission_malformed_binding_poisons_retry(self) -> None:
        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": self.chat_url,
        }
        factory_called = False

        async def malformed_activation():
            return object()

        def request_factory():
            nonlocal factory_called
            factory_called = True
            raise AssertionError("HTTP must not open for malformed activation")

        with patch.dict("os.environ", env, clear=False):
            with bind_main_realtime_pre_admission(malformed_activation):
                with self.assertRaises(AttributeError):
                    async with admitted_main_request(
                        request_factory,
                        kind=MainRequestKind.REALTIME,
                    ):
                        pass
                with self.assertRaisesRegex(
                    RuntimeError,
                    "main_llm_pre_admission_failed",
                ):
                    async with admitted_main_request(
                        request_factory,
                        kind=MainRequestKind.REALTIME,
                    ):
                        pass

        self.assertFalse(factory_called)

    async def test_realtime_pre_admission_orphan_task_cannot_escape_scope(self) -> None:
        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": self.chat_url,
        }
        release = asyncio.Event()
        factory_called = False

        async def activate():
            raise AssertionError("expired scope must fail before activation")

        def request_factory():
            nonlocal factory_called
            factory_called = True
            raise AssertionError("expired scope must fail before HTTP")

        async def orphan() -> None:
            await release.wait()
            async with admitted_main_request(
                request_factory,
                kind=MainRequestKind.REALTIME,
            ):
                pass

        with patch.dict("os.environ", env, clear=False):
            with bind_main_realtime_pre_admission(activate):
                task = asyncio.create_task(orphan())
                await asyncio.sleep(0)
            release.set()
            with self.assertRaisesRegex(
                RuntimeError,
                "main_llm_pre_admission_scope_expired",
            ):
                await task

        self.assertFalse(factory_called)

    async def test_context_ticket_is_realtime_gateway_only_and_claimed_once(self) -> None:
        reservation = await self._reserve(24)
        local_factory_called = False

        def local_factory():
            nonlocal local_factory_called
            local_factory_called = True
            raise AssertionError("local request must fail before opening HTTP")

        with patch.dict(
            "os.environ",
            {"MAIN_LLM_ADMISSION_CLIENT_MODE": "local"},
            clear=False,
        ):
            with bind_main_foreground_reservation(reservation):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "reservation_requires_gateway",
                ):
                    async with admitted_main_request(
                        local_factory,
                        kind=MainRequestKind.REALTIME,
                    ):
                        pass
        self.assertFalse(local_factory_called)

        env = {
            "MAIN_LLM_ADMISSION_CLIENT_MODE": "gateway",
            "MAIN_LLM_ADMISSION_GATEWAY_URL": self.chat_url,
        }
        with patch.dict("os.environ", env, clear=False):
            with bind_main_foreground_reservation(reservation) as use:
                background_headers = main_admission_headers(
                    MainRequestKind.BACKGROUND
                )
                self.assertNotIn(
                    MAIN_FOREGROUND_RESERVATION_ID_HEADER,
                    background_headers,
                )

                async def request(label: str) -> None:
                    async with admitted_main_request(
                        lambda: self.session.post(
                            self.chat_url,
                            json={"label": label},
                            headers=main_admission_headers(
                                MainRequestKind.REALTIME
                            ),
                        ),
                        kind=MainRequestKind.REALTIME,
                    ) as response:
                        self.assertEqual(response.status, 200)
                        await response.read()

                results = await asyncio.gather(
                    asyncio.create_task(request("claim-a")),
                    asyncio.create_task(request("claim-b")),
                    return_exceptions=True,
                )
                self.assertTrue(use.redeemed)
                self.assertFalse(use.fallback_used)
                self.assertEqual(sum(result is None for result in results), 1)
                conflicts = [
                    result
                    for result in results
                    if isinstance(result, RuntimeError)
                ]
                self.assertEqual(len(conflicts), 1)
                self.assertEqual(
                    str(conflicts[0]),
                    "main_llm_foreground_reservation_already_claimed",
                )

        self.assertEqual(len(self.order), 1)
        self.assertIn(self.order[0], {"claim-a", "claim-b"})

    async def test_expiry_automatically_releases_lower_priority_barrier(self) -> None:
        reservation = await self._reserve(30)
        started = time.monotonic()
        background_task = asyncio.create_task(
            self.session.post(
                self.chat_url,
                json={"label": "background-after-expiry"},
                headers=main_admission_headers(MainRequestKind.BACKGROUND),
            )
        )
        await asyncio.sleep(0.05)
        self.assertFalse(background_task.done())
        self.assertEqual(self.order, [])

        realtime = await asyncio.wait_for(
            self.session.post(
                self.chat_url,
                json={"label": "plain-realtime-during-reservation"},
                headers=main_admission_headers(MainRequestKind.REALTIME),
            ),
            timeout=0.2,
        )
        await realtime.read()
        self.assertFalse(background_task.done())
        self.assertEqual(self.order, ["plain-realtime-during-reservation"])

        response = await asyncio.wait_for(background_task, timeout=2)
        await response.read()
        self.assertGreaterEqual(time.monotonic() - started, 0.75)
        self.assertEqual(
            self.order,
            ["plain-realtime-during-reservation", "background-after-expiry"],
        )
        with self.assertRaises(MainForegroundReservationRejected):
            await self._redeem(reservation, "expired-replay")

    async def test_redemption_overtakes_realtime_and_blocks_lower_priority(self) -> None:
        holder = await self.session.post(
            self.chat_url,
            json={"label": "holder"},
            headers=main_admission_headers(MainRequestKind.INTERACTIVE),
        )
        reservation = await self._reserve(40)
        background_task = asyncio.create_task(
            self.session.post(
                self.chat_url,
                json={"label": "background"},
                headers=main_admission_headers(MainRequestKind.BACKGROUND),
            )
        )
        await asyncio.sleep(0.01)
        realtime_task = asyncio.create_task(
            self.session.post(
                self.chat_url,
                json={"label": "plain-realtime"},
                headers=main_admission_headers(MainRequestKind.REALTIME),
            )
        )
        await asyncio.sleep(0.01)
        redemption_task = asyncio.create_task(
            self._redeem(reservation, "reserved-realtime")
        )
        await asyncio.sleep(0.02)
        self.assertEqual(self.order, ["holder"])

        self.release_holder.set()
        await holder.read()
        await asyncio.wait_for(redemption_task, timeout=1)
        realtime = await asyncio.wait_for(realtime_task, timeout=1)
        await realtime.read()
        background = await asyncio.wait_for(background_task, timeout=1)
        await background.read()

        self.assertEqual(
            self.order,
            ["holder", "reserved-realtime", "plain-realtime", "background"],
        )

    async def test_reservation_preempts_active_background_generation(self) -> None:
        background = await self.session.post(
            self.chat_url,
            json={"label": "holder"},
            headers=main_admission_headers(MainRequestKind.BACKGROUND),
        )
        reservation = await self._reserve(41)

        status, body = await asyncio.wait_for(
            self._redeem(reservation, "reserved-after-preempt"),
            timeout=1,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"data: reserved-after-preempt\n\n")
        self.assertEqual(self.order, ["holder", "reserved-after-preempt"])
        background.close()

    async def test_disconnected_queued_redemption_leaves_no_ghost_ticket(self) -> None:
        holder = await self.session.post(
            self.chat_url,
            json={"label": "holder"},
            headers=main_admission_headers(MainRequestKind.INTERACTIVE),
        )
        reservation = await self._reserve(42)
        redemption = asyncio.create_task(
            self._redeem(reservation, "disconnected-redemption")
        )
        for _ in range(100):
            if self.lane._state().foreground_reservation is None:
                break
            await asyncio.sleep(0.005)
        self.assertIsNone(self.lane._state().foreground_reservation)

        redemption.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await redemption
        self.release_holder.set()
        await holder.read()

        following = await asyncio.wait_for(
            self.session.post(
                self.chat_url,
                json={"label": "after-queued-disconnect"},
                headers=main_admission_headers(MainRequestKind.REALTIME),
            ),
            timeout=1,
        )
        await following.read()
        self.assertEqual(self.order, ["holder", "after-queued-disconnect"])

    async def test_control_endpoint_rejects_noncanonical_or_extra_fields(self) -> None:
        path = "/v1/admission/foreground-reservations"
        for body in (
            b'{"captureGeneration":1,"captureGeneration":2,"backendEpoch":"backend-epoch-1"}',
            b'{"captureGeneration":1,"backendEpoch":"backend-epoch-1","text":"private"}',
            b'{"captureGeneration":true,"backendEpoch":"backend-epoch-1"}',
        ):
            with self.subTest(body=body):
                response = await self.session.post(
                    self.gateway.make_url(path),
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(response.status, 400)
                await response.read()
        self.assertEqual(self.order, [])


if __name__ == "__main__":
    unittest.main()
