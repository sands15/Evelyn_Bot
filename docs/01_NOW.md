---
tags:
  - evelyn
  - working-context
type: current-context
last_reviewed: 2026-08-12
---

# Evelyn — Now

Codex가 작업 시작 시 읽는 작은 작업 문맥이다. 상세 사실은 링크된 권위 문서와
코드·테스트에서 필요한 구간만 검색한다. 이 파일은 80줄 이하로 유지한다.

## 프로젝트 목표

로컬 우선 개인 비서 이블린을 자연스러운 대화, 음성, 근거 있는 장기 기억,
안전한 도구·Minecraft 자율행동을 갖춘 안정적인 런타임으로 완성한다.

## 현재 초점

- 실사용 로그 기반 오류 수정, Control Page Discord 모드 OFF/ON과 OmniVoice 실제 청취 검증
- 로컬 마이크 동의 후 실제 장치 barge-in 연속성 검증
- Discord 음성의 실제 채널 E2E 검증
- Conversation Continuity와 local/Discord 음성의 장애·재시작 실환경 검증

## 최근 확인

- 2026-08-09 Mindcraft history는 bounded process-local·no-mount이며 Codex는 off다.
  Node LLM은 authenticated Bot API broker만 쓰고 서버가 fixed local/router를 선택한다. Minecraft lease 위임은 exact nonnegative JSON integer `guildId`만 받는다.
  broker는 core exposure를 frame consumer의 exact `delivered|discarded` ACK까지, generation fence는 final route/action sink까지 유지한다.
  recovery step은 exact history snapshot의 process-local one-shot issuance만 소비하고, 손상 world-effect policy는 validation 예외나 false-ready 대신 fixed blocker로 닫힌다. Disconnect/kick reason과 bot error event도 분류에만 쓰고 output에는 고정 문구만 남긴다. Player chat/whisper는 empty `only_chat_with`에서 차단되고 exact configured name만 허용하며 self-prompt/system autonomous path는 독립적이다. Protocol `PartialReadError`도 전역으로 삼키지 않고 표준 listener dispatch를 유지한다. 실행 중 `/start`는 goal·effect binding을 재표기하지 않으며 malformed-packet와 goal 전환의 live 검증은 남아 있다.
  Minecraft Autonomy plan은 current grant의 연속 prefix만 만들고, `자율시작`은 기존 cleanup→(route intent가 있으면 재연결·검증)→grant→start 순서를 지키며 world-action admission은 disconnect까지 직렬화된다. `자율정지`는 intent를 보존하고 exact current outcome fsync 뒤에만 cursor를 진행한다. durable bound-receipt history, legacy cleanup과 live 검증은 남아 있다.
- 2026-08-08~09 memory 삭제는 Busy fallback과 2초 admission을 유지한다. 적용된
  direct·cascade source는 durable redaction 성공 뒤에만 unlink하고, applied-cleanup 503은 강제 재조회 뒤 자동 재시도하지 않으며 손상 full receipt는 `unattributed`로 강등한다.
  replica 검증은 통과했지만 host ACL·Docker mount, live busy 전이와 rotation은 P1이다.
- 2026-08-08 필수 provenance가 손상된 recall이 정상 pinned note ID를 빌려
  `attributed`가 되던 경로를 cache·receipt 공용 검사와 전체 prompt 보류로 닫았다.
- 2026-08-08 Control Page의 transient degraded 화면 덮기와 stale poll 경쟁을
  ready latch·채팅 보존·single-flight/generation fence로 막았다(UI 176·집중 60 통과, live GET 일치). 빈 Fast Control 채팅은 `Evelyn`의 한국어 환영 인사로 시작하고, 저장 기억 미요청 응답은 exact `not_used` receipt로 음성·텍스트를 함께 보존한다.
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
  통과했다. Bridge는 mic OFF, output ready, clone warmup 564.1 ms, error/play count 0이었다. 시작 실패는 고정 `EVL-START-NNNN` 코드·조치와 content-free latest log로 표시하며 user-owned `docs/99_PROJECT_INBOX.md`는 source-dirty 검사에서 제외한다.
- 오래된 `C:\Evelyn` Compose에는 `vision_runtime`이 없어 현재 Vision ingress와
  결합하면 DNS/502가 반복됐다. 현재 저장소 Compose로 재생성해 ingress와 runtime을
  함께 띄웠고 Vision health가 통과했다.
- Windows venv launcher PID와 실제 Bridge PID가 달라 Supervisor readiness가
  실패하던 문제를 실제 base Python 직접 소유 방식으로 수정했다. Supervisor가
  소유한 PID와 서명 상태의 Bridge PID가 일치하고 TTS warmup도 완료됐다.
- Local Voice는 단일 capture owner, durable claim·consent fence로 재시작 경쟁을 막고, TTS 중 첫 threshold 후보의 playback generation을 flush까지 보존한다. 같은 generation이 해제된 tail은 새 owner·validation이 없고 선두 `이블린`이 확인될 때만 기존 admission으로 보낸다. `/mic on`은 오른쪽 drawer의 검증·청취 동의 버튼을 표시·포커스한다.
  Local Bridge 재생 실패는 user-only로 잇고 Discord partial checkpoint는 assistant/receipt/state만 복구해 TTL을 보존한다.
- validation GET은 현재 consent를 반환하고 local mic capture·speaker verification probe/enrollment·Opus/STT startup·LLM warmup body·Control Page welcome LLM non-200 body·Bridge·TTS warmup/control·Control Page server-start·router/tool-router, Discord playback trace·voice connect retry·voice validation observer·voice last-channel state save·Minecraft snapshot, cognitive refresh·memory mirror·summary·proactive question promotion·vault maintenance·self-identity queue와 Main/Fast failed-tool·vision metrics/watch는 예외 원문을 가린다. Runtime Error 관측은 Fast Control continuity를 포함하고 기록된 예외와 필수 service probe 장애를 구분한다. Optional payload-less 실패는 desired-state 부재로 health에만 남으며 backend 반영은 재시작 대기다.
  Discord stale voice client는 강제 정리 뒤 표준 연결로 복구하고, 검색 복구·playback timeout·무재생 거부·late-turn fence가 stale 완료를 막는다.
- validation LLM은 memory/history/tool 없이 격리되고 원문은 일반 history에 남지 않는다. awaiting 세션은 `active_until` 뒤 만료되고 Discord 명령 답변은 `not_used` receipt로 완료 문맥을 유지한다.
- 손상 consent/heartbeat와 Control Page crash는 exact ACK·watchdog physical OFF로 닫힌다.
- Supervisor 복구는 목적별 최소 credential과 소유한 process handle만 사용하며 Control Page의 Discord 토글은 core를 유지한 채 `discord_bot`만 전환한다.
- Main↔Fast continuity는 선택 session 활동시각으로 ordering·stale·revocation/reset을 판정해 무관한 session commit의 재정렬·부활을 막고 선택 대상의 누락 metadata는 fail-closed한다. CI-equivalent 전체 3,262개(skip 22),
  continuity 인접 276개(skip 3), voice 647개(skip 5)와 구문 검사가 통과했다. 마이크·Discord·Minecraft·Docker는 기동하지 않았다.

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
- [[worklog/2026-08-12]] — 당일 구현·검증 근거
