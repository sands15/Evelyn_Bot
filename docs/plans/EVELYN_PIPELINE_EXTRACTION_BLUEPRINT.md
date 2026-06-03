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
