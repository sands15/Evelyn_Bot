---
tags:
  - evelyn
  - working-context
type: current-context
last_reviewed: 2026-08-08
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
- 실제 Minecraft 승인 행동과 결과 증거 검증

## 최근 확인

- 2026-08-08 기본 `tts:8880`을 실제 `k2-fsa/OmniVoice` container로 교체했다.
  recipe `7cfc51e96088`, source revision `485c81d`, 서버 Python 20개와 model snapshot
  13개 SHA-256, read-only profile/cache, exact model ID/revision health가 모두 통과했다.
  실제 Evelyn clone sentence stream은 24 kHz mono 16-bit PCM 101,280 bytes를
  728 ms에 반환했다. profile API는 `ref_text`를 숨겼고 운영 로그에서도 합성 원문,
  경로, session/turn 식별자가 검출되지 않았다. PCM은 메모리에서 검사 후 폐기했다.
  사용자 스피커 청취와 local/Discord 10-turn·무음 E2E는 아직 남아 있다.
- 현재 저장소의 revision-gated launcher로 Docker local core와 Windows Host
  Supervisor/Local I/O Bridge를 실제 기동했다. Bot API, Control Page, LLM 3개,
  당시 VoxCPM 기반 TTS, STT, Vision ingress/runtime가 모두 healthy이며 공식 runtime checker가
  Local Bridge 포함 전체 필수 항목을 통과했다.
- 오래된 `C:\Evelyn` Compose에는 `vision_runtime`이 없어 현재 Vision ingress와
  결합하면 DNS/502가 반복됐다. 현재 저장소 Compose로 재생성해 ingress와 runtime을
  함께 띄웠고 Vision health가 통과했다.
- Windows venv launcher PID와 실제 Bridge PID가 달라 Supervisor readiness가
  실패하던 문제를 실제 base Python 직접 소유 방식으로 수정했다. Supervisor가
  소유한 PID와 서명 상태의 Bridge PID가 일치하고 TTS warmup도 완료됐다.
- 로컬 기본 재빌드에서 비활성 Discord 이미지의 대형 의존성을 제외했으며,
  명시적으로 Discord를 유지할 때만 해당 이미지를 빌드한다.
- Local Voice는 단일 capture owner, durable reservation/claim, capture-consent fence와
  cross-process attempt lease로 재시작·경쟁·중복 실행을 fail-closed 처리한다.
- validation LLM은 memory/history/tool 없이 격리되고 assistant 원문을 일반
  history/replay에 남기지 않는다.
- 손상·누락·역전된 consent/heartbeat와 Control Page hard-crash는 exact ACK,
  서명 상태와 watchdog physical OFF로 닫힌다.
- Supervisor 복구는 목적별 최소 credential과 소유한 프로세스 handle만 사용한다.
- 관련 CI-equivalent 전체 discover 3044개(skip 20), 최종 hardening 묶음 267개
  (skip 1)가 통과했다.
  `compileall`, `pip check`, JS 구문, Compose config와 diff check도 통과했다. 실제
  마이크·스피커·Discord live 검증은 아직 수행하지 않았다. 현재 마이크는 동의
  경계를 유지해 OFF이며 Discord와 Minecraft도 기동하지 않았다.

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
- [[worklog/2026-08-08]] — OmniVoice 실제 전환과 live 합성 근거

## 다음 작업 종료 시

- 현재 초점·차단점·다음 행동이 달라졌을 때만 이 문서를 짧게 갱신한다.
- 상세 결과는 `worklog/YYYY-MM-DD.md`에 기록하고 여기에는 링크만 남긴다.
