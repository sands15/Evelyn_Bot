from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .autonomy_router import resolve_route_executor_from_runtime
from .fast_path_policy import (
    context_policy_for_fast_path_policy_from_runtime,
    deep_route_marker_count_from_runtime,
    fast_path_policy_from_runtime,
    has_negated_search_marker_from_runtime,
    is_control_page_source_from_runtime,
    is_obvious_continue_from_runtime,
    is_simple_directive_from_runtime,
    needs_search_or_deep_routing_from_runtime,
)
from .json_llm_request_runtime import ask_json_llm_from_runtime
from .llm_context_assembly import prepare_llm_messages_from_runtime
from .llm_route_runtime import classify_llm_route_from_runtime
from .llm_warmup_runtime import warmup_llm_from_runtime
from .main_llm_runtime import (
    ask_llm_once_from_runtime,
    execute_main_llm_once_from_runtime,
    render_tool_synthesis_recent_context as render_tool_synthesis_recent_context_with_deps,
    resolve_promised_search_final_answer_from_runtime,
    synthesize_tool_result_with_main_llm_from_runtime,
    tool_synthesis_answer_drifted as tool_synthesis_answer_drifted_payload,
)
from .response_output_policy import (
    extract_answer_from_reasoning_from_runtime,
    extract_json_object_from_runtime,
    sanitize_model_output_from_runtime,
)
from .search_answer_runtime import answer_from_search_results_from_runtime
from .search_followup_runtime import (
    build_search_query_from_runtime,
    deliver_proactive_followup_from_runtime,
    run_search_followup_from_runtime,
    schedule_search_followup_from_runtime,
    schedule_search_followup_singleflight_from_runtime,
)
from .voice_route_execution import (
    execute_main_llm_streaming_turn as execute_main_llm_streaming_turn_with_deps,
    execute_search_then_answer_action as execute_search_then_answer_action_with_deps,
    maybe_execute_registered_route as maybe_execute_registered_route_with_deps,
    maybe_handle_short_circuit_route as maybe_handle_short_circuit_route_with_deps,
    prepare_route_context as prepare_route_context_with_deps,
)
from .voice_turn_entry_runtime import ask_llm_streaming_from_runtime


DepsFactory = Callable[[], Any]


@dataclass(frozen=True)
class LlmRouteCompositionDeps:
    fast_path: DepsFactory
    llm_context: DepsFactory
    summary_json: DepsFactory
    router_json: DepsFactory
    llm_route: DepsFactory
    response_output: DepsFactory
    search_answer: DepsFactory
    search_followup: DepsFactory
    llm_warmup: DepsFactory
    main_llm: DepsFactory
    ask_llm_once: DepsFactory
    route_executor: DepsFactory
    voice_route_execution: DepsFactory
    voice_main_streaming: DepsFactory
    voice_turn_entry: DepsFactory
    search_payload: Callable[..., Awaitable[list[Any]]]


class LlmRouteComposition:
    """Owns LLM, search, and route adapters formerly declared in main.py."""

    def __init__(self, deps: LlmRouteCompositionDeps) -> None:
        self.deps = deps

    def is_control_page_source(self, source: str) -> bool:
        return is_control_page_source_from_runtime(source, deps=self.deps.fast_path())

    def deep_route_marker_count(self, text: str, *, ignore_search_markers: bool = False) -> int:
        return deep_route_marker_count_from_runtime(
            text,
            ignore_search_markers=ignore_search_markers,
            deps=self.deps.fast_path(),
        )

    def has_negated_search_marker(self, text: str) -> bool:
        return has_negated_search_marker_from_runtime(text, deps=self.deps.fast_path())

    def needs_search_or_deep_routing(self, text: str, *, source: str = "text") -> bool:
        return needs_search_or_deep_routing_from_runtime(text, source=source, deps=self.deps.fast_path())

    def is_simple_directive(self, text: str, *, source: str = "text") -> bool:
        return is_simple_directive_from_runtime(text, source=source, deps=self.deps.fast_path())

    def is_obvious_continue(self, text: str, source: str, room_state: dict | None = None) -> bool:
        return is_obvious_continue_from_runtime(text, source, room_state=room_state, deps=self.deps.fast_path())

    def fast_path_policy(self, text: str, source: str, room_state: dict | None = None) -> dict | None:
        return fast_path_policy_from_runtime(text, source, room_state=room_state, deps=self.deps.fast_path())

    def context_policy_for_fast_path_policy(self, policy: dict | None, *, source: str) -> dict[str, Any]:
        return context_policy_for_fast_path_policy_from_runtime(policy, source=source, deps=self.deps.fast_path())

    async def prepare_llm_messages(
        self,
        user_text: str,
        *,
        guild_id: int | None = None,
        session_key: str | None = None,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        source: str = "text",
        debug_text: str | None = None,
        metrics: dict | None = None,
        turn_scope: Any | None = None,
    ) -> tuple[list[dict], dict | None, str, Any]:
        return await prepare_llm_messages_from_runtime(
            user_text,
            deps=self.deps.llm_context(),
            guild_id=guild_id,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            turn_scope=turn_scope,
        )

    def extract_json_object(self, text: str) -> dict:
        return extract_json_object_from_runtime(text)

    async def ask_summary_llm(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 500,
        timeout_seconds: float = 90,
        purpose: str = "memory_summary",
        hot_path: bool = False,
        turn_id: str | None = None,
        session_key: str | None = None,
        source: str | None = None,
        guild_id: int | None = None,
    ) -> dict:
        return await ask_json_llm_from_runtime(
            messages,
            deps=self.deps.summary_json(),
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            purpose=purpose,
            hot_path=hot_path,
            turn_id=turn_id,
            session_key=session_key,
            source=source,
            guild_id=guild_id,
        )

    async def ask_router_llm(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        timeout_seconds: float,
        purpose: str = "route",
        hot_path: bool = True,
        turn_id: str | None = None,
        session_key: str | None = None,
        source: str | None = None,
        guild_id: int | None = None,
    ) -> dict:
        return await ask_json_llm_from_runtime(
            messages,
            deps=self.deps.router_json(),
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            purpose=purpose,
            hot_path=hot_path,
            turn_id=turn_id,
            session_key=session_key,
            source=source,
            guild_id=guild_id,
        )

    async def classify_llm_route(
        self,
        user_text: str,
        *,
        guild_id: int | None = None,
        source: str = "text",
        session_key: str | None = None,
    ) -> tuple[str, dict | None]:
        return await classify_llm_route_from_runtime(
            user_text,
            deps=self.deps.llm_route(),
            guild_id=guild_id,
            source=source,
            session_key=session_key,
        )

    def sanitize_model_output(self, text: str) -> str:
        return sanitize_model_output_from_runtime(text, deps=self.deps.response_output())

    def extract_answer_from_reasoning(self, reasoning: str, user_text: str) -> str:
        return extract_answer_from_reasoning_from_runtime(reasoning, user_text, deps=self.deps.response_output())

    def build_search_query(
        self,
        guild_id: int | None,
        user_text: str,
        *,
        session_key: str | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> str:
        return build_search_query_from_runtime(
            guild_id,
            user_text,
            session_key=session_key,
            messages=messages,
            deps=self.deps.search_followup(),
        )

    async def search_duckduckgo(self, query: str, *, limit: int = 5) -> list[dict]:
        return [result.to_dict() for result in await self.deps.search_payload(query, limit=limit)]

    async def answer_from_search_results(self, query: str, results: list[dict]) -> str:
        return await answer_from_search_results_from_runtime(query, results, deps=self.deps.search_answer())

    async def deliver_proactive_followup(
        self,
        guild_id: int,
        query: str,
        answer: str,
        *,
        session_key: str | None,
        room_key: str | None,
        person_key: str | None,
        session_memory_key: str | None,
        channel_id: int | None,
        reply_to_message_id: int | None = None,
        source: str,
        turn_scope: Any | None = None,
        runtime_mode: str | None = None,
    ) -> None:
        await deliver_proactive_followup_from_runtime(
            guild_id,
            query,
            answer,
            deps=self.deps.search_followup(),
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            channel_id=channel_id,
            reply_to_message_id=reply_to_message_id,
            source=source,
            turn_scope=turn_scope,
            runtime_mode=runtime_mode,
        )

    def schedule_search_followup_singleflight(
        self,
        guild_id: int,
        query: str,
        *,
        session_key: str,
        room_key: str | None,
        person_key: str | None,
        session_memory_key: str | None,
        channel_id: int | None,
        reply_to_message_id: int | None,
        source: str,
        turn_scope: Any | None = None,
        runtime_mode: str | None = None,
    ) -> Any:
        return schedule_search_followup_singleflight_from_runtime(
            guild_id,
            query,
            deps=self.deps.search_followup(),
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            channel_id=channel_id,
            reply_to_message_id=reply_to_message_id,
            source=source,
            turn_scope=turn_scope,
            runtime_mode=runtime_mode,
        )

    async def run_search_followup(
        self,
        guild_id: int,
        query: str,
        *,
        session_key: str | None,
        room_key: str | None,
        person_key: str | None,
        session_memory_key: str | None,
        channel_id: int | None,
        reply_to_message_id: int | None = None,
        source: str,
        turn_scope: Any | None = None,
        runtime_mode: str | None = None,
        search_key: str | None = None,
    ) -> None:
        await run_search_followup_from_runtime(
            guild_id,
            query,
            deps=self.deps.search_followup(),
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            channel_id=channel_id,
            reply_to_message_id=reply_to_message_id,
            source=source,
            turn_scope=turn_scope,
            runtime_mode=runtime_mode,
            search_key=search_key,
        )

    def schedule_search_followup(
        self,
        guild_id: int,
        session_key: str | None,
        user_text: str,
        answer: str,
        *,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        channel_id: int | None,
        reply_to_message_id: int | None = None,
        source: str,
        force: bool = False,
        turn_scope: Any | None = None,
        runtime_mode: str | None = None,
        continuity_generation: int | None = None,
    ) -> None:
        schedule_search_followup_from_runtime(
            guild_id,
            session_key,
            user_text,
            answer,
            deps=self.deps.search_followup(),
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            channel_id=channel_id,
            reply_to_message_id=reply_to_message_id,
            source=source,
            force=force,
            turn_scope=turn_scope,
            runtime_mode=runtime_mode,
            continuity_generation=continuity_generation,
        )

    async def warmup_llm(self) -> None:
        await warmup_llm_from_runtime(deps=self.deps.llm_warmup())

    async def execute_main_llm_once(self, *, payload: dict[str, Any], user_text: str) -> tuple[str, str]:
        return await execute_main_llm_once_from_runtime(
            deps=self.deps.main_llm(),
            payload=payload,
            user_text=user_text,
        )

    def render_tool_synthesis_recent_context(
        self,
        messages: list[dict[str, Any]] | None,
        *,
        user_text: str,
        max_items: int = 6,
        max_chars: int = 900,
    ) -> str:
        return render_tool_synthesis_recent_context_with_deps(
            messages,
            deps=self.deps.main_llm(),
            user_text=user_text,
            max_items=max_items,
            max_chars=max_chars,
        )

    def tool_synthesis_answer_drifted(self, answer: str, *, user_text: str, tool_result_text: str) -> bool:
        return tool_synthesis_answer_drifted_payload(
            answer,
            user_text=user_text,
            tool_result_text=tool_result_text,
        )

    async def synthesize_tool_result_with_main_llm(
        self,
        *,
        user_text: str,
        tool_name: str,
        tool_result_text: str,
        guild_id: int | None = None,
        session_key: str | None = None,
        source: str = "text",
        messages: list[dict[str, Any]] | None = None,
        cognitive_state: dict | None = None,
        route_decision: Any | None = None,
        metrics: dict | None = None,
    ) -> str:
        return await synthesize_tool_result_with_main_llm_from_runtime(
            deps=self.deps.main_llm(),
            user_text=user_text,
            tool_name=tool_name,
            tool_result_text=tool_result_text,
            guild_id=guild_id,
            session_key=session_key,
            source=source,
            messages=messages,
            cognitive_state=cognitive_state,
            route_decision=route_decision,
            metrics=metrics,
        )

    async def resolve_promised_search_final_answer(
        self,
        *,
        user_text: str,
        answer_text: str,
        guild_id: int | None = None,
        session_key: str | None = None,
        source: str = "text",
        messages: list[dict[str, Any]] | None = None,
        cognitive_state: dict | None = None,
        route_decision: Any | None = None,
        metrics: dict | None = None,
    ) -> str:
        return await resolve_promised_search_final_answer_from_runtime(
            deps=self.deps.main_llm(),
            user_text=user_text,
            answer_text=answer_text,
            guild_id=guild_id,
            session_key=session_key,
            source=source,
            messages=messages,
            cognitive_state=cognitive_state,
            route_decision=route_decision,
            metrics=metrics,
        )

    async def ask_llm_once(
        self,
        user_text: str,
        guild_id: int | None = None,
        *,
        session_key: str | None = None,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        source: str = "text",
        debug_text: str | None = None,
        metrics: dict | None = None,
        record_question_trace_enabled: bool = True,
    ) -> str:
        return await ask_llm_once_from_runtime(
            user_text,
            deps=self.deps.ask_llm_once(),
            guild_id=guild_id,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            record_question_trace_enabled=record_question_trace_enabled,
        )

    def resolve_route_executor(self, *, guild_id: int | None, route_name: str) -> Any:
        return resolve_route_executor_from_runtime(
            guild_id,
            route_name,
            deps=self.deps.route_executor(),
        )

    async def execute_search_then_answer_action(
        self,
        *,
        guild_id: int | None,
        user_text: str,
        session_key: str | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> Any:
        return await execute_search_then_answer_action_with_deps(
            deps=self.deps.voice_route_execution(),
            guild_id=guild_id,
            user_text=user_text,
            session_key=session_key,
            messages=messages,
        )

    async def prepare_route_context(
        self,
        user_text: str,
        guild_id: int | None = None,
        *,
        session_key: str | None = None,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        source: str = "text",
        debug_text: str | None = None,
        metrics: dict | None = None,
        turn_scope: Any | None = None,
    ) -> Any:
        return await prepare_route_context_with_deps(
            user_text,
            guild_id,
            deps=self.deps.voice_route_execution(),
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            turn_scope=turn_scope,
        )

    async def maybe_handle_short_circuit_route(
        self,
        *,
        route_decision: Any,
        source: str,
        guild_id: int | None,
        user_text: str,
        session_key: str | None,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        debug_text: str | None = None,
        on_sentence: Callable[[str], Awaitable[None]] | None = None,
        on_first_chunk: Callable[[], None] | None = None,
        awaiting_user_reply: bool = False,
        metrics: dict | None = None,
        messages: list[dict[str, Any]] | None = None,
        cognitive_state: dict | None = None,
    ) -> Any:
        return await maybe_handle_short_circuit_route_with_deps(
            deps=self.deps.voice_route_execution(),
            route_decision=route_decision,
            source=source,
            guild_id=guild_id,
            user_text=user_text,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            debug_text=debug_text,
            on_sentence=on_sentence,
            on_first_chunk=on_first_chunk,
            awaiting_user_reply=awaiting_user_reply,
            metrics=metrics,
            messages=messages,
            cognitive_state=cognitive_state,
        )

    async def maybe_execute_registered_route(
        self,
        *,
        route_decision: Any,
        user_text: str,
        source: str,
        guild_id: int | None,
        session_key: str | None,
        room_key: str | None,
        person_key: str | None,
        session_memory_key: str | None,
        debug_text: str | None,
        metrics: dict | None,
        cognitive_state: dict | None,
        messages: list[dict[str, Any]] | None = None,
        allow_internal_routes: set[str] | None = None,
    ) -> str | None:
        return await maybe_execute_registered_route_with_deps(
            deps=self.deps.voice_route_execution(),
            route_decision=route_decision,
            user_text=user_text,
            source=source,
            guild_id=guild_id,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            debug_text=debug_text,
            metrics=metrics,
            cognitive_state=cognitive_state,
            messages=messages,
            allow_internal_routes=allow_internal_routes,
        )

    async def execute_main_llm_streaming_turn(
        self,
        *,
        request: Any,
        route_context: Any,
        on_first_chunk: Callable[[], None] | None,
    ) -> str:
        return await execute_main_llm_streaming_turn_with_deps(
            deps=self.deps.voice_main_streaming(),
            request=request,
            route_context=route_context,
            on_first_chunk=on_first_chunk,
        )

    async def ask_llm_streaming(
        self,
        user_text: str,
        guild_id: int | None = None,
        on_sentence: Callable[[str], Awaitable[None]] | None = None,
        on_first_chunk: Callable[[], None] | None = None,
        *,
        session_key: str | None = None,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        source: str = "text",
        debug_text: str | None = None,
        metrics: dict | None = None,
        turn_scope: Any | None = None,
    ) -> str:
        return await ask_llm_streaming_from_runtime(
            user_text,
            deps=self.deps.voice_turn_entry(),
            guild_id=guild_id,
            on_sentence=on_sentence,
            on_first_chunk=on_first_chunk,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            turn_scope=turn_scope,
        )


__all__ = ["LlmRouteComposition", "LlmRouteCompositionDeps"]
