import sys
import unittest
from pathlib import Path


REPO_ROOT = next(path for path in Path(__file__).resolve().parents if (path / "main.py").exists())
RUNTIME_ROOT = REPO_ROOT / "evelyn_core" / "runtime"
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from evelyn_core.discord_session_policy import (  # noqa: E402
    DiscordRoomSessionPolicy,
    LocalMicDiscordSuppressionInput,
    TtsInterruptMeta,
    VoiceReplyGateInput,
    decide_local_mic_discord_suppression,
    decide_voice_reply_gate,
    is_short_followup_candidate_policy,
    should_ignore_short_transcription_policy,
    should_interrupt_tts,
    should_skip_full_stt_after_wake_probe_policy,
)


def normalize(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def contains_wake_word(text: str) -> bool:
    return "evelyn" in normalize(text)


def false_predicate(_text: str) -> bool:
    return False


def is_similar(left: str, right: str) -> bool:
    return normalize(left) == normalize(right)


def decide(**overrides):
    base = {
        "text": "evelyn hello",
        "wake_detected": False,
        "wake_match_mode": "",
        "user_id": 10,
        "owner_user_id": None,
        "owner_active": False,
        "active_session": False,
        "awaiting_user_reply": False,
        "active_speaker_user_id": None,
        "last_stt_text": "",
        "tts_suppression": None,
        "cooldown_active": False,
    }
    base.update(overrides)
    return decide_voice_reply_gate(
        VoiceReplyGateInput(**base),
        normalize_voice_text=normalize,
        contains_wake_word=contains_wake_word,
        looks_like_brief_filler_text=false_predicate,
        looks_like_repetitive_noise_text=false_predicate,
        is_similar=is_similar,
        min_text_len=3,
    )


class DiscordSessionPolicyTests(unittest.TestCase):
    def test_wake_word_accepts_entry(self) -> None:
        decision = decide()

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.gate_mode, "wake_entry")

    def test_owner_followup_accepts_without_wake(self) -> None:
        decision = decide(
            text="continue that",
            owner_user_id=10,
            owner_active=True,
            active_session=True,
        )

        self.assertTrue(decision.accepted)
        self.assertEqual(decision.gate_mode, "owner_followup")

    def test_other_owner_requires_exact_wake_for_takeover(self) -> None:
        no_wake = decide(text="hello", owner_user_id=20, wake_detected=False)
        fuzzy_wake = decide(text="evelyn hello", owner_user_id=20, wake_detected=True, wake_match_mode="fuzzy")
        exact_wake = decide(text="evelyn hello", owner_user_id=20, wake_detected=True, wake_match_mode="exact")

        self.assertFalse(no_wake.accepted)
        self.assertEqual(no_wake.reason, "owner_mismatch_needs_wake")
        self.assertFalse(fuzzy_wake.accepted)
        self.assertEqual(fuzzy_wake.reason, "owner_takeover_requires_exact_wake")
        self.assertTrue(exact_wake.accepted)
        self.assertEqual(exact_wake.gate_mode, "owner_takeover")

    def test_tts_suppression_wins_before_text_gate(self) -> None:
        decision = decide(text="", tts_suppression="tts_post_playback")

        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason, "tts_post_playback")

    def test_duplicate_and_cooldown_reject(self) -> None:
        duplicate = decide(text="evelyn hello", last_stt_text="evelyn hello")
        cooldown = decide(text="evelyn hello", cooldown_active=True)

        self.assertFalse(duplicate.accepted)
        self.assertEqual(duplicate.reason, "duplicate")
        self.assertFalse(cooldown.accepted)
        self.assertEqual(cooldown.reason, "cooldown")

    def test_local_mic_policy_never_suppresses_local_mic_source(self) -> None:
        decision = decide_local_mic_discord_suppression(
            LocalMicDiscordSuppressionInput(
                member_id=10,
                source="local_mic",
                input_mode="auto",
                capture_ready=True,
                local_mic_recent=True,
                preferred_user_ids={10},
            ),
            normalize_voice_input_mode=normalize,
            should_route_discord_user_to_local_mic=lambda user_id, *, preferred_user_ids, capture_ready: True,
        )

        self.assertFalse(decision.suppress)

    def test_local_mic_policy_suppresses_preferred_user_in_local_mode(self) -> None:
        decision = decide_local_mic_discord_suppression(
            LocalMicDiscordSuppressionInput(
                member_id=10,
                source="discord_voice",
                input_mode="local",
                capture_ready=False,
                local_mic_recent=False,
                preferred_user_ids={10},
            ),
            normalize_voice_input_mode=normalize,
            should_route_discord_user_to_local_mic=lambda user_id, *, preferred_user_ids, capture_ready: (
                capture_ready and user_id in preferred_user_ids
            ),
        )

        self.assertTrue(decision.suppress)
        self.assertEqual(decision.normalized_input_mode, "local")

    def test_local_mic_policy_auto_requires_recent_local_mic_and_capture_ready(self) -> None:
        def route(user_id, *, preferred_user_ids, capture_ready):
            return capture_ready and user_id in preferred_user_ids

        no_recent = decide_local_mic_discord_suppression(
            LocalMicDiscordSuppressionInput(10, "discord_voice", "auto", True, False, {10}),
            normalize_voice_input_mode=normalize,
            should_route_discord_user_to_local_mic=route,
        )
        recent = decide_local_mic_discord_suppression(
            LocalMicDiscordSuppressionInput(10, "discord_voice", "auto", True, True, {10}),
            normalize_voice_input_mode=normalize,
            should_route_discord_user_to_local_mic=route,
        )

        self.assertFalse(no_recent.suppress)
        self.assertTrue(recent.suppress)

    def test_tts_interrupt_allows_wake_active_speaker_and_strong_vad(self) -> None:
        self.assertTrue(should_interrupt_tts(TtsInterruptMeta(wake_detected=True, audio_sec=0.18)))
        self.assertTrue(
            should_interrupt_tts(
                TtsInterruptMeta(
                    active_speaker_match=True,
                    voice_like=True,
                    vad_prob=0.55,
                    audio_sec=0.35,
                )
            )
        )
        self.assertTrue(should_interrupt_tts(TtsInterruptMeta(vad_prob=0.6, audio_sec=0.35, rms_ok=True)))

    def test_tts_interrupt_rejects_short_or_weak_audio(self) -> None:
        self.assertFalse(should_interrupt_tts(TtsInterruptMeta(wake_detected=True, audio_sec=0.17)))
        self.assertFalse(
            should_interrupt_tts(
                TtsInterruptMeta(
                    active_speaker_match=True,
                    voice_like=True,
                    vad_prob=0.54,
                    audio_sec=0.35,
                )
            )
        )
        self.assertFalse(should_interrupt_tts(TtsInterruptMeta(vad_prob=0.6, audio_sec=0.35, rms_ok=False)))

    def test_wake_probe_policy_skips_empty_filler_and_noise_without_wake(self) -> None:
        def skip(wake_probe: str, *, wake_detected: bool = False, duration_sec: float = 1.0) -> bool:
            return should_skip_full_stt_after_wake_probe_policy(
                wake_detected=wake_detected,
                wake_probe=wake_probe,
                duration_sec=duration_sec,
                no_wake_max_continue_sec=1.2,
                clean_text=normalize,
                looks_like_brief_filler_text=lambda text: text in {"um", "uh"},
                looks_like_repetitive_noise_text=lambda text: text == "aaaa",
            )

        self.assertTrue(skip(""))
        self.assertTrue(skip("um"))
        self.assertTrue(skip("aaaa", duration_sec=2.0))
        self.assertFalse(skip("um", duration_sec=2.0))
        self.assertFalse(skip("", wake_detected=True))

    def test_short_transcription_policy_ignores_empty_and_short_non_wake(self) -> None:
        def ignore(text: str, *, audio_sec: float = 0.4, wake_detected: bool = False) -> bool:
            return should_ignore_short_transcription_policy(
                text=text,
                audio_sec=audio_sec,
                wake_detected=wake_detected,
                normalize_voice_text=normalize,
                normalized_wake_words=lambda: {"evelyn"},
                min_audio_sec=0.5,
                min_transcribed_len=6,
                wake_short_text_keep_len=2,
            )

        self.assertTrue(ignore(""))
        self.assertTrue(ignore("hi"))
        self.assertFalse(ignore("evelyn"))
        self.assertFalse(ignore("ok", wake_detected=True))
        self.assertFalse(ignore("hello world", audio_sec=0.4))

    def test_short_followup_candidate_policy_requires_owner_followup_without_wake(self) -> None:
        def candidate(text: str, *, owner_followup_active: bool = True, wake_detected: bool = False, audio_sec: float = 0.8) -> bool:
            return is_short_followup_candidate_policy(
                text=text,
                audio_sec=audio_sec,
                wake_detected=wake_detected,
                owner_followup_active=owner_followup_active,
                normalize_voice_text=normalize,
                min_audio_sec=0.5,
                min_transcribed_len=6,
            )

        self.assertTrue(candidate("short"))
        self.assertFalse(candidate("short", owner_followup_active=False))
        self.assertFalse(candidate("short", wake_detected=True))
        self.assertFalse(candidate(""))
        self.assertFalse(candidate("this is a long followup", audio_sec=0.8))

    def test_room_session_policy_facade_wraps_owner_and_reply_state(self) -> None:
        owner_ids: dict[str, int] = {}
        owner_until: dict[str, float] = {}
        reply_in_progress: dict[str, bool] = {}
        events: list[tuple[str, dict]] = []

        policy = DiscordRoomSessionPolicy(
            room_owner_user_ids=owner_ids,
            room_owner_until=owner_until,
            room_reply_in_progress=reply_in_progress,
            log_event=lambda event, **payload: events.append((event, payload)),
            now_monotonic=lambda: 100.0,
            pick_active_speaker=lambda _room_key: 10,
        )

        policy.set_owner("room-1", 10, ttl_sec=5.0, reason="test", session_key="session-1", turn_id="turn-1", segment_id=2)
        policy.set_reply_in_progress("room-1", True, owner_user_id=10)

        self.assertTrue(policy.is_owner_active("room-1", 10))
        self.assertEqual(policy.snapshot("room-1")["owner_user_id"], 10)
        self.assertTrue(policy.snapshot("room-1")["reply_in_progress"])
        self.assertEqual(events[0][0], "room_owner_update")
        self.assertEqual(events[1][0], "room_reply_state")

        policy.clear_owner("room-1")

        self.assertNotIn("room-1", owner_ids)
        self.assertNotIn("room-1", owner_until)


if __name__ == "__main__":
    unittest.main()
