from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.autonomy_runtime_factory import (
    AutonomyRuntimeFactoryDeps,
    get_or_create_autonomy_engine_from_runtime,
)


class FakeGuild:
    def __init__(self, channels: dict[int, Any] | None = None) -> None:
        self.channels = channels or {}

    def get_channel(self, channel_id: int) -> Any:
        return self.channels.get(channel_id)


class AutonomyRuntimeFactoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self.engines: dict[int, Any] = {}
        self.channels = {10: SimpleNamespace(id=10, name="observe", send=object())}
        self.guild = FakeGuild(self.channels)
        self.history = [{"role": "user", "content": "최근 질문"}]
        self.refresh_tasks: dict[int, Any] = {}
        self.last_refresh: dict[int, float] = {}
        self.last_ping: dict[int, float] = {}
        self.followup_targets: dict[str, Any] = {}
        self.clock_values = iter([100.0, 100.25, 200.0, 200.5])
        self.deps = self.build_deps()

    def build_deps(self) -> AutonomyRuntimeFactoryDeps:
        async def send(channel: Any, text: str) -> None:
            self.events.append(("send", (channel, text)))

        async def update_cognitive(*args: Any, **kwargs: Any) -> dict[str, Any]:
            self.events.append(("refresh", (args, kwargs)))
            return {"updated_at": "now", "action": "reply", "confidence": 0.9}

        return AutonomyRuntimeFactoryDeps(
            autonomy_engines=self.engines,
            get_guild=lambda _guild_id: self.guild,
            get_observe_channel_ids=lambda _guild_id: [10],
            get_command_only_channel_ids=lambda _guild_id: [20],
            session_followup_targets=self.followup_targets,
            clean_text=lambda text: text.strip(),
            send_discord_text=send,
            question_cooldown_hit=lambda _key: False,
            evaluate_proactive_question_gate=lambda **_kwargs: SimpleNamespace(allowed=False),
            proactive_question_scope_candidates=lambda **_kwargs: [],
            select_question_to_ask=lambda *_args, **_kwargs: None,
            runtime_session_key=lambda **kwargs: f"runtime:{kwargs['guild_id']}",
            get_conversation_history=lambda **_kwargs: list(self.history),
            pick_recent_user_text=lambda history: history[-1]["content"] if history else "",
            localtime=lambda: SimpleNamespace(tm_hour=12),
            monotonic=lambda: next(self.clock_values),
            autonomy_last_cognitive_refresh_at=self.last_refresh,
            autonomy_cognitive_refresh_tasks=self.refresh_tasks,
            read_cached_cognitive_state=lambda _guild_id: {"action": "idle"},
            read_vision_watch_state=lambda: {"enabled": False},
            local_tts_snapshot=lambda: {"active": False},
            serialize_local_mic_runtime_state=lambda: {"ready": True},
            get_active_session_count=lambda: 2,
            get_inflight_llm_requests=lambda: 1,
            last_autonomy_ping_at=self.last_ping,
            answer_promises_search=lambda _text: False,
            append_history=lambda *args, **kwargs: self.events.append(("history", (args, kwargs))),
            schedule_memory_update=lambda *args, **kwargs: self.events.append(("memory", (args, kwargs))),
            mark_session_active=lambda *args, **kwargs: self.events.append(("session", (args, kwargs))),
            build_topic_id=lambda *parts: ":".join(parts),
            mark_self_state_assistant_output=lambda **kwargs: self.events.append(("self_state", kwargs)),
            select_and_mark_proactive_question=lambda **_kwargs: None,
            update_cognitive_state=update_cognitive,
            autonomy_cognitive_stale_sec=60.0,
            autonomy_cognitive_min_interval_sec=10.0,
            autonomy_cognitive_force_refresh_sec=120.0,
            vision_watch_interval_sec=5.0,
            active_conversation_text_question_sec=30.0,
            active_conversation_text_sec=15.0,
            autonomy_poll_interval_sec=4.0,
        )

    def create_engine(self):
        return get_or_create_autonomy_engine_from_runtime(11, deps=self.deps)

    def default_executor(self):
        return self.create_engine().executor.default_executor

    def test_factory_caches_engine_and_preserves_poll_interval(self) -> None:
        first = self.create_engine()
        second = self.create_engine()

        self.assertIs(first, second)
        self.assertIs(self.engines[11], first)
        self.assertEqual(first.poll_interval_sec, 4.0)

    async def test_notify_cleans_text_and_uses_preferred_channel(self) -> None:
        engine = self.create_engine()

        await engine.notify("  알림  ")

        self.assertEqual(self.events, [("send", (self.channels[10], "알림"))])

    async def test_observe_builds_runtime_snapshot(self) -> None:
        observation = await self.default_executor().observe()

        self.assertTrue(observation["connected"])
        self.assertEqual(observation["active_sessions"], 2)
        self.assertEqual(observation["inflight_llm_requests"], 1)
        self.assertEqual(observation["observe_channel_ids"], [10])
        self.assertEqual(observation["command_only_channel_ids"], [20])
        self.assertFalse(observation["quiet_hours"])

    async def test_send_followup_records_history_memory_session_and_ping(self) -> None:
        result = await self.default_executor().send_followup_fn(
            "후속 답변",
            awaiting_user_reply=True,
            user_text="사용자 질문",
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.last_ping[11], 100.0)
        kinds = [kind for kind, _payload in self.events]
        self.assertEqual(kinds, ["send", "history", "memory", "session", "self_state"])
        session_payload = next(payload for kind, payload in self.events if kind == "session")
        self.assertEqual(session_payload[1]["ttl_sec"], 30.0)
        self.assertTrue(session_payload[1]["awaiting_user_reply"])

    async def test_send_followup_blocks_without_channel(self) -> None:
        self.guild = FakeGuild()

        result = await self.default_executor().send_followup_fn("후속 답변")

        self.assertEqual(result, {"status": "blocked", "reason": "no_followup_channel"})

    async def test_refresh_cognitive_state_tracks_and_clears_task(self) -> None:
        result = await self.default_executor().refresh_cognitive_state_fn()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["reason"], "router_refreshed")
        self.assertEqual(result["elapsed_ms"], 250.0)
        self.assertEqual(self.last_refresh[11], 100.0)
        self.assertNotIn(11, self.refresh_tasks)

    async def test_maybe_ping_respects_recent_ping_cooldown(self) -> None:
        self.last_ping[11] = 50.0

        result = await self.default_executor().maybe_ping_user_fn("확인")

        self.assertEqual(result, {"status": "blocked", "reason": "ping_cooldown"})

    def test_main_delegates_autonomy_factory_to_runtime_module(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        start = source.index("def get_or_create_autonomy_engine(")
        end = source.index("def remember_session_followup_target(", start)
        function_source = source[start:end]

        self.assertIn("get_or_create_autonomy_engine_from_runtime(", function_source)
        self.assertNotIn("async def _default_observe", function_source)
        self.assertNotIn("AutonomyEngine(", function_source)


if __name__ == "__main__":
    unittest.main()
