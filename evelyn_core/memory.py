import json
import re
import time
from pathlib import Path

from .config import (
    GUILD_SETTINGS_ROOT,
    MEMORY_FACT_LIMIT,
    MEMORY_LOOP_LIMIT,
    MEMORY_RAW_LIMIT,
    MEMORY_ROOT,
    MEMORY_ROW_MAX_CHARS,
    MEMORY_VAULT_DAYS,
    MEMORY_WORKING_SUMMARY_MAX_CHARS,
)
from .text import clean_text


def guild_memory_dir(guild_id: int) -> Path:
    """길드별 메모리 파일이 모이는 디렉터리를 만들고 반환한다."""
    path = MEMORY_ROOT / f"guild_{guild_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def guild_settings_path(guild_id: int) -> Path:
    """길드별 설정 파일 경로를 반환한다."""
    GUILD_SETTINGS_ROOT.mkdir(parents=True, exist_ok=True)
    return GUILD_SETTINGS_ROOT / f"guild_{guild_id}.json"


def memory_vault_dir(guild_id: int) -> Path:
    path = guild_memory_dir(guild_id) / "vault"
    path.mkdir(parents=True, exist_ok=True)
    return path


def memory_summary_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "rolling_summary.txt"


def memory_raw_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "raw_transcript.jsonl"


def vault_raw_dir(guild_id: int) -> Path:
    path = memory_vault_dir(guild_id) / "raw"
    path.mkdir(parents=True, exist_ok=True)
    return path


def vault_daily_raw_path(guild_id: int, day_key: str | None = None) -> Path:
    day_key = day_key or time.strftime("%Y-%m-%d")
    return vault_raw_dir(guild_id) / f"{day_key}.jsonl"


def memory_facts_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "durable_facts.jsonl"


def vault_facts_path(guild_id: int) -> Path:
    return memory_vault_dir(guild_id) / "facts.jsonl"


def memory_questions_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "open_questions.jsonl"


def vault_questions_path(guild_id: int) -> Path:
    return memory_vault_dir(guild_id) / "questions.jsonl"


def cognitive_state_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "cognitive_state.json"


def memory_loops_path(guild_id: int) -> Path:
    return guild_memory_dir(guild_id) / "open_loops.jsonl"


def read_text_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(clean_text(text), encoding="utf-8")


def read_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_json_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_jsonl(path: Path) -> list[dict]:
    """jsonl 파일을 읽어 dict 행 목록으로 반환한다. 깨진 줄은 건너뛴다."""
    if not path.exists():
        return []

    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    """dict 행 목록을 jsonl 형식으로 저장한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    if content:
        content += "\n"
    path.write_text(content, encoding="utf-8")


def append_jsonl_rows(path: Path, rows: list[dict], limit: int) -> None:
    existing = read_jsonl(path)
    existing.extend(row for row in rows if isinstance(row, dict))
    if len(existing) > limit:
        existing = existing[-limit:]
    write_jsonl(path, existing)


def compact_working_summary(text: str) -> str:
    text = clean_text(text)
    if len(text) <= MEMORY_WORKING_SUMMARY_MAX_CHARS:
        return text
    return clean_text(text[-MEMORY_WORKING_SUMMARY_MAX_CHARS:])


def compact_memory_text(text: str, max_chars: int | None = None) -> str:
    text = clean_text(text)
    limit = max_chars or MEMORY_ROW_MAX_CHARS
    if len(text) <= limit:
        return text
    return clean_text(text[:limit] + "...")


def format_memory_rows_for_llm(rows: list[dict], *, max_items: int, max_chars: int | None = None) -> str:
    lines: list[str] = []
    for row in rows[-max_items:]:
        if not isinstance(row, dict):
            continue
        text = compact_memory_text(str(row.get("text", "")), max_chars=max_chars)
        if not text:
            continue
        speaker = compact_memory_text(str(row.get("speaker", row.get("role", row.get("type", "memory")))), max_chars=24)
        source = compact_memory_text(str(row.get("source", row.get("type", "unknown"))), max_chars=16)
        lines.append(f"- {speaker} ({source}): {text}")
    return "\n".join(lines) if lines else "(없음)"


def is_context_size_error(exc: Exception) -> bool:
    return "Context size has been exceeded" in str(exc)


def merge_memory_rows(*row_groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for rows in row_groups:
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = clean_text(str(row.get("text", "")))
            row_type = clean_text(str(row.get("type", row.get("role", "memory")))) or "memory"
            if len(text) < 1:
                continue
            key = (row_type, text)
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
    return merged


def read_vault_raw_rows(guild_id: int, *, days: int | None = None) -> list[dict]:
    days = days or MEMORY_VAULT_DAYS
    paths = sorted(vault_raw_dir(guild_id).glob("*.jsonl"))
    selected = paths[-max(1, days):]
    rows: list[dict] = []
    for path in selected:
        rows.extend(read_jsonl(path))
    return rows


def read_fact_rows(guild_id: int) -> list[dict]:
    return merge_memory_rows(read_jsonl(memory_facts_path(guild_id)), read_jsonl(vault_facts_path(guild_id)))


def append_raw_transcript_rows(guild_id: int, rows: list[dict]) -> None:
    """새 대화 raw transcript를 hot 파일과 일자별 vault 파일에 함께 누적한다."""
    normalized: list[dict] = []
    now = int(time.time())

    for row in rows:
        text = clean_text(str(row.get("text", "")))
        if len(text) < 1:
            continue
        normalized.append(
            {
                "role": clean_text(str(row.get("role", "user"))) or "user",
                "speaker": clean_text(str(row.get("speaker", ""))),
                "source": clean_text(str(row.get("source", "unknown"))) or "unknown",
                "text": text,
                "saved_at": int(row.get("saved_at", now)),
            }
        )

    if normalized:
        append_jsonl_rows(memory_raw_path(guild_id), normalized, MEMORY_RAW_LIMIT)
        append_jsonl_rows(vault_daily_raw_path(guild_id), normalized, max(MEMORY_RAW_LIMIT * 20, 5000))


def append_unique_memory_rows(path: Path, rows: list[dict], limit: int, *, mirror_path: Path | None = None) -> None:
    """중복 텍스트를 제외하고 메모리 행을 추가한 뒤, 필요하면 mirror 파일에도 반영한다."""
    existing = read_jsonl(path)
    mirror_existing = read_jsonl(mirror_path) if mirror_path is not None else []
    seen = {clean_text(str(row.get("text", ""))) for row in merge_memory_rows(existing, mirror_existing)}
    appended_rows: list[dict] = []

    for row in rows:
        text = clean_text(str(row.get("text", "")))
        if len(text) < 2 or text in seen:
            continue
        saved_row = {
            "text": text,
            "type": clean_text(str(row.get("type", "memory"))) or "memory",
            "saved_at": int(time.time()),
        }
        seen.add(text)
        existing.append(saved_row)
        appended_rows.append(saved_row)

    if len(existing) > limit:
        existing = existing[-limit:]

    write_jsonl(path, existing)
    if mirror_path is not None and appended_rows:
        mirror_rows = mirror_existing + appended_rows
        write_jsonl(mirror_path, mirror_rows)


def memory_tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_]+|[가-힣]{2,}", clean_text(text).lower()))


def select_relevant_memory_rows(query: str, rows: list[dict], limit: int) -> list[dict]:
    """질문과 토큰이 많이 겹치는 메모리를 우선순위로 골라 prompt에 넣는다."""
    q = memory_tokens(query)
    if not rows:
        return []

    scored: list[tuple[int, int, dict]] = []
    for index, row in enumerate(rows):
        text = clean_text(str(row.get("text", "")))
        if not text:
            continue
        score = len(q & memory_tokens(text))
        recency = int(row.get("saved_at", index))
        scored.append((score, recency, row))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = [row for score, _, row in scored if score > 0][:limit]
    if selected:
        return selected
    return [row for _, _, row in scored[:limit]]


def read_question_rows(guild_id: int) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for path in (memory_questions_path(guild_id), memory_loops_path(guild_id), vault_questions_path(guild_id)):
        for row in read_jsonl(path):
            text = clean_text(str(row.get("text", "")))
            if len(text) < 2 or text in seen:
                continue
            seen.add(text)
            merged.append(row)
    return merged


def normalize_cognitive_action(value: str) -> str:
    action = clean_text(value).lower()
    if action in {"ask", "question", "clarify"}:
        return "ask"
    if action in {"wait", "listen", "hold"}:
        return "wait"
    return "answer"


def normalize_cognitive_state(data: dict) -> dict:
    """서브 LLM의 cognitive JSON을 안전한 내부 표준 형태로 정규화한다."""
    if not isinstance(data, dict):
        data = {}

    question_for_user = clean_text(
        str(data.get("question_for_user", data.get("suggested_user_question", "")))
    )
    confidence_raw = data.get("confidence", 0.5)
    try:
        confidence = float(confidence_raw)
    except Exception:
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    retrieved_context_ids = data.get("retrieved_context_ids", [])
    if not isinstance(retrieved_context_ids, list):
        retrieved_context_ids = []

    return {
        "action": normalize_cognitive_action(str(data.get("action", "answer"))),
        "confidence": confidence,
        "user_intent": clean_text(str(data.get("user_intent", ""))),
        "state_summary": clean_text(str(data.get("state_summary", ""))),
        "question_for_user": question_for_user,
        "main_prompt_hint": clean_text(str(data.get("main_prompt_hint", ""))),
        "reason_brief": clean_text(str(data.get("reason_brief", ""))),
        "retrieved_context_ids": [clean_text(str(x)) for x in retrieved_context_ids if clean_text(str(x))],
        "updated_at": int(data.get("updated_at", time.time())),
    }
