# Evelyn Plans Consolidated

Last consolidated: 2026-06-08

This file merges the previous `docs/plans/*.md` planning documents into one searchable reference. Some sections are historical or already implemented; verify against code before treating a plan as current runtime behavior.

## Included Source Files

- `EVELYN_ASSISTANT_PHASE1_STRUCTURE_REFACTOR.md`
- `EVELYN_CONTROL_PAGE_MODE_TARGET.md`
- `EVELYN_HOTPATH_STABILIZATION_REFACTOR_PLAN.md`
- `EVELYN_LANDING_PAGE_TARGET.md`
- `EVELYN_PAGE_DISTRIBUTION_TARGET.md`
- `EVELYN_PIPELINE_EXTRACTION_BLUEPRINT.md`
- `EVELYN_TTS_PHASE2_RUNTIME_VERIFICATION_CHECKLIST.md`
- `KANANA_VISION_TOOL_USE_IMPLEMENTATION_PLAN_2026-06-06.md`
- `LOCAL_ONLY_AND_LLM_TOPOLOGY_EXECUTION_BLUEPRINT.md`
- `THIN_QUESTION_FEATURE_EXECUTION_NOTE.md`
- `VOICE_PIPELINE_REFACTOR_PLAN.md`

---

## Source: EVELYN_ASSISTANT_PHASE1_STRUCTURE_REFACTOR.md

# Evelyn Assistant Phase 1 Structure Refactor

## Purpose

This document defines the first structural refactor pass needed to move Evelyn
toward the target assistant architecture without mixing in large behavioral
changes too early.

Phase 1 is about boundaries, not ambition.

The goal is to reduce architectural coupling in the current assistant runtime
while preserving current user-facing behavior as much as possible.

---

## References

- target blueprint: `docs/EVELYN_ASSISTANT_TARGET_ARCHITECTURE.md`
- current-to-target mapping: `docs/EVELYN_CURRENT_STRUCTURE_LAYER_MAPPING.md`
- voice chain reference: `docs/plans/VOICE_PIPELINE_REFACTOR_PLAN.md`

---

## Phase 1 Goal

Turn `main.py` from the place where Evelyn effectively is into a composition
root that wires together clearer modules.

By the end of Phase 1, Evelyn should still behave like the same assistant, but
the following boundaries should be explicit:

- ingress/session
- realtime turn orchestration
- TTS client contract
- memory facade
- runtime snapshot and presentation facade

---

## Why This Comes Before TTS Replacement

The desired WSL `omnivoice-triton` move is not just a faster server swap.
It requires a stable assistant-side TTS contract first.

If Phase 1 is skipped, the TTS migration will be forced to untangle:

- assistant hot path behavior
- TTS transport details
- playback assumptions
- voice profile handling
- health and warmup logic

all at the same time.

That is the wrong order.

Phase 1 should create the assistant-side boundary first.
Phase 2 can then swap the backend behind that boundary.

---

## Non-Goals for Phase 1

Phase 1 should not try to do the following:

- replace OmniVoice with `omnivoice-triton`
- redesign the control page visually
- redesign long-term memory semantics
- redesign the Minecraft autonomy architecture
- change the launcher topology
- change wake policy or relaxed/strict gate policy

This pass is specifically about code shape and runtime boundaries.

---

## Constraints That Must Stay Stable

The following product and policy behavior should stay intact:

- whitelist-first wake handling
- room-owner and per-speaker session separation
- turn-based tracing with `turn_id`, `segment_id`, `chunk_index`
- current voice-first UX expectations
- visible launcher and runtime surfaces
- existing control/status operability

---

## Phase 1 Deliverables

Phase 1 should end with five clear assistant-side modules or facades.

## 1. Ingress and Session Facade

Own:

- audio acceptance/rejection decisions
- wake gating
- session ownership
- pre-STT quality gates
- queue acceptance metadata

Must not own:

- main answer generation
- TTS request transport
- memory writeback

Done when:

- the rest of the system receives an accepted turn-shaped input instead of raw
  ingress logic
- rejection reasons are explicit and observable

---

## 2. Realtime Turn Orchestrator

Own:

- STT call sequencing
- fast route selection
- main response generation
- answer payload shaping for delivery
- turn trace lifecycle

Must not own:

- wake policy
- raw Discord ingress handling
- backend-specific TTS transport details
- post-turn long-memory mutation internals

Done when:

- one narrow module represents the hot path for accepted turns
- the hot path can be reasoned about without reading presentation or memory
  internals

---

## 3. TTS Client Facade

Own:

- TTS service health
- warmup
- synth request contract
- voice/profile lookup contract
- response normalization into assistant playback input

Must not own:

- high-level reply decisions
- room ownership logic
- long-term memory

Done when:

- the assistant no longer cares whether the backend is Windows OmniVoice or a
  future WSL compatibility service
- `main.py` no longer directly encodes the backend-specific HTTP shape

---

## 4. Memory Facade

Own:

- turn-time recall interface
- post-turn writeback scheduling
- history append/writeback separation
- timeout and background execution policy

Must not own:

- wake logic
- TTS transport
- UI formatting

Done when:

- the hot path calls a compact recall contract
- writeback happens through a separate background-facing contract

---

## 5. Runtime Snapshot and Presentation Facade

Own:

- runtime snapshot collection
- voice pipeline snapshot assembly
- control-page/status payload shaping
- presentation-facing status contracts

Must not own:

- answer generation
- wake/session policy
- tool execution internals

Done when:

- presentation reads from a shared snapshot/facade instead of scattered global
  state and ad hoc runtime probes

---

## Recommended Order

The safest Phase 1 order is:

1. Define assistant-side contracts and data shapes.
2. Extract ingress/session responsibility.
3. Extract the TTS client boundary.
4. Extract the realtime turn orchestrator.
5. Extract the memory facade.
6. Extract runtime snapshot/presentation assembly.
7. Collapse `main.py` into wiring and bot entrypoints.

This order keeps the highest-risk runtime path understandable while still
building toward backend replacement.

---

## Detailed Work Sequence

## Step 1. Define contracts first

Create typed or otherwise explicit assistant contracts for:

- accepted voice turn
- rejected segment
- route decision
- answer payload
- TTS synth request/result
- memory recall request/result
- post-turn writeback event
- runtime snapshot

Reason:

Without contract-first extraction, the refactor will just move implicit globals
into more files.

Initial draft code location:

- `evelyn_core/runtime/evelyn_core/assistant_contracts.py`

That file should be treated as the first shared contract surface for Phase 1.
Existing `voice_pipeline.py` types remain valid, but this new module is the
place where cross-layer assistant contracts should converge.

---

## Step 2. Extract ingress/session logic

Pull acceptance, wake, ownership, and queueing decisions behind one facade.

Reason:

This is the cleanest way to stop raw voice ingress concerns from continuing to
bleed into the rest of the turn code.

Guardrail:

Do not change wake behavior during this step.
Only change where the decisions live.

---

## Step 3. Extract the TTS client boundary

Move health, warmup, synth request, and profile handling behind one client.

Reason:

This is the prerequisite for a clean WSL TTS backend swap.

Guardrail:

Keep the current HTTP contract stable during Phase 1.
Do not mix backend replacement into this extraction.

---

## Step 4. Extract the realtime turn orchestrator

Move the accepted-turn hot path into one narrow module that coordinates:

- STT
- fast route selection
- main reply generation
- TTS request build
- playback handoff

Reason:

This is the actual assistant core and needs to become legible before deeper
optimization or replacement work.

Guardrail:

Do not let memory writeback or control-page formatting remain in this path.

Current incremental slice:

- move the accepted voice-turn activation block out of `main.py`
- let one orchestration helper own:
  - accepted turn id creation
  - reply lifecycle calculation
  - room owner/session update preparation
  - accepted-turn contract assembly
- keep `main.py` responsible only for wiring the returned activation into turn
  scope, locking, and delivery
- next, move accepted-turn execution binding/cleanup out too:
  - turn-scope creation and stale-scope replacement
  - reply-in-progress state entry
  - execution cleanup and reply-state release
- then move the voice reply delivery branch out of `main.py`:
  - wake-only canned reply path
  - LLM-plus-TTS delivery path
  - answer normalization before side effects
- in the meantime, keep shrinking the post-delivery block:
  - finalize side effects
  - turn-complete logging
- current checkpoint after runtime verification:
  - `main.py` now delegates:
    - accepted-turn activation
    - execution binding/cleanup
    - room-locked voice reply delivery/finalize
    - gate evaluation plus accepted reply-request preparation
    - speaker-activity update plus active-speaker metric packaging
    - room lock lookup plus pre-lock wait reporting
    - delivery execution plus cleanup wrapping
    - accepted execution plus delivery-runtime assembly
    - accepted-path sequencing and execution handoff
    - reply-preparation result handling, including accepted/drop branching
    - transcript-driven speaker/reply processing after final STT
    - transcript-driven reply facade via grouped context/dependency objects
  - the main remaining inline responsibilities in this path are now mostly:
    - top-level transcript/debug logging
    - construction of the grouped request/dependency facade inputs

---

## Step 5. Extract the memory facade

Split:

- recall needed now
- writeback needed later

Reason:

This is required for low-latency behavior and for long-term memory growth
without hot-path pollution.

Guardrail:

The first pass should preserve current memory usefulness, not redesign memory
content quality.

---

## Step 6. Extract runtime snapshot and presentation

Gather voice, TTS, runtime-service, and Minecraft-facing status into a shared
snapshot layer that control page and command handlers consume.

Reason:

This preserves visibility while reducing UI/runtime coupling.

Guardrail:

Do not regress operational visibility to make the code look cleaner.

---

## Step 7. Reduce `main.py`

After the above extractions, `main.py` should mostly contain:

- bot startup
- event registration
- dependency wiring
- command entrypoints
- composition of already-extracted modules

Reason:

This is the measurable sign that the refactor actually happened.

---

## Success Criteria

Phase 1 is successful when all of the following are true:

- `main.py` is no longer the only place where the assistant can be understood
- accepted-turn handling can be read without reading control-page code
- TTS backend assumptions are isolated behind one client/facade
- memory recall and writeback are separate contracts
- presentation consumes a stable runtime snapshot shape
- the current user-visible behavior remains broadly intact

---

## What Phase 2 Should Then Do

After Phase 1, the next logical structural move is:

- replace the current Windows OmniVoice backend with a WSL-hosted
  compatibility service backed by `omnivoice-triton`

Phase 2 should be a backend swap behind the preserved TTS client contract, not
another broad assistant refactor.

---

## Source: EVELYN_CONTROL_PAGE_MODE_TARGET.md

# Evelyn Control Page Mode Target

## Goal

Keep one stable three-column control page frame, but make the page read as an operator dashboard by default and only turn into a Minecraft mission dashboard when a live Minecraft session is actually active.

## Required Shape

- keep the existing three-slot desktop frame:
  - left = state/context slot
  - center = current focus slot
  - right = actions/tools slot
- keep shared chrome stable across modes:
  - avatar
  - chat thread
  - composer
  - outer frame
- swap slot content and slot language by mode instead of swapping the full page

## Mode Rules

### Default Mode

- page should read as Evelyn operations, not Minecraft standby
- surface:
  - current voice/runtime state
  - focus summary
  - recommended next action
  - latest response / latest user context
  - current issue or wait reason when relevant

### Minecraft Mode

- page should read as live mission control
- surface:
  - goal
  - task and stage
  - position / progress
  - inventory and survival state
  - recent activity

### Warmup / Offline / Issue States

- warmup stays in default mode until a real Minecraft session is live
- offline, stale, and issue states should be visible as operating states, not hidden inside generic copy
- mode labels, pills, and panel headings should reflect these states directly

## Frontend Implementation Rules

- backend remains the source of truth for `ui.mode`, `ui.submode`, and `ui.reason`
- frontend should use class/data-attribute mode toggles instead of ad-hoc inline visibility logic
- headings, pills, and summary copy should switch with the mode so default mode does not look Minecraft-branded

---

## Source: EVELYN_HOTPATH_STABILIZATION_REFACTOR_PLAN.md

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

---

## Source: EVELYN_LANDING_PAGE_TARGET.md

# Evelyn Landing Page Target

## Goal

Rebuild the current page by preserving the user's sketch skeleton and only adding detail on top of that skeleton.

## Required Layout

- full-screen three-panel composition
- left rail as three stacked modules:
  - small yellow note card on top
  - inline handwritten-style yellow status text in the middle
  - large orange expansion card at the bottom
- center stage as the dominant area with a thin top bar and one very large live model viewport
- right rail as two stacked modules:
  - compact profile and runtime status card on top
  - larger command console/input card on the bottom

## Color Mapping

- yellow boxes map to notes, reminders, and raw world-state annotations
- orange box maps to expandable future data blocks
- green/teal right-side boxes map to profile, runtime state, and command console
- dark green/black center viewport remains mostly open and is where the CSS Evelyn model sits
- keep the box colors from the sketch; add detail inside them rather than inventing new frame colors or moving the boxes

## Visual Direction

- dark teal/ink base with subtle beige influence in the background mix
- visible wireframe-style borders, especially pink/red outer rails
- yellow and orange callout boxes on the left rail
- keep the look closer to a control room mockup than a product hero page
- avoid marketing-landing-page copy blocks; the page should feel like an interface layout first
- the center viewport should dominate attention more than headers, text, or navigation
- preserve the sketch's empty space and box geometry; do not redesign the page into a different layout

## Center Stage

- top strip with Evelyn brand on the left and compact system pills on the right
- large empty viewport feel with the CSS Evelyn model centered inside it
- CSS-only Evelyn model placed inside the center viewport
- model should feel like a live 2.5D placeholder rather than a flat icon
- small overlay annotations are fine, but they must not overwhelm the viewport

## Right Rail

- upper block reserved for profile/identity status
- lower block styled as a command box
- include command chips or sample commands that feel derived from the Discord bot commands
- the lower command area should read like a console, not a marketing CTA

## Implementation Rules

- keep the page static under \`docs/\`
- no framework build step
- mobile layout can stack, but desktop should preserve the three-panel hierarchy
- JavaScript should stay light and only support small interactions such as chip-to-input filling and reveal timing

---

## Source: EVELYN_PAGE_DISTRIBUTION_TARGET.md

# Evelyn Page Distribution Target

## Goal

Make the Evelyn landing page reachable from Discord with a stable command.

## Required Shape

- the page remains a static site under `docs/`
- deployment should be GitHub Pages-friendly
- Discord command should send a public URL, not a local filesystem path
- URL should support explicit override by config, with a deterministic GitHub-derived fallback

## Fallback Rules

1. use configured `EVELYN_PAGE_URL` when present
2. otherwise derive `https://<owner>.github.io/<repo>/` from git remote origin when possible
3. if neither exists, fail clearly instead of pretending the page is reachable

---

## Source: EVELYN_PIPELINE_EXTRACTION_BLUEPRINT.md

# Evelyn Pipeline Extraction Blueprint

Last reviewed: 2026-06-02
Status: implemented extraction blueprint and change log
Primary current reference: `CURRENT_EVELYN_PIPELINE.md`

This blueprint turns the current pipeline discussion into an implementation
order. The goal is not a second architecture beside the existing one. The goal
is to collect the pieces already extracted from `main.py` into clearer runtime
contracts, then move Discord-specific responsibilities outward in controlled
slices.

As of 2026-06-02, the planned extraction sequence below has been implemented in
bounded slices. The current runtime map is `CURRENT_EVELYN_PIPELINE.md`; this
file is now the implementation record and continuation guide.

## Current Premise

The current live pipeline already has:

- `VoiceTurnRequest`
- `VoiceTurnOrchestrator`
- `RouteDecision` execution flags
- `ContextPolicy`
- `MemoryWriterDecision`
- `TurnScope`
- `TtsPlaybackTracker`, `TtsPlaybackRegistry`, prepared playback helpers
- memory write-behind status events
- runtime artifact retention helpers
- compact Minecraft/Voyager runtime snapshots

Therefore, new work should prefer facade extraction, contract tightening, and
test coverage over introducing parallel systems.

## Target Shape

```text
Discord / local mic / future transports
-> ingress adapter
-> VoiceTurnRequest
-> VoiceTurnOrchestrator
-> route/context/action/main LLM
-> delivery gateway
-> Discord delivery / TTS playback manager / control page updates
-> memory write-behind
```

Core code should receive stable primitive context such as `guild_id`,
`channel_id`, `user_id`, `session_key`, `room_key`, `source`, `turn_id`, and
callbacks. Discord objects should stay close to Discord adapters.

## Guardrails

- Do not restart Evelyn, OpenClaw gateway, Voyager, Minecraft, model servers, or
  TTS without explicit approval.
- Do not rewrite the whole pipeline in one pass.
- Do not create a second TTS/playback/session system beside the existing one.
- Do not move Discord session policy before delivery and ingress contracts are
  stable.
- Do not make router LLM, summary/sub LLM, memory deep search, or Voyager polling
  mandatory for every turn.
- Preserve current user-facing behavior unless a phase explicitly owns that
  behavior.
- Each phase needs focused tests before broad cleanup.

## Phase 0 - Baseline And Trace Contract

Purpose: make sure later movement has a ruler.

Tasks:

- Capture current `voice_turn_summary` fields used for route, TTS, playback,
  cancellation, memory write-behind, and Minecraft snapshot freshness.
- Add or update a small doc/test assertion for required turn summary fields.
- Confirm current focused tests and full unittest discovery baseline before code
  movement.

Completion criteria:

- Required trace fields are named before extraction starts.
- Current baseline is recorded in the active plan or implementation note.
- No runtime restart is required.

## Phase 1 - TTS Playback Manager Facade

Purpose: reduce `main.py` knowledge of PCM queues, AudioSource setup, OmniVoice
stream loops, prepared playback, and cancellation details.

This should wrap existing pieces in `tts_playback.py`; it should not replace
them wholesale.

Existing pieces to preserve:

- `TtsPlaybackTracker`
- `TtsPlaybackRegistry`
- `PreparedTtsPlaybackQueue`
- `PreparedPlaybackStarter`
- cached answer matching helpers
- playback state start/update/stop/finish helpers

Proposed contract:

```python
class TtsPlaybackManager:
    async def speak_once(self, request: TtsPlaybackRequest) -> TtsPlaybackResult: ...
    async def speak_streaming(self, request: TtsStreamingPlaybackRequest) -> TtsPlaybackResult: ...
    async def cancel_turn(self, turn_id: str, reason: str) -> None: ...
    async def cancel_guild(self, guild_id: int, reason: str) -> None: ...
    def snapshot(self, guild_id: int | None = None) -> dict: ...
```

First slice:

- Introduce request/result dataclasses around current helper arguments.
- Move one narrow single-answer playback path behind the facade.
- Keep `main.py` responsible for high-level turn decisions only.

Second slice:

- Move streaming TTS producer/playback startup behind the facade.
- Keep TurnTrace marks for first byte, first audio, playback start, completion,
  cancellation, and failure.

Completion criteria:

- `main.py` no longer directly owns new PCM queue construction for moved paths.
- A new voice turn can cancel prior playback through one facade call.
- Existing TTS tests pass and at least one facade-focused test exists.

Implementation note, 2026-06-02:

- First facade slice started.
- `TtsPlaybackManager` now wraps existing tracker/registry helpers for active
  state reads, start/update/finish/clear, input suppression checks, guild
  cancellation, turn cancellation, and snapshots.
- `main.py` now uses the manager for active TTS cancellation and selected input
  suppression checks.
- Second facade slice moved streaming prepared-queue playback orchestration into
  `TtsPlaybackManager.stream_sentences(...)` via `TtsStreamingPlaybackRequest`.
- `main.py` still builds the OmniVoice source callback, but no longer directly
  constructs the streaming prepared queue, `QueuedAudioSource`,
  `PreparedTtsPlaybackQueue`, `PreparedPlaybackStarter`, or streaming cleanup
  path.
- Third facade slice moved cached/single-source playback tracking and
  play/finish summary handling into `TtsPlaybackManager.play_source_once(...)`
  via `TtsSourcePlaybackRequest`.
- `main.py` still creates the cached/OmniVoice audio source, but no longer calls
  low-level playback tracking helpers or `play_audio_source(...)` directly for
  cached/non-stream answer playback.

## Phase 2 - Discord Delivery Extraction

Purpose: move Discord message send/edit/update and voice playback attachment
details out of core turn orchestration before touching ingress/session policy.

Candidate module:

```text
evelyn_core/runtime/evelyn_core/discord_delivery.py
```

Responsibilities:

- send or edit Discord text responses;
- bridge delivery plans to Discord text and voice playback;
- start/stop voice playback through the TTS playback manager;
- expose Discord delivery failures as structured delivery errors;
- keep Discord objects at the adapter edge.

Core-facing contract:

```python
class DeliveryGateway:
    async def deliver_text(self, target: DeliveryTarget, payload: AnswerPayload) -> DeliveryResult: ...
    async def deliver_voice(self, target: DeliveryTarget, plan: DeliveryPlan) -> DeliveryResult: ...
    async def cancel_delivery(self, target: DeliveryTarget, turn_id: str, reason: str) -> None: ...
```

Completion criteria:

- message send/edit/update helpers are grouped behind a delivery module;
- voice playback start/stop is invoked through delivery/TTS contracts;
- `VoiceTurnOrchestrator` still sees delivery as dependency callbacks, not raw
  Discord implementation;
- focused tests cover delivery success and delivery failure mapping.

Implementation note, 2026-06-02:

- First delivery slice started.
- Added `evelyn_core/runtime/evelyn_core/discord_delivery.py` with
  `send_discord_text(...)` and `DiscordTextDeliveryResult`.
- Moved proactive followup text delivery, including reference-message fallback,
  behind the delivery helper.
- Moved the final text send in `stream_text_reply(...)` behind the same helper.
- Moved autonomy notify/followup text sends behind the same helper.
- Added `DiscordStreamingVoiceDeliveryRequest`,
  `build_streaming_voice_delivery(...)`, and
  `execute_streaming_voice_delivery_plan(...)`.
- `main.py` no longer directly constructs the voice delivery sentence queue or
  `TTSQueueSink`; it delegates that setup to the Discord delivery adapter while
  keeping the existing `start_streaming_voice_delivery(...)` wrapper for call
  stability.
- This slice intentionally does not move command handler `ctx.send(...)` calls,
  non-stream voice playback, or message edit buffering yet.

## Phase 3 - TurnScope To TurnLifecycle

Purpose: extend the existing cancel scope instead of creating a parallel state
machine.

Existing base:

- `TurnScope`
- `turn_id`
- scoped task registration
- stale turn cancellation counters
- `raise_if_cancelled()`

Add carefully:

```python
class TurnState(Enum):
    RECEIVING_AUDIO = "receiving_audio"
    STT_RUNNING = "stt_running"
    ROUTING = "routing"
    CONTEXT_ASSEMBLING = "context_assembling"
    LLM_RUNNING = "llm_running"
    TTS_RUNNING = "tts_running"
    PLAYING = "playing"
    CANCELLED = "cancelled"
    DONE = "done"
    FAILED = "failed"
```

`TurnScope` should gain:

- current state;
- transition log;
- cancellation reason;
- `is_current(expected_turn_id)` or equivalent stale check;
- trace emission on state transition.

Completion criteria:

- barge-in cancellation changes scope state to `CANCELLED`;
- late LLM/TTS chunks are rejected by turn identity or cancelled state;
- TurnTrace records state transitions enough to debug stuck turns;
- no duplicate lifecycle registry exists beside current room/session turn scope
  storage.

Implementation note, 2026-06-02:

- First lifecycle slice started.
- Added `evelyn_core/runtime/evelyn_core/turn_lifecycle.py`.
- Moved the existing `TurnScope` shape out of `main.py` and extended it with
  `TurnState`, transition log, cancel reason, `is_current(...)`,
  `is_stale(...)`, and `snapshot()`.
- Existing task registration, task cancellation, and `raise_if_cancelled()`
  behavior are preserved.
- `replace_room_turn_scope(...)` now records `replaced_by_new_turn` as the
  stale-turn cancellation reason.
- Initial state transitions are recorded for route preparation, main LLM
  execution, and TTS execution. Broader state tracing should be added only as
  each hot path is touched.

## Phase 4 - TurnExecutionBudget

Purpose: connect router/context/memory time and fallback behavior to route
policy without over-generalizing.

Use a new name to avoid confusion with existing `ContextBudget`.

```python
@dataclass
class TurnExecutionBudget:
    max_router_ms: int = 800
    max_context_ms: int = 1200
    max_memory_items: int = 6
    max_prompt_tokens: int = 2048
    allow_background_refresh: bool = True
    stale_context_ok: bool = True
    fallback_allowed: bool = True
```

Initial mapping:

- realtime/fast path: short budget, cached state allowed, background refresh
  preferred;
- normal answer: default budget;
- accuracy/search/Minecraft route: larger budget, but still bounded;
- voice response: avoid blocking TTS start on optional background work.

Completion criteria:

- `RouteDecision` or route metadata can carry a budget object/dict;
- context assembly uses the budget for at least one real bound;
- budget overflow logs fallback reason in turn metrics;
- no path starts forcing router/memory/Voyager work on every turn.

Implementation note, 2026-06-02:

- First budget slice started.
- Added `evelyn_core/runtime/evelyn_core/turn_budget.py` with
  `TurnExecutionBudget` and `build_turn_execution_budget(...)`.
- Router classification now uses the budget's router timeout and includes
  `execution_budget` metadata for fast-path, fallback, invalid-router, and
  router-selected decisions.
- `prepare_route_context(...)` records the final route/context-policy budget in
  turn metrics metadata.
- This slice intentionally does not introduce a generic workflow scheduler or a
  broad per-route timeout matrix.

## Phase 5 - Replay/Golden Test Harness

Purpose: make hot-path regressions reproducible without a live Discord voice
session.

First fixtures:

- barge-in cancels active TTS;
- late LLM chunk after cancellation is ignored;
- TTS cancel stops producer/playback and marks trace;
- memory write-behind failure records failed status and does not block response.

Harness shape:

```text
tests/fixtures/turn_replay/
  barge_in_cancel.json
  late_llm_chunk.json
  tts_cancel.json
  memory_writebehind_failure.json
```

Completion criteria:

- each fixture can run in unittest without Discord connection;
- fixture output asserts turn state, delivery outcome, cancellation, and trace
  fields;
- failures point to route/context/LLM/TTS/delivery/memory layers.

Implementation note, 2026-06-02:

- First replay/golden slice started.
- Added fixture files under `tests/fixtures/turn_replay/` for barge-in
  cancellation, late LLM chunk, TTS cancellation, and memory write-behind
  failure.
- Added `tests/test_turn_replay_golden.py`.
- The first harness intentionally reuses existing focused contracts instead of
  pretending to be a full live Discord turn simulator.

## Phase 6 - Discord Ingress Extraction

Purpose: make `main.py` thinner by moving Discord event parsing and source
normalization into an adapter.

Candidate module:

```text
evelyn_core/runtime/evelyn_core/discord_ingress.py
```

Responsibilities:

- convert `on_message` inputs into normalized text turn inputs;
- convert voice receive/local mic handoff metadata into `VoiceTurnRequest`
  source fields;
- keep Discord member/channel/guild objects near adapter code;
- produce primitive IDs and session keys for core code.

Do after delivery extraction because ingress has more session state and more
behavioral risk.

Completion criteria:

- `main.py` Discord event handlers become thin wrappers;
- core turn execution can be invoked from a normalized request without Discord
  object assumptions;
- focused tests cover text ingress, voice ingress metadata, and local mic routed
  user metadata.

Implementation note, 2026-06-02:

- First ingress slice started.
- Added `evelyn_core/runtime/evelyn_core/discord_ingress.py`.
- Text session key, reply slot key, room/person/session memory key construction,
  text gate acceptance, and text turn input normalization now have focused
  helper functions.
- `main.py` keeps the `on_message(...)` control flow, but it now builds a
  `DiscordTextIngressContext` for text IDs and uses the text gate/input helper
  for the first extraction boundary.
- Voice room/session key construction and voice debug-meta/source normalization
  now have focused helpers, and `process_member_audio(...)` builds a
  `DiscordVoiceIngressContext`.
- This slice intentionally does not move reply-message fetching, command gating,
  voice queue policy, local mic routing policy, or Discord session policy.

## Phase 7 - Discord Session Policy Extraction

Purpose: move the stateful Discord-specific policy last, after the transport
edges are clear.

Candidate module:

```text
evelyn_core/runtime/evelyn_core/discord_session_policy.py
```

Responsibilities:

- room owner and active session decisions;
- wake/no-wake policy;
- suppression windows;
- barge-in qualification;
- reply-in-progress and awaiting-user-reply decisions;
- local mic to Discord user routing policy.

This is last because it has the highest behavior coupling.

Completion criteria:

- session policy has focused tests using primitive IDs and timestamps;
- policy returns explicit decisions rather than mutating scattered globals;
- old behavior is preserved for room ownership, wake handling, suppression, and
  barge-in.

Implementation note, 2026-06-02:

- First session-policy slice started.
- Added `evelyn_core/runtime/evelyn_core/discord_session_policy.py`.
- Voice reply gate decision now lives in `decide_voice_reply_gate(...)` with a
  primitive `VoiceReplyGateInput`; `main.py` still gathers live room/session
  state and calls the policy helper from `should_reply_to_voice(...)`.
- Local-mic Discord suppression decision now lives in
  `decide_local_mic_discord_suppression(...)`; `main.py` still updates
  `local_mic_runtime_state`.
- Barge-in TTS interrupt qualification now lives in `should_interrupt_tts(...)`
  with `TtsInterruptMeta`.
- Wake-probe full-STT skip, short transcription ignore, and short owner-followup
  candidate checks now live in pure policy helpers; `main.py` still computes
  runtime audio lengths and supplies existing text predicates/constants.
- Room owner and reply-in-progress state mutation are now accessed through
  `DiscordRoomSessionPolicy`, which wraps the existing `voice_orchestration`
  room-state helpers and the live room maps.
- Added `evelyn_core/runtime/evelyn_core/voice_stt_flow.py` with
  `WakeSttResult`, wake interpretation helpers, `run_partial_stt_flow(...)`, and
  `run_full_stt_with_optional_rescore(...)`.
- `main.py` still owns the live wake probe and audio preprocessing flow, but
  wake result normalization, strict/fuzzy wake interpretation,
  final wake-veto decision, partial-STT/speculative-policy execution, and
  full-STT/rescore execution now have focused contracts.
- Added `build_final_transcript_flow(...)` so final STT correction, partial text
  state update, stable transcript commit, transcript-result construction, and
  speculative-policy calculation are grouped behind a testable callback-based
  helper.
- This slice intentionally does not move the full wake probe/STT execution flow
  out of `process_member_audio(...)` yet.

## Recommended Implementation Order

1. Phase 0 baseline and trace contract.
2. Phase 1 TTS playback manager facade.
3. Phase 2 Discord delivery extraction.
4. Phase 3 TurnLifecycle extension.
5. Phase 4 TurnExecutionBudget.
6. Phase 5 replay/golden tests.
7. Phase 6 Discord ingress extraction.
8. Phase 7 Discord session policy extraction.

This order intentionally moves delivery before ingress, and ingress before
session policy. Delivery is easier to isolate and gives useful pressure on the
TTS facade. Session policy is saved for last because it holds the most implicit
behavior.

## Completion Summary, 2026-06-02

Completed:

- Phase 0 baseline and trace contract.
- Phase 1 TTS playback manager facade.
- Phase 2 Discord delivery extraction.
- Phase 3 TurnLifecycle extension.
- Phase 4 TurnExecutionBudget.
- Phase 5 replay/golden harness.
- Phase 6 Discord ingress extraction.
- Phase 7 Discord session policy extraction.
- Extra voice STT flow extraction after Phase 7:
  - wake result normalization;
  - strict/fuzzy wake interpretation;
  - partial STT flow;
  - full STT + optional rescore flow;
  - final transcript assembly;
  - final wake-veto decision.

Validation at completion:

- `python -m unittest discover -s tests` ran 184 tests OK.
- `python -m py_compile main.py` and touched runtime/test modules passed.
- `git diff --check` had no content errors; only existing LF/CRLF working-copy
  warnings remained.
- No runtime restart was performed.

Residual work should be treated as follow-up, not part of this extraction pass:

- live runtime verification after an approved restart;
- optional cleanup of mojibake/human-readable log strings in `main.py`;
- further adapter thinning around Discord command handlers and debug artifact
  side effects;
- possible future split of `TtsPlaybackManager` into a dedicated module if
  `tts_playback.py` grows too large.

## Per-Phase Verification Template

Before editing:

- confirm target files and current git status;
- identify whether the change touches hot path, background worker, or adapter;
- list expected unchanged user-visible behavior;
- choose focused tests before code movement.

After editing:

- run focused tests for touched contract;
- run relevant broader tests if shared contracts changed;
- run `git diff --check`;
- check likely new failure modes at least twice before reporting completion;
- update `CURRENT_EVELYN_PIPELINE.md` or this blueprint if the contract changes.

## Open Questions

- Should the TTS facade live entirely in `tts_playback.py`, or should there be a
  thin `tts_playback_manager.py` that imports existing helpers?
- Should `TurnState` live beside `TurnScope` initially in `main.py`, then move
  out after tests, or move directly into a runtime module?
- Should Discord delivery and ingress use one `discord_adapter.py` module at
  first, then split, or start as separate modules?

Default answers unless proven otherwise:

- Keep the first TTS facade near existing helpers to reduce import churn.
- Extend `TurnScope` in place first, then move once tests make the contract
  stable.
- Split Discord delivery and ingress from the start, because their failure modes
  and test fixtures are different.

Current answers after implementation:

- The first TTS facade lives in `tts_playback.py`.
- `TurnScope` now lives in `turn_lifecycle.py`.
- Discord delivery and ingress were split from the start.

---

## Source: EVELYN_TTS_PHASE2_RUNTIME_VERIFICATION_CHECKLIST.md

# Evelyn TTS Phase 2 Runtime Verification Checklist

Purpose: decide whether Phase 2 TTS playback cleanup is safe to close before starting Phase 3.

Rule: do not restart Evelyn, OpenClaw, TTS, Voyager, or gateway unless ?뺥썕 explicitly approves it.

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

Only run after ?뺥썕 approves restarting Evelyn or otherwise confirms the running process has loaded the new code.

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

---

## Source: KANANA_VISION_TOOL_USE_IMPLEMENTATION_PLAN_2026-06-06.md

# Kanana Vision Tool-Use Implementation Plan

Date: 2026-06-06
Owner context: Evelyn local runtime on ?뺥썕's Windows/WSL machine

## Purpose

This document turns the 2026-06-06 discussion into an implementation plan for
the next Evelyn runtime iteration.

The desired behavior is not simply "load more models." The target is:

- Use `kakaocorp/kanana-1.5-8b-instruct-2505` + Evelyn LoRA as the main LLM.
- Let Evelyn periodically notice meaningful screen changes and react lightly,
  because ?뺥썕 does not always talk much.
- Make Evelyn use tools more reliably instead of guessing.
- Keep the RTX 3090 VRAM budget under control.
- Avoid retaining screen/OCR data longer than needed.

## Current Verified Facts

### Main LLM

`Kanana Q4_K_M + Evelyn LoRA v1` is verified for the standalone Main LLM path.

Current intended defaults:

- `MAIN_LLM_HF=off`
- `MAIN_LLM_MODEL=/mnt/c/Users/Admin/llama.cpp/models/kanana-1.5-8b-instruct-2505-q4_k_m.gguf`
- `MAIN_LLM_LORA=/mnt/c/Evelyn/training/evelyn_lora_kanana_v1/outputs/kanana_evelyn_lora_v1.gguf`
- `LLM_MODEL_NAME=kanana-1.5-8b-instruct-2505-q4_k_m+evelyn-lora-v1`
- `MAIN_LLM_STOP_TOKENS=<|eot_id|>,<|end_of_text|>`

Touched files from the main LLM switch:

- `C:\Evelyn\evelyn_core\start_env.bat`
- `C:\Evelyn\evelyn_core\start_main_llm.bat`
- `C:\Evelyn\evelyn_core\runtime\launchers\run_main_llm.sh`
- `C:\Evelyn\evelyn_core\runtime\evelyn_core\config.py`
- `C:\Evelyn\evelyn_core\runtime\evelyn_core\skills\routing\voice_llm.py`
- `C:\Evelyn\evelyn_core\runtime\evelyn_core\skills\conversation\__init__.py`
- `C:\Evelyn\main.py`
- `C:\Evelyn\training\evelyn_lora_v1\eval_runtime_v1.py`

Standalone verification result:

- `9820` responded through `/v1/models`.
- llama.cpp command included `--lora-scaled ...kanana_evelyn_lora_v1.gguf:1.0`.
- 6 smoke prompts passed: screen honesty, no fake button pressing, local TTS
  diagnostic, echo resistance, Chinese leakage, stop-token leakage.
- 90 eval prompts completed:
  - bad-pattern rows: `0`
  - Han-script rows: `0`
  - echo-like rows: `0`
  - cold-marker rows: `0`
  - awkward-marker-only rows: `9`
- Cached generation speed observed around `92-96 tok/s`.
- Standalone RTX 3090 VRAM for Kanana+LoRA was about `7.9 GB`.

Previous non-Gemma baseline:

- Removed from the active Evelyn plan because its license does not fit
  commercial-use-safe defaults.
- Keep new router/sub/main LLM planning on Gemma 4 text-only models unless the
  replacement license is checked first.

### Full Stack VRAM Failure

Starting the full local stack with Kanana succeeded briefly, then exceeded the
RTX 3090 practical VRAM budget.

Positive checks before failure:

- Control page reached `100% / ?꾩껜 ?쒕쾭 以鍮??꾨즺`.
- `mainModel` showed `kanana-1.5-8b-instruct-2505-q4_k_m+evelyn-lora-v1`.
- Local mic was enabled with `captureReady=true`.
- Foreground launch avoided the old black-screen capture failure:
  `capture_black=false`.
- Vision health showed SmolVLM2 and Falcon-OCR loaded.
- A control-page chat request reached the local speaker path.

Failure:

- RTX 3090 climbed to about `24.1 GB / 24.6 GB`, GPU util `100%`.
- Bot-Control / control page `8799` stopped responding.
- ?뺥썕 manually brought down the bot processor because OOM was happening.
- After shutdown, Evelyn ports `8799/8880/8891/9820/9821/9822` were down and
  RTX 3090 returned to about `1.4 GB` used.

Conclusion:

Kanana standalone is healthy. The full-stack failure was caused by the combined
load of Main LLM + TTS + Vision + STT/Qwen ASR on the RTX 3090.

### Falcon-OCR Measurement

Falcon-OCR was measured alone in WSL on the RTX 3090 with:

- `VISION_LOAD_SMOL=false`
- `VISION_LOAD_OCR=true`
- `VISION_OCR_DTYPE=auto`
- small synthetic OCR image

VRAM:

- baseline before load: `1276 MB`
- after Falcon-OCR load: `2498 MB`
- load added about `1.2 GB`
- after one OCR inference: `4228 MB`
- peak over baseline was about `3.0 GB`
- after process exit: back to about `1279 MB`

Timing from unloaded state:

- Python/PyTorch/vision module import: about `5.6 sec`
- Falcon-OCR model load itself: about `2.7 sec`
- first OCR inference: about `17.0 sec`
- full cold path: about `25.7 sec`

Practical reading:

- If Vision service is already running and only Falcon-OCR is unloaded, first
  OCR should feel like about `20 sec`.
- If the whole process is cold, first OCR should feel like about `25-30 sec`.
- Repeated OCR while the model stays loaded should be faster than the cold path.

## Product Direction

?뺥썕 wants Evelyn to feel present even when he is not talking much.

The right first target is:

- periodic screen awareness
- light reactions to meaningful changes
- cautious silence most of the time
- tool use before claims

The wrong target is:

- full OCR all the time
- detailed screen interpretation every few seconds
- claiming certainty from weak vision output
- keeping screen data indefinitely

## Design Decisions

### 1. Use a Lightweight Full-Stack Profile First

The next full-stack validation should not load every heavy model at startup.

Initial safe profile:

```bat
set VISION_LOAD_OCR=false
set VISION_WATCH_RUN_OCR=false
```

Recommended additional constraints:

```bat
set VISION_WATCH_ENABLED=true
set VISION_WATCH_INTERVAL_SEC=25
set VISION_WATCH_MAX_IMAGE_DIM=1280
set VISION_WATCH_THUMBNAIL_SIZE=384
```

The first profile should validate:

- Kanana main LLM
- control page
- local speaker TTS
- local mic capture state
- SmolVLM2-only screen watch
- no Falcon-OCR at startup

### 2. OCR Should Be Lazy-Loaded

Falcon-OCR should not be loaded at Vision service startup by default.

Target behavior:

1. Start Vision service with SmolVLM2 only.
2. If a user asks for text reading, load Falcon-OCR on demand.
3. Run OCR.
4. Pass OCR text to Main LLM as one-turn context.
5. Delete the screenshot if retention is not needed.
6. If Falcon-OCR is idle for a configured interval, unload it.

Suggested env knobs:

```bat
set VISION_LOAD_OCR=false
set VISION_OCR_LAZY_LOAD=true
set VISION_OCR_IDLE_UNLOAD_SEC=600
set VISION_OCR_UNLOAD_AFTER_REQUEST=false
```

`VISION_OCR_UNLOAD_AFTER_REQUEST=true` can be used for the safest VRAM mode,
but a 5-10 minute idle timeout is more ergonomic when the user asks for several
OCR reads in a row.

### 3. Screen Watch Should Be SmolVLM2-Only By Default

Automatic screen watch should be cheap and cautious.

Loop:

1. Capture primary display, preferably not all virtual screens.
2. Downscale analysis image to about `1280x720`.
3. Compare thumbnail diff first.
4. If the frame is black, mark it and skip vision analysis.
5. If the frame did not meaningfully change, stay silent.
6. If it changed, use SmolVLM2 to produce a short scene summary.
7. Store only the latest state, not a long history.
8. Ask the self-model/autonomy gate whether to speak.

Automatic OCR remains off:

```bat
set VISION_WATCH_RUN_OCR=false
```

### 4. Evelyn Should React Lightly, Not Constantly

Periodic screen awareness should feed an autonomy gate, not direct speech.

Speak gate inputs:

- last assistant speech time
- current TTS activity
- local mic / user speaking state
- quiet hours
- screen change score
- vision confidence / scene reliability
- repeated scene detection
- runtime health
- user idle time

Recommended initial limits:

- maximum 1 proactive screen comment per 10 minutes
- maximum 2 proactive comments per hour
- no proactive speech while TTS is active
- no proactive speech while local mic is actively capturing
- no proactive speech for low-confidence or repeated scenes

Allowed proactive shapes:

- "?붾㈃??醫 諛붾?寃?媛숈븘. 吏湲??ㅼ젙 履?留뚯???以묒씠??"
- "吏湲덉? 以鍮??붾㈃泥섎읆 蹂댁뿬. ?닿? 議곗슜??蹂닿퀬 ?덉쓣寃?"
- "湲?먮뒗 ?뺥솗???쎌쑝?ㅻ㈃ OCR??耳쒖빞 ??"

Disallowed shapes:

- "?닿? ?뺤씤?덉뼱" when no reliable tool result exists
- "踰꾪듉 ?뚮??? or fake PC actions
- confident screen/game-state claims from the user's screen when bot state was
  not actually observed

### 5. Screen Data Should Be Ephemeral

Screen capture and OCR output are sensitive. The default should be short-lived.

Cleanup targets:

- `C:\Evelyn\runtime_artifacts\vision\*.png`
- `C:\Evelyn\runtime_artifacts\vision_watch\*.jpg`
- `C:\Evelyn\runtime_artifacts\vision_watch\*_thumb.jpg`
- one-turn `Vision Context` strings
- OCR text after the LLM turn completes

Rules:

- Pass only summarized scene/OCR text to the Main LLM.
- Do not write raw OCR text to long-term memory by default.
- Exclude `Vision Context` from memory write-behind unless explicitly marked.
- Delete request screenshots after use when `VISION_DELETE_REQUEST_IMAGES=true`.
- Keep watch images only long enough for diagnostics.

Suggested env knobs:

```bat
set VISION_DELETE_REQUEST_IMAGES=true
set VISION_CONTEXT_ONE_TURN_ONLY=true
set VISION_MEMORY_WRITE_ENABLED=false
set VISION_WATCH_MAX_FILES=20
set VISION_WATCH_RETENTION_MINUTES=30
```

### 6. Tool Use Should Be Policy-Driven

Kanana should not be expected to perform perfect native function calling by
itself. Evelyn runtime should decide when a tool is required, call safe tools,
and give the result to the Main LLM as compact context.

Flow:

```text
user text / screen event
-> tool-use policy layer
-> safe tools auto-called when needed
-> risky or expensive tools gated
-> Tool Context added to prompt
-> Kanana answers in Evelyn style
```

Tool classes:

- Runtime status: ports, health, GPU/VRAM, TTS, mic, Vision.
- Screen/Vision: latest watch state, SmolVLM2 describe, Falcon-OCR lazy-load.
- Memory: user/project decisions and preferences.
- Files/tests/logs: only when working on code or diagnostics.
- Current information/web: only when the user asks for current/latest facts.
- External or destructive actions: require permission.

Tool-use rules:

- If the user asks about current runtime state, inspect runtime state first.
- If the user asks "can you see my screen?", use Vision or say the screen was
  not checked.
- If the user asks to read text, use OCR or say OCR is not loaded/available.
- If the user asks about logs/files/tests, inspect them before answering.
- If a tool fails, say exactly what failed instead of pretending success.
- Never claim a PC action was done unless the tool actually did it.

## Implementation Plan

### Phase 0 - Stabilize Safe Launch Profile

Goal: run Kanana full local mode without OOM.

Implementation note:

- `start_local.bat --lightweight` selects the lightweight local profile.
- It sets `VISION_LOAD_OCR=false`, `VISION_WATCH_RUN_OCR=false`, and
  `VISION_OCR_LAZY_LOAD=true` when those values were not already provided by
  the caller.
- The lightweight profile variables are applied before `start_env.bat` fills
  default values, so `VISION_LOAD_OCR=true` from the default full profile does
  not override lightweight mode.

Tasks:

1. Add a lightweight local profile or env preset:
   - `VISION_LOAD_OCR=false`
   - `VISION_WATCH_RUN_OCR=false`
   - keep SmolVLM2 watch enabled
2. Confirm full stack reaches `100%`.
3. Confirm RTX 3090 headroom after ready state.
4. Confirm control page chat uses Kanana and local speaker.
5. Confirm local mic remains `captureReady=true`.
6. Confirm vision watch has `capture_black=false`.

Validation:

```powershell
Invoke-WebRequest http://127.0.0.1:8799/api/control-page/state
Invoke-WebRequest http://127.0.0.1:8891/health
nvidia-smi --query-gpu=index,name,memory.used,memory.free,memory.total --format=csv,noheader
```

Expected:

- `mainModel=kanana-1.5-8b-instruct-2505-q4_k_m+evelyn-lora-v1`
- `Vision ocr.loaded=false`
- no OOM
- no black capture in foreground mode

Validation result on `2026-06-06`:

- `C:\Evelyn\start_local.bat --foreground --lightweight` started in a visible
  CMD window.
- Ports `8799`, `8880`, `8891`, and `9820` listened.
- Control page boot reached `100%`, phase `?꾩껜 ?쒕쾭 以鍮??꾨즺`.
- Control state reported
  `mainModel=kanana-1.5-8b-instruct-2505-q4_k_m+evelyn-lora-v1`.
- Local mic capture reached `captureReady=true`.
- Vision watch updated with `capture_black=false` and `run_ocr=false`.
- Vision `/health` reported `ocr.loaded=false` and `ocr.lazyLoad=true`.
- RTX 3090 stayed below OOM range; observed full-stack used memory was about
  `17.5 GB / 24.6 GB` while Vision itself reported about `2.8 GB` used.

### Phase 1 - Falcon-OCR Lazy Load

Goal: avoid loading Falcon-OCR at startup while keeping OCR available on demand.

Status: implemented in code on `2026-06-06`.

Code area:

- `C:\Evelyn\evelyn_core\runtime\evelyn_core\vision_service.py`
- `C:\Evelyn\evelyn_core\runtime\launchers\start_vision.ps1`
- `C:\Evelyn\evelyn_core\start_env.bat`

Tasks:

1. Add lazy OCR state:
   - `_ocr_model`
   - `_ocr_loaded_at`
   - `_ocr_last_used_at`
   - `_ocr_lock`
2. Add `ensure_ocr_loaded()`.
3. Add `unload_ocr()`.
4. Add idle unload background task or request-time idle check.
5. Make `/v1/vision/ocr` and `/v1/vision/analyze(run_ocr=true)` call
   `ensure_ocr_loaded()` if `VISION_OCR_LAZY_LOAD=true`.
6. Add health fields:
   - `ocr.loaded`
   - `ocr.lazyLoad`
   - `ocr.lastUsedAt`
   - `ocr.idleUnloadSec`
7. Use `gc.collect()` and `torch.cuda.empty_cache()` when unloading.

Expected UX:

- First OCR after unload may take about `20 sec`.
- Later OCR requests during the idle window should be faster.
- VRAM returns after idle unload.

Implementation notes:

- `VISION_OCR_LAZY_LOAD=true` lets `/v1/vision/ocr` and
  `/v1/vision/analyze` with `run_ocr=true` load Falcon-OCR on demand.
- `VISION_OCR_IDLE_UNLOAD_SEC=600` is the default idle unload window.
- `VISION_OCR_UNLOAD_AFTER_REQUEST=true` is available for the most
  aggressive VRAM-saving mode.
- `/health` now reports `ocr.lazyLoad`, `ocr.loadedAt`, `ocr.lastUsedAt`,
  `ocr.idleUnloadSec`, and `ocr.unloadAfterRequest`.
- The WSL Vision launcher now forwards the lazy OCR environment variables
  into the bash process.

Verification:

```powershell
py -3 -m py_compile `
  C:\Evelyn\evelyn_core\runtime\evelyn_core\vision_service.py `
  C:\Evelyn\tests\test_vision_service_lazy_ocr.py

py -3 -m unittest tests.test_vision_service_lazy_ocr tests.test_local_mic_routing
```

Result: both checks passed.

### Phase 2 - Ephemeral Vision Context and Cleanup

Goal: screen data does not stick around unnecessarily.

Status: implemented in code on `2026-06-06`.

Code areas:

- `C:\Evelyn\main.py`
- `C:\Evelyn\evelyn_core\runtime\evelyn_core\vision_watch.py`
- memory write-behind / memory context code paths

Tasks:

1. Mark Vision Context as one-turn context.
2. Ensure memory writer does not store raw OCR or scene text by default.
3. Add screenshot deletion after user-requested vision analysis.
4. Keep watch cleanup bounded by count and age.
5. Add control page state fields for last cleanup and retained image count.

Suggested config:

```bat
set VISION_DELETE_REQUEST_IMAGES=true
set VISION_CONTEXT_ONE_TURN_ONLY=true
set VISION_MEMORY_WRITE_ENABLED=false
```

Implemented config:

```bat
set VISION_WATCH_KEEP_FILES=48
set VISION_WATCH_MAX_FILE_AGE_SEC=1800
set VISION_CONTEXT_SCENE_TTL_SEC=600
set VISION_CONTEXT_OCR_TTL_SEC=180
set VISION_DELETE_REQUEST_IMAGES=true
set VISION_MEMORY_WRITE_ENABLED=false
```

Implementation notes:

- User-requested live screen captures under `runtime_artifacts\vision` are
  deleted after Vision analysis by default.
- Watch images under `runtime_artifacts\vision_watch` are bounded by both file
  count and age.
- Watch state now carries cleanup metadata:
  - `cleanup.lastCleanupAt`
  - `cleanup.deletedFiles`
  - `cleanup.retainedFiles`
  - `cleanup.keepFiles`
  - `cleanup.maxFileAgeSec`
- Scene and OCR text are rendered into prompt context only while their TTLs are
  valid.
- When the screen changes, stale scene/OCR text is cleared until the new frame
  is analyzed.
- Memory write-behind redacts labeled Vision/OCR context by default unless
  `VISION_MEMORY_WRITE_ENABLED=true`.

Verification:

```powershell
py -3 -m py_compile `
  C:\Evelyn\main.py `
  C:\Evelyn\evelyn_core\runtime\evelyn_core\vision_watch.py `
  C:\Evelyn\tests\test_vision_context_cleanup.py

py -3 -m unittest `
  tests.test_vision_context_cleanup `
  tests.test_vision_service_lazy_ocr `
  tests.test_local_mic_routing
```

Result: both checks passed.

### Phase 3 - Proactive Screen Awareness

Goal: Evelyn can react to the screen occasionally without being annoying.

Status: implemented in code on `2026-06-06`.

Code areas:

- `C:\Evelyn\evelyn_core\runtime\evelyn_core\vision_watch.py`
- `C:\Evelyn\evelyn_core\runtime\evelyn_core\self_model.py`
- `C:\Evelyn\evelyn_core\runtime\evelyn_core\autonomy.py`
- `C:\Evelyn\main.py`

Tasks:

1. Feed screen watch events into the self-model drive state.
2. Add screen-change impulse types:
   - `stay_silent`
   - `comment_on_screen_change`
   - `ask_light_question`
   - `suggest_next_step`
3. Gate speech by cooldown and recent TTS/mic activity.
4. Use confidence-aware language.
5. Store last few scene fingerprints to avoid repeated comments.

Validation:

- No speech for unchanged screens.
- No speech while local TTS is active.
- No speech if vision is black/unreliable.
- A clear scene change can produce one short comment after cooldown.

Implementation notes:

- Vision watch now produces `image_fingerprint` and `scene_fingerprint`.
- Autonomy observation passes:
  - `vision_change_recent`
  - `vision_unreliable`
  - `vision_fingerprint`
  - `local_tts_active`
  - `local_mic_recent`
- Self-model gates proactive screen impulses when:
  - TTS is active
  - local mic input was recent
  - screen capture/analysis is unreliable
  - the same screen fingerprint was already reacted to recently
  - normal proactive cooldown/hourly limit applies
- Self-model stores pending/last vision fingerprints so repeated comments are
  suppressed.
- Autonomy `ping` plans now use the self-model impulse text metadata instead
  of dropping back to a generic ping message.

Verification:

```powershell
py -3 -m py_compile `
  C:\Evelyn\main.py `
  C:\Evelyn\evelyn_core\runtime\evelyn_core\vision_watch.py `
  C:\Evelyn\evelyn_core\runtime\evelyn_core\self_model.py `
  C:\Evelyn\evelyn_core\runtime\evelyn_core\autonomy.py `
  C:\Evelyn\tests\test_self_model_vision_awareness.py

py -3 -m unittest `
  tests.test_self_model_vision_awareness `
  tests.test_vision_context_cleanup `
  tests.test_vision_service_lazy_ocr `
  tests.test_local_mic_routing
```

Result: both checks passed.

### Phase 4 - Tool-Use Policy Layer

Goal: Evelyn calls tools when a claim needs evidence.

Code areas:

- existing route/context policy code
- `main.py` direct LLM paths
- skills registry / tool execution helpers

Tasks:

1. Add a `ToolUseDecision` structure:
   - `tool_name`
   - `reason`
   - `risk`
   - `cost`
   - `auto_allowed`
   - `required_before_answer`
2. Add policy rules for:
   - runtime status questions
   - screen questions
   - OCR/text-reading questions
   - memory questions
   - file/log/test questions
   - current/latest info questions
3. Auto-call low-risk local read tools.
4. Gate expensive/risky tools by cost, cooldown, or user permission.
5. Add `Tool Context` to Main LLM messages.
6. Teach answer shaping to mention failed/missing tool evidence.

Initial policy examples:

```text
"?붾㈃ 蹂댁뿬?" -> vision latest/capture required
"湲???쎌뼱以? -> OCR required, lazy-load allowed
"吏湲??곹깭 ?대븣?" -> runtime status required
"?꾧퉴 萸??뺥뻽吏?" -> memory search/get required
"濡쒓렇 遊먯쨾" -> file/log read required
"理쒖떊 ?뺣낫" -> web/current info required
```

Implemented status:

- Added `ToolUseDecision` and `render_tool_use_context()` in `context_pipeline.py`.
- Added a `Tool Use Policy` section to `ContextPacket` / `ContextBuilder`.
- Added policy rules for runtime status, screen capture/watch, OCR, memory recall,
  local file/log read, and current external info.
- `prepare_llm_messages()` now builds tool-use decisions after context policy
  selection, forces live runtime status when a runtime-status answer needs
  evidence, promotes screen/OCR requests into `needs_vision`, records memory
  and vision execution status, and injects tool evidence/failure guidance into
  the Main LLM context.
- Current external-info and local-file/log reads are intentionally marked as
  not auto-called by this runtime path unless a concrete tool result exists.
- Runtime VRAM/OOM questions now use a deterministic fast path instead of Main
  LLM interpretation:
  - `load_runtime_gpu_status()` reads `nvidia-smi`.
  - `answer_gpu_runtime_status_query()` answers `VRAM` / `OOM` / `GPU` queries
    before the LLM can be pulled toward stale OOM memory.
  - Runtime context includes `current_gpu_snapshot`, `current_oom_signal`, and
    a rule that `recent_errors` are historical unless current signals agree.
- Vision service now exposes `POST /v1/vision/ocr/unload`.
- The lightweight local profile now sets
  `VISION_OCR_UNLOAD_AFTER_REQUEST=true` by default, so user-requested OCR does
  not keep Falcon-OCR resident after a response.

Validation:

```powershell
py -3 -m py_compile `
  C:\Evelyn\main.py `
  C:\Evelyn\evelyn_core\runtime\evelyn_core\context_pipeline.py `
  C:\Evelyn\tests\test_context_pipeline_tool_policy.py

py -3 -m unittest `
  tests.test_context_pipeline_tool_policy `
  tests.test_self_model_vision_awareness `
  tests.test_vision_context_cleanup `
  tests.test_vision_service_lazy_ocr `
  tests.test_local_mic_routing

git diff --check
```

Result: compile passed, related tests passed. Later live smoke caught and fixed
a VRAM/OOM misreport; the expanded relevant test set is now 29 tests.

Live smoke status after restart:

- Boot reached `100% / ?꾩껜 ?쒕쾭 以鍮??꾨즺`.
- Main model: `kanana-1.5-8b-instruct-2505-q4_k_m+evelyn-lora-v1`.
- Local mic: `captureReady=true`.
- Vision: SmolVLM2 loaded, Falcon-OCR `loaded=false`, `lazyLoad=true`,
  `unloadAfterRequest=true`.
- VRAM/OOM fast path returned current GPU names and used/total VRAM instead of
  claiming stale OOM.
- OCR lazy-load worked, then `POST /v1/vision/ocr/unload` returned
  `unloaded=true`.
- Final clean restart baseline: RTX 3090 about `17.0GB / 24.6GB`; RTX 4060
  Laptop GPU about `6.6GB / 8.2GB`; no OOM observed.

### Phase 5 - Full Validation

Run the following only after Phase 0-2 are stable.

Checks:

1. Start lightweight full stack.
2. Confirm no OOM after 5 minutes idle.
3. Control page chat smoke.
4. Local TTS smoke.
5. Local mic status.
6. Vision watch foreground capture.
7. User-requested OCR lazy-load.
8. OCR idle unload and VRAM return.
9. Screen data cleanup.
10. Tool-use policy smoke prompts.

Example prompts:

```text
吏湲??곹깭 ?대븣?
吏湲??붾㈃ 蹂댁뿬?
?붾㈃ 湲???쎌뼱以?
?닿? 諛⑷툑 留먰븳 嫄?湲곗뼲??
濡쒓렇 ?뺤씤?섍퀬 留먰빐以?
```

Failure criteria:

- RTX 3090 exceeds safe headroom for sustained operation.
- Bot-Control `8799` becomes unresponsive.
- Vision claims screen certainty when capture failed.
- OCR text is written to memory by default.
- Evelyn claims to have used a tool without a successful tool result.

## Open Questions

- Should OCR unload immediately after each request, or after an idle timeout?
  Current recommendation: idle timeout `5-10 min`.
- Should STT/Qwen ASR also become lazy-loaded for local-only mode?
  This may be needed if Kanana + TTS + SmolVLM2 still leaves too little headroom.
- Should SmolVLM2 run on RTX 3090, RTX 4060, or CPU in the lightweight profile?
  Measure before deciding.
- Should automatic screen reactions be spoken through TTS, shown in the control
  page only, or both?
  Current recommendation: start with control page + very limited TTS.

## Next Concrete Step

Restart the lightweight local stack once to load Phase 3 and Phase 4 code,
then run Phase 5 smoke validation:

1. Start the lightweight local launch profile:

   ```bat
   C:\Evelyn\start_local.bat --foreground --lightweight
   ```

2. Verify Phase 3 proactive screen-awareness behavior with a low-frequency
   visible screen change.
3. Verify Phase 4 tool-use policy prompts:
   - runtime status asks force runtime status evidence,
   - screen/OCR asks produce vision tool context,
   - current external info does not pretend web verification happened,
   - local file/log asks do not claim local evidence without a tool result.
4. Verify OCR lazy-load/unload still avoids OOM on the RTX 3090.

Do not re-run the full all-model stack as-is. It has already hit the RTX 3090
VRAM ceiling.

---

## Source: LOCAL_ONLY_AND_LLM_TOPOLOGY_EXECUTION_BLUEPRINT.md

# Evelyn Local-Only And LLM Topology Execution Blueprint

?묒꽦 湲곗?: 2026-06-02

## 紐⑹쟻

Evelyn??Discord??怨좎젙??遊뉗뿉??濡쒖뺄 ?ㅽ뻾 媛?ν븳 assistant runtime?쇰줈 遺꾨━?섍퀬, LLM topology 蹂寃쎌쓣 媛먯쑝濡??먮떒?섏? ?딅룄濡?turn ?⑥쐞 model-call trace瑜??④릿??

??臾몄꽌????踰덉뿉 ?洹쒕え 遺꾨━瑜??앸궡湲??꾪븳 臾몄꽌媛 ?꾨땲?? ?꾩옱 `main.py`? ?대? 遺꾨━??`discord_*`, `voice_*`, `turn_trace` 紐⑤뱢??湲곗??쇰줈 ?덉쟾?섍쾶 吏꾪뻾???쒖꽌瑜??뺤쓽?쒕떎.

## ?꾩옱 ?먮떒

- ?꾩쟾 ?⑥씪 LLM ?꾪솚? ?꾩쭅 ?대Ⅴ??
- 議곌굔遺 硫??LLM? ?좎??섎릺, 紐낇솗???쇰컲 ??붿쓽 hot path??Main LLM 以묒떖?쇰줈 吏㏐쾶 ?좎??쒕떎.
- Router, Summary/Sub, Cognitive blocking? ?ㅼ젣 ?몄텧瑜좉낵 吏?곗쓣 癒쇱? 怨꾩륫?????쒗븳?쒕떎.
- Discord??湲곕뒫?쇰줈???좎??섎릺, core runtime???꾩닔 遺??議곌굔?먯꽌 ?쒓굅?쒕떎.

## 1李??꾨즺 踰붿쐞

### Local-only boot

- `DISCORD_ENABLED=false`?대㈃ Discord token 寃?ъ? `bot.run()`??嫄대꼫?대떎.
- control page??Discord guild ?놁씠 `control-page:local` ?몄뀡?쇰줈 ?숈옉?쒕떎.
- 濡쒖뺄 ?몄뀡? `guild_id=0`???ъ슜??湲곗〈 Discord guild memory? ?욎씠吏 ?딄쾶 ?쒕떎.
- `start_local.bat`??Main/Router/Sub/TTS瑜??꾩슫 ??local-only `main.py`瑜??ㅽ뻾?쒕떎.
- local mic???꾩쭅 Discord user target??臾띠뿬 ?덉쑝誘濡?1李⑥뿉?쒕뒗 湲곕낯 鍮꾪솢?깊솕?쒕떎.

### Model call trace

紐⑤뱺 LLM ??븷 ?몄텧? `model_call` ?대깽?몃줈 蹂꾨룄 JSONL trace瑜??④릿??

?꾩닔 ?꾨뱶:

- `model_role`: `main`, `router`, `summary`
- `purpose`: `main_response`, `route`, `cognitive`, `memory_summary`
- `hot_path`: ?ъ슜???묐떟 ??blocking ?щ?
- `success`: ?깃났 ?щ?
- `latency_ms`: ?몄텧 ?꾩껜 吏??- `first_token_ms`: Main streaming first token 吏?? ?대떦 ?쒖뿉留?- `turn_id`, `session_key`, `source`, `guild_id`
- `error`: ?ㅽ뙣 ??吏㏃? ?ㅻ쪟

???대깽?몃뒗 summary payload???쇱썙 ?ｊ린蹂대떎 ?먯떆 event濡??④릿?? 洹몃옒??怨쇨굅泥섎읆 p95 summary留??④퀬 ?몄텧瑜??됯퇏 吏?곗쓣 蹂듭썝?섏? 紐삵븯??臾몄젣瑜??쇳븳??

?ъ떆???꾩뿉??吏?쒓? 諛붾줈 鍮꾩뼱 蹂댁씠吏 ?딅룄濡?control page state瑜?留뚮뱾 ??理쒓렐 `logs/turn_trace/*.jsonl`?먯꽌 `model_call` ?대깽?몃? ??踰?replay?쒕떎. ?덉쟾 trace泥섎읆 `model_call`???녿뒗 ?뚯씪? denominator濡??곗? ?딅뒗??

### Control page runtime metrics

`/api/control-page/state`??`runtime.modelCallMetrics`??rolling 吏묎퀎瑜??몄텧?쒕떎.

- `routerRouteCallRate`: completed turn summary ?鍮?Router route ?몄텧瑜?- `routerAvgLatencyMs`, `routerP95LatencyMs`: Router route ?됯퇏/p95 吏??- `mainFirstTokenAvgMs`, `mainFirstTokenP95Ms`: Main streaming first token ?됯퇏/p95
- `summaryHotPathRate`: Summary/Sub ?몄텧 以?hot path 鍮꾩쑉
- `cognitiveBlockingRate`: completed turn summary ?鍮?cognitive blocking 鍮꾩쑉
- `byPurpose`: `modelRole + purpose + hotPath` ?⑥쐞???몃? count/latency

## 吏꾪뻾 ?쒖꽌

1. local-only boot path 異붽? - ?꾨즺
2. control page local chat path 異붽? - ?꾨즺
3. `model_call` trace helper 異붽? - ?꾨즺
4. Router LLM route/cognitive ?몄텧??purpose 援щ텇 異붽? - ?꾨즺
5. Summary/Sub write-behind ?몄텧??purpose 援щ텇 異붽? - ?꾨즺
6. Main LLM streaming ?몄텧??request latency? first token latency 湲곕줉 - ?꾨즺
7. control page runtime payload??rolling model-call 吏묎퀎 ?몄텧 - ?꾨즺
8. control page UI??model-call metric grid 異붽? - ?꾨즺
9. 理쒓렐 turn trace JSONL??`model_call` replay 吏묎퀎 異붽? - ?꾨즺
10. control page local chat?먮룄 `text_turn_summary` denominator 異붽? - ?꾨즺
11. tests/py_compile濡?regression ?뺤씤 - ?꾨즺
12. ?ㅼ젣 濡쒖뺄紐⑤뱶濡?紐????ㅽ뻾??Router call rate, avg/p95 latency, Summary hot-path rate, Cognitive blocking rate 寃利?- 遺遺??꾨즺
    - local-only boot, control API, local chat, `model_call`, `text_turn_summary` ??μ? ?뺤씤??
    - 寃利?以?pre-summary `model_call` 1嫄댁씠 ?⑥븘 ?덉뼱 `modelCallCount`媛 `turnSummaryCount`蹂대떎 1 ?ш쾶 蹂댁씪 ???덉쓬.
    - Router/Summary/Cognitive 吏?쒕뒗 ?꾩쭅 ?대떦 ?몄텧??諛쒖깮?섏? ?딆븘 null/0 ?곹깭媛 ?뺤긽??
13. control page Router 議곌굔 1李?議곗젙 - ?꾨즺
    - control page chat? `source=control_page`濡?trace???④릿??
    - ?쇰컲 濡쒖뺄 梨꾪똿? ???볦? 湲몄씠 踰붿쐞?먯꽌 Main 吏곹뻾 fast path瑜??꾨떎.
    - `寃???놁씠`, `李얠? 留먭퀬`, `without search` 媛숈? 遺?뺥삎 寃???쒗쁽? search/deep route trigger?먯꽌 ?쒖쇅?쒕떎.
    - 寃利?寃곌낵 媛숈? 遺?뺥삎 寃??臾몄옣?먯꽌 `routerRouteCallCount`???좎??섍퀬 `mainResponseCallCount`留?利앷??덈떎.

## ?꾩쭅 ?섏? ?딅뒗 寃?
- Discord dependency ?쒓굅
- runtime 以?Discord start/stop ?좉?
- local mic???꾩쟾 濡쒖뺄 STT/TTS playback route
- ??`ModelCallPolicy` ???異붿긽???꾩엯
- Router ?몄텧 議곌굔 ???蹂寃?- model-call metric 李⑦듃??
????ぉ?ㅼ? trace媛 ?볦씤 ??蹂꾨룄 ?④퀎濡?吏꾪뻾?쒕떎.

## 寃利?湲곗?

- `py_compile main.py` ?듦낵
- turn trace??`model_call` ?대깽?멸? ?⑤뒗??
- Router route ?몄텧怨?cognitive ?몄텧??`purpose`濡?援щ텇?쒕떎.
- Summary/Sub write-behind??`hot_path=false`濡?湲곕줉?쒕떎.
- local-only mode?먯꽌 Discord token ?놁씠 control page媛 ?щ떎.
- 湲곗〈 Discord mode?먯꽌??`DISCORD_ENABLED=true`????湲곗〈 `bot.run()` 寃쎈줈媛 ?좎??쒕떎.
- control page local chat??`model_call`怨?`text_turn_summary`瑜??④퍡 ?④꺼 rate denominator媛 ?앷릿??

## 由ъ뒪?ъ? ???
- `main.py`媛 ?대? ?щ윭 吏꾪뻾 以?蹂寃쎌쓣 ?ы븿?쒕떎.
  - ??? 湲곗〈 蹂寃쎌쓣 ?섎룎由ъ? ?딄퀬, 醫곸? helper? call-site留?異붽??쒕떎.
- `local mic`媛 Discord target??臾띠뿬 ?덈떎.
  - ??? 1李?local mode?먯꽌??`LOCAL_MIC_ENABLED=false`濡??쒖옉?쒕떎.
- Main LLM streaming first token? ?щ윭 fallback 寃쎈줈媛 ?덈떎.
  - ??? first chunk ?대깽?몄? final model_call ?대깽?몃? 紐⑤몢 ?④릿??
- Summary/Sub hot path ?щ????몄텧 ?⑥닔留뚯쑝濡??먮떒?섍린 ?대졄??
  - ??? ?몄텧?섎뒗 履쎌뿉??`hot_path`瑜?紐낆떆?곸쑝濡??섍릿??

---

## Source: THIN_QUESTION_FEATURE_EXECUTION_NOTE.md

# Evelyn Thin Question Feature Execution Note

?묒꽦 湲곗?: 2026-06-02

## 紐⑹쟻

吏덈Ц 湲곕뒫??蹂꾨룄 ?곸떆 LLM?대굹 ????뺤콉 媛앹껜濡?留뚮뱾吏 ?딄퀬, 湲곗〈 ?묐떟 hot path???뉕쾶 遺숈씤??

## 1李??곸슜 踰붿쐞

- `RouteDecision`??吏덈Ц ?쒖뼱 ?꾨뱶 異붽?
  - `ask_mode`
  - `max_question_count`
  - `question_hint`
  - `question_reason`
  - `question_source`
- Router媛 ?대? ?몄텧?섎뒗 turn?먯꽌留?吏덈Ц ?꾨뱶瑜?媛숈씠 諛쏆쓣 ???덈룄濡?Router schema ?뺤옣
- Router媛 ?앸왂?섎뒗 fast-path turn? cheap rule濡?吏덈Ц ?덉슜 ?щ?瑜??먮떒
- Main LLM prompt??`[QUESTION_HINT]` 釉붾줉 異붽?
- 理쒖쥌 ?듬??먯꽌 `?`/`竊? 湲곗? 吏덈Ц 臾몄옣??0~1媛쒕줈 ?쒗븳
- `question_trace` turn event 湲곕줉
- control page state??`runtime.questionMetrics` 異붽?
- control page diagnostics UI??吏덈Ц metric grid 異붽?

## ?덉쟾 湲곗?

- 吏덈Ц 湲곕뒫 ?뚮Ц??Router ?몄텧瑜좎쓣 ?섎━吏 ?딅뒗??
- 湲곕낯媛믪? `ask_mode=none`, `max_question_count=0`?대떎.
- ?ъ슜?먭? 吏곸젒 ?듬?, ?꾨즺 蹂닿퀬, ?щ?留???? 吏㏃? ?듬????붽뎄?섎㈃ 吏덈Ц??湲덉??쒕떎.
- 吏덈Ц? 理쒕? 1媛쒕쭔 ?덉슜?쒕떎.
- cooldown 湲곕낯媛?
  - `QUESTION_MIN_TURN_GAP=3`
  - `QUESTION_MIN_SECONDS_GAP=60`
  - `QUESTION_MAX_PER_10_TURNS=3`
  - `QUESTION_DISABLE_AFTER_FRUSTRATION_SEC=300`
- ?꾩껜 湲곕뒫 off ?ㅼ쐞移?
  - `QUESTION_FEATURE_ENABLED=false`

## ?꾩쭅 ?섏? ?딅뒗 寃?
- `QuestionQueue`
- `ProactiveQuestionEngine`
- 吏덈Ц ?듬???`preference_candidate` memory write-behind ?곌껐
- 吏덈Ц ?덉쭏 湲곕컲 durable fact ?밴꺽
- TTS streaming chunk ?⑥쐞???ㅼ떆媛?吏덈Ц ?쒓굅

????ぉ? 吏덈Ц trace? control page metric??硫곗튌 蹂???蹂꾨룄 ?④퀎濡??먮떒?쒕떎.

## 寃利?
- `py -3 -m py_compile C:\Evelyn\main.py C:\Evelyn\evelyn_core\runtime\evelyn_core\voice_pipeline.py`
- `node --check C:\Evelyn\docs\assets\evelyn-page.js`
- `py -3 -m unittest tests.test_route_policy tests.test_turn_trace_summary tests.test_query_intents`
- `py -3 -m unittest tests.test_voice_turn_orchestrator tests.test_turn_budget`

紐⑤몢 ?듦낵.

## Live verification

2026-06-02 local-only runtime?먯꽌 control page chat?쇰줈 ?뺤씤?덈떎.

- Direct-answer turn
  - `ask_mode=none`
  - `question_added=false`
  - `question_reason=direct_answer_requested`
- Technical follow-up turn
  - `ask_mode=topic_continue`
  - `question_added=true`
  - final question count 1
- Immediate next technical turn
  - cooldown hit
  - `ask_mode=none`
  - `question_reason=question_cooldown`
- Forced multi-question removal
  - input asked the model to output three question sentences
  - first run exposed a bug: `question_removed=true` was counted, but all-question removal fell back to the original answer
  - fixed by returning a safe non-question fallback when every sentence is removed
  - retest result: final reply `?? ?뚭쿋??`, `question_removed=1`, final question count 0
- Off switch
  - restarted with `QUESTION_FEATURE_ENABLED=false`
  - technical follow-up input did not add a question
  - final question count 0
- Control page payload/UI assets
  - `/api/control-page/state` exposes `runtime.questionMetrics`
  - served HTML contains `question-added-rate`, `question-removed-count`, `question-ask-mode`
  - served JS contains `runtime.questionMetrics`, `topAskMode`, `questionAddedRate`

2026-06-03 follow-up verification:

- Local-only runtime started from `start_local.bat` on the current `structural-change` checkout.
- Control page API reached ready state with boot progress `100`.
- Control page local chat still records model-call metrics: Router route rate, Router latency, Main response count, and cognitive blocking rate were populated.
- A forced multi-question live prompt returned a final reply with `finalQuestionCount=0`.
- Browser-rendered screenshot was captured at `runtime_artifacts/control_page/evelyn-local-20260603-visual-2.png`; the control page left the boot splash and rendered Avatar/Chat panels without obvious overlap at `1440x1200`.
- `tests/test_question_shaping.py` now locks that streamed TTS chunks pass through question filtering before `on_sentence(chunk)`.

Remaining verification gap:

- live Discord voice/TTS listening pass for streamed chunk behavior, because the code path and regression test are covered but a real voice-channel playback turn was not run in this follow-up.

---

## Source: VOICE_PIPELINE_REFACTOR_PLAN.md

# Voice Pipeline Refactor Plan

## Goal

Refactor Evelyn's voice pipeline into a chain-shaped architecture with narrow interfaces between stages.

Design rule:
- each stage should have one primary responsibility
- each stage should know only its direct next stage
- cross-cutting concerns must not leak sideways across the pipeline
- search, routing, TTS, and playback should not be tightly coupled

---

## Problems in Current Structure

Current issues observed during debugging:

1. Voice input, STT, routing, search follow-up, TTS, playback, interrupt logic, and turn tracing are too intertwined.
2. Shared state leaks across layers (`turn_scope`, reply state, partial STT state, playback state, search follow-up state).
3. Playback bugs can surface as search/LLM failures because delivery is a shared downstream path.
4. Turn cancellation is too broad and can kill unrelated downstream work.
5. TTS interrupt decisions are made before the system cleanly separates input suppression, intent handling, and delivery.
6. Logging/trace payload handling has been able to break runtime behavior, which means observability is not isolated enough.

---

## Target Architecture

The target architecture is a linear chain:

```text
Audio Input
-> Audio Segment Filter
-> STT
-> Intent Router
-> Action Executor
-> Answer Composer
-> Delivery Builder
-> Delivery Runtime
```

### Hybrid Local Mic + Discord Input

For mixed-source rooms, the input stage should branch before segmentation:

```text
Local Mic (preferred speaker only) ----\\
                                        -> Source Router -> Audio Segment Filter -> STT -> ...
Discord Voice (all other speakers) ----/
```

Rules:
- exactly one configured Discord user id can be treated as the local-mic speaker
- that user's Discord voice packets must be dropped at ingress to prevent duplicate STT
- all other Discord speakers continue through the existing Discord receive path
- downstream stages must still see the same logical Discord speaker id so room ownership, wake handling, memory, and command authorization stay coherent

Implementation shape:
- keep source selection in a narrow helper/module instead of scattering id checks across `main.py`
- keep local mic capture optional and fail-closed: if local mic capture does not start, Discord audio for that user must not be dropped
- feed local mic utterances into the same `process_member_audio` pipeline using the resolved Discord member identity

### Stability Guardrails Before Full Refactor

Before the full chain extraction, the current monolithic pipeline should still obey these safety rules:

- voice ingress is bounded; if input arrives faster than STT/LLM/TTS can consume it, stale audio is dropped instead of processed late
- every queued voice segment carries `turn_id`, `segment_id`, enqueue time, and queue wait metadata into logs and debug artifacts
- STT rescore is conditional and separately timed out so the second pass cannot double latency on short or already usable utterances
- TTS producer tasks are registered in the current turn scope and are cancelled with playback when a turn is aborted
- debug WAV/JSON stems are scoped to a single logical segment, not an entire speaker session
- restart recovery should remember the last successful voice channel and rejoin it on bot startup unless the user explicitly left
- STT inference should be single-flight with a short cooldown after timeout so timed-out worker threads do not stampede the model
- TTS chunking should avoid tiny standalone follow-up chunks and expose first-audio latency as a runtime metric
- failure logs should identify the failing layer: voice reconnect, queue ingress, STT, LLM, TTS request, or playback
- the control page should show voice queue depth, STT busy/cooldown, drop counts, and TTS first-audio p95 so instability is visible without reading logs

For voice queries needing search:

```text
Audio Input
-> Audio Segment Filter
-> STT
-> Intent Router(search_then_answer)
-> Search Executor
-> Answer Composer
-> Delivery Builder
-> Delivery Runtime
```

---

## Stage Definitions

### 1. Audio Input
Responsibility:
- receive Discord audio
- packet reorder/jitter handling
- speaker/ssrc mapping
- raw utterance segmentation

Input:
- Discord voice packets

Output:
- `VoiceSegment`

Must not know about:
- STT decisions
- routing
- LLM
- TTS
- playback policy

---

### 2. Audio Segment Filter
Responsibility:
- onset gate
- silence/noise filtering
- post-TTS suppression
- bot-speaking suppression
- decide whether a segment is worth STT

Input:
- `VoiceSegment`

Output:
- `FilteredVoiceSegment | DropReason`

Must not know about:
- LLM
- search
- TTS internals

Important rule:
- this layer decides whether a segment is processed at all
- it should be the only place for self/post-playback suppression

---

### 3. STT
Responsibility:
- wake probe
- partial transcript
- full transcript
- transcript confidence / metadata

Input:
- `FilteredVoiceSegment`

Output:
- `TranscriptResult`

Suggested shape:

```python
@dataclass
class TranscriptResult:
    wake_detected: bool
    wake_match_mode: str
    wake_alias: str | None
    probe_text: str
    confirm_text: str
    partial_text: str
    committed_text: str
    final_text: str
    speaker_user_id: int | None
    duration_sec: float
```

Must not know about:
- routing decisions
- search execution
- TTS
- playback

Important rule:
- no persistent partial/committed carryover should leak across turns unless explicitly scoped and versioned

---

### 4. Intent Router
Responsibility:
- decide what kind of action is needed
- direct answer / ask / wait / search_then_answer / ignore

Input:
- `TranscriptResult`
- conversation/session state snapshot

Output:
- `RouteDecision`

Suggested shape:

```python
@dataclass
class RouteDecision:
    action: str  # answer | ask | wait | search_then_answer | ignore
    route: str   # main_direct | search | etc.
    prompt_text: str
    user_visible_preface: str | None
    needs_search: bool
    should_interrupt_delivery: bool
```

Must not know about:
- TTS transport
- Discord playback
- Omnivoice source implementation

---

### 5. Action Executor
Responsibility:
- perform the one action selected by router
- if search is required, run search
- if direct answer is enough, call answer generation path

Input:
- `RouteDecision`

Output:
- `ActionResult`

Suggested shape:

```python
@dataclass
class ActionResult:
    action: str
    answer_text: str
    metadata: dict
```

Rules:
- search executor only returns structured search result or answer text
- it must not speak directly
- it must not touch Discord playback

---

### 6. Answer Composer
Responsibility:
- convert search result / LLM result / wait/ask decision into final user-facing text
- apply formatting policy
- split display text vs spoken text if needed

Input:
- `ActionResult`

Output:
- `AnswerPayload`

Suggested shape:

```python
@dataclass
class AnswerPayload:
    display_text: str
    spoken_text: str
    should_store_history: bool
    followup_state: dict
```

Must not know about:
- playback queue internals
- Omnivoice transport

---

### 7. Delivery Builder
Responsibility:
- convert `AnswerPayload` into delivery artifacts
- for voice: spoken_text -> TTS source plan
- for text: display_text -> message payload

Input:
- `AnswerPayload`

Output:
- `DeliveryPlan`

Suggested shape:

```python
@dataclass
class DeliveryPlan:
    text_message: str | None
    tts_chunks: list[str]
    should_play_voice: bool
```

Must not know about:
- Discord voice packet handling
- search logic
- router logic

---

### 8. Delivery Runtime
Responsibility:
- run the prepared delivery plan
- TTS source creation
- playback queueing
- Discord playback
- message send

Input:
- `DeliveryPlan`

Output:
- `DeliveryResult`

Rules:
- this is the only layer that knows Omnivoice stream and Discord playback details
- this layer should not decide user intent
- this layer should not cancel upstream logic except through explicit delivery control contracts

---

## Cross-Cutting Concerns

These must be isolated and not embedded ad hoc into business logic:

### A. Trace / Logging
- tracing must never be able to crash runtime behavior
- trace payload merge must be centralized
- logging should be side-effect-safe

### B. Cancellation
- separate `intent turn cancellation` from `delivery interruption`
- do not use a single broad cancellation scope for all stages

Recommended split:
- `IntentScope`
- `DeliveryScope`

### C. Session State
- separate state buckets:
  - transcript state
  - routing state
  - conversation state
  - delivery state
- avoid one mutable session blob affecting every stage

### D. Interrupt Policy
- interrupt decisions should live between router and delivery runtime
- input suppression and playback interruption should not be mixed into transcript generation logic

---

## Refactor Sequence

### Phase 1 - Extract stable data objects
Create explicit dataclasses/types for:
- `VoiceSegment`
- `FilteredVoiceSegment`
- `TranscriptResult`
- `RouteDecision`
- `ActionResult`
- `AnswerPayload`
- `DeliveryPlan`

Goal:
- stop passing large bundles of loosely-related mutable state between steps

### Phase 2 - Isolate transcript pipeline
Move into a focused module/service:
- wake probe
- partial/full transcript
- transcript scoring
- transcript state reset rules

Goal:
- STT can be reasoned about independently from routing and playback

### Phase 3 - Isolate router and action execution
Split:
- router/gating decisions
- search execution
- main LLM execution

Goal:
- `search_then_answer` becomes a pure action path, not an ad hoc voice special case

### Phase 4 - Isolate delivery
Split:
- answer composition
- TTS source creation
- playback runtime

Goal:
- delivery failures do not look like routing/search failures

### Phase 5 - Narrow cancellation model
Replace broad room turn cancellation with explicit stage scopes:
- interrupt intent generation separately from delivery
- avoid killing active playback unless policy explicitly says so

### Phase 6 - Remove leftover sideways dependencies
Examples to eliminate:
- search logic touching playback directly
- transcript logic touching delivery state directly
- trace payload logic embedded into source runtime in unsafe ways

---

## Immediate Refactor Guardrails

While refactoring:

1. No new stage may directly read another stage's private mutable state.
2. Each stage should consume a typed input object and return a typed output object.
3. Delivery code must not call router/search policy directly.
4. Search code must not call TTS/playback directly.
5. Trace/logging must never be allowed to crash execution.
6. State resets must happen at explicit turn boundaries.
7. Interrupt rules must be expressed as policy, not scattered conditionals.

---

## Current Known High-Risk Areas

These should be prioritized during implementation:

1. partial/committed transcript leakage across turns
2. broad `replace_room_turn_scope(... old.cancel())`
3. aggressive `should_interrupt_tts(...)`
4. voice-specific `search_then_answer` special-casing inside `ask_llm_streaming(...)`
5. delivery trace/logging inside playback source internals
6. shared mutable session state influencing unrelated stages

---

## Success Criteria

The refactor is successful when:

- a search bug cannot break TTS playback internals
- a playback bug cannot appear as an LLM/router failure
- transcript carryover cannot leak between turns without explicit design
- interrupt behavior is explainable through one policy module
- each stage can be tested in isolation
- voice flow can be diagrammed as a linear chain without sideways dependencies

---

## Working Rule

Use this file as the source of truth during refactoring.
Before structural edits, compare the planned change against this document.
If the code drifts from this design, update either the code or this document intentionally ??not implicitly.
