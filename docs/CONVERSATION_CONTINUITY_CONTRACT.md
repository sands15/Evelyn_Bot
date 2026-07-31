# Conversation Continuity Contract

Document status: **Current**
Last reviewed: 2026-07-31 KST

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

체크포인트 스키마는 `conversation_continuity.checkpoint.v2`다. 기본 유효 시간은
15분이고 파일 상한은 1 MiB다. 만료·손상·스키마 불일치·크기 초과 파일은
복구하지 않고 즉시 폐기한다. 저장 실패 시 이전 체크포인트도 폐기해 초기화된
상태나 삭제된 세션이 다음 재시작에서 되살아나는 것을 막는다. Windows 파일
잠금으로 이전 파일을 즉시 unlink하지 못해도 status의 `checkpointRevokedAt`
이후보다 오래된 checkpoint는 다음 restore에서 거부한다.

v2 checkpoint는 1부터 증가하는 `generation`, 직전 checkpoint의
`previousHash`, `checkpointHash`를 제외한 canonical JSON의 SHA-256
`checkpointHash`를 포함한다. 대화문을 바꾸고 self-hash를 다시 계산하더라도
별도 head와 일치하지 않으면 복구하지 않는다.

`runtime_artifacts/conversation_continuity/checkpoint_head.json`은 최신
generation과 checkpoint hash를 고정하는 content-free durable head다. schema,
`active|empty`, generation, hash, 갱신 시각과 `contentFree=true`만 저장하고
대화문·사용자·guild/channel/message/session ID는 저장하지 않는다.

- checkpoint를 먼저 `fsync`·원자 교체하고 head를 durable 교체한다.
- checkpoint가 head보다 정확히 한 generation 앞서고 `previousHash`가 기존
  head와 일치하면 head 교체 직전 crash로만 판정해 head를 복구한다.
- 과거 generation rollback, 같은 generation의 다른 hash, active head 뒤
  checkpoint 삭제는 fail-closed한다.
- 빈 store는 먼저 `empty` head를 한 generation 전진시킨 뒤 checkpoint를
  삭제한다. unlink가 지연돼도 이전 대화가 복구되지 않는다.
- 기존 v1 checkpoint는 raw JSON 전체의 domain-separated SHA-256으로
  generation 0 head에 먼저 고정한다. 다음 상태 변경에서 v2 generation 1로
  연결한다.

hash/head는 우발적·비협조적 변조와 rollback을 탐지한다. checkpoint와 head를
함께 다시 쓸 수 있는 filesystem 관리자에 대한 keyed authenticity나 외부
불변 원장은 아니다.

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
경계를 내구성 있게 남긴다. head도 같은 durable atomic writer를 사용한다.

외부에 답변 전달이 완료된 턴은 1초 periodic writer를 기다리지 않는다.
`commit_completed_turn()` 또는 async wrapper가 즉시 강제 flush하고,
`state=active|empty`, `rollbackProtected=true`, 저장 세션 수와 generation을
검증한다. 검증 실패는 원문 예외 없이
`conversation_continuity_commit_failed`로 정규화한다.

전달과 기록 순서는 다음 계약을 따른다.

- Discord text는 텍스트 전송, 완료 상태 반영, durable commit 뒤 선택적 음성
  재생을 수행한다. 선택적 TTS가 실패해도 이미 전달된 텍스트는 남는다.
- Control Page 일반·검색 답변은 세션 완료 상태를 반영하고 durable commit한
  뒤 로컬 TTS를 예약한다.
- 검색 후속 답변은 Discord text 전달 직후 한 번만 history와 checkpoint를
  기록한다. 같은 답변의 선택적 voice가 실패해도 중복 기록하지 않는다.
- 자율 후속 답변과 Discord 명령 응답도 실제 전송·기록 뒤 즉시 commit한다.
- 음성 답변은 재생 완료 뒤 history, active session, room owner를 반영하고
  즉시 commit한다.

Discord message reference fallback도 delivery-at-most-once 경계를 따른다.

- message ID 변환 등 reference 생성이 네트워크 전송 전에 로컬에서 실패하면
  reference 없는 전송을 한 번 수행할 수 있다.
- Discord가 첫 reference 전송을 확실히 거부한 비모호 4xx 응답에서만
  reference 없는 전송을 한 번 수행한다.
- timeout, 연결 단절, 상태 없는 예외, 5xx와 `408|409|425|429`는 첫 전송이
  Discord에 수락됐는지 증명할 수 없다. 이 경우 wrapper는 일반 메시지로
  재전송하지 않고 원래 오류를 상위로 전달한다.
- 이 경계는 ambiguous failure에서 응답 하나를 잃을 가능성보다 같은 응답을
  두 번 전달해 관계 상태를 왜곡하는 위험을 우선 차단한다.

이미 외부에 전달된 뒤 commit이 실패하면 답변을 거짓으로 미전달 처리하거나
중복 전송하지 않는다. 대신 고정 오류 코드와 예외 타입만 관측하고 periodic
writer가 다시 저장을 시도한다.

## Restore and lifecycle

- 인스턴스 잠금을 획득한 프로세스만 체크포인트를 복구한다.
- 인스턴스 잠금이 checkpoint/head의 단일 writer 권한이다.
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
코드 카운터와 현재 guild revocation 개수만 포함한다. additive 상태 필드
`checkpointIntegrity`, `checkpointGeneration`, `checkpointHeadState`,
`rollbackProtected`가 현재 보호 상태를 공개한다.

Runtime Health의 `runtime_errors.summary.v1`에는
`conversationContinuity` owner가 추가된다. heartbeat가 5초를 넘으면 stale이며,
복구·저장 실패는 고정 코드와 예외 타입만 공개한다.

`status.json`의 additive `completedTurnCommit`은
`conversation_continuity.commit-metrics.v1`이다. 이 지표는 현재 프로세스에서
성공한 최근 256개 durable checkpoint/head commit의 last/p50/p95/max
밀리초와 누적 시도·성공·실패 횟수, 마지막 성공 여부만 보존한다. 대화문,
transcript, 사용자·guild/channel/message/session/turn ID, 경로와 예외
메시지는 저장하지 않는다.

- 성공 표본 20개 전에는 `idle|warming`이다.
- 20개 이후 p95가 100ms를 넘으면 `warning`과
  `conversation_continuity_commit_latency_high`를 공개한다.
- 경고는 대화 실패나 durable commit 실패가 아니며 첫 버전에서는
  관측 신호다.
- 마지막 commit 실패는 `error`와
  `conversation_continuity_commit_failed`로 표시하고 실패 지연은 성공
  percentile에 섞지 않는다.
- 표본은 재시작 뒤 복구하지 않는다. 오래된 프로세스의 stale 경고는 Runtime
  Errors의 현재 경고로 승격하지 않는다.
- Runtime Errors와 Control Page는 허용 필드만 다시 투영하며 알 수 없는
  nested 필드나 private 값을 전달하지 않는다.

## Retention and deletion

- 정상 실행 중 체크포인트는 마지막 상태 변경 후 15분 안에서만 복구 가능하다.
- restore가 만료를 발견하면 즉시 삭제한다.
- 일반 runtime artifact retention은 방어적으로 1일 이상 된
  `conversation_continuity/active.json`과 `checkpoint_head.json`도 삭제
  대상으로 선택한다.
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
- self-hash를 다시 계산한 valid JSON 내용 변조 거부
- 과거 generation rollback과 active head 뒤 checkpoint 삭제 거부
- checkpoint commit 뒤 head commit crash의 정확한 1-generation 복구
- v1 raw checkpoint anchoring과 다음 write의 v2 chain migration
- content-free head와 status integrity 공개 계약
- 저장 실패 시 이전 파일 fail-closed 폐기
- unlink 실패 시 revocation marker로 이전 파일 복구 거부
- 빈 store 및 guild reset 후 즉시 체크포인트 갱신
- anchor map이 없는 sparse guild state와 음성 merge record의 독립 제거
- guild revocation 기록 직후와 runtime clear 직후 `os._exit` 시 비복구
- 다른 guild의 checkpoint는 같은 crash 경계에서도 정상 복구
- revocation ledger의 content-free·corrupt fail-closed 계약
- single-flight periodic writer와 직접 사전 변경 감지
- Discord text 전달 뒤 선택적 TTS 실패 전 즉시 durable commit
- Discord reference의 로컬 생성 실패·확정 4xx fallback과
  timeout·5xx·상태 없는 ambiguous failure의 무재전송
- Control Page 일반·검색, 검색 후속, 자율 후속, Discord 명령과 음성 완료
  경로의 전달·기록·commit 순서
- commit 실패 시 중복 전송 없이 고정 오류 코드만 기록
- Runtime Errors의 privacy 및 stale/current-error 판정
- 완료 턴 commit의 20표본 warming/warning 판정, bounded percentile과
  실패 횟수
- Runtime Errors의 commit 지연 경고 투영, stale 비승격과 nested-field
  privacy
- Control Page의 읽기 전용 p50/p95·표본 수 표시와 JavaScript parse

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

`tests.discord_io.test_discord_text_turn`,
`tests.discord_io.test_discord_delivery`,
`tests.ui.test_control_page_text_runtime`,
`tests.ui.test_control_page_search_runtime`,
`tests.core.test_search_followup_runtime`,
`tests.core.test_autonomy_runtime_factory`,
`tests.discord_io.test_discord_command_session_runtime`,
`tests.voice.test_voice_reply_side_effects`는 각 전달 경로에서 commit의 정확한
순서와 선택적 TTS 실패 뒤 연속성 보존을 검증한다.
