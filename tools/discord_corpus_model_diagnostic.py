from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import stat
import sys
import wave
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib import request
from urllib.parse import urlsplit

import numpy as np


REPO_ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / "main.py").exists()
)
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
for import_root in (REPO_ROOT, RUNTIME_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from tools.discord_voice_corpus_capture import DOMAIN_PHRASES  # noqa: E402
from tools.ko_stt_scoreboard import normalize_korean_text  # noqa: E402
from evelyn_core.runtime_artifact_io import atomic_json_write  # noqa: E402
from evelyn_core.stt_client import transcribe_audio16k_via_service  # noqa: E402
from evelyn_core.voice_validation import transcript_match  # noqa: E402


REPORT_SCHEMA = "evelyn.discord-corpus-model-diagnostic.v1"
STT_MODEL = "Qwen/Qwen3-ASR-1.7B"
STT_BACKEND = "vllm"
STT_ENGINE = {
    "maxModelLen": 8192,
    "gpuMemoryUtilization": 0.35,
    "maxNumSeqs": 1,
    "audioPerPrompt": 1,
}
SAMPLE_RATE = 16_000
MAX_AUDIO_SAMPLES = SAMPLE_RATE * 30
MAX_WAV_BYTES = MAX_AUDIO_SAMPLES * 2 + 64 * 1024
MAX_RESPONSE_TEXT_CHARS = 10_000
MAX_HEALTH_BYTES = 128 * 1024
MATCH_THRESHOLD = 0.70
STAGING_MARKER_NAME = ".evelyn-owned-discord-capture.json"
STAGING_MARKER_SCHEMA = "evelyn.discord-capture-staging.v1"
STAGING_OWNER = "evelyn.discord-capture-lab.v1"
MAX_MARKER_BYTES = 16 * 1024
CAPTURE_TOOL_SHA256 = hashlib.sha256(
    (REPO_ROOT / "tools" / "discord_voice_corpus_capture.py").read_bytes()
).hexdigest()
CRITICAL_ENTITIES = (
    ("이블린", "다이아몬드 곡괭이", "찾아줘"),
    ("이블린", "참나무 원목", "열두 개", "모아줘"),
    ("이블린", "제작대", "빵 세 개", "만들어줘"),
    ("이블린", "크리퍼", "스켈레톤", "피해줘"),
    ("이블린", "control page", "상태", "확인해줘"),
    ("이블린", "discord", "음성 연결", "다시 확인해줘"),
    ("이블린", "main llm", "qwen asr", "상태", "알려줘"),
    ("이블린", "gpu 일 번", "vram", "확인해줘"),
    ("이블린", "마인크래프트 voyager", "상태만", "보여줘"),
    ("이블린", "오후 세 시 이십오 분", "열두 개", "세어줘"),
)
FAILURE_CODES = frozenset(
    {
        "invalid_arguments",
        "corpus_invalid",
        "stt_health_pre_invalid",
        "stt_request_failed",
        "stt_response_invalid",
        "stt_health_post_invalid",
        "model_diagnostic_failed",
        "output_write_failed",
        "internal_failure",
    }
)


class DiagnosticFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        safe_code = code if code in FAILURE_CODES else "internal_failure"
        super().__init__(safe_code)
        self.code = safe_code


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise DiagnosticFailure("invalid_arguments")


def _empty_report() -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "status": "fail",
        "failureCode": None,
        "counts": {
            "expectedWavCount": len(DOMAIN_PHRASES),
            "validWavCount": 0,
            "batchAttemptCount": 0,
            "responseCount": 0,
            "nonemptyCount": 0,
            "matchedCount": 0,
            "normalizedExactCount": 0,
            "criticalEntityExactCount": 0,
            "sameIndexStrictUniqueBestCount": 0,
            "errorCount": 0,
        },
        "health": {"pre": False, "post": False},
        "gates": {
            "canonicalExact10": False,
            "preHealthExact": False,
            "postHealthExact": False,
            "batchExactlyOnce10": False,
            "nonempty10": False,
            "matched10": False,
            "normalizedExact10": False,
            "criticalEntityExact10": False,
            "sameIndexStrictUniqueBest10": False,
            "errorsZero": True,
        },
    }


def _finish_report(report: dict[str, Any], failure_code: str | None) -> dict[str, Any]:
    counts = report["counts"]
    health = report["health"]
    gates = {
        "canonicalExact10": counts["validWavCount"] == len(DOMAIN_PHRASES),
        "preHealthExact": health["pre"] is True,
        "postHealthExact": health["post"] is True,
        "batchExactlyOnce10": counts["batchAttemptCount"] == len(DOMAIN_PHRASES),
        "nonempty10": counts["nonemptyCount"] == len(DOMAIN_PHRASES),
        "matched10": counts["matchedCount"] == len(DOMAIN_PHRASES),
        "normalizedExact10": counts["normalizedExactCount"] == len(DOMAIN_PHRASES),
        "criticalEntityExact10": (
            counts["criticalEntityExactCount"] == len(DOMAIN_PHRASES)
        ),
        "sameIndexStrictUniqueBest10": (
            counts["sameIndexStrictUniqueBestCount"] == len(DOMAIN_PHRASES)
        ),
        "errorsZero": counts["errorCount"] == 0,
    }
    passed = all(gates.values()) and failure_code is None
    report["status"] = "pass" if passed else "fail"
    report["failureCode"] = None if passed else (
        failure_code or "model_diagnostic_failed"
    )
    report["gates"] = gates
    return report


def _is_reparse(metadata: os.stat_result) -> bool:
    attributes = int(getattr(metadata, "st_file_attributes", 0) or 0)
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _read_stable_regular(path: Path, *, max_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
        if _is_reparse(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise DiagnosticFailure("corpus_invalid")
        if not 0 < metadata.st_size <= max_bytes:
            raise DiagnosticFailure("corpus_invalid")
        with path.open("rb") as source:
            raw = source.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise DiagnosticFailure("corpus_invalid")
        after = path.lstat()
        if (
            _is_reparse(after)
            or not stat.S_ISREG(after.st_mode)
            or (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise DiagnosticFailure("corpus_invalid")
        return raw
    except DiagnosticFailure:
        raise
    except OSError as exc:
        raise DiagnosticFailure("corpus_invalid") from exc


def _read_canonical_wav(path: Path) -> tuple[np.ndarray, str]:
    try:
        raw = _read_stable_regular(path, max_bytes=MAX_WAV_BYTES)
        with wave.open(io.BytesIO(raw), "rb") as wav:
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or wav.getframerate() != SAMPLE_RATE
                or wav.getcomptype() != "NONE"
            ):
                raise DiagnosticFailure("corpus_invalid")
            sample_count = int(wav.getnframes())
            if not 0 < sample_count <= MAX_AUDIO_SAMPLES:
                raise DiagnosticFailure("corpus_invalid")
            frames = wav.readframes(sample_count)
            if len(frames) != sample_count * 2:
                raise DiagnosticFailure("corpus_invalid")
    except DiagnosticFailure:
        raise
    except (OSError, EOFError, wave.Error) as exc:
        raise DiagnosticFailure("corpus_invalid") from exc
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    return audio, hashlib.sha256(raw).hexdigest()


def _marker_matches(marker: Any, audio_sha256: Sequence[str]) -> bool:
    if not isinstance(marker, Mapping):
        return False
    expected_keys = {
        "schema",
        "owner",
        "runId",
        "attemptId",
        "sourceRevision",
        "captureToolSha256",
        "itemCount",
        "audioSha256",
    }
    run_id = marker.get("runId")
    attempt_id = marker.get("attemptId")
    source_revision = marker.get("sourceRevision")
    hashes = marker.get("audioSha256")
    return bool(
        set(marker) == expected_keys
        and marker.get("schema") == STAGING_MARKER_SCHEMA
        and marker.get("owner") == STAGING_OWNER
        and isinstance(run_id, str)
        and len(run_id) == 32
        and all(character in "0123456789abcdef" for character in run_id)
        and isinstance(attempt_id, str)
        and 1 <= len(attempt_id) <= 64
        and all(character.isascii() and (character.isalnum() or character in "_.-") for character in attempt_id)
        and isinstance(source_revision, str)
        and len(source_revision) == 40
        and all(character in "0123456789abcdef" for character in source_revision)
        and marker.get("captureToolSha256") == CAPTURE_TOOL_SHA256
        and type(marker.get("itemCount")) is int
        and marker.get("itemCount") == len(DOMAIN_PHRASES)
        and isinstance(hashes, list)
        and hashes == list(audio_sha256)
        and len(set(hashes)) == len(DOMAIN_PHRASES)
    )


def load_canonical_corpus(corpus_dir: Path) -> tuple[np.ndarray, ...]:
    root = Path(corpus_dir)
    try:
        metadata = root.lstat()
        if _is_reparse(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise DiagnosticFailure("corpus_invalid")
        expected_names = tuple(
            f"clip-{index:04d}.wav" for index in range(1, len(DOMAIN_PHRASES) + 1)
        )
        entries = tuple(root.iterdir())
        if {entry.name for entry in entries} != set(expected_names) | {
            STAGING_MARKER_NAME
        }:
            raise DiagnosticFailure("corpus_invalid")
        by_name = {entry.name: entry for entry in entries}
        loaded = tuple(_read_canonical_wav(by_name[name]) for name in expected_names)
        try:
            marker = json.loads(
                _read_stable_regular(
                    by_name[STAGING_MARKER_NAME],
                    max_bytes=MAX_MARKER_BYTES,
                ).decode("utf-8")
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise DiagnosticFailure("corpus_invalid") from exc
        if not _marker_matches(marker, tuple(digest for _audio, digest in loaded)):
            raise DiagnosticFailure("corpus_invalid")
        marker = None
        return tuple(audio for audio, _digest in loaded)
    except DiagnosticFailure:
        raise
    except OSError as exc:
        raise DiagnosticFailure("corpus_invalid") from exc


def _health_exact(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    max_audio_sec = payload.get("maxAudioSec")
    error_count = payload.get("errorCount")
    engine = payload.get("engine")
    engine_exact = bool(
        isinstance(engine, Mapping)
        and set(engine) == set(STT_ENGINE)
        and type(engine.get("maxModelLen")) is int
        and engine.get("maxModelLen") == STT_ENGINE["maxModelLen"]
        and type(engine.get("gpuMemoryUtilization")) is float
        and math.isfinite(engine.get("gpuMemoryUtilization"))
        and engine.get("gpuMemoryUtilization")
        == STT_ENGINE["gpuMemoryUtilization"]
        and type(engine.get("maxNumSeqs")) is int
        and engine.get("maxNumSeqs") == STT_ENGINE["maxNumSeqs"]
        and type(engine.get("audioPerPrompt")) is int
        and engine.get("audioPerPrompt") == STT_ENGINE["audioPerPrompt"]
    )
    return bool(
        payload.get("ok") is True
        and payload.get("ready") is True
        and payload.get("model") == STT_MODEL
        and payload.get("backend") == STT_BACKEND
        and type(max_audio_sec) in {int, float}
        and math.isfinite(float(max_audio_sec))
        and float(max_audio_sec) == 30.0
        and engine_exact
        and type(error_count) is int
        and error_count == 0
    )


def fetch_health(*, service_url: str, timeout_sec: float) -> dict[str, Any]:
    req = request.Request(f"{service_url.rstrip('/')}/health", method="GET")
    try:
        with request.urlopen(req, timeout=max(1.0, float(timeout_sec))) as response:
            raw = response.read(MAX_HEALTH_BYTES + 1)
        if len(raw) > MAX_HEALTH_BYTES:
            raise DiagnosticFailure("internal_failure")
        payload = json.loads(raw.decode("utf-8"))
    except DiagnosticFailure:
        raise
    except Exception as exc:
        raise DiagnosticFailure("internal_failure") from exc
    if not isinstance(payload, dict):
        raise DiagnosticFailure("internal_failure")
    return payload


def _validate_service_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 8892
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise argparse.ArgumentTypeError("invalid")
    return f"http://{parsed.hostname}:{parsed.port}"


def _contains_exact_entity(text: str, entity: str) -> bool:
    expected = normalize_korean_text(entity, keep_spaces=False)
    actual = normalize_korean_text(text, keep_spaces=False)
    return bool(expected and expected in actual)


def _timeout_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("invalid") from None
    if not math.isfinite(parsed) or not 1.0 <= parsed <= 300.0:
        raise argparse.ArgumentTypeError("invalid")
    return parsed


def run_diagnostic(
    *,
    corpus_dir: Path,
    stt_url: str,
    timeout_sec: float = 60.0,
    health: Callable[..., dict[str, Any]] | None = None,
    batch: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    report = _empty_report()
    try:
        stt_url = _validate_service_url(stt_url)
    except (argparse.ArgumentTypeError, TypeError, ValueError):
        return _finish_report(report, "invalid_arguments")
    health_client = health or fetch_health
    batch_client = batch or transcribe_audio16k_via_service
    try:
        audio_items = load_canonical_corpus(corpus_dir)
    except DiagnosticFailure:
        return _finish_report(report, "corpus_invalid")
    report["counts"]["validWavCount"] = len(audio_items)

    try:
        report["health"]["pre"] = _health_exact(
            health_client(service_url=stt_url, timeout_sec=timeout_sec)
        )
    except Exception:
        report["health"]["pre"] = False
    if report["health"]["pre"] is not True:
        return _finish_report(report, "stt_health_pre_invalid")

    first_batch_failure: str | None = None
    for index, audio in enumerate(audio_items):
        report["counts"]["batchAttemptCount"] += 1
        try:
            response = batch_client(
                audio,
                service_url=stt_url,
                timeout_sec=timeout_sec,
                sampling_rate=SAMPLE_RATE,
                max_new_tokens=256,
                stage="validation-batch",
                language="Korean",
                validation_bound=True,
            )
        except Exception:
            report["counts"]["errorCount"] += 1
            first_batch_failure = first_batch_failure or "stt_request_failed"
            continue
        text = response.get("text") if isinstance(response, dict) else None
        if not isinstance(text, str) or len(text) > MAX_RESPONSE_TEXT_CHARS:
            report["counts"]["errorCount"] += 1
            first_batch_failure = first_batch_failure or "stt_response_invalid"
            text = None
            response = None
            continue
        report["counts"]["responseCount"] += 1
        text = text.strip()
        if text:
            report["counts"]["nonemptyCount"] += 1
            scores = tuple(
                transcript_match(
                    text,
                    phrase,
                    keywords=(),
                    threshold=MATCH_THRESHOLD,
                )
                for phrase in DOMAIN_PHRASES
            )
            if scores[index]["matched"] is True:
                report["counts"]["matchedCount"] += 1
            if normalize_korean_text(
                text,
                keep_spaces=False,
            ) == normalize_korean_text(DOMAIN_PHRASES[index], keep_spaces=False):
                report["counts"]["normalizedExactCount"] += 1
            if all(
                _contains_exact_entity(text, entity)
                for entity in CRITICAL_ENTITIES[index]
            ):
                report["counts"]["criticalEntityExactCount"] += 1
            own_similarity = float(scores[index]["similarity"])
            if all(
                own_similarity > float(score["similarity"])
                for other_index, score in enumerate(scores)
                if other_index != index
            ):
                report["counts"]["sameIndexStrictUniqueBestCount"] += 1
        text = None
        response = None

    try:
        report["health"]["post"] = _health_exact(
            health_client(service_url=stt_url, timeout_sec=timeout_sec)
        )
    except Exception:
        report["health"]["post"] = False
    if report["health"]["post"] is not True:
        return _finish_report(report, "stt_health_post_invalid")
    if first_batch_failure is not None:
        return _finish_report(report, first_batch_failure)
    return _finish_report(report, None)


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(
        description="Run an aggregate-only post-capture Discord corpus diagnostic."
    )
    parser.add_argument("--corpus-dir", required=True, type=Path)
    parser.add_argument("--stt-url", required=True, type=_validate_service_url)
    parser.add_argument("--timeout-seconds", type=_timeout_seconds, default=60.0)
    parser.add_argument("--output", type=Path)
    return parser


def _render(report: Mapping[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _output_is_separate_from_corpus(output: Path, corpus_dir: Path) -> bool:
    try:
        corpus = Path(corpus_dir).resolve(strict=True)
        target = Path(output).resolve(strict=False)
        target.relative_to(corpus)
    except ValueError:
        return True
    except OSError:
        return False
    return False


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.output is not None and not _output_is_separate_from_corpus(
            args.output, args.corpus_dir
        ):
            raise DiagnosticFailure("invalid_arguments")
        report = run_diagnostic(
            corpus_dir=args.corpus_dir,
            stt_url=args.stt_url,
            timeout_sec=args.timeout_seconds,
        )
        if args.output is not None:
            try:
                atomic_json_write(Path(args.output), report, attempts=1, durable=True)
            except Exception:
                report = _finish_report(report, "output_write_failed")
    except DiagnosticFailure as exc:
        report = _finish_report(_empty_report(), exc.code)
    except Exception:
        report = _finish_report(_empty_report(), "internal_failure")
    print(_render(report), flush=True)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
