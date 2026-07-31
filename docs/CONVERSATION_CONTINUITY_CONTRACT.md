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
- 세션당 최근 확정 이력 최대 12개
- 항목당 최대 2,000자
- 활성 대화의 남은 TTL, 사용자 ID, speaker, topic/turn ID, follow-up target

다음 항목은 저장하지 않는다.

- raw audio
- 부분 STT 및 아직 확정되지 않은 transcript
- system prompt
- stack trace, 예외 메시지, 파일시스템 경로

## Accepted voice turn delivery failure

최종 STT와 reply gate를 통과한 음성 발화는 답변 전달이 실패해도 사라진 턴으로
취급하지 않는다. Discord voice connection 부재, 빈 최종 답변, LLM/TTS 전달
실패 시 다음 경계를 적용한다.

- 사용자 발화만 history에 한 번 추가하고 존재하지 않는 assistant 답변은 만들지
  않는다. 따라서 복구된 history는 `user` row로 끝날 수 있다.
- 세션의 마지막 speaker를 `user`로 유지하고 room owner를 보존한 뒤 같은 durable
  continuity commit 계약으로 즉시 checkpoint한다.
- 같은 turn finalizer가 중복 호출돼도 process-local turn metrics의
  `unanswered_voice_turn_recorded` 표식으로 history mutation과 commit을 한 번만
  수행한다.
- 전달되지 않은 답변을 기억으로 쓰거나 cognitive/search follow-up을 시작하지
  않는다. 다음 정상 턴은 보존된 사용자 발화를 문맥으로 받아 관계를 이어간다.
- Main과 Fast Control의 최종 prompt 경계는 최근 non-empty conversational row가
  `user`이면 고정 `conversation.unanswered-user.v1` 규칙을 system context에
  추가한다. 이 규칙은 직전 발화가 전달된 assistant 답변 없이 남았음을 모델에
  알리고 현재 요청과 함께 필요에 따라 다루게 하며, 실제 사용자 문장은 복제하지
  않는다. 답변이 정상 전달되어 history가 `assistant`로 끝나면 규칙은 자동으로
  사라진다.
- `context_pipeline`과 `turn_summary.v1`에는 content-free boolean
  `unanswered_user_turn_context`만 남긴다. checkpoint 복구와 검증된 cross-surface
  merge 뒤에도 같은 history 기반 판정을 다시 수행하므로 별도 미응답 본문이나
  shadow state를 저장하지 않는다.
- 실패 메타데이터는 `voice_connection_unavailable`, `voice_delivery_empty`,
  `voice_delivery_failed`, `conversation_continuity_commit_failed` 같은 고정 코드와
  예외 클래스 이름만 사용한다. 예외 메시지·경로·토큰은 status와 turn summary에
  넣지 않는다.

이 checkpoint의 사용자 발화는 기존 단기 대화 continuity 범위 안의 확정
transcript다. 음성 검증 report/event에 transcript나 raw audio를 새로 복제하는
것은 계속 금지한다.

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

## Keyed authenticity and external monotonic anchors

`EVELYN_CONTINUITY_AUTH_KEY_FILE`을 설정하면 Main/Discord owner와 Fast Control
owner는 content-free checkpoint head를
`conversation_continuity.checkpoint-head.v2`로 기록하고 HMAC-SHA256으로
인증한다. cross-surface reader와 완료 턴 durable receipt도 같은 인증을 통과한
current head만 받아들인다. 서명에는 `conversation_continuity` 또는
`fast_control_continuity` owner scope도 포함되어 한 owner의 정상 서명본을 다른
owner 폴더로 복사하는 교차-owner replay를 거부한다.

같은 키는 서로 다른 HMAC domain으로 두 content-free 보조 저장소도 인증한다.

- `conversation_continuity.guild_revocations.v2`는 ledger 전체를 인증해 guild ID,
  철회 시각과 정책의 변경을 거부한다. 외부 앵커를 켜면 generation, previous
  hash, ledger hash가 추가된 v3로 승격된다. Main restore와 cross-surface
  reader가 같은 tag와 현재 앵커 위치를 검증한다.
- `fast_control.action-recovery-head.v2`는 recovery journal의 generation/hash를
  인증한다. journal과 일반 head hash를 함께 다시 계산해도 tag가 없으면
  `auth_error`가 된다.
- Action recovery 인증 오류는 자동 중단 안내나 acknowledgement를 기록하지
  않는다. 올바른 키로 재시작할 수 있도록 journal/head를 보존하고 background
  action 시작과 continuity commit을 막는다.

`EVELYN_CONTINUITY_AUTH_ANCHOR_DIR`도 설정하면 runtime artifact와 분리된 보호
디렉터리에 다음 네 content-free 단조 슬롯을 기록한다.

- Main/Discord checkpoint generation/hash
- Fast Control checkpoint generation/hash
- Main guild revocation ledger generation/hash
- Fast Action recovery journal generation/hash

각 슬롯은 `conversation_continuity.external-anchor.v1` HMAC record를 서로 다른
파일에 저장한다. record에는 slot, generation, artifact hash, 갱신 시각과
`contentFree=true`만 들어가며 대화문, ID, 경로, action 내용은 들어가지 않는다.
owner restore와 writer는 현재 artifact가 앵커와 정확히 같은지 검증한다. artifact
commit 뒤 앵커 commit 전에 죽은 경우에는 인증된 chain이 정확히 한 generation
앞서 있고 이전 hash가 현재 앵커와 연결될 때만 앵커를 전진시킨다. read-only
cross-surface reader는 lagging 앵커를 직접 승격하지 않는다.

따라서 보호된 앵커는 다음을 구분한다.

- 정상 현재 상태: generation/hash가 정확히 일치한다.
- 단일 commit crash: owner만 한 단계 앞선 chain을 복구하고 앵커를 전진시킨다.
- 이미 서명된 과거 artifact replay: 현재 앵커보다 뒤이므로 fail-closed한다.
- runtime continuity 폴더 전체 삭제: 남아 있는 앵커와 빈 generation 0이
  불일치하므로 새 빈 상태를 만들지 않는다.

- 키 파일은 최소 32바이트의 raw key 또는 `base64:` 접두사가 붙은 base64
  key여야 한다. 상대 경로, symlink, 8KiB 초과 파일과 repository 또는
  `runtime_artifacts` 보호 경계 내부의 키 파일은 거부한다.
- 키·키 경로·auth tag는 status나 오류 로그에 노출하지 않는다. 공개 상태에는
  `keyedAuthenticity`, `tamperEvident`, `externalAnchorConfigured`,
  `externalReplayProtected`, 각 anchor state와 고정 오류 코드만 남긴다.
- 앵커 경로도 절대 경로여야 하며 repository와 `runtime_artifacts` 밖에 미리
  생성된 실제 디렉터리여야 한다. symlink, 누락, 내부 경로는 거부한다. 키 없이
  앵커만 설정하는 구성도 거부한다.
- 서명된 v2 head를 키 없이 읽거나 잘못된 키/tag로 읽으면 fail-closed한다.
  이때 checkpoint/head 원본은 지우거나 새 상태로 덮지 않아 올바른 키로 다시
  시작할 수 있다.
- 기존 unsigned v1 head는 키를 설정했다는 이유만으로 자동 신뢰하지 않는다.
  운영자가 원본을 별도로 검토한 뒤 한 번만
  `EVELYN_CONTINUITY_AUTH_BOOTSTRAP=true`로 시작해야 v2로 승격된다. 승격 후에는
  즉시 `false`로 되돌린다. read-only cross-surface 경로는 bootstrap 상태를
  직접 신뢰하거나 승격하지 않는다.
- 기존 keyed head와 revocation v2에 외부 앵커를 처음 도입할 때도 같은 one-shot
  bootstrap이 필요하다. 운영자는 서비스를 모두 멈추고 현재 artifact를 검토·
  백업한 뒤 빈 외부 앵커 디렉터리로 한 번만 bootstrap한다. 모든 owner status의
  `checkpointAnchorState=verified`, `externalReplayProtected=true`와 Main의
  `guildRevocationsAnchorState=verified`를 확인한 뒤 즉시 bootstrap을 끈다.
- Docker에서는 base compose와 `docker-compose.continuity-auth.yml`을 함께
  사용한다. override는 호스트의 절대 키 경로를 `/run/secrets`에 read-only
  secret으로 마운트하고, 별도의 절대 앵커 디렉터리를
  `/var/lib/evelyn-continuity-anchor`에 read-write bind mount해 Bot API와
  Discord가 같은 키와 네 독립 슬롯을 사용하게 한다. 앵커 디렉터리는 두
  서비스 계정만 쓸 수 있도록 host ACL을 제한한다.

키만 켠 HMAC 경계는 공유 continuity 폴더의 관리자가 checkpoint/head,
guild-revocation ledger, action journal/head를 임의의 새 내용으로 다시 쓰는
공격을 탐지한다. 외부 앵커까지 켜면 runtime artifact 관리자가 이미 서명된 과거
세트를 replay하거나 continuity 폴더 전체를 지우는 공격도 탐지한다. 단, 이
보장은 외부 앵커 디렉터리가 artifact 공격자에게서 분리·보호된다는 신뢰 경계에
의존한다. 공격자가 앵커의 과거 사본까지 artifact와 함께 replay하거나 키를
사용할 수 있으면 로컬 파일만으로는 이를 구분할 수 없으며 TPM NV counter나
원격 append-only 원장이 다음 강화 단계다.

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
  전환한다. 외부 키를 켜면 journal/head 동시 임의 재작성은 인증에서 거부되며,
  외부 앵커를 함께 켜면 이미 서명된 과거 쌍의 replay와 두 파일 동시 삭제도
  현재 action anchor와 불일치해 거부된다.
- 공개 `actions.recovery` 상태는 pending/recovery count, 고정 오류 코드와
  generation/integrity/head 상태, `rollbackProtected`, `contentFree=true`,
  `rawText=false`, `automaticRetry=false`만 포함한다. 외부 키 사용 시 content-free
  `headAuthenticity`, `keyedAuthenticity`, `tamperEvident`도 포함하고, 외부 앵커
  사용 시 `anchorState`, `externalAnchorConfigured`,
  `externalReplayProtected`를 추가한다.

이 분리는 두 프로세스가 하나의 checkpoint를 경쟁해서 덮어쓰는 것을 막는
single-writer 경계다. surface 전환은 별도 mutation owner를 추가하지 않고
`cross_surface_continuity.py`의 read-only verifier가 양쪽 checkpoint를
검증한 뒤 다음 LLM request의 bounded recent context에서만 합친다.

- checkpoint v2 self-hash와 content-free head의 generation/hash가 정확히
  일치하는 current snapshot만 읽는다. writer가 복구할 수 있는 one-generation
  lag도 reader는 직접 수리하지 않고 거부한다. 외부 앵커가 설정된 경우 owner
  scope에 맞는 checkpoint anchor와 Main guild-revocation anchor도 정확히
  일치해야 한다.
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
대화문·사용자·guild/channel/message/session ID는 저장하지 않는다. 외부 키를
사용하는 v2 head에는 HMAC 알고리즘, content-free key ID와 auth tag가 추가된다.

- checkpoint를 먼저 `fsync`·원자 교체하고 head를 durable 교체한다.
- checkpoint가 head보다 정확히 한 generation 앞서고 `previousHash`가 기존
  head와 일치하면 head 교체 직전 crash로만 판정해 head를 복구한다.
- 과거 generation rollback, 같은 generation의 다른 hash, active head 뒤
  checkpoint 삭제는 fail-closed한다.
- 빈 store는 먼저 `empty` head를 한 generation 전진시킨 뒤 checkpoint를
  삭제한다. unlink가 지연돼도 이전 대화가 복구되지 않는다.
- 무키 모드의 기존 v1 checkpoint는 raw JSON 전체의 domain-separated
  SHA-256으로 generation 0 head에 먼저 고정한다. 외부 키를 켠 상태에서는
  검토 후 one-shot bootstrap을 명시해야만 signed head로 승격하며, 다음 상태
  변경에서 checkpoint v2 generation 1로 연결한다.

무키 hash/head는 우발적·비협조적 변조와 일반 rollback을 탐지한다. keyed head
v2는 checkpoint와 일반 head를 함께 임의 재작성하는 공격까지 탐지한다. 외부
단조 앵커를 함께 쓰면 보호된 앵커 기준의 서명 replay와 전체 artifact 삭제도
거부한다.

`runtime_artifacts/conversation_continuity/guild_revocations.json`은 길드 초기화가
체크포인트보다 먼저 내구성 있게 기록됐음을 나타내는 write-ahead ledger다.
무키 스키마는 `conversation_continuity.guild_revocations.v1`, 외부 키 사용
스키마는 HMAC 필드가 추가된 v2, 외부 앵커 사용 스키마는 hash chain이 추가된
v3이며 최근 길드 최대 256개의 숫자 ID와 철회 시각만 저장한다. 대화문, 사용자
ID, 채널 ID, 세션 키, 경로와 오류 메시지는
저장하지 않는다. ledger가 손상됐거나 schema·크기·파일 형식·인증 검사를
통과하지 못하면 기존 checkpoint 전체를 복구하지 않는 fail-closed 정책을
적용한다.

checkpoint 파일은 임시 파일에 JSON을 쓴 뒤 flush와 `fsync`를 완료하고
원자적으로 교체한다. 일반 heartbeat는 불필요한 디스크 동기화를 하지 않지만,
checkpoint 저장 실패로 발생한 revocation status는 `fsync`해 fail-closed
경계를 내구성 있게 남긴다. head도 같은 durable atomic writer를 사용한다.

외부에 답변 전달이 완료된 턴은 1초 periodic writer를 기다리지 않는다.
`commit_completed_turn(session_key, turn_id)` 또는 async wrapper가 즉시
강제 flush하고,
`state=active|empty`, `rollbackProtected=true`, 저장 세션 수와 generation을
검증한다. 검증 실패는 원문 예외 없이
`conversation_continuity_commit_failed`로 정규화한다.

commit 대상 session은 `maxSessions` 순위 밖이어도 이번 checkpoint에 반드시
포함한다. writer는 current head와 checkpoint를 다시 읽어 exact session과
turn ID가 실제 저장됐는지 확인한다. 자율 후속과 Discord 명령도 전달마다 새
turn ID를 먼저 발급한다. 다른 최신 session만 저장된 결과나 같은 session의
이전 turn은 이번 commit의 성공 증거가 아니다.

각 전달 surface는 commit callback이 예외 없이 반환됐다는 사실만으로 성공을
판정하지 않는다. 반환된 `conversation_continuity.status.v1`에서 다음 증거를
모두 exact type/value로 다시 검증한다.

- `state=ready`
- `rollbackProtected=true`
- `checkpointIntegrity=verified`
- `checkpointHeadState=current`
- 외부 앵커가 설정된 경우 `externalReplayProtected=true`
- 양수 `checkpointGeneration`과 `persistedSessionCount`
- `conversation_continuity.commit-metrics.v1`의 양수 시도·성공·표본 수와
  `lastSucceeded=true`, `lastTargetVerified=true`

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
`checkpointHeadAuthenticity`, `guildRevocationsAuthenticity`,
`keyedAuthenticity`, 두 content-free tamper-evidence boolean과
`rollbackProtected`가 현재 보호 상태를 공개한다.

Runtime Health의 `runtime_errors.summary.v1`에는
`conversationContinuity` owner가 추가된다. heartbeat가 5초를 넘으면 stale이며,
복구·저장 실패는 고정 코드와 예외 타입만 공개한다.

`status.json`의 additive `completedTurnCommit`은
`conversation_continuity.commit-metrics.v1`이다. 이 지표는 현재 프로세스에서
성공한 최근 256개 durable checkpoint/head commit의 last/p50/p95/max
밀리초와 누적 시도·성공·실패 횟수, 마지막 성공 및 대상 검증 여부만 보존한다. 대화문,
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
- signed revocation ledger의 guild/timestamp 위조 거부와 unsigned one-shot
  bootstrap, 실제 guild-reset crash/restart 인증 유지
- signed Fast Action journal/head 동시 hash 재작성의 `auth_error`, 원본 보존,
  자동 notice/ack 0회와 unsigned one-shot bootstrap
- signed Fast Action crash/restart의 중단 안내 1회, 자동 재실행 0회와
  `tamperEvident=true`
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
