# Current Evelyn Pipeline

Last reviewed: 2026-06-02
Scope: live Evelyn assistant pipeline in `C:\Evelyn`
Status: authoritative current-runtime map

This document is the current reference for Evelyn's live assistant pipeline. It
describes the code shape after the 2026-06-02 extraction pass that wrapped TTS
playback, split Discord delivery/ingress/session policy helpers, extended turn
lifecycle/budget contracts, and moved voice STT execution contracts out of
`main.py`.

For Minecraft/Voyager-specific architecture, see `CURRENT_EVELYN_ARCHITECTURE.md`.
For target architecture ideas, see the docs listed in `docs/DOCUMENTATION_INDEX.md`.

## Short Version

```text
Discord text / Discord voice / local mic
-> Discord ingress helpers + main.py live event handlers
-> voice filtering / wake handling / STT helpers when audio
-> VoiceTurnRequest
-> VoiceTurnOrchestrator
-> prepare_route_context
-> runtime mode + fast path + fallback/router LLM route choice
-> cognitive state and ContextPolicy
-> RouteDecision execution flags
-> short-circuit, registered skill route, policy answer, or main LLM
-> delivery plan
-> Discord delivery adapter + TTS playback manager
-> memory write-behind
```

The main LLM does not decide whether the router is needed. Routing and context
policy happen before the main LLM call. The router LLM is optional, and the
summary/sub LLM is also optional.

## Runtime Owners

- `main.py` is still the central application surface and live integration host.
  It owns process startup, Discord event handlers, live voice receive control
  flow, side-effect logging, debug artifact writes, and runtime service wiring.
- `evelyn_core/runtime/evelyn_core/voice_orchestration.py` owns the middle turn
  orchestration boundary through `VoiceTurnRequest`, `VoiceTurnRouteContext`,
  `VoiceTurnResult`, and `VoiceTurnOrchestrator`.
- `evelyn_core/runtime/evelyn_core/voice_pipeline.py` owns route decision data
  structures and voice classification helpers.
- `evelyn_core/runtime/evelyn_core/context_pipeline.py` owns `ContextPolicy`,
  `ContextPacket`, `ContextBuilder`, and `MemoryWriterDecision`.
- `evelyn_core/runtime/evelyn_core/skills/*` owns registered extension routes.
- `evelyn_core/runtime/evelyn_core/tts_playback.py` owns much of the TTS playback
  state/contract helper surface. `TtsPlaybackManager` is the first facade over
  the existing tracker/registry helpers and now owns single-source playback plus
  streaming prepared-queue playback orchestration. `main.py` still coordinates
  high-level Discord, OmniVoice request construction, and turn lifecycle
  decisions.
- `evelyn_core/runtime/evelyn_core/discord_delivery.py` is the first Discord
  delivery adapter slice. It currently centralizes plain text send plus
  reference-message fallback behavior for the proactive followup path and final
  text replies. Autonomy notify/followup text sends also use this helper. The
  same module now owns streaming voice delivery setup for sentence queues,
  `TTSQueueSink`, and delivery-plan chunk execution.
- `evelyn_core/runtime/evelyn_core/turn_lifecycle.py` owns `TurnScope` and the
  first lifecycle fields layered onto the existing cancel scope: `TurnState`,
  transition log, cancel reason, stale/current checks, and snapshots.
- `evelyn_core/runtime/evelyn_core/turn_budget.py` owns the first
  `TurnExecutionBudget` helper. Router classification uses its router timeout,
  and route/context policy records budget metadata for fallback visibility.
- `tests/fixtures/turn_replay/` and `tests/test_turn_replay_golden.py` are the
  first replay/golden harness for barge-in cancellation, late LLM chunks, TTS
  cancellation, and memory write-behind failure.
- `evelyn_core/runtime/evelyn_core/discord_ingress.py` is the first Discord
  ingress adapter slice. It centralizes text session/reply-slot key construction,
  text gate acceptance, text turn input normalization, voice session key
  construction, and voice debug-meta/source normalization helpers while
  `main.py` still owns the live Discord `on_message(...)` and voice receive
  flows.
- `evelyn_core/runtime/evelyn_core/discord_session_policy.py` is the first
  Discord session-policy slice. It owns pure decision helpers for voice reply
  gating, local-mic Discord suppression, TTS interruption, wake-probe full-STT
  skip, short transcription ignore, and short owner-followup candidate checks;
  it also provides `DiscordRoomSessionPolicy` as the room owner/reply-in-progress
  facade over the existing `voice_orchestration` room-state helpers. `main.py`
  still gathers live session and local mic runtime state.
- `evelyn_core/runtime/evelyn_core/voice_stt_flow.py` is the first voice STT
  execution-contract slice. It normalizes wake STT mappings, applies strict
  and fuzzy wake interpretation, owns final wake-veto decision logic, and owns
  partial STT, full STT/rescore, and final transcript assembly as focused
  helpers; `main.py` still owns the live wake probe, audio preprocessing, and
  drop/followup flow.

## Extraction Status

The 2026-06-02 extraction pass is complete enough to treat this document as the
current pipeline map.

Implemented slices:

- TTS playback manager facade over existing playback tracker/registry helpers.
- Discord text delivery and streaming voice-delivery setup helpers.
- `TurnScope` moved into a lifecycle module with state, transition log, cancel
  reason, and stale/current checks.
- `TurnExecutionBudget` connected to router timeout and route/context metadata.
- Replay/golden fixtures for barge-in, late LLM chunk, TTS cancellation, and
  memory write-behind failure.
- Discord ingress helpers for text keys/gates/input normalization and voice
  debug/source normalization.
- Discord session-policy helpers for voice reply gate, local mic suppression,
  TTS interrupt qualification, wake/no-wake skip, short transcript ignore,
  short follow-up candidate, and room-owner/reply facade.
- Voice STT flow helpers for wake interpretation, partial STT, full STT/rescore,
  final transcript assembly, and final wake-veto decision.

Intentionally still in `main.py`:

- live Discord event-loop control flow;
- command handler `ctx.send(...)` calls;
- live wake probe execution and audio preprocessing;
- voice drop side effects, debug audio writes, and human-readable stage logs;
- high-level OmniVoice request construction;
- process/service startup and shutdown wiring.

This keeps the hot path behavior stable while reducing `main.py` ownership of
pure decisions and low-level playback contracts.

## Input Paths

### Voice

```text
Discord audio segment / local mic audio
-> quality, VAD, suppression, cooldown, and wake checks
-> wake probe / partial STT / full STT as needed
-> transcript cleanup and correction
-> normal turn request
```

Important code:

- `main.py`: STT loading, voice queues, live wake/audio side effects, turn
  entrypoints.
- `evelyn_core/runtime/evelyn_core/voice_stt_flow.py`: wake interpretation,
  partial STT, full STT/rescore, final transcript assembly, and final wake veto.
- `evelyn_core/runtime/evelyn_core/discord_session_policy.py`: voice reply gate
  and Discord session policy decisions.
- `evelyn_core/runtime/evelyn_core/discord_ingress.py`: Discord text/voice input
  normalization helpers.
- `evelyn_core/runtime/evelyn_core/audio.py`: audio helpers.
- `evelyn_core/runtime/evelyn_core/text.py`: transcript cleanup helpers.

### Text

```text
Discord text command/message
-> session and route eligibility checks
-> normal turn request
```

Text input skips STT but joins the same route/execution/delivery shape.

## Turn Orchestration

`ask_llm_streaming()` now builds a normalized `VoiceTurnRequest` and hands it to
`VoiceTurnOrchestrator`.

The orchestrator runs this order:

1. Prepare route context.
2. Try short-circuit route handling.
3. Try a registered skill route.
4. If `route_decision.needs_main_llm` is false, deliver the policy answer.
5. Otherwise call the main LLM path.
6. Return a `VoiceTurnResult` with the handler that completed the turn.

Orchestrator failures mark the failed layer in metrics, so turn summaries can
separate route-context, short-circuit, skill-route, delivery, and main-LLM
failures.

## Routing And Policy

Routing is decided before the main LLM.

```text
user_text
-> compute_runtime_mode
-> apply_runtime_mode
-> fast_path_policy or fallback/router LLM route
-> cognitive state
-> ContextPolicy
-> RouteDecision
```

Key files and functions:

- `main.py:1820` `compute_runtime_mode`
- `main.py:1834` `apply_runtime_mode`
- `main.py:2308` `fast_path_policy`
- `main.py:3270` `prepare_llm_messages`
- `main.py:3805` `classify_llm_route_async`
- `main.py:9176` `prepare_route_context`
- `evelyn_core/runtime/evelyn_core/context_pipeline.py:61` `ContextPolicy`
- `evelyn_core/runtime/evelyn_core/voice_pipeline.py:41` `RouteDecision`

### Router LLM Is Conditional

The router LLM is not mandatory.

It is skipped when:

- runtime mode is `realtime` and `skip_router` is set;
- `fast_path_policy()` returns a clear route;
- source is voice and the input does not force voice-context routing;
- `ROUTER_LLM_ENABLED` is false;
- the router call fails, in which case fallback routing is used.

When used, the router LLM returns a selected route plus a `context_policy`
object. That policy is normalized through `ContextPolicy.from_mapping()` before
prompt assembly.

### Fast Path

Fast path can choose lightweight outcomes without paying router/context costs.
Examples:

- empty input -> wait;
- obvious continue marker -> wait;
- simple directive -> main direct answer;
- light request -> main direct answer;
- explicit search trigger -> search executor.

Fast-path metadata can also say memory and runtime context are not needed for
that turn.

## Cognitive State And Context

Cognitive state is separate from main answer generation. It can be:

- fast-path generated;
- read from cached state;
- refreshed in the hot path when missing or when route is `sub_wait`;
- refreshed in the background for ordinary turns.

The context pipeline then builds a compact packet:

```text
conversation state
+ memory context if policy needs memory
+ runtime state if policy needs runtime
+ Minecraft state / skill graph if policy asks for it
+ vision context if policy asks for it
-> ContextBuilder
-> main LLM messages
```

The main LLM receives only the assembled packet, not every memory layer by
default.

## Main LLM

The main LLM is called only after route policy says it is needed.

`RouteDecision.needs_main_llm` and `ContextPolicy.needs_main_llm` are combined
in `prepare_route_context()`. If the final value is false, the orchestrator can
return a policy/short-circuit answer without calling the main LLM.

The main LLM is still the normal user-facing generator for ordinary answers.

## Summary/Sub LLM And Memory Write-Behind

The summary/sub LLM is not part of every live response.

Memory writing now goes through `MemoryWriterDecision` before heavier summary,
fact, open-question, or Minecraft-failure updates are scheduled.

The summary/sub LLM is skipped when `MemoryWriterDecision.should_run_summary_llm()`
is false. In realtime mode, memory work can be deferred/raw-only so voice latency
does not get worse.

Memory write-behind status is recorded as queued, running, completed, failed,
cancelled, skipped, or deferred in:

```text
runtime_artifacts/memory/writebehind_status.jsonl
```

## Skill Routes

Registered skills are extension routes, not replacements for the core pipeline.

Current core route families include:

- `main_direct`
- `policy_short_circuit`
- `search_executor`
- `delivery`

Route ownership guidance lives in `ROUTE_OWNERSHIP_POLICY.md` and
`evelyn_core/runtime/evelyn_core/skills/README.md`.

## Delivery And TTS

Delivery is selected after route execution:

```text
answer payload
-> delivery plan
-> Discord text and/or TTS
-> TTS chunking / streaming / playback
-> playback completion or cancellation metrics
```

`needs_tts` is now an explicit route policy flag. The orchestrator and delivery
helpers respect it, so a route can answer without voice playback when policy
requires that.

TTS is still a sensitive hot path. Do not change TTS internals unless a concrete
live failure or an approved refactor phase requires it.

## Minecraft/Voyager Branch

Minecraft automation is optional from the assistant pipeline point of view.
When route policy asks for Minecraft context or action, the assistant side uses
compact runtime snapshots and capability/skill hints.

High-level branch:

```text
main.py / route policy
-> minecraft_autonomy_client.py
-> voyager_service.py on port 8765
-> upstream_voyager_runner.py
-> third_party/Voyager
-> Codex gateway on port 8787
-> mineflayer bridge on port 3000
-> Minecraft server on port 25565
```

The live Minecraft/Voyager architecture reference remains
`CURRENT_EVELYN_ARCHITECTURE.md`.

## Current Validation Baseline

Latest verification recorded on 2026-06-02 after the extraction pass:

- `python -m unittest discover -s tests` ran 184 tests OK.
- `python -m py_compile main.py` plus touched runtime/test modules passed.
- `git diff --check` reported no content problems; only existing LF/CRLF
  warnings were printed for several working-copy files.

This document is a map, not proof that the current runtime process has been
restarted with the latest code. Runtime verification still requires checking
the running process, ports, logs, and model services.

## Runtime Verification Needed

Before calling the new extraction live-runtime verified:

- ask before restarting Evelyn or related services;
- confirm the running process was started from the updated checkout;
- check Discord text response, Discord voice response, and local mic handoff;
- check barge-in/TTS cancellation behavior;
- check at least one wake-detected voice turn and one owner-followup voice turn;
- watch `runtime_artifacts/memory/writebehind_status.jsonl` for memory
  write-behind failures.

## Editing Rules

- Do not restart Evelyn, OpenClaw gateway, Voyager, Minecraft, or model servers
  without explicit approval.
- Do not delete `bot_memory` or `runtime_artifacts` data directly.
- Prefer route policy flags over ad hoc hot-path conditionals.
- Keep fast-path and realtime paths cheap.
- Treat old target/plan docs as design history unless `docs/DOCUMENTATION_INDEX.md`
  marks them current.
