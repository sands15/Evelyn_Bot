from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import config as runtime_config
from .context_pipeline import ContextPolicy
from .memory_deletion_journal import (
    MemoryDeletionJournalIntegrityError,
    memory_deletion_journal_read_guard,
)
from .memory_exposure import (
    MemoryExposurePosition,
    capture_memory_exposure_position,
    memory_exposure_guard,
    read_memory_version,
)
from .route_fallback_policy import normalize_route_name, should_force_voice_context_route
from .turn_budget import build_turn_execution_budget


@dataclass(frozen=True)
class LlmRouteRuntimeDeps:
    classify_llm_route_fallback: Callable[..., str]
    fast_path_policy: Callable[[str, str, dict], dict | None]
    session_state_snapshot: Callable[[str | None], dict]
    load_working_summary: Callable[[int], str]
    load_cognitive_state: Callable[[int], dict]
    normalize_cognitive_state: Callable[[dict], dict]
    load_recent_raw: Callable[[int], list[dict]]
    load_recent_facts: Callable[[int], list[dict]]
    format_memory_rows_for_llm: Callable[..., str]
    compact_memory_text: Callable[..., str]
    ask_router_llm: Callable[..., Awaitable[Any]]
    current_turn_id: Callable[[str | None], str | None]
    clean_text: Callable[[str], str]
    normalize_question_policy_mapping: Callable[..., dict]
    router_route_timeout_sec: float
    cognitive_timeout_sec: float
    router_llm_enabled: bool
    router_route_max_tokens: int
    log: Callable[[str], Any]


async def classify_llm_route_from_runtime(
    user_text: str,
    *,
    deps: LlmRouteRuntimeDeps,
    guild_id: int | None = None,
    source: str = "text",
    session_key: str | None = None,
    memory_index_dir: Path | None = None,
) -> tuple[str, dict | None]:
    fallback_route = deps.classify_llm_route_fallback(user_text, source=source)
    budget = build_turn_execution_budget(
        router_timeout_sec=deps.router_route_timeout_sec,
        context_timeout_sec=deps.cognitive_timeout_sec,
        memory_timeout_sec=deps.cognitive_timeout_sec,
        fallback_route=fallback_route,
        router_enabled=deps.router_llm_enabled,
    )
    fast_policy = deps.fast_path_policy(user_text, source, deps.session_state_snapshot(session_key))
    if fast_policy is not None:
        fast_route = normalize_route_name(str(fast_policy.get("route", fallback_route)))
        fast_budget = build_turn_execution_budget(
            router_timeout_sec=deps.router_route_timeout_sec,
            context_timeout_sec=deps.cognitive_timeout_sec,
            memory_timeout_sec=deps.cognitive_timeout_sec,
            fallback_route=fallback_route,
            router_enabled=False,
            context_policy=fast_policy,
            fallback_reason="fast_path",
        )
        return fast_route, {
            "selected": fast_route,
            "source": "fast_path",
            "confidence": 0.92,
            "reason_brief": deps.clean_text(str(fast_policy.get("reason_brief", "fast_path"))),
            "fallback": fallback_route,
            "execution_budget": fast_budget.to_dict(),
        }
    force_voice_context = source == "voice" and should_force_voice_context_route(user_text)
    if (source == "voice" and not force_voice_context) or not deps.router_llm_enabled:
        return fallback_route, {
            "selected": fallback_route,
            "source": "fallback",
            "execution_budget": budget.to_dict(),
        }

    deletion_index_dir = (
        Path(memory_index_dir)
        if memory_index_dir is not None
        else Path(runtime_config.MEMORY_ROOT) / "memory_index"
    )
    build_guard = (
        memory_deletion_journal_read_guard(
            deletion_index_dir,
            require_stable=True,
        )
        if guild_id is not None
        else contextlib.nullcontext(None)
    )
    route_memory_exposure_position = None
    try:
        with build_guard as memory_deletion_position:
            # Legacy derived artifacts have no source receipt.  A current
            # deletion position cannot prove that an old summary/state/raw
            # row was not derived from a note deleted before this turn, so
            # the router must ignore them until provenance is persisted.
            summary = ""
            default_state = deps.normalize_cognitive_state({})
            state = default_state
            recent_raw: list[dict] = []
            recent_facts: list[dict] = []
            has_memory_material = False

            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are Evelyn's lightweight router and context policy planner. "
                        "Return exactly one JSON object and no other text. "
                        "Required shape: "
                        '{"selected":"main_direct|voice_context|sub_wait","confidence":0.0,'
                        '"reason_brief":"short reason","context_policy":{'
                        '"intent":"chat|question|minecraft_task|vision_question|memory_update|control",'
                        '"needs_main_llm":true,"needs_memory":true,"needs_runtime_state":true,'
                        '"needs_minecraft_state":false,"needs_vision":false,"needs_skill_graph":false,'
                        '"needs_long_context":false,"priority":"latency|accuracy|action",'
                        '"context_focus":["current_goal"],"response_mode":"short|normal|detailed|action_only"},'
                        '"ask_mode":"none|clarify|soft_followup|preference_probe|topic_continue|idle_checkin",'
                        '"max_question_count":0,"question_reason":"short reason","question_hint":"direction only","question_source":"router"}. '
                        "Use main_direct for ordinary direct replies, voice_context when recent state/memory is important, "
                        "and sub_wait when search/wait/search_then_answer style reasoning is needed. "
                        "Set minecraft/vision/skill flags only when the current turn needs them. "
                        "Question rules: do not add a router call just for questions; if a direct answer/task/fix is requested, "
                        "use ask_mode=none and max_question_count=0. If a light follow-up is useful, allow at most one question. "
                        "question_hint is only a direction, not a final sentence."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"최근 요약:\n{summary or '(없음)'}\n\n"
                        f"현재 cognitive_state:\n{json.dumps(state, ensure_ascii=False)}\n\n"
                        f"최근 raw_transcript:\n{deps.format_memory_rows_for_llm(recent_raw, max_items=3)}\n\n"
                        f"최근 durable_facts:\n{deps.format_memory_rows_for_llm(recent_facts, max_items=3)}\n\n"
                        f"현재 사용자 입력:\n{deps.compact_memory_text(user_text, max_chars=160)}\n\n"
                        f"fallback_route={fallback_route}\nsource={source}"
                    ),
                },
            ]
            if (
                memory_deletion_position is not None
                and has_memory_material
            ):
                route_memory_exposure_position = (
                    MemoryExposurePosition(
                        deletion_position=memory_deletion_position,
                        memory_version=read_memory_version(
                            deletion_index_dir
                        ),
                    )
                )
                capture_memory_exposure_position(
                    route_memory_exposure_position
                )

            with memory_exposure_guard(
                expected_position=route_memory_exposure_position,
                required=(
                    route_memory_exposure_position is not None
                ),
                index_dir=deletion_index_dir,
            ):
                result = await deps.ask_router_llm(
                    messages,
                    max_tokens=deps.router_route_max_tokens,
                    timeout_seconds=budget.router_timeout_sec,
                    purpose="route",
                    hot_path=True,
                    turn_id=deps.current_turn_id(session_key),
                    session_key=session_key,
                    source=source,
                    guild_id=guild_id,
                    memory_deletion_position=memory_deletion_position,
                    memory_boundary_required=guild_id is not None,
                    memory_deletion_index_dir=(
                        deletion_index_dir
                        if guild_id is not None
                        else None
                    ),
                )
    except MemoryDeletionJournalIntegrityError:
        raise
    except Exception as exc:
        deps.log(
            "[ROUTER] route 실패 fallback 사용: "
            f"errorType={type(exc).__name__}"
        )
        return fallback_route, {
            "selected": fallback_route,
            "source": "fallback",
            "error": "router_failed",
            "execution_budget": budget.to_dict(),
        }

    if not isinstance(result, dict):
        return fallback_route, {
            "selected": fallback_route,
            "source": "fallback",
            "reason_brief": "invalid_router_json",
            "execution_budget": budget.to_dict(),
        }

    selected = normalize_route_name(str(result.get("selected", fallback_route)))
    meta = {
        "selected": selected,
        "source": "router",
        "confidence": float(result.get("confidence", 0.0) or 0.0),
        "reason_brief": deps.clean_text(str(result.get("reason_brief", ""))),
        "fallback": fallback_route,
        "execution_budget": budget.to_dict(),
    }
    question_policy = deps.normalize_question_policy_mapping(
        {
            "ask_mode": result.get("ask_mode"),
            "max_question_count": result.get("max_question_count"),
            "question_hint": result.get("question_hint"),
            "question_reason": result.get("question_reason"),
            "question_source": result.get("question_source") or "router",
        },
        default_source="router",
    )
    meta.update(question_policy)
    raw_context_policy = result.get("context_policy")
    if isinstance(raw_context_policy, dict):
        meta["context_policy"] = ContextPolicy.from_mapping(raw_context_policy).to_dict()
    return selected, meta
