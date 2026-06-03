# Evelyn Documentation Index

Last reviewed: 2026-06-02

Use this file to choose the right document before editing Evelyn. Older docs are
kept for history and design context, but they are not all current-runtime
references.

## Current Runtime References

- `CURRENT_EVELYN_PIPELINE.md`
  - Authoritative current assistant pipeline map.
  - Use for Discord voice/text, router, main LLM, summary/sub LLM, context,
    skills, TTS delivery, and memory write-behind flow.

- `CURRENT_BOT_STRUCTURE.md`
  - Short operational map.
  - Kept as a compact overview and pointer to the current pipeline document.

- `CURRENT_EVELYN_ARCHITECTURE.md`
  - Current Minecraft/Voyager runtime architecture snapshot.
  - Not the full assistant voice/LLM pipeline reference.

- `CORE_ARCHITECTURE_BOUNDARY.md`
  - Core vs extension ownership boundary.

- `ROUTE_OWNERSHIP_POLICY.md`
  - Route ownership policy for core and skill routes.

- `docs/CONTEXT_PIPELINE_TARGET.md`
  - Partly current: includes implemented context-pipeline status, but the file
    also contains target direction.

- `docs/RUNTIME_ARTIFACTS_RETENTION.md`
  - Current retention guidance for runtime artifacts.

## Current Plan / Change Logs

- `docs/plans/EVELYN_PIPELINE_EXTRACTION_BLUEPRINT.md`
  - Implemented extraction blueprint and change log for the 2026-06-02 sequence:
    TTS playback manager facade, Discord delivery, TurnLifecycle, execution
    budget, replay/golden tests, Discord ingress, Discord session policy, and
    voice STT flow extraction.

- `docs/plans/EVELYN_HOTPATH_STABILIZATION_REFACTOR_PLAN.md`
  - Active hot-path stabilization plan and recent implementation log.
  - Use it to understand recent changes after `CURRENT_BOT_STRUCTURE.md`.

- `docs/plans/EVELYN_TTS_PHASE2_RUNTIME_VERIFICATION_CHECKLIST.md`
  - Runtime verification checklist for the TTS playback cleanup.

## Target Architecture / Design Direction

These describe desired end states. Do not treat them as current runtime facts
without checking code or current docs.

- `docs/EVELYN_ASSISTANT_TARGET_ARCHITECTURE.md`
- `docs/EVELYN_CURRENT_STRUCTURE_LAYER_MAPPING.md`
- `docs/EVELYN_MEMORY_VAULT_ARCHITECTURE.md`
- `docs/GROWTH_ORIENTED_BOT_ARCHITECTURE.md`
- `docs/GROWTH_ORIENTED_BOT_REFACTOR_ROADMAP.md`
- `docs/GROWTH_ORIENTED_BOT_COMPLETION_CHECKLIST.md`
- `docs/MINECRAFT_AGENT_CODE_FIRST_ARCHITECTURE.md`
- `docs/MINECRAFT_BOT_TARGET_ARCHITECTURE.md`
- `docs/KO_STT_SCOREBOARD_TARGET.md`
- `docs/plans/EVELYN_ASSISTANT_PHASE1_STRUCTURE_REFACTOR.md`
- `docs/plans/VOICE_PIPELINE_REFACTOR_PLAN.md`
- `docs/plans/EVELYN_CONTROL_PAGE_MODE_TARGET.md`
- `docs/plans/EVELYN_LANDING_PAGE_TARGET.md`
- `docs/plans/EVELYN_PAGE_DISTRIBUTION_TARGET.md`

## Historical / Narrow References

- `docs/evelyn-dialogue-ux-fastpath-2026-05-28.md`
  - Historical fast-path design reference. Some ideas are implemented, but the
    current route policy lives in `CURRENT_EVELYN_PIPELINE.md` and code.

- `docs/tts-streaming-architecture-2026-05-27.md`
  - Historical TTS streaming note.

- `docs/voice-recv-pr56-port-plan.md`
  - Historical Discord voice receive porting plan.

- `docs/voice-recv-validation-2026-04-19.md`
  - Historical validation note.

- `docs/recovery/VOYAGER_BRIDGE_RECOVERY.md`
  - Recovery-specific reference for the Voyager bridge.

## UI Files

These are runtime/control-page assets, not architecture docs.

- `docs/index.html`
- `docs/evelyn-control-preview.html`

## Rule Of Thumb

When answering "what is current?", start with:

1. `CURRENT_EVELYN_PIPELINE.md`
2. `CURRENT_BOT_STRUCTURE.md`
3. `docs/plans/EVELYN_HOTPATH_STABILIZATION_REFACTOR_PLAN.md`
4. code

When answering "where are we going?", use target and plan docs, then verify
against code before reporting it as implemented.
