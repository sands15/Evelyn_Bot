from __future__ import annotations

import time
import argparse
import json
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any


ACTIVE_ARTIFACT_NAMES = frozenset(
    {
        "last_request.json",
        "voice_last_channel.json",
        "upstream_bridge_status.json",
        "voyager_goal_state.json",
    }
)


@dataclass(frozen=True)
class RetentionRule:
    name: str
    patterns: tuple[str, ...]
    max_age_days: float | None = None
    max_total_bytes: int | None = None
    preserve_newest: int = 1


@dataclass(frozen=True)
class RuntimeArtifact:
    path: Path
    relative_path: str
    size_bytes: int
    mtime: float


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    relative_path: str
    size_bytes: int
    reason: str
    rule: str


@dataclass
class CleanupPlan:
    root: Path
    candidates: list[CleanupCandidate] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(item.size_bytes for item in self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "candidate_count": len(self.candidates),
            "total_bytes": self.total_bytes,
            "candidates": [
                {
                    "path": str(item.path),
                    "relative_path": item.relative_path,
                    "size_bytes": item.size_bytes,
                    "reason": item.reason,
                    "rule": item.rule,
                }
                for item in self.candidates
            ],
        }


DEFAULT_RETENTION_RULES: tuple[RetentionRule, ...] = (
    RetentionRule("logs", ("logs/*.log",), max_age_days=14, max_total_bytes=50 * 1024 * 1024, preserve_newest=2),
    RetentionRule("turn_trace", ("turn_trace/*.jsonl",), max_age_days=30, max_total_bytes=100 * 1024 * 1024, preserve_newest=7),
    RetentionRule("benchmarks", ("benchmarks/*.jsonl",), max_age_days=30, max_total_bytes=20 * 1024 * 1024, preserve_newest=3),
    RetentionRule("memory_writebehind", ("memory/*.jsonl",), max_age_days=30, max_total_bytes=20 * 1024 * 1024, preserve_newest=3),
    RetentionRule("voice_debug_audio", ("voice_debug/**/*.wav", "voice_debug/**/*.pcm"), max_age_days=7, max_total_bytes=2 * 1024 * 1024 * 1024, preserve_newest=50),
    RetentionRule("control_page_dumps", ("control_page/dumps/*",), max_age_days=7, max_total_bytes=50 * 1024 * 1024, preserve_newest=3),
    RetentionRule("minecraft_window_state", ("minecraft/window_state/*.json",), max_age_days=30, max_total_bytes=50 * 1024 * 1024, preserve_newest=10),
    RetentionRule("test_status", ("test_status/*",), max_age_days=7, max_total_bytes=10 * 1024 * 1024, preserve_newest=2),
    RetentionRule("voyager_jsonl", ("voyager/*.jsonl",), max_age_days=30, max_total_bytes=50 * 1024 * 1024, preserve_newest=5),
)


def _resolve_root(root: Path) -> Path:
    return Path(root).resolve()


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def _relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def inventory_runtime_artifacts(root: Path) -> list[RuntimeArtifact]:
    resolved_root = _resolve_root(root)
    if not resolved_root.exists():
        return []
    artifacts: list[RuntimeArtifact] = []
    for path in resolved_root.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not _is_within_root(resolved, resolved_root):
            continue
        stat = resolved.stat()
        artifacts.append(
            RuntimeArtifact(
                path=resolved,
                relative_path=_relative_posix(resolved, resolved_root),
                size_bytes=int(stat.st_size),
                mtime=float(stat.st_mtime),
            )
        )
    return artifacts


def artifact_matches_rule(artifact: RuntimeArtifact, rule: RetentionRule) -> bool:
    return any(fnmatch(artifact.relative_path, pattern) for pattern in rule.patterns)


def _protected_by_name(artifact: RuntimeArtifact) -> bool:
    return artifact.path.name in ACTIVE_ARTIFACT_NAMES


def select_cleanup_candidates(
    artifacts: list[RuntimeArtifact],
    rule: RetentionRule,
    *,
    now: float | None = None,
) -> list[CleanupCandidate]:
    current_time = time.time() if now is None else now
    matched = [artifact for artifact in artifacts if artifact_matches_rule(artifact, rule) and not _protected_by_name(artifact)]
    if not matched:
        return []

    newest_first = sorted(matched, key=lambda item: item.mtime, reverse=True)
    protected = set(item.relative_path for item in newest_first[: max(0, rule.preserve_newest)])
    candidates: dict[str, CleanupCandidate] = {}

    if rule.max_age_days is not None:
        max_age_sec = max(0.0, rule.max_age_days) * 86400.0
        for artifact in newest_first:
            if artifact.relative_path in protected:
                continue
            age_sec = max(0.0, current_time - artifact.mtime)
            if age_sec > max_age_sec:
                candidates[artifact.relative_path] = CleanupCandidate(
                    path=artifact.path,
                    relative_path=artifact.relative_path,
                    size_bytes=artifact.size_bytes,
                    reason=f"age>{rule.max_age_days}d",
                    rule=rule.name,
                )

    if rule.max_total_bytes is not None:
        total = sum(item.size_bytes for item in matched if item.relative_path not in candidates)
        oldest_first = sorted(matched, key=lambda item: item.mtime)
        for artifact in oldest_first:
            if total <= rule.max_total_bytes:
                break
            if artifact.relative_path in protected:
                continue
            if artifact.relative_path in candidates:
                continue
            candidates[artifact.relative_path] = CleanupCandidate(
                path=artifact.path,
                relative_path=artifact.relative_path,
                size_bytes=artifact.size_bytes,
                reason=f"total>{rule.max_total_bytes}B",
                rule=rule.name,
            )
            total -= artifact.size_bytes

    return sorted(candidates.values(), key=lambda item: item.relative_path)


def build_cleanup_plan(
    root: Path,
    *,
    rules: tuple[RetentionRule, ...] = DEFAULT_RETENTION_RULES,
    now: float | None = None,
) -> CleanupPlan:
    resolved_root = _resolve_root(root)
    artifacts = inventory_runtime_artifacts(resolved_root)
    plan = CleanupPlan(root=resolved_root)
    seen: set[str] = set()
    for rule in rules:
        for candidate in select_cleanup_candidates(artifacts, rule, now=now):
            resolved = candidate.path.resolve()
            if not _is_within_root(resolved, resolved_root):
                continue
            if candidate.relative_path in seen:
                continue
            seen.add(candidate.relative_path)
            plan.candidates.append(candidate)
    return plan


def apply_cleanup_plan(plan: CleanupPlan, *, dry_run: bool = True) -> dict[str, Any]:
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for candidate in plan.candidates:
        if dry_run:
            continue
        try:
            candidate.path.unlink()
            deleted.append(candidate.relative_path)
        except Exception as exc:
            failed.append({"relative_path": candidate.relative_path, "error": repr(exc)})
    return {
        "dry_run": dry_run,
        "candidate_count": len(plan.candidates),
        "candidate_bytes": plan.total_bytes,
        "deleted": deleted,
        "failed": failed,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or apply Evelyn runtime_artifacts retention cleanup.")
    parser.add_argument("--root", type=Path, default=Path("runtime_artifacts"), help="runtime_artifacts root")
    parser.add_argument("--apply", action="store_true", help="delete selected candidates; default is dry-run")
    args = parser.parse_args(argv)

    plan = build_cleanup_plan(args.root)
    result = apply_cleanup_plan(plan, dry_run=not args.apply)
    print(json.dumps({"plan": plan.to_dict(), "result": result}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
