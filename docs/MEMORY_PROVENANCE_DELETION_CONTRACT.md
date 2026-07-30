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

## Optimistic edit and correction

Control Page에서 기억을 수정할 때는 마지막으로 읽은 card의 `sourceHash`를
`expectedContentHash`로 함께 보낸다.

1. edit API는 64자리 현재 content hash가 없으면 요청을 거부한다.
2. 저장 직전에 note를 다시 읽고 hash를 비교한다.
3. 다른 탭, Obsidian, 다른 요청이 먼저 수정했다면 HTTP 409와
   `memory_note_changed_since_read`를 반환하며 최신 파일을 덮지 않는다.
4. 편집 파일은 같은 디렉터리의 임시 파일에 UTF-8로 쓰고 flush·`fsync`한
   뒤 `os.replace`로 원자 교체한다.

사용자 수정은 기존 자동 생성 근거를 현재 내용의 근거인 것처럼 유지하지 않는다.
편집된 front matter와 `memory.provenance.v1`은 다음을 기록한다.

- 현재 `source=user-edit`, `sourceType=user`
- 현재 본문과 제목으로 계산한 새 SHA-256 `evidenceHashes`
- `sourceRefs=[control-page-memory-editor]`
- 증가하는 `revision`
- 최초 `originSource`, `originSourceRefs`
- 이전 evidence hash를 현재 근거와 분리한
  `revisedFromEvidenceHashes`
- `confidence=high`, `userEditedAt`

SQLite/FTS/vector/retrieval cache는 schema version 6으로 다시 동기화한다.
인덱스나 user-state 후처리가 실패하면 편집을 완전 성공으로 보고하지 않고
HTTP 503, `memory_edit_cleanup_required`, `edited=true`를 반환한다.
원자 파일 교체 자체가 실패하면 HTTP 500, `memory_edit_failed`,
`edited=false`다.

## Two-step deletion

사용자 기억 삭제는 preview와 apply를 분리한다.

1. preview는 note의 현재 content hash에 묶인 일회용 confirm token을 발급한다.
2. token은 120초 뒤 만료된다.
3. apply는 note ID, memory root, content hash를 다시 검사한다.
4. 만료, 재사용, 대상 불일치, preview 뒤 내용 변경은 fail-closed로 거부한다.

bootstrap contract, legacy-managed source, internal note는 이 사용자 삭제
경로로 지울 수 없다.

preview는 선택한 note뿐 아니라 `derivedFrom` 그래프를 함께 고정한다.

- 유일한 근거를 잃어 연쇄 철회될 note 목록
- 살아 있는 다른 근거가 있어 격리될 note 목록
- 각 격리 note의 철회·차단·잔존 source note ID
- 전체 영향 그래프의 SHA-256 fingerprint

apply 직전에 source content hash와 영향 fingerprint를 모두 다시 계산한다.
preview 뒤 파생 note나 근거 관계가 바뀌면 HTTP 409,
`memory_derivation_impact_changed_since_preview`로 아무것도 삭제하지 않는다.

## Tombstone-first durability

apply가 최종 내용을 검증한 뒤에는 다음 순서를 지킨다.

1. `memory.deletion.tombstone.v1` 한 줄을 journal에 append한다.
2. flush와 `fsync`가 끝난 뒤 source Markdown을 제거한다.
3. user state, SQLite/FTS/vector rows, recall cache, hot context를 정리한다.
4. `derivedFrom`을 따라 파생 기억을 연쇄 철회 또는 격리한다.

tombstone은 삭제 권한의 내구성 있는 경계다. title, body, source path,
content hash를 저장하지 않고 note ID, note/source type, 정규화된 reason,
삭제 시각만 저장한다.

tombstone 기록 뒤 프로세스가 중단되어 source 파일이 남더라도 그 note는
논리적으로 삭제된 상태다. indexing, direct note lookup, user snapshot,
recall, supersede 경로는 tombstoned note를 노출하지 않는다. 다음 index sync는
남은 source와 user state를 재조정하고 파생 인덱스를 제거한다.

## Derived-memory partial revocation

파생 기억은 직접 source note ID를 `derived_from`에 기록한다. 삭제 journal의
note ID가 이 그래프에 나타나면 다음 규칙을 재시작 가능한 결정론적 순서로
적용한다.

1. 모든 직접 근거가 tombstoned 상태면 해당 파생 note에도
   `reason=source_revoked`인 content-free tombstone을 append한다.
2. 하나 이상의 살아 있는 근거가 남으면 원문 Markdown은 사용자 검토용으로
   보존하지만 `memory.derivation.revocations.v1`에 quarantine 상태를 기록한다.
3. quarantine note를 근거로 하는 하위 파생 note도 전파 quarantine한다.
4. quarantine note는 SQLite, FTS, vector, graph, retrieval cache, hot context와
   prompt block에 들어가지 않는다. Control Page 관리 카드만 본문과 철회·잔존
   근거 ID를 표시한다.
5. quarantine 상태에서는 확인(confirm)을 거부한다. 사용자가 직접 수정하면
   현재 source는 `user-edit`가 되고 과거 derived relation은
   `originDerivedFrom`으로 이동해 직접 교정 근거로 격리를 해제한다.

root tombstone append 직후 프로세스가 죽어도 다음 recall/index sync가 source
파일을 다시 스캔해 동일한 연쇄 철회와 quarantine을 재구성한다. quarantine
상태 파일에는 title, body, path나 대화 원문을 저장하지 않는다.

## Privacy-preserving recomposition

Sub-LLM이 준비되면 maintenance/activation 경로가 quarantine note를
`memory.derivation.recomposition.v1` 계약으로 재합성한다.

- 삭제된 source와 quarantine source는 입력에서 제외한다.
- 기존 파생 note의 title/body도 모델 입력으로 재사용하지 않는다.
- 현재 살아 있는 source note만 ID, type, title, body, content hash와 함께
  로컬 Sub-LLM에 전달한다.
- 응답은 새 title/body/tags/links/confidence만 받는다.
- LLM 처리 중 target이나 source hash가 바뀌면 결과를 쓰지 않는다.
- 성공 시 `source=sub-llm-partial-recomposition`, 살아 있는
  `derived_from`, 새 evidence hash, 증가한 revision과
  `revocation_resolved_at`을 원자적으로 기록한다.

Sub-LLM이 없거나 상위 quarantine source가 아직 복구되지 않았으면 자동으로
내용을 만들지 않고 quarantine을 계속 유지한다.

## Cache and daily-note continuity

hot context에는 deletion journal의 수정 시각과 크기를 함께 저장한다. 캐시와
현재 journal의 상태가 다르면 cached prompt는 빈 값으로 처리되어 삭제된 기억이
다시 주입되지 않는다. derivation revocation 파일의 수정 시각과 크기도 같은
방식으로 묶는다. 다음 index sync는 stale hot-context와 prompt-block 파일도
제거한다. 새 hot-context와 prompt block은 원자 파일 교체로 갱신한다.

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
- preview 뒤 파생 영향 그래프 변경:
  HTTP 409, `memory_derivation_impact_changed_since_preview`, 삭제 없음

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
- derived revocation state:
  `bot_memory/memory_index/memory_derivation_revocations.json`
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

`tests.memory.test_memory_edit_restart`는 사용자 편집 뒤 새 Python
프로세스에서 note detail과 recall을 다시 열어 수정 본문,
`source=user-edit`, revision, 새 evidence가 유지되는지 검증한다.

`tests.memory.test_memory_derivation_revocation`은 다음을 검증한다.

- 단일 source 연쇄 tombstone과 multi-source quarantine
- quarantine 하위 파생의 전파 격리
- preview/apply 영향 fingerprint 충돌 거부
- recall/FTS/vector/graph/hot-context fail-closed
- content-free revocation state
- root tombstone `fsync` 직후 강제 종료와 새 Python 프로세스의
  cascade/quarantine 복구
- Sub-LLM 입력에 삭제 source와 기존 파생 본문이 포함되지 않음
- 남은 source만 사용한 topological 재합성과 user-edit 해제

## Remaining boundary

연쇄 철회는 note가 선언한 `derived_from` metadata에 의존한다. 수동 작성 또는
과거 importer가 실제 근거 관계를 이 필드에 기록하지 않았다면 런타임이 숨은
의존성을 추론하지 않는다. 또한 multi-source note의 자동 재합성은 Sub-LLM이
준비될 때까지 fail-closed quarantine으로 남는다. 따라서 현재 계약은
“선언된 provenance graph 전체의 철회”이며, provenance가 누락된 과거 note까지
내용 유사도만으로 삭제했다고 주장하지 않는다.
