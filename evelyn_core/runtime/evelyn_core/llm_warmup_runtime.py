from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .main_inference_contract import (
    MainLlmPayload,
    MainRequestKind,
    admitted_main_request,
    compile_main_prompt,
    main_admission_headers,
)
from .text import clean_text


@dataclass(frozen=True)
class LlmWarmupProbeEvidence:
    prompt_index: int
    suffix_index: int
    prompt_tokens_processed: int | None
    prompt_tokens_cached: int | None
    prompt_eval_ms: float | None
    finish_reason: str | None

    @property
    def timing_complete(self) -> bool:
        return (
            self.prompt_tokens_processed is not None
            and self.prompt_tokens_cached is not None
            and self.prompt_eval_ms is not None
        )


@dataclass(frozen=True)
class LlmWarmupEvidence:
    schema: str
    probes: tuple[LlmWarmupProbeEvidence, ...]
    prompt_abi_ids: tuple[str, ...]
    exact_runtime_identity: bool
    production_prompt_match: bool

    @property
    def cache_reuse_proven(self) -> bool:
        pairs: dict[int, list[LlmWarmupProbeEvidence]] = {}
        for probe in self.probes:
            pairs.setdefault(probe.prompt_index, []).append(probe)
        return bool(pairs) and all(
            len(probes) == 2
            and all(probe.timing_complete and probe.finish_reason for probe in probes)
            and _has_material_cache_reuse(probes[1])
            for probes in pairs.values()
        )


@dataclass(frozen=True)
class LlmWarmupRuntimeDeps:
    get_http_session: Callable[[], Awaitable[Any]]
    client_timeout: Callable[..., Any]
    mark_startup_component: Callable[[str, str, str], Any]
    llm_server_url: str
    model_name: str
    system_prompts: tuple[str, ...]
    main_llm_chat_content_format: str
    voice_llm_max_tokens: int
    main_llm_stop_tokens: tuple[str, ...] | list[str]
    decode_sse_stream_line: Callable[[bytes], dict[str, Any] | None]
    log: Callable[..., Any] = print
    require_cache_proof: bool = True
    require_exact_prompt_abi: bool = False
    expected_prompt_abi_ids: tuple[str, ...] | None = None


_WARMUP_DYNAMIC_CONTEXTS = (
    "[Warmup Context]\nprobe=A",
    "[Warmup Context]\nprobe=B",
)
_WARMUP_FINAL_USER = "준비 상태를 한 단어로 확인해."
_FINISH_REASONS = frozenset({"stop", "length", "tool_calls", "content_filter"})
_MIN_SECOND_PROBE_CACHE_HIT_RATIO = 0.5


def _has_material_cache_reuse(probe: LlmWarmupProbeEvidence) -> bool:
    processed = probe.prompt_tokens_processed
    cached = probe.prompt_tokens_cached
    if processed is None or cached is None or cached <= 0:
        return False
    total = processed + cached
    return total > 0 and cached / total >= _MIN_SECOND_PROBE_CACHE_HIT_RATIO


def _fail_warmup(deps: LlmWarmupRuntimeDeps, detail: str) -> None:
    deps.mark_startup_component("main_warmup", "failed", detail)
    raise RuntimeError("LLM warmup failed")


def _event_payload(raw_line: bytes) -> dict[str, Any] | None:
    line = raw_line.decode("utf-8", errors="ignore").strip()
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line or line == "[DONE]":
        return None
    try:
        payload = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _nonnegative_int(value: Any) -> int | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
        or value < 0
    ):
        return None
    integer = int(value)
    return integer if integer == value else None


def _nonnegative_float(value: Any) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or (isinstance(value, float) and not math.isfinite(value))
        or value < 0
    ):
        return None
    try:
        return float(value)
    except OverflowError:
        return None


def _timing_fields(raw_line: bytes) -> dict[str, Any]:
    payload = _event_payload(raw_line)
    if payload is None:
        return {}
    timings = payload.get("timings")
    timings = timings if isinstance(timings, dict) else {}
    usage = payload.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    prompt_details = usage.get("prompt_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    choices = payload.get("choices")
    choice = (
        choices[0]
        if isinstance(choices, list)
        and choices
        and isinstance(choices[0], dict)
        else {}
    )
    finish_reason = choice.get("finish_reason")
    return {
        "prompt_tokens_processed": _nonnegative_int(timings.get("prompt_n")),
        "prompt_tokens_cached": _nonnegative_int(
            timings.get("cache_n", prompt_details.get("cached_tokens"))
        ),
        "prompt_eval_ms": _nonnegative_float(timings.get("prompt_ms")),
        "finish_reason": finish_reason if finish_reason in _FINISH_REASONS else None,
    }


def _is_malformed_stream_line(
    raw_line: bytes,
    event: dict[str, Any] | None,
) -> bool:
    line = raw_line.decode("utf-8", errors="ignore").strip()
    if not line or line.startswith(":") or line.startswith(("event:", "id:", "retry:")):
        return False
    return event is None


async def warmup_llm_from_runtime(*, deps: LlmWarmupRuntimeDeps) -> LlmWarmupEvidence:
    deps.mark_startup_component("main_warmup", "running", "Main LLM warmup request")
    session = await deps.get_http_session()
    deps.log("[STARTUP] llm_warmup_begin")
    prompts = tuple(
        dict.fromkeys(
            normalized
            for prompt in deps.system_prompts
            if (normalized := clean_text(prompt))
        )
    )
    if not prompts or deps.voice_llm_max_tokens < 1:
        _fail_warmup(deps, "llm_warmup_request_invalid")
    expected_prompt_abi_ids = deps.expected_prompt_abi_ids
    if expected_prompt_abi_ids is not None and (
        len(expected_prompt_abi_ids) != len(prompts)
        or any(not isinstance(value, str) or not value for value in expected_prompt_abi_ids)
    ):
        _fail_warmup(deps, "llm_warmup_prompt_abi_mismatch")

    probes: list[LlmWarmupProbeEvidence] = []
    prompt_abi_ids: list[str] = []
    exact_runtime_identity = True
    for prompt_index, system_prompt in enumerate(prompts):
        for suffix_index, dynamic_context in enumerate(
            _WARMUP_DYNAMIC_CONTEXTS
        ):
            compiled = compile_main_prompt(
                model_name=deps.model_name,
                messages=[
                    {
                        "role": "system",
                        "content": f"{system_prompt}\n\n{dynamic_context}",
                    }
                ],
                final_user_text=_WARMUP_FINAL_USER,
                content_format=deps.main_llm_chat_content_format,
                stable_system_prefix=system_prompt,
            )
            exact_runtime_identity = bool(
                exact_runtime_identity
                and compiled.abi.exact_runtime_identity
            )
            if (
                deps.require_exact_prompt_abi
                and not compiled.abi.exact_runtime_identity
            ):
                _fail_warmup(
                    deps,
                    "llm_warmup_prompt_abi_unverified",
                )
            if (
                expected_prompt_abi_ids is not None
                and compiled.abi.prompt_abi_id
                != expected_prompt_abi_ids[prompt_index]
            ):
                _fail_warmup(deps, "llm_warmup_prompt_abi_mismatch")
            prompt_abi_ids.append(compiled.abi.prompt_abi_id)
            payload = MainLlmPayload({
                "model": deps.model_name,
                "messages": compiled.wire_messages(),
                "temperature": 0.0,
                "max_tokens": 1,
                "stream": True,
                "stream_options": {"include_usage": True},
                "timings_per_token": True,
                "cache_prompt": True,
                "stop": list(deps.main_llm_stop_tokens),
            }, prompt_abi=compiled.abi, request_kind=MainRequestKind.WARMUP)
            saw_delta = False
            saw_terminal = False
            timing: dict[str, Any] = {}
            async with admitted_main_request(
                lambda: session.post(
                    deps.llm_server_url,
                    json=payload,
                    headers=main_admission_headers(MainRequestKind.WARMUP),
                    timeout=deps.client_timeout(total=20),
                ),
                kind=MainRequestKind.WARMUP,
            ) as resp:
                if resp.status != 200:
                    _fail_warmup(deps, "llm_warmup_failed")
                async for raw_line in resp.content:
                    timing.update(
                        {
                            key: value
                            for key, value in _timing_fields(raw_line).items()
                            if value is not None
                        }
                    )
                    try:
                        event = deps.decode_sse_stream_line(raw_line)
                    except Exception:
                        _fail_warmup(deps, "llm_warmup_stream_malformed")
                    if _is_malformed_stream_line(raw_line, event):
                        _fail_warmup(deps, "llm_warmup_stream_malformed")
                    if event is None:
                        continue
                    if not isinstance(event, dict):
                        _fail_warmup(deps, "llm_warmup_stream_malformed")
                    if event.get("done") is True:
                        saw_terminal = True
                        break
                    if event.get("delta_text"):
                        saw_delta = True
            if not saw_terminal or not saw_delta:
                _fail_warmup(deps, "llm_warmup_stream_incomplete")
            probes.append(
                LlmWarmupProbeEvidence(
                    prompt_index=prompt_index,
                    suffix_index=suffix_index,
                    prompt_tokens_processed=timing.get("prompt_tokens_processed"),
                    prompt_tokens_cached=timing.get("prompt_tokens_cached"),
                    prompt_eval_ms=timing.get("prompt_eval_ms"),
                    finish_reason=timing.get("finish_reason"),
                )
            )

    evidence = LlmWarmupEvidence(
        schema="evelyn.main-llm-warmup-evidence.v3",
        probes=tuple(probes),
        prompt_abi_ids=tuple(dict.fromkeys(prompt_abi_ids)),
        exact_runtime_identity=exact_runtime_identity,
        production_prompt_match=expected_prompt_abi_ids is not None,
    )
    if deps.require_cache_proof and not evidence.cache_reuse_proven:
        _fail_warmup(deps, "llm_warmup_cache_proof_missing")
    deps.mark_startup_component("main_warmup", "done", "")
    deps.log("[STARTUP] llm_warmup_done")
    return evidence
