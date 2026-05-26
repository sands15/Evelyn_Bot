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
