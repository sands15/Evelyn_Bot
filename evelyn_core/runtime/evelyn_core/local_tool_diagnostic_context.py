from __future__ import annotations

from pathlib import Path

from .text import clean_text


LOCAL_TOOL_DIAGNOSTIC_MARKERS = ("tool", "function", "llm", "도구", "툴", "호출", "메인")
LOCAL_TOOL_DIAGNOSTIC_TERMS = (
    "build_tool_use_decisions",
    "render_tool_use_context",
    "tool_use_decisions",
    "local_file_or_log_read",
    "runtime_status",
    "build_main_llm_payload",
    "prepare_llm_messages",
    "build_main_response_guidance",
)


def collect_local_tool_diagnostic_matches(
    path: Path,
    terms: tuple[str, ...] = LOCAL_TOOL_DIAGNOSTIC_TERMS,
    *,
    max_matches: int = 5,
) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [f"{path.name}: read_failed={clean_text(repr(exc))[:120]}"]
    matches: list[str] = []
    lowered_terms = tuple(term.lower() for term in terms if term)
    for index, line in enumerate(lines, start=1):
        lowered = line.lower()
        if any(term in lowered for term in lowered_terms):
            matches.append(f"{path.name}:{index}: {clean_text(line)[:160]}")
            if len(matches) >= max_matches:
                break
    return matches


def local_tool_diagnostic_candidate_paths(project_root: Path) -> tuple[Path, ...]:
    return (
        project_root / "evelyn_core" / "runtime" / "evelyn_core" / "context_pipeline.py",
        project_root / "evelyn_core" / "runtime" / "evelyn_core" / "skills" / "routing" / "voice_llm.py",
        project_root / "main.py",
        project_root / "tests" / "test_context_pipeline_tool_policy.py",
    )


def build_local_tool_diagnostic_context(
    user_text: str,
    *,
    project_root: Path,
    max_matches_per_file: int = 4,
    max_lines: int = 18,
) -> str:
    text = clean_text(user_text).lower()
    if not any(marker in text for marker in LOCAL_TOOL_DIAGNOSTIC_MARKERS):
        return ""
    evidence: list[str] = ["local_tool_diagnostic_snapshot:"]
    for path in local_tool_diagnostic_candidate_paths(project_root):
        evidence.extend(
            collect_local_tool_diagnostic_matches(
                path,
                LOCAL_TOOL_DIAGNOSTIC_TERMS,
                max_matches=max_matches_per_file,
            )
        )
    return "\n".join(evidence[:max_lines])


__all__ = [
    "build_local_tool_diagnostic_context",
    "collect_local_tool_diagnostic_matches",
    "local_tool_diagnostic_candidate_paths",
]
