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

- 로컬 및 Discord 음성의 실제 장치 E2E 검증
- 검증된 source를 실행 이미지로 교체하고 revision/readiness를 다시 확인
- 실제 Minecraft 승인 행동과 결과 증거 검증
- 전체 회귀, Docker 빌드와 smoke를 통한 최종 운영 증거 확보

## 최근 확인

- Control Page capture owner는 stable `owner_claim.lock`의 process-lifetime OS
  lock으로 하나만 허용한다. loser는 동의 상태·heartbeat·마이크를 건드리지 않고
  기동을 중단하며, 정상 종료와 취소 중에도 writer drain과 OFF 철회가 끝난 뒤에만
  잠금을 반납한다. retention도 lock을 정리 후보에서 영구 제외한다. 실제 별도
  프로세스 crash와 후속 인수 회귀를 통과했다.
- Local Voice의 `consume -> durable ingress claim` crash-loss 창은 typed transaction과
  실제 claim 직후 강제 종료·재시작 회귀로 닫았다.
- Local Voice token은 응답 전에 content-free durable reservation을 만들며, Bot
  재시작 뒤 exact binding과 현재 capture-consent fence로만 claim할 수 있다. v2 proof는
  발급 fence digest를 포함하며 OFF는 restart-orphan reserved row도 scope purge한다.
  durable consent write와 마지막 reserve/claim은 stable `claim_lease.lock`으로
  선형화되어 A→B token 부활과 revoke-vs-acceptedText race를 닫았다. 철회 대기는
  2초로 제한되고 timeout·write 실패는 memory-first `revoking`과 physical OFF로
  닫힌다. post-claim validation event 실패도 token이나 LLM 재실행을 열지 않는다.
- validation issue/consume과 retry/abort는 cross-process attempt lease로 직렬화되고,
  성공·409·503 lease를 실제 HTTP terminal까지 유지한다. validation LLM은
  memory/history/tool 없이 격리되며 assistant 원문은 normal history/replay에 남기지
  않는다.
- 손상·누락된 capture consent, OFF supersession, 취소 뒤 늦은 ON, heartbeat 위조·
  역전과 validation terminal 경쟁은 exact ACK/auth/statusSeq/enable fence로 닫았다.
- consent preview는 최신 1개와 정확한 validation 세대에만 유효하며, idle ON 뒤
  Discord-only 시작과 mutation I/O 예외도 즉시 exact OFF로 닫힌다.
- Supervisor 복구는 의존 서비스를 재생성하지 않고, Bridge·Docker·재시작 자식은
  목적별 최소 credential만 받는다. 전체 재시작은 Supervisor가 소유한다.
- Control Page hard-crash 뒤에도 서명된 content-free lease가 4초 stale이면 Bridge가
  독립적으로 캡처를 멈춘다. stop 실패는 Bridge exit 76으로 OS handle을 회수하고,
  Supervisor는 현재 자식·instance·sequence·physical OFF를 모두 확인한다.
- 관련 CI-equivalent 전체 discover 3044개(skip 20), 최종 hardening 묶음 267개
  (skip 1)가 통과했다.
  `compileall`, `pip check`, JS 구문, Compose config와 diff check도 통과했다. 실제
  마이크·스피커·Discord live 검증은 수행하지 않았다.

## 작업 원칙

- 현재 상태와 목표 설계를 분리한다.
- 소스 테스트 통과를 live 검증으로 표현하지 않는다.
- Discord, 마이크, Minecraft, Docker 등 live 동작은 사용자 승인 범위에서만 수행한다.
- private transcript, token, 음성, screenshot, runtime artifact를 문서에 저장하지 않는다.
- 코딩은 `ponytail full`을 기본으로 하되 안전·보존·명시 계약과 검증은 줄이지
  않는다. 종료 때 별도 감소율·절감 수치 보고는 요구하지 않는다.

## 자세한 근거

- [[CURRENT_STATE]] — 구현·검증의 현재 사실
- [[ACTIVE_RISKS]] — 남은 위험과 검증 공백
- [[DOCUMENTATION_INDEX]] — 문서 권위와 탐색 경로
- [[02_DECISIONS]] — 지속할 결정과 근거
- [[worklog/2026-08-02]] — 오늘의 검증 근거와 남은 경계

## 다음 작업 종료 시

- 현재 초점·차단점·다음 행동이 달라졌을 때만 이 문서를 짧게 갱신한다.
- 상세 결과는 `worklog/YYYY-MM-DD.md`에 기록하고 여기에는 링크만 남긴다.
