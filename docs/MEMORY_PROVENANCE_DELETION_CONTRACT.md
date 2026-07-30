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

## Legacy provenance backfill audit

과거 note에 `derived_from`이 비어 있는 경우 Control Page의
`GET /api/control-page/memory-provenance-audit`가
`memory.provenance.backfill-audit.v1` 후보 보고서를 만든다.

감사는 다음의 기존 명시적 metadata만 사용한다.

- source note ID 또는 vault 상대 경로와 정확히 일치하는 `source_refs`
- source note의 현재 원문 hash 또는 기존 consolidation body digest와
  정확히 일치하는 `evidence_hashes`

본문·제목 유사도, 임베딩 근접도, LLM 추측은 신호로 사용하지 않는다. source
ref와 evidence hash가 같은 source 집합을 가리킬 때만 `verified`, 하나의
정확 신호만 있으면 `review`, 두 신호가 서로 다른 source를 가리키거나 후보가
모호하면 `ambiguous`다. 기존 graph에 cycle을 만드는 후보와 user edit로
의도적으로 분리되어 `origin_derived_from`만 남은 note는 후보에서 제외한다.

보고서는 다음 경계를 지킨다.

- 조회 API와 저장 보고서는 항상 `readOnly=true`, `autoApply=false`다.
- `verified`와 `review` 후보만 `canApply=true`이고 `ambiguous`, 보호 대상,
  숨김 대상은 `canApply=false`와 차단 사유를 반환한다.
- 사용자 note Markdown과 현재 `derived_from`을 수정하지 않는다.
- 저장 파일에는 note ID, 후보 source ID, 판정 코드, 집계와 graph
  fingerprint만 기록한다.
- title, body, source path/ref, evidence hash, transcript는 저장하지 않는다.
- graph가 바뀌면 새 fingerprint로 보고서를 다시 계산한다.

Control Page는 live note의 공개 title/type을 결합해 후보를 보여 주지만
어떤 후보도 자동으로 적용하지 않는다. 조회 결과는 backfill 사실이 아니라
사용자 검토를 위한 증거 목록이다.

## Content-free provenance coverage

같은 감사 응답은 `memory.provenance.coverage.v1` 집계를 제공한다. coverage는
각 note를 선언된 파생 관계, 직접 source, 사용자 직접 교정, exact 후보,
ambiguous, 불일치 metadata, 명시 신호 없음으로 분류하고 다음 차원으로만
합산한다.

- source type
- note type
- 마지막 갱신 시각 기준 `0_7d`, `8_30d`, `31_180d`,
  `over_180d`, `unknown`

각 bucket에는 전체 수, 근거 상태가 확정된 수, 사용자 검토가 필요한 수만
들어간다. coverage에는 note ID, title, body, path, source ref, evidence hash,
transcript가 들어가지 않는다. `checkedAt`은 응답에만 존재하고, 저장된 감사
보고서에는 안정된 집계만 기록한다.

새 derived write가 `derived_from` 없이 거부될 때는
`memory_provenance_forward_write_rejections.json`의 내구성 있는
`memory.provenance.forward-write-rejections.v1` 카운터를 증가시킨다. 이
파일은 총 거부 수, note type별 수, 최초·최근 거부 시각만 보존한다. 요청의
title, body, source, source ref, hash 또는 경로는 저장하지 않는다. 파일이
없거나 손상됐거나 숫자가 유효하지 않으면 감사 조회는 실패하지 않고 해당
값을 0으로 취급한다.

## Two-step provenance backfill

근거 연결은 감사 조회와 분리된 두 POST 요청으로만 수행한다.

1. `POST /api/control-page/memory-provenance-backfill/{noteId}/preview`
2. `POST /api/control-page/memory-provenance-backfill/{noteId}/apply`

preview 요청은 감사에서 반환한 source note ID 집합 전체를 제출해야 한다.
현재 후보 집합과 정확히 같지 않거나 후보가 `ambiguous`이면 토큰을 발급하지
않는다. 발급된 암호학적 난수 token은 120초 동안 한 번만 쓸 수 있고 다음
상태에 함께 묶인다.

- memory root와 target note ID
- target의 현재 SHA-256 content hash
- 모든 source note ID와 각각의 현재 content hash
- 후보 판정 상태와 전체 provenance graph fingerprint
- 위 값을 정규화해 계산한 binding fingerprint

apply는 token을 먼저 소비한 뒤 같은 후보를 다시 계산한다. 대상·source·무관한
다른 graph node를 포함해 graph가 하나라도 바뀌거나, token이 만료·재사용되거나,
다른 대상/root에 제출되면 HTTP 409로 거부하고 파일을 쓰지 않는다. token은
프로세스 메모리에만 있으므로 Bot API 재시작 뒤에는 복구되지 않는다.

사용자 확인이 성공하면 제목과 본문 suffix는 byte-for-byte 그대로 두고 front
matter에만 `derived_from`, `provenance_backfilled_at`,
`provenance_backfill_method=exact-metadata-user-confirmed`,
`provenance_backfill_audit_hash`, 증가한 `revision`을 원자적으로 기록한다.
그 뒤 index와 hot context를 다시 만든다. 파일 교체 실패는
`memory_provenance_backfill_failed`, 후처리 실패는
`memory_provenance_backfill_cleanup_required`이며 후자는 `applied=true`를
함께 반환한다. preview/apply 모두 기존 Control Page CSRF 계약을 따른다.

## User-selected provenance repair

exact metadata가 없거나 기존 metadata가 현재 vault source와 일치하지 않는
과거 note는 내용 유사도, 임베딩 또는 LLM으로 source를 추천하지 않는다.
Control Page는 다음 별도 계약으로 사용자가 source note를 직접 고르게 한다.

1. `GET /api/control-page/memory-provenance-manual/{noteId}/sources`
2. `POST /api/control-page/memory-provenance-manual/{noteId}/preview`
3. `POST /api/control-page/memory-provenance-backfill/{noteId}/apply`

GET 응답 `memory.provenance.manual-source-options.v1`에는 대상과 허용 source의
공개 ID, title, type, source type만 들어간다. body, path, source hash와
evidence hash는 반환하지 않는다. source 목록은 추천 순서가 아니라 안정된
ID 순서이며, 사용자가 최대 12개를 명시적으로 선택한다.

수동 선택은 `missing_explicit_signal` 또는
`unmatched_explicit_metadata` 대상에만 허용한다. exact 후보가 있으면 기존
exact-metadata 경로를 사용해야 하고, ambiguous 대상은 계속 거부한다. 대상과
source는 공개·visible·비격리 상태여야 하며 legacy/internal 관리 note를
source로 쓸 수 없다. source는 직접 근거, 이미 선언된 파생 근거 또는 사용자
직접 교정 근거여야 하고, 대상 자신이나 대상을 의존하는 source는 cycle 방지를
위해 제외한다.

manual preview도 CSRF가 필요하고 자동 적용하지 않는다. token은 exact 경로와
같이 120초·일회용이며 root, target/source content hash, 전체 graph
fingerprint에 더해 `selectionMode=user_selected`와 대상의 수동 검토 사유에
묶인다. preview 뒤 관련 없는 note 하나라도 바뀌면 apply는 HTTP 409로
거부한다. 성공 시 title/body는 그대로 두고
`provenance_backfill_method=user-selected-source-note-ids`를 기록한다.

새 파생 note의 forward write 계약도 별도로 강제한다.
`write_memory_vault_note`는 consolidation 또는 recomposition source를
`derived`로 판정하면 비어 있지 않은 `derived_from`을 요구하고 self-reference를
거부한다. 일일·semantic consolidation과 partial recomposition은 실제 source
note ID를 명시한다. 기본 source는 파생으로 오해될 수 있는 `consolidation`이
아니라 `runtime`이다.

## Existing provenance relink, unlink, and undo

이미 근거가 연결된 파생 기억과 사용자가 연결을 해제한 기억은 본문 편집과
분리된 provenance correction 계약으로 관리한다.

- `GET /api/control-page/memory-provenance-corrections`
- `GET /api/control-page/memory-provenance-corrections/{noteId}/sources`
- `POST /api/control-page/memory-provenance-corrections/{noteId}/preview`
- `POST /api/control-page/memory-provenance-corrections/{noteId}/apply`
- `POST /api/control-page/memory-provenance-corrections/{noteId}/undo/preview`
- `POST /api/control-page/memory-provenance-corrections/{noteId}/undo/apply`

조회 응답은 대상과 source의 공개 ID, title, type, source type 및 현재 연결만
반환한다. body, path, content/source/evidence hash와 transcript는 반환하지
않는다. hidden, legacy/internal, quarantine, 미접지 source와 cycle을 만드는
source는 선택할 수 없다. 최대 12개를 ID 안정 순서로 직접 선택하며 자동 추천,
본문 유사도, 임베딩과 LLM 추론은 사용하지 않는다.

`sourceNoteIds=[]`를 명시하면 현재 `derived_from`을 모두 해제하는 `unlink`다.
필드 누락이나 배열이 아닌 값은 unlink로 해석하지 않고 HTTP 400으로 거부한다.
해제는 target이나 source note를 삭제하지 않으며 제거된 ID는
`origin_derived_from`에 보존한다. 이후 `relink`에서 현재 직접 source로 다시
선택한 ID는 origin history에서 제외해 현재 관계와 과거 관계가 겹치지 않게
한다.

correction preview는 120초 동안 한 번만 쓸 수 있는 난수 token을 발급한다.
token은 다음 전체 상태에 묶인다.

- memory root, target note ID와 현재 revision
- target의 현재 content hash
- 현재·제안 `derived_from`과 `origin_derived_from`
- 선택된 모든 source의 현재 content hash
- 전체 provenance graph fingerprint와 정규화된 binding fingerprint
- correction/undo 종류와 undo 대상 change ID

apply는 token을 먼저 소비하고 memory delete/edit/correction lock을 함께 잡은
뒤 같은 binding을 다시 계산한다. target, source 또는 무관한 graph node라도
바뀌었거나 token이 만료·재사용됐으면 HTTP 409로 거부하며 파일을 쓰지 않는다.
모든 변경 POST는 기존 Control Page CSRF 계약을 따른다.

성공한 relink/unlink는 title과 body suffix를 byte-for-byte 유지하고 front
matter만 원자적으로 교체한다. 새 `derived_from`, 누적
`origin_derived_from`, 증가한 `revision`과 다음 metadata를 기록한다.

- `provenance_corrected_at`
- `provenance_correction_change_id`
- `provenance_correction_method=user-relinked-source-note-ids`
  또는 `user-unlinked-source-note-ids`

변경 내구성 경계는 다음 순서다.

1. `memory.provenance.correction.event.v1`의 `prepared` event를 append,
   flush, `fsync`한다.
2. 같은 디렉터리의 임시 파일을 내구성 있게 쓴 뒤 Markdown을 원자 교체한다.
3. 같은 change ID의 `committed` event를 append, flush, `fsync`한다.
4. SQLite/FTS/vector index, hot context와 provenance audit를 다시 만든다.

journal은 change/action/target ID, 이전·새 source/origin ID, 이전·새 revision,
undo 대상 ID, actor와 시각만 저장한다. title, body, path, content/source/
evidence hash와 transcript는 저장하지 않는다. 삭제 tombstone처럼 audit
연속성을 위해 자동 retention 없이 append-only로 보존한다.

새 event는 `memory.provenance.correction.event.v2`이며 1부터 증가하는
`sequence`, 직전 event의 `previousHash`, `eventHash`를 제외한 event의
canonical JSON으로 계산한 SHA-256 `eventHash`를 가진다. 최초 v2 event 앞에
v1 event가 있으면
v1 raw line 전체를 domain-separated SHA-256으로 묶은 값이
`previousHash`가 된다. 따라서 v1 prefix의 수정·삭제도 이후 검증에서
감지한다.

마지막 sequence/hash는 별도
`memory_provenance_correction_chain_head.json`에 durable atomic replace로
기록한다. 이 head가 journal보다 앞서거나 기존 prefix와 다르면 손상으로
판정한다. journal append의 `fsync` 뒤 head 교체 전에 중단되어 head가 없거나
뒤처진 경우에는, journal chain과 기존 head prefix가 모두 유효할 때만 같은
writer lease 아래 head를 복구한다. journal과 head가 모두 비어 있는 최초
상태는 정상이다.

correction 전체는 Windows byte-range lock 또는 POSIX `flock`과 프로세스 내부
owner table을 함께 사용한다. 임의 명령이나 경로를 받는 lease API는 없으며,
diagnostic marker에는 schema, held/released, process nonce, PID, 시각,
stale-owner 회수 여부와 `contentFree=true`만 기록한다. 다른 thread/process가
이미 소유 중이면 대기하거나 겹쳐 쓰지 않고 즉시
`memory_provenance_correction_writer_unavailable`로 거부한다.

프로세스가 1번 뒤 2번 전에 종료되면 적용되지 않은 `prepared`는 현재 note와
일치하지 않아 committed로 승격되지 않는다. 2번 뒤 3번 전에 종료되면 다음
overview/source-options/preview가 note의 change ID, revision, source/origin
ID를 대조하고 일치할 때만 `recoveredAfterRestart=true` committed event를
추가한다. 이 reconciliation GET은 note를 수정하지 않지만 누락된 journal
terminal event를 복구할 수 있다. 파일 교체 직후 같은 프로세스에서 예외가
발생해도 note를 재판독해 적용 상태가 확인되면 잘못된 `failed` terminal을
기록하지 않는다.

가장 최근 committed relink/unlink만 명시적으로 undo할 수 있다. 현재 note의
change ID, revision과 source/origin ID가 그 변경의 결과와 정확히 같아야 한다.
undo는 이전 source/origin을 복원하면서
`provenance_correction_method=user-undo`,
`provenance_correction_undo_of`와 새 change ID를 기록하는 별도 append-only
변경이다. undo 자체에는 자동 redo를 제공하지 않는다.

파일 교체 전에 실패하면 HTTP 500,
`memory_provenance_correction_failed`, `applied=false`다. 파일은 바뀌었지만
journal commit이나 index/hot-context/audit 후처리가 실패하면 HTTP 503,
`memory_provenance_correction_cleanup_required`, `applied=true`와 구체적인
고정 cleanup error code를 반환한다. 다음 조회의 journal reconciliation과
index sync가 상태를 복구한다.

journal/hash/head가 손상됐거나 읽을 수 없으면 overview와 mutation은 HTTP
503으로 fail-closed한다. apply는 confirm token을 소비하거나 Markdown을
쓰기 전에 integrity를 검사한다. writer lock 또는 marker를 확보할 수 없을
때도 HTTP 503이며 note는 바뀌지 않는다.

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

Control Page memory snapshot은 `memory.quarantine.status.v1` 집계도 제공한다.
여기에는 현재 격리 수, 즉시 재합성 가능한 수, 상위 근거로 차단된 수, 가장
오래된 `quarantinedAt`과 경과 초, 시간 판정 불가 수만 들어간다. note ID,
title, body와 transcript는 집계에 포함하지 않는다.

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
- content-free provenance audit:
  `bot_memory/memory_index/memory_provenance_backfill_audit.json`
- content-free forward-write rejection counter:
  `bot_memory/memory_index/memory_provenance_forward_write_rejections.json`
- content-free provenance correction journal:
  `bot_memory/memory_index/memory_provenance_corrections.jsonl`
- correction journal chain head:
  `bot_memory/memory_index/memory_provenance_correction_chain_head.json`
- content-free correction writer marker:
  `bot_memory/memory_index/memory_provenance_correction_writer.json`
- correction writer OS lock:
  `bot_memory/memory_index/.memory_provenance_correction_writer.lock`
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

`tests.memory.test_memory_provenance_audit`,
`tests.memory.test_memory_provenance_backfill`,
`tests.memory.test_memory_provenance_manual`과
`tests.memory.test_memory_provenance_correction`,
`tests.runtime.test_memory_provenance_audit_api`는 다음을 검증한다.

- exact source ref와 evidence hash의 교차 검증
- 충돌 신호의 `ambiguous` 유지, cycle과 user-detach 후보 거부
- 내용이 같다는 이유만으로 후보를 만들지 않음
- 읽기 전용 audit 뒤 source/target Markdown이 byte-for-byte 그대로 유지됨
- 저장 보고서에 title, body, path/ref, evidence hash가 없음
- quarantine 수, 재합성 가능 수와 최장 대기 시간 집계
- 2단계 CSRF API와 명시적 사용자 확인 UI
- target/source/full-graph 변경, 잘못된 source, 만료·재사용 token 거부
- Bot API 재시작 뒤 token 무효화와 성공 적용 뒤 provenance 복구
- 성공 적용에서 제목·본문 byte 안정성, 원자 쓰기 실패 시 원본 보존
- 새 derived write의 `derived_from` 필수 계약
- source type·note type·age별 content-free coverage
- derived write 거부 카운터의 내구성과 내용 비저장, 손상 값 fail-closed 처리
- 신호 없음·불일치 대상의 사용자 직접 source 선택과 exact/ambiguous 분리
- 숨김·격리·legacy/internal·미접지·cycle source 거부
- 수동 preview의 target/source/full-graph 충돌 거부
- 기존 관계의 relink, 명시적 빈 배열 unlink와 origin history 보존
- 가장 최근 변경만의 explicit undo와 undo 이후 자동 redo 금지
- correction journal의 content-free 필드와 prepared-before-write 순서
- v2 hash chain, 별도 head의 tail deletion 탐지와 legacy v1 prefix anchoring
- 같은 프로세스 thread 및 별도 프로세스 writer 경쟁의 즉시 거부
- journal/head 손상 시 note 무수정 fail-closed와 HTTP 503
- journal append 뒤 head 교체 중단의 lagging-head 복구
- 파일 교체 뒤 예외의 read-back commit 및 commit event 누락의 새 프로세스 복구
- correction API의 CSRF, 잘못된 빈 요청 거부와 공개 hash/body 비노출

## Remaining boundary

연쇄 철회는 여전히 note가 선언한 `derived_from` metadata에 의존한다. 감사
보고서는 기존 source ref/hash로 정확히 증명할 수 있는 후보만 찾고, 신호가
없는 대상은 사용자가 source ID를 직접 선택해야 한다. coverage 100%는 모든
note가 계약상 분류됐다는 뜻이지 기억 내용이나 사용자의 선택이 사실임을
보증하지 않는다.

기존 관계의 relink/unlink와 최근 변경 undo까지 구현됐지만, 이 계약도 사용자가
선택한 source가 의미적으로 사실인지 자동 증명하지 않는다. journal은
content-free·append-only이고 v2 SHA-256 chain, 별도 durable head와 OS
single-writer lock으로 보호된다. 다만 이는 keyed signature나 외부 불변
anchor가 아니므로 journal과 head를 함께 다시 쓸 수 있는 filesystem 관리자에
대한 authenticity 또는 여러 host 사이의 분산 합의를 제공하지 않는다. 실제
vault에는 현재 derived relationship이 없어 운영 데이터에 mutation을 가하는
live correction은 수행하지 않았다.

multi-source note의 자동 재합성은 Sub-LLM이 준비될 때까지 fail-closed
quarantine으로 남는다. 따라서 현재 계약은 “선언된 provenance graph 전체의
철회 + exact metadata 또는 사용자 직접 선택에 의한 conflict-safe
backfill/relink/unlink/undo”이며, coverage 100%나 사용자 선택만으로 기억
내용의 사실성을 보증하지 않는다.
