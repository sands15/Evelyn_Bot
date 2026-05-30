# Current Bot Structure

Last reviewed: 2026-05-29

This is the short operational map for the current Evelyn bot. It summarizes the
runtime as it exists today, not the final target architecture.

## One-line Summary

Evelyn is a local-first Discord voice bot centered on `main.py`, with local STT,
router/main/summary LLMs, OmniVoice TTS, a Markdown-backed memory system, a
control page, and an optional Minecraft/Voyager automation stack.

## Main Runtime Shape

```text
Discord text / voice / local mic
-> main.py
-> voice filtering / wake handling / session policy
-> STT
-> router / fast-path policy
-> memory + runtime context assembly
-> main LLM or action executor
-> answer shaping
-> TTS / Discord text / control-page delivery
```

`main.py` is still the central application surface. It currently owns too many
responsibilities at once:

- Discord bot events and command routing
- voice session ownership, wake handling, suppression, and turn orchestration
- STT loading and transcription calls
- router/main/sub LLM request flow
- memory recall and post-turn writeback scheduling
- OmniVoice HTTP requests and Discord playback sources
- control-page status formatting
- Minecraft/Voyager status merge for the user-facing bot

The desired future direction is to keep the hot path chain-shaped, with narrow
interfaces between input, filtering, STT, routing, execution, answer composition,
and delivery.

## Core Components

### Discord Bot Core

Primary file:

- `main.py`

Responsibilities:

- Discord client startup
- text command handling
- voice channel join/rejoin behavior
- per-speaker and per-room state
- wake/no-wake policy
- final response delivery

Important supporting modules:

- `evelyn_voice/` for custom Discord voice receive behavior
- `evelyn_core/runtime/evelyn_core/voice_pipeline.py` for voice classification and policy helpers
- `evelyn_core/runtime/evelyn_core/voice_orchestration.py` for some extracted turn/reply helpers
- `evelyn_core/runtime/evelyn_core/local_mic.py` for optional local mic capture

### Speech Pipeline

Current flow:

```text
audio segment
-> quality/noise/suppression checks
-> wake probe / partial STT / full STT
-> transcript post-corrections
-> route/reply decision
```

Current STT backend:

- default config: `STT_BACKEND=qwen_asr`
- default model: `Qwen/Qwen3-ASR-1.7B`
- compute type: `float16`

Important files:

- `main.py` for STT model loading and transcription calls
- `evelyn_core/runtime/evelyn_core/audio.py` for resampling, denoise, VAD/noise helpers
- `evelyn_core/runtime/evelyn_core/text.py` for transcript cleanup and wake/text normalization

### LLM Stack

Evelyn uses multiple local LLM roles:

- Main LLM: final user-facing answer generation
- Router/sub decision logic: route and cognitive policy
- Summary/sub LLM: memory summaries, durable facts, and open-question updates

The exact launcher/model values are environment-driven. `README.md` and
`evelyn_core/start_env.bat` describe the expected local defaults.

### TTS and Delivery

Current TTS shape:

```text
answer text
-> sentence/chunk split
-> OmniVoice HTTP streaming request
-> PCM buffering
-> Discord AudioSource playback
```

Primary locations:

- `main.py` for OmniVoice client, buffering, queueing, and playback sources
- `evelyn_core/runtime/evelyn_core/config.py` for TTS stream/buffer knobs
- OmniVoice server on `127.0.0.1:8880`

Current known design pressure:

- TTS request, streaming, buffering, playback, and turn cancellation are still
  too close together in `main.py`.
- Recent work added more explicit playback buffering and contract payloads, but
  this area should still be treated as a sensitive hot path.

### Memory System

Current memory is split into compatibility memory, vault memory, and runtime
indexes.

Compatibility layout:

```text
bot_memory/guild_<id>/
  raw_transcript.jsonl
  rolling_summary.txt
  durable_facts.jsonl
  open_questions.jsonl
  person_user_<id>/
  room_text_<id>/
  room_voice_<id>/
  session_.../
```

Vault/index layout:

```text
bot_memory/memory_vault/
  core/
  daily/
  episodes/
  concepts/
  procedures/
  projects/

bot_memory/memory_index/
  memory.sqlite
  memory_graph.json
  hot_context.json
  prompt_blocks/
```

Current rule of thumb:

- `memory_vault/` is intended to become the durable, human-readable source.
- `memory_index/` is rebuildable runtime support.
- `guild_*` files are still compatibility inputs and should not be deleted
  casually.
- memory writeback is coordinated from `main.py`, with lower-level helpers in
  `evelyn_core/runtime/evelyn_core/memory.py` and `memory_vault.py`.

## Minecraft / Voyager Stack

Minecraft automation is not a single bot process. It is a layered bridge:

```text
main.py / user command
-> minecraft_autonomy_client.py
-> voyager_service.py on port 8765
-> upstream_voyager_runner.py
-> third_party/Voyager
-> Codex gateway on port 8787 for action generation
-> mineflayer bridge on port 3000
-> Minecraft server on port 25565
```

Important files:

- `evelyn_core/runtime/evelyn_core/minecraft_autonomy_client.py`
- `evelyn_core/runtime/evelyn_core/voyager_service.py`
- `evelyn_core/runtime/evelyn_core/upstream_voyager_runner.py`
- `evelyn_core/runtime/evelyn_core/codex_gateway_server.py`
- `third_party/Voyager/`

Important runtime state:

- `runtime_artifacts/voyager/upstream_bridge_status.json`
- `runtime_artifacts/logs/upstream_bridge_runner.log`
- `bot_memory/upstream_ckpt/`
- `runtime_artifacts/voyager/voyager_goal_state.json`
- `runtime_artifacts/voyager/death_events.jsonl`
- `runtime_artifacts/state/voice_last_channel.json`

## Control Page

The control page is a local operator dashboard for runtime status, voice state,
Minecraft/Voyager status, and memory inspection.

Important files:

- `docs/index.html`
- `docs/assets/evelyn-page.js`
- `evelyn_core/runtime/evelyn_core/control_page_server.py`
- `evelyn_core/runtime/evelyn_core/control_page_windows.py`

Debug screenshots and DOM dumps are runtime artifacts rather than durable
memory. Current snapshots should live under `runtime_artifacts/control_page/`
or recoverable trash if no longer needed.

## Skill System

The skill system is an extension layer, not a replacement for the core voice
pipeline.

Important files:

- `evelyn_core/runtime/evelyn_core/skills/README.md`
- `evelyn_core/runtime/evelyn_core/skills/*/STRUCTURE.md`
- `ROUTE_OWNERSHIP_POLICY.md`

Core-owned:

- STT
- router subLLM
- main LLM
- TTS
- realtime voice pipeline orchestration

Extension-owned:

- Minecraft/domain skills
- search workflow
- follow-up automation
- external executor integrations
- custom route-specific features

## Current `bot_memory` / `runtime_artifacts` Review

Review date: 2026-05-29. Runtime logs, status snapshots, test status files, and
control-page dumps were moved out of `bot_memory/` into `runtime_artifacts/`.
The image debug files were moved to recoverable trash.

Top runtime artifact contributors at review time:

- `runtime_artifacts/logs/upstream_bridge_runner.log`: about 181 MB
- `bot_memory/upstream_ckpt/`: about 6.24 MB across about 1232 files
- `runtime_artifacts/logs/voyager_service_stdout.log`: about 3.93 MB
- `runtime_artifacts/logs/upstream_bridge_errors.log`: about 1.25 MB

Moved from `bot_memory/` to `runtime_artifacts/`:

- `context_pipeline_benchmarks.jsonl` -> `runtime_artifacts/benchmarks/context_pipeline_benchmarks.jsonl`
- `voice_last_channel.json` -> `runtime_artifacts/state/voice_last_channel.json`
- `codex_gateway_last_request.json` -> `runtime_artifacts/codex_gateway/last_request.json`
- `codex_gateway_errors.log` -> `runtime_artifacts/logs/codex_gateway_errors.log`
- `voyager_goal_state.json` -> `runtime_artifacts/voyager/voyager_goal_state.json`
- `upstream_bridge_status.json` -> `runtime_artifacts/voyager/upstream_bridge_status.json`
- `voyager_death_events.jsonl` -> `runtime_artifacts/voyager/death_events.jsonl`
- `upstream_bridge_runner.log` -> `runtime_artifacts/logs/upstream_bridge_runner.log`
- `upstream_bridge_errors.log` -> `runtime_artifacts/logs/upstream_bridge_errors.log`
- `voyager_service_errors.log` -> `runtime_artifacts/logs/voyager_service_errors.log`
- `voyager_service_stdout.log` -> `runtime_artifacts/logs/voyager_service_stdout.log`
- `voyager_service_stderr.log` -> `runtime_artifacts/logs/voyager_service_stderr.log`
- `voyager_service_8765.lock` -> `runtime_artifacts/locks/voyager_service_8765.lock`
- `evelyn-page-dom-dump.html` -> `runtime_artifacts/control_page/dumps/evelyn-page-dom-dump.html`
- `test_inference_status.json` -> `runtime_artifacts/test_status/test_inference_status.json`
- `upstream_bridge_status_test.json` -> `runtime_artifacts/test_status/upstream_bridge_status_test.json`
- `window_*.json` snapshots -> `runtime_artifacts/minecraft/window_state/`

Moved to recoverable trash on 2026-05-29:

- `bot_memory/evelyn-page-chat-ui-check.png`
- `bot_memory/evelyn-page-chat-only-scroll.png`
- `bot_memory/evelyn-context-icon-check.png`
- `bot_memory/evelyn-context-flat-compass.png`
- `bot_memory/evelyn-context-gold-needles.png`
- `bot_memory/evelyn-context-compass-redo.png`

Trash location:

- `C:\Users\Admin\.openclaw\workspace\.trash\evelyn-bot-memory-images-20260529-1130\`

Files that look noisy but probably are not dummy data:

- `bot_memory/upstream_ckpt/events/*`
  - many files are repeated Voyager task attempts and checkpoints
  - they are small individually but numerous
  - treat them as checkpoint/history data unless pruning policy is defined
- `bot_memory/guild_*`
  - active compatibility memory; do not clean without a migration/check policy
- `bot_memory/memory_vault/*`
  - durable human-readable memory; do not treat as cache
- `bot_memory/memory_index/*`
  - runtime indexes/caches; likely rebuildable, but should be regenerated by a
    tool rather than manually removed
- `bot_memory/voyager_strategy_memory.json`
  - strategy memory; keep until a deliberate Voyager memory migration exists

Cleanup recommendation:

1. Keep `guild_*`, `memory_vault/`, `memory_index/`, `upstream_ckpt/`, and
   `voyager_strategy_memory.json` in `bot_memory/` by default.
2. Keep live status, logs, lock files, test status, and UI/debug snapshots under
   `runtime_artifacts/`.
3. Rotate `runtime_artifacts/logs/*.log` instead of deleting them directly.
4. Move future `evelyn-page-*.png` and `evelyn-context-*.png` files to
   recoverable trash or an artifact folder, not durable memory.

## Existing Reference Docs

Use these for deeper detail:

- `docs/EVELYN_CURRENT_STRUCTURE_LAYER_MAPPING.md`
- `docs/plans/VOICE_PIPELINE_REFACTOR_PLAN.md`
- `docs/EVELYN_MEMORY_VAULT_ARCHITECTURE.md`
- `docs/CONTEXT_PIPELINE_TARGET.md`
- `CURRENT_EVELYN_ARCHITECTURE.md`
- `CORE_ARCHITECTURE_BOUNDARY.md`
- `evelyn_core/runtime/evelyn_core/skills/README.md`

## Practical Editing Notes

- Be careful editing `main.py`; it is still the central hot path.
- Do not restart Evelyn, OpenClaw gateway, or Voyager services without asking.
- Do not delete `bot_memory` content directly. Prefer recoverable moves and
  document what was moved.
- Preserve user-facing tone/style and verify requirements before reporting
  completion.
