from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.main_inference_contract import (  # noqa: E402
    MainForegroundReservation,
    MainForegroundReservationRejected,
)
from evelyn_core.voice_main_foreground_runtime import (  # noqa: E402
    cancel_voice_main_foreground,
    try_reserve_voice_main_foreground,
)


class VoiceMainForegroundRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_reservation_uses_exact_epoch_and_records_content_free_state(self) -> None:
        reservation = MainForegroundReservation(
            reservation_id="a" * 32,
            capture_generation=9,
            backend_epoch="epoch-9",
            ttl_ms=900,
        )
        session = object()
        metrics: dict = {"meta": {}}
        with tempfile.TemporaryDirectory() as directory:
            epoch_path = Path(directory) / "epoch"
            epoch_path.write_text("epoch-9", encoding="ascii")
            with (
                patch.dict(
                    os.environ,
                    {"MAIN_LLM_EPOCH_FILE": str(epoch_path)},
                    clear=False,
                ),
                patch(
                    "evelyn_core.voice_main_foreground_runtime.reserve_main_foreground",
                    AsyncMock(return_value=reservation),
                ) as reserve,
                patch(
                    "evelyn_core.voice_main_foreground_runtime.main_admission_client_mode",
                    return_value="gateway",
                ),
            ):
                result = await try_reserve_voice_main_foreground(
                    9,
                    get_http_session=AsyncMock(return_value=session),
                    metrics=metrics,
                )

        self.assertIs(result, reservation)
        reserve.assert_awaited_once_with(
            session,
            capture_generation=9,
            backend_epoch="epoch-9",
        )
        self.assertEqual(
            metrics["meta"]["main_foreground_reservation"],
            {"state": "reserved", "failureType": "", "contentFree": True},
        )
        self.assertNotIn(reservation.reservation_id, repr(metrics))

    async def test_missing_epoch_fails_closed(self) -> None:
        metrics: dict = {"meta": {}}
        with patch.dict(os.environ, {"MAIN_LLM_EPOCH_FILE": ""}, clear=False), patch(
            "evelyn_core.voice_main_foreground_runtime.main_admission_client_mode",
            return_value="gateway",
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "main_llm_backend_epoch_unavailable",
            ):
                await try_reserve_voice_main_foreground(
                    1,
                    get_http_session=AsyncMock(),
                    metrics=metrics,
                )
        self.assertEqual(
            metrics["meta"]["main_foreground_reservation"]["state"],
            "failed",
        )

    async def test_typed_rejection_is_the_only_reservation_fallback(self) -> None:
        metrics: dict = {"meta": {}}
        with (
            patch(
                "evelyn_core.voice_main_foreground_runtime.current_main_llm_backend_epoch",
                return_value="epoch-1",
            ),
            patch(
                "evelyn_core.voice_main_foreground_runtime.reserve_main_foreground",
                AsyncMock(side_effect=MainForegroundReservationRejected("conflict")),
            ),
            patch(
                "evelyn_core.voice_main_foreground_runtime.main_admission_client_mode",
                return_value="gateway",
            ),
        ):
            result = await try_reserve_voice_main_foreground(
                1,
                get_http_session=AsyncMock(return_value=object()),
                metrics=metrics,
            )
        self.assertIsNone(result)
        self.assertEqual(
            metrics["meta"]["main_foreground_reservation"]["state"],
            "rejected",
        )

    async def test_network_and_malformed_receipt_errors_propagate(self) -> None:
        for failure in (ConnectionError("private endpoint"), RuntimeError("bad receipt")):
            metrics: dict = {"meta": {}}
            with (
                patch(
                    "evelyn_core.voice_main_foreground_runtime.current_main_llm_backend_epoch",
                    return_value="epoch-1",
                ),
                patch(
                    "evelyn_core.voice_main_foreground_runtime.reserve_main_foreground",
                    AsyncMock(side_effect=failure),
                ),
                patch(
                    "evelyn_core.voice_main_foreground_runtime.main_admission_client_mode",
                    return_value="gateway",
                ),
            ):
                with self.assertRaises(type(failure)):
                    await try_reserve_voice_main_foreground(
                        1,
                        get_http_session=AsyncMock(return_value=object()),
                        metrics=metrics,
                    )
            state = metrics["meta"]["main_foreground_reservation"]
            self.assertEqual(state["state"], "failed")
            self.assertEqual(state["failureType"], type(failure).__name__)
            self.assertNotIn(str(failure), repr(metrics))

    async def test_local_admission_cannot_create_a_gateway_ticket(self) -> None:
        metrics: dict = {"meta": {}}
        session = AsyncMock()
        with patch(
            "evelyn_core.voice_main_foreground_runtime.main_admission_client_mode",
            return_value="local",
        ), patch(
            "evelyn_core.voice_main_foreground_runtime.reserve_main_foreground",
            AsyncMock(),
        ) as reserve:
            with self.assertRaisesRegex(
                RuntimeError,
                "main_llm_foreground_reservation_requires_gateway",
            ):
                await try_reserve_voice_main_foreground(
                    1,
                    get_http_session=session,
                    metrics=metrics,
                )
        session.assert_not_awaited()
        reserve.assert_not_awaited()
        self.assertEqual(
            metrics["meta"]["main_foreground_reservation"]["failureType"],
            "client_mode",
        )

    async def test_terminal_cancel_never_breaks_voice(self) -> None:
        metrics: dict = {"meta": {}}

        reservation = MainForegroundReservation(
            reservation_id="b" * 32,
            capture_generation=1,
            backend_epoch="epoch-1",
            ttl_ms=900,
        )
        with patch(
            "evelyn_core.voice_main_foreground_runtime.cancel_main_foreground",
            AsyncMock(side_effect=MainForegroundReservationRejected()),
        ):
            await cancel_voice_main_foreground(
                reservation,
                get_http_session=AsyncMock(return_value=object()),
                metrics=metrics,
            )
        self.assertEqual(
            metrics["meta"]["main_foreground_reservation"]["state"],
            "already_terminal",
        )


if __name__ == "__main__":
    unittest.main()
