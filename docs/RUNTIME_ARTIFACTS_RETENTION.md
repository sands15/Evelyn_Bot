# Runtime Artifacts Retention

Evelyn runtime outputs live under `runtime_artifacts/`, `logs/`, and `debug_audio/`. They are useful for debugging, but should not grow without bound. Raw microphone captures are sensitive and are disabled by default.

Cleanup is explicit. Nothing is deleted automatically by importing the helper module.

## Dry Run

```powershell
python evelyn_core\runtime\evelyn_core\runtime_artifacts_retention.py --root runtime_artifacts
python evelyn_core\runtime\evelyn_core\runtime_artifacts_retention.py --root logs
$env:PYTHONPATH="$PWD\evelyn_core\runtime"
python -m evelyn_core.voice_debug_audio --root debug_audio
```

The default is dry-run. It prints a JSON plan with candidate files, reasons, and total bytes.

## Apply

```powershell
python evelyn_core\runtime\evelyn_core\runtime_artifacts_retention.py --root runtime_artifacts --apply
python evelyn_core\runtime\evelyn_core\runtime_artifacts_retention.py --root logs --apply
$env:PYTHONPATH="$PWD\evelyn_core\runtime"
python -m evelyn_core.voice_debug_audio --root debug_audio --apply
```

Apply deletes only files selected by the plan. Active state files are protected by filename. Stop or verify the owning runtime before applying a log plan. Do not apply a voice plan until the recordings are no longer needed for an active investigation.

## Automatic Bounds

- `VOICE_DEBUG_SAVE_AUDIO=false` is the default. Turn it on only for a bounded investigation.
- A voice recording is treated as one logical bundle: raw WAV, STT WAV, and JSON metadata are retained or removed together.
- When debug saving is enabled, each successful write enforces 200 bundles, 7 days, and 256 MiB per guild while preserving the newest 10 bundles.
- Voyager text logs rotate before a launch or bounded append crosses 25 MiB. Four backups are kept.
- When a status board is redirected to a file, it emits at most once every 30 seconds instead of once every second.

Environment overrides:

- `VOICE_DEBUG_MAX_FILES_PER_GUILD`
- `VOICE_DEBUG_MAX_AGE_DAYS`
- `VOICE_DEBUG_MAX_TOTAL_MB_PER_GUILD`
- `VOICE_DEBUG_PRESERVE_NEWEST`
- `EVELYN_LOG_MAX_BYTES`
- `EVELYN_LOG_BACKUP_COUNT`
- `EVELYN_STATUS_LOG_INTERVAL_SEC`

## Default Rules

- `logs/*.log*`: keep newest 1, remove logs and rotated backups older than 14 days or over 100 MB total.
- `turn_trace/*.jsonl`: keep newest 7, remove traces older than 30 days or over 100 MB total.
- `autonomy_authorization/events/*.jsonl`: keep newest 7, remove authorization
  audit journals older than 30 days or over 20 MB total.
- `minecraft_world_lease/events/*.jsonl`: keep newest 7, remove world-action
  lease audit journals older than 30 days or over 20 MB total.
- `benchmarks/*.jsonl`: keep newest 3, remove rows older than 30 days or over 20 MB total.
- `memory/*.jsonl`: keep newest 3, remove write-behind status logs older than 30 days or over 20 MB total.
- Legacy `runtime_artifacts/voice_debug/**/*.wav` and `*.pcm`: keep newest 50, remove audio older than 7 days or over 2 GB total. The active top-level `debug_audio/` store uses the separate bundle-aware command above.
- `control_page/dumps/*`: keep newest 3, remove dumps older than 7 days or over 50 MB total.
- `minecraft/window_state/*.json`: keep newest 10, remove window snapshots older than 30 days or over 50 MB total.
- `test_status/*`: keep newest 2, remove test status files older than 7 days or over 10 MB total.
- `voyager/*.jsonl`: keep newest 5, remove event logs older than 30 days or over 50 MB total.

Protected active files:

- `last_request.json`
- `voice_last_channel.json`
- `upstream_bridge_status.json`
- `voyager_goal_state.json`

The planner also verifies candidate paths stay inside the configured root before applying cleanup. Voice cleanup resolves every bundle member under its guild directory before unlinking it.

## 2026-07-15 Dry Run

No files were deleted. Before/after counts and byte totals were identical.

- `debug_audio`: 2,066 logical bundle candidates, 342,672,040 bytes.
- `runtime_artifacts`: 12 log candidates, 196,367,554 bytes.
- `logs`: 6 turn-trace candidates, 3,631,909 bytes.

The largest individual candidate is the stale `runtime_artifacts/logs/upstream_bridge_runner.log` at 189,955,706 bytes. Its growth source was a full status-board redraw written to redirected stdout every second; the 30-second redirected-output limit addresses that cause.

## 2026-07-15 Applied Cleanup

After explicit approval, the new bounds were deployed by rebuilding and recreating only `evelyn-bot-api` and `evelyn-voyager`. The other eight Evelyn containers were not recreated.

The dry-run plan was recalculated immediately before deletion. Candidate counts and byte totals matched the recorded plan, the stale Voyager logs were not changing, the Voyager runner reported `runner_alive=false`, and the voice-debug file count remained stable during the pre-delete observation.

- `debug_audio`: deleted 2,066 logical bundles and 342,672,040 bytes.
- `runtime_artifacts`: deleted 12 files and 196,367,554 bytes.
- `logs`: deleted 6 turn-trace files and 3,631,909 bytes.
- Total: deleted 2,084 planned items or bundles and 542,671,503 bytes.
- Failures: 0.

A second dry run after deletion returned zero candidates and zero candidate bytes for all three roots. The voice cleanup retained the newest 10 bundles in each populated guild directory. All ten Evelyn containers remained healthy, and Control-Page boot progress remained 100%.
