# Voice receive validation, 2026-04-19

Scope: phase 4 validation pass after PR #56 style porting phases 1-3 in `evelyn_voice/client.py`.

## Data scanned
- Source: `C:\Evelyn\debug_audio\**\*.json`
- JSON files: 187
- Files with `voice_receive` metadata: 162

## Aggregate findings
- `voice_receive.unstable=true`: 151 / 162
- `voice_receive.short_clip=true`: 26 / 162
- Top final outcomes:
  - `[UNSTABLE AUDIO IGNORE]`: 110 (historical, from earlier gating behavior)
  - `[VAD IGNORE]`: 41
- Top unstable reasons:
  - `real_silence=20`: 65
  - `front_burst_detected`: 59
  - `burst_trim_ms=320`: 52
  - `heavy_trim_ms=320`: 49
  - `fec=3`: 39
  - `fec=4`: 36
  - `plc=4`: 28
  - `plc=5`: 27

## Interpretation
1. The pipeline is now preserving more clips through to STT/VAD, but unstable metadata is still very noisy.
2. `real_silence=20` is the single most frequent unstable reason, suggesting the threshold is too sensitive for real Discord traffic.
3. Front-burst related reasons still fire often, especially on short clips with a loud onset and then usable speech.
4. Because unstable no longer hard-stops full STT, the most useful remaining work is reducing false-positive unstable diagnostics so logs and debug JSON stay meaningful.

## Immediate tuning recommendation
- Raise non-short `real_silence` unstable threshold from 20 to 24.
- Keep short-clip threshold more conservative than before, but do not let short clips inflate unstable noise unless combined with stronger repair evidence.

## Notes
- Validation used current debug_audio history, so counts include historical files produced before some of the newer gating changes.
- Future validation should compare only post-change samples from a fresh run window to avoid mixing historical and current behavior.
