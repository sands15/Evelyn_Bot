from __future__ import annotations

import sys
from pathlib import Path
import unittest


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_ui_runtime import (
    ControlPageUiRuntimeDeps,
    append_control_page_chat_log_from_runtime,
    build_control_page_panel_state_from_runtime,
    control_page_effective_guild_id_from_runtime,
    control_page_effective_guild_name_from_runtime,
    control_page_local_url_from_runtime,
    control_page_session_key_from_runtime,
    enqueue_control_page_ui_command_from_runtime,
    get_control_page_chat_log_from_runtime,
    sanitize_control_page_welcome_text_from_runtime,
)  # noqa: E402


class _UiStore:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []


class FakeCommandStore(_UiStore):
    def enqueue(self, action: str, *, panel_id: str | None = None) -> dict[str, object]:
        payload: dict[str, object] = {"action": action, "panel_id": panel_id}
        self.rows.append(payload)
        return payload

    def panel_state(self) -> dict[str, object]:
        return {"actions": list(self.rows)}


class FakeChatLogStore(_UiStore):
    def __init__(self) -> None:
        super().__init__()
        self.rows_by_guild: dict[int, list[dict[str, object]]] = {}

    def append(self, guild_id: int, role: str, author: str, text: str) -> None:
        self.rows_by_guild.setdefault(int(guild_id), []).append({
            "guild_id": int(guild_id),
            "role": role,
            "author": author,
            "text": text,
        })

    def get(self, guild_id: int) -> list[dict[str, object]]:
        return list(self.rows_by_guild.get(int(guild_id), []))


def _build_deps() -> tuple[ControlPageUiRuntimeDeps, FakeCommandStore, FakeChatLogStore]:
    command_store = FakeCommandStore()
    chat_log_store = FakeChatLogStore()
    deps = ControlPageUiRuntimeDeps(
        control_page_host="127.0.0.1",
        control_page_port=8799,
        local_control_guild_id=999,
        local_control_guild_name="Evelyn Local",
        control_page_welcome_fallback="fallback",
        clean_text=lambda text: text.strip(),
        sanitize_control_page_welcome_text_payload=lambda text, fallback: text or fallback,
        control_page_ui_command_store=command_store,
        control_page_chat_log_store=chat_log_store,
    )
    return deps, command_store, chat_log_store


class ControlPageUiRuntimeTests(unittest.TestCase):
    def test_control_page_session_key_uses_local_guild_for_none_and_local_id(self) -> None:
        deps, _, _ = _build_deps()
        self.assertEqual(control_page_session_key_from_runtime(None, deps=deps), "control-page:local")
        self.assertEqual(control_page_session_key_from_runtime(999, deps=deps), "control-page:local")
        self.assertEqual(control_page_session_key_from_runtime(1001, deps=deps), "control-page:1001")

    def test_control_page_guild_and_panel_state_helpers(self) -> None:
        deps, command_store, _ = _build_deps()
        guild = type("Guild", (), {"id": 1001, "name": "DevGuild"})
        self.assertEqual(control_page_local_url_from_runtime(deps), "http://127.0.0.1:8799/")
        self.assertEqual(control_page_effective_guild_id_from_runtime(guild(), deps=deps), 1001)
        self.assertEqual(control_page_effective_guild_id_from_runtime(None, deps=deps), 999)
        self.assertEqual(control_page_effective_guild_name_from_runtime(guild(), deps=deps), "DevGuild")
        self.assertEqual(control_page_effective_guild_name_from_runtime(None, deps=deps), "Evelyn Local")

        payload = enqueue_control_page_ui_command_from_runtime("open", panel_id="memory", deps=deps)
        panel_state = build_control_page_panel_state_from_runtime(deps=deps)
        self.assertEqual(payload["action"], "open")
        self.assertEqual(panel_state["actions"][-1]["action"], "open")

    def test_append_and_read_control_page_chat_log(self) -> None:
        deps, _, chat_log_store = _build_deps()
        append_control_page_chat_log_from_runtime(11, "assistant", "bot", "hello", deps=deps)
        append_control_page_chat_log_from_runtime(11, "user", "alice", "hi", deps=deps)
        rows = get_control_page_chat_log_from_runtime(11, deps=deps)
        self.assertEqual(rows[0]["text"], "hello")
        self.assertEqual(rows[1]["author"], "alice")
        self.assertEqual(chat_log_store.rows_by_guild[11], rows)

    def test_sanitize_control_page_welcome_text_uses_payload_fallback(self) -> None:
        deps, _, _ = _build_deps()
        self.assertEqual(
            sanitize_control_page_welcome_text_from_runtime("", deps=deps),
            "fallback",
        )


if __name__ == "__main__":
    unittest.main()
