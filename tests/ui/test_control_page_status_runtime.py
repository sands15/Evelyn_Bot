from __future__ import annotations

import sys
from pathlib import Path
import asyncio
from types import SimpleNamespace
import unittest


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.control_page_status_runtime import (  # noqa: E402
    ControlPageStatusRuntimeDeps,
    build_control_page_autonomy_reply_from_runtime,
    build_control_page_local_status_text_from_runtime,
    build_control_page_inventory_reply_from_runtime,
    build_control_page_minecraft_reply_from_runtime,
    build_control_page_status_reply_from_runtime,
    build_control_page_status_text_from_runtime,
    build_control_page_voice_continuity_reply_from_runtime,
    build_control_page_voice_status_reply_from_runtime,
)


def _deps(**overrides) -> ControlPageStatusRuntimeDeps:
    values = dict(
        model_name="main",
        router_model_name="router",
        summary_model_name="summary",
        stt_model_name="stt",
        discord_enabled=True,
        bot_api_host="127.0.0.1",
        bot_api_port=8798,
        control_page_local_url=lambda: "http://127.0.0.1:8799/",
        voice_input_mode_status_line=lambda: "voice-mode",
        local_mic_status_line=lambda: "mic-ready",
        current_tts_target_name=lambda guild: "Alice",
        is_tracked_tts_playback_active=lambda guild_id: True,
        local_tts_snapshot=lambda: {"active": True},
        local_mic_runtime_state=lambda: {"enabled": True, "captureReady": True},
        build_voice_pipeline_snapshot=lambda guild: {"bargeInContinuity": {"count": 2}},
        format_voice_continuity_detail_lines=lambda continuity: [f"count={continuity.get('count', 0)}"],
        build_status_text_payload=lambda **kwargs: f"{kwargs['guild_name']}|{kwargs['tts_target']}|{kwargs['voice_input_mode']}",
        build_local_status_text_payload=lambda runtime_services, **kwargs: f"{kwargs['local_url']}|{kwargs['local_speaking']}|{kwargs['local_listening']}",
        build_voice_status_reply_payload=lambda voice, **kwargs: f"{kwargs['channel_name']}|{kwargs['continuity_detail_lines'][0]}",
        build_voice_continuity_reply_payload=lambda lines: "\n".join(lines),
        get_control_page_minecraft_snapshot=lambda guild_id: {
            "dummy": True,
            "inventory": ["sword", "pickaxe"],
            "completed_count": 1,
            "goal": "mine",
        },
        build_control_page_inventory_reply_payload=lambda payload: f"inventory:{len(payload.get('inventory', []))}",
        build_control_page_minecraft_reply_payload=lambda payload: f"minecraft:{payload.get('goal', 'none')}",
        get_autonomy_engine=lambda _guild_id: None,
        get_routed_autonomy_executor=lambda _guild_id: None,
        build_control_page_autonomy_reply_payload=lambda **kwargs: (
            f"status={kwargs['status']}|goal={kwargs['goal']}|plan={kwargs['plan']}|minecraft={kwargs['minecraft_enabled']}|actions={kwargs['allowed_actions']}"
        ),
    )
    values.update(overrides)
    return ControlPageStatusRuntimeDeps(**values)


class ControlPageStatusRuntimeTests(unittest.TestCase):
    def test_guild_status_uses_voice_channel_and_tts_target(self) -> None:
        voice_client = SimpleNamespace(channel=SimpleNamespace(name="General"), is_listening=lambda: True)
        guild = SimpleNamespace(id=7, name="Guild", voice_client=voice_client)

        text = build_control_page_status_text_from_runtime(guild, {"inventory": []}, deps=_deps())

        self.assertEqual(text, "Guild|Alice|voice-mode")

    def test_local_status_uses_local_tts_and_mic_state(self) -> None:
        text = build_control_page_local_status_text_from_runtime({"mainReady": True}, deps=_deps())

        self.assertEqual(text, "http://127.0.0.1:8799/|True|True")

    def test_voice_status_and_continuity_use_detail_formatter(self) -> None:
        guild = SimpleNamespace(voice_client=SimpleNamespace(channel=SimpleNamespace(name="Voice")))
        deps = _deps()

        status = build_control_page_voice_status_reply_from_runtime(guild, deps=deps)
        continuity = build_control_page_voice_continuity_reply_from_runtime({"count": 3}, deps=deps)

        self.assertEqual(status, "Voice|count=2")
        self.assertEqual(continuity, "count=3")

    def test_inventory_and_minecraft_reply_use_current_snapshot(self) -> None:
        async def run() -> None:
            guild = SimpleNamespace(id=7)
            deps = _deps(
                get_control_page_minecraft_snapshot=lambda guild_id: asyncio.sleep(0, {"dummy": False, "inventory": ["a"]}),
            )
            inventory = await build_control_page_inventory_reply_from_runtime(guild, deps=deps)
            minecraft = await build_control_page_minecraft_reply_from_runtime(guild, deps=deps)

            self.assertEqual(inventory, "inventory:1")
            self.assertEqual(minecraft, "minecraft:none")

        asyncio.run(run())

    def test_status_reply_fetches_snapshot(self) -> None:
        async def run() -> None:
            guild = SimpleNamespace(
                id=7,
                name="Guild",
                voice_client=SimpleNamespace(
                    channel=SimpleNamespace(name="General"),
                    is_listening=lambda: True,
                ),
            )
            calls = {"count": 0}

            async def snapshot(guild_id: int) -> dict[str, Any]:
                calls["count"] += 1
                self.assertEqual(guild_id, 7)
                return {"inventory": []}

            text = await build_control_page_status_reply_from_runtime(guild, deps=_deps(get_control_page_minecraft_snapshot=snapshot))
            self.assertEqual(text, "Guild|Alice|voice-mode")
            self.assertEqual(calls["count"], 1)

        asyncio.run(run())

    def test_autonomy_reply_falls_back_when_engine_missing_or_present(self) -> None:
        guild = SimpleNamespace(id=9)
        deps = _deps()
        self.assertEqual(
            build_control_page_autonomy_reply_from_runtime(guild, deps=deps),
            "자율 행동 엔진이 아직 만들어지지 않았어.",
        )

        engine = SimpleNamespace(
            state=SimpleNamespace(
                status="running",
                safety_mode="안전",
                current_goal=SimpleNamespace(summary="보급 확보"),
                current_plan=SimpleNamespace(summary="채굴 계획"),
                drive_state={"mood": "normal"},
                failure_count=2,
                last_error="없음",
                allowed_actions=["move", "build"],
            )
        )
        deps = _deps(
            get_autonomy_engine=lambda _guild_id: engine,
            get_routed_autonomy_executor=lambda _guild_id: SimpleNamespace(
                is_domain_enabled=lambda domain: domain == "minecraft",
            ),
            build_control_page_autonomy_reply_payload=lambda **kwargs: (
                f"{kwargs['goal']}|{kwargs['plan']}|{kwargs['minecraft_enabled']}|{kwargs['allowed_actions']}"
            ),
        )
        self.assertEqual(
            build_control_page_autonomy_reply_from_runtime(guild, deps=deps),
            "보급 확보|채굴 계획|True|['move', 'build']",
        )


if __name__ == "__main__":
    unittest.main()
