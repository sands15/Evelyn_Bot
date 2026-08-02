---
tags:
  - evelyn
  - working-context
type: current-context
last_reviewed: 2026-08-02
---

# Evelyn — Now

Codex가 작업 시작 시 읽는 작은 작업 문맥이다. 상세 사실은 링크된 권위 문서와
코드·테스트에서 필요한 구간만 검색한다. 이 파일은 80줄 이하로 유지한다.

## 프로젝트 목표

로컬 우선 개인 비서 이블린을 자연스러운 대화, 음성, 근거 있는 장기 기억,
안전한 도구·Minecraft 자율행동을 갖춘 안정적인 런타임으로 완성한다.

## 현재 초점

- Control Page hard-crash 뒤 Host/Bridge가 capture lease 만료를 독립 집행하는
  watchdog과 다중 owner 배제
- admission token 발급 뒤 Bot 재시작과 validation attempt cross-process lease
- 로컬 및 Discord 음성의 실제 장치 E2E 검증
- 실제 Minecraft 승인 행동과 결과 증거 검증
- 전체 회귀, Docker 빌드와 smoke를 통한 최종 운영 증거 확보

## 최근 확인

- Local Voice의 `consume -> durable ingress claim` crash-loss 창은 typed transaction과
  실제 claim 직후 강제 종료·재시작 회귀로 닫았다.
- 손상·누락된 capture consent, OFF supersession, 취소 뒤 늦은 ON, heartbeat 위조·
  역전과 validation terminal 경쟁은 exact ACK/auth/statusSeq/enable fence로 닫았다.
- consent preview는 최신 1개와 정확한 validation 세대에만 유효하며, idle ON 뒤
  Discord-only 시작과 mutation I/O 예외도 즉시 exact OFF로 닫힌다.
- Supervisor 복구는 의존 서비스를 재생성하지 않고, Bridge·Docker·재시작 자식은
  목적별 최소 credential만 받는다. 전체 재시작은 Supervisor가 소유한다.
- 관련 source 회귀는 runtime 667개(skip 4), voice 전체 568개(skip 5),
  `test_local*.py` 182개가 통과했다. 실제 마이크·스피커·Discord live 검증은
  수행하지 않았다.

## 작업 원칙

- 현재 상태와 목표 설계를 분리한다.
- 소스 테스트 통과를 live 검증으로 표현하지 않는다.
- Discord, 마이크, Minecraft, Docker 등 live 동작은 사용자 승인 범위에서만 수행한다.
- private transcript, token, 음성, screenshot, runtime artifact를 문서에 저장하지 않는다.
- 코딩은 `ponytail full`을 기본으로 하되 안전·보존·명시 계약과 검증은 줄이지
  않고, 작업 종료 때 측정 가능한 감소율을 보고한다.

## 자세한 근거

- [[CURRENT_STATE]] — 구현·검증의 현재 사실
- [[ACTIVE_RISKS]] — 남은 위험과 검증 공백
- [[DOCUMENTATION_INDEX]] — 문서 권위와 탐색 경로
- [[02_DECISIONS]] — 지속할 결정과 근거
- [[worklog/2026-08-02]] — 오늘의 검증 근거와 남은 경계

## 다음 작업 종료 시

- 현재 초점·차단점·다음 행동이 달라졌을 때만 이 문서를 짧게 갱신한다.
- 상세 결과는 `worklog/YYYY-MM-DD.md`에 기록하고 여기에는 링크만 남긴다.
