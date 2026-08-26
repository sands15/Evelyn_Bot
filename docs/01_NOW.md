---
tags:
  - evelyn
  - working-context
type: current-context
last_reviewed: 2026-08-27
---

# Evelyn — Now

Codex가 작업 시작 시 읽는 작은 작업 문맥이다. 상세 사실은 링크된 권위 문서와 코드·테스트에서
필요한 구간만 검색한다.

## 프로젝트 목표

로컬 우선 개인 비서 이블린을 자연스러운 대화, 음성, 근거 있는 장기 기억과 안전한 도구·Minecraft
자율행동을 갖춘 안정적인 런타임으로 완성한다.

## 현재 초점

- 승인 순서는 P0-1 Main finalist → P0-2 queue/timeout → P0-3 recovery checkpoint →
  P0-4 revised STT headless GPU1 → P0-5 Qwen3.8이다. P0-1~3은 검증 완료했고 P0-4 source/offline
  계약과 제한된 Docker/GPU live 검증을 진행 중이다.
- P0-4는 microphone, speaker, Discord, Minecraft 없이 현행 Qwen3-14B와 old/new STT image를
  분리 비교한다. overlap 2+20, private positive 40/negative 10 batch+stream, cancel/successor,
  cold restart 3회와 exact cleanup이 모두 필요하다.
- private corpus directory가 현재 absent(`0/50`)다. 합성 자료로 대체하지 않으며 이 gate가
  닫히기 전에는 STT image를 승격하거나 P0-5 Qwen3.8을 시작하지 않는다.

## 최근 검증

- P0-1 Attempt 7은 warm `200×2`, restart-ready `30×2`, ABBA `20`, soak `1,000`을 완주했다.
  graph-off/on p50/p95/p99는 `238.7/260.7/290.1ms` 대 `201.85/219.1/239.8ms`,
  p95 95% CI `[-45.7,-26.7]ms`, effect size `-3.0166`이다. fresh verifier, quality/cache/error,
  exact cleanup과 Docker/production OFF가 모두 통과했다.
- P0-2 queue/inference clock 분리와 timeout cancel-safe lane 회귀를 닫았다. 관련 회귀와 canonical
  `4573 passed, 22 skipped, 1391 subtests passed`가 통과했다.
- P0-3 commit `d1c8863bc7d338ea6b7ff3013d1e0574dff73e80`, annotated tag
  `evelyn-recovery-2026-08-26`, bundle SHA-256
  `dd8e4bc54397eb289cc20d6c8fdfca2ecd107cab13bed81d812416d47f188dcb`를 clean clone에서
  canonical 4,573개(+subtests 1,391)로 검증했다. root/submodule은 checkpoint에서 clean이다.
- P0-4 source에는 v2 overlap receipt, private corpus runner, hub-only read-only/offline STT mount,
  최소 build context, digest-pinned two-stage STT recipe와 package-set hash가 구현됐다.
  P0-4 focused는 `75 passed, 29 subtests`, voice 전체는 `816 passed, 5 skipped, 98 subtests`,
  canonical은 `4601 passed, 22 skipped, 1409 subtests`로 통과했다. Docker/GPU live는 아직
  실행하지 않았다. [[GPU1_CONCURRENCY_BENCHMARK]]
- 2026-08-16 historical GPU1 v1 live는 1+5에서 Fast Main/Qwen/STT p95
  `422.6/2233.2/626.1ms`, min free `10,284MiB`, error 0이었다. 현재 v2 승격 증거는 아니다.
- Main graph-on/SWA1/ubatch2048와 OmniVoice CUDA 12.9/FlashInfer 0.6.15는 source/live 근거가
  분리돼 있다. speaker/Discord를 포함한 전체 체감 SLO는 아직 완료가 아니다.
- Minecraft 운영 bot은 OFF다. 격리 fresh-world shelter/restart 시나리오는 통과했지만 운영
  Discord/lease와 실제 음성 E2E는 후속 승인 범위다. 상세 상태는 [[CURRENT_STATE]]를 따른다.

## 다음 행동

1. P0-4 identity/privacy receipt 회귀, 관련 suite와 canonical을 통과시키고 clean commit을 만든다.
2. clean source에서만 기존 image/host preflight, old baseline, revised STT build/health를 실행한다.
3. private 50-item gate가 없으면 안전 복구·evidence 보존 뒤 P0-4를 blocked로 보고한다.
4. P0-4가 전부 통과한 뒤에만 P0-5 Qwen3.8 artifact/build/A/B를 시작한다.

## 작업 원칙

- 현재 구현, 목표 설계, source test와 live evidence를 구분한다.
- root-cause end-state, 수치 gate, rollback, exact-owned cleanup과 원상복구를 함께 구현한다.
- docs에는 private transcript/audio/path, credential, log와 runtime artifact를 넣지 않는다.

## 자세한 근거

- [[CURRENT_STATE]] · [[ACTIVE_RISKS]] · [[02_DECISIONS]] · [[worklog/2026-08-27]] ·
  [[DOCUMENTATION_INDEX]]
