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
  P0-4 revised STT headless GPU1 → P0-5 Qwen3.8이다. P0-1~3은 검증 완료했고 P0-4 source와
  old/new STT image의 제한된 Docker/GPU 2+20 overlap은 검증했다.
- P0-4는 microphone, speaker, Discord, Minecraft 없이 현행 Qwen3-14B와 old/new STT image를
  분리 비교한다. overlap 2+20, private positive 40/negative 10 batch+stream, cancel/successor,
  cold restart 3회와 exact cleanup이 모두 필요하다.
- Discord transport exact-10과 반복 token prompt는 commits `2f802bd`, `314a358`로 닫았지만 staged
  audio는 one-phrase shape와 revised STT content/order gate가 `exact 0/10`, `order 0/10`, CER `1.4091`로 실패했다.
  transport 증거만 보존하며 고정 private manifest는 absent(`0/50`), promotion은 계속 차단한다.

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
- P0-4 source `d95ea89`의 old/new 2+20은 모두 오류 0으로 통과했다. old report SHA-256
  `5309ba0e...2d5e`의 STT/Main/Qwen p95는 `728.5/18.6/2270.3ms`, GPU1 min free는
  `10,294MiB`였다. 새 image `sha256:afece0d2...29c5`는 actual vLLM engine
  `8192/0.35/1/audio1`, max audio 30초와 package-set `c7518d52...e519`를 결박했다.
  새 report SHA-256 `cb72eb22...14b1`의 STT/Main/Qwen p95는 `158.2/24.9/2030.3ms`,
  min free `6,144MiB`였고 독립 비교와 환경 안정성이 통과했다. exact cleanup은 container/network/volume
  `0/0/0`, GPU1 `0MiB×3`, production·Docker Desktop OFF였다. 최종 canonical은
  `4605 passed, 22 skipped, 1425 subtests`다. 이는 full promotion 증거가 아니다.
  [[GPU1_CONCURRENCY_BENCHMARK]]
- 2026-08-16 historical GPU1 v1 live는 1+5에서 Fast Main/Qwen/STT p95
  `422.6/2233.2/626.1ms`, min free `10,284MiB`, error 0이었다. 현재 v2 승격 증거는 아니다.
- Main graph-on/SWA1/ubatch2048와 OmniVoice CUDA 12.9/FlashInfer 0.6.15는 source/live 근거가
  분리돼 있다. speaker/Discord를 포함한 전체 체감 SLO는 아직 완료가 아니다.
- Discord live transport는 DPAPI, gateway/voice UDP, canonical unique PCM·marker/hash `10/10`을 통과했다.
  그러나 길이 중앙값 `0.84s`, 두 파일에 전체 길이의 `80.3%`가 몰렸고 revised STT content/order도 실패했다.
  transcript는 출력·저장하지 않았고 invalid staging은 보존했다. exact-owned lab은 0, credential residue는 0,
  Docker Desktop/WSL은 OFF, GPU1은 `0MiB×3`, Wallpaper Engine은 기존 실행 상태였다.
- Minecraft 운영 bot은 OFF다. 격리 fresh-world shelter/restart 시나리오는 통과했지만 운영
  Discord/lease와 실제 음성 E2E는 후속 승인 범위다. 상세 상태는 [[CURRENT_STATE]]를 따른다.

## 다음 행동

1. phrase별 안내→한 문장 capture→즉시 content/order 판정→실패 clip만 retry하는 exact-10 경로로
   다시 수집한다. 현재 invalid staging은 corpus로 승격하지 않는다.
2. private 50-item이 완성되면 positive 40/negative 10 batch+stream, cancel/successor와 cold
   restart 3회를 실행한다.
3. 위 gate가 전부 통과한 뒤에만 revised STT image를 승격하고 P0-5 Qwen3.8을 시작한다.

## 작업 원칙

- 현재 구현, 목표 설계, source test와 live evidence를 구분한다.
- root-cause end-state, 수치 gate, rollback, exact-owned cleanup과 원상복구를 함께 구현한다.
- docs에는 private transcript/audio/path, credential, log와 runtime artifact를 넣지 않는다.

## 자세한 근거

- [[CURRENT_STATE]] · [[ACTIVE_RISKS]] · [[02_DECISIONS]] · [[worklog/2026-08-27]] ·
  [[DOCUMENTATION_INDEX]]
