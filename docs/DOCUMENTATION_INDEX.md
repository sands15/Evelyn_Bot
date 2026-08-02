# Evelyn Documentation Index

Last reviewed: 2026-08-02

Use this file to choose the right document before editing Evelyn. Current runtime
facts still need to be verified against code before reporting them as
implemented behavior.

## Project Navigation And Working Memory

- `docs/00_EVELYN_HOME.md`
  - 공식 Obsidian Vault의 탐색 시작점. 현재 사실을 복제하지 않고 권위 문서로
    연결한다.
- `docs/01_NOW.md`
  - Codex가 작업 시작 시 읽는 80줄 이하의 현재 초점·차단점 snapshot.
- `docs/02_DECISIONS.md`
  - 오래 유지할 제품·아키텍처·운영 결정과 근거.
- `docs/worklog/YYYY-MM-DD.md`
  - 의미 있는 구현 checkpoint, 검증 결과와 다음 행동의 날짜별 기록.
- `docs/99_PROJECT_INBOX.md`
  - 아직 검토되지 않은 아이디어와 질문. 검토 전에는 현재 구현이나 확정 요구로
    취급하지 않는다.

## Current Runtime References

- `docs/CURRENT_STATE.md`
  - 현재 브랜치, 배포 여부, 마지막 런타임 증거와 검증 상태만 유지한다.
  - 현재 사실을 확인할 때 가장 먼저 읽는다.

- `docs/ACTIVE_RISKS.md`
  - 해결되지 않은 위험, 검증 공백, 다음 조치와 재검토일만 유지한다.
  - 완료된 작업이나 낙관적 전망을 기록하지 않는다.

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

- `docs/LOCAL_VOICE_ADMISSION_CONTRACT.md`
  - 로컬 STT가 대화·도구·side effect로 들어가기 전의 정확한 선행 호출어,
    45초 follow-up, 고영향 fresh-wake, validation 한정 예외, 10초 일회성
    capability, durable ingress 원자성, browser source-spoof 차단·privacy 계약.

- `docs/VOICE_CAPTURE_CONSENT.md`
  - Control Page 로컬 검증의 시간 제한 마이크 캡처 동의와 철회 계약. 캡처
    동의가 발화 admission 권한을 대신하지 않는 경계를 함께 설명한다.

- `docs/DEPENDENCY_CONFIG_CREDENTIAL_HARDENING.md`
  - Current dependency compatibility matrix, typed owner configuration,
    exception observability, and dedicated Codex credential boundary.

- `docs/CLOUD_MIGRATION.md`
  - 클라우드 개발·빌드에 사용하는 source-only transfer 계약. pinned Mindcraft
    소스를 포함하는 재현 가능한 번들, secret/runtime/audio 차단 규칙과 hybrid
    배치 경계를 정의한다.

- `docs/CONVERSATION_CONTINUITY_CONTRACT.md`
  - 프로세스 재시작 뒤 완료된 대화 턴과 active follow-up을 15분 동안
    제한적으로 복구하는 checkpoint, 전달 직후 durable commit, content-free
    commit p50/p95 지표, Fast Control background action의 원문 없는
    crash-recovery 표식, generation/hash head, 시작 generation 기반 안내
    correlation과 자동 재시도 금지, Discord ambiguous-send 무재전송, privacy,
    deletion, observability 계약.

- `docs/CONVERSATION_INGRESS_RECOVERY_CONTRACT.md`
  - LLM·도구·외부 전달 전에 stable source delivery를 내구성 있게 claim하고,
    재시작 뒤 pending/in-flight/ambiguous 턴을 자동 재실행하지 않는 ingress
    상태 머신, stream write 경계, continuity generation 결합, privacy·retention
    및 Fast Control·Discord owner 통합 계약.

- `docs/MEMORY_PROVENANCE_DELETION_CONTRACT.md`
  - 기억의 source/evidence provenance, content-hash 기반 충돌 없는 사용자
    수정, 2단계 삭제, tombstone-first 내구성, 파생 기억의 연쇄 철회·격리·
    privacy-preserving 재합성, 누락 provenance의 exact-metadata 감사와
    target/source/full-graph hash에 묶인 2단계 backfill, 신호가 없는 과거
    기억의 사용자 직접 source 선택, 기존 관계의 conflict-safe
    relink/unlink와 최신 변경 undo, content-free write-ahead correction
    journal, source/note/age별 coverage, derived write 거부 카운터와 forward
    검증, quarantine 대기 관측, fail-closed 조회·cache 무효화와 재시작
    재조정 계약.

- `docs/AUTONOMY_AUTHORIZATION_CONTRACT.md`
  - 현재 프로세스에만 유효한 자율행동 grant, exact action scope, 결과 증거,
    Minecraft 직접 제어의 fail-closed 성공 판정, exact `auditReady`/
    `statusReady`, process-lifetime owner OS lock, 내구성 감사 journal·status와
    audit loss 중 safety-executable stop 계약.

- `docs/MINECRAFT_WORLD_ACTION_LEASE.md`
  - Minecraft runner의 Bot API 단일 lease owner, stable `owner_claim.lock`의
    process-lifetime kernel authority, 서비스 proof·15초 status guard,
    인증된 Discord 위임, 경쟁 owner 차단, 재시작 비복구와 flush/`fsync`
    audit/status-gated capability, 401 privacy와 remote stale-cache 제거 계약을
    정의한다.

- `docs/MINECRAFT_AUTONOMY_READINESS_CONTRACT.md`
  - Mindcraft HTTP liveness와 실제 Minecraft 자율행동 readiness를 분리하는
    exact dependency, blocker, task contract, fail-closed 소비자 계약.

- `docs/MINDCRAFT_MIGRATION.md`
  - 현재 pinned Mindcraft runtime, Evelyn overlay, world lease, survival
    controller와 generated-code lint gate의 빌드·운영 검증 기록.

- `docs/VISION_EVIDENCE_CONTRACT.md`
  - 화면 관찰 요청과 실제 관찰 근거를 분리하는 `vision.evidence.v2`,
    15초 freshness, source-conflict fallback, scene/OCR별 충족 조건과
    fail-closed prompt/tool gate 계약.

- `docs/HOST_VISION_BRIDGE_CONTRACT.md`
  - Docker와 Windows 사이의 단일 화면 관찰 queue, exact schema/TTL/allowlist,
    foreground metadata·native OCR 신뢰 경계, 즉시 삭제와 live E2E 증거 계약.

- `docs/UI_ACTION_TARGET_CONTRACT.md`
  - 현재 전경 UIA Button의 `invoke`만 허용하는 30초 일회성 preview/apply,
    실행 직전 대상 재관찰, 실행 후 exact postcondition 검증,
    `outcome_unverified` fail-closed와 content-free 감사 계약.

- `docs/RUNTIME_ERROR_OBSERVABILITY_CONTRACT.md`
  - Runtime owner별 고정 오류 코드·카운터와 Control Page 공개 privacy 계약.

- `docs/EVELYN_COMPLETENESS_EVALUATION_2026-06-09.md`
  - Detailed completeness evaluation and redesign-priority reference.
  - Includes the 2026-06-15 update for local TTS barge-in, speaker
    verification, and remaining live-verification gaps.
  - Use when deciding what to stabilize before adding major new features.

- `docs/EVELYN_PROJECT_AUDIT_2026-07-15_KR.md`
  - Current whole-project audit with weighted scores, verified runtime/test
    evidence, security and reproducibility risks, and a prioritized roadmap.
  - Use this as the latest conservative engineering-quality baseline.

- `docs/RUNTIME_SERVICE_MANIFEST_HEALTH_REPAIR_BLUEPRINT.md`
  - Temporary design blueprint for `Runtime Service Manifest + Health/Repair Layer`.
  - Use before implementing runtime service manifest, structured health, diagnostics, and repair actions.
- `docs/EVELYN_CONTROL_PAGE_OPERATION_GUIDE_KR.md`
  - 한글 운영 가이드: 상태코드 사전, 재시작 기준, 8798 계약 가드, 사용자 안내 문구.

- `docs/MAIN_PY_DECOMPOSITION_TARGET_KR.md`
  - `main.py`를 엔트리포인트/배선 파일로 줄이기 위한 최종 책임 경계와 단계별 분리 순서.

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

1. `docs/CURRENT_STATE.md`
2. `docs/ACTIVE_RISKS.md`
3. `CURRENT_EVELYN_PIPELINE.md`
4. `CURRENT_BOT_STRUCTURE.md`
5. code

When answering "where are we going?", use target and consolidated plan docs,
then verify against code before reporting it as implemented.
