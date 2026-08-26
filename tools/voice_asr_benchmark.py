from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import re
import stat
import sys
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import numpy as np


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
for import_root in (REPO_ROOT, RUNTIME_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from tools.ko_stt_scoreboard import (  # noqa: E402
    normalize_korean_text,
    score_transcript,
)
from evelyn_core.local_voice_admission import (  # noqa: E402
    local_voice_requires_fresh_wake,
    split_exact_leading_wake,
)
from evelyn_core.runtime_artifact_io import atomic_json_write  # noqa: E402
from evelyn_core.stt_client import (  # noqa: E402
    cancel_stt_stream_via_service,
    finish_stt_stream_via_service,
    push_stt_stream_chunk_via_service,
    start_stt_stream_via_service,
    transcribe_audio16k_via_service,
)
from evelyn_core.stt_streaming_runtime import (  # noqa: E402
    CompletedSttStream,
    transcribe_complete_audio_stream,
)


REPORT_SCHEMA = "evelyn.voice-asr-headless.v1"
MANIFEST_SCHEMA = "evelyn.voice-asr-corpus.v1"
VALIDATION_ROOT = REPO_ROOT / "runtime_artifacts" / "validation"
PRIVATE_ROOT = VALIDATION_ROOT / "voice_asr"
MANIFEST_PATH = PRIVATE_ROOT / "manifest.json"
DEFAULT_REPORT = VALIDATION_ROOT / "voice_asr_headless_report.json"
DEFAULT_STT_URL = "http://127.0.0.1:8892"
P0_4_STT_MODEL = "Qwen/Qwen3-ASR-1.7B"
P0_4_STT_BACKEND = "vllm"
P0_4_STT_MEMORY_UTILIZATION = 0.35
SAMPLE_RATE = 16_000
CHUNK_SAMPLES = 8_000
CHUNK_INTERVAL_SEC = 0.5
MAX_AUDIO_SAMPLES = SAMPLE_RATE * 30

POSITIVE_CLASSES = {
    "suite-clean": 10,
    "suite-far-field": 10,
    "domain-clean": 10,
    "domain-discord-pcm": 10,
}
NEGATIVE_CLASSES = {
    "silence": 2,
    "fan-keyboard": 2,
    "music": 2,
    "tts-echo": 2,
    "near-miss": 2,
}
ITEM_KEYS = {
    "kind",
    "sourceClass",
    "audio",
    "audioSha256",
    "reference",
    "entities",
}
FAILURE_CODES = (
    "manifest_invalid",
    "batch_request_failed",
    "batch_response_invalid",
    "batch_empty_positive",
    "stream_request_failed",
    "stream_response_invalid",
    "stream_empty_positive",
    "stream_non_authoritative",
    "stream_stable_prefix_conflict",
    "cancel_smoke_failed",
    "cleanup_failed",
    "internal_failure",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SOURCE_COMMIT = re.compile(r"[0-9a-f]{40}")
_ATTEMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_GPU_UUID = re.compile(
    r"GPU-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class ContractError(RuntimeError):
    """A fixed, content-free validation failure."""


@dataclass(frozen=True, repr=False)
class AudioBinding:
    kind: str
    source_class: str
    path: Path
    sha256: str
    reference: str
    entities: tuple[str, ...]
    file_identity: tuple[int, int, int, int]
    sample_count: int


@dataclass(frozen=True, repr=False)
class Corpus:
    root: Path
    root_identity: tuple[int, int, int, int]
    manifest_path: Path
    manifest_identity: tuple[int, int, int, int]
    manifest_sha256: str
    items: tuple[AudioBinding, ...]
    entity_occurrences: int


@dataclass(frozen=True)
class BenchmarkConfig:
    attempt_id: str
    source_commit: str
    image_sha256: str
    gpu_uuid: str
    expected_manifest_sha256: str
    stt_url: str = DEFAULT_STT_URL
    timeout_sec: float = 60.0
    retain_private_audio: bool = False


@dataclass(frozen=True, repr=False)
class StreamObservation:
    text: str
    latency_ms: float
    first_partial_ms: float | None
    revision_count: int
    authoritative: bool
    fallback_reason: str | None


@dataclass(frozen=True)
class ClientFunctions:
    batch: Callable[..., dict[str, Any]] = transcribe_audio16k_via_service
    start: Callable[..., dict[str, Any]] = start_stt_stream_via_service
    push: Callable[..., dict[str, Any]] = push_stt_stream_chunk_via_service
    finish: Callable[..., dict[str, Any]] = finish_stt_stream_via_service
    cancel: Callable[..., dict[str, Any]] = cancel_stt_stream_via_service
    stream: Callable[..., Any] = transcribe_complete_audio_stream


def _fixed_error(code: str) -> ContractError:
    if code not in FAILURE_CODES and code not in {
        "audio_binding_changed",
        "audio_cleanup_incomplete",
        "private_file_set_changed",
        "report_target_invalid",
    }:
        code = "internal_failure"
    return ContractError(code)


def _is_reparse(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _regular_metadata(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise _fixed_error("manifest_invalid") from exc
    if _is_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise _fixed_error("manifest_invalid")
    return metadata


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise _fixed_error("manifest_invalid") from exc
    return digest.hexdigest()


def _validate_component_tree(root: Path, target: Path) -> None:
    current = target
    while current != root:
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise _fixed_error("manifest_invalid") from exc
        if _is_reparse(metadata):
            raise _fixed_error("manifest_invalid")
        current = current.parent


def _read_wav_metadata(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as handle:
            if (
                handle.getnchannels() != 1
                or handle.getsampwidth() != 2
                or handle.getframerate() != SAMPLE_RATE
                or handle.getcomptype() != "NONE"
            ):
                raise _fixed_error("manifest_invalid")
            sample_count = int(handle.getnframes())
            if not 0 < sample_count <= MAX_AUDIO_SAMPLES:
                raise _fixed_error("manifest_invalid")
            if len(handle.readframes(sample_count)) != sample_count * 2:
                raise _fixed_error("manifest_invalid")
    except ContractError:
        raise
    except (OSError, EOFError, wave.Error) as exc:
        raise _fixed_error("manifest_invalid") from exc
    return sample_count


def _entity_occurrence_count(entity: str, text: str) -> int:
    entity_tokens = normalize_korean_text(entity, keep_spaces=True).split()
    text_tokens = normalize_korean_text(text, keep_spaces=True).split()
    width = len(entity_tokens)
    return (
        sum(
            1
            for index in range(len(text_tokens) - width + 1)
            if text_tokens[index : index + width] == entity_tokens
        )
        if width
        else 0
    )


def _entity_in_text(entity: str, text: str) -> bool:
    return _entity_occurrence_count(entity, text) > 0


def _private_regular_files(root: Path) -> set[Path]:
    observed: set[Path] = set()
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise _fixed_error("manifest_invalid") from exc
    if _is_reparse(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
        raise _fixed_error("manifest_invalid")
    for directory, names, files in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in names:
            metadata = (directory_path / name).lstat()
            if _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
                raise _fixed_error("manifest_invalid")
        for name in files:
            candidate = directory_path / name
            metadata = candidate.lstat()
            if _is_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
                raise _fixed_error("manifest_invalid")
            observed.add(candidate.resolve(strict=True))
    return observed


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _value in pairs]
    if len(keys) != len(set(keys)):
        raise _fixed_error("manifest_invalid")
    return dict(pairs)


def load_corpus_manifest(
    root: Path = PRIVATE_ROOT,
    *,
    expected_manifest_sha256: str | None = None,
) -> Corpus:
    requested_root = Path(root).absolute()
    try:
        root_metadata = requested_root.lstat()
    except OSError as exc:
        raise _fixed_error("manifest_invalid") from exc
    if _is_reparse(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
        raise _fixed_error("manifest_invalid")
    private_root = requested_root.resolve(strict=True)
    manifest_path = private_root / "manifest.json"
    manifest_metadata = _regular_metadata(manifest_path)
    if manifest_metadata.st_size > 512 * 1024:
        raise _fixed_error("manifest_invalid")
    try:
        raw = manifest_path.read_bytes()
        manifest_sha256 = hashlib.sha256(raw).hexdigest()
        if (
            expected_manifest_sha256 is not None
            and manifest_sha256 != expected_manifest_sha256
        ):
            raise _fixed_error("manifest_invalid")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _fixed_error("manifest_invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "items"}
        or payload.get("schema") != MANIFEST_SCHEMA
        or not isinstance(payload.get("items"), list)
        or len(payload["items"]) != 50
    ):
        raise _fixed_error("manifest_invalid")

    items: list[AudioBinding] = []
    paths: set[Path] = set()
    physical_files: set[tuple[int, int]] = set()
    audio_hashes: set[str] = set()
    positive_counts = dict.fromkeys(POSITIVE_CLASSES, 0)
    negative_counts = dict.fromkeys(NEGATIVE_CLASSES, 0)
    entity_occurrences = 0
    for item in payload["items"]:
        if not isinstance(item, dict) or set(item) != ITEM_KEYS:
            raise _fixed_error("manifest_invalid")
        kind = item.get("kind")
        source_class = item.get("sourceClass")
        if kind == "positive" and source_class in POSITIVE_CLASSES:
            positive_counts[source_class] += 1
        elif kind == "negative" and source_class in NEGATIVE_CLASSES:
            negative_counts[source_class] += 1
        else:
            raise _fixed_error("manifest_invalid")

        audio_name = item.get("audio")
        expected_sha256 = item.get("audioSha256")
        reference = item.get("reference")
        entities = item.get("entities")
        if (
            not isinstance(audio_name, str)
            or not audio_name
            or not isinstance(expected_sha256, str)
            or _SHA256.fullmatch(expected_sha256) is None
            or not isinstance(reference, str)
            or len(reference) > 2_000
            or not isinstance(entities, list)
            or any(not isinstance(entity, str) or len(entity) > 200 for entity in entities)
        ):
            raise _fixed_error("manifest_invalid")
        relative = Path(audio_name)
        if (
            relative.is_absolute()
            or len(relative.parts) != 1
            or relative.suffix.lower() != ".wav"
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise _fixed_error("manifest_invalid")
        try:
            audio_path = (private_root / relative).resolve(strict=True)
            audio_path.relative_to(private_root)
        except (OSError, ValueError) as exc:
            raise _fixed_error("manifest_invalid") from exc
        _validate_component_tree(private_root, audio_path)
        metadata = _regular_metadata(audio_path)
        if metadata.st_size > (MAX_AUDIO_SAMPLES * 2) + 65_536:
            raise _fixed_error("manifest_invalid")
        physical_identity = (int(metadata.st_dev), int(metadata.st_ino))
        if audio_path in paths or (physical_identity[1] and physical_identity in physical_files):
            raise _fixed_error("manifest_invalid")
        paths.add(audio_path)
        physical_files.add(physical_identity)
        if _sha256_file(audio_path) != expected_sha256:
            raise _fixed_error("manifest_invalid")
        if expected_sha256 in audio_hashes:
            raise _fixed_error("manifest_invalid")
        audio_hashes.add(expected_sha256)
        sample_count = _read_wav_metadata(audio_path)

        normalized_entities = tuple(entity.strip() for entity in entities)
        if kind == "positive":
            if not reference.strip():
                raise _fixed_error("manifest_invalid")
            normalized_keys = [
                normalize_korean_text(entity, keep_spaces=True)
                for entity in normalized_entities
            ]
            if (
                any(not key for key in normalized_keys)
                or len(set(normalized_keys)) != len(normalized_keys)
                or any(not _entity_in_text(entity, reference) for entity in normalized_entities)
            ):
                raise _fixed_error("manifest_invalid")
            entity_occurrences += sum(
                _entity_occurrence_count(entity, reference)
                for entity in normalized_entities
            )
        elif reference != "" or normalized_entities:
            raise _fixed_error("manifest_invalid")

        items.append(
            AudioBinding(
                kind=kind,
                source_class=source_class,
                path=audio_path,
                sha256=expected_sha256,
                reference=reference,
                entities=normalized_entities,
                file_identity=_file_identity(metadata),
                sample_count=sample_count,
            )
        )

    if (
        positive_counts != POSITIVE_CLASSES
        or negative_counts != NEGATIVE_CLASSES
        or entity_occurrences < 20
    ):
        raise _fixed_error("manifest_invalid")
    allowed_files = paths | {manifest_path.resolve(strict=True)}
    if _private_regular_files(private_root) != allowed_files:
        raise _fixed_error("manifest_invalid")
    return Corpus(
        root=private_root,
        root_identity=_file_identity(root_metadata),
        manifest_path=manifest_path.resolve(strict=True),
        manifest_identity=_file_identity(manifest_metadata),
        manifest_sha256=manifest_sha256,
        items=tuple(items),
        entity_occurrences=entity_occurrences,
    )


def load_audio(binding: AudioBinding) -> np.ndarray:
    before = _regular_metadata(binding.path)
    if _file_identity(before) != binding.file_identity:
        raise _fixed_error("audio_binding_changed")
    try:
        raw = binding.path.read_bytes()
        after = _regular_metadata(binding.path)
        if (
            _file_identity(after) != binding.file_identity
            or hashlib.sha256(raw).hexdigest() != binding.sha256
        ):
            raise _fixed_error("audio_binding_changed")
        with wave.open(io.BytesIO(raw), "rb") as handle:
            frames = handle.readframes(binding.sample_count)
    except ContractError:
        raise
    except (OSError, EOFError, wave.Error) as exc:
        raise _fixed_error("audio_binding_changed") from exc
    if len(frames) != binding.sample_count * 2:
        raise _fixed_error("audio_binding_changed")
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


class _PacedPush:
    def __init__(
        self,
        delegate: Callable[..., dict[str, Any]],
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delegate = delegate
        self.monotonic = monotonic
        self.sleep = sleep
        self.started: float | None = None
        self.first_partial_ms: float | None = None

    def __call__(self, pcm16: Any, **kwargs: Any) -> dict[str, Any]:
        sequence = int(kwargs.get("sequence", 0))
        if self.started is None:
            self.started = self.monotonic()
        delay = self.started + (sequence * CHUNK_INTERVAL_SEC) - self.monotonic()
        if delay > 0:
            self.sleep(delay)
        response = self.delegate(pcm16, **kwargs)
        if (
            self.first_partial_ms is None
            and isinstance(response, dict)
            and isinstance(response.get("text"), str)
            and response["text"].strip()
        ):
            self.first_partial_ms = max(0.0, (self.monotonic() - self.started) * 1000.0)
        return response


def _batch_observation(
    audio: np.ndarray,
    *,
    config: BenchmarkConfig,
    batch: Callable[..., dict[str, Any]],
) -> tuple[str, float]:
    started = time.monotonic()
    response = batch(
        audio,
        service_url=config.stt_url,
        timeout_sec=config.timeout_sec,
        sampling_rate=SAMPLE_RATE,
        max_new_tokens=256,
        stage="validation-batch",
        language="Korean",
        validation_bound=True,
    )
    elapsed_ms = max(0.0, (time.monotonic() - started) * 1000.0)
    if (
        not isinstance(response, dict)
        or not isinstance(response.get("text"), str)
        or len(response["text"]) > 10_000
    ):
        raise _fixed_error("batch_response_invalid")
    return response["text"].strip(), elapsed_ms


async def _stream_observation(
    audio: np.ndarray,
    *,
    config: BenchmarkConfig,
    clients: ClientFunctions,
    start: Callable[..., dict[str, Any]] | None = None,
    push: Callable[..., dict[str, Any]] | None = None,
    cancel: Callable[..., dict[str, Any]] | None = None,
) -> StreamObservation:
    paced_push = _PacedPush(push or clients.push)
    started = time.monotonic()
    result = await clients.stream(
        audio,
        sampling_rate=SAMPLE_RATE,
        service_url=config.stt_url,
        timeout_sec=config.timeout_sec,
        start_stream=start or clients.start,
        push_chunk=paced_push,
        finish_stream=clients.finish,
        cancel_stream=cancel or clients.cancel,
        chunk_samples=CHUNK_SAMPLES,
    )
    if (
        not isinstance(result, CompletedSttStream)
        or not isinstance(result.final_text, str)
        or len(result.final_text) > 10_000
        or type(result.authoritative) is not bool
        or type(result.revision_count) is not int
        or result.revision_count < 1
        or result.fallback_reason not in {None, "empty_final", "stable_prefix_conflict"}
    ):
        raise _fixed_error("stream_response_invalid")
    return StreamObservation(
        text=result.final_text.strip(),
        latency_ms=max(0.0, (time.monotonic() - started) * 1000.0),
        first_partial_ms=paced_push.first_partial_ms,
        revision_count=result.revision_count,
        authoritative=result.authoritative,
        fallback_reason=result.fallback_reason,
    )


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, int(np.ceil(len(ordered) * fraction)) - 1))
    return round(ordered[index], 3)


def _new_mode() -> dict[str, Any]:
    return {
        "attemptCount": 0,
        "usableCount": 0,
        "emptyCount": 0,
        "malformedCount": 0,
        "errorCount": 0,
        "authoritativeCount": 0,
        "stablePrefixConflictCount": 0,
        "charDistance": 0,
        "referenceChars": 0,
        "entityMatchCount": 0,
        "entityOccurrenceCount": 0,
        "latencies": [],
        "firstPartials": [],
        "revisions": [],
    }


def _new_negative_mode() -> dict[str, Any]:
    return {
        "attemptCount": 0,
        "responseCount": 0,
        "malformedCount": 0,
        "errorCount": 0,
        "acceptedTurnCount": 0,
        "unauthorizedHighImpactCandidateCount": 0,
        "latencies": [],
    }


def _new_aggregates() -> dict[str, Any]:
    return {
        "processedPositive": 0,
        "processedNegative": 0,
        "positiveBatch": _new_mode(),
        "positiveStream": _new_mode(),
        "negativeBatch": _new_negative_mode(),
        "negativeStream": _new_negative_mode(),
        "failureCounts": dict.fromkeys(FAILURE_CODES, 0),
        "cancelSuccessor": {
            "attempted": False,
            "startCount": 0,
            "streamIdsDistinct": False,
            "cancelTargetFirst": False,
            "cancellationObserved": False,
            "remoteCancelCount": 0,
            "remoteCancelSuccessCount": 0,
            "taskDrained": False,
            "pendingTaskCount": 0,
            "batchFallbackUsable": False,
            "successorAuthoritative": False,
            "successorRevisionCount": 0,
        },
    }


def _record_failure(aggregates: dict[str, Any], code: str) -> None:
    safe_code = code if code in FAILURE_CODES else "internal_failure"
    aggregates["failureCounts"][safe_code] += 1


def _mode_snapshot(mode: dict[str, Any]) -> dict[str, Any]:
    denominator = int(mode["referenceChars"])
    entity_total = int(mode["entityOccurrenceCount"])
    revisions = list(mode["revisions"])
    return {
        "attemptCount": int(mode["attemptCount"]),
        "usableCount": int(mode["usableCount"]),
        "emptyCount": int(mode["emptyCount"]),
        "malformedCount": int(mode["malformedCount"]),
        "errorCount": int(mode["errorCount"]),
        "authoritativeCount": int(mode["authoritativeCount"]),
        "stablePrefixConflictCount": int(mode["stablePrefixConflictCount"]),
        "charDistance": int(mode["charDistance"]),
        "referenceChars": denominator,
        "microCer": round(float(mode["charDistance"]) / denominator, 8) if denominator else None,
        "entityMatchCount": int(mode["entityMatchCount"]),
        "entityOccurrenceCount": entity_total,
        "entityAccuracy": round(float(mode["entityMatchCount"]) / entity_total, 8) if entity_total else None,
        "latencyP50Ms": _percentile(mode["latencies"], 0.5),
        "latencyP95Ms": _percentile(mode["latencies"], 0.95),
        "firstPartialCount": len(mode["firstPartials"]),
        "firstPartialP95Ms": _percentile(mode["firstPartials"], 0.95),
        "revisionMin": min(revisions, default=None),
        "revisionMax": max(revisions, default=None),
    }


def _negative_snapshot(mode: dict[str, Any]) -> dict[str, Any]:
    return {
        "attemptCount": int(mode["attemptCount"]),
        "responseCount": int(mode["responseCount"]),
        "malformedCount": int(mode["malformedCount"]),
        "errorCount": int(mode["errorCount"]),
        "acceptedTurnCount": int(mode["acceptedTurnCount"]),
        "unauthorizedHighImpactCandidateCount": int(
            mode["unauthorizedHighImpactCandidateCount"]
        ),
        "latencyP50Ms": _percentile(mode["latencies"], 0.5),
        "latencyP95Ms": _percentile(mode["latencies"], 0.95),
    }


def _aggregates_snapshot(aggregates: dict[str, Any]) -> dict[str, Any]:
    return {
        "processedPositive": int(aggregates["processedPositive"]),
        "processedNegative": int(aggregates["processedNegative"]),
        "positive": {
            "batch": _mode_snapshot(aggregates["positiveBatch"]),
            "stream": _mode_snapshot(aggregates["positiveStream"]),
        },
        "negative": {
            "batch": _negative_snapshot(aggregates["negativeBatch"]),
            "stream": _negative_snapshot(aggregates["negativeStream"]),
        },
        "cancelSuccessor": dict(aggregates["cancelSuccessor"]),
        "failureCounts": {
            code: int(aggregates["failureCounts"][code]) for code in FAILURE_CODES
        },
    }


def _record_positive(
    mode: dict[str, Any],
    *,
    reference: str,
    entities: tuple[str, ...],
    text: str,
    latency_ms: float,
    stream: StreamObservation | None = None,
) -> None:
    mode["latencies"].append(float(latency_ms))
    reference_occurrences = sum(
        _entity_occurrence_count(entity, reference) for entity in entities
    )
    mode["entityOccurrenceCount"] += reference_occurrences
    if stream is not None:
        mode["revisions"].append(stream.revision_count)
        if stream.first_partial_ms is not None:
            mode["firstPartials"].append(stream.first_partial_ms)
        mode["authoritativeCount"] += int(stream.authoritative)
        mode["stablePrefixConflictCount"] += int(
            stream.fallback_reason == "stable_prefix_conflict"
        )
    if not text:
        mode["emptyCount"] += 1
        return
    if stream is not None and not stream.authoritative:
        return
    score = score_transcript(reference, text)
    mode["usableCount"] += 1
    mode["charDistance"] += int(score["char_distance"])
    mode["referenceChars"] += int(score["char_reference_len"])
    mode["entityMatchCount"] += sum(
        min(
            _entity_occurrence_count(entity, reference),
            _entity_occurrence_count(entity, text),
        )
        for entity in entities
    )


def _record_negative(mode: dict[str, Any], *, text: str, latency_ms: float) -> None:
    mode["responseCount"] += 1
    mode["latencies"].append(float(latency_ms))
    accepted, forward_text = split_exact_leading_wake(text)
    high_impact = bool(accepted and local_voice_requires_fresh_wake(forward_text))
    mode["acceptedTurnCount"] += int(accepted)
    mode["unauthorizedHighImpactCandidateCount"] += int(high_impact)


async def _process_item(
    binding: AudioBinding,
    *,
    config: BenchmarkConfig,
    clients: ClientFunctions,
    aggregates: dict[str, Any],
) -> None:
    audio = load_audio(binding)
    if binding.kind == "positive":
        aggregates["processedPositive"] += 1
        batch_mode = aggregates["positiveBatch"]
        batch_mode["attemptCount"] += 1
        try:
            batch_text, batch_ms = _batch_observation(audio, config=config, batch=clients.batch)
        except ContractError as exc:
            code = str(exc)
            batch_mode["malformedCount"] += int(code == "batch_response_invalid")
            batch_mode["errorCount"] += 1
            _record_failure(aggregates, code)
        except Exception:
            batch_mode["errorCount"] += 1
            _record_failure(aggregates, "batch_request_failed")
        else:
            _record_positive(
                batch_mode,
                reference=binding.reference,
                entities=binding.entities,
                text=batch_text,
                latency_ms=batch_ms,
            )
            if not batch_text:
                _record_failure(aggregates, "batch_empty_positive")

        stream_mode = aggregates["positiveStream"]
        stream_mode["attemptCount"] += 1
        try:
            stream = await _stream_observation(audio, config=config, clients=clients)
        except ContractError as exc:
            code = str(exc)
            stream_mode["malformedCount"] += int(code == "stream_response_invalid")
            stream_mode["errorCount"] += 1
            _record_failure(aggregates, code)
        except Exception:
            stream_mode["errorCount"] += 1
            _record_failure(aggregates, "stream_request_failed")
        else:
            _record_positive(
                stream_mode,
                reference=binding.reference,
                entities=binding.entities,
                text=stream.text,
                latency_ms=stream.latency_ms,
                stream=stream,
            )
            if stream.fallback_reason == "stable_prefix_conflict":
                _record_failure(aggregates, "stream_stable_prefix_conflict")
            elif not stream.text:
                _record_failure(aggregates, "stream_empty_positive")
            elif not stream.authoritative:
                _record_failure(aggregates, "stream_non_authoritative")
        return

    aggregates["processedNegative"] += 1
    batch_mode = aggregates["negativeBatch"]
    batch_mode["attemptCount"] += 1
    try:
        batch_text, batch_ms = _batch_observation(audio, config=config, batch=clients.batch)
    except ContractError as exc:
        code = str(exc)
        batch_mode["malformedCount"] += int(code == "batch_response_invalid")
        batch_mode["errorCount"] += 1
        _record_failure(aggregates, code)
    except Exception:
        batch_mode["errorCount"] += 1
        _record_failure(aggregates, "batch_request_failed")
    else:
        _record_negative(batch_mode, text=batch_text, latency_ms=batch_ms)

    stream_mode = aggregates["negativeStream"]
    stream_mode["attemptCount"] += 1
    try:
        stream = await _stream_observation(audio, config=config, clients=clients)
    except ContractError as exc:
        code = str(exc)
        stream_mode["malformedCount"] += int(code == "stream_response_invalid")
        stream_mode["errorCount"] += 1
        _record_failure(aggregates, code)
    except Exception:
        stream_mode["errorCount"] += 1
        _record_failure(aggregates, "stream_request_failed")
    else:
        if stream.fallback_reason == "stable_prefix_conflict":
            stream_mode["errorCount"] += 1
            _record_failure(aggregates, "stream_stable_prefix_conflict")
        elif stream.text and not stream.authoritative:
            stream_mode["errorCount"] += 1
            _record_failure(aggregates, "stream_non_authoritative")
        else:
            _record_negative(stream_mode, text=stream.text, latency_ms=stream.latency_ms)


class _CancellationPush:
    def __init__(self, delegate: Callable[..., dict[str, Any]]) -> None:
        self.delegate = delegate
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, pcm16: Any, **kwargs: Any) -> dict[str, Any]:
        self.entered.set()
        self.release.wait()
        return self.delegate(pcm16, **kwargs)


class _TrackingStart:
    def __init__(self, delegate: Callable[..., dict[str, Any]]) -> None:
        self.delegate = delegate
        self.stream_ids: list[str | None] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        response = self.delegate(**kwargs)
        stream_id = response.get("streamId") if isinstance(response, dict) else None
        self.stream_ids.append(stream_id if isinstance(stream_id, str) else None)
        return response


class _CountingCancel:
    def __init__(
        self,
        delegate: Callable[..., dict[str, Any]],
        tracking_start: _TrackingStart,
    ) -> None:
        self.delegate = delegate
        self.tracking_start = tracking_start
        self.count = 0
        self.success_count = 0
        self.target_first = False

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.count += 1
        first_id = self.tracking_start.stream_ids[0] if self.tracking_start.stream_ids else None
        self.target_first = bool(first_id and kwargs.get("stream_id") == first_id)
        response = self.delegate(**kwargs)
        if not isinstance(response, dict) or response.get("cancelled") is not True:
            raise _fixed_error("cancel_smoke_failed")
        self.success_count += 1
        return response


async def _cancel_successor_smoke(
    binding: AudioBinding,
    *,
    config: BenchmarkConfig,
    clients: ClientFunctions,
) -> dict[str, Any]:
    result = {
        "attempted": True,
        "startCount": 0,
        "streamIdsDistinct": False,
        "cancelTargetFirst": False,
        "cancellationObserved": False,
        "remoteCancelCount": 0,
        "remoteCancelSuccessCount": 0,
        "taskDrained": False,
        "pendingTaskCount": 0,
        "batchFallbackUsable": False,
        "successorAuthoritative": False,
        "successorRevisionCount": 0,
    }
    audio = load_audio(binding)
    tracking_start = _TrackingStart(clients.start)
    cancellation_push = _CancellationPush(clients.push)
    counting_cancel = _CountingCancel(clients.cancel, tracking_start)
    task = asyncio.create_task(
        _stream_observation(
            audio,
            config=config,
            clients=clients,
            start=tracking_start,
            push=cancellation_push,
            cancel=counting_cancel,
        ),
        name="voice-asr-cancel-smoke",
    )
    try:
        entered = await asyncio.to_thread(
            cancellation_push.entered.wait,
            min(max(1.0, config.timeout_sec), 10.0),
        )
        if not entered:
            raise _fixed_error("cancel_smoke_failed")
        task.cancel()
        await asyncio.sleep(0)
        cancellation_push.release.set()
        try:
            await task
        except asyncio.CancelledError:
            result["cancellationObserved"] = True
        result["taskDrained"] = task.done()
        result["pendingTaskCount"] = int(not task.done())
        result["remoteCancelCount"] = counting_cancel.count
        result["remoteCancelSuccessCount"] = counting_cancel.success_count

        fallback_text, _ = _batch_observation(audio, config=config, batch=clients.batch)
        result["batchFallbackUsable"] = bool(fallback_text)
        successor = await _stream_observation(
            audio,
            config=config,
            clients=clients,
            start=tracking_start,
        )
        result["successorAuthoritative"] = bool(successor.authoritative and successor.text)
        result["successorRevisionCount"] = successor.revision_count
    finally:
        cancellation_push.release.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        result["taskDrained"] = task.done()
        result["pendingTaskCount"] = int(not task.done())
        result["remoteCancelCount"] = counting_cancel.count
        result["remoteCancelSuccessCount"] = counting_cancel.success_count
        result["startCount"] = len(tracking_start.stream_ids)
        result["streamIdsDistinct"] = bool(
            len(tracking_start.stream_ids) == 2
            and all(tracking_start.stream_ids)
            and tracking_start.stream_ids[0] != tracking_start.stream_ids[1]
        )
        result["cancelTargetFirst"] = counting_cancel.target_first
    return result


def evaluate(aggregates: dict[str, Any]) -> dict[str, Any]:
    snapshot = _aggregates_snapshot(aggregates)
    positive_batch = snapshot["positive"]["batch"]
    positive_stream = snapshot["positive"]["stream"]
    negative_batch = snapshot["negative"]["batch"]
    negative_stream = snapshot["negative"]["stream"]
    smoke = snapshot["cancelSuccessor"]
    gates = {
        "positiveBatchUsable40": positive_batch["usableCount"] == 40,
        "positiveStreamUsable40": positive_stream["usableCount"] == 40,
        "streamCerNotWorse": (
            isinstance(positive_batch["microCer"], (int, float))
            and isinstance(positive_stream["microCer"], (int, float))
            and positive_stream["microCer"] <= positive_batch["microCer"]
        ),
        "entityAccuracy95": (
            isinstance(positive_batch["entityAccuracy"], (int, float))
            and isinstance(positive_stream["entityAccuracy"], (int, float))
            and positive_batch["entityAccuracy"] >= 0.95
            and positive_stream["entityAccuracy"] >= 0.95
        ),
        "streamConflictMalformedErrorZero": (
            positive_stream["stablePrefixConflictCount"] == 0
            and positive_stream["malformedCount"] == 0
            and positive_stream["errorCount"] == 0
        ),
        "negativeResponses20": (
            negative_batch["responseCount"] == 10
            and negative_stream["responseCount"] == 10
        ),
        "negativeAcceptedZero": (
            negative_batch["acceptedTurnCount"] == 0
            and negative_stream["acceptedTurnCount"] == 0
        ),
        "negativeHighImpactZero": (
            negative_batch["unauthorizedHighImpactCandidateCount"] == 0
            and negative_stream["unauthorizedHighImpactCandidateCount"] == 0
        ),
        "negativeMalformedErrorZero": (
            negative_batch["malformedCount"] == 0
            and negative_batch["errorCount"] == 0
            and negative_stream["malformedCount"] == 0
            and negative_stream["errorCount"] == 0
        ),
        "cancelSuccessor": (
            smoke["attempted"] is True
            and smoke["startCount"] == 2
            and smoke["streamIdsDistinct"] is True
            and smoke["cancelTargetFirst"] is True
            and smoke["cancellationObserved"] is True
            and smoke["remoteCancelCount"] == 1
            and smoke["remoteCancelSuccessCount"] == 1
            and smoke["taskDrained"] is True
            and smoke["pendingTaskCount"] == 0
            and smoke["batchFallbackUsable"] is True
            and smoke["successorAuthoritative"] is True
        ),
        "fixedFailuresZero": sum(snapshot["failureCounts"].values()) == 0,
    }
    return {"passed": all(gates.values()), "gates": gates}


def _validate_report_target(path: Path, *, validation_root: Path, private_root: Path) -> Path:
    requested_root = Path(validation_root).absolute()
    requested_root.mkdir(parents=True, exist_ok=True)
    root_metadata = requested_root.lstat()
    if _is_reparse(root_metadata) or not stat.S_ISDIR(root_metadata.st_mode):
        raise _fixed_error("report_target_invalid")
    root = requested_root.resolve(strict=True)
    target = Path(path).resolve(strict=False)
    private_boundary = Path(private_root).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise _fixed_error("report_target_invalid") from exc
    if (
        target.suffix != ".json"
        or target == private_boundary
        or private_boundary in target.parents
    ):
        raise _fixed_error("report_target_invalid")
    current = target.parent
    while current != root:
        if current.exists():
            metadata = current.lstat()
            if _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
                raise _fixed_error("report_target_invalid")
        current = current.parent
    if target.exists():
        metadata = target.lstat()
        if _is_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise _fixed_error("report_target_invalid")
    return target


def _write_report(path: Path, report: dict[str, Any]) -> None:
    atomic_json_write(path, report, durable=True)


def _report(
    config: BenchmarkConfig,
    aggregates: dict[str, Any],
    *,
    started_epoch_sec: float,
    phase: str,
    status: str,
    corpus: Corpus | None,
    evaluation: dict[str, Any] | None = None,
    cleanup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "status": status,
        "phase": phase,
        "startedAtEpochSec": round(float(started_epoch_sec), 6),
        "identity": {
            "attemptId": config.attempt_id,
            "sourceCommit": config.source_commit,
            "imageSha256": config.image_sha256,
            "gpuUuid": config.gpu_uuid,
            "model": P0_4_STT_MODEL,
            "backend": P0_4_STT_BACKEND,
            "memoryUtilization": P0_4_STT_MEMORY_UTILIZATION,
            "expectedManifestSha256": config.expected_manifest_sha256,
            "manifestSha256": corpus.manifest_sha256 if corpus is not None else None,
        },
        "configuration": {
            "positiveExpected": 40,
            "negativeExpected": 10,
            "sampleRate": SAMPLE_RATE,
            "chunkSamples": CHUNK_SAMPLES,
            "chunkIntervalMs": int(CHUNK_INTERVAL_SEC * 1000),
            "timeoutMs": round(config.timeout_sec * 1000.0, 3),
            "retainPrivateAudio": bool(config.retain_private_audio),
            "manifestEntityOccurrenceCount": (
                corpus.entity_occurrences if corpus is not None else 0
            ),
        },
        "aggregates": _aggregates_snapshot(aggregates),
        "evaluation": evaluation or {"passed": False, "gates": {}},
        "cleanup": cleanup
        or {
            "retainedByRequest": bool(config.retain_private_audio),
            "expectedCount": len(corpus.items) + 1 if corpus is not None else 0,
            "deletedCount": 0,
            "failureCount": 0,
            "quarantined": False,
            "quarantineRemoved": False,
        },
    }


def _quarantine_path(corpus: Corpus, config: BenchmarkConfig) -> Path:
    attempt_digest = hashlib.sha256(config.attempt_id.encode("ascii")).hexdigest()[:16]
    return corpus.root.parent / (
        f".voice_asr-quarantine-{attempt_digest}-{corpus.manifest_sha256[:16]}"
    )


def quarantine_bound_corpus(
    corpus: Corpus,
    config: BenchmarkConfig,
    *,
    validation_root: Path,
) -> Path:
    parent = corpus.root.parent
    if (
        parent != Path(validation_root).resolve(strict=True)
        or corpus.root.name != PRIVATE_ROOT.name
    ):
        raise _fixed_error("cleanup_failed")
    parent_metadata = parent.lstat()
    if _is_reparse(parent_metadata) or not stat.S_ISDIR(parent_metadata.st_mode):
        raise _fixed_error("cleanup_failed")
    quarantine = _quarantine_path(corpus, config)
    try:
        quarantine.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise _fixed_error("cleanup_failed") from exc
    else:
        raise _fixed_error("cleanup_failed")

    allowed = {corpus.manifest_path, *(binding.path for binding in corpus.items)}
    try:
        root_metadata = corpus.root.lstat()
        if (
            _is_reparse(root_metadata)
            or not stat.S_ISDIR(root_metadata.st_mode)
            or _file_identity(root_metadata) != corpus.root_identity
        ):
            raise _fixed_error("cleanup_failed")
        if _private_regular_files(corpus.root) != allowed:
            raise _fixed_error("private_file_set_changed")
        for binding in corpus.items:
            metadata = _regular_metadata(binding.path)
            if (
                _file_identity(metadata) != binding.file_identity
                or _sha256_file(binding.path) != binding.sha256
            ):
                raise _fixed_error("audio_binding_changed")
        manifest_metadata = _regular_metadata(corpus.manifest_path)
        if (
            _file_identity(manifest_metadata) != corpus.manifest_identity
            or _sha256_file(corpus.manifest_path) != corpus.manifest_sha256
        ):
            raise _fixed_error("audio_binding_changed")
    except ContractError as exc:
        raise _fixed_error("cleanup_failed") from exc

    try:
        os.rename(corpus.root, quarantine)
        moved_metadata = quarantine.lstat()
        if (
            corpus.root.exists()
            or corpus.root.is_symlink()
            or _is_reparse(moved_metadata)
            or not stat.S_ISDIR(moved_metadata.st_mode)
            or _file_identity(moved_metadata) != corpus.root_identity
        ):
            raise _fixed_error("cleanup_failed")
    except ContractError:
        raise
    except OSError as exc:
        raise _fixed_error("cleanup_failed") from exc
    return quarantine


def cleanup_quarantined_corpus(corpus: Corpus, quarantine: Path) -> int:
    expected_quarantine = quarantine.absolute()
    if (
        expected_quarantine.parent != corpus.root.parent
        or corpus.root.exists()
        or corpus.root.is_symlink()
    ):
        raise _fixed_error("cleanup_failed")
    try:
        quarantine_metadata = expected_quarantine.lstat()
        if (
            _is_reparse(quarantine_metadata)
            or not stat.S_ISDIR(quarantine_metadata.st_mode)
            or _file_identity(quarantine_metadata) != corpus.root_identity
        ):
            raise _fixed_error("cleanup_failed")
    except ContractError:
        raise
    except OSError as exc:
        raise _fixed_error("cleanup_failed") from exc

    moved_bindings = tuple(
        (binding, expected_quarantine / binding.path.relative_to(corpus.root))
        for binding in corpus.items
    )
    moved_manifest = expected_quarantine / corpus.manifest_path.relative_to(corpus.root)
    allowed = {moved_manifest, *(path for _binding, path in moved_bindings)}
    try:
        if _private_regular_files(expected_quarantine) != {
            path.resolve(strict=True) for path in allowed
        }:
            raise _fixed_error("cleanup_failed")
        for binding, path in moved_bindings:
            metadata = _regular_metadata(path)
            if (
                _file_identity(metadata) != binding.file_identity
                or _sha256_file(path) != binding.sha256
            ):
                raise _fixed_error("cleanup_failed")
        manifest_metadata = _regular_metadata(moved_manifest)
        if (
            _file_identity(manifest_metadata) != corpus.manifest_identity
            or _sha256_file(moved_manifest) != corpus.manifest_sha256
        ):
            raise _fixed_error("cleanup_failed")
    except ContractError:
        raise

    deleted = 0
    for binding, path in moved_bindings:
        try:
            metadata = _regular_metadata(path)
            if (
                _file_identity(metadata) != binding.file_identity
                or _sha256_file(path) != binding.sha256
            ):
                raise _fixed_error("cleanup_failed")
            path.unlink()
        except ContractError:
            raise
        except OSError as exc:
            raise _fixed_error("cleanup_failed") from exc
        deleted += 1
        if path.exists() or path.is_symlink():
            raise _fixed_error("cleanup_failed")
    try:
        manifest_metadata = _regular_metadata(moved_manifest)
        if (
            _file_identity(manifest_metadata) != corpus.manifest_identity
            or _sha256_file(moved_manifest) != corpus.manifest_sha256
        ):
            raise _fixed_error("cleanup_failed")
        moved_manifest.unlink()
    except ContractError:
        raise
    except OSError as exc:
        raise _fixed_error("cleanup_failed") from exc
    deleted += 1
    if (
        moved_manifest.exists()
        or moved_manifest.is_symlink()
        or _private_regular_files(expected_quarantine)
        or deleted != len(corpus.items) + 1
    ):
        raise _fixed_error("cleanup_failed")
    try:
        expected_quarantine.rmdir()
    except OSError as exc:
        raise _fixed_error("cleanup_failed") from exc
    return deleted


async def run_benchmark(
    config: BenchmarkConfig,
    *,
    private_root: Path = PRIVATE_ROOT,
    report_path: Path = DEFAULT_REPORT,
    validation_root: Path = VALIDATION_ROOT,
    clients: ClientFunctions | None = None,
) -> dict[str, Any]:
    client_functions = clients or ClientFunctions()
    aggregates = _new_aggregates()
    started_epoch_sec = time.time()
    safe_private_root = Path(private_root).absolute()
    target = _validate_report_target(
        report_path,
        validation_root=Path(validation_root),
        private_root=safe_private_root,
    )
    _write_report(
        target,
        _report(
            config,
            aggregates,
            started_epoch_sec=started_epoch_sec,
            phase="running",
            status="running",
            corpus=None,
        ),
    )
    try:
        corpus = load_corpus_manifest(
            safe_private_root,
            expected_manifest_sha256=config.expected_manifest_sha256,
        )
    except Exception:
        _record_failure(aggregates, "manifest_invalid")
        terminal = _report(
            config,
            aggregates,
            started_epoch_sec=started_epoch_sec,
            phase="terminal",
            status="fail",
            corpus=None,
        )
        _write_report(target, terminal)
        return terminal

    _write_report(
        target,
        _report(
            config,
            aggregates,
            started_epoch_sec=started_epoch_sec,
            phase="preflight",
            status="running",
            corpus=corpus,
        ),
    )
    try:
        for binding in corpus.items:
            await _process_item(
                binding,
                config=config,
                clients=client_functions,
                aggregates=aggregates,
            )
            _write_report(
                target,
                _report(
                    config,
                    aggregates,
                    started_epoch_sec=started_epoch_sec,
                    phase="corpus",
                    status="running",
                    corpus=corpus,
                ),
            )
        positive = next(item for item in corpus.items if item.kind == "positive")
        try:
            aggregates["cancelSuccessor"] = await _cancel_successor_smoke(
                positive,
                config=config,
                clients=client_functions,
            )
        except Exception:
            _record_failure(aggregates, "cancel_smoke_failed")
        smoke = aggregates["cancelSuccessor"]
        if not (
            smoke["cancellationObserved"]
            and smoke["remoteCancelCount"] == 1
            and smoke["remoteCancelSuccessCount"] == 1
            and smoke["taskDrained"]
            and smoke["pendingTaskCount"] == 0
            and smoke["batchFallbackUsable"]
            and smoke["successorAuthoritative"]
        ):
            if aggregates["failureCounts"]["cancel_smoke_failed"] == 0:
                _record_failure(aggregates, "cancel_smoke_failed")
    except Exception:
        _record_failure(aggregates, "internal_failure")

    evaluation = evaluate(aggregates)
    evaluation_report = _report(
        config,
        aggregates,
        started_epoch_sec=started_epoch_sec,
        phase="evaluation",
        status="running",
        corpus=corpus,
        evaluation=evaluation,
    )
    # The durable evaluation is the deletion authority. Never clean first.
    _write_report(target, evaluation_report)

    cleanup = {
        "retainedByRequest": bool(config.retain_private_audio),
        "expectedCount": len(corpus.items) + 1,
        "deletedCount": 0,
        "failureCount": 0,
        "quarantined": False,
        "quarantineRemoved": False,
    }
    if not config.retain_private_audio:
        try:
            quarantine = quarantine_bound_corpus(
                corpus,
                config,
                validation_root=Path(validation_root),
            )
            cleanup["quarantined"] = True
            cleanup_evaluation = {
                "passed": False,
                "gates": {**evaluation["gates"], "cleanup": False},
            }
            _write_report(
                target,
                _report(
                    config,
                    aggregates,
                    started_epoch_sec=started_epoch_sec,
                    phase="cleanup",
                    status="running",
                    corpus=corpus,
                    evaluation=cleanup_evaluation,
                    cleanup=cleanup,
                ),
            )
            cleanup["deletedCount"] = cleanup_quarantined_corpus(
                corpus,
                quarantine,
            )
            cleanup["quarantineRemoved"] = not quarantine.exists()
        except Exception:
            cleanup["failureCount"] = 1
            _record_failure(aggregates, "cleanup_failed")
    cleanup_passed = bool(
        config.retain_private_audio
        or (
            cleanup["failureCount"] == 0
            and cleanup["deletedCount"] == cleanup["expectedCount"]
            and cleanup["quarantined"] is True
            and cleanup["quarantineRemoved"] is True
        )
    )
    final_evaluation = evaluate(aggregates)
    final_evaluation["gates"]["cleanup"] = cleanup_passed
    final_evaluation["passed"] = bool(
        final_evaluation["passed"] and cleanup_passed
    )
    terminal = _report(
        config,
        aggregates,
        started_epoch_sec=started_epoch_sec,
        phase="terminal",
        status="pass" if final_evaluation["passed"] else "fail",
        corpus=corpus,
        evaluation=final_evaluation,
        cleanup=cleanup,
    )
    _write_report(target, terminal)
    return terminal


def _normalize_image_sha256(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized.startswith("sha256:"):
        normalized = normalized[7:]
    return normalized


def _validate_service_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError("stt-url must be a loopback HTTP service root")
    return f"http://{parsed.hostname}:{parsed.port}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay the fixed private ASR corpus without capture or route effects."
    )
    parser.add_argument("--attempt", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--image-sha256", required=True)
    parser.add_argument("--gpu-uuid", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--stt-url", type=_validate_service_url, default=DEFAULT_STT_URL)
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    parser.add_argument("--retain-private-audio", action="store_true")
    args = parser.parse_args(argv)
    args.image_sha256 = _normalize_image_sha256(args.image_sha256)
    if _ATTEMPT_ID.fullmatch(args.attempt) is None:
        parser.error("attempt must be a bounded identifier")
    if _SOURCE_COMMIT.fullmatch(args.source_commit) is None:
        parser.error("source-commit must be 40 lowercase hex characters")
    if _SHA256.fullmatch(args.image_sha256) is None:
        parser.error("image-sha256 must be 64 lowercase hex characters")
    if _GPU_UUID.fullmatch(args.gpu_uuid) is None:
        parser.error("gpu-uuid must be an NVIDIA GPU UUID")
    if _SHA256.fullmatch(args.manifest_sha256) is None:
        parser.error("manifest-sha256 must be 64 lowercase hex characters")
    if args.stt_url != DEFAULT_STT_URL or args.timeout_sec != 60.0:
        parser.error("P0-4 requires the fixed STT endpoint and 60-second timeout")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = BenchmarkConfig(
        attempt_id=args.attempt,
        source_commit=args.source_commit,
        image_sha256=args.image_sha256,
        gpu_uuid=args.gpu_uuid,
        expected_manifest_sha256=args.manifest_sha256,
        stt_url=args.stt_url,
        timeout_sec=args.timeout_sec,
        retain_private_audio=args.retain_private_audio,
    )
    report = asyncio.run(run_benchmark(config))
    print(
        json.dumps(
            {
                "schema": REPORT_SCHEMA,
                "status": report["status"],
                "positive": report["aggregates"]["processedPositive"],
                "negative": report["aggregates"]["processedNegative"],
            },
            separators=(",", ":"),
        ),
        flush=True,
    )
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
