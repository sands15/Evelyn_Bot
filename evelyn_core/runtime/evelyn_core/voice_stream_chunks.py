from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .question_shaping import filter_stream_chunk_for_question_limits
from .text import clean_tts_text
from .tts_playback import ChunkWindow, SpeechChunker
from .voice_pipeline import DeliveryPlan


@dataclass(frozen=True)
class VoiceStreamChunkDeps:
    tts_first_chunk_min_chars: int
    tts_first_chunk_target_chars: int
    tts_first_chunk_max_chars: int
    tts_next_chunk_min_chars: int
    tts_next_chunk_target_chars: int
    tts_next_chunk_max_chars: int


def build_stream_speech_chunker_from_runtime(*, metrics: dict | None, deps: VoiceStreamChunkDeps) -> SpeechChunker:
    speech_chunker = SpeechChunker()
    speech_chunker.config.first_window = ChunkWindow(
        max(1, deps.tts_first_chunk_min_chars),
        max(deps.tts_first_chunk_min_chars, deps.tts_first_chunk_target_chars),
        max(deps.tts_first_chunk_target_chars, deps.tts_first_chunk_max_chars),
        True,
        False,
    )
    speech_chunker.config.next_window = ChunkWindow(
        max(1, deps.tts_next_chunk_min_chars),
        max(deps.tts_next_chunk_min_chars, deps.tts_next_chunk_target_chars),
        max(deps.tts_next_chunk_target_chars, deps.tts_next_chunk_max_chars),
        False,
        True,
    )
    runtime_opts = ((metrics or {}).get("meta") or {}).get("runtime_opts") or {}
    if runtime_opts.get("tts_chunk_min_chars"):
        speech_chunker.config.next_window = ChunkWindow(
            int(runtime_opts.get("tts_chunk_min_chars") or speech_chunker.config.next_window.min_chars),
            speech_chunker.config.next_window.target_chars,
            speech_chunker.config.next_window.max_chars,
            speech_chunker.config.next_window.allow_soft_breaks,
            speech_chunker.config.next_window.soft_break_overflow_only,
        )
    return speech_chunker


async def emit_stream_delta_chunks(
    delta_text: str,
    *,
    speech_chunker: SpeechChunker,
    on_sentence: Callable[[str], Awaitable[None]] | None,
    question_stream_state: dict[str, int] | None = None,
) -> bool:
    emitted_any = False
    if on_sentence is not None:
        for chunk in speech_chunker.push(delta_text, max_chunks=1):
            if not chunk:
                continue
            if question_stream_state is not None:
                chunk, question_meta = filter_stream_chunk_for_question_limits(
                    chunk,
                    max_question_count=int(question_stream_state.get("max_question_count", 0)),
                    question_count_so_far=int(question_stream_state.get("question_count", 0)),
                )
                question_stream_state["question_count"] = int(question_stream_state.get("question_count", 0)) + int(
                    question_meta.get("question_count_after", 0) or 0
                )
                question_stream_state["question_removed_count"] = int(question_stream_state.get("question_removed_count", 0)) + (
                    1 if question_meta.get("question_removed") else 0
                )
                if not chunk:
                    continue
            emitted_any = True
            await on_sentence(chunk)
    return emitted_any


async def flush_streamed_answer_chunks(
    answer: str,
    *,
    speech_chunker: SpeechChunker,
    on_sentence: Callable[[str], Awaitable[None]] | None,
    emitted_any: bool,
    question_stream_state: dict[str, int] | None = None,
) -> None:
    if on_sentence is None:
        return
    ready_chunks = speech_chunker.flush()
    if not ready_chunks and answer and not emitted_any:
        ready_chunks = [clean_tts_text(answer)]
    for chunk in ready_chunks:
        if not chunk:
            continue
        if question_stream_state is not None:
            chunk, question_meta = filter_stream_chunk_for_question_limits(
                chunk,
                max_question_count=int(question_stream_state.get("max_question_count", 0)),
                question_count_so_far=int(question_stream_state.get("question_count", 0)),
            )
            question_stream_state["question_count"] = int(question_stream_state.get("question_count", 0)) + int(
                question_meta.get("question_count_after", 0) or 0
            )
            question_stream_state["question_removed_count"] = int(question_stream_state.get("question_removed_count", 0)) + (
                1 if question_meta.get("question_removed") else 0
            )
            if not chunk:
                continue
        await on_sentence(chunk)


async def emit_delivery_plan_chunks(
    delivery_plan: DeliveryPlan,
    *,
    on_sentence: Callable[[str], Awaitable[None]] | None,
) -> None:
    if on_sentence is None:
        return
    for chunk in delivery_plan.tts_chunks:
        if not chunk:
            continue
        await on_sentence(chunk)

