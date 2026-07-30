from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.search_followup_runtime import (  # noqa: E402
    SearchFollowupRuntimeDeps,
    build_search_query_from_runtime,
    deliver_proactive_followup_from_runtime,
)


def build_deps(
    *,
    get_conversation_history_result=None,
    compact_summary: str = "메모 요약",
    history_calls: list[dict[str, int | str | None]] | None = None,
    summary_path_calls: list[int | None] | None = None,
    summary_read_calls: list[object] | None = None,
) -> SearchFollowupRuntimeDeps:
    history_calls = [] if history_calls is None else history_calls
    summary_path_calls = [] if summary_path_calls is None else summary_path_calls
    summary_read_calls = [] if summary_read_calls is None else summary_read_calls

    def _get_conversation_history(*, session_key: str | None, guild_id: int | None):
        history_calls.append({"session_key": session_key, "guild_id": guild_id})
        return get_conversation_history_result or []

    def _memory_summary_path(guild_id: int):
        summary_path_calls.append(guild_id)
        return f"summary:{guild_id}"

    def _read_text_file(path):
        summary_read_calls.append(path)
        return "raw summary"

    def _compact_working_summary(text: str) -> str:
        return f"compact::{text}::{compact_summary}"

    async def _commit_session_continuity():
        return {"generation": 1}

    return SearchFollowupRuntimeDeps(
        bot=object(),
        discord_object_factory=lambda **kwargs: object(),
        session_followup_targets={},
        background_search_tasks={},
        inflight_search_tasks={},
        apply_runtime_mode=lambda runtime_mode="normal": {"skip_search_followup": False},
        parse_response_action_tag=lambda text: (None, text),
        answer_promises_search=lambda text: False,
        build_search_query=lambda *args, **kwargs: "",
        runtime_session_key=lambda *args, **kwargs: None,
        remember_session_followup_target=lambda *args, **kwargs: None,
        get_conversation_history=_get_conversation_history,
        memory_summary_path=_memory_summary_path,
        read_text_file=_read_text_file,
        compact_working_summary=_compact_working_summary,
        search_duckduckgo=lambda *args, **kwargs: [],
        answer_from_search_results=lambda *args, **kwargs: "",
        resolve_open_question_rows=lambda *args, **kwargs: 0,
        write_json_file=lambda *args, **kwargs: None,
        cognitive_state_path=lambda *args, **kwargs: None,
        send_discord_text=lambda *args, **kwargs: None,
        format_display_text=lambda *args, **kwargs: "",
        speak_answer=lambda *args, **kwargs: None,
        current_turn_id=lambda *args, **kwargs: "turn",
        append_history=lambda *args, **kwargs: None,
        schedule_memory_update=lambda *args, **kwargs: None,
        create_turn_scoped_task=lambda *args, **kwargs: None,
        attach_current_task=lambda *args, **kwargs: None,
        detach_task=lambda *args, **kwargs: None,
        record_search_followup_queued=lambda: None,
        commit_session_continuity=_commit_session_continuity,
        log=lambda *args, **kwargs: None,
    )


class SearchFollowupRuntimeTests(unittest.TestCase):
    def test_build_search_query_uses_provided_messages_without_history_lookup(self) -> None:
        history_calls: list[dict[str, int | str | None]] = []
        summary_path_calls: list[int | None] = []
        summary_read_calls: list[object] = []
        deps = build_deps(
            history_calls=history_calls,
            summary_path_calls=summary_path_calls,
            summary_read_calls=summary_read_calls,
        )
        query = build_search_query_from_runtime(
            None,
            "이건 긴 검색 질의입니다",
            messages=[{"role": "user", "content": "과거 문장"}, {"role": "assistant", "content": "답변"}],
            deps=deps,
        )

        self.assertEqual(query, "이건 긴 검색 질의입니다")
        self.assertEqual(history_calls, [])
        self.assertEqual(summary_path_calls, [])
        self.assertEqual(summary_read_calls, [])

    def test_build_search_query_uses_history_and_memory_summary_when_messages_missing(self) -> None:
        history_calls: list[dict[str, int | str | None]] = []
        summary_path_calls: list[int | None] = []
        summary_read_calls: list[object] = []
        deps = build_deps(
            get_conversation_history_result=[{"role": "user", "content": "오픈AI"}],
            compact_summary="요약 텍스트",
            history_calls=history_calls,
            summary_path_calls=summary_path_calls,
            summary_read_calls=summary_read_calls,
        )
        query = build_search_query_from_runtime(
            42,
            "짧음",
            session_key="session-42",
            deps=deps,
        )

        self.assertEqual(query, "짧음 compact::raw summary::요약 텍스트")
        self.assertEqual(history_calls, [{"session_key": "session-42", "guild_id": 42}])
        self.assertEqual(summary_path_calls, [42])
        self.assertEqual(summary_read_calls, ["summary:42"])

    def test_delivered_text_is_committed_before_optional_voice(self) -> None:
        events: list[str] = []

        class Channel:
            async def send(self, *_args, **_kwargs):
                return None

        class Voice:
            def is_connected(self):
                return True

        class Bot:
            def get_guild(self, _guild_id):
                return type("Guild", (), {"voice_client": Voice()})()

            def get_channel(self, _channel_id):
                return Channel()

        async def send(*_args, **_kwargs):
            events.append("send")

        async def commit():
            events.append("commit")
            return {"generation": 2}

        async def speak(*_args, **_kwargs):
            events.append("voice")
            raise RuntimeError("optional voice failed")

        deps = build_deps()
        deps = SearchFollowupRuntimeDeps(
            **{
                **deps.__dict__,
                "bot": Bot(),
                "send_discord_text": send,
                "append_history": lambda *_args, **_kwargs: events.append(
                    "history"
                ),
                "schedule_memory_update": lambda *_args, **_kwargs: events.append(
                    "memory"
                ),
                "commit_session_continuity": commit,
                "speak_answer": speak,
            }
        )

        asyncio.run(
            deliver_proactive_followup_from_runtime(
                1,
                "query",
                "answer",
                deps=deps,
                session_key="session",
                room_key=None,
                person_key=None,
                session_memory_key=None,
                channel_id=10,
                source="search",
            )
        )

        self.assertEqual(
            events,
            ["send", "history", "commit", "memory", "voice"],
        )


if __name__ == "__main__":
    unittest.main()
