# Memory Provenance And Deletion Contract

Document status: **Current**
Last reviewed: 2026-08-02 KST

이 문서는 Evelyn Memory Vault가 기억의 근거를 공개하고 사용자 삭제를
영구적으로 지키는 현재 런타임 계약을 정의한다.

## Provenance

Control Page의 memory card와 recall metadata는
`memory.provenance.v1`을 사용한다. 각 항목은 최소한 다음 정보를 갖는다.

- `schema=memory.provenance.v1`, `noteId`, `source`, `sourceType`
- `sourceRefs`, `derivedFrom`
- `evidenceHashes`
- `confidence`

recall prompt에는 원문 전체 대신 선택된 기억의 note ID, source, evidence
요약과 confidence를 넣는다. 파생 기억은 원본 note ID와 evidence hash를
유지해야 한다.

### Exact rendered-set recall attribution

turn-time recall은 기본 선택 note, graph 확장 note와 task-like procedural
추가 note를 note ID 기준으로 합친 단일 `rendered_rows` 집합을 만든다. 일반
memory와 procedural memory section은 표시 위치만 다르며, 같은 note를 두 section에
중복 렌더링하지 않는다. 실제 prompt에 렌더링한 모든 note와 오직 그 note만 다음
항목에 동일하게 들어가야 한다.

`procedure`, `procedural`, `procedures` type은 모두 같은 internal procedural
분류로 정규화해 일반 recall에서는 숨기고 명시적 관리 recall에서만 procedural
section에 넣는다. `max_items`는 selection과 cache key를 만들기 전에 1~12로
정규화하며 procedural 추가 note도 전체 12-note receipt 한도를 넘지 않는다.

- `MemoryRecallResult.facts`, `sources`
- `metadata.provenance`, canonical `metadata.rendered_note_ids`
- `memory.recall-receipt.v1.noteIds`와 최종
  `memory.context-receipt.v1.suppliedNoteIds`
- retrieval cache의 facts, sources, provenance와 rendered note IDs

이 집합이나 순서가 cache payload 내부에서 불일치하면 cache miss로 처리하고 현재
vault에서 다시 계산한다. retrieval cache schema는 cache key와 payload 양쪽에
결속한다. schema가 없는 과거 cache 또는 다른 알고리즘 버전의 cache는 TTL이
남았더라도 재사용하지 않으며 cache hit도 section 줄바꿈을 그대로 보존한다.
빈 context는 note ID를 공급한 것으로 기록하지 않는다. malformed provenance,
중복 note ID, 누락된 `rendered_note_ids` 또는 선언 집합 불일치는 본문이 있더라도
`groundingState=unattributed`와 빈 note ID 집합으로 fail-closed한다. 따라서
procedural 추가 note도 삭제 exposure와
assistant conversation receipt에 항상 결속되고, 해당 note 삭제 뒤 이전 version의
assistant history는 재사용 전에 제거된다.

`schema`와 위 필수 field의 type, canonical `sourceType`은 retrieval cache 재사용과
recall receipt 생성 양쪽에서 같은 validator로 검사한다. 이 검사를 통과하지 못한
recall 본문은 정상 pinned hot-context와 함께 있어도 그 note ID를 빌려 전체 문맥을
`attributed`로 승격할 수 없다. 이 경우 결합 문맥의 supplied note ID를 비우고 최종
Main/Fast prompt 경계에서 본문 전체를 보류한다.

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
없으면 빈 집계로 취급한다. 레거시·손상 형식이나 닫힌 enum 밖의 키는 감사
lease와 프로세스 내 관찰 lock 아래에서 content-free canonical 집계로 내구성
있게 원자 교체하며, 알 수 없는 note type은 `unknown`, 유효하지 않은 숫자는
0으로 정규화한다. 이 교체가 실패하면 감사 결과를 쓰거나 성공으로 반환하지
않고 `memory_deletion_journal_integrity_failed`로 fail-closed한다.

기존 `memory_provenance_backfill_audit.json`도 parsed object가 같다는 사실만으로
재사용하지 않는다. `generatedAt`은 엄격한 UTC timestamp일 때만 보존하고 전체
raw JSON이 canonical serialization과 byte-for-byte 같지 않으면 감사 lease와
관찰 lock 아래 내구성 있게 원자 교체한다. 중복 key나 숨은 원문을 제거하는
교체가 실패하면 같은 고정 integrity 오류로 fail-closed한다.

## Content-free legacy context coverage

같은 감사 응답과 저장 보고서는 `legacyContextCoverage`에
`memory.legacy-context-coverage.v1` 집계를 제공한다. 이 집계는 global semantic
vault note가 아니라 `guild_*` 아래 guild/room/person/session scope에 저장된
rolling summary, raw transcript row, fact, question의 현재 근거 연결 상태를
측정한다.

- summary는 본문 SHA-256과 `rolling_summary.provenance.json`이 일치하고 파생
  evidence/source ID 형식이 유효할 때만 attributed다.
- raw row는 `conversation_turn` evidence ID, source turn ID와 role의 결합이
  정확할 때만 attributed다.
- fact/question은 각 파생 kind와 source evidence ID가 유효할 때만 attributed다.
- 근거 필드가 없으면 missing, 필드·sidecar·본문 hash가 손상되면 invalid로 세며
  둘 다 `memory.context-use.v1`의 확인 전용 항목이다.

응답에는 전체/attributed/확인 전용 수, missing/invalid 수, kind/scope/storage별
집계, 손상 JSON·읽기 실패·과대 파일·안전하지 않은 location 수만 들어간다.
guild/note/turn/evidence ID, scope key, 파일명·경로, summary/fact/question 본문과
transcript는 넣지 않는다. 집계기는 없는 디렉터리를 만들거나 기억을 수정하지
않으며 symlink나 memory root 밖으로 해석되는 location은 읽지 않는다.

수치는 저장된 row와 summary 기준이다. hot 파일과 일자별 vault mirror를 모두
셀 수 있으므로 고유 대화 턴 수나 실제 한 요청에서 prompt에 선택된 항목 수로
해석하면 안 된다. 이 한계는 `itemSemantics`와 `mayContainMirrors`에 명시한다.

## Final prompt withholding boundary

Main과 Fast Control의 최종 prompt 경계는 `groundingState=attributed`인 결합
문맥만 기억 본문으로 제공한다. `partial` 또는 `unattributed`이면 결합 문자열에서
안전한 component를 다시 분리할 구조적 대응표가 없으므로 본문 전체를
fail-closed로 보류한다. 모델에는 content-free `MEMORY_WITHHELD_RULE`만 제공해
보류된 기억의 구체적인 내용을 보았다고 주장하지 못하게 하고, 꼭 필요할 때
사용자에게 관련 정보를 다시 말하거나 직접 확인해 달라고 요청하게 한다.

1,680자 제한을 넘은 attributed 문맥도 잘린 본문과 evidence ID의 대응을 증명할
수 없으므로 같은 보류 경계를 사용한다. receipt의 `state=withheld`,
`promptMemoryWithheld=true`, `withheldItemCount`, `withheldNoteCount`,
`withheldLegacyItemCount`는 이 판정을 content-free로 기록한다. 실제로 모델에
제공된 note/legacy evidence ID와 count는 0으로 비우며, 길이 초과였다면 기존
`promptTruncated`와 잘리기 전 candidate count도 함께 남긴다.
`confirmOnlyItemCount`와 `opaqueConfirmOnlyComponentCount`는 원문 component가
prompt에 남지 않으므로 0이다.

## Conversation receipt and deletion-safe delivery

기억에 의존해 생성될 수 있는 assistant 대화 row는
`conversation.memory-receipt-ref.v1`을 함께 이동한다. 이 compact receipt는
`schema`, `state`, `memoryVersion`, 정렬된 canonical `suppliedNoteIds`,
`suppliedNoteCount`, `contentFree=true`만 가지며 상태는 다음 세 개다.

- `bound`: attributed memory receipt에서 공급 note ID와 memory version을
  완전히 축약했다.
- `not_used`: 해당 assistant 텍스트가 저장 기억을 사용하지 않았음이
  명시적으로 증명됐다.
- `unattributed`: receipt가 누락됐거나 손상됐고, legacy 의존성을 compact
  형식으로 완전히 표현할 수 없거나, 기억 비의존성을 증명할 수 없다.

사용자 row는 receipt가 필요 없다. assistant row의 compact receipt는
process-local history에만 남지 않고 durable continuity checkpoint, restart restore,
session merge와 Control Page·Discord text·Discord voice의 cross-surface merge까지
전파된다. 인접한 중복 row를 합칠 때도 누락·서로 다른 version·표현
불가능한 의존성을 `not_used`로 낮추지 않고 `unattributed`로
fail-closed한다. receipt는 내부 삭제 선형화에만 사용하고 공개 chat,
state, action projection에서는 제거한다.

대화 history를 prompt, persona/cognitive state, router/planner, search follow-up
또는 tool 입력으로 재사용하기 전에 assistant row를 다시 검사한다. receipt가
없는 legacy row, 손상된 receipt, `unattributed`, 현재 memory version과 다른
`bound`, tombstoned note에 묶인 `bound`는 모두 제거한다. 필터를 통과한
`bound` row의 note ID와 deletion position만 typed memory exposure로 합성한다.
history에서 파생된 persona/cognitive/router cache도 strict receipt로 현재성이
증명되지 않으면 무시하고, tool/search 결과가 history를 다시 쓸 때는
필터링된 exposure를 응답 receipt에 병합한다.

자율행동 runtime도 같은 예외 없는 history 소비자다. observation과 선택된
summary·recent-context·ping·cognitive-refresh callback은 매 조회에서 공용 history
필터를 사용한다. 최초 observation의 typed exposure는 plan·execute·state commit
전체를 guard하며, observation 뒤 새 `bound` row를 읽는 callback은 그 소비와
side effect 동안 자체 guard를 다시 잡는다. 무결성 실패는 일반 executor 오류로
낮추지 않고 action·cursor·state commit 전에 fail-closed한다. guard 밖으로 반환하거나
autonomy cognitive state에 저장하는 projection에는 대화 원문, raw summary/text,
private assistant-history 의사결정 신호를 남기지 않는다. fixed operational failure
code는 내용 비저장 상태 진단으로 유지한다. receipt 없는 runtime cache는 재시작 때
전부 버리고 안전한 상태를 즉시 다시 저장한다. Minecraft plan/cursor는 typed world
observation만으로 결정되므로 대화 exposure guard 안에서 유지한다. 자율 후속 row는
현재 exposure에서 만든 compact receipt를 continuity에 함께 기록한다. 일반 autonomy
loop나 cognitive refresh가 살아 있는 guild reset은 continuity·파일 mutation 전에
거부하며, idle engine은 같은 객체의 cache를 content-free 상태로 비운다.

guild/room/person/session의 stored summary, fact, question과 assistant raw는 vault note
삭제 현재성을 증명하는 receipt가 없으므로 layered prompt 입력에서 전역 보류한다.
exact user raw만 기존 evidence shape 검사를 거쳐 사용할 수 있다. 같은 원문이
`legacy/*` mirror나 `daily/*` conversation note, semantic derived note로 우회하지
못하도록 live vault recall과 hot context는 `conversation|derived|legacy` source type을
제외한다. retrieval cache는 `memory.retrieval-cache.v2`, hot context는
`recall_policy=deletion-current-v1`일 때만 재사용한다. 저장·감사와 사용자 검토는
유지하며 explicit user/system note recall은 유지한다.

memory-derived proactive question queue는 deletion-current receipt를 갖추기 전까지
선택과 mark를 전역 fail-closed한다. `open_questions.jsonl`의 provenance-bearing 원본은
유지하지만 별도 queue에 raw/ask text를 복제하지 않는다. 현재 모델 답변에 직접 든
명시적 질문은 이 queue와 무관하므로 유지한다. 과거 queue/pending 원문은 index
sync에서 제거하고, note 삭제는 receipt 없는 autonomy cache도 제거한다. scope의
symlink·junction alias나 cleanup 실패는 삭제 성공으로 낮추지 않는다.

Main Control Page와 Fast Control Page의 memory-bound JSON response는 handler
종료로 경계가 끝나지 않는다. actual HTTP `prepare` 전에 exact deletion
position을 재검사하고 `write_eof` 종료까지 lease를 유지한다. stale로
판정되면 응답 본문을 쓰기 전 exact
`memory_deletion_journal_integrity_failed` 503과 `Cache-Control: no-store`로
닫힌다. 호환되지 않는 writer가 lease를 보유한 일시적 경쟁이면 exact
`memory_deletion_journal_busy` 503과 `Cache-Control: no-store`로 반환해
사용자가 잠시 뒤 재시도할 수 있게 한다. Fast chat stream도 첫 content event 전 `memory_boundary`를 먼저
전송하고 terminal event에서 같은 경계를 재확인한다.

공개 Control Page의 8798→8799 프록시는 내부 응답을 단순 복사하지 않는다.
8798은 `bound|not_used`와 content-free exposure를 전용 내부 헤더로 넘기고,
8799는 필수 state/chat/shutdown/action-events 경로에서 헤더 누락·손상을
fail-closed한다. upstream EOF 뒤 브라우저 응답 `prepare` 전에 같은 position을
다시 검증하고 실제 browser `write_eof`까지 새 lease를 유지한다. state를
재직렬화해 runtime health를 합치는 경우에도 position을 보존한다. 내부 handoff
헤더와 note ID는 브라우저 응답에 전달하지 않는다.

Control Page text/search와 voice의 assistant side effect는 공용 reply-boundary
validator를 사용한다. `bound` receipt의 memory version과 정렬된 note ID가 현재
exposure와 정확히 같아야 하며, `bound`인데 exposure가 없거나 nonempty exposure에
`not_used`가 붙는 경우도 persistence·continuity·TTS·공개 반환 전에 거부한다.

Discord/in-process TTS는 producer가 잡고 있는 exposure lease와
playback task의 lease를 겹치지 않게 handoff한다. streaming chunk를 먼저
buffer하고 producer close 후 playback owner가 동일 position을 새로 검증해
합성·재생한다. Windows Local I/O Bridge의 direct Fast stream은 `bound`
문장·delta를 HTTP EOF까지 합성하거나 재생하지 않는다. EOF로
server lease가 끝난 뒤에만 wire boundary를 strict parse하고 host guard를
획득해 TTS/PCM을 시작한다. 경계 누락·손상·불일치·stale는
장치 write 0회로 fail-closed한다.

성공한 voice 턴의 history/continuity, memory write, search follow-up,
session/persona 갱신 같은 post-playback side effect는 정확한 reply receipt와
exposure가 일치하고 실제 playback이 성공한 뒤에만 같은 guard 안에서
commit한다. stale·receipt mismatch·재생 실패는 assistant 답변과 파생
side effect를 남기지 않으며, 이미 수용된 사용자 턴만 미응답
continuity로 내구성 있게 보존할 수 있다.

compact receipt, wire boundary, guard status, voice validation event/report에는 raw
audio, transcript, prompt/history/assistant content를 저장하지 않는다. 여기의
`contentFree=true`는 삭제 선형화 메타데이터에 원문을 복제하지 않는다는
뜻이며, 실제 사용자 대화 history/continuity의 보존은 대화 저장 계약을
따른다.

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

1. `memory.provenance.correction.event.v2`의 `prepared` event를 append,
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

v2 row는 UTF-8 canonical JSON 한 줄과 정확한 LF terminator만 허용한다.
duplicate key, CRLF, 공백·key 순서가 다른 동치 JSON, terminator 누락,
추가·누락 field를 거부한다. prepared/committed/failed는 각각 exact field set과
타입을 가지며 change ID, actor/action, error code, revision, UTC timestamp와
ledger ID는 닫힌 domain을 통과해야 한다. 이미 존재하는 duplicate-free legacy
v1 row는 raw byte를 다시 쓰지 않고 immutable prefix anchor로만 사용한다.

마지막 sequence/hash는 별도
`memory_provenance_correction_chain_head.json`에 durable atomic replace로
기록한다. 이 head가 journal보다 앞서거나 기존 prefix와 다르면 손상으로
판정한다. journal append의 `fsync` 뒤 head 교체 전에 중단되어 head가 없거나
뒤처진 경우에는, journal chain과 기존 head prefix가 모두 유효할 때만 같은
writer lease 아래 head를 복구한다. journal과 head가 모두 비어 있는 최초
상태는 정상이다.

local head, deletion initialization witness와 signed external anchor도 strict
duplicate-key parser, exact schema와 canonical artifact JSON byte 검사를 통과해야
한다. 기존 event hash나 HMAC을 그대로 둔 채 숨은 중복 field, key 순서나 공백을
바꾼 artifact는 유효한 head/anchor/witness로 인정하지 않는다.

correction 전체는 Windows byte-range lock 또는 POSIX `flock`과 프로세스 내부
owner table을 함께 사용한다. 임의 명령이나 경로를 받는 lease API는 없으며,
diagnostic marker에는 schema, held/released, process nonce, PID, 시각,
stale-owner 회수 여부와 `contentFree=true`만 기록한다. 다른 thread/process가
이미 소유 중이면 대기하거나 겹쳐 쓰지 않고 즉시
`memory_provenance_correction_writer_unavailable`로 거부한다.
marker도 duplicate key 없는 exact canonical schema와 닫힌 상태·timestamp를
요구한다. 손상 marker는 공개 조회에서 소유권 근거로 사용하지 않고 `unknown`을
반환하며, 파일 mutation은 다음 writer lease를 획득한 뒤에만 정리한다.

선택적으로 correction head에 기억 전용 HMAC-SHA256 authenticity를 적용한다.
`EVELYN_MEMORY_INTEGRITY_KEY_FILE`은 repository와 `bot_memory` 밖의 절대 경로,
symlink가 아닌 32 byte 이상 key 파일이어야 한다. 관계 연속성용
`EVELYN_CONTINUITY_AUTH_KEY_FILE`과는 HMAC domain과 환경 변수를 모두 분리하며
같은 key를 재사용하지 않는 것이 운영 계약이다. 키가 설정되면 head는
`memory.provenance.correction-chain-head.v2`로 승격되고 sequence, event hash,
시각과 content-free metadata 전체를 인증한다. 서명 head가 있는데 key가 없거나
tag/key ID가 다르면 fail-closed한다.

`EVELYN_MEMORY_INTEGRITY_ANCHOR_DIR`을 함께 설정하면 Bot API가 repository,
`bot_memory`, `runtime_artifacts` 밖의 보호 디렉터리에
`memory.provenance.correction-external-anchor.v1`을 기록한다. 앵커는 마지막
sequence/hash와 HMAC만 가지며 기억 내용은 저장하지 않는다. journal/head의
서명된 과거 복제본 재생, 둘의 동시 재작성, 전체 삭제는 더 높은 외부 앵커와
충돌해 거부된다. journal append 뒤 head 기록 전 중단 또는 head 기록 뒤 anchor
교체 전 중단은 기존 anchor와 정확히 연결되는 한 단계에 한해 같은 writer lease
아래 복구한다. key 파일은 Bot API가 쓰는 anchor 디렉터리 안에 둘 수 없다.

기존 무서명 이력이나 최초 빈 상태를 채택할 때만
`EVELYN_MEMORY_INTEGRITY_BOOTSTRAP=true`를 한 번 사용한다. 이때도 현재 chain을
먼저 검증한 뒤 signed head와 anchor를 만든다. Control Page가
`journalAuthenticity=verified`, `journalExternalAnchorState=verified`,
`journalRollbackProtected=true`를 보고하면 즉시 false로 되돌린다. 외부 anchor
디렉터리가 설정됐는데 record가 없고 bootstrap이 꺼져 있으면 자동으로 새
신뢰 기준을 만들지 않는다.

`tools/verify_memory_deletion_integrity.py`는 실제 memory root를 받거나 자동
탐색하지 않는다. repository 밖의 기존 빈 scratch/anchor와 별도 key만 받아
disposable deletion ledger를 만들고, bootstrap=false 거부, bootstrap=true인 짧은
자식 프로세스 한 번, bootstrap=false인 새 프로세스의 strict 검증, signed 과거
journal+head pair replay 거부와 정상 pair 복원을 순서대로 확인한다. 출력은 고정
상태·오류·sequence만 포함하며 note/event/key/path 원문은 포함하지 않는다.

이 도구의 `replicaContractVerified=true`는 path isolation과 anchor 계약만 뜻한다.
현재 자동 검증은 POSIX owner/mode, Windows owner/DACL, Docker host secret·bind
mount의 effective permission을 증명하지 않으므로 항상
`permissionState=not_verified`, `operationallyVerified=false`를 반환한다. 이 별도
권한 증거 없이 `rollbackProtected=true`만으로 운영 권한 분리까지 완료됐다고
판정하지 않는다.

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

## Content-bound user review confirmation

Control Page의 기억 카드 `confirm`은 사용자가 화면에서 검토한 정확한 note
revision에만 적용된다. 요청은 카드에서 받은 64자리 `expectedContentHash`를
필수로 보내며, 서버는 단일 edit lock 안에서 note를 다시 읽어 현재
`sourceHash`와 비교한다. 다르면 `memory_note_changed_since_read`로 아무 상태도
쓰지 않는다.

성공하면 `memory.user-review-confirmation.v1` receipt와 함께 sidecar에
`confirmed_at`, `confirmed_content_hash`를 flush/fsync 뒤 원자 기록한다. snapshot/card는 저장된
hash가 현재 note hash와 정확히 같은 경우에만 `confirmed=true`,
`confirmationState=confirmed`, `confirmationContentBound=true`를 반환한다. 이후
파일 변경이나 예전 hash 없는 sidecar를 발견하면 `confirmed=false`,
`confirmationState=stale`로 fail-closed하고 재확인을 요구한다. state write가
실패하면 성공을 반환하지 않는다.

본문이 공개 화면에 표시되지 않는 legacy note와 public mutation surface 밖의
internal note, explicit confirmation 무결성이 손상된 note는 확인할 수 없다. 이
review confirmation은 현재 내용을 사용자가 봤다는 UI 상태이며 source refs나
evidence hash를 새로 만들지 않고, ungrounded 기억을 `attributed`로 승격하지도
않는다. 새 직접 사용자 근거가 필요하면 현재 발화의 `/remember` 또는
`기억해줘:` 계약으로 별도 user-confirmed note를 만든다.

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

1. 공개 삭제 요청의 strict `memory.deletion.tombstone.v1` payload를 검증한다.
2. 이를 `contentFree=true`, 단조 `sequence`, `previousHash`, `eventHash`를
   가진 `memory.deletion.tombstone.v2` event로 변환해 journal에 append하고
   file data를 동기화한다.
3. 현재 chain position과 legacy raw-prefix hash를 durable head에 원자 교체한다.
   Windows는 `MoveFileExW(REPLACE_EXISTING|WRITE_THROUGH)`, POSIX는 temp file
   `fsync` 뒤 rename과 parent-directory `fsync`가 모두 성공해야 commit으로
   인정한다.
4. 선택적 memory-integrity key가 있으면 deletion 전용 HMAC domain/scope로
   head를 서명한다. 외부 anchor가 있으면 head를 기록한 뒤 별도
   `memory-deletions.json` position을 순서대로 전진시킨다. 어느 단계든
   실패하면 삭제 성공을 반환하지 않으며, 둘을 하나의 filesystem atomic
   operation으로 간주하지 않는다.
5. journal commit 뒤 source Markdown을 먼저 content-free deletion stub으로
   durable replace하고 unlink한다. unlink가 지연되거나 되돌아가도 note body는
   다시 나타나지 않는다.
6. user state, SQLite/FTS/vector rows, recall cache, hot context를 정리한다.
7. `derivedFrom`을 따라 파생 기억을 연쇄 철회 또는 격리한다. 연쇄 철회 source도
   같은 redaction-before-unlink 경계를 사용한다.

tombstone은 삭제 권한의 내구성 있는 경계다. title, body, source path,
content hash를 저장하지 않고 canonical ledger note ID, 닫힌 note/source type,
정규화된 reason, 삭제 시각만 저장한다. Evelyn 고유 machine ID 형식만 그대로
허용하며, front matter의 임의 ID는 domain-separated SHA-256
`opaque-<64hex>`로 투영한다. 임의 type/source label도 각각 `unknown` 또는
정해진 alias enum으로 정규화한다. 따라서 사용자 작성 ID가 문장이나 transcript
형태여도 새 v2 journal, redaction stub, API apply 결과, content-free receipt와
provenance audit에는 원문이 기록되지 않는다. application 내부 graph join은 기존
raw ID를 유지하고 삭제 여부를 비교할 때만 같은 ledger projection을 적용한다.
Fast의 사용자 지정 memory provider가 주는 receipt도 note ID를 같은 ledger ID로
투영하되 이미 canonical인 ledger ID는 이중 hash하지 않는다. `retrievalMode`는
`fts`, `scan`, `fts+vector`, `scan+vector`, `cache`, `unknown`의 닫힌 enum만
허용하며 provider·retrieval cache의 자유 형식 값은 `unknown`으로 바꾼다.
legacy evidence/source/turn ID도 producer, 최종 receipt와 durable turn summary에서
각각 domain-separated `opaque-evidence-*`/`opaque-turn-*`로 투영한다. explicit
confirmation 성공 receipt의 note ID는 canonical ledger ID, source ref는
`turn:opaque-turn-<64hex>:user` 형식만 허용한다. 원본 source ref는 provenance를
담는 content-bearing note 안에만 남고 `contentFree=true` receipt에는 나오지 않는다.
`memory_derivation_revocations.json`도 target/direct/revoked/blocked/remaining ID를
ledger ID로만 저장한다. 읽을 때 현재 live graph의 raw ID 후보를 같은 방식으로
투영해 정확히 하나만 일치할 때 역매핑한다. 비정규 ID나 충돌·모호성은 추측하지
않고 `memory_derivation_revocations_corrupt`로 fail-closed한다. 이미 삭제되어 live
graph에 없는 canonical stale target은 reconciliation 동안에만 허용한 뒤 artifact에서
제거한다. 정상 legacy raw artifact와 duplicate/additional key 또는 비정규 byte
serialization은 writer lease와 observability lock 아래 canonical content-free 형식으로
내구성 있게 다시 쓰며, 이 교체가 실패하면 고정 integrity 오류로 fail-closed한다.
역매핑된 source ID와 새 graph state는 모두 raw ID 기준으로 중복 제거·정렬한 뒤
비교한다. 따라서 의미가 같은 반복 reconciliation은 artifact byte, `updatedAt`과
hot-context generation을 변경하지 않는다.

기존 v1 journal은 원본 byte prefix 전체의 SHA-256 domain hash로 고정한다.
첫 read가 non-empty legacy-only journal을 만나면 writer lease 아래 sequence 0
head를 먼저 durable write한 뒤에만 내용을 반환한다. 이후 valid한 legacy tail을
지우거나 바꾸는 것도 integrity failure다. v2 chain은 빈 줄, partial JSON,
duplicate key, non-canonical serialization, pathological integer/depth,
per-record·전체 크기 초과, unknown/extra/missing field, sequence gap,
hash mismatch, v1-after-v2와 symlink artifact를 모두 fail-closed한다.

journal `fsync`와 head 교체 사이의 정확한 1-event lag만 crash recovery로
허용한다. 둘 이상의 lag, head-ahead, journal/head 불일치, journal 없이 head만
남은 상태는 복구하지 않는다. directory metadata sync가 실패했으면 파일이
보이더라도 durable commit으로 보고하지 않는다.

tombstone 기록 뒤 프로세스가 중단되어 source 파일이 남더라도 그 note는
논리적으로 삭제된 상태다. indexing, direct note lookup, user snapshot,
recall, supersede 경로는 tombstoned note를 노출하지 않는다. 다음 index sync는
남은 source와 user state를 재조정하고 파생 인덱스를 제거한다.

현재 journal/head가 유지되는 동안 redaction stub과 tombstone 검증은 지연된
unlink 또는 남은 source 파일의 본문 노출을 막는다. journal, head와 source를
함께 과거 상태로 복원하는 공격은 검증된 외부 anchor 없이는 탐지하지 못한다.

snapshot과 삭제 preview/apply는 content-free
`memory.deletion.integrity.v1`을 함께 반환한다. `rollbackProtected=true`는
keyed head와 외부 anchor가 모두 검증된 경우에만 참이다. 외부 anchor가
검증되지 않은 기본·key-only 상태는 local corruption과 단독 truncation은
탐지하지만 유효한 journal+head 과거 쌍 replay는 탐지하지 못한다.

### Exposure linearization

memory response, outbound LLM과 conversation-history exposure처럼 deletion
journal을 쓰지 않는 긴 구간은 같은 OS lock byte의 shared reader lease 안에서
journal을 검증하고 결과가 경계를 벗어나기 직전에 다시 검증한다. 여러 reader는
동시에 진행할 수 있지만 deletion writer는 모든 reader가 끝날 때까지 진입하지
못한다. reader에서 writer로의 lock upgrade는 허용하지 않는다. 검증 중 복구가
필요하면 shared lease를 놓고 writer lease에서 상태를 다시 검사·복구한 뒤 shared
lease를 새로 얻는다.

정상 recall은 deletion writer lease에서 index sync와 retrieval cache read/write를
기존대로 수행한다. writer 진입 전 exact lock busy인 경우에만 fresh shared reader
lease로 전환하며, repair가 필요한 journal state는 쓰지 않고 fixed integrity failure로
닫는다. 이 fallback은 SQLite를 `mode=ro&immutable=1`로 열고 WAL/SHM/journal sidecar,
symlink/junction, `schema_version != 6`, 비정규 `memory_version`과 필수
metadata/notes query 실패를 거부한다. retrieval cache,
FTS/vector/graph rank, hot context, legacy/layered memory와 disk cognitive state는 읽지
않는다. 최대 500개 index 후보를 현재 Markdown의 exact path·note ID·전체 hash,
tombstone, canonical quarantine와 confirmation state에 다시 결합하고, 명시적
user/system note 중 `derived_from`이 없는 항목만 렌더링한다. 실패나 unavailable은
본문 없이 `indexFresh=false`, `readOnlyFallback=true`로 관측한다.

Main 전체 context와 Fast memory provider는 fallback의 같은
`memory.deletion.position.v1`을 receipt와 outbound guard에 결합한다. shared fallback은
파일·SQLite·chain head를 만들거나 복구하지 않는다. 정상 writer/cache 경로는
`indexFresh=true`, `readOnlyFallback=false`를 명시한다. graph/snapshot/detail,
provenance preview/apply, index/cache rebuild와 memory write처럼 실제 artifact write를
포함하는 나머지 경로는 deletion writer lease를 유지한다. 정상 삭제는 노출 전 또는
노출 후로만 선형화되며, 이미 읽은 본문 뒤에 삭제가 성공한 상태로 그 본문을 반환할
수 없다.

Control Page의 memory edit와 provenance/delete preview·apply는 writer admission이
일시적으로 busy일 때만 최대 2초 동안 50ms 간격으로 기다린다. 각 시도는 worker
thread에서 outer writer lease를 얻고 같은 thread에서 기존 domain operation을 정확히
한 번 호출한다. lease가 yield하기 전의 busy만 다시 시도하며, operation 본문·guard
종료·result-shaped busy는 재실행하지 않는다. deadline 뒤 늦게 얻은 lease도 operation과
confirm token을 소비하지 않는다. 요청 취소가 admission보다 먼저면 operation은 0회,
admission이 먼저면 시작한 operation을 1회 끝낸 뒤 worker 오류를 취소보다 우선 보존한다.
2초 안에 admission되지 않으면 기존 content-free/no-store busy 503을 반환한다.

semantic consolidation은 fresh shared reader lease 안에서 현재 source의 tombstone과
전체 content hash를 확인하고 Sub-LLM 요청·전체 응답을 수행한다. shared lease를 완전히
놓은 뒤 fresh writer에서 source hash를 다시 확인하고, 같을 때만 note batch와 후속
index sync를 같은 writer phase에서 완료한다.

derivation recomposition은 짧은 writer phase에서 현재 journal position과 exact
revocation state를 캡처하고, writer를 놓은 뒤 target/source 후보와 hash를 수집한다.
fresh shared reader lease 안에서 position·revocation·hash를 모두 다시 확인한 뒤
Sub-LLM 요청·전체 응답을 수행하고, 응답 뒤 shared lease를 완전히 놓는다. fresh
writer에서는 pre-sync 후 tombstone, exact revocation entry, target/source hash와
live/quarantine source 집합을 다시 계산하며, 같을 때만 note write와 후속
index/revocation sync를 같은 writer phase에서 완료한다. 두 경로 모두 일반 reader와
공존하지만 삭제·편집 writer는 shared phase 동안 fail-fast busy다. handoff 중 삭제나
사용자 편집이 먼저 성공하면 current state가 이기며 모델 결과는 쓰지 않는다.

전체 legacy+vault memory context build는 하나의 검증된
`memory.deletion.position.v1`에서 수행한다. 공개 memory receipt에는 sequence와
position digest만 있는 `deletionBoundary`를 남기고, root-bound
`MemoryDeletionPosition` 객체는 현재 async context/Fast typed request 안에서만
운반한다. Main non-stream, Voice stream/legacy response, Fast Control stream의
실제 HTTP sink는 request factory를 호출하기 전에 그 position을 다시 검증하고
응답 소비가 끝날 때까지 deletion lease를 유지한다. build 뒤 삭제가 먼저
commit됐거나 position이 다른 root·sequence라면 고정 integrity 오류로 중단하며
HTTP POST는 시작하지 않는다. 기억 본문이 empty/withheld/not-requested이면 내부
position을 지우고 공개 boundary를 `not_required`로 만든다.

cognitive-state, 경량 route planner와 장기 memory writeback처럼 legacy/layered
기억을 읽는 background JSON LLM도 동일하다. 메시지를 만드는 guard에서 position을
캡처해 primary와 compact retry 모두에 명시적으로 전달하고, 공통 JSON sink는
`memory_boundary_required=true`일 때 POST factory 호출 전에 재검증한다. 응답으로
생성한 cognitive state, summary와 durable facts는 같은 position을 다시 획득한
guard 안에서만 기록한다. 이 사이 삭제가 먼저 commit되면 모델 결과를 저장하지
않고 고정 integrity 오류로 중단한다. 기억을 입력받지 않는 JSON LLM 호출은
boundary 없이 기존 계약을 유지한다.

provenance-correction journal의 새 v2 prepared event도 target/source/origin
application ID를 그대로 저장하지 않고 deletion-ledger ID만 기록한다. recovery와
undo는 현재 live graph와 immutable legacy v1 prefix에서 만든 exact 1:1 mapping으로
raw graph identity를 복원한다. 미매핑, 충돌 또는 canonical 형식이 아닌 v2 ID는
추측하지 않고 correction journal integrity failure로 차단한다. persisted
provenance coverage의 `bySourceType`, `byNoteType`과 forward rejection note type은
각각 닫힌 source/note type enum으로 정규화하고 alias bucket을 합산한다.

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
- LLM 처리 중 tombstone, target/source hash, quarantine, revocation entry나 live-source
  집합이 바뀌면 결과를 쓰지 않는다.
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

hot context에는 deletion journal과 chain head 각각의 수정 시각·크기를 함께
저장한다. cache metadata가 현재 4-value journal/head state와 다르거나 ledger
자체가 strict validation을 통과하지 못하면 cached prompt는 빈 값으로 처리되어
삭제된 기억이 다시 주입되지 않는다. derivation revocation 파일의 수정 시각과
크기도 같은 방식으로 묶는다. 다음 index sync는 stale hot-context와 prompt-block
파일도 제거한다. 새 hot-context와 prompt block은 원자 파일 교체로 갱신한다.

현재 날짜의 daily note를 삭제한 뒤 새 대화가 생기면 같은 파일 경로를 다시
사용할 수 있지만 note ID는
`daily-YYYY-MM-DD-continuation-N`으로 바뀐다. 삭제된 원문은 복원하지 않고
삭제 이후의 새 대화만 새 identity에 기록한다.

## API result semantics

- 완전 정리: HTTP 200, `ok=true`, `deleted=true`, `tombstoned=true`
- tombstone은 내구성 있게 기록됐지만 파생 정리가 남음:
  HTTP 503, `ok=false`, `error=memory_delete_cleanup_required`,
  `tombstoned=true`, `cleanupErrors=[...]`
- deletion-ledger 무결성/내구 commit 실패로 분류되기 전의 예기치 않은 사전
  실패: HTTP 500, `error=memory_delete_failed`
- 정상 lock 경쟁으로 reader/writer lease를 즉시 획득하지 못함:
  HTTP 503, exact
  `{ "ok": false, "error": "memory_deletion_journal_busy" }`; mutation 없음,
  잠시 뒤 재시도 가능
- deletion ledger가 손상됐거나 stale position·내구성 검증이 실패함:
  HTTP 503, exact
  `{ "ok": false, "error": "memory_deletion_journal_integrity_failed" }`
- preview 뒤 파생 영향 그래프 변경:
  HTTP 409, `memory_derivation_impact_changed_since_preview`, 삭제 없음

`cleanup_required`에서도 조회와 prompt 경로는 tombstone을 기준으로
fail-closed한다. 운영자는 재시작 또는 index sync 뒤 잔여 파일과 인덱스가
정리됐는지 확인한다.

busy와 integrity 503에는 parser 원문, 예외 메시지, source/title/body, transcript,
host path나 sibling field를 넣지 않고 `Cache-Control: no-store`를 적용한다. recall의 같은 실패도
빈 context/facts/sources와 고정 error code만 반환한다. Bot API chat state
handler도 두 오류를 generic `control_page_chat_failed`로 바꾸지 않고 최외곽
middleware까지 다시 던지며, 공개 Control Page proxy는 각각 같은 exact 503과
`no-store`를 보존한다.

## Storage

- journal:
  `bot_memory/memory_index/memory_deletions.jsonl`
- deletion chain head:
  `bot_memory/memory_index/memory_deletions_chain_head.json`
- deletion writer OS lock:
  `bot_memory/memory_index/.memory_deletions_writer.lock`
- optional external deletion anchor (configured root):
  `memory-deletions.json`
- signed external deletion-initialization witness (configured root):
  `memory-deletions.initialized.json`
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
- optional external correction anchor (configured root):
  `memory-provenance-corrections.json`
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

`tests.memory.test_memory_deletion_journal_integrity`와
`tests.memory.test_memory_deletion_integrity_restart`는 추가로 다음을 검증한다.

- strict v1/v2 shape, legacy raw-prefix pin, v2 sequence/hash chain
- malformed/partial/oversized/symlink artifact, duplicate JSON key,
  non-canonical v2 row와 pathological JSON의 고정 integrity 실패
- journal이 없고 head가 남은 상태 거부, 검증된 외부 anchor 사용 시
  journal+head pair replay와 journal/head/anchor 전체 삭제 거부
- 공유 anchor에 correction journal만 있는 진짜 미초기화 deletion ledger 허용,
  첫 승인 삭제의 signed initialization witness 생성과 기존 anchor marker migration
- unsigned replica의 bootstrap=false 거부, true인 fresh child 한 번의 채택,
  false인 새 child strict 검증과 signed 과거 pair replay 거부 뒤 정상 pair 복원
- local head, initialization witness와 external anchor의 duplicate key뿐 아니라
  non-canonical whitespace/key order도 고정 integrity 오류로 거부
- exact 1-event crash recovery와 그 이상의 lag 거부
- signed head와 외부 anchor의 과거 journal+head replay 탐지
- writer allowlist가 아니라 OS single-writer lease를 무시한 경쟁 append 거부
- Windows `LockFileEx`와 POSIX `LOCK_SH`에서 별도 프로세스 reader 공존,
  reader/writer 상호 차단, async owner별 해제와 reader→writer upgrade 거부
- fresh-process torn/missing/lag 상태와 same-ID resurrection 차단
- cached recall, 전체 vault context, semantic Sub-LLM, derivation recomposition
  경계의 concurrent delete 선형화
- active shared reader 때문에 normal recall writer가 busy일 때 immutable SQLite와
  current Markdown을 사용한 no-cache fallback, cache/FTS/vector/graph/hot/legacy
  비사용, 동일 deletion position의 Main/Fast receipt·outbound 결합
- fallback 중 delete writer의 exact busy와 confirm token 비소모, lagging head,
  missing/corrupt/old-schema DB와 SQLite sidecar의 byte-stable unavailable 처리
- tombstone 뒤 stale source·SQLite·cache를 복원해도 삭제 본문 비회상,
  tampered DB body 비노출과 `derived_from` note의 보수적 제외
- `max_items=1`에서 selected concept 밖 procedural note가 추가되어도 렌더링,
  provenance, cache와 receipt note 집합이 정확히 같고 selected procedure는 한 번만
  렌더링되는 계약
- schema 없는 legacy retrieval cache의 재사용 거부와 procedural note 삭제 뒤
  이전 receipt-bound assistant history 제거
- 전체 legacy+vault context position capture, content-free receipt projection,
  build와 Main/Voice/Fast HTTP admission 사이 삭제 시 POST 0회 fail-closed
- unlink 실패 시 source Markdown의 title/body가 content-free stub으로 먼저
  redaction되는 계약
- public API와 8798→8799 proxy의 exact content-free busy/integrity 503,
  `no-store`, private sibling 제거와 retry UI projection

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
- 기억 전용 HMAC head와 외부 monotonic anchor의 명시적 one-shot bootstrap
- HMAC 변조, signed past replay, journal/head 전체 삭제의 fail-closed 탐지
- journal-ahead-of-head와 head-ahead-of-anchor의 한 단계 crash recovery
- key/anchor의 repository·memory root 내부 경로와 symlink 거부
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
content-free·append-only이고 v2 SHA-256 chain, HMAC head, 외부 monotonic anchor,
OS single-writer lock으로 보호할 수 있다. 이 보호는 memory 파일을 제어하는
공격자와 key/anchor 경로를 분리한다는 trust boundary에 의존한다. 공격자가 key를
읽거나 외부 anchor도 함께 과거로 되돌릴 수 있으면 로컬 파일만으로는 이를
구분할 수 없으며 TPM/원격 append-only ledger 또는 여러 host 사이의 분산 합의는
제공하지 않는다. 실제 vault에는 현재 derived relationship이 없어 운영 데이터에
mutation을 가하는 live correction은 수행하지 않았다.

multi-source note의 자동 재합성은 Sub-LLM이 준비될 때까지 fail-closed
quarantine으로 남는다. 따라서 현재 계약은 “선언된 provenance graph 전체의
철회 + exact metadata 또는 사용자 직접 선택에 의한 conflict-safe
backfill/relink/unlink/undo”이며, coverage 100%나 사용자 선택만으로 기억
내용의 사실성을 보증하지 않는다.
