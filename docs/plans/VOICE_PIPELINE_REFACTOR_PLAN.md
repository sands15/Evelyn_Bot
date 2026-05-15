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
If the code drifts from this design, update either the code or this document intentionally — not implicitly.
