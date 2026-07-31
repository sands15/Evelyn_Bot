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

split Docker의 standalone Bot API는 Discord/Main owner와 같은 파일을 동시에
쓰지 않는다. 대신 `runtime_artifacts/fast_control_continuity/`에 독립된
`active.json`, `checkpoint_head.json`, `status.json`을 두고 동일한 v2
hash-chain·rollback protection·exact receipt 검증을 사용한다.

- 보존 범위는 최대 30분, 최근 role/content 40개이며 system prompt와
  task/runtime secret은 저장하지 않는다.
- Bot API 시작 시 정상·고정 실패 턴과 background follow-up을 복구해
  Control Page chat과 다음 Main LLM request의 recent context에 다시 넣는다.
- 일반 JSON, NDJSON stream의 성공·고정 실패, background 완료·실패는
  completion 응답 전에 즉시 durable commit한다.
- tool planner 자체가 실패해도 일반·stream과 같은 고정 오류 및 commit
  경계를 통과한다.
- `fast_control.continuity-status.v1`은 state, generation, message count와
  고정 오류 코드만 공개하며 대화 내용은 포함하지 않는다.

Fast Control의 background 조사 작업은 시작 답변만 복구되고 실제
`asyncio.Task`는 재시작 뒤 복구할 수 없다. 이를 진행 중인 것처럼 남기거나
자동 재실행하지 않기 위해
`runtime_artifacts/fast_control_actions/recovery.json`에
`fast_control.action-recovery.v3` 표식을 durable 기록하고,
`recovery.head.json`의 content-free head가 현재 generation과 journal hash를
고정한다.

- 최대 40개의 `actionId`, `running|terminal_committing`, 시작 시각,
  action 시작 당시 Fast continuity generation, 예상 최종 generation과 journal
  generation/이전 hash/현재 hash만 저장한다. 사용자 요청, 시작·최종 답변,
  tool evidence, 오류 원문과 경로는 저장하지 않는다.
- background action의 시작 응답을 공개하거나 runner를 launch하기 전에
  `running` 표식을 먼저 `fsync`·원자 교체한다. 기록할 수 없으면
  “작업을 시작한다”는 응답을 만들지 않는다.
- 최종 성공·실패 답변은 Fast continuity owner의 단일 잠금 안에서 다음
  generation을 먼저 `terminal_committing`에 기록한 뒤 대화 checkpoint에
  commit한다. 정확한 durable receipt 뒤에만 action 표식을 제거한다.
- process가 checkpoint commit 뒤 표식 제거 전에 죽어도 새 owner의 current
  generation이 예상 값에 도달했고 continuity가 `durableReady=true`일 때만
  이미 전달된 결과로 인정해 조용히 정리한다.
- generation에 도달하지 못했거나 표식이 `running`이면 고정 중단 안내를
  완료 턴으로 한 번 durable commit한다. 부작용 중복을 막기 위해 원래 작업은
  자동 재시도하지 않는다.
- 재시작 뒤 마지막 문장이 같은 고정 안내여도 action 시작 generation보다
  현재 continuity generation이 실제로 클 때만 이번 action의 이미 전달된
  안내로 인정한다. 이전 action의 동일 안내 뒤 새 marker만 기록하고 죽은
  경우에는 새 중단 안내를 생략하지 않는다. 반대로 안내 commit 뒤 journal
  ack 전에 죽으면 더 큰 generation이 전달을 증명해 중복 안내를 만들지 않는다.
- action commit 실패 뒤 예상 generation을 `running`으로 되돌려 이후 일반
  대화 commit이 같은 번호를 사용해도 결과 전달로 오판하지 않는다. 이 되돌림
  자체를 기록할 수 없으면 일반 continuity generation 전진도 fail-closed한다.
- journal이 손상되면 새 background action을 시작하지 않는다. 재시작
  reconciliation은 원문 없는 고정 안내가 durable해진 뒤에만 손상 표식을
  빈 exact-schema 상태로 교체한다.
- journal은 먼저, head는 다음에 durable 교체한다. journal만 정확히 한
  generation 앞서고 `previousHash`가 현재 head를 가리키면 head 교체 직전
  crash로만 인정해 head를 복구한다. 최초 빈 generation 1 journal의 genesis
  연결도 같은 bootstrap crash 경계로 anchor한다. head가 생긴 뒤 journal
  삭제, 진행 표식 생성 뒤 head 삭제, self-hash 불일치, 과거 journal rollback과
  그 밖의 generation 불일치는 fail-closed한다.
- 기존 v1 exact-schema journal은 raw byte hash로 generation 0 head에 먼저
  고정한다. v2 진행 entry에는 action 시작 continuity generation이 없으므로
  오래된 안내를 재사용하지 않고 보수적으로 새 안내를 commit한 뒤 v3로
  전환한다. journal과 head를 함께 다시 쓰거나 함께 삭제할 수 있는 filesystem
  관리자는 이 로컬 증거 경계 밖이다.
- 공개 `actions.recovery` 상태는 pending/recovery count, 고정 오류 코드와
  generation/integrity/head 상태, `rollbackProtected`, `contentFree=true`,
  `rawText=false`, `automaticRetry=false`만 포함한다.

이 분리는 두 프로세스가 하나의 checkpoint를 경쟁해서 덮어쓰는 것을 막는
single-writer 경계다. surface 전환은 별도 mutation owner를 추가하지 않고
`cross_surface_continuity.py`의 read-only verifier가 양쪽 checkpoint를
검증한 뒤 다음 LLM request의 bounded recent context에서만 합친다.

- checkpoint v2 self-hash와 content-free head의 generation/hash가 정확히
  일치하는 current snapshot만 읽는다. writer가 복구할 수 있는 one-generation
  lag도 reader는 직접 수리하지 않고 거부한다.
- stale·future·expired·oversized·symlink·손상 파일, privacy policy 위반과
  손상된 guild revocation ledger는 fail-closed한다.
- Main checkpoint에서 Control Page로 가져올 session은 명시적으로 설정한
  Discord guild ID와 user ID가 모두 일치해야 한다. 반대 방향도 현재 Discord
  turn의 session key가 같은 personal scope일 때만 Fast Control 문맥을 읽는다.
- 현재 owner의 더 최신 empty boundary 또는 대상 scope가 없는 더 최신
  checkpoint는 reset 경계다. 그보다 오래된 다른 owner의 문맥을 다시 넣지
  않아 삭제 전 대화가 surface 전환으로 되살아나는 것을 막는다.
- 양 owner의 `savedAt`으로 owner chunk 순서를 정하고, 현재 user input과 인접
  중복을 제거한 뒤 Main의 최신 eligible session 한 개에서 기본 최근 8개만
  prompt에 넣는다. 원문은 새 artifact나 status에 복사하지 않는다.
- Fast Control의 tool planner와 Main LLM payload가 같은 merged context를
  사용한다. Main/Discord는 공통 `prepare_llm_messages` 진입점에서 합치므로
  text와 voice 응답이 같은 경계를 지난다.

교차 연결은 `CROSS_SURFACE_CONTINUITY_ENABLED=true`와 양의
`CROSS_SURFACE_CONTINUITY_GUILD_ID`,
`CROSS_SURFACE_CONTINUITY_USER_ID`가 모두 있어야 활성화된다. 하나라도 없으면
`cross_surface_scope_not_configured`로 fail-closed하며 기존 각 surface의
독립 history만 사용한다. `cross_surface_continuity.status.v1`은 owner state,
generation, message/session count와 고정 오류 코드만 공개하며 대화문·ID는
공개하지 않는다.

각 prompt 조립은 `cross_surface_continuity.merge.v1` 증거를 process
memory에서만 만든다. Main/Discord는 해당 턴의 metrics
`meta.cross_surface_continuity`에 exact-field 사본을 넣고, Fast Control은
`runtime.crossSurfaceContinuity.lastMerge`에 마지막 시도만 공개한다. 이
증거는 checkpoint나 별도 artifact에 저장하지 않는다.

- `state`는 `idle`, `disabled`, `scope_mismatch`, `local_only`,
  `reset_boundary`, `rejected`, `merged` 중 하나이며 `sourceSurface`는
  `main|fast_control`의 고정 값이다.
- 양 owner의 검증 상태와 generation, local/cross/output message count,
  owner chunk ordering, 갱신 시각, 병합 지연과 고정 reason code만 포함한다.
- `policy`는 항상 `contentFree=true`, `persisted=false`, `readOnly=true`다.
- 대화문, user/guild/session/turn ID, checkpoint hash·경로와 callback의
  임의 private 필드는 exact-field consumer projection에서 버린다.
- 현재 surface owner가 손상·변조로 `rejected`이면 상대 owner가 정상이어도
  교차 문맥을 넣지 않는다. 로컬 reset/delete 경계를 확인할 수 없는 상태에서
  상대 문맥을 되살리지 않기 위한 fail-closed 규칙이다.

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

각 전달 surface는 commit callback이 예외 없이 반환됐다는 사실만으로 성공을
판정하지 않는다. 반환된 `conversation_continuity.status.v1`에서 다음 증거를
모두 exact type/value로 다시 검증한다.

- `state=ready`
- `rollbackProtected=true`
- `checkpointIntegrity=verified`
- `checkpointHeadState=current`
- 양수 `checkpointGeneration`과 `persistedSessionCount`
- `conversation_continuity.commit-metrics.v1`의 양수 시도·성공·표본 수와
  `lastSucceeded=true`

검증 성공 후 소비자에게 남기는 최소 receipt는 다음과 같다.

```json
{
  "schema": "conversation_continuity.commit-receipt.v1",
  "durable": true,
  "generation": 7,
  "persistedSessionCount": 1
}
```

부분 status, legacy `generation` 필드, 잘못된 schema/type, lagging head,
rollback protection 누락, 이전 commit의 실패 지표는 모두 고정
`conversation_continuity_commit_failed`로 처리한다. callback의 임의 private
필드는 receipt, metrics, log에 복사하지 않는다.

전달과 기록 순서는 다음 계약을 따른다.

- Discord text는 텍스트 전송, 완료 상태 반영, durable commit 뒤 선택적 음성
  재생을 수행한다. 선택적 TTS가 실패해도 이미 전달된 텍스트는 남는다.
- Discord text 생성이 완료 전에 실패해 고정 `text_turn_failed` 응답을
  전달한 경우에도 그 전송 성공 뒤 실패 응답을 완료 턴으로 기록하고 같은
  durable commit을 수행한다. fallback 전송이 실패하거나 성공 여부가
  모호하면 history/checkpoint를 변경하지 않으며, 기록·commit 실패 때문에
  fallback을 다시 보내지 않는다.
- Control Page 일반·검색 답변은 세션 완료 상태를 반영하고 durable commit한
  뒤 로컬 TTS를 예약한다.
- 검색 후속 답변은 Discord text 전달 직후 한 번만 history와 checkpoint를
  기록한다. 같은 답변의 선택적 voice가 실패해도 중복 기록하지 않는다.
- 자율 후속 답변과 Discord 명령 응답도 실제 전송·기록 뒤 즉시 commit한다.
  Discord 명령은 composition이 주입한 단일 context owner가 성공한 plain-text
  `ctx.send()`를 가로채므로 도움말·상태·접두사·자율 제어·채널 설정·초기화,
  Minecraft와 권한 거부 응답이 모두 같은 경계를 통과한다.
- Discord 명령 전송 자체가 실패하면 history와 checkpoint를 변경하지 않는다.
  전송 성공 뒤 continuity 기록이 실패해도 이미 전달된 응답을 재전송하거나
  command 실패로 바꾸지 않고 고정 event와 exception type만 기록한다.
  Minecraft handler의 이전 수동 기록은 제거해 응답당 기록·commit을 한 번으로
  제한한다.
- 음성 답변은 재생 완료 뒤 history, active session, room owner를 반영하고
  즉시 commit한다.
- Discord text/command, Control Page 일반·검색, 검색 후속, 자율 후속과
  음성 완료는 모두 같은 receipt validator를 사용한다. 자율 후속의 generation
  역시 owner의 `checkpointGeneration`에서만 가져온다.
- split Fast Control 일반·stream·background 응답도 같은 receipt validator를
  사용하되 별도 single-writer checkpoint에 기록한다.

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
- 전달 surface의 receipt 검증 실패는 이미 전달된 턴을 다시 보내지 않지만,
  해당 surface의 `continuity_commit`을 `failed`로 남긴다.
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
- 실제 owner status가 exact minimal receipt로 축약되는지 검증
- 부분·legacy·손상 status와 이전 실패 metric의 durable 성공 오판 방지
- Discord text/command, Control Page 일반·검색, 검색 후속, 자율 후속,
  음성 완료의 동일 receipt 판정
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
- Discord text의 전달된 고정 실패 턴 commit, fallback 전송 실패 시
  무기록, 기록 실패 시 무재전송
- Discord reference의 로컬 생성 실패·확정 4xx fallback과
  timeout·5xx·상태 없는 ambiguous failure의 무재전송
- Control Page 일반·검색, 검색 후속, 자율 후속, Discord 명령과 음성 완료
  경로의 전달·기록·commit 순서
- Discord 명령 19개와 권한 거부 응답의 단일 post-delivery owner,
  전송 실패 시 무기록, Minecraft 중복 commit 방지
- commit 실패 시 중복 전송 없이 고정 오류 코드만 기록
- Runtime Errors의 privacy 및 stale/current-error 판정
- 완료 턴 commit의 20표본 warming/warning 판정, bounded percentile과
  실패 횟수
- Runtime Errors의 commit 지연 경고 투영, stale 비승격과 nested-field
  privacy
- Control Page의 읽기 전용 p50/p95·표본 수 표시와 JavaScript parse
- Fast Control 정상·실패·planner 실패·stream·background follow-up의
  commit과 fresh-process 복구, LLM recent context 재주입
- Fast Control background action의 durable 시작 표식, continuity generation
  결합, 정상 완료 뒤 제거, commit 실패 generation 재사용 방지와 실제
  `os._exit` 재시작 중단 안내
- 손상·쓰기 실패 action recovery journal의 fail-closed 시작 차단,
  content-free 공개 상태와 자동 재시도 금지
- action recovery v2의 빈 chain 초기화, journal/head 삭제, 과거 journal
  rollback, self-hash, one-generation head 지연 복구와 v1 anchor/migration
- action recovery v3의 시작 continuity generation 결합, 이전 동일 안내의
  새 action 오인 방지, 안내 commit 뒤 ack crash의 무중복 복구와 v2 migration
- read-only cross-surface reader의 current hash/head 검증, 변조·lagging
  head·symlink·stale·손상 revocation 거부와 무변경 파일 증거
- Discord guild/user exact scope, 다른 member/server 제외와 content-free
  status
- owner `savedAt` 순서, 현재 input 제거, bounded merge와 양방향 Main/Fast
  prompt 주입
- 더 최신 empty/reset boundary가 다른 owner의 오래된 대화를 되살리지 않는지
  검증
- 현재 owner가 거부된 경우 정상인 상대 문맥도 주입하지 않는 fail-closed
  경계
- Main 턴 metrics와 Fast `lastMerge`의 exact-field, content-free,
  process-local 증거 및 임의 private 필드 제거

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
