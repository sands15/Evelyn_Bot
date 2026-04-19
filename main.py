import audioop
import hashlib
import html
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import time
import asyncio
import uuid
import wave
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp
import numpy as np
import torch
import discord
from discord.ext import commands
from transformers import AutoProcessor, WhisperForConditionalGeneration

from evelyn_core.audio import (
    apply_light_denoise,
    compute_voice_band_metrics,
    compute_waveform_activity_stats,
    downmix_int16_stereo_to_mono_float,
    is_likely_environment_noise,
    is_probably_silent,
    prepare_stt_audio,
    resample_audio_float,
    slice_audio_window,
)
from evelyn_core.config import *
from evelyn_core.memory import *
from evelyn_core.text import (
    apply_stt_post_corrections,
    clean_text,
    clean_tts_text,
    contains_leading_wake_word,
    contains_wake_word,
    extract_leading_wake_alias,
    fuzzy_leading_wake_alias,
    is_similar,
    looks_like_brief_filler_text,
    looks_like_gibberish_probe,
    looks_like_repetitive_noise_text,
    normalize_omnivoice_tags,
    normalize_voice_text,
    normalized_wake_words,
    strip_leading_voice_fillers,
    strip_omnivoice_tags,
    strip_voice_wake_word,
    visible_text,
)
from evelyn_voice import EvelynVoiceClient


# =========================================================
# 봇 설정
# =========================================================
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True
intents.members = True

guild_prefix_cache: dict[int, str] = {}
session_histories: dict[str, list[dict]] = {}
session_followup_targets: dict[str, dict[str, int]] = {}
active_session_until: dict[str, float] = {}
active_session_user_ids: dict[str, int] = {}
session_awaiting_user_reply: dict[str, bool] = {}
session_last_speaker: dict[str, str] = {}
session_topic_ids: dict[str, str] = {}
session_turn_ids: dict[str, str] = {}
session_segment_counters: dict[str, int] = {}
session_last_turn_accepted_at: dict[str, float] = {}
background_search_tasks: dict[str, asyncio.Task] = {}


def normalize_command_prefix(prefix: str | None) -> str:
    prefix = (prefix or "").strip()
    if not prefix:
        return DEFAULT_COMMAND_PREFIX
    if any(ch.isspace() for ch in prefix):
        raise ValueError("명령어 시작 부호에는 공백을 넣을 수 없어.")
    if len(prefix) > 5:
        raise ValueError("명령어 시작 부호는 5자 이하로 해줘.")
    return prefix


def get_guild_command_prefix(guild_id: int | None) -> str:
    if guild_id is None:
        return DEFAULT_COMMAND_PREFIX
    cached = guild_prefix_cache.get(guild_id)
    if cached:
        return cached

    settings = read_json_file(guild_settings_path(guild_id))
    prefix = normalize_command_prefix(str(settings.get("command_prefix", DEFAULT_COMMAND_PREFIX)))
    guild_prefix_cache[guild_id] = prefix
    return prefix


def save_guild_command_prefix(guild_id: int, prefix: str) -> str:
    prefix = normalize_command_prefix(prefix)
    settings_path = guild_settings_path(guild_id)
    settings = read_json_file(settings_path)
    settings["command_prefix"] = prefix
    settings["updated_at"] = int(time.time())
    write_json_file(settings_path, settings)
    guild_prefix_cache[guild_id] = prefix
    return prefix


async def resolve_command_prefix(_bot, message: discord.Message):
    prefix = get_guild_command_prefix(message.guild.id if message.guild else None)
    return commands.when_mentioned_or(prefix)(_bot, message)


bot = commands.Bot(command_prefix=resolve_command_prefix, intents=intents)

session_locks: dict[str, asyncio.Lock] = {}
tts_lock = asyncio.Lock()
voice_debug_counts: dict[int, int] = {}

tts_warmup_started = False
stt_processor: Optional[Any] = None
stt_model: Optional[Any] = None
stt_backend: Optional[str] = None
http_session: Optional[aiohttp.ClientSession] = None

last_voice_reply_at: dict[int, float] = {}
last_voice_text: dict[int, str] = {}
last_bot_audio_end_at: dict[int, float] = {}
bot_speaking_guilds: set[int] = set()
memory_locks: dict[int, asyncio.Lock] = {}
cognitive_locks: dict[int, asyncio.Lock] = {}
background_cognitive_tasks: dict[str, asyncio.Task] = {}
voice_ingress_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
voice_worker_task: asyncio.Task | None = None


# =========================================================
# 유틸
# =========================================================
def new_conversation_history() -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def runtime_session_key(*, session_key: str | None = None, guild_id: int | None = None) -> str | None:
    if session_key:
        return session_key
    if guild_id is None:
        return None
    return f"guild:{guild_id}:default"


def make_text_session_key(guild_id: int, channel_id: int, user_id: int | None = None, thread_id: int | None = None) -> str:
    thread_part = f":thread:{thread_id}" if thread_id is not None else ""
    user_part = f":user:{user_id}" if user_id is not None else ""
    return f"guild:{guild_id}:text:{channel_id}{thread_part}{user_part}"


def make_voice_session_key(guild_id: int, voice_channel_id: int | None, user_id: int | None = None) -> str:
    channel_part = voice_channel_id if voice_channel_id is not None else "none"
    user_part = f":user:{user_id}" if user_id is not None else ""
    return f"guild:{guild_id}:voice:{channel_part}{user_part}"


def make_room_memory_key(kind: str, room_id: int | None) -> str:
    room_part = room_id if room_id is not None else "none"
    return f"{kind}:{room_part}"


def make_person_memory_key(user_id: int | None) -> str | None:
    if user_id is None:
        return None
    return f"user:{user_id}"


def make_session_memory_key(session_key: str | None, user_id: int | None = None) -> str | None:
    if not session_key:
        return None
    if user_id is None:
        return session_key
    return f"{session_key}:user:{user_id}"


def remember_session_followup_target(session_key: str, *, channel_id: int | None = None) -> None:
    if channel_id is None:
        return
    session_followup_targets[session_key] = {"channel_id": channel_id}


def build_topic_id(*texts: str) -> str:
    material = "\n".join(clean_text(text) for text in texts if clean_text(text))
    if not material:
        material = "idle"
    return hashlib.sha1(material.encode("utf-8", errors="ignore")).hexdigest()[:12]


def new_turn_id() -> str:
    return uuid.uuid4().hex[:12]


def current_turn_id(session_key: str | None) -> str | None:
    if not session_key:
        return None
    return session_turn_ids.get(session_key)


def next_segment_id(session_key: str | None) -> int:
    if not session_key:
        return 1
    next_value = session_segment_counters.get(session_key, 0) + 1
    session_segment_counters[session_key] = next_value
    return next_value


def start_new_turn(session_key: str | None, *, turn_id: str | None = None) -> str:
    turn_id = turn_id or new_turn_id()
    if session_key:
        session_turn_ids[session_key] = turn_id
        session_last_turn_accepted_at[session_key] = time.monotonic()
    return turn_id


def session_state_snapshot(session_key: str | None) -> dict:
    if not session_key:
        return {}
    return {
        "active_until": active_session_until.get(session_key, 0.0),
        "awaiting_user_reply": session_awaiting_user_reply.get(session_key, False),
        "last_speaker": session_last_speaker.get(session_key, ""),
        "topic_id": session_topic_ids.get(session_key, ""),
        "turn_id": session_turn_ids.get(session_key, ""),
        "last_turn_accepted_at": session_last_turn_accepted_at.get(session_key, 0.0),
    }


def update_session_state(
    session_key: str | None,
    *,
    user_id: int | None = None,
    speaker: str | None = None,
    ttl_sec: float | None = None,
    awaiting_user_reply: bool | None = None,
    topic_id: str | None = None,
    answer_text: str | None = None,
    user_text: str | None = None,
) -> None:
    if not session_key:
        return
    if ttl_sec is not None:
        active_session_until[session_key] = time.monotonic() + ttl_sec
    if user_id is not None:
        active_session_user_ids[session_key] = user_id
    if speaker is not None:
        session_last_speaker[session_key] = speaker
    if awaiting_user_reply is not None:
        session_awaiting_user_reply[session_key] = awaiting_user_reply
        if awaiting_user_reply and ttl_sec is None:
            active_session_until[session_key] = time.monotonic() + ACTIVE_CONVERSATION_AWAITING_REPLY_SEC
    if topic_id:
        session_topic_ids[session_key] = topic_id
    elif user_text or answer_text:
        session_topic_ids[session_key] = build_topic_id(user_text or "", answer_text or "")
    if session_key and session_key not in session_turn_ids:
        session_turn_ids[session_key] = new_turn_id()


def mark_session_active(
    session_key: str,
    *,
    user_id: int | None = None,
    ttl_sec: float = 90.0,
    speaker: str = "assistant",
    awaiting_user_reply: bool | None = None,
    topic_id: str | None = None,
    answer_text: str | None = None,
    user_text: str | None = None,
) -> None:
    update_session_state(
        session_key,
        user_id=user_id,
        speaker=speaker,
        ttl_sec=ttl_sec,
        awaiting_user_reply=awaiting_user_reply,
        topic_id=topic_id,
        answer_text=answer_text,
        user_text=user_text,
    )


def is_session_active_for_user(session_key: str, user_id: int | None = None) -> bool:
    expires_at = active_session_until.get(session_key, 0.0)
    awaiting_user_reply = session_awaiting_user_reply.get(session_key, False)
    if expires_at <= time.monotonic() and not awaiting_user_reply:
        return False
    remembered_user = active_session_user_ids.get(session_key)
    if remembered_user is not None and user_id is not None and remembered_user != user_id:
        return False
    return True


def get_conversation_history(*, session_key: str | None = None, guild_id: int | None = None) -> list[dict]:
    resolved = runtime_session_key(session_key=session_key, guild_id=guild_id)
    if resolved is None:
        return new_conversation_history()
    return session_histories.setdefault(resolved, new_conversation_history())


def trim_history(*, session_key: str | None = None, guild_id: int | None = None) -> None:
    history = get_conversation_history(session_key=session_key, guild_id=guild_id)
    if len(history) > 1 + MAX_HISTORY_ITEMS:
        del history[1:-MAX_HISTORY_ITEMS]


def append_history(session_key: str | None, user_text: str, answer: str, *, guild_id: int | None = None) -> None:
    history = get_conversation_history(session_key=session_key, guild_id=guild_id)
    history.append({"role": "user", "content": clean_text(user_text)})
    history.append({"role": "assistant", "content": clean_text(answer)})
    trim_history(session_key=session_key, guild_id=guild_id)


def reset_guild_runtime_state(guild_id: int) -> None:
    prefix = f"guild:{guild_id}:"
    for key in [key for key in session_histories if key.startswith(prefix)]:
        session_histories.pop(key, None)
    for key in [key for key in session_followup_targets if key.startswith(prefix)]:
        session_followup_targets.pop(key, None)
    for key in [key for key in active_session_until if key.startswith(prefix)]:
        active_session_until.pop(key, None)
        active_session_user_ids.pop(key, None)
        session_awaiting_user_reply.pop(key, None)
        session_last_speaker.pop(key, None)
        session_topic_ids.pop(key, None)
        session_turn_ids.pop(key, None)
        session_segment_counters.pop(key, None)
        session_last_turn_accepted_at.pop(key, None)
    for key in [key for key in session_locks if key.startswith(prefix)]:
        session_locks.pop(key, None)
    for key, task in list(background_search_tasks.items()):
        if key.startswith(prefix):
            if task is not None and not task.done():
                task.cancel()
            background_search_tasks.pop(key, None)
    last_voice_reply_at.pop(guild_id, None)
    last_voice_text.pop(guild_id, None)
    last_bot_audio_end_at.pop(guild_id, None)
    bot_speaking_guilds.discard(guild_id)
    memory_locks.pop(guild_id, None)
    cognitive_locks.pop(guild_id, None)
    for key, task in list(background_cognitive_tasks.items()):
        if key.startswith(prefix):
            if task is not None and not task.done():
                task.cancel()
            background_cognitive_tasks.pop(key, None)


def _sanitize_debug_label(value: str | None, *, fallback: str = "unknown") -> str:
    text = (value or "").strip()
    text = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", text)
    text = text.strip("._-")
    return text or fallback


def _trim_voice_debug_dir(guild_dir: Path) -> None:
    if VOICE_DEBUG_MAX_FILES_PER_GUILD <= 0 or not guild_dir.exists():
        return
    wavs = sorted(guild_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    overflow = len(wavs) - VOICE_DEBUG_MAX_FILES_PER_GUILD
    if overflow <= 0:
        return
    for path in wavs[:overflow]:
        try:
            path.unlink()
        except Exception:
            pass


def log_turn_event(event: str, **payload) -> None:
    if not TURN_TRACE_JSON_LOG:
        return
    record = {"event": event, "ts": round(time.time(), 3)}
    for key, value in payload.items():
        if value is None:
            continue
        record[key] = value
    print("[TURN TRACE] " + json.dumps(record, ensure_ascii=False, sort_keys=True))


def merge_log_event_payload(*, explicit: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = dict(extra or {})
    for key in explicit.keys():
        merged.pop(key, None)
    merged.update(explicit)
    return merged


def new_turn_metrics(
    *,
    source: str,
    session_key: str | None = None,
    guild_id: int | None = None,
    user_id: int | None = None,
    topic_id: str | None = None,
    turn_id: str | None = None,
    segment_id: int | None = None,
    chunk_index: int | None = None,
) -> dict:
    metrics = {
        "started_at": time.monotonic(),
        "marks": {"t_ingress": 0.0},
        "meta": {
            "source": source,
            "session_key": session_key,
            "guild_id": guild_id,
            "user_id": user_id,
            "topic_id": topic_id,
            "turn_id": turn_id,
            "segment_id": segment_id,
            "chunk_index": chunk_index,
        },
    }
    log_turn_event(
        "turn_ingress",
        source=source,
        session_key=session_key,
        guild_id=guild_id,
        user_id=user_id,
        topic_id=topic_id,
        turn_id=turn_id,
        segment_id=segment_id,
        chunk_index=chunk_index,
    )
    return metrics


def register_drop_reason(metrics: dict | None, reason: str, **extra) -> None:
    if not metrics:
        return
    meta = metrics.setdefault("meta", {})
    meta["drop_reason"] = reason
    explicit = {
        "turn_id": meta.get("turn_id"),
        "segment_id": meta.get("segment_id"),
        "chunk_index": meta.get("chunk_index"),
        "session_key": extra.get("session_key") if extra.get("session_key") is not None else meta.get("session_key"),
        "reason": reason,
    }
    log_turn_event("turn_drop", **merge_log_event_payload(explicit=explicit, extra=extra))


def save_voice_debug_audio(
    guild_id: int,
    speaker: str,
    pcm_bytes: bytes,
    audio16k: np.ndarray,
    *,
    wake_probe: str | None = None,
    final_text: str | None = None,
    debug_meta: dict | None = None,
    save_stt_audio: bool = True,
    stt_meta: dict | None = None,
) -> None:
    if not VOICE_DEBUG_SAVE_AUDIO:
        return
    try:
        base_dir = Path(VOICE_DEBUG_AUDIO_DIR)
        if not base_dir.is_absolute():
            base_dir = Path(__file__).resolve().parent / base_dir
        guild_dir = base_dir / str(guild_id)
        guild_dir.mkdir(parents=True, exist_ok=True)

        idx = voice_debug_counts.get(guild_id, 0) + 1
        voice_debug_counts[guild_id] = idx
        stamp = time.strftime("%Y%m%d-%H%M%S")
        speaker_label = _sanitize_debug_label(speaker)
        stem = f"{stamp}_{idx:04d}_{speaker_label}"

        raw_path = guild_dir / f"{stem}_raw48k.wav"
        stt_path = guild_dir / f"{stem}_stt16k.wav"
        meta_path = guild_dir / f"{stem}.json"

        with wave.open(str(raw_path), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(RATE)
            wf.writeframes(pcm_bytes)

        if save_stt_audio:
            audio16k_int16 = np.clip(audio16k, -1.0, 1.0)
            audio16k_int16 = (audio16k_int16 * 32767.0).astype(np.int16)
            with wave.open(str(stt_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(TARGET_RATE)
                wf.writeframes(audio16k_int16.tobytes())

        meta = {
            "saved_at": stamp,
            "guild_id": guild_id,
            "speaker": speaker,
            "raw_path": str(raw_path),
            "stt_path": str(stt_path) if save_stt_audio else None,
            "stt_saved": bool(save_stt_audio),
            "raw_bytes": len(pcm_bytes),
            "raw_seconds": round(len(pcm_bytes) / float(RATE * CHANNELS * 2), 3),
            "stt_samples": int(audio16k.size),
            "stt_seconds": round(audio16k.size / float(TARGET_RATE), 3),
            "wake_probe": wake_probe,
            "final_text": final_text,
        }
        if debug_meta is not None:
            meta["voice_receive"] = debug_meta
        if stt_meta is not None:
            meta["stt"] = stt_meta
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        _trim_voice_debug_dir(guild_dir)
        stt_log = str(stt_path) if save_stt_audio else "[SKIPPED]"
        print(f"[VOICE DEBUG SAVE] speaker={speaker} raw={raw_path} stt={stt_log}")
    except Exception as e:
        print(f"[VOICE DEBUG SAVE FAIL] speaker={speaker} err={e!r}")


def should_ignore_short_transcription(
    text: str,
    pcm_bytes: bytes,
    *,
    wake_detected: bool = False,
) -> bool:
    text_n = normalize_voice_text(text)
    if not text_n:
        return True

    if text_n in normalized_wake_words():
        return False

    if wake_detected and len(text_n) >= WAKE_SHORT_TEXT_KEEP_LEN:
        return False

    audio_sec = len(pcm_bytes) / (RATE * CHANNELS * 2)
    if audio_sec < MIN_AUDIO_SEC and len(text_n) < MIN_TRANSCRIBED_LEN:
        return True

    return False


def should_reply_to_voice(
    guild_id: int,
    text: str,
    *,
    wake_detected: bool = False,
    session_key: str | None = None,
    user_id: int | None = None,
) -> tuple[bool, str]:
    now = time.monotonic()
    text_n = normalize_voice_text(text)
    active_session = session_key is not None and is_session_active_for_user(session_key, user_id)
    state = session_state_snapshot(session_key)

    if guild_id in bot_speaking_guilds:
        return False, "bot_is_speaking"

    if now - last_bot_audio_end_at.get(guild_id, 0.0) < POST_TTS_IGNORE_SEC:
        return False, "post_tts_ignore"

    if not text_n:
        return False, "empty"

    if not wake_detected and not contains_wake_word(text_n) and not active_session:
        return False, "no_wake_word"

    if len(text_n) < MIN_TEXT_LEN and not wake_detected and not active_session:
        return False, "too_short"

    if state.get("last_speaker") == "assistant" and state.get("awaiting_user_reply"):
        active_session = True

    if now - last_voice_reply_at.get(guild_id, 0.0) < REPLY_COOLDOWN_SEC and not active_session:
        return False, "cooldown"

    if is_similar(text_n, last_voice_text.get(guild_id, "")):
        return False, "duplicate"

    last_voice_text[guild_id] = text_n
    last_voice_reply_at[guild_id] = now
    return True, "ok"


def ask_confidence_threshold_for_source(source: str) -> float:
    return ASK_CONFIDENCE_THRESHOLD_VOICE if source == "voice" else ASK_CONFIDENCE_THRESHOLD_TEXT


def should_skip_full_stt_after_wake_probe(*, wake_detected: bool, wake_probe: str, duration_sec: float) -> bool:
    if wake_detected:
        return False

    probe = clean_text(wake_probe)
    if not probe and duration_sec <= VOICE_NO_WAKE_MAX_CONTINUE_SEC:
        return True
    if looks_like_brief_filler_text(probe) and duration_sec <= VOICE_NO_WAKE_MAX_CONTINUE_SEC:
        return True
    if looks_like_repetitive_noise_text(probe):
        return True
    return False


def should_require_confirm_exact_for_wake(debug_meta: dict | None) -> bool:
    if not debug_meta:
        return False
    reasons = [str(reason) for reason in (debug_meta.get("reasons") or [])]
    if any(
        marker in reason
        for reason in reasons
        for marker in ("opus_fail", "plc", "fec", "front_burst_detected", "heavy_trim_ms", "burst_trim_ms")
    ):
        return True
    if debug_meta.get("front_burst_detected"):
        return True
    if int(debug_meta.get("opus_fail") or 0) > 0:
        return True
    if int(debug_meta.get("plc_packets") or 0) > 0:
        return True
    if int(debug_meta.get("fec_packets") or 0) > 0:
        return True
    if float(debug_meta.get("trim_ms") or 0.0) >= 220.0:
        return True
    if float(debug_meta.get("burst_trim_ms") or 0.0) >= 140.0:
        return True
    return False


def is_transport_corrupted_audio(debug_meta: dict | None) -> bool:
    if not debug_meta:
        return False
    reasons = [str(reason) for reason in (debug_meta.get("reasons") or [])]
    required_markers = ("opus_fail", "plc", "fec", "front_burst_detected", "heavy_trim_ms", "burst_trim_ms")
    reason_hits = {marker: any(marker in reason for reason in reasons) for marker in required_markers}
    return (
        (reason_hits["opus_fail"] or int(debug_meta.get("opus_fail") or 0) >= 4)
        and (reason_hits["plc"] or int(debug_meta.get("plc_packets") or 0) >= 2)
        and (reason_hits["fec"] or int(debug_meta.get("fec_packets") or 0) >= 2)
        and (reason_hits["front_burst_detected"] or bool(debug_meta.get("front_burst_detected")))
        and (reason_hits["heavy_trim_ms"] or float(debug_meta.get("trim_ms") or 0.0) >= 220.0)
        and (reason_hits["burst_trim_ms"] or float(debug_meta.get("burst_trim_ms") or 0.0) >= 140.0)
    )


def is_tail_fragment_candidate(
    *,
    session_key: str | None,
    raw_seconds: float,
    voiced_ms: float,
    longest_voiced_ms: float,
    unstable: bool,
) -> bool:
    if not session_key:
        return False
    accepted_at = session_last_turn_accepted_at.get(session_key, 0.0)
    if accepted_at <= 0.0:
        return False
    if (time.monotonic() - accepted_at) > TAIL_FRAGMENT_WINDOW_SEC:
        return False
    if raw_seconds > TAIL_FRAGMENT_MAX_RAW_SEC:
        return False
    if voiced_ms > TAIL_FRAGMENT_MAX_VOICED_MS:
        return False
    if longest_voiced_ms > TAIL_FRAGMENT_MAX_LONGEST_MS:
        return False
    return unstable or raw_seconds <= (TAIL_FRAGMENT_MAX_RAW_SEC * 0.6)


def apply_ask_gating(cognitive_state: dict | None = None, *, source: str = "text") -> dict:
    state = normalize_cognitive_state(cognitive_state or {})
    threshold = ask_confidence_threshold_for_source(source)

    if state.get("action") == "ask":
        question_for_user = clean_text(str(state.get("question_for_user", "")))
        confidence = float(state.get("confidence", 0.0) or 0.0)
        if not question_for_user or confidence < threshold:
            gated = dict(state)
            gated["action"] = "wait" if source == "voice" else "answer"
            reason = clean_text(str(gated.get("reason_brief", "")))
            gate_note = f"ask_gated_{source}_{confidence:.2f}_lt_{threshold:.2f}"
            gated["reason_brief"] = clean_text(f"{reason} {gate_note}") if reason else gate_note
            return gated

    return state


def policy_response_for_state(cognitive_state: dict | None = None, *, source: str = "text") -> str | None:
    state = apply_ask_gating(cognitive_state, source=source)
    action = state.get("action", "answer")

    if action == "ask":
        question = clean_text(str(state.get("question_for_user", "")))
        if question:
            return question
        return None

    if action == "wait":
        return "응, 계속 말해줘." if source == "voice" else "잠깐, 이어서 말해줘."

    return None


async def voice_ingress_worker() -> None:
    while True:
        item = await voice_ingress_queue.get()
        try:
            await _process_member_audio_impl(**item)
        except Exception as e:
            print(f"[VOICE WORKER] 실패: {e!r}")
        finally:
            voice_ingress_queue.task_done()


def ensure_voice_worker_started() -> None:
    global voice_worker_task
    if voice_worker_task is not None and not voice_worker_task.done():
        return
    voice_worker_task = asyncio.create_task(voice_ingress_worker())


def should_label_question_response(text: str, *, session_key: str | None = None) -> bool:
    visible = visible_text(text).strip()
    if not visible:
        return False
    if visible.startswith("[질문]"):
        return False
    if session_key is not None and session_state_snapshot(session_key).get("awaiting_user_reply"):
        return True
    return "?" in visible or "？" in visible


def format_display_text(text: str, *, session_key: str | None = None) -> str:
    visible = visible_text(text).strip()
    if not visible:
        return visible
    if should_label_question_response(visible, session_key=session_key):
        return f"[질문] {visible}"
    return visible


def read_cached_cognitive_state(
    guild_id: int | None,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> dict | None:
    if guild_id is None:
        return None
    return read_layered_cognitive_state(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )


async def refresh_cognitive_state_in_background(
    guild_id: int,
    user_text: str,
    *,
    reason: str,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> None:
    task_key = session_memory_key or runtime_session_key(guild_id=guild_id)
    started_at = time.monotonic()
    try:
        await update_cognitive_state(
            guild_id,
            user_text,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
        )
        log_turn_event(
            "cognitive_background_done",
            session_key=session_key,
            turn_id=current_turn_id(session_key),
            cognitive_background_ms=round((time.monotonic() - started_at) * 1000.0, 1),
            reason=reason,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[COGNITIVE] background refresh 실패 guild={guild_id} session={task_key!r} reason={reason} err={e!r}")
    finally:
        task = background_cognitive_tasks.get(task_key)
        if task is asyncio.current_task():
            background_cognitive_tasks.pop(task_key, None)


def schedule_cognitive_refresh(
    guild_id: int | None,
    user_text: str,
    *,
    reason: str,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> None:
    if guild_id is None:
        return
    task_key = session_memory_key or runtime_session_key(guild_id=guild_id)
    if task_key is None:
        return
    existing = background_cognitive_tasks.get(task_key)
    if existing is not None and not existing.done():
        existing.cancel()
    background_cognitive_tasks[task_key] = asyncio.create_task(
        refresh_cognitive_state_in_background(
            guild_id,
            user_text,
            reason=reason,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
        )
    )


def build_main_response_guidance(cognitive_state: dict | None = None, *, source: str = "text") -> str:
    state = apply_ask_gating(cognitive_state, source=source)
    threshold = ask_confidence_threshold_for_source(source)
    parts = [
        "주의: 생각 과정 말하지 말고, 최종 답변만 한국어로 한두 문장으로 짧게 말해.",
        OMNIVOICE_TAG_GUIDANCE,
        "텍스트만 봤을 때도 자연스럽게 읽혀야 하고, 태그를 빼도 문장이 성립해야 한다.",
        "sub handoff의 question_for_user는 사용자가 한 말이 아니다. 내부 메모이므로, 그 문장을 사용자의 질문으로 오해해서 답하지 마라.",
        f"ask 행동은 {source} 입력에서 confidence {threshold:.2f} 이상일 때만 허용한다.",
    ]

    action = state.get("action", "answer")
    if state.get("user_intent"):
        parts.append(f"사용자 의도 추정: {state['user_intent']}")

    if action == "ask":
        parts.append("지금은 바로 단정하기보다 사용자의 원래 발화에 이어서 짧은 확인 질문을 먼저 하는 편이 자연스럽다.")
        parts.append("질문형 태그가 필요하면 [question-en], [question-ah], [question-oh], [question-ei], [question-yi] 중 하나만 골라라.")
        parts.append("ask 모드에서는 question_for_user의 의미를 바꾸지 말고 거의 그대로 사용해라. 새 정보 추가, 해석 확장, 답변형 전환을 하지 마라.")
        if state.get("question_for_user"):
            parts.append(f"사용자에게 그대로 되물을 문장: {state['question_for_user']}")
    elif action == "wait":
        parts.append("지금은 길게 답하지 말고, 더 들을 여지를 두는 짧은 반응이 자연스럽다.")
        parts.append("wait 상황에서는 감정 태그를 거의 쓰지 말고, 정말 필요할 때만 [sigh] 같은 약한 태그 하나만 써라.")
    else:
        parts.append("지금은 답변을 주는 편이 자연스럽다.")
        parts.append("확인이나 수긍에는 [confirmation-en], 가벼운 웃음에는 [laughter], 놀람에는 [surprise-oh]나 [surprise-wa]를 필요할 때만 써라.")

    if state.get("main_prompt_hint"):
        parts.append(f"응답 추가 힌트: {state['main_prompt_hint']}")
    if state.get("confidence", 0.0) > 0:
        parts.append(f"내부 판단 신뢰도: {state['confidence']:.2f}")

    return " ".join(clean_text(part) for part in parts if clean_text(part))


def normalize_route_name(value: str) -> str:
    route = clean_text(value).lower()
    if route in {"subwait", "sub_wait", "wait", "fresh_sub", "fresh-sub"}:
        return "sub_wait"
    if route in {"subhint", "sub_hint", "hint", "cached_sub", "cached-sub"}:
        return "sub_hint"
    return "main_direct"


def should_force_voice_context_route(user_text: str) -> bool:
    text = clean_text(user_text)
    if not text:
        return False
    voice_context_markers = [
        "기억", "방금", "아까", "전에", "이전", "대화", "말했던", "했었던", "했던", "했었어",
        "무슨 얘기", "뭐였", "기억나", "기억해", "이어", "계속", "정리", "요약",
        "우리", "우리가", "하기로 했", "먹기로 했", "가기로 했", "약속", "정했",
    ]
    marker_hits = sum(1 for marker in voice_context_markers if marker in text)
    if marker_hits >= 1:
        return True
    return bool(re.search(r"(우리|우리가).*(했었|하기로 했|먹기로 했|가기로 했)", text))


def classify_llm_route_fallback(user_text: str, *, source: str = "text") -> str:
    text = clean_text(user_text)
    if source == "voice" and not should_force_voice_context_route(text):
        return "main_direct"

    short_text = len(text) <= 18 or len(text.split()) <= 4
    if short_text and source != "voice":
        return "main_direct"

    context_markers = [
        "아까", "방금", "전에", "이전", "기억", "문맥", "계속", "이어서",
        "요약", "정리", "판단", "비교", "설명", "의견", "생각", "왜", "어떻게",
    ]
    marker_hits = sum(1 for marker in context_markers if marker in text)

    if len(text) >= 60 or marker_hits >= 2:
        return "sub_wait"
    if len(text) >= 24 or marker_hits >= 1:
        return "sub_hint"
    return "main_direct"


async def prepare_llm_messages(
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
) -> tuple[list[dict], dict | None, str]:
    route_started_at = time.monotonic()
    route, route_meta = await classify_llm_route_async(user_text, guild_id=guild_id, source=source)
    if metrics is not None:
        metrics.setdefault("marks", {})["route_ready"] = (time.monotonic() - route_started_at) * 1000.0
        metrics.setdefault("meta", {}).update(
            {
                "source": source,
                "session_key": session_key,
                "guild_id": guild_id,
                "topic_id": session_topic_ids.get(session_key or "", "") if session_key else None,
            }
        )
    messages = list(get_conversation_history(session_key=session_key, guild_id=guild_id))
    cognitive_state: dict | None = None

    cognitive_started_at = time.monotonic()
    cached_cognitive_state = read_cached_cognitive_state(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    should_block_on_cognitive = guild_id is not None and (cached_cognitive_state is None or route == "sub_wait")
    if should_block_on_cognitive and guild_id is not None:
        cognitive_state = await update_cognitive_state(
            guild_id,
            user_text,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
        )
        if metrics is not None:
            metrics.setdefault("meta", {})["cognitive_mode"] = "blocking"
    else:
        cognitive_state = cached_cognitive_state
        if guild_id is not None:
            schedule_cognitive_refresh(
                guild_id,
                user_text,
                reason=f"{source}:{route}",
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
            )
        if metrics is not None:
            metrics.setdefault("meta", {})["cognitive_mode"] = "background"
    if metrics is not None and should_block_on_cognitive:
        metrics.setdefault("marks", {})["cognitive_hotpath_ms"] = (time.monotonic() - cognitive_started_at) * 1000.0

    if guild_id is not None:
        memory_started_at = time.monotonic()
        memory_context = build_memory_context(
            guild_id,
            user_text,
            cognitive_state=cognitive_state,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
        )
        if metrics is not None:
            memory_elapsed = (time.monotonic() - memory_started_at) * 1000.0
            metrics.setdefault("marks", {})["memory_ready"] = memory_elapsed
            metrics.setdefault("marks", {})["t_context_build"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
        if memory_context:
            base_system = messages[0]["content"] if messages and messages[0].get("role") == "system" else ""
            merged_system = clean_text(base_system + "\n\n" + memory_context)

            if messages and messages[0].get("role") == "system":
                messages[0] = {"role": "system", "content": merged_system}
            else:
                messages.insert(0, {"role": "system", "content": merged_system})

    if cognitive_state is not None:
        gated_state = apply_ask_gating(cognitive_state, source=source)
        if gated_state.get("action") != cognitive_state.get("action"):
            print(
                f"[ASK GATE] source={source} action={cognitive_state.get('action')} -> {gated_state.get('action')} confidence={float(cognitive_state.get('confidence', 0.0) or 0.0):.2f} threshold={ask_confidence_threshold_for_source(source):.2f}"
            )
            cognitive_state = gated_state

    if metrics is not None:
        metrics.setdefault("marks", {})["t_policy"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
    route_text = debug_text if debug_text is not None else user_text
    meta = metrics.get("meta") if metrics is not None else {}
    log_turn_event(
        "policy_ready",
        turn_id=(meta or {}).get("turn_id"),
        segment_id=(meta or {}).get("segment_id"),
        chunk_index=(meta or {}).get("chunk_index"),
        session_key=session_key,
        source=source,
        route=route,
        cognitive_action=(cognitive_state or {}).get("action") if cognitive_state else None,
        topic_id=session_topic_ids.get(session_key or "", "") if session_key else None,
    )
    if route_meta and route_meta.get("source") == "router":
        print(
            f"[LLM ROUTE] source={source} route={route} via=router confidence={float(route_meta.get('confidence', 0.0) or 0.0):.2f} reason={route_meta.get('reason_brief', '')!r} text={visible_text(route_text)!r}"
        )
    else:
        print(f"[LLM ROUTE] source={source} route={route} via=fallback text={visible_text(route_text)!r}")
    return messages, cognitive_state, route


def collect_memory_layers(
    guild_id: int,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> dict[str, dict]:
    layers: dict[str, dict] = {
        "guild": {
            "label": "공용 방 기억",
            "scope_type": "guild",
            "scope_key": None,
            "summary": compact_working_summary(read_text_file(memory_summary_path(guild_id))),
            "raw": read_jsonl(memory_raw_path(guild_id)),
            "vault_raw": read_vault_raw_rows(guild_id),
            "facts": read_fact_rows(guild_id),
            "questions": read_question_rows(guild_id),
        }
    }

    if room_key:
        layers["room"] = {
            "label": "방 기억",
            "scope_type": "room",
            "scope_key": room_key,
            "summary": compact_working_summary(read_text_file(memory_summary_path(guild_id, scope_type="room", scope_key=room_key))),
            "raw": read_jsonl(memory_raw_path(guild_id, scope_type="room", scope_key=room_key)),
            "vault_raw": read_vault_raw_rows(guild_id, scope_type="room", scope_key=room_key),
            "facts": read_fact_rows(guild_id, scope_type="room", scope_key=room_key),
            "questions": read_question_rows(guild_id, scope_type="room", scope_key=room_key),
        }

    if person_key:
        layers["person"] = {
            "label": "이 사람 기억",
            "scope_type": "person",
            "scope_key": person_key,
            "summary": compact_working_summary(read_text_file(memory_summary_path(guild_id, scope_type="person", scope_key=person_key))),
            "raw": read_jsonl(memory_raw_path(guild_id, scope_type="person", scope_key=person_key)),
            "vault_raw": read_vault_raw_rows(guild_id, scope_type="person", scope_key=person_key),
            "facts": read_fact_rows(guild_id, scope_type="person", scope_key=person_key),
            "questions": read_question_rows(guild_id, scope_type="person", scope_key=person_key),
        }

    if session_memory_key:
        layers["session"] = {
            "label": "현재 세션 기억",
            "scope_type": "session",
            "scope_key": session_memory_key,
            "summary": compact_working_summary(read_text_file(memory_summary_path(guild_id, scope_type="session", scope_key=session_memory_key))),
            "raw": read_jsonl(memory_raw_path(guild_id, scope_type="session", scope_key=session_memory_key)),
            "vault_raw": read_vault_raw_rows(guild_id, scope_type="session", scope_key=session_memory_key),
            "facts": read_fact_rows(guild_id, scope_type="session", scope_key=session_memory_key),
            "questions": read_question_rows(guild_id, scope_type="session", scope_key=session_memory_key),
        }

    return layers


def merge_recent_memory_rows(*row_groups: list[dict], limit: int) -> list[dict]:
    merged = merge_memory_rows(*row_groups)
    merged.sort(key=lambda row: int(row.get("saved_at", 0) or 0))
    return merged[-limit:]


def read_layered_cognitive_state(
    guild_id: int,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> dict | None:
    if session_memory_key:
        session_state = read_json_file(cognitive_state_path(guild_id, scope_type="session", scope_key=session_memory_key))
        if session_state:
            return normalize_cognitive_state(session_state)
    if person_key:
        person_state = read_json_file(cognitive_state_path(guild_id, scope_type="person", scope_key=person_key))
        if person_state:
            return normalize_cognitive_state(person_state)
    if room_key:
        room_state = read_json_file(cognitive_state_path(guild_id, scope_type="room", scope_key=room_key))
        if room_state:
            return normalize_cognitive_state(room_state)
    guild_state = read_json_file(cognitive_state_path(guild_id))
    return normalize_cognitive_state(guild_state) if guild_state else None


def format_memory_row_lines(rows: list[dict]) -> str:
    return "\n".join(
        f"- {clean_text(str(row.get('speaker', row.get('role', 'unknown')))) or 'unknown'}"
        f" ({clean_text(str(row.get('source', 'unknown'))) or 'unknown'}): {clean_text(str(row.get('text', '')))}"
        for row in rows
        if clean_text(str(row.get('text', '')))
    )


def build_memory_context(
    guild_id: int,
    user_text: str,
    cognitive_state: dict | None = None,
    *,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> str:
    layers = collect_memory_layers(
        guild_id,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
    )
    facts = select_relevant_memory_rows(
        user_text,
        merge_memory_rows(*(layer["facts"] for layer in layers.values())),
        MEMORY_RETRIEVE_LIMIT,
    )
    questions = select_relevant_memory_rows(
        user_text,
        merge_memory_rows(*(layer["questions"] for layer in layers.values())),
        4,
    )
    vault_raw_rows = select_relevant_memory_rows(
        user_text,
        merge_memory_rows(*(layer["vault_raw"] for layer in layers.values())),
        MEMORY_VAULT_RAW_RETRIEVE_LIMIT,
    )
    state = normalize_cognitive_state(
        cognitive_state or read_layered_cognitive_state(
            guild_id,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
        ) or {}
    )

    parts: list[str] = []
    summary_lines = [
        f"- {layer['label']}: {layer['summary']}"
        for layer in (layers.get("session"), layers.get("person"), layers.get("room"), layers.get("guild"))
        if layer and layer.get("summary")
    ]
    if summary_lines:
        parts.append("현재 작업 요약:\n" + "\n".join(summary_lines))

    session_rows = merge_recent_memory_rows(*(layer["raw"] for layer in (layers.get("session"),) if layer), limit=4)
    if session_rows:
        parts.append("현재 세션 최근 대화:\n" + format_memory_row_lines(session_rows))

    person_rows = merge_recent_memory_rows(*(layer["raw"] for layer in (layers.get("person"),) if layer), limit=4)
    if person_rows:
        parts.append("이 사람과의 최근 대화:\n" + format_memory_row_lines(person_rows))

    room_rows = merge_recent_memory_rows(
        *(layer["raw"] for layer in (layers.get("room"), layers.get("guild")) if layer),
        limit=MEMORY_RAW_CONTEXT_LIMIT,
    )
    if room_rows:
        parts.append("방 최근 대화:\n" + format_memory_row_lines(room_rows))

    if vault_raw_rows:
        parts.append("문서 보관함에서 꺼낸 관련 대화:\n" + format_memory_row_lines(vault_raw_rows))
    session_state = session_state_snapshot(session_key)
    if state.get("state_summary") or state.get("question_for_user") or state.get("main_prompt_hint") or session_state:
        action_label = {
            "answer": "답하기",
            "ask": "질문하기",
            "wait": "더 듣기",
        }.get(state.get("action", "answer"), "답하기")
        state_lines = [f"- 권장 행동: {action_label}"]
        if state.get("user_intent"):
            state_lines.append(f"- 사용자 의도: {state['user_intent']}")
        if state.get("state_summary"):
            state_lines.append(f"- 현재 판단: {state['state_summary']}")
        if state.get("question_for_user"):
            state_lines.append(f"- 사용자에게 되물을 내부 질문 초안: {state['question_for_user']}")
        if state.get("main_prompt_hint"):
            state_lines.append(f"- 응답 힌트: {state['main_prompt_hint']}")
        if state.get("retrieved_context_ids"):
            state_lines.append(f"- 참고 문맥 ID: {', '.join(state['retrieved_context_ids'][:4])}")
        if session_state.get("last_speaker"):
            state_lines.append(f"- 마지막 화자: {session_state['last_speaker']}")
        if session_state.get("awaiting_user_reply"):
            state_lines.append("- 사용자 후속 응답 대기 중")
        if session_state.get("topic_id"):
            state_lines.append(f"- 현재 topic_id: {session_state['topic_id']}")
        parts.append("현재 내부 상태(사용자 발화 아님):\n" + "\n".join(state_lines))
    if facts:
        parts.append(
            "장기 기억 후보:\n" + "\n".join(f"- {clean_text(str(row.get('text', '')))}" for row in facts)
        )
    if questions:
        parts.append(
            "열린 질문/가설:\n" + "\n".join(f"- {clean_text(str(row.get('text', '')))}" for row in questions)
        )

    if not parts:
        return ""

    return (
        "다음은 이전 대화에서 정리한 참고 메모다. 사실처럼 단정하지 말고, 현재 질문과 맞는 경우에만 자연스럽게 반영해라.\n\n"
        + "\n\n".join(parts)
    )


def extract_json_object(text: str) -> dict:
    text = text.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    return {}


async def ask_summary_llm(
    messages: list[dict],
    *,
    max_tokens: int = 500,
    timeout_seconds: float = 90,
) -> dict:
    session = await get_http_session()
    payload = {
        "model": SUMMARY_MODEL_NAME,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async with session.post(SUMMARY_LLM_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"요약 LLM 서버 오류: {resp.status} / {error_text[:300]}")

        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {}

        msg = choices[0].get("message", {})
        text = clean_text(msg.get("content", "") or msg.get("reasoning_content", ""))
        return extract_json_object(text)


async def ask_router_llm(
    messages: list[dict],
    *,
    max_tokens: int,
    timeout_seconds: float,
) -> dict:
    session = await get_http_session()
    payload = {
        "model": ROUTER_MODEL_NAME,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async with session.post(ROUTER_LLM_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"router LLM 서버 오류: {resp.status} / {error_text[:300]}")

        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            return {}

        msg = choices[0].get("message", {})
        text = clean_text(msg.get("content", "") or msg.get("reasoning_content", ""))
        return extract_json_object(text)


async def classify_llm_route_async(user_text: str, *, guild_id: int | None = None, source: str = "text") -> tuple[str, dict | None]:
    fallback_route = classify_llm_route_fallback(user_text, source=source)
    force_voice_context = source == "voice" and should_force_voice_context_route(user_text)
    if (source == "voice" and not force_voice_context) or not ROUTER_LLM_ENABLED:
        return fallback_route, {"selected": fallback_route, "source": "fallback"}

    summary = compact_working_summary(read_text_file(memory_summary_path(guild_id))) if guild_id is not None else ""
    state = normalize_cognitive_state(read_json_file(cognitive_state_path(guild_id))) if guild_id is not None else normalize_cognitive_state({})
    recent_raw = read_jsonl(memory_raw_path(guild_id))[-3:] if guild_id is not None else []
    recent_facts = read_fact_rows(guild_id)[-3:] if guild_id is not None else []

    messages = [
        {
            "role": "system",
            "content": (
                "너는 Evelyn의 router다. 반드시 JSON 객체 하나만 출력한다. "
                "형식은 {\"route\": \"main_direct|sub_hint|sub_wait\", \"confidence\": number, \"reason_brief\": string}. "
                "main_direct는 메인 LLM으로 바로 처리하면 되는 경우다. "
                "sub_hint는 저장된 state/summary를 힌트로만 참고하면 충분한 경우다. "
                "sub_wait는 fresh router 판단과 문맥 반영을 잠깐 기다리는 편이 좋은 경우다. "
                "짧고 직접적인 요청, 단순 대답, 즉답 가능한 질문은 main_direct를 우선한다. "
                "이전 대화 맥락, 기억, 비교, 판단, 요약, 이어지는 작업, 애매한 의도는 sub_hint 또는 sub_wait로 올린다. "
                "JSON 외 다른 텍스트는 절대 출력하지 마라."
            ),
        },
        {
            "role": "user",
            "content": (
                f"최근 요약:\n{summary or '(없음)'}\n\n"
                f"최근 cognitive_state:\n{json.dumps(state, ensure_ascii=False)}\n\n"
                f"최근 raw_transcript:\n{format_memory_rows_for_llm(recent_raw, max_items=3)}\n\n"
                f"최근 durable_facts:\n{format_memory_rows_for_llm(recent_facts, max_items=3)}\n\n"
                f"현재 입력:\n{compact_memory_text(user_text, max_chars=200)}"
            ),
        },
    ]

    try:
        result = await ask_router_llm(messages, max_tokens=ROUTER_ROUTE_MAX_TOKENS, timeout_seconds=ROUTER_ROUTE_TIMEOUT_SEC)
    except Exception as e:
        print(f"[ROUTER ROUTE] fallback reason={e!r}")
        return fallback_route, {"selected": fallback_route, "source": "fallback", "error": repr(e)}

    route = normalize_route_name(str(result.get("route", fallback_route)))
    confidence_raw = result.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except Exception:
        confidence = 0.0
    meta = {
        "selected": route,
        "source": "router",
        "confidence": max(0.0, min(1.0, confidence)),
        "reason_brief": clean_text(str(result.get("reason_brief", ""))),
        "fallback": fallback_route,
    }
    return route, meta


async def update_cognitive_state(
    guild_id: int,
    user_text: str,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> dict:
    started_at = time.monotonic()
    lock = cognitive_locks.setdefault(guild_id, asyncio.Lock())
    scope_type = "session" if session_memory_key else "person" if person_key else "room" if room_key else "guild"
    scope_key = session_memory_key if session_memory_key else person_key if person_key else room_key
    async with lock:
        layers = collect_memory_layers(
            guild_id,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
        )
        summary_lines = [
            f"- {layer['label']}: {layer['summary']}"
            for layer in (layers.get("session"), layers.get("person"), layers.get("room"), layers.get("guild"))
            if layer and layer.get("summary")
        ]
        current_summary = "\n".join(summary_lines)
        current_state = normalize_cognitive_state(
            read_layered_cognitive_state(
                guild_id,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
            ) or {}
        )
        recent_raw = merge_recent_memory_rows(
            *(layer["raw"] for layer in layers.values()),
            limit=MEMORY_COGNITIVE_RAW_LIMIT,
        )
        recent_facts = merge_recent_memory_rows(
            *(layer["facts"] for layer in layers.values()),
            limit=4,
        )
        recent_questions = merge_recent_memory_rows(
            *(layer["questions"] for layer in layers.values()),
            limit=4,
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "너는 실시간 대화 조율자다. 반드시 JSON 객체 하나만 출력한다. "
                    "형식은 {\"action\": \"answer|ask|wait\", \"confidence\": number, \"user_intent\": string, \"state_summary\": string, \"question_for_user\": string, \"main_prompt_hint\": string, \"reason_brief\": string, \"retrieved_context_ids\": string[]}. "
                    "answer는 지금 답하면 되는 경우다. ask는 사용자의 원래 발화에 이어서 짧게 되묻거나 확인 질문을 하는 편이 자연스러운 경우다. wait는 아직 단정하지 말고 더 듣거나 짧게 여지를 두는 편이 자연스러운 경우다. "
                    "question_for_user는 사용자가 한 말이 아니라, 메인 LLM이 사용자에게 되물을 내부 질문 초안이다. 절대로 사용자의 질문을 베껴 쓰거나 사용자가 이미 한 말처럼 적지 마라. "
                    "user_intent에는 사용자가 진짜로 하려는 말을 아주 짧게 적어라. state_summary에는 현재 상황을 한두 문장으로 적어라. main_prompt_hint에는 메인 LLM이 말할 때 지켜야 할 한 줄 힌트를 적어라. confidence는 0~1, reason_brief는 아주 짧게 써라. JSON 외 다른 텍스트는 절대 출력하지 마라."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"이전 cognitive_state:\n{json.dumps(current_state, ensure_ascii=False)}\n\n"
                    f"현재 layered_summary:\n{current_summary or '(없음)'}\n\n"
                    f"최근 raw_transcript:\n{format_memory_rows_for_llm(recent_raw, max_items=MEMORY_COGNITIVE_RAW_LIMIT)}\n\n"
                    f"최근 durable_facts:\n{format_memory_rows_for_llm(recent_facts, max_items=4)}\n\n"
                    f"최근 open_questions:\n{format_memory_rows_for_llm(recent_questions, max_items=4)}\n\n"
                    f"현재 사용자 입력:\n{compact_memory_text(user_text, max_chars=160)}"
                ),
            },
        ]

        try:
            result = await ask_router_llm(
                messages,
                max_tokens=COGNITIVE_MAX_TOKENS,
                timeout_seconds=COGNITIVE_TIMEOUT_SEC,
            )
        except Exception as e:
            if is_context_size_error(e):
                compact_messages = [
                    messages[0],
                    {
                        "role": "user",
                        "content": (
                            f"현재 layered_summary:\n{current_summary or '(없음)'}\n\n"
                            f"현재 사용자 입력:\n{compact_memory_text(user_text, max_chars=120)}"
                        ),
                    },
                ]
                try:
                    result = await ask_router_llm(
                        compact_messages,
                        max_tokens=COGNITIVE_MAX_TOKENS,
                        timeout_seconds=max(3.0, COGNITIVE_TIMEOUT_SEC - 2.0),
                    )
                except Exception as e2:
                    e = e2
                    print(f"[COGNITIVE] compact retry 실패: {e2}")
                else:
                    print("[COGNITIVE] compact retry 성공")
            if 'result' not in locals() or not isinstance(result, dict):
                print(f"[COGNITIVE] 상태 업데이트 실패 또는 timeout: {e}")
                elapsed_ms = (time.monotonic() - started_at) * 1000.0
                if should_log_voice_timing(elapsed_ms):
                    print(f"[COGNITIVE LATENCY] guild={guild_id} scope={scope_type}:{scope_key or 'default'} failed_after_ms={elapsed_ms:.0f}")
                fallback = current_state or {
                    "action": "answer",
                    "confidence": 0.5,
                    "user_intent": clean_text(user_text),
                    "state_summary": clean_text(user_text),
                    "question_for_user": "",
                    "main_prompt_hint": "짧고 자연스럽게 답해라.",
                    "reason_brief": "fallback",
                    "retrieved_context_ids": [],
                    "updated_at": int(time.time()),
                }
                write_json_file(cognitive_state_path(guild_id, scope_type=scope_type, scope_key=scope_key), fallback)
                return fallback

        state = normalize_cognitive_state(result)
        if not state.get("state_summary"):
            state["state_summary"] = current_state.get("state_summary", "") or clean_text(user_text)
        if not state.get("main_prompt_hint"):
            state["main_prompt_hint"] = "짧고 자연스럽게 답해라."
        state["updated_at"] = int(time.time())
        write_json_file(cognitive_state_path(guild_id, scope_type=scope_type, scope_key=scope_key), state)
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        if should_log_voice_timing(elapsed_ms):
            print(f"[COGNITIVE LATENCY] guild={guild_id} scope={scope_type}:{scope_key or 'default'} action={state.get('action')} ms={elapsed_ms:.0f}")

        if state.get("action") == "ask" and state.get("question_for_user"):
            print(
                f"[COGNITIVE ASK] guild={guild_id} scope={scope_type}:{scope_key or 'default'} question={state['question_for_user']!r} reason={state.get('reason_brief', '')!r} confidence={state.get('confidence', 0.0):.2f}"
            )

        return state


async def update_long_term_memory(
    guild_id: int,
    user_text: str,
    answer: str,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> None:
    started_at = time.monotonic()
    lock = memory_locks.setdefault(guild_id, asyncio.Lock())
    scope_note = session_memory_key or room_key or "guild"
    async with lock:
        layers = collect_memory_layers(
            guild_id,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
        )
        summary_lines = [
            f"- {layer['label']}: {layer['summary']}"
            for layer in (layers.get("session"), layers.get("person"), layers.get("room"), layers.get("guild"))
            if layer and layer.get("summary")
        ]
        current_summary = "\n".join(summary_lines)
        recent_raw = merge_recent_memory_rows(
            *(layer["raw"] for layer in layers.values()),
            limit=MEMORY_LONGTERM_RAW_LIMIT,
        )
        recent_facts = merge_recent_memory_rows(
            *(layer["facts"] for layer in layers.values()),
            limit=6,
        )
        recent_questions = merge_recent_memory_rows(
            *(layer["questions"] for layer in layers.values()),
            limit=4,
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "너는 대화 장기기억 관리자이자 상황 정리자다. 반드시 JSON 객체 하나만 출력한다. "
                    "형식은 {\"summary_update\": string, \"durable_facts\": [{\"type\": string, \"text\": string}], \"open_questions\": [{\"type\": string, \"text\": string}]}. "
                    "summary_update는 지금 상황을 짧고 자연스러운 한국어로 압축한 누적 요약이다. "
                    "durable_facts에는 오래 기억할 만한 선호, 설정, 프로젝트 결정, 반복되는 사실만 넣어라. "
                    "open_questions에는 아직 확정되지 않은 추정, 확인이 필요한 질문, 다음에 물어볼 만한 포인트만 넣어라. "
                    "잡담, 일회성 노이즈, 이미 해결된 내용은 넣지 마라. JSON 외 다른 텍스트는 절대 출력하지 마라."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"현재 layered_summary:\n{current_summary or '(없음)'}\n\n"
                    f"최근 raw_transcript:\n{format_memory_rows_for_llm(recent_raw, max_items=MEMORY_LONGTERM_RAW_LIMIT)}\n\n"
                    f"최근 durable_facts:\n{format_memory_rows_for_llm(recent_facts, max_items=6)}\n\n"
                    f"최근 open_questions:\n{format_memory_rows_for_llm(recent_questions, max_items=4)}\n\n"
                    f"새 대화:\n- user: {compact_memory_text(user_text, max_chars=120)}\n- assistant: {compact_memory_text(answer, max_chars=120)}"
                ),
            },
        ]

        try:
            result = await ask_summary_llm(messages)
        except Exception as e:
            if is_context_size_error(e):
                compact_messages = [
                    messages[0],
                    {
                        "role": "user",
                        "content": (
                            f"현재 layered_summary:\n{current_summary or '(없음)'}\n\n"
                            f"새 대화:\n- user: {compact_memory_text(user_text, max_chars=100)}\n- assistant: {compact_memory_text(answer, max_chars=100)}"
                        ),
                    },
                ]
                try:
                    result = await ask_summary_llm(compact_messages, max_tokens=220, timeout_seconds=20)
                except Exception as e2:
                    e = e2
                    print(f"[MEMORY] compact retry 실패: {e2}")
                else:
                    print("[MEMORY] compact retry 성공")
            if 'result' not in locals() or not isinstance(result, dict):
                print(f"[MEMORY] 요약 업데이트 실패: {e}")
                elapsed_ms = (time.monotonic() - started_at) * 1000.0
                if should_log_voice_timing(elapsed_ms):
                    print(f"[MEMORY LATENCY] guild={guild_id} scope={scope_note} failed_after_ms={elapsed_ms:.0f}")
                return

        scope_targets: list[tuple[str, str | None]] = [("guild", None)]
        if room_key:
            scope_targets.append(("room", room_key))
        if person_key:
            scope_targets.append(("person", person_key))
        if session_memory_key:
            scope_targets.append(("session", session_memory_key))

        summary_update = compact_working_summary(str(result.get("summary_update", "")))
        if summary_update:
            for scope_type, scope_key in scope_targets:
                write_text_file(memory_summary_path(guild_id, scope_type=scope_type, scope_key=scope_key), summary_update)

        durable_facts = result.get("durable_facts", [])
        if isinstance(durable_facts, list):
            rows = [row for row in durable_facts if isinstance(row, dict)]
            for scope_type, scope_key in scope_targets:
                append_unique_memory_rows(
                    memory_facts_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                    rows,
                    MEMORY_FACT_LIMIT,
                    mirror_path=vault_facts_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                )

        open_questions = result.get("open_questions", [])
        if isinstance(open_questions, list):
            rows = [row for row in open_questions if isinstance(row, dict)]
            for scope_type, scope_key in scope_targets:
                append_unique_memory_rows(
                    memory_questions_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                    rows,
                    MEMORY_LOOP_LIMIT,
                    mirror_path=vault_questions_path(guild_id, scope_type=scope_type, scope_key=scope_key),
                )

        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        if should_log_voice_timing(elapsed_ms):
            print(f"[MEMORY LATENCY] guild={guild_id} scope={scope_note} ms={elapsed_ms:.0f}")


def schedule_memory_update(
    guild_id: int,
    user_text: str,
    answer: str,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "chat",
    user_speaker: str = "user",
    assistant_speaker: str = "Evelyn",
) -> None:
    rows = [
        {"role": "user", "speaker": user_speaker, "source": source, "text": user_text},
        {"role": "assistant", "speaker": assistant_speaker, "source": source, "text": answer},
    ]
    append_raw_transcript_rows(guild_id, rows)
    if room_key:
        append_raw_transcript_rows(guild_id, rows, scope_type="room", scope_key=room_key)
    if person_key:
        append_raw_transcript_rows(guild_id, rows, scope_type="person", scope_key=person_key)
    if session_memory_key:
        append_raw_transcript_rows(guild_id, rows, scope_type="session", scope_key=session_memory_key)
    asyncio.create_task(
        update_long_term_memory(
            guild_id,
            user_text,
            answer,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
        )
    )
    asyncio.create_task(
        update_cognitive_state(
            guild_id,
            user_text,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
        )
    )


def split_tts_sentences(buffer: str, *, force: bool = False) -> tuple[list[str], str]:
    working = buffer or ""
    chunks: list[str] = []

    while True:
        match = re.search(r"(.+?[.!?…。]+)(?:\s+|$)", working, flags=re.DOTALL)
        if not match:
            break

        sentence = clean_tts_text(match.group(1))
        if sentence:
            chunks.append(sentence)
        working = working[match.end():].lstrip()

    if not force:
        compact = clean_text(working)
        if len(compact) >= TTS_EARLY_CHUNK_LEN:
            cut = max(working.rfind(","), working.rfind("，"))
            if cut >= TTS_EARLY_CUT_MIN:
                sentence = clean_tts_text(working[:cut])
                if sentence:
                    chunks.append(sentence)
                working = working[cut + 1 :].lstrip()
        return chunks, working

    tail = clean_tts_text(working)
    if tail:
        chunks.append(tail)
    return chunks, ""


def sanitize_model_output(text: str) -> str:
    text = text or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<think>.*$", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = normalize_omnivoice_tags(text)
    text = clean_text(text)
    return text


def strip_markdown_noise(text: str) -> str:
    text = re.sub(r"[*_`#]+", "", text)
    text = re.sub(r"^[\-\*\d\.\)\s]+", "", text)
    return clean_text(text)


def looks_like_meta_line(text: str) -> bool:
    t = text.strip().lower()
    blocked_prefixes = (
        "thinking process",
        "analyze the request",
        "determine the",
        "draft",
        "option",
        "selecting",
        "refining",
        "final polish",
        "final output generation",
        "role:",
        "language:",
        "format:",
        "length:",
        "tone:",
        "content:",
        "input:",
        "wait",
        "let's",
        "let’s",
        "or:",
    )
    if any(t.startswith(p) for p in blocked_prefixes):
        return True
    if "**" in text:
        return True
    if len(re.findall(r"[A-Za-z]", text)) > len(re.findall(r"[가-힣]", text)) * 2:
        return True
    return False


def extract_answer_from_reasoning(reasoning: str, user_text: str) -> str:
    text = sanitize_model_output(reasoning)
    if not text:
        return ""

    text = text.replace("\r", "\n")
    candidates: list[str] = []

    quoted = re.findall(r"[\"“”'‘’]([^\"“”'‘’]{4,120})[\"“”'‘’]", text)
    for q in quoted:
        q = strip_markdown_noise(q)
        if not q or not re.search(r"[가-힣]", q):
            continue
        if looks_like_meta_line(q):
            continue
        if clean_text(q) == clean_text(user_text):
            continue
        candidates.append(q)

    explicit_patterns = [
        r"(?:최종\s*답변|답변|response|assistant)\s*[:：]\s*([^\n]{4,120})",
        r"(?:최종\s*출력|final\s*answer)\s*[:：]\s*([^\n]{4,120})",
    ]
    for pattern in explicit_patterns:
        for match in re.findall(pattern, text, flags=re.IGNORECASE):
            candidate = strip_markdown_noise(match)
            if not candidate or not re.search(r"[가-힣]", candidate):
                continue
            if looks_like_meta_line(candidate):
                continue
            if clean_text(candidate) == clean_text(user_text):
                continue
            candidates.append(candidate)

    seen = set()
    filtered: list[str] = []
    for c in candidates:
        c = clean_text(c).strip("\"'“”‘’")
        if not c or c in seen:
            continue
        seen.add(c)
        if len(c) < 6 or len(c) > 120:
            continue
        filtered.append(c)

    return filtered[-1] if filtered else ""


async def get_http_session() -> aiohttp.ClientSession:
    global http_session
    if http_session is None or http_session.closed:
        timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10)
        http_session = aiohttp.ClientSession(timeout=timeout)
    return http_session


def answer_promises_search(answer_text: str) -> bool:
    text = clean_text(strip_omnivoice_tags(answer_text)).lower()
    if not text:
        return False

    completed_markers = [
        "찾아봤",
        "검색해봤",
        "확인해봤",
        "알아봤",
        "찾아보니",
        "검색해보니",
        "결과는",
    ]
    if any(marker in text for marker in completed_markers):
        return False

    promise_markers = [
        "찾아볼게",
        "찾아볼께",
        "찾아보고",
        "찾아보고 올게",
        "검색해볼게",
        "검색해볼께",
        "확인해볼게",
        "알아볼게",
        "찾는 중",
        "찾아보고 있어",
        "자료 찾아볼게",
    ]
    return any(marker in text for marker in promise_markers)


def build_search_query(guild_id: int, user_text: str) -> str:
    text = clean_text(strip_omnivoice_tags(user_text))
    if len(text) >= 8:
        return text

    summary = compact_working_summary(read_text_file(memory_summary_path(guild_id)))
    if summary:
        return clean_text(f"{text} {summary}")
    return text


def decode_duckduckgo_url(url: str) -> str:
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return html.unescape(url)


def strip_html_tags(text: str) -> str:
    return clean_text(html.unescape(re.sub(r"<[^>]+>", " ", text or "")))


async def search_duckduckgo(query: str, *, limit: int = 5) -> list[dict]:
    session = await get_http_session()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"}
    results: list[dict] = []
    seen_urls: set[str] = set()

    try:
        async with session.get(
            "https://api.duckduckgo.com/",
            params={
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "0",
            },
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=12),
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                abstract = clean_text(str(data.get("AbstractText", "")))
                abstract_url = clean_text(str(data.get("AbstractURL", "")))
                abstract_source = clean_text(str(data.get("AbstractSource", "DuckDuckGo"))) or "DuckDuckGo"
                if abstract and abstract_url:
                    results.append({
                        "title": abstract_source,
                        "snippet": abstract,
                        "url": abstract_url,
                    })
                    seen_urls.add(abstract_url)

                def collect_topics(items):
                    for item in items or []:
                        if not isinstance(item, dict):
                            continue
                        if "Topics" in item:
                            collect_topics(item.get("Topics"))
                            continue
                        snippet = clean_text(str(item.get("Text", "")))
                        url = clean_text(str(item.get("FirstURL", "")))
                        if not snippet or not url or url in seen_urls:
                            continue
                        results.append({
                            "title": snippet.split(" - ", 1)[0][:80],
                            "snippet": snippet,
                            "url": url,
                        })
                        seen_urls.add(url)
                        if len(results) >= limit:
                            return

                collect_topics(data.get("RelatedTopics", []))
    except Exception as e:
        print(f"[SEARCH] instant API 실패: {e!r}")

    if len(results) >= limit:
        return results[:limit]

    try:
        async with session.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query, "kl": "kr-ko"},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return results[:limit]
            page = await resp.text()
    except Exception as e:
        print(f"[SEARCH] html search 실패: {e!r}")
        return results[:limit]

    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>(?P<rest>.*?)</div>',
        re.S,
    )
    for match in pattern.finditer(page):
        url = decode_duckduckgo_url(match.group("url"))
        if not url or url in seen_urls:
            continue
        title = strip_html_tags(match.group("title"))
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>|class="result__snippet"[^>]*>(.*?)</div>', match.group("rest"), re.S)
        snippet = strip_html_tags((snippet_match.group(1) or snippet_match.group(2)) if snippet_match else "")
        if not title and not snippet:
            continue
        results.append({"title": title or url, "snippet": snippet, "url": url})
        seen_urls.add(url)
        if len(results) >= limit:
            break

    return results[:limit]


async def answer_from_search_results(query: str, results: list[dict]) -> str:
    if not results:
        return "찾아봤는데 지금 바로 쓸 만한 결과를 못 찾았어. 검색어를 조금 더 구체적으로 말해주면 다시 찾아볼게."

    session = await get_http_session()
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": (
                    "너는 검색 결과를 짧게 정리하는 비서다. 검색은 이미 끝났다. "
                    "'찾아볼게', '찾는 중', '확인해볼게' 같은 표현은 절대 쓰지 마라. "
                    "찾은 내용만 한국어로 바로 말하고, 한두 문장 뒤에 필요하면 출처 1~2개를 괄호로 덧붙여라."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"사용자 질문:\n{query}\n\n"
                    + "검색 결과:\n"
                    + "\n".join(
                        f"- {clean_text(row.get('title', ''))} | {clean_text(row.get('snippet', ''))} | {clean_text(row.get('url', ''))}"
                        for row in results[:5]
                    )
                ),
            },
        ],
        "temperature": 0.1,
        "max_tokens": 220,
        "stream": False,
    }

    async with session.post(LLM_SERVER_URL, json=payload, timeout=aiohttp.ClientTimeout(total=45)) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"검색 정리 LLM 오류: {resp.status} / {error_text[:300]}")
        data = await resp.json()

    choices = data.get("choices", [])
    if not choices:
        first = results[0]
        return clean_text(f"찾아보니까 {first.get('snippet', '')} ({first.get('url', '')})")

    message = choices[0].get("message", {})
    answer = sanitize_model_output(message.get("content", ""))
    if answer:
        return answer

    first = results[0]
    return clean_text(f"찾아보니까 {first.get('snippet', '')} ({first.get('url', '')})")


async def deliver_proactive_followup(
    guild_id: int,
    query: str,
    answer: str,
    *,
    session_key: str | None,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    channel_id: int | None,
    source: str,
) -> None:
    plain_answer = strip_omnivoice_tags(answer) or answer
    guild = bot.get_guild(guild_id)
    target_channel_id = channel_id
    if target_channel_id is None and session_key is not None:
        target_channel_id = session_followup_targets.get(session_key, {}).get("channel_id")

    if target_channel_id is not None:
        channel = bot.get_channel(target_channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(target_channel_id)
            except Exception:
                channel = None
        if channel is not None and hasattr(channel, "send"):
            await channel.send(format_display_text(answer, session_key=session_key))

    vc = guild.voice_client if guild else None
    if vc is not None and vc.is_connected():
        try:
            await speak_answer(vc, answer, turn_id=current_turn_id(session_key), session_key=session_key)
        except Exception as e:
            print(f"[SEARCH] proactive TTS 실패: {e!r}")

    append_history(session_key, query, plain_answer, guild_id=guild_id)
    schedule_memory_update(
        guild_id,
        query,
        plain_answer,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        user_speaker="search_task",
        assistant_speaker="Evelyn",
    )


async def run_search_followup(
    guild_id: int,
    query: str,
    *,
    session_key: str | None,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    channel_id: int | None,
    source: str,
) -> None:
    try:
        results = await search_duckduckgo(query)
        answer = await answer_from_search_results(query, results)
        removed = resolve_open_question_rows(guild_id, query, answer)
        if room_key:
            removed += resolve_open_question_rows(guild_id, query, answer, scope_type="room", scope_key=room_key)
        if person_key:
            removed += resolve_open_question_rows(guild_id, query, answer, scope_type="person", scope_key=person_key)
        if session_memory_key:
            removed += resolve_open_question_rows(guild_id, query, answer, scope_type="session", scope_key=session_memory_key)
        if removed:
            print(f"[SEARCH] resolved_open_questions guild={guild_id} removed={removed}")
        completed_state = {
            "action": "answer",
            "confidence": 1.0,
            "user_intent": clean_text(query),
            "state_summary": "검색을 마쳤고 결과를 사용자에게 전달했다.",
            "question_for_user": "",
            "main_prompt_hint": "찾은 내용을 바로 전달해라.",
            "reason_brief": "search_completed",
            "retrieved_context_ids": [],
            "updated_at": int(time.time()),
        }
        write_json_file(cognitive_state_path(guild_id), completed_state)
        if room_key:
            write_json_file(cognitive_state_path(guild_id, scope_type="room", scope_key=room_key), completed_state)
        if person_key:
            write_json_file(cognitive_state_path(guild_id, scope_type="person", scope_key=person_key), completed_state)
        if session_memory_key:
            write_json_file(cognitive_state_path(guild_id, scope_type="session", scope_key=session_memory_key), completed_state)
        await deliver_proactive_followup(
            guild_id,
            query,
            answer,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            channel_id=channel_id,
            source=source,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[SEARCH] follow-up 실패 guild={guild_id} query={query!r} err={e!r}")
    finally:
        task_key = runtime_session_key(session_key=session_key, guild_id=guild_id)
        task = background_search_tasks.get(task_key) if task_key is not None else None
        if task is asyncio.current_task() and task_key is not None:
            background_search_tasks.pop(task_key, None)


def schedule_search_followup(
    guild_id: int,
    session_key: str | None,
    user_text: str,
    answer: str,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    channel_id: int | None,
    source: str,
) -> None:
    if not guild_id or not answer_promises_search(answer):
        return

    query = build_search_query(guild_id, user_text)
    if len(query) < 2:
        return

    task_key = runtime_session_key(session_key=session_key, guild_id=guild_id)
    if task_key is None:
        return

    if channel_id is not None:
        remember_session_followup_target(task_key, channel_id=channel_id)

    existing = background_search_tasks.get(task_key)
    if existing is not None and not existing.done():
        existing.cancel()

    print(f"[SEARCH] scheduled guild={guild_id} session={task_key!r} query={query!r} source={source}")
    background_search_tasks[task_key] = asyncio.create_task(
        run_search_followup(
            guild_id,
            query,
            session_key=task_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            channel_id=channel_id,
            source=source,
        )
    )


class OmniVoicePCMStream(discord.AudioSource):
    def __init__(
        self,
        *,
        on_first_frame: Callable[[], None] | None = None,
        on_first_packet_sent: Callable[[], None] | None = None,
    ):
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._buffer = bytearray()
        self._done = False
        self._closed = False
        self._rate_state = None
        self._input_remainder = b""
        self._first_frame_sent = False
        self._on_first_frame = on_first_frame
        self._on_first_packet_sent = on_first_packet_sent
        self.error: Exception | None = None

    def feed_pcm24_mono(self, chunk: bytes) -> None:
        if self._closed or not chunk:
            return

        pcm = self._input_remainder + chunk
        if len(pcm) % 2 == 1:
            self._input_remainder = pcm[-1:]
            pcm = pcm[:-1]
        else:
            self._input_remainder = b""

        if not pcm:
            return

        upsampled, self._rate_state = audioop.ratecv(
            pcm,
            2,
            OMNIVOICE_PCM_CHANNELS,
            OMNIVOICE_PCM_RATE,
            DISCORD_PCM_RATE,
            self._rate_state,
        )
        stereo = audioop.tostereo(upsampled, 2, 1, 1)
        if stereo:
            self._queue.put(stereo)

    def finish(self) -> None:
        self._done = True
        self._queue.put(None)

    def fail(self, err: Exception) -> None:
        self.error = err
        self.finish()

    def read(self) -> bytes:
        while len(self._buffer) < DISCORD_FRAME_BYTES:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._done:
                    break
                continue

            if item is None:
                self._done = True
                break

            self._buffer.extend(item)

        if len(self._buffer) >= DISCORD_FRAME_BYTES:
            chunk = bytes(self._buffer[:DISCORD_FRAME_BYTES])
            del self._buffer[:DISCORD_FRAME_BYTES]
            if not self._first_frame_sent and any(chunk):
                self._first_frame_sent = True
                if self._on_first_frame is not None:
                    self._on_first_frame()
                if self._on_first_packet_sent is not None:
                    self._on_first_packet_sent()
            return chunk

        if self._done and self._buffer:
            chunk = bytes(self._buffer)
            self._buffer.clear()
            padded = chunk + (b"\x00" * (DISCORD_FRAME_BYTES - len(chunk)))
            if not self._first_frame_sent and any(padded):
                self._first_frame_sent = True
                if self._on_first_frame is not None:
                    self._on_first_frame()
                if self._on_first_packet_sent is not None:
                    self._on_first_packet_sent()
            return padded

        return b""

    def cleanup(self) -> None:
        self._closed = True
        self._done = True
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass


async def set_tts_presence(is_warming_up: bool) -> None:
    if bot.user is None:
        return
    try:
        if is_warming_up:
            await bot.change_presence(activity=discord.Game(name="봇 준비중..."))
        else:
            await bot.change_presence(activity=None)
    except Exception as e:
        print("Presence 변경 실패:", repr(e))


async def warmup_tts_server() -> None:
    global tts_warmup_started

    tts_warmup_started = True

    session = await get_http_session()
    timeout = aiohttp.ClientTimeout(total=10)
    async with session.get(f"{OMNIVOICE_SERVER_URL}/health", timeout=timeout) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"OmniVoice health check 실패: {resp.status} / {text[:200]}")
        print("OmniVoice 서버 준비 확인 완료")

    payload = {
        "model": OMNIVOICE_MODEL,
        "input": "안녕",
        "voice": OMNIVOICE_VOICE if OMNIVOICE_VOICE else "auto",
        "response_format": "pcm",
        "stream": True,
    }
    if OMNIVOICE_LANGUAGE:
        payload["language"] = OMNIVOICE_LANGUAGE

    async with session.post(
        f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
        json=payload,
        timeout=aiohttp.ClientTimeout(total=20),
    ) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"OmniVoice warmup 실패: {resp.status} / {text[:200]}")
        async for chunk in resp.content.iter_chunked(4096):
            if chunk:
                print("OmniVoice TTS 워밍업 완료")
                break


def should_log_voice_timing(elapsed_ms: float) -> bool:
    return elapsed_ms >= VOICE_TIMING_LOG_THRESHOLD_MS


def log_voice_latency(metrics: dict | None, key: str, label: str) -> None:
    if not metrics or metrics.get(key):
        return

    started_at = metrics.get("started_at")
    if started_at is None:
        return

    elapsed_ms = (time.monotonic() - float(started_at)) * 1000.0
    metrics[key] = True
    metrics.setdefault("marks", {})[key] = elapsed_ms
    alias_map = {
        "llm_first_chunk_logged": ["t_main_first_token"],
        "tts_first_byte_logged": ["t_tts_first_byte", "t_tts_first_audio"],
        "tts_first_frame_logged": ["t_tts_first_frame"],
        "first_packet_sent_logged": ["t_playback_first_packet"],
    }
    aliases = alias_map.get(key, [])
    for alias in aliases:
        metrics.setdefault("marks", {})[alias] = elapsed_ms
    if should_log_voice_timing(elapsed_ms):
        print(f"[VOICE LATENCY] {label}: {elapsed_ms:.0f}ms")


def record_voice_mark(metrics: dict | None, key: str) -> float | None:
    if not metrics:
        return None
    started_at = metrics.get("started_at")
    if started_at is None:
        return None
    elapsed_ms = (time.monotonic() - float(started_at)) * 1000.0
    metrics.setdefault("marks", {})[key] = elapsed_ms
    return elapsed_ms


def log_voice_stage(metrics: dict | None, label: str, *, extra: str = "", key: str | None = None) -> None:
    if not metrics:
        return
    started_at = metrics.get("started_at")
    if started_at is None:
        return
    elapsed_ms = (time.monotonic() - float(started_at)) * 1000.0
    if key:
        metrics.setdefault("marks", {})[key] = elapsed_ms
    stage_alias = {
        "route_ready": "t_policy",
        "memory_ready": "t_context_build",
        "stt_done": "t_stt_done",
        "llm_done": "t_main_done",
    }
    if key and key in stage_alias:
        metrics.setdefault("marks", {})[stage_alias[key]] = elapsed_ms
    if not should_log_voice_timing(elapsed_ms):
        return
    suffix = f" | {extra}" if extra else ""
    print(f"[VOICE STAGE] {label}: {elapsed_ms:.0f}ms{suffix}")


def log_voice_bottleneck_summary(metrics: dict | None, *, label: str, extra: str = "") -> None:
    if not metrics:
        return
    started_at = metrics.get("started_at")
    if started_at is None:
        return

    total_ms = (time.monotonic() - float(started_at)) * 1000.0
    marks = metrics.get("marks") or {}

    def _fmt(name: str) -> str:
        value = marks.get(name)
        return f"{float(value):.0f}ms" if value is not None else "-"

    if should_log_voice_timing(total_ms):
        parts = [
            f"total={total_ms:.0f}ms",
            f"route={_fmt('route_ready')}",
            f"cognitive={_fmt('cognitive_hotpath_ms')}",
            f"memory={_fmt('memory_ready')}",
            f"wake_probe_ms={_fmt('wake_done')}",
            f"stt={_fmt('stt_done')}",
            f"llm_first={_fmt('llm_first_chunk_logged')}",
            f"llm_done={_fmt('llm_done')}",
            f"tts_req={_fmt('tts_request_logged')}",
            f"tts_headers={_fmt('tts_response_headers_logged')}",
            f"tts_first={_fmt('tts_first_byte_logged')}",
            f"tts_frame={_fmt('tts_first_frame_logged')}",
            f"playback={_fmt('first_packet_sent_logged')}",
        ]
        if extra:
            parts.append(extra)
        print(f"[VOICE BOTTLENECK] {label} | " + " | ".join(parts))

    meta = metrics.get("meta") or {}
    log_turn_event(
        "turn_summary",
        label=label,
        turn_id=meta.get("turn_id"),
        segment_id=meta.get("segment_id"),
        chunk_index=meta.get("chunk_index"),
        source=meta.get("source"),
        session_key=meta.get("session_key"),
        topic_id=meta.get("topic_id"),
        drop_reason=meta.get("drop_reason"),
        t_ingress=marks.get("t_ingress"),
        t_policy=marks.get("t_policy"),
        t_context_build=marks.get("t_context_build"),
        cognitive_hotpath_ms=marks.get("cognitive_hotpath_ms"),
        t_main_first_token=marks.get("t_main_first_token"),
        t_main_done=marks.get("t_main_done"),
        t_tts_first_audio=marks.get("t_tts_first_audio"),
        t_playback_first_packet=marks.get("t_playback_first_packet"),
        t_stt_done=marks.get("t_stt_done"),
        total_ms=round(total_ms, 1),
        extra=extra or None,
    )


async def create_omnivoice_source(
    text: str,
    *,
    on_task_started: Callable[[], None] | None = None,
    on_request_start: Callable[[], None] | None = None,
    on_response_headers: Callable[[], None] | None = None,
    on_first_byte: Callable[[], None] | None = None,
    on_first_frame: Callable[[], None] | None = None,
    on_first_packet_sent: Callable[[], None] | None = None,
    turn_id: str | None = None,
    chunk_index: int | None = None,
    session_key: str | None = None,
) -> OmniVoicePCMStream:
    text = clean_tts_text(text)
    if not text:
        raise ValueError("TTS 텍스트가 비어 있습니다.")

    source = OmniVoicePCMStream(
        on_first_frame=on_first_frame,
        on_first_packet_sent=on_first_packet_sent,
    )

    async def producer() -> None:
        session = await get_http_session()
        timeout = aiohttp.ClientTimeout(total=OMNIVOICE_TIMEOUT_SEC)
        first_pcm_logged = False

        if on_task_started is not None:
            on_task_started()
        log_turn_event(
            "playback_task_started",
            turn_id=turn_id,
            chunk_index=chunk_index,
            session_key=session_key,
        )

        async def stream_with_voice(voice_name: str) -> tuple[bool, str]:
            payload = {
                "model": OMNIVOICE_MODEL,
                "input": text,
                "voice": voice_name,
                "response_format": "pcm",
                "stream": OMNIVOICE_STREAM,
            }
            if OMNIVOICE_LANGUAGE:
                payload["language"] = OMNIVOICE_LANGUAGE

            nonlocal first_pcm_logged

            if on_request_start is not None:
                on_request_start()
            log_turn_event(
                "tts_request_started",
                turn_id=turn_id,
                chunk_index=chunk_index,
                session_key=session_key,
                voice=voice_name,
            )
            async with session.post(
                f"{OMNIVOICE_SERVER_URL}/v1/audio/speech",
                json=payload,
                timeout=timeout,
            ) as resp:
                if on_response_headers is not None:
                    on_response_headers()
                if resp.status != 200:
                    return False, await resp.text()

                async for chunk in resp.content.iter_chunked(8192):
                    if chunk:
                        if on_first_byte is not None and not first_pcm_logged:
                            on_first_byte()
                        if not first_pcm_logged:
                            first_pcm_logged = True
                            log_turn_event(
                                "tts_first_pcm_received",
                                turn_id=turn_id,
                                chunk_index=chunk_index,
                                session_key=session_key,
                                bytes=len(chunk),
                            )
                        source.feed_pcm24_mono(chunk)
                return True, ""

        try:
            ok, error_text = await stream_with_voice(OMNIVOICE_VOICE)
            if not ok:
                if OMNIVOICE_VOICE.startswith("clone:"):
                    print(f"[TTS FALLBACK] clone voice 실패 -> auto 사용 | voice={OMNIVOICE_VOICE} err={error_text[:200]}")
                    ok, error_text = await stream_with_voice("auto")
                if not ok:
                    raise RuntimeError(f"OmniVoice 서버 오류: {error_text[:300]}")
        except Exception as e:
            source.fail(e)
            return

        source.finish()

    asyncio.create_task(producer())
    return source


# =========================================================
# STT
# =========================================================
def resolve_stt_torch_dtype() -> torch.dtype:
    value = str(STT_COMPUTE_TYPE).strip().lower()
    mapping = {
        "float16": torch.float16,
        "fp16": torch.float16,
        "half": torch.float16,
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float32": torch.float32,
        "fp32": torch.float32,
        "float": torch.float32,
    }
    return mapping.get(value, torch.float32)


def normalize_stt_language(language: str | None = None) -> str | None:
    value = str(language if language is not None else STT_LANGUAGE).strip()
    if not value:
        return None

    lowered = value.lower()
    aliases = {
        "korean": "ko",
        "kor": "ko",
        "kr": "ko",
        "ko-kr": "ko",
        "ko_kr": "ko",
        "english": "en",
    }
    return aliases.get(lowered, lowered)


def get_stt_model() -> tuple[str, Any, Any]:
    global stt_processor, stt_model, stt_backend

    if stt_backend == "hf_whisper" and stt_processor is not None and stt_model is not None:
        return stt_backend, stt_processor, stt_model

    device = "cuda" if torch.cuda.is_available() else "cpu"
    token = os.getenv("HF_TOKEN")
    torch_dtype = resolve_stt_torch_dtype()

    print(f"STT 로드 시작: model={STT_MODEL_NAME}, device={device}, dtype={torch_dtype}")

    stt_backend = "hf_whisper"
    stt_processor = AutoProcessor.from_pretrained(
        STT_MODEL_NAME,
        token=token,
    )
    hf_load_kwargs = {
        "token": token,
        "torch_dtype": torch_dtype,
        "low_cpu_mem_usage": True,
    }
    stt_model = WhisperForConditionalGeneration.from_pretrained(
        STT_MODEL_NAME,
        **hf_load_kwargs,
    ).to(device)
    stt_model.eval()
    print("STT 로드 완료 (Whisper/transformers)")
    return stt_backend, stt_processor, stt_model


def transcribe_audio16k_sync(audio16k: np.ndarray, max_new_tokens: int = 256, *, sampling_rate: int = TARGET_RATE, stage: str = "full") -> str:
    if audio16k.size == 0:
        return ""

    effective_rate = max(1, int(sampling_rate))
    print(f"[STT INPUT][{stage}] sampling_rate={effective_rate} samples={audio16k.size} sec={audio16k.size / float(effective_rate):.2f}")
    backend, processor, model = get_stt_model()
    stt_audio = np.asarray(audio16k, dtype=np.float32)

    whisper_audio = stt_audio
    if effective_rate != TARGET_RATE:
        whisper_audio = resample_audio_float(whisper_audio, effective_rate, TARGET_RATE)
        print(f"[STT RESAMPLE][{stage}] {effective_rate} -> {TARGET_RATE} samples={whisper_audio.size}")

    if stage == "full":
        beam_size = max(1, STT_WHISPER_FULL_BEAM_SIZE)
    elif stage == "full-rescore":
        beam_size = max(1, STT_WHISPER_FULL_RESCORE_BEAM_SIZE)
    elif stage == "wake-confirm":
        beam_size = max(1, STT_WHISPER_WAKE_CONFIRM_BEAM_SIZE)
    else:
        beam_size = max(1, STT_WHISPER_WAKE_BEAM_SIZE)

    language = normalize_stt_language() if STT_FORCE_LANGUAGE else None
    inputs = processor(
        whisper_audio,
        sampling_rate=TARGET_RATE,
        return_tensors="pt",
        return_attention_mask=True,
    )
    moved = {}
    for k, v in inputs.items():
        if torch.is_tensor(v):
            if torch.is_floating_point(v):
                moved[k] = v.to(model.device, dtype=model.dtype)
            else:
                moved[k] = v.to(model.device)
        else:
            moved[k] = v

    generate_kwargs = {
        "max_new_tokens": max_new_tokens,
        "num_beams": beam_size,
        "do_sample": False,
    }
    if "attention_mask" in moved and moved["attention_mask"] is not None:
        generate_kwargs["attention_mask"] = moved["attention_mask"]

    if STT_FORCE_LANGUAGE and hasattr(processor, "get_decoder_prompt_ids"):
        decoder_prompt_ids = processor.get_decoder_prompt_ids(
            language=language,
            task="transcribe",
        )
        if decoder_prompt_ids:
            generate_kwargs["forced_decoder_ids"] = decoder_prompt_ids

    with torch.inference_mode():
        outputs = model.generate(**moved, **generate_kwargs)

    text = processor.batch_decode(outputs, skip_special_tokens=True)[0]
    return clean_text(text)


def score_stt_candidate(text: str, *, wake_probe: str = "") -> float:
    text = clean_text(text)
    if not text:
        return -100.0

    normalized = normalize_voice_text(text)
    if not normalized:
        return -80.0

    compact = normalized.replace(" ", "")
    token_count = len([t for t in normalized.split() if t])
    hangul_alnum_count = len(re.findall(r"[가-힣A-Za-z0-9]", text))
    unique_chars = len(set(compact))
    unique_ratio = unique_chars / max(1, len(compact))

    score = 0.0
    score += min(24.0, len(compact) * 0.75)
    score += min(6.0, token_count * 0.6)
    score += min(8.0, hangul_alnum_count * 0.15)
    score += unique_ratio * 2.0

    if contains_wake_word(text):
        score += 10.0
    if wake_probe:
        wake_probe_n = normalize_voice_text(wake_probe)
        if wake_probe_n and wake_probe_n in normalized:
            score += 2.0

    if looks_like_brief_filler_text(text):
        score -= 14.0
    if looks_like_repetitive_noise_text(text):
        score -= 16.0
    if re.search(r"(.)\1{3,}", compact):
        score -= 6.0
    if len(compact) <= 2:
        score -= 4.0

    return score


def choose_full_stt_candidate(primary_text: str, rescore_text: str, *, wake_probe: str = "") -> tuple[str, dict]:
    primary = clean_text(primary_text)
    rescore = clean_text(rescore_text)
    primary_score = score_stt_candidate(primary, wake_probe=wake_probe)
    rescore_score = score_stt_candidate(rescore, wake_probe=wake_probe)

    choice = "primary"
    chosen_text = primary

    if not primary and rescore:
        choice = "rescore"
        chosen_text = rescore
    elif rescore and not is_similar(primary, rescore):
        if rescore_score >= primary_score + 1.5:
            choice = "rescore"
            chosen_text = rescore
        elif contains_wake_word(rescore) and not contains_wake_word(primary) and rescore_score >= primary_score:
            choice = "rescore"
            chosen_text = rescore
        elif len(normalize_voice_text(rescore).replace(" ", "")) >= len(normalize_voice_text(primary).replace(" ", "")) + 3 and rescore_score > primary_score:
            choice = "rescore"
            chosen_text = rescore

    return chosen_text, {
        "enabled": True,
        "primary_text": primary,
        "primary_score": round(primary_score, 3),
        "rescore_text": rescore,
        "rescore_score": round(rescore_score, 3),
        "selected": choice,
    }


def detect_wake_word_sync(audio: np.ndarray, *, sampling_rate: int = TARGET_RATE) -> dict[str, str | bool | None]:
    wake_audio = slice_audio_window(audio, WAKE_AUDIO_SEC, sampling_rate=sampling_rate)
    wake_text = transcribe_audio16k_sync(
        wake_audio,
        max_new_tokens=WAKE_MAX_TOKENS,
        sampling_rate=sampling_rate,
        stage="wake",
    )

    probe_text = strip_leading_voice_fillers(wake_text)
    probe_alias = extract_leading_wake_alias(probe_text)
    probe_fuzzy_alias = fuzzy_leading_wake_alias(probe_text) if probe_alias is None else None
    confirm_text = ""

    if probe_alias is None and looks_like_gibberish_probe(probe_text):
        return {
            "wake_detected": False,
            "wake_probe_text": wake_text,
            "wake_confirm_text": "",
            "wake_match_mode": "rejected",
            "wake_alias": None,
            "wake_reject_reason": "gibberish_probe",
        }

    if probe_alias is None and probe_fuzzy_alias is None:
        return {
            "wake_detected": False,
            "wake_probe_text": wake_text,
            "wake_confirm_text": "",
            "wake_match_mode": "rejected",
            "wake_alias": None,
            "wake_reject_reason": "probe_miss",
        }

    confirm_audio = slice_audio_window(audio, WAKE_CONFIRM_AUDIO_SEC, sampling_rate=sampling_rate)
    confirm_text = transcribe_audio16k_sync(
        confirm_audio,
        max_new_tokens=WAKE_CONFIRM_MAX_TOKENS,
        sampling_rate=sampling_rate,
        stage="wake-confirm",
    )
    confirm_probe = strip_leading_voice_fillers(confirm_text)
    confirm_alias = extract_leading_wake_alias(confirm_probe)

    if probe_alias is not None and confirm_alias == probe_alias:
        return {
            "wake_detected": True,
            "wake_probe_text": wake_text,
            "wake_confirm_text": confirm_text,
            "wake_match_mode": "exact",
            "wake_alias": probe_alias,
            "wake_reject_reason": None,
        }

    confirm_fuzzy_alias = fuzzy_leading_wake_alias(confirm_probe) if confirm_alias is None else None
    if probe_alias is None and probe_fuzzy_alias is not None and confirm_fuzzy_alias == probe_fuzzy_alias:
        return {
            "wake_detected": True,
            "wake_probe_text": wake_text,
            "wake_confirm_text": confirm_text,
            "wake_match_mode": "fuzzy",
            "wake_alias": probe_fuzzy_alias,
            "wake_reject_reason": None,
        }

    return {
        "wake_detected": False,
        "wake_probe_text": wake_text,
        "wake_confirm_text": confirm_text,
        "wake_match_mode": "rejected",
        "wake_alias": probe_alias or probe_fuzzy_alias,
        "wake_reject_reason": "confirm_miss",
    }


# =========================================================
# 디스코드 음성
# =========================================================
async def connect_evelyn_voice_client(target_channel: discord.VoiceChannel) -> EvelynVoiceClient:
    last_error: Exception | None = None

    for attempt in range(1, VOICE_CONNECT_RETRIES + 1):
        try:
            print(
                f"[VOICE CONNECT] attempt={attempt}/{VOICE_CONNECT_RETRIES} channel={target_channel.name} timeout={VOICE_CONNECT_TIMEOUT}"
            )
            vc = await target_channel.connect(
                cls=EvelynVoiceClient,
                timeout=VOICE_CONNECT_TIMEOUT,
                reconnect=False,
            )
            if not isinstance(vc, EvelynVoiceClient):
                raise RuntimeError(f"unexpected voice client type: {type(vc)!r}")
            return vc
        except Exception as e:
            last_error = e
            print(
                f"[VOICE CONNECT FAIL] attempt={attempt}/{VOICE_CONNECT_RETRIES} channel={target_channel.name} err={e!r}"
            )

            stale_vc = target_channel.guild.voice_client
            if stale_vc is not None:
                try:
                    await stale_vc.disconnect(force=True)
                except Exception:
                    pass

            try:
                await target_channel.guild.change_voice_state(channel=None, self_deaf=False, self_mute=False)
            except Exception:
                pass

            if attempt < VOICE_CONNECT_RETRIES:
                await asyncio.sleep(VOICE_CONNECT_RETRY_DELAY_SEC)

    assert last_error is not None
    raise last_error


async def ensure_listening_voice_client(guild: discord.Guild, target_channel: discord.VoiceChannel) -> Optional[EvelynVoiceClient]:
    vc = guild.voice_client

    if vc is not None and not isinstance(vc, EvelynVoiceClient):
        await vc.disconnect(force=True)
        vc = None

    if vc is None:
        vc = await connect_evelyn_voice_client(target_channel)
    elif vc.channel != target_channel:
        await vc.move_to(target_channel)

    if isinstance(vc, EvelynVoiceClient):
        vc.on_user_audio = process_member_audio
        if not vc.is_listening():
            vc.listen()
        return vc

    return None


async def ensure_voice_client(message: discord.Message) -> Optional[EvelynVoiceClient]:
    if not message.guild:
        return None

    voice_state = getattr(message.author, "voice", None)
    if not voice_state or not voice_state.channel:
        return None

    vc = await ensure_listening_voice_client(message.guild, voice_state.channel)
    return vc


async def wait_until_not_playing(vc: discord.VoiceClient) -> None:
    while vc.is_playing() or vc.is_paused():
        await asyncio.sleep(0.05)


async def play_audio_source(
    vc: discord.VoiceClient,
    source: discord.AudioSource,
    *,
    on_play_start: Callable[[], None] | None = None,
) -> None:
    await wait_until_not_playing(vc)

    done = asyncio.Event()
    playback_error: list[Exception | None] = [None]

    def after_play(err):
        if err:
            playback_error[0] = err
        bot.loop.call_soon_threadsafe(done.set)

    if on_play_start is not None:
        on_play_start()
    vc.play(source, after=after_play)
    await done.wait()

    if playback_error[0] is not None:
        raise playback_error[0]

    if isinstance(source, OmniVoicePCMStream) and source.error is not None:
        raise source.error


async def speak_answer(
    vc: discord.VoiceClient,
    answer: str,
    *,
    turn_id: str | None = None,
    session_key: str | None = None,
) -> None:
    guild_id = getattr(getattr(vc, "guild", None), "id", None)

    async with tts_lock:
        source = await create_omnivoice_source(
            answer,
            turn_id=turn_id,
            chunk_index=1,
            session_key=session_key,
            on_first_packet_sent=lambda: log_turn_event(
                "first_packet_sent",
                turn_id=turn_id,
                chunk_index=1,
                session_key=session_key,
            ),
        )
        try:
            if guild_id is not None:
                bot_speaking_guilds.add(guild_id)
            await play_audio_source(vc, source)
        finally:
            if guild_id is not None:
                bot_speaking_guilds.discard(guild_id)
                last_bot_audio_end_at[guild_id] = time.monotonic()


async def stream_tts_sentences(
    vc: discord.VoiceClient,
    sentence_queue: "asyncio.Queue[str | None]",
    *,
    metrics: dict | None = None,
    turn_id: str | None = None,
    session_key: str | None = None,
) -> None:
    guild_id = getattr(getattr(vc, "guild", None), "id", None)
    did_speak = False

    async with tts_lock:
        try:
            chunk_index = 0
            while True:
                sentence = await sentence_queue.get()
                if sentence is None:
                    break

                sentence = clean_tts_text(sentence)
                if not sentence:
                    continue

                chunk_index += 1

                if guild_id is not None and not did_speak:
                    bot_speaking_guilds.add(guild_id)

                did_speak = True
                source = await create_omnivoice_source(
                    sentence,
                    turn_id=turn_id,
                    chunk_index=chunk_index,
                    session_key=session_key,
                    on_request_start=lambda: log_voice_latency(metrics, "tts_request_logged", "TTS 요청 시작 시간"),
                    on_response_headers=lambda: log_voice_latency(metrics, "tts_response_headers_logged", "TTS 응답 헤더 도착 시간"),
                    on_first_byte=lambda: log_voice_latency(metrics, "tts_first_byte_logged", "TTS 첫 바이트 도착 시간"),
                    on_first_frame=lambda: log_voice_latency(metrics, "tts_first_frame_logged", "TTS 첫 프레임 공급 시간"),
                    on_first_packet_sent=lambda ci=chunk_index: (
                        log_voice_latency(metrics, "first_packet_sent_logged", "첫 패킷 송신 시간"),
                        log_turn_event(
                            "first_packet_sent",
                            turn_id=turn_id,
                            chunk_index=ci,
                            session_key=session_key,
                        )
                    ),
                )
                await play_audio_source(vc, source)
        finally:
            if guild_id is not None:
                bot_speaking_guilds.discard(guild_id)
                if did_speak:
                    last_bot_audio_end_at[guild_id] = time.monotonic()


# =========================================================
# LLM
# =========================================================
def fallback_answer_for(user_text: str) -> str:
    user_text = clean_text(user_text)
    if not user_text:
        return "응, 듣고 있어."
    return "응, 잠깐만."


async def ask_llm_once(
    user_text: str,
    guild_id: int | None = None,
    *,
    session_key: str | None = None,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
    source: str = "text",
    debug_text: str | None = None,
) -> str:
    messages, cognitive_state, _route = await prepare_llm_messages(
        user_text,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        debug_text=debug_text,
    )

    policy_response = policy_response_for_state(cognitive_state, source=source)
    gated_state = apply_ask_gating(cognitive_state, source=source) if cognitive_state is not None else None
    if policy_response:
        if session_key is not None and gated_state is not None:
            update_session_state(
                session_key,
                speaker="assistant",
                awaiting_user_reply=gated_state.get("action") in {"ask", "wait"},
                answer_text=policy_response,
                user_text=user_text,
            )
        return policy_response

    guided_user_text = user_text
    if gated_state and gated_state.get("action") == "ask" and gated_state.get("question_for_user"):
        guided_user_text = clean_text(str(gated_state.get("question_for_user", "")))
    final_user_text = f"{guided_user_text}\n\n{build_main_response_guidance(cognitive_state, source=source)}"

    payload = {
        "model": MODEL_NAME,
        "messages": messages + [{"role": "user", "content": final_user_text}],
        "temperature": 0.1,
        "max_tokens": VOICE_LLM_MAX_TOKENS,
        "stream": False,
    }

    timeout = aiohttp.ClientTimeout(total=120)
    session = await get_http_session()

    async with session.post(LLM_SERVER_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"LLM 서버 오류: {resp.status} / {error_text[:300]}")

        data = await resp.json()
        choices = data.get("choices", [])
        if not choices:
            return fallback_answer_for(user_text)

        choice = choices[0]
        msg = choice.get("message", {})
        answer = sanitize_model_output(msg.get("content", ""))
        reasoning = msg.get("reasoning_content", "")
        finish_reason = choice.get("finish_reason", "")

        if answer:
            return answer

        extracted = extract_answer_from_reasoning(reasoning, user_text)
        if extracted:
            return extracted

        print(f"LLM 응답 본문이 비어 있어서 fallback 사용, finish_reason={finish_reason}")
        return fallback_answer_for(user_text)


def _extract_text_payload(value) -> str:
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    return ""


def extract_stream_delta_text(data: dict) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""

    choice = choices[0]
    delta = choice.get("delta") or {}
    content = _extract_text_payload(delta.get("content"))
    if content:
        return content

    message = choice.get("message") or {}
    content = _extract_text_payload(message.get("content"))
    if content:
        return content

    text = choice.get("text")
    return text if isinstance(text, str) else ""


def extract_stream_reasoning_text(data: dict) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""

    choice = choices[0]
    delta = choice.get("delta") or {}
    reasoning = _extract_text_payload(delta.get("reasoning_content"))
    if reasoning:
        return reasoning

    reasoning = _extract_text_payload(delta.get("reasoning"))
    if reasoning:
        return reasoning

    message = choice.get("message") or {}
    reasoning = _extract_text_payload(message.get("reasoning_content"))
    if reasoning:
        return reasoning

    reasoning = _extract_text_payload(message.get("reasoning"))
    return reasoning


async def ask_llm_streaming(
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
) -> str:
    messages, cognitive_state, _route = await prepare_llm_messages(
        user_text,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        source=source,
        debug_text=debug_text,
        metrics=metrics,
    )

    policy_response = policy_response_for_state(cognitive_state, source=source)
    gated_state = apply_ask_gating(cognitive_state, source=source) if cognitive_state is not None else None
    if policy_response:
        if session_key is not None and gated_state is not None:
            update_session_state(
                session_key,
                speaker="assistant",
                awaiting_user_reply=gated_state.get("action") in {"ask", "wait"},
                answer_text=policy_response,
                user_text=user_text,
            )
        if on_first_chunk is not None:
            on_first_chunk()
        if on_sentence is not None:
            await on_sentence(policy_response)
        if metrics is not None:
            metrics.setdefault("marks", {})["policy_short_circuit"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            metrics.setdefault("marks", {})["llm_done"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            metrics.setdefault("marks", {})["t_main_done"] = metrics.setdefault("marks", {}).get("llm_done")
        return policy_response

    guided_user_text = user_text
    if gated_state and gated_state.get("action") == "ask" and gated_state.get("question_for_user"):
        guided_user_text = clean_text(str(gated_state.get("question_for_user", "")))
    final_user_text = f"{guided_user_text}\n\n{build_main_response_guidance(cognitive_state, source=source)}"

    payload = {
        "model": MODEL_NAME,
        "messages": messages + [{"role": "user", "content": final_user_text}],
        "temperature": 0.1,
        "max_tokens": VOICE_LLM_MAX_TOKENS,
        "stream": True,
    }

    timeout = aiohttp.ClientTimeout(total=120)
    session = await get_http_session()
    raw_parts: list[str] = []
    reasoning_parts: list[str] = []
    sentence_buffer = ""
    emitted_any = False
    llm_started_at = time.monotonic()

    async with session.post(LLM_SERVER_URL, json=payload, timeout=timeout) as resp:
        if resp.status != 200:
            error_text = await resp.text()
            raise RuntimeError(f"LLM 서버 오류: {resp.status} / {error_text[:300]}")

        content_type = resp.headers.get("Content-Type", "")
        if "application/json" in content_type.lower():
            data = await resp.json()
            choices = data.get("choices", [])
            answer = ""
            if choices:
                msg = choices[0].get("message", {})
                answer = sanitize_model_output(msg.get("content", ""))
            if not answer:
                print("[LLM STREAM] json 응답 본문 비어 있음, non-stream 재시도")
                answer = await ask_llm_once(
                    user_text,
                    guild_id=guild_id,
                    session_key=session_key,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    source=source,
                    debug_text=debug_text,
                )
            if on_first_chunk is not None:
                on_first_chunk()
            if on_sentence is not None:
                await on_sentence(answer)
            if metrics is not None:
                metrics.setdefault("marks", {})["llm_done"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0
            return answer

        async for raw_line in resp.content:
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if not line or line.startswith(":"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line:
                continue
            if line == "[DONE]":
                break

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            delta_text = extract_stream_delta_text(data)
            reasoning_text = extract_stream_reasoning_text(data)
            if reasoning_text:
                reasoning_parts.append(reasoning_text)

            if not delta_text:
                continue

            if on_first_chunk is not None:
                on_first_chunk()
                on_first_chunk = None

            raw_parts.append(delta_text)
            sentence_buffer += delta_text

            if on_sentence is not None:
                ready_chunks, sentence_buffer = split_tts_sentences(sentence_buffer)
                for chunk in ready_chunks:
                    emitted_any = True
                    await on_sentence(chunk)

    answer = sanitize_model_output("".join(raw_parts))
    if not answer:
        print(
            f"[LLM STREAM] stream 본문 비어 있음, non-stream 재시도 | raw_len={len(''.join(raw_parts))} reasoning_len={len(''.join(reasoning_parts))} emitted_any={emitted_any}"
        )
        answer = await ask_llm_once(
            user_text,
            guild_id=guild_id,
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
        )

    if on_sentence is not None:
        ready_chunks, sentence_buffer = split_tts_sentences(sentence_buffer, force=True)
        if not ready_chunks and answer and not emitted_any:
            ready_chunks = [answer]
        for chunk in ready_chunks:
            emitted_any = True
            await on_sentence(chunk)

    if metrics is not None:
        metrics.setdefault("marks", {})["llm_http_ms"] = (time.monotonic() - llm_started_at) * 1000.0
        metrics.setdefault("marks", {})["llm_done"] = (time.monotonic() - float(metrics.get("started_at", time.monotonic()))) * 1000.0

    return answer


async def ask_llm_and_speak_streaming(
    vc: discord.VoiceClient,
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
) -> str:
    if metrics is None:
        metrics = new_turn_metrics(
            source=source,
            session_key=session_key,
            guild_id=guild_id,
            topic_id=session_topic_ids.get(session_key),
            turn_id=current_turn_id(session_key),
            segment_id=0,
        )
    else:
        metrics.setdefault("started_at", time.monotonic())
        metrics.setdefault("marks", {})
        metrics.setdefault("meta", {})
    metrics.setdefault("llm_first_chunk_logged", False)
    metrics.setdefault("tts_request_logged", False)
    metrics.setdefault("tts_response_headers_logged", False)
    metrics.setdefault("tts_first_byte_logged", False)
    metrics.setdefault("tts_first_frame_logged", False)
    metrics.setdefault("first_packet_sent_logged", False)
    log_voice_stage(metrics, "LLM/TTS 파이프라인 시작", extra=f"source={source}")
    sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
    playback_task = asyncio.create_task(
        stream_tts_sentences(
            vc,
            sentence_queue,
            metrics=metrics,
            turn_id=metrics.get("meta", {}).get("turn_id"),
            session_key=session_key,
        )
    )
    llm_error: Exception | None = None
    answer = ""

    async def enqueue_sentence(sentence: str) -> None:
        sentence = clean_tts_text(sentence)
        if sentence:
            await sentence_queue.put(sentence)

    try:
        answer = await ask_llm_streaming(
            user_text,
            guild_id=guild_id,
            on_sentence=enqueue_sentence,
            on_first_chunk=lambda: log_voice_latency(metrics, "llm_first_chunk_logged", "LLM 첫 chunk 시간"),
            session_key=session_key,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source=source,
            debug_text=debug_text,
            metrics=metrics,
        )
        log_voice_stage(metrics, "LLM 완료", extra=f"chars={len(answer)}", key="llm_done")
        answer = clean_text(answer)
        if answer and on_final_answer is not None:
            await on_final_answer(answer)
    except Exception as e:
        llm_error = e
        answer = ""
    finally:
        await sentence_queue.put(None)

    try:
        await playback_task
    except Exception:
        if llm_error is None:
            raise

    if llm_error is not None:
        raise llm_error

    log_voice_bottleneck_summary(metrics, label="voice_reply", extra=f"source={source} chars={len(answer)}")
    return answer


async def stream_text_reply(
    channel: discord.abc.Messageable,
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
) -> tuple[str, discord.Message | None]:
    metrics = new_turn_metrics(
        source=source,
        session_key=session_key,
        guild_id=guild_id,
        topic_id=session_topic_ids.get(session_key),
        turn_id=turn_id,
        segment_id=0,
    )
    streamed_message: discord.Message | None = await channel.send("…")
    rendered_text = "…"
    streamed_parts: list[str] = []

    async def on_sentence(sentence: str) -> None:
        nonlocal streamed_message, rendered_text
        if not sentence:
            return
        streamed_parts.append(sentence)
        candidate = format_display_text("".join(streamed_parts), session_key=session_key).strip()
        if not candidate or candidate == rendered_text:
            return
        if streamed_message is None:
            streamed_message = await channel.send(candidate)
        else:
            await streamed_message.edit(content=candidate)
        rendered_text = candidate

    answer = await ask_llm_streaming(
        user_text,
        guild_id=guild_id,
        session_key=session_key,
        room_key=room_key,
        person_key=person_key,
        session_memory_key=session_memory_key,
        on_sentence=on_sentence,
        on_first_chunk=lambda: log_voice_latency(metrics, "llm_first_chunk_logged", "LLM 첫 chunk 시간"),
        source=source,
        debug_text=debug_text,
        metrics=metrics,
    )
    final_text = format_display_text(answer, session_key=session_key).strip()
    if streamed_message is None:
        streamed_message = await channel.send(final_text or fallback_answer_for(user_text))
    elif final_text and final_text != rendered_text:
        await streamed_message.edit(content=final_text)
    log_voice_bottleneck_summary(metrics, label="text_reply", extra=f"chars={len(final_text)}")
    return answer, streamed_message


# =========================================================
# 음성 입력 처리
# =========================================================
async def process_member_audio(member: discord.Member | None, pcm_bytes: bytes, debug_meta: dict | None = None) -> None:
    if member is None or member.bot:
        return

    guild = getattr(member, "guild", None)
    if guild is None:
        return

    ensure_voice_worker_started()

    guild_id = guild.id
    voice_channel_id = getattr(getattr(guild.voice_client, "channel", None), "id", None)
    session_key = make_voice_session_key(guild_id, voice_channel_id, member.id)
    room_key = make_room_memory_key("voice", voice_channel_id)
    person_key = make_person_memory_key(member.id)
    session_memory_key = make_session_memory_key(session_key, member.id)
    segment_id = next_segment_id(session_key)
    turn_id = new_turn_id()
    voice_ingress_queue.put_nowait(
        {
            "member": member,
            "pcm_bytes": pcm_bytes,
            "debug_meta": debug_meta,
            "session_key": session_key,
            "room_key": room_key,
            "person_key": person_key,
            "session_memory_key": session_memory_key,
            "turn_id": turn_id,
            "segment_id": segment_id,
        }
    )


async def _process_member_audio_impl(
    member: discord.Member | None,
    pcm_bytes: bytes,
    debug_meta: dict | None = None,
    *,
    session_key: str,
    room_key: str | None,
    person_key: str | None,
    session_memory_key: str | None,
    turn_id: str,
    segment_id: int,
) -> None:
    if member is None:
        return

    if member.bot:
        return

    guild = getattr(member, "guild", None)
    if guild is None:
        return

    guild_id = guild.id
    topic_id = session_topic_ids.get(session_key) or build_topic_id(member.display_name or str(member.id))
    metrics = new_turn_metrics(
        source="voice",
        session_key=session_key,
        guild_id=guild_id,
        user_id=member.id,
        topic_id=topic_id,
        turn_id=turn_id,
        segment_id=segment_id,
    )
    log_voice_stage(metrics, "voice_worker_turn 시작", extra=f"speaker={member.display_name} pcm_bytes={len(pcm_bytes)}")

    if STT_USE_RAW_48K:
        audio16k = downmix_int16_stereo_to_mono_float(pcm_bytes)
        audio_for_wake = apply_light_denoise(audio16k, sampling_rate=RATE)
        stt_sampling_rate = RATE
        wake_sampling_rate = RATE
    else:
        audio16k = prepare_stt_audio(pcm_bytes)
        audio_for_wake = audio16k
        stt_sampling_rate = TARGET_RATE
        wake_sampling_rate = TARGET_RATE
    speaker_name = member.display_name or str(member.id)
    if audio16k.size == 0:
        register_drop_reason(metrics, "empty_audio", session_key=session_key)
        log_voice_stage(metrics, "오디오 비어있음")
        log_voice_bottleneck_summary(metrics, label="voice_reply", extra="drop=empty_audio")
        return

    raw_seconds = len(pcm_bytes) / float(RATE * CHANNELS * 2)
    if raw_seconds <= VOICE_MIN_TOTAL_SEC:
        print(f"[FULL STT SKIP] reason=too_short_total speaker={member.display_name} raw_seconds={raw_seconds:.3f}")
        print(f"[SHORT AUDIO IGNORE] speaker={member.display_name} raw_seconds={raw_seconds:.3f}")
        save_voice_debug_audio(
            guild_id,
            speaker_name,
            pcm_bytes,
            audio16k,
            final_text="[SHORT AUDIO IGNORE]",
            debug_meta=debug_meta,
            save_stt_audio=False,
        )
        register_drop_reason(metrics, "too_short_total", session_key=session_key, raw_seconds=round(raw_seconds, 3))
        log_voice_stage(metrics, "전체 길이 너무 짧아서 제외", extra=f"raw_seconds={raw_seconds:.3f}")
        log_voice_bottleneck_summary(metrics, label="voice_reply", extra="drop=too_short_total")
        return

    unstable_audio = bool(debug_meta and debug_meta.get("unstable"))
    transport_corrupted = is_transport_corrupted_audio(debug_meta)
    if unstable_audio:
        reasons = ",".join(str(r) for r in debug_meta.get("reasons", []))
        print(f"[UNSTABLE AUDIO] speaker={member.display_name} reasons={reasons}")
        log_voice_stage(metrics, "불안정 음성 감지", extra=f"reasons={reasons}")

    duration_sec = len(audio16k) / float(max(1, stt_sampling_rate))
    waveform_stats = compute_waveform_activity_stats(audio16k, sampling_rate=stt_sampling_rate)
    voiced_ms = float(waveform_stats.get("voiced_ms") or 0.0)
    longest_voiced_ms = float(waveform_stats.get("longest_voiced_ms") or 0.0)
    if transport_corrupted and raw_seconds <= max(1.4, TAIL_FRAGMENT_MAX_RAW_SEC + 0.5):
        register_drop_reason(
            metrics,
            "transport_corrupted",
            session_key=session_key,
            raw_seconds=round(raw_seconds, 3),
            voiced_ms=round(voiced_ms, 1),
            longest_voiced_ms=round(longest_voiced_ms, 1),
        )
        log_voice_stage(
            metrics,
            "transport corrupted 조기 종료",
            extra=f"raw_seconds={raw_seconds:.3f} voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f}",
        )
        log_voice_bottleneck_summary(metrics, label="voice_reply", extra="drop=transport_corrupted")
        return
    if is_tail_fragment_candidate(
        session_key=session_key,
        raw_seconds=raw_seconds,
        voiced_ms=voiced_ms,
        longest_voiced_ms=longest_voiced_ms,
        unstable=unstable_audio,
    ):
        register_drop_reason(
            metrics,
            "tail_fragment_drop",
            session_key=session_key,
            raw_seconds=round(raw_seconds, 3),
            voiced_ms=round(voiced_ms, 1),
            longest_voiced_ms=round(longest_voiced_ms, 1),
        )
        log_voice_stage(
            metrics,
            "tail fragment 조기 종료",
            extra=f"raw_seconds={raw_seconds:.3f} voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f}",
        )
        log_voice_bottleneck_summary(metrics, label="voice_reply", extra="drop=tail_fragment_drop")
        return
    if VAD_ENABLED and is_probably_silent(audio16k, sampling_rate=stt_sampling_rate):
        duration_sec = len(audio16k) / float(max(1, stt_sampling_rate))
        peak = float(np.max(np.abs(audio16k))) if audio16k.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(audio16k)))) if audio16k.size else 0.0
        voiced_ms = float(waveform_stats.get("voiced_ms") or 0.0)
        longest_voiced_ms = float(waveform_stats.get("longest_voiced_ms") or 0.0)
        body_rms = float(waveform_stats.get("body_rms") or 0.0)
        body_peak = float(waveform_stats.get("body_peak") or 0.0)
        waveform_override = (not transport_corrupted) and voiced_ms >= VOICE_WAVEFORM_MIN_VOICED_MS and (
            longest_voiced_ms >= VOICE_WAVEFORM_MIN_RUN_MS
            or body_rms >= VOICE_WAVEFORM_BODY_RMS_MIN
            or body_peak >= VOICE_WAVEFORM_BODY_PEAK_MIN
        )
        if waveform_override:
            print(
                f"[VAD OVERRIDE] speaker={member.display_name} sec={duration_sec:.2f} voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f} body_rms={body_rms:.4f} body_peak={body_peak:.4f}"
            )
            log_voice_stage(
                metrics,
                "VAD override",
                extra=f"voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f} body_rms={body_rms:.4f} body_peak={body_peak:.4f}",
            )
        else:
            print(f"[FULL STT SKIP] reason=vad_ignore speaker={member.display_name} sampling_rate={stt_sampling_rate} sec={duration_sec:.2f} peak={peak:.4f} rms={rms:.4f} voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f} body_rms={body_rms:.4f}")
            print(f"[VAD IGNORE] speaker={member.display_name} sampling_rate={stt_sampling_rate} sec={duration_sec:.2f} peak={peak:.4f} rms={rms:.4f}")
            save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, final_text="[VAD IGNORE]", debug_meta=debug_meta)
            register_drop_reason(metrics, "vad_ignore", session_key=session_key, voiced_ms=round(voiced_ms, 1))
            log_voice_stage(metrics, "VAD 무시 처리", extra=f"sampling_rate={stt_sampling_rate} sec={duration_sec:.2f} peak={peak:.4f} rms={rms:.4f} voiced_ms={voiced_ms:.0f} longest_ms={longest_voiced_ms:.0f} body_rms={body_rms:.4f}")
            log_voice_bottleneck_summary(metrics, label="voice_reply", extra="drop=vad_ignore")
            return

    log_voice_stage(metrics, "웨이크 프로브 시작", extra=f"samples={audio_for_wake.size} sampling_rate={wake_sampling_rate}")
    try:
        wake_result = await asyncio.to_thread(detect_wake_word_sync, audio_for_wake, sampling_rate=wake_sampling_rate)
    except Exception as e:
        print(f"❌ [WAKE STT] {e}")
        register_drop_reason(metrics, "wake_probe_error", session_key=session_key, error=repr(e))
        log_voice_stage(metrics, "웨이크 프로브 실패", extra=repr(e))
        log_voice_bottleneck_summary(metrics, label="voice_reply", extra="drop=wake_probe_error")
        return

    wake_probe = apply_stt_post_corrections(str(wake_result.get("wake_probe_text") or ""), wake_detected=False)
    wake_confirm = apply_stt_post_corrections(str(wake_result.get("wake_confirm_text") or ""), wake_detected=False)
    wake_detected = bool(wake_result.get("wake_detected"))
    wake_match_mode = str(wake_result.get("wake_match_mode") or ("exact" if wake_detected else "rejected"))
    wake_alias = clean_text(str(wake_result.get("wake_alias") or "")) or None
    wake_reject_reason = clean_text(str(wake_result.get("wake_reject_reason") or "")) or None

    strict_confirm_required = should_require_confirm_exact_for_wake(debug_meta)
    if strict_confirm_required and wake_match_mode != "exact":
        wake_detected = False
        wake_match_mode = "rejected"
        wake_reject_reason = "unstable_audio"

    log_voice_stage(
        metrics,
        "웨이크 프로브 완료",
        extra=(
            f"wake_detected={wake_detected} wake_match_mode={wake_match_mode} wake_alias={wake_alias!r} "
            f"wake_probe_text={wake_probe!r} wake_confirm_text={wake_confirm!r} wake_reject_reason={wake_reject_reason!r}"
        ),
        key="wake_done",
    )

    active_session = is_session_active_for_user(session_key, member.id)
    hard_drop_reasons = {"unstable_audio", "gibberish_probe", "probe_miss", "confirm_miss", "wake_probe_low_signal", "full_text_veto", "transport_corrupted"}
    if not wake_detected:
        reject_reason = wake_reject_reason or "confirm_miss"
        if (not active_session) or (reject_reason in hard_drop_reasons):
            register_drop_reason(
                metrics,
                reject_reason,
                session_key=session_key,
                wake_probe_text=wake_probe,
                wake_confirm_text=wake_confirm,
                wake_match_mode=wake_match_mode,
                wake_alias=wake_alias,
            )
            log_voice_stage(metrics, "웨이크 거부", extra=f"wake_reject_reason={reject_reason} wake_match_mode={wake_match_mode} active_session={active_session}")
            log_voice_bottleneck_summary(metrics, label="voice_reply", extra=f"drop={reject_reason}")
            return
    env_noise_candidate = is_likely_environment_noise(audio_for_wake, sampling_rate=wake_sampling_rate)
    filler_candidate = looks_like_brief_filler_text(wake_probe)
    repetitive_noise_candidate = looks_like_repetitive_noise_text(wake_probe)

    if env_noise_candidate:
        band_ratio, flatness, rms = compute_voice_band_metrics(audio_for_wake, sampling_rate=wake_sampling_rate)
        if not wake_detected and not active_session and raw_seconds <= VOICE_NO_WAKE_MAX_CONTINUE_SEC:
            print(f"[FULL STT SKIP] reason=env_ignore speaker={member.display_name} probe={wake_probe!r}")
            print(
                f"[ENV IGNORE] speaker={member.display_name} band_ratio={band_ratio:.3f} flatness={flatness:.3f} rms={rms:.4f} probe={wake_probe!r}"
            )
            save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text="[ENV IGNORE]", debug_meta=debug_meta)
            register_drop_reason(metrics, "env_ignore", session_key=session_key, wake_probe_text=wake_probe)
            log_voice_stage(metrics, "환경음 후보 조기 종료", extra=f"wake_probe_text={wake_probe!r}")
            log_voice_bottleneck_summary(metrics, label="voice_reply", extra="drop=env_ignore")
            return
        print(f"[FULL STT CONTINUE] reason=env_ignore speaker={member.display_name} probe={wake_probe!r}")
        print(
            f"[ENV IGNORE] speaker={member.display_name} band_ratio={band_ratio:.3f} flatness={flatness:.3f} rms={rms:.4f} probe={wake_probe!r}"
        )
        log_voice_stage(metrics, "환경음 후보지만 본문 STT 진행", extra=f"wake_probe_text={wake_probe!r}")

    if filler_candidate:
        if not wake_detected and not active_session and raw_seconds <= VOICE_NO_WAKE_MAX_CONTINUE_SEC:
            print(f"[FULL STT SKIP] reason=filler_ignore speaker={member.display_name} probe={wake_probe!r}")
            print(f"[FILLER IGNORE] speaker={member.display_name} probe={wake_probe!r}")
            save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text="[FILLER IGNORE]", debug_meta=debug_meta)
            register_drop_reason(metrics, "filler_ignore", session_key=session_key, wake_probe_text=wake_probe)
            log_voice_stage(metrics, "짧은 필러 후보 조기 종료", extra=f"wake_probe_text={wake_probe!r}")
            log_voice_bottleneck_summary(metrics, label="voice_reply", extra="drop=filler_ignore")
            return
        print(f"[FULL STT CONTINUE] reason=filler_ignore speaker={member.display_name} probe={wake_probe!r}")
        print(f"[FILLER IGNORE] speaker={member.display_name} probe={wake_probe!r}")
        log_voice_stage(metrics, "짧은 필러 후보지만 본문 STT 진행", extra=f"wake_probe_text={wake_probe!r}")

    if repetitive_noise_candidate:
        if not wake_detected and not active_session:
            print(f"[FULL STT SKIP] reason=noise_text_ignore speaker={member.display_name} probe={wake_probe!r}")
            print(f"[NOISE TEXT IGNORE] speaker={member.display_name} probe={wake_probe!r}")
            save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text="[NOISE TEXT IGNORE]", debug_meta=debug_meta)
            register_drop_reason(metrics, "noise_text_ignore", session_key=session_key, wake_probe_text=wake_probe)
            log_voice_stage(metrics, "반복 소음 후보 조기 종료", extra=f"wake_probe_text={wake_probe!r}")
            log_voice_bottleneck_summary(metrics, label="voice_reply", extra="drop=noise_text_ignore")
            return
        print(f"[FULL STT CONTINUE] reason=noise_text_ignore speaker={member.display_name} probe={wake_probe!r}")
        print(f"[NOISE TEXT IGNORE] speaker={member.display_name} probe={wake_probe!r}")
        log_voice_stage(metrics, "반복 소음 후보지만 본문 STT 진행", extra=f"wake_probe_text={wake_probe!r}")

    if not wake_detected:
        print(f"[FULL STT CONTINUE] reason=wake_ignore speaker={member.display_name} probe={wake_probe!r}")
        if wake_probe:
            print(f"[WAKE IGNORE] {member.display_name}: {wake_probe!r}")
        if should_skip_full_stt_after_wake_probe(wake_detected=wake_detected, wake_probe=wake_probe, duration_sec=duration_sec):
            print(f"[FULL STT SKIP] reason=wake_probe_low_signal speaker={member.display_name} probe={wake_probe!r} sec={duration_sec:.2f}")
            save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text="[WAKE PROBE SKIP]", debug_meta=debug_meta)
            register_drop_reason(metrics, "wake_probe_low_signal", session_key=session_key, wake_probe_text=wake_probe, wake_detected=wake_detected)
            log_voice_stage(metrics, "웨이크 프로브 기반 조기 종료", extra=f"wake_probe_text={wake_probe!r} sec={duration_sec:.2f}")
            return
        log_voice_stage(metrics, "웨이크 미검출이지만 본문 STT 진행", extra=f"wake_probe_text={wake_probe!r}")

    print(f"[FULL STT ENTER] speaker={member.display_name} sampling_rate={stt_sampling_rate} samples={audio16k.size} wake_detected={wake_detected}")
    log_voice_stage(metrics, "본문 STT 시작", extra=f"samples={audio16k.size}")
    stt_meta: dict | None = None
    try:
        primary_text = await asyncio.to_thread(transcribe_audio16k_sync, audio16k, VOICE_STT_MAX_NEW_TOKENS, sampling_rate=stt_sampling_rate, stage="full")
    except Exception as e:
        print(f"❌ [STT] {e}")
        log_voice_stage(metrics, "본문 STT 실패", extra=repr(e))
        return

    text = primary_text
    if STT_FULL_RESCORING_ENABLED:
        log_voice_stage(metrics, "본문 STT 2차 rescoring 시작")
        try:
            rescore_text = await asyncio.to_thread(
                transcribe_audio16k_sync,
                audio16k,
                VOICE_STT_MAX_NEW_TOKENS + max(0, STT_FULL_RESCORE_EXTRA_TOKENS),
                sampling_rate=stt_sampling_rate,
                stage="full-rescore",
            )
            text, stt_meta = choose_full_stt_candidate(primary_text, rescore_text, wake_probe=wake_probe)
            print(
                f"[STT RESCORE] speaker={member.display_name} selected={stt_meta['selected']} primary_score={stt_meta['primary_score']:.3f} rescore_score={stt_meta['rescore_score']:.3f}"
            )
            if stt_meta["selected"] == "rescore":
                print(f"[STT RESCORE PICK] primary={primary_text!r} -> rescore={rescore_text!r}")
            log_voice_stage(metrics, "본문 STT 2차 rescoring 완료", extra=f"selected={stt_meta['selected']}")
        except Exception as e:
            stt_meta = {"enabled": True, "selected": "primary", "rescore_error": repr(e), "primary_text": primary_text}
            print(f"⚠️ [STT RESCORE FAIL] {e}")
            log_voice_stage(metrics, "본문 STT 2차 rescoring 실패", extra=repr(e))
    else:
        stt_meta = {"enabled": False, "selected": "primary", "primary_text": primary_text}

    log_voice_stage(metrics, "본문 STT 완료", extra=f"text_len={len(text)}", key="stt_done")

    if not text:
        save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text="[EMPTY STT]", debug_meta=debug_meta, stt_meta=stt_meta)
        log_voice_stage(metrics, "본문 STT 빈 결과")
        return

    corrected_text = apply_stt_post_corrections(text, wake_detected=wake_detected)
    if corrected_text != text:
        print(f"[STT CORRECT] raw={text!r} -> corrected={corrected_text!r}")
    text = corrected_text

    if should_ignore_short_transcription(text, pcm_bytes, wake_detected=wake_detected):
        print(f"[STT IGNORE] short_noise: {text!r}")
        save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text=text, debug_meta=debug_meta, stt_meta=stt_meta)
        log_voice_stage(metrics, "짧은 STT 무시", extra=f"text={text!r}")
        return

    if not active_session:
        final_wake_alias = extract_leading_wake_alias(text)
        if final_wake_alias is None:
            wake_detected = False
            wake_match_mode = "rejected"
            wake_reject_reason = "full_text_veto"
            register_drop_reason(
                metrics,
                "full_text_veto",
                session_key=session_key,
                wake_probe_text=wake_probe,
                wake_confirm_text=wake_confirm,
                final_text=text,
            )
            save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text=text, debug_meta=debug_meta, stt_meta=stt_meta)
            log_voice_stage(metrics, "최종 텍스트 veto", extra=f"wake_reject_reason={wake_reject_reason} text={text!r}")
            log_voice_bottleneck_summary(metrics, label="voice_reply", extra="drop=full_text_veto")
            return
        wake_alias = final_wake_alias

    save_voice_debug_audio(guild_id, speaker_name, pcm_bytes, audio16k, wake_probe=wake_probe, final_text=text, debug_meta=debug_meta, stt_meta=stt_meta)
    print(
        f"🎤 [{member.display_name}] wake_detected={wake_detected} wake_match_mode={wake_match_mode} wake_alias={wake_alias!r} "
        f"wake_probe_text={wake_probe!r} wake_confirm_text={wake_confirm!r} wake_reject_reason={wake_reject_reason!r} text={text}"
    )

    ok, reason = should_reply_to_voice(
        guild_id,
        text,
        wake_detected=wake_detected,
        session_key=session_key,
        user_id=member.id,
    )
    if not ok:
        print(f"[STT IGNORE] {reason}: {text!r}")
        register_drop_reason(metrics, reason, session_key=session_key, text=text)
        log_voice_stage(metrics, "응답 차단", extra=f"reason={reason}")
        log_voice_bottleneck_summary(metrics, label="voice_reply", extra=f"drop={reason}")
        return

    log_voice_stage(metrics, "웨이크 통과", extra=f"user_text={strip_voice_wake_word(text)!r}")

    raw_user_text = strip_voice_wake_word(text)
    prompt_user_text = raw_user_text or "사용자가 너를 이름만 불렀다. 아주 짧고 자연스럽게 반응해라."
    history_user_text = raw_user_text or text
    topic_id = build_topic_id(history_user_text, session_topic_ids.get(session_key, ""))
    accepted_turn_id = start_new_turn(session_key, turn_id=turn_id)
    update_session_state(
        session_key,
        user_id=member.id,
        speaker="user",
        awaiting_user_reply=False,
        topic_id=topic_id,
        user_text=history_user_text,
    )
    metrics.setdefault("meta", {}).update({"topic_id": topic_id, "turn_id": accepted_turn_id})
    vc = guild.voice_client
    lock = session_locks.setdefault(session_key, asyncio.Lock())

    if lock.locked():
        print(f"[VOICE WAIT] session={session_key} speaker={member.display_name} text={history_user_text!r}")
        log_voice_stage(metrics, "세션 락 대기", extra=f"session={session_key}")

    async with lock:
        log_voice_stage(metrics, "세션 락 획득", extra=f"session={session_key}")
        vc = guild.voice_client
        if vc is None:
            return

        async def on_final_answer(answer_text: str) -> None:
            print(f"💬 [Evelyn] {visible_text(answer_text)}")

        try:
            answer = await ask_llm_and_speak_streaming(
                vc,
                prompt_user_text,
                guild_id=guild_id,
                on_final_answer=on_final_answer,
                session_key=session_key,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                source="voice",
                debug_text=history_user_text,
                metrics=metrics,
            )
            log_voice_stage(metrics, "LLM/TTS 완료", extra=f"answer_len={len(answer)}")
        except Exception as e:
            print(f"❌ [LLM/TTS] {e}")
            return

        answer = clean_text(answer)
        if not answer:
            log_voice_stage(metrics, "최종 답변 비어있음")
            return

        plain_answer = strip_omnivoice_tags(answer)
        if not plain_answer:
            plain_answer = answer

        append_history(session_key, history_user_text, plain_answer, guild_id=guild_id)
        schedule_memory_update(
            guild_id,
            history_user_text,
            plain_answer,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            source="voice",
            user_speaker=member.display_name,
            assistant_speaker="Evelyn",
        )
        schedule_search_followup(
            guild_id,
            session_key,
            history_user_text,
            plain_answer,
            room_key=room_key,
            person_key=person_key,
            session_memory_key=session_memory_key,
            channel_id=None,
            source="search-followup-voice",
        )
        awaiting_reply = bool("?" in plain_answer or "？" in plain_answer)
        mark_session_active(
            session_key,
            user_id=member.id,
            ttl_sec=ACTIVE_CONVERSATION_VOICE_QUESTION_SEC if awaiting_reply else ACTIVE_CONVERSATION_VOICE_SEC,
            speaker="assistant",
            awaiting_user_reply=awaiting_reply,
            topic_id=topic_id,
            answer_text=plain_answer,
            user_text=history_user_text,
        )
        log_voice_stage(metrics, "voice_worker_turn 완료", extra=f"speaker={member.display_name}")


# =========================================================
# 이벤트
# =========================================================
@bot.event
async def on_ready():
    print(f"로그인 완료: {bot.user}")
    ensure_voice_worker_started()
    await set_tts_presence(True)
    try:
        await asyncio.to_thread(get_stt_model)
    except Exception as e:
        print(f"STT 로드 실패: {e}")

    try:
        await warmup_tts_server()
    except Exception as e:
        print("OmniVoice 서버 준비 확인 실패:", repr(e))
    finally:
        await set_tts_presence(False)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if not message.guild:
        await bot.process_commands(message)
        return

    thread_id = getattr(message.channel, "id", None) if isinstance(getattr(message.channel, "parent", None), discord.TextChannel) else None
    session_key = make_text_session_key(message.guild.id, message.channel.id, message.author.id, thread_id=thread_id)
    room_key = make_room_memory_key("text", message.channel.id)
    person_key = make_person_memory_key(message.author.id)
    session_memory_key = make_session_memory_key(session_key, message.author.id)
    remember_session_followup_target(session_key, channel_id=message.channel.id)

    is_wake_word = contains_wake_word(message.content)
    is_reply = False
    is_active_session = is_session_active_for_user(session_key, message.author.id)

    if message.reference:
        try:
            replied_msg = await message.channel.fetch_message(message.reference.message_id)
            if replied_msg.author == bot.user:
                is_reply = True
        except Exception as e:
            print("답장 확인 오류:", repr(e))

    if not (is_wake_word or is_reply or is_active_session):
        log_turn_event(
            "turn_drop",
            turn_id=current_turn_id(session_key),
            segment_id=0,
            source="text",
            session_key=session_key,
            reason="text_gate_not_open",
            user_id=message.author.id,
        )
        await bot.process_commands(message)
        return

    user_text = strip_voice_wake_word(message.content) if is_wake_word else message.content.strip()
    if not user_text:
        user_text = "부르셨나요?"

    topic_id = build_topic_id(user_text, session_topic_ids.get(session_key, ""))
    turn_id = start_new_turn(session_key)
    update_session_state(
        session_key,
        user_id=message.author.id,
        speaker="user",
        awaiting_user_reply=False,
        topic_id=topic_id,
        user_text=user_text,
    )

    get_conversation_history(session_key=session_key, guild_id=message.guild.id)

    lock = session_locks.setdefault(session_key, asyncio.Lock())

    if lock.locked():
        await message.channel.send("⏳ 지금 다른 응답을 처리 중이야. 잠깐만.")
        await bot.process_commands(message)
        return

    async with lock:
        try:
            async with message.channel.typing():
                vc = None
                if AUTO_JOIN_VOICE:
                    vc = await ensure_voice_client(message)

                answer, _sent_message = await stream_text_reply(
                    message.channel,
                    user_text,
                    guild_id=message.guild.id,
                    session_key=session_key,
                    turn_id=turn_id,
                    room_key=room_key,
                    person_key=person_key,
                    session_memory_key=session_memory_key,
                    source="text",
                    debug_text=user_text,
                )
                plain_answer = strip_omnivoice_tags(answer)
                if not plain_answer:
                    plain_answer = answer

            append_history(session_key, user_text, plain_answer, guild_id=message.guild.id)
            schedule_memory_update(
                message.guild.id,
                user_text,
                plain_answer,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                source="text",
                user_speaker=message.author.display_name,
                assistant_speaker="Evelyn",
            )
            schedule_search_followup(
                message.guild.id,
                session_key,
                user_text,
                plain_answer,
                room_key=room_key,
                person_key=person_key,
                session_memory_key=session_memory_key,
                channel_id=message.channel.id,
                source="search-followup-text",
            )

            awaiting_reply = bool("?" in plain_answer or "？" in plain_answer)
            mark_session_active(
                session_key,
                user_id=message.author.id,
                ttl_sec=ACTIVE_CONVERSATION_TEXT_QUESTION_SEC if awaiting_reply else ACTIVE_CONVERSATION_TEXT_SEC,
                speaker="assistant",
                awaiting_user_reply=awaiting_reply,
                topic_id=topic_id,
                answer_text=plain_answer,
                user_text=user_text,
            )

            if vc is not None:
                await speak_answer(vc, answer)

        except Exception as e:
            print("전체 오류:", repr(e))
            await message.channel.send(f"❌ 오류 발생: {e}")

    await bot.process_commands(message)


# =========================================================
# 명령어
# =========================================================
@bot.command(name="들어와", aliases=["join"])
async def join_voice(ctx):
    voice_state = getattr(ctx.author, "voice", None)
    if not voice_state or not voice_state.channel:
        await ctx.send("먼저 음성 채널에 들어가줘.")
        return

    try:
        vc = await ensure_listening_voice_client(ctx.guild, voice_state.channel)
        if vc is None:
            await ctx.send("❌ 음성 연결에 실패했어.")
            return
        await ctx.send(f"🔊 {voice_state.channel.name}에 들어왔어. 이제 듣고 말할게.")
    except Exception as e:
        print("음성 연결 오류:", repr(e))
        await ctx.send(f"❌ 음성 연결 실패: {e}")


@bot.command(name="다시들어와", aliases=["rejoin"])
async def rejoin_voice(ctx):
    channel = ctx.author.voice.channel if ctx.author.voice else None
    if channel is None:
        await ctx.send("먼저 음성 채널에 들어가줘.")
        return

    vc = ctx.guild.voice_client
    if vc is not None:
        try:
            if hasattr(vc, "stop_listening"):
                vc.stop_listening()
        except Exception:
            pass
        await vc.disconnect(force=True)

    try:
        new_vc = await ensure_listening_voice_client(ctx.guild, channel)
        if new_vc is None:
            await ctx.send("❌ 재연결 실패")
            return
        await ctx.send("🔄 다시 붙었어. 이제 계속 들을게.")
    except Exception as e:
        print("재연결 오류:", repr(e))
        await ctx.send(f"❌ 재연결 실패: {e}")


@bot.command(name="나가", aliases=["leave"])
async def leave_voice(ctx):
    vc = ctx.guild.voice_client
    if vc is None:
        await ctx.send("이미 나와 있어.")
        return

    try:
        if hasattr(vc, "stop_listening"):
            vc.stop_listening()
    except Exception:
        pass

    await vc.disconnect()
    await ctx.send("👋 나갔어.")


async def restart_bot_process() -> None:
    await asyncio.sleep(1.0)
    script_path = Path(__file__).resolve()
    project_dir = script_path.parent
    start_bot_bat = project_dir / "evelyn_core" / "start_bot.bat"
    start_bat = project_dir / "evelyn_core" / "start.bat"

    env = os.environ.copy()
    env.setdefault("STT_USE_RAW_48K", "false")

    if start_bot_bat.exists():
        subprocess.Popen(
            ["cmd.exe", "/c", str(start_bot_bat)],
            cwd=str(project_dir),
            env=env,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    elif start_bat.exists():
        subprocess.Popen(
            ["cmd.exe", "/c", str(start_bat)],
            cwd=str(project_dir),
            env=env,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    else:
        subprocess.Popen(
            [sys.executable, str(script_path)],
            cwd=str(project_dir),
            env=env,
            close_fds=True,
        )
    os._exit(0)


async def shutdown_bot_process() -> None:
    await asyncio.sleep(0.5)
    try:
        for guild in list(bot.guilds):
            vc = guild.voice_client
            if vc is None:
                continue
            try:
                if hasattr(vc, "stop_listening"):
                    vc.stop_listening()
            except Exception:
                pass
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass
    finally:
        os._exit(0)


@bot.command(name="재시작", aliases=["restart"])
@commands.check(lambda ctx: ctx.author.id in ALLOWED_RESTART_USER_IDS)
async def restart_bot_command(ctx):
    await ctx.send("🔄 봇을 재시작할게. 잠깐만 기다려줘.")
    asyncio.create_task(restart_bot_process())


@restart_bot_command.error
async def restart_bot_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("이 명령은 허용된 사용자만 쓸 수 있어.")
        return
    raise error


@bot.command(name="종료", aliases=["shutdown", "quit", "exit"])
@commands.check(lambda ctx: ctx.author.id in ALLOWED_RESTART_USER_IDS)
async def shutdown_bot_command(ctx):
    await ctx.send("⏹️ 봇을 종료할게.")
    asyncio.create_task(shutdown_bot_process())


@shutdown_bot_command.error
async def shutdown_bot_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("이 명령은 허용된 사용자만 쓸 수 있어.")
        return
    raise error


@bot.command(name="상태", aliases=["status"])
async def status_command(ctx):
    guild = ctx.guild
    vc = guild.voice_client if guild else None
    voice_channel_name = getattr(getattr(vc, "channel", None), "name", None) or "없음"
    listening = bool(vc and hasattr(vc, "is_listening") and vc.is_listening())
    debug_audio_state = "on" if VOICE_DEBUG_SAVE_AUDIO else "off"
    opus_env_state = os.getenv("OPUS_ERROR_TO_SILENCE")
    try:
        from evelyn_voice.client import OPUS_ERROR_TO_SILENCE as OPUS_RUNTIME_VALUE
    except Exception:
        OPUS_RUNTIME_VALUE = None

    await ctx.send(
        "\n".join([
            f"모델: {MODEL_NAME}",
            f"라우터모델: {ROUTER_MODEL_NAME}",
            f"서브모델: {SUMMARY_MODEL_NAME}",
            f"STT: {STT_MODEL_NAME}",
            f"음성채널: {voice_channel_name}",
            f"리스닝: {'on' if listening else 'off'}",
            f"디버그 오디오 저장: {debug_audio_state}",
            f"OPUS_ERROR_TO_SILENCE(env): {opus_env_state if opus_env_state is not None else 'unset'}",
            f"OPUS_ERROR_TO_SILENCE(runtime): {OPUS_RUNTIME_VALUE}",
            f"VAD: {'on' if VAD_ENABLED else 'off'} ({VAD_PROVIDER})",
        ])
    )


@bot.command(name="접두사", aliases=["prefix"])
@commands.has_guild_permissions(manage_guild=True)
async def set_guild_prefix(ctx, new_prefix: str | None = None):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return

    guild_id = ctx.guild.id
    current_prefix = get_guild_command_prefix(guild_id)

    if not new_prefix:
        await ctx.send(
            f"현재 이 길드 명령어 시작 부호는 `{current_prefix}` 야. 바꾸려면 `{current_prefix}접두사 ?` 처럼 써줘. 기본값으로 돌리려면 `{current_prefix}접두사 기본`"
        )
        return

    if new_prefix.lower() in {"기본", "default", "reset"}:
        saved_prefix = save_guild_command_prefix(guild_id, DEFAULT_COMMAND_PREFIX)
        await ctx.send(f"✅ 명령어 시작 부호를 기본값 `{saved_prefix}` 로 되돌렸어.")
        return

    try:
        saved_prefix = save_guild_command_prefix(guild_id, new_prefix)
    except ValueError as e:
        await ctx.send(f"❌ {e}")
        return

    await ctx.send(f"✅ 이 길드 명령어 시작 부호를 `{saved_prefix}` 로 저장했어. 이제 `{saved_prefix}초기화`, `{saved_prefix}들어와` 처럼 쓰면 돼.")


@set_guild_prefix.error
async def set_guild_prefix_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("이 명령은 서버 관리 권한이 있어야 쓸 수 있어.")
        return
    raise error


@bot.command(name="초기화", aliases=["reset"])
@commands.check(lambda ctx: ctx.author.id in ALLOWED_RESTART_USER_IDS)
async def reset_guild_memory(ctx):
    if ctx.guild is None:
        await ctx.send("이 명령은 길드에서만 쓸 수 있어.")
        return

    guild_id = ctx.guild.id
    memory_dir = MEMORY_ROOT / f"guild_{guild_id}"
    current_prefix = get_guild_command_prefix(guild_id)

    reset_guild_runtime_state(guild_id)
    if memory_dir.exists():
        shutil.rmtree(memory_dir)

    await ctx.send(f"🧹 {ctx.guild.name} 메모리와 대화 히스토리를 이 길드만 초기화했어. 명령어 시작 부호 `{current_prefix}` 설정은 유지했어.")


@reset_guild_memory.error
async def reset_guild_memory_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.send("이 명령은 허용된 사용자만 쓸 수 있어.")
        return
    raise error


# =========================================================
# 실행
# =========================================================
if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN 환경변수가 설정되지 않았습니다.")

bot.run(DISCORD_BOT_TOKEN)
