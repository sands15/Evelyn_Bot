# discord-ext-voice-recv PR #56 포팅 계획 (Evelyn)

목표: `discord-ext-voice-recv` PR #56의 안정화 아이디어를 `evelyn_voice/client.py` 커스텀 수신 경로에 단계적으로 옮겨서, 2026 Discord DAVE 수신 경로에서 발생하는 깨짐/unknown SSRC/drop 문제를 줄인다.

## 현재 상태 요약
- Evelyn은 라이브러리 PR을 직접 쓰는 구조가 아니라 `evelyn_voice/client.py`에 커스텀 DAVE 수신 경로가 있다.
- 이미 일부 아이디어는 들어가 있음:
  - pending inner packet retry
  - pending SSRC packet deque
  - DAVE passthrough / remap 시도
  - fake packet expansion, FEC/PLC
- 하지만 아직 부족한 부분이 있음:
  - unknown SSRC buffering이 retry 횟수 중심이라 늦은 매핑에 약함
  - anomaly logging이 interval 기반으로 정리돼 있지 않음
  - packet/decode 흐름이 분기별로 누적되어 초반 손상과 복구 판단이 복잡함

## 단계별 실행 계획

### 1단계. unknown SSRC buffering 보강
목표: SSRC 매핑이 늦게 들어와도 utterance를 너무 빨리 버리지 않기.

실행 항목:
- age 기반 unknown SSRC hold 도입
- pending SSRC packet prune 로직 추가
- interval-based unknown SSRC anomaly logging 추가
- retry 횟수보다 pending packet age를 우선하는 재큐잉 로직으로 변경

완료 기준:
- 늦은 SSRC 매핑 상황에서 utterance가 즉시 drop되지 않음
- 같은 unknown SSRC 경고가 로그를 과도하게 도배하지 않음

### 2단계. DAVE inner payload 안정 처리 정리
목표: inner decrypt 지연/실패 시 더 일관된 retry 및 remap 흐름 확보.

실행 항목:
- `_resolve_dave_audio_payload()`와 `_drain_pending_inner_packets()` 공통 reason 정리
- retryable / terminal reason 체계화
- candidate user remap 성공 시 SSRC binding과 backlog 회수 흐름 강화
- pending inner packet 진단 필드 보강

완료 기준:
- DAVE inner decrypt 실패 사유가 로그/메타에서 분명하게 보임
- remap 후 backlog 회수가 더 안정적으로 동작함

### 3단계. packet/decode 흐름 재정리
목표: too-short packet / fake packet / Opus fail / FEC / PLC / silence fill 처리 순서를 단순화.

실행 항목:
- decode 실패 경로를 helper 함수로 묶기
- 초반 손상 처리와 중간 손상 처리를 분리
- silence fill / PLC / FEC 적용 지점 일관화
- packet repair 통계 정리

완료 기준:
- 수신 흐름 분기가 줄고, 각 repair 경로가 명확해짐
- debug json과 콘솔 경고가 decode 흐름을 더 잘 설명함

### 4단계. 검증 및 튜닝
목표: 실제 Discord 음성 샘플로 false unstable / wake clipping / 깨짐 빈도 비교.

실행 항목:
- debug_audio 샘플 전후 비교
- unknown SSRC / inner decrypt / repair 카운트 비교
- 필요 시 threshold 미세조정

완료 기준:
- 억울한 drop 감소
- 앞부분 깨짐/skip 감소
- 로그 노이즈 증가 없이 원인 추적 가능

## 이번 턴 실행 범위
- 1단계부터 바로 적용 시작
