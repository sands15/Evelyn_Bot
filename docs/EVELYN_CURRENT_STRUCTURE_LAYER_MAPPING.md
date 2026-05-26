# Evelyn Current Structure Layer Mapping

## Purpose

This document maps today's Evelyn code layout onto the target assistant
architecture defined in `EVELYN_ASSISTANT_TARGET_ARCHITECTURE.md`.

It is not a proposal for the final shape by itself.
It is a bridge document answering one question:

Which parts of the current code already resemble the target layers, and which
parts are still entangled?

---

## References

- target blueprint: `docs/EVELYN_ASSISTANT_TARGET_ARCHITECTURE.md`
- current runtime snapshot: `CURRENT_EVELYN_ARCHITECTURE.md`
- voice pipeline refactor reference: `docs/plans/VOICE_PIPELINE_REFACTOR_PLAN.md`

---

## Current Overall Shape

At the assistant level, Evelyn is still centered on `main.py`.

Today `main.py` acts as all of the following at once:

- Discord voice ingress host
- session and wake gate owner
- realtime turn orchestrator
- TTS client and playback builder
- memory recall and writeback coordinator
- control-page/status formatter
- text command and runtime command router

That means the desired layers already exist in intent, but many of them are
still implemented as one large application surface rather than as explicit
contracts.

---

## Layer Mapping

## 1. Ingress and Session Layer

### Current ownership

Mostly concentrated in `main.py`:

- `process_member_audio`
- `_process_member_audio_impl`
- `voice_ingress_worker`
- `on_voice_state_update`
- Discord receive hookups such as `vc.on_user_audio = process_member_audio`
- local mic state and source status handling
- room owner and per-speaker session logic
- wake and quality gates before full turn handling

### What is good already

- Evelyn already has a strong policy model here.
- Wake handling is not naive.
- Room owner and per-speaker state separation is already part of the design.
- Tail-fragment and post-response suppression concerns are already recognized.

### What is still wrong

- Ingress decisions are too close to STT, routing, and reply execution.
- Queueing, acceptance, suppression, and answer generation still touch the same
  broad runtime path.
- This layer is policy-strong but boundary-weak.

### Mapping verdict

The layer exists conceptually, but not yet as an isolated module boundary.

---

## 2. Realtime Turn Layer

### Current ownership

Mostly in `main.py`, especially around:

- `_process_member_audio_impl`
- STT handling inside the voice path
- routing and response selection
- main reply generation
- `create_omnivoice_source`
- playback kickoff and interrupt handling
- turn tracing and segment/chunk bookkeeping

It also pulls in supporting runtime context such as:

- `build_runtime_status_context`
- live Minecraft observation helpers
- conversation guidance assembly

### What is good already

- The code already thinks in turns.
- Turn tracing is more mature than most of the surrounding boundaries.
- There is already an implicit chain:
  `accepted audio -> STT -> route -> answer -> TTS -> playback`

### What is still wrong

- The hot path is too thick.
- Runtime status enrichment, memory context, domain state, and answer shaping
  can all leak into the same execution path.
- The code does not yet enforce a narrow "speak sooner first" contract.

### Mapping verdict

This is the most important existing layer and also the most overloaded one.

---

## 3. Tool Planning Layer

### Current ownership

Distributed and partially mixed into `main.py`:

- route selection for direct answer vs search-like or domain-specific behavior
- command interpretation for control-page and bot commands
- domain routing toward Minecraft autonomy
- conversation-to-action bridging for local runtime operations

Related files already hint at a cleaner future split:

- `evelyn_core/runtime/evelyn_core/autonomy_router.py`
- `evelyn_core/runtime/evelyn_core/skills/routing/STRUCTURE.md`

### What is good already

- Evelyn already distinguishes between normal assistant response behavior and
  specialized runtime actions.
- Minecraft/domain routing is not treated as just another string trick.

### What is still wrong

- Planning and execution are not cleanly separated.
- `main.py` still directly mixes user-facing handling with executor calls.
- The tool layer is more domain-aware than contract-aware.

### Mapping verdict

The planning intent exists, but it needs a typed request boundary of its own.

---

## 4. Tool Execution Layer

### Current ownership

This layer is the healthiest part of the current structure.

Existing runtime adapters already live outside `main.py`, including:

- `evelyn_core/runtime/evelyn_core/minecraft_autonomy_client.py`
- `evelyn_core/runtime/evelyn_core/voyager_service.py`
- `evelyn_core/runtime/evelyn_core/codex_gateway_server.py`
- `evelyn_core/runtime/evelyn_core/local_runtime.py`
- `evelyn_core/runtime/evelyn_core/autonomy.py`

### What is good already

- Some heavy execution responsibilities already live in their own runtime
  modules or local services.
- The Voyager/Minecraft side already uses explicit service boundaries and
  health concepts.
- This is closer to the desired "adapter" shape than the rest of the system.

### What is still wrong

- `main.py` still reaches into executors too directly.
- User intent interpretation and executor triggering are not cleanly separated.
- Structured execution result contracts are still too ad hoc at the assistant
  layer.

### Mapping verdict

This layer has the best existing foundation and should be preserved, not
rewritten from scratch.

---

## 5. Memory Layer

### Current ownership

Mostly split between `main.py` coordination and lower-level helpers:

In `main.py`:

- `build_memory_context`
- `append_history`
- `schedule_memory_update`
- `update_long_term_memory`

Lower-level support:

- `evelyn_core/runtime/evelyn_core/memory.py`

### What is good already

- Recall and writeback are at least distinguishable in the current code.
- There is already a scheduling concept for post-turn memory work.
- History append and long-term update are not completely collapsed into one
  function.

### What is still wrong

- The app layer still owns too much memory orchestration detail.
- Turn-time recall and post-turn writeback are not yet enforced as separate
  contracts.
- Memory can still leak too close to the hot path.

### Mapping verdict

This layer exists in partial form, but its contract still lives in application
logic instead of a dedicated memory facade.

---

## 6. Presentation Layer

### Current ownership

Split between `main.py` and runtime helpers.

In `main.py`:

- `build_voice_pipeline_snapshot`
- `build_control_page_status_text`
- `build_control_page_status_reply`
- `build_control_page_voice_status_reply`
- control-page snapshot caching and formatting
- status and command reply shaping

Related runtime file:

- `evelyn_core/runtime/evelyn_core/control_page_server.py`

### What is good already

- Evelyn already values operational visibility.
- The control page is treated as a first-class runtime surface.
- Voice and Minecraft runtime state are already being surfaced to the user.

### What is still wrong

- Presentation still knows too much about raw runtime shape.
- Snapshot collection, runtime reads, and user-facing formatting are too close
  together.
- UI/presentation still depends heavily on `main.py` globals.

### Mapping verdict

Presentation is important and active, but not yet consuming a clean shared
runtime snapshot contract.

---

## 7. TTS Service Boundary

### Current ownership

Primarily in `main.py`:

- `warmup_tts_server`
- `create_omnivoice_source`
- current HTTP assumptions for `/health`, `/v1/audio/speech`,
  `/v1/voices/profiles`

### What is good already

- Evelyn already treats TTS as a service boundary in practice.
- Health and warmup are explicit concepts.

### What is still wrong

- The client contract is too tied to the current OmniVoice deployment shape.
- Backend assumptions leak into the assistant core.
- This is the main blocker for a clean WSL `omnivoice-triton` swap.

### Mapping verdict

The boundary exists, but it is currently too implementation-specific to be the
stable long-term contract.

---

## What Should Be Preserved As-Is in Principle

These are already good architectural decisions even if their implementation
location must change:

- whitelist-first wake behavior
- room-owner and per-speaker session separation
- turn-based tracing with `turn_id`, `segment_id`, and `chunk_index`
- visible runtime/control-page observability
- externalized heavy runtime adapters on the Minecraft/Voyager side

---

## What Is Most Entangled Today

The most coupled responsibilities are:

- ingress/session decisions and realtime answer execution
- realtime answer execution and TTS client behavior
- memory coordination and turn handling
- runtime snapshot assembly and presentation formatting
- command interpretation and tool execution

This is why `main.py` feels like the real assistant runtime even when other
modules exist.

---

## Immediate Structural Reading

If Evelyn is going to converge toward the target assistant architecture, the
highest-value near-term separation points are:

1. ingress/session boundary
2. realtime turn boundary
3. TTS client boundary
4. memory facade boundary
5. runtime snapshot and presentation boundary

Tool execution itself is not the first thing to rewrite.
Its adapter side is already ahead of the assistant core.

