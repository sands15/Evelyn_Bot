# Runtime Artifacts Retention

Evelyn runtime outputs live under `runtime_artifacts/`. They are useful for debugging, but should not grow without bound.

Cleanup is explicit. Nothing is deleted automatically by importing the helper module.

## Dry Run

```powershell
python evelyn_core\runtime\evelyn_core\runtime_artifacts_retention.py --root runtime_artifacts
```

The default is dry-run. It prints a JSON plan with candidate files, reasons, and total bytes.

## Apply

```powershell
python evelyn_core\runtime\evelyn_core\runtime_artifacts_retention.py --root runtime_artifacts --apply
```

Apply deletes only files selected by the plan. Active state files are protected by filename.

## Default Rules

- `logs/*.log`: keep newest 2, remove logs older than 14 days or over 50 MB total.
- `turn_trace/*.jsonl`: keep newest 7, remove traces older than 30 days or over 100 MB total.
- `benchmarks/*.jsonl`: keep newest 3, remove rows older than 30 days or over 20 MB total.
- `memory/*.jsonl`: keep newest 3, remove write-behind status logs older than 30 days or over 20 MB total.
- `voice_debug/**/*.wav` and `voice_debug/**/*.pcm`: keep newest 50, remove audio older than 7 days or over 2 GB total.
- `control_page/dumps/*`: keep newest 3, remove dumps older than 7 days or over 50 MB total.
- `minecraft/window_state/*.json`: keep newest 10, remove window snapshots older than 30 days or over 50 MB total.
- `test_status/*`: keep newest 2, remove test status files older than 7 days or over 10 MB total.
- `voyager/*.jsonl`: keep newest 5, remove event logs older than 30 days or over 50 MB total.

Protected active files:

- `last_request.json`
- `voice_last_channel.json`
- `upstream_bridge_status.json`
- `voyager_goal_state.json`

The planner also verifies candidate paths stay inside the configured root before applying cleanup.
