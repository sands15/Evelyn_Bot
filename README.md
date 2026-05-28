# Evelyn Bot

Evelyn is a local-first Discord voice companion and automation bot. The project
combines realtime voice input, local LLM routing, OmniVoice TTS, long-term
memory, a browser/control page, and optional Minecraft/Voyager automation.

The current direction is not a generic "fast assistant". Evelyn should feel like
a familiar character that can answer naturally, remember project context, avoid
assistant-like phrasing, and use heavier reasoning only when the turn actually
needs it.

## Current Focus

- Character-first Korean voice conversation.
- Strict wake/session handling for noisy Discord voice channels.
- Main/router/sub LLM split instead of sending every turn through one model.
- Fast cached or lightweight paths for call/status turns.
- OmniVoice streaming with adaptive playout buffering.
- Obsidian-style Markdown memory vault with SQLite indexes and graph view.
- Minecraft/Voyager integration as a layered runtime, not a single black box.

## Runtime Components

Default local services:

| Component | Default |
| --- | --- |
| Bot/control API | `8798` |
| Standalone control page | `8799` |
| Main LLM | `9820` |
| Sub/Summary LLM | `9821` |
| Router LLM | `9822` |
| OmniVoice TTS | `8880` |
| Voyager service | `8765` |
| Codex action gateway | `8787` |
| Mineflayer bridge | `3000` |
| Minecraft server | `25565` |

Current launcher defaults live in `evelyn_core/start_env.bat`. Check that file
before changing model, GPU, or port assumptions.

## Model Layout

The current default model split is:

- Main LLM: `LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct-GGUF:Q4_K_M`
  - Default endpoint: `http://127.0.0.1:9820/v1/chat/completions`
  - Used for real conversation and longer answers.
  - Launched through llama.cpp with prompt cache settings.

- Sub/Summary LLM: `supergemma4-e4b-abliterated-Q5_K_M.gguf`
  - Default endpoint: `http://127.0.0.1:9821/v1/chat/completions`
  - Used for summaries, memory work, and background consolidation when
    available.

- Router LLM: Gemma E2B GGUF
  - Default endpoint: `http://127.0.0.1:9822/v1/chat/completions`
  - Used for lightweight routing and turn classification.

- STT default: `Qwen/Qwen3-ASR-1.7B`
  - Config keys: `STT_BACKEND=qwen_asr`, `STT_MODEL_NAME=Qwen/Qwen3-ASR-1.7B`
  - STT quality is treated as more important than making memory/main models
    larger.

GPU convention used by the local launchers:

- `GPU 0`: RTX 3090
- `GPU 1`: RTX 4060

## Voice Pipeline

The voice path is built around Discord voice receive, strict wake handling, turn
classification, and path-specific response delivery.

High-level flow:

1. Receive Discord voice packets.
2. Decrypt RTP/DAVE audio.
3. Decode Opus to PCM.
4. Segment and resample audio for STT.
5. Run wake probe, wake confirm, and full STT when allowed.
6. Classify the turn type.
7. Choose a delivery path.
8. Generate text or use cached/lightweight output.
9. Send TTS audio to Discord.

Important policies:

- Wake candidates are whitelist-first and strict.
- Gibberish, unstable wake candidates, and confirm misses are dropped.
- Only the active room owner gets relaxed follow-up handling.
- Tail fragments should not re-enter wake/full-STT paths.
- When Evelyn asks the user a question, visible text should use `[question]` or
  the configured localized marker, but TTS should not read that label.

## Turn Paths

The structured voice pipeline tracks turn type and selected path metadata.

Common turn types:

- `wake_call`
- `casual_check`
- `short_confirm`
- `runtime_status`
- `minecraft_command`
- `conversation`
- `knowledge_or_search`
- `repair`

Common selected paths:

- `cached_audio_fast_path`
- `light_dialogue_path`
- `runtime_status_path`
- `minecraft_action_path`
- `main_conversation_path`
- `search_or_long_answer_path`
- `repair_path`

These fields are recorded in metrics, bottleneck logs, and turn trace events so
latency and routing mistakes can be debugged later.

## TTS

Evelyn uses OmniVoice through a local HTTP service.

Current default TTS settings:

- Strategy: `blockwise_capped_first`
- Follow-up strategy: `blockwise_capped_first`
- Block size: `16`
- First block steps: `8`
- Later block steps: `10`
- First immediate cap: `250ms`
- Lookahead crossfade: `0ms`
- Adaptive playback jitter: enabled
- Playback buffer clamp: `700ms` to `2600ms`

The current audio source uses a ring-buffer playout controller rather than a
simple "play as soon as the first bytes arrive" queue. It tracks block arrival
gaps, adapts the start buffer, and inserts short silence frames during temporary
underruns instead of ending playback early.

Useful trace events include:

- `tts_request_start`
- `tts_response_headers`
- `tts_first_byte`
- `playback_jitter_buffer_adapt`
- `playback_jitter_buffer_ready`
- `playback_underrun`

## Memory Vault

Evelyn's long-term memory is moving toward an Obsidian-compatible Markdown vault
backed by rebuildable runtime indexes.

Target shape:

```text
Markdown vault = durable human-readable memory
SQLite metadata = note map, freshness, links, cache state
FTS/vector index = fast recall
Graph links = relationship navigation
Hot context = realtime prompt memory
Prompt block cache = reusable prompt snippets
```

Main files:

- `evelyn_core/runtime/evelyn_core/memory_vault.py`
- `docs/EVELYN_MEMORY_VAULT_ARCHITECTURE.md`
- `tests/test_memory_vault.py`

Generated runtime data is ignored by git under `bot_memory/`.

Implemented memory features:

- Markdown note bootstrap.
- Legacy guild memory mirror into the vault.
- Daily transcript mirroring.
- SQLite metadata index.
- FTS5 recall with scan fallback.
- Deterministic hashing-vector recall.
- Graph link indexing.
- Retrieval cache.
- Hot context generation.
- Sub LLM dependency probing.
- Semantic consolidation worker when the sub LLM is available.
- Memory graph export for the control page.

Current limitation: vector recall uses deterministic `hashing-v1`, not a learned
embedding model. The schema is designed so a real embedding model can replace it
later without changing the recall facade.

## Control Page

The control page includes a Memory panel backed by:

- `GET /api/control-page/memory-graph`
- `export_memory_graph()` in `memory_vault.py`

The graph view renders Markdown vault notes as nodes and relationships as edges.
Edges can come from explicit note links, shared tags/projects, retrieval cache
co-hits, and vector similarity.

The control page is also responsible for local stack controls. Shutdown/quit
routes are handled locally by the standalone control page so the stack can still
be stopped even when the bot API is down.

## Minecraft and Voyager

Voyager integration is treated as three coupled layers:

1. Codex action gateway.
2. Voyager service/runner orchestration.
3. Mineflayer/Minecraft control plane.

Do not debug it as a single LLM adapter swap.

Important files:

- `evelyn_core/runtime/evelyn_core/codex_gateway_server.py`
- `evelyn_core/runtime/evelyn_core/voyager_service.py`
- `evelyn_core/runtime/evelyn_core/upstream_voyager_runner.py`
- `third_party/Voyager/`

Runtime state and large logs live under `bot_memory/` and are ignored by git.

## Startup

Common entrypoints:

- `start.bat`
- `evelyn_core/start.bat`
- `evelyn_core/start_env.bat`

The root launcher is a shim. The runtime defaults are defined in
`evelyn_core/start_env.bat`.

Before changing live behavior, check:

```powershell
git status --short --branch
Get-Content evelyn_core\start_env.bat
```

## Verification

Useful local checks:

```powershell
python -m py_compile main.py evelyn_core\runtime\evelyn_core\config.py
python tests\test_memory_vault.py
python tests\test_dialogue_turn_classifier.py
node --check docs\assets\evelyn-page.js
git diff --check
```

For runtime verification, do not rely on one signal. Check the relevant ports,
processes, GPU holders, WSL/helper layers, and control API state for the scope
being reported.

## Documentation

Key architecture notes:

- `CURRENT_EVELYN_ARCHITECTURE.md`
- `CORE_ARCHITECTURE_BOUNDARY.md`
- `ROUTE_OWNERSHIP_POLICY.md`
- `docs/EVELYN_ASSISTANT_TARGET_ARCHITECTURE.md`
- `docs/CONTEXT_PIPELINE_TARGET.md`
- `docs/EVELYN_CURRENT_STRUCTURE_LAYER_MAPPING.md`
- `docs/EVELYN_MEMORY_VAULT_ARCHITECTURE.md`
- `docs/evelyn-dialogue-ux-fastpath-2026-05-28.md`
- `docs/tts-streaming-architecture-2026-05-27.md`

## Repository Hygiene

Ignored runtime/local data includes:

- `bot_memory/`
- `debug_audio/`
- `guild_settings/`
- `logs/`
- `.env`
- `.evelyn_bot.lock`
- `node_modules/`
- `.venv-voyager/`

Do not commit generated runtime logs, local memory, voice recordings, or private
environment files.
