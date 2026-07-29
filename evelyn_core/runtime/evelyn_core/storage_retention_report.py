from __future__ import annotations

import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .paths import get_repo_root, get_runtime_artifacts_root
from .runtime_artifact_io import atomic_json_write
from .runtime_artifacts_retention import (
    DEFAULT_RETENTION_RULES,
    RetentionRule,
    artifact_matches_rule,
    build_cleanup_plan,
    inventory_runtime_artifacts,
)


STORAGE_RETENTION_REPORT_SCHEMA = "storage_retention.report.v1"
DEFAULT_REPORT_INTERVAL_SEC = 6 * 60 * 60
DEFAULT_STALE_AFTER_SEC = 12 * 60 * 60
STORAGE_RETENTION_REPORT_RELATIVE_PATH = Path("retention") / "status.json"

HOST_LOG_RETENTION_RULES: tuple[RetentionRule, ...] = (
    RetentionRule(
        "host_logs",
        ("*.log*",),
        max_age_days=14,
        max_total_bytes=100 * 1024 * 1024,
        preserve_newest=1,
    ),
    RetentionRule(
        "host_turn_trace",
        ("turn_trace/*.jsonl",),
        max_age_days=30,
        max_total_bytes=100 * 1024 * 1024,
        preserve_newest=7,
    ),
)


def _nonnegative_float(value: Any, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(0.0, number) if math.isfinite(number) else fallback


def _configured_voice_debug_root(project_root: Path) -> Path:
    configured = Path(os.getenv("VOICE_DEBUG_AUDIO_DIR", "debug_audio"))
    return configured.resolve() if configured.is_absolute() else (project_root / configured).resolve()


def _reason_summary(plan: Any) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in plan.candidates:
        key = (str(candidate.rule), str(candidate.reason))
        item = grouped.setdefault(
            key,
            {
                "rule": key[0],
                "reason": key[1],
                "candidateCount": 0,
                "candidateBytes": 0,
            },
        )
        item["candidateCount"] += 1
        item["candidateBytes"] += int(candidate.size_bytes)
    return sorted(grouped.values(), key=lambda item: (item["rule"], item["reason"]))


def _cleanup_scope(
    scope_id: str,
    root: Path,
    *,
    rules: tuple[RetentionRule, ...],
    now: float,
) -> dict[str, Any]:
    if not root.exists():
        return {
            "id": scope_id,
            "state": "absent",
            "trackedCount": 0,
            "candidateCount": 0,
            "candidateBytes": 0,
            "reasons": [],
        }
    artifacts = inventory_runtime_artifacts(root)
    tracked = [
        artifact
        for artifact in artifacts
        if any(artifact_matches_rule(artifact, rule) for rule in rules)
    ]
    plan = build_cleanup_plan(root, rules=rules, now=now)
    return {
        "id": scope_id,
        "state": "attention" if plan.candidates else "clear",
        "trackedCount": len(tracked),
        "candidateCount": len(plan.candidates),
        "candidateBytes": plan.total_bytes,
        "reasons": _reason_summary(plan),
    }


def _voice_debug_scope(root: Path, *, now: float) -> dict[str, Any]:
    if not root.exists():
        return {
            "id": "voiceDebug",
            "state": "absent",
            "trackedCount": 0,
            "groupCount": 0,
            "candidateCount": 0,
            "candidateBytes": 0,
            "reasons": [],
        }
    from .voice_debug_audio import trim_voice_debug_root

    result = trim_voice_debug_root(
        root,
        max_files=max(1, int(os.getenv("VOICE_DEBUG_MAX_FILES_PER_GUILD", "200"))),
        max_age_days=_nonnegative_float(os.getenv("VOICE_DEBUG_MAX_AGE_DAYS"), 7.0),
        max_total_bytes_per_guild=max(
            1,
            int(os.getenv("VOICE_DEBUG_MAX_TOTAL_MB_PER_GUILD", "256")),
        )
        * 1024
        * 1024,
        preserve_newest=10,
        dry_run=True,
        now=now,
    )
    guilds = result.get("guilds") if isinstance(result.get("guilds"), dict) else {}
    candidate_count = int(result.get("candidate_count") or 0)
    return {
        "id": "voiceDebug",
        "state": "attention" if candidate_count else "clear",
        "trackedCount": sum(int(item.get("bundle_count") or 0) for item in guilds.values()),
        "groupCount": len(guilds),
        "candidateCount": candidate_count,
        "candidateBytes": int(result.get("candidate_bytes") or 0),
        "reasons": (
            [
                {
                    "rule": "voice_debug_bundle",
                    "reason": "age|count|size",
                    "candidateCount": candidate_count,
                    "candidateBytes": int(result.get("candidate_bytes") or 0),
                }
            ]
            if candidate_count
            else []
        ),
    }


def build_storage_retention_report(
    *,
    project_root: Path | None = None,
    artifacts_root: Path | None = None,
    now: float | None = None,
    interval_sec: float = DEFAULT_REPORT_INTERVAL_SEC,
) -> dict[str, Any]:
    project = Path(project_root or get_repo_root()).resolve()
    artifacts = Path(artifacts_root or get_runtime_artifacts_root()).resolve()
    generated_at = time.time() if now is None else float(now)
    scope_specs: tuple[tuple[str, Callable[[], dict[str, Any]]], ...] = (
        (
            "runtimeArtifacts",
            lambda: _cleanup_scope(
                "runtimeArtifacts",
                artifacts,
                rules=DEFAULT_RETENTION_RULES,
                now=generated_at,
            ),
        ),
        (
            "hostLogs",
            lambda: _cleanup_scope(
                "hostLogs",
                project / "logs",
                rules=HOST_LOG_RETENTION_RULES,
                now=generated_at,
            ),
        ),
        (
            "voiceDebug",
            lambda: _voice_debug_scope(
                _configured_voice_debug_root(project),
                now=generated_at,
            ),
        ),
    )
    scopes: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, str]] = []
    for scope_id, build_scope in scope_specs:
        try:
            scopes[scope_id] = build_scope()
        except Exception as exc:
            scopes[scope_id] = {
                "id": scope_id,
                "state": "error",
                "trackedCount": 0,
                "candidateCount": 0,
                "candidateBytes": 0,
                "reasons": [],
                "error": "scan_failed",
            }
            warnings.append(
                {
                    "scope": scope_id,
                    "code": "scan_failed",
                    "detail": type(exc).__name__,
                }
            )

    candidate_count = sum(int(scope.get("candidateCount") or 0) for scope in scopes.values())
    candidate_bytes = sum(int(scope.get("candidateBytes") or 0) for scope in scopes.values())
    error_count = sum(1 for scope in scopes.values() if scope.get("state") == "error")
    state = "error" if error_count else "attention" if candidate_count else "clear"
    interval = max(60.0, float(interval_sec))
    return {
        "schema": STORAGE_RETENTION_REPORT_SCHEMA,
        "state": state,
        "generatedAt": generated_at,
        "nextScanAt": generated_at + interval,
        "dryRun": True,
        "automaticDeletion": False,
        "summary": {
            "scopeCount": len(scopes),
            "errorCount": error_count,
            "candidateCount": candidate_count,
            "candidateBytes": candidate_bytes,
        },
        "scopes": scopes,
        "warnings": warnings,
    }


def storage_retention_report_path(
    artifacts_root: Path | None = None,
) -> Path:
    root = Path(artifacts_root or get_runtime_artifacts_root()).resolve()
    return root / STORAGE_RETENTION_REPORT_RELATIVE_PATH


def write_storage_retention_report(
    report: dict[str, Any],
    *,
    artifacts_root: Path | None = None,
) -> Path:
    path = storage_retention_report_path(artifacts_root)
    atomic_json_write(path, report)
    return path


def _public_scope(scope_id: str, raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    reasons = source.get("reasons") if isinstance(source.get("reasons"), list) else []
    public_reasons = []
    for reason in reasons:
        if not isinstance(reason, dict):
            continue
        public_reasons.append(
            {
                "rule": str(reason.get("rule") or ""),
                "reason": str(reason.get("reason") or ""),
                "candidateCount": max(0, int(reason.get("candidateCount") or 0)),
                "candidateBytes": max(0, int(reason.get("candidateBytes") or 0)),
            }
        )
    state = str(source.get("state") or "unknown")
    if state not in {"clear", "attention", "absent", "error", "unknown"}:
        state = "unknown"
    public = {
        "id": scope_id,
        "state": state,
        "trackedCount": max(0, int(source.get("trackedCount") or 0)),
        "candidateCount": max(0, int(source.get("candidateCount") or 0)),
        "candidateBytes": max(0, int(source.get("candidateBytes") or 0)),
        "reasons": public_reasons,
    }
    if scope_id == "voiceDebug":
        public["groupCount"] = max(0, int(source.get("groupCount") or 0))
    if state == "error":
        public["error"] = str(source.get("error") or "scan_failed")
    return public


def read_storage_retention_report(
    *,
    artifacts_root: Path | None = None,
    now: float | None = None,
    stale_after_sec: float = DEFAULT_STALE_AFTER_SEC,
) -> dict[str, Any]:
    policy = {
        "dryRunOnly": True,
        "automaticDeletion": False,
        "applyApiAvailable": False,
    }
    path = storage_retention_report_path(artifacts_root)
    if not path.exists():
        return {
            "ok": True,
            "available": False,
            "stale": False,
            "state": "unavailable",
            "error": "storage_retention_report_missing",
            "policy": policy,
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema") != STORAGE_RETENTION_REPORT_SCHEMA:
            raise ValueError("invalid_schema")
        generated_at = float(raw.get("generatedAt"))
        raw_scopes = raw.get("scopes") if isinstance(raw.get("scopes"), dict) else {}
        scopes = {
            scope_id: _public_scope(scope_id, raw_scopes.get(scope_id))
            for scope_id in ("runtimeArtifacts", "hostLogs", "voiceDebug")
        }
        generated_at = _nonnegative_float(generated_at, -1.0)
        if generated_at < 0:
            raise ValueError("invalid_generated_at")
        candidate_count = sum(
            scope["candidateCount"] for scope in scopes.values()
        )
        candidate_bytes = sum(
            scope["candidateBytes"] for scope in scopes.values()
        )
        error_count = sum(
            1 for scope in scopes.values() if scope["state"] == "error"
        )
        state = "error" if error_count else "attention" if candidate_count else "clear"
        public_report = {
            "schema": STORAGE_RETENTION_REPORT_SCHEMA,
            "state": state,
            "generatedAt": generated_at,
            "nextScanAt": _nonnegative_float(raw.get("nextScanAt"), 0.0),
            "dryRun": True,
            "automaticDeletion": False,
            "summary": {
                "scopeCount": len(scopes),
                "errorCount": error_count,
                "candidateCount": candidate_count,
                "candidateBytes": candidate_bytes,
            },
            "scopes": scopes,
        }
        current_time = time.time() if now is None else float(now)
        age_sec = max(0.0, current_time - generated_at)
        stale = age_sec > max(60.0, float(stale_after_sec))
        return {
            "ok": True,
            "available": True,
            "stale": stale,
            "ageSec": age_sec,
            "state": "stale" if stale else public_report["state"],
            "report": public_report,
            "policy": policy,
        }
    except (OSError, OverflowError, TypeError, ValueError) as exc:
        return {
            "ok": False,
            "available": False,
            "stale": False,
            "state": "unavailable",
            "error": "storage_retention_report_invalid",
            "detail": type(exc).__name__,
            "policy": policy,
        }


class StorageRetentionReporter:
    def __init__(
        self,
        *,
        project_root: Path | None = None,
        artifacts_root: Path | None = None,
        interval_sec: float | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.project_root = Path(project_root or get_repo_root()).resolve()
        self.artifacts_root = Path(
            artifacts_root or get_runtime_artifacts_root()
        ).resolve()
        configured_interval = (
            interval_sec
            if interval_sec is not None
            else _nonnegative_float(
                os.getenv("EVELYN_RETENTION_REPORT_INTERVAL_SEC"),
                DEFAULT_REPORT_INTERVAL_SEC,
            )
        )
        self.interval_sec = max(60.0, float(configured_interval))
        self.now = now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._status: dict[str, Any] = {
            "state": "idle",
            "lastGeneratedAt": None,
            "nextScanAt": None,
            "lastError": "",
            "dryRun": True,
            "automaticDeletion": False,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def run_once(self) -> dict[str, Any]:
        generated_at = self.now()
        report = build_storage_retention_report(
            project_root=self.project_root,
            artifacts_root=self.artifacts_root,
            now=generated_at,
            interval_sec=self.interval_sec,
        )
        write_storage_retention_report(
            report,
            artifacts_root=self.artifacts_root,
        )
        with self._lock:
            self._status = {
                "state": report["state"],
                "lastGeneratedAt": report["generatedAt"],
                "nextScanAt": report["nextScanAt"],
                "lastError": "",
                "dryRun": True,
                "automaticDeletion": False,
            }
        return report

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:
                with self._lock:
                    self._status = {
                        **self._status,
                        "state": "error",
                        "lastError": f"report_write_failed:{type(exc).__name__}",
                        "nextScanAt": self.now() + self.interval_sec,
                    }
            if self._stop.wait(self.interval_sec):
                break

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="evelyn-storage-retention-report",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, timeout_sec: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, float(timeout_sec)))


__all__ = [
    "DEFAULT_REPORT_INTERVAL_SEC",
    "DEFAULT_STALE_AFTER_SEC",
    "HOST_LOG_RETENTION_RULES",
    "STORAGE_RETENTION_REPORT_RELATIVE_PATH",
    "STORAGE_RETENTION_REPORT_SCHEMA",
    "StorageRetentionReporter",
    "build_storage_retention_report",
    "read_storage_retention_report",
    "storage_retention_report_path",
    "write_storage_retention_report",
]
