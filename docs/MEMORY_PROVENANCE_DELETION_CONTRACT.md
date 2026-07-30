# Memory Provenance And Deletion Contract

Document status: **Current**
Last reviewed: 2026-07-30 KST

이 문서는 Evelyn Memory Vault가 기억의 근거를 공개하고 사용자 삭제를
영구적으로 지키는 현재 런타임 계약을 정의한다.

## Provenance

Control Page의 memory card와 recall metadata는
`memory.provenance.v1`을 사용한다. 각 항목은 최소한 다음 정보를 갖는다.

- `noteId`, `source`, `sourceType`
- `sourceRefs`, `derivedFrom`
- `evidenceHashes`
- `confidence`

recall prompt에는 원문 전체 대신 선택된 기억의 note ID, source, evidence
요약과 confidence를 넣는다. 파생 기억은 원본 note ID와 evidence hash를
유지해야 한다.

## Two-step deletion

사용자 기억 삭제는 preview와 apply를 분리한다.

1. preview는 note의 현재 content hash에 묶인 일회용 confirm token을 발급한다.
2. token은 120초 뒤 만료된다.
3. apply는 note ID, memory root, content hash를 다시 검사한다.
4. 만료, 재사용, 대상 불일치, preview 뒤 내용 변경은 fail-closed로 거부한다.

bootstrap contract, legacy-managed source, internal note는 이 사용자 삭제
경로로 지울 수 없다.

## Tombstone-first durability

apply가 최종 내용을 검증한 뒤에는 다음 순서를 지킨다.

1. `memory.deletion.tombstone.v1` 한 줄을 journal에 append한다.
2. flush와 `fsync`가 끝난 뒤 source Markdown을 제거한다.
3. user state, SQLite/FTS/vector rows, recall cache, hot context를 정리한다.

tombstone은 삭제 권한의 내구성 있는 경계다. title, body, source path,
content hash를 저장하지 않고 note ID, note/source type, 정규화된 reason,
삭제 시각만 저장한다.

tombstone 기록 뒤 프로세스가 중단되어 source 파일이 남더라도 그 note는
논리적으로 삭제된 상태다. indexing, direct note lookup, user snapshot,
recall, supersede 경로는 tombstoned note를 노출하지 않는다. 다음 index sync는
남은 source와 user state를 재조정하고 파생 인덱스를 제거한다.

## Cache and daily-note continuity

hot context에는 deletion journal의 수정 시각과 크기를 함께 저장한다. 캐시와
현재 journal의 상태가 다르면 cached prompt는 빈 값으로 처리되어 삭제된 기억이
다시 주입되지 않는다. 다음 index sync는 stale hot-context와 prompt-block
파일도 제거한다.

현재 날짜의 daily note를 삭제한 뒤 새 대화가 생기면 같은 파일 경로를 다시
사용할 수 있지만 note ID는
`daily-YYYY-MM-DD-continuation-N`으로 바뀐다. 삭제된 원문은 복원하지 않고
삭제 이후의 새 대화만 새 identity에 기록한다.

## API result semantics

- 완전 정리: HTTP 200, `ok=true`, `deleted=true`, `tombstoned=true`
- tombstone은 내구성 있게 기록됐지만 파생 정리가 남음:
  HTTP 503, `ok=false`, `error=memory_delete_cleanup_required`,
  `tombstoned=true`, `cleanupErrors=[...]`
- tombstone 자체를 내구성 있게 기록하지 못함:
  HTTP 500, `error=memory_delete_failed`

`cleanup_required`에서도 조회와 prompt 경로는 tombstone을 기준으로
fail-closed한다. 운영자는 재시작 또는 index sync 뒤 잔여 파일과 인덱스가
정리됐는지 확인한다.

## Storage

- journal:
  `bot_memory/memory_index/memory_deletions.jsonl`
- user state:
  `bot_memory/memory_index/user_note_state.json`
- derived index:
  `bot_memory/memory_index/memory.sqlite`
- hot context:
  `bot_memory/memory_index/hot_context.json`

위 경로의 정확한 root는 실행 설정 또는 테스트 root에 따라 달라질 수 있다.

## Verification

`tests.memory.test_memory_deletion_restart`는 실제 별도 Python 프로세스에서
tombstone append의 `fsync` 직후 `os._exit(73)`을 실행한다. 이 시점에는
source Markdown, SQLite note/vector/FTS row, retrieval cache, hot-context,
prompt block과 user state가 의도적으로 남아 있다.

두 번째 새 Python 프로세스는 다음을 검증한다.

- 최초 direct detail과 hot-context read가 삭제 내용을 반환하지 않는다.
- snapshot/index sync가 남은 source, note/vector/FTS/graph row,
  retrieval cache, user state, hot-context와 prompt block을 제거한다.
- recall 결과에 삭제 title/body가 없고 동일 note ID 재생성이 차단된다.
- tombstone에는 title, body, path, content hash가 없다.
