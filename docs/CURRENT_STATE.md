# Evelyn Current State

Document status: **Current**
Last reviewed: 2026-08-09 KST
Source branch: `codex/omnivoice-tts-cutover`, memory provenance hardening increment

이 문서는 현재 확인된 사실만 기록한다. 목표 구조와 과거 계획은 다른 설계/계획 문서를 사용한다.

## Source state

- 전체 프로젝트 감사의 즉시 항목을 별도 안정화 브랜치에서 처리 중이다.
- `main.py` 분해는 목표 범위에 도달했다.
  - 현재 2,500줄이며 원래 목표 범위인 1,500~2,500줄 안에 들어왔다.
  - top-level/nested 함수 정의, `global`/`nonlocal`, dependency-builder 함수 정의는 모두 0개다.
  - 기능 구현, 판정, 상태 mutation은 owner runtime/composition 모듈에 있고 `main.py`는 설정 import,
    객체 생성, 명시적 typed dependency wiring, Discord 등록, runtime 진입을 담당한다.
  - dependency wiring은 암시적 registry나 `globals()` 우회 없이 한 줄 최대 두 인자, 158자 이하로 유지한다.
- 핵심 준비 상태와 선택 기능 준비 상태를 분리했다.
  - `ok`: 필수 핵심 서비스 준비 여부
  - `fullyHealthy`: 선택 기능을 포함한 전체 건강 여부
  - Voyager/Mindcraft HTTP 응답과 실제 Minecraft 자율행동 준비 여부도
    `minecraft_autonomy.readiness.v1`로 분리했다.
  - world lease, runner, fresh telemetry, Minecraft 연결, gated task
    contract와 active autonomy를 exact boolean dependency로 재계산한다.
  - task contract는 `evelyn_goal_manager` 명령 게이트와
    `explicit_postcondition` 결과 검증을 모두 요구한다.
  - 누락·손상·상위 상태와 모순된 Mindcraft 계약은 fail-closed하며,
    Control Page와 Runtime Health는 같은 validator를 사용한다.
  - Bot API의 Minecraft 단일-owner authority는 stable `owner_claim.lock`에
    process lifetime 동안 유지하는 exclusive OS lock이다. `owner_claim.json`과
    timestamp는 진단 heartbeat이며 살아 있는 owner의 takeover 근거가 아니다.
    claim nonce는 status/proof epoch mismatch를 거부하는 fencing 값일 뿐 owner
    선출 근거가 아니다. 정상 shutdown은 shielded runtime cleanup 뒤 kernel lock을
    반납하고 crash나 process exit에서는 OS가 lock을 해제한다. 새 owner는
    nonce/token을 회전하고 lease를 복구하지 않는다. 15초는 stale service/status를
    거부하는 runner guard다.
    별도 `world_action.lock`은 Mindcraft/Voyager의 proof 검증부터 start/goal
    effect commit까지와 successor epoch publication을 직렬화한다. busy 또는
    unavailable이면 503으로 fail-closed하며 timestamp fallback은 없다.
    Bot API 컨테이너에는 31초 artifact fence와 외부 runtime 정리를 모두 끝낼
    60초 stop grace를 적용한다.
  - 로컬 image-refresh launcher는 기존 Bot API 컨테이너의 정지를 확인하되,
    crash 뒤 남을 수 있는 `owner_claim.json`은 경고만 표시한다. successor의
    process-lifetime OS lock 획득이 재기동 시 유일한 fail-closed owner 판정이다.
- 현재 worktree의 Minecraft world-action lease source 계약은 감사 내구성을
  실행 권한에 포함한다.
  - status/proof consumer는 `auditReady`와 `statusReady`가 모두 정확한
    boolean `true`일 때만 lease를 유효하게 인정한다. 누락·`false`·비-boolean
    값은 각각 `minecraft_world_lease_audit_unavailable` 또는
    `minecraft_world_lease_status_write_failed`다.
  - event row는 append 뒤 flush와 `fsync`가 끝나야 기록 성공이다. POSIX에서
    새 daily journal을 만들 때는 events directory entry sync도 성공해야 한다.
    초기화, lease 발급, runner start와 goal mutation은 필요한 event를 내구
    기록하지 못하면 fail-closed하고 active lease/process capability를 제거한다.
  - stop, revoke, watchdog cleanup과 shutdown은 감사 저장소를 잃어도 안전을
    위해 계속 실행한다. 다만 정지가 실제로 확인돼도 audit unavailable과
    `manual_intervention_required`를 반환·보고하며 감사된 성공으로 바꾸지 않는다.
  - public status artifact commit 실패도 lease/delegation capability를 제거하고
    실행 중일 가능성이 있는 runtime을 force-stop한다. fixed 결과는
    `minecraft_world_lease_status_write_failed`와
    `manual_intervention_required`이며 stale status만으로 권한을 복구하지 않는다.
  - 내부 mutation endpoint의 unauthenticated 401은 `leaseStatus`를 포함하지
    않는다. remote delegate는 status 누락·손상, 오류, transport failure와
    cancellation에서 기존 active cache를 즉시 inactive error로 지운다.
  - status와 audit journal에는 raw goal, transcript, Minecraft chat, token과
    임의 arguments를 저장하지 않는다.
  - 직전 durable-audit source snapshot은 bundled Python의 Minecraft 115개(skip 7), runtime
    513개(skip 4), 인접 Discord/Mindcraft/UI 39개 회귀를 통과했다. 실제
    Minecraft connect/goal/stop E2E는 아직 확인된 사실로 기록하지 않는다.
  - 현재 lifetime-lock increment는 bundled Python의 Minecraft 156개(skip 8),
    runtime 518개(skip 4)를 통과했다. 후속 source-verification 정리에서 stale
    opaque note ID 기대값, Windows SQLite 연결 수명, Voyager 경량 import의 선택
    `requests` 결합을 각각 수정했다. 전체 discover 2,482개는 실패 없이 통과했고
    skip은 18개였다. `compileall`, 모든 Control Page asset JavaScript 구문 검사와
    `git diff --check`도 통과했다. 혼합 환경 `pip check`의 기존 platform-tag 6건과
    실제 main/Minecraft/Docker smoke는 미검증이다.
- 음성 P0 검증 FSM과 로컬 재생 연속성 경계를 강화했다.
  - 현재 surface와 barge-in에 연결된 interrupt 단계만 이벤트를 받을 수 있다.
    지난 단계 재시도와 재생 완료 전 청취 확인은 서버에서 거부한다.
  - STT 불일치, 중복 final/turn/playback/interrupt, 완료·취소 동시 관측,
    무음 구간의 모든 음성·재생 활동은 즉시 해당 시도를 실패시킨다.
  - 재생 직전에 일반 큐에 들어온 발화는 TTS 종료·cooldown 때문에 폐기하지
    않는다. 재생 중 발화는 기존 barge-in 큐와 VAD/RMS/화자 검증을 유지한다.
  - clone TTS 실패 시 같은 playback owner 안에서 `auto` voice로 한 번
    fallback하므로 재귀 claim 충돌과 이중 재생을 만들지 않는다.
  - 현재 소스에는 로컬 STT admission 경계가 추가됐다. 초기 발화는 정확한
    선행 `이블린`, 성공 소비 뒤 일반 follow-up은 45초를 요구하며,
    shutdown/restart, mic, Minecraft 변경 의도는 follow-up 중에도 새 호출을
    요구한다.
  - admission capability는 10초 일회성이고 bridge instance, turn,
    canonical forward-text SHA-256, mode와 exact validation attempt에 묶인다.
    admission lock 안에서 모든 binding을 검증한 뒤 canonical
    `[bridgeInstanceId, turnId]`의 ingress journal claim을 먼저 durable commit하고,
    exact frozen receipt를 검증한 뒤에만 token·replay ledger·follow-up·count를
    한 번에 확정한다. claim 실패나 잘못된 receipt는 이 admission 상태를 바꾸지
    않아 같은 token으로 안전하게 재시도할 수 있다. 소비된 turn은 120초 replay
    ledger로 stream/non-stream 이중 실행을 막고, validation-bound 소비는 일반
    follow-up 창을 열거나 갱신하지 않는다.
  - 공개 Control Page chat은 source를 `control_page`로 고정하고 브라우저의
    `local_bridge` 주장과 admission/bridge/validation 필드를 Bot API에
    전달하지 않는다. 공개 admission status는 count와 고정 reason만 남기며
    audio, transcript, raw text, token을 포함하지 않는다.
  - 이 변경은 현재 worktree의 소스·합성 계약이며 실행 중인 Bot API/Bridge를
    교체하거나 실제 마이크를 켜지 않았다. 실제 10턴·barge-in·silence live
    hardware 증거는 여전히 없다.
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
  - awaiting follow-up도 `active_until`을 넘으면 실행 중 text/voice admission에서
    inactive다. 따라서 unanswered 질문이 ambient 입력을 무기한 여는 경로가 없고,
    fresh restart의 TTL 복구 판정과 같은 결과를 낸다.
  - 외부 전달이 끝난 완료 턴은 1초 periodic writer를 기다리지 않고 즉시
    durable commit한다. Discord text는 commit 뒤 선택적 TTS를 실행하므로
    TTS 실패가 이미 전달된 답변을 history에서 잃게 하지 않는다.
  - Discord text 생성이 실패해도 고정 `text_turn_failed` 응답 전송에
    성공하면 그 실패 턴을 history와 checkpoint에 즉시 남긴다. fallback
    전송이 실패하거나 모호하면 기록하지 않고, 기록 실패 때문에 같은
    fallback을 다시 보내지 않는다.
  - Control Page 일반·검색, 검색 후속, 자율 후속, Discord 명령, 음성 재생
    완료도 같은 commit 계약을 사용한다. 실패 시 중복 전송하지 않고
    `conversation_continuity_commit_failed`만 기록한다.
  - Discord와 local speaker 음성은 playback pipeline이
    `playback_completed=false`를 명시한 stale validation·무재생·부분 재생 결과를
    완료로 확정하지 않는다. Local streaming은 전역 누계가 아니라 이 턴의 exact
    queued/played chunk 수를 우선 사용한다. 고정 전달 실패와 user-only continuity
    경로로 보내며 single·streaming 양쪽을 검사한다.
  - Local Bridge의 `failed|partial|cancelled` playback ACK도 accepted user row만
    ingress `turnId`로 checkpoint하고 assistant text·receipt는 버린다. checkpoint 뒤
    journal 삭제가 실패하면 exact current turn과 마지막 user row로 재시작에서 한 번
    정리하며, 다음 Fast prompt는 이를 미응답 문맥으로 소비한다.
  - Voice search follow-up의 최초 전달과 재시작 복구도 같은 playback metrics를
    소비한다. 무재생 최초 전달은 history/continuity를 commit하지 않고, 복구 중
    무재생은 `delivery_uncertain`으로 보존해 자동 재전송하지 않는다.
  - 취소된 `TurnScope`에 current task가 늦게 attach되면 즉시 거부하고, 새
    background task는 coroutine 본문 실행 전에 취소한다. 음성 worker의 처리
    예외 로그는 exception type만 남기고 원문 메시지는 기록하지 않는다.
  - Discord 명령 19개와 권한 거부 응답은 composition이 주입한 단일
    post-delivery context owner를 통과한다. 성공한 plain-text 전송만
    history와 checkpoint에 한 번 기록하며, 전송 실패는 기록하지 않고
    Minecraft의 이전 수동 기록도 제거해 이중 commit을 막는다.
    이 응답은 저장 기억을 사용하지 않은 `not_used` receipt로 기록되어 공용
    history filter 뒤에도 assistant 완료 행이 남고 미응답 user tail로 오인되지 않는다.
  - 실제 Control Page가 호출하는 standalone Bot API도 더 이상
    process-local `CHAT_MESSAGES`만 사용하지 않는다. 별도 single-writer
    `fast_control_continuity` v2 chain에 일반·stream·background의 정상·고정
    실패 턴을 최대 30분/40개로 저장하고, 시작 시 UI와 다음 LLM context로
    복구한다.
  - 두 owner는 계속 각 파일의 single writer다. 새 read-only verifier가
    v2 hash/current head, TTL, privacy policy와 revocation ledger를 모두
    검증한 snapshot만 Discord/Main 공통 LLM context와 Fast Control
    LLM/tool planner의 최근 8개 대화로 양방향 합친다.
  - 교차 연결은 명시한 개인 Discord guild/user scope가 모두 일치할 때만
    활성화된다. 다른 member/server는 제외하고, 설정 누락·변조·lagging
    head·손상·stale 상태는 기존 surface history만 쓰는 fail-closed다.
  - 더 최신 empty owner 또는 target scope가 비어 있는 더 최신 checkpoint를
    reset boundary로 취급해 다른 owner의 오래된 대화가 삭제 뒤 되살아나지
    않는다. 공개 상태는 count/generation/고정 code만 포함한다.
  - 각 prompt merge는 `cross_surface_continuity.merge.v1`의 process-local
    증거를 남긴다. Main/Discord는 턴 metrics, Fast Control은 마지막 merge
    status에서 state, owner generation/count, ordering, latency만 공개한다.
    원문·사용자/세션 ID·hash·경로와 임의 private 필드는 exact-field
    projection에서 제거하고 artifact에는 저장하지 않는다.
  - 현재 surface owner가 손상돼 reset/delete 경계를 검증할 수 없으면
    정상인 상대 owner도 주입하지 않는 fail-closed 규칙을 적용한다.
  - Fast Control background action은 시작 전에
    `fast_control.action-recovery.v3` content-free 표식을 durable 기록한다.
    최종 성공·실패 답변은 Fast continuity owner 잠금 안에서 예상 generation과
    결합하며, receipt가 확인된 뒤에만 표식을 제거한다.
  - 실행 중 crash는 고정 중단 안내를 한 번 durable commit하고 원래 action을
    자동 재시도하지 않는다. 최종 답변 commit 뒤 표식 제거 전 crash는 current
    generation으로 전달을 확인해 거짓 중단 안내를 추가하지 않는다.
  - action commit 실패는 표식을 `running`으로 되돌려 이후 일반 턴의 같은
    generation을 action 결과로 오판하지 않는다. journal 손상·쓰기 실패는
    새 장시간 작업과 안전하지 않은 continuity generation 전진을 fail-closed
    한다. 공개 상태에는 원문·tool evidence·경로가 없다.
  - action journal의 generation/이전 hash/현재 hash와 별도 content-free
    durable head가 진행 표식이 생성된 chain의 단일 파일 삭제·rollback을
    검출한다. journal이 head보다 정확히 한 generation 앞선 유효 chain일 때만
    head-write crash로 복구하며, 기존 v1은 raw byte hash generation 0 anchor
    뒤 다음 mutation에서 v3로 전환한다.
  - 각 action marker는 시작 당시 Fast continuity generation을 함께 저장한다.
    마지막 복구 안내가 이 generation보다 실제로 뒤에 있을 때만 이번 action의
    안내로 인정하므로 이전 동일 안내를 새 action에 재사용하는 ABA 오인을
    막는다. v1/v2 pending marker는 보수적으로 새 안내 뒤 v3로 전환한다.
  - 모든 전달 surface는 callback 반환만으로 durable 성공을 선언하지 않는다.
    status schema, ready state, current head, verified integrity, rollback
    protection, 양수 generation/session count와 이번 commit의 성공 metric을
    검증한 `conversation_continuity.commit-receipt.v1`만 받는다.
  - receipt는 exact commit target에도 결박된다. writer는 요청된 session을
    `maxSessions` 밖에서도 포함하고, turn ID까지 current checkpoint/head에서
    재검증한다. 자율 후속과 Discord 명령도 전달마다 새 turn을 발급하며, 공개
    지표에는 ID 없이 `lastTargetVerified`만 남긴다.
  - 부분·legacy·손상 status는 이미 전달된 답변을 다시 보내지 않되
    continuity 실패로 남긴다. 자율 후속의 generation 0 오기록도 실제
    `checkpointGeneration` receipt로 수정했다.
  - Discord reference fallback은 로컬 reference 생성 실패 또는 비모호한
    Discord 4xx 거부에서만 일반 메시지를 한 번 보낸다. timeout, 연결 오류,
    상태 없는 예외, 5xx와 `408|409|425|429`처럼 첫 요청의 성공 여부가
    모호하면 재전송하지 않아 같은 답변이 두 번 전달되는 창을 닫는다.
  - 완료 턴 durable commit은 process-local 최근 성공 256개의 지연을
    content-free 상태로 계측한다. 20개 전에는 `warming`, 이후 p95가
    100ms를 넘으면 `conversation_continuity_commit_latency_high` 경고다.
    시도·성공·실패 횟수, last/p50/p95/max와 고정 코드만
    `conversation_continuity.status.v1` 및 Runtime Errors에 공개한다.
- 공개 오류 경계는 `public_error_contract.py`의 고정 코드·문구를 사용한다.
  - Discord 명령/text, Control Page, Fast Control chat/stream/background
    action, runtime repair, mic/bridge와 Minecraft snapshot이 예외 메시지,
    내부 URL, filesystem 경로, token-like 문자열을 응답에 복사하지 않는다.
  - status와 task event의 오류 코드는 구문 검증하며 알 수 없는 문자열은
    surface별 고정 fallback으로 바꾼다.
  - 운영 로그도 예외 원문 대신 고정 event와 exception type만 남긴다. Router
    fallback metadata는 `router_failed`, Local Bridge turn status는
    `turn_pipeline_failed`만 보존한다.
  - Main/Fast tool decision의 `failed` evidence는 serialization에서
    `<tool_name>_failed`로 고정한다. Main vision 예외 metrics도
    `vision_runtime_error`만 남겨 다음 prompt와 turn metrics에 원문을 복사하지 않는다.
  - Control Page legacy runtime service probe의 Bot API TCP/HTTP, Voyager,
    Codex와 전체 refresh 오류도 exact allowlist 코드만 공개한다. 최종 payload
    builder가 알 수 없는 error/login 문자열을 generic 코드로 바꾸므로 내부
    URL·경로·token-like 원문이 `services`로 다시 들어오지 않는다.
  - Codex readiness는 gateway HTTP의 `ok`가 아니라 exact
    `backendReady is true`를 요구한다. gateway만 살아 있고 credential/CLI
    backend가 준비되지 않은 상태는 fail-closed로 `codexReady=false`다.
  - Main/Voice LLM의 runtime status context는 Codex/Voyager artifact와
    error log 원문을 넣지 않는다. `runtime.recent-error.v1`의 exact
    owner/code/coarse age bucket만 최대 3개 렌더링하고 최종 consumer가
    allowlist를 다시 검증한다.
  - Runtime Health collector의 raw probe payload는 내부 readiness·capability·
    diagnostic 합성까지만 사용한다. Bot API와 Control Page cache owner는
    `runtime_health.public.v1` 폐쇄형 projection을 거친 결과만 브라우저에
    제공한다. probe target/payload/error, host 설정, artifact 경로, PID,
    장치명과 임의 legacy/observability 확장 필드는 공개되지 않는다.
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
  - executor 관찰 예외는 `autonomy.failure.v1`의 고정
    `autonomy_executor_observe_failed` marker로만 판단 상태에 들어간다.
    예외 메시지, token-like 문자열, 내부 URL과 filesystem 경로는 저장하지
    않는다.
  - action executor 예외는 고정 `autonomy_executor_execute_failed`인
    `failed/verified=false` 결과로 action audit에 남고 plan cursor를
    전진시키지 않는다. 그 밖의 cycle 예외는 `autonomy_cycle_failed`다.
  - legacy 영속 상태를 읽을 때와 다시 쓸 때 raw `last_error` 및
    `executor_errors`를 정규화하며, Discord와 Control Page 최종 출력도 같은
    exact allowlist를 다시 검사한다.
- Codex Gateway는 bearer token 외에도 Docker 격리와 verified tool-access gate를
  요구한다. 기본 Minecraft 경로에서는 시작·token 조회·network 호출이 0이며,
  미검증 `/codex/action`은 subprocess 생성 전에 고정 503으로 닫힌다.
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
    기록한다. 레거시·손상 파일은 감사 lease 아래 닫힌 enum의 canonical
    집계로 내구성 있게 원자 교체하고, 유효하지 않은 숫자는 0으로 처리한다.
    교체 실패는 감사 보고서를 남기지 않고 고정 integrity 오류로 닫힌다.
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
- 실제 답변 문맥은 `memory.context-receipt.v1`을 함께 만든다.
  - receipt에는 모델에 제공된 vault note ID, memory version, retrieval mode,
    cache 여부, source-type별 수와 legacy 항목 수만 남긴다. 기억 본문, 제목,
    경로, transcript와 사용자 입력은 넣지 않는다.
  - Discord/Main의 turn summary는 `provided|empty|not_requested`,
    `attributed|partial|unattributed`, 제공 note ID/count, legacy count와
    hot-context 상태를 additive 필드로 기록한다. 이는 “모델에 제공됨”의
    증거이며 모델이 실제 답변에 사용했다고 과장하지 않는다.
  - Fast Control 일반·stream 응답과 해당 assistant chat card도 같은
    content-free receipt를 반환한다. 사용자 주입 memory provider처럼 exact
    note ID를 증명하지 못하는 경로는 `unattributed`로 표시한다.
  - 새 raw 대화 memory row는 현재 turn ID에서 만든 stable `evidence_id`,
    `source_turn_id`, 고정 `conversation_turn` kind를 guild/room/person/session
    raw JSONL에 동일하게 보존하고 JSONL mirror가 활성인 호출도 같은 필드를
    유지한다. ID에는 발화 내용이 들어가지 않으며 allowlist 형식에 맞지 않는
    metadata는 저장하지 않는다.
  - receipt와 turn summary는 실제 prompt에 선택된 raw row의 evidence/turn
    ID를 domain-separated `opaque-evidence-*`/`opaque-turn-*`로 투영한 값과
    attributed/unattributed legacy 항목 수를 공개한다. 새 rolling
    summary는 내용 SHA-256에 묶인 sidecar에 파생 evidence ID와 실제 Summary
    LLM 입력 evidence/turn ID를 저장하고, 내용이 따로 바뀌면 provenance만
    fail-closed로 버린다. 새 facts/questions도 같은 실제 입력 ID와 별도 파생
    evidence ID를 JSONL과 mirror에 보존한다. 다만 이 turn-level provenance에는
    vault note 삭제 현재성을 증명하는 receipt가 없으므로 stored summary/fact/question과
    assistant raw는 prompt 입력에서 보류한다. exact user raw와 현재 턴만 사용한다.
  - receipt와 turn summary는 새 파생 항목, 직접 입력 evidence와 source turn의
    안정적인 opaque projection만 content-free 필드로 공개한다. 원본 ID는
    content-bearing/source sidecar 안에만 남는다. 공개 projection은
    Summary/Main LLM에 “제공된 입력”을 상호 연관하는 계보이며 모델이 실제로
    사용했거나 기억 내용이 사실임을 뜻하지 않는다. 기존
    raw/summary/fact/question은 내용을 보고 근거를 추측해 소급 부여하지 않고
    계속 `partial|unattributed`로 드러낸다.
  - 최종 Main/Fast prompt 경계는 `memory.context-use.v1`을 적용한다. 모든 기억은
    명령이 아닌 데이터로 감싼다. evidence ID/count가 완전한 `attributed` 결합
    문맥만 본문을 모델에 제공한다. 기존 raw/summary/fact/question이나 vault
    문맥이 섞여 `partial|unattributed`이면 component별 안전한 분리를 추측하지
    않고 본문 전체를 보류하며 고정 `MEMORY_WITHHELD_RULE`만 제공한다. 모델은
    보류된 기억의 구체적인 내용을 보았다고 주장할 수 없고, 꼭 필요할 때만
    사용자에게 관련 정보를 다시 말하거나 직접 확인해 달라고 요청한다.
  - producer가 선언한 `groundingState`는 그대로 신뢰하지 않는다. 제공 note ID와
    legacy evidence ID가 실제 count와 함께 있는지 최종 경계에서 다시 계산하고,
    근거 ID 없이 `attributed`를 주장한 문맥은 `unattributed`로 강등한다.
  - recall provenance는 exact `memory.provenance.v1` schema, 필수 field type과
    canonical source type을 cache와 receipt에서 같은 validator로 재검사한다.
    손상 recall이 정상 pinned hot-context와 섞여도 hot note ID를 빌려 전체 문맥을
    `attributed`로 승격하지 못하며, supplied ID를 비워 최종 prompt에서 전체
    기억 본문을 보류한다.
  - memory prompt는 ContextBuilder의 1,800자 제한보다 작은 1,680자로 먼저 제한한다.
    잘림이 발생하면 잘린 본문과 개별 ID의 대응을 증명할 수 없으므로 note/legacy
    귀속을 모두 버리고 본문 전체를 보류한다. receipt와 turn summary에는
    `state=withheld`, `promptMemoryWithheld`, `promptEvidenceDiscarded`, 보류된
    note/legacy/item count와 길이 초과 시 `promptTruncated`·잘리기 전 candidate
    count만 남긴다. 실제 supplied ID/count, 본문과 transcript는 기록하지 않는다.
  - Control Page의 provenance 감사 응답·저장 보고서·UI는 별도
    `memory.legacy-context-coverage.v1` 집계를 제공한다. guild/room/person/session
    scope의 저장 summary/raw/fact/question을 prompt와 같은 evidence 규칙으로
    재검사하고 전체/attributed/확인 전용 수와 kind/scope/storage별 수만 공개한다.
    ID, scope key, 파일명·경로, 본문과 transcript는 공개하지 않으며 손상 JSON,
    누락·불일치 evidence, 읽기 실패와 unsafe location도 count로만 남긴다.
    저장 row 기준이라 hot/일자별 mirror를 함께 셀 수 있고 실제 prompt 선택 수는
    아니라는 한계를 계약과 화면에 명시한다.
  - 현재 발화나 답변을 Summary LLM 입력에 포함하면서 유효한 current turn ID가
    없으면 과거 기억의 evidence를 새 결과의 근거로 빌려 쓰지 않는다. 이때 새
    summary/fact/question은 귀속 정보가 없는 확인 전용 상태로 fail-closed한다.
  - Fast Control과 gate를 통과한 Discord 텍스트·음성 turn은
    `/remember <fact>`, `/memory remember <fact>`와 엄격한
    `기억해줘: <fact>` 형식만 직접 사용자 확인 기억으로 저장한다. 일반 대화나
    “기억”이라는 단어가 포함된 문장을 자동 영구 저장하지 않는다.
  - 저장 노트는 surface에 맞는 `control-page-user` 또는 `discord-user` 직접
    출처, 현재 request/accepted turn 참조, 본문 evidence hash와 `confirmed_at`을
    하나의 Markdown write에 함께 남긴다. Discord 메시지 ID는 저장 멱등성에만
    사용하고 공개 근거는 내부 accepted turn ID로 분리한다.
  - 같은 action ID 재시도는 같은 노트와 최초 저장 근거로 멱등 처리하고 다른
    본문으로 재사용하면 거부한다. API/stream/chat/voice 관측 receipt는 exact
    schema의 note ID·상태·turn 참조·확인 시각만 포함하며 기억 본문이나
    transcript를 넣지 않는다. 직접 저장 turn은 일반 Summary LLM memory writer와
    search follow-up을 건너뛰어 같은 발화를 중복 저장하지 않는다.
  - 비동기 memory write-behind는 Summary LLM owner가 명시적으로
    `ok=false`를 반환하면 `completed`로 기록하지 않고 고정
    `long_term_memory_update_failed` 예외를 기존 failed 상태 경계로 전달한다.
    turn task detach는 성공·실패 모두 유지한다. step/event-log 실패 payload와
    30일 보존 JSONL·운영 로그에는 예외 원문 대신 고정 error code와 exception
    type만 남긴다.
  - 저장·중복 성공은 다시 읽은 card의 본문, 직접 사용자 source/source type,
    단일 turn source ref, 본문 SHA-256 evidence, `confirmed_at`과 현재
    recall eligibility를 모두 재검사한 뒤에만 반환한다. 일부 metadata가
    손상된 기존 파일은 성공으로 복구 추정하지 않고 content-free
    `memory_confirmation_write_unverified`로 fail-closed한다.
  - 새 노트는 `memory.user-confirmation.note.v1` marker를 함께 기록한다. recall
    index는 marker, `user-confirmed` tag 또는 고정 storage path로 이 계열을
    다시 식별하고, source/ref/evidence/confirmed timestamp를 매 동기화에서
    재검사한다. 무결성이 깨진 노트는 기존 retrieval cache·FTS·vector·hot
    context에서 제거되며 Control Page에는 `근거 손상`과 content-free blocker로
    표시된다. 사용자가 편집하면 `user-edit` ref와 새 title/body evidence hash,
    새 확인 시각으로 다시 결합한 뒤에만 회상 가능해진다.
  - Control Page 카드의 일반 `확인`은 이제 사용자가 읽은 정확한 note
    `sourceHash`를 필수로 보내고 서버가 edit lock 안에서 현재 revision을 다시
    대조한다. 성공 sidecar는 확인 시각과 `confirmed_content_hash`를 flush/fsync
    뒤 함께 보존한다. 이후 note가 바뀌거나 예전 sidecar에 hash가 없으면 confirmed 표시를
    유지하지 않고 `confirmationState=stale`·재확인 수로 드러낸다. 숨겨진 legacy,
    internal note와 explicit confirmation 무결성이 손상된 note는 확인할 수 없고
    state write 실패도 성공으로 보고하지 않는다. 이 UI review 상태는
    source/evidence를 새로 만들거나 ungrounded 기억을 attributed로 승격하지 않는다.
  - Control Page는 요청마다 request ID를 만들고 Local I/O Bridge는 기존 음성
    turn ID를 일반·stream 요청에 전달한다. 노트 파일명과 공개 ID는 기억 본문
    hash에서 만들지 않아 content-free receipt가 본문 equality oracle이 되지 않는다.
  - pinned hot-context는 현재 recall의 memory version과 정확히 같고 포함 note
    ID가 있는 경우에만 live prompt에 들어간다. 과거 형식, 손상, 삭제/파생
    상태 불일치와 stale version은 fail-closed로 제외한다.

## Deployment state

아래 timestamp claim의 15초 대기·회수 기록은 lifetime lock 도입 전 배포에서
실제로 관측한 역사적 사실이다. 현재 소스의 owner 인수 규칙으로 재해석하지 않는다.
현재 contract의 Docker bind-mount cross-container lock coherence와 crash release는
아직 live 배포 증거가 없다.

- `bd0786d` 소스로 Mindcraft 이미지만 다시 빌드했다.
  - Mindcraft:
    `sha256:56963ab15c8f98a9eee454bfdfe1feff14e118e68b85fe84e812ac278122e667`
  - ESLint 10.8.0, 실제 config dependency와 build-time runtime lint
    smoke가 포함된다.
  - 이미지는 빌드·검증만 했고 실행 중인 Mindcraft 서비스는 시작하거나
    교체하지 않았다.
- `0b054ac` 소스로 Bot API, Control Page, Discord와 Mindcraft 이미지를
  정식 Dockerfile에서 빌드했다.
  - Bot API:
    `sha256:af2dd47c2290cb4a663494cc801d21461e66e832e7851e4d0273974057689baf`
  - Control Page:
    `sha256:7fa972a3c7791e6f1214b4e15071d1215b9a7baeeef425160d321adc75060581`
  - Discord:
    `sha256:1bbf4d34322c6d548bbaf305f88fe024fbb16286f236f95a9623c978584a0850`
  - Mindcraft:
    `sha256:7964e7b21ea9b4efe74b85826f332aa771f973fb5928321c4d590b35c2881334`
  - Bot API와 Control Page만 교체했다. 이전 owner claim의 15초 stale
    guard 안에서 첫 Bot API 기동이 fail-closed 종료됐고, guard 경과 뒤
    같은 컨테이너가 claim을 인계받았다. 두 서비스는 현재 새 digest로
    `healthy`, restart count 0이다.
  - Discord와 Mindcraft 이미지는 빌드·검증만 했으며 실제 서비스를
    시작하지 않았다. LLM/STT/TTS/Vision, 마이크와 Host Bridge도 이번
    작업에서 시작하지 않았다.
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
- 해당 이전 배포 당시 Bot API, Control Page, Main/Router/Sub LLM, TTS,
  STT, Vision 여덟 컨테이너가 모두 `healthy`였고 새 Bot API와 Control
  Page의 restart count는 0이었다.
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
- 해당 이전 배포 당시 Windows Host Supervisor와 Local I/O Bridge heartbeat는
  fresh였고 bridge는 `ready=true`, TTS warmup 완료, Host Vision
  `running`이었다.
- 개인정보 보호 기본값에 따라 로컬 마이크는 비활성 상태다.
- Minecraft/Mindcraft는 기본 local Qwen planner로 지연 시작되며 Codex Gateway를
  요구하지 않는다. 선택적 Gateway와 Discord bot도 사용자 요청 없이 시작하지 않았다.

## Last runtime evidence

2026-07-31 Mindcraft code-generation lint gate 복구 결과:

- 이전 image의 실제 `Coder._lintCode()`는
  `eslint-plugin-no-floating-promise`가 설치되지 않아
  `ERR_MODULE_NOT_FOUND`로 중단되는 것을 재현했다.
- runtime ESLint를 10.8.0으로 올리고 `@eslint/js`, `globals`,
  `eslint-plugin-no-floating-promise`를 exact production dependency로
  고정했다.
- 새 이미지 빌드는 실제 `eslint.config.js`로 정상 코드를 허용하고 선언된
  async 함수의 floating call을 거부한 뒤에만 완료된다.
- 실제 patched `Coder._lintCode()`도 같은 허용·거부 계약을 통과했다.
  config를 찾을 수 없는 cwd에서는 예외를 전파하거나 코드를 실행하지 않고
  고정 문구로 fail-closed했다.
- production audit은 이전 moderate 14/high 5에서 moderate 14/high 0,
  critical 0으로 개선됐다.

2026-07-31 Minecraft functional readiness 배포 후 비파괴 검증 결과:

- 실행 중인 Bot API와 Control Page `/health`는 모두 `ok=true`다.
- 현재 실제 Voyager/Mindcraft는 정지 상태이므로 Control Page의
  `serviceHealth`는 `voyager.state=down`, `voyagerHttpReady=false`,
  `voyagerRuntimeReady=false`, legacy `voyagerReady=false`를 반환한다.
- Main/Router/Sub LLM, TTS, STT와 Vision도 의도적으로 시작하지 않아
  전체 runtime은 `down`이다. 이를 Minecraft readiness 성공으로 오인하지
  않는다.
- Mindcraft 이미지는 고정 submodule 커밋
  `b36eaf7e61b3f6bd031fdb531812b2e3c42b6c73`에서 정식 빌드했다.
- 새 Mindcraft image의 production-only `npm audit` 집계는 moderate 14,
  high 5, critical 0이며 미해결 위험으로 유지한다.

2026-07-31 완료 턴 commit 지연 계측 배포 후 비파괴 검증 결과:

- `5acdc83`은 durable checkpoint/head commit의 호출 지연을 계측하고,
  최근 성공 256개의 p50/p95/max, 누적 시도·성공·실패 횟수와 마지막 성공
  여부만 공개한다. 대화문, 사용자·세션 ID, 경로와 예외 메시지는 지표에
  포함하지 않는다.
- Runtime Errors는 fresh한 20개 이상 표본의 p95가 100ms를 넘을 때만
  Conversation Continuity를 `degraded`, 전체를 `attention`으로 표시한다.
  stale 경고는 현재 경고로 승격하지 않는다. Control Page는 p50/p95와
  표본 수를 읽기 전용으로 표시한다.
- Docker가 약 10시간 동안 종료돼 있던 상태에서 새 Bot API와 Control Page만
  교체·기동했다. 둘 다 새 digest로 `healthy`, restart count 0이다.
  Main/Router/Sub LLM, TTS, STT, Vision, Discord, Host Supervisor와 Local
  I/O Bridge는 이번 배포에서 임의로 시작하지 않았다. 따라서 실제 대화
  commit 표본은 아직 0개이며 continuity source는 `missing`이다.
- 새 image digest:
  - Bot API:
    `sha256:e7feaaa8fc923f895e78bffbc8f9499d48d51efb6e61856823a88208d40e7a3f`
  - Control Page:
    `sha256:a069154e39a03b928fe57aa9fb3aba75a4cc756dd7f4bc35821f23ecc3886553`
  - Discord:
    `sha256:d9e5f743624fa45ae71ce71c3f9d1a7ca73c4ef4dcb94958d84f4cd5644d566b`
- 배포된 `/api/control-page/runtime-errors`는
  `runtime_errors.summary.v1`과 privacy false flags를 반환했고 실제
  Windows 경로나 `privateMessage`를 포함하지 않았다. 현재 continuity owner가
  기동하지 않아 전체 상태는 `unknown`, source는 `missing`으로 정확히
  표시됐다.

같은 날 UI Action 배포 후 비파괴 검증 결과:

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
- 영구 삭제 journal은 duplicate key와 non-canonical v2 row를 거부하는 strict
  v1/v2 parser, legacy raw-prefix sequence-0 head, v2 sequence/hash chain과 durable
  local head를 사용한다.
  - malformed/partial/oversized/symlink artifact, pathological JSON, tail truncation,
    head mismatch는 `memory_deletion_journal_integrity_failed`로 fail-closed한다.
  - Windows는 write-through replace, POSIX는 rename 뒤 parent-directory `fsync`를
    완료해야 durable head로 인정한다.
  - tombstone 뒤 source Markdown은 content-free stub으로 durable 교체를 시도한
    다음 unlink하며, 현재 journal/head가 유지되는 동안 index/recall/context에서
    본문을 노출하지 않는다.
  - 임의 front-matter ID는 삭제 ledger 경계에서 domain-separated
    `opaque-<64hex>`로 바꾸고 note/source type은 닫힌 enum으로 정규화한다.
    application graph의 raw identity는 보존하면서 journal, stub, apply 결과,
    content-free receipt와 persisted provenance audit에는 자연어 ID를 남기지 않는다.
    derivation revocation artifact도 ledger ID만 저장하고 live graph와 exact
    canonical 역매핑한다. 비정규 ID와 충돌·모호성은 추측 없이 fail-closed한다.
    이미 삭제되어 live graph에 없는 canonical stale target은 reconciliation
    동안에만 허용한 뒤 artifact에서 제거해 삭제 완료 상태를 영구 장애로
    오인하지 않는다.
  - recall/context/Control Page/provenance/index/write와 semantic consolidation,
    derivation recomposition은 삭제 lease와 선형화된다.
  - 전체 legacy+vault memory context는 하나의 root-bound deletion position을
    캡처한다. Main/Voice/Fast HTTP sink는 전송 직전에 이를 다시 검증하고 응답
    소비까지 lease를 유지하므로, 중간 삭제가 먼저 commit되면 POST 0회로
    fail-closed한다. 공개 receipt에는 content-free digest projection만 남는다.
  - `memory.deletion.integrity.v1.rollbackProtected`는 signed head와 외부 anchor가
    모두 검증된 경우에만 true다. 기본 또는 key-only 상태는 journal+head 과거
    쌍 replay를 탐지하지 못한다.
  - disposable-replica verifier는 unsigned 이력의 bootstrap=false 거부, true인
    fresh child 한 번의 채택, false인 새 child strict 상태와 signed 과거 pair
    replay 거부, 정상 pair 복원을 실제 subprocess에서 확인한다. 실제 memory root를
    받지 않으며 출력에는 note/event/key/path 원문이 없다.
  - replica contract와 path isolation은 검증됐지만 실제 host key/anchor의 ACL·owner,
    Docker secret/bind mount effective permission은 검증하지 않는다. 따라서 verifier는
    `permissionState=not_verified`, `operationallyVerified=false`를 유지한다.
  - 공유 anchor에 다른 journal만 있고 deletion ledger가 전혀 없는 상태는
    `uninitialized`로 읽을 수 있다. 첫 승인 삭제는 서명된 content-free
    `memory-deletions.initialized.json` witness를 먼저 기록하며, 그 뒤
    journal/head/anchor가 사라져도 미초기화로 오인하지 않는다.
  - unsigned/signed local head, initialization witness와 external anchor는 writer의
    canonical JSON bytes와 exact 비교해 key order나 공백만 바꾼 artifact도 거부한다.
  - 예외와 반환형 양쪽의 integrity 오류는 HTTP 최외곽에서 exact 503 본문으로
    축약하고 `Cache-Control: no-store`를 적용한다.
  - cognitive-state, 경량 route planner와 장기 memory writeback은 기억을 읽기
    전에 root-bound position을 캡처하고 primary/compact 요청의 JSON LLM sink에
    required boundary로 전달한다. 응답 뒤 cognitive state 또는 memory summary를
    기록하기 전에도 같은 position을 재검증해, 그 사이 삭제가 commit됐으면
    파생 상태를 쓰지 않는다.
  - provenance-correction v2 prepared event는 target/source/origin을 ledger ID로만
    저장한다. recovery와 undo는 live graph 및 immutable legacy v1 row를 통한
    exact 1:1 mapping만 허용하고 미매핑·충돌·비정규 v2 ID를 fail-closed한다.
    새 v2 JSONL row, local head와 signed external anchor는 duplicate key, 추가·누락
    field와 non-canonical byte serialization을 거부한다. change ID, actor/action,
    error code, timestamp와 revision도 event kind별 exact schema와 닫힌 domain을
    통과해야 한다. legacy v1 raw row는 그대로 immutable anchor로 유지한다.
    writer marker도 exact canonical schema만 상태로 인정하며, 손상 marker는 공개
    조회에서 `unknown`으로 처리하고 다음 writer lease 아래에서만 정리한다.
    persisted provenance coverage의 source/note type과 forward rejection type도
    닫힌 enum으로 정규화·alias 집계한다.
  - Fast custom memory receipt의 note ID도 ledger ID로 투영하고 이미 canonical인
    ID는 유지한다. retrieval mode는 닫힌 enum으로 제한해 provider나 손상 cache의
    자유 형식 값이 `contentFree=true` receipt와 cache summary로 나오지 않는다.
    explicit-confirmation 성공 receipt도 note ID와 source ref를 각각 canonical
    ledger ID와 `turn:opaque-turn-<64hex>:user` 형식으로 투영한다. legacy evidence/source/turn
    ID는 producer, 최종 receipt와 durable turn summary 세 경계에서 각각의
    `opaque-evidence-*`/`opaque-turn-*` namespace로 방어적으로 투영한다.
  - persisted provenance audit는 strict `generatedAt`과 전체 raw canonical JSON을
    검사한다. duplicate key·추가 원문·비정규 serialization은 감사 lease와 관찰
    lock 아래 durable 교체하며, 실패하면 성공 audit를 반환하지 않는다.
  - Bot API chat state, Fast non-stream/stream과 공개 Control Page proxy는
    deletion integrity 오류를 generic chat 실패나 `bot_api_unavailable`로
    강등하지 않는다. 최외곽 응답은 exact content-free 503과 `no-store`이며,
    stream은 첫 model delta를 요청해 memory build와 upstream admission이
    성공한 뒤에만 HTTP 200을 prepare한다.
  - 8798→8799 state/chat/shutdown/action-events는 strict content-free handoff
    header를 사용한다. 공개 프록시는 upstream EOF 뒤 browser write 직전에 같은
    exposure를 재검증하고 write 종료까지 lease를 유지하며, state 재직렬화에도
    경계를 보존한다. handoff header와 note ID는 브라우저에 노출하지 않는다.
  - Control Page text/search와 voice는 공용 reply-boundary validator로 compact
    receipt의 state, memory version, note ID를 현재 exposure와 정확히 대조한 뒤에만
    assistant persistence, continuity, TTS와 반환을 수행한다.

## Verification state

- 이번 working-tree increment는 `core 630`, `runtime 544`, `ui 166`, `voice 547`,
  `discord_io 109`, `memory 256`, 그 밖의 hygiene/tools/vision/voyager/
  minecraft/mindcraft 340개, 합계 2,592개를 통과했고 18개는 환경·선택 기능으로
  skip됐다. Python source 623개 구문 검사, Control Page asset JavaScript 7개
  `node --check`, Fast Control Compose config와 `git diff --check`도 통과했다.
  테스트는 임시 memory/runtime root만 사용했고 실제 서비스, 실제 사용자 기억,
  마이크·스피커·Discord는 시작하지 않았다.

검증한 코드 기준점: `bd0786d`

- current-source에서 Mindcraft 15개, Minecraft 79개, Docker 계약 16개를
  통과했다.
- 새 Mindcraft image의 build-time lint smoke, 실제 Coder 정상/거부 경로,
  config-missing fail-closed 경로, `npm ls`, production audit,
  Python `compileall`/`pip check`와 Node syntax를 통과했다.
- 공식 Discord Python 환경의 current-source mount에서 focused 65개,
  Runtime 전체 373개(skip 2), Mindcraft 14개, Minecraft 79개,
  관련 Control Page 11개(skip 1)를 통과했다.
- 새 이미지에 구워진 소스로 Bot API readiness/health 24개,
  Control Page 배선 10개, Discord client/main 배선 18개를 통과했다.
- 네 이미지의 `compileall`과 `pip check`, Mindcraft overlay의
  `node --check`, exact readiness 생산·검증 smoke를 통과했다.
- Mindcraft 전체 정식 빌드와 production-only `npm audit --omit=dev`
  집계를 완료했다. 실제 Discord·Minecraft·마이크·모델 서비스는
  시작하지 않았다.
- bundled Python에서 commit metrics, Runtime Errors와 Control Page UI
  집중 테스트 37개를 통과했다.
- 공식 Discord Python 3.11 환경의 current-source read-only mount에서
  모든 완료 턴 경로·continuity·Runtime Errors·Runtime Health 집중 테스트
  85개(skip 3), Runtime 전체 370개(skip 2), UI 전체 154개(skip 7),
  Discord I/O 95개, Voice 413개를 통과했다.
- Core 전체 467개는 기능 assertion 실패 0개였고 이미지에 `git` 실행 파일이
  없어 과거 `main.py` signature를 조회하는 기존 테스트 2개만 환경 오류였다.
- 격리 artifact root에서 실제 `main.py` Control Page smoke와 강제 종료·
  재시작 continuity 복구 2개를 통과했다.
- 새 Bot API와 Control Page 이미지 내부 소스는 read-only test mount에서
  각각 집중 테스트 51개(skip 1), Discord 이미지는 모든 완료 턴 경로 집중
  테스트 76개(skip 1)를 통과했다. 세 이미지 모두 전체 `compileall`과
  `pip check`를 통과했다.
- `node --check`, local-only sentinel Compose config와 `git diff --check`를
  통과했다.

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
- `a879380`의 delivered-turn durability와 public error contract 변경은
  bundled Python 집중 테스트 107개, 공식 Discord Python 환경의 current-source
  통합 집중 테스트 219개, composition 배선 테스트 18개를 통과했다.
- Bot API 이미지의 전체 discovery는 1,727개를 실행해 기능 assertion 실패
  0개였다. 이미지에 없는 git, Pillow, Discord와 Voyager package 경로 때문에
  환경 import 오류 17개와 skip 19개가 남았다.
- Discord를 비활성화한 격리 환경에서 실제 `main.py` Control Page smoke와
  강제 종료·재시작 continuity 복구를 각각 다시 통과했다. 기본 runtime
  artifact는 사용하지 않았다.
- 새 이미지 digest는 Bot API
  `sha256:1cf8ade15988c3cf8420d11e0a514835933650c1ced8ac8e613d0b7c726eb1ac`,
  Control Page
  `sha256:d9c6db01e8f3ac75807c8919c7e155273321f07f6aaeffb00dd1563b41dff0b1`,
  Discord
  `sha256:a8858aae57a63b1be537962a60e8d04847cf1d43b0d947dd0d148f0a04d559bf`다.
  세 이미지의 내부 `compileall`과 `pip check`를 통과했다.
- Bot API와 Control Page만 새 이미지로 교체했다. 첫 Bot API 기동은 이전
  owner claim이 아직 15초 stale 경계를 지나지 않아
  `minecraft_world_lease_owner_conflict`로 fail-closed 종료됐다. stale 경과
  뒤 같은 컨테이너가 claim을 회수해 `healthy`, restart count 0이 됐다.
  Control Page도 새 이미지에서 `healthy`, restart count 0이다.
- `3473a44`의 음성 P0 FSM·입력 연속성 변경은 전체 음성 테스트 414개와
  검증 API/capability/동의/Discord heartbeat/UI 테스트 50개를 통과했다.
  전체 discovery는 Discord 이미지에서 1,874개를 실행했고 기능 assertion
  실패는 0개였다. 이미지에 없는 `git`, Linux의 `WindowsPath`, Voyager
  `gymnasium` 때문에 난 환경 오류 4개가 가리킨 실제 테스트 7개는 Windows
  Python과 Codex Gateway/Voyager 이미지에서 모두 통과했다.
- 새 이미지 digest는 Bot API
  `sha256:fd4b48cc5cdaeebaa3973d674f6be8a5fccbd1c66ac303e9cc426a283edccb13`,
  Control Page
  `sha256:cdb1270127412370515daff1c6ab4af0e01541dd476b7497f1812d4671045577`,
  Discord
  `sha256:65e0554b97f5fe97aa3a9e2b850d6cf5c6a5682ed5f9c03d97def97e20d7be2a`다.
  세 이미지의 `pip check`, Python `compileall`, validation JavaScript
  `node --check`, `git diff --check`를 통과했다.
- Bot API와 Control Page만 새 이미지로 교체했다. 첫 Bot API 기동은 교체 전
  컨테이너의 owner claim이 15초 stale 유예 안에 있어 fail-closed 종료됐고,
  유예 뒤 같은 컨테이너가 정상 인수했다. 현재 두 서비스는 `healthy`,
  restart count 0이다. Discord와 마이크·LLM/STT/TTS는 시작하지 않았다.
- `d504303`의 owner handoff 변경은 Minecraft 전체 80개와 Fast Control/
  shutdown/lease boundary 85개, owner·Compose 집중 33개를 통과했다.
  실제 Bot API SIGTERM은 4.2초 안에 exit 0으로 claim을 제거했고, 그 이미지
  사이의 `docker compose --force-recreate`는 stale 대기나 첫 프로세스 종료
  없이 바로 `healthy`가 됐다.
- 현재 Bot API 이미지 digest는
  `sha256:141fdc86304f7c0ac6e91a40b09e4eccf9994fb70f652701ad3f32d45d4d0eb7`이며
  `stopTimeout=30`, `healthy`, restart count 0이다. 이미지 내부
  `compileall`과 `pip check`, Compose config, `git diff --check`를 통과했다.
- 배포 뒤 공식 `check_docker_runtime.ps1 -IncludeLocalBridge`는
  Control Page, Bot API, Main/Router/Sub LLM, TTS, STT, Vision과 Windows
  Local I/O Bridge를 모두 준비 상태로 판정했다. 실제 Discord bot,
  Minecraft/Voyager와 마이크 capture는 시작하지 않았다.
- `159093e`는 Minecraft 단일 owner의 5초 claim/status heartbeat와 lease
  만료 처리를 그대로 유지하면서, lease 없는 지연 시작 상태의 HTTP probe만
  30초로 분리했다. 내부 background `/status` 실패는 반복 로그를 남기지
  않지만 명시적 상태·시작·중지·목표 요청의 실패 보고는 유지한다.
- Minecraft/Fast Control/lease boundary/shutdown 집중 테스트 131개,
  Python `compileall`, Bot API 이미지 `pip check`, Compose config와
  `git diff --check`를 통과했다.
- 배포 전 기존 Bot API는 Minecraft가 꺼진 상태에서 90초 동안 동일
  `TimeoutError`를 13번 기록했다. 새 이미지 배포 뒤 30초 probe 경계를
  지나도 해당 반복 로그는 0건이며, owner claim과 공개 상태 heartbeat는
  계속 전진했다.
- 현재 Bot API 이미지 digest는
  `sha256:d1e8e5920019859e011b52fcb7dabfaf94831f601526c9ed1898fbedba6a47f3`이고
  `healthy`, restart count 0이다. Control Page도 계속 `healthy`다.
- `c858fcc`와 `8da072e`는 Bot API와 공개 Control Page의 반복 runtime
  health 수집을 [2초 single-flight snapshot과 6초 fail-closed freshness
  경계](RUNTIME_HEALTH_SNAPSHOT_CONTRACT.md)로 교체했다. 일반 상태·채팅
  응답은 stale-while-revalidate를 사용하고, 명시적 `/status`, 진단, repair,
  override는 fresh 수집을 유지한다.
- 새 cache owner 단위·Fast Control·Control Page 결합 테스트 105개와
  Control Page API 집중 테스트 37개를 통과했다. 새 Control Page 이미지에서
  runtime 전체 386개를 실행해 실패 0개, 환경 의도 skip 2개였다.
- 배포 전 Bot API 직접 상태 요청은 p50 605ms / p95 1,172ms였고, 배포 후
  30회 측정은 p50 4.7ms / p95 15.48ms였다. 공개 8799 경로는 cache owner
  교체 전 p95 376.58ms, 교체 후 steady-state p95 20.09ms였다. 첫 cold
  요청은 fresh 증거를 기다려 805.81ms였고, 이후 관찰한 cache 최대 나이는
  2.3초, stale 응답은 0건이었다.
- 현재 이미지 digest는 Bot API
  `sha256:6471bf4b32c2cd5704e82c899c27b73ad333805653bdbbad287676cfa65dcd4d`,
  Control Page
  `sha256:61c6a6b62d8a2128fe3194cc053e7a84aa53952ad8c2cf403bef76f2663d46b6`다.
  두 컨테이너 모두 `healthy`, restart count 0이고 이미지 `compileall`,
  `pip check`, Compose config와 `git diff --check`를 통과했다.
- `eba918f`는 파생 기억 재합성이 `pending`일 때 일반 900초 vault
  유지보수와 분리된 기본 60초 retry gate를 기록한다. 다음 비실시간 기억
  유지보수 기회에 단일 기존 task 경계로 다시 시도하며, 로그에는 note ID나
  본문 없이 guild ID, 대기 개수, retry 시간만 남긴다. 재합성이 clear이면
  기존 900초 간격을 그대로 유지한다.
- memory 전체 133개를 Bot API와 새 Discord/main 이미지에서 각각 통과했다.
  Discord 이미지의 `compileall`과 `pip check`도 통과했으며 digest는
  `sha256:219f3ec72c5263397d45cc16d367f9e3b0bbe637098b237994a486a4cbbdfde4`다.
  이미지만 빌드했고 실제 Discord bot은 시작하지 않았다.
- 운영 vault는 read-only API로만 확인했다. 현재 note 3개, provenance
  coverage 100%, declared derivation 0개, quarantine 0개이며 실제 기억
  파일에 correction/recomposition mutation을 실행하지 않았다.
- `67a7adf`는 Discord reference 전송의 모든 예외를 일반 메시지로 다시
  보내던 fallback을 delivery-at-most-once 경계로 제한했다. 확정 404와 로컬
  reference 생성 실패는 fallback하고, timeout·503은 첫 요청 한 번만 남긴다.
- 공식 Discord 이미지에서 새 전달 테스트 9개, 인접 검색 후속/Discord text
  테스트 8개와 Discord I/O 전체 98개를 통과했다. core 468개는 기능 assertion
  실패 0개였고, 이미지에 `git`이 없어 난 기존 서명 검사 2개는 Windows
  Python에서 해당 모듈 13개를 따로 실행해 통과했다.
- Discord 테스트 이미지 digest는
  `sha256:66470617533a4d44eca6b53b0b91c2cf6e043a651675a63d74eeb083e2c22181`이다.
  이미지 `compileall`, `pip check`, 전체 profile Compose config와
  `git diff --check`를 통과했다. 이미지만 빌드했고 실제 Discord bot과
  마이크·Minecraft는 시작하지 않았다.
- `0f0201f`는 Control Page runtime probe의 예외 원문 공개와 Codex
  `ok`/`backendReady` 혼동을 닫았다. token-like 문자열, 내부 URL, Windows
  경로를 넣은 개별 probe·전체 refresh·untrusted health 입력은 고정 코드로만
  투영된다.
- 공식 이미지 환경에서 집중 41개, runtime 388개(skip 2), UI 154개(skip 7)를
  통과했다. core 468개는 기능 assertion 실패 0개였고 이미지에 없는 `git`
  오류 2개는 Windows에서 해당 모듈 13개를 재실행해 통과했다.
- 새 Control Page image
  `sha256:2a20b778b966e18930de96120146a08f1758b2f9b2c86fd74e8513b1181aaf0c`를
  단독 교체했다. 실제 `/health`는 ok, 공개 state에는 Bearer·Windows user
  path·traceback 패턴이 없었고 컨테이너는 healthy, restart count 0이다.
- Discord/Main owner image
  `sha256:570dd9be4de3c89dc39c1bc0060fe3b89fc4c3dd5cda3d7e7141d652b83793f5`는
  `compileall`, `pip check`와 집중 테스트를 통과했지만 시작하지 않았다.
  Bot API는 기존 healthy image와 restart count 0을 유지했다.
- `436fb59`는 runtime artifact/log의 최근 오류 원문을 Main/Voice prompt에
  넣던 경로를 content-free marker로 교체했다. Codex stderr/message와
  Voyager error/critique/log tail은 더 이상 읽어 렌더링하지 않으며 정상
  critique를 오류로 오인하지 않는다.
- status/context 집중 22개와 runtime 393개(skip 2)를 통과했다. core
  468개는 기능 assertion 실패 0개였고 기존 `git` 환경 오류 2개는 Windows
  모듈 13개로 보완했다. 운영 artifact의 read-only marker 결과는 현재
  0개였다.
- 새 Discord/Main image
  `sha256:1ad4935410afec659a1862e11d3950c3657d379618bb29d3190cde7f58cc69b9`는
  `compileall`, `pip check`와 집중 테스트를 통과했지만 시작하지 않았다.
  실행 중인 Control Page와 Bot API는 그대로 healthy다.
- `26c97e8`은 자율 executor의 관찰·실행·cycle 예외 원문을
  `autonomy.failure.v1` 고정 marker로 교체했다. 실행 예외는 action별
  failed/unverified audit 결과이며 승인된 계획을 성공으로 진행시키지 않는다.
- 새 계약 집중 테스트 78개, Discord I/O 99개, runtime 393개(skip 2),
  UI 154개(skip 7)를 통과했다. core 476개는 기능 assertion 실패 0개였고
  이미지에 `git`이 없어 난 기존 서명 검사 2개와 Windows 전용 OCR은 Windows
  모듈 19개로 보완했다.
- 새 Discord/Main image
  `sha256:f0d82b867babaeb5ad4731116fa90c4ae91e30630dfc6ca6e64bca36506c83b9`는
  내부 `compileall`, `pip check`, 새 계약 포함 확인과 이미지 내부 집중
  테스트 78개를 통과했다. 실제 Discord/Main 서비스는 시작하지 않았다.
  실행 중인 Control Page와 Bot API 두 서비스는 그대로 유지했다.
- `2272668`은 완료 턴의 commit callback 반환값을 모든 전달 surface에서
  exact durable receipt로 재검증한다. 부분 status나 이전 실패 metric은
  성공으로 기록되지 않으며, 자율 후속의 production generation이 항상 0이던
  필드 불일치를 함께 수정했다.
- receipt/owner/surface 집중 테스트 61개, Discord I/O 101개, UI 156개
  (skip 7), 음성 415개, runtime 393개(skip 2)를 통과했다. core 483개는
  기능 assertion 실패 0개였고 이미지에 `git`이 없어 난 기존 2개는 Windows
  모듈 13개로 보완했다.
- 새 Discord/Main image
  `sha256:9e0b178be17328a9ffec393b72e31343b9dfd645dba9f1d8da955ac1f6e3b93d`는
  내부 `compileall`, `pip check`, receipt 계약 import와 이미지 내부 집중
  테스트 61개를 통과했다. 실제 Discord/Main 서비스는 시작하지 않았다.
- `4193075`는 Minecraft 네 명령에만 있던 수동 continuity 기록을
  composition의 단일 post-delivery command context로 올렸다. 도움말·상태·
  접두사·자율 제어·채널 설정·초기화·음성 제어와 권한 거부까지 모든 등록
  명령 응답을 포괄하며, 성공한 전송 뒤 한 번만 기록한다.
- 새 owner 집중 테스트 31개, Discord I/O 105개와 surface 공통
  commit/receipt/restart 테스트 70개가 공식 Discord 이미지에서 통과했다.
  실제 Discord나 무거운 음성·LLM 서비스는 시작하지 않았다.
- 새 Discord/Main image
  `sha256:368d5decb1441a9b5b2b5ab9e5e5991da62814116c09cbfb12546b974ac2f878`는
  이미지 내부 `compileall`, `pip check`와 읽기 전용 테스트 마운트로 실행한
  집중 테스트 36개를 통과했다. 이미지는 시작하지 않았고 실행 중인 Bot API와
  Control Page 두 서비스는 그대로 healthy다.
- `2fcf597`은 Discord 일반 대화가 생성 단계에서 실패해도 고정 실패 응답이
  실제 전달된 경우 그 실패 턴을 완료 상태로 기록하고 즉시 durable commit한다.
  fallback 전송 실패는 무기록, 완료 기록 실패는 무재전송이며 예외 원문은
  history·artifact·metric·log 어디에도 들어가지 않는다.
- 실패 턴 집중 테스트 8개, Discord I/O 107개와 surface 공통
  commit/receipt/restart 테스트 72개가 공식 Discord 이미지에서 통과했다.
- 새 Discord/Main image
  `sha256:09ada81d7802c35f672e40f528d74f0b63ce9eb9027d5a7249640247b4117130`는
  이미지 내부 `compileall`, `pip check`와 읽기 전용 테스트 마운트의
  continuity 집중 테스트 44개를 통과했다. 이미지는 시작하지 않았고 실행
  중인 Bot API와 Control Page 두 서비스는 그대로 healthy다.
- `f0543b7`은 standalone Bot API의 volatile `CHAT_MESSAGES`에 전용
  `fast_control_continuity` single-writer owner를 연결했다. 일반 JSON,
  NDJSON stream, planner 실패와 background 완료·실패가 exact durable
  receipt를 검증하며 fresh process가 복구한 role/content를 다음 LLM request에
  다시 넣는다.
- owner/restart/retention 집중 20개와 Fast Control 인접 테스트를 포함한
  92개, runtime 398개(skip 2), UI 156개(skip 7)를 통과했다. core 490개는
  기능 assertion 실패 0개였고 이미지에 Git이 없어 난 기존 서명 검사 2개는
  Windows 모듈 13개로 보완했다.
- 새 Bot API image
  `sha256:74394ea08b6254e4c70bea1e7b840d4d73db709213e2f90f5cdeb96c1e572316`은
  이미지 내부 `compileall`, `pip check`와 읽기 전용 테스트 마운트의
  Fast Control continuity 집중 테스트 93개를 통과했다. 이미지는 시작하지
  않았고 실행 중인 기존 Bot API와 Control Page는 그대로 healthy다.
- 현재 작업은 두 checkpoint를 합쳐 경쟁 writer를 만들지 않고, 검증된
  snapshot만 prompt-time에 읽는 양방향 surface handoff를 연결했다. Main은
  공통 `prepare_llm_messages`, Fast Control은 LLM payload와 tool planner에서
  같은 bounded merge를 사용한다.
- owner-level read-only/tamper/stale/revocation/scope/reset/양방향 merge와
  composition wiring 집중 테스트 23개가 Windows bundled Python에서
  통과했다(Windows symlink 생성 불가 1개 skip).
- 공식 이미지 source-mount 전체 회귀는 core 501개 중 기능 assertion
  실패 0개와 이미지에 Git이 없어 난 기존 서명 검사 2개 환경 오류,
  runtime 399개(skip 2), UI 156개(skip 7), Discord I/O 107개, voice
  415개를 기록했다. 두 서명 모듈 13개는 Windows에서 통과했다.
- 최종 내장 소스 Bot API image
  `sha256:a01daecc9a861ad4ca639aa46716b4c1142e718bb6769acd7e07df4dfe4f3d9a`는
  Fast Control·stream·cross-surface 집중 85개를 통과했다. 최종
  Discord/Main image
  `sha256:514e753663d090888591a3ef002d4cd9cc5bbb75cd4eea982f11067942523db0`는
  continuity·공통 prompt 집중 47개를 통과했다. 두 이미지 모두
  `compileall`과 `pip check`를 통과했고 전체 profile Compose config와
  정적 Compose 계약 18개도 통과했다.
- 이번 merge-evidence 변경 뒤 기존 공식 이미지의 current-source
  read-only mount에서 runtime 400개(skip 2), UI 156개(skip 7), Discord
  I/O 107개, voice 415개를 통과했다. core 503개는 기능 assertion 실패가
  0개였고, 이미지에 `git`이 없어 난 기존 서명 검사 환경 오류 2개는
  Windows의 해당 모듈 13개로 보완했다.
- 새 내장 소스 Bot API image
  `sha256:6d37750cdb905937c858348a1ec9ddcee7244a0ca58d65ef127a733fd8855c82`는
  Fast Control·stream·merge-evidence 집중 86개를 통과했다. 새
  Discord/Main image
  `sha256:fd33eeb2041823044f3ed07fd82f190a2c5dbf530654d0a1c7a461639272bea0`는
  continuity·공통 prompt·merge-evidence 집중 48개를 통과했다. 두 이미지
  모두 내장 소스 `compileall`과 `pip check`를 통과했다.
- 이번 action-recovery 변경의 current-source 전체 회귀는 runtime
  407개(skip 2), UI 156개(skip 7), Discord I/O 107개, voice 415개를
  통과했다. core 511개는 기능 assertion 실패 0개였고 이미지에 `git`이
  없어 난 기존 서명 검사 환경 오류 2개는 Windows의 해당 모듈 13개로
  보완했다.
- 새 내장 소스 Bot API image
  `sha256:76389d7f0a9e8c14a605df1e754343888ccd9235980119b4c41725a62b0a3103`와
  Discord/Main image
  `sha256:4e27ac4204454acaa795dfbb3adf24cb99faa5e9e76cc536f7beb77f66fe54d7`는
  각각 action recovery·Fast continuity·API·stream 집중 95개를 통과했다.
  두 이미지 모두 내장 소스 `compileall`과 `pip check`, 전체 profile
  Compose config를 통과했다.
- 새 이미지만 만들었고 실행 중인 Bot API는 기존 image
  `sha256:6471bf4b32c2cd5704e82c899c27b73ad333805653bdbbad287676cfa65dcd4d`,
  Control Page는 기존 image
  `sha256:2a20b778b966e18930de96120146a08f1758b2f9b2c86fd74e8513b1181aaf0c`를
  유지한다. 두 컨테이너 모두 여전히 healthy이며 실제 Discord/Main,
  마이크와 무거운 모델 서비스는 시작하지 않았다.
- action recovery v2 변경의 current-source 전체 회귀는 runtime 408개
  (skip 2), UI 156개(skip 7), Discord I/O 107개를 통과했다. core 518개는
  기능 assertion 실패 0개였고 이미지에 `git`이 없어 난 기존 서명 검사
  환경 오류 2개는 Windows의 해당 모듈 13개로 보완했다.
- 새 내장 소스 Bot API image
  `sha256:583f7c3d5516000e292e620b75a309c6c730c1f41bb2c84ab9f3be3a6931b643`와
  Discord/Main image
  `sha256:e31e5a013df9ca97bdd40b756bfe8df9b6c60ead78f96c35b724a6fde2cd4261`는
  각각 v2 chain/head·Fast continuity·API·stream 집중 103개를 통과했다.
  두 이미지 모두 내장 소스 `compileall`과 `pip check`, 전체 profile
  Compose config를 통과했다.
- 이번에도 새 이미지만 만들었고 실행 중인 기존 Bot API와 Control Page는
  교체하지 않았다. 두 서비스는 동일한 기존 image로 계속 healthy이며 실제
  Discord/Main, 마이크와 무거운 모델 서비스는 시작하지 않았다.
- action recovery v3 변경의 current-source 전체 회귀는 runtime 411개
  (skip 2), UI 156개(skip 7), Discord I/O 107개를 통과했다. core 520개는
  기능 assertion 실패 0개였고 이미지에 `git`이 없어 난 기존 서명 검사
  환경 오류 2개는 Windows의 해당 모듈 13개로 보완했다.
- 새 내장 소스 Bot API image
  `sha256:991abf38703006eb235c4fe6816da6434688d8281b9654a8a27cd93a9a5f9987`와
  Discord/Main image
  `sha256:edf989950e79c68362fc09398064abf2bd51ad91a60a1729b6327f276f0804aa`는
  각각 시작 generation correlation·v1/v2 migration·Fast continuity·API·
  stream 집중 108개를 통과했다. 두 이미지 모두 내장 소스 `compileall`과
  `pip check`, 전체 profile Compose config를 통과했다.
- v3 이미지도 빌드와 검증만 수행했다. 실행 중인 기존 Bot API와 Control
  Page는 같은 image로 계속 healthy이며 실제 Discord/Main, 마이크와 무거운
  모델 서비스는 시작하지 않았다.
- 실제 인증된 Discord↔Control Page handoff는 아래 운영 경계 때문에 별도
  검증 상태로 남긴다. 새 이미지만 빌드했고 실행 중인 기존 Bot API와
  Control Page는 교체하지 않았으며 둘 다 healthy 상태다.
- memory context receipt 변경의 current-source 검증은 focused 140개,
  memory 134개, runtime 413개(skip 2), UI 156개(skip 7), voice 415개를
  통과했다. Discord 의존 이미지의 core 533개는 기능 assertion 실패 0개였고
  이미지에 `git`이 없어 난 기존 서명 검사 환경 오류 2개는 Windows의 해당
  모듈 13개로 보완했다. bundled Python의 비네트워크 집중 59개와
  `compileall`, `git diff --check`도 통과했다.
- 이 변경에서는 실제 사용자 기억을 수정·삭제하지 않았고 Discord, 마이크,
  Minecraft와 무거운 모델 서비스를 시작하지 않았다. source-mount 검증만
  수행했으며 실행 중인 Bot API와 Control Page도 교체하지 않았다.
- raw turn evidence 변경의 current-source 검증은 memory 136개, runtime
  413개(skip 2), voice 415개를 통과했다. Discord 의존 이미지의 core
  534개는 기능 assertion 실패 0개였고 `git` 부재의 기존 서명 검사 환경 오류
  2개는 Windows의 해당 모듈 13개로 보완했다. bundled Python 집중 66개와
  `compileall`, `git diff --check`도 통과했다.
- derived memory evidence 변경의 current-source 검증은 집중 45개, memory
  139개, runtime 413개(skip 2), UI 156개(skip 7), Discord I/O 108개,
  voice 415개를 통과했다. Discord 의존 이미지의 core 536개는 기능 assertion
  실패 0개였고 이미지에 `git`이 없어 난 기존 서명 검사 환경 오류 2개는
  Windows의 해당 모듈 13개로 보완했다. bundled Python과 두 테스트 이미지의
  `pip check`, `compileall`, 전체 profile Compose config, `git diff --check`도
  통과했다. source는 read-only mount, runtime artifacts와 test memory는
  컨테이너 임시 경로를 사용했으며 실행 중인 서비스는 교체하지 않았다.
- 확인 전용 memory prompt 경계 변경은 bundled Python 집중 35개, Fast 경계
  23개, memory 139개, runtime 415개(skip 2), UI 156개(skip 7), Discord I/O
  108개와 voice 415개를 통과했다. core 545개는 기능 assertion 실패 0개였고
  Main/Discord 이미지에 `git`이 없어 난 기존 서명 검사 환경 오류 2개는 Windows
  관련 모듈 19개로 보완했다. 전체 discovery는 1,996개를 실행해 같은 `git`
  오류 2개, Linux의 Windows OCR 경로 오류 1개, 서드파티 Voyager package
  initializer 의존성 오류 1개만 남겼고, Windows 19개와 Voyager 18개+격리
  local index 4개가 각각 통과해 기능 경로를 보완했다. bundled Python과 두
  테스트 이미지의 `compileall`/`pip check`, 전체 profile Compose config도
  통과했다. 실제 기억·실행 중 서비스는 변경하지 않았고 Discord, 마이크,
  Minecraft와 무거운 모델 서비스는 시작하지 않았다.
- legacy context coverage 변경은 focused API 9개와 core/memory/UI 51개,
  memory 142개, runtime 415개(skip 2), UI 156개(skip 7)를 통과했다. core
  545개는 기능 assertion 실패 0개였고 이미지의 `git` 부재 오류 2개는 Windows
  13개로 보완했다. 전체 discovery 1,999개는 같은 `git` 오류 2개, Linux의
  Windows OCR 환경 오류 1개와 Voyager package initializer 의존성 오류 1개만
  남겼으며 Windows 19개, Voyager 18개와 격리 local index 4개가 모두 통과했다.
  실제 Control Page는 HTTP 200, 새 coverage 카드/schema 포함, health 정상임을
  확인했다. bundled Python과 두 테스트 이미지의 `compileall`/`pip check`, 전체
  profile Compose config와 `git diff --check`도 통과했다. 실제 `bot_memory`는
  읽기 전용 집계만 수행했고 scope 3개, legacy 저장 항목 0개(`empty`)였다.
  실행 중인 서비스와 사용자 기억은 변경하지 않았다.
- evidence-bound user memory 변경은 memory 147개, runtime 417개(skip 2), UI
  157개(skip 7), voice 415개를 통과했다. Main/Discord 이미지의 core 545개는
  기능 assertion 실패 0개였고 이미지에 `git`이 없어 난 기존 서명 검사 환경 오류
  2개만 남았다. 전체 discovery는 2,007개를 실행해 같은 `git` 오류 2개,
  Linux의 Windows OCR 환경 오류 1개와 서드파티 Voyager `gymnasium` 의존성
  오류 1개만 남겼다. Compose config, source `py_compile`, UI inline
  `node --check`, `git diff --check`를 통과했다.
- 새 내장 소스 검증 이미지는 Bot API
  `sha256:65758f34b495c799effc382bd538320c3a8b31f6d10b2495a89017a731c9a4f8`,
  Control Page
  `sha256:23794015780b40316f080365d5f1f82b5e6375ad176e9f596b1c0e096442c861`이며
  둘 다 내장 package `compileall`과 `pip check`를 통과했다. 실행 중인 기존
  Bot API와 Control Page는 교체하지 않았고 계속 healthy다. 실제 사용자 기억,
  Discord, 마이크, Minecraft와 무거운 모델 서비스도 변경하거나 시작하지 않았다.
- Discord explicit-confirm memory 변경은 집중 107개, memory 152개, runtime
  417개(skip 2), Discord I/O 109개, voice 417개와 UI 157개(skip 7)를
  통과했다. Main/Discord 이미지의 core 546개는 기능 assertion 실패 0개였고
  이미지에 `git`이 없어 난 기존 서명 검사 환경 오류 2개는 Windows의 해당
  모듈 13개로 보완했다. 전체 discovery는 2,016개를 실행해 같은 `git` 오류
  2개, Linux의 Windows OCR 환경 오류 1개와 서드파티 Voyager `gymnasium`
  의존성 오류 1개만 남겼고 Windows OCR 6개도 별도로 통과했다.
- 새 내장 소스 검증 이미지는 Bot API
  `sha256:043ae4176b191752fd8abb641041e2435fc778c97a9c6197475b6269177dd6df`,
  Discord/Main
  `sha256:2df481e0660a2b45f7252966c2dfa1fd90bddd46d98654c29e1a2464603107ad`이며
  각각 내장 소스 집중 85개와 22개, `compileall`, `pip check`를 통과했다.
  전체 profile Compose config와 `git diff --check`도 통과했다. 실행 중인 기존
  Bot API와 Control Page는 교체하지 않았고 계속 healthy다. 실제 사용자 기억,
  Discord, 마이크, Minecraft와 무거운 모델 서비스도 변경하거나 시작하지 않았다.
- explicit-confirm memory lifecycle 강화는 저장·회수·삭제 집중 12개,
  memory 전체 154개와 Control/Discord/voice/trace 인접 경로 98개를 통과했다.
  격리된 lifecycle은 Discord 직접 출처 기억이 attributed prompt로 제공되고,
  2단계 삭제 뒤 같은 query·receipt·tombstone 어디에도 다시 나타나지 않음을
  확인했다. 손상 provenance 재시도는 성공 대신 content-free 실패가 된다.
- 새 내장 소스 검증 이미지는 Bot API
  `sha256:16e8a41da36e593fa0f2e0c61102857dec1fee6857d8c61cc0dab1a04b549a64`,
  Discord/Main
  `sha256:0213e52e21d9f617c077607b50c322c76373f96fc29377f4223093f2663528c1`이며
  각각 내장 소스 88개와 22개, `compileall`, `pip check`를 통과했다. 전체
  profile Compose config도 통과했다. 실행 중 서비스와 실제 사용자 기억은
  변경하지 않았다.
- confirmed-memory recall integrity 변경은 집중 29개, memory 158개,
  runtime 417개(skip 2), UI 157개(skip 7), Discord I/O 109개와 voice
  417개를 통과했다. core 546개는 기능 assertion 실패 0개였고 이미지의 `git`
  부재 오류 2개는 Windows의 해당 모듈 13개로 보완했다. 손상 전 cache hit를
  만든 뒤 evidence를 제거한 테스트에서도 다음 recall은 memory version을
  전진시키고 노트와 cache를 prompt에서 제거했으며, 사용자 편집 뒤 새 근거로만
  복구했다. inline JavaScript `node --check`와 Compose config도 통과했다.
- 새 내장 소스 검증 이미지는 Bot API
  `sha256:76d8c74a70da8bf276ff11352c4232a65f21696b694d30c1d87f44702212ba83`,
  Discord/Main
  `sha256:cfeffd07a86b916ab38c7d170d4fca5cecfe1dcb6b3dadfecec946cbb21f742c`,
  Control Page
  `sha256:a2c7c68b97d350df4549bc3bb8e8896ae209f7d20c28e227f4335695f97da2c0`이며
  내장 소스 집중 90개·22개·14개와 각 이미지 `compileall`/`pip check`를
  통과했다. 실행 중 기존 Bot API와 Control Page는 교체하지 않았고 healthy다.
  실제 사용자 기억과 Discord, 마이크, Minecraft는 변경하거나 시작하지 않았다.
- Runtime Health 공개 projection 변경은 집중 100개, runtime 전체 419개
  (skip 2), UI 157개(skip 7)를 통과했다. core 547개는 기능 assertion 실패
  0개였고 Discord/Main 이미지의 `git` 부재 오류 2개는 Windows의 해당 모듈
  13개로 보완했다. 새 projection은 readiness·capability·복구 결정을 유지하면서
  raw probe payload, target, exception field, host 설정, PID, 장치명과 임의
  legacy/observability 확장 필드가 직렬화되지 않음을 검증했다.
- 현재 소스를 내장한 검증 이미지는 Bot API
  `sha256:94fd9824ff568ccad11141910ca46bd943b4a342b30219f1ffa58f714966c343`,
  Control Page
  `sha256:9c2cdb09f4e763732b5ee9629012d10f3443244173908d7cd045e9d9bccae21c`이다.
  이미지 내부 소스 기준 집중 테스트 100개·38개와 양쪽 `compileall`,
  `pip check`, 전체 profile Compose config가 통과했다. 실행 중인 기존 Bot API와
  Control Page는 교체하지 않았다.
- 음성 전달 실패 연속성 변경은 reply gate를 통과한 사용자 발화 뒤 Discord
  connection 부재, 빈 답변, LLM/TTS 전달 실패가 발생해도 사용자 row만 한 번
  history에 남기고 즉시 durable continuity commit한다. 존재하지 않는 assistant
  답변, memory write, cognitive/search follow-up은 만들지 않는다. 새 프로세스의
  checkpoint restore에서도 마지막 speaker와 미응답 사용자 발화가 유지된다.
- 복구된 미응답 발화는 이제 저장에만 머물지 않는다. Main과 Fast Control의
  최종 prompt assembly가 history의 마지막 non-empty 대화 row를 판정해
  `conversation.unanswered-user.v1` 고정 규칙을 system context에 넣는다. 정상
  assistant 답변 뒤에는 자동 제거되고, restart 및 검증된 cross-surface history에도
  같은 규칙이 적용된다. metrics와 `turn_summary.v1`에는 사용자 본문 없이
  `unanswered_user_turn_context` boolean만 기록한다.
- voice pipeline snapshot, rejoin 상태, STT/wake/TTS 로그와 turn summary는 고정
  오류 코드와 검증된 예외 클래스 이름만 사용한다. legacy 오류 메시지·경로·토큰은
  공개 projection에서 제거하며 STT/TTS/voice delivery 카운터의 고정 코드는
  그대로 보존한다.
- 변경 소스는 집중 테스트 101개, voice 424개, runtime 421개(skip 2), UI
  157개(skip 7), Discord I/O 109개, memory 158개를 통과했다. core 551개는 기능
  assertion 실패 0개였고 이미지에 `git`이 없어 난 기존 signature 환경 오류
  2개는 Windows의 해당 모듈 13개로 보완했다.
- 현재 소스를 내장한 검증 이미지는 Discord/Main
  `sha256:0300dbb9477e4e93bf7c2a0c14c10a7d42a279c9b7ae88b74a5183c952e62877`,
  Bot API
  `sha256:41b56e7ffa4be528c9244fcda158c10712191df631f1b397ecdfee6d9cff364f`,
  Control Page
  `sha256:1f5afc9ffa026574c014bea8a4c4260e602c1a091e9aaef76023f0e36aafb2f5`이다.
  이미지 내부 소스 테스트 101개·65개·65개와 각 이미지 `compileall`, `pip check`,
  전체 profile Compose config, voice validation JavaScript `node --check`,
  `git diff --check`를 통과했다. 실행 중인 기존 Bot API와 Control Page는 교체하지
  않았고 실제 Discord, 마이크, 스피커, 무거운 모델과 사용자 runtime artifact는
  변경하거나 시작하지 않았다.
- 미응답 prompt 연속성 변경은 source-mounted 집중 91개와 core 556개 중 기능
  assertion 실패 0개를 확인했다. core 이미지에 `git`이 없어 난 기존 signature
  환경 오류 2개는 Windows의 해당 모듈 13개로 보완했다. runtime 423개(skip 2),
  voice 424개, UI 157개(skip 7), Discord I/O 109개, memory 158개도 통과했다.
- 현재 소스를 내장한 검증 이미지는 Discord/Main
  `sha256:4f1ab18fd0d6866d0f1f94b709fffdd368849f69cbe26939951e41f029f47a68`,
  Bot API
  `sha256:34ed66e232f2d31bef83c84999d733838bbecf0951274bdef93a319c08df05a9`,
  Control Page
  `sha256:65900f3903090c84e94f7c9cdcc02966fa116b644157b04013f06bae66fdc72c`이다.
  이미지 내부 제품 소스에 read-only test harness를 연결한 집중 테스트 91개씩과
  각 이미지 `compileall`/`pip check`, 전체 profile Compose config, voice
  validation JavaScript `node --check`, `git diff --check`를 통과했다. 실행 중인
  서비스는 교체하지 않았고 Discord, 마이크, 스피커, Minecraft와 사용자 runtime
  artifact는 변경하거나 시작하지 않았다.
- grounded-memory 최종 prompt 경계는 `partial|unattributed` 기억 본문과 길이
  초과로 귀속 대응이 깨진 본문을 Main/Fast 모델 입력에서 보류한다. 고정
  `MEMORY_WITHHELD_RULE`만 남기고 supplied evidence를 비우며 receipt/turn
  summary에는 `state=withheld`, 보류 여부와 content-free item/note/legacy
  count만 기록한다. 알 수 없는 producer receipt 필드도 최종 allowlist
  projection에서 제거한다. `attributed` 기억 본문과 직접 사용자 확인·삭제
  lifecycle은 그대로 유지한다.
- current-source 검증은 core 558개 중 기능 assertion 실패 0개를 확인했다.
  이미지에 `git`이 없어 난 기존 signature 환경 오류 2개는 Windows의 해당
  모듈 13개로 보완했다. runtime 423개(skip 2), memory 158개, voice 424개,
  UI 157개(skip 7), Discord I/O 109개도 통과했다.
- 현재 소스를 내장한 검증 이미지는 Discord/Main
  `sha256:23832d0ab649ca6d0073c02ffc7f3fffd47d9518206cd727a82b9cf5a22f0489`,
  Bot API
  `sha256:d099566ace6d1e06a68f04059c9908174c2ca62faaa76aebc257ced16812c19e`,
  Control Page
  `sha256:05e99df8f6e3ef79fbd2005eee45734d2d3e4ced2f59c67a0033f7c60b14271b`이다.
  이미지 내부 제품 소스에 read-only test harness를 연결한 집중 테스트 147개씩,
  각 이미지 `compileall`/`pip check`, 전체 profile Compose config, voice
  validation JavaScript `node --check`, bundled Python `compileall`과
  `git diff --check`를 통과했다. 실행 중인 서비스는 교체하지 않았고 실제 사용자
  기억, Discord, 마이크, 스피커, Minecraft와 runtime artifact는 변경하지 않았다.
- content-bound user review confirmation은 Control Page가 표시한 exact
  `sourceHash`를 서버에서 lock 안에 다시 비교하고, 성공 sidecar도 같은 hash에
  결박한다. 누락 hash, stale revision, 숨겨진 legacy/internal note, 손상된
  explicit-confirm note와 state write 실패를 각각 fail-closed하며, 이후 note
  변경은 `confirmationState=stale`로 강등한다. 이 review 상태는 provenance
  backfill이나 recall grounding으로 사용하지 않는다.
- current-source 검증은 core 558개 중 기능 assertion 실패 0개를 확인했다.
  Discord 이미지의 `git` 부재로 난 기존 signature 환경 오류 2개는 Windows의
  해당 모듈 13개로 보완했다. runtime 425개(skip 2), memory 162개, UI 157개,
  voice 424개, Discord I/O 109개가 통과했다. 실제 HTTP confirmation의
  CSRF/hash/stale/write-failure 집중 테스트 6개도 통과했다.
- 현재 소스를 내장한 검증 이미지는 Discord/Main
  `sha256:3cd615caeb3f5799f3873768c753d878df553904309d9c551e9ad508169238ed`,
  Bot API
  `sha256:878bd8a79e37a1f450dd057a40bff0d678f32cacf5c42618fac6c7be19c9f1ba`,
  Control Page
  `sha256:0b61d91b17127ce5335215113a2ecd473da6705bcc4ed042b4d7502788697cf7`이다.
  이미지 내부 제품 소스 집중 테스트 132개씩(skip 1), 각 이미지
  `compileall`/`pip check`, 전체 profile Compose config, 모든 Control Page asset
  JavaScript `node --check`, bundled Python `compileall`, `git diff --check`를
  통과했다. 실행 중인 서비스를 교체하지 않았고 실제 사용자 기억과 runtime
  artifact를 읽거나 수정하지 않았다.
- Voice P0 최종 인과성·보안 경계는 retry/abort 뒤 stale attempt, 검증 시작 전
  대기 중이던 무표식 입력·출력, local partial-write fallback 중복, Discord
  teardown ABA와 실제 재생 전·자연 종료 뒤 positive interrupt evidence를 차단한다.
  로컬 interrupt는 첫 PCM write 성공, worker terminal 전 atomic stop acceptance
  token, 정확한 worker 종료·generation·현재 validation attempt가 모두 있어야
  확정된다. 손상된 session ID/path, symlink 탈출, 축약·변조된 canonical suite와
  위조 terminal `passed`, 비-boolean 청취 확인도 fail-closed한다. 최초 attempt의
  기존 confirm payload는 호환하지만 retry 뒤에는 현재 attempt revision을 명시해야
  한다. 검증 중 raw audio debug capture와 STT/wake/reply/재생 예외 원문 운영 로그는
  저장하지 않으며, Host Supervisor는 검증 중 Local Bridge crash를 자동 재시작하지
  않고 현재 attempt를 실패시킨다.
- 최종 소스는 host validation/UI 66개(skip 1), local interrupt 35개,
  Bot validation/API/UI 82개(skip 1), readiness 91개, 전체 voice 499개를
  통과했다. repository 전체 2,260개는 기능
  assertion 실패 0개였고 Discord slim 이미지의 `git` 부재 2개, Linux의 Windows
  OCR 1개, 선택 Voyager `gymnasium` 부재 1개만 환경 오류였다. 앞의 세 환경
  오류 경로는 Windows host 20개로 보완했다. 실제 `main.py` Control Page 격리
  smoke, 양 이미지 `compileall`/`pip check`, 기본·전체 profile Compose config,
  voice validation JavaScript `node --check`도 통과했다.
- 최종 검증 이미지는 Bot API
  `sha256:fd5ff7dbe5c5224f3de6157fffd85ccc1697d6d6810b94808c8bca1ad98fd33b`,
  Discord/Main
  `sha256:75e0ce25d6d7a738a03d48fa04dab65099b3cce61cac8a47a331ce7efb8584be`이다.
  이미지는 빌드·격리 검증만 했고 실행 중인 서비스를 교체하지 않았다. 실제
  Discord, 마이크, 스피커, Minecraft, 무거운 모델과 사용자 runtime artifact는
  시작하거나 읽거나 수정하지 않았다.
- memory-deletion integrity 최종 current-source 통합은 기존 Bot API 이미지에
  저장소를 read-only mount하고 network를 차단한 상태에서 472개(skip 2)를
  통과했다. strict deletion/correction journal과 authenticity, canonical audit와
  derivation revocation, content-free ID projection, background JSON LLM,
  Main/Voice/Fast/Discord sink, exact 503/proxy, API/UI와 durable artifact write를
  함께 검증했다. 실제 stale journal position을 outbound guard, Bot API
  state/middleware와 공개 proxy까지 통과시키는 테스트는 HTTP factory 호출 0회와
  exact `503 + no-store`를 확인한다.
- 같은 소스의 `compileall`, 이미지 `pip check`, no-op `Bot.run` Main 배선 smoke,
  fast-control 및 memory-integrity Compose 병합, Control Page 인라인 JavaScript와
  11개 asset의 `node --check`, `git diff --check`를 통과했다. 최종 검증은 기존
  이미지로만 수행했지만, 중간 하위 작업에서 로컬
  `evelyn-fast-control-bot_api:latest` 이미지가 한 번 실수로 빌드됐다(manifest
  list `sha256:ae9ee365523fe23086f7e6b3f820e2cb5661d97dc20949d9f0d891a404213b3e`).
  이 이미지를 배포하거나 서비스로 기동하지 않았고, 이후 추가 빌드는 중단했다.
- 격리된 임시 memory root의 host 전체 discovery는 1,868개를 실행해 assertion
  실패 0개, skip 17개였다. 번들 환경에 `aiohttp`, `discord`, `requests`, `davey`,
  `torch`가 없어 42개 모듈은 import 단계에서 실패했으며, 이번 변경과 직접
  관련된 해당 경로는 위 Docker 통합 검증으로 보완했다. 실제 사용자 memory,
  Discord, 마이크, 스피커, 모델 서비스와 실행 중 컨테이너는 건드리지 않았다.
- 현재 conversation deletion 증분은 assistant row에 content-free
  `conversation.memory-receipt-ref.v1`을 부여한다. `bound`는 attributed
  memory version과 canonical supplied-note ID에 묶이고, `not_used`는 저장
  기억 비사용이 명시적으로 증명된 row만 표시하며, 누락·손상·
  표현 불가능한 legacy 의존성은 `unattributed`로 fail-closed한다.
  receipt는 durable continuity, restart restore, session merge와 Control Page·Discord
  text·Discord voice cross-surface merge까지 보존된다. 공개 chat/state/action
  projection은 receipt와 note ID를 제거한다.
- Main/Fast/Voice/Search/tool의 history assembly는 assistant receipt를 검사해
  missing legacy, invalid, `unattributed`, stale memory version, tombstoned-note row를
  prompt 전에 제거한다. 필터를 통과한 결과의 deletion position만
  persona/cognitive/router/planner, search follow-up와 tool 결과에 병합하고,
  history-derived cache는 strict receipt로 현재성을 증명하지 못하면 무시한다.
- production autonomy도 observation·summary·recent-context·ping·cognitive-refresh의
  다섯 history 소비 지점에서 같은 필터를 사용한다. 최초 observation exposure는
  plan→execute→persist 전체를 guard하고, 이후 fresh callback은 소비와 side effect
  동안 자체 guard를 잡는다. 무결성 실패는 executor 실패로 낮추지 않는다. 반환·
  durable autonomy state는 raw history/summary/text와 private assistant-history
  의사결정 신호를 제거하며, self-state의 unresolved 입력은 user row만 센다.
  자율 후속 history는 current exposure의 compact receipt를 continuity까지
  보존한다.
  receipt 없는 observation/goal/plan/step/router/drive cache는 재시작 때 재사용하지
  않고 안전한 운영 필드만 남긴 상태로 즉시 다시 저장한다. Minecraft plan/cursor는
  world observation만으로 결정되므로 bound 대화가 함께 있어도 유지한다. 일반
  autonomy loop나 cognitive refresh가 진행 중인 guild memory reset은 continuity나
  파일을 지우기 전에 고정 코드로 거부하고, 사용자가 자율행동을 먼저 끄거나 작업
  종료 뒤 재시도하도록 안내한다.
- memory-derived proactive queue는 deletion-current receipt가 없어 selection과 ID
  mark를 닫았고, producer도 raw/ask text duplicate를 비운다. 과거 queue와 pending
  원문은 memory index sync에서 제거하고 note 삭제는 receipt 없는 autonomy cache도
  정리한다. symlink·junction scope alias는 다른 scope를 지우지 않고 전체 작업을
  fail-closed한다.
- legacy layered memory는 deletion-current lineage가 생길 때까지 stored summary,
  facts, questions와 assistant raw를 Main/cognitive/writeback 입력에서 전역 보류한다.
  exact user raw만 기존 evidence 검사를 거쳐 사용할 수 있다. 같은 원문을 복제한
  legacy mirror, daily conversation과 semantic derived note도 live recall/hot context에서
  제외한다. retrieval cache v2와 hot-context recall policy marker가 과거 cache 재사용을
  막는다. provenance-bearing `open_questions.jsonl` 저장·감사, explicit user/system
  note와 현재 모델 답변의 명시적 질문은 그대로 유지한다.
- derivation revocation의 ledger ID 저장 순서와 raw graph ID 비교 순서를 같은 raw-ID
  canonical 순서로 맞췄다. 의미가 같은 반복 sync는 revocation 파일과 `updatedAt`,
  hot-context를 다시 쓰지 않는다. `contentFree` 없는 과거 raw-ID artifact는 첫 read에서
  private ID를 제거한 canonical 형식으로 durable migration하며 실패는 fail-closed한다.
- 이 memory/autonomy 증분을 포함한 CI-equivalent 전체 탐색은 2026-08-08
  `Ran 3078`, `OK (skipped=21)`이었다. core 731개(skip 1), memory 269개(skip 1),
  Discord I/O 124개, `compileall`, `pip check`, `git diff --check`도 통과했다.
  실행 중인 로컬 LLM에 의존하던 Fast Control 실패 테스트는 직접·음성 queue 호출을
  모두 고정 실패로 격리했다. live 서비스와 실제 사용자 기억은 변경하지 않았다.
- Main Control Page와 Fast Control Page는 handler 반환이 아니라 actual HTTP
  `prepare` 직전부터 `write_eof`까지 exact memory exposure guard를 유지한다.
  stale guard는 content-free exact 503과 `no-store`로 닫히며, Fast stream은 첫
  content 전에 typed `memory_boundary`를 전송한다. Discord/in-process TTS는
  producer lease가 끝난 뒤 playback owner가 동일 경계를 재확인하며,
  Windows Local I/O Bridge는 bound sentence/delta를 HTTP EOF까지 buffer한 뒤
  host guard 안에서만 TTS/PCM을 시작한다. missing·malformed·stale
  boundary는 실제 재생 0회다.
- voice success side effect는 exact reply receipt와 exposure가 일치하고 실제
  playback이 성공한 뒤에만 commit한다. 이 단계 이전의 stale·mismatch·
  재생 실패는 assistant history/continuity, memory write, search follow-up,
  session/persona 갱신을 남기지 않는다. 수용된 사용자 턴만 미응답
  continuity로 보존할 수 있다. receipt/boundary/guard/validation 메타데이터에는
  raw audio, transcript, prompt/history/assistant content를 복제해 저장하지 않는다.
- 이 증분에서 현재까지 확정된 격리 검증은 session/continuity 66개,
  Fast delivery boundary 34개, Fast action 77개, voice post-playback side effect
  12개, TTS handoff 14개, local direct EOF→host handoff 8개, Control Page
  집중 305개다. 전체 저장소 회귀·정적 검사의 최종 합산은 통합 종료 후
  갱신한다. 이 검증은 임시 memory/artifact root만 사용했고 실제 사용자
  기억과 runtime artifact를 읽거나 수정하지 않았다. Discord, 마이크,
  스피커, Minecraft, Docker 서비스도 시작·교체하지 않았으므로 실제
  음성 하드웨어 E2E는 여전히 미검증이다.

## Operational boundaries

- Bot API: `127.0.0.1:8798`
- Control-Page: `127.0.0.1:8799`
- Optional Codex Gateway: `127.0.0.1:8787` (`codex-gateway` profile,
  tool-access 검증 전 not-ready)
- Control-Page 변경성 요청은 CSRF 세션 계약을 사용한다.
- 런타임 repair는 preview와 apply를 분리하며, preview만으로 프로세스를 시작하지 않는다.
- Host Vision 요청은 `runtime_artifacts/host_vision/`의 exact-schema queue만
  사용하고, Host Supervisor가 소유한 Local I/O Bridge만 화면을 캡처한다.
- Host UI Action 요청은 `runtime_artifacts/host_ui_action/`의 exact-schema
  queue만 사용한다. 경계는 배포됐지만 실제 action 실행 횟수는 0이다.
- 현재 실행 중인 Docker 서비스는 Bot API와 Control Page뿐이다. 무거운
  LLM/STT/TTS/Vision과 Discord/Minecraft, Windows Host Supervisor/Local
  Bridge는 이번 작업에서 시작하지 않았다.
- 추가 Voice/Mindcraft P0 source 감사에서 required speaker verification의
  미판정 fail-open, 실제 출력 형식 검증 없는 local `outputReady`, cached user에
  기대던 Discord gateway readiness, background Mindcraft reconcile의
  world-action lease 검증/효과 TOCTOU를 닫았다. 로컬 barge-in은 exact
  `matched=true`만 승인하고, 출력은 선택/default 장치의 24 kHz mono `int16`
  설정을 비가청 probe한 결과가 exact true여야 한다. Discord heartbeat는 live
  `is_ready()`, not-closed와 `ws.open`을 모두 요구한다. Mindcraft reconcile은 guarded lease
  read부터 stop/ensure-start effect까지 stable `world_action.lock`을 유지하며
  endpoint가 이미 획득한 exact-path lock capability만 재사용한다.
- 이 증분은 Voice 529개(skip 5), Mindcraft 18개, Minecraft 157개(skip 8), 저장소
  전체 2,503개(skip 18)를 통과했다. Python `compileall`과 Control Page JavaScript
  11개의 `node --check`도 통과했다. 실제 출력 스트림, 마이크, Discord gateway,
  Minecraft runner, Docker 이미지/서비스는 시작하거나 교체하지 않았으므로
  surface별 10턴·무음과 실제 owner handoff/effect 증거는 계속
  [ACTIVE_RISKS.md](ACTIVE_RISKS.md)에 남는다.
- 2026-08-01의 추가 runtime identity 경계는 Bot API, Control Page와 Discord
  이미지에 동일한 exact 40/64자리 source revision을 build-time에 넣고, 실행 시
  기대 revision과 일치할 때만 `runtime_source_identity.v1`을 `aligned`로 판정한다.
  누락·손상은 `unverified`, exact 불일치는 `mismatch`이며 Bot state/chat/voice와
  Control Page proxy/health, Discord artifact readiness가 모두 fail-closed한다.
  launcher는 clean Git HEAD 또는 그와 같은 명시 revision만 허용하고 dirty tree와
  오래된 환경 revision을 거부한다. `EVELYN_DOCKER_BUILD=true`의 허용 이미지에는
  Discord도 포함돼 Bot/Control만 새롭고 Discord가 오래된 혼합 배포를 막는다.
- Windows background launcher는 Supervisor의 `localBridge.running`만 신뢰하지
  않는다. 별도 Local I/O Bridge status의 exact schema, `ready`, mic 상태와
  `captureReady`를 확인하고 Supervisor/Bridge 양쪽 heartbeat가 각각 두 번
  증가해야 시작 완료를 반환한다. Voice P0 무음 단계도 서버의 15초 타이머만으로
  통과하지 않는다. Local은 bridge+mic+capture, Discord는 선택 guild/channel의
  gateway+voice connection+listening heartbeat가 현재 attempt에 연속 결합돼야
  하며 최대 gap은 각각 2초/3초다. stale/false heartbeat, 중간 단절, 이전 retry
  attempt와 순서가 뒤집힌 샘플로 gap을 숨기는 경우를 모두 실패 처리한다.
- 이 증분의 current-source 검증은 runtime 574개(skip 4), voice 547개, UI
  166개(skip 7)를 통과했다. core 630개는 기능 실패 0개였고 검증 이미지의 `git`
  부재로 난 기존 signature 검사 2개를 Windows bundled Python에서 별도로
  통과시켰다. source identity/launcher/Compose/무음 liveness 집중 149개와
  source identity API 포함 Control Page 집중 95개, 기본·전체 profile Compose
  config와 Python/PowerShell 구문 검사도 통과했다. 실행 중인 오래된 Bot API와
  Control Page는 교체하지 않았고 실제 마이크, 스피커, Discord, Minecraft와
  사용자 runtime artifact를 시작하거나 변경하지 않았다.
- 2026-08-02의 Local Bridge process-lifetime 경계는 OS single-instance lock,
  Windows `KILL_ON_JOB_CLOSE` Job Object와 durable PID+birth identity startup
  reconcile을 함께 사용한다. Supervisor가 죽으면 Job close가 할당된 자식을
  정리하고, 재시작 시에는 exact birth identity가 같은 고아만 종료·사후 검증한다.
  PID reuse는 신호를 보내지 않으며 ambiguous identity/lock, Job assignment와
  durable write 실패는 새 브리지를 만들지 않는다. launcher readiness도 현재
  Supervisor child PID와 Bridge heartbeat PID의 일치, ownership ready와 birth
  기록을 요구한다. focused 139개와 실제 Windows Job close child test를 통과했지만
  현재 실행 서비스 교체와 실제 음성 E2E는 수행하지 않았다.
- 2026-08-02의 로컬 capture-consent 경계는 상태 load를
  `verified | missing | untrusted`로 나누고, 신뢰할 수 없는 상태를 exact OFF ACK
  이전에 `inactive`로 취급하지 않는다. durable `enabling/revoking` fence,
  revision+일회성 action ID+Bridge digest+physical capture 상태 ACK, 독립된
  reporter/internal-control bearer, 단조 `statusSeq`와 instance generation,
  OFF가 증가시키는 ON enable fence를 함께 사용한다. 취소·terminal validation·
  malformed upstream·손상 state·Control Page 재시작은 같은 consent lock과 recovery
  경로로 OFF를 재시도한다. Local Bridge는 ambient `LOCAL_MIC_ENABLED`로 캡처를
  시작하지 않고 일반 `/mic on`도 동의 경로를 우회하지 못한다. focused 159개와
  runtime 667개(skip 4), voice 전체 568개(skip 5), `test_local*.py` 182개가 통과했고
  Python/Node/PowerShell 구문, standalone Compose config와 clean bundle
  `pip check`도 통과했다.
  launcher credential은 Docker `up` 생성 순간에 서비스별 reporter/internal/capture
  채널을, Supervisor 생성 순간에는 reporter와 capture 채널만 자식 환경에 넣고 즉시
  제거한다. capture HMAC 키는 매 launcher 세대 새로 만들며 host preflight,
  Control Page의 helper/opener와 브라우저는 이를 상속하지 않는다.
  preview는 최신 1개와 발급 당시 validation 세대에 묶이고, unbound 동의는 canonical
  idle에서만 유지된다. idle ON 뒤 Discord-only 세션, bound identity/state 유실과
  confirm/retry/abort의 모호한 I/O 예외는 모두 즉시 exact OFF로 전환한다.
  Supervisor의 Docker 복구는 `--no-deps`와 credential-scoped 환경을 사용한다.
  Local Bridge 하위 프로세스는 credential을 받지 않으며, 전체 재시작은 exit 75를
  받은 Supervisor가 필요한 Discord/Codex 설정만 짧은 handoff에 전달한다.
  이는 source/mock 증거이며 실제 마이크·스피커·Discord는 실행하지 않았다.
  current source에서는 Control Page가 manager 생성·상태 읽기 전에 stable
  `voice_capture_consent/owner_claim.lock`의 process-lifetime OS lock을 획득한다.
  busy/unavailable loser는 고정 오류로 startup을 중단하며 state, heartbeat, mic
  control을 수행하지 않는다. aiohttp cleanup 역순과 cancellation-draining shutdown
  task가 monitor/heartbeat writer 종료와 exact OFF 철회 뒤에만 lock을 반납한다.
  runtime artifact retention은 어떤 matching rule에서도 `owner_claim.lock`을 삭제
  후보로 만들지 않는다.
  실제 별도 프로세스 경합과 `os._exit(78)` crash 뒤 successor 인수, cleanup 취소 중
  contender 배제까지 검증했다.
- 2026-08-02 current source의 hard-crash watchdog은 Control Page가 1초마다 게시하는
  목적 제한 HMAC의 content-free owner/lease projection을 Local Bridge가 각 0.25초
  status tick과 ON 전·후에 검사한다. 4초 stale, expiry, owner/lease replacement,
  누락·손상·symlink에서는 새 입력과 admission을 폐기하고 exact capture stop을
  수행한다. stop 실패는 exit 76으로 프로세스를 끝내 OS handle을 회수한다.
  Bot API에는 capture HMAC 키를 주입하지 않는다. 대신 bearer-authenticated Bridge
  status의 content-free fence digest를 Host lease와 Control Page durable consent state에
  3자 대조하고, 현재 Bridge/mic/watchdog 상태와 함께 admission 발급·claim 경계에서
  fail-closed 판정한다. 공개 status에서는 Bridge instance와 fence digest를 제거한다.
  durable consent state 변경과 Bot API의 마지막 fence 확인+reservation/claim은 stable
  `claim_lease.lock`의 process-local mutex+OS lock으로 선형화한다. Control Page의
  blocking acquire는 2초 단일 deadline을 사용하고, timeout·state write 실패는
  memory-first `revoking/untrusted` 뒤 physical OFF와 durable reconcile을 계속한다.
  async revoke는 lock wait를 worker thread로 옮긴다. 두 stable lock은 retention 정리
  대상에서 영구 제외된다.
  Supervisor stop evidence는 현재 child PID/시작 시각, 서명된 전체 status, 고정
  instance, `statusSeq` high-water, watchdog 시각과 nested/top-level physical OFF를
  모두 요구한다. owner heartbeat는 4 KiB, Bridge status는 128 KiB로 제한된다.
  검증 밖의 Bridge exit는 기존 예산 안에서 disabled-default로 복구하고 검증 중 exit는
  자동 재개하지 않는다. focused runtime 152개와 voice 51개, owner/인접 회귀
  90개(skip 1), runtime 전체 692개(skip 4), voice 전체 574개(skip 5), 전체
  discover 2938개(skip 20)가 통과했다.
  Compose config, Python/JavaScript/PowerShell 구문, `pip check`도 통과했지만 실행 중
  서비스를 교체하거나 실제 마이크·스피커·Discord를 시작하지 않았다.
- 2026-08-02 current source에는 LLM·tool·외부 전달 전에 stable source delivery를
  기록하는 `conversation.ingress-recovery.v1` journal/head owner가 추가됐다.
  Fast Control은 browser `requestId`와 Local Bridge의 canonical
  `[bridgeInstanceId, turnId]`, Discord text는 message ID를 사용하고 journal
  `turnId`를 continuity의 권위 있는 turn ID로 재사용한다. pending/in-flight/
  ambiguous delivery는 재시작 뒤 자동 실행·재전송하지 않는다. Control Page HTTP EOF,
  Discord 전송 성공과 Local Bridge의 exact software-playback ACK만 assistant 완료를
  확정한다. Local Bridge 실패 ACK는 user-only checkpoint 뒤 journal을 정리하고,
  Fast partial stream의 확인되지 않은 assistant는 재전송하지 않는다. Discord는
  session/reply-slot 선점으로 동시 claim을 직렬화하며 기존 turn P 뒤 전달된 turn
  Q도 exact sent text/receipt를 보존해 재시작 reconcile한다. 공개 status에는 raw
  text와 source/entry/turn ID를 내보내지 않는다. `delivery_succeeded` 복구의
  저장 증거는 현재 `turnId`와 history의 exact user/assistant/receipt tail이 함께
  일치해야 한다. 같은 current turn의 strict user-only crash checkpoint만 assistant,
  compact receipt와 assistant state를 commit 전에 한 번 완성하며 journal과 같은 NFKC
  정규화를 쓴다. fresh/pair/user-only 복구는 기존 `active_until`을 보존하고 새 TTL을
  발급하지 않는다. 다른 turn의 동일 문장과 과거 동일 문답은 commit 증거로 쓰지 않는다.
- Local Voice Fast Control 경로는 별도 `consume()` 뒤 claim을 호출하지 않는다.
  stream/non-stream이 같은 typed transaction을 사용하고, token 응답 전에 exact
  content-free ingress reservation을 durable 기록한다. Bot 재시작 뒤에는 Bridge가
  제시한 token digest, bridge/turn/text/mode/validation binding과 현재 capture fence를
  다시 계산해 reservation을 claim하며, durable receipt의 schema, entry ID, text hash,
  phase/disposition, process 여부와 journal generation을 exact 검증한다. durable
  reservation token은 verified flag, reservation ref와 ingress turn도 exact해야 한다.
  issue 전·reservation 직전·직후와 claim 직전에 capture-consent fence를 다시 검사한다.
  철회 race에서는 exact reservation과 live capability를 revoke하며 revocation write를
  증명하지 못하면 content-free 503으로 닫는다.
  ingress turn과 reservation reference의 v2 proof에는 발급 capture fence digest가
  포함된다. 따라서 Bot manager 재시작과 consent A→B 교체 뒤에도 A token이 B에서
  claim되지 않는다. mic-off·철회·restart·shutdown은 manager가 모르는 restart-orphan
  `reserved` row도 exact Fast Control scope에서 durable purge하며 claimed/completed와
  다른 scope는 보존한다.
  실제 reserve/claim/head write 오류와 claim 직후 `os._exit`을 재현했고, 재시작 뒤
  accepted pending 하나, 자동 대화 재실행 0과 replay-only duplicate를 확인했다.
  따라서 `token response -> Bot restart -> chat`과 `consume -> durable claim`의 source
  crash-loss 창은 닫혔다. 이는 실제 음성 장치 E2E 완료를 뜻하지 않는다.
- Local validation issue/consume과 confirm/retry/abort는 attempt별 cross-process OS
  lease로 직렬화된다. 성공·409·503 응답의 lease는 실제 HTTP EOF 또는 terminal
  failure까지 유지된다. validation LLM payload는 system+현재 user만 포함하고
  memory/history/tool/search/vision을 사용하지 않는다. assistant 원문은 normal
  history/checkpoint/replay에 저장하지 않고 content-free SHA-256 marker와
  non-replayable receipt로 terminalize한다. accepted user text는 exact claim을 위해
  bounded ingress journal TTL 동안만 남고 report/history로 복제하지 않는다. durable
  claim 뒤 validation event write 실패는 type-only 로그로 흡수해 token과 LLM을
  재실행하지 않으며, 누락된 accepted event는 현재 attempt의 성공 증거가 되지 않는다.
- 최종 current-source hardening 묶음은 Local Voice admission, Fast Control ingress,
  consent/claim lease, validation API·attempt lease와 retention을 포함해 267개(skip 1)가
  통과했다. 2026-08-02 `.venv`에서 CI와 같은
  `unittest discover -s tests -t . -p "test_*.py"` 범위는 `Ran 3044`,
  `OK (skipped=20)`이었다. `compileall`, `pip check`, validation JavaScript 구문,
  Compose config와 `git diff --check`도 통과했다. 이전 Windows world-action lock 정리
  오류 2개는 살아
  있는 자식을 검증하기 위해 잠금을 의도적으로 보존한 테스트가 임시 폴더 정리 전에
  정상 회수 경로를 실행하지 않던 harness 문제였다. 테스트 cleanup은 자식 종료를
  모사한 뒤 exact cancel로 잠금을 반납하므로, child-alive 동안의 fail-closed fence는
  그대로 유지된다. 현재 실행 서비스 교체, 실제 Discord redelivery/network timeout,
  마이크·스피커 live E2E는 수행하지 않았다.
- 자율행동 P0에는 별도 content-free dry observer를 추가했다.
  - `autonomy-p0.v1`은 Control Page에서 session 상태와 고정 blocker를 보여주되,
    Discord 명령, grant/lease, runtime repair, 서비스, Minecraft goal/effect 또는
    host request queue를 실행하지 않는다.
  - assistant 트랙은 같은 grant와 실행별 `actionRunId`의 pre-authorize,
    post-execution recheck 두 건 및 그 뒤의 exact typed outcome만 journal 순서대로
    인정한다. Minecraft cleanup도 revoke와 verified stop이 같은 lease에 속해야
    하며, process rollover는 non-restoration과 새 epoch의 global stop을 함께
    증명해야 한다.
    Minecraft의 `goal_verified`와 readiness만으로는 world effect를 인정하지 않고
    trusted explicit postcondition 증거를 별도로 요구한다.
  - 현재 production `RoutedAutonomyExecutor`에는 guild별 typed Minecraft executor가
    등록된다. Discord `마크접속`이 실제 연결을 확인한 뒤 route를 활성화하고,
    `자율시작`이 route를 다시 검증한 경우에만 exact allowlist
    `minecraft:find_food_source`를 grant에 추가한다. route가 없거나 재검증에
    실패하면 assistant scope만 발급한다.
    `자율정지`의 engine lifecycle disconnect는 물리 executor만 멈추고 process-local
    route intent를 보존한다. 명시적 `마크종료`의 `disable_domain`만 route intent를
    먼저 지우므로 뒤이은 executor cleanup이 실패해도 다시 활성화되지 않는다.
    enable·disable과 lifecycle connect·disconnect는 같은 router lock에서 직렬화한다.
    engine start·stop도 task cancel과 executor cleanup, 상태 commit이 끝날 때까지 같은
    engine lock을 유지한다. disabled stale loop와 실패한 cleanup은 재시작 전에 정리하며,
    start 호출자 취소는 새 loop를 만들지 않고 재전파한다.
  - 실행 중 trusted planner는 현재 grant에 포함된 step의 연속 prefix만 만든다.
    첫 미허가 step에서 멈추므로 안전 선행조건을 건너뛰지 않고, prefix가 비면
    executor·authorization audit 호출 없이 다음 관찰을 기다린다. 음식이 생긴 뒤에는
    `find_food_source`를 반복하지 않는다. stale·직접 주입 plan의 강제 거부는 유지한다.
  - Mindcraft action gateway의 content-free world-effect projector와 validation
    observer는 같은 shared artifact의 exact grant·lease·actionRun·goalRun·contract
    증거를 상관시킨다. source 배선은 연결됐지만 실제 Discord와 Minecraft world에서
    단일 승인 E2E를 수행하지 않았으므로 자율행동 P0 운영 완료를 주장하지 않는다.
  - 최종 회귀는 runtime 583개(skip 4), Minecraft 160개(skip 7), UI 171개(skip 8),
    변경 집중 132개를 통과했다. core discovery 656개에는 기능 assertion 실패가
    없었고 Bot API 검증 이미지에 없는 `git`·Pillow·Discord 의존 4건은 Windows
    bundled runtime과 Discord 이미지에서 실제 9개 테스트로 모두 통과했다.
    `compileall`, 새 JavaScript `node --check`, `pip check`, Compose config와
    `git diff --check`도 통과했다. 실행 중 컨테이너와 실제 Discord·Minecraft,
    사용자 grant/lease/runtime artifact는 시작하거나 교체하지 않았다.

## 2026-08-03 기본 TTS OmniVoice 전환 source 상태

- 기본 Compose 서비스명과 URL은 `tts:8880`으로 유지하면서 구현을
  `k2-fsa/OmniVoice`로 교체했다. 기존 VoxCPM2는 `voxcpm_fallback`과
  `tts-fallback` profile, host port 8881의 opt-in 호환성·진단 서비스로만 남는다.
  자동/runtime fallback은 아니며 이를 시작해도 client는 8880에서 reroute되지 않는다.
- 새 이미지에는 외부 checkout 전체, 음성 샘플, venv와 runtime cache를 넣지 않는다.
  `omnivoice_server/`의 Python 파일 20개만 named context로 복사하며 exact 파일 목록과
  SHA-256을 build에서 검증한다. 시작 시 고정 revision의 필수 model snapshot 경로
  13개도 SHA-256으로 검증한다. 직접 runtime 의존성만 고정했으며 전이 wheel과 base
  image digest까지 고정된 완전 재현 build는 아니다. Evelyn profile과 Hugging Face
  `hub/` cache는 read-only다. cache는 offline revision
  `c5fdb5ccb189668d56333f77ba2629f4cd7535f4`를 포함해야 한다.
- Compose health, Windows launcher, service manifest와 공식 runtime checker가
  `healthy + ready + model_loaded + k2-fsa/OmniVoice + exact model_revision`을 동일하게
  요구한다. HTTP 200이나 이전 VoxCPM health만으로는 readiness를 얻지 못한다.
  Local Bridge 기본 경로는 OmniVoice `/v1/audio/speech`의 sentence PCM stream이다.
  실험적 blockwise generation은 disconnect cancellation이 안전해질 때까지 꺼져 있다.
- 서버 patch는 operational log에서 합성 text, 경로, session/turn 식별자를 제거하고
  profile API와 request validation 오류 응답에서 입력 원문을 숨긴다. Compose는 TTS image를 `pull_policy: never`로
  외부에서 받지 않는다. path-safe builder가 missing/fresh image를 만들며 standalone TTS
  launcher도 image가 없으면 이를 호출한다. client의 clone fallback 로그와 최종
  실패 예외도 upstream HTTP 오류 본문·voice profile을 복사하지 않고 고정
  `tts_request_failed`·`omnivoice_request_failed`만 남긴다. Local Bridge warmup도
  non-200 body를 읽지 않고 status에는 `tts_warmup_failed`, 로그에는 이 코드와
  exception type만 남긴다. Supervisor repair는 existing image만 재사용한다.
- 최종 변경 집중 149개와 기존 OmniVoice request/source·VoxCPM profile 계약 17개가 통과했다.
  Local Bridge의 기본 경로 변경 뒤 voice 전체 606개(skip 5)도 통과했다.
  최종 runtime 전체 756개(skip 4)도 실패 없이 통과했다.

## 2026-08-08 기본 TTS OmniVoice live 전환

- recipe `evelyn-omnivoice-tts:recipe-7cfc51e96088`을 source revision
  `485c81d480f45dba4935a26ebb874d11e2f5931a`에서 build하고, 종료된 VoxCPM2
  `evelyn-tts`를 같은 서비스명·host port 8880의 OmniVoice container로 recreate했다.
- container 시작 시 model snapshot 13개 SHA-256이 통과했다. runtime health는
  `healthy`, `ready=true`, `model_loaded=true`, `model_id=k2-fsa/OmniVoice`, exact
  `model_revision=c5fdb5ccb189668d56333f77ba2629f4cd7535f4`를 반환했고 Docker health도
  `healthy`다. profile과 Hugging Face hub mount는 실제로 read-only이며 CUDA는
  RTX 5090에서 사용 가능하다.
- 직접 `/v1/audio/speech` clone sentence stream은 HTTP 200, 24 kHz mono 16-bit
  little-endian PCM 101,280 bytes(약 2.11초)를 728 ms에 반환했다. PCM은 메모리에서
  contract만 확인하고 즉시 폐기했으며 파일·재생·transcript를 남기지 않았다.
  `/v1/models`는 OmniVoice root를, Evelyn profile API는 `has_ref_text=true`와
  `ref_text` 미노출을 반환했다. 실제 합성 후 로그에도 probe text, reference path,
  session/turn 식별자가 없었다.
- 이 증거는 server·clone contract의 live 통과다. 사용자 스피커 청취, 실제 마이크와
  Discord의 10-turn·무음·barge-in E2E 완료를 뜻하지 않는다.

## 2026-08-08 정상 실행과 Local Voice 연속성 배포

- 기본 launcher는 Bot API·Control Page image의
  `EVELYN_IMAGE_SOURCE_REVISION`을 clean Git revision과 exact 비교한다. 둘 중 하나가
  missing/stale이면 allowlist builder로 Bot API, Control Page와 Vision을 갱신하고,
  두 control service의 role·image/expected revision과 proxy identity를 `/health`에서
  확인한 뒤에만 Host Supervisor를 시작하고 준비 완료를 보고한다.
- 실제 stale `7c4770e` image에서 자동 rebuild가 선택됐다. 첫 실행이 드러낸 optional
  `BuildContexts` StrictMode 결함을 공통 builder에서 수정한 뒤 정상
  `start_local.bat --background`가 exit 0으로 완료됐다. 공식 checker는 Control Page,
  Bot API, Main/Router/Sub LLM, OmniVoice, STT, Vision과 Windows Local Bridge의 필수
  readiness를 모두 통과했다.
- Local Bridge는 mic OFF, output ready, OmniVoice clone warmup 564.1 ms, error count 0,
  playback count 0이었다. HTTP PCM 본문·remainder·tail write는 기존 cancellation-safe
  writer를 사용해 cancel 중 worker 종료 전 playback owner를 넘기지 않는다. clone의
  HTTP 200 빈 PCM은 재생 전일 때만 기존 `auto` 후보로 넘어가며 부분 재생 뒤에는 중복
  발화를 만들지 않는다. voice 608개(skip 5), runtime 756개(skip 4)가 통과했다.
- 실제 마이크·스피커는 사용하지 않았고 Discord·Minecraft도 기동하지 않았다. 따라서
  사용자 청취, local/Discord 10-turn·무음·barge-in과 승인된 세계 행동은 남아 있다.

## 2026-08-08 Control Page 채팅 화면 연속성

- Control Page 채팅 전송은 form navigation이나 page reload를 사용하지 않는다. 사용자
  증상과 일치하는 확인된 코드 경로는 일시적인 Bot API proxy 실패의 HTTP 200 degraded
  state가 boot splash를 다시 표시하고 단일 `Control` 진단문으로 기존 채팅 DOM을
  교체하는 것이었다. 서버의 durable 대화 기록이 삭제되는 경로는 아니었다.
- page lifetime에서 최초 ready를 단조 latch한다. 이후 정확한
  `bot_api_unavailable`·`bot_api_proxy_pending` degraded state는 runtime health와 repair
  상태만 갱신하고 boot splash와 기존 채팅 기록은 유지한다. 정상 또는 다른 상태의
  실제 Bot API chat history는 계속 렌더링한다.
- 1.5초 state poll은 single-flight로 제한했다. 모든 refresh에 generation을 부여하고
  채팅 전송 시작·종료에서 세대를 전진시켜, 오래된 poll 성공·실패가 최신 채팅 응답과
  연결 상태를 덮지 못한다. 초기 부팅부터 Bot API가 unavailable인 경우에는 기존
  boot splash와 진단 채팅을 유지한다.
- 서버 continuity checkpoint·재시작 복구와 브라우저의 14분 stable pending request ID는
  기존 계약대로 유지된다. UI 전체 176개, proxy/runtime health/continuity 포함 집중
  60개, fresh degraded→ready→degraded→recovered와 stale generation을 실행하는 Node
  전이 표, JavaScript 구문 검사가 통과했다. bind-mounted live root와 helper asset은
  변경 파일과 exact hash가 일치하고 GET health/state가 ready·boot 100%였다. 실제 채팅
  POST와 장애 유도, 이미지 교체는 하지 않았으므로 전이의 live 재현은 아직 수행하지 않았다.

남은 문제는 [ACTIVE_RISKS.md](ACTIVE_RISKS.md)에만 유지한다.

## 2026-08-08 deletion journal shared exposure lease

- 삭제 journal writer와 같은 lock file·byte range에 shared reader lease를 추가했다.
  Windows는 `LockFileEx`, POSIX는 `flock(LOCK_SH)`를 사용하고 process 안에서는
  thread와 async task를 함께 식별한 reader owner 집합을 유지한다. nested reader와
  writer→reader 재진입은 허용하지만 reader→writer upgrade는 fail-fast한다.
- 이미 materialize된 Main/Voice/Search/Discord/Control Page/TTS memory exposure와 실제
  HTTP response, deletion-only JSON LLM outbound, route LLM, conversation receipt
  screening은 shared lease를 사용한다. 두 reader는 process·교차 process에서 공존하고,
  한 reader라도 남아 있으면 삭제 writer는 journal을 바꾸지 않고 고정 busy 오류를 반환한다.
- 정상 경합은 `memory_deletion_journal_busy`로 무결성 손상과 분리했다. 8798과 8799는
  exact no-store 503의 두 필드만 반환하고 Control Page는 잠시 뒤 재시도를 안내한다.
  OS 오류, lock owner, path, note ID와 원문은 응답에 포함하지 않는다.
- 일반 reader는 repair가 필요한 snapshot을 shared lease 밖의 writer에서 재검증·repair한
  뒤 다시 진입한다. fresh recall의 Busy fallback은 `allow_repair=false`라 chain head를
  쓰지 않고 unavailable로 닫는다. index sync, retrieval-cache migration/write와 실제
  cognitive·memory artifact commit은 writer로 유지했다.
- 정상 recall은 기존 writer sync/cache를 유지하고 진입 전 exact busy만 shared no-cache
  fallback으로 전환한다. fallback은 SQLite sidecar·symlink, `schema_version != 6`,
  비정규 `memory_version`과 필수 metadata/notes query 실패를 거부하고
  `mode=ro&immutable=1`에서 최대 500개 후보를 현재 Markdown hash·ID·path,
  tombstone·quarantine·confirmation에 다시 결합한다. cache/FTS/vector/graph/hot,
  legacy layer와 disk cognitive state는 사용하지 않고 explicit user/system note만
  렌더링한다. Main과 Fast는 같은 shared deletion position을 receipt와 outbound에
  결합하며 `indexFresh=false`, `readOnlyFallback=true`를 남긴다.
- semantic consolidation은 shared phase에서 current source tombstone과 full hash를
  확인하고 Sub-LLM 응답을 받은 뒤, fresh writer에서 source hash를 다시 확인해 note
  batch와 index sync를 적용한다. derivation recomposition은 짧은 writer snapshot에서
  position과 exact revocation digest를 캡처하고, writer를 놓은 뒤 target/source 후보와
  hash를 수집해 shared phase에서 모두 다시 확인한다. apply writer는 pre-sync 뒤 exact
  revocation entry, ordered live/quarantine source와 hash를 재계산한다. 두 경로 모두
  current state가 달라지면 stale result를 버리고, 같을 때만 write와 post-sync를 같은
  writer 안에서 완료한다.
- 일반 reader는 최대 45초 Sub-LLM과 공존한다. 삭제·편집 writer는 그 shared phase 동안
  retryable busy가 될 수 있고 recomposition은 기본 최대 4회 반복한다. Control Page의
  memory edit와 provenance/delete preview·apply는 same-worker outer writer 아래에서
  pre-entry busy만 최대 2초·50ms 간격으로 기다린다. operation·guard exit·result-shaped
  busy는 재실행하지 않고, deadline 뒤 늦은 admission과 admission 전 취소는 operation과
  token 소비 0회로 닫는다. admission 뒤 취소는 operation 1회를 끝내고 worker 오류를
  보존한다. 2초를 넘긴 경합은 exact no-store 503이며 live UI 전이는 아직 미검증이다.
- Windows 교차-process reader-reader/reader-writer, async owner 수명, 재진입·upgrade,
  동시 response 소비와 8798·8799/API/UI privacy projection을 synthetic data로 검증했다.
  paused Sub-LLM 중 reader 공존·삭제 Busy, shared→writer handoff의 source 삭제와 target
  user-edit 우선, stale model canary 전 파일 비저장도 실제 thread race로 검증했다.
- memory 292개(skip 1), core 736개(skip 1), runtime 771개(skip 4)와 CI-equivalent
  전체 3,123개(skip 21)가 통과했다. 실제 사용자 기억과 live Discord·마이크·Minecraft·
  Docker 서비스는 사용하거나 변경하지 않았다.

## 2026-08-08 Minecraft local planner와 Codex 실행 경계

- 기본 Mindcraft profile의 conversation·code model은 local
  `Qwen3-14B-Q4_K_M.gguf`, embedding은 local hash다. 전략 subgoal, router,
  recovery와 utility도 `MINDCRAFT_CODEX_ENABLED=false`일 때 local 경로를 유지한다.
- 기본 `voyager` Compose profile과 launcher에서 Codex dependency, token mount와
  credential preflight를 제거했다. legacy Voyager runner도 backend 기본값 `local`을
  따르며 Python client는 HTTP health나 spawn 전에 반환한다.
- Mindcraft Codex adapter는 enable gate를 token read·fetch보다 먼저 검사하고,
  classifier 실패는 exact `ignore`로 닫는다. 2026-08-09 history 경계가 free-text
  summary와 raw archive 자체를 제거한 현재 상태는 다음 절에 기록한다.
- 선택적 `codex-gateway` profile만 `auth.json` 단일 read-only mount와 빈 `/workspace`를
  사용한다. custom shell·host-native gateway는 제거했다. pinned image에서 tool registry와
  secret canary가 검증되기 전에는 `toolAccessVerified=false`, `backendReady=false`, action
  503, subprocess 0을 유지한다. HTTP 200만으로 Runtime Health ready가 되지 않는다.
- 이는 source·합성 회귀 증거다. 이번 변경에서 Docker image를 rebuild하거나 Minecraft,
  Codex CLI, 계정 로그인과 실제 world action을 실행하지 않았다.
- 관련 집중 157개(skip 10), 별도 local-backend zero-call 회귀, CI-equivalent 전체
  3,136개(skip 22), JavaScript·PowerShell·JSON 구문과 overlay patch 적용 검사가
  통과했다. Node 행동 suite는 다음 Mindcraft image build의 blocking gate로 연결했지만
  이번에는 image build를 실행하지 않았다.

## 2026-08-09 Mindcraft ephemeral history, broker와 process-local sink 경계

- 기본 history는 `mindcraft.history.ephemeral.v1` bounded turn과 process-local monotonic
  generation만 유지한다. `save()`는 content-free checkpoint 상태만 반환하고 디스크에
  대화나 summary를 쓰지 않는다. `load_memory=false`이며 Compose의
  `bot_memory/mindcraft` mount도 제거됐다.
- 기존 `memory.json`, `histories/*.json`과 legacy log는 현재 runtime이 읽거나 새 generation으로
  rebase하거나 삭제하지 않는다. 따라서 legacy byte cleanup·이관이나 restart restore를
  구현한 것으로 간주하지 않는다.
- `History.getHistory()`는 generation snapshot을 붙인다. outer exposure는
  `handleMessage`의 첫 await 전부터 LLM, command, history save와 실제 awaited
  route/action sink가 끝날 때까지 유지된다. inter-agent pause/classifier도 exposure 안에서
  끝내고 예약 timer/queue는 clear에서 폐기한다. 중간 generation 변경은 stale 결과로
  버리고, exposure 중 `!clearChat`은 `mindcraft_history_busy`로 닫힌다.
- 성공한 clear는 turns·summary placeholder·`last_sender`, planner recovery/action-mode와
  self-prompt, goal-manager의 recent execution/gate/observation 상태를 함께 초기화한 뒤
  process-local generation을 올린다. goal state/status는 raw command·result·gate reason과
  observation argument를 저장하지 않고 enum code·count/boolean만 남긴다. runtime status의
  blocked chat/error도 fixed code이며 legacy raw projection은 재사용하지 않는다.
- planner recovery persistence 함수는 no-op이고 Compose도 planner state path를 제공하지
  않는다. recovery step은 exact history snapshot을 key로 한 process-local one-shot issuance만
  소비한다. 다른 target, 수동 명령, 다른 concurrent turn과 재사용된 receipt는 plan을 진행하지
  못하며 token과 raw command는 durable goal/status projection에 남지 않는다.
- Google translation 대신 local identity translator를 사용해 Minecraft chat을 제3자
  번역 서비스로 보내지 않는다. Python owner는 Node child stdout/stderr를 `DEVNULL`로
  연결한다. 이는 새 child 원문 log 생성을 막지만 기존 data/log를 삭제했다는 뜻은 아니다.
- Python status owner와 Node writer는 legacy/free-text 상태를 재사용하지 않고 exact
  content-free projection만 공개·기록한다.
- Node planner의 local/router/subgoal/recovery 요청은 caller가 URL·model을 고를 수 없는
  authenticated Bot API broker 하나만 사용한다. 전용 token file을 쓰고 direct model
  endpoint와 Codex fallback은 기본 service에서 제거했다. 현재 ephemeral turn은 core memory를
  입력으로 받지 않으므로 broker request projection의 각 row에 strict `not_used` receipt를
  붙이고, 반환 receipt도 `not_used`가 아니면 거부한다.
- broker는 공용 conversation filter와 `memory_exposure_request`를 재사용해 fixed upstream을
  호출하고, 첫 NDJSON result를 보낸 뒤 Node consumer가 exact `delivered|discarded` ACK를
  완료할 때까지 lease를 유지한다. 이 ACK는 frame parse/validation 증거이지 history append,
  chat route나 world action 성공 증거는 아니다. process-local generation fence는 이와 별도로
  실제 awaited route/action sink까지 유지된다. replay window는 active lease ID를 보존하면서
  오래된 완료 ID만 축출한다.
- 검증은 continuity 87개, Mindcraft/broker/Compose 관련 56개, voice 633개(skip 5),
  CI-equivalent 전체 3,190개(skip 22)와 Python·JavaScript 구문을 통과했다.
- 이 증분은 source-level 경계다. Docker image build, image 내부 full Node suite, 실제
  Minecraft login·action과 live clear는 수행하지 않았다. durable rollback-safe history와
  bound-receipt restart restore는 없고 legacy data/log cleanup은 사용자 승인 migration으로만
  수행한다. recovery issuance correlation은 process-local이며 restart restore 계약은 아니다.
  `!clearChat`은 대화 유래 상태 reset이며 자율 목표의 영구 정지는 `!endGoal`을 사용한다.

## 2026-08-09 비-Minecraft 음성·Runtime Health 경계

- voice validation GET은 consent lock 안에서 session을 한 번만 읽고 terminal/expired
  session의 capture를 정리한 뒤 현재 consent capability를 공개 사본에 붙인다. 성공과
  cleanup 실패 응답이 철회 전 `active=true`를 재사용하지 않는다.
- Runtime Health의 모든 probe는 manifest `timeout_ms`로 제한된다. 멈춘 runner는
  content-free `timeout` 결과로 끝나며 required service readiness를 얻지 못한다.
- Discord voice reconnect wait가 client를 반환하지 못하거나 같은 채널 client가 이미
  disconnected면 stale client를 강제 정리하고 기존 connect 경로로 새 client를 만든다.
  stale 정리부터 replacement 생성·재사용까지 guild connect lock 안에서 직렬화하고,
  disconnect 자체가 실패하면 현재 stale 객체에 한해 Discord registry cleanup을 수행한다.
  disconnect 도중 replacement가 설치된 경우에도 current client를 다시 확인해 끊지 않고
  재사용한다. 실제 connected same-channel client만 listener rearm·warmup·last-channel
  저장 성공 경계를 통과한다.
- 전달 시도 전인 Discord voice search follow-up은 시작 시 연결이 없으면 claim만
  해제하고 `delivery_ready`를 유지한다. connected client 재무장 직후 recovery를 다시
  실행하며, `delivery_attempted` 뒤의 모호한 실패는 자동 재생하지 않는다.
- Discord playback은 prior source와 `after` callback을 기존 `OMNIVOICE_TIMEOUT_SEC`
  (기본 180초)로 제한한다. timeout은 같은 source가 current일 때만 voice client를 정지하고
  기존 failure 경로로 전파되어 room lock과 user-only continuity를 정확히 한 번 정리한다.
- Local Bridge는 validation 발화를 STT 서비스에 보낼 때 content-free
  `validation_bound=true`만 전달한다. session/attempt ID는 보내지 않으며 STT 완료
  로그는 transcript 대신 길이 marker를 기록한다. 일반 발화는 `false`를 명시한다.
- 변경 파일 집중 80개와 voice 전체 610개(skip 5)가 통과했다. 실행 중 image를
  교체하거나 마이크·Discord를
  시작하지 않았다. 읽기 전용 preflight에서 active validation session 없음, mic physical
  OFF, 24 kHz mono int16 output ready, OmniVoice warmup 완료와 오류 0을 확인했다.
- search follow-up 22개와 Discord I/O 전체 125개도 통과했다.
- 최신 offline 경계 검증은 memory 전체 293개(skip 1), voice 전체 615개(skip 5),
  deletion HTTP/middleware 35개와 Python compile·diff check를 통과했다.
- TTS 재생이 필요한 `local_bridge` JSON/stream reply는 HTTP EOF에서 assistant history,
  continuity와 background action을 확정하지 않는다. authenticated status의 exact
  bridge instance·turn·assistant hash `played` ACK 뒤 server-side currentness를 다시 확인하고
  한 번만 확정한다. ACK는 HTTP 전송에만 붙고 signed status artifact에는 저장하지 않는다.
- wrong/stale/duplicate ACK는 side effect를 반복하지 않는다. `failed|partial|cancelled`,
  error reply, bridge rotation·동시 재시작과 TTL orphan은 exact ingress/action을 정리하고
  자동 재생 없이 다음 turn을 연다. 이는 software playback completion 경계이며 사용자가
  실제로 들었다는 live 증거는 아니다. 최신 continuity 87개와 voice 633개(skip 5)가 통과했다.

## 2026-08-09 Memory applied-cleanup 경계

- memory edit, provenance backfill과 correction/undo가 파일 commit 뒤 후처리에서 실패한
  503을 일반 `api_error`로 버리지 않는다. 세 fixed cleanup code만 공개 allowlist에
  보존하고 응답의 다른 필드는 계속 폐기한다.
- edit/backfill/manual/correction/undo는 적용된 cleanup code에서 4개 memory snapshot을
  무효화하고 강제 재조회한 뒤 적용 사실과 자동 재시도 금지를 표시한다. busy와
  integrity 503은 적용 성공으로 오인하지 않는다.
- 삭제 tombstone과 source redaction/unlink 뒤 index 또는 hot-context 정리가
  integrity-class 오류를 내면, deletion ledger의 최종 재검증이 통과한 경우만
  `memory_delete_cleanup_required`, `tombstoned=true`를 보존한다. ledger 자체가
  손상됐으면 기존 exact/content-free integrity 503으로 fail-closed한다.
- UI 전체 178개와 diff check가 통과했다. 실행 중 Control Page image는 교체하지 않았다.
