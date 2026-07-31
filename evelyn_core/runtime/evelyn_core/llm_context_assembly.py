from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .context_pipeline import ContextBuilder, ContextPolicy
from .cross_surface_continuity import (
    CrossSurfaceMergeOutcome,
)
from .vision_runtime import VisionEvidence, record_vision_evidence, vision_evidence_from_metrics


@dataclass(frozen=True)
class LlmContextAssemblyDeps:
    compute_runtime_mode: Callable[[dict | None], str]
    apply_runtime_mode: Callable[[str], dict[str, Any]]
    classify_llm_route_fallback: Callable[..., str]
    classify_llm_route_async: Callable[..., Awaitable[tuple[str, dict | None]]]
    session_topic_ids: dict[str, str]
    get_conversation_history: Callable[..., list[dict[str, Any]]]
    read_cached_cognitive_state: Callable[..., dict | None]
    get_matching_speculative_policy: Callable[..., dict | None]
    fast_path_policy: Callable[..., dict | None]
    session_state_snapshot: Callable[[str | None], dict[str, Any]]
    context_policy_for_fast_path_policy: Callable[..., dict[str, Any]]
    extract_question_policy_from_route_meta: Callable[[dict | None], dict[str, Any]]
    build_fast_cognitive_state: Callable[..., dict[str, Any]]
    update_cognitive_state: Callable[..., Awaitable[dict[str, Any]]]
    schedule_cognitive_refresh: Callable[..., Any]
    build_context_policy_for_turn: Callable[..., ContextPolicy]
    build_tool_use_decisions: Callable[..., list[Any]]
    build_runtime_status_context: Callable[..., Awaitable[str]]
    clean_text: Callable[[str], str]
    build_local_tool_diagnostic_context: Callable[..., str]
    project_root: Path
    build_memory_context: Callable[..., str]
    update_self_state_for_turn: Callable[..., dict[str, Any]]
    observe_live_minecraft_state: Callable[..., Awaitable[dict[str, Any] | None]]
    attach_minecraft_runtime_snapshot: Callable[..., dict[str, Any]]
    control_page_minecraft_cache_refresh_sec: float
    control_page_minecraft_cache_max_stale_sec: float
    build_conversation_state_context: Callable[..., str]
    build_runtime_state_context: Callable[..., str]
    build_evelyn_runtime_dependency_context: Callable[[], str]
    render_self_judgment_context: Callable[..., str]
    render_self_state_context: Callable[[dict[str, Any]], str]
    render_vision_watch_context: Callable[[], str]
    build_minecraft_skill_context: Callable[..., str]
    odyssey_capability_json_dir: Path
    build_skill_context_hint: Callable[[ContextPolicy], str]
    build_vision_context_hint: Callable[..., str]
    build_live_vision_context: Callable[..., Awaitable[str]]
    render_tool_use_context: Callable[[list[Any]], str]
    build_basic_context_packet: Callable[..., Any]
    ask_confidence_threshold_for_source: Callable[[str], float]
    apply_ask_gating: Callable[..., dict[str, Any]]
    log_turn_event: Callable[..., Any]
    visible_text: Callable[[str], str]
    merge_cross_surface_context: (
        Callable[
            ...,
            (
                CrossSurfaceMergeOutcome
                | list[dict[str, Any]]
            ),
        ]
        | None
    ) = None
    log: Callable[..., Any] = print


def apply_vision_evidence_to_tool_decisions(
    tool_use_decisions: list[Any],
    evidence: VisionEvidence,
) -> None:
    for decision in tool_use_decisions:
        if decision.tool_name not in {"vision_capture_or_watch", "vision_ocr"}:
            continue
        decision.status = (
            "executed"
            if evidence.satisfies_tool(decision.tool_name)
            else "failed_or_unavailable"
        )
        decision.evidence = evidence.provenance_summary(tool_name=decision.tool_name)


async def prepare_llm_messages_from_runtime(
    user_text: str,
    *,
    deps: LlmContextAssemblyDeps,
    guild_id: int | None = None,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
    metrics: dict | None = None,
    turn_scope: Any = None,
) -> tuple[list[dict], dict | None, str, ContextPolicy]:
    if turn_scope is not None:
        turn_scope.raise_if_cancelled()
    runtime_mode = deps.compute_runtime_mode(metrics)
    runtime_opts = deps.apply_runtime_mode(runtime_mode)
    route_started_at = time.monotonic()
    if runtime_opts.get("skip_router"):
        route = deps.classify_llm_route_fallback(user_text, source=source)
        route_meta = {"selected": route, "source": "runtime_mode", "mode": runtime_mode}
    else:
        route, route_meta = await deps.classify_llm_route_async(
            user_text,
            guild_id=guild_id,
            source=source,
            session_key=session_key,
        )
    if metrics is not None:
        metrics.setdefault("marks", {})["route_ready"] = (time.monotonic() - route_started_at) * 1000.0
        metrics.setdefault("meta", {}).update(
            {
                "source": source,
                "session_key": session_key,
                "guild_id": guild_id,
                "topic_id": deps.session_topic_ids.get(session_key or "", "") if session_key else None,
                "runtime_mode": runtime_mode,
                "runtime_opts": dict(runtime_opts),
            }
        )
    messages = list(deps.get_conversation_history(session_key=session_key, guild_id=guild_id))
    if deps.merge_cross_surface_context is not None:
        merge_outcome = deps.merge_cross_surface_context(
            messages,
            session_key=session_key,
            current_user_text=user_text,
        )
        if isinstance(
            merge_outcome,
            CrossSurfaceMergeOutcome,
        ):
            messages = [
                dict(message)
                for message in merge_outcome.messages
            ]
            if metrics is not None:
                metrics.setdefault("meta", {})[
                    "cross_surface_continuity"
                ] = merge_outcome.public_status()
        else:
            messages = list(merge_outcome)
    cognitive_state: dict | None = None

    if turn_scope is not None:
        turn_scope.raise_if_cancelled()
    cognitive_started_at = time.monotonic()
    cached_cognitive_state = deps.read_cached_cognitive_state(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    speculative = deps.get_matching_speculative_policy(session_key, user_text) if source == "voice" else None
    local_fast_policy = (speculative or {}).get("policy") or deps.fast_path_policy(
        user_text,
        source,
        deps.session_state_snapshot(session_key),
    )
    if local_fast_policy is not None:
        route_meta = dict(route_meta or {})
        route_meta["context_policy"] = deps.context_policy_for_fast_path_policy(local_fast_policy, source=source)
    if metrics is not None:
        metrics.setdefault("meta", {})["route_question_policy"] = deps.extract_question_policy_from_route_meta(route_meta)
    should_block_on_cognitive = guild_id is not None and (cached_cognitive_state is None or route == "sub_wait")
    if local_fast_policy is not None:
        cognitive_state = deps.build_fast_cognitive_state(
            user_text,
            action=str(local_fast_policy.get("action", "answer")),
            current_state=cached_cognitive_state,
            reason_brief=str(local_fast_policy.get("reason_brief", "fast_path")),
        )
        if metrics is not None:
            metrics.setdefault("meta", {})["cognitive_mode"] = "fast_path"
    elif should_block_on_cognitive and guild_id is not None:
        cognitive_state = await deps.update_cognitive_state(
            guild_id,
            user_text,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            turn_scope=turn_scope,
        )
        if metrics is not None:
            metrics.setdefault("meta", {})["cognitive_mode"] = "blocking"
    else:
        cognitive_state = cached_cognitive_state
        if guild_id is not None and runtime_opts.get("memory_update_mode") != "defer":
            deps.schedule_cognitive_refresh(
                guild_id,
                user_text,
                reason=f"{source}:{route}",
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                source=source,
                turn_scope=turn_scope,
            )
        if metrics is not None:
            metrics.setdefault("meta", {})["cognitive_mode"] = "background"
    if metrics is not None and should_block_on_cognitive:
        metrics.setdefault("marks", {})["cognitive_hotpath_ms"] = (time.monotonic() - cognitive_started_at) * 1000.0

    if turn_scope is not None:
        turn_scope.raise_if_cancelled()

    context_policy = deps.build_context_policy_for_turn(
        user_text=user_text,
        source=source,
        route=route,
        route_meta=route_meta,
        cognitive_state=cognitive_state,
    )
    tool_use_decisions = deps.build_tool_use_decisions(user_text, context_policy)
    if any(decision.tool_name in {"vision_capture_or_watch", "vision_ocr"} for decision in tool_use_decisions):
        context_policy.needs_vision = True
        context_policy.priority = "accuracy"
        if "tool_vision" not in context_policy.context_focus:
            context_policy.context_focus.append("tool_vision")
    if any(decision.tool_name == "web_current_info" for decision in tool_use_decisions):
        context_policy.needs_search = True

    for decision in tool_use_decisions:
        if decision.tool_name != "runtime_status" or not decision.auto_allowed:
            continue
        try:
            runtime_status = await deps.build_runtime_status_context(force=bool(decision.required_before_answer))
            decision.status = "executed" if deps.clean_text(runtime_status) else "executed_empty"
            decision.evidence = deps.clean_text(runtime_status)[:500]
        except Exception as exc:
            decision.status = "failed"
            decision.evidence = deps.clean_text(repr(exc))[:240]
    for decision in tool_use_decisions:
        if decision.tool_name != "local_file_or_log_read" or not decision.auto_allowed:
            continue
        try:
            local_context = deps.build_local_tool_diagnostic_context(user_text, project_root=deps.project_root)
            decision.status = "executed" if deps.clean_text(local_context) else "executed_empty"
            decision.evidence = deps.clean_text(local_context)[:800] if deps.clean_text(local_context) else "No matching local diagnostic snippets were selected."
        except Exception as exc:
            decision.status = "failed"
            decision.evidence = deps.clean_text(repr(exc))[:240]

    memory_context = ""
    memory_receipt: dict[str, Any] = {
        "schema": "memory.context-receipt.v1",
        "state": "not_requested",
        "groundingState": "not_requested",
        "contentFree": True,
    }
    if guild_id is not None and context_policy.needs_memory:
        memory_started_at = time.monotonic()
        memory_context = deps.build_memory_context(
            guild_id,
            user_text,
            cognitive_state=cognitive_state,
            session_key=session_key,
            session_state=deps.session_state_snapshot(session_key),
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            receipt=memory_receipt,
        )
        if metrics is not None:
            memory_elapsed = (time.monotonic() - memory_started_at) * 1000.0
            metrics.setdefault("marks", {})["memory_ready"] = memory_elapsed
    elif metrics is not None:
        metrics.setdefault("meta", {})["memory_context_skipped_by_policy"] = True
    for decision in tool_use_decisions:
        if decision.tool_name != "memory_recall":
            continue
        if guild_id is None:
            decision.status = "skipped_no_memory_scope"
            decision.evidence = "No guild/session memory scope was available for this turn."
        elif deps.clean_text(memory_context):
            decision.status = "executed"
            decision.evidence = (
                f"memory_context_chars={len(memory_context)}; "
                f"receipt_state={deps.clean_text(str(memory_receipt.get('state') or 'unknown'))}; "
                f"grounding={deps.clean_text(str(memory_receipt.get('groundingState') or 'unknown'))}; "
                f"note_count={int(memory_receipt.get('suppliedNoteCount') or 0)}"
            )
        else:
            decision.status = "executed_empty"
            decision.evidence = "No relevant memory rows were selected."

    session_snapshot = deps.session_state_snapshot(session_key)
    self_state = deps.update_self_state_for_turn(user_text, source=source)
    live_context_minecraft_state: dict[str, Any] | None = None
    if guild_id is not None and (context_policy.needs_minecraft_state or context_policy.needs_skill_graph):
        try:
            live_context_minecraft_state = await deps.observe_live_minecraft_state(guild_id)
        except Exception as e:
            live_context_minecraft_state = deps.attach_minecraft_runtime_snapshot(
                {"last_error": deps.clean_text(repr(e))[:160]},
                source="context_error",
                now=time.time(),
                observed_at=time.time(),
                stale_after_sec=deps.control_page_minecraft_cache_refresh_sec,
                expired_after_sec=deps.control_page_minecraft_cache_max_stale_sec,
                last_error=deps.clean_text(repr(e))[:160],
            )
        if metrics is not None and isinstance(live_context_minecraft_state, dict):
            runtime_snapshot = live_context_minecraft_state.get("runtime_snapshot")
            if isinstance(runtime_snapshot, dict):
                metrics.setdefault("meta", {})["minecraft_snapshot_age_ms"] = (
                    None
                    if runtime_snapshot.get("age_sec") is None
                    else max(0.0, float(runtime_snapshot.get("age_sec") or 0.0) * 1000.0)
                )
                metrics.setdefault("meta", {})["minecraft_snapshot_freshness"] = runtime_snapshot.get("freshness")
    conversation_context = deps.build_conversation_state_context(
        cognitive_state=cognitive_state,
        session_state=session_snapshot,
        route=route,
    )
    runtime_context = deps.build_runtime_state_context(
        source=source,
        route=route,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        session_state=session_snapshot,
    )
    dependency_context = deps.build_evelyn_runtime_dependency_context()
    self_judgment_context = deps.render_self_judgment_context(
        user_text,
        source=source,
        state=self_state,
        route=route,
        context_policy=context_policy,
    )
    runtime_context = "\n".join(
        part
        for part in (
            runtime_context,
            deps.render_self_state_context(self_state),
            self_judgment_context,
            deps.render_vision_watch_context(),
            dependency_context,
        )
        if deps.clean_text(part)
    )
    skill_context = deps.build_minecraft_skill_context(
        context_policy,
        user_text=user_text,
        minecraft_state=live_context_minecraft_state,
        skill_library_path=deps.project_root / "third_party" / "Voyager" / "skill_library" / "skill" / "skills.json",
        capability_data_dir=deps.odyssey_capability_json_dir if deps.odyssey_capability_json_dir.exists() else None,
    )
    if not skill_context:
        skill_context = deps.build_skill_context_hint(context_policy)
    vision_context_parts = [deps.build_vision_context_hint(context_policy, user_text=user_text)]
    vision_evidence = VisionEvidence(
        state="unavailable" if context_policy.needs_vision else "unknown",
        reason_code="not_requested" if not context_policy.needs_vision else "missing_evidence_contract",
    )
    if context_policy.needs_vision:
        vision_context_parts.append(
            "VISION_ANSWER_RULE: This turn requested screen/vision evidence. "
            "Only a vision.evidence.v2 result with evidence_available=true, freshness=live, "
            "and an unexpired timestamp counts as an observation. "
            "A request, policy hint, capture attempt, or failure message is not visual evidence. "
            "When evidence is unavailable, say that the screen could not be observed and do not infer its contents."
        )
        vision_runtime_metrics = metrics if metrics is not None else {"meta": {}, "marks": {}}
        try:
            live_vision_context = await deps.build_live_vision_context(
                user_text,
                metrics=vision_runtime_metrics,
            )
        except Exception as exc:
            vision_runtime_metrics.setdefault("meta", {})["vision_runtime_error"] = deps.clean_text(
                repr(exc)
            )[:240]
            record_vision_evidence(
                vision_runtime_metrics,
                VisionEvidence(state="failed", reason_code="vision_runtime_error"),
            )
            live_vision_context = (
                "Local screen vision failed before a usable observation was produced. "
                "Do not claim the screen was analyzed."
            )
        vision_evidence = vision_evidence_from_metrics(vision_runtime_metrics)
        if vision_evidence.state != "observed":
            live_vision_context = (
                "Local screen observation was discarded because its evidence "
                "was unavailable, stale, or invalid. Do not infer screen contents."
            )
        vision_context_parts.append(live_vision_context)
        vision_context_parts.append(
            "VISION_EVIDENCE_GATE: " + vision_evidence.provenance_summary()
        )
    vision_context = "\n\n".join(part for part in vision_context_parts if deps.clean_text(part))
    apply_vision_evidence_to_tool_decisions(tool_use_decisions, vision_evidence)
    tool_context = deps.render_tool_use_context(tool_use_decisions)
    context_packet = deps.build_basic_context_packet(
        current_user_input="",
        memory_context=memory_context if context_policy.needs_memory else "",
        runtime_state=runtime_context if context_policy.needs_runtime_state else dependency_context,
        conversation_state=conversation_context,
        skill_context=skill_context,
        vision_context=vision_context,
        tool_context=tool_context,
        policy=context_policy,
    )
    if context_packet.sections():
        messages = ContextBuilder().build_messages(context_packet, messages)
    if metrics is not None:
        metrics.setdefault("marks", {})["t_context_build"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
        metrics.setdefault("meta", {})["context_pipeline"] = {
            "phase": "policy_packet",
            "route": route,
            "policy": context_policy.to_dict(),
            "memory_context_chars": len(memory_context),
            "memory_receipt": dict(memory_receipt),
            "tool_decisions": [decision.to_dict() for decision in tool_use_decisions],
            "message_count": len(messages),
            "sections": [section.source or section.name for section in context_packet.sections()],
            "section_chars": {
                section.source or section.name: len(section.cleaned_content())
                for section in context_packet.sections()
            },
            "minecraft_context": bool(live_context_minecraft_state),
            "vision_context": bool(vision_context),
            "vision_requested": bool(context_policy.needs_vision),
            "vision_evidence_available": bool(vision_evidence.evidence_available),
            "vision_evidence_state": vision_evidence.state,
            "vision_scene_available": bool(vision_evidence.scene_available),
            "vision_ocr_available": bool(vision_evidence.ocr_available),
            "vision_actionable": bool(vision_evidence.actionable),
            "self_judgment_context": bool(self_judgment_context),
        }

    if cognitive_state is not None:
        gated_state = deps.apply_ask_gating(cognitive_state, source=source)
        if gated_state.get("action") != cognitive_state.get("action"):
            deps.log(
                f"[ASK GATE] source={source} action={cognitive_state.get('action')} -> {gated_state.get('action')} confidence={float(cognitive_state.get('confidence', 0.0) or 0.0):.2f} threshold={deps.ask_confidence_threshold_for_source(source):.2f}"
            )
            cognitive_state = gated_state

    if metrics is not None:
        metrics.setdefault("marks", {})["t_policy"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
    route_text = debug_text if debug_text is not None else user_text
    meta = metrics.get("meta") if metrics is not None else {}
    deps.log_turn_event(
        "policy_ready",
        turn_id=(meta or {}).get("turn_id"),
        segment_id=(meta or {}).get("segment_id"),
        chunk_index=(meta or {}).get("chunk_index"),
        session_key=session_key,
        source=source,
        route=route,
        cognitive_action=(cognitive_state or {}).get("action") if cognitive_state else None,
        topic_id=deps.session_topic_ids.get(session_key or "", "") if session_key else None,
    )
    if route_meta and route_meta.get("source") == "router":
        deps.log(
            f"[LLM ROUTE] source={source} route={route} via=router confidence={float(route_meta.get('confidence', 0.0) or 0.0):.2f} reason={route_meta.get('reason_brief', '')!r} text={deps.visible_text(route_text)!r}"
        )
    else:
        deps.log(f"[LLM ROUTE] source={source} route={route} via=fallback text={deps.visible_text(route_text)!r}")
    return messages, cognitive_state, route, context_policy
