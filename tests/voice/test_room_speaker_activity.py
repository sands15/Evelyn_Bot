from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.room_speaker_activity import RoomSpeakerActivityStore  # noqa: E402
from evelyn_core.session_memory_state import SessionStateStore  # noqa: E402


class RoomSpeakerActivityTests(unittest.TestCase):
    def test_create_empty_owns_three_distinct_maps(self) -> None:
        store = RoomSpeakerActivityStore.create_empty()
        store.room_owner_user_ids["room"] = 1

        self.assertEqual(store.room_owner_user_ids, {"room": 1})
        self.assertEqual(store.room_owner_until, {})
        self.assertEqual(store.recent_speaker_stats, {})

    def test_restore_owners_uses_only_unique_latest_active_canonical_voice_session(self) -> None:
        sessions = SessionStateStore.create_empty()
        sessions.active_user_ids.update(
            {
                "guild:1:voice:2:user:3": 3,
                "guild:1:voice:2:user:4": 4,
                "guild:1:voice:5:user:6": 6,
                "guild:1:voice:5:user:7": 7,
                "guild:1:voice:none:user:8": 8,
                "guild:01:voice:9:user:10": 10,
                "guild:1:text:9:user:11": 11,
                "guild:1:voice:10:user:12": 99,
                "guild:1:voice:10:user:17": 17,
                "guild:1:voice:11:user:13": 13,
                "guild:1:voice:12:user:14": 14,
                "guild:1:voice:12:user:15": 15,
                "guild:1:voice:13:user:16": 16,
            }
        )
        sessions.active_until.update(
            {key: 140.0 for key in sessions.active_user_ids}
        )
        sessions.active_until["guild:1:voice:11:user:13"] = 99.0
        sessions.last_active_at.update(
            {
                "guild:1:voice:2:user:3": 90.0,
                "guild:1:voice:2:user:4": 95.0,
                "guild:1:voice:5:user:6": 97.0,
                "guild:1:voice:5:user:7": 97.0,
                "guild:1:voice:none:user:8": 98.0,
                "guild:01:voice:9:user:10": 98.0,
                "guild:1:text:9:user:11": 98.0,
                "guild:1:voice:10:user:12": 98.0,
                "guild:1:voice:10:user:17": 90.0,
                "guild:1:voice:11:user:13": 98.0,
                "guild:1:voice:12:user:14": 94.0,
                "guild:1:voice:12:user:15": 99.0,
                "guild:1:voice:13:user:16": -40.0,
            }
        )
        sessions.active_until["guild:1:voice:12:user:15"] = 99.5
        sessions.active_user_ids.pop("guild:1:voice:12:user:15")
        store = RoomSpeakerActivityStore({}, {"stale": 1}, {"stale": 999.0})

        restored = store.restore_owners_from_sessions(sessions, now=100.0)

        self.assertEqual(restored, 2)
        self.assertEqual(
            store.room_owner_user_ids,
            {"guild:1:voice:2": 4, "guild:1:voice:13": 16},
        )
        self.assertEqual(
            store.room_owner_until,
            {"guild:1:voice:2": 140.0, "guild:1:voice:13": 140.0},
        )

    def test_prune_removes_stale_speakers(self) -> None:
        stats = {
            "room": {
                1: {"last_packet_at": 10.0},
                2: {"last_packet_at": 7.4},
            }
        }
        store = RoomSpeakerActivityStore(stats, {}, {})

        keep = store.prune("room", now=10.0)

        self.assertEqual(list(keep), [1])
        self.assertEqual(stats["room"], {1: {"last_packet_at": 10.0}})

    def test_update_records_decayed_activity(self) -> None:
        store = RoomSpeakerActivityStore(
            {"room": {1: {"last_packet_at": 19.8, "recent_voiced_ms": 100.0, "recent_raw_ms": 200.0, "body_rms": 0.4}}},
            {},
            {},
        )

        entry = store.update("room", 1, voiced_ms=40.0, raw_seconds=0.1, rms=0.1, wake_detected=True, now=20.0)

        self.assertEqual(entry["last_packet_at"], 20.0)
        self.assertAlmostEqual(entry["recent_voiced_ms"], 55.0)
        self.assertAlmostEqual(entry["recent_raw_ms"], 110.0)
        self.assertAlmostEqual(entry["body_rms"], 0.24)
        self.assertEqual(entry["wake_priority"], 20.0)

    def test_active_owner_wins_when_recent(self) -> None:
        store = RoomSpeakerActivityStore(
            {
                "room": {
                    1: {"last_packet_at": 19.7, "recent_voiced_ms": 1.0, "body_rms": 0.1},
                    2: {"last_packet_at": 20.0, "recent_voiced_ms": 100.0, "body_rms": 0.9},
                }
            },
            {"room": 1},
            {"room": 21.0},
        )

        self.assertEqual(store.pick_active_speaker("room", now=20.0), 1)

    def test_scores_wake_then_voice_then_rms_then_recency(self) -> None:
        store = RoomSpeakerActivityStore(
            {
                "room": {
                    1: {"last_packet_at": 20.0, "recent_voiced_ms": 120.0, "body_rms": 0.4, "wake_priority": 0.0},
                    2: {"last_packet_at": 19.9, "recent_voiced_ms": 10.0, "body_rms": 0.1, "wake_priority": 19.5},
                }
            },
            {},
            {},
        )

        self.assertEqual(store.pick_active_speaker("room", now=20.0), 2)

    def test_empty_keys_are_noops(self) -> None:
        store = RoomSpeakerActivityStore({}, {}, {})

        self.assertEqual(store.prune(None), {})
        self.assertEqual(store.update(None, 1, voiced_ms=1, raw_seconds=1, rms=1), {})
        self.assertIsNone(store.pick_active_speaker(None))


if __name__ == "__main__":
    unittest.main()
