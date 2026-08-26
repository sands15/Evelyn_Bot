from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.conversation_session_composition import (
    ConversationSessionComposition,
    ConversationSessionCompositionDeps,
)


class FakeRoomSpeakerStore:
    def __init__(self) -> None:
        self.active_speaker = 7
        self.calls: list[tuple] = []

    def prune(self, room_key, *, now=None):
        self.calls.append(("prune", room_key, now))
        return {7: {"voiced_ms": 100.0}}

    def update(self, room_key, user_id, **kwargs):
        self.calls.append(("update", room_key, user_id, kwargs))
        return {"voiced_ms": kwargs["voiced_ms"]}

    def pick_active_speaker(self, room_key):
        self.calls.append(("pick", room_key))
        return self.active_speaker


class ConversationSessionCompositionTests(unittest.TestCase):
    def build_composition(self):
        session_deps = object()
        room_store = FakeRoomSpeakerStore()
        owner_ids: dict[str, int] = {}
        owner_until: dict[str, float] = {}
        reply_in_progress: dict[str, bool] = {}
        events: list[tuple] = []
        composition = ConversationSessionComposition(
            ConversationSessionCompositionDeps(
                session=lambda: session_deps,
                room_owner_user_ids=owner_ids,
                room_owner_until=owner_until,
                room_reply_in_progress=reply_in_progress,
                room_speaker_activity_store=room_store,
                monotonic=lambda: 100.0,
                log_event=lambda *args, **kwargs: events.append((args, kwargs)),
            )
        )
        return composition, session_deps, room_store, owner_ids, owner_until, reply_in_progress, events

    def test_session_turn_adapters_use_the_live_dependency_factory(self) -> None:
        composition, session_deps, *_ = self.build_composition()
        started = object()

        with patch(
            "evelyn_core.conversation_session_composition.begin_user_text_turn_from_runtime",
            return_value=started,
        ) as begin, patch(
            "evelyn_core.conversation_session_composition.mark_session_active_from_runtime",
        ) as mark_active:
            result = composition.begin_user_text_turn(
                "session-1",
                "hello",
                guild_id=10,
                user_id=20,
                turn_id="claimed-turn",
            )
            composition.mark_session_active(
                "session-1", ttl_sec=30.0, awaiting_user_reply=True
            )

        self.assertIs(result, started)
        begin.assert_called_once_with(
            "session-1",
            "hello",
            guild_id=10,
            user_id=20,
            turn_id="claimed-turn",
            deps=session_deps,
        )
        mark_active.assert_called_once_with(
            "session-1",
            user_id=None,
            speaker="assistant",
            ttl_sec=30.0,
            awaiting_user_reply=True,
            topic_id=None,
            answer_text=None,
            user_text=None,
            deps=session_deps,
        )

    def test_history_adapters_preserve_arguments(self) -> None:
        composition, session_deps, *_ = self.build_composition()

        with patch(
            "evelyn_core.conversation_session_composition.append_history_from_runtime",
        ) as append, patch(
            "evelyn_core.conversation_session_composition.recent_assistant_reply_summary_from_runtime",
            return_value="summary",
        ) as recent:
            composition.append_history("session-1", "user", "answer", guild_id=5)
            result = composition.recent_assistant_reply_summary(
                session_key="session-1", guild_id=5, limit=2
            )

        append.assert_called_once_with(
            "session-1", "user", "answer", guild_id=5, deps=session_deps
        )
        recent.assert_called_once_with(
            session_key="session-1", guild_id=5, limit=2, deps=session_deps
        )
        self.assertEqual(result, "summary")

    def test_history_adapter_forwards_memory_receipt(self) -> None:
        composition, session_deps, *_ = self.build_composition()
        receipt = {
            "schema": "conversation.memory-receipt-ref.v1",
            "state": "not_used",
            "memoryVersion": 0,
            "suppliedNoteIds": [],
            "suppliedNoteCount": 0,
            "contentFree": True,
        }

        with patch(
            "evelyn_core.conversation_session_composition.append_history_from_runtime",
        ) as append:
            composition.append_history(
                "session-1",
                "user",
                "answer",
                guild_id=5,
                memory_receipt=receipt,
            )

        append.assert_called_once_with(
            "session-1",
            "user",
            "answer",
            guild_id=5,
            memory_receipt=receipt,
            deps=session_deps,
        )

    def test_room_activity_adapters_share_the_injected_store(self) -> None:
        composition, _, store, *_ = self.build_composition()

        self.assertEqual(
            composition.prune_room_speaker_stats("room-1", now=90.0),
            {7: {"voiced_ms": 100.0}},
        )
        self.assertEqual(
            composition.update_room_speaker_activity(
                "room-1",
                7,
                voiced_ms=120.0,
                raw_seconds=0.8,
                rms=0.2,
                wake_detected=True,
            ),
            {"voiced_ms": 120.0},
        )
        self.assertEqual(composition.pick_active_speaker("room-1"), 7)
        self.assertEqual([call[0] for call in store.calls], ["prune", "update", "pick"])

    def test_room_owner_policy_uses_live_maps_and_event_callback(self) -> None:
        composition, _, _, owner_ids, owner_until, reply_in_progress, events = self.build_composition()

        composition.set_room_owner(
            "room-1",
            7,
            ttl_sec=30.0,
            reason="wake",
            session_key="session-1",
            turn_id="turn-1",
            segment_id=2,
        )
        composition.set_room_reply_in_progress("room-1", True, owner_user_id=7)

        self.assertEqual(owner_ids["room-1"], 7)
        self.assertEqual(owner_until["room-1"], 130.0)
        self.assertTrue(reply_in_progress["room-1"])
        self.assertTrue(composition.is_room_owner_active("room-1", 7))
        self.assertGreaterEqual(len(events), 2)

        composition.clear_room_owner("room-1")
        self.assertNotIn("room-1", owner_ids)
        self.assertNotIn("room-1", owner_until)

    def test_all_moved_public_signatures_match_previous_main(self) -> None:
        mapping = {
            "new_conversation_history": "new_conversation_history",
            "remember_session_followup_target": "remember_session_followup_target",
            "build_topic_id": "build_topic_id",
            "new_turn_id": "new_turn_id",
            "current_turn_id": "current_turn_id",
            "next_segment_id": "next_segment_id",
            "start_new_turn": "start_new_turn",
            "begin_user_text_turn": "begin_user_text_turn",
            "finish_assistant_text_turn": "finish_assistant_text_turn",
            "session_state_snapshot": "session_state_snapshot",
            "discord_room_session_policy": "discord_room_session_policy",
            "_clear_room_owner": "clear_room_owner",
            "room_state_snapshot": "room_state_snapshot",
            "_prune_room_speaker_stats": "prune_room_speaker_stats",
            "update_room_speaker_activity": "update_room_speaker_activity",
            "pick_active_speaker": "pick_active_speaker",
            "is_room_owner_active": "is_room_owner_active",
            "set_room_owner": "set_room_owner",
            "set_room_reply_in_progress": "set_room_reply_in_progress",
            "increment_session_bad_audio": "increment_session_bad_audio",
            "reset_session_bad_audio": "reset_session_bad_audio",
            "update_session_state": "update_session_state",
            "mark_session_active": "mark_session_active",
            "is_session_active_for_user": "is_session_active_for_user",
            "get_conversation_history": "get_conversation_history",
            "trim_history": "trim_history",
            "append_history": "append_history",
            "recent_assistant_reply_summary": "recent_assistant_reply_summary",
            "persona_state_hint_for_turn": "persona_state_hint_for_turn",
        }
        old_tree = ast.parse(
            subprocess.check_output(
                ["git", "show", "5136ea8:main.py"], text=True, encoding="utf-8"
            )
        )
        new_tree = ast.parse(
            (RUNTIME_ROOT / "evelyn_core" / "conversation_session_composition.py").read_text(
                encoding="utf-8"
            )
        )
        old_functions = {
            node.name: node
            for node in old_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        composition_class = next(
            node
            for node in new_tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ConversationSessionComposition"
        )
        new_methods = {
            node.name: node
            for node in composition_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        def signature(node, *, method=False):
            positional = [arg.arg for arg in node.args.posonlyargs + node.args.args]
            if method:
                positional = positional[1:]
            return (
                isinstance(node, ast.AsyncFunctionDef),
                positional,
                [ast.unparse(default) for default in node.args.defaults],
                node.args.vararg.arg if node.args.vararg else None,
                [
                    (arg.arg, None if default is None else ast.unparse(default))
                    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults)
                ],
                node.args.kwarg.arg if node.args.kwarg else None,
            )

        mismatches = []
        additive_keyword_defaults = {
            "begin_user_text_turn": (
                ("turn_id", "None"),
                ("precommit_user_only", "False"),
            ),
            "finish_assistant_text_turn": (
                ("memory_receipt", "None"),
                ("complete_turn_id", "None"),
            ),
            "append_history": (
                ("memory_receipt", "None"),
                ("complete_turn_id", "None"),
            ),
        }
        for old_name, new_name in mapping.items():
            old_signature = signature(old_functions[old_name])
            new_signature = signature(new_methods[new_name], method=True)
            comparable_signature = new_signature
            if old_name in additive_keyword_defaults:
                additions = additive_keyword_defaults[old_name]
                self.assertEqual(
                    tuple(new_signature[4][-len(additions):]),
                    additions,
                )
                comparable_signature = (
                    *new_signature[:4],
                    new_signature[4][:-len(additions)],
                    new_signature[5],
                )
            if old_signature != comparable_signature:
                mismatches.append((old_name, old_signature, new_signature))

        self.assertEqual(mismatches, [])

    def test_main_uses_explicit_composition_bindings_before_consumers(self) -> None:
        source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
        runtime_source = (
            RUNTIME_ROOT / "evelyn_core" / "conversation_session_composition.py"
        ).read_text(encoding="utf-8")

        composition_index = source.index(
            "conversation_session_composition = ConversationSessionComposition("
        )
        autonomy_index = source.index(
            "autonomy_runtime_composition = AutonomyRuntimeComposition("
        )
        self.assertLess(composition_index, autonomy_index)
        self.assertIn(
            "append_history = conversation_session_composition.append_history",
            source,
        )
        self.assertIn(
            "_clear_room_owner = conversation_session_composition.clear_room_owner",
            source,
        )
        self.assertNotIn("globals()", runtime_source)


if __name__ == "__main__":
    unittest.main()
