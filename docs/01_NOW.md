---
tags:
  - evelyn
  - working-context
type: current-context
last_reviewed: 2026-08-09
---

# Evelyn — Now

Codex가 작업 시작 시 읽는 작은 작업 문맥이다. 상세 사실은 링크된 권위 문서와
코드·테스트에서 필요한 구간만 검색한다. 이 파일은 80줄 이하로 유지한다.

## 프로젝트 목표

로컬 우선 개인 비서 이블린을 자연스러운 대화, 음성, 근거 있는 장기 기억,
안전한 도구·Minecraft 자율행동을 갖춘 안정적인 런타임으로 완성한다.

## 현재 초점

- OmniVoice 실제 스피커 청취 확인과 로컬 10-turn·무음 음성 E2E 검증
- 로컬 마이크 동의 후 실제 장치 barge-in 연속성 검증
- Discord 음성의 실제 채널 E2E 검증
- Conversation Continuity와 local/Discord 음성의 장애·재시작 실환경 검증

## 최근 확인

- 2026-08-09 Mindcraft history는 bounded process-local·no-mount이며 Codex는 off다.
  Node LLM은 authenticated Bot API broker만 쓰고 서버가 fixed local/router를 선택한다. Minecraft lease 위임은 exact nonnegative JSON integer `guildId`만 받는다.
  broker는 core exposure를 frame consumer의 exact `delivered|discarded` ACK까지, generation fence는 final
  route/action sink까지 유지한다. recovery step은 exact history snapshot의 process-local
  one-shot issuance만 소비한다. Minecraft Autonomy plan은 current grant의 연속 prefix만 만들고, route와 engine lifecycle은 cleanup까지 직렬화되며 `자율정지`는 intent를 보존한다. durable bound-receipt history, legacy cleanup과 live 검증은 남아 있다.
- 2026-08-08~09 memory 삭제는 Busy fallback과 2초 admission을 유지한다. 적용된
  edit·provenance·post-tombstone cleanup 503은 강제 재조회 뒤 자동 재시도하지 않고, 손상 full receipt는 `unattributed`로 강등한다.
  replica 검증은 통과했지만 host ACL·Docker mount, live busy 전이와 rotation은 P1이다.
- 2026-08-08 필수 provenance가 손상된 recall이 정상 pinned note ID를 빌려
  `attributed`가 되던 경로를 cache·receipt 공용 검사와 전체 prompt 보류로 닫았다.
- 2026-08-08 Control Page의 transient degraded 화면 덮기와 stale poll 경쟁을
  ready latch·채팅 보존·single-flight/generation fence로 막았다(UI 176·집중 60 통과, live GET 일치).
- 2026-08-08 기본 `tts:8880`을 실제 `k2-fsa/OmniVoice` container로 교체했다.
  recipe `7cfc51e96088`, source revision `485c81d`, 서버 Python 20개와 model snapshot
  13개 SHA-256, read-only profile/cache, exact model ID/revision health가 모두 통과했다.
  실제 Evelyn clone sentence stream은 24 kHz mono 16-bit PCM 101,280 bytes를
  728 ms에 반환했다. profile API는 `ref_text`를 숨겼고 운영 로그에서도 합성 원문,
  경로, session/turn 식별자가 검출되지 않았다. PCM은 메모리에서 검사 후 폐기했다.
  사용자 스피커 청취와 local/Discord 10-turn·무음 E2E는 아직 남아 있다.
- 현재 저장소의 정상 launcher가 stale Bot API·Control Page image를 exact source
  revision으로 감지해 재빌드한다. 2026-08-08 실제 `start_local.bat --background`에서
  Control Page, Bot API, LLM 3개, OmniVoice, STT, Vision과 Local Bridge가 공식 checker를
  통과했다. Bridge는 mic OFF, output ready, clone warmup 564.1 ms, error/play count 0이었다.
- 오래된 `C:\Evelyn` Compose에는 `vision_runtime`이 없어 현재 Vision ingress와
  결합하면 DNS/502가 반복됐다. 현재 저장소 Compose로 재생성해 ingress와 runtime을
  함께 띄웠고 Vision health가 통과했다.
- Windows venv launcher PID와 실제 Bridge PID가 달라 Supervisor readiness가
  실패하던 문제를 실제 base Python 직접 소유 방식으로 수정했다. Supervisor가
  소유한 PID와 서명 상태의 Bridge PID가 일치하고 TTS warmup도 완료됐다.
- Local Voice는 단일 capture owner, durable claim·consent fence로 재시작 경쟁을 막고,
  Local Bridge 재생 실패는 user-only로 잇고 Discord partial checkpoint는 assistant/receipt/state만 복구해 TTL을 보존한다.
- validation GET은 현재 consent를 반환하고 STT·Bridge·TTS warmup/control·router와 Main/Fast failed-tool·vision metrics는 예외 원문을 가린다. Runtime Health probe도 timeout을 지킨다.
  Discord stale voice client는 강제 정리 뒤 표준 연결로 복구하고, 검색 복구·playback timeout·무재생 거부·late-turn fence가 stale 완료를 막는다.
- validation LLM은 memory/history/tool 없이 격리되고 원문은 일반 history에 남지 않는다. awaiting 세션은 `active_until` 뒤 만료되고 Discord 명령 답변은 `not_used` receipt로 완료 문맥을 유지한다.
- 손상 consent/heartbeat와 Control Page crash는 exact ACK·watchdog physical OFF로 닫힌다.
- Supervisor 복구는 목적별 최소 credential과 소유한 프로세스 handle만 사용한다.
- CI-equivalent 전체 3,216개(skip 22), Local Bridge continuity 74개, Mindcraft 56개,
  voice 639개(skip 5)와 구문 검사가 통과했다. 마이크·Discord·Minecraft·Docker는 기동하지 않았다.

## 작업 원칙

- 현재 상태와 목표 설계를 분리한다.
- 소스 테스트 통과를 live 검증으로 표현하지 않는다.
- Discord, 마이크, Minecraft, Docker 등 live 동작은 사용자 승인 범위에서만 수행한다.
- private transcript, token, 음성, screenshot, runtime artifact를 문서에 저장하지 않는다.

## 자세한 근거

- [[CURRENT_STATE]] — 구현·검증의 현재 사실
- [[ACTIVE_RISKS]] — 남은 위험과 검증 공백
- [[DOCUMENTATION_INDEX]] — 문서 권위와 탐색 경로
- [[02_DECISIONS]] — 지속할 결정과 근거
- [[worklog/2026-08-09]] — 당일 구현·검증 근거
