from __future__ import annotations

from typing import Any

from .memory import (
    memory_raw_path,
    read_jsonl,
    read_vault_raw_rows,
)


def _user_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("role") or "").strip().lower() == "user"
    ]


def collect_memory_layers(
    guild_id: int,
    *,
    room_key: str | None = None,
    person_key: str | None = None,
    session_memory_key: str | None = None,
) -> dict[str, dict[str, Any]]:
    # ponytail: expose derived layers only after they gain a deletion-current receipt.
    layers: dict[str, dict[str, Any]] = {
        "guild": {
            "label": "공용 방 기억",
            "scope_type": "guild",
            "scope_key": None,
            "summary": "",
            "summary_provenance": {},
            "raw": _user_rows(read_jsonl(memory_raw_path(guild_id))),
            "vault_raw": _user_rows(read_vault_raw_rows(guild_id)),
            "facts": [],
            "questions": [],
        }
    }

    if room_key:
        layers["room"] = {
            "label": "방 기억",
            "scope_type": "room",
            "scope_key": room_key,
            "summary": "",
            "summary_provenance": {},
            "raw": _user_rows(read_jsonl(memory_raw_path(guild_id, scope_type="room", scope_key=room_key))),
            "vault_raw": _user_rows(read_vault_raw_rows(guild_id, scope_type="room", scope_key=room_key)),
            "facts": [],
            "questions": [],
        }

    if person_key:
        layers["person"] = {
            "label": "이 사람 기억",
            "scope_type": "person",
            "scope_key": person_key,
            "summary": "",
            "summary_provenance": {},
            "raw": _user_rows(read_jsonl(memory_raw_path(guild_id, scope_type="person", scope_key=person_key))),
            "vault_raw": _user_rows(read_vault_raw_rows(guild_id, scope_type="person", scope_key=person_key)),
            "facts": [],
            "questions": [],
        }

    if session_memory_key:
        layers["session"] = {
            "label": "현재 세션 기억",
            "scope_type": "session",
            "scope_key": session_memory_key,
            "summary": "",
            "summary_provenance": {},
            "raw": _user_rows(read_jsonl(memory_raw_path(guild_id, scope_type="session", scope_key=session_memory_key))),
            "vault_raw": _user_rows(read_vault_raw_rows(guild_id, scope_type="session", scope_key=session_memory_key)),
            "facts": [],
            "questions": [],
        }

    return layers


__all__ = ["collect_memory_layers"]
