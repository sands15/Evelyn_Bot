from __future__ import annotations

from typing import Any

from .memory import (
    compact_working_summary,
    memory_raw_path,
    memory_summary_path,
    read_fact_rows,
    read_jsonl,
    read_question_rows,
    read_text_file,
    read_vault_raw_rows,
)


def collect_memory_layers(
    guild_id: int,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> dict[str, dict[str, Any]]:
    layers: dict[str, dict[str, Any]] = {
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
            "summary": compact_working_summary(
                read_text_file(memory_summary_path(guild_id, scope_type="room", scope_key=room_key))
            ),
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
            "summary": compact_working_summary(
                read_text_file(memory_summary_path(guild_id, scope_type="person", scope_key=person_key))
            ),
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
            "summary": compact_working_summary(
                read_text_file(memory_summary_path(guild_id, scope_type="session", scope_key=session_memory_key))
            ),
            "raw": read_jsonl(memory_raw_path(guild_id, scope_type="session", scope_key=session_memory_key)),
            "vault_raw": read_vault_raw_rows(guild_id, scope_type="session", scope_key=session_memory_key),
            "facts": read_fact_rows(guild_id, scope_type="session", scope_key=session_memory_key),
            "questions": read_question_rows(guild_id, scope_type="session", scope_key=session_memory_key),
        }

    return layers


__all__ = ["collect_memory_layers"]
