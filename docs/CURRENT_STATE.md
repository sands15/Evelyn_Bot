# Evelyn Current State

Document status: **Current**
Last reviewed: 2026-08-24 KST
Source branch: `codex/omnivoice-tts-cutover`, bounded LLM task-loop increment

이 문서는 현재 확인된 사실만 기록한다. 목표 구조와 과거 계획은 다른 설계/계획 문서를 사용한다.

## 2026-08-20~21 bounded LLM task loop

- `/task|/작업 <목표>`는 Router0 fast policy에서 `task_executor` registered skill로 들어가며 Core
  text/voice는 검증된 completed task evidence를 도구별 deterministic typed finalizer로 표시하고 noncompleted는 fixed typed
  outcome으로 끝낸다. read-only/search와 verified workspace mutation 모두 Main 자유문을 terminal claim 권한으로 쓰지
  않는다. Fast Control은 같은 loop를 background FastAction으로 실행하고
  `/작업취소 <task-id>`로 exact task를 취소한다.
- 실제 route가 `task_executor`이면 router preface·일반 short-circuit와 specialist 실행을 모두 건너뛰고 registered
  task skill만 호출한다. completed 주장은 exact top-level/observation schema, step/model count와 마지막
  verified-success evidence를 통과해야 하며, 결과가 `None`·빈 문자열·사용자 입력 echo이거나 이 계약이 맞지 않으면
  fixed `TASK_LOOP_INVALID_RESULT`로 닫고 Main을 호출하지 않는다. 유효한 completed는 tool별 exact evidence key/type/count,
  생산자와 같은 size·truncation 계약 및 goal-bound args를 다시 확인한 뒤에만 fixed bounded outcome을 반환한다.
  verified mutation은 적용 receipt와 동일 path/SHA post-read가 맞을 때만 완료한다. preface나
  `needs_main_llm:false`, worker summary, 대화 history가 finalizer를 대신하지 못한다.
- 기본 task는 최대 6 task-step·120초다. 해석·도구 선택이 필요한 단계는 Qwen worker가 typed tool JSON을
  하나씩 제안한다. closed exact `workspace_read`의 initial path, receipt가 유일하게 결박한 same-SHA
  continuation과 post-apply verification read는 runtime이 worker 호출 없이 typed decision으로 실행한다.
  이 transition도 같은 step budget, TurnScope cancellation, grant와 signed Host receipt 검증을 통과한다.
  worker/tool await는 각각 6초/20초이며 executed-but-unverified는 `uncertain` terminal이라 자동 재시도하지 않는다.
- worker admission 직전과 worker/runtime-bound decision 반환 직후, `workspace_test`, edit stage와 automatic
  tool executor 진입 직전에 wall grant expiry와 monotonic task budget을 다시 검사한다. 만료 decision은
  executor 0회로 typed terminal에 닫힌다. pending exact stage의 bounded cancel은 권한 축소 cleanup이므로
  deadline 뒤에도 실행한다.
- Bot API의 authenticated Mindcraft broker가 task worker, 일반 specialist와 Mindcraft의 모든 production Qwen
  요청을 capacity-one FIFO로 소유한다. active 1개와 최대 3개 waiter를 허용하고 queue deadline은 enqueue부터,
  kind별 inference deadline은 실제 slot grant부터 계산한다. queued disconnect는 POST 전에 빠지고, in-flight caller가
  취소·timeout되어도 upstream EOF까지 slot을 유지해 late result를 다른 request에 전달하지 않는다. grant 직전에도
  deadline·disconnect·abandon·poison을 다시 검사한다. Router는 별도 모델이라 이 owner 밖에 남는다.
- transport timeout·ClientError나 EOF를 확인하지 못한 응답은 owner를 poison하고 Qwen epoch가 바뀌기 전에는
  복구하지 않는다. POST 직전 durable marker에 Qwen boot epoch를 기록하고 bounded full EOF에서는 HTTP/JSON/content
  semantic 성공 여부와 무관하게 지운다. full EOF 뒤 semantic 실패는 그 invocation만 실패시킨다. Compose는 explicit Bot restart/update에
  Qwen restart를 결합하며, crash 뒤 stale marker는 새 epoch와 content-free Qwen health가 모두 확인되어야 제거된다.
  broker health도 mounted epoch의 missing/corrupt 상태와 active marker epoch 불일치를 upstream probe 전에 503으로
  닫고 probe 전후 epoch·owner를 재확인한다. Qwen boot command는 epoch 기록 실패 시 model server를 시작하지 않는다.
  graceful shutdown은 새 admission을 먼저 닫고 최대 120초 자연 drain하며 container/launcher stop budget은 130초다.
- Python/Node client는 request ID와 exact memory receipt, bounded frame/trailing/ACK body를 검사하고 consumer parse 뒤에만
  `delivered` ACK한다. Qwen과 Router 양쪽의 server-side final memory guard가 실패하면 ACK도 503으로 닫힌다. 이 admission·cancel·restart
  경계는 offline deterministic test로 확인했지만 실제 GPU 동시 부하와 crash-recovery는 아직 live 검증하지 않았다.
- goal completion은 LLM의 문장이나 `success_criteria`만 믿지 않는다. read-only 완료는 closed single-operation
  grammar와 exact path/query/scope/recursive args 및 latest model-visible typed receipt를 요구하고,
  list/search/diff는 `truncated:false`여야 한다. `workspace_read`는 UTF-8 byte offset 0부터 EOF까지 같은 path,
  full-file SHA와 byte count가 이어지는 연속 청크만 허용하고, 보이는 content를 다시 합친 SHA까지 일치해야
  완료한다. 내부 공백을 포함한 path/query/quoted literal은 원문 그대로 결박한다. exact quoted-content edit만
  목표에 적힌 path·old/new literal과 적용 뒤 같은 경로 candidate SHA read가 모두 맞을 때 완료한다.
  noncompleted Core/Fast 결과는 Main이나 worker 자유문장으로 성공 승격하지 않고 fixed typed outcome으로 전달한다.
- completed runtime/read/list/search/diff/web evidence와 일반 검색 card는 deterministic renderer가 canonical JSON UTF-8의
  bounded hex prefix, 원본·preview byte 수와 truncation 상태로 표시한다. 이 evidence는 display sanitizer가 바꾸거나
  OmniVoice/model tag로 해석할 수 없고, spoken text는 evidence 없는 고정 요약으로 분리한다. 검색 실패·0건·결과 있음은
  Voice JSON/SSE에서 서로 다른 상태로 전달하며, search-capable SSE는 typed resolution 전 모델 delta를 TTS로 보내지 않는다.
  semantic `review|summarize|explain|compare`는 raw read/diff/search receipt만으로 완료하지 않는다.
- 자동 권한은 authenticated Control Page의 allowed source tree list/search/read/exact-file diff,
  runtime status, goal에서 exact query를 추출할 수 있는 closed single-operation public web search에 한정된다.
  모델이 다른 query를 만들거나 compound/negated command이면 provider 호출 전에 차단한다. private/runtime roots, symlink/junction/reparse,
  broad diff와 cross-tool workspace→web egress는 차단한다. 다른 surface의 workspace access와 모든
  edit/test는 coarse grant로 자동 실행하지 않는다. `workspace_edit`는 Control Page exact approval로만 적용되고,
  `workspace_test`는 runtime이 만든 behavioral candidate와 Host의 exact stage binding이 있을 때만 격리 실행된다.
- Host Supervisor task queue protocol v2는 `LOCAL_BRIDGE_STATUS_AUTH_TOKEN`을 직렬화하지 않고 request/response
  domain-separated HMAC에만 사용한다. 현재 boot instance, task/grant/action/step/surface/tool/args hash,
  도구별 bounded TTL과 one-shot replay state를 검증한다. 이 queue는 bounded read-only 요청과 signed edit
  staging·candidate test를 처리하고, 실제 apply는 별도 Control Page↔Host mutation HMAC queue만 사용한다.
  read는 한 번에 최대 2KiB를 요청하되 evidence 자체와 JSON-string transport가 각각 3,999자를 넘지 않도록
  UTF-8 경계에서 더 줄인다. 다음 요청은 runtime이 exact path/offset/2KiB/expected full SHA로 강제한다.
  기본 6 task-step 예산에서는 deterministic terminal transition을 남겨야 하므로 최대 5개 read 청크,
  escaping이 적을 때 약 10KiB까지
  전체 읽기를 완료할 수 있고 그보다 길면 성공 대신 bounded terminal로 끝난다. v1 Host와는 protocol mismatch로
  fail-closed하므로 이 변경을 반영하려면 Host 재시작이 필요하다.
  list/search/diff는 capacity-one worker에서 실행해 Host supervision loop를 막지 않으며, skipped/oversized/unreadable
  search와 untracked/불완전 diff는 `truncated:true`라 완료 권한이 없다. diff의 complete 기준은 tracked path의
  `HEAD→worktree`다.
- 단일 UTF-8 file create/replace는 별도 Host mutation HMAC과 generation 아래 먼저 stage된다. task manager는
  한 pending/한 mutation attempt만 허용하고 full diff, base/candidate SHA-256, preview digest, exact git status,
  target identity와 dirty-base 확인을 30초 single-use token에 묶는다. Control Page claim→Host apply→Bot complete가
  끝나면 기다리던 같은 coroutine이 receipt를 받아 재개한다. exact quoted-content edit만 뒤의 same-path read SHA가
  candidate와 같을 때 goal을 완료한다. behavioral edit는 적용과 SHA 재확인 뒤에도 즉시
  `uncertain/workspace_behavior_outcome_unverified`로 끝나며 자동 재시도나 해결 완료 주장이 없다. approval cancel은
  manager prepare→exact Host stage cancel→manager complete의 2단계 결과 확인 뒤에만 waiter를 `cancelled`로 깨운다.
  Host 결과의 approval/stage/instance binding이 맞지 않으면 `uncertain`이다. direct `/작업취소`는 exact asyncio task
  intent가 있을 때만 FastAction을 `cancelled`로 기록하며, 서버성 cancellation은 `failed/background_action_cancelled`와
  recovery-required를 유지한다. approved apply 뒤 `resuming` effect barrier는 same-path read와 terminal 기록이 끝날 때까지
  exact task에 남고 background terminal cleanup만 해제한다. apply/cancel 결과를 이미 확인 중인 task는 raw coroutine
  cancel로 경쟁시키지 않는다.
- worker가 mode와 일치하는 단일 `create|replace` envelope를 반환하면 그 exact inner args만 풀고,
  `{path, content}`는 기존 파일을 덮어쓰지 못하는 create args로만 정규화한다. 이 외 malformed edit args는
  sandbox-required behavioral edit로 분류하지 않고 typed `task_worker_workspace_edit_args_invalid` observation으로
  worker에 돌려주며 Host tool은 호출하지 않는다.
- behavioral candidate test는 frozen Git-tracked manifest와 exact candidate overlay만 project-scoped snapshot에
  복사해 fixed Bot API image에서 `python_unittest` runner로 실행한다. container는 network/IPC none, read-only,
  uid 65534, cap-drop/no-new-privileges, bounded PID/memory/CPU/tmpfs/log/time을 사용하고 Host capacity-one worker가
  main supervision loop 밖에서 소유한다. 실패한 target은 revised candidate에서도 모두 다시 실행해야 한다.
- arbitrary candidate가 같은 Python interpreter의 종료 코드를 흉내낼 수 있으므로 test pass/fail은
  `semanticVerified:false`인 후보 선택·승인 보조 관측일 뿐 behavioral completion 권한이 아니다. full diff 승인 UI도
  이 한계를 표시한다. active harness/authority/evaluator와 sandbox runner 자체는 task edit에서 차단한다.
- Host는 fixed image inspect+isolation canary, signed sandbox request/result, exact stage/tree digest와 apply 직전 base/candidate
  tree 재검사를 요구한다. container create 응답 유실, response write 실패, cancel/test race와 hard crash는 exact container
  cleanup 및 project-owned snapshot marker-last purge로 fail-closed한다. literal/behavioral stage 모두 원 action binding으로
  폐기한다.
- pre-consume literal apply가 verified failure를 반환해도 task loop는 exact pending stage를 Host에서 폐기하기 전
  pending binding을 지우지 않는다. behavioral apply의 raw `success|succeeded` alias는 모두 exact path/SHA와
  `semanticVerified:false` fence를 통과해야 한다. apply completion claim은 유효한 complete 뒤에만 제거하므로 잘못된
  late complete가 진행 중인 cancel claim을 없애지 못한다.
- Windows workspace read/stage/apply는 project root부터 target parent까지 directory handle을 no-reparse로 열어
  final path와 directory identity를 확인하고 작업 종료까지 유지한다. 이 pin이 열리지 않거나 ancestor가 바뀌면
  fail-closed하며, 작업 중 ancestor rename/write/delete/reparse 교체를 허용하지 않는다. replace는 non-default
  NTFS stream을 fail-closed하고 conditional exchange가 밀어낸 base의 identity와 SHA를 검증한다. 외부 저장이
  끼거나 cleanup이 불확실하면 보존한 backup과 `workspace_edit_recovery_required`를 남기고 성공을 반환하지 않는다.
  public state에는 locator만, preview에는 strict allowlist만 내보낸다.
- detached clean source snapshot의 공식 lightweight launcher로 source-gated Bot API·Control Page와 Windows
  Host Supervisor를 기동했다. Browser exact long-read는 3개 same-SHA 청크와 task-worker model call 0회로 completed했고,
  exact create는 full diff/base `ABSENT`/candidate SHA 확인→30초 one-use approval→Host apply→same-path read→Main
  응답을 완료했다. 적용 파일 SHA는 preview candidate SHA와 일치했다. Host generation 회전이 남긴
  `stop.request` 때문에 replacement가 즉시 종료되는 live 실패도 재현해 old process 종료 확인 뒤 marker를
  제거하도록 수정하고 공식 launcher success를 다시 확인했다. behavioral candidate-test/apply, 만료·dirty-save·
  중간 변경과 Qwen burst/epoch recovery의 live E2E, Discord/voice E2E는 아직 미검증이다.
- task goal은 outer whitespace만 제외하고 4,000자까지 그대로 보존한다. 4,001자 이상은 tail을 잘라 다른
  목표로 재결박하지 않고 parser, direct loop와 restored worker state에서 모두 `task_goal_empty`로 fail-closed한다.
  approval claim 뒤 minimum deadline이 지난 late Host completion은 candidate SHA가 맞아도
  `uncertain/outcome_unverified`로 waiter를 깨우며 `approved/resuming`으로 승격하지 않는다.

## 2026-08-22 async effect boundary hardening

- conversation ingress의 restart recovery와 same-process phase transition은 wall clock rollback에도
  `createdAt|updatedAt <= logical time <= expiresAt`을 유지한다. continuity restore는 저장된
  `expiresAt`과 원래 발급 창을 hard cap으로 사용해 더 긴 reader 설정이 checkpoint나 row를 연장하지 못한다.
- process-local autonomy grant와 Minecraft world lease는 wall·monotonic deadline이 모두 살아 있어야 한다.
  공개 world proof/status 형식은 유지하고 private same-host secret에 exact lease ID·monotonic 만료를 결박했으며,
  executor connect와 world enable await 뒤 commit 직전 만료는 disconnect·lease revoke·verified stop으로 닫는다.
- Discord playback started/completed/qualified receipt는 exact current source의 0이 아닌 첫 frame이 실제
  packet send에 성공한 뒤에만 발급된다. source read, `play()`/after callback, 무음·unread source, UDP send
  실패는 성공이 아니다. inherited base connect가 끝난 뒤 custom setup 중 취소돼도 base disconnect와
  exact-self cleanup을 drain하고 replacement registry와 최초 cancellation을 보존한다.
- 최신 canonical `python -m pytest -q`는 4,106 passed, 22 skipped, 1 warning, subtests 1,142로 offline
  통과했다. production Python compile과 scoped whitespace 검사도 통과했다. warning은 기존 `audioop`
  deprecation이며, live Discord·마이크·스피커·Minecraft·Docker·GPU·STT 서비스는 실행하지 않았다.

## 2026-08-22 owner/currentness follow-up

- Fast Control checkpoint와 ingress scope는 configured guild/user의 opaque SHA-256 principal key를 사용한다.
  cross-surface reader는 그 exact key만 선택하며 legacy fixed key와 다른 principal artifact는 fail-closed한다.
  checkpoint·status에는 raw guild/user가 없다.
- verified empty head의 `updatedAt`은 hashed `resetBoundaryAt`으로 다음 active checkpoint까지 전달된다. 따라서
  첫 post-reset local turn 뒤에도 boundary 이전 cross 문맥은 부활하지 않고, boundary 이후 같은 principal의
  새 cross activity는 정상 병합된다.
- required speaker verification은 exact boolean `matched=True`만 TTS interrupt를 허용하고, 정책 자체가
  적용되지 않은 `status=skipped`만 예외다. Local Bridge는 status 게시 await 뒤 no-key batch STT 직전에도
  captured admission epoch를 재검사해 restart/shutdown/mic invalidation 뒤 PCM을 모델에 보내지 않는다.
- Local mic OFF는 physical `applied + captureStopped` ACK만으로 성공하지 않는다. 현재 local input lease의
  durable release receipt까지 정확해야 하며 실패·손상 receipt는 고정 503 `voice_input_lease_unavailable`이다.
- Python 3.12에서 streaming playback 테스트 fake가 event loop를 동기 점유하던 교착은 실제 Discord
  AudioPlayer처럼 worker thread가 source를 소비하도록 교정했다. production queue 의미는 바꾸지 않았고 audible·
  zero PCM 모두 실제 packet receipt 경계를 통과한다.

## 2026-08-22 harness evidence/currentness follow-up

- Main/Fast의 required tool evidence는 exact `executed`만 통과한다. `planned`, `needs_local_tool`,
  `executed_empty`, `executed_withheld`, `failed`, `failed_or_unavailable` 등은 고정 답변으로 terminalize하고 모델을
  호출하지 않는다. 선택 evidence의 degraded 동작은 유지하며 web과 task는 등록된 executor가 다음 단계를 소유한다.
- runtime status cache는 현재 시각보다 미래인 `cached_at`을 fresh로 인정하지 않는다. Local voice consent apply는
  physical mic ON 뒤 health 재확인을 10초로 제한하고, timeout/실패 시 기존 recovery가 mic OFF와 503을 확정한다.
- task goal은 허용된 4,000자 전체가 worker state까지 보존되고 grant는 `now >= expiresAt`에서 실행 전 만료된다.
  FastAction coordinator는 running task를 history cap 때문에 제거하지 않으며, runner가 cancel을 삼킨 뒤 반환하면
  성공/취소가 아니라 `failed/background_action_cancel_outcome_unverified`로 닫아 자동 재시도를 금지한다.
- autonomy outcome audit callback은 exact `True` 또는 `recorded:true`만 성공이다. `None`이나 예외는 grant를 폐기하고
  engine을 중단하며 cursor를 유지한다. Minecraft disconnect/cancel 뒤 늦은 원 action 결과는 stale이고, 검증 실패로
  inflight binding이 남은 executor는 exact cleanup 전 reconnect할 수 없다.
- batch memory writebehind 교체 시 취소를 무시하고 살아 있는 predecessor는 `memory-drain` alias로 실제 종료까지
  registry에 남아 guild reset을 `memory_background_work_inflight`로 막는다.
- 합친 핵심 회귀는 316 passed, subtests 223, canonical 전체는 4,106 passed, 22 skipped, 1 warning,
  subtests 1,142였다. 변경 production 11개 compile과 scoped `git diff --check`도 통과했다. 실제 service/device는
  실행하지 않았다.

## 2026-08-22 holistic harness acceptance seam

- `VoiceTurnOrchestrator`는 route context, short-circuit, registered route와 Main await가 반환된 직후 exact
  `TurnScope`를 다시 검사한다. adapter가 cancel intent를 삼키거나 owner가 교체돼도 stale answer/evidence를
  다음 route 또는 completion에 넘기지 않는다. 이미 실제 delivery effect를 소유한 await는 기존 delivery
  finalizer 의미를 유지한다.
- behavioral workspace candidate 뒤 worker가 wrong tool, malformed `workspace_test.targets`, 누락된 bound
  targets 또는 exact invalid JSON을 내면 tool을 실행하지 않고 typed observation을 다음 worker state에 넣는다.
  candidate는 bounded budget 동안 유지하며 exhaustion에서 exact stage를 한 번 폐기한다. arbitrary worker
  `ValueError`, transport와 timeout은 기존 terminal 의미를 유지한다.
- Discord autonomy follow-up은 send 정상 반환 뒤 cancellation이 와도 continuity finalizer를 drain한다.
  engine은 verified outcome audit, cursor와 state를 persist한 다음 원래 cancellation을 재전파하므로 같은
  follow-up을 restart 뒤 다시 보내지 않는다. durable `[autonomy]` pair는 그 이전 search promise만 해소하고,
  이후 새 promise·partial/unattributed pair는 pending으로 남는다.
- autonomy cycle/executor의 사용자 가시 오류도 current canonical Discord text session과 reply slot을 사용해
  `[autonomy:error]` + assistant receipt를 history/continuity에 한 번 남긴다. 이 내부 marker는 recent user나
  search completion으로 사용하지 않으며 send/commit 실패가 재귀 오류 알림을 만들지 않는다.
- 테스트가 설치된 real `numpy|aiohttp`를 import하기 전에 불완전 fake를 `sys.modules`에 남기던 10개 경계를
  real import 우선/fallback-only로 바꿨다. 기존 50 ms wall timing test의 flake도 단독 반복 RED 뒤 의미를
  유지한 500 ms outer budget으로 안정화했다.
- 통합 broad는 1,074 passed, 5 skipped, subtests 357, canonical 전체는 4,120 passed, 22 skipped,
  1 warning, subtests 1,146이었다. production/test compile과 scoped whitespace 검사가 통과했고 실제 Discord,
  마이크·스피커, Minecraft, Docker/GPU/STT는 실행하지 않았다.

## 2026-08-22 continuity commit late-result ownership

- 취소된 오래된 async commit worker가 같은 session의 최신 성공 뒤 실패하면 디스크에는 최신 turn이 남지만
  process state와 `completedTurnCommit.last*`를 `error/false`로 덮던 race를 deterministic RED로 재현했다.
- public sync/async entry가 worker 실행 전에 내부 epoch를 예약한다. 같은 session의 더 최신 **성공**만 stale
  attempt를 supersede하며, 다른 session의 성공이나 더 최신 실패는 기존 유효 commit을 막지 않는다.
- stale attempt는 현재 checkpoint/head를 anchor한 뒤 rollback, keyed authenticity와 external replay 구조를
  검증한다. callback-free exact target은 durable receipt로 coalesce할 수 있지만 stale `before_commit` callback은
  실행하지 않는다. 정상 superseded 실패는 누적 failure만 남기고 최신 `last*` health를 보존하며 실제
  corruption/auth failure는 계속 `error`다.
- race 6개와 실제 continuity/autonomy/Control Page/Discord 호출 경로 148개, subtests 21개가 통과했다.
  canonical 전체는 4,126 passed, 22 skipped, 1 warning, subtests 1,146이었다. `py_compile`과 scoped
  `git diff --check`도 통과했다. 이 검증 시점에는 live filesystem stall이나 외부 서비스를 실행하지 않았고 bare
  `to_thread` writer의 영구 정지가 남아 있었다. 이 제한은 아래 completed-turn artifact process 구현으로
  해소됐으며, 명령 기반 terminal lifecycle은 별도 경계로 분리했다.

## 2026-08-22 command terminal lifecycle bounded recovery

- restart, bot shutdown, stack/local scheduled shutdown은 한 terminal owner를 공유하고 요청 시 동기 claim한다.
  Discord terminal 확인문은 전송 성공 뒤 command continuity 기록 전에 owner를 arm하며 full-stack helper도
  확인문 전달 뒤 실행한다. Timer thread 생성 뒤 `start()`가 실패한 경우도 해당 timer를 취소하고 claim을 rollback한다.
- 기본 20초 hard watchdog과 1초 앞선 restart soft launcher는 non-daemon이다. flush, cleanup, scheduler,
  launcher나 logger가 멈춰도 launcher와 exit는 각각 exact once이며 event loop 종료가 watchdog을 버리지 못한다.
  host fallback은 `main.py`와 현재 mode env를 사용하고 batch를 Python으로 실행하지 않는다.
- Docker `discord_bot`은 Windows launcher를 건너뛰고 composition 생성 뒤 최소 10초 admission을 cancellation으로
  우회하지 않은 다음 exit 75를 낸다. Compose는 이 서비스에만 `on-failure:3`을 적용한다. shutdown은 exit 0이다.
- 이 fail-stop 자체는 flush 성공이나 writer 중단 증거가 아니다. 당시 일반
  `commit_completed_turn_async()`의 bare `to_thread` 영구 정지는 남아 있었고, 다음 절의 artifact process가
  후속으로 해소했다. 이 절에서는 source/offline 회귀만 수행했으며 실제 Docker restart, Discord,
  unreleased filesystem flush는 실행하지 않았다.
- 최종 집중 회귀는 167 passed와 subtests 33, runtime 전체는 1,107 passed, 4 skipped와 subtests 417이었다.
  canonical 전체는 4,159 passed, 22 skipped, 1 warning과 subtests 1,146이었다. warning은 기존 `audioop`
  deprecation이며 production/test `py_compile`과 scoped `git diff --check`도 통과했다.

## 2026-08-22 completed-turn artifact process와 journal-only recovery

- Main/Fast Control의 completed-turn checkpoint, continuity authenticity, Discord/Fast ingress와 FastAction의
  production 기본 artifact I/O는 `durable_artifact_process.py`의 shared warm child로 실행된다. parent는
  request deadline에서 멈춘 child를 terminate/kill/wait하며, reap 전에는 replacement를 시작하지 않는다.
  이후 살아 있는 current worker 또는 replacement worker로 exact temporary cleanup과 disk-first reconciliation을
  수행한다. parent hard-exit, warm PID 재사용, pre/post-replace stall, bounded read retry, credential 환경
  격리와 relative-path 거부를 결정론적으로 검증했다.
- Discord `delivery_succeeded` restart는 journal terminal marker를 checkpoint보다 먼저 쓰며, marker-only
  crash는 exact active/empty/fresh predecessor에서 한 번만 recommit한다. checkpoint 뒤 journal completion
  crash는 generation을 더 올리지 않는다. 같은 turn ID의 conflicting tail, 잘못된 receipt generation과
  keyed fresh missing-head는 fail-closed하고 speculative store를 되돌린다. Main은 ingress reconciliation 뒤
  truly pristine state만 empty bootstrap한다.
- write outcome이 불명확해진 ingress/FastAction journal은 다음 mutation 전에 journal/head/external anchor를
  authoritative reload한다. transient read/write failure는 재시도할 수 있지만 validated hash/schema/auth
  conflict는 닫힌다. completed-turn은 content-free in-flight count/stall age/deadline status 게시를 먼저
  best-effort로 시도하고, Runtime Errors와 public Runtime Health는 마지막으로 성공한 status의 허용 필드만
  투영한다.
- 관련 통합 묶음은 439 passed, 1 skipped, subtests 57, journal/Discord 묶음은 115 passed, subtests 3이었다.
  canonical `python -m pytest -q`는 4,188 passed, 22 skipped, 1 warning, subtests 1,149로 172.82초에
  통과했다. warning은 기존 `audioop` deprecation이다. source compile도 통과했다. 실제 Discord,
  filesystem/antivirus, Docker, microphone, Minecraft, GPU/STT 서비스는 실행하지 않았다.

## 2026-08-23 task/voice boundary verification

- swallowed worker cancellation, raw external `Task.cancel()`, remaining-zero와 authorize 뒤 grant expiry는
  tool executor 진입 0회로 닫혔다. workspace stage/test executor가 cancellation을 삼켜 failed·invalid
  receipt를 반환해도 정규화 전에 cancellation을 재전파하며, stage 반환 뒤 deadline이 끝나면 approval에
  진입하지 않고 exact pending stage cleanup만 수행한다. task-loop 전체는 91 passed, subtests 127이었다.
- voice lease/local API/Fast API 묶음은 152 passed, subtests 21이었다. pre/post-replace·read stall,
  event-loop 생존, cancellation drain, heartbeat epoch와 stale ON/OFF race를 포함한다.
- `VoiceInputLeaseManager`의 load/commit은 completed-turn과 같은 shared warm killable artifact child와
  bounded deadline을 쓴다. pre-replace stall은 kill/reap 뒤 실패 폐쇄하고 post-replace outcome-unknown은
  canonical disk를 exact 재확인한다. unreadable canonical owner는 `blocked`로 latch해 overwrite를 막는다.
- Bot API lease I/O는 event loop 밖에서 실행하고 cancellation에도 bounded call을 shield-drain한다. Local
  status 전체 수락, Local HTTP ON/OFF, 채팅 mic OFF, Discord acquire/release, retirement와 failed-ON cleanup은
  같은 loop transition lock으로 직렬화한다. physical ACK wait는 lock 밖이라 heartbeat가 완료할 수 있다.
- direct Main `local_mic`과 Discord voice는 accepted exact user-only turn의 durable receipt 뒤에만
  owner·TurnScope·LLM/TTS를 시작한다. Control Page 일반 text도 같은 선행 durability를 사용하고 성공 시
  exact `complete_turn_id`에 assistant만 붙인다. LLM 실패 fresh restore는 `[system, user]`, 성공 restore는
  `[system, user, assistant]`이며 current user는 Main payload에 한 번만 들어간다. caller cancellation은
  physical continuity commit이 끝날 때까지 session lock을 유지한 뒤 재전파한다.
- client STT timeout/cancellation은 caller terminal을 유지하되 blocking physical thread가 실제 반환할
  때까지 shared inference lock을 보유한다. 따라서 late physical inference와 successor 호출은 겹치지 않는다.
- production compile과 최종 canonical `python -m pytest -q`는 4,209 passed, 22 skipped, 1 warning,
  subtests 1,151로 통과했다. 독립 Control Page continuity 리뷰에서도 추가 P1/P2 finding은 없었다. warning은
  기존 `audioop` deprecation이며 실제 Discord, filesystem/antivirus, Docker, microphone, Minecraft,
  GPU/STT 서비스는 실행하지 않았다.

## 2026-08-24 harness external-effect와 recovery boundary

- task approval wire v2는 exact task/stage/path/SHA와 유한 TTL을 결박한다. worker·stage·test·tool 반환 뒤와
  effect 직전에 cancellation, wall expiry, monotonic deadline과 guild epoch를 다시 검사한다. STT caller가
  반복 취소돼도 physical worker와 cleanup을 끝까지 drain하고 shared lock을 실제 물리 수명까지 유지한다.
- Discord command, text search follow-up과 assistant autonomy 전송은 기존 ingress recovery journal에서
  `claim -> bind -> delivery_inflight -> physical send -> delivery_succeeded -> canonical -> complete`를
  분리한다. timeout/cancellation의 원격 수락 여부가 모호하면 successor effect를 막고 자동 재전송하지 않는다.
- search restart adoption은 pre-send generation baseline, exact receipt와 source/delivery pair뿐 아니라 source
  message를 exact reply/reference한 유일한 bot-authored same-content message를 요구한다. 최초 recovery가
  끝나기 전 Discord text/voice ingress와 voice worker·Control Page·Local mic 시작은 fail-closed한다.
- autonomy는 durable `delivery_inflight` 뒤 physical send task 생성 직전에 original grant/action/run을 다시
  확인한다. evidence allowlist와 strict verifier가 `verified`를 재계산하며, post-effect memory integrity 오류도
  exact outcome audit와 state/cursor/ping fence를 먼저 내구화한 뒤 fixed-type 오류로 재전파한다.
- focused 묶음은 task/STT 257, command 160, search/app/voice 150, autonomy/ingress 191 passed였다. production
  compile과 `main.py` 2,498줄 구조 경계를 통과했고 최종 canonical은 4,284 passed, 22 skipped, 1 warning,
  subtests 1,182로 224.40초에 통과했다. warning은 기존 `audioop` deprecation이다. 이 canonical 회귀
  자체에서는 실제 Discord gateway, filesystem/antivirus, Docker, microphone, Minecraft와 GPU/STT
  서비스를 실행하지 않았다.
- 같은 날 STT를 아예 우회한 Bot API→Main→OmniVoice first-PCM live benchmark는 1 cold, 3 warmup,
  10 measured로 오류 0이었다. warm p50/p95는 첫 delta 353.8/415.7ms, 첫 문장 374.2/435.8ms,
  TTS first PCM 427.6/446.2ms, 합산 post-STT first PCM 804.0/873.5ms였고 cold 합산은 2,545.9ms였다.
  입력·continuity는 임시 volume으로 격리했다. OS speaker/Control Page proxy/Discord는 포함하지 않아 실제
  사용자 청취 E2E가 아니라 core service 경계다.

## 2026-08-25 OmniVoice FlashInfer CUDA 12.9 live 적용

- 현재 target은 `evelyn-omnivoice-tts:recipe-e8151492550b`이다. pinned CUDA 12.9.2 base,
  `libnpp-12-9=12.4.1.87-1`, Torch/Torchaudio 2.8.0+cu129, TorchCodec 0.7.0+cu129,
  FlashInfer Python/Cubin 0.6.15.post1과 JIT cache +cu129를 사용한다. OmniVoice 0.1.5와 고정 model
  revision, 검증된 FlashInfer module commit은 유지한다.
- CUDA 12.8 recipe는 RTX 3090에서 동작했지만 FlashInfer 0.6.15의 실제 SM12 normalization 경로가
  CUDA 12.9 이상을 요구해 RTX 5090 warmup에서 실패했다. CUDA 12.9 recipe image
  `sha256:ec6efba2dbcd9c79a3d0666d801f635d54cab4ea4a35272d669f0ab23e27a5ae`는
  RTX 5090 `(12, 0)`에서 model load와 health를 통과했다.
- live health는 `flashinfer_cuda_graph`, JIT off, 2/4/8초 bucket, concurrency 1과 12-step을 확인했고
  host/container GPU UUID도 일치했다. sentence streaming은 유지하며 blockwise는 비활성이다.
- STT를 제외한 1 cold, 3 discarded warmup, 10 measured 결과 warm p50/p95는 first delta
  `589.5/688.9ms`, first sentence `637.7/732.4ms`, TTS first PCM `193.3/215.4ms`, 합산
  `818.2/947.9ms`였고 cold 합산은 `1,717.0ms`였다. report는
  `runtime_artifacts/validation/post_stt_latency/report-flashinfer-12step-gpu0-cu129.json`이다.
- 기존 16-step 결과보다 TTS 구간은 크게 줄었지만 현재 합산의 주 구간은 LLM first sentence까지다.
  실제 speaker/Discord와 사용자 청취 품질, 8초 초과 eager fallback, 장기 VRAM soak는 남아 있다.

## 2026-08-25 Main LLM latency source checkpoint

- `evelyn.voice-latency-trace.v1`은 accepted turn의 request부터 admission slot, prompt, raw/safe token,
  speech commit, TTS first PCM, playback first write와 completion까지 18개의 bounded monotonic stage를
  content-free하게 기록한다. llama.cpp의 allowlisted numeric queue/prompt/cache/prefill/generation timing만
  별도 receipt에 포함하고 원문, prompt, 경로와 음성 식별자는 넣지 않는다.
- 공용 Prompt ABI v2는 model hash뿐 아니라 llama-server와 local/shared-library closure, 실제 server argv와
  CUDA graph env, embedded tokenizer/chat template, canonical prompt wire를 exact identity로 묶는다. Main
  warmup은 서로 다른 두 suffix를 terminal까지 drain하고 second-suffix cache hit, typed timing/finish reason과
  atomic backend epoch가 모두 맞아야 readiness를 연다. Fast와 Core/Discord는 production의
  `clean_text` canonical prefix로 기대 Prompt ABI를 별도 계산하며 불일치는 HTTP 전에 닫는다. Bot readiness는
  cache proof와 `productionPromptMatch=true`를 함께 요구하고 TTL 안에서 proactive proof refresh도 수행한다.
- 모든 production Main surface는 별도 `main_llm_gateway`의 realtime-first 단일 owner를 거친다. Core/Discord와
  Local→Fast 음성은 accepted turn에만 content-free ticket을 준비하고, 실제 첫 REALTIME Main admission
  경계에서 stale ticket을 같은 capture generation/backend epoch로 재발급한다. BACKGROUND는 예약을 만들지
  않으며 malformed/network binding은 fail-closed, typed reservation rejection만 plain REALTIME 한 번으로
  fallback한다.
- Main gateway의 upstream header/stream await는 하나의 cancel-safe polling fence에서 operation을 끝까지
  수거하고 EOF 뒤 중복 read를 만들지 않는다. Qwen broker는 queue 대기와 inference clock을 분리하고,
  slot·durable marker claim 뒤부터 검증된 upstream 결과까지에만 inference 예산을 적용한다. 2026-08-27
  timeout 뒤 REALTIME successor와 queue 뒤 Qwen successor 회귀, 관련 묶음 및 canonical
  `4,573 passed, 22 skipped, 1,391 subtests passed`가 통과했다.
- `SpeechCommitGate`를 Core/Fast/Local streaming 경로가 공유한다. generation fence와 final-prefix equality를
  만족한 irreversible chunk만 TTS에 넘기며 준비된 TTS와 playback은 같은 owner 아래 bounded overlap한다.
- `tools/main_latency_optimizer_loop.py --run-owned-lab`은 local proposer가 allowlisted 숫자 설정만 제안하고
  최대 12회 aggregate feedback을 받는 고정 loop다. runner는 immutable image와 baseline/source/model/GPU/
  corpus/harness identity, internal-only Docker network, read-only input, repeated ABBA, restart→readiness와
  readiness 이후 first-response 분리 측정, finalist 1,000-turn soak, cache/GPU PID/privacy/quality/resource
  gate를 사용한다. proposer는 shell, 파일, Docker, evaluator와 production 권한을 받지 않는다.
- runner/worker는 POSIX process group 또는 Windows KillOnClose Job으로 자식 tree를 소유한다. host-wide
  campaign lock과 startup/terminal global reconciliation은 exact owner Docker object와 owned temp directory가
  stable zero가 될 때까지 다음 run을 막고, 불확실 cleanup은 `CLEANUP_REQUIRED`로 종료한다.
- production lifecycle contract는 candidate/run/evaluation-bound external observer receipt와
  evaluator/lifecycle capability를 분리한다. coordinator는 사실값 인자를 받지 않고 worker가 고정 source를
  읽은 opaque receipt만 검증한다. 선택적 SQLite journal은 observation one-shot consumption과 state/head
  CAS를 영속화해 재시작 뒤 replay와 accepted/rollback fork를 차단한다. public bootstrap에는 observer
  adapter가 없으므로 현재 자동 loop는 production을 바꾸지 않고 `awaiting_approval`에서 fail-close한다.
- 기존 llama.cpp build의 CUDA library에는 RTX 5090 native cubin이 없고 `sm_52` PTX만 있었다. first-use
  PTX JIT와 일치한 약 11.3초 first-resident tail을 제거하기 위해 기존 build를 보존한 CUDA 12.9.2
  `120a-real` side-by-side build를 만들었다. 정적 검증은 cubin target이 `sm_120a` 하나이고 PTX가 없으며
  server version/commit identity가 고정됐음을 확인했다.
- `docker/Dockerfile.llama`는 pinned CUDA 12.9.2 runtime base를 사용한다. optional
  `EVELYN_MAIN_LLM_BUILD_DIR`은 llama.cpp root 아래에서 검증된 build만 Main/GPU0의 `/llama/build`에
  read-only overlay하고 Router/Sub 등 GPU1 LLM build는 그대로 둔다. fixed lab은 선택 build의 containment,
  reparse-free boundary, server binary와 content identity를 검증한다. 설정하지 않으면 기존 `build`를 쓴다.
- 실제 TTS 합성 readiness 정정 뒤 native SM120/SWA1 graph-off와 graph-on fixed E2E가 모두 status
  `completed`, strict cache `33/33`, validity failure `0`, clean cleanup을 통과했다. graph-off answer first-PCM
  first-after-warmup/resident p50/p95/idle은 `314.0/253.1/298.7/324.7ms`, graph-on은
  `294.6/207.7/262.6/228.8ms`였다. graph-on resident TTFT는
  `57.938/36.931/39.619/39.586/14.415ms`, TTS first PCM은 `92.8/92.0/92.6/91.7/93.1ms`였다.
- 독립 graph-on도 status `completed`, cache `33/33`, validity failure `0`, clean cleanup이었다.
  first-after-warmup answer first PCM은 `278.8ms`, resident p50/p95는 answer first PCM
  `205.8/259.0ms`, TTFT `38.602/56.366ms`, safe commit `111.896/165.704ms`, TTS first PCM
  `91.8/93.3ms`였다. idle answer first PCM은 `387.8ms`로 첫 graph-on run의 `228.8ms`보다 변동했다.
- 최신 backend exact-payload TTFT graph-off cold/capture/resident/idle은
  `284.942/20.167/20.165/210.148ms`, graph-on은 `320.215/14.411/16.286/122.597ms`였다. 전체 answer
  PCM 기준으로 graph-on이 first-after-warmup, resident p95와 idle을 모두 낮췄다.
- Compose source 기본 GPU 역할은 GPU0 Main+TTS, GPU1 STT다. local Main 기본 설정으로 batch `2048`, ubatch
  `2048`, cache reuse `256`, cache RAM `8192MiB`, CUDA graph `1`, full-SWA `1`, native SM120 Main build를
  source 기본값으로 승격했다. launcher와 Compose는 Main build가 exact `120a-real`이 아니거나 없으면 시작 전에
  닫고 일반 build로 fallback하지 않는다. GPU1 Router/Minecraft/Sub LLM은 기존 multi-architecture build를 유지한다.
- 2026-08-27 finalist Attempt 7은 graph-off/on warm `200×2`, restart-ready `30×2`, ABBA macro-block
  `20`, soak `1,000`을 완주했다. fresh-process verifier는 `verified`, evaluator는
  `eligible/candidate_passed/passed`였다. warm first-PCM p50/p95/p99는 graph-off
  `238.7/260.7/290.1ms`, graph-on `201.85/219.1/239.8ms`이고, paired p95 delta의 95% CI는
  `[-45.7,-26.7]ms`, effect size는 `-3.0166`이다. output equivalence `200/200`, 오류·품질·안전·cache
  failure 0, GPU 최소 여유 `9,886/9,768MiB`, exact-owned cleanup `0/0/0`, Docker `OFF→OFF`, production
  OFF와 signed host restoration을 독립 검증했다. 이는 Main→TTS-ready fixed harness 승격 근거이며,
  microphone, speaker/Discord first-write와 실제 청취를 포함한 전체 음성 SLO 완료를 뜻하지 않는다.

## Source state

- 전체 프로젝트 감사의 즉시 항목을 별도 안정화 브랜치에서 처리 중이다.
- `main.py` 분해는 목표 범위에 도달했다.
  - 현재 2,498줄이며 원래 목표 범위인 1,500~2,500줄 안에 들어왔다.
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
    authenticated mutation의 `guildId`도 exact nonnegative JSON integer만
    허용하며 bool·float·문자열·누락 값은 owner 호출 전에 고정 오류로 거부한다.
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
  - 로컬 마이크는 첫 threshold 후보 블록에서 exact playback owner/token과
    interrupt binding을 고정한다. callback 전에 그 generation이 해제되고 새
    owner·active validation이 없으면 VAD/RMS/필수 화자 검증을 거친 뒤 cooldown으로
    버리지 않되, STT가 정확한 선두 `이블린`을 확인할 때만 기존 Local Voice
    admission으로 전달한다. 현재 owner의 qualified 취소와 다른 generation의
    fail-closed 계약은 유지한다.
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
  - Control Page 일반·검색, Discord text 검색 후속, 자율 후속, Discord 명령과
    음성 재생 완료는 같은 durable receipt validator를 사용한다. 각 전달 경로의
    effect 순서는 별도 계약을 따르며 commit 실패 때문에 이미 발생한 전달을
    중복 실행하지 않는다.
  - Discord와 local speaker 음성은 playback pipeline이
    `playback_completed=false`를 명시한 stale validation·무재생·부분 재생 결과를
    완료로 확정하지 않는다. Local streaming은 전역 누계가 아니라 이 턴의 exact
    queued/played chunk 수를 우선 사용한다. 고정 전달 실패와 user-only continuity
    경로로 보내며 single·streaming 양쪽을 검사한다.
  - Local Bridge의 `failed|partial|cancelled` playback ACK도 accepted user row만
    ingress `turnId`로 checkpoint하고 assistant text·receipt는 버린다. checkpoint 뒤
    journal 삭제가 실패하면 exact current turn과 마지막 user row로 재시작에서 한 번
    정리하며, 다음 Fast prompt는 이를 미응답 문맥으로 소비한다.
  - 자동 voice search follow-up은 exact voice TurnScope delivery owner가 없으므로
    현재 예약·직접 전달·재시작 재생을 fail-closed한다. voice playback이나
    text history/continuity를 만들지 않으며 별도 voice lifecycle 설계가 남아 있다.
  - 취소된 `TurnScope`에 current task가 늦게 attach되면 즉시 거부하고, 새
    background task는 coroutine 본문 실행 전에 취소한다. 음성 worker의 처리
    예외 로그는 exception type만 남기고 원문 메시지는 기록하지 않는다.
  - Discord 명령 19개와 권한 거부 응답은 composition이 주입한 단일
    post-delivery context owner를 통과한다. 성공한 plain-text 전송만
    history와 checkpoint에 한 번 기록하며, 전송 실패는 기록하지 않고
    Minecraft의 이전 수동 기록도 제거해 이중 commit을 막는다.
    이 응답은 저장 기억을 사용하지 않은 `not_used` receipt로 기록되어 공용
    history filter 뒤에도 assistant 완료 행이 남고 미응답 user tail로 오인되지 않는다.
  - Discord text ingress가 직접 처리하는 명시적 기억 저장 결과 답변도 memory-write
    receipt와 응답 의존 receipt를 분리한다. exact `not_used` receipt가 response-ready,
    assistant history, terminal commit과 ingress completion에 동일하게 전달되므로 공용
    history filter가 저장 결과 답변을 미귀속 행으로 제거하지 않는다.
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
  - v2 session row는 checkpoint 시점 기준 `lastActiveAgoSec`를 저장하고,
    restart에서는 checkpoint age와 합쳐 process-local 활동시각을 복원한다. 합친
    effective age가 `maxAgeSec`를 넘으면 해당 history와 active state를 복구하지
    않으므로 다른 session의 최신 flush가 만료 row의 수명을 늘리지 못한다.
    누락·bool·음수·비유한 activity metadata도 owner restore에서 row 단위로
    제외한다. raw legacy artifact와 generation-0 anchor는 보존하되 history는
    자동 투영하지 않고 새 실제 turn만 다음 v2 generation으로 잇는다.
    verifier는 설정된 scope의 선택 session 활동시각으로 Main/Fast ordering,
    stale과 완료된 reset을 판정한다. 미완료 guild revocation marker는 timestamp
    비교 없이 exact guild row를 제외하므로 벽시계 역행 뒤에도 Main restore와
    Fast/Main cross-surface merge가 철회 전 문맥을 되살리지 않는다. 다른 Main session의 후속 commit은
    오래된 대상 session을 최신으로 만들지 못하며, 선택 대상의 누락·손상된 활동
    metadata는 cross-surface에서 fail-closed한다. 이 내부 시각은 public status에
    노출하지 않는다.
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
    Minecraft status fallback은 upstream error를 `minecraft_status_failed`, local
    exception을 `minecraft_status_failed:<exception-type>`으로 Main LLM context,
    snapshot cache와 Control Page에 전달한다.
  - status와 task event의 오류 코드는 구문 검증하며 알 수 없는 문자열은
    surface별 고정 fallback으로 바꾼다.
  - Mindcraft connection handler와 bot error listener는 disconnect/kick/error input을
    내부 분류에만 사용한다. console·MindServer
    `bot-output`과 handler/listener output에는 fixed classified message만 남긴다.
  - Mindcraft `mcdata`는 protocol client의 `emit`을 재정의하거나
    `PartialReadError`를 선별해 삼키지 않고 표준 listener dispatch를 유지한다.
  - 운영 로그도 예외 원문 대신 고정 event와 exception type만 남긴다. Router
    fallback metadata는 `router_failed`, Local Bridge turn status는
    `turn_pipeline_failed`, control TTS status는 `control_tts_failed`만 보존한다. Voice
    Voice validation observer가 실패해도 이어지는 turn-trace writer 호출을 유지한다.
    observer·writer·file/console sink와 그 fallback 출력의 일반 예외는 원래 turn control
    flow로 전파하지 않으며, 관측 가능한 fallback에는 fixed prefix와 exception type 또는
    `trace_error_type`만 남긴다. Model-call의 동적 예외도 trace 전에 exception type으로
    축약한다. 취소와 process control signal은 삼키지 않는다.
  - Main/Fast tool decision의 `failed` evidence는 serialization에서
    `<tool_name>_failed`로 고정한다. Main vision 예외 metrics도
    `vision_runtime_error`만 남긴다. background Vision Watch의 분석·capture 실패도
    fixed code/type만 artifact·Control Page·soft context에 투영해 원문을 복사하지 않는다.
  - Main LLM warmup non-200 body는 읽지 않고 startup detail에는
    `llm_warmup_failed`, 외부 wrapper에는 고정 `LLM warmup failed`만 남긴다.
  - Control Page welcome LLM non-200 body는 읽지 않으며, failure model-call turn
    trace와 operation log에는 exception type만 남기고 fallback welcome을 유지한다.
  - Fast Control의 빈 채팅도 상태 안내 대신 author `Evelyn`의 한국어 fallback
    welcome 한 문장으로 시작한다. readiness detail은 기존 boot/runtime state에 남긴다.
  - Control Page tool-router failure operation log도 exception type만 남기며, 실패
    결정의 기존 `None` 반환과 text/search fallback을 유지한다.
  - OmniVoice startup health·generate warmup의 non-200 body도 읽지 않고 startup
    detail에는 `tts_warmup_failed`, 외부 wrapper에는 phase별 고정 문구만 남긴다.
  - Control Page server-start 실패는 startup detail에
    `control_page_start_failed:<exception-type>`만 남기고 operation log도 fixed
    code/type만 기록한다. local-only outer wrapper는 원인 traceback을 억제한 fixed
    `Control Page start failed`다.
  - speaker verification probe embedding 실패 detail은
    `speaker_verification_failed:<exception-type>`으로 고정해 Local Bridge validation
    event와 TTS interrupt metrics에 예외 메시지·경로를 복제하지 않는다. enrollment
    skip·success 운영 로그도 exception type과 sample count만 남긴다.
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
    없으면 현재 성공 결과도 `authorization_audit_unavailable`인 unverified로
    바꾸고 모든 grant를 폐기한다. outcome append 직전 grant가 바뀌어 durable
    event의 `verified/authorizationCurrent`가 false여도 cursor를 유지하고 engine을
    중단하므로 새 실행을 허용하지 않는다.
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
    `source_turn_id`, 고정 `conversation_turn` kind를 보존한다. person-bound turn은
    guild raw JSONL 중복을 만들지 않고 room/person/사용자 결합 session raw에 기록한다.
    person key가 없는 호환 경로는 기존 guild/room과 요청된 session fallback을 유지한다.
    ID에는 발화 내용이 들어가지 않으며 allowlist 형식에 맞지 않는 metadata는 저장하지 않는다.
  - trusted person-bound 조회는 기존 guild raw와 guild `vault_raw`를 selection·render·
    count·receipt 전에 제외한다. 현재 ingress room은 공유 문맥으로 남고 exact person과
    사용자 결합 session은 유지한다. 제외된 guild row의 count·opaque ID도 공개 receipt에
    남기지 않으며, 기존 guild artifact에서 owner를 추정하거나 자동 migration하지 않는다.
  - receipt와 turn summary는 실제 prompt에 선택된 raw row의 evidence/turn
    ID를 domain-separated `opaque-evidence-*`/`opaque-turn-*`로 투영한 값과
    attributed/unattributed legacy 항목 수를 공개한다. 새 rolling
    summary는 내용 SHA-256에 묶인 sidecar에 파생 evidence ID와 실제 Summary
    LLM 입력 evidence/turn ID를 저장하고, 내용이 따로 바뀌면 provenance만
    fail-closed로 버린다. 새 facts/questions도 같은 실제 입력 ID와 별도 파생
    evidence ID를 JSONL과 mirror에 보존한다. 다만 이 turn-level provenance에는
    vault note 삭제 현재성을 증명하는 receipt가 없으므로 stored summary/fact/question과
    assistant raw는 prompt 입력에서 보류한다. exact user raw도 person-bound 요청의
    room/person/session 또는 person key 없는 호환 경로의 guild/room과 요청된 session
    layer에서 evidence 검사를 통과한 경우에만 현재 턴과 함께 사용한다.
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
    turn task detach는 성공·실패 모두 유지한다. step/event-log 실패 payload·30일
    보존 JSONL·운영 로그에는 예외 원문 대신 고정 error code와 exception type만
    남긴다. self-identity review queue write 실패가 turn trace decision으로 전달될
    때도 fixed code/type만 남기며, daily vault mirror 실패 로그도 fixed prefix와
    exception type만 남긴다. Summary LLM primary·compact retry 실패 로그도 fixed
    prefix와 exception type만 남긴다. proactive open-question promotion과 background
    vault maintenance 실패 로그의 예외 detail도 `errorType=<exception-type>`만 남긴다.
    background cognitive refresh 실패 로그도 session key·reason·예외 메시지를 제외하고
    fixed prefix와 exception type만 남긴다. Cognitive-state action·latency 로그 역시
    질문·검색 의도·reason·session scope key·예외 메시지를 제외하고 fixed action/scope,
    문자 수, latency와 exception type만 남긴다.
  - owner 없는 self-identity review queue copy의 user/assistant 원문은 사람 검토·export에
    남기되 self-identity runtime-state section이 soft tone hint로 읽지 않는다. renderer는
    reviewed identity profile만 사용하고 pending candidate를 자동 승격하지 않는다. 같은
    턴 원문의 별도 scope-authorized history/raw 사용과 label decision metadata는 유지한다.
  - 저장·중복 성공은 다시 읽은 card의 본문, 직접 사용자 source/source type,
    단일 turn source ref, 본문 SHA-256 evidence, `confirmed_at`과 현재
    recall eligibility를 모두 재검사한 뒤에만 반환한다. 일부 metadata가
    손상된 기존 파일은 성공으로 복구 추정하지 않고 content-free
    `memory_confirmation_write_unverified`로 fail-closed한다.
  - 새 노트는 `memory.user-confirmation.note.v2` marker와 private canonical
    `owner_scope`를 함께 기록한다. recall index는 marker, `user-confirmed` tag 또는
    고정 storage path로 이 계열을
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
  - tombstone 뒤 source Markdown은 content-free stub으로 durable 교체가 성공한
    뒤에만 unlink한다. direct 또는 cascade redaction이 실패하면 원본을 남기고
    cleanup-required로 보고하며, journal/head가 유지되는 동안 index/recall/context에서
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
  full receipt의 비사용 판정도 exact schema/content-free 상태, 알려진 no-memory state,
  빈 note ID/count만 허용한다. metrics 누락·null, non-mapping, 모순된 ID/count·grounding·
  version과 손상 compact state는 `unattributed`이며 parser 예외로 turn을 중단하지 않는다.
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
  exact user raw도 person-bound 요청의 room/person/session 또는 person key 없는 호환
  경로의 guild/room과 요청된 session layer에서 기존 evidence 검사를 통과해야 한다. 같은 원문을 복제한
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
- Control Page의 Discord 모드 토글도 CSRF와 Host Supervisor의 일회용 2분
  preview/apply token을 사용한다. request body는 exact boolean `enabled`만 받고
  fixed `start_discord_bot|stop_discord_bot`으로 매핑해 임의 command를 받지 않는다.
- OFF/ON은 별도 Compose `discord_bot`만 전환해 Bot API·Control Page와 로컬 core를
  유지한다. apply 202는 요청 수락일 뿐이고 UI는 `runtime.serviceHealth` heartbeat로
  실제 상태를 다시 확인한다. OFF는 `SIGINT`와 30초 grace를 사용한다.
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
  시작하지 않고 일반 `/mic on`도 동의 경로를 우회하지 못한다. capture 실패 상태·
  warning은 fixed code와 exception type만 남겨 Control Page와 Main dependency
  context에 예외 메시지·경로를 전달하지 않는다. focused 159개와
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
  Local Bridge 하위 프로세스는 credential을 받지 않으며, Windows Local Bridge 전체 재시작은 exit 75를
  받은 Host Supervisor가 필요한 Discord/Codex 설정만 짧은 handoff에 전달한다.
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
  이 fence는 발화 flush 뒤 정상적으로 false가 되는 VAD `captureActive`가 아니라 mic ON,
  capture-ready/not-stopped, current watchdog·lease/state digest를 검증한다.
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
    등록된다. Discord `마크접속`은 실제 연결 확인과 route 활성화의 literal `True`를
    모두 요구한 뒤에만 성공으로 응답하고,
    `자율시작`이 route를 다시 검증한 경우에만 exact allowlist
    `minecraft:find_food_source`를 grant에 추가한다. route가 없거나 재검증에
    실패하면 assistant scope만 발급한다.
    `자율정지`의 engine lifecycle disconnect는 물리 executor만 멈추고 process-local
    route intent를 보존한다. 명시적 `마크종료`의 `disable_domain`만 route intent를
    먼저 지우므로 뒤이은 executor cleanup이 실패해도 다시 활성화되지 않는다.
    enable·disable과 lifecycle connect·disconnect는 같은 router lock에서 직렬화한다.
    engine start·stop도 task cancel과 executor cleanup, 상태 commit이 끝날 때까지 같은
    engine lock을 유지한다. Discord `자율시작`은 state label과 관계없이 engine stop을
    먼저 끝낸다. 보존된 Minecraft route intent가 있으면 route를 재연결·검증하고, 그 결과에
    따라 assistant 기본 scope와 검증 성공 시에만 Minecraft allowlist를 포함한 grant를 발급한
    뒤 start한다. 따라서 disabled stale loop와 실패한 cleanup은 새 Minecraft child보다 먼저
    정리되며, start 호출자 취소는 새 loop를 만들지 않고 재전파한다.
    typed Minecraft executor는 readiness 뒤 connected 상태 재검사와 inflight 게시를
    disconnect와 같은 lock으로 선형화해 stop 완료 뒤 새 world dispatch를 막는다.
  - 실행 중 trusted planner는 현재 grant에 포함된 step의 연속 prefix만 만든다.
    첫 미허가 step에서 멈추므로 안전 선행조건을 건너뛰지 않고, prefix가 비면
    executor·authorization audit 호출 없이 다음 관찰을 기다린다. 음식이 생긴 뒤에는
    `find_food_source`를 반복하지 않는다. stale·직접 주입 plan의 강제 거부는 유지한다.
  - Mindcraft action gateway의 content-free world-effect projector와 validation
    observer는 같은 shared artifact의 exact grant·lease·actionRun·goalRun·contract
    증거를 상관시킨다. persisted policy의 `telemetryMaxAgeSec`가 유한한 0.1 이상
    숫자가 아니면 예외나 ready로 올리지 않고 fixed postcondition blocker로 닫는다.
    source 배선은 연결됐지만 실제 Discord와 Minecraft world에서
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
  확인한 뒤에만 Host Supervisor를 시작하고 준비 완료를 보고한다. 실행 소스가 아닌
  user-owned `docs/99_PROJECT_INBOX.md`만 dirty-tree 검사에서 제외하며 다른
  tracked/untracked 변경은 계속 fail-closed한다.
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
  fallback으로 전환한다. fallback은 SQLite sidecar·symlink, `schema_version != 7`,
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
  credential preflight를 제거했다. 당시 legacy Voyager runner 기본값은 `local`이었지만
  2026-08-21 `disabled`로 대체했고, explicit legacy local backend도 direct Qwen을 열지 않고
  fail-closed한다.
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
- Mindcraft player chat/whisper ingress는 빈 `only_chat_with`를 deny-all로 해석하고 exact
  configured player name만 받는다. 이 gate는 player ingress에만 적용되므로 self-prompt와
  system autonomous `handleMessage` path는 독립적이다.
- 이미 child가 실행 중인 `/start`는 durable goal과 immutable world-effect binding을
  바꾸지 않는 no-op이다. 실행 중 목표 변경은 별도 `/goal` restart path를 사용하며,
  child stop이 성공한 뒤에만 새 durable goal을 기록한다.
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
  저장 성공 경계를 통과한다. last voice-channel state 저장 실패 운영 로그도
  `[VOICE STATE SAVE FAIL] errorType=<exception-type>`만 남긴다.
  Discord voice connect retry 실패 로그는 기존 attempt/channel metadata와 exception
  type만 남기고, 모든 retry가 실패하면 원문 대신 fixed `voice_connect_failed` wrapper만
  caller에 전달한다.
- 자동 Discord voice search follow-up은 exact voice TurnScope delivery owner가 없으므로
  예약·직접 전달·recovery playback을 fail-closed한다. 과거 pending voice intent도 전송하지
  않고 terminal uncertainty로 남기며, 재도입에는 voice lifecycle 결박이 필요하다.
- Discord playback은 prior source와 `after` callback을 기존 `OMNIVOICE_TIMEOUT_SEC`
  (기본 180초)로 제한한다. timeout은 같은 source가 current일 때만 voice client를 정지하고
  기존 failure 경로로 전파되어 room lock과 user-only continuity를 정확히 한 번 정리한다.
  동적 `vc.play`·`after` callback·stream source 실패의 turn trace에는 fixed
  `discord_playback_failed`와 exception type만 남기며, 기존 timeout code도 content-free다.
- Opus load와 STT warmup 실패는 startup component에 각각 fixed
  `opus_load_failed:<type>`·`stt_warmup_failed:<type>`만 남긴다. 외부 wrapper도 고정
  문구를 cause 없이 발생시켜 Control Page boot progress와 traceback에 원문·경로를 복제하지 않는다.
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
- 삭제 tombstone 뒤 direct·cascade source는 durable redaction 성공 뒤에만 unlink한다.
  redaction이나 source cleanup이 남으면 전체 tombstone source를 최종 재조정한 뒤
  `memory_delete_cleanup_required`, `tombstoned=true`로 보고한다. redaction/unlink 뒤 index 또는 hot-context 정리가
  integrity-class 오류를 내면, deletion ledger의 최종 재검증이 통과한 경우만
  `memory_delete_cleanup_required`, `tombstoned=true`를 보존한다. ledger 자체가
  손상됐으면 기존 exact/content-free integrity 503으로 fail-closed한다.
- UI 전체 178개와 diff check가 통과했다. 실행 중 Control Page image는 교체하지 않았다.

## 2026-08-11 Local mic 안내와 Fast Control 텍스트 projection

- 일반 `/mic on`은 validation 전용 청취 동의를 우회하지 않는다. 대신 Control Page의
  오른쪽 상태 drawer를 먼저 열고 음성 검증 시작·청취 동의 영역을 화면에 표시한 뒤
  포커스한다. capture-ready와 authenticated
  watchdog, durable consent/host-lease fence가 모두 current인 경우에만 현재 ON 상태를
  알린다. Bot API process generation이 바뀌면 브라우저 panel-command cursor를 초기화해
  재시작 뒤 작은 command ID도 적용한다.
- 저장 기억을 요청하지 않은 Fast Control turn은 빈 prompt context에서도 full receipt의
  `state=not_requested`, `groundingState=not_requested`를 보존해 compact `not_used`로
  완료한다. 따라서 생성·TTS·continuity가 성공한 assistant 텍스트가 모순된 receipt로
  `unattributed`가 되어 공개 채팅에서 빠지지 않는다. 손상되거나 기억 의존성을 증명하지
  못한 receipt를 가리는 기존 fail-closed 필터는 완화하지 않았다.
- 실행 중 artifact를 content-free field로 확인했을 때 최근 turn은 assistant text와
  playback/continuity 완료 증거가 있었지만 일부 receipt만 `unattributed`였고, Local Bridge는
  장치 오류 없이 mic OFF였다. 실행 중 Control Page를 새로고침해 drawer가 열리고 검증 시작
  버튼이 보이며 포커스되는 것까지 확인했다. 서비스·마이크·스피커는 재기동하지 않았고
  실제 동의·청취 동작은 다음 사용자 실사용에서 확인한다.

## 2026-08-12 Runtime error observability coverage

- Fast Control continuity의 실제 `fast_control_continuity/status.json`을 별도 오류 source로
  합성한다. Discord/Main continuity artifact와 경로가 달라도 commit·restore 실패 카운터와
  최근 고정 오류 코드를 Control Page에서 놓치지 않는다.
- 필수 runtime service probe가 counter payload 전에 실패하면 현재 장애로
  표시하되 기록된 예외 횟수는 올리지 않는다. Control Page·Bot API·세 LLM·TTS·STT를
  포함하며 UI는 `기록된 예외`와 `현재 장애`를 구분한다. Optional payload-less 실패는
  desired-state 부재로 Runtime Health에만 남는다. Python backend는 다음 재시작부터 적용되며
  현재 실행 중 backend는 아직 이전 source 목록을 제공한다.

## 2026-08-12 Discord voice listener channel-generation 경계

- 채널 이동 시 `stop_listening()`은 listener generation을 올리고 tracked task와 delayed
  SSRC retry에 cancellation을 요청하며 bounded media/utterance queue를 교체한다. 이어
  기존 guild voice TurnScope와 active TTS를 정지하고, playback이 남으면 client를
  disconnect해 이동을 중단한다. 정리가 끝난 뒤에만 `move_to`를 실행하고, 실제 channel ID가
  target과 일치할 때만 새 listener와 last-channel 저장을 진행한다. 반환 뒤 target이 아니면
  disconnect와 fixed `voice_channel_move_failed`로 닫는다. guild lifecycle lock은 요청된
  이동과 관측 event를 직렬화하며, 관측 event는 현재 exact channel만 정리·재청취한다.
  lock 대기 또는 cleanup 중 더 최신 이동이 생긴 stale event는 client를 되돌리지 않는다.
- Discord receive와 Discord-target local mic의 internal client/generation/channel binding은
  assembly, dequeue, STT 반환 뒤를 포함한 pipeline gate, reply dispatch와 delivery client
  lookup에서 재검증된다. connect·move 동안 pending ingress fence를 유지한다. 비치명
  warmup은 lifecycle lock을 놓은 뒤 실행하므로 그 사이 새 channel event가 이전 turn/TTS를
  즉시 정리할 수 있다. TurnScope는 per-item child만 취소하므로 shared ingress worker는
  다른 guild의 queue를 계속 처리한다. Discord `before→after` channel change는 내부·외부
  모두 관측한 exact client/channel에만 같은 idempotent cleanup을 재적용한다.
- 실제 Discord 두 채널 live E2E와 gateway/audio 장치 전이는 실행하지 않았다.

## 2026-08-12 Discord voice accepted-delivery handoff

- 기존 shared ingress worker가 한 발화의 STT·gate뿐 아니라 LLM/TTS/playback 종료까지
  기다려, 재생 중 들어온 다음 발화가 TTS interrupt와 새 TurnScope acceptance에 도달하지
  못하고 기본 queue age를 넘길 수 있던 경계를 같은-room 두 item으로 재현했다. 기존 회귀는
  두 번째 발화가 아니라 테스트 본문이 첫 scope를 외부 취소해 이 순환을 가렸다.
- worker는 STT·gate, durable user-only checkpoint, owner 갱신과 새 scope/current process-task
  attach까지 계속 직렬화한다. 그 직후 per-item handoff를 알리고 기존 process task를 새
  background task로 복제하지 않은 채 TurnScope delivery owner로 남긴다. worker는 다음 item을
  dequeue하며, 다음 accepted turn이 이전 scope task를 취소한다. overlap 모델에서 orphan을
  만들던 `owner_followup && reply_in_progress` 예외도 제거해 모든 새 accepted turn이 같은 room의
  이전 scope를 교체한다. TurnScope의 per-task registration depth는 inner voice helper의 detach 뒤에도
  outer handoff ownership을 유지한다. handed-off 일반 예외는 원문 없이 type만 회수한다.
- queue completion은 drop/reject item의 처리 반환 또는 accepted item의 handoff까지의 ingress
  accounting이고 playback/assistant completion receipt가 아니다. worker shutdown은 handoff 전
  child만 취소하고, handoff 뒤 task는 existing scope cleanup이 소유한다. 검증은 offline fake
  queue/task/scope이며 실제 Discord audio, speaker barge-in latency와 8초 stale-drop 개선은 live로
  확인하지 않았다.
- 변경 직결 64개, voice 전체 670개(skip 5), core 전체 798개(skip 1), CI-equivalent 전체
  3,333개(skip 22), Python 구문·diff check가 통과했다. 실제 Discord·마이크·스피커·LLM·TTS는
  기동하지 않았다.

## 2026-08-12 Discord voice 수락 턴의 pre-delivery continuity

- final STT와 reply gate가 수락한 Discord voice turn은 exact current turn ID와 정규화된
  user tail을 user-only history로 만든 뒤 durable continuity receipt를 요구한다. receipt가
  반환되기 전에는 room owner·TurnScope·LLM·TTS·playback을 시작하지 않는다. commit 실패는
  고정 `conversation_continuity_commit_failed`와 예외 type만 남기고 downstream 실행 없이
  닫는다. direct Main `local_mic`도 Fast ingress를 거치지 않으므로 같은 exact source-turn
  user-only checkpoint를 사용한다. 별도 Local Bridge/Fast ingress 경로의 checkpoint owner는 유지한다.
- Main prompt projection은 durable precommit 표식, current turn ID, exact trailing user
  content가 모두 일치할 때만 저장된 현재 user row 한 개를 복사본에서 제거한다. 따라서
  durable history는 보존하면서 같은 현재 질문을 LLM payload에는 한 번만 넣고, stale turn이나
  손상 tail은 고정 mismatch로 거부한다.
- 정상 완료는 같은 current turn의 exact user-only tail에 assistant와 receipt만 붙이고 exact
  completed pair의 재호출은 history를 바꾸지 않는다. 실패·취소는 선행 user-only checkpoint를
  유지하며 취소 신호를 다시 전파한다. durable receipt 직후의 subprocess hard-exit 복구는
  exact user-only tail 한 개와 assistant 없음, reply side effect 미실행을 확인했다. 이 보장은
  history mutation 경계이며 continuity generation이나 후속 memory/search side effect의
  exactly-once를 뜻하지 않는다.
- 변경 직결 84개, 인접 156개, voice 전체 667개(skip 5), CI-equivalent 전체
  3,290개(skip 22), Python 구문·diff check가 통과했다. 검증은 offline source/test와
  subprocess crash recovery이며 실제 Discord·마이크·스피커·LLM·TTS를 기동하지 않았다.

## 2026-08-12 Discord voice의 post-playback text projection

- non-validation Main voice turn의 실제 playback client가 Discord channel을 가질 때,
  audio playback과 기존 assistant/history/continuity finalization 경로가 끝난 뒤 캡처한
  TurnScope가 취소되지 않았는지와 같은 client/channel binding을 다시 확인하고 canonical
  visible text를 그 channel에 application-level 한 번 전송 시도한다. local-mic ingress가 실제 Discord
  voice client로 route된 경우는 포함하고, 별도 Windows LocalIoBridge/Control Page 경로와
  `LocalControlVoiceClient(channel=None)`는 포함하지 않는다. validation·empty·pre-send scope
  cancellation·moved/replaced target은 전송하지 않는다. channel send 자체의 취소는 관측·
  재시도 없이 전파하며 실제 전달 여부는 모호하다. finalizer가 반환한 canonical memory
  exposure position이 non-null이면 send용 deletion read lease를 required로 다시 획득해
  await 전체에 유지하고 stale exposure는 send 전에 고정 projection 실패로 닫는다.
- text send의 일반 예외는 audio와 finalization 결과를 뒤집거나 재전송하지 않는다. shared
  `DiscordRuntimeStatus`와 별도 voice pipeline observer에 고정
  `discord_voice_text_delivery_failed`와 exception type만 각각 기록을 시도하며, observer
  자체의 일반 예외도 완료된 turn을 바꾸지 않는다. 이 projection은 exactly-once, durable
  receipt 또는 restart replay가 아니다.
- 변경 직결 55개, voice 전체 668개(skip 5), CI-equivalent 전체 3,299개(skip 22)가
  offline에서 통과했다. 실제 Discord channel permission·표시·지연, timeout ambiguity,
  live memory 삭제 경합, 마이크·스피커·Docker는 검증하지 않았다.

## 2026-08-12 Control Page forced-search exact turn binding

- 강제 검색은 이전 `current_turn_id`를 재사용하지 않는다. 첫 per-session critical section에서
  `begin_user_text_turn`과 새 TurnScope를 결박하고, 검색·합성 await 동안 lock을 놓는다. 최종
  critical section은 exact current scope를 다시 검사한 뒤 반환 turn ID로 history 완료,
  durable continuity commit과 로컬 TTS를 수행한다. 그래서 검색은 이전 일반 턴을 취소하고,
  검색 대기 중 들어온 후속 턴은 검색의 stale sink를 0회로 만든다. 일반·검색 TTS task도
  완료까지 exact scope를 소유해 다음 턴이 stale 재생을 취소한다.
- focused 34개, continuity 인접 68개, UI 전체 186개와 CI-equivalent 전체
  3,294개(skip 22), Python 구문 검사가 통과했다. 검증은 offline source/test이며 실행 중
  Control Page·LLM·TTS·Docker를 시작하거나 교체하지 않았다.

## 2026-08-12 Discord autonomy-start runtime error producer

- Discord `자율시작`의 engine 생성, 기존 cleanup, 새 start 실패는 기존
  `DiscordRuntimeStatus` owner에 고정 `autonomy_start_failed`와 exception type만 기록한다.
  기록기 자체의 일반 예외는 승인 회수와 고정 실패 응답을 막지 않는다.
- engine start가 성공한 뒤 응답 전송만 실패한 경우는 시작 실패로 기록하거나 grant를
  회수하지 않는다. cleanup/route/start await 취소는 오류 기록·응답 없이 기존 grant를 회수한 뒤 재전파한다.
  관련 82개, Discord I/O 133개, Runtime Errors/Health 인접 70개와
  CI-equivalent 전체 3,298개(skip 22)가 통과했다. 검증은 offline이며 실제 Discord·
  Minecraft·Docker를 시작하지 않았다.

## 2026-08-12 Discord voice duplicate cooldown 경계

- 동일하거나 유사한 STT는 기존 reply cooldown 동안에만 `duplicate`로 차단한다.
  cooldown이 끝나면 owner·speaker·wake gate를 정상 재평가하므로, 다른 gate가 허용한
  exact wake는 duplicate만으로 막히지 않고 `wake_entry`로 다시 수락될 수 있다. TTS
  suppression과 cooldown 안의 recent echo 차단은 그대로 유지한다.
- 정책·runtime gate 집중 20개, voice 전체 668개(skip 5), CI-equivalent 전체
  3,299개(skip 22)가 통과했다. 검증은 offline source/test이며 실제 Discord·마이크·
  스피커를 기동하지 않았다.

## 2026-08-12 explicit memory principal isolation

- 직접 사용자 확인 기억은 attribution과 별도로 opaque owner scope를 갖는다. Discord
  text/voice와 Main recall은 trusted exact guild/person을 공유하고, Fast JSON·stream·
  default recall은 startup의 cross-surface configured principal 또는 별도 local-only
  principal 하나를 공유한다. 같은 owner/action 재시도만 멱등이고 다른 owner의 같은
  action ID는 별도 노트다.
- Markdown이 source of truth이며 SQLite index schema 7은 private `owner_scope`를 복제한다.
  retrieval cache v3 key, FTS/scan, vector 추가, graph neighbor, read-only Markdown 재결합과
  최종 render에서 exact owner를 검사한다. owner-scoped core/project는 global hot context에서
  제외하며 token은 receipt/card/provenance/snapshot/graph에 투영하지 않는다.
- owner가 없는 v1, marker-only, tag/path 계열 direct-confirm note는 관리 UI·편집·삭제에는
  남지만 자동 Main/Fast prompt에서는 fail-closed한다. 기존 guild/session에서 owner를 자동
  추정하거나 migration하지 않으며 현재 principal의 새 `/remember` 근거가 필요하다.
- 변경 직결 집중 333개(skip 1), memory 전체 307개(skip 1), CI-equivalent 전체
  3,306개(skip 22), Python 구문·diff check가 통과했다. 검증은 offline temp-vault/source
  tests이며 실제 Discord·Control Page·사용자 memory·Docker는 기동하거나 수정하지 않았다.

## 2026-08-12 Discord voice completion commit ordering

- audio playback 뒤 assistant history를 붙이고도 선택적 `schedule_memory_update`의 동기
  raw transcript write가 실패하면 active follow-up, room owner와 completion checkpoint 전에
  빠져나가던 순서를 actual `OSError`로 재현했다. 사용자는 답변을 들었지만 즉시 restart하면
  선행 precommit의 user-only tail만 복구될 수 있었다.
- 같은 memory-exposure guard 안에서 exact assistant append, session snapshot과 active TTL,
  process-local room owner 반영, completion commit 시도를 먼저 실행한다. 기존 benchmark,
  memory update와 cognitive gating은 그 뒤에 실행한다. commit이 durable하면
  선택적 예외가 계속 전파되더라도 persisted completion pair와 active follow-up state를
  되돌리지 않는다. commit 자체의 실패는 기존 고정 오류 경계를 유지한다.
- 실제 `SessionStateStore`와 `SessionContinuityCheckpoint` 회귀는 memory update `OSError` 뒤
  fresh restore에서 exact `[system, user, assistant]`와 assistant 답변을 확인했다. 변경 직결
  17개, voice/continuity 인접 67개, voice 전체 669개(skip 5), CI-equivalent 전체
  3,307개(skip 22), Python 구문·diff check가 통과했다. 검증은 offline이며 실제 Discord,
  마이크, 스피커, LLM, TTS, Docker를 기동하지 않았다.

## 2026-08-12 autonomy delivered-followup terminal boundary

- 실제 Default/Routed executor와 `AutonomyEngine`에서 Discord follow-up 전송 성공 뒤
  memory update가 `OSError`를 내면 action이 generic executor failure가 되고 cursor 0을
  유지해, 4초 poll마다 같은 follow-up과 오류 알림을 다시 보내는 경계를 재현했다.
- send await가 정상 반환하면 즉시 900초 ping fence를 세운다. 그 뒤 새 turn, history,
  active session과 continuity commit을 선택적 memory/self-state보다 먼저 처리한다.
  post-send 일반 예외는 shared `DiscordRuntimeStatus`에 고정
  `autonomy_followup_finalize_failed`와 exception type만 기록하고, verified
  `discord_send_completed` 결과와 plan cursor를 유지한다. observer와 logger의 일반
  예외도 전달 결과를 바꾸지 않으며 취소·memory deletion integrity 신호는 재전파한다.
- search-pending과 unresolved maintain need는 같은 900초 fence 안에서 다시 만들지 않는다.
  이 보장은 send의 정상 반환 뒤 같은 프로세스의 자동 재실행에 한정하며 timeout·취소의
  원격 전달 모호성, process crash exactly-once 또는 durable outbox를 뜻하지 않는다.
- 변경 직결 22개, autonomy/Runtime Errors 인접 160개, CI-equivalent 전체
  3,308개(skip 22), Python 구문·diff check가 통과했다. 검증은 offline이며 실제
  Discord·LLM·Docker를 기동하지 않았다.

## 2026-08-12 per-session continuity restore expiry

- `maxAgeSec=60`에서 A가 101초 묵은 뒤 B의 fresh flush가 checkpoint `savedAt`을
  갱신하면 restart artifact age는 1초가 되어, effective age 102초인 A와 fresh B가
  모두 복구되는 경계를 actual `SessionStateStore`와 checkpoint로 재현했다. 다음 A
  text turn history에도 만료 문맥이 다시 들어갔다.
- restore는 mutation 전에 `checkpoint age + lastActiveAgoSec`를 row별로 계산하고
  max age를 넘는 history/active state를 제외한다. activity age가 누락·bool·음수·
  비유한인 legacy/손상 row도 자동 복구하지 않는다. raw legacy checkpoint의
  generation-0 anchor와 rollback protection은 유지하고, 새 실제 turn만 v2
  generation-1로 chain한다. schema나 새 owner는 추가하지 않았다.
- continuity 직결 46개(skip 1), 인접 95개(skip 1), CI-equivalent 전체
  3,309개(skip 22), Python 구문·diff check가 통과했다. 검증은 offline이며 실제
  Discord·LLM·Docker를 기동하지 않았다.

## 2026-08-12 autonomy stop Runtime Error ownership

- 실제 `DiscordAppComposition`에서 `자율정지` engine cleanup이 실패하면 grant 회수와
  고정 응답은 실행됐지만 shared `DiscordRuntimeStatus`의 code/type은 빈 값이었다.
  cleanup 성공 뒤 성공 응답만 실패한 경우도 같은 catch가 정지 실패로 오분류해 두 번째
  실패 응답을 시도했다.
- stop의 일반 예외만 고정 `autonomy_stop_failed`와 exception type으로 shared Discord
  Runtime Errors에 기록한다. composition의 기존 no-throw observer를 재사용하므로 관측기
  실패가 grant 회수·고정 응답을 막지 않는다. 성공 응답은 cleanup try 밖에 두어 전송
  실패를 정지 실패로 기록하거나 재응답하지 않고, 취소는 기존처럼 그대로 전파한다.
- actual `AutonomyEngine`의 child loop cleanup을 기다리는 동안 caller가 취소되면 기존
  `suppress(CancelledError)`가 caller 취소까지 삼켜 성공 응답으로 진행하던 경계도 닫았다.
  start와 같은 current-task cancellation 판별로 caller 취소만 재전파하고, child 자체
  cancellation은 계속 drain한다. 취소된 cleanup은 `stopping`에 남아 다음 stop이 재시도한다.
- 변경 직결 3개, autonomy core 62개, Discord I/O 135개, Runtime Errors 인접 21개,
  CI-equivalent 전체 3,312개(skip 22), Python 구문·diff check가 통과했다. 검증은 offline이며 실제
  Discord·Minecraft·Docker를 기동하지 않았다.

## 2026-08-12 person-bound legacy raw and identity-review prompt isolation

- actual temp memory root에서 Discord A의 person-bound turn이 guild raw fallback을 통해
  다른 room/user B의 Main prompt와 receipt에 `attributed`로 들어가던 경계를 재현했다.
  별도 actual `record_self_identity_turn`→`render_self_state_context` 경로에서도 tone/identity
  candidate의 owner 없는 queue copy가 self-model runtime-state hint로 렌더링됐다.
- person-bound write는 guild raw JSONL 중복만 생략하고 room/person/사용자 결합 session을
  유지한다. person-bound read는 기존 guild raw와 guild `vault_raw`를 selection·render·
  count·receipt 전에 제외한다. current room 공유, same-person cross-room continuity와 person
  key 없는 local/legacy guild/room 및 요청된 session fallback은 유지하며 permission/history
  authorization을 새로 검증한다고 과장하지 않는다.
- identity review queue와 export는 그대로 유지하되 owner 없는 queue copy를 self-identity
  runtime-state hint로 읽지 않는다. renderer는 reviewed identity profile만 사용한다. 같은 턴
  원문의 별도 scope-authorized history/raw 사용은 유지하고, 기존 queue를 자동 삭제·owner
  추정·migration하지 않는다. 새 schema·owner token·cache는 추가하지 않았다.
- privacy 집중 99개, memory 전체 307개(skip 1), CI-equivalent 전체 3,313개(skip 22),
  Python 구문·diff check가 통과했다. 검증은 offline이며 실제 Discord·사용자 memory·배포
  runtime·Docker를 기동하거나 수정하지 않았다.

## 2026-08-12 Discord prefixed-command reply-slot ordering

- 같은 channel에서 느린 일반 text A가 reply slot을 점유한 동안 `!status` B를 보내면,
  기존 command precheck가 slot을 우회해 B를 먼저 전달하고 전용 command turn을 commit한
  뒤 A의 정상 답변이 전송되는 역전을 재현했다. 또 A가 생성 중 실패하면 lock이 먼저 풀려
  B가 고정 실패 reply와 그 continuity를 추월하는 failure-path old-red도 확인했다.
- guild-prefixed command의 `process_commands()` 전체를 기존 exact guild/channel/thread
  reply-slot lock으로 감쌌고, 예외에서는 같은 lock을 고정 실패 reply의 delivery·continuity까지
  유지한다. 일반 A가 먼저면 이 경계와 선택적 voice가 끝난 뒤 B가 실행되고, B가 먼저면 뒤 일반 turn은 기존 busy-drop을 따른다. 새 scope·queue·
  admission owner나 command preemption은 추가하지 않았다.
- 생성 실패 logger가 던져도 fallback을 계속하고 summary observer 실패가 취소를 덮지
  않는 회귀를 포함해 command/text 28개, Discord I/O 137개, continuity 인접 62개,
  CI-equivalent 전체 3,315개(skip 22), Python 구문·diff
  check가 통과했다. 검증은 offline fake Discord channel/event이며 실제 Discord·LLM·TTS는 기동하지 않았다.

## 2026-08-12 Discord normal-text Runtime Error ownership

- normal Discord text generation을 private canary가 든 `RuntimeError`로 실패시키면 고정
  `text_turn_failed` 응답과 continuity는 완료되지만 shared `DiscordRuntimeStatus`가
  `errorCount=0`, 빈 `lastErrorCode/Type`으로 남는 false-clear를 actual status 객체로 재현했다.
- text handler outer failure boundary에 기존 shared recorder를 dependency composition으로
  전달해 `discord_text_turn_failed`, exception type과 process-lifetime count만 best-effort로
  기록한다. recorder 자체 실패는 고정 응답·continuity를 막지 않고, `CancelledError`는 이
  `Exception` 경계에 들어오지 않는다. 새 status owner·queue·schema는 만들지 않았다.
- 변경 직결 41개, Discord I/O 138개, continuity 인접 62개, Runtime Errors/Health/UI
  40개와 CI-equivalent 전체 3,316개(skip 22), Python 구문·diff check가 통과했다. 검증은 offline fake Discord와 actual
  `DiscordRuntimeStatus`이며 실제 gateway·heartbeat·Control Page 표시는 기동하지 않았다.

## 2026-08-12 autonomy exact Discord text recipient continuity

- 실제 canonical A/B user session과 checkpoint를 사용하면 기존 autonomy는 target-map key를
  버리고 configured channel에 전송한 뒤 `guild:<id>:default`에 history/active/commit해, 다음
  exact user prompt와 restart 문맥에서 전달한 후속을 잃었다. 만료 session, 같은 session의 더
  새 message, busy reply slot과 cognitive refresh/command 경합도 별도 offline old-red로 고정했다.
- 대화형 observation은 active canonical Discord text target만 session/message/channel identity/
  last-active snapshot으로 결박한다. 명시적 관찰채널 또는 thread parent 안에서 가장 최근 active
  candidate를 고르고 voice/default/noncanonical·만료·변경 target은 제외한다. send와 required
  continuity는 exact recipient의 기존 reply/session lock과 room/person/user-session memory scope를
  사용하며 fresh restore에서 exact user/assistant pair와 `not_used` receipt를 확인했다.
- busy/no-target은 question mark나 send/ping/history/commit/memory 없이 transient blocked로 남는다.
  cognitive refresh는 reply slot을 nonwaiting claim해 exact session을 확보한 뒤 slot을 다음 text
  turn에 넘기고 session lock 아래 update를 끝낸다. prefixed command도 `reply -> session` 순서를
  사용한다. normal handler의 direct target writer는 durable claim+turn begin 성공 뒤에만 실행되어
  busy/ignored/redelivery ingress를 제외한다. 전달·기록된 plain-text command reply는 기존 command
  continuity 경로로 target을 갱신할 수 있다.
- `send_discord_text` await 자체의 일반 예외는 shared status에
  `autonomy_followup_send_failed`와 type/count만
  best-effort로 기록하고 원래 예외를 재전파한다. 취소·memory integrity와 blocked 결과는
  기록하지 않으며 모든 소유 lock을 해제한다. 새 target schema·queue·owner는 추가하지 않았다.
- 변경 직결 72개, autonomy 인접 132개, Discord I/O 139개와 CI-equivalent 전체
  3,331개(skip 22), Python 구문·diff check가 통과했다. 검증은 offline fake Discord와 actual
  `SessionStateStore`/checkpoint/status이며 실제 Discord·LLM·heartbeat·Docker를 기동하지 않았다.

## 2026-08-12 Discord text search-followup source/delivery turn binding

- 실제 `SessionStateStore`·checkpoint·recovery journal에서 검색 A가 실행 중인 동안 같은
  session의 successor B를 시작하면, 기존 delivery가 A의 query/result pair를 mutable B
  turn ID로 commit해 fresh restart에서도 B가 완료된 것처럼 복구하는 경계를 재현했다.
- 예약 시 canonical Discord text source turn ID를 고정하고, 결과 전달은 같은
  channel/thread reply slot과 exact session lock을 `reply -> session` 순서로 잡은 뒤 source가
  여전히 current일 때만 진행한다. 성공한 pair는 별도 delivery turn ID로 assistant state와
  함께 exact commit한다. 같은 query의 successor는 이전 task를 취소·교체한다.
- recovery journal은 immutable source `turnId`와 `deliveryTurnId`를 분리한다. prepare 직후
  crash는 source anchor로 새 delivery turn을 만들며, recovery 취소는 process-local claim을
  해제한다. durable commit 뒤 일반 memory 후처리 실패는 type-only로 격리해 전송을 계속하되
  memory deletion integrity 신호는 재전파한다.
- exact voice TurnScope delivery owner가 없는 자동 voice search follow-up은 예약·직접 전달·
  recovery playback을 모두 fail-closed한다. 이는 일시적 기능 축소이며 별도 voice lifecycle
  결박 없이는 재도입하지 않는다.
- 검색 후속·memory exposure·composition·voice side-effect 직결/인접 65개와
  CI-equivalent 전체 3,340개(skip 22), main 2,500줄/158자 구조 예산과 diff check가 통과했다.
  검증은 offline이며 실제 Discord·검색 서비스·음성·Docker는 기동하지 않았다.

## 2026-08-12 Minecraft connect command route completion boundary

- 실제 composition과 `DiscordRuntimeStatus`에서 물리 연결은 검증됐지만 route가 `False` 또는
  private 예외를 낸 경우에도 기존 명령이 성공 문구를 보내고 Runtime Errors를 비워 두는
  false-success를 offline으로 재현했다.
- `마크접속`은 물리 연결 확인과 route 활성화의 literal `True`를 모두 성공 조건으로 삼는다.
  route 실패는 fixed `minecraft_connect_failed`와 exception type/count만 남기고 고정 실패
  응답으로 닫는다. 이미 성립한 물리 연결은 자동 rollback·재시도하지 않는다.
- effect 성공 뒤 Discord 응답 전송 실패는 연결 실패로 기록하거나 두 번째 응답을 보내지 않고
  원래 예외를 재전파한다. 집중 61개와 CI-equivalent 전체 3,342개(skip 22), Python 구문·
  diff check가 통과했다. 검증은 offline이며 실제 Discord·Minecraft·heartbeat·Control Page를
  기동하지 않았다.

## 2026-08-13 Local voice validation progress and consent state fences

- validation-bound STT가 admission에서 거절되거나 너무 짧으면 더 이상 current attempt를
  `pending`에 두지 않고 fixed error event로 실패시켜 같은 단계 retry를 허용한다. 일반
  no-wake/ambient drop은 기존처럼 조용히 버린다.
- consent active commit과 validation bind 뒤 host lease를 즉시 다시 게시해 다음 1초 heartbeat
  전의 정상 발화를 stale fence 409로 거부하던 창을 없앴다. watchdog 물리 stop은 같은 lease
  heartbeat만으로 authorized가 되지 않으며, 검증된 새 explicit ON에서만 latch를 해제한다.
  fresh validation GET이 active consent와 4초 이상 지속된 physical-off blocker를 함께 보면
  OFF/revoke 후 consent action을 다시 연다.
- Control Page의 listening 표시는 순간 VAD `captureActive`가 아니라 current Bridge의
  `ready && captureReady`를 사용한다. 음성 검증 UI는 POST와 consent/repair action을 한 번에
  하나만 실행하고, mutation/신규 poll 뒤 늦게 도착한 GET을 버리며, backend와 같은
  playback-started/completed·nonfailed·one-shot 조건에서만 청취 확인을 허용한다. 409/400의
  allowlisted voice code는 고정 안내로 표시하고 mutation 실패 뒤 canonical session을 다시 읽는다.
  `reply_final` 뒤 30초 동안 playback event가 하나도 없으면 세션 TTL까지 pending으로 두지 않고
  `playback_start_timeout`으로 현재 attempt를 실패시켜 단계 retry를 연다.
- 변경 영향·인접 모듈 414개(skip 1)가 통과했다. 검증은 offline source/test이며 실행 중 Control Page,
  Bot API, Local Bridge, 마이크·스피커·Docker를 기동하거나 교체하지 않았다.

## 2026-08-13 Live2D tail root-driven overlapping action

- 기존 idle tail은 root를 ±8.8도로 독립 왕복시키고 뒤 6개 분절이 앞 각도의
  78~82%를 상속해, 위상 굽힘보다 전체 피벗이 약 3배 강한 그네 동작이었다.
- 참고 영상을 직접 확인해 root의 primary arc, 관절별 overlapping delay, 반전 뒤 tip inertia,
  S↔7 실루엣 교대를 기준으로 삼았다. root만 ±6.5도로 구동하고 뒤 6개 absolute heading은
  앞 heading을 spring으로 지연 추종한다. idle에서는 동일 7개 관절에 먼저 쓰인 native
  physics 출력과 끝으로 커지는 인접 heading 차이를 weight 1로 blend해 custom motion이 실제 최종값이 되며,
  speaking fade에서는 weight 0의 native Body/Breath physics로 부드럽게 돌아간다.
- 초기 follower는 올바른 구조여도 주기가 약 14초이고 tip 반전이 약 1.5초 늦어 참고 영상보다
  느렸다. phase 속도뿐 아니라 spring을 `scale²`, damping을 `scale`로 함께 3.75배 시간 압축해
  normalized overlap을 유지하면서 동작만 빠르게 했다. 고정 60 Hz 회귀는 주기 3.4~4.1초,
  root→tip 반전 0.3~0.5초, S·7 구간과 heading/local clamp를 함께 고정한다.
- 실행 중 8799가 `tail-overlap-4`를 로드한 상태의 actual model을 8.5초 샘플링한 결과
  주기 3.721초, heading 반전 지연은 root→tip
  `[0, 0.063, 0.119, 0.180, 0.259, 0.339, 0.400]`초였고 browser error는 0이었다.
  위치·vertex·drawable render order는 변경하지 않았다. 전체 꼬리 노출 여부는 별도 model asset
  과제이며 이번 움직임 수식의 완료 조건으로 섞지 않는다. Live2D asset 17개와 전체 UI 191개가
  통과했고 사용자 최종 시각 튜닝은 남아 있다.

## 2026-08-13 Lease-bound Minecraft service bootstrap

- Control Page의 explicit `connect|goal`은 더 이상 최대 수분의 기동·연결 await로 6초 public
  proxy를 붙잡지 않는다. 초기 작업 응답과 continuity가 전달된 뒤 기존 FastAction이 실행되고,
  성공·실패 follow-up은 같은 task ID로 기록된다. validation voice에서는 이 command/tool 경계를
  계속 호출하지 않는다.
- 이미 관리 중인 Minecraft HTTP runtime의 lease-bound `start()`가 첫 `/start`에서 service
  offline을 확인한 경우에만 Windows Host Supervisor의 fixed `start_voyager` preview/apply를
  호출한다. action argv는 `voyager` 하나와 `--no-build --no-deps`로 고정된다. Voyager는 필요하면
  생성·재생성하지만 core가 관리하는 `router_llm`과 `minecraft_llm` prerequisite는 기동하거나
  재생성하지 않는다. `/health`가 true가 된 뒤 같은 proof의 `/start`를 정확히 한 번 재시도하며,
  최종 성공은 기존 physical connection verification이 소유한다.
- direct `set_goal()`, status/stop/action과 proof 없는 start는 서비스를 기동하지 않는다. 다만
  matching active lease가 없는 Fast Control goal은 새 lease의 `connect(goal=...)` 경로이므로
  bootstrap될 수 있다. Local Bridge의 폐기된 command queue는 authorization-required로
  fail-closed하며 이를 launcher Runtime Error로 오분류하거나 raw exception/path를 heartbeat에
  저장하지 않는다.
- functional-readiness 대기는 설정으로 늘릴 수 있지만 exact minimum은 60초다. 기본 시간 상한은
  service health 대기 300초, Discord delegated connect 480초, delegated `connect_ack`/goal/disconnect와
  owner-side direct `/goal` 30초, 그 밖의 owner-side service request 2.5초다. timeout은 성공 증거가
  아니며 최종 결과는 lease owner의 기존 verified outcome으로만 판정한다.
- graceful delegated `connect|goal` 취소는 이미 보낸 mutation의 응답을 먼저 회수한 뒤 캡처한
  exact lease ID 조건부 `disconnect`를 완료하고 취소를 재전파한다. transport/response 유실과 typed
  result 손상도 ID가 알려진 경우에만 같은 보상을 거치며 unknown/replacement lease는 blind-disconnect하지
  않는다. delegated goal과 Fast Control local goal은 status에서 캡처한 exact lease ID를 owner operation
  lock 안에서 검증한 뒤에만 goal/audit effect를 시작한다. local/delegated autonomy action도 dispatch 전에
  같은 ID를 캡처해 executor→delegation→owner lock까지 전달하고, cancel·uncertain response·shutdown fallback은
  그 action ID의 lease만 정리한다. ID 없는 손상 record는 network cleanup도 시작하지 않는다. 성공 delegated
  connect는 응답 전 exact
  `(guildId, leaseId)` pending ACK와 30초 Bot API watchdog을 등록한다. remote ACK는 caller 취소에도
  shield/collect하며, ACK 응답만 유실되면 exact public status가 같은 active lease와
  `delegatedConnectPending=false`를 증명할 때만 성공으로 수렴한다. ACK 전 same-guild 비-disconnect
  mutation은 거부하고 explicit disconnect는 mutation과 함께 pending을 정리한다.
- ACK가 없거나 정리가 실패하면 watchdog은 exact lease ID를 조건으로 disconnect를 재시도한다.
  lease가 교체되면 stale watchdog은 새 lease를 정지하지 않으며, remote가 lease ID를 알기 전
  취소·실패하면 blind disconnect하지 않고 owner 경계에 맡긴다. 따라서 delegated-worker의
  ACK 이전 hard-kill은 source에서 bounded cleanup으로 닫혔고, direct owner connect와 480초 remote
  request upper bound는 바뀌지 않았다.
- 빈 `/minecraft goal`은 실행 없이 고정 사용법을 반환한다. Discord 도움말은 실제 guild-prefix
  `마크접속|마크상태|마크종료|마크목표`를 표시하고, 존재하지 않는 Discord slash 안내는
  `minecraft-connect` prefix 명령으로 교정했다. no-argument command의 추가 인자도 더 이상
  버리고 실행하지 않는다.
- 서로 겹쳐 합산하지 않는 broad 13개 모듈 418개, 전체 Minecraft 253개(skip 11), ACK-focused
  61개와 Python compile·scoped diff check가 통과했다.
- 2026-08-13 사용자 승인 live run에서 기존 survival/easy Java 서버를 재사용하고 `bot_api`,
  `router_llm`, `minecraft_llm`, `voyager` 네 컨테이너만 기동했다. 두 LLM은 healthy, Bot API
  source identity는 verified, Voyager HTTP health는 ready였고 다른 Evelyn 컨테이너는 시작하지
  않았다. 첫 `/minecraft connect`는 빈 새 `bot_profiles` 때문에 60초 뒤 fixed failure로 끝났고
  lease revoke와 runner stop이 verified됐다. 이후 일회성 격리 helper가 새 profile에만 Microsoft
  device-code 인증을 완료했고, 예전 `C:\Evelyn\bot_profiles`는 복사하거나 사용하지 않았다.
  두 번째 `/minecraft connect`(`fast-action-2`)는 completed가 됐으며 서버 join 1회,
  `connected=true`, fresh telemetry, exact functional readiness, authorized lease를 확인했다.
- authenticated safe-world 관찰에서 `survival_controller.updated_at`이 두 시점과 추가 10개 sample
  동안 계속 증가했다. 10개 모두 connected/fresh/ready였고 health 20, hunger 15 이상, hostile 0,
  controller error 0, phase `planner_control`이었다. 위험이 없는 상태에서 생존 모드가 불필요한
  행동을 만들지 않고 제어를 넘기는 live 경계까지 증명했다. 네 Minecraft 전용 컨테이너는 실행
  상태로 유지했다.
- 첫 Microsoft device-code는 native `onMsaCode`가 code와 bounded TTL만 exact marker로 내보내고,
  sidecar가 이를 process memory에서만 보관한다. 엄격히 검증된 challenge는 실행 중·미연결·만료 전
  Control Page state와 기존 Minecraft 상태 한 줄에만 투영된다. 일반 `/minecraft status` reply와
  chat continuity·TTS·health·observe·child artifact·telemetry에는 코드를 넣지 않으며 만료, 연결,
  reset, child exit와 verified stop 때 제거한다. challenge는 readiness/lease ACK/timeout을 바꾸지 않는다.
  core/Fast/UI 집중 40개, Fast 95개, UI 35개와 최종 CI-equivalent 3,642개(skip 22)가 통과했다.
  정상 profile-bound 첫 로그인 surface와 실제 hostile/저체력/식량 부족 반응, forced Discord-worker
  stop, goal/disconnect와 world-effect E2E는 별도 사용자 승인 세션에서 검증해야 한다.
- live 전 source 감사로 passive mob hostile 오인, `self_preservation` 뒤 survival 평가 순서,
  nested hostile ownership, content-free survival status projection을 수정했다. planner recovery
  single-flight 순서와 Docker CRLF patch 입력도 고쳤다. `tests/mindcraft` 28개,
  `tests/minecraft` 243개(skip 11), Docker Compose 계약 24개, goal-manager 30개,
  survival-mode 35개와 실제 Voyager image build-time suite가 통과했다.

## 2026-08-13 Mindcraft 빠른 생존 판단과 검증형 전투 경험

- 기존 `self_preservation`이 용암·화재·익사·치명 피해의 유일 P0 소유자다. Evelyn P1은
  health/food, breath, hostile spawn, 18·8·4m band crossing/gone event를 coalesce하고,
  stationary hostile 대비 150ms cached-hostile fallback을 사용한다. 일반 event는 flag만 남기지만
  8블록 안의 actionable hostile은 full snapshot·LLM 없이 최대 1.2초의 bounded direct-sprint reflex를
  시작할 수 있다. full tactic과 검증은 기존 직렬 runner의 single-flight 경로로 돌아간다.
- public survival projection은 고정 wake enum과 bounded `wake_to_decision_ms`,
  `decision_to_action_ms`만 추가로 공개한다. 좌표·entity·snapshot은 계속 projection 밖이다.
- 식량 회복은 허기 14~11에서 idle-only, 10 이하에서 planner action을 선점하며, 소지 음식 없이
  health 10 이하이면 hunger와 무관하게 같은 critical recovery를 선점한다. source 탐색은 적이 없고
  지상·비수중일 때만 허용하며, 소지 음식도 적·수중이 없을 때 기존 monitored 경로로 먹는다. 안전한
  지하 섭취는 유지한다. 성숙 작물과 성체 `cow|pig|sheep`만 사용하고 안전 경로로 최대 32블록 안의
  먹잇감에 접근한다. 이동·수확·
  사냥·조리·섭취는 100ms마다 disconnect/수중/알려진 이름 또는 Mineflayer `type=hostile`/health
  하락을 확인하며 timeout 때 끝나지 않은 작업 Promise를 기다리지 않는다. 실제 hunger 또는 안전식량
  증가만 성공·진전으로 인정한다. 주변 source가 없으면 30초 planner search handoff를 주고, 같은
  `#food` 실패의 즉시 재삽입과 고정 3초 선점 loop는 bounded backoff로 막는다.
- 전투는 기존 custom-PvP의 cooldown/critical/strafe/tap을 유지하고 shield-close preset은 방패를
  off-hand에 장착한다. 250ms마다 preset과 terrain/health/장비 안전 문맥을 다시 계산해 달라지면
  공격을 멈추고 P1 판단으로 돌아간다. 누적 health 하락과 실제 실행한 preset의 성공·실패·사망만
  typed episode 증거로 쓰고 fallback·timeout·interrupt·인프라 실패는 학습에서 제외한다. episode는
  최대 256개를 원자적 비동기 JSON으로 저장하며 정상 disconnect와 SIGINT/SIGTERM에서 load 뒤 bounded
  flush한다. 같은 Minecraft 1.21.11/custom-PvP 1.7.16에서 검증 성공 2회 후에만 전술을 승격하고
  연속 실패·사망 2회면 격리하며 새 검증 성공 2회 뒤에만 복귀한다. 경험은 base flee를 fight로
  올리거나 water/unknown/crowd/boss hard fence를 넘지 못한다.
- 회피 controller는 모든 immediate threat의 거리·원거리 LOS cover를 함께 점수화하고, forced sprint
  직선 구간의 머리·발·지지·hazard와 한 블록 높이 변화를 bounded 검사한다. recovery reflex는 2.5초
  heading을 유지하되 더 안전한 경로에는 양보한다. `evelynMovementOwner`가 활성인 동안 idle cleanup과
  `self_preservation`은 control state를 지우지 않는다. 무장 없음·다중 적·원거리 적의 tactical flee는
  P0 reflex 뒤에도 18블록 안전 반경까지 같은 direct recovery sprint를 유지한다.
- Goal Manager는 Overworld의 routine fallback을 먼저 쓰되 최근 비선점 실패 6개와 같은 후보는
  건너뛴다. 후보 없음·비 Overworld·실패 recovery는 기존 subgoal Qwen 경로를 유지한다.
  Compose 배포값은 mode runner의 `MINDCRAFT_MODE_INTERVAL_MS=100`과 ActionManager의
  `MINDCRAFT_INTERRUPT_POLL_MS=100`이며 self-prompt cooldown은 300ms다. 전자는 직렬 mode update
  간격이고 후자는 `requestInterrupt()` 뒤 현재 cooperative promise의 종료 여부를 다시 확인하는
  주기일 뿐 강제 취소나 100ms 이내 중단을 보장하지 않는다. 자율 self-prompt의 command docs만 현재
  subgoal allowlist로 제한하며 사용자 턴은 전체 정상 명령을 유지한다. 매 prompt `$STATS`에서는
  wildcard nearby-block scan을 제거하고 명시적 `!nearbyBlocks`의 기존 전체 관측 의미는 유지한다.
- ActionManager의 mode takeover는 `requestInterrupt()` 뒤 최대 1.2초만 cooperative 종료를 기다리고,
  끝나지 않으면 새 action을 `busy`로 거부해 child를 죽이지 않는다. 명시적 process stop의 10초
  최외곽 fallback은 유지한다. 종료는 idempotent fence를 세운 뒤 `bot.quit`/`bot.end`로 연결을 먼저
  닫고 combat-history load와 writer flush를 기다린 다음 process를 끝낸다. SIGKILL 중 마지막 episode는
  여전히 보장하지 않는다.
- child stdout/stderr는 bounded PIPE reader가 계속 비우되 원문은 즉시 폐기한다. public artifact에는
  고정 category와 exit code/signal/timestamp만 최대 12개 남긴다. readiness는 spawn 이후 3초 연속 연결과
  현재 generation의 fatal child event 부재를 추가로 요구한다.
- 최종 `evelyn-fast-control-voyager:latest` image
  `sha256:f039c6808dd926afb7d1d8de3650670b69a1cfd4572a6bc0d085e9e65a0fc999`는 planner 32개와
  통합 combat/experience/escape/goal/survival 129개, 합계 Node 161개 및 patch/lint/combat/latency
  smoke를 통과했다. Python Mindcraft 계약 31개, Compose config, source↔container 핵심 파일 hash와
  scoped syntax/diff check도 통과했다. 컨테이너는 해당 image로 healthy이며 bot은
  `running=false`, `connected=false`, world lease 비활성 상태다.
- 사용자 승인 live에서 hunger wake→decision 71ms, hostile band wake→decision 58ms와 이후
  `handle_hostile` decision→action 0ms 표본을 확인했다. movement lease 수정 뒤 direct reflex가 근접
  다중 위협에서 피해 없이 안전 반경 근처까지 거리를 넓힌 표본도 있었다. 그러나 뒤의 tactical
  pathfinder 전환이 추격을 다시 허용해 피해와 사망 표본이 발생했고, 이를 continuous direct recovery
  sprint로 수정했다. 마지막 bounded run은 actionable hostile가 투영되지 않아 이 tactical 수정의
  action→effect를 live 검증하지 못했고, 종료 중 추가 피해를 막는 disconnect-first 순서도 offline
  verifier만 통과했다. health-critical food recovery는 동일 최종 image의 몹 spawning을 끈 별도 무위협 fixture에서
  health 10, hunger 15, safe food 0을 만든 뒤 `acquire_food → food_crop_verified`, 두 번의
  `inventory_food_verified`, hunger 15→20과 health 10 유지로 action→effect를 검증했다. fixture bot의
  Docker stop은 20초 안에 끝나지 않아 exit 137로 강제 종료됐으며 OOM은 아니었다. 임시 서버·컨테이너·
  artifact는 제거했다. 운영 월드의 최종 상태가 치명적 저체력이므로 재접속하지 않았다. 운영 월드
  식량 획득, 장기
  생존률, p50/p95, restart 뒤 경험 승격·격리는 아직 live evidence가 아니다.

## 2026-08-13 Speed-first unified LLM turn plan

- Core Router의 `tools[]`, context flags와 `specialist`는 공용 `ContextPolicy`와
  `RouteDecision`으로 정규화된다. top-level 계획만 있고 nested policy가 없어도 계획을
  버리지 않는다. obvious direct/continue와 명시적 safe command는 zero-Router fast path를
  유지하고, 그 외 semantic voice는 Router를 호출한다. 일반적인 `찾아줘/알아봐`만으로는
  web route를 강제하지 않는다.
- Main은 유일한 user-facing finalizer다. Qwen3-14B Q4와 registered skill은 bounded
  untrusted evidence만 반환하고, memory/runtime/tool/Minecraft/vision packet 중 허용된
  section을 낮은 권한 data로 공유한다. Qwen은 `deep_reasoning|minecraft_planning` 선택 시만
  1회 실행하고 6초 실패 시 Main 단독으로 degrade한다. Fast background research와 runtime
  investigation도 Qwen evidence 뒤 Main 1회를 사용한다.
- 정상 호출 예산은 direct Router 0/Main 1, semantic Router 1/optional Qwen 1/Main 1,
  registered search tool/Main 1이다. search의 intermediate Main summary, empty-stream의
  Router/context 재실행, cached state의 pre-Main cognitive refresh, Main 직전 runtime/
  Minecraft 재관측과 non-Minecraft skill의 Minecraft 관측을 제거했다.
- Router 승인 없는 promised-search escalation은 외부 요청을 보내지 않는다. 검색·Qwen·
  skill evidence는 system instruction으로 승격하지 않고 필드·전체 길이를 제한한다.
  Main 합성 실패도 raw 검색/runtime evidence 대신 content-free 실패로 닫는다. benchmark에는
  tool query/reason/evidence 및 allowlist 밖 status를 남기지 않는다.
- 관련 Core/Fast/voice/runtime/memory 회귀 403개가 통과했다. 2026-08-13 당시 RTX 3090의
  관측 free VRAM은 17,646 MiB이고 Qwen GGUF는 8.38 GiB라 정적 용량상 적재 가능했지만
  9823 service는 실행하지 않았다. 이후 2026-08-16 live latency와 GPU1 overlap 근거는
  아래 concurrency benchmark 절에 기록한다. 응답 품질 평가는 별도 과제다.

## 2026-08-16 GPU1 Qwen specialist + STT concurrency benchmark

- historical v1 runner는 당시 fixed 1,773자 Fast Main prompt, Qwen specialist 256-token 요청과
  repository의 1.64초 PCM16 STT fixture를 같은 barrier에서 시작하고 physical GPU1을 50ms마다
  수집했다. prompt/audio는 hash와 크기만 남기고 raw audio와 transcript는 기록하지 않았다.
- 2026-08-16 사용자 승인 뒤 Main GPU0, Qwen specialist+STT physical GPU1의 실제 1 warmup+5
  measured overlap은 Fast Main TTFT p95 422.6ms, Qwen p95 2,233.2ms/timeout 0, STT final p95
  626.1ms, GPU1 min free 10,284MiB/peak utilization 98%, GPU sample 102와 error 0으로 통과했다.
  테스트 컨테이너와 Docker Desktop은 종료했고 GPU1 사용량은 0MiB로 복귀했다.
- 2026-08-27 current source의 P0-4 v2 mode는 old/new STT image를 각각 warmup 2+measured 20으로
  고정하고 raw baseline SHA, clean source, unique Compose project, exact container/image/model/runtime,
  physical GPU, read-only mount, STT cache·dependency identity의 pre/post stability를 fail-close한다.
  별도 private positive 40/negative 10 batch+stream runner는 aggregate와 고정 실패 코드만 남기고
  기본적으로 bound manifest/audio 51개를 원자적 quarantine 뒤 삭제한다.
- 진단 Compose는 Main/Qwen을 loopback 9820/9823에만 열고 STT cache의 `hub/`만 read-only/offline으로
  mount하며 log는 attempt-owned named volume을 쓴다. source `d95ea896...5c6c3`에서 old/new 2+20을
  오류 0으로 실행했다. old report SHA-256 `5309ba0e...2d5e`의 STT/Main/Qwen p95는
  `728.5/18.6/2270.3ms`, min free는 `10,294MiB`였다. 새 image `sha256:afece0d2...29c5`와
  package-set `c7518d52...e519`를 결박한 report SHA-256 `cb72eb22...14b1`의 같은 p95는
  `158.2/24.9/2030.3ms`, min free는 `6,144MiB`였다. 독립 비교와 pre/post 환경 안정성이 통과했다.
- exact cleanup 뒤 owned container/network/volume은 `0/0/0`, GPU1은 `0MiB` 연속 3회, production은
  OFF였다. assembled private corpus는 absent(`0/50`)이고 accepted staging candidate는 후술하는
  `10/50`뿐이므로 corpus, cancel/successor, cold restart, promotion과 P0-5는 차단돼 있다. 이 2+20은
  full P0-4 promotion 증거가 아니다.
- 2026-08-28 guided Discord capture는 transport/shape `10/10` 뒤 자동 model diagnostic에서 FAIL했다.
  사용자의 명시적 선택은 원본 FAIL report hash와 exact capture marker hash를 함께 기록한 별도 receipt로
  이 10개만 미래 `domain-discord-pcm` 후보로 accepted했다. legacy diagnostic v1은 marker digest가 없어
  same-run 암호 결박이 false이고, pairing authority는 사용자 지시다. 후속 diagnostic v2는 marker SHA를
  직접 포함한다. receipt는 production promotion false이며 나머지 40개와 이후 자동 gate를 대체하지 않는다.
- benchmark report는 production admission, Windows launcher, Discord와 Local Voice가 읽지 않는다.
  historical v1 결과는 revised image A/B·corpus·restart 증거로 재사용하지 않는다. 실행 계약은
  [[GPU1_CONCURRENCY_BENCHMARK]]가 소유한다.

## 2026-08-21 Main LLM TTFT source optimization

- Fast Main의 고정 system prefix를 계약 문구를 유지한 채 1,773자에서 938자로 줄였다. startup
  warmup은 voice/Discord의 `SYSTEM_PROMPT`와 Fast Control의 실제 고정 prefix를 각각
  `cache_prompt=true`로 요청한다.
- voice/local prompt는 저장 history를 지우지 않고 첫 system message와 최근 non-system 8개만
  모델에 보낸다. memory/runtime context는 정책 또는 tool decision이 요구한 턴에만 붙이며,
  screen evidence 턴의 capability contract는 유지한다. Fast Control의 Main HTTP 연결은 process
  수명 동안 재사용하고 cleanup에서 닫는다.
- llama.cpp Main launcher와 Compose는 `GGML_CUDA_GRAPH_OPT=1`, batch 2048, ubatch 1024,
  prompt-cache RAM 8192 MiB, cache reuse 256을 명시한다. benchmark는 서버의 processed/cached
  prompt token, `prompt_ms`, prefill/prediction throughput과 cache-hit ratio를 client TTFT와 함께
  기록한다.
- 관련 Core/Fast/voice/runtime/Compose/benchmark 회귀 251개와 production Python compile,
  scoped whitespace 검사가 통과했다. Docker/GPU/마이크/Discord는 실행하지 않았으므로 실제
  TTFT 개선폭과 ubatch 1024의 target-GPU 이득은 아직 live A/B 근거가 없다.

## 2026-08-14 Minecraft 공개 자산 재사용과 20-cell 전투 matrix

- 공식 공개 구현을 비교한 결과, 현재 `@nxg-org/mineflayer-custom-pvp@1.7.16`보다 검증된
  Mineflayer-native drop-in 전투기는 찾지 못했다. custom-PvP를 1:1 actuator로 유지하고 이미
  함께 로드되는 `mineflayer-tracker`의 도달시간 기준 투사체 정보를 P0 방어에 연결했다. 이미
  off-hand에 든 방패는 즉시 사용하고, 방패가 없으면 기존 corridor·hazard·cover 검사를 통과한
  회피를 시작한다. live arrow 단독 effect는 아직 별도 검증하지 않았다.
- Odyssey/Voyager는 low-level kill wrapper 대신 위협·장비 조합 커리큘럼과 검증 성공 뒤 skill을
  승격하는 패턴만 채택했다. `tools/voyager/combat_matrix.py`는 격리 Minecraft 1.21.11 서버에서
  single zombie, single skeleton, zombie+skeleton, three zombies, creeper를 무방어/방어와 낮/밤으로
  교차한 20개 cell을 fresh runtime/history로 자동 실행한다. production 정지, exact image/version,
  arena/loadout/time/tagged count, terminal episode와 owned cleanup을 fail-closed 검증한다.
- 첫 full run은 5/20이었다. evaluator의 외부 안정 관측과 실제 100ms controller가 달랐고, 전술
  도주가 18m 첫 crossing에서 controls를 끄면서 추격 hostile가 즉시 재진입했다. 전술 도주는
  24m 여유를 먼저 확보한 뒤 18m 밖 연속 2초를 같은 movement lease에서 확인하고, 재진입하면
  재회피하도록 수정했다. 연속 P0 reflex는 각 1,250ms 상한을 따로 검사하고 총시간을 P1 지연에서
  제외한다. 안전하게 사라진 creeper만 flee의 hostile-count 감소를 허용한다.
- 최종 live matrix report
  `runtime_artifacts/validation/combat_matrix_batch_full3_pass_20of20_20260814/report.json`은 20/20,
  infrastructure failure 0, death 0, cleanup verified다. 관측 P0 event→control은 0~1ms, P0 뒤 P1
  판단은 11~90ms, decision→action은 0~1ms였다. 전체 기록 피해는 약 4.76 health, cell 최대 2였고
  three-zombie 네 cell은 모두 피해 0으로 성공했다. 전용 컨테이너와 25565/25573 listener는 종료 뒤
  0개였다. 운영 Minecraft는 시작하거나 재접속하지 않았다.
- 최신 `evelyn-fast-control-voyager:latest`
  `sha256:499dbe8634c1df82bf922ea370ab1856288cf06a4bf153145e98799a28a5d50b`는 planner 32개와 통합
  combat/experience/escape/goal/survival 149개, 합계 Node 181개 및 lint/combat/latency verifier를
  통과했다. Python Mindcraft 계약 31개와 matrix 22개, 합계 53개도 통과했다.
- 이 결과는 roofed arena의 고정 20개 전투·도주 effect 증거다. 자연 채집·식량 고갈·대피소·여러
  day/night·희귀 지형·disconnect/restart 경험 승격을 포함한 장기 생존률이나 통계적 p95는 아니다.

## 2026-08-14 Minecraft 투사체·저체력 근접전·20분 자연 생존

- actual 1.21.11 arrow-only smoke는 hostile 0인 상태에서 projectile P0를 0ms에 시작하고 이미 든
  방패로 blocked damage 30을 확인했다. health damage·death·runtime error는 0이고 owned cleanup도
  통과했다. 근거는
  `runtime_artifacts/validation/combat_matrix_batch_projectile6_pass_20260814/report.json`이다.
- 근접 hostile P0가 반복돼 full tactical P1을 굶기던 경합은 단일 pending handoff와 50ms keeper로
  닫았다. P1 callback 입장 전에는 정확히 하나의 bounded P0 또는 재검사 timer를 유지하고, 실제
  ActionManager의 callback 없는 busy 결과·projectile 경합·disconnect에서도 stale handoff를 남기지
  않는다. 저체력 emergency melee가 입장한 뒤 legacy `self_preservation`의 일반 `moveAway(20)`가
  중단시키던 충돌은 `mode:evelyn_survival`의 `handle_hostile` 동안 그 branch만 억제했다. 화재·용암·
  익사·낙하 블록 branch는 이전 우선순위를 유지한다.
- 체력 10·무갑옷·iron sword·단일 좀비 격리 smoke는 P0 0ms, 첫 reflex 1,100ms, P1 잔여 11ms,
  action admission 0ms 뒤 `melee`를 4,508ms에 verified success로 끝냈다. P1 피해·사망·남은 hostile은
  모두 0이며 cleanup도 통과했다. evaluator는 고정된 P0 episode 수 다음 첫 episode만 P1에 귀속해,
  뒤이은 P0 death가 P1 preset을 덮지 않는다. 근거는
  `runtime_artifacts/validation/combat_matrix_emergency_zombie_pass_20260814_201635/report.json`이다.
- 최신 `evelyn-fast-control-voyager:latest`
  `sha256:d7808a72b10cb2e1fe97347db89ec4558f6b7aa0399d14f07b8565ade368c14d`는 planner 32개,
  통합 Node 183개와 runtime-lint/combat/latency verifier를 통과했다. combat-matrix와 long-soak
  Python 회귀는 60개가 통과했다.
- normal difficulty, natural time/weather/mob spawning/regeneration을 유지한 fresh-world 1,200초 soak는
  1,086/1,086 connected-fresh sample, 최저 health/hunger 20, death·critical·runtime error 0으로 통과했다.
  120초 안에 실제 이동 424cm, log 6개, wooden pickaxe 제작·인벤토리 후조건을 확인했고 종료 뒤
  container·Java·port cleanup도 검증했다. 근거는
  `runtime_artifacts/validation/long_survival_soak_pass_20260814_203758/report.json`이다.
- 이 soak에서는 shelter가 한 번 실패했고 dirt 채굴·획득·사용, 전투와 식량 recovery 노출은 0이었다.
  당시 vertical 23m·actionable false인 hostile가 decision admission에는 제외되지만 shelter 내부 raw
  24m guard에는 포함되는 판정 불일치가 `shelter_context_unsafe`의 가장 강한 원인이다. 상세 verification은
  public artifact에서 제거돼 exact terminal code는 확증하지 못했다. 따라서 20분 자연 안정 유지와
  자율 bootstrap은 live 증거지만 shelter success, 식량 고갈 회복, 여러 day/night, restart 뒤 경험
  유지와 운영 lease/Discord functional-readiness E2E는 아니다.

## 2026-08-15 Minecraft 길찾기 호출·복구 업그레이드

- `mineflayer-pathfinder` dependency는 유지했다. 일반 이동은 공간적으로 분리된 최대 4개 block
  후보를 `GoalCompositeAny`로 평가하고, entity는 `GoalFollow`로 추적하며 실제 도착 거리로 성공을
  판정한다. 비파괴 preview의 `partial`은 실패로 버리지 않고, 실제 `NoPath|Timeout`일 때만 파괴
  허용 movement를 한 번 재시도한다.
- shelter는 반경 8의 bounded 유효 plan 전체를 하나의 composite goal로 전달하고 실제 도착 후보를
  현장에서 다시 검증한다. path helper는 같은 goal의 `stuck` 두 번째 발생에서 중단하고 timer와
  listener를 정리한다. projectile/escape controller가 새 goal을 세운 경우 이전 path cleanup은 그
  goal을 정지하거나 지우지 않는다. 먹잇감 접근의 false resolve도 사냥 성공으로 진행하지 않는다.
- pathfinder의 빈 success는 현재 block 또는 pathfinder의 한 칸 위 시작점이 goal일 때만 성공으로
  처리하며 core `stateGoal`도 같은 규칙으로 정리한다. navigation/pathfinder 회귀 20개와 전체
  survival-mode 84개가 통과했고 Docker build에 두 navigation test를 gate로 추가했다. 전체 11개
  overlay patch chain은 clean apply됐다.
- 실제 지형에서 추가로 드러난 제작 결과 정착, 작업대 배치, bootstrap 재개, 후보 cluster 회전,
  비파괴 접근, drop near→exact 회수, 수직 줄기 batch의 block-first 처리와 무효 dig 재시도를 같은
  patch chain 안에서 닫았다. 전술 도주는 직선 corridor가 없으면 같은 lease에서 pathfinder로 fallback하고,
  near/critical 전환에서는 안전 corridor가 생기는 즉시 sprint로 재승격한다. 식량 action은 actionable+LOS
  24m monitor와 full-hunger reserve 보유 계약을 사용한다.
- 최종 image gate는 combat+survival 217개, navigation+pathfinder 44개, planner 32개를 통과했다.
  격리 navigation matrix는 4/4 cell에서 통나무 3개·곡괭이·death/error 0·cleanup을 확인했다.
  fresh-world natural soak는 1,200.5초, death/runtime error/critical 0, 최저 health/hunger 17,
  최종 health 20·hunger 17, verified goal 5·stuck 0으로 통과했다. 근거는 각각
  `runtime_artifacts/validation/navigation_matrix/report.json`과
  `runtime_artifacts/validation/long_survival_soak/report.json`이다. 운영 Minecraft는 재접속하지 않았다.

## 2026-08-16 Minecraft shelter/restart bounded 검증

- `tools/voyager/shelter_restart_scenario.py`는 운영 25565와 `evelyn-mindcraft`를 fail-closed guard하고,
  전용 25575·새 world·단일 owned container에서 shelter dirt 18개, 두 night→day cycle, SIGTERM exit 0,
  같은 runtime의 combat-experience exact prefix와 restart 뒤 새 verified append를 순서대로 판정한다.
- 첫 fresh 실행은 `shelter_site_unbuildable`을 재현했다. bounded shelter site 검색을 반경 8에서 후보가
  없을 때 한 번만 16으로 넓힌 뒤 해당 code는 사라졌다. 승인된 한 차례 full 재실행에서는
  `shelter_return_failed` 뒤 `shelter_gather_timeout`, dirt 0, navigation partial/timeout 고착이 확인됐다.
  저장된 fresh world를 offline으로 읽으면 최종 위치 64블록 내 dirt/grass가 46,973개이고 최근접은
  1.22블록이므로 재료 부재가 아니라 일반 수집기의 단일 경로 고착이다.
- shelter는 보이고 닿으며 `safeToBreak`인 dirt/grass를 먼저 한 개씩 수집하고 매번 inventory 증가를
  검증하도록 최소 수정했다. 추가 승인 run은 첫 shelter window에서 composite 후보 이동 `NoPath`가
  4회 발생했고, 첫 night→day cycle 1회 뒤 676.5초에 evaluator가 `scenario_infrastructure_error`로
  종료됐다. 세 server query가 모두 끝난 뒤 monitor 갱신만 누락된 실행 순서와 파일 timestamp로
  Windows atomic report 교체 `OSError`를 확정했다. cleanup은 통과했다.
- composite `NoPath` 뒤 가까운 유효 shelter site 최대 4개를 exact goal로 재시도하고, atomic report
  교체의 `PermissionError`만 50ms 간격 최대 3회 재시도하도록 최소 수정했다. combat/survival 221개,
  navigation/pathfinder 44개, Voyager 도구 80개와 19 subtests, lint/combat/latency 및 Docker image
  build gate가 통과했다. 최신 image는
  `sha256:52d0012ccabbc9fbc4289fea988870484086e3969b3f48e310293f054a212ef1`이다.
- 후속 live telemetry는 1.21.11 `findBlocks`의 positionless palette probe를 위치 검사로 거부해 일반
  수집 후보가 0이 되던 경로, collect/return pathfinder가 dirt 18개를 scaffolding으로 소비하던 경로,
  채굴 item을 따라 구덩이에 내려간 뒤 재료가 있어도 shelter center로 복귀하지 못하던 경로를 각각
  확정했다. palette probe에는 이름 검사만 적용하고, shelter 전용 movement는 scaffolding/tower를 끄며,
  각 direct collect 뒤 같은 center로 복귀하도록 최소 수정했다. verified dawn exit만 증가시키는
  process-lifetime `shelter_success_count`와 content-free gather failure subtype도 evaluator에 투영한다.
- Docker SIGTERM은 PID 1 launcher에만 도착하고 실제 agent child의 경험 flush를 호출하지 않아 5초와
  15초 모두 SIGKILL/exit 137이 됐다. launcher가 기존 `Mindcraft.shutdown()`을 SIGTERM/SIGINT에서 한 번만
  호출하도록 연결한 뒤 isolated stop은 2,264ms, agent checkpoint와 child/container exit 0으로 통과했다.
- 의도적으로 두 번 만든 husk combat가 다음 natural cycle의 health/food 상태를 오염시키지 않도록 각
  controlled encounter에 iron sword와 cooked beef 2개만 명시적으로 제공한다. 한 개 fixture의 두 full
  run은 recovery 뒤 food search로 shelter를 이탈해 두 번째 밤 death를 재현했고, 두 개 fixture는 실제
  `eat_inventory_food`와 기존 enclosure 재사용을 거쳐 조건을 낮추지 않고 통과했다.
- 최종 content-free report `runtime_artifacts/validation/shelter_restart_scenario/report.json`은 1,961.2초,
  seed 5,031,407, shelter dirt 사용 36, 자연 night→day 2회, SIGTERM graceful restart, combat-experience
  prefix 1 보존과 post-restart 9 append, connected-fresh coverage 1.0, death/runtime error 0, final
  health/hunger 17.17/15, cleanup verified, `passed=true`다. 운영 25565와 `evelyn-mindcraft`는 전 과정에서
  닫혀 있었다. 최종 image `sha256:73121f284152ca4cd5223b98101de0e2ee38ad395b033577625e200bbb550ad2`는
  Node combat/survival 227개, navigation 44개와 lint/combat/latency gate를 통과했다.
- GoalManager의 실제 dragon kill 순서는 awaited `!attack(kill=true)` 안에서 `entityDead`가 먼저
  발생하고 그 뒤 action result가 기록된다. 성공한 autonomous exact dragon attack의 before→after
  defeated count가 증가하고 현재 predicate가 완료됐을 때 result 경계에서도 ultimate completion을
  latch해 이 순서의 누락을 닫았다. 기존 event-after-result 경로와 failed/cancelled latch 차단은
  유지한다. ActionManager의 고정 generic-exception marker도 실패로 분류해 combat latch를 무장하지
  않으며, completed 상태에서는 verified exact `!endGoal`만 gate를 통과해 SelfPrompter를 한 번 멈춘다.
  GoalManager 40개와 실제 registry를 포함한 planner 34개가 통과했다. live dragon combat은 실행하지 않았다.
- lease-bound food action에서 아이템 증가 뒤 command 반환 전 periodic `update()`가 subgoal을 먼저
  완료 처리해 world-effect candidate를 잃던 순서를 재현했다. exact binding이 있고 ActionManager가
  실행 중일 때만 passive predicate 소비를 미루며 snapshot 갱신·publish와 일반 autonomy 동작은 유지한다.
  action result가 같은 false→true 전이를 단일 소유해 gateway timeout의 false failure를 막는다.
- gateway poll은 same-binding candidate를 먼저 읽되 projector가 exact lease·binding·process·telemetry
  freshness·functional readiness를 다시 검증한다. candidate가 없으면 active guard를 직접 재확인하며,
  action이 readiness를 한 번 관측한 뒤 disconnect/stale이 되면 180초 timeout까지 `running`으로 두지 않고
  fixed failure→binding disarm→verified runner stop→lock release로 끝낸다. 첫 readiness 전 startup만 기존처럼
  bounded 대기한다.

## 2026-08-16 음성 restart·listener·local-memory 연속성

- checkpoint restore 직후 canonical voice-user session 전체에서 room별 유일한 최신 활동을 먼저
  선택하고 exact user binding과 미만료 TTL을 만족할 때만 process-local room owner를 복구한다.
  만료·불일치 최신 row와 동률은 이전 owner를 되살리지 않으며, hard-exit 회귀는 wake 없는 owner
  follow-up만 `owner_followup`으로 수락하고 다른 사용자를 거부한다.
- 저장 음성 채널의 `on_ready` 복원은 fixed transient 실패만 generation-fenced 3회×0.5초 재시도한다.
  receive/decrypt/utterance task의 예상 밖 종료는 exact listener generation을 정리하고 input lease를
  해제한 뒤 같은 guild/channel/client의 단일 owner task가 replacement generation까지 합쳐 최대
  3회만 재무장한다. explicit stop·stale task는 재무장하지 않는다.
- self voice-state의 외부 채널 이동이 listener를 명시적으로 정리한 뒤 첫 exact-target ensure에서
  transient 실패해도 같은 guild/client/channel fence 안에서 최대 3회×0.5초 재시도한다. 더 최신
  이동은 stale retry를 중단하고 persistent 실패는 bounded error로 끝낸다.
- non-resumable Discord READY가 live voice registry를 비운 뒤 replacement가 등록돼도 늦게 종료되는
  orphan client는 current registry owner가 `self`일 때만 inherited key cleanup을 수행한다. 그렇지
  않으면 자기 listener task·sink·decoder·queue와 input lease만 정리해 replacement를 보존한다.
- Discord voice-server 내부 재연결은 새 WebSocket을 handshake 전에 custom gateway에 bind한다.
  UDP socket identity가 같아도 non-resume session이면 이전 SSRC·DAVE map, decoder/utterance queue와
  listener lease를 정리하고 기존 bounded rearm 경계에서 fresh key/session을 적용한다. 이전 socket의
  늦은 JSON/binary frame은 exact socket identity가 아니면 새 상태를 바꾸지 않는다.
- Main direct local-speaker의 synthetic guild `0`은 memory owner validator에서 계속 거부한다.
  대신 write와 recall에 Fast/Control Page와 같은 startup-fixed canonical owner를 명시 전달하며,
  configured 양수 guild/user가 완전할 때만 두 surface가 같은 principal을 쓴다. token은 request
  repr·metrics·checkpoint에 투영하지 않는다.
- guild 기억 초기화는 대상 guild의 normal/batch writebehind와 모든 guild의 live vault maintenance가
  끝나기 전에는 durable revocation이나 runtime 삭제를 시작하지 않는다. normal writebehind는 scope가
  먼저 해제돼도 content-free guild task key로 완료까지 남고, 취소된 vault outer task는 실제
  `to_thread` worker를 shield/drain한 뒤 cancellation을 재전파하므로 worker가 전역 index를 쓰는 동안
  reset admission이 열리지 않는다.
- text/voice의 명시적 `/remember`도 실제 worker가 끝날 때까지 같은 reset task registry에 남는다.
  새 explicit note는 owner와 별도의 opaque guild/local reset scope를 저장한다. durable reset marker
  안에서 exact guild scope의 전역 vault note를 기존 tombstone 삭제 계약으로 제거하고 다른 guild/local
  note를 보존한다. canonical binding은 renamed·tag/contract 손상보다 우선하며 invalid UTF-8과 nonlocal
  scope-less legacy는 mutation 전에 fail-closed한다. 삭제 뒤 새 process recall에서도 target note가
  보이지 않음을 검증했다.
  Discord text ingress reset은 exact guild의 durable journal 모든 phase를 제거하고 epoch를 올린다.
  claim 뒤 history·followup·TurnScope 등록까지 같은 reset lock에서 current epoch를 확인하며, crash 뒤에는
  checkpoint가 없어도 unfinished ledger를 먼저 replay한다. active marker는 wall-clock 역행과 무관하게
  owner restore와 read-only cross-surface reader 양쪽에서 해당 guild 전체 복원을 막고 다른 guild와
  local ingress/session은 보존한다.
- search-followup recovery purge도 persistent vault/ingress/runtime reset과 같은 live+startup callback에
  들어간다. marker 직후 target guild를 닫고 runtime task·TurnScope·history와 ingress epoch를 먼저
  정리한다. journal 부분 write는 ordinary mutation을 차단한 `error/write_failed`로 유지하고 reset
  재시도만 authoritative reload·one-generation roll-forward를 수행한다. 반복 transient 실패 뒤에도
  같은 process의 다음 reset이 수렴하며 structural 손상은 fail-closed한다. continuity flush/finalize까지
  성공한 뒤에만 exact guild를 다시 열고, 실패 응답은 private cause 없이 fixed code로 제한한다.
  일반·복구 search task와 same-session 교체 전 task는 guild-prefixed owner/drain key로 실제 종료까지
  남으므로 Discord send await 중 reset은 mutation 전에 `search_background_work_inflight`로 닫힌다.
- Discord `자율시작`은 admission guild epoch를 route await 뒤 grant 직전과 executor connect 뒤 runtime
  commit 직전에 재검증한다. same-guild reset으로 stale하면 disconnect·grant revoke 후 고정 재시도 답변을
  내며 enabled state나 autonomy loop/world effect를 만들지 않는다. 다른 guild reset은 영향을 주지 않는다.
- reset mutation은 target guild voice-ingress epoch를 먼저 증가시키고 target partial-STT cache와
  speculative policy를 지운다. 이전 epoch의 queue/buffer, startup wait, partial/full/wake STT와
  speaker verification/TTS interrupt는 raw debug 저장·bad-audio 재생성·accepted-turn checkpoint 전에
  fail-closed한다. blocking partial worker는 shared state를 쓰지 않고 event loop 복귀 후 exact current일
  때만 cache/partial/committed/speculative state를 동기 commit한다. local synthetic guild `0`은 epoch
  조회에서 유효하고 reset increment는 양수 guild만 받는다.
- Local Bridge mic ON은 start thread를 cancellation 뒤에도 수거한 다음 exact service stop과 physical OFF를
  검증한다. 실패하면 desired/revision은 보존한 fixed failed state를 내고 stop 검증 실패는 fail-safe exit로
  닫으므로 OFF 응답 뒤 late start가 캡처를 되살리지 않는다.
- Discord input lease의 첫 acquire가 server commit 뒤 취소되거나 응답이 불명확해도 shield/drain 뒤 exact
  lease를 release하고 cancellation을 재전파한다. 마지막 token release가 commit 전·후에 취소돼도 단일
  idempotent retry가 server owner를 비워 Local Mic acquire를 다시 열며, 이미 listener가 있는 중복 acquire와
  고정 conflict/unauthorized 거부는 현재 lease를 해제하거나 추측성 cleanup을 만들지 않는다.
- Discord I/O 157개, 전체 voice 744개(skip 5), memory 312개(skip 1), ingress/reset focused 104개가
  각 offline 묶음에서 통과했다.
  실제 Discord gateway/audio 장치, process crash/restart와 Local mic 실사용은 실행하지 않았다.
- 이 증분과 memory reset/vault worker·Minecraft readiness/dragon event-order, voice lease/reconnect/mic
  rollback, cognitive privacy를 포함한 CI-equivalent 전체 3,710개(skip 22)가 실패 없이 통과했다.

## 2026-08-17 취소 재생·자동 기억 reset·dragon restart 인과

- Discord single-source TTS가 `vc.play()` 뒤 callback을 기다리는 동안 TurnScope/guild reset으로
  취소되면 exact current source일 때만 `vc.stop()`을 호출하고 cancellation을 재전파한다. replacement
  source는 보존한다. 전체 voice 748개(skip 5)와 reset/Discord 인접 74개가 offline 통과했다.
- automatic daily는 `daily/guild-<id>/<date>.md`, deterministic episode는
  `episodes/guild-<id>/...`, semantic note는 guild-prefixed storage key로 분리하며 모두 같은 opaque
  reset scope와 exact source reference를 쓴다. reset은 same-scope derivation graph를 leaf-first로
  tombstone하고 explicit/automatic/semantic/recognized legacy target만 삭제한다. 혼합 shared daily,
  extra/cross-guild reference, unknown legacy/local file, scope/path 재결박과 invalid UTF-8은 mutation 전에
  닫는다. raw/legacy tree는 `st_dev/st_ino`·SHA-256 snapshot과 각 unlink 직전 identity를 재검증하고
  broad recursive delete를 쓰지 않는다. partial tombstone 뒤 repair/retry, restart recall/index/cache,
  다른 guild/local 보존과 late injection 회귀를 포함해 memory 336개(skip 1), focused 44개,
  reset/Discord 61개와 독립 86개가 통과했다.
- 성공했지만 nonterminal인 autonomous dragon attack의 `lastDragonCombatAt`은 child restart에서
  복원하지 않는다. 검증된 ultimate completion은 계속 durable하며, completed 상태의 verified
  `!endGoal`만 survival recovery와 unsafe-unarmed gate를 지나 SelfPrompter를 정확히 한 번 멈춘다.
  GoalManager 42개와 actual registry 회귀가 통과했다. live Discord/audio/Minecraft는 실행하지 않았다.

## 2026-08-15 단일 음성 입력 owner와 상태형 한국어 ASR

- Bot API는 `runtime_artifacts/voice_input_lease/owner.json`의 opaque owner state로 Local Mic과
  Discord voice 중 하나만 캡처하도록 중재한다. Local ON은 consent/fence 검증 뒤 control publish 전에
  lease를 얻고 exact OFF revision/action의 `applied + captureStopped` ACK 뒤에만 해제한다. Discord는
  lease 뒤에만 `listen()`하며 receive/decrypt/utterance task 종료 뒤 해제한다. channel move/rearm은
  process-global refcount로 소유권을 이어 간다. same-instance inactive 관측, 인증·persist·release 실패,
  stale/unknown owner는 새 source를 열지 않는다.
- STT 서비스는 `Qwen3ASRModel.LLM` 한 개를 상주시켜 기존 batch endpoint와 bounded
  `start/chunk/finish/cancel` 세션을 함께 제공한다. 세션은 raw 16 kHz PCM16, 순번, 최대 30초,
  60초 TTL, 최대 4개와 process-global inference lock을 사용한다. cancel된 lock waiter는 모델 호출 전
  live session identity를 다시 확인하고, finish는 추론 완료 전 capacity slot을 반납하지 않는다.
- Local Mic은 capture-time 16 kHz mono PCM16을 최대 500ms 단위로 background worker에 보내고,
  non-empty·stable-prefix-consistent final만 기존 Local admission에 전달한다. 오류·충돌·backlog는
  기존 batch를 최대 한 번 사용하며 stale epoch와 거절된 barge-in은 stream/future를 폐기한다.
- Discord는 현재 endpoint 뒤 완성 PCM을 resident 모델의 batch endpoint에 정확히 한 번 보내 정상
  경로의 중복 wake/after-the-fact partial/full/rescore를 없앴다. transport 손상 표식이 있으면 기존
  독립 wake confirm을 유지한다. packet-time Opus decode는 아직 구현하지 않아 Discord capture-time
  partial은 현재 상태가 아니며, true session streaming은 Local capture-time 경로에만 있다.
- 2026-08-21 Local playback owner claim은 더 이상 barge-in source를 게시하지 않는다. exact owner/token의
  실제 첫 PCM write가 성공한 뒤에만 source를 게시하므로 blocked/failed write와 다음 sentence의 새 token은
  이전 turn 단위 playback 상태로 positive interrupt를 만들지 못한다.
- Local과 buffered STT runtime은 remote start 중 취소돼도 blocking start 결과를 수거하고 반환된 stream ID를
  bounded cancel한다. Local start 응답에 유효한 ID가 있으나 sampling/profile/sequence 계약이 틀린 경우도
  state 생성 전에 해당 session을 cancel한다.
- client batch STT의 caller timeout/cancellation 뒤에도 이미 시작한 blocking thread가 반환할 때까지 shared
  inference lock을 유지하고 done callback에서만 해제한다. physical client가 영구 정지하면 lock도
  fail-closed로 남으며 실제 network/driver/GPU hard stall은 live 검증 전이다.
- ordinary STT/wake/final 관측은 transcript 대신 chars, latency, 고정 reason/error type만 남긴다.
  `STT_FULL_RESCORING_ENABLED` 기본값은 false이며 Local streaming과 Discord single-final 재사용은
  각각 `LOCAL_BRIDGE_STT_STREAMING_ENABLED=false`, `STT_STREAMING_ENABLED=false`로 rollback할 수 있다.
- Docker source는 `vllm==0.14.0`, Torch/Torchaudio 2.9.1+cu128을 사용한다. live-loaded revised image의
  actual engine은 max model length `8192`, GPU memory utilization `0.35`, max sequence `1`, audio per
  prompt `1`이고 입력 상한은 `30초`였다. mismatch는 startup을 fail-close한다. headless GPU1 2+20은
  검증했지만 private corpus, Discord gateway와 마이크는 시작하거나 검증하지 않았다.
- 2026-08-28 Discord corpus 도구는 텍스트 phrase별 prompt 이후 시작한 한 발화만
  shape/activity/duplicate gate로 저장하고, exact 10개 뒤 pinned STT에서 각 WAV를 한 번만 진단한다.
  평가 STT는 admission·자동 retry·삭제·승격에 관여하지 않으며 최종 상태 알림은 cleanup 뒤 text-only다.
  승인된 guided live capture는 canonical `10/10`과 정상 길이 분포를 통과했지만 aggregate-only 모델 진단은
  similarity `8/10`, order `9/10`, normalized/entity-action exact `0/10`으로 실패했다. 이는 transport
  live 근거이지 private corpus나 revised STT promotion 근거가 아니다. [[worklog/2026-08-28]]

## 2026-08-28 P1-4 private archive source/offline 상태

- 기본 OFF인 private archive source 기반이 구현돼 있다. `bot_api`가 C: SQLite 기준 원본, D: replica,
  외부 anchor의 단독 writer이며 generation·idempotency·OS lease·HMAC 무결성, schema v1 검증 후 v2 migration,
  30일 oldest-first retention과 replica reconciliation을 소유한다. archive root는 Git·`docs/`·일반 runtime
  memory와 분리되며 raw audio, partial STT, credential, 내부 prompt/tool 원문은 저장 대상이 아니다.
- Discord shared session은 operator/guild/text·voice channel/boot generation/TTL에 결박되고 안내·현재 동의가
  없거나 mute/deaf/suppress/gateway unknown이면 eligible voice participation과 STT admission을 닫는다.
  durable voice transition과 `[start,end)` presence/eligible interval을 기록한다. 일반 사용자는 자기 text/final
  STT와 직접 연결된 Evelyn reply·task·Minecraft 결과만 guild ephemeral command로 열람·삭제한다.
- 관리자 전체 열람은 ordinary Control Page와 분리한 8800 loopback HTTPS origin만 제공한다. UAC-signed host
  attestation, 등록 Discord ID의 대소문자 구분 영숫자 4자리 DM OTP, 5분 absolute/2분 idle session,
  Windows lock/logout·restart revocation을 요구한다. record·participation·voice transition·legal-minimal page는
  SQL keyset 100행과 900 KiB cap을 사용하며 browser cursor는 session/kind/generation/180초에 결박된 opaque
  handle이다. legal-minimal projection은 이름과 실제 UTC 발생시각만 내보내며, user/admin 삭제 시 아직
  30일이 지나지 않은 사건에만 생성한다. 원래 발생시각+30일에는 oldest-first audit→VACUUM→D: 복제 경로로
  지우고 retention 삭제나 이미 만료된 직접 삭제에서는 새로 만들지 않는다.
- 사용자 삭제는 기간 생략 시 해당 principal 전체 범위이며 shared record에서는 그 사용자 부분을
  `사용자의 요청으로 삭제됨` tombstone으로 바꾼다. C:/D:와 파생 sink 정화가 모두 증명될 때만 완료한다.
  17개 필수 sink의 logical owner route, process prompt/tool cache exact metadata, memory/cognitive/ingress/search/
  STT/TTS writer·task fence가 연결돼 있다. Bot API는 동일 request/generation/scope의 remote receipt 전부와
  fresh negative recall이 확인될 때만 완료한다. 완료된 exact process lineage는 process 종료까지 retired로
  유지한다. 불완전 lineage, attribution 없는 legacy/global cache, 손상·누락·unsafe memory root는 만들거나
  일부 변경하지 않고 `manual_review/local_cleanup_pending`으로 둔다.
- Minecraft connect/goal/disconnect command root가 grant currentness와 최종 verified world effect/result까지
  parent lineage로 이어진다. local text는 turn/session/memory-owner/evidence lineage를 가지며 파생 답변·task가
  부모 lineage를 상속한다. Discord mode에서는 local microphone service와 늦게 도착한 local segment를 모두
  닫고 Discord voice만 받는다. exact gateway SSRC→user ID가 권한·소유권 근거이며 현재 `display_name`은 표시용
  owner name snapshot이다. mapping 없는 lone-member/current-speaker 추정은 제거했다. local-private microphone은
  archive ON에서 별도로 fail-closed하며 Discord mode의 완료 gate가 아니다.
- 수정 후 변경 영향 전체는 `1061 passed, 1 skipped, 203 subtests`다. canonical 1차 실행은
  `4969 passed, 23 skipped, 1502 subtests, 8 failed`였고, 수정 뒤 그 실패 파일·인접 경로
  `58 passed, 6 subtests`가 통과했다. 같은 clean canonical은 반복하지 않았다. 변경 Python compile과
  JS/PowerShell/Compose/diff 검증은 통과했다. 후속 Discord/local mic 경계는
  `125 passed, 15 subtests`와 Python compile/diff check를 통과했다. Discord gateway, microphone, Minecraft, Docker service 및
  실제 C:/D: 장애·복구는 검증하지 않았다.
  이 문단의 P1-4 검증 뒤 P1-5 feedback candidate/version promotion도 아래와 같이 구현·검증됐다.

## 2026-08-28 P1-3 지식 작업 계약·평가 source/offline 상태

- 기존 task loop와 FastAction 위에 exact `TaskWorkContract`가 구현돼 있다. task ID, principal 소유 token,
  skill origin, instruction/context manifest, auto/approval tool 집합, output schema, evaluator와 선택적 guidance
  version/digest를 한 실행 identity에 묶는다. 공개 `taskRecord`는 process-local·content-free이며 최근 4건만
  Control Page에 내보내고 goal, prompt, evidence body, principal, reply와 module path는 포함하지 않는다.
- text, voice와 local Control Page task가 같은 계약을 사용한다. active guidance는 archive 내부 서명 조회로만
  받아 system instruction·TaskGrant·approval·verifier 뒤의 비권위 planner input으로 넣는다. 예외·취소·재시작,
  stale generation과 잘못된 completed receipt는 false success를 남기지 않는다.
- `review|summarize|explain|compare`는 허용된 source와 exact evidence refs를 가진 구조화 grounded draft를
  먼저 만든다. URL은 explicit opt-in의 안전한 HTTPS만 허용하고 private/local/credential/redirect source를
  거부한다. `semanticVerified=false`, `humanReviewRequired=true`이며 음성 안내에는 source body/URL이 없다.
- `tools/task_agent_eval.py`는 고정 synthetic 24-row(`grounded 12`, `safety 8`, `lifecycle 4`)의 실제 입력과
  evidence refs를 opaque case ID에 결박한다. baseline/candidate 48회를 기존 Qwen broker `task` admission으로
  capacity-one 직렬 실행하고, 취소·120초 row deadline 때 현재 HTTP invocation을 취소·수거한 뒤 successor 없이
  content-free incomplete report를 원자 저장한다. cross-case ref, 무권한 effect, private marker leakage와 임의
  source/evaluator/tool-grant report를 거부한다.

## 2026-08-28 P1-5 사람 교정 기반 개선 source/offline 상태

- local operator의 exact correction은 먼저 feedback source에 결박된 candidate다. category allowlist와
  task/source/principal/surface/session/nonce currentness를 확인하며 Discord 피드백은 같은 사용자가 현재 shared
  session에서 실제 전달 완료된 자기 최신 답변에 붙인 review-only signal이다. Discord에서 candidate 생성,
  generalize, eval, approve, activate는 할 수 없다.
- 사람 operator가 private 사실·식별자·인용·말투·원문/hash/embedding을 제거했다고 직접 검토하고 일반 규칙을
  다시 작성해야 source-free independent guidance가 된다. 원본 피드백 삭제는 correction/source-bound 계보와
  무결성 상태를 `사용자의 삭제 요청에 따라 삭제됨`으로 처리하지만 이미 독립화된 개선 버전과 active pointer는
  자동 취소하지 않는다. C: 기준 DB와 D: replica에는 같은 삭제가 적용된다.
- independent version은 P1-3 고정 eval 전건, action/version/archive generation에 결박된 fresh local-admin OTP,
  서버가 수집한 exact 10개 local grounded read-only canary receipt와 generation CAS를 통과해야 활성화된다.
  client aggregate는 받지 않는다. canary 예외·재시작·source 삭제는 raw durable running record에서 실패 종료하고
  candidate/descendants를 revoke하지만 기존 active는 바꾸지 않는다.
- active failure는 exact task source의 삭제 parent를 상속하며 current contract/evaluator의 고정 regression
  code만 rollback을 연다. rollback은 현재 eval→approval→canary→activation chain과 guidance digest를 다시
  검증한다. revoke도 대상·reason·generation에 결박된 fresh OTP가 필요하고 descendants까지 폐기한다.
- P1-3/P1-5 완료 뒤 최종 canonical은 `5064 passed, 18 skipped, 1545 subtests`, 실패 0건이다. P1-3A 집중
  `342 passed, 217 subtests`, P1-3B 집중 `188 passed, 184 subtests`, P1-5/Discord 집중
  `349 passed, 74 subtests`, 추가 결합 `73 passed, 19 subtests`, 잠금·symlink 인접 회귀
  `170 passed, 114 subtests`도 통과했다. Python compile, 두 admin JavaScript syntax와 diff check가 통과했다.
  live Discord, mic, Minecraft, Docker/Qwen service, 실제 24-row/10건 canary와 C:/D: 장애는 실행하지 않았다.
- Discord archive command publisher는 지정 guild에만 5개 command를 임시 게시하고 exact returned ID/shape의
  protected v2 ownership ledger로 production clear·fallback·restart recovery를 수행한다. global sync, bulk overwrite,
  이름 기반 삭제는 없고 global/other registry의 canonical 전후 동일성을 함께 검사한다. 대상 이름은 strict UTF-8
  stdin과 전체 membership exact-unique 비교로만 해석하며 validate child가 게시 직전 same-session same-ID를 재확인한다.
- 승인된 live registry run은 대상/global/다른 guild `0/51/0`에서 대상 5개 게시와 exact-ID 회수를 확인했다.
  결과는 `publishedVerified=true`, `restoredVerified=true`, `recoveryRequired=false`였고 독립 사후 read도 대상
  managed/전체 `0/0`, global `51`, 다른 guild `0`, 보호 run directory `0`이었다. 이름·ID·token·command body는
  공개 출력이나 문서에 남기지 않았다.
- 같은 checkpoint의 최종 canonical은 `5153 passed, 18 skipped, 1 warning`, 실패 0건이다. 전체 회귀에서 드러난
  기존 gateway·Qwen 테스트 2건의 50~100ms localhost wall-clock flake는 제품 제한시간을 바꾸지 않고 실제 lane
  재획득 event와 controlled clock으로 검증 의미를 분리했으며 관련 파일 전체 `36 passed, 14 subtests`를 통과했다.
- 위 결과는 application command registry publish/restore만의 live 증거다. Docker engine/service와 archive
  key/TLS/attestation은 준비되지 않았고 durable mic consent도 fresh OFF reconcile이 필요하다. 따라서 실제
  Gateway same-user/wrong-user/stale-session/delivery-failure와 ephemeral 180초 interaction, archive 저장 E2E는
  아직 live 미검증이며 production archive는 변경하지 않았다.

## 2026-09-01 Discord feedback live prerequisite source/offline 상태

- test archive provisioner는 고정 primary/replica/anchor/secret root의 identity/UAC, 분리 NTFS volume·BitLocker,
  private ACL과 stopped-service를 먼저 확인한다. 새 install은 독립 key 5개와 loopback TLS material을 원자 생성하고,
  같은 owner marker의 재시도만 idempotent하게 재사용하며 부분 실패는 이번 run이 만든 root만 rollback한다.
- feedback live launcher는 dirty checkout을 commit/stash/reset하지 않고 exact source snapshot을 race-free digest로
  고정해 no-cache image 3개를 build한다. image label·environment·exported manifest가 같은 digest인지 재검증하고,
  project source는 read-only, runtime write는 run-owned scratch로 격리한다. fresh host supervisor의 physical mic-OFF
  control ACK와 durable consent `inactive`를 모두 확인하기 전 Discord admission을 열지 않는다.
- 직접 소유 검증은 P1 핵심 `181 passed, 72 subtests`, prerequisite `205 passed, 44 subtests`가 통과했다. 전체
  canonical은 Python 3.11에서 `5153 passed, 18 skipped, 1584 subtests`, 실패 0건이며 기존 `audioop` 경고 1건만
  남았다. Discord, Docker, microphone, GPU, Minecraft와 production archive는 기동하거나 변경하지 않았다.
- 실제 C:/D: test provision, host attestation, current-source image build/load, fresh mic-OFF와 Gateway/ephemeral은
  별도 live 승인 대기다. 이 source/offline 결과는 archive 저장 E2E나 production 운영 증거가 아니다.
