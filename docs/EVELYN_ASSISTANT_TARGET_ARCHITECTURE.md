# Evelyn Assistant Target Architecture

## Purpose

This document defines the end-state architecture for Evelyn as a low-latency,
tool-capable, memory-bearing voice assistant. It is not a snapshot of today's
implementation. It is the target shape that future structural changes should
converge toward.

This target is driven by five product goals:

1. low first-response latency
2. stable long-term memory without polluting the realtime path
3. safe and extensible tool execution
4. clear runtime boundaries between services
5. UI and avatar growth without coupling presentation to core logic

Related reference:

- current runtime snapshot: `CURRENT_EVELYN_ARCHITECTURE.md`

---

## Core Design Rule

Evelyn should not behave like one large monolithic pipeline.

It should behave like a layered realtime assistant system with explicit
contracts between:

- ingress
- realtime turn orchestration
- tools
- memory
- presentation

The fastest path must stay narrow.
The slowest path must stay out of the way.

---

## End-State Layer Model

```text
Audio / PTT / Wake Input
-> Ingress and Session Layer
-> Realtime Turn Layer
-> Tool Planning Layer
-> Tool Execution Layer
-> Memory Layer
-> Presentation Layer
```

The layers below are logical boundaries first. They may map to modules, local
services, or external services, but their contracts must stay stable.

---

## 1. Ingress and Session Layer

### Responsibility

Own the decision to open, continue, interrupt, or reject a voice turn.

### Inputs

- Discord voice packets
- local microphone packets
- push-to-talk events
- wake-word candidate events

### Outputs

- accepted voice segment
- rejected segment with reason
- session ownership updates
- interrupt / cancel signals

### Must own

- VAD
- pre-roll capture
- wake-word / push-to-talk gating
- room owner state
- per-speaker STT session state
- tail-fragment suppression
- quality gates before STT

### Must not own

- LLM reasoning
- memory lookup
- tool choice
- TTS execution

### End-state rule

Ingress decides "should this become a turn?" and "who owns this turn?".
It does not decide the answer.

---

## 2. Realtime Turn Layer

### Responsibility

Run the fastest possible answer loop for one accepted turn.

### Inputs

- accepted segment from ingress
- minimal runtime state needed for the turn

### Outputs

- spoken answer request
- text answer
- tool intent request
- memory writeback event
- turn trace events

### Internal shape

```text
STT
-> fast route selection
-> main response generation
-> TTS request build
-> playback delivery
```

### End-state rules

- This is the hot path.
- It must stay short and bounded.
- It may do thin recall, but not deep background work.
- It may trigger tools, but must not directly execute heavyweight tool logic.
- It must be observable by `turn_id`, `segment_id`, and `chunk_index`.

### Latency rule

If a task does not help the current turn speak sooner, it does not belong in
the hot path.

---

## 3. Tool Planning Layer

### Responsibility

Translate user intent into structured tool requests.

### Inputs

- tool-relevant turn result from the realtime layer
- compact runtime state
- policy and authorization context

### Outputs

- structured execution request
- refusal
- clarification request

### Must own

- tool selection
- argument shaping
- deterministic guard and allowlist checks
- routing to the correct executor family

### Must not own

- browser details
- desktop automation details
- smart-home protocol details
- memory persistence details

### End-state rule

Tool planning decides what should happen.
Tool execution decides how it actually happens.

---

## 4. Tool Execution Layer

### Responsibility

Execute structured actions safely through dedicated adapters.

### Executor families

- browser adapter
- desktop adapter
- smart-home adapter
- project/runtime adapter
- game/Minecraft adapter

### Inputs

- validated execution request

### Outputs

- structured result
- structured failure
- structured observation

### End-state rules

- Executors must not parse free-form user intent.
- Executors must return typed results, not conversational prose.
- Executor failures must stay local to their adapter and not poison the
  realtime turn contract.

---

## 5. Memory Layer

### Responsibility

Handle recall and writeback without bloating the hot path.

### Sub-layers

1. turn-time recall
2. post-turn writeback
3. long-term store maintenance

### Turn-time recall

Use only when it materially improves the current answer.

Rules:

- compact
- bounded
- selective
- timeout-controlled

### Post-turn writeback

Handle after the answer path is already stable.

Rules:

- asynchronous
- allowed to be slower
- may summarize, extract facts, update vector stores, or sync to Obsidian

### End-state rule

Recall and writeback must be separate contracts.
The assistant must not block first audio on heavy memory work.

---

## 6. TTS Service Boundary

### Responsibility

Expose a stable speech contract to the app core.

### App-core expectation

Evelyn core should know only a TTS contract such as:

- health
- synthesize speech
- voice/profile selection
- timing and failure reporting

### Backend freedom

The backend may be:

- current OmniVoice server
- WSL `omnivoice-triton` service
- future CosyVoice direct service
- another engine entirely

### End-state rule

The app core must depend on the TTS contract, not on a specific backend
implementation.

---

## 7. Presentation Layer

### Responsibility

Render state and responses for humans.

### Surfaces

- control page
- text chat surface
- avatar surface
- future visual dashboard

### Must own

- state visualization
- timeline display
- playback indicators
- operator controls

### Must not own

- turn orchestration
- memory mutation policy
- tool routing policy
- service health logic

### End-state rule

UI is a renderer and operator surface, not the source of business logic.

---

## 8. Runtime Service Layout

The target runtime should evolve toward explicit local services with narrow
contracts.

### Target service families

- ingress/session service
- STT service
- main response service
- TTS service
- memory service
- tool execution services
- control/status service

Not every family has to become a separate process immediately, but the code
should be modular enough that process separation becomes easy rather than
painful.

---

## 9. What Must Stay Intact During Refactor

These are Evelyn strengths and should be preserved:

- whitelist-first wake handling
- room-owner and per-speaker session separation
- strict tail-fragment rejection
- turn-based tracing with stable ids
- first PCM and playback-start timing separation
- router/main/sub role separation
- voice-first UX decisions such as visible `[질문]` text behavior
- control-page operational visibility

---

## 10. What Must Be Separated

These are the major structural debts to eliminate:

1. `main.py` orchestration concentration
2. hot path and slow path mixed together
3. app core bound too tightly to one TTS backend
4. tool planning and tool execution mixed together
5. memory recall and writeback mixed together
6. ingress policy mixed with response generation
7. runtime logic leaking into presentation

---

## 11. End-State Quality Bar

The architecture is only successful if all of the following become true:

### Realtime quality

- first response latency is dominated by the true hot path, not by memory or
  tool overhead
- TTS first-audio timing is measurable and comparable across backends
- turn cancellation is explicit and observable

### Structural quality

- each layer has one primary responsibility
- contracts are narrow and typed
- swapping a TTS backend does not require rewriting app core logic
- adding a new tool adapter does not require rewriting turn orchestration
- UI growth does not require moving logic into the UI layer

### Operational quality

- failures are attributable to a specific layer
- control/status surfaces report layer health separately
- recovery can target the failing layer instead of restarting everything

---

## 12. Migration Direction

This target should be reached in stages, not by one giant rewrite.

### Recommended order

1. split hot-path orchestration from background work
2. split ingress/session logic from response logic
3. isolate the TTS service contract from the current backend
4. separate tool planning from tool execution
5. separate memory recall from asynchronous writeback
6. reduce `main.py` into composition glue instead of owning every concern

### Explicit non-goal

Do not chase a cosmetic "microservice" layout before the contracts are clean.
Logical separation matters first; process separation follows where useful.

---

## 13. Authoritative Use

This document should be used as the target blueprint for Evelyn assistant
structural changes.

If a proposed change improves one subsystem but pushes the architecture away
from these boundaries, the change should be reconsidered or contained behind a
temporary compatibility layer.
