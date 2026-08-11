from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .cached_tts_runtime import cached_audio_path_for_answer_from_runtime, play_cached_answer_audio_from_runtime
from .discord_text_reply_runtime import stream_text_reply_from_runtime
from .discord_tts_stream_runtime import speak_answer_from_runtime, stream_tts_sentences_from_runtime
from .local_tts_stream_runtime import speak_answer_local_from_runtime, stream_local_tts_sentences_from_runtime
from .tts_interrupt_runtime import (
    speaker_verification_allows_tts_interrupt_from_runtime,
    stop_active_tts_playback_from_runtime,
    verify_speaker_for_tts_interrupt_from_runtime,
)
from .voice_delivery_runtime import (
    ask_llm_and_speak_local_from_runtime,
    ask_llm_and_speak_streaming_from_runtime,
    execute_voice_delivery_plan_from_runtime,
    finalize_voice_answer_from_runtime,
)
from .voice_ingress_runtime import (
    delayed_voice_utterance_flush_from_runtime,
    enqueue_voice_ingress_for_processing_from_runtime,
    flush_voice_utterance_buffer_from_runtime,
    process_member_audio_from_runtime,
    schedule_voice_utterance_item_from_runtime,
    voice_ingress_worker_from_runtime,
    voice_utterance_buffer_key,
)
from .voice_member_audio_pipeline_runtime import process_member_audio_pipeline_from_runtime
from .voice_reply_gate_runtime import should_reply_to_voice_from_runtime
from .voice_reply_side_effects import (
    checkpoint_accepted_voice_turn_from_runtime,
    finalize_voice_reply_side_effects_from_runtime,
)
from .voice_response_runtime import (
    build_first_response_from_runtime,
    build_followup_response_from_runtime,
    is_duplicate_followup,
    normalize_compare_text,
    split_first_response_and_followup as split_first_response_and_followup_with_deps,
)
from .voice_stream_chunks import (
    build_stream_speech_chunker_from_runtime,
    emit_delivery_plan_chunks as emit_delivery_plan_chunks_payload,
    emit_stream_delta_chunks as emit_stream_delta_chunks_payload,
    flush_streamed_answer_chunks as flush_streamed_answer_chunks_payload,
)


DepsFactory = Callable[[], Any]


@dataclass(frozen=True)
class VoiceIoCompositionDeps:
    reply_side_effects: DepsFactory
    reply_gate: DepsFactory
    ingress: DepsFactory
    ingress_entrypoint: DepsFactory
    tts_interrupt: DepsFactory
    cached_tts: DepsFactory
    discord_tts_single: DepsFactory
    discord_tts_stream: DepsFactory
    local_tts_single: DepsFactory
    local_tts_stream: DepsFactory
    response: DepsFactory
    stream_chunks: DepsFactory
    delivery: DepsFactory
    text_reply: DepsFactory
    member_audio_pipeline: DepsFactory


class VoiceIoComposition:
    """Owns voice/STT/TTS delivery adapters formerly declared in main.py."""

    def __init__(self, deps: VoiceIoCompositionDeps) -> None:
        self.deps = deps

    def checkpoint_accepted_voice_turn(
        self,
        *,
        session_key: str,
        user_id: int,
        user_text: str,
        accepted_turn_id: str,
        ttl_sec: float,
        topic_id: str,
        metrics: dict,
    ) -> None:
        checkpoint_accepted_voice_turn_from_runtime(
            session_key=session_key,
            user_id=user_id,
            user_text=user_text,
            accepted_turn_id=accepted_turn_id,
            ttl_sec=ttl_sec,
            topic_id=topic_id,
            metrics=metrics,
            deps=self.deps.reply_side_effects(),
        )

    def finalize_voice_reply_side_effects(
        self,
        *,
        guild_id: int,
        member: Any,
        session_key: str,
        room_session_key: str,
        room_key: str | None,
        person_key: str | None,
        session_memory_key: str | None,
        voice_reply: Any,
        plain_answer: str,
        metrics: dict,
        turn_scope: Any,
        accepted_turn_id: str,
        segment_id: int,
        delivery_succeeded: bool = True,
        failure_code: str = "",
    ) -> None:
        finalize_voice_reply_side_effects_from_runtime(
            guild_id=guild_id,
            member=member,
            session_key=session_key,
            room_session_key=room_session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            voice_reply=voice_reply,
            plain_answer=plain_answer,
            metrics=metrics,
            turn_scope=turn_scope,
            accepted_turn_id=accepted_turn_id,
            segment_id=segment_id,
            delivery_succeeded=delivery_succeeded,
            failure_code=failure_code,
            deps=self.deps.reply_side_effects(),
        )

    def should_reply_to_voice(
        self,
        guild_id: int,
        text: str,
        *,
        wake_detected: bool = False,
        wake_match_mode: str = "",
        session_key: str | None = None,
        room_session_key: str | None = None,
        user_id: int | None = None,
        active_speaker_user_id: int | None = None,
        ignore_tts_suppression: bool = False,
    ) -> tuple[bool, str, str]:
        return should_reply_to_voice_from_runtime(
            guild_id=guild_id,
            text=text,
            wake_detected=wake_detected,
            wake_match_mode=wake_match_mode,
            session_key=session_key,
            room_session_key=room_session_key,
            user_id=user_id,
            active_speaker_user_id=active_speaker_user_id,
            ignore_tts_suppression=ignore_tts_suppression,
            deps=self.deps.reply_gate(),
        )

    async def voice_ingress_worker(self) -> None:
        await voice_ingress_worker_from_runtime(deps=self.deps.ingress())

    def voice_utterance_buffer_key(self, item: dict[str, Any]) -> str:
        return voice_utterance_buffer_key(item)

    async def enqueue_voice_ingress_for_processing(self, item: dict[str, Any]) -> None:
        await enqueue_voice_ingress_for_processing_from_runtime(item, deps=self.deps.ingress())

    async def flush_voice_utterance_buffer(self, key: str) -> None:
        await flush_voice_utterance_buffer_from_runtime(key, deps=self.deps.ingress())

    async def delayed_voice_utterance_flush(self, key: str, delay_sec: float) -> None:
        await delayed_voice_utterance_flush_from_runtime(key, delay_sec, deps=self.deps.ingress())

    async def schedule_voice_utterance_item(self, item: dict[str, Any]) -> None:
        await schedule_voice_utterance_item_from_runtime(item, deps=self.deps.ingress())

    async def stop_active_tts_playback(self, guild_id: int | None, *, reason: str = "interrupt") -> bool:
        return await stop_active_tts_playback_from_runtime(
            guild_id,
            deps=self.deps.tts_interrupt(),
            reason=reason,
        )

    async def verify_speaker_for_tts_interrupt(
        self,
        audio: Any,
        *,
        sampling_rate: int,
        source: str | None,
        metrics: dict | None = None,
    ) -> Any:
        return await verify_speaker_for_tts_interrupt_from_runtime(
            audio,
            deps=self.deps.tts_interrupt(),
            sampling_rate=sampling_rate,
            source=source,
            metrics=metrics,
        )

    def speaker_verification_allows_tts_interrupt(self, result: Any) -> bool:
        return speaker_verification_allows_tts_interrupt_from_runtime(result)

    def cached_audio_path_for_answer(self, answer: str) -> Any:
        return cached_audio_path_for_answer_from_runtime(answer, deps=self.deps.cached_tts())

    async def play_cached_answer_audio(
        self,
        vc: Any,
        answer: str,
        *,
        turn_id: str | None = None,
        session_key: str | None = None,
        metrics: dict | None = None,
    ) -> bool:
        return await play_cached_answer_audio_from_runtime(
            vc,
            answer,
            deps=self.deps.cached_tts(),
            turn_id=turn_id,
            session_key=session_key,
            metrics=metrics,
        )

    async def speak_answer(
        self,
        vc: Any,
        answer: str,
        *,
        turn_id: str | None = None,
        session_key: str | None = None,
        turn_scope: Any | None = None,
        metrics: dict | None = None,
    ) -> None:
        await speak_answer_from_runtime(
            vc,
            answer,
            deps=self.deps.discord_tts_single(),
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
            metrics=metrics,
        )

    async def stream_tts_sentences(
        self,
        vc: Any,
        sentence_queue: Any,
        *,
        metrics: dict | None = None,
        turn_id: str | None = None,
        session_key: str | None = None,
        turn_scope: Any | None = None,
    ) -> None:
        await stream_tts_sentences_from_runtime(
            vc,
            sentence_queue,
            deps=self.deps.discord_tts_stream(),
            metrics=metrics,
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
        )

    async def speak_answer_local(
        self,
        answer: str,
        *,
        turn_id: str | None = None,
        session_key: str | None = None,
        turn_scope: Any | None = None,
        metrics: dict | None = None,
    ) -> bool:
        return await speak_answer_local_from_runtime(
            answer,
            deps=self.deps.local_tts_single(),
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
            metrics=metrics,
        )

    async def stream_local_tts_sentences(
        self,
        sentence_queue: Any,
        *,
        metrics: dict | None = None,
        turn_id: str | None = None,
        session_key: str | None = None,
        turn_scope: Any | None = None,
    ) -> int:
        return await stream_local_tts_sentences_from_runtime(
            sentence_queue,
            deps=self.deps.local_tts_stream(),
            metrics=metrics,
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
        )

    def split_first_response_and_followup(self, answer: str) -> tuple[str, str]:
        return split_first_response_and_followup_with_deps(answer, deps=self.deps.response())

    def normalize_compare_text(self, text: str) -> str:
        return normalize_compare_text(text)

    def is_duplicate_followup(self, first_response: str, followup_text: str) -> bool:
        return is_duplicate_followup(first_response, followup_text)

    async def build_first_response(
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
    ) -> tuple[Any, str, dict | None]:
        return await build_first_response_from_runtime(
            user_text,
            deps=self.deps.response(),
            guild_id=guild_id,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
        )

    async def build_followup_response(
        self,
        user_text: str,
        first_response: str,
        *,
        guild_id: int | None = None,
        session_key: str | None = None,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        source: str = "text",
        debug_text: str | None = None,
        metrics: dict | None = None,
    ) -> Any:
        return await build_followup_response_from_runtime(
            user_text,
            first_response,
            deps=self.deps.response(),
            guild_id=guild_id,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
        )

    def build_stream_speech_chunker(self, *, metrics: dict | None) -> Any:
        return build_stream_speech_chunker_from_runtime(metrics=metrics, deps=self.deps.stream_chunks())

    async def emit_stream_delta_chunks(
        self,
        delta_text: str,
        *,
        speech_chunker: Any,
        on_sentence: Callable[[str], Awaitable[None]] | None,
        question_stream_state: dict[str, int] | None = None,
    ) -> bool:
        return await emit_stream_delta_chunks_payload(
            delta_text,
            speech_chunker=speech_chunker,
            on_sentence=on_sentence,
            question_stream_state=question_stream_state,
        )

    async def flush_streamed_answer_chunks(
        self,
        answer: str,
        *,
        speech_chunker: Any,
        on_sentence: Callable[[str], Awaitable[None]] | None,
        emitted_any: bool,
        question_stream_state: dict[str, int] | None = None,
    ) -> None:
        await flush_streamed_answer_chunks_payload(
            answer,
            speech_chunker=speech_chunker,
            on_sentence=on_sentence,
            emitted_any=emitted_any,
            question_stream_state=question_stream_state,
        )

    async def emit_delivery_plan_chunks(
        self,
        delivery_plan: Any,
        *,
        on_sentence: Callable[[str], Awaitable[None]] | None,
    ) -> None:
        await emit_delivery_plan_chunks_payload(delivery_plan, on_sentence=on_sentence)

    async def execute_voice_delivery_plan(
        self,
        vc: Any,
        delivery_plan: Any,
        *,
        metrics: dict,
        turn_id: str | None,
        session_key: str | None,
        turn_scope: Any | None,
    ) -> int:
        return await execute_voice_delivery_plan_from_runtime(
            vc,
            delivery_plan,
            deps=self.deps.delivery(),
            metrics=metrics,
            turn_id=turn_id,
            session_key=session_key,
            turn_scope=turn_scope,
        )

    async def finalize_voice_answer(
        self,
        answer: str,
        *,
        on_final_answer: Callable[[str], Awaitable[None]] | None,
        delivery: Any,
        metrics: dict,
    ) -> tuple[str, int]:
        return await finalize_voice_answer_from_runtime(
            answer,
            on_final_answer=on_final_answer,
            delivery=delivery,
            metrics=metrics,
            deps=self.deps.delivery(),
        )

    async def ask_llm_and_speak_local(
        self,
        _vc: Any,
        user_text: str,
        guild_id: int | None = None,
        on_final_answer: Callable[[str], Awaitable[None]] | None = None,
        *,
        session_key: str | None = None,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        source: str = "voice",
        debug_text: str | None = None,
        metrics: dict | None = None,
        turn_scope: Any | None = None,
    ) -> str:
        return await ask_llm_and_speak_local_from_runtime(
            _vc,
            user_text,
            deps=self.deps.delivery(),
            guild_id=guild_id,
            on_final_answer=on_final_answer,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            turn_scope=turn_scope,
        )

    async def ask_llm_and_speak_streaming(
        self,
        vc: Any,
        user_text: str,
        guild_id: int | None = None,
        on_final_answer: Callable[[str], Awaitable[None]] | None = None,
        *,
        session_key: str | None = None,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        source: str = "voice",
        debug_text: str | None = None,
        metrics: dict | None = None,
        turn_scope: Any | None = None,
    ) -> str:
        return await ask_llm_and_speak_streaming_from_runtime(
            vc,
            user_text,
            deps=self.deps.delivery(),
            guild_id=guild_id,
            on_final_answer=on_final_answer,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
            turn_scope=turn_scope,
        )

    async def stream_text_reply(
        self,
        channel: Any,
        user_text: str,
        *,
        guild_id: int,
        session_key: str,
        turn_id: str | None = None,
        room_key: str | None = None,
        person_key: str | None = None,
        session_memory_key: str | None = None,
        source: str = "text",
        debug_text: str | None = None,
        include_voice: bool = False,
        turn_scope: Any | None = None,
        proactive_resolution: dict | None = None,
        ingress_recovery_context: dict | None = None,
        before_text_delivery: Callable[..., Any] | None = None,
        after_text_delivery: Callable[..., Any] | None = None,
    ) -> tuple[str, Any | None, dict, Any]:
        return await stream_text_reply_from_runtime(
            channel,
            user_text,
            guild_id=guild_id,
            session_key=session_key,
            turn_id=turn_id,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            include_voice=include_voice,
            turn_scope=turn_scope,
            proactive_resolution=proactive_resolution,
            ingress_recovery_context=ingress_recovery_context,
            before_text_delivery=before_text_delivery,
            after_text_delivery=after_text_delivery,
            deps=self.deps.text_reply(),
        )

    async def process_member_audio(
        self,
        member: Any | None,
        pcm_bytes: bytes,
        debug_meta: dict | None = None,
    ) -> None:
        await process_member_audio_from_runtime(
            member=member,
            pcm_bytes=pcm_bytes,
            debug_meta=debug_meta,
            deps=self.deps.ingress_entrypoint(),
        )

    async def process_member_audio_impl(
        self,
        member: Any | None,
        pcm_bytes: bytes,
        debug_meta: dict | None = None,
        *,
        session_key: str,
        room_session_key: str,
        room_key: str | None,
        person_key: str | None,
        session_memory_key: str | None,
        turn_id: str,
        segment_id: int,
        ingress_during_reply: bool = False,
        owner_user_id_on_ingress: int | None = None,
        voice_listener_binding: Any = None,
    ) -> None:
        await process_member_audio_pipeline_from_runtime(
            member,
            pcm_bytes,
            debug_meta,
            session_key=session_key,
            room_session_key=room_session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            turn_id=turn_id,
            segment_id=segment_id,
            ingress_during_reply=ingress_during_reply,
            owner_user_id_on_ingress=owner_user_id_on_ingress,
            voice_listener_binding=voice_listener_binding,
            deps=self.deps.member_audio_pipeline(),
        )
