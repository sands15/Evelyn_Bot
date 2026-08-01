from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

import numpy as np


@dataclass(frozen=True)
class SttTextRuntimeDeps:
    clean_text: Callable[[str], str]
    normalize_voice_text: Callable[[str], str]
    contains_wake_word: Callable[[str], bool]
    looks_like_brief_filler_text: Callable[[str], bool]
    looks_like_repetitive_noise_text: Callable[[str], bool]
    is_similar: Callable[[str, str], bool]
    session_partial_stt_text: MutableMapping[str, str]
    session_committed_stt_text: MutableMapping[str, str]
    partial_stt_cache: MutableMapping[str, dict[str, Any]]


def build_stt_text_runtime_deps(
    *,
    clean_text: Callable[[str], str],
    normalize_voice_text: Callable[[str], str],
    contains_wake_word: Callable[[str], bool],
    looks_like_brief_filler_text: Callable[[str], bool],
    looks_like_repetitive_noise_text: Callable[[str], bool],
    is_similar: Callable[[str, str], bool],
    session_partial_stt_text: MutableMapping[str, str],
    session_committed_stt_text: MutableMapping[str, str],
    partial_stt_cache: MutableMapping[str, dict[str, Any]],
) -> SttTextRuntimeDeps:
    return SttTextRuntimeDeps(
        clean_text=clean_text,
        normalize_voice_text=normalize_voice_text,
        contains_wake_word=contains_wake_word,
        looks_like_brief_filler_text=looks_like_brief_filler_text,
        looks_like_repetitive_noise_text=looks_like_repetitive_noise_text,
        is_similar=is_similar,
        session_partial_stt_text=session_partial_stt_text,
        session_committed_stt_text=session_committed_stt_text,
        partial_stt_cache=partial_stt_cache,
    )


def build_partial_stt_window_from_runtime(audio16k: np.ndarray, *, sampling_rate: int = 16000) -> np.ndarray:
    if audio16k.size == 0:
        return audio16k
    rate = max(1, int(sampling_rate))
    max_samples = int(rate * 1.2)
    overlap_samples = int(rate * 0.3)
    if audio16k.size <= max_samples:
        return np.asarray(audio16k, dtype=np.float32)
    start = max(0, audio16k.size - max_samples)
    if start > overlap_samples:
        start -= overlap_samples
    return np.asarray(audio16k[start:], dtype=np.float32)


def longest_common_prefix_text_from_runtime(a: str, b: str, *, clean_text: Callable[[str], str]) -> str:
    left = clean_text(a)
    right = clean_text(b)
    limit = min(len(left), len(right))
    idx = 0
    while idx < limit and left[idx] == right[idx]:
        idx += 1
    return left[:idx]


def commit_stable_transcript_from_runtime(
    session_key: str | None,
    *,
    new_partial_text: str,
    deps: SttTextRuntimeDeps,
) -> str:
    if not session_key:
        return deps.clean_text(new_partial_text)
    prev_partial = deps.clean_text(deps.session_partial_stt_text.get(session_key, ""))
    committed = deps.clean_text(deps.session_committed_stt_text.get(session_key, ""))
    current_partial = deps.clean_text(new_partial_text)
    deps.session_partial_stt_text[session_key] = current_partial
    if not current_partial:
        return committed
    stable = (
        longest_common_prefix_text_from_runtime(prev_partial, current_partial, clean_text=deps.clean_text)
        if prev_partial
        else current_partial
    )
    safe = stable[:-3].strip() if len(stable) > 3 else ""
    if not safe and current_partial == prev_partial:
        safe = current_partial
    if safe and len(safe) > len(committed):
        committed = deps.clean_text(safe)
        deps.session_committed_stt_text[session_key] = committed
    elif not committed:
        deps.session_committed_stt_text[session_key] = committed
    return committed


def get_partial_transcript_from_runtime(
    session_key: str | None,
    audio16k: np.ndarray,
    *,
    sampling_rate: int,
    max_new_tokens: int,
    transcribe_audio16k_sync: Callable[..., str],
    deps: SttTextRuntimeDeps,
    validation_bound: bool = False,
) -> tuple[str, str]:
    partial_audio = build_partial_stt_window_from_runtime(audio16k, sampling_rate=sampling_rate)
    partial_samples = int(partial_audio.size)
    min_partial_samples = max(1, int(float(sampling_rate) * 0.85))
    if partial_samples < min_partial_samples:
        committed_text = deps.clean_text(deps.session_committed_stt_text.get(session_key or "", ""))
        return "", committed_text

    audio_hash = hashlib.sha1(np.asarray(partial_audio, dtype=np.float32).tobytes()).hexdigest()
    cache_key = session_key or "__global__"
    cached = deps.partial_stt_cache.get(cache_key)
    if cached and cached.get("hash") == audio_hash:
        partial_text = deps.clean_text(str(cached.get("partial_text") or ""))
        committed_text = commit_stable_transcript_from_runtime(
            session_key,
            new_partial_text=partial_text,
            deps=deps,
        )
        return partial_text, committed_text

    transcribe_kwargs: dict[str, Any] = {
        "max_new_tokens": max_new_tokens,
        "sampling_rate": sampling_rate,
        "stage": "partial",
    }
    if validation_bound:
        transcribe_kwargs["validation_bound"] = True
    partial_text = transcribe_audio16k_sync(partial_audio, **transcribe_kwargs)
    deps.partial_stt_cache[cache_key] = {
        "hash": audio_hash,
        "partial_text": partial_text,
        "samples": partial_samples,
        "updated_at": time.monotonic(),
    }
    committed_text = commit_stable_transcript_from_runtime(
        session_key,
        new_partial_text=partial_text,
        deps=deps,
    )
    return partial_text, committed_text


def score_stt_candidate_from_runtime(
    text: str,
    *,
    wake_probe: str = "",
    deps: SttTextRuntimeDeps,
) -> float:
    text = deps.clean_text(text)
    if not text:
        return -100.0

    normalized = deps.normalize_voice_text(text)
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

    if deps.contains_wake_word(text):
        score += 10.0
    if wake_probe:
        wake_probe_n = deps.normalize_voice_text(wake_probe)
        if wake_probe_n and wake_probe_n in normalized:
            score += 2.0

    if deps.looks_like_brief_filler_text(text):
        score -= 14.0
    if deps.looks_like_repetitive_noise_text(text):
        score -= 16.0
    if re.search(r"(.)\1{3,}", compact):
        score -= 6.0
    if len(compact) <= 2:
        score -= 4.0

    return score


def choose_full_stt_candidate_from_runtime(
    primary_text: str,
    rescore_text: str,
    *,
    wake_probe: str = "",
    deps: SttTextRuntimeDeps,
) -> tuple[str, dict]:
    primary = deps.clean_text(primary_text)
    rescore = deps.clean_text(rescore_text)
    primary_score = score_stt_candidate_from_runtime(primary, wake_probe=wake_probe, deps=deps)
    rescore_score = score_stt_candidate_from_runtime(rescore, wake_probe=wake_probe, deps=deps)

    choice = "primary"
    chosen_text = primary

    if not primary and rescore:
        choice = "rescore"
        chosen_text = rescore
    elif rescore and not deps.is_similar(primary, rescore):
        if rescore_score >= primary_score + 1.5:
            choice = "rescore"
            chosen_text = rescore
        elif deps.contains_wake_word(rescore) and not deps.contains_wake_word(primary) and rescore_score >= primary_score:
            choice = "rescore"
            chosen_text = rescore
        elif len(deps.normalize_voice_text(rescore).replace(" ", "")) >= len(deps.normalize_voice_text(primary).replace(" ", "")) + 3 and rescore_score > primary_score:
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


def detect_wake_word_sync_from_runtime(
    audio: np.ndarray,
    *,
    sampling_rate: int,
    wake_audio_sec: float,
    wake_confirm_audio_sec: float,
    wake_max_tokens: int,
    wake_confirm_max_tokens: int,
    transcribe_audio16k_sync: Callable[..., str],
    apply_stt_post_corrections: Callable[[str, bool], str],
    strip_leading_voice_fillers: Callable[[str], str],
    extract_leading_wake_alias: Callable[[str], str | None],
    fuzzy_leading_wake_alias: Callable[[str], str | None],
    looks_like_gibberish_probe: Callable[[str], bool],
    slice_audio_window: Callable[[np.ndarray, float, int], np.ndarray],
    validation_bound: bool = False,
) -> dict[str, str | bool | None]:
    wake_audio = slice_audio_window(audio, wake_audio_sec, sampling_rate=sampling_rate)
    wake_kwargs: dict[str, Any] = {
        "max_new_tokens": wake_max_tokens,
        "sampling_rate": sampling_rate,
        "stage": "wake",
    }
    if validation_bound:
        wake_kwargs["validation_bound"] = True
    wake_raw_text = transcribe_audio16k_sync(wake_audio, **wake_kwargs)
    wake_text = apply_stt_post_corrections(wake_raw_text, wake_detected=False)

    probe_text = strip_leading_voice_fillers(wake_text)
    probe_alias = extract_leading_wake_alias(probe_text)
    probe_fuzzy_alias = fuzzy_leading_wake_alias(probe_text) if probe_alias is None else None

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

    confirm_audio = slice_audio_window(audio, wake_confirm_audio_sec, sampling_rate=sampling_rate)
    confirm_kwargs: dict[str, Any] = {
        "max_new_tokens": wake_confirm_max_tokens,
        "sampling_rate": sampling_rate,
        "stage": "wake-confirm",
    }
    if validation_bound:
        confirm_kwargs["validation_bound"] = True
    confirm_raw_text = transcribe_audio16k_sync(
        confirm_audio,
        **confirm_kwargs,
    )
    confirm_text = apply_stt_post_corrections(confirm_raw_text, wake_detected=False)
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


__all__ = [
    "SttTextRuntimeDeps",
    "build_stt_text_runtime_deps",
    "build_partial_stt_window_from_runtime",
    "longest_common_prefix_text_from_runtime",
    "commit_stable_transcript_from_runtime",
    "get_partial_transcript_from_runtime",
    "score_stt_candidate_from_runtime",
    "choose_full_stt_candidate_from_runtime",
    "detect_wake_word_sync_from_runtime",
]
