---
tags:
  - evelyn
  - working-context
type: current-context
last_reviewed: 2026-09-02
---

# Evelyn — Now

Codex가 작업 시작 시 읽는 작은 문맥이다. 상세 사실은 링크된 문서와 코드·테스트에서 필요한 구간만 검색한다.

## 프로젝트 목표

로컬 우선 개인 비서 이블린을 자연스러운 대화, 음성, 근거 있는 장기 기억과 안전한 도구·Minecraft 자율행동을 갖춘 안정적인 런타임으로 완성한다.

## 현재 초점

- P1-3 exact task contract, grounded draft·고정 24-row evaluator와 P1-5 사람 교정 기반 개선 승격의
  source/offline 구현·전체 회귀를 완료했다. 실제 Qwen 평가·Discord feedback·10건 canary는 live 대기다.
- 기존 Obsidian-compatible Markdown vault가 장기기억의 durable source다. P1-4의 30일 exact private archive는
  별도 선택 기능이며 기본 OFF다. exact 기록·사용자별 열람/삭제 요구가 명시되지 않으면 BitLocker/live를 재개하지 않는다.
- 2026-09-02 선택 기능을 필수 다음 작업으로 오판해 BitLocker 경로를 진행했다. 상태 확인 외 변경은 없었고
  archive root·service·암호화 생성은 0이다. 미완료 plan 항목만으로 사용자 우선순위를 추정하지 않는다.
- 기존 승인 순서의 P0-1~3은 검증 완료했다. P0-4 revised STT는 제한된 GPU1 2+20을 통과했지만 private
  corpus·cancel/successor·cold restart가 남아 있고 P0-5 Qwen3.8은 시작하지 않았다.

## 최근 검증

- P1-3/P1-5 최종 canonical은 `5064 passed, 18 skipped, 1545 subtests`, 실패 0이다. 집중 검증은
  P1-3A `342/217`, P1-3B `188/184`, P1-5/Discord `349/74`, 추가 결합 `73/19`, 잠금·symlink 인접
  `170/114`(passed/subtests)다. Python compile, 두 admin JS와 diff check가 통과했다. source/offline
  증거이며 live 서비스는 실행하지 않았다. [[worklog/2026-08-28]]
- 2026-09-01 현재 checkpoint 대상 158개 경로는 승인 범위와 일치하고 제외 대상 0개다. P1 직접 소유 `181/72`, live
  prerequisite `205/44`(passed/subtests), canonical `5153 passed, 18 skipped, 1584 subtests`가 통과했다.
  외부 서비스는 기동하지 않았으며 registry 외 Gateway/archive E2E 증거는 아니다. [[worklog/2026-09-01]]
- 2026-09-02 feedback live launcher는 cleanup 불확실성에서 recovery ledger를 보존하도록 `6df290c`에서 보강했고 회귀 `10 passed`를 통과했다. [[worklog/2026-09-02]]
- 2026-09-02 명시적 사용자 확인 Markdown 기억은 격리 저장소의 실제 프로세스 재시작 경계에서
  저장→귀속 회상→삭제→비회상을 통과했다. 실제 기억과 외부 서비스는 건드리지 않았다. [[worklog/2026-09-02]]
- 2026-09-02 자동 일일·의미 파생 Markdown은 같은 길드에서도 계속 회상 차단됨을 확인했다. 현재 일일 파일이
  room/person/session 소유 경계를 보존하지 않으므로 reset scope·부모 hash만으로는 안전하게 열 수 없다. [[worklog/2026-09-02]]
- P0-1 Attempt 7, P0-2 queue/timeout, P0-3 recovery checkpoint는 완료 상태다. P0-3 commit/tag와 clean-clone
  근거는 [[CURRENT_STATE]]를 따른다.
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
- Discord guided 재수집은 credential prompt 없이 canonical unique PCM·marker/hash `10/10`을 통과했다.
  사후 exact-once STT는 nonempty `10/10`, error `0`이지만 content/order gate가 실패했다. transcript는
  출력·저장하지 않았고 staging은 자동 retry/delete/promotion 없이 보존했다. Docker AI inference socket
  재발 원인을 비활성화한 뒤 exact-owned lab 0, Docker/WSL·production OFF, GPU1 `0MiB×3`, Wallpaper 유지와
  cleanup을 확인했다. user-acceptance receipt는 두 원본 hash/fresh verifier를 통과하며 same-run binding과
  production promotion은 false다. 후속 diagnostic v2는 marker SHA를 포함하고 Discord accepted 알림도
  전송했다. [[worklog/2026-08-28]]
- Minecraft 운영 bot은 OFF다. 격리 fresh-world shelter/restart 시나리오는 통과했지만 운영
  Discord/lease와 실제 음성 E2E는 후속 승인 범위다. 상세 상태는 [[CURRENT_STATE]]를 따른다.

## 다음 행동

1. private archive·BitLocker를 다음 작업으로 추정하지 않는다. exact 30일 기록 요구가 명시될 때만 별도 재승인한다.
2. 명시적 기억 경로는 검증됐다. 자동 파생 기억은 owner별 source 분리와 exact turn 삭제 계보를 함께 설계할 때만 재개한다.
3. 실제 Qwen 24-row·grounded canary와 P0-4 50-item gate도 사용자 우선순위를 먼저 확인한다.

## 작업 원칙

- 현재 구현, 목표 설계, source test와 live evidence를 구분한다.
- root-cause end-state, 수치 gate, rollback, exact-owned cleanup과 원상복구를 함께 구현한다.
- docs에는 private transcript/audio/path, credential, log와 runtime artifact를 넣지 않는다.

## 자세한 근거

- [[CURRENT_STATE]] · [[ACTIVE_RISKS]] · [[02_DECISIONS]] · [[worklog/2026-09-02]] · [[DOCUMENTATION_INDEX]]
