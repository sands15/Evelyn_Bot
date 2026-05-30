# Evelyn TTS Phase 2 Runtime Verification Checklist

Purpose: decide whether Phase 2 TTS playback cleanup is safe to close before starting Phase 3.

Rule: do not restart Evelyn, OpenClaw, TTS, Voyager, or gateway unless 정훈 explicitly approves it.

## 0. Preflight

- [ ] Confirm current branch/worktree status is understood.
- [ ] Confirm no unrelated user changes will be reverted.
- [ ] Confirm `docs/plans/EVELYN_HOTPATH_STABILIZATION_REFACTOR_PLAN.md` reflects the latest Phase 2 state.
- [ ] Confirm `tts_playback.py`, `turn_trace.py`, and TTS/TurnTrace tests are present.
- [ ] Confirm whether Evelyn is currently running old code or restarted with the new code.

Commands:

```powershell
git -C C:\Evelyn status --short
python -m py_compile C:\Evelyn\main.py C:\Evelyn\evelyn_core\runtime\evelyn_core\tts_playback.py C:\Evelyn\evelyn_core\runtime\evelyn_core\turn_trace.py C:\Evelyn\tests\test_tts_playback_contract.py C:\Evelyn\tests\test_turn_trace_summary.py
```

## 1. Non-Restart Verification

- [ ] Run focused TTS playback contract tests.
- [ ] Run focused TurnTrace summary tests.
- [ ] Run full unittest discovery.
- [ ] Run `git diff --check` for touched files.
- [ ] Run unicode/`??` corruption scan for touched files.
- [ ] Confirm `main.py` has no direct TTS registry mutation/read patterns.
- [ ] Confirm `main.py` has no direct `bot_speaking_guilds` membership check.
- [ ] Confirm `main.py` has no direct `last_bot_audio_end_at.get(...)` suppression check.

Commands:

```powershell
python -m unittest C:\Evelyn\tests\test_tts_playback_contract.py C:\Evelyn\tests\test_turn_trace_summary.py
python -m unittest discover -s C:\Evelyn\tests
git -C C:\Evelyn diff --check -- main.py evelyn_core/runtime/evelyn_core/tts_playback.py evelyn_core/runtime/evelyn_core/turn_trace.py tests/test_tts_playback_contract.py tests/test_turn_trace_summary.py
rg -n "active_tts_playbacks\.(keys|get|set|update|pop)\(|len\(active_tts_playbacks\)|guild\.id in active_tts_playbacks|guild_id in bot_speaking_guilds|last_bot_audio_end_at\.get" C:\Evelyn\main.py
```

Expected:

- Focused tests pass.
- Full tests pass.
- `diff --check` has no whitespace errors; LF/CRLF warning is acceptable if unchanged.
- `rg` should return no matches for direct old TTS state access patterns.

## 2. Runtime Verification After Approved Restart

Only run after 정훈 approves restarting Evelyn or otherwise confirms the running process has loaded the new code.

- [ ] Start or restart Evelyn visibly, not as a hidden sidecar.
- [ ] Confirm TTS server health before voice tests.
- [ ] Confirm Evelyn joins/listens in the expected Discord voice channel.
- [ ] Send one cached wake response that should use `wake_call_default.wav`.
- [ ] Send one normal short text-to-TTS response.
- [ ] Send one streaming LLM response that produces multiple TTS chunks.
- [ ] Trigger one qualified interruption/barge-in while TTS is playing.
- [ ] Trigger one post-TTS short noise/voice input within `POST_TTS_IGNORE_SEC`.
- [ ] Trigger one normal voice input after the post-TTS window expires.

Expected:

- Cached response plays once, with no duplicate playback.
- Normal short TTS plays once and completes.
- Streaming TTS starts after prepared source readiness and drains cleanly.
- Barge-in cancels active producer/playback tasks.
- Post-TTS input is dropped as `post_tts_ignore`.
- Later voice input is accepted normally.

## 3. TurnTrace Checks

Inspect the latest TurnTrace JSONL rows after runtime verification.

- [ ] `voice_turn_summary` exists for completed voice reply.
- [ ] `text_turn_summary` exists for text/control reply.
- [ ] `voice_drop_summary` exists for dropped voice input.
- [ ] `needs_tts` is explicit.
- [ ] `playback_started` is explicit.
- [ ] `playback_completed` is explicit.
- [ ] `playback_cancelled` is explicit.
- [ ] `tts_first_audio_ms` is present when TTS audio was produced.
- [ ] `playback_first_packet_ms` is present when Discord playback emitted audio.
- [ ] `error_layer` and `error` are `null` on successful turns.

Suggested inspection:

```powershell
Get-ChildItem C:\Evelyn\runtime_artifacts\turn_trace -Filter *.jsonl | Sort-Object LastWriteTime -Descending | Select-Object -First 1
```

Review recent summary rows and compare these fields:

```json
{
  "event": "voice_turn_summary",
  "needs_tts": true,
  "playback_started": true,
  "playback_completed": true,
  "playback_cancelled": false,
  "tts_first_audio_ms": 0,
  "playback_first_packet_ms": 0,
  "error_layer": null,
  "error": null
}
```

## 4. Failure Checks To Repeat Twice

Run these checks after the first runtime pass and again after a second short TTS turn.

- [ ] No stuck playback task remains in TTS tracker/backlog.
- [ ] No missed cancellation after barge-in.
- [ ] No duplicate playback for cached, single, or streaming TTS.
- [ ] No missing first-audio metric on successful TTS.
- [ ] No missing playback completion/cancellation field in summary.
- [ ] No stale `bot_is_speaking` suppression after playback completes.
- [ ] No excessive `playback_underrun_silence` events during normal playback.
- [ ] No unhandled exception in TTS prefetch/prepared playback path.

## 5. Phase 2 Close Criteria

Phase 2 can be marked complete only when all are true:

- [ ] Non-restart verification passes.
- [ ] Runtime verification passes after approved restart or confirmed loaded process.
- [ ] TurnTrace rows show playback started/completed/cancelled state correctly.
- [ ] Barge-in cancellation is verified.
- [ ] Post-playback suppression is verified.
- [ ] No stuck task, missed cancellation, duplicate playback, or missing first-audio metric is observed in two checks.
- [ ] Plan document is updated with final Phase 2 completion status.
- [ ] A memory note is written with exact test counts and runtime result.

## 6. If Something Fails

- [ ] Capture the failing command or runtime action.
- [ ] Capture the latest relevant TurnTrace rows.
- [ ] Capture any `tts_playback_failed`, `discord_playback_exception`, or `playback_underrun_silence` events.
- [ ] Do not proceed to Phase 3.
- [ ] Patch the smallest failing slice.
- [ ] Re-run focused tests, full tests, and the relevant runtime case.
