from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping

from .conversation_archive import (
    DeletionPurgeWorkOrder,
    archive_lineage_handle,
)
from .conversation_archive_purge import (
    ConversationArchivePurgeError,
    LocalPurgeOwner,
    PurgePass,
)
from .memory_vault import (
    delete_memory_vault_user_note,
    memory_vault_root,
    parse_memory_note,
    preview_memory_vault_user_note_deletion,
    rebuild_memory_vault_derived_state,
    refresh_legacy_memory_node_notes,
)
from .runtime_artifact_io import atomic_text_write


MEMORY_BUNDLE_PURGE_SINKS = (
    "bot_memory",
    "search_cache",
    "prompt_tool_cache",
    "embedding_index",
    "cognitive_state",
    "open_question_state",
)

_JSONL_SINKS = {
    "raw_transcript.jsonl": "bot_memory",
    "durable_facts.jsonl": "bot_memory",
    "facts.jsonl": "bot_memory",
    "open_questions.jsonl": "open_question_state",
    "questions.jsonl": "open_question_state",
    "proactive_questions.jsonl": "open_question_state",
}
_TURN_MARKER_RE = re.compile(
    r"(?m)^>\r?\n"
    r"> ### [^\r\n]*\r?\n"
    r"> <!-- evelyn-turn-lineage:(?P<turn>[A-Za-z0-9._:-]{1,80}):begin -->\r?\n"
    r".*?"
    r"^> <!-- evelyn-turn-lineage:(?P=turn):end -->\r?\n"
    r"^>\r?\n?",
    re.DOTALL,
)
_EVIDENCE_RE = re.compile(
    r"^turn:(?P<turn>[A-Za-z0-9._:-]{1,80}):(user|assistant)$"
)


class _CandidateUnverifiable(RuntimeError):
    pass


@dataclass
class _BundleReport:
    removed: dict[str, int] = field(
        default_factory=lambda: {sink: 0 for sink in MEMORY_BUNDLE_PURGE_SINKS}
    )
    remaining: dict[str, int] = field(
        default_factory=lambda: {sink: 0 for sink in MEMORY_BUNDLE_PURGE_SINKS}
    )
    manual: dict[str, int] = field(
        default_factory=lambda: {sink: 0 for sink in MEMORY_BUNDLE_PURGE_SINKS}
    )

    def result(self, sink: str) -> PurgePass:
        return PurgePass(
            removed_count=self.removed[sink],
            remaining_copies=self.remaining[sink],
            manual_review_count=self.manual[sink],
        )

    def block_bundle(self) -> PurgePass:
        return PurgePass(
            remaining_copies=sum(self.remaining.values()),
            manual_review_count=sum(self.manual.values()),
        )

    def mark_bundle_manual(self) -> None:
        for sink in MEMORY_BUNDLE_PURGE_SINKS:
            self.manual[sink] = max(1, self.manual[sink])


class _LineageMatcher:
    def __init__(self, *, key: bytes, work_order: DeletionPurgeWorkOrder) -> None:
        self._key = bytes(key)
        self._lineage_complete = work_order.lineage_complete
        self._owner_wide = bool(
            work_order.scope_all
            and work_order.principal_id is not None
            and work_order.reason in {"user_requested", "admin_requested"}
        )
        self._handles: dict[str, set[str]] = {}
        for kind, digest in work_order.lineage_handles:
            self._handles.setdefault(kind, set()).add(digest)

    @property
    def usable(self) -> bool:
        return self._lineage_complete and bool(self._handles)

    def matches(self, kind: str, value: object) -> bool:
        candidates = self._handles.get(kind)
        if not candidates or not isinstance(value, str) or not value:
            return False
        try:
            digest = archive_lineage_handle(self._key, kind, value)
        except Exception:
            return False
        return digest in candidates

    def row_matches(self, row: Mapping[str, object]) -> bool:
        for key, kind in (
            ("source_turn_id", "turn"),
            ("session_key", "session"),
            ("session_memory_key", "session"),
            ("evidence_id", "memory_evidence"),
            ("note_id", "memory_note"),
            ("noteId", "memory_note"),
        ):
            if self.matches(kind, row.get(key)):
                return True
        if self._owner_wide and self.matches(
            "memory_owner", row.get("owner_scope")
        ):
            return True
        for key, kind in (
            ("source_turn_ids", "turn"),
            ("source_evidence_ids", "memory_evidence"),
            ("source_refs", "memory_evidence"),
            ("origin_source_refs", "memory_evidence"),
        ):
            values = row.get(key)
            if isinstance(values, (list, tuple)) and any(
                self.matches(kind, value) for value in values
            ):
                return True
        evidence = row.get("evidence_id")
        if isinstance(evidence, str):
            match = _EVIDENCE_RE.fullmatch(evidence)
            if match is not None and self.matches("turn", match.group("turn")):
                return True
        return False


class _MemoryBundleOwner:
    def __init__(
        self,
        *,
        memory_root: Path,
        lineage_key: bytes,
        process_tool_cache_purge: Callable[
            [DeletionPurgeWorkOrder], PurgePass
        ]
        | None,
        writer_fence_current: Callable[[DeletionPurgeWorkOrder], bool] | None,
    ) -> None:
        self._root = Path(memory_root)
        self._key = bytes(lineage_key)
        self._process_tool_cache_purge = process_tool_cache_purge
        self._writer_fence_current = writer_fence_current
        self._lock = threading.RLock()
        self._reports: dict[
            tuple[str, int, bool, tuple[tuple[str, str], ...]],
            _BundleReport,
        ] = {}
        self._verified_sinks: dict[
            tuple[str, int, bool, tuple[tuple[str, str], ...]],
            set[str],
        ] = {}

    @staticmethod
    def _cache_key(
        work_order: DeletionPurgeWorkOrder,
    ) -> tuple[str, int, bool, tuple[tuple[str, str], ...]]:
        return (
            work_order.request_id,
            work_order.deletion_generation,
            work_order.lineage_complete,
            work_order.lineage_handles,
        )

    def _resolved_root(self) -> Path:
        if (
            self._root.is_symlink()
            or bool(getattr(self._root, "is_junction", lambda: False)())
            or not self._root.is_dir()
        ):
            raise OSError("unsafe_memory_runtime_artifact_path")
        return self._root.resolve()

    @staticmethod
    def _safe_file(path: Path, *, root: Path) -> Path:
        if (
            path.is_symlink()
            or bool(getattr(path, "is_junction", lambda: False)())
            or not path.is_file()
        ):
            raise OSError("unsafe_memory_runtime_artifact_path")
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise OSError("unsafe_memory_runtime_artifact_path")
        return resolved

    def _jsonl_paths(self, *, root: Path) -> tuple[tuple[Path, str], ...]:
        paths: list[tuple[Path, str]] = []
        for path in root.rglob("*.jsonl"):
            sink = _JSONL_SINKS.get(path.name)
            if sink is None and path.parent.name == "raw":
                sink = "bot_memory"
            if sink is not None:
                paths.append((self._safe_file(path, root=root), sink))
        return tuple(sorted(paths, key=lambda item: str(item[0])))

    @staticmethod
    def _parsed_jsonl(raw: str) -> list[tuple[str, Mapping[str, object] | None]]:
        parsed: list[tuple[str, Mapping[str, object] | None]] = []
        for line in raw.splitlines(keepends=True):
            if not line.strip():
                parsed.append((line, None))
                continue
            try:
                row = json.loads(line)
            except (TypeError, ValueError, json.JSONDecodeError):
                raise _CandidateUnverifiable(
                    "memory_candidate_unverifiable"
                ) from None
            if not isinstance(row, dict):
                raise _CandidateUnverifiable(
                    "memory_candidate_unverifiable"
                )
            parsed.append((line, row))
        return parsed

    @staticmethod
    def _rewrite_jsonl(
        path: Path,
        *,
        matcher: _LineageMatcher,
    ) -> int:
        raw = path.read_text(encoding="utf-8", errors="strict")
        kept: list[str] = []
        removed = 0
        for line, row in _MemoryBundleOwner._parsed_jsonl(raw):
            if row is not None and matcher.row_matches(row):
                removed += 1
            else:
                kept.append(line)
        if removed:
            atomic_text_write(path, "".join(kept), durable=True)
        return removed

    @staticmethod
    def _json_file_matches(path: Path, matcher: _LineageMatcher) -> bool:
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            raise _CandidateUnverifiable(
                "memory_candidate_unverifiable"
            ) from None
        if not isinstance(payload, dict):
            raise _CandidateUnverifiable("memory_candidate_unverifiable")
        return matcher.row_matches(payload)

    @staticmethod
    def _parse_note(path: Path) -> tuple[object, str]:
        try:
            raw = path.read_text(encoding="utf-8", errors="strict")
            if "\x00" in raw:
                raise ValueError
            if raw.startswith("---"):
                lines = raw.splitlines()
                try:
                    end = next(
                        index
                        for index, line in enumerate(lines[1:], start=1)
                        if line.strip() == "---"
                    )
                except StopIteration:
                    raise ValueError from None
                if any(
                    line.strip() and ":" not in line
                    for line in lines[1:end]
                ):
                    raise ValueError
            return parse_memory_note(path, raw), raw
        except Exception:
            raise _CandidateUnverifiable(
                "memory_candidate_unverifiable"
            ) from None

    def _writer_fence_is_current(
        self,
        work_order: DeletionPurgeWorkOrder,
    ) -> bool:
        if self._writer_fence_current is None:
            return False
        try:
            return self._writer_fence_current(work_order) is True
        except Exception:
            return False

    @staticmethod
    def _unlink(path: Path) -> bool:
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False

    def _purge_summary_and_state(
        self,
        *,
        root: Path,
        matcher: _LineageMatcher,
        report: _BundleReport,
        changed_sources: set[Path],
    ) -> None:
        for summary in root.rglob("rolling_summary.txt"):
            try:
                summary = self._safe_file(summary, root=root)
                provenance = summary.with_name(
                    "rolling_summary.provenance.json"
                )
                if not provenance.is_file() or provenance.is_symlink():
                    raise _CandidateUnverifiable(
                        "memory_candidate_unverifiable"
                    )
            except (OSError, _CandidateUnverifiable):
                report.manual["bot_memory"] = 1
        for provenance in root.rglob("rolling_summary.provenance.json"):
            try:
                provenance = self._safe_file(provenance, root=root)
                matched = self._json_file_matches(provenance, matcher)
            except (OSError, UnicodeError, _CandidateUnverifiable):
                report.manual["bot_memory"] = 1
                continue
            if not matched:
                continue
            summary = provenance.with_name("rolling_summary.txt")
            for target in (provenance, summary):
                if target.exists():
                    self._safe_file(target, root=root)
                    if self._unlink(target):
                        report.removed["bot_memory"] += 1
                        changed_sources.add(target)
        for state in root.rglob("cognitive_state.json"):
            try:
                state = self._safe_file(state, root=root)
                matched = self._json_file_matches(state, matcher)
            except (OSError, UnicodeError, _CandidateUnverifiable):
                report.manual["cognitive_state"] = 1
                continue
            if matched and self._unlink(state):
                report.removed["cognitive_state"] += 1
                changed_sources.add(state)
        for pending in root.rglob("pending_proactive_question.json"):
            try:
                pending = self._safe_file(pending, root=root)
                matched = self._json_file_matches(pending, matcher)
            except (OSError, UnicodeError, _CandidateUnverifiable):
                report.manual["open_question_state"] = 1
                continue
            if matched and self._unlink(pending):
                report.removed["open_question_state"] += 1
                changed_sources.add(pending)

    def _purge_daily_blocks(
        self,
        *,
        root: Path,
        matcher: _LineageMatcher,
        report: _BundleReport,
    ) -> None:
        vault = memory_vault_root(root)
        if not vault.exists():
            return
        if vault.is_symlink() or not vault.resolve().is_relative_to(root):
            raise OSError("unsafe_memory_runtime_artifact_path")
        for path in vault.rglob("*.md"):
            try:
                path = self._safe_file(path, root=root)
                _note, raw = self._parse_note(path)
            except (OSError, UnicodeError, _CandidateUnverifiable):
                report.manual["bot_memory"] = 1
                continue
            removed = 0

            def replace(match: re.Match[str]) -> str:
                nonlocal removed
                if matcher.matches("turn", match.group("turn")):
                    removed += 1
                    return ""
                return match.group(0)

            updated = _TURN_MARKER_RE.sub(replace, raw)
            if removed:
                atomic_text_write(path, updated, durable=True)
                report.removed["bot_memory"] += removed

    @staticmethod
    def _metadata_values(value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            return (value,) if value else ()
        if isinstance(value, (list, tuple)):
            return tuple(item for item in value if isinstance(item, str) and item)
        return ()

    def _note_matches(self, note: object, matcher: _LineageMatcher) -> bool:
        note_id = getattr(note, "note_id", "")
        metadata = getattr(note, "metadata", {})
        if matcher.matches("memory_note", note_id) or not isinstance(metadata, dict):
            return matcher.matches("memory_note", note_id)
        if matcher._owner_wide and matcher.matches(
            "memory_owner", metadata.get("owner_scope")
        ):
            return True
        for key in (
            "source_ref",
            "source_refs",
            "origin_source_refs",
            "revised_from_evidence_hashes",
        ):
            for value in self._metadata_values(metadata.get(key)):
                if matcher.matches("memory_evidence", value):
                    return True
                match = _EVIDENCE_RE.fullmatch(value)
                if match is not None and matcher.matches(
                    "turn", match.group("turn")
                ):
                    return True
        return False

    def _purge_user_notes(
        self,
        *,
        root: Path,
        matcher: _LineageMatcher,
        work_order: DeletionPurgeWorkOrder,
        report: _BundleReport,
    ) -> None:
        vault = memory_vault_root(root)
        if not vault.exists():
            return
        targets: list[str] = []
        for path in sorted(vault.rglob("*.md")):
            try:
                path = self._safe_file(path, root=root)
                note, _raw = self._parse_note(path)
            except (OSError, UnicodeError, _CandidateUnverifiable):
                report.manual["bot_memory"] = 1
                continue
            if self._note_matches(note, matcher):
                targets.append(path.relative_to(vault).as_posix())
        for rel_path in targets:
            preview = preview_memory_vault_user_note_deletion(
                rel_path,
                reason=(
                    "obsolete_memory"
                    if work_order.reason == "retention_expired"
                    else "privacy_request"
                ),
                root=root,
            )
            if preview.get("ok") is not True:
                report.manual["bot_memory"] += 1
                continue
            result = delete_memory_vault_user_note(
                rel_path,
                str(preview.get("confirmToken") or ""),
                reason=(
                    "obsolete_memory"
                    if work_order.reason == "retention_expired"
                    else "privacy_request"
                ),
                root=root,
            )
            if result.get("ok") is True and result.get("deleted") is True:
                report.removed["bot_memory"] += 1
            else:
                report.manual["bot_memory"] += 1

    def _invalidate_legacy_mirrors(
        self,
        *,
        root: Path,
        changed_sources: Iterable[Path],
        report: _BundleReport,
    ) -> None:
        changed = {str(path.resolve()) for path in changed_sources}
        if not changed:
            return
        vault = memory_vault_root(root)
        guild_ids: set[int] = set()
        for source in changed:
            try:
                relative = Path(source).relative_to(root)
            except ValueError:
                continue
            if relative.parts:
                match = re.fullmatch(r"guild_(\d+)", relative.parts[0])
                if match is not None:
                    guild_ids.add(int(match.group(1)))
        if vault.exists():
            for path in sorted(vault.rglob("*.md")):
                try:
                    path = self._safe_file(path, root=root)
                    note, _raw = self._parse_note(path)
                except (OSError, UnicodeError, _CandidateUnverifiable):
                    report.manual["bot_memory"] = 1
                    continue
                if note.metadata.get("source") != "legacy-memory-node-mirror":
                    continue
                refs = self._metadata_values(note.metadata.get("source_refs"))
                if len(refs) != 1 or str(Path(refs[0]).resolve()) not in changed:
                    continue
                if self._unlink(path):
                    report.removed["bot_memory"] += 1
        for guild_id in sorted(guild_ids):
            refresh_legacy_memory_node_notes(guild_id, root=root)

    def _negative_recall(
        self,
        *,
        root: Path,
        matcher: _LineageMatcher,
        report: _BundleReport,
    ) -> None:
        for path, sink in self._jsonl_paths(root=root):
            try:
                raw = path.read_text(encoding="utf-8", errors="strict")
                rows = self._parsed_jsonl(raw)
            except (OSError, UnicodeError, _CandidateUnverifiable):
                report.manual[sink] = 1
                continue
            for _line, row in rows:
                if row is not None and matcher.row_matches(row):
                    report.remaining[sink] += 1
        for summary in root.rglob("rolling_summary.txt"):
            try:
                summary = self._safe_file(summary, root=root)
                provenance = summary.with_name(
                    "rolling_summary.provenance.json"
                )
                if not provenance.is_file() or provenance.is_symlink():
                    raise _CandidateUnverifiable(
                        "memory_candidate_unverifiable"
                    )
            except (OSError, _CandidateUnverifiable):
                report.manual["bot_memory"] = 1
        for pattern, sink in (
            ("rolling_summary.provenance.json", "bot_memory"),
            ("cognitive_state.json", "cognitive_state"),
            ("pending_proactive_question.json", "open_question_state"),
        ):
            for path in root.rglob(pattern):
                try:
                    path = self._safe_file(path, root=root)
                    matched = self._json_file_matches(path, matcher)
                except (OSError, UnicodeError, _CandidateUnverifiable):
                    report.manual[sink] = 1
                    continue
                if matched:
                    report.remaining[sink] += 1
        vault = memory_vault_root(root)
        if vault.exists():
            if (
                vault.is_symlink()
                or bool(getattr(vault, "is_junction", lambda: False)())
                or not vault.is_dir()
                or not vault.resolve().is_relative_to(root)
            ):
                raise OSError("unsafe_memory_runtime_artifact_path")
            for path in vault.rglob("*.md"):
                try:
                    path = self._safe_file(path, root=root)
                    note, raw = self._parse_note(path)
                except (OSError, UnicodeError, _CandidateUnverifiable):
                    report.manual["bot_memory"] = 1
                    continue
                for match in _TURN_MARKER_RE.finditer(raw):
                    if matcher.matches("turn", match.group("turn")):
                        report.remaining["bot_memory"] += 1
                if self._note_matches(note, matcher):
                    report.remaining["bot_memory"] += 1

    def _execute(self, work_order: DeletionPurgeWorkOrder) -> _BundleReport:
        report = _BundleReport()
        matcher = _LineageMatcher(key=self._key, work_order=work_order)
        if not matcher.usable:
            report.mark_bundle_manual()
            return report
        if not self._writer_fence_is_current(work_order):
            report.mark_bundle_manual()
            return report
        try:
            root = self._resolved_root()
            preflight = _BundleReport()
            self._negative_recall(
                root=root,
                matcher=matcher,
                report=preflight,
            )
            if any(preflight.manual.values()):
                preflight.mark_bundle_manual()
                return preflight
            changed_sources: set[Path] = set()
            for path, sink in self._jsonl_paths(root=root):
                try:
                    removed = self._rewrite_jsonl(path, matcher=matcher)
                except (OSError, UnicodeError, _CandidateUnverifiable):
                    report.manual[sink] = 1
                    continue
                if removed:
                    report.removed[sink] += removed
                    changed_sources.add(path)
            self._purge_summary_and_state(
                root=root,
                matcher=matcher,
                report=report,
                changed_sources=changed_sources,
            )
            self._purge_daily_blocks(
                root=root,
                matcher=matcher,
                report=report,
            )
            self._purge_user_notes(
                root=root,
                matcher=matcher,
                work_order=work_order,
                report=report,
            )
            self._invalidate_legacy_mirrors(
                root=root,
                changed_sources=changed_sources,
                report=report,
            )
            local_candidate_manual = any(
                report.manual[sink]
                for sink in (
                    "bot_memory",
                    "cognitive_state",
                    "open_question_state",
                )
            )
            if local_candidate_manual:
                for sink in (
                    "search_cache",
                    "prompt_tool_cache",
                    "embedding_index",
                ):
                    report.manual[sink] = 1
            else:
                rebuild = rebuild_memory_vault_derived_state(root=root)
                removed_derived = int(rebuild.get("removedDerivedFiles") or 0)
                for sink in (
                    "search_cache",
                    "prompt_tool_cache",
                    "embedding_index",
                ):
                    report.removed[sink] += removed_derived
            if self._process_tool_cache_purge is None:
                # The memory prompt cache is rebuilt above.  A process-local tool
                # dispatch cache has a different owner and cannot be claimed here.
                report.manual["prompt_tool_cache"] = 1
            else:
                external = self._process_tool_cache_purge(work_order)
                if not isinstance(external, PurgePass):
                    raise TypeError("archive_purge_owner_result_invalid")
                report.removed["prompt_tool_cache"] += external.removed_count
                report.remaining["prompt_tool_cache"] += external.remaining_copies
                report.manual["prompt_tool_cache"] += external.manual_review_count
            self._negative_recall(root=root, matcher=matcher, report=report)
            if not self._writer_fence_is_current(work_order):
                report.mark_bundle_manual()
        except Exception:
            report.mark_bundle_manual()
        return report

    def _finish_verification(
        self,
        *,
        cache_key: tuple[str, int, bool, tuple[tuple[str, str], ...]],
        sink: str,
        work_order: DeletionPurgeWorkOrder,
    ) -> None:
        verified = self._verified_sinks.setdefault(cache_key, set())
        verified.add(sink)
        expected = set(MEMORY_BUNDLE_PURGE_SINKS).intersection(
            work_order.required_sinks
        )
        if verified.issuperset(expected):
            # Share one physical rewrite across the logical sinks in this
            # coordinator pass, but never across retries.
            self._reports.pop(cache_key, None)
            self._verified_sinks.pop(cache_key, None)

    def purge(self, sink: str, work_order: DeletionPurgeWorkOrder) -> PurgePass:
        key = self._cache_key(work_order)
        with self._lock:
            report = self._reports.get(key)
            if report is None:
                report = self._execute(work_order)
                self._reports[key] = report
                while len(self._reports) > 256:
                    expired = next(iter(self._reports))
                    self._reports.pop(expired)
                    self._verified_sinks.pop(expired, None)
            return report.result(sink)

    def negative_recall(
        self,
        sink: str,
        work_order: DeletionPurgeWorkOrder,
    ) -> PurgePass:
        with self._lock:
            cache_key = self._cache_key(work_order)
            cached = self._reports.get(cache_key)
            if cached is None:
                cached = self._execute(work_order)
                self._reports[cache_key] = cached
            verification = _BundleReport()
            matcher = _LineageMatcher(key=self._key, work_order=work_order)
            try:
                if not matcher.usable:
                    verification.mark_bundle_manual()
                elif not self._writer_fence_is_current(work_order):
                    verification.mark_bundle_manual()
                else:
                    root = self._resolved_root()
                    self._negative_recall(
                        root=root,
                        matcher=matcher,
                        report=verification,
                    )
                    if (
                        sink == "prompt_tool_cache"
                        and self._process_tool_cache_purge is None
                    ):
                        verification.manual[sink] = 1
                    elif (
                        sink == "prompt_tool_cache"
                        and self._process_tool_cache_purge is not None
                    ):
                        external = self._process_tool_cache_purge(work_order)
                        if not isinstance(external, PurgePass):
                            raise TypeError("archive_purge_owner_result_invalid")
                        verification.remaining[sink] += external.remaining_copies
                        verification.manual[sink] += external.manual_review_count
                    if not self._writer_fence_is_current(work_order):
                        verification.mark_bundle_manual()
            except Exception:
                verification.mark_bundle_manual()
            result = verification.block_bundle()
            self._finish_verification(
                cache_key=cache_key,
                sink=sink,
                work_order=work_order,
            )
            return result


def memory_bundle_purge_owners(
    *,
    memory_root: Path,
    lineage_key: bytes,
    process_tool_cache_purge: Callable[
        [DeletionPurgeWorkOrder], PurgePass
    ]
    | None = None,
    writer_fence_current: Callable[[DeletionPurgeWorkOrder], bool] | None = None,
) -> tuple[LocalPurgeOwner, ...]:
    """Build one physical memory owner projected onto the six archive sinks."""

    if (
        len(bytes(lineage_key)) < 32
        or (
            process_tool_cache_purge is not None
            and not callable(process_tool_cache_purge)
        )
        or (writer_fence_current is not None and not callable(writer_fence_current))
    ):
        raise ConversationArchivePurgeError("archive_purge_owner_invalid")

    physical = _MemoryBundleOwner(
        memory_root=memory_root,
        lineage_key=lineage_key,
        process_tool_cache_purge=process_tool_cache_purge,
        writer_fence_current=writer_fence_current,
    )
    return tuple(
        LocalPurgeOwner(
            sink=sink,
            purge=lambda work_order, sink=sink: physical.purge(sink, work_order),
            negative_recall=lambda work_order, sink=sink: physical.negative_recall(
                sink, work_order
            ),
        )
        for sink in MEMORY_BUNDLE_PURGE_SINKS
    )


__all__ = ["MEMORY_BUNDLE_PURGE_SINKS", "memory_bundle_purge_owners"]
