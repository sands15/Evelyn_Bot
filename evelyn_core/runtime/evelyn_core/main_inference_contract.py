from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
import secrets
import threading
import time
import weakref
from collections import deque
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from .paths import get_runtime_artifacts_root
from .text import clean_text


PROMPT_ABI_SCHEMA = "evelyn.main-prompt-abi.v2"
PROMPT_CONTENT_VERSION = "evelyn.openai-chat-content.v1"
PROMPT_ORDERING_POLICY = "stable-system_context_dialogue_dynamic_current-user.v1"

_CONTEXT_GROUP_MARKERS = tuple(
    f"\n\n[{title}]\n"
    for title in (
        "Pinned Memory",
        "Conversation State",
        "Retrieved Memory",
        "Runtime State",
        "Tool Use Policy",
        "Skill / Capability Context",
        "Vision Context",
    )
)
_SHA256_RE = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_PROMPT_ABI_SENTINEL = "evelyn-prompt-abi-sentinel-v1"
_MAIN_LLM_EPOCH_PATTERN = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z",
    re.ASCII,
)
_PRIORITY_AGING_SEC = 2.0
_ADMISSION_TIMEOUT_SEC = {
    0: 15.0,
    1: 60.0,
    2: 30.0,
    3: 120.0,
}
MAIN_ADMISSION_KIND_HEADER = "X-Evelyn-Main-Request-Kind"
MAIN_ADMISSION_QUEUE_MS_HEADER = "X-Evelyn-Main-Queue-Ms"
MAIN_ADMISSION_REQUEST_ID_HEADER = "X-Evelyn-Main-Request-Id"
MAIN_ADMISSION_UPSTREAM_HEADERS_MS_HEADER = "X-Evelyn-Main-Upstream-Headers-Ms"
MAIN_ADMISSION_RECEIPT_HEADER = "X-Evelyn-Main-Admission-Receipt"
MAIN_ADMISSION_RECEIPT_VALUE = "evelyn.main-admission.v1"
MAIN_ADMISSION_UPSTREAM_WRITE_MS_HEADER = "X-Evelyn-Main-Upstream-Write-Ms"
MAIN_FOREGROUND_RESERVATION_SCHEMA = "evelyn.main-foreground-reservation.v1"
MAIN_FOREGROUND_RESERVATION_PATH = "/v1/admission/foreground-reservations"
MAIN_FOREGROUND_RESERVATION_CANCEL_PATH = (
    "/v1/admission/foreground-reservations/cancel"
)
MAIN_FOREGROUND_RESERVATION_ID_HEADER = "X-Evelyn-Main-Reservation-Id"
MAIN_FOREGROUND_CAPTURE_GENERATION_HEADER = "X-Evelyn-Capture-Generation"
MAIN_FOREGROUND_BACKEND_EPOCH_HEADER = "X-Evelyn-Main-Backend-Epoch"
MAIN_FOREGROUND_RESERVATION_RESULT_HEADER = "X-Evelyn-Main-Reservation-Result"
DEFAULT_MAIN_FOREGROUND_RESERVATION_TTL_MS = 900
MIN_MAIN_FOREGROUND_RESERVATION_TTL_MS = 500
MAX_MAIN_FOREGROUND_RESERVATION_TTL_MS = 1000
_MAIN_ADMISSION_CLIENT_MODES = frozenset({"local", "gateway"})
_MAIN_ADMISSION_REQUEST_ID_RE = re.compile(r"[a-f0-9]{24}\Z", re.ASCII)
_MAIN_FOREGROUND_RESERVATION_ID_RE = re.compile(r"[a-f0-9]{32}\Z", re.ASCII)
_MAX_CAPTURE_GENERATION = (1 << 63) - 1
_MAX_ADMISSION_RECEIPT_DURATION_MS = 300_000.0


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _exact_sha256_env(name: str) -> str | None:
    value = clean_text(os.getenv(name, "")).casefold()
    return value if _SHA256_RE.fullmatch(value) is not None else None


def main_prompt_exact_identity_required() -> bool:
    configured = clean_text(
        os.getenv("MAIN_LLM_REQUIRE_EXACT_PROMPT_ABI", "")
    ).casefold()
    if configured:
        if configured in {"1", "true", "yes", "on"}:
            return True
        if configured in {"0", "false", "no", "off"}:
            return False
        raise ValueError("main_llm_exact_prompt_abi_config_invalid")
    return bool(
        clean_text(os.getenv("MAIN_LLM_IDENTITY_FILE", ""))
        or any(
            clean_text(os.getenv(name, ""))
            for name in (
                "MAIN_LLM_MODEL_SHA256",
                "MAIN_LLM_TOKENIZER_SHA256",
                "MAIN_LLM_CHAT_TEMPLATE_SHA256",
                "MAIN_LLM_SERVER_SHA256",
                "MAIN_LLM_SERVER_IDENTITY_FILE",
                "MAIN_LLM_RUNTIME_TEMPLATE_SHA256",
                "MAIN_LLM_RUNTIME_TEMPLATE_IDENTITY_FILE",
            )
        )
    )


def _exact_sha256_file(path_env_name: str) -> str | None:
    configured_path = clean_text(os.getenv(path_env_name, ""))
    if not configured_path:
        return None
    try:
        raw_value = Path(configured_path).read_text(encoding="ascii")
    except (OSError, UnicodeError):
        return None
    value = clean_text(raw_value).casefold()
    return value if _SHA256_RE.fullmatch(value) is not None else None


def _exact_runtime_identity_shas(
    normalized_format: str,
) -> tuple[str | None, str | None, str | None, str | None]:
    model_sha = _exact_sha256_env("MAIN_LLM_MODEL_SHA256")
    tokenizer_sha = _exact_sha256_env("MAIN_LLM_TOKENIZER_SHA256")
    template_sha = _exact_sha256_env("MAIN_LLM_CHAT_TEMPLATE_SHA256")
    server_sha = _exact_sha256_env(
        "MAIN_LLM_SERVER_SHA256"
    ) or _exact_sha256_file("MAIN_LLM_SERVER_IDENTITY_FILE")
    runtime_template_sha = _exact_sha256_env(
        "MAIN_LLM_RUNTIME_TEMPLATE_SHA256"
    ) or _exact_sha256_file("MAIN_LLM_RUNTIME_TEMPLATE_IDENTITY_FILE")
    server_runtime_sha = (
        _sha256_text(
            "llama-server-runtime:"
            f"{server_sha}:{runtime_template_sha}"
        )
        if server_sha and runtime_template_sha
        else None
    )
    if all((model_sha, tokenizer_sha, template_sha, server_runtime_sha)):
        return model_sha, tokenizer_sha, template_sha, server_runtime_sha

    identity_path = clean_text(os.getenv("MAIN_LLM_IDENTITY_FILE", ""))
    embedded = clean_text(
        os.getenv("MAIN_LLM_PROMPT_ASSETS_EMBEDDED", "")
    ).casefold() in {"1", "true", "yes"}
    if not embedded or not runtime_template_sha:
        return model_sha, tokenizer_sha, template_sha, server_runtime_sha
    exact_model_sha = model_sha
    if exact_model_sha is None and identity_path:
        try:
            raw_model_sha = Path(identity_path).read_text(
                encoding="ascii",
            )
        except (OSError, UnicodeError):
            raw_model_sha = ""
        exact_model_sha = clean_text(raw_model_sha).casefold()
    if exact_model_sha is None:
        return model_sha, tokenizer_sha, template_sha, server_runtime_sha
    if _SHA256_RE.fullmatch(exact_model_sha) is None:
        return model_sha, tokenizer_sha, template_sha, server_runtime_sha
    return (
        exact_model_sha,
        _sha256_text(f"embedded-tokenizer:{exact_model_sha}"),
        _sha256_text(
            "embedded-chat-template:"
            f"{exact_model_sha}:{normalized_format}:{runtime_template_sha}"
        ),
        server_runtime_sha,
    )


def current_main_llm_backend_epoch() -> str | None:
    """Return the configured Main backend epoch, failing closed when invalid."""

    configured = clean_text(os.getenv("MAIN_LLM_EPOCH_FILE", ""))
    if not configured:
        return None
    try:
        value = Path(configured).read_text(encoding="ascii")[:129].strip()
    except (OSError, UnicodeError):
        return ""
    if _MAIN_LLM_EPOCH_PATTERN.fullmatch(value) is None:
        return ""
    return value


def _extract_image_urls_from_text(text: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"url=(https?://[^\s;]+)", text):
        url = match.group(1).strip().rstrip(").,]")
        if url and url not in urls:
            urls.append(url)
    for match in re.finditer(r"https?://[^\s<>)]+", text):
        url = match.group(0).strip().rstrip(").,]")
        lowered = url.lower()
        if (
            any(
                lowered.endswith(suffix)
                for suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif")
            )
            and url not in urls
        ):
            urls.append(url)
    return urls[:4]


def _as_openai_text_content(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                normalized.append({"type": "text", "text": item["text"]})
            elif item.get("type") == "image_url":
                image_url = item.get("image_url")
                if isinstance(image_url, dict) and isinstance(
                    image_url.get("url"), str
                ):
                    normalized.append(
                        {"type": "image_url", "image_url": {"url": image_url["url"]}}
                    )
                elif isinstance(image_url, str):
                    normalized.append(
                        {"type": "image_url", "image_url": {"url": image_url}}
                    )
        return normalized
    text = clean_text(str(value))
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if "[Attached Visual Inputs]" in text:
        for url in _extract_image_urls_from_text(text):
            content.append({"type": "image_url", "image_url": {"url": url}})
    return content


def normalize_main_chat_messages(
    messages: Iterable[dict[str, Any]],
    *,
    content_format: str = "plain",
) -> list[dict[str, Any]]:
    """Project the canonical OpenAI-chat wire shape without changing content."""

    content_array = clean_text(content_format).lower() in {
        "openai",
        "content-array",
        "content_array",
    }
    normalized: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = clean_text(str(message.get("role") or "user")) or "user"
        content = message.get("content", "")
        if role == "user" and content_array:
            content = _as_openai_text_content(content)
        projected: dict[str, Any] = {"role": role, "content": content}
        for optional_key in ("name", "tool_call_id", "tool_calls"):
            if optional_key in message:
                projected[optional_key] = message[optional_key]
        normalized.append(projected)
    return normalized


def _stable_system_prefix(
    messages: Iterable[dict[str, Any]],
    explicit_prefix: str | None,
) -> str:
    if explicit_prefix is not None:
        return str(explicit_prefix).strip()
    message_list = list(messages)
    first = message_list[0] if message_list else None
    if not isinstance(first, dict) or first.get("role") != "system":
        first = None
    content = str((first or {}).get("content") or "").strip()
    marker_indexes = [
        index
        for marker in _CONTEXT_GROUP_MARKERS
        if (index := content.find(marker)) >= 0
    ]
    return content[: min(marker_indexes)].rstrip() if marker_indexes else content


@dataclass(frozen=True, slots=True)
class PromptAbiIdentity:
    schema: str
    prompt_abi_id: str
    stable_system_prefix_sha256: str
    model_identity_sha256: str
    tokenizer_identity_sha256: str
    chat_template_identity_sha256: str
    server_runtime_identity_sha256: str
    wire_contract_sha256: str
    content_format: str
    content_version: str
    ordering_policy: str
    exact_runtime_identity: bool

    def public_summary(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "promptAbiId": self.prompt_abi_id,
            "contentFormat": self.content_format,
            "exactRuntimeIdentity": self.exact_runtime_identity,
        }


@dataclass(frozen=True, slots=True)
class CompiledMainPrompt:
    messages: tuple[dict[str, Any], ...]
    abi: PromptAbiIdentity
    stable_prefix_chars: int
    dynamic_suffix_chars: int

    def wire_messages(self) -> list[dict[str, Any]]:
        return [dict(message) for message in self.messages]


def compile_main_prompt(
    *,
    model_name: str,
    messages: Iterable[dict[str, Any]],
    final_user_text: str,
    content_format: str = "plain",
    stable_system_prefix: str | None = None,
) -> CompiledMainPrompt:
    """Compile the shared Main prompt order and its content-free ABI identity."""

    source_messages = [
        dict(message) for message in messages if isinstance(message, dict)
    ]
    stable_prefix = _stable_system_prefix(source_messages, stable_system_prefix)
    ordered = [*source_messages, {"role": "user", "content": final_user_text}]
    wire_messages = normalize_main_chat_messages(
        ordered,
        content_format=content_format,
    )
    if not isinstance(wire_messages, list) or not all(
        isinstance(message, dict) for message in wire_messages
    ):
        raise TypeError("main_prompt_message_builder_invalid")

    normalized_format = clean_text(content_format).casefold() or "plain"
    wire_probe = normalize_main_chat_messages(
        [
            {"role": "system", "content": stable_prefix},
            {"role": "user", "content": _PROMPT_ABI_SENTINEL},
        ],
        content_format=content_format,
    )
    if not isinstance(wire_probe, list) or not all(
        isinstance(message, dict) for message in wire_probe
    ):
        raise TypeError("main_prompt_message_builder_invalid")
    try:
        wire_contract_sha = hashlib.sha256(
            json.dumps(
                wire_probe,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        ).hexdigest()
    except (TypeError, ValueError):
        raise TypeError("main_prompt_message_builder_invalid") from None
    model_sha, tokenizer_sha, template_sha, server_runtime_sha = (
        _exact_runtime_identity_shas(normalized_format)
    )
    exact_runtime_identity = all(
        (model_sha, tokenizer_sha, template_sha, server_runtime_sha)
    )
    model_identity = model_sha or _sha256_text(f"logical-model:{clean_text(model_name)}")
    tokenizer_identity = tokenizer_sha or _sha256_text(
        f"model-bundled-tokenizer:{clean_text(model_name)}"
    )
    template_identity = template_sha or _sha256_text(
        f"server-chat-template:{normalized_format}:{PROMPT_CONTENT_VERSION}"
    )
    server_runtime_identity = server_runtime_sha or _sha256_text(
        "logical-server-runtime:unverified"
    )
    stable_sha = _sha256_text(stable_prefix)
    canonical_identity = {
        "schema": PROMPT_ABI_SCHEMA,
        "model": model_identity,
        "tokenizer": tokenizer_identity,
        "chatTemplate": template_identity,
        "serverRuntime": server_runtime_identity,
        "contentFormat": normalized_format,
        "contentVersion": PROMPT_CONTENT_VERSION,
        "stableSystemPrefix": stable_sha,
        "wireContract": wire_contract_sha,
        "orderingPolicy": PROMPT_ORDERING_POLICY,
    }
    prompt_abi_id = _sha256_text(
        json.dumps(
            canonical_identity,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    dynamic_chars = sum(
        len(str(message.get("content") or "")) for message in source_messages
    ) - len(stable_prefix) + len(final_user_text)
    return CompiledMainPrompt(
        messages=tuple(dict(message) for message in wire_messages),
        abi=PromptAbiIdentity(
            schema=PROMPT_ABI_SCHEMA,
            prompt_abi_id=prompt_abi_id,
            stable_system_prefix_sha256=stable_sha,
            model_identity_sha256=model_identity,
            tokenizer_identity_sha256=tokenizer_identity,
            chat_template_identity_sha256=template_identity,
            server_runtime_identity_sha256=server_runtime_identity,
            wire_contract_sha256=wire_contract_sha,
            content_format=normalized_format,
            content_version=PROMPT_CONTENT_VERSION,
            ordering_policy=PROMPT_ORDERING_POLICY,
            exact_runtime_identity=bool(exact_runtime_identity),
        ),
        stable_prefix_chars=len(stable_prefix),
        dynamic_suffix_chars=max(0, dynamic_chars),
    )


class MainRequestKind(IntEnum):
    REALTIME = 0
    INTERACTIVE = 1
    BACKGROUND = 2
    WARMUP = 3


class MainForegroundReservationRejected(RuntimeError):
    """A reservation was not redeemed; retrying as plain REALTIME is safe."""

    def __init__(self, reason: str = "unavailable") -> None:
        self.reason = reason
        super().__init__("main_llm_foreground_reservation_rejected")


@dataclass(frozen=True, slots=True)
class MainForegroundReservationBinding:
    reservation_id: str
    capture_generation: int
    backend_epoch: str


@dataclass(frozen=True, slots=True)
class MainForegroundReservation(MainForegroundReservationBinding):
    ttl_ms: int


@dataclass(slots=True)
class MainForegroundReservationUse:
    reservation: MainForegroundReservationBinding
    redeemed: bool = False
    fallback_used: bool = False
    enabled: bool = True
    claimed_task: asyncio.Task[Any] | None = None


@dataclass(slots=True)
class MainRealtimePreAdmissionActivation:
    activator: Callable[
        [], Awaitable[MainForegroundReservationBinding | None]
    ]
    attempted: bool = False
    enabled: bool = True
    claimed_task: asyncio.Task[Any] | None = None
    failure: BaseException | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_MAIN_FOREGROUND_RESERVATION_USE: ContextVar[
    MainForegroundReservationUse | None
] = ContextVar("main_foreground_reservation_use", default=None)
_MAIN_REALTIME_PRE_ADMISSION_ACTIVATION: ContextVar[
    MainRealtimePreAdmissionActivation | None
] = ContextVar("main_realtime_pre_admission_activation", default=None)


@contextlib.contextmanager
def bind_main_foreground_reservation(
    reservation: MainForegroundReservationBinding,
):
    binding = main_foreground_reservation_binding(
        reservation_id=reservation.reservation_id,
        capture_generation=reservation.capture_generation,
        backend_epoch=reservation.backend_epoch,
    )
    use = MainForegroundReservationUse(binding)
    token = _MAIN_FOREGROUND_RESERVATION_USE.set(use)
    try:
        yield use
    finally:
        use.enabled = False
        _MAIN_FOREGROUND_RESERVATION_USE.reset(token)


@contextlib.contextmanager
def bind_main_realtime_pre_admission(
    activator: Callable[
        [], Awaitable[MainForegroundReservationBinding | None]
    ],
):
    if not callable(activator):
        raise TypeError("main_llm_pre_admission_activator_invalid")
    activation = MainRealtimePreAdmissionActivation(activator)
    token = _MAIN_REALTIME_PRE_ADMISSION_ACTIVATION.set(activation)
    try:
        yield activation
    finally:
        activation.enabled = False
        _MAIN_REALTIME_PRE_ADMISSION_ACTIVATION.reset(token)


async def _activate_main_realtime_pre_admission(
) -> MainForegroundReservationUse | None:
    activation = _MAIN_REALTIME_PRE_ADMISSION_ACTIVATION.get()
    if activation is None:
        return None
    if not activation.enabled:
        raise RuntimeError("main_llm_pre_admission_scope_expired")
    current_task = asyncio.current_task()
    if current_task is None:
        raise RuntimeError("main_llm_pre_admission_task_missing")
    async with activation.lock:
        if not activation.enabled:
            raise RuntimeError("main_llm_pre_admission_scope_expired")
        if activation.attempted:
            if activation.claimed_task is not current_task:
                raise RuntimeError("main_llm_pre_admission_already_claimed")
            if activation.failure is not None:
                raise RuntimeError("main_llm_pre_admission_failed") from activation.failure
            return None
        activation.attempted = True
        activation.claimed_task = current_task
        try:
            reservation = await activation.activator()
            if reservation is None:
                return None
            binding = main_foreground_reservation_binding(
                reservation_id=reservation.reservation_id,
                capture_generation=reservation.capture_generation,
                backend_epoch=reservation.backend_epoch,
            )
        except BaseException as exc:
            activation.failure = exc
            raise
        return MainForegroundReservationUse(
            reservation=binding,
            claimed_task=current_task,
        )


def current_main_foreground_reservation(
) -> MainForegroundReservationBinding | None:
    use = _MAIN_FOREGROUND_RESERVATION_USE.get()
    try:
        current_task = asyncio.current_task()
    except RuntimeError:
        current_task = None
    return (
        use.reservation
        if use is not None
        and use.enabled
        and use.claimed_task in {None, current_task}
        else None
    )


def main_backend_epoch_from_wire(value: Any) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or _MAIN_LLM_EPOCH_PATTERN.fullmatch(value) is None
    ):
        raise ValueError("main_llm_backend_epoch_invalid")
    return value


def main_capture_generation_from_wire(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_CAPTURE_GENERATION:
        raise ValueError("main_llm_capture_generation_invalid")
    return value


def main_foreground_reservation_id_from_wire(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _MAIN_FOREGROUND_RESERVATION_ID_RE.fullmatch(value) is None
    ):
        raise ValueError("main_llm_foreground_reservation_id_invalid")
    return value


def main_foreground_reservation_ttl_ms(value: Any) -> int:
    if (
        type(value) is not int
        or not MIN_MAIN_FOREGROUND_RESERVATION_TTL_MS
        <= value
        <= MAX_MAIN_FOREGROUND_RESERVATION_TTL_MS
    ):
        raise ValueError("main_llm_foreground_reservation_ttl_invalid")
    return value


def main_foreground_reservation_binding(
    *,
    reservation_id: Any,
    capture_generation: Any,
    backend_epoch: Any,
) -> MainForegroundReservationBinding:
    return MainForegroundReservationBinding(
        reservation_id=main_foreground_reservation_id_from_wire(reservation_id),
        capture_generation=main_capture_generation_from_wire(capture_generation),
        backend_epoch=main_backend_epoch_from_wire(backend_epoch),
    )


def main_foreground_reservation_to_wire(
    reservation: MainForegroundReservation,
) -> dict[str, Any]:
    binding = main_foreground_reservation_binding(
        reservation_id=reservation.reservation_id,
        capture_generation=reservation.capture_generation,
        backend_epoch=reservation.backend_epoch,
    )
    return {
        "schema": MAIN_FOREGROUND_RESERVATION_SCHEMA,
        "reservationId": binding.reservation_id,
        "captureGeneration": binding.capture_generation,
        "backendEpoch": binding.backend_epoch,
        "ttlMs": main_foreground_reservation_ttl_ms(reservation.ttl_ms),
    }


def main_foreground_reservation_from_wire(
    value: Any,
) -> MainForegroundReservation:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "reservationId",
            "captureGeneration",
            "backendEpoch",
            "ttlMs",
        }
        or value.get("schema") != MAIN_FOREGROUND_RESERVATION_SCHEMA
    ):
        raise ValueError("main_llm_foreground_reservation_wire_invalid")
    binding = main_foreground_reservation_binding(
        reservation_id=value.get("reservationId"),
        capture_generation=value.get("captureGeneration"),
        backend_epoch=value.get("backendEpoch"),
    )
    return MainForegroundReservation(
        reservation_id=binding.reservation_id,
        capture_generation=binding.capture_generation,
        backend_epoch=binding.backend_epoch,
        ttl_ms=main_foreground_reservation_ttl_ms(value.get("ttlMs")),
    )


def main_foreground_reservation_request_payload(
    *,
    capture_generation: Any,
    backend_epoch: Any,
) -> dict[str, Any]:
    return {
        "captureGeneration": main_capture_generation_from_wire(
            capture_generation
        ),
        "backendEpoch": main_backend_epoch_from_wire(backend_epoch),
    }


def main_foreground_reservation_headers(
    reservation: MainForegroundReservationBinding,
) -> dict[str, str]:
    binding = main_foreground_reservation_binding(
        reservation_id=reservation.reservation_id,
        capture_generation=reservation.capture_generation,
        backend_epoch=reservation.backend_epoch,
    )
    return {
        MAIN_ADMISSION_KIND_HEADER: MainRequestKind.REALTIME.name.casefold(),
        MAIN_FOREGROUND_RESERVATION_ID_HEADER: binding.reservation_id,
        MAIN_FOREGROUND_CAPTURE_GENERATION_HEADER: str(
            binding.capture_generation
        ),
        MAIN_FOREGROUND_BACKEND_EPOCH_HEADER: binding.backend_epoch,
    }


def main_foreground_reservation_cancel_payload(
    reservation: MainForegroundReservationBinding,
) -> dict[str, Any]:
    binding = main_foreground_reservation_binding(
        reservation_id=reservation.reservation_id,
        capture_generation=reservation.capture_generation,
        backend_epoch=reservation.backend_epoch,
    )
    return {
        "reservationId": binding.reservation_id,
        "captureGeneration": binding.capture_generation,
        "backendEpoch": binding.backend_epoch,
    }


def _main_gateway_control_url(gateway_url: str | None, path: str) -> str:
    configured = clean_text(
        gateway_url or os.getenv("MAIN_LLM_ADMISSION_GATEWAY_URL", "")
    )
    parsed = urlsplit(configured)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path.rstrip("/") != "/v1/chat/completions"
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("main_llm_admission_gateway_url_invalid")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _strict_json_object(raw: bytes, *, error: str) -> dict[str, Any]:
    if not raw or len(raw) > 4096:
        raise RuntimeError(error)

    def pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate key")
            value[key] = item
        return value

    try:
        payload = json.loads(raw, object_pairs_hook=pairs_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise RuntimeError(error) from None
    if not isinstance(payload, dict):
        raise RuntimeError(error)
    return payload


async def _main_foreground_response_payload(response: Any) -> dict[str, Any]:
    if getattr(response, "content_type", None) != "application/json":
        raise RuntimeError("main_llm_foreground_reservation_receipt_invalid")
    return _strict_json_object(
        await response.read(),
        error="main_llm_foreground_reservation_receipt_invalid",
    )


def _raise_reservation_response_error(
    response: Any,
    payload: dict[str, Any],
) -> None:
    if (
        getattr(response, "status", None) in {409, 410, 412, 503}
        and response.headers.get(MAIN_FOREGROUND_RESERVATION_RESULT_HEADER)
        == "rejected"
        and payload
        == {
            "ok": False,
            "schema": MAIN_FOREGROUND_RESERVATION_SCHEMA,
            "error": "main_llm_foreground_reservation_rejected",
        }
    ):
        raise MainForegroundReservationRejected()
    raise RuntimeError("main_llm_foreground_reservation_receipt_invalid")


async def reserve_main_foreground(
    session: Any,
    *,
    capture_generation: Any,
    backend_epoch: Any,
    gateway_url: str | None = None,
) -> MainForegroundReservation:
    payload = main_foreground_reservation_request_payload(
        capture_generation=capture_generation,
        backend_epoch=backend_epoch,
    )
    url = _main_gateway_control_url(
        gateway_url,
        MAIN_FOREGROUND_RESERVATION_PATH,
    )
    async with session.post(url, json=payload) as response:
        receipt = await _main_foreground_response_payload(response)
        if response.status != 201:
            _raise_reservation_response_error(response, receipt)
        if (
            response.headers.get(MAIN_FOREGROUND_RESERVATION_RESULT_HEADER)
            != "reserved"
            or set(receipt)
            != {
                "ok",
                "schema",
                "reservationId",
                "captureGeneration",
                "backendEpoch",
                "ttlMs",
            }
            or receipt.get("ok") is not True
            or receipt.get("schema") != MAIN_FOREGROUND_RESERVATION_SCHEMA
            or receipt.get("captureGeneration") != payload["captureGeneration"]
            or receipt.get("backendEpoch") != payload["backendEpoch"]
        ):
            raise RuntimeError("main_llm_foreground_reservation_receipt_invalid")
        binding = main_foreground_reservation_binding(
            reservation_id=receipt.get("reservationId"),
            capture_generation=receipt.get("captureGeneration"),
            backend_epoch=receipt.get("backendEpoch"),
        )
        ttl_ms = main_foreground_reservation_ttl_ms(receipt.get("ttlMs"))
        return MainForegroundReservation(
            reservation_id=binding.reservation_id,
            capture_generation=binding.capture_generation,
            backend_epoch=binding.backend_epoch,
            ttl_ms=ttl_ms,
        )


async def cancel_main_foreground(
    session: Any,
    reservation: MainForegroundReservationBinding,
    *,
    gateway_url: str | None = None,
) -> None:
    payload = main_foreground_reservation_cancel_payload(reservation)
    url = _main_gateway_control_url(
        gateway_url,
        MAIN_FOREGROUND_RESERVATION_CANCEL_PATH,
    )
    async with session.post(url, json=payload) as response:
        receipt = await _main_foreground_response_payload(response)
        if response.status != 200:
            _raise_reservation_response_error(response, receipt)
        if (
            response.headers.get(MAIN_FOREGROUND_RESERVATION_RESULT_HEADER)
            != "cancelled"
            or receipt
            != {
                "ok": True,
                "schema": MAIN_FOREGROUND_RESERVATION_SCHEMA,
                "reservationId": payload["reservationId"],
            }
        ):
            raise RuntimeError("main_llm_foreground_reservation_receipt_invalid")


def main_admission_client_mode() -> str:
    """Return the explicit admission owner mode, failing closed on bad config."""

    mode = clean_text(
        os.getenv("MAIN_LLM_ADMISSION_CLIENT_MODE", "local")
    ).casefold()
    if mode not in _MAIN_ADMISSION_CLIENT_MODES:
        raise RuntimeError("main_llm_admission_client_mode_invalid")
    if mode == "gateway" and not clean_text(
        os.getenv("MAIN_LLM_ADMISSION_GATEWAY_URL", "")
    ):
        raise RuntimeError("main_llm_admission_gateway_url_missing")
    return mode


def main_admission_headers(kind: MainRequestKind) -> dict[str, str]:
    """Build the only accepted wire representation of a Main request kind."""

    request_kind = MainRequestKind(kind)
    headers = {MAIN_ADMISSION_KIND_HEADER: request_kind.name.casefold()}
    reservation = current_main_foreground_reservation()
    if request_kind is MainRequestKind.REALTIME and reservation is not None:
        headers.update(main_foreground_reservation_headers(reservation))
    return headers


def main_request_kind_from_header(value: str | None) -> MainRequestKind:
    """Parse the gateway trust-boundary header without aliases or defaults."""

    if not isinstance(value, str) or value != value.strip().casefold():
        raise ValueError("main_llm_admission_kind_invalid")
    try:
        return MainRequestKind[value.upper()]
    except KeyError:
        raise ValueError("main_llm_admission_kind_invalid") from None


def _gateway_admission_lease(
    response: Any,
    kind: MainRequestKind,
    reservation: MainForegroundReservationBinding | None = None,
) -> MainAdmissionLease:
    try:
        reservation_result = response.headers.get(
            MAIN_FOREGROUND_RESERVATION_RESULT_HEADER
        )
        response_reservation_id = response.headers.get(
            MAIN_FOREGROUND_RESERVATION_ID_HEADER
        )
    except AttributeError:
        raise RuntimeError("main_llm_admission_receipt_invalid") from None
    if reservation is not None:
        binding = main_foreground_reservation_binding(
            reservation_id=reservation.reservation_id,
            capture_generation=reservation.capture_generation,
            backend_epoch=reservation.backend_epoch,
        )
        if (
            reservation_result == "rejected"
            and getattr(response, "status", None) in {409, 410, 412, 503}
            and response_reservation_id is None
        ):
            raise MainForegroundReservationRejected()
        if (
            reservation_result != "redeemed"
            or response_reservation_id != binding.reservation_id
        ):
            raise RuntimeError("main_llm_admission_receipt_invalid")
    elif reservation_result is not None or response_reservation_id is not None:
        raise RuntimeError("main_llm_admission_receipt_invalid")
    try:
        headers = response.headers
        receipt = headers[MAIN_ADMISSION_RECEIPT_HEADER]
        request_id = headers[MAIN_ADMISSION_REQUEST_ID_HEADER]
        queue_ms = float(headers[MAIN_ADMISSION_QUEUE_MS_HEADER])
        upstream_headers_ms = float(
            headers[MAIN_ADMISSION_UPSTREAM_HEADERS_MS_HEADER]
        )
        upstream_write_ms = float(
            headers[MAIN_ADMISSION_UPSTREAM_WRITE_MS_HEADER]
        )
    except (AttributeError, KeyError, TypeError, ValueError):
        raise RuntimeError("main_llm_admission_receipt_invalid") from None
    if (
        receipt != MAIN_ADMISSION_RECEIPT_VALUE
        or not isinstance(request_id, str)
        or _MAIN_ADMISSION_REQUEST_ID_RE.fullmatch(request_id) is None
        or not math.isfinite(queue_ms)
        or not 0.0 <= queue_ms <= _MAX_ADMISSION_RECEIPT_DURATION_MS
        or not math.isfinite(upstream_headers_ms)
        or not 0.0
        <= upstream_headers_ms
        <= _MAX_ADMISSION_RECEIPT_DURATION_MS
        or not math.isfinite(upstream_write_ms)
        or not 0.0 <= upstream_write_ms <= upstream_headers_ms
    ):
        raise RuntimeError("main_llm_admission_receipt_invalid")
    received_at = time.monotonic()
    admitted_at = max(
        0.0,
        received_at - upstream_headers_ms / 1000.0,
    )
    return MainAdmissionLease(
        request_id=request_id,
        kind=MainRequestKind(kind),
        queue_ms=queue_ms,
        admitted_at=admitted_at,
        _task=asyncio.current_task(),
        raw_request_written_at=(
            admitted_at + upstream_write_ms / 1000.0
        ),
    )


def main_request_kind_for_source(source: str) -> MainRequestKind:
    normalized = clean_text(source).casefold()
    if normalized in {
        "voice",
        "discord_voice",
        "local_bridge",
        "local_mic",
        "voice_validation",
    }:
        return MainRequestKind.REALTIME
    if normalized in {"warmup", "startup"}:
        return MainRequestKind.WARMUP
    if normalized in {
        "background",
        "control_page_welcome",
        "tool_synthesis",
        "validation",
    }:
        return MainRequestKind.BACKGROUND
    return MainRequestKind.INTERACTIVE


class MainLlmPayload(dict[str, Any]):
    """JSON-compatible payload carrying process-local ABI/admission metadata."""

    def __init__(
        self,
        *args: Any,
        prompt_abi: PromptAbiIdentity,
        request_kind: MainRequestKind,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.prompt_abi = prompt_abi
        self.request_kind = request_kind


def main_request_kind_from_payload(
    payload: dict[str, Any],
    *,
    default: MainRequestKind = MainRequestKind.INTERACTIVE,
) -> MainRequestKind:
    value = getattr(payload, "request_kind", default)
    try:
        return MainRequestKind(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class MainAdmissionLease:
    request_id: str
    kind: MainRequestKind
    queue_ms: float
    admitted_at: float
    _task: asyncio.Task[Any] | None
    raw_request_written_at: float | None = None
    _preempted: asyncio.Event | None = None


class _LoopLaneState:
    def __init__(self) -> None:
        self.condition = asyncio.Condition()
        self.active: MainAdmissionLease | None = None
        self.waiters = {kind: deque() for kind in MainRequestKind}
        self.foreground_reservation: _LaneForegroundReservation | None = None
        self.foreground_expiry_handle: asyncio.TimerHandle | None = None


@dataclass(frozen=True, slots=True)
class _LaneForegroundReservation:
    value: MainForegroundReservation
    expires_at: float


@dataclass(frozen=True, slots=True)
class _AdmissionTicket:
    queued_at: float
    reservation_redemption: bool = False


@dataclass(slots=True)
class _ProcessLockHandle:
    stream: Any


def _admission_lock_path() -> Path:
    configured = clean_text(os.getenv("MAIN_LLM_ADMISSION_LOCK_FILE", ""))
    if configured:
        return Path(configured)
    return get_runtime_artifacts_root() / "main_llm_admission" / "owner.lock"


def _try_process_lock() -> _ProcessLockHandle | None:
    path = _admission_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ImportError):
        stream.close()
        return None
    return _ProcessLockHandle(stream)


def _release_process_lock(handle: _ProcessLockHandle | None) -> None:
    if handle is None:
        return
    stream = handle.stream
    try:
        if os.name == "nt":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        stream.close()


async def _acquire_process_lock(deadline: float) -> _ProcessLockHandle:
    while True:
        handle = _try_process_lock()
        if handle is not None:
            return handle
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("main inference admission timed out")
        await asyncio.sleep(min(0.01, remaining))


class MainInferenceLane:
    """Single Main owner with realtime-first FIFO admission."""

    def __init__(self, *, use_process_lock: bool = True) -> None:
        self._use_process_lock = bool(use_process_lock)
        self._states: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopLaneState
        ] = weakref.WeakKeyDictionary()
        self._states_lock = threading.Lock()

    def _state(self) -> _LoopLaneState:
        loop = asyncio.get_running_loop()
        with self._states_lock:
            state = self._states.get(loop)
            if state is None:
                state = _LoopLaneState()
                self._states[loop] = state
            return state

    @staticmethod
    def _reservation_matches(
        stored: MainForegroundReservation,
        binding: MainForegroundReservationBinding,
    ) -> bool:
        return (
            stored.reservation_id == binding.reservation_id
            and stored.capture_generation == binding.capture_generation
            and stored.backend_epoch == binding.backend_epoch
        )

    @staticmethod
    def _clear_foreground_locked(state: _LoopLaneState) -> None:
        handle = state.foreground_expiry_handle
        state.foreground_expiry_handle = None
        state.foreground_reservation = None
        if handle is not None:
            handle.cancel()

    def _prune_expired_foreground_locked(
        self,
        state: _LoopLaneState,
        now: float,
    ) -> bool:
        reservation = state.foreground_reservation
        if reservation is None or reservation.expires_at > now:
            return False
        self._clear_foreground_locked(state)
        return True

    async def _expire_foreground(
        self,
        state: _LoopLaneState,
        reservation_id: str,
    ) -> None:
        async with state.condition:
            reservation = state.foreground_reservation
            if (
                reservation is not None
                and reservation.value.reservation_id == reservation_id
                and reservation.expires_at <= time.monotonic()
            ):
                self._clear_foreground_locked(state)
                state.condition.notify_all()

    async def reserve_foreground(
        self,
        *,
        capture_generation: Any,
        backend_epoch: Any,
        ttl_ms: Any = DEFAULT_MAIN_FOREGROUND_RESERVATION_TTL_MS,
    ) -> MainForegroundReservation:
        generation = main_capture_generation_from_wire(capture_generation)
        epoch = main_backend_epoch_from_wire(backend_epoch)
        ttl = main_foreground_reservation_ttl_ms(ttl_ms)
        state = self._state()
        now = time.monotonic()
        async with state.condition:
            self._prune_expired_foreground_locked(state, now)
            if state.foreground_reservation is not None:
                raise MainForegroundReservationRejected("conflict")
            reservation = MainForegroundReservation(
                reservation_id=secrets.token_hex(16),
                capture_generation=generation,
                backend_epoch=epoch,
                ttl_ms=ttl,
            )
            state.foreground_reservation = _LaneForegroundReservation(
                value=reservation,
                expires_at=now + ttl / 1000.0,
            )
            active = state.active
            if (
                active is not None
                and active.kind
                in {MainRequestKind.BACKGROUND, MainRequestKind.WARMUP}
                and active._preempted is not None
            ):
                active._preempted.set()
            loop = asyncio.get_running_loop()
            state.foreground_expiry_handle = loop.call_later(
                ttl / 1000.0,
                lambda: asyncio.create_task(
                    self._expire_foreground(state, reservation.reservation_id)
                ),
            )
            state.condition.notify_all()
            return reservation

    async def cancel_foreground(
        self,
        reservation: MainForegroundReservationBinding,
    ) -> None:
        binding = main_foreground_reservation_binding(
            reservation_id=reservation.reservation_id,
            capture_generation=reservation.capture_generation,
            backend_epoch=reservation.backend_epoch,
        )
        state = self._state()
        async with state.condition:
            stored = state.foreground_reservation
            if stored is None:
                raise MainForegroundReservationRejected("unavailable")
            matches = self._reservation_matches(stored.value, binding)
            if stored.expires_at <= time.monotonic():
                self._clear_foreground_locked(state)
                state.condition.notify_all()
                raise MainForegroundReservationRejected(
                    "expired" if matches else "unavailable"
                )
            if not matches:
                raise MainForegroundReservationRejected("unavailable")
            self._clear_foreground_locked(state)
            state.condition.notify_all()

    def _claim_foreground_locked(
        self,
        state: _LoopLaneState,
        binding: MainForegroundReservationBinding,
    ) -> None:
        stored = state.foreground_reservation
        if stored is None:
            raise MainForegroundReservationRejected("unavailable")
        matches = self._reservation_matches(stored.value, binding)
        if stored.expires_at <= time.monotonic():
            self._clear_foreground_locked(state)
            state.condition.notify_all()
            raise MainForegroundReservationRejected(
                "expired" if matches else "unavailable"
            )
        if not matches:
            raise MainForegroundReservationRejected("unavailable")
        self._clear_foreground_locked(state)

    def _can_admit(
        self,
        state: _LoopLaneState,
        kind: MainRequestKind,
        ticket: _AdmissionTicket,
    ) -> bool:
        self._prune_expired_foreground_locked(state, time.monotonic())
        if (
            state.active is not None
            or not state.waiters[kind]
            or state.waiters[kind][0] is not ticket
        ):
            return False
        heads = [
            (queued_kind, waiters[0])
            for queued_kind, waiters in state.waiters.items()
            if waiters
        ]
        reserved = [item for item in heads if item[1].reservation_redemption]
        if reserved:
            selected_kind, _selected_ticket = min(
                reserved,
                key=lambda item: item[1].queued_at,
            )
            return selected_kind == kind
        if state.foreground_reservation is not None:
            heads = [
                item for item in heads if item[0] == MainRequestKind.REALTIME
            ]
            if not heads:
                return False
        now = time.monotonic()
        aged = [
            item
            for item in heads
            if now - item[1].queued_at >= _PRIORITY_AGING_SEC
        ]
        selected_kind, _selected_ticket = (
            min(aged, key=lambda item: item[1].queued_at)
            if aged
            else min(heads, key=lambda item: int(item[0]))
        )
        return selected_kind == kind

    @asynccontextmanager
    async def admit(
        self,
        kind: MainRequestKind,
        *,
        on_acquired: Callable[[MainAdmissionLease], None] | None = None,
        reservation: MainForegroundReservationBinding | None = None,
    ) -> AsyncIterator[MainAdmissionLease]:
        request_kind = MainRequestKind(kind)
        state = self._state()
        current_task = asyncio.current_task()
        active = state.active
        if active is not None and active._task is current_task:
            if reservation is not None:
                raise MainForegroundReservationRejected("unavailable")
            yield active
            return

        queued_at = time.monotonic()
        binding = None
        if reservation is not None:
            if request_kind != MainRequestKind.REALTIME:
                raise MainForegroundReservationRejected("unavailable")
            binding = main_foreground_reservation_binding(
                reservation_id=reservation.reservation_id,
                capture_generation=reservation.capture_generation,
                backend_epoch=reservation.backend_epoch,
            )
        ticket = _AdmissionTicket(
            queued_at,
            reservation_redemption=binding is not None,
        )
        timeout_sec = _ADMISSION_TIMEOUT_SEC[int(request_kind)]
        deadline = queued_at + timeout_sec
        async with state.condition:
            if binding is not None:
                self._claim_foreground_locked(state, binding)
                state.waiters[request_kind].appendleft(ticket)
            else:
                state.waiters[request_kind].append(ticket)
            try:
                await asyncio.wait_for(
                    state.condition.wait_for(
                        lambda: self._can_admit(
                            state,
                            request_kind,
                            ticket,
                        )
                    ),
                    timeout=max(0.001, deadline - time.monotonic()),
                )
            except BaseException:
                with contextlib.suppress(ValueError):
                    state.waiters[request_kind].remove(ticket)
                state.condition.notify_all()
                raise
            state.waiters[request_kind].popleft()
            placeholder = MainAdmissionLease(
                request_id=secrets.token_hex(12),
                kind=request_kind,
                queue_ms=0.0,
                admitted_at=queued_at,
                _task=current_task,
                _preempted=asyncio.Event(),
            )
            state.active = placeholder
        process_lock: _ProcessLockHandle | None = None
        lease = placeholder
        try:
            if self._use_process_lock:
                process_lock = await _acquire_process_lock(deadline)
            admitted_at = time.monotonic()
            lease = MainAdmissionLease(
                request_id=placeholder.request_id,
                kind=request_kind,
                queue_ms=max(0.0, (admitted_at - queued_at) * 1000.0),
                admitted_at=admitted_at,
                _task=current_task,
                _preempted=placeholder._preempted,
            )
            async with state.condition:
                if state.active is placeholder:
                    state.active = lease
            if on_acquired is not None:
                on_acquired(lease)
            yield lease
        finally:
            _release_process_lock(process_lock)
            async with state.condition:
                if state.active is placeholder or state.active is lease:
                    state.active = None
                state.condition.notify_all()


MAIN_INFERENCE_LANE = MainInferenceLane()


@asynccontextmanager
async def admitted_main_request(
    request_context_factory: Callable[[], Any],
    *,
    kind: MainRequestKind,
    on_acquired: Callable[[MainAdmissionLease], None] | None = None,
    reservation: MainForegroundReservationBinding | None = None,
) -> AsyncIterator[Any]:
    client_mode = main_admission_client_mode()
    contextual_use = _MAIN_FOREGROUND_RESERVATION_USE.get()
    deferred_activation: MainRealtimePreAdmissionActivation | None = None
    deferred_use: MainForegroundReservationUse | None = None
    deferred_token = None
    admission_validated = False
    if (
        client_mode == "gateway"
        and reservation is None
        and MainRequestKind(kind) is MainRequestKind.REALTIME
        and (contextual_use is None or not contextual_use.enabled)
    ):
        deferred_activation = _MAIN_REALTIME_PRE_ADMISSION_ACTIVATION.get()
        deferred_use = await _activate_main_realtime_pre_admission()
        if deferred_use is not None:
            deferred_token = _MAIN_FOREGROUND_RESERVATION_USE.set(deferred_use)
            contextual_use = deferred_use
    current_task = asyncio.current_task()
    try:
        contextual_claim_conflict = bool(
            reservation is None
            and MainRequestKind(kind) is MainRequestKind.REALTIME
            and contextual_use is not None
            and contextual_use.enabled
            and contextual_use.claimed_task is not None
            and contextual_use.claimed_task is not current_task
        )
        if contextual_claim_conflict:
            raise RuntimeError("main_llm_foreground_reservation_already_claimed")
        contextual_candidate = bool(
            reservation is None
            and MainRequestKind(kind) is MainRequestKind.REALTIME
            and contextual_use is not None
            and contextual_use.enabled
            and contextual_use.claimed_task in {None, current_task}
        )
        if contextual_candidate and client_mode != "gateway":
            raise RuntimeError("main_llm_foreground_reservation_requires_gateway")
        if client_mode == "gateway":
            contextual_reservation = (
                contextual_use.reservation
                if contextual_candidate
                else None
            )
            if contextual_reservation is not None:
                contextual_use.claimed_task = current_task
            selected_reservation = reservation or contextual_reservation
            rejected = False
            async with request_context_factory() as response:
                try:
                    lease = _gateway_admission_lease(
                        response,
                        kind,
                        reservation=selected_reservation,
                    )
                except MainForegroundReservationRejected:
                    if contextual_reservation is None:
                        raise
                    contextual_use.enabled = False
                    contextual_use.fallback_used = True
                    rejected = True
                else:
                    if contextual_reservation is not None:
                        contextual_use.redeemed = True
                        contextual_use.enabled = False
                    if on_acquired is not None:
                        on_acquired(lease)
                    admission_validated = True
                    yield response
            if rejected:
                async with request_context_factory() as response:
                    lease = _gateway_admission_lease(response, kind)
                    if on_acquired is not None:
                        on_acquired(lease)
                    admission_validated = True
                    yield response
            return
        if reservation is not None:
            raise RuntimeError("main_llm_foreground_reservation_requires_gateway")
        async with MAIN_INFERENCE_LANE.admit(kind, on_acquired=on_acquired):
            async with request_context_factory() as response:
                admission_validated = True
                yield response
    except BaseException as exc:
        if (
            deferred_activation is not None
            and deferred_use is not None
            and not admission_validated
            and deferred_activation.failure is None
        ):
            deferred_activation.failure = exc
        raise
    finally:
        if deferred_use is not None:
            deferred_use.enabled = False
        if deferred_token is not None:
            _MAIN_FOREGROUND_RESERVATION_USE.reset(deferred_token)


__all__ = [
    "CompiledMainPrompt",
    "DEFAULT_MAIN_FOREGROUND_RESERVATION_TTL_MS",
    "MAIN_INFERENCE_LANE",
    "MainAdmissionLease",
    "MainForegroundReservation",
    "MainForegroundReservationBinding",
    "MainForegroundReservationRejected",
    "MainForegroundReservationUse",
    "MainRealtimePreAdmissionActivation",
    "MainInferenceLane",
    "MainLlmPayload",
    "MainRequestKind",
    "MAIN_ADMISSION_KIND_HEADER",
    "MAIN_ADMISSION_QUEUE_MS_HEADER",
    "MAIN_ADMISSION_REQUEST_ID_HEADER",
    "MAIN_ADMISSION_RECEIPT_HEADER",
    "MAIN_ADMISSION_RECEIPT_VALUE",
    "MAIN_ADMISSION_UPSTREAM_HEADERS_MS_HEADER",
    "MAIN_ADMISSION_UPSTREAM_WRITE_MS_HEADER",
    "MAIN_FOREGROUND_BACKEND_EPOCH_HEADER",
    "MAIN_FOREGROUND_CAPTURE_GENERATION_HEADER",
    "MAIN_FOREGROUND_RESERVATION_CANCEL_PATH",
    "MAIN_FOREGROUND_RESERVATION_ID_HEADER",
    "MAIN_FOREGROUND_RESERVATION_PATH",
    "MAIN_FOREGROUND_RESERVATION_RESULT_HEADER",
    "MAIN_FOREGROUND_RESERVATION_SCHEMA",
    "MAX_MAIN_FOREGROUND_RESERVATION_TTL_MS",
    "MIN_MAIN_FOREGROUND_RESERVATION_TTL_MS",
    "PROMPT_ABI_SCHEMA",
    "PromptAbiIdentity",
    "admitted_main_request",
    "bind_main_foreground_reservation",
    "bind_main_realtime_pre_admission",
    "cancel_main_foreground",
    "compile_main_prompt",
    "current_main_llm_backend_epoch",
    "current_main_foreground_reservation",
    "main_backend_epoch_from_wire",
    "main_admission_client_mode",
    "main_admission_headers",
    "main_capture_generation_from_wire",
    "main_foreground_reservation_binding",
    "main_foreground_reservation_cancel_payload",
    "main_foreground_reservation_from_wire",
    "main_foreground_reservation_headers",
    "main_foreground_reservation_id_from_wire",
    "main_foreground_reservation_request_payload",
    "main_foreground_reservation_to_wire",
    "main_foreground_reservation_ttl_ms",
    "main_request_kind_for_source",
    "main_request_kind_from_header",
    "main_request_kind_from_payload",
    "main_prompt_exact_identity_required",
    "normalize_main_chat_messages",
    "reserve_main_foreground",
]
