# Evelyn Hot Path Stabilization Refactor Plan

Date: 2026-05-29
Scope: Evelyn voice/dialogue hot path, TTS playback, turn tracing, route policy, memory writer, Minecraft/Voyager runtime snapshots, and runtime artifacts.

## Purpose

This document turns the current refactor direction into an implementation order.

The guiding decision is:

- stabilize the hot path before adding large new behavior
- finish already-started separations instead of creating parallel replacements
- make every phase observable before touching the next fragile runtime path
- preserve current voice, Discord, control page, and Minecraft behavior unless a phase explicitly owns that surface

The plan is intentionally ordered by blast radius. Each phase should leave Evelyn runnable and easier to debug than before.

## Current Read

The refactor direction is sound, but several pieces already exist in partial form:

- TTS is partly separated, but `main.py` still contains playback/streaming responsibilities that overlap with `evelyn_core/runtime/evelyn_core/tts_playback.py`.
- Voice turn orchestration already has a home in `evelyn_core/runtime/evelyn_core/voice_orchestration.py`, so the next step is migration, not a fresh rewrite.
- Turn/event tracing already exists in `main.py`, but it needs a stable per-turn summary contract.
- `runtime_artifacts` is already the right destination for runtime outputs; the next need is retention and rotation, not another directory split.
- `RouteDecision` exists in `evelyn_core/runtime/evelyn_core/voice_pipeline.py`, but the route contract still needs explicit execution policy flags such as `needs_main_llm`, `needs_memory`, `needs_tts`, and runtime-state needs.
- Context and memory pipeline work is already progressing in `docs/CONTEXT_PIPELINE_TARGET.md`; this plan should align with that instead of duplicating it.

## Non-Goals

Do not use this plan to:

- rewrite the whole bot in one pass
- replace the memory vault implementation
- redesign the Minecraft/Voyager architecture from scratch
- change the user-facing control page mode model
- hide runtime instability behind broader exception handling
- introduce a second TTS or route system beside the existing one

## Execution Order

### Phase 0: Baseline and Guardrails

Goal:

Capture the current runtime shape before editing hot-path logic.

Work:

- Record current key files and ownership boundaries.
- Confirm existing tests related to voice pipeline, TTS tag handling, memory writer, context pipeline, and Minecraft state still run.
- Identify which processes need manual restart and avoid restarting the gateway without user approval.
- Add or update a short implementation note before each risky migration.

Likely files:

- `main.py`
- `evelyn_core/runtime/evelyn_core/voice_pipeline.py`
- `evelyn_core/runtime/evelyn_core/voice_orchestration.py`
- `evelyn_core/runtime/evelyn_core/tts_playback.py`
- `evelyn_core/runtime/evelyn_core/paths.py`
- `docs/plans/VOICE_PIPELINE_REFACTOR_PLAN.md`
- `docs/CONTEXT_PIPELINE_TARGET.md`

Exit criteria:

- Current test baseline is known.
- Any failing tests are classified as pre-existing or caused by the phase.
- No runtime process has been restarted without explicit permission.

### Phase 1: TurnTrace Summary Contract

Goal:

Make every user turn leave one compact, comparable summary before deeper refactors.

Why first:

TTS and orchestration changes can fail in subtle ways. A stable turn summary gives each later phase a ruler.

Work:

- Define a stable `TurnTrace` or `TurnSummary` schema.
- Keep existing event JSONL behavior, but add a final summary record per logical turn.
- Include enough fields to debug routing, context, LLM, TTS, playback, cancellation, and memory writes.
- Ensure trace writing cannot break the hot path.

Suggested summary fields:

```json
{
  "turn_id": "...",
  "source": "voice | discord | control | minecraft | internal",
  "input_text": "...",
  "route": "...",
  "needs_main_llm": true,
  "needs_memory": true,
  "needs_tts": true,
  "context_tokens_estimate": 0,
  "llm_ms": 0,
  "tts_first_audio_ms": 0,
  "playback_started": false,
  "playback_cancelled": false,
  "memory_writer_decision": {},
  "minecraft_snapshot_age_ms": null,
  "error_layer": null,
  "error": null
}
```

Acceptance:

- Every completed or aborted turn emits a summary.
- Missing optional fields are explicit `null`, not absent due to accidental code paths.
- Trace write failure is logged but does not crash or block reply delivery.

Validation:

- Run focused tests for trace helpers if available.
- Run one text/control-page style path if possible.
- Inspect one produced JSONL row for schema stability.

### Phase 2: TTS Playback Manager Cleanup

Goal:

Make one owner responsible for TTS playback state, cancellation, first-audio timing, and stream cleanup.

Why second:

Playback is one of the most fragile hot-path surfaces. It should move only after Phase 1 makes regressions visible.

Work:

- Treat `evelyn_core/runtime/evelyn_core/tts_playback.py` as the target home.
- Move duplicated stream/playback bookkeeping out of `main.py` in small slices.
- Preserve existing public call behavior from `main.py` until the manager boundary is proven.
- Keep barge-in, cancellation, and post-playback suppression behavior explicit.
- Standardize first-audio latency and playback completion metrics into the turn summary.

Target ownership:

- `TtsPlaybackManager`: playback lifecycle, cancellation, stream cleanup, first-audio timestamp, current playback state.
- `main.py`: call into the manager and handle high-level turn decisions only.
- TTS provider/voice synthesis code: synthesize audio; do not own Discord playback policy.

Acceptance:

- There is no parallel TTS playback state split between `main.py` and `tts_playback.py`.
- Cancelling a turn cancels active TTS producer/playback tasks.
- Playback completion and cancellation are both reflected in turn summary.
- Existing emotion/tag cleanup behavior remains unchanged.

Validation:

- Run TTS-related unit tests.
- Run a short local voice/text-to-TTS path if the runtime is available.
- Check likely failures twice: stuck playback task, missed cancellation, duplicate playback, missing first-audio metric.

### Phase 3: VoiceTurnOrchestrator Migration

Goal:

Move turn progression responsibility from `main.py` into a narrow orchestrator without changing the runtime contract.

Why third:

Once tracing and TTS are clearer, the orchestrator can coordinate stages without inheriting ambiguous playback behavior.

Work:

- Expand `voice_orchestration.py` around the current real flow.
- Move one responsibility at a time: route call, action execution, answer payload creation, delivery call.
- Keep source-specific ingress in the existing place until the orchestrator boundary is stable.
- Avoid changing STT segmentation and Discord audio receive behavior in this phase.

Target flow:

```text
Voice input / text input
  -> normalized turn request
  -> VoiceTurnOrchestrator
  -> route decision
  -> action / LLM / search / control command
  -> answer payload
  -> delivery / TTS manager
  -> trace summary
```

Acceptance:

- `main.py` no longer owns the middle of the voice turn when the orchestrator owns it.
- Existing entry points still work.
- Orchestrator dependencies are passed explicitly rather than imported from global state when practical.
- Failures identify the layer that failed.

Validation:

- Run voice pipeline tests.
- Run at least one non-voice text/control command path to ensure shared turn logic did not become voice-only.
- Check likely failures twice: wrong source identity, lost room/session state, delivery called twice, no reply on route fallback.

### Phase 4: RouteDecision Execution Policy

Goal:

Make the router decide not only "what route", but also what expensive systems are needed for this turn.

Why fourth:

After the orchestrator boundary exists, route policy can reduce unnecessary LLM/memory/TTS work without scattering conditionals.

Work:

- Extend `RouteDecision` with explicit policy flags.
- Align with `ContextPolicy` from `docs/CONTEXT_PIPELINE_TARGET.md`.
- Make low-latency paths skip memory/context/LLM/TTS only through the route policy, not ad hoc checks.
- Add fallback defaults for old callers.

Suggested fields:

```python
needs_main_llm: bool
needs_memory: bool
needs_runtime_state: bool
needs_minecraft_state: bool
needs_vision: bool
needs_tts: bool
needs_search: bool
response_mode: str
priority: str
```

Acceptance:

- Existing route decisions still deserialize/construct safely.
- The orchestrator reads policy flags rather than duplicating route classification.
- Turn summary records the policy used.
- Context pipeline receives route policy consistently.

Validation:

- Router tests for default/fallback construction.
- A fast acknowledgement path that does not need the main LLM.
- A normal answer path that still includes memory and TTS.
- A Minecraft/status path that requests runtime state intentionally.

### Phase 5: Memory Write-Behind

Goal:

Keep memory writes from increasing voice-turn latency or destabilizing replies.

Why fifth:

Route policy and turn tracing should already identify when memory is needed. Then memory writing can be made asynchronous without hiding outcomes.

Work:

- Use the existing memory writer decision contract from `docs/CONTEXT_PIPELINE_TARGET.md`.
- Queue post-turn summary/fact/open-question writes after response delivery.
- Record queued, completed, skipped, and failed writes in trace summaries or artifacts.
- Keep durable user/project preferences conservative; do not save noisy observations as long-term memory.

Acceptance:

- Reply delivery does not wait on non-critical memory writes.
- Failed background memory write is visible in logs/trace.
- User-explicit "remember this" still receives stronger handling than incidental observations.
- Shutdown/restart does not silently lose critical queued writes without a record.

Validation:

- Memory writer tests.
- Simulated write failure.
- Check likely failures twice: duplicated memories, lost explicit memory, blocking the voice reply, writing private/noisy data.

### Phase 6: Minecraft/Voyager Runtime Snapshot

Goal:

Make Minecraft/Voyager status a structured snapshot consumed by routes, context, and UI instead of ad hoc live reads.

Why sixth:

The voice/dialogue path should not block or hallucinate from stale game state. A snapshot boundary makes freshness explicit.

Work:

- Define one compact runtime snapshot shape for Minecraft/Voyager state.
- Include freshness metadata: timestamp, age, source, connected/running/error state.
- Feed route/context/UI from the snapshot where possible.
- Do not infer bot inventory/state from screenshots of the user's game screen.

Suggested snapshot sections:

- connection/service state
- current goal/task
- inventory summary
- position/dimension if available
- recent failure/error
- last observation timestamp
- data freshness classification

Acceptance:

- UI and dialogue can tell stale state from absent state.
- Minecraft context uses compact state, not raw large logs.
- Snapshot failure does not block unrelated chat.
- Turn summary can record snapshot age when a turn uses Minecraft state.

Validation:

- Existing Minecraft/Voyager service tests if available.
- One status query with no Minecraft session.
- One status query with stale/failed snapshot.
- Check likely failures twice: stale state presented as live, screenshot treated as bot state, blocking status call, missing error surfacing.

### Phase 7: Runtime Artifacts Retention and Rotation

Goal:

Keep runtime artifacts useful without unbounded growth.

Why last:

Earlier phases should produce clearer traces and snapshots first. Then retention rules can be applied to the right files.

Work:

- Inventory files under `runtime_artifacts`.
- Add retention policy for logs, traces, benchmarks, audio/debug files, and snapshots.
- Prefer size/count/age limits that keep recent debugging material.
- Make cleanup explicit and recoverable where practical.

Acceptance:

- Runtime artifacts have documented retention defaults.
- Cleanup does not remove current active files.
- Debug files from recent failures are preserved long enough to investigate.
- Rotation behavior is testable without deleting unrelated user files.

Validation:

- Dry-run cleanup mode if implemented.
- Unit tests for path scoping and retention selection.
- Check likely failures twice: deleting active logs, deleting wrong directory, unbounded artifact growth, hiding recent failure evidence.

## Cross-Phase Rules

- Keep changes small enough to verify in one sitting.
- Do not restart OpenClaw gateway or Evelyn services without explicit user approval.
- Never treat screenshots from the user's own game view as authoritative bot-state evidence.
- Preserve current user-facing tone and UI behavior unless the phase explicitly changes it.
- Update docs when a phase changes ownership boundaries.
- Prefer existing modules and contracts over new parallel systems.
- Before reporting a phase done, re-check the requested requirement and likely new failure modes 2 to 3 times.

## Recommended First Implementation Slice

Start with Phase 1 only:

1. Locate the current turn/event trace writer in `main.py`.
2. Add a narrow `TurnSummary` helper or dataclass near the existing trace code.
3. Emit one summary on successful answer, ignored/aborted turn, and exception path.
4. Keep the schema tolerant: optional fields can be `null`, but the key names should remain stable.
5. Add a focused test or small local verification that malformed trace data cannot break reply delivery.

Only after that should TTS ownership be moved.

## Implementation Status

### 2026-05-29

- Phase 1 started: `evelyn_core/runtime/evelyn_core/turn_trace.py` now owns the stable turn summary payload shape.
- Existing `text_turn_summary`, `voice_turn_summary`, and `voice_drop_summary` events now use the stable summary schema while keeping existing event names.
- Summary events preserve explicit `null` values so missing optional fields are visible instead of silently absent.
- `MemoryWriterDecision` can now serialize itself through `to_dict()` and text/voice turn summaries attach the memory writer decision when available.
- Phase 2 first slice started: duplicate TTS playback source/helper definitions were removed from `main.py`; `main.py` now uses the implementations imported from `evelyn_core/runtime/evelyn_core/tts_playback.py`.
- `tts_playback.py` logging is connected to `main.py` through `configure_tts_playback_logging(log_turn_event)`.
- Phase 2 second slice: active TTS playback state is now held behind `TtsPlaybackRegistry` in `tts_playback.py` instead of direct dict mutation in `main.py`.
- Interrupt cleanup mechanics for queued TTS playback moved into `stop_tts_playback_state()`; `main.py` now performs guild-level bookkeeping and trace logging around that helper.
- Added focused tests for turn summary schema and TTS playback contract helpers.
- Phase 2 continued in small slices: queue sinks, streaming delivery, audio source playback, sentence chunking, prefetch, prepared playback queue drain, cleanup, playback starter, and playback tracking helpers now live in `tts_playback.py`.
- `main.py` no longer directly mutates TTS playback registry start/update/stop/finish state; it calls TTS playback helpers and keeps high-level Discord/turn decisions.
- Turn summaries now include explicit `playback_completed` alongside `playback_started` and `playback_cancelled`, and TTS paths mark cached/single/streaming playback completion state.
- Cached TTS answer matching and TTS playback registry read helpers now live in `tts_playback.py`; `main.py` passes config/runtime context and no longer directly calls registry `keys/get/len/contains` methods.
- Post-TTS input suppression is now evaluated through `tts_input_suppression_reason()` in `tts_playback.py`, and the TTS state containers are grouped under `TtsPlaybackTracker`.
- TTS playback helpers now accept the tracker directly, so `main.py` no longer needs to pass registry/speaking/last-audio containers separately through the playback lifecycle.
- Current validation baseline after the latest Phase 2 slice: focused TTS tests pass, full unittest discovery runs 89 tests OK, `diff --check` has only the existing LF/CRLF warning, and unicode corruption scans report 0 suspicious matches in touched files.

### 2026-05-30

- Phase 3 implemented: `VoiceTurnOrchestrator` now owns the middle of the voice/text turn flow through explicit request, route context, result, and dependency objects.
- `ask_llm_streaming()` now builds a normalized `VoiceTurnRequest` and delegates short-circuit handling, registered route execution, skill-route delivery, and main-LLM fallback through the orchestrator.
- Orchestrator failures mark the failed layer in turn metrics so summaries can distinguish route context, short-circuit, skill route, delivery, and main LLM failures.
- Added focused `tests/test_voice_turn_orchestrator.py` coverage for short-circuit stop, skill-route delivery, main fallback, source/session identity propagation, single delivery, no-reply fallback, and error-layer marking.
- Phase 4 implemented: `RouteDecision` now carries explicit execution policy flags aligned with `ContextPolicy`, including main LLM, memory, runtime, Minecraft, vision, skill graph, long context, search, TTS, response mode, and priority.
- Context policy now preserves new route flags, low-latency fast paths can skip memory/runtime context through route metadata, and `prepare_llm_messages()` builds memory context only after policy says it is needed.
- `VoiceTurnOrchestrator` reads route policy flags: it can deliver a policy answer without main LLM when `needs_main_llm` is false, and skill/policy/main delivery respects `needs_tts`.
- Turn summaries now include route-policy details for `needs_search`, `route_priority`, and `response_mode`.
- Added `tests/test_route_policy.py` and expanded trace/orchestrator tests for default construction, old-mapping fallback, fast acknowledgement without main LLM, normal answer memory/TTS, and Minecraft/status policy.
- Phase 5 first slice implemented: memory write-behind task state now has a small helper module that marks queued, running, completed, failed, cancelled, skipped, and deferred outcomes on the existing memory writer decision payload.
- `schedule_memory_update()` now returns visible write-behind status, records raw transcript/vault mirror outcomes, wraps summary/cognitive refresh work so background failures are logged and reflected in the decision payload, and avoids replacing explicit long-term-memory writes with incidental batch updates.
- Memory write-behind status is also appended to `runtime_artifacts/memory/writebehind_status.jsonl`, so queued/running/completed/failed/cancelled/skipped/deferred outcomes remain visible outside the in-memory decision payload.
- Added `tests/test_memory_writebehind.py` coverage for completed writes, simulated failure, cancellation, queue status, status JSONL events, safe event serialization, and explicit-memory task-key behavior.
- Current validation baseline: focused policy/orchestrator/trace/control-page/write-behind tests pass, full unittest discovery runs 110 tests OK, and `diff --check` has only the existing LF/CRLF warning.
- Phase 6 first slice implemented: `evelyn_core/runtime/evelyn_core/minecraft_runtime_snapshot.py` defines a compact Minecraft/Voyager runtime snapshot with freshness, age, source, connection/running state, goal/task, position, health/hunger, inventory summary, and error fields.
- Live status, live observation, control-page live snapshots, cache copies, and context error fallbacks now attach the standard `runtime_snapshot` while preserving the existing flat control-page fields.
- Minecraft context and dialogue summaries now expose snapshot freshness/age/error so stale or absent state is not presented as live state.
- Turn summaries can now record `minecraft_snapshot_age_ms` and `minecraft_snapshot_freshness` when a turn uses Minecraft state.
- Control-page JSON and `/status` text now expose snapshot freshness and age through the standard runtime snapshot while preserving existing fields.
- Added `tests/test_minecraft_runtime_snapshot.py` coverage for fresh, absent, stale/expired, error, flat-shape preservation, skill-context freshness, and UI status-field cases. Current validation baseline: full unittest discovery runs 117 tests OK, and `diff --check` has only LF/CRLF warnings.
- Phase 7 implemented: `evelyn_core/runtime/evelyn_core/runtime_artifacts_retention.py` now inventories `runtime_artifacts`, builds a dry-run cleanup plan, protects active state files, enforces root scoping, and can apply cleanup only when explicitly invoked with `--apply`.
- Runtime artifact retention defaults are documented in `docs/RUNTIME_ARTIFACTS_RETENTION.md`, covering logs, turn traces, benchmarks, memory write-behind events, voice debug audio, control-page dumps, Minecraft window snapshots, test status files, and Voyager JSONL logs.
- Added `tests/test_runtime_artifacts_retention.py` coverage for inventory scoping, age rules, size rules, active-file protection, dry-run behavior, apply behavior, and CLI dry-run defaults.
- A real dry-run against `runtime_artifacts` selected 4 old/overflow log candidates totaling 5,675,675 bytes and deleted nothing. Current validation baseline: full unittest discovery runs 124 tests OK, `py_compile` passes for touched runtime modules and `main.py`, and `diff --check` has only LF/CRLF warnings.

## Related Documents

- `docs/plans/VOICE_PIPELINE_REFACTOR_PLAN.md`
- `docs/CONTEXT_PIPELINE_TARGET.md`
- `docs/EVELYN_MEMORY_VAULT_ARCHITECTURE.md`
- `docs/EVELYN_CURRENT_STRUCTURE_LAYER_MAPPING.md`
- `CURRENT_EVELYN_ARCHITECTURE.md`
- `CURRENT_BOT_STRUCTURE.md`
