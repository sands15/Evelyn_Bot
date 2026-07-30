# Conversation Continuity Contract

Document status: **Current**
Last reviewed: 2026-07-30 KST

## Purpose

봇 프로세스가 재시작되더라도 직전 대화의 완료된 턴과 활성 후속 질문을 짧은
시간 동안 복구해 관계의 연속성을 보존한다. 이 체크포인트는 장기 기억이 아니며,
크래시 복구만을 위한 로컬·단기 상태다.

## Persistence boundary

`runtime_artifacts/conversation_continuity/active.json`은 다음 항목만 저장한다.

- 최대 32개 세션
- 세션당 최근 완료 이력 최대 12개
- 항목당 최대 2,000자
- 활성 대화의 남은 TTL, 사용자 ID, speaker, topic/turn ID, follow-up target

다음 항목은 저장하지 않는다.

- raw audio
- 부분 STT 및 아직 확정되지 않은 transcript
- system prompt
- stack trace, 예외 메시지, 파일시스템 경로

체크포인트 스키마는 `conversation_continuity.checkpoint.v1`이다. 기본 유효 시간은
15분이고 파일 상한은 1 MiB다. 만료·손상·스키마 불일치·크기 초과 파일은
복구하지 않고 즉시 폐기한다. 저장 실패 시 이전 체크포인트도 폐기해 초기화된
상태나 삭제된 세션이 다음 재시작에서 되살아나는 것을 막는다. Windows 파일
잠금으로 이전 파일을 즉시 unlink하지 못해도 status의 `checkpointRevokedAt`
이후보다 오래된 checkpoint는 다음 restore에서 거부한다.

## Restore and lifecycle

- 인스턴스 잠금을 획득한 프로세스만 체크포인트를 복구한다.
- 복구 시 현재 코드의 system prompt를 새로 삽입한다.
- monotonic clock 값 자체는 재사용하지 않고, 저장된 남은 TTL에서 실제 경과
  시간을 차감해 새 프로세스의 clock으로 변환한다.
- 1초 주기의 single-flight writer가 직접 변경된 세션 사전도 감지한다.
- 정상 restart, shutdown, process exit 전에 동기 flush를 시도한다.
- Discord guild 기억 초기화는 runtime state를 지운 직후 즉시 flush한다.

## Operational status

`runtime_artifacts/conversation_continuity/status.json`은
`conversation_continuity.status.v1` heartbeat를 제공한다. 이 파일은 payload나
대화문을 포함하지 않고 상태, 복구·저장 시각, 세션 수, 보존 정책, 고정 오류
코드 카운터만 포함한다.

Runtime Health의 `runtime_errors.summary.v1`에는
`conversationContinuity` owner가 추가된다. heartbeat가 5초를 넘으면 stale이며,
복구·저장 실패는 고정 코드와 예외 타입만 공개한다.

## Retention and deletion

- 정상 실행 중 체크포인트는 마지막 상태 변경 후 15분 안에서만 복구 가능하다.
- restore가 만료를 발견하면 즉시 삭제한다.
- 일반 runtime artifact retention은 방어적으로 1일 이상 된
  `conversation_continuity/active.json`도 삭제 대상으로 선택한다.
- 세션 store가 비면 체크포인트를 삭제한다.
- guild 기억 초기화는 해당 guild 세션을 제거한 결과를 즉시 저장한다.

이 계약은 영구 기억의 provenance·tombstone·permanent delete 계약을 대체하지
않는다.

## Verification

필수 테스트는 다음을 포함한다.

- 완료 턴 및 active follow-up의 fresh restart 복구
- 현재 system prompt 재삽입과 raw audio/부분 STT 제외
- 경과 시간에 따른 follow-up 만료
- stale·corrupt·oversized checkpoint 거부와 폐기
- 저장 실패 시 이전 파일 fail-closed 폐기
- unlink 실패 시 revocation marker로 이전 파일 복구 거부
- 빈 store 및 guild reset 후 즉시 체크포인트 갱신
- single-flight periodic writer와 직접 사전 변경 감지
- Runtime Errors의 privacy 및 stale/current-error 판정
