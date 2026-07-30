# Evelyn Current State

Document status: **Current**
Last reviewed: 2026-07-31 KST
Source branch: `codex/dependency-config-hardening` through `52f7bf5`

이 문서는 현재 확인된 사실만 기록한다. 목표 구조와 과거 계획은 다른 설계/계획 문서를 사용한다.

## Source state

- 전체 프로젝트 감사의 즉시 항목을 별도 안정화 브랜치에서 처리 중이다.
- `main.py` 분해는 목표 범위에 도달했다.
  - 현재 2,402줄이며 원래 목표 범위인 1,500~2,500줄 안에 들어왔다.
  - top-level/nested 함수 정의, `global`/`nonlocal`, dependency-builder 함수 정의는 모두 0개다.
  - 기능 구현, 판정, 상태 mutation은 owner runtime/composition 모듈에 있고 `main.py`는 설정 import,
    객체 생성, 명시적 typed dependency wiring, Discord 등록, runtime 진입을 담당한다.
  - dependency wiring은 암시적 registry나 `globals()` 우회 없이 한 줄 최대 두 인자, 158자 이하로 유지한다.
- 핵심 준비 상태와 선택 기능 준비 상태를 분리했다.
  - `ok`: 필수 핵심 서비스 준비 여부
  - `fullyHealthy`: 선택 기능을 포함한 전체 건강 여부
  - Voyager HTTP 응답과 실제 runner/bridge/Minecraft 준비 여부도 분리했다.
- 루트 Python 의존성은 `requirements.lock`으로 고정했다.
- GitHub Actions는 Windows/Python 3.11/Node 24에서 전체 회귀 테스트와 실제 `main.py` 프로세스 smoke를 실행한다.
- 단기 대화 연속성 checkpoint의 guild 초기화 경계를 강화했다.
  - content-free per-guild revocation ledger를 runtime clear 전에 durable
    기록한다.
  - checkpoint owner 잠금 안에서 모든 guild-prefixed session/room state와
    merge record를 독립적으로 제거하고 새 checkpoint를 강제 저장한다.
  - checkpoint 교체 전 crash에서는 revocation marker가 이전 checkpoint의
    대상 guild만 차단하며 다른 guild는 복구한다.
  - marker 기록 실패 시 runtime reset을 시작하지 않고, ledger 손상 시 기존
    checkpoint 전체를 fail-closed로 거부한다.
  - checkpoint v2는 증가하는 generation, 이전 hash와 canonical payload hash를
    저장하고 content-free durable head가 최신 generation/hash를 고정한다.
  - valid JSON 변조 뒤 self-hash 재계산, 과거 generation rollback, active
    head 뒤 checkpoint 삭제를 복구하지 않는다.
  - checkpoint commit 뒤 head commit 전 crash만 정확한 한 generation
    chain으로 복구하며, v1 checkpoint는 raw JSON hash로 generation 0에
    고정한 뒤 다음 변경에서 v2로 연결한다.
- 자율행동의 승인과 결과 증거를 같은 action·grant에 묶었다.
  - `autonomy.outcome-evidence-policy.v1`이 모든 supported action의 exact
    evidence code를 정의한다. 비어 있지 않은 임의 코드나 다른 action의
    올바른 코드는 성공 증거가 아니다.
  - executor 성공 뒤 실행 전 grant ID를 다시 검사하며, 실행 중 grant가
    교체·만료·철회되면 engine을 중단하고 plan cursor를 유지한다.
  - retry budget 소진은 `verified=false`인 blocked 결과이며 성공·skip으로
    변환하지 않는다.
  - 승인·결정·결과 event는 flush/fsync 뒤 반환한다. journal을 기록할 수
    없으면 새 실행을 허용하지 않고 모든 grant를 폐기한다.
  - Discord `자율상태`는 현재 guild의 승인 활성 여부, 남은 TTL, audit
    readiness와 strict evidence policy를 표시하되 issuer와 grant ID는
    공개하지 않는다.
- Codex Gateway의 `/codex/action`은 bearer token을 요구한다. `/health`는 읽기 전용으로 유지한다.
- 사용되지 않던 `docs/assets/evelyn-page.js`는 삭제했고, UI 테스트는 실제 `docs/index.html` 인라인 컨트롤러를 검사한다.
- Docker Compose의 사용자별 `C:/Users/Admin/...` 경로는 환경변수와 `USERPROFILE` 기반으로 바꿨다.
- Windows Host Vision Bridge가 Docker Bot API의 화면 요청을 실제 호스트
  캡처에 연결한다.
  - exact schema, TTL, 크기 제한을 사용하며 임의 명령·argv·경로를 받지 않는다.
  - foreground window title/class, 읽기 전용 Windows UI Automation,
    SmolVLM scene, Windows Runtime OCR을 서로 다른 신뢰도로 합성한다.
  - UI Automation은 foreground 일치, 5초 freshness, 허용 control type,
    요청별 충분성을 통과할 때만 exact-text evidence가 된다.
  - 정확한 창 제목은 Main LLM 전에 근거에서 그대로 복사하며, 요청한
    버튼·메뉴·탭 근거가 없으면 결정론적으로 추측을 거부한다.
  - Edit·Document 값, PID·경로·명령행, Value/Invoke pattern을 읽지 않고
    UI focus·click·입력 mutation을 수행하지 않는다.
  - screenshot, OCR tile, 요청/응답은 요청 뒤 삭제되고 status에는 근거
    metadata와 지연만 남는다.
  - per-turn evidence는 `vision.evidence.v2`이며 screenshot capture 뒤
    15초 안에서만 live다. timestamp 누락·역전·미래·만료와 v1 legacy
    observed payload는 tool evidence가 될 수 없다.
  - 전경 창과 UI Automation source가 충돌하면 두 structured source를
    버리고 screenshot/native OCR만 low-confidence·non-actionable fallback으로
    사용한다.
  - stale/invalid observation 원문은 Host Bridge response, client result,
    Main/Fast LLM context에서 반복 제거한다.
- 읽기 전용 화면 관찰과 실제 UI mutation 사이에 별도 Host UI Action
  경계를 추가했다.
  - 현재 전경의 이름 있는 enabled UIA `Button`과 `invoke`만 허용하며,
    임의 command/argv/path/좌표/키보드와 background window는 거부한다.
  - 30초 일회성 token은 process memory에만 있고 재시작 뒤 복구되지 않는다.
    apply 전에 foreground window와 element ID/control/name/automation ID/
    bounds를 다시 관찰해 preview와 완전히 같은지 확인한다.
  - fixed PowerShell executor는 `InvokePattern`을 한 번만 호출한다. 이후
    `target_absent`, `target_disabled`, `window_changed` 중 승인된 조건이
    관찰돼야 성공이다.
  - 실행됐지만 결과를 증명하지 못하면 `outcome_unverified` 실패로 보존하고
    자동 재시도하지 않는다. 감사 journal을 `fsync`할 수 없으면 새 실행을
    시작하지 않는다.
  - status와 감사 journal은 target/window text, element ID, token, 화면
    내용을 저장하지 않는다. journal은 30일/20 MiB/최근 7개 보존 규칙을
    사용한다.
  - Control Page는 CSRF 적용 preview/apply와 별도 `window.confirm`을
    제공한다.
- 한글 프로젝트 경로의 Docker Buildx 문제를 피하기 위해 빌드 동안만 사용하지
  않는 드라이브 문자를 매핑한다. allowlist 이미지 세 개만 빌드하고 자신이 만든
  매핑만 검증 후 해제한다.
- Control Page 메모리 수정은 읽어 온 카드의 64자 `expectedContentHash`를
  필수로 제출한다.
  - 다른 편집으로 내용이 바뀌었으면 `409 memory_note_changed_since_read`로
    거부하고 최신 카드를 다시 불러오므로 오래된 화면이 새 내용을 덮어쓰지 않는다.
  - 수정 파일은 같은 디렉터리 임시 파일을 flush/fsync한 뒤 원자적으로
    교체한다. 교체 실패 시 원본을 보존하고 `500 memory_edit_failed`를 반환한다.
  - 수정된 기억은 현재 source를 `user-edit`로 바꾸고 revision과 새 evidence
    hash를 기록한다. 원래 source/source refs와 이전 evidence hash는 별도
    provenance 필드에 보존한다.
  - 메모리 인덱스 schema는 v6이며 재시작 뒤에도 수정 provenance가 유지된다.
- 기억 삭제 preview는 현재 `derivedFrom` graph 영향과 fingerprint를 함께
  고정한다. apply 전 graph가 바뀌면 409로 아무것도 삭제하지 않는다.
  - 유일한 근거를 잃는 파생 기억은 content-free tombstone으로 연쇄 철회한다.
  - 다른 근거가 남은 기억과 그 하위 파생은 Markdown만 사용자 검토용으로
    보존하고 recall/FTS/vector/graph/hot-context에서 격리한다.
  - root tombstone 직후 프로세스가 죽어도 새 프로세스가 같은 cascade와
    quarantine을 재구성한다.
  - Sub-LLM 재합성은 삭제 source와 기존 파생 본문을 입력하지 않고 살아 있는
    source note만 사용한다. Sub-LLM이 없으면 추측하지 않고 격리를 유지한다.
  - 사용자가 직접 수정하면 과거 derived relation은 `originDerivedFrom`으로
    분리되고 현재 근거는 `user-edit`로 바뀐다.
- 누락된 과거 `derived_from`은 provenance 감사에서 먼저 읽기 전용 후보로
  표시한다.
  - source note ID/vault 상대 경로와 정확히 일치하는 source ref, 현재
    source hash 또는 기존 consolidation body digest와 정확히 일치하는
    evidence hash만 사용한다.
  - 본문·제목 유사도, 임베딩과 LLM 추측은 사용하지 않는다.
  - ref/hash 교차 일치, 단일 정확 신호, 충돌·복수 후보를
    `verified`/`review`/`ambiguous`로 분리하고 cycle 및 user-detach 후보를
    제외한다.
  - 저장 보고서는 note/source ID와 판정 코드·집계·graph fingerprint만
    포함한다. title, body, path/ref, evidence hash, transcript는 저장하지
    않는다.
  - `verified`/`review`만 별도 preview/apply와 브라우저의 명시 확인 뒤 연결할
    수 있다. `ambiguous`와 보호 대상은 적용할 수 없으며 자동 적용은 없다.
  - 120초 일회용 token은 target/source content hash와 전체 graph
    fingerprint에 묶인다. 어느 node라도 바뀌거나 Bot API가 재시작되면
    fail-closed로 적용하지 않는다.
  - 성공 시 제목·본문은 그대로 두고 `derived_from`과 사용자 확인 backfill
    metadata만 원자적으로 기록한 뒤 index/hot context를 재구성한다.
  - 새 consolidation/recomposition write는 실제 `derived_from`이 없으면
    `memory_derived_from_required`로 거부한다.
  - source type·note type·age별 `memory.provenance.coverage.v1`은 note ID,
    title, body, path, source ref/hash와 transcript 없이 구조적 근거 상태만
    집계한다.
  - 거부된 derived write는 note type별 content-free 내구 카운터에만
    기록한다. 손상된 숫자는 감사 API를 실패시키지 않고 0으로 처리한다.
  - exact 신호가 없거나 현재 source와 불일치하는 과거 note는 자동 추론하지
    않는다. 공개·visible·비격리·접지된 source를 사용자가 최대 12개까지 직접
    선택하고 별도 preview/apply로만 연결한다.
  - manual token도 selection mode, target/source content hash와 전체 graph
    fingerprint에 묶인다. exact/ambiguous 대상, 숨김·격리·legacy/internal·
    미접지·cycle source는 fail-closed로 거부한다.
  - 이미 연결된 파생 관계는 별도 correction overview/source-options와
    preview/apply로 relink하거나 명시적 빈 배열로 unlink할 수 있다.
    제거한 source ID는 `origin_derived_from`에 보존하며 가장 최근
    relink/unlink만 현재 revision·관계가 정확히 일치할 때 별도 변경으로
    undo한다.
  - correction token은 target/source content hash, current/proposed
    source·origin ID와 전체 graph fingerprint에 묶인 120초 일회용이다.
    write-ahead journal을 `fsync`한 뒤 Markdown을 원자 교체하고 committed를
    기록하며, commit 직전 중단은 새 프로세스가 note metadata와 정확히
    일치할 때만 복구한다.
  - correction event v2는 sequence/previous hash/event hash를 잇고 별도
    durable head로 꼬리 삭제를 감지한다. v1 prefix도 raw-line anchor로
    고정하며, 유효한 journal append 뒤 head 교체 중단만 lease 아래 복구한다.
  - Windows byte-range lock/POSIX `flock`과 프로세스 owner table이 correction
    전체를 단일 writer로 만든다. journal/head 손상과 writer 경쟁은 token
    소비나 Markdown write 전에 fail-closed하며 API는 HTTP 503을 반환한다.
  - journal과 공개 API는 note/source ID, revision, action과 공개 title/type만
    다루며 body, path, content/source/evidence hash와 transcript를 저장하거나
    노출하지 않는다. 모든 변경은 CSRF와 브라우저의 별도 확인이 필요하다.
- memory snapshot은 격리 수, 재합성 가능 수, 차단 수, 가장 오래된 대기
  시각·경과 초를 `memory.quarantine.status.v1`로 집계한다. 집계에는
  note ID나 콘텐츠를 넣지 않는다.

## Deployment state

- `1348321` 소스로 `bot_api` 이미지를 실제 재빌드·교체하고 Host Supervisor의
  allowlist `restart_local_bridge` 액션으로 Local I/O/Host Vision bridge를
  재기동했다. Control Page와 Vision 서비스는 변경이 없어 기존 healthy
  이미지를 유지했다.
- 이후 `6f55a27` 소스로 `bot_api`와 `control_page` 이미지를 실제
  재빌드·교체해 conflict-safe 메모리 수정 계약과 UI를 배포했다.
- 이후 `b9e4c6b` 소스로 두 이미지를 다시 빌드·교체해 파생 기억의
  cascade/quarantine/recomposition 계약과 삭제 영향 UI를 배포했다.
- 이후 `fa1fd78` 소스로 `bot_api`와 `control_page` 이미지를 다시
  빌드·교체해 exact-metadata provenance 감사, content-free 후보 보고서,
  quarantine 대기 관측과 Control Page “근거 감사” 탭을 배포했다.
- 이후 `ca9492b` 소스로 두 이미지를 다시 빌드·교체해 conflict-safe
  provenance backfill, explicit-confirm UI와 derived-write forward 검증을
  배포했다.
- 이후 `c656fc8` 소스로 두 이미지를 다시 빌드·교체해 content-free
  provenance coverage·forward rejection 관측, 신호 없는 과거 기억의
  사용자 직접 source 선택과 Control Page 교정 UI를 배포했다.
  - Bot API image:
    `sha256:5bfc251e86826146eaa386e74ed1981ad79c44e98e2516e2e7e72ddb365e3ec6`
  - Control Page image:
    `sha256:fe1be3464ca9236fdabdb6835842bf5926b0a315a594672b905a4e48eceb2130`
- 이후 `c92a158` 소스로 Bot API와 Control Page를 다시 빌드·교체해 기존
  provenance 관계의 conflict-safe relink/unlink, content-free write-ahead
  journal, 재시작 복구, 최신 변경 explicit undo와 Control Page 관리 UI를
  배포했다.
  - Bot API image:
    `sha256:903e2cf6546fe2aeafb2a2d1b33526bbf498c1b74057d206c8f1ed2457b5870d`
  - Control Page image:
    `sha256:e8e8cc962adc2190b7749fc9b692887cef2382934cd1dcfe2b0871d0af73800a`
  - 기존 Bot API를 먼저 정상 종료해 Minecraft owner claim이 사라진 것을
    확인한 뒤 두 컨테이너만 교체했다. 두 컨테이너 모두 첫 기동에서
    `healthy`, restart count 0이다.
- 이후 `b2cb9a2` 소스로 Control Page만 교체해 로컬 음성 검증 전용 마이크
  동의 임대, preview/apply/revoke API, 세션 종료·만료·재시작 fail-closed
  철회와 명시적 grant/revoke UI를 배포했다. Bot API와 나머지 서비스는
  교체하지 않았다.
  - Control Page image:
    `sha256:6b9598799e76f33f03a9740e81fbb9426fb22e20e4609458f8c78c34d3d37485`
  - 새 Control Page는 첫 기동에서 `healthy`, restart count 0이다.
  - 배포 전후 Local I/O Bridge의 실제 `micEnabled=false`를 확인했으며,
    사용자의 허용 버튼은 누르지 않았다.
- `9fc3899` 소스로 Discord bot 이미지를 빌드해 대화 연속성의 guild reset
  crash 경계와 실제 `main.py` 재시작을 검증했다. 실제 Discord 서비스는
  시작하거나 교체하지 않았다.
  - Discord image:
    `sha256:b5984c5ec26a28a8a927982f4f85fc6df01c5f38946f86eec24675a25090d338`
- `b5d352a` 소스로 Discord bot 이미지를 다시 빌드해 exact action evidence,
  grant post-check, audit fail-closed와 Discord 승인 상태 표시를 검증했다.
  실제 Discord 서비스는 시작하거나 교체하지 않았다.
  - Discord image:
    `sha256:7c8563c727bd7e8aeb8a806835da16df0648c5a516b5a9f48cf9dfef721f99d6`
- `743cfd3` 소스로 Bot API와 Vision 이미지를 빌드해 per-turn vision
  freshness/source-conflict fail-closed 경계를 합성 검증했다. 실행 중인
  Bot API·Vision 컨테이너는 교체하지 않았고 실제 화면 캡처도 요청하지 않았다.
  - Bot API image:
    `sha256:898cf1df0adc40aa40fb989108b7187c6401b8d25727b6ee8d1ba93926176802`
  - Vision image:
    `sha256:30bad0c4399c60a89ae9cb9729fc29f9896c7375e5e8504599de6ffdcd9e0c81`
- `b53a529` 소스로 Bot API와 Control Page 이미지를 빌드·배포해 UIA Button
  preview/apply, Host queue, 실행 전 재관찰, 실행 후 postcondition 검증과
  `outcome_unverified` 전달을 검증했다. 실제 UI action은 수행하지 않았다.
  - Bot API image:
    `sha256:737e2b9f5b819eaa235e2a6f937707c29900877e47d9985a1826b988b6004ec0`
  - Control Page image:
    `sha256:6d01495a80f57d58b9aca62902dc9225ed5ee8880793cefe039915240f516c7f`
  - 기존 Bot API를 15초 grace로 먼저 정상 종료했고
    `minecraft_world_lease/owner_claim.json`이 사라진 뒤 두 컨테이너만
    교체했다. 새 owner nonce가 생성됐고 active world lease는 없다.
  - 두 컨테이너는 새 digest로 healthy, restart count 0이다.
  - Host Supervisor allowlist `restart_local_bridge` preview/apply로
    Local I/O Bridge를 새 프로세스로 교체했다. 새 bridge는 ready,
    Host Vision `vision.evidence.v2`, Host UI Action `running/auditReady`다.
  - 마이크는 OFF, Discord/Minecraft/Voyager는 기동하지 않았다.
- 이전 `c656fc8` 배포의 recreate 직후에는 이전 Bot API owner claim이 15초
  stale guard 안에 있어 첫 Bot API start가
  `minecraft_world_lease_owner_conflict`로 fail-closed 종료됐다. guard 만료
  뒤 같은 새 컨테이너가 claim을 회수해 정상 기동했고, 중복 world owner는
  만들어지지 않았다. `c92a158` 배포에서는 정상 종료와 claim 반납을 먼저
  확인해 이 일시 충돌이 재발하지 않았다.
- Bot API, Control Page, Main/Router/Sub LLM, TTS, STT, Vision 여덟
  컨테이너가 모두 `healthy`다. 새 Bot API와 Control Page의 restart count는
  0이다.
- `ce31793` 소스로 Bot API와 Control Page를 직접 재빌드·교체해 correction
  journal v2 hash chain, durable head, OS/process single-writer 경계와
  integrity/writer HTTP 503을 배포했다.
  - Bot API image:
    `sha256:288f3a977ad5f7637022f64c0e1fc15768a2aae041502e3a1ff4b93fc4110a9d`
  - Control Page image:
    `sha256:1bdb56fe6bc63b05ce514bd3ab8e00873c0ca3035171fcd96129e06fc55272c7`
  - Bot API를 15초 grace로 정상 종료해 Minecraft owner claim 반납을 확인한
    뒤 두 컨테이너만 교체했다. 둘 다 healthy, restart count 0이다.
- `52f7bf5` 소스로 Discord bot 이미지를 빌드해 conversation checkpoint v2의
  hash/generation chain, durable head, rollback·삭제 거부와 legacy migration을
  검증했다. 실제 Discord 서비스는 시작하거나 교체하지 않았다.
  - Discord image:
    `sha256:e46bdd3ae0afb4aaeddcb6bbc5a12a1a6d4b7512bd8683daf80a31151b5be9f0`
  - 실제 `runtime_artifacts/conversation_continuity`에는 checkpoint/head가
    없어 기존 대화를 생성하거나 migration하지 않았다.
- Windows Host Supervisor와 Local I/O Bridge heartbeat는 fresh이고 bridge는
  `ready=true`, TTS warmup 완료, Host Vision `running`이다.
- 개인정보 보호 기본값에 따라 로컬 마이크는 비활성 상태다.
- Minecraft/Voyager와 Codex Gateway는 기본 local core에서 지연 시작되며 현재
  실행하지 않는다. Discord bot도 사용자 요청 없이 시작하지 않았다.

## Last runtime evidence

2026-07-31 UI Action 배포 후 비파괴 검증 결과:

- `GET /api/control-page/ui-action`은 Control Page와 Bot API 모두
  `host_ui_action.status.v1`, `state=running`, `auditReady=true`,
  `allowedActions=["invoke"]`, `arbitraryCoordinates=false`를 반환했다.
- CSRF 없는 preview는 403, 임의 `command` 필드는 400, 존재하지 않는
  20자리 element ID는 409 `ui_action_target_missing`, `userConfirmed=false`
  apply는 400으로 거부됐다.
- well-formed missing-target 요청은 Host queue가 1회 처리했지만 preview
  token을 만들지 않았고 executor도 호출하지 않았다. authorization state는
  `authorization_required`, preview/execution/verified count는 모두 0,
  denied count만 1이다.
- requests/processing/responses queue는 모두 0개다. authorization/status와
  JSONL audit에는 window title/class, element ID, target name, automation ID,
  bounds, confirm token, argv, working directory가 없었다. audit event는
  `process_started`, `action_denied`뿐이다.
- 실제 브라우저 panel은 `RUNNING`과 세 postcondition을 렌더링했고
  warning/error console log는 0개였다. preview/apply 버튼은 누르지 않았다.
- `9d1edf6`에는 preview와 확인된 apply를 각각 명시적으로 무장하는 5초 전경
  전환, 취소 버튼, 절대 deadline과 2초 late-callback fail-closed가 추가됐다.
  `target_disabled`를 수동 reset할 수 있는 단일 Button Windows fixture도
  준비했다.
  - Control Page image:
    `sha256:ba9d99a0f8b7740e601c5fd69e2778ef361c49d5f1b8c16c5c11f23b3571b896`
  - Control Page만 교체했고 healthy, restart count 0이다.
  - 배포 이미지의 cache-busted HTML/JS와 fixture 포함 여부, `pip check`를
    확인했다.
  - 실제 브라우저는 새 5초 전환 UI와 `RUNNING`을 렌더링했고
    warning/error log는 0개였다.
  - fixture는 실행하지 않았고 preview/apply도 누르지 않아 실제 action은
    계속 0회다.
- `a98f611`은 불투명 element ID 수동 입력을 read-only Button discovery로
  대체했다. 명시적으로 무장한 5초 뒤 현재 전경에서 이름 있고 enabled인
  Button을 최대 24개 읽으며, 발견만으로 preview token이나 실행 권한은
  만들어지지 않는다.
  - Bot API image:
    `sha256:a730927d1528492013fbeb71d2baad251cc5b3e9f0cf8094a3c880314dd875c2`
  - Control Page image:
    `sha256:4abebc98c19bcb340dd966cfe210df9ed2aac178667c6b66b9b8ad7ab711751f`
  - Bot API owner claim을 15초 grace 정상 종료로 반납한 뒤 두 컨테이너만
    교체했다. 둘 다 healthy, restart count 0이다.
  - Host Supervisor의 allowlisted `restart_local_bridge` preview/apply로
    Local Bridge를 교체했고 Host UI Action은
    `host_ui_action.request.v2`/`response.v2`, `running/auditReady`다.
  - discovery API는 CSRF 없는 요청을 403, 임의 `command` 필드를 400
    `ui_action_invalid_discover_request`로 Host 관찰 전에 거부했다.
  - 배포 브라우저는 `5초 후 Button 찾기`, 발견 전 disabled 대상 selector,
    `RUNNING`을 렌더링했고 warning/error log는 0개였다.
  - 유효한 discovery는 실행하지 않아 discovery/preview/execution/verified
    count가 모두 0이고 requests/processing/responses queue도 모두 비어 있다.
- 공식 `check_docker_runtime.ps1 -IncludeLocalBridge`가 Control Page,
  Bot API, Main/Router/Sub LLM, TTS, STT, Vision과 Windows Local I/O
  Bridge를 모두 준비 상태로 판정했다.
- 배포된 correction overview는 `journalIntegrity=empty`,
  `journalChainReady=true`, `journalWriterProtected=true`,
  `relationshipCount=0`을 반환했다. 실제 vault Markdown 3개의 조회 전후
  SHA-256 변경은 0개였고 journal/head/writer artifact도 생성되지 않았다.

2026-07-30 실제 local-only runtime checker 결과:

- Control Page, Bot API, Main/Router/Sub LLM, TTS, STT, Vision HTTP health 통과
- `controlReady`, `botReady`, `mainReady`, `routerReady`, `subReady`,
  `ttsReady`, `sttReady`, `chatReady`, `voiceReady`, `visionReady` 모두 `true`
- Windows Local I/O Bridge attached/ready
- 지연 시작되는 `voyagerReady`, `codexReady`는 경고이며 core 실패로 계산하지 않음
- 공식 checker 최종 결과: `Docker runtime check passed.`

배포된 로컬 음성 동의 경계를 비활성 상태에서 검증했다.

- `GET /api/control-page/voice-capture-consent`는
  `voice.capture-consent.v1`, `state=inactive`, `active=false`,
  `storesAudio=false`, `storesTranscript=false`를 반환했다.
- 실제 Local I/O Bridge는 `ready=true`, `micEnabled=false`,
  `micControlRevision=0`으로 유지됐다.
- 동의 전 voice capability는 `local_mic_disabled`,
  `local_mic_capture_not_ready`, `local_mic_consent_required`를 blocker로
  보고하고 `grant_voice_validation_mic_consent` action을 제공했다.
- CSRF token 없는 preview POST는 403으로 거부됐다.
- 실제 브라우저 DOM에는 “검증 세션 동안 마이크 허용” 버튼과 기본 OFF 안내가
  렌더링됐고 warning/error console log는 0개였다. 버튼은 누르지 않았다.
- 배포 뒤 공식 `check_docker_runtime.ps1 -IncludeLocalBridge`가 다시
  통과했다.

배포된 Control Page에서 실제 화면 질문 두 종류를 재검증했다.

- 고정 UI Automation observer는 전경 SDL 창 제목
  `테라리아: 모래는 OP다`를 관찰했다.
- 정확한 창 제목 질문에는 설명·공백 변경 없이
  `테라리아: 모래는 OP다`를 그대로 응답했다. Host Vision evidence는
  `reason_code=live_accessibility_observation`, `actionable=true`였다.
- 같은 SDL 앱이 Button control을 노출하지 않는 상태에서 버튼 이름 질문은
  `화면 캡처는 됐지만 이번에는 글자를 읽을 수 있는 근거를 얻지 못했어.
  제목이나 버튼 이름은 추측하지 않을게.`라고 응답했다.
- 두 Host Vision 응답 모두 `screenshotDeleted=true`였고 requests,
  processing, responses, screenshots 디렉터리는 모두 0개였다.
- `status.json`만 남았으며 화면·OCR·사용자 문장 원문은 포함하지 않는다.

배포된 메모리 UI/API도 비파괴적으로 재검증했다.

- Control Page 배포 HTML에 `expectedContentHash`, `api_error:409`,
  `originSource`, revision 표시 계약이 모두 존재한다.
- 읽기 전용 메모리 API가 반환한 실제 카드 2개 모두 64자 content hash와
  provenance 객체, `originSource`·`revision` 필드를 제공했다.
- 두 배포 컨테이너의 실제 SQLite memory schema는 v6이다. 현재 실제 vault는
  indexed note 2개, 선언된 derived note 0개, quarantine 0개다.
- 배포 HTML은 파생 영향 preview, 연쇄 철회 경고, quarantine badge,
  stale-impact 409 거부와 `originDerivedFrom` 표시 계약을 모두 제공한다.
- 실제 사용자 기억의 수정·삭제는 수행하지 않았다.
- 배포된 provenance 감사 API는 실제 note 2개를 검사해 후보 0개,
  `verified=0`, `ambiguous=0`, quarantine `clear/0`을 반환했다.
- 생성된 `memory_provenance_backfill_audit.json`은 entry 0개이며 report
  schema/read-only 정책과 집계만 남겼다. 금지된 title/body/path/ref/
  evidence hash/transcript key는 0개였다.
- 실제 브라우저에서 메모리 창의 “근거 감사” 탭을 열어 격리 수,
  재합성 가능 수, 가장 오래된 대기, 후보/교차 검증/신호 충돌 집계와
  “본문 유사도 미사용·조회만으로 미수정·별도 2단계 확인” 경계가
  렌더링되는 것을 확인했다.
- 배포 HTML에 backfill preview/apply route, `window.confirm`, stale graph
  409 처리와 자동 적용 금지 문구가 존재한다.
- 실제 vault에는 적용 가능한 후보가 0개였으므로 preview/apply를 호출하지
  않았고 실제 사용자 기억은 수정하지 않았다.
- 배포된 coverage API는 실제 note 2개 중 grounded 2개, needs-review 0개,
  ratio 1.0을 반환했다. forward-write rejection, exact 후보, manual 대상,
  ambiguous는 모두 0이며 `autoApply=false`,
  `contentSimilarityUsed=false`다.
- 저장된 감사 보고서의 coverage에는 요청 시각을 남기지 않으며 금지된
  title/body/path/source-ref/evidence-hash/transcript/content-hash key가
  0개다.
- 실제 브라우저의 “근거 감사” 화면에서 100%, `2 / 2`, source
  `conversation 2/2`, note type `daily 2/2`, age bucket, 직접 선택 0과
  자동 적용·본문 유사도·임베딩·LLM 추론 금지 문구가 렌더링됐다. 브라우저
  console warning/error는 0개였고 연결 버튼은 누르지 않았다.
- 배포된 correction API는
  `memory.provenance.corrections.v1`, `readOnly=true`,
  `autoApply=false`, `journalContentFree=true`를 반환했다. 실제 vault에는
  derived relationship이 0개라 관리 대상도 0개였고 correction journal은
  생성되지 않았다.
- correction API 조회 전후 실제 Markdown 2개의 SHA-256을 대조해 변경·추가
  파일이 0개임을 확인했다. 실제 사용자 기억에는 relink/unlink/undo를
  적용하지 않았다.
- 실제 브라우저의 “현재 근거 연결 관리” 영역에 관리 대상 0, 미리보기 후
  2단계 적용, 최근 변경 1회 undo, origin history와 content-free
  write-ahead journal 경계가 렌더링됐다. console warning/error는 0개였다.

## Verification state

검증한 코드 기준점: `52f7bf5`

- bundled Python에서 continuity/restart/guild reset/retention 집중 테스트
  33개를 통과했다.
- 공식 Bot API Python 3.11 환경의 current-source mount에서 continuity,
  retention, Runtime Errors, startup 47개(skip 2)와 runtime 전체
  364개(skip 2)를 통과했다.
- 공식 Discord 환경의 current-source mount에서 core 458개를 실행해 기능
  assertion 실패는 0개였다. 이미지에 `git` 실행 파일이 없어 과거 signature
  비교 2개만 환경 오류였다.
- 새 Discord 이미지에서 continuity/restart/guild reset/retention/
  observability 42개와 실제 `main.py` crash/restart 1개를 통과했다.
- 재계산 self-hash 변조, 과거 generation rollback, active head 뒤 checkpoint
  삭제, 정확한 한 generation head-lag 복구, v1 anchoring과 v2 migration을
  합성 검증했다. 이미지의 `compileall`과 `pip check`도 통과했다.
- 실제 Discord bot은 시작하지 않았고 실제 continuity artifact도 없었으므로
  사용자 대화를 생성·변경·복구하지 않았다.
- bundled Python에서 correction 15개와 memory discovery 131개를 통과했다.
- 공식 Bot API Python 3.11 환경의 current-source mount에서 correction/API
  24개와 runtime 364개(skip 2)를 통과했다.
- 새 Bot API 이미지에 구워진 source와 read-only test mount로 correction/API
  24개를 통과했다. 새 Bot API와 Control Page 이미지의 `compileall`,
  `pip check`도 통과했다.
- hash/event 수정, chain tail 삭제, legacy prefix 수정, 같은 프로세스 thread와
  별도 프로세스 writer 경쟁, commit 뒤 lagging head 복구를 합성 검증했다.
- 배포 뒤 두 새 컨테이너는 위 digest로 healthy, restart count 0이고 공식
  `check_docker_runtime.ps1 -IncludeLocalBridge`가 다시 통과했다.
- 공식 Python/aiohttp 이미지의 read-only source mount에서 UI action/CSRF/
  retention 집중 테스트 52개, runtime 전체 361개(skip 2), UI 전체 154개
  (skip 7)를 통과했다.
- Vision 전체 102개는 새 UI action/기존 observation 기능 assertion 실패
  0개였고, Linux가 `WindowsPath`를 만들 수 없는 기존 native OCR platform
  오류 1개만 남았다. 같은 native OCR 6개는 Windows에서 통과했다.
- stale observation, 30초 token 만료·재사용·restart 비복구, changed
  foreground/target/bounds, disabled target, 임의 필드, executor tampering,
  세 postcondition, 실행 후 `outcome_unverified`, 감사 privacy/retention을
  합성 검증했다.
- 새 Bot API와 Control Page 이미지 내부에서 실제 module/route/asset smoke,
  `compileall`, `pip check`와 local-only sentinel Compose config를 통과했다.
- 배포 뒤 실제 브라우저 DOM에서 새 승인 panel과 세 postcondition option,
  `RUNNING`을 확인했고 warning/error console log는 0개였다. 안전한
  missing-target preview 외 실제 apply/UI invoke는 수행하지 않았다.
- bundled Python의 freshness·host client·LLM context 집중 테스트 32개와,
  공식 Bot API 이미지에 구워진 소스의 집중 테스트 51개를 통과했다.
- Pillow와 aiohttp가 함께 있는 공식 Discord 테스트 환경의 current-source
  mount에서 Host Vision Bridge 6개를 통과했다.
- 전체 Vision discovery 78개는 기능 assertion 실패 0개였고, Linux에서
  테스트가 `os.name`을 Windows로 patch한 뒤 `WindowsPath`를 생성하는 기존
  platform 오류 1개만 남았다.
- 최종 Bot API·Vision 이미지의 `compileall`, `pip check`, Compose config를
  통과했다.
- 검증 중 실제 화면 캡처, UI mutation, 마이크·Discord·Minecraft 시작은
  수행하지 않았다. 실행 중인 Bot API·Vision은 기존 healthy 이미지와
  restart count 0을 유지했다.
- 새 공식 Discord 이미지 내부 소스로 자율 승인·restart 비복구·exact
  evidence·실행 중 grant 교체/만료·audit write 실패·Discord status·Minecraft
  lease·실제 `main.py` crash/restart 통합 테스트 96개를 통과했다.
- 전체 core 452개는 기능 assertion 실패 0개였다. 이미지에 `git` 실행 파일이
  없어 과거 main signature 비교 2개만 환경 오류로 남았다.
- 새 Discord 이미지의 `compileall`과 `pip check`를 통과했다.
- 실제 Discord bot과 Minecraft/Voyager는 시작하지 않았으므로 live action
  effect 검증은 수행하지 않았다.
- 새 공식 Discord 이미지에서 guild reset, continuity, Discord command wiring,
  Runtime Errors, opt-in real-main startup/crash-restart 집중 테스트 68개를
  통과했다.
- 별도 두 프로세스 테스트는 durable guild marker 직후와 runtime clear 직후
  각각 `os._exit`로 중단했다. 재기동 시 초기화 대상 guild는 복구되지 않았고
  다른 guild의 완료 턴과 active follow-up은 유지됐다.
- 공식 Discord 이미지의 전체 core 440개는 기능 assertion 실패 0개였다.
  이미지에 `git` 실행 파일이 없어 과거 main signature 비교 2개만 환경 오류로
  남았다.
- 새 Discord 이미지의 `compileall`과 `pip check`를 통과했다.
- 실제 Discord bot은 시작하지 않았으므로 인증된 live guild 초기화 E2E는
  아직 수행하지 않았다.
- bundled Python에서 memory discovery 125개와 UI discovery 149개,
  correction/UI focused 23개, `compileall`, Control Page 인라인 JavaScript
  parse와 `git diff --check`를 통과했다.
- 기존 Bot API Python 3.11 이미지의 read-only source mount에서 correction
  module/API 16개와 runtime 342개를 통과했다. runtime skip은 2개다.
- 전체 discovery는 1,614개를 실행해 기능 assertion 실패 0개였다. 경량
  Bot API 이미지에 없는 git, Pillow, Discord와 Voyager 선택 패키지 때문에
  기존 import/platform 오류 17개와 skip 17개가 남았다.
- 새로 빌드한 이미지 내부 소스와 HTML 자체를 대상으로 correction/API/UI
  30개를 통과했다. 새 Bot API와 Control Page 이미지의 `compileall`과
  `pip check`, local-only sentinel Compose config도 통과했다.
- 배포 뒤 공식 `check_docker_runtime.ps1 -IncludeLocalBridge`가
  Control Page, Bot API, Main/Router/Sub LLM, TTS, STT, Vision 및 Windows
  Local I/O Bridge를 모두 준비 상태로 판정했다.
- bundled Python에서 memory discovery 116개와 provenance/UI focused
  34개, `compileall`, Control Page 인라인 JavaScript parse와
  `git diff --check`를 통과했다.
- Bot API Python 3.11 이미지에서 manual provenance API 5개,
  runtime 340개, UI 148개를 통과했다. UI 6개와 runtime 2개는 명시적으로
  skip됐다.
- 같은 이미지의 전체 discovery는 1,584개를 실행해 기능 assertion 실패
  0개였다. 경량 이미지에 없는 git, Pillow, Discord, requests 의존성 때문에
  기존 import/platform 오류 17개와 skip 17개가 남았다.
- 수동 provenance 테스트는 content-free coverage와 거부 카운터,
  손상 카운터 처리, exact/ambiguous/direct target 분리,
  숨김·격리·legacy/internal·미접지·cycle source 거부, target/source/
  unrelated full-graph 충돌 거부, 제목·본문 byte 안정성을 검증했다.
- 배포 뒤 실제 API 집계와 저장 보고서 privacy를 확인하고, 실제 브라우저 DOM과
  console을 검증했다. 공식
  `check_docker_runtime.ps1 -IncludeLocalBridge`도 통과했다.
- 새 Bot API Python 3.11 이미지에서 전체 `unittest discover` 1,585개를
  실행했다. 기능 assertion 실패는 0개였다.
- 이미지별 의존성 또는 OS 차이로 발생한 10개 import/platform 오류는 Windows,
  Discord, Codex Gateway/Voyager 소유 환경에서 각각 재실행해 모두 통과했다.
- 관련 Fast Control, capability, repair, launcher, local mic 테스트 168개 통과
- Vision 전체 중 Host bridge를 제외한 58개와 Host bridge 4개 통과
- 새 accessibility/Fast Context/Fast Control/vision quality 회귀 134개를
  Windows Python에서 통과했고, 배포 Bot API 이미지의 read-only source
  mount에서 핵심 102개를 다시 통과
- 메모리 계층 85개와 Control/API/runtime/UI 계층 89개, 합계 174개를
  Bot API Python 3.11 이미지의 read-only source mount에서 통과
- 별도 focused run 91개에서 충돌 거부, 원자적 쓰기 실패 시 원본 보존,
  user-edit provenance, CSRF, 새 Python 프로세스 재시작 복구를 통과
- 최종 memory discovery 95개와 관련 API/runtime/UI 61개 통과
- 이번 provenance 감사 변경 뒤 memory discovery 102개 통과
- 공식 Bot API Python 3.11 이미지에서 전체 memory runtime API 13개,
  provenance/delete/edit/UI focused 26개 통과
- audit privacy/UI/source-hygiene focused 21개와 Control Page 인라인
  JavaScript 파싱 통과
- 이번 conflict-safe backfill 변경 뒤 bundled Python에서 memory discovery
  108개와 focused backfill/audit/UI 26개, compileall과 Control Page 인라인
  JavaScript 파싱을 통과했다.
- 최종 Bot API Python 3.11 이미지 환경에서 memory discovery 108개,
  memory runtime API 15개, provenance/delete/edit/UI focused 28개를
  read-only source mount로 통과했다.
- 같은 이미지의 전체 discovery는 1,593개를 실행해 기능 assertion 실패
  0개였다. 이미지에 없는 Discord/Pillow 등 소유 환경 의존성과 platform
  차이로 기존 17개 import/platform 오류가 남았고 skipped는 17개였다.
- 배포 직전 Compose config는 Discord token이 없는 shell에서는 필수 변수
  검증으로 거부됐고, local-only sentinel을 명시한 배포에서는 설정을 정상
  해석했다. Bot API를 먼저 정상 종료해 Minecraft owner claim 반납을 확인한
  뒤 두 컨테이너만 교체했다.
- 배포 뒤 Bot API와 Control Page는 새 image digest로 `healthy`, restart
  count 0이며 나머지 Main/Router/Sub LLM, TTS, STT, Vision도 계속
  `healthy`다.
- 전체 discovery는 1,575개를 실행해 기능 assertion 실패 0개였다. Bot API
  경량 이미지에 없는 git, Pillow, Discord, requests/gymnasium 및 Linux에서
  실행할 수 없는 WindowsPath 때문에 생긴 17개 import/platform 오류는
  Discord/Vision/Windows와 직접 소유 모듈 환경에서 각각 재실행했다.
- 파생 철회 전용 테스트는 단일-source 연쇄 tombstone, multi-source와 하위
  파생 quarantine, 영향 fingerprint 409, content-free state, 살아 있는
  source만의 topological 재합성, user edit 해제, hot-context 원자 갱신을
  통과했다.
- root tombstone `fsync` 직후 별도 Python 프로세스를 강제 종료한 뒤 새
  프로세스가 source 파일·인덱스·graph를 fail-closed로 정리하고
  cascade/quarantine을 복구하는 테스트를 통과했다.
- 실제 `main.py` Control Page 기동 및 강제 종료 뒤 대화 연속성 복구 smoke
  2개 통과
- Python `compileall`, 모든 `docs/assets/*.js`의 `node --check`, 변경
  PowerShell parser, `git diff --check` 통과
- `docker compose config --quiet` 통과
- 새 Bot API와 Vision 이미지 `pip check` 통과
- 새 Bot API `compileall`, Bot API/Control Page `pip check`, Compose
  config 통과
- 한글 경로 allowlist 빌드, Bot API owner-claim 정상 해제, 이미지 교체,
  전체 launcher readiness E2E 통과
- 배포 후 공식 `check_docker_runtime.ps1 -IncludeLocalBridge` 통과

## Operational boundaries

- Bot API: `127.0.0.1:8798`
- Control-Page: `127.0.0.1:8799`
- Codex Gateway: `127.0.0.1:8787`
- Control-Page 변경성 요청은 CSRF 세션 계약을 사용한다.
- 런타임 repair는 preview와 apply를 분리하며, preview만으로 프로세스를 시작하지 않는다.
- Host Vision 요청은 `runtime_artifacts/host_vision/`의 exact-schema queue만
  사용하고, Host Supervisor가 소유한 Local I/O Bridge만 화면을 캡처한다.
- Host UI Action 요청은 `runtime_artifacts/host_ui_action/`의 exact-schema
  queue만 사용한다. 경계는 배포됐지만 실제 action 실행 횟수는 0이다.

남은 문제는 [ACTIVE_RISKS.md](ACTIVE_RISKS.md)에만 유지한다.
