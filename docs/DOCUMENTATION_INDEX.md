# Evelyn Documentation Index

Last reviewed: 2026-06-12

Use this file to choose the right document before editing Evelyn. Current runtime
facts still need to be verified against code before reporting them as
implemented behavior.

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

- `docs/EVELYN_COMPLETENESS_EVALUATION_2026-06-09.md`
  - Detailed completeness evaluation and redesign-priority reference.
  - Use when deciding what to stabilize before adding major new features.

- `docs/RUNTIME_SERVICE_MANIFEST_HEALTH_REPAIR_BLUEPRINT.md`
  - Temporary design blueprint for `Runtime Service Manifest + Health/Repair Layer`.
  - Use before implementing runtime service manifest, structured health, diagnostics, and repair actions.

- `docs/EVELYN_FAST_BOOT_ARCHITECTURE.md`
  - Fast Boot 우선 구현 전략과 상태 모델(`controlReady`, `botApiReady`, `chatReady`, `voiceReady`, `fullReady`),
    라우트별 degraded response, 검증 순서를 정리한 운영 설계 문서.

- `docs/EVELYN_FINAL_READINESS_JUDGMENT_BLUEPRINT.md`
  - Final readiness judgment blueprint focused on final-operability criteria.
  - Defines status taxonomy, diagnosis matrix, and 65/75/85 completion gates.

- `docs/EVELYN_DOCKER_COMPOSE_MIGRATION_BLUEPRINT.md`
  - Docker/Compose 전환 장단점, 단계별 로드맵, 서비스별 컨테이너화 적합도, GPU 주의 항목을 정리한
    운영 이동 계획.

## Consolidated Plans

- `docs/EVELYN_DOCKER_RUNTIME_QUICKSTART.md`
  - 1차 Compose 전환용 control-page + bot-api 실행 가이드와 검증 항목 정리.

- `docs/plans/EVELYN_PLANS_CONSOLIDATED.md`
  - Single combined reference for the previous `docs/plans/*.md` files.
  - Includes active plans, completed change logs, target notes, and historical
    execution notes.
  - Verify against current code before treating any section as runtime truth.

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

## Historical / Narrow References

- `docs/evelyn-dialogue-ux-fastpath-2026-05-28.md`
  - Historical fast-path design reference. Some ideas are implemented, but the
    current route policy lives in `CURRENT_EVELYN_PIPELINE.md` and code.

- `docs/recovery/VOYAGER_BRIDGE_RECOVERY.md`
  - Recovery-specific reference for the Voyager bridge.

## UI Files

These are runtime/control-page assets, not architecture docs.

- `docs/index.html`
  - Live control page. Currently promoted from the approved preview page.
- `docs/assets/`
  - Image, CSS, JS, and vendor assets that may still be used by older or future
    control-page revisions.

## Rule Of Thumb

When answering "what is current?", start with:

1. `CURRENT_EVELYN_PIPELINE.md`
2. `CURRENT_BOT_STRUCTURE.md`
3. `docs/plans/EVELYN_PLANS_CONSOLIDATED.md`
4. code

When answering "where are we going?", use target and consolidated plan docs,
then verify against code before reporting it as implemented.
