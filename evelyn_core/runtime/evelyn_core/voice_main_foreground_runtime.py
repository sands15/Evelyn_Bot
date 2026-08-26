from __future__ import annotations

from typing import Any, Awaitable, Callable, MutableMapping

from .main_inference_contract import (
    MainForegroundReservation,
    MainForegroundReservationBinding,
    MainForegroundReservationRejected,
    cancel_main_foreground,
    current_main_llm_backend_epoch,
    main_admission_client_mode,
    reserve_main_foreground,
)


def _record_state(
    metrics: MutableMapping[str, Any] | None,
    state: str,
    *,
    failure_type: str = "",
) -> None:
    if metrics is None:
        return
    metrics.setdefault("meta", {})["main_foreground_reservation"] = {
        "state": state,
        "failureType": failure_type,
        "contentFree": True,
    }


async def try_reserve_voice_main_foreground(
    capture_generation: int,
    *,
    get_http_session: Callable[[], Awaitable[Any]],
    metrics: MutableMapping[str, Any] | None = None,
) -> MainForegroundReservation | None:
    if main_admission_client_mode() != "gateway":
        _record_state(metrics, "failed", failure_type="client_mode")
        raise RuntimeError("main_llm_foreground_reservation_requires_gateway")
    backend_epoch = current_main_llm_backend_epoch()
    if not backend_epoch:
        _record_state(metrics, "failed", failure_type="backend_epoch")
        raise RuntimeError("main_llm_backend_epoch_unavailable")
    try:
        reservation = await reserve_main_foreground(
            await get_http_session(),
            capture_generation=capture_generation,
            backend_epoch=backend_epoch,
        )
    except MainForegroundReservationRejected:
        _record_state(metrics, "rejected")
        return None
    except Exception as exc:
        _record_state(metrics, "failed", failure_type=type(exc).__name__)
        raise
    _record_state(metrics, "reserved")
    return reservation


async def cancel_voice_main_foreground(
    reservation: MainForegroundReservationBinding,
    *,
    get_http_session: Callable[[], Awaitable[Any]],
    metrics: MutableMapping[str, Any] | None = None,
) -> None:
    try:
        await cancel_main_foreground(await get_http_session(), reservation)
    except MainForegroundReservationRejected:
        _record_state(metrics, "already_terminal")
    except Exception as exc:
        _record_state(metrics, "cancel_failed", failure_type=type(exc).__name__)
    else:
        _record_state(metrics, "cancelled")


__all__ = [
    "cancel_voice_main_foreground",
    "try_reserve_voice_main_foreground",
]
