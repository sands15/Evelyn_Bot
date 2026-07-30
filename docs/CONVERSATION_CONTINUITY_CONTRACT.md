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

`runtime_artifacts/conversation_continuity/guild_revocations.json`은 길드 초기화가
체크포인트보다 먼저 내구성 있게 기록됐음을 나타내는 write-ahead ledger다.
스키마는 `conversation_continuity.guild_revocations.v1`이며 최근 길드 최대
256개의 숫자 ID와 철회 시각만 저장한다. 대화문, 사용자 ID, 채널 ID, 세션 키,
경로와 오류 메시지는 저장하지 않는다. ledger가 손상됐거나 schema·크기·파일
형식 검사를 통과하지 못하면 기존 checkpoint 전체를 복구하지 않는 fail-closed
정책을 적용한다.

checkpoint 파일은 임시 파일에 JSON을 쓴 뒤 flush와 `fsync`를 완료하고
원자적으로 교체한다. 일반 heartbeat는 불필요한 디스크 동기화를 하지 않지만,
checkpoint 저장 실패로 발생한 revocation status는 `fsync`해 fail-closed
경계를 내구성 있게 남긴다.

## Restore and lifecycle

- 인스턴스 잠금을 획득한 프로세스만 체크포인트를 복구한다.
- 복구 시 현재 코드의 system prompt를 새로 삽입한다.
- monotonic clock 값 자체는 재사용하지 않고, 저장된 남은 TTL에서 실제 경과
  시간을 차감해 새 프로세스의 clock으로 변환한다.
- 1초 주기의 single-flight writer가 직접 변경된 세션 사전도 감지한다.
- 정상 restart, shutdown, process exit 전에 동기 flush를 시도한다.
- Discord guild 기억 초기화는 checkpoint owner의 단일 잠금 안에서 처리한다.
  먼저 guild revocation을 durable ledger에 기록하고, 모든 guild-prefixed
  runtime map을 각각 지운 뒤 checkpoint를 강제 저장한다. 새 checkpoint가
  내구성 있게 교체된 다음에만 revocation을 제거한다.
- marker 기록 전 실패하면 runtime state를 지우지 않는다. marker 기록 뒤
  runtime reset 또는 checkpoint 저장 도중 프로세스가 종료되면 다음
  restore가 이전 checkpoint에서 그 guild만 제외한다.

## Operational status

`runtime_artifacts/conversation_continuity/status.json`은
`conversation_continuity.status.v1` heartbeat를 제공한다. 이 파일은 payload나
대화문을 포함하지 않고 상태, 복구·저장 시각, 세션 수, 보존 정책, 고정 오류
코드 카운터와 현재 guild revocation 개수만 포함한다.

Runtime Health의 `runtime_errors.summary.v1`에는
`conversationContinuity` owner가 추가된다. heartbeat가 5초를 넘으면 stale이며,
복구·저장 실패는 고정 코드와 예외 타입만 공개한다.

## Retention and deletion

- 정상 실행 중 체크포인트는 마지막 상태 변경 후 15분 안에서만 복구 가능하다.
- restore가 만료를 발견하면 즉시 삭제한다.
- 일반 runtime artifact retention은 방어적으로 1일 이상 된
  `conversation_continuity/active.json`도 삭제 대상으로 선택한다.
- 세션 store가 비면 체크포인트를 삭제한다.
- guild revocation ledger는 history가 아니라 bounded active metadata다. 정상
  초기화가 끝나면 해당 marker를 제거하며, 중단된 초기화의 marker만 안전한
  checkpoint 교체가 끝날 때까지 유지한다.
- guild 기억 초기화는 해당 guild의 모든 sparse runtime state를 제거한 결과를
  즉시 저장한다.

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
- anchor map이 없는 sparse guild state와 음성 merge record의 독립 제거
- guild revocation 기록 직후와 runtime clear 직후 `os._exit` 시 비복구
- 다른 guild의 checkpoint는 같은 crash 경계에서도 정상 복구
- revocation ledger의 content-free·corrupt fail-closed 계약
- single-flight periodic writer와 직접 사전 변경 감지
- Runtime Errors의 privacy 및 stale/current-error 판정

`tests.core.test_session_continuity_restart`는 periodic writer가 실제
checkpoint를 만든 뒤 첫 Python 프로세스를 `os._exit(74)`로 종료한다. 두 번째
새 Python 프로세스는 완료 턴, active follow-up TTL, user ownership, speaker,
topic/turn ID와 reply target을 복구하고, 현재 system prompt를 새로 삽입하며
부분 STT와 이전 system prompt가 남지 않는지 검증한다.

`tests.core.test_session_continuity_guild_reset_restart`는 두 개의 독립 Python
프로세스를 사용한다. 첫 프로세스를 durable marker 직후 또는 runtime clear
직후 `os._exit`로 종료하고, 두 번째 프로세스가 초기화 대상 guild를 복구하지
않으면서 다른 guild의 완료 턴과 active follow-up은 보존하는지 검증한다.

`tests.runtime.test_runtime_startup_integration`의 opt-in real-main 시나리오는
설정된 `EVELYN_RUNTIME_ARTIFACTS_DIR`에 합성 checkpoint를 만든 뒤 실제
`main.py`를 기동하고 강제 종료한 다음 같은 artifact root로 다시 기동한다.
두 main 인스턴스가 각각 restore를 보고해야 하며 repository의 기본
`runtime_artifacts` checkpoint는 변경하면 안 된다. CI는
`EVELYN_RUN_REAL_MAIN_INTEGRATION=1`로 이 시나리오를 실행한다.
