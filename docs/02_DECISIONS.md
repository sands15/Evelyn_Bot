---
tags:
  - evelyn
  - decisions
type: decision-log
---

# Evelyn Decision Log

오래 유지할 제품·아키텍처·운영 결정을 근거와 함께 기록한다. 이미 존재하는
권위 계약의 세부 결정을 복제하지 말고 해당 문서에 링크한다.

## 기록 형식

```md
## YYYY-MM-DD — 결정 제목

- 상태: 제안 | 승인 | 대체됨
- 결정:
- 이유:
- 근거: [[문서]] 또는 `코드/테스트 경로`
- 영향:
- 대체한 결정:
```

## 2026-08-26 — 구현은 설계 동결과 사용자 명시 승인 뒤에만 수행

- 상태: 승인
- 결정: 모든 구현 항목은 기본 미승인으로 취급한다. read-only 조사와 실패 재현으로
  root cause, 범위·비범위, 선택한 해법, 변경 파일·계약, 실패·재시작 경계, rollback,
  테스트와 완료 조건을 먼저 `plan.md`에 고정한다. 미해결 질문이 0개가 된 설계만
  승인 요청 대상으로 삼고, 사용자가 대상 항목의 구현을 명시적으로 승인한 뒤에만
  코드·테스트·설정·dependency·runtime 상태를 바꾼다. 승인 후에는 동결 설계를
  기계적으로 구현한다. 같은 범위·안전 경계·완료 gate 안의 실패 진단, 회귀 수정,
  후속 attempt와 검증 반복은 최초 승인에 포함한다. 새로운 제품 판단이나 외부 효과,
  모델·임계값·범위 변경이 필요할 때만 멈추고 재설계·재승인을 받는다.
- 이유: 창의적 선택과 구현을 섞으면 작업 도중 범위·계약이 변하고 사용자가 승인하지
  않은 변경이 따라 들어올 수 있다. 설계를 먼저 닫으면 승인 대상과 실제 diff를 정확히
  대조할 수 있다.
- 근거: `../plan.md`의 `절대 승인 게이트`.
- 영향: 계획 등록, 우선순위 지정, 조사 계속 요청은 구현 승인으로 해석하지 않는다.
  승인된 항목은 계획의 선행 gate가 닫히는 즉시 다음 기계적 단계로 진행한다. 설계 밖 변경이나
  승인되지 않은 외부 동작에는 별도 승인이 필요하며, 실행 중 검증의 범위는 확장하지 않는다.
- 대체한 결정: 없음.

## 2026-08-26 — 복구 기준점은 외부 capsule 뒤 단일 checkpoint로 고정

- 상태: 제안 — 설계 동결, 구현 미승인
- 결정: P0-1/P0-2 gate 뒤 restricted external recovery capsule의 path/count/SHA-256와
  DACL을 먼저 검증한다. parent overlay로 pinned Mindcraft submodule을 clean하게 만든 뒤
  의도된 현재 tree를 한 개의 checkpoint commit, annotated tag, verified Git bundle로 고정하고
  temporary clean clone의 전체 suite로 재현한다. raw Live2D 원본, scratch/auth probe,
  credential/runtime artifact는 source checkpoint에 넣지 않고 dirty local remote에는 push하지 않는다.
- 이유: 현재 변경량의 과거 의도를 추측해 commit을 나누는 것보다, 사용자 작업을 byte-level로
  먼저 복구 가능하게 만들고 검증된 현재 end-state를 하나로 고정하는 편이 손실·false history
  위험을 최소화한다.
- 근거: `../plan.md`의 `P0-3`, [[worklog/2026-08-26]].
- 영향: stash/reset/force/amend와 submodule 새 commit은 사용하지 않는다. capsule 또는 검증이
  실패하면 다음 단계로 가지 않으며, commit 뒤 실패도 checkpoint를 보존해 증거를 숨기지 않는다.
- 대체한 결정: 없음.

## 2026-08-26 — 대표 음성 E2E는 기존 경로의 content-free 수치 증거로 닫음

- 상태: 제안 — 설계 동결, 구현·live action 미승인
- 결정: Discord endpoint silence 기본 후보를 0.60초로 고정하고, 기존 Local streaming과
  Discord completed-PCM batch 경로에 speech/partial/endpoint/final/barge-stop monotonic timing만
  추가한다. `voice-p0.v2`, private 50-item corpus, Local/Discord 각 11-step suite로 정확도,
  지연, 단일 capture owner, playback/continuity와 restart restoration을 함께 판정한다.
- 이유: 현재 0.82초 기본은 Discord endpoint p95 700ms 목표보다 길어 달성 불가능하다. 기존
  single-owner와 ASR 구조를 유지하면서 직접 원인과 관측 공백만 닫는 것이 가장 작은 해법이며,
  truncation 0/40과 batch 대비 CER gate가 공격적인 endpoint 변경을 막는다.
- 근거: `../plan.md`의 `P1-1`, [[KOREAN_ASR_TARGET_ARCHITECTURE]],
  `../evelyn_voice/client.py`, [[worklog/2026-08-26]].
- 영향: packet-time Discord streaming, 새 model/KWS/AEC/scheduler/dependency는 비범위다.
  raw audio/transcript는 docs/report에 남기지 않고, 실패 시 이전 STT image·streaming flags·
  silence 0.82초·원래 Docker/mic/Discord desired state로 복구한다.
- 대체한 결정: 없음.

## 2026-08-22 — TTL과 비동기 성공은 발급 창과 commit 직전 effect receipt로 제한

- 상태: 승인
- 결정: durable continuity checkpoint는 저장된 `expiresAt`과 원래
  `expiresAt - savedAt` 창을 현재 reader 설정으로 연장하지 않는다. process-local autonomy grant와
  Minecraft lease는 wall deadline과 발급 process의 monotonic deadline이 모두 살아 있어야 하며 restart에서
  복구하지 않는다. 외부 world proof의 공개 형식은 유지하고 same-host private secret에 exact lease ID와
  monotonic 만료를 결박한다. executor connect·world enable 같은 await 뒤에는 state commit 직전에 권한을 다시
  검사한다. Discord 재생 시작·완료는 source read나 `play()` callback이 아니라 exact current source의 0이 아닌
  첫 packet이 실제 UDP send 경계를 통과한 receipt를 요구하고, connect 취소는 base connect 완료 여부에 따라
  inherited disconnect까지 회수한다.
- 이유: 새 설정, wall clock rollback, await 사이 만료·취소와 transport 내부 packet drop이 이미 끝난 권한이나
  실제로 들리지 않은 발화를 성공으로 되살릴 수 있다. 발급자가 정한 창과 되돌릴 수 없는 effect 경계를 함께
  확인해야 restart 연속성과 행동·음성 성공 판정이 같은 인과를 따른다.
- 근거: `evelyn_core/runtime/evelyn_core/session_continuity.py`,
  `evelyn_core/runtime/evelyn_core/autonomy_authorization.py`,
  `evelyn_core/runtime/evelyn_core/autonomy.py`,
  `evelyn_core/runtime/evelyn_core/minecraft_world_lease.py`,
  `evelyn_core/runtime/evelyn_core/minecraft_world_lease_contract.py`,
  `evelyn_core/runtime/evelyn_core/tts_playback.py`, `evelyn_voice/client.py`,
  관련 `tests/core`, `tests/minecraft`, `tests/mindcraft`, `tests/voice`, [[worklog/2026-08-22]].
- 영향: 더 긴 reader TTL, 벽시계 역행, connect/enable await와 source read만으로는 만료된 권한이나
  playback 성공을 만들지 못한다. private monotonic 결박은 same-host·same-process 수명 경계이며 public proof나
  restart 복구 권한이 아니다. 실제 OS clock fault, Discord gateway/UDP와 Minecraft E2E는 live 검증 전이다.

## 2026-08-22 — 연속성·승인·음성 성공은 exact owner와 current receipt에 결박

- 상태: 승인
- 결정: configured Fast Control continuity는 exact guild/user를 domain-separated SHA-256으로 만든 opaque
  session key로만 쓰고 읽는다. 기존 고정 Fast key는 configured principal에서 복구하지 않으며, verified empty
  head의 시각은 `resetBoundaryAt`으로 다음 authenticated checkpoint에 전파한다. task goal은 4,000자까지 원문을
  보존하고 초과 입력은 자르지 않고 거부한다. approval claim 뒤 deadline이 끝난 Host 완료는 성공 receipt여도
  `uncertain/outcome_unverified`다. required speaker verification은 exact `matched is True`만 허용하고 정책 미적용
  `status=skipped`만 예외로 둔다. Local PCM은 status await 뒤 STT 직전에도 admission epoch가 current여야 하며,
  mic OFF 성공은 physical stop ACK와 exact durable input-lease release receipt를 모두 요구한다.
- 이유: 고정 cross-surface key, 잘린 목표, 만료 뒤 성공, 불확실한 화자·stale PCM과 lease가 남은 OFF는 각각
  다른 사람의 문맥·권한·음성을 현재 요청으로 재결박하는 false-green을 만든다. 같은 최소 원칙으로 owner,
  deadline, current generation과 실제 effect receipt를 끝까지 확인해야 한다.
- 근거: `fast_control_continuity.py`, `cross_surface_continuity.py`, `session_continuity.py`,
  `task_loop_runtime.py`, `task_approval_runtime.py`, `tts_interrupt_runtime.py`, `local_io_bridge.py`,
  `fast_control_api.py`와 대응 `tests/core`, `tests/runtime`, `tests/voice`, [[worklog/2026-08-22]].
- 영향: raw guild/user는 Fast checkpoint·status에 저장되지 않는다. reset 전 cross 문맥은 첫 post-reset local turn
  뒤에도 차단되지만 boundary 뒤 같은 principal의 새 활동은 정상 병합된다. 물리 mic OFF 뒤 lease persistence가
  불확실하면 고정 503 `voice_input_lease_unavailable`로 닫는다. 실제 두 surface handoff와 마이크·Discord 장치
  timing은 live 검증 전이다.

## 2026-08-21 — 경계 성공은 시도 상태가 아니라 실제 effect receipt로 인정

- 상태: 승인
- 결정: ingress restart recovery 시간은 journal의 `createdAt|updatedAt`보다 뒤이고 `expiresAt`보다 앞인
  논리 시간으로 기록한다. Local Voice의 barge-in source는 owner claim이나 turn 단위 boolean이 아니라 exact
  playback owner/token의 첫 PCM write 성공 뒤에만 게시한다. 원격 STT start 중 caller가 취소되면 이미 시작한
  thread를 수거하고 반환된 stream ID를 bounded DELETE한 뒤 원래 취소를 재전파하며, 유효한 ID가 포함된 잘못된
  start contract도 즉시 DELETE한다.
- 이유: wall clock, asyncio task와 blocking HTTP/device thread는 서로 같은 순서로 끝난다는 보장이 없다.
  attempt·claim·cancel만 성공 증거로 쓰면 다음 restart에서 journal이 손상되거나, 소리가 나기 전 interrupt를
  승인하거나, caller 밖에 원격 session을 남기는 false-green이 생긴다.
- 근거: `evelyn_core/runtime/evelyn_core/conversation_ingress_recovery.py`,
  `evelyn_core/runtime/evelyn_core/local_io_bridge.py`, `evelyn_core/runtime/evelyn_core/stt_client.py`,
  `tests/core/test_conversation_ingress_recovery.py`, `tests/voice/test_local_bridge_barge_in.py`,
  `tests/voice/test_stt_streaming_runtime.py`, `tests/voice/test_local_asr_streaming.py`, [[worklog/2026-08-21]].
- 영향: clock rollback과 새 sentence owner는 이전 성공을 재사용하지 못하고, write 실패·차단은 positive playback을
  만들지 않는다. client가 stream ID 자체를 받지 못한 transport loss/hard kill은 server TTL에 의존하며, 실제
  장치·원격 서비스 fault injection은 live 검증 전이다.

## 2026-08-21 — task·검색 terminal claim은 deterministic typed receipt로 제한

- 상태: 승인
- 결정: task의 unattended `web_search`는 closed single-operation goal에서 exact query를 추출할 수 있을 때만
  허용하고 provider 호출 직전에 model args와 goal을 다시 대조한다. completed task의 read-only/search/mutation과
  일반 검색의 사용자용 terminal은 Main 자유문이나 history가 아니라 tool별 exact typed receipt의 deterministic
  renderer가 만든다. 외부·파일 내용은 canonical JSON UTF-8의 bounded hex prefix로 표시하고 spoken text와 분리한다.
- 이유: tool 이름만 허용하면 모델이 goal 밖의 query를 외부로 보낼 수 있고, 자유문 finalizer는 receipt 범위를
  넘어 모든 버그 해결이나 전체 테스트 통과를 주장할 수 있다. raw 배열·escape 문자열은 display/TTS sanitizer가
  제거·변형하거나 음성 제어로 해석할 수 있으므로 검증 데이터 형식도 sanitizer 불변이어야 한다.
- 근거: `evelyn_core/runtime/evelyn_core/task_loop_runtime.py`,
  `evelyn_core/runtime/evelyn_core/main_llm_runtime.py`, `fast_control_api.py`,
  `tests/core/test_task_loop_runtime.py`, `tests/core/test_ask_llm_once_runtime.py`,
  `tests/voice/test_voice_turn_orchestrator.py`, `tests/runtime/test_fast_task_loop_integration.py`.
- 영향: compound·negated·underbound web goal은 approval-required로 닫히고 다른 query는 egress 전에 차단된다.
  typed schema·goal binding·producer size/count 계약이나 renderer round trip이 어긋나면 실패 폐쇄한다. Voice는 검색
  결과 있음·0건·실패를 구분하고 resolution 전 model delta를 말하지 않는다. raw receipt만으로 의미 리뷰·요약·비교를
  완료하지 않으며 URL 표시 정책과 semantic finalizer는 별도 결정 전까지 기능 한계로 남긴다.

## 2026-08-21 — production Qwen은 Bot API 단일 admission owner로 직렬화

- 상태: 승인
- 결정: task worker, 일반 specialist와 Mindcraft의 production Qwen 요청은 기존 authenticated Bot API broker를
  통해서만 보낸다. broker는 active 1·waiter 3의 FIFO, enqueue 기준 queue deadline과 slot-grant 기준 inference
  deadline을 소유한다. caller 취소 뒤에도 이미 시작한 upstream은 EOF까지 drain하고 transport 불확실성은 owner를
  poison한다. POST 직전 durable marker에 Qwen boot epoch를 기록하고 bounded full EOF에서 지우며, full EOF 뒤
  HTTP/JSON/content semantic 실패는 invocation-local로 처리한다. stale marker는 Qwen
  process epoch 교체와 content-free health 확인 뒤에만 복구한다.
- 이유: `llama-server -np 1`만으로는 여러 container caller의 queue·timeout·cancellation과 Bot 재시작 경계를
  결박할 수 없다. 모델 응답을 다음 요청으로 오귀속하거나 삭제된 기억을 사용한 late result를 성공 ACK해서는 안 된다.
- 근거: `evelyn_core/runtime/evelyn_core/mindcraft_llm_broker.py`,
  `evelyn_core/runtime/evelyn_core/task_loop_runtime.py`, `specialist_llm_runtime.py`,
  `external/mindcraft_evelyn/src/models/evelyn_planner.js`, `docker-compose.fast-control.yml`,
  `tests/runtime/test_mindcraft_llm_broker.py`, `tests/runtime/test_docker_compose_contract.py`.
- 영향: exact request ID·memory receipt와 bounded frame/trailing/ACK를 검사하고 consumer가 성공한 뒤에만
  `delivered`를 ACK한다. final memory guard 실패도 ACK 실패다. shutdown은 admission을 먼저 닫고 자연 drain하며
  health는 active marker/mounted epoch 일치와 probe 전후 epoch·owner를 확인한다. Qwen은 explicit Compose Bot
  restart/update에 결합된다. plain Docker Bot restart는 stale marker를 자동 복구하지 않는다. Router는 별도
  모델이라 owner 밖이고, 실제 GPU burst/crash recovery는 live 검증 전이다.

## 2026-08-21 — 승인 취소는 Host 정리 결과 뒤에 확정

- 상태: 승인
- 결정: staged workspace edit 취소는 task manager가 exact claim을 `cancelling`으로 준비하고, Control Page가
  Host에 그 stage 취소를 보낸 뒤, approval/stage/Host instance가 결박된 성공 receipt로 manager를 complete하는
  2단계 절차를 사용한다. waiter는 Host 결과 전에는 깨어나지 않는다.
- 이유: manager가 먼저 waiter를 깨우면 task loop의 finally cleanup과 Control Page의 Host cancel이 같은 stage를
  서로 취소할 수 있다. 먼저 소비한 쪽의 cleanup은 성공해도 다른 쪽은 `stage_unavailable`을 받아 실제 결과와
  사용자 응답이 어긋난다.
- 근거: `evelyn_core/runtime/evelyn_core/task_approval_runtime.py`,
  `evelyn_core/runtime/evelyn_core/control_page_server.py`,
  `tests/runtime/test_task_approval_runtime.py`, `tests/runtime/test_task_approval_control_page.py`,
  `tests/runtime/test_fast_task_loop_integration.py`, [[worklog/2026-08-21]].
- 영향: Host cleanup receipt가 없거나 binding이 다르면 `uncertain`으로 끝내고 성공 취소를 추정하지 않는다.
  이미 `claimed|resuming|cancelling`인 approval에 direct `/작업취소`가 와도 raw coroutine cancel로 경쟁시키지 않는다.
  approved apply의 `resuming` barrier는 post-apply read와 background terminal cleanup까지 유지하고 exact task cleanup만
  해제한다.
  approval 밖의 explicit cancel만 exact asyncio task intent로 `cancelled`를 기록하고, 서버성 cancellation은 복구가
  필요한 `failed`로 유지한다.

## 2026-08-21 — 후보 sandbox test는 승인 보조 관측으로만 사용

- 상태: 승인
- 결정: behavioral workspace candidate는 frozen Git-tracked snapshot과 exact candidate overlay를 fixed image의
  bounded sandbox에서 선택한 test로 관찰할 수 있다. 이 receipt는 항상 `semanticVerified:false`이며 goal 완료
  권한이 아니다. 사람이 full diff를 승인해 적용하고 같은 path candidate SHA를 재확인해도 behavioral task는
  `workspace_behavior_outcome_unverified`로 끝낸다. path와 old/new literal이 목표에 정확히 결박된 content edit만
  SHA 검증으로 완료할 수 있다.
- 이유: 후보와 test가 같은 interpreter에서 실행되면 후보 코드가 종료 상태나 runner protocol을 흉내낼 수 있다.
  격리 실행은 후보 선택과 사람 검토에는 유용하지만 동작적 목표 달성을 증명하지는 못한다.
- 근거: `workspace_test_sandbox.py`, `workspace_task_tools.py`, `task_loop_runtime.py`,
  `tests/runtime/test_workspace_test_sandbox.py`, `tests/core/test_task_loop_runtime.py`, [[CURRENT_STATE]].
- 영향: failed target은 revised candidate에서도 전부 다시 실행한다. container/snapshot cleanup ambiguity는 현재 Host
  수명 동안 readiness를 latch-off한다. 향후 semantic completion은 candidate를 import하지 않는 고정 evaluator나
  별도 privilege principal과 사람 승격 절차가 생긴 뒤에만 별도 결정으로 연다.

## 2026-08-21 — fully-bound task transition은 runtime이 소유

- 상태: 승인
- 결정: goal·receipt·authority state가 다음 행동을 유일하게 정한 exact initial read, same-SHA read
  continuation과 post-apply verification read는 runtime이 typed transition으로 직접 실행한다. 해석·도구 선택이
  필요한 단계만 Qwen worker에 맡긴다. malformed edit args는 behavioral sandbox로 추정하지 않고 typed
  observation으로 worker에 되돌린다.
- 이유: 이미 결박된 전이를 모델에 다시 물어보면 권한이나 검증은 강화되지 않고 model noncompliance,
  GPU round trip과 지연만 늘어난다. 반대로 형식 오류를 sandbox 필요 작업으로 오인하면 재계획 기회 없이
  가용성 상태에 막힌다.
- 근거: `evelyn_core/runtime/evelyn_core/task_loop_runtime.py`,
  `evelyn_core/runtime/evelyn_core/main_llm_runtime.py`, `tests/core/test_task_loop_runtime.py`,
  `tests/core/test_ask_llm_once_runtime.py`, [[worklog/2026-08-21]].
- 영향: deterministic transition도 기존 task-step bound, TurnScope cancellation, grant, signed Host receipt,
  approval과 completion verifier를 우회하지 않는다. compound/derived goal과 behavioral test 선택은 자동 결박하지
  않는다. exact long-read live canary는 3개 청크를 task worker model call 0회로 완료했다.

## 2026-08-20 — 기존 turn pipeline에 bounded task loop를 얇게 결합

- 상태: 승인
- 결정: 새 agent framework나 자유형 shell을 추가하지 않는다. 명시적 작업 요청만 기존
  Router → registered skill/FastAction → Main 경로에서 `plan one step → typed executor → observe/verify
  → continue|replan` loop로 처리한다. Qwen worker는 판단이 필요한 다음 step JSON만 제안하고, executor receipt만
  실행·검증 상태를 바꾸며, terminal 결과는 tool별 typed deterministic renderer가 전달한다.
- 이유: 현재 route, cancellation, FastAction recovery, memory exposure와 도메인별 lease가 이미
  권위 경계다. 이를 우회하는 범용 agent를 더하면 성공 판정과 취소·복구가 이중화된다.
- 근거: `evelyn_core/runtime/evelyn_core/task_loop_runtime.py`,
  `skills/task_loop/__init__.py`, `workspace_task_tools.py`, `host_supervisor.py`,
  `tests/core/test_task_loop_runtime.py`, `tests/core/test_task_route_orchestration.py`,
  `tests/runtime/test_workspace_task_tools.py`, `tests/runtime/test_fast_task_loop_integration.py`.
- 영향: 자동 권한은 server-attested Control Page의 bounded read-only workspace 도구, runtime status와
  명시적 public web search뿐이다. 장문 read는 protocol v2의 same-path/full-SHA UTF-8 byte 청크를 runtime이
  정확히 이어 붙이고 0→EOF content digest를 재검증할 때만 완료한다. 기본 task는 final 판단을 포함한 bounded
  step 안에서만 읽으며 초과 파일을 성공으로 승격하지 않는다. 단일 UTF-8 파일 create/replace는 Control Page가 보여 주는 전체 diff와
  30초 one-use token, dirty base의 별도 확인을 거친 exact task/grant/action/step 승인에서만 Host가 적용하고
  같은 coroutine을 재개한다. 한 task당 mutation 승인 시도는 한 번뿐이며 실행 뒤 같은 candidate SHA를
  독립 read로 검증해야 완료된다. test는 Host가 만든 exact behavioral stage에 한해 격리 관측으로만 실행하고
  semantic 완료에는 쓰지 않는다. 서비스·Docker·Minecraft, delete/auth/push/deploy/external send/policy 변경/
  arbitrary shell은 계속 차단한다. 실제 task route는 preface·short-circuit·specialist보다 registered task skill을
  우선하고, 유효한 completed receipt만 deterministic finalizer로 보낸다. Windows read/stage/apply는 root→target parent
  ancestor handle pin이 없으면 fail-closed한다. 특히 실행 중인 harness·authority/evaluator와 direct trust cone은 자기 승인으로 수정하지 않고,
  향후 별도 candidate workspace와 외부 evaluator를 갖춘 다음 run에서만 승격한다.

## 2026-08-20 — workspace mutation은 staged exact approval로만 허용

- 상태: 승인
- 결정: LLM task loop의 `workspace_edit`는 Control Page에 표시되는 단일 파일 create/replace preview를
  사람이 명시적으로 확인한 경우에만 실행한다. approval은 task/grant/action/step/tool/args hash,
  Host generation, base/candidate SHA-256, preview digest, git status와 target identity에 결박한다.
- 이유: 모델의 계획 능력과 사용자 작업 협업을 열면서도, dirty worktree 덮어쓰기·승인 재사용·불확실한
  effect 자동 재시도·host code execution을 권한으로 착각하지 않기 위해서다.
- 근거: `task_approval_runtime.py`, `workspace_task_tools.py`, `control_page_server.py`,
  `tests/runtime/test_task_approval_runtime.py`, `tests/runtime/test_workspace_task_tools.py`.
- 영향: public state에는 locator만 남고 token/raw args/diff는 저장하지 않는다. stage IPC의 bounded raw edit
  payload는 Host가 읽은 직후 제거하고 boot/TTL purge하는 transient local boundary로만 허용한다. Windows
  replace는 ADS가 있으면 거부하고 conditional exchange의 displaced base를 검증하며, race나 cleanup ambiguity는
  `recovery_required`로 닫고 자동 재시도하지 않는다. `workspace_test`는 runtime이 만든 exact behavioral candidate를
  fixed-image sandbox에서 관찰하는 경우만 허용하며 receipt는 승인 보조일 뿐 semantic completion 권한이 아니다.

## 2026-08-02 — Obsidian을 개발 작업 기억으로 사용

- 상태: 승인
- 결정: `docs/`를 개발자용 Obsidian Vault로 사용하고 Codex가 Markdown을 직접
  검색·갱신한다.
- 이유: 작업 문맥과 결정 근거를 세션 밖에서도 사람이 검토 가능한 형태로 유지한다.
- 근거: [[00_EVELYN_HOME]], [[01_NOW]], 루트 `AGENTS.md`
- 영향: 큰 문서는 선택적으로 검색하고, 체크포인트와 현재 문맥만 짧게 기록한다.

## 2026-08-02 — 공식 프로젝트 문서 저장소 단일화

- 상태: 승인
- 결정: 공식 프로젝트 문서와 개발자용 Obsidian Vault는
  `C:\Users\Admin\Documents\이블린 - Evelyn\docs` 하나만 사용한다.
  `C:\Evelyn\docs`에는 앞으로 이중 작성하지 않는다.
- 이유: 현재 Git 작업공간, 코드·테스트 변경 이력, 루트 `AGENTS.md`의 문서 계약을
  같은 저장소 안에서 함께 검토하고 체크포인트하기 위해서다.
- 근거: 사용자 지정, 루트 `AGENTS.md`, 현재 Git 작업공간
- 영향: 과거 `C:\Evelyn\docs`에만 있는 유효 문서는 필요할 때 한 번 비교·이관하고,
  이후 모든 문서 갱신은 이 저장소의 `docs/`에서 관리한다.

## 2026-08-02 — 코딩 작업에 Ponytail full 적용

- 상태: 승인 — 정량 종료 보고 조항은 아래 결정으로 대체
- 결정: 모든 코딩 작업은 기존 구현 재사용, 표준 기능, 최소 코드 순서의
  `ponytail full`을 기본으로 한다. 작업 종료 보고에는 시작 전에 기록한 기준안과
  최종 생산 코드 변경량을 비교한 감소율, 변경 파일 수와 새 의존성 수를 포함한다.
- 이유: 제품 보장을 유지하면서 중복 추상화·미래용 설계·불필요한 의존성을 줄인다.
- 근거: 사용자 지정, `ponytail` skill, [[01_NOW]]
- 영향: 보안, 데이터 보존, 명시된 계약, 신뢰 경계 검증과 이를 고정하는 테스트는
  감소 대상으로 보지 않는다. 기준안이 없는 기존 작업은 수치를 꾸며내지 않고
  `산정 불가`로 표시한다.

## 2026-08-02 — Ponytail 정량 종료 보고 생략

- 상태: 승인
- 결정: 코딩 작업의 `ponytail full` 적용은 유지하되, 작업 종료 때 감소율·변경량·
  절감 수치를 별도 항목으로 보고하지 않는다.
- 이유: 최소 구현 원칙은 작업 방식으로 유지하되 결과 보고는 구현·검증·남은 위험에
  집중한다.
- 근거: 사용자 지정, [[01_NOW]]
- 영향: 보안, 데이터 보존, 명시 계약과 검증을 줄이지 않는 기존 예외는 그대로다.
  필요한 경우 코드 diff 자체는 검토하되 Ponytail 성과 지표로 포장하지 않는다.

## 2026-08-02 — Host capture 증거는 세대·목적 제한 HMAC으로 인증

- 상태: 승인
- 결정: 공유 artifact를 지나는 capture owner lease, Bridge status와 Supervisor stop
  evidence는 서로 다른 HMAC domain을 사용한다. 키는 공식 launcher 세대마다 새로
  만들고 Control Page, Host Supervisor, Local Bridge에만 전달한다. Bot API에는 키를
  주지 않고, authenticated Bridge가 보고한 content-free fence digest를 Host lease와
  durable consent state에 대조해 admission을 판정한다.
- 이유: 공유 폴더의 read/write 권한만으로 캡처 권한이나 physical OFF 증거를 위조할
  수 없어야 하며, raw owner/lease 값이나 음성 데이터를 저장할 필요도 없어야 한다.
- 근거: [[VOICE_CAPTURE_CONSENT]],
  `evelyn_core/runtime/evelyn_core/voice_capture_consent.py`,
  `tests/runtime/test_host_supervisor.py`
- 영향: artifact는 content-free digest와 인증 tag만 보존한다. 키 누락·오류,
  cross-scope replay, stale·replacement와 status rollback은 fail-closed하며 일반
  자식 프로세스에는 키를 상속하지 않는다. Bot API가 캡처 lease를 새로 서명할
  권한은 없으며 공개 상태에서도 fence digest를 제거한다.

## 2026-08-02 — Local Voice admission을 캡처 동의 세대와 선형화

- 상태: 승인
- 결정: durable Local Voice reservation·claim proof를 발급 당시 capture fence
  digest에 묶고, consent state write와 마지막 reserve/claim은 stable OS claim lease로
  직렬화한다. durable reservation이 있는 token은 exact reservation ref·ingress turn과
  `reservation_verified=true` receipt만 소비한다. OFF 계열 전이는 manager 재시작으로
  알 수 없는 같은 scope의 `reserved` row도 durable purge한다.
- 이유: 동의 A에서 발급한 token이 동의 B에서 부활하거나, 철회와 accepted text 저장이
  엇갈리거나, Bot 재시작 때문에 미소비 reservation이 대화 권한으로 남아서는 안 된다.
- 근거: [[LOCAL_VOICE_ADMISSION_CONTRACT]], [[VOICE_CAPTURE_CONSENT]],
  `evelyn_core/runtime/evelyn_core/local_voice_admission.py`,
  `tests/runtime/test_fast_control_ingress_integration.py`
- 영향: claim이 먼저 durable commit되면 token은 terminalize하고 관측 이벤트 오류가
  이를 503/retryable 상태로 되돌리지 않는다. 철회가 claim lease를 제때 얻지 못하면
  메모리에서 먼저 `revoking`으로 닫고 physical OFF를 계속 시도한다. raw audio와
  transcript는 이 계약이나 보고서에 추가로 저장하지 않는다.

## 2026-08-03 — 기본 TTS를 실제 OmniVoice로 고정

- 상태: 승인
- 결정: 내부·호스트 계약 `tts:8880`은 유지하면서 기본 모델을
  `k2-fsa/OmniVoice`로 교체한다. offline model cache는 revision
  `c5fdb5ccb189668d56333f77ba2629f4cd7535f4`를 read-only로 제공하고 health의
  `model_revision`까지 exact 일치해야 한다. VoxCPM2의 host `8881` 서비스는
  opt-in 호환성·진단용으로만 보존하며 자동 또는 runtime fallback으로 사용하지 않는다.
- 이유: 기존 클라이언트가 이미 OmniVoice clone·24 kHz PCM streaming 계약을
  사용하므로 호출 계층을 바꾸지 않고 실제 모델과 표시·health의 불일치를 없앤다.
- 근거: `docker-compose.fast-control.yml`, `docker/Dockerfile.omnivoice`,
  `evelyn_core/runtime/service_manifest.json`,
  `tests/runtime/test_docker_compose_contract.py`
- 영향: 기본 TTS build는 검토된 외부 Python 소스 20개의 SHA-256 allowlist와 고정된
  직접 runtime 의존성 버전을 사용한다. 시작 시 고정 revision의 필수 model snapshot
  경로 13개도 SHA-256으로 검증한다. 전이 wheel과 CUDA base image digest까지 고정한
  완전 재현 build라는 뜻은 아니다. profile은 read-only이며
  profile API와 validation 오류 응답은 입력 원문을 숨기고 운영 로그에는 합성 text,
  경로, session/turn 식별자를 남기지 않는다. 기본 합성은 sentence streaming을 사용하고 실험적 blockwise 경로는
  client disconnect cancellation이 안전해질 때까지 비활성화한다. Compose는 TTS image를
  `pull_policy: never`로 외부에서 받지 않으며 공식 path-safe builder가 누락되었거나 새로
  요청된 image를 만든다. Supervisor 복구는 이미 있는 image만 사용한다. 8881 서비스를
  시작해도 기존 client는 `tts:8880`에서 reroute되지 않는다. image build/recreate와 실제
  clone-stream smoke 전에는 live 전환 완료로 보고하지 않는다.

## 2026-08-08 — Mindcraft 기본 판단은 brokered local, Codex는 검증 전 비활성

- 상태: 승인
- 결정: Mindcraft의 기본 action backend는 Bot API broker를 통한 local Qwen으로 유지한다. broker adapter가 없는
  legacy Voyager action backend 기본값은 `disabled`이며 명시적 local 설정도 fail-closed한다.
  Codex Gateway는 별도 Docker profile에만 두고, pinned image에서 tool registry와
  secret canary가 검증되기 전에는 health not-ready, action 503, subprocess 0을
  강제한다. host-native/custom shell gateway는 지원하지 않는다.
- 이유: Minecraft chat과 recovery context는 신뢰할 수 없는 입력을 포함한다.
  `read-only` sandbox는 같은 principal의 credential·runtime file 읽기를 차단하지
  않으므로 filesystem-capable Codex CLI를 기본 경로에 두면 관계 연속성을 위한 기억과
  자격증명을 외부 model output으로 노출할 수 있다.
- 근거: `external/mindcraft_evelyn/src/models/evelyn_planner.js`,
  `external/mindcraft_evelyn/src/models/codex_gateway.js`,
  `evelyn_core/runtime/evelyn_core/codex_gateway_server.py`,
  `docker-compose.fast-control.yml`, 관련 runtime/Mindcraft 회귀
- 영향: Mindcraft local planning·recovery는 계속 동작하며 기본 Minecraft 시작은 Codex credential을
  요구하지 않는다. persistent memory summary는 기본 경로에서 로드하거나 새로 만들지
  않는다. legacy Voyager action은 `codex-gateway`를 명시하지 않으면 실행하지 않는다. Codex 품질 경로는 verified
  no-tools boundary나 목적 제한 broker가 생길 때까지 의도적으로 사용할 수 없다.

## 2026-08-09 — Mindcraft history는 process-local, LLM은 fixed Bot API broker로 제한

- 상태: 승인
- 결정: 기본 Mindcraft는 bounded ephemeral history만 사용하고 `load_memory=false`와
  no-memory-mount를 유지한다. planner recovery도 저장하지 않는다. Node는 direct model
  endpoint를 호출하지 않고 전용 token-file authenticated Bot API broker만 사용하며,
  broker가 fixed local/router upstream과 core conversation filter를 소유하고
  `memory_exposure_request`를 frame consumer의 exact ACK까지 유지한다. process-local
  generation exposure는 turn 첫 await 전부터 awaited final route/action sink까지 보호한다.
  inter-agent ingress·timer queue도 같은 generation에 묶고 clear에서 폐기한다. recovery
  step은 exact history snapshot의 process-local one-shot issuance만 실행 결과로 소비한다.
- 이유: core outbound/deletion primitive는 Python broker에서 재사용하되, 불완전한 durable
  history를 새로 만들지 않는 것이 삭제·편집 후 부활과 근거 없는 재사용을 막는 최소 경계다.
- 근거: [[MINDCRAFT_MIGRATION]], `external/mindcraft_evelyn/src/agent/history.js`,
  `external/mindcraft_evelyn/src/utils/evelyn_history_boundary.js`,
  `external/mindcraft_evelyn/history_sink_boundary.patch`,
  `external/mindcraft_evelyn/src/models/evelyn_planner.js`,
  `evelyn_core/runtime/evelyn_core/mindcraft_llm_broker.py`, `docker-compose.fast-control.yml`
- 영향: legacy memory/archive/log는 읽거나 rebase·삭제하지 않으며 cleanup은 사용자 승인
  migration으로 분리한다. 현재 core memory 입력이 없는 broker request projection은 strict
  `not_used` receipt를 쓰며 ACK는 frame 소비까지만 증명한다. durable bound-receipt history는
  별도 계약으로 남긴다. recovery token과 raw command는 저장하지 않으며 restart 후 이어 쓰지 않는다.
  goal/status artifact는 enum code·count/boolean만 남긴다. `!clearChat`은 대화 유래 상태만
  비우며 자율 목표의 영구 정지는 `!endGoal` 계약을 사용한다.

## 2026-08-13 — Router가 계획하고 Main만 최종 발화

- 상태: 승인
- 결정: 명시적이고 안전한 low-cost shortcut을 제외한 semantic turn은 Router가
  authoritative `tools[]`, context flags와 `specialist`를 선택한다. Qwen과 registered
  skill은 bounded evidence만 만들고 Main이 모든 사용자용 답변을 최종화한다. Sub는
  post-response memory write-behind로 유지하며 모든 모델을 매 turn 호출하지 않는다.
- 이유: 단어 하나로 도구를 강제하면 기억 recall이 불필요한 웹 검색과 결합되고, 각 모델이
  독립적으로 답하면 persona·근거·안전 경계와 호출 수가 갈라진다. 동시에 모든 모델을
  직렬 호출하면 현재 병목인 Router/Main prompt latency를 더 악화시킨다.
- 근거: `evelyn_core/runtime/evelyn_core/context_pipeline.py`,
  `llm_route_runtime.py`, `specialist_llm_runtime.py`, `main_llm_runtime.py`,
  `fast_control_api.py`, `tests/core/test_llm_call_budget.py`,
  `tests/core/test_turn_plan_contract.py`
- 영향: direct turn은 Router 0/Main 1을 목표로 하고, semantic turn만 Router 1과 조건부
  specialist 1을 추가한다. 외부 검색·Qwen·skill output은 낮은 권한 data이며 Router가
  승인하지 않은 검색은 실행하지 않는다. explicit command keyword shortcut은 latency를
  위해 남지만 모호한 자연어의 tool 선택 권한은 갖지 않는다.

## 2026-08-13 — Minecraft 생존은 단일 반사 소유자와 검증형 전투 경험으로 운영

- 상태: 승인
- 결정: 용암·화재·익사·치명 피해는 기존 `self_preservation`만 P0 소유자로 유지한다.
  Evelyn 고유의 적·식량·지상 복구는 Mineflayer event가 dirty/urgent flag를 합치고 기존
  직렬 mode runner가 실행하는 P1으로 둔다. 단, 8블록 안 actionable hostile에는 full snapshot·LLM
  없이 bounded direct-sprint reflex만 허용하고 단일 movement lease로 control ownership을 고정한다.
  일반 진행은 검증 가능한 fallback subgoal을 먼저 쓰며, 전투 경험은 고정 enum·수치만 로컬에
  bounded 저장한다.
- 이유: 이벤트 callback에서 행동을 직접 시작하면 중복 controller race가 생기고, 매 단계
  LLM 호출은 이미 알려진 진행을 늦춘다. 반대로 검증 전 경험이 전투를 공격적으로 만들면
  장기 생존을 악화시킬 수 있으므로 같은 Minecraft/custom-PvP 버전의 성공 2회만 승격하고
  연속 실패 2회는 격리한다. 격리 해제에도 새 검증 성공 2회를 요구하며 hard safety는 경험으로
  완화하지 않는다.
- 근거: `external/mindcraft_evelyn/src/agent/evelyn_survival_mode.js`,
  `evelyn_combat_experience.js`, `evelyn_combat.js`, `evelyn_goal_manager.js`,
  `docker/Dockerfile.mindcraft`와 대응 Node/Python 계약 테스트
- 영향: 위험 event는 평상시 1.5초 gate를 우회하되 full snapshot/action은 single-flight다.
  목재·작업대·초기 무기·곡괭이 진행은 실패 이력이 없을 때 Qwen 없이 선택하며 novel/recovery는
  기존 Qwen으로 돌아간다. mode runner는 `MINDCRAFT_MODE_INTERVAL_MS=100`, ActionManager interrupt
  poll은 `MINDCRAFT_INTERRUPT_POLL_MS=100`으로 배포하고 self-prompt cooldown은 300ms로 둔다.
  interrupt poll은 `requestInterrupt()` 뒤 현재 promise의 cooperative 종료를 재확인하는 주기이며
  강제 취소나 100ms 안의 종료를 보장하지 않는다. command docs는 자율 self-prompt에서만 현재
  subgoal allowlist로 제한하고 사용자 턴은 전체 정상 명령을 유지한다. 매 prompt의 `$STATS`에서는
  wildcard nearby-block scan을 빼되 명시적 `!nearbyBlocks`의 전체 관측 의미는 유지한다. 근접 reflex의
  movement lease는 idle cleanup과 `self_preservation`의 control reset보다 우선한다. 무장 없음·다중 적·
  원거리 적의 tactical flee는 P0 뒤에도 안전 반경까지 corridor-checked direct sprint를 유지한다. 전투 중에는
  250ms마다 preset과 안전 문맥을 재검증해 달라지면 P1 판단으로 돌아간다. 실제 실행한 custom-PvP
  전술의 성공·실패·사망만 경험 증거이며 fallback·timeout·interrupt·인프라 실패는 `interrupted`로
  분리한다. 기록은 대화 기억이 아니며 좌표·transcript·원문을 저장하지 않고, 정상 disconnect와
  SIGINT/SIGTERM에서는 load 완료 뒤 bounded flush한다. 식량 recovery는 소지 음식이 없고
  health 10 이하이면 hunger와 무관하게 선점하되, 적이 없고 지상·비수중인 안전 상태에서만 source를
  찾는다. 소지 음식도 적·수중이 없을 때만 같은 100ms 감시 경로로 먹으며 안전한 지하 섭취는 유지한다.
  source는 성숙 작물과 성체 `cow|pig|sheep`만 대상으로 하고, 이동·수확·사냥·조리·섭취를 실제
  hunger/안전식량 증가로 검증한다. 적·피해·수중·timeout은 현재 Promise 완료를 기다리지 않고
  cooperative interrupt하며, 주변 식량원이 없으면 무작위 이동 대신 30초 planner search handoff와
  10→20→40→60초 재시도 상한을 쓴다. generic `obtain:#food`에는 `!attack`을 열지 않는다. 정상 종료는
  Minecraft 연결을 먼저 닫고 combat-history load·flush 뒤 process를 종료한다. 강제 종료
  중인 active episode, 실제 전투 effect·경험 승격과 식량 획득 성공의 live E2E는 아직 보장하거나
  검증하지 않았다.

## 2026-08-14 — 전투기는 유지하고 tracker·공개 커리큘럼·자동 matrix를 결합

- 상태: 승인
- 결정: `@nxg-org/mineflayer-custom-pvp`를 1:1 전투 actuator로 유지하고 새 전투 plugin이나
  pixel policy를 production 반사층에 추가하지 않는다. 이미 custom-PvP가 로드하는
  `mineflayer-tracker`의 투사체 정보를 P0 방어에 사용한다. Odyssey와 Voyager에서는 저수준
  kill wrapper가 아니라 위협·장비·시간 조합의 커리큘럼과 검증형 skill 승격 원칙만 가져온다.
- 이유: 조사한 공개 Mineflayer-native 구현 중 현재 actuator보다 검증된 drop-in 교체품은 없었다.
  반면 기존 tracker는 추가 dependency 없이 화살 도달 시간을 제공하며, 공개 agent의 강점은
  단일 전투 코드보다 재현 가능한 시나리오와 성공 증거 관리에 있었다.
- 근거: `external/mindcraft_evelyn/src/agent/evelyn_survival_mode.js`,
  `evelyn_escape_controller.js`, `tools/voyager/combat_matrix.py`,
  `tests/tools/test_voyager_combat_matrix.py`,
  `runtime_artifacts/validation/combat_matrix_batch_full3_pass_20of20_20260814/report.json`
- 영향: 전투 변경은 먼저 5위협×2장비×2시간의 20-cell 격리 matrix를 모두 통과해야 한다.
  P0 제어 시작, 연속 reflex 총시간, P1 판단, action start를 따로 측정하며 도주는 18m 밖 2초
  연속 안정까지 검증한다. 2026-08-14 최종 matrix 20/20, 실제 arrow shield P0, 저체력 단일 좀비
  emergency melee와 fresh-world 1,200초 자연 soak가 각각 통과했다. 다만 한 arena와 한 Minecraft
  day의 증거이므로 shelter success, 식량 고갈·여러 day/night·재시작을 포함한 장기 생존률로
  일반화하지 않는다.

## 2026-08-15 — 공식 Mineflayer pathfinder를 유지하고 호출·복구 정책을 강화

- 상태: 승인
- 결정: 새 길찾기 dependency나 비검증 engine으로 교체하지 않는다. 현재
  `mineflayer-pathfinder`의 `GoalCompositeAny`, `GoalFollow`, `partial` 재계획과 movement
  제약을 사용해 후보 선택·동적 추적·정체 복구·도착 검증을 강화한다.
- 이유: 공식 pathfinder는 현재 스택에서 가장 검증된 A* 구현이며, 확인된 실패는 엔진 부재보다
  첫 후보 고정, `partial` 오판, 거짓 성공, 반복 stuck와 외부 preemption goal 정리에서 발생했다.
  새 dependency는 이 문제를 자동으로 해결하지 않으며 지원·테스트 증거도 더 약했다.
- 근거: `external/mindcraft_evelyn/relocation.patch`,
  `external/mindcraft_evelyn/pathfinder.patch`,
  `external/mindcraft_evelyn/src/agent/evelyn_survival_mode.js`,
  `external/mindcraft_evelyn/tests/navigation_skills.test.mjs`,
  `external/mindcraft_evelyn/tests/pathfinder_goto.test.mjs`,
  `external/mindcraft_evelyn/tests/survival_mode.test.mjs`
- 영향: 자원은 공간적으로 분산된 후보를 한 goal로 평가하고, shelter는 모든 bounded 유효 후보 중
  실제 도착 지점을 재검증한다. 움직이는 먹잇감은 `GoalFollow`로 추적한다. 비파괴 `partial`은
  계속 시도하되 실제 `NoPath|Timeout`이면 파괴 허용 경로를 한 번 재시도하고, 같은 goal의 stuck
  2회만 중단한다. 외부 안전 controller가 새 goal을 소유하면 기존 cleanup이 지우지 않는다.
  이후 격리 1.21.11의 4-cell navigation matrix가 직선·우회벽·상승·막힌 최근접 후보 회전을 4/4로,
  fresh-world 1,200.5초 soak가 verified goal 5회·stuck 0·death/error 0으로 통과했다. 이는 해당 fixture와
  한 natural run의 action-to-effect 증거이며 shelter 완성·여러 day/night·restart까지 일반화하지 않는다.

## 2026-08-15 — 음성 입력은 단일 캡처 owner, ASR은 단일 상주 Qwen final로 운영

- 상태: 승인
- 결정: Bot API의 durable lease가 `local_mic|discord_voice` 중 정확히 하나만 물리 입력 owner로
  허용한다. Local은 ON 게시 전에 획득하고 exact physical-stop ACK 뒤 반납하며, Discord는
  `listen()` 전에 획득하고 listener task 종료 뒤 반납한다. ASR은 기존 STT 서비스의 상주
  `Qwen3ASRModel.LLM` 하나를 사용한다. Local capture는 ephemeral `start/chunk/finish/cancel`
  세션을 쓰고, 아직 packet-time decode가 없는 Discord는 완료 PCM을 batch로 정확히 한 번 인식해
  같은 final을 wake와 본문에 재사용한다. partial은 비권위이며 final만 기존 admission으로 보낸다.
- 이유: surface별 캡처가 독립 process라 process-local flag만으로 동시 입력 race를 막을 수 없고,
  Discord의 기존 wake/partial/full/rescore는 같은 모델을 반복 호출한다. 중앙 캡처 lease와 한 모델의
  surface별 단일 final이면 authority 경계를 바꾸지 않으면서 동시 capture와 중복 추론을 함께 줄인다.
- 근거: `evelyn_core/runtime/evelyn_core/voice_input_lease.py`, `stt_service.py`, `stt_client.py`,
  `voice_asr_stream.py`, `local_mic.py`, `local_io_bridge.py`, `evelyn_voice/client.py` 및 대응
  `tests/runtime/test_voice_input_lease.py`, `tests/voice/test_stt_stream_service.py`,
  `tests/voice/test_local_asr_streaming.py`.
- 영향: admission, wake/follow-up, consent, replay와 high-impact confirmation owner는 그대로다.
  same-instance inactive 관측만으로 lease를 풀지 않고 persistence/auth/release 실패는 새 source를
  차단한다. transcript·raw PCM은 lease artifact나 일반 로그에 쓰지 않는다. cloud 자동 fallback,
  KWS 승인, Discord packet-time decode는 포함하지 않으며, 새 STT image/GPU와 실제 두 surface 전환은
  live 검증 전 상태다.

## 2026-08-16 — GPU1 benchmark report를 runtime admission에 연결하지 않음

- 상태: 기존 receipt gate 결정 철회
- 결정: `tools/gpu1_latency_benchmark.py`의 결과는 독립 진단 report로만 남기고 Qwen,
  Discord STT, Local STT의 runtime admission이나 fallback을 변경하지 않는다.
- 이유: 7일짜리 정적 report는 운영 중 latency/VRAM 초과를 감지하지 못하고, 정상 Compose에서
  GPU0를 쓰는 STT까지 만료 시 강등했다. Qwen/STT container를 unload하지도 않아 memory
  보호나 startup OOM 방지 장치가 아니었다.
- 근거: 사용자 운영 영향 검토와 2026-08-16 live benchmark 결과. 요청별 Qwen 6초 timeout과
  기존 STT 자체 오류 fallback은 유지한다.
- 영향: normal Compose/Windows launcher는 benchmark report를 읽지 않는다. benchmark override만
  승인된 측정 시 STT를 physical GPU1으로 옮기며 실제 GPU 실행은 계속 별도 승인이 필요하다.

## 2026-08-21 — Main TTFT는 stable-prefix 우선으로 줄이고 동적 문맥은 요청형으로 유지

- 상태: 구현, live A/B 대기
- 결정: voice와 Fast Main의 고정 system prefix를 startup에서 실제 production 형식으로 warmup한다.
  평상시 voice prompt는 첫 system message와 최근 non-system 8개만 보내고, memory/runtime state는
  정책 또는 tool decision이 요구할 때만 주입한다. 저장 history 자체는 삭제하지 않는다.
- 이유: Main TTFT에서는 고정 prefix의 재평가와 매 턴 변하는 긴 문맥이 직접적인 prefill/cache
  손실이다. 이 경계를 줄이면 모델·품질·권한 구조를 바꾸지 않고 cache reuse 가능성을 높인다.
- 근거: `assistant_prompt_contract.py`, `llm_warmup_runtime.py`, `llm_context_assembly.py`,
  `fast_context_contract.py`, `fast_control_api.py`, `docker-compose.fast-control.yml`,
  `tools/gpu1_latency_benchmark.py`와 관련 251개 offline 회귀.
- 영향: CUDA graph/batch/cache와 persistent loopback HTTP 연결을 함께 사용한다. draft model 없는
  speculative/MTP, 더 작은 Main 모델 교체, in-process GPU 결합은 품질·메모리·배포 계약이 달라져
  이번 source-only 최적화에 포함하지 않는다. 기존 422.6 ms live 결과는 새 설정의 성능 근거가
  아니며 새 승인된 A/B 뒤에만 개선폭을 확정한다.

## 2026-08-22 — 하네스 성공은 긍정 receipt와 current owner가 함께 증명

- 상태: 승인, source 구현·회귀 완료; live fault injection 대기
- 결정: required tool evidence는 exact `status=executed`만 답변 권한으로 인정한다. 실행을 소유한
  `search_executor|task_executor`에는 그 단계까지 권한을 넘기되, 그 밖의 empty/failed/planned/withheld
  상태에서는 Main/Fast 생성을 시작하지 않는다. 비동기 action은 현재 task·grant·lease·run binding과
  terminal receipt가 모두 맞아야 성공·cursor 진행·재연결을 허용한다.
- 이유: 모델 문장, callback의 `None`, 취소 요청, HTTP ACK와 readiness는 실제 effect·감사 기록·종료를
  각각 증명하지 않는다. 이들을 성공으로 간주하면 false completion, 중복 실행과 reset 뒤 늦은 write가
  생길 수 있다.
- 근거: `context_pipeline.py`, `voice_route_execution.py`, `fast_context_contract.py`,
  `task_loop_runtime.py`, `fast_action_runtime.py`, `fast_control_api.py`, `autonomy.py`,
  `minecraft_autonomy_executor.py`, `memory_update_runtime.py`, `control_page_server.py`와
  `docs/worklog/2026-08-22.md`의 RED/GREEN·canonical 검증.
- 영향: 실행 중 FastAction은 history cap을 넘겨서라도 terminal까지 보존한다. 취소를 runner가 삼키면
  `failed/background_action_cancel_outcome_unverified`로 닫고 자동 재시도하지 않는다. 교체된 memory
  predecessor도 실제 종료까지 drain alias로 reset admission을 막는다. mic 활성 뒤 health 확인은 10초
  상한이며 실패 시 physical OFF로 회수한다. 이 source 규칙은 live 장치·Discord·Minecraft effect의
  성공 증거를 대신하지 않는다.
- 결정: staged workspace candidate가 있는 동안 malformed/non-JSON worker decision은 effect나 terminal
  결과가 아니라 비실행 typed observation이다. 같은 candidate를 bounded step budget 안에서 보존해 모델에
  다시 제시하고, 예산 소진 때 exact stage를 한 번 폐기한다. 임의 worker 예외·transport·timeout은 계속
  terminal이다.
- 결정: Discord 전송이 정상 반환한 뒤의 cancellation은 history/session/continuity finalizer와 현재
  action audit·cursor·state commit을 끝낸 뒤 원래 `CancelledError`를 재전파한다. 사용자에게 보이는 autonomy
  오류 알림도 exact active session/reply slot과 `[autonomy:error]` pair를 사용하며 정상 search 완료 표식과
  구분한다. 이 결정 당시 별도 설계가 필요했던 continuity commit 영구 정지의 bounded recovery는 아래
  completed-turn artifact I/O 결정에서 후속 구현됐다.

## 2026-08-22 — continuity commit의 최신 성공은 동일 session 성공 epoch로 보호

- 상태: 승인, source 구현·offline 회귀 완료; live filesystem fault injection 대기
- 결정: completed-turn commit의 public sync/async entry는 worker 실행 전에 외부에서 주입할 수 없는
  process-local epoch를 예약한다. 같은 session에서 더 최신 epoch가 **성공한 경우에만** 오래된 attempt를
  supersede한다. 다른 session의 성공과 더 최신 실패는 오래된 유효 commit의 권한을 빼앗지 않는다.
- 결정: stale attempt도 현재 payload/head를 anchor하고 rollback, keyed authenticity와 external replay 구조를
  먼저 검증한다. callback-free attempt의 exact target이 이미 durable할 때만 receipt를 coalesce한다.
  `before_commit` callback은 stale 경로에서 실행하지 않는다. 최신 target이 대체한 정상 stale 실패는 누적
  failure로 관측하되 최신 성공의 `lastSucceeded/lastTargetVerified/lastAt/lastMs`를 덮지 않는다. 실제
  checkpoint/auth/anchor 손상은 supersede로 숨기지 않고 `error`로 공개한다.
- 이유: `asyncio` task 취소는 이미 시작된 `to_thread` filesystem writer를 중단하지 않는다. 호출 시점의
  소유권 없이 완료 순서만 따르면 오래된 worker가 최신 durable 성공의 process health를 뒤늦게 오염시킨다.
- 근거: `session_continuity.py`, `test_session_continuity.py`,
  `CONVERSATION_CONTINUITY_CONTRACT.md`, `worklog/2026-08-22.md`의 same/cross-session,
  newer-failure, reset, lagging, corruption, callback race RED/GREEN.
- 영향(당시): 이 fence만으로는 filesystem worker가 영구 정지할 때의 bounded completion을 제공하지 않았다.
  이 제한은 아래 completed-turn artifact I/O 결정으로 해소됐고 terminal lifecycle 계약은 별도로 유지한다.

## 2026-08-22 — 명령 기반 terminal lifecycle은 first-owner fail-stop으로 제한

- 상태: 승인, source 구현·offline 회귀 완료; live Docker/filesystem fault injection 대기
- 결정: restart, bot shutdown과 stack/local scheduled shutdown은 호출 시 한 terminal owner를 동기 선점한다.
  Discord terminal 확인문은 실제 전달 뒤 command continuity 기록 전에 이를 arm한다. 기본 20초 hard-exit과
  restart의 1초 이른 soft-launch timer는 non-daemon이며 launcher와 exit를 각각 exact once로 제한한다.
- 결정: Docker `discord_bot`은 Windows launcher를 호출하지 않고 최소 10초 restart-policy admission 뒤
  exit 75를 낸다. 이 서비스만 Compose `on-failure:3`을 사용한다. 정상 shutdown은 exit 0이고, host
  local/Discord launcher 성공도 exit 0이다. scheduled helper와 Timer setup은 claim 직후 bounded하며
  thread 생성 뒤의 Timer arm 실패도 timer를 취소한 다음 claim을 rollback한다.
- 이유: 동기 flush, scheduler, launcher 또는 logger 하나가 멈춰도 terminal 명령이 무기한 process를
  붙들거나 경쟁 restart와 shutdown이 서로 새 프로세스를 다시 내리면 안 된다. daemon timer는 event loop와
  interpreter가 먼저 끝날 때 책임을 잃으므로 terminal owner에는 사용할 수 없다.
- 근거: `runtime_lifecycle.py`, `runtime_lifecycle_composition.py`, `discord_command_session_runtime.py`,
  `discord_command_handlers.py`, `docker-compose.fast-control.yml`과 lifecycle/Discord/Compose RED 회귀.
- 영향(당시): 이 terminal deadline 자체는 flush 성공이나 writer kill을 뜻하지 않았다. 일반 completed-turn의
  bare `to_thread` 영구 정지는 이번 결정 범위 밖이었고 아래 artifact I/O 결정이 후속 구현했다.

## 2026-08-22 — completed-turn artifact I/O는 warm killable child와 disk-first reconciliation을 사용

- 상태: 승인, source 구현·deterministic fault 회귀 완료; 실제 filesystem/Discord live fault 대기
- 결정: Main/Fast Control은 한 shared warm artifact child를 lazy-start한다. production checkpoint/head,
  authenticity anchor, ingress와 FastAction journal의 기본 primitive만 strict framed protocol로 위임한다.
  process lock 대기와 request에는 deadline을 둔다. lock 획득 timeout은 request 제출 전 실패로 current child를
  건드리지 않고, lock을 얻은 request exchange의 timeout/disconnect는 해당 child를 terminate/kill/wait로
  reap하기 전 replacement를 시작하지 않는다. worker 환경에는 runtime과 최소 OS 변수만 전달한다.
- 결정: mutation outcome이 불명확하면 killed PID/request ID의 exact temporary를 먼저 지우고 canonical disk를
  읽는다. exact requested bytes와 durable sync가 확인된 post-replace만 성공으로 수렴하며 그 외 mutation은
  재실행하지 않는다. journal owner는 error 뒤 authoritative journal/head/external anchor를 검증하고 one-step
  lag만 수리한 뒤 mutation을 재개한다.
- 결정: restart `delivery_succeeded`도 checkpoint `before_commit`에서 terminal marker를 먼저 기록한다.
  journal-only marker는 exact active/empty 또는 무키 fresh predecessor에서만 한 번 recommit하며, same-turn
  conflicting tail, 잘못된 receipt generation과 keyed fresh missing head는 거부하고 speculative store를 되돌린다.
  Main은 ingress reconciliation 뒤 truly pristine generation 0만 empty bootstrap한다.
- 결정: completed-turn은 artifact mutation 전후 content-free in-flight status 게시를 best-effort로 시도한다.
  성공한 write만 원자적이며, reader는 마지막 성공 heartbeat에서 stall age를 계산한다. status write 실패는
  commit의 성공·실패 control flow를 바꾸지 않는다.
- 이유: `asyncio.wait_for(to_thread(...))`는 filesystem thread와 RLock을 중단하지 못해 timeout 뒤 late write와
  permanent lock을 남긴다. killable process와 disk-first 확인은 실제 실행 owner를 회수하면서도 이미 교체된
  durable 결과를 거짓 실패나 중복 mutation으로 만들지 않는다.
- 근거: `durable_artifact_process.py`, `durable_artifact_worker.py`, `runtime_artifact_io.py`,
  `session_continuity.py`, ingress/FastAction recovery owner와 deterministic pre/post-replace, read stall,
  parent death, J-only crash·generation·conflicting-tail 회귀. canonical 4,188 passed, 22 skipped,
  subtests 1,149가 통과했다.
- 영향: 각 artifact owner/scope는 유한하지만 checkpoint 뒤 별도 journal completion까지 포함한 다단계
  transaction 전체를 하나의 5초 deadline이라고 부르지 않는다. custom hook과 callback 내부 비-I/O 대기,
  실제 antivirus/network filesystem과 OS process termination 거부는 live 검증 전이다.

## 2026-08-23 — task decision과 effect는 두 시간 경계를 직전 재검증

- 상태: 승인, source 구현·deterministic race 회귀 완료; live Host/GPU effect 대기
- 결정: worker admission, worker/runtime-bound decision 반환 뒤와 각 effect executor 진입 직전에 wall
  grant expiry와 process-local monotonic task budget을 재검사한다. raw asyncio task cancellation도 같은
  currentness에 포함하며 workspace stage/test await 직후 receipt 정규화 전과 approval 직전에도 검사한다.
  만료 decision이나 취소를 삼킨 receipt는 effect 권한이 없다. exact pending stage cancel만 fail-closed
  cleanup으로 deadline 뒤 허용한다.
- 이유: asyncio timeout cancellation을 worker가 삼키거나 timeout-zero coroutine이 first yield 전 실행될 수
  있고, authorize 뒤 wall expiry도 effect 권한이 아니다. await timeout 자체는 executor 미진입 증거가 아니다.
- 근거: `task_loop_runtime.py`, `tests/core/test_task_loop_runtime.py`,
  `docs/worklog/2026-08-23.md`의 swallowed-cancellation·post-stage/test·pre-approval expiry 회귀와
  task-loop 91 passed, subtests 127.
- 영향: human approval wait의 compute-budget 제외와 approval manager TTL은 유지한다. late worker decision은
  normalize/authorize/tool dispatch로 진행하지 않으며, 외부 effect가 이미 dispatch된 뒤 process가 죽어
  결과를 잃는 outcome ambiguity는 계속 자동 재시도하지 않는 별도 live 경계다.

## 2026-08-23 — 음성 입력 lease는 bounded durable owner와 단일 async transition owner를 사용

- 상태: 승인, source 구현·deterministic fault/race 회귀 완료; live device/filesystem/Discord 대기
- 결정: voice lease load/mutation과 Discord owner observation은 Fast continuity의 shared warm killable
  artifact child와 bounded deadline을 사용한다. outcome-unknown은 canonical disk-first exact payload와
  durable sync 확인만 성공이며, 그 외에는 blocked latch·503으로 닫고 mutation을 재실행하지 않는다.
- 결정: async lease operation은 `to_thread`에서 실행하고 cancellation 전에 shield-drain한다. Local ON,
  chat/UI OFF publish, authenticated status/stop-release, Discord acquire/release와 retirement prepare/complete의
  authoritative transition point는 같은 per-event-loop lock을 쓴다.
- 이유: kill 불가능한 filesystem thread와 lock 밖 late completion은 Bot API 정지, 동시 capture, stale OFF
  또는 retirement가 successor owner를 해제하는 false-green을 만들 수 있다.
- 근거: `voice_input_lease.py`, `fast_control_api.py`, `tests/runtime/test_voice_input_lease.py`,
  `tests/runtime/test_local_voice_admission_api.py`, `tests/runtime/test_fast_control_api_tools.py`와
  `docs/worklog/2026-08-23.md`의 pre/post-replace/read stall 및 transition race 회귀.
- 영향: 불명확한 persistence는 availability failure로 닫히며 같은 process에서 자동 회복·blind retry하지
  않는다. 실제 장치·Discord·antivirus/filesystem fault는 live 검증 전이다.

## 2026-08-23 — accepted conversational turn은 fallible reply work 전에 durable하다

- 상태: 승인, direct Main local mic·Control Page source 구현 및 restart/cancellation 회귀 완료; live fault 대기
- 결정: Discord voice와 direct Main `local_mic`은 accepted exact source turn을 user-only로 durable commit한
  뒤에만 room owner·TurnScope·LLM/TTS를 시작한다. Control Page 일반 text도 session lock 안에서 exact
  user-only turn과 durable receipt를 만든 뒤 scope/LLM을 시작하고, 성공 시 같은 `complete_turn_id`에
  assistant만 붙인다. precommitted current user는 prompt 복사본에서 exact 검증 후 제거해 final payload에 한 번만 둔다.
- 결정: Control Page continuity commit 중 caller cancellation은 physical commit을 shield-drain하는 동안
  session lock을 유지하고 완료 뒤 원래 cancellation을 재전파한다.
- 이유: accepted user row보다 reply work가 먼저 시작되거나 disk commit보다 successor mutation이 먼저면
  LLM 실패·취소·restart가 사용자의 턴을 지우거나 중복 user/assistant row를 만들 수 있다.
- 근거: `voice_orchestration.py`, `control_page_text_runtime.py`, `session_turn_runtime.py`,
  `session_memory_state.py`, `llm_context_assembly.py`와 관련 restart·prompt-once·commit-drain 회귀.
- 영향: receipt 전 hard exit는 보장하지 않고 자동 reply 재실행도 하지 않는다. 실제 process hard-exit,
  Discord/TTS·filesystem 장애에서 다음 턴이 미응답 문맥을 잇는지는 live 검증 전이다.

## 2026-08-23 — STT inference 소유권은 physical worker 수명을 따른다

- 상태: 승인, source timeout/cancellation 회귀 완료; live network/GPU fault 대기
- 결정: blocking STT를 실행하는 `to_thread` wrapper가 caller timeout/cancellation으로 중단돼도 shared
  inference lock은 실제 thread가 반환할 때 done callback에서만 해제한다. caller의 기존 terminal 신호는
  즉시 유지하고 late exception은 소비한다.
- 이유: asyncio wrapper의 수명은 이미 시작된 OS thread의 종료 증거가 아니며, lock을 먼저 풀면 같은
  client/GPU에 물리 추론이 겹칠 수 있다.
- 근거: `stt_task_runtime.py`, `tests/voice/test_stt_task_runtime.py`의 timeout/cancel physical-worker 회귀.
- 영향: physical client가 영구 정지하면 lock도 availability fail-closed로 남는다. production HTTP timeout은
  정상 network 대기를 제한하지만 실제 driver/GPU hard stall은 live 검증 전이다.

## 2026-08-24 — 외부 전송과 canonical continuity는 durable intent와 물리 effect 수명에 결박

- 상태: 승인, source 구현·deterministic cancellation/restart 회귀 완료; live Discord/filesystem fault 대기
- 결정: Discord command, search follow-up과 assistant autonomy 전송은 별도 queue/schema를 만들지 않고
  기존 ingress recovery journal을 공유한다. exact source/action-run ID로 intent와 response를 durable하게
  결박한 뒤 `delivery_inflight -> physical send -> exact Discord receipt -> delivery_succeeded -> canonical
  continuity -> complete` 순서를 지킨다. autonomy 권한은 original grant/run ID가 effect 직전에도 current인지
  다시 검사하며 새 grant로 만료를 연장하지 않는다.
- 결정: timeout·cancellation처럼 원격 수락 여부가 불명확하면 successor effect를 차단하고 자동 재전송하지
  않는다. definitive pre-effect 거부만 journal을 안전하게 닫고 retry할 수 있다. search restart adoption은
  pre-send generation baseline, unique source/delivery pair와 exact receipt에 더해 source message를 exact
  reply/reference한 유일한 bot-authored same-content message만 채널 증거로 인정한다. 최초 restored snapshot
  recovery가 끝날 때까지 새 Discord text/voice ingress를 차단한다.
- 결정: journal completion은 memory/cognitive/question 같은 선택 projection보다 먼저 기록한다. post-effect
  memory integrity 실패도 exact outcome audit와 state/cursor/fence를 먼저 내구화한 뒤 fixed type만 재전파한다. LLM은
  bounded task loop에서 시도·검증 결과를 다시 받아 재계획할 수 있지만, 하네스의 승인 정책이나 자기
  코드를 무승인으로 고치지 않는다. workspace mutation은 계속 exact staged approval과 post-apply receipt를
  요구한다.
- 이유: send 성공과 local task 완료, checkpoint generation 증가와 exact pair durability, caller cancellation과
  physical child 종료는 서로 다른 사실이다. 이를 하나의 성공 boolean으로 합치면 restart 중복 전송,
  stale generation 오귀속과 권한 철회 뒤 effect가 생긴다.
- 근거: `conversation_ingress_recovery.py`, `discord_command_session_runtime.py`,
  `search_followup_runtime.py`, `discord_app_composition_runtime.py`, `autonomy_runtime_factory.py`와
  `tests/core/test_search_followup_*`, `tests/core/test_autonomy_*`,
  `tests/discord_io/test_discord_*`, `docs/worklog/2026-08-24.md`.
- 영향: remote timeout의 실제 수락 여부와 OS/network/GPU hard stall은 추측하지 않고 availability를
  fail-close한다. 실제 Discord gateway와 filesystem/antivirus fault는 별도 승인된 live 검증 전이다.

## 2026-08-24 — OmniVoice 가속은 bounded FlashInfer recipe로 고정

- 상태: 2026-08-25 CUDA 12.9 image build, RTX 5090 health와 post-STT latency까지 live 완료;
  실제 청취·8초 초과 fallback·장기 soak 대기
- 결정: `omnivoice==0.1.5`를 유지하고 공식 signed feature commit
  `28bc0889d92110491d726a9c79f26a895db5a074`의 단일 FlashInfer 모듈만 SHA-256으로
  검증해 이식한다. pinned CUDA 12.9.2 base, Torch/Torchaudio 2.8.0+cu129,
  TorchCodec 0.7.0+cu129, FlashInfer Python/Cubin 0.6.15.post1과 JIT cache +cu129를 직접 고정한다.
- 결정: iterative bidirectional decode에서 쓰지 않는 KV cache를 끄고 request 종료마다 수행하던
  Python/CUDA 전역 GC를 제거한다. CUDA graph는 2/4/8초 세 bucket과 overhead 512로 제한하고
  8초 초과 또는 budget 밖 shape는 eager로 되돌린다. RTX 5090의 runtime JIT는 끄며
  module-global context 때문에 동시 inference는 1로 고정한다. 기본 generation step은 12다.
- 이유: post-STT 측정에서 TTS first PCM이 warm core latency의 큰 단일 구간이었고, 이 경로는
  모델·음질 의미를 바꾸는 blockwise streaming 없이 allocation, attention kernel과 graph launch
  overhead를 줄일 수 있다. CUDA 12.8도 일반적인 SM120 target을 제공하지만, 선택한 FlashInfer
  0.6.15의 실제 SM12 normalization 경로는 CUDA 12.9 이상을 요구해 RTX 5090 warmup에서 실패했다.
- 근거: `docker/Dockerfile.omnivoice`, `docker/run_omnivoice.sh`,
  `docker-compose.fast-control.yml`, external `omnivoice_server/services/model.py`,
  `tests/runtime/test_docker_compose_contract.py`, external `tests/test_model_cache.py`,
  [[worklog/2026-08-24]], [[worklog/2026-08-25]].
- 영향: image build assertion은 Torch/Torchaudio/TorchCodec과 FlashInfer Python/Cubin/JIT
  package를 확인한다. TTS runtime health와 Compose/service-manifest/checker는 health에 노출된
  Torch/CUDA/FlashInfer Python/JIT, backend, bucket, concurrency와 step을 exact·type-strict하게
  검증한다. `recipe-e8151492550b`는
  RTX 5090에서 CUDA graph health와 warm TTS first PCM p50/p95 `193.3/215.4ms`를 통과했다.
  sentence streaming은 유지하고 실험적 blockwise 경로는 계속 비활성이다. CUDA base digest는
  고정했지만 전이 wheel 전체 lock이 없어 완전 bit-reproducible build라는 뜻은 아니다.

## 2026-08-25 — Main LLM 지연은 검증된 첫 답변 PCM의 end-to-end 목표로 최적화

- 상태: 목표 구조와 fixed optimizer/lab/lifecycle validation source 구현, native SM120 TTS-ready graph A/B와
  독립 graph-on 재현, local config 기본값 승격 완료; production observer, repeated campaign/soak와
  speaker/Discord SLO 승격은 대기
- 결정: 성능 문제의 기본 해법은 임시 filler, 안전 경계 생략이나 단일 subsystem 수치 개선이 아니라
  root-cause 계측, end-state 구조, 회귀 방지와 운영 rollback을 포함한다. warm 일반 대화의 target은
  post-STT answer first PCM p50/p95 `<=600/750ms`이며 filler acknowledgement는 answer 지표에서 분리한다.
- 결정: durable ingress와 memory exposure, stream safety, evidence/approval, exact cancellation/playback owner를
  유지한 채 content-free stage trace, 공용 versioned Prompt ABI, terminal-drain+second-suffix cache proof,
  단일 Main realtime lane, irreversible speech commit과 bounded TTS overlap으로 수렴한다. GPU0 Main+TTS,
  GPU1 STT+support는 current fact가 아니라 controlled A/B할 우선 후보이며 turn별 dynamic migration은 하지 않는다.
- 결정: LLM은 typed allowlist 안에서 latency candidate를 최대 12회 제안하고 fixed owned harness의 aggregate
  receipt만 받아 재시도할 수 있다. harness는 immutable identity, repeated ABBA, restart readiness, finalist
  soak와 fixed safety/quality/resource evaluator를 소유한다. unknown cleanup은 다음 run을 허용하지 않는
  `CLEANUP_REQUIRED`이며 host-wide lock과 stable-zero reconciliation으로만 해제한다. primary 실행 실패
  상태는 cleanup 지연으로 덮지 않고, 실행 결과와 최종 cleanup 증거를 독립적으로 보존한다.
- 결정: full-SWA는 동일 모델의 typed `0|1` 실험 차원이다. SWA0은 strict second-suffix cache proof가 0%라
  readiness 부적격이고 SWA1만 strict cache/Prompt ABI와 TTS-ready A/B를 통과했으므로 local 기본값은 `1`이다.
  WDDM에서는 pre-run GPU baseline과 idle utilization을 만족하지 못한 표본을 채택하지 않으며 backend
  관측만으로 causal 효과를 주장하지 않는다.
- 결정: RTX 5090 Main의 first-use PTX JIT tail은 CUDA 12.9.2 `120a-real` native-only build로 제거한다.
  side-by-side build는 Main/GPU0에만 read-only 선택하고 GPU1 LLM과 기존 build를 바꾸지 않는다. 선택 build는
  llama.cpp root containment, reparse-free leaf, server binary와 content identity를 검증한다. TTS-ready A/B가
  clean하게 끝났으므로 native SM120을 local 기본 Main build로 선택한다. 다른 build override도 exact
  `120a-real` contract를 만족해야 하며 일반 build로 silent fallback하지 않는다. 기존 multi-architecture
  build는 GPU1 LLM용으로 보존한다.
- 결정: local Main numeric 기본값은 batch `2048`, ubatch `2048`, cache reuse `256`, cache RAM `8192MiB`,
  CUDA graph `1`, full-SWA `1`이다. 첫 TTS-ready A/B에서 graph-on이 answer first PCM의
  first-after-warmup, resident p95와 idle을 모두 낮췄고 독립 run에서 first/resident 개선이 재현됐다.
  idle tail은 변동했으므로 broader restart/idle 분포를 계속 측정한다. 이 config default 선택은 LLM의
  automatic self-promotion 권한이나 observer/canary/soak 완료를 뜻하지 않는다.
- 결정: production warmup은 단순히 같은 문자열 길이나 cache hit만 확인하지 않는다. Fast와 Core/Discord가
  실제 사용하는 canonical system prefix로 Prompt ABI를 독립 계산하고 warmup ABI와 exact match해야 HTTP와
  readiness를 진행한다. prompt 원문은 evidence에 기록하지 않는다.
- 결정: active harness, evaluator, observer, authority, safety/tool/memory 계약과 production promotion은 LLM의
  자기 수정·자기 승인을 허용하지 않는다. production 전이는 candidate/run/evaluation에 결박된 runtime
  observer receipt와 exact 사람 승인, canary/soak/rollback receipt를 요구한다. 선택적 durable journal은
  restart 뒤 receipt replay와 accepted/rollback fork를 CAS로 차단한다. benchmark report를 runtime
  admission/fallback으로 사용하지 않는다.
- 결정: public coordinator는 observer capability나 factual observation issuer를 제공하지 않는다. 고정
  source-reading worker가 별도로 결박되지 않으면 observation은 `runtime_observer_unavailable`이며
  approval 이후 상태로 진행하지 않는다.
- 이유: 2026-08-25 measured warm p50/p95는 first safe delta `589.5/688.9ms`, first sentence
  `637.7/732.4ms`, TTS first PCM `193.3/215.4ms`, 합산 `818.2/947.9ms`였다. first delta 이후
  sentence까지는 약 `45ms`라 첫 문장 splitter나 speculative tail보다 Main 이전/queue/prompt cache/prefill/raw
  first token 분해가 우선이다. 현재 표본 10개는 promotion 증거가 아니다.
- 이유: 기존 CUDA library는 RTX 5090 native cubin 없이 `sm_52` PTX만 포함했고 첫 resident append의 약
  11.3초 tail은 graph on/off 모두에서 나타났다. `sm_120a` cubin만 포함한 native build의 graph-off 진단은
  strict cache `33/33`, validity failure `0`, resident first-PCM p50/p95 `239.9/292.1ms`, TTFT
  `38.298/57.301ms`였고 11.3초 tail을 제거했다. 다만 이 first-PCM 표본은 실제 TTS 합성 readiness 정정 전이라
  end-to-end SLO나 graph 기본값의 승격 증거가 아니다.
- 이유: readiness 정정 뒤 graph-off/on은 모두 cache `33/33`, validity failure `0`, clean cleanup을 통과했다.
  graph-on은 graph-off 대비 answer first PCM first-after-warmup/resident p95/idle을
  `314.0/298.7/324.7ms`에서 `294.6/262.6/228.8ms`로 낮췄고 resident p50은
  `253.1ms`에서 `207.7ms`로 낮췄다. 독립 graph-on은 first-after-warmup `278.8ms`, resident
  p50/p95 `205.8/259.0ms`로 first/resident 결과를 재현했다. 반면 idle은 `387.8ms`로 변해 graph 선택과
  별개로 장기 idle tail을 남은 위험으로 둔다. 두 graph-on run의 resident TTS first-PCM p95는
  `93.1/93.3ms`였다.
- 근거: [[MAIN_LLM_LATENCY_TARGET_ARCHITECTURE]], [[worklog/2026-08-25]],
  `tools/post_stt_latency_benchmark.py`, `tools/gpu1_latency_benchmark.py`,
  `evelyn_core/runtime/evelyn_core/llm_warmup_runtime.py`,
  `evelyn_core/runtime/evelyn_core/fast_control_api.py`.
- 영향: 공용 Prompt ABI v2, 전역 Main admission gateway와 JIT foreground reservation, 18-stage trace,
  shared speech commit, exact per-leg lifecycle과 WDDM cleanup을 갖춘 fixed Docker lab/runner/evaluator,
  bounded LLM feedback loop와 external-observer receipt validation lifecycle을 source에 구현했다. SWA0의
  live strict cache proof는 부적격이었고 SWA1/native graph-on을 local source/config 기본값으로 선택했다.
  다음은 30/200 repeated campaign, restart/1,000-turn soak, speaker/Discord first-write와 production observer
  worker다. SLO 미달일 때만 동일 모델
  backend bakeoff, 이후 model/quantization과 blockwise TTS를 검토한다. 이번 source 변경은 현재 실행 중인
  runtime이나 live 서비스를 재배치·재시작하지 않았다.

## 2026-08-26 — revised STT 기준선을 Qwen3.8 교체 전에 고정

- 상태: 승인
- 결정: P0-1/P0-2/P0-3 뒤 microphone·Discord 없는 revised STT image/GPU1 검증을 P0-4로
  수행하고, Qwen3.8 교체를 P0-5로 미룬다. 현행 Qwen3-14B와 old/new STT image를 같은
  warmup 2+measured 20 overlap으로 비교하며 private 40 positive/10 negative batch/stream corpus,
  cancel/successor, 3회 restart와 cleanup을 통과해야 새 STT image를 기준선으로 승격한다.
- 이유: STT image와 Qwen model을 동시에 바꾸면 GPU1 latency·VRAM·오류 회귀의 원인을 분리할 수
  없다. 현행 14B에서 STT 기준선을 먼저 만들면 이후 Qwen3.8 A/B가 한 변수만 바꾸게 된다.
- 근거: `plan.md` P0-4, [[KOREAN_ASR_TARGET_ARCHITECTURE]], `tools/gpu1_latency_benchmark.py`.
- 영향: P0-4는 STT image/model/GPU와 headless API만 기동하며 microphone, speaker, Discord,
  Minecraft는 시작하지 않는다. P0-5는 verified revised STT image를 양쪽 Qwen 조건에 동일 사용한다.
- 대체한 결정: P1-1의 STT image build/GPU baseline을 전체 실제 음성 E2E와 함께 실행하던 순서.

## 2026-08-26 — Qwen3.8 교체 후보는 GPU1 전용 27B Q4_K_M side-by-side로 고정

- 상태: 구현 및 제한된 GPU1 live 검증 승인됨·P0-5 선행 gate 대기
- 결정: 공식 Qwen3.8에 14B가 없으므로 GPU1 RTX 3090 24GB의 유일한 승격 후보를
  `lmstudio-community/Qwen3.8-27B-GGUF` revision `5a7da681...`의 표준
  `Qwen3.8-27B-Q4_K_M.gguf`, SHA-256 `e00082f7...e520`으로 고정한다. Q3 자동 fallback,
  dynamic/UD quant, Q5 이상, MTP, vision/mmproj, reasoning과 context 확대는 이번 변경에서 제외한다.
- 결정: 현행 llama.cpp와 Qwen3-14B를 덮어쓰지 않는다. llama.cpp commit `4d19b287...`를
  별도 worktree에서 SM86으로 build해 `minecraft_llm`에만 read-only mount하고 matching
  server/shared libraries를 사용한다. 기존 artifact는 P1-1/P1-2 뒤 별도 삭제 승인 전까지 보존한다.
- 결정: 20-case production-role A/B, canonical source 회귀, warmup 2+measured 20 GPU1
  Fast Main+Qwen+STT overlap, 3회 restart를 모두 통과할 때만 승격한다. Qwen p95 6,000ms,
  STT p95 1,200ms, GPU min free 2,048MiB, error/timeout/OOM 0을 넘지 못하면 14B로 rollback한다.
- 이유: Qwen3.8-27B Q4는 현행 14B보다 약 7.8GB 커 24GB에 이론상 들어가도 ASR 동시부하
  여유가 작다. 모델 이름이나 단독 load만으로는 품질·VRAM·latency·CUDA build 호환을 증명하지
  못하며, 기존 runtime을 보존한 격리 A/B가 가장 작은 안전한 교체 단위다.
- 근거: `plan.md` P0-5, `docker-compose.fast-control.yml`, `tools/gpu1_latency_benchmark.py`,
  official Qwen3.8 collection/model card와 pinned community GGUF revision/hash 감사.
- 영향: 사용자 승인 전 source/config/model/build/Docker/GPU 상태 변경은 0이다. 승인 뒤에도
  Minecraft/Discord/microphone/speaker는 기동하지 않으며 Q4 gate 실패를 다른 quant로 우회하지 않는다.

## 2026-08-26 — Main finalist cleanup은 exact owner와 bounded WDDM idle을 분리

- 상태: attempt 5 실패 분석과 attempt 6 복구 설계 동결·승인 대기
- 결정: run-owned leak 권위는 terminal cleanup의 process/GPU allocation/artifact 0/0/0과
  global Docker empty/original-state로 유지한다. unrelated WDDM activity는 동일 driver/VRAM,
  연속 3회 utilization 10% 이하, free ratio 75% 이상으로 별도 증명하며 baseline/post MiB는 남긴다.
- 결정: signed measurement receipt와 content-free timing을 host restoration 전에 atomic 보존한다.
  host proof와 evaluation이 없으면 계속 `cleanup_required`이고 promotion/offline-completed 판정은 없다.
  ignored finalist driver는 tracked `tools/` source로 옮겨 checkpoint/bundle에서 재현한다.
- 이유: attempt 5는 workload와 exact-owned cleanup, Docker OFF를 달성했지만 unrelated GPU0 WDDM
  allocation 변화로 두 180초 baseline wait가 실패했다. 그 전에 receipt를 persist하지 않아 완료한
  측정도 terminal artifact에서 사라졌다. baseline tolerance 확대나 WDDM 제거가 아니라 owner와
  global idle의 역할을 분리해야 재현성과 leak safety를 함께 보존한다.
- 근거: attempt 5 content-free terminal artifact, `tools/main_latency_host_lifecycle.py`,
  `runtime_artifacts/validation/run_main_latency_finalist_attempt2.py`, `plan.md` P0-1 recovery design.
- 영향: attempt 5 artifact는 사후 수정하지 않는다. 동일 workload attempt 6의 fresh-process verifier가
  통과해야 P0-1이 완료되며 P0-2/P0-3/P0-4/P0-5는 그 전까지 시작하지 않는다.

## 2026-08-27 — Mindcraft dependency는 pristine pin과 parent overlay를 단일 source of truth로 유지

- 상태: 구현·clean overlay·immutable recovery checkpoint 검증 완료
- 결정: `external/mindcraft`는 pinned commit 그대로 clean하게 유지하고 Evelyn의 patch, overlay와
  generated auth-cache ignore는 `external/mindcraft_evelyn/`에서만 소유한다. clean 재구성 gate는
  Docker build와 같은 순서의 GNU `patch --dry-run -p1`/apply 뒤 parent overlay copy를 사용한다.
- 이유: raw submodule `.gitignore`를 직접 수정해 hygiene test를 맞추면 pin+overlay 계약과 bundle의
  clean 재현성이 깨진다. 반대로 auth cache를 무시하지 않으면 generated placeholder가 dependency
  status를 오염시킨다.
- 근거: `external/mindcraft_evelyn/evelyn.patch`,
  `tests/hygiene/test_dependency_security_policy.py`, `plan.md` P0-3,
  [[worklog/2026-08-27]].
- 영향: submodule 새 commit이나 pointer 변경은 없다. clean checkout, Docker build와 hygiene test가
  같은 parent-owned overlay 계약을 검증하며 generated auth cache는 Git stage 대상이 아니다.

## 2026-08-27 — STT vLLM engine capacity는 30초 단일 audio 경계로 고정

- 상태: source·image health·old/new diagnostic overlap 2+20 검증 완료;
  private corpus·cancel/successor·cold restart·production 승격 대기
- 결정: revised STT의 vLLM engine는 `max_model_len=8192`,
  `gpu_memory_utilization=0.35`, `max_num_seqs=1`, `limit_mm_per_prompt.audio=1`,
  `maxAudioSec=30`으로 고정한다. startup은 engine의 actual applied config를 다시 읽어
  이 exact contract와 다르면 ready를 거부하고 health에는 실제 값만 공개한다.
- 이유: 첫 vLLM image는 model의 `65,536` context를 상속해 KV cache에 약 `7.0GiB`를
  요구했지만 memory fraction `0.35`의 실제 budget에는 약 `0.89GiB`만 남아 startup이
  fail-close했다. 추정 한계 `8,336`보다 작은 `8,192`는 KV 크기를 bounded하고, service의
  30초 input cap과 단일 physical worker에 sequence/audio concurrency `1`을 함께 고정한다.
- 근거: `evelyn_core/runtime/evelyn_core/stt_service.py`,
  `tests/voice/test_stt_service_contract.py`, `tests/voice/test_stt_stream_service.py`,
  `tools/gpu1_latency_benchmark.py`, `plan.md` P0-4.
- 영향: STT endpoint, `Qwen/Qwen3-ASR-1.7B`, memory fraction `0.35`, public transcription schema/API,
  launcher/admission은 변경하지 않는다. old/new 2+20 diagnostic PASS는 image-ready 근거이지
  promotion 자격 자체가 아니며, private corpus·cancel·restart·cleanup이 닫힐 때까지 새 STT
  image 승격과 P0-5 Qwen3.8 시작을 모두 차단한다.

## 2026-08-27 — Discord capture credential은 Windows 사용자 경계에 암호화 보존

- 상태: source·비라이브 회귀 검증 완료, 최초 live 저장/인증 대기
- 결정: Discord capture credential은 repository, 환경변수, argv, Docker metadata와 문서에 저장하지
  않는다. Windows CurrentUser DPAPI와 현재 사용자/SYSTEM 전용 ACL의 host cache에 암호문만 두고,
  기존 redirected stdin handoff 뒤 평문 byte buffer를 즉시 zeroing한다.
- 결정: 명확한 Discord login rejection일 때만 exact cache를 제거한다. Docker, gateway, network, TTL과
  일반 capture 실패에는 보존해 반복 prompt loop를 만들지 않는다. 교체가 필요하면 Docker를 시작하지
  않는 explicit clear parameter를 사용한다.
- 이유: 매 run마다 secret을 폐기하던 기존 계약은 장애 재시도마다 사용자 입력을 요구했다. 평문 `.env`
  또는 process/container 환경변수 재사용은 편하지만 secret 노출면을 넓힌다. CurrentUser DPAPI는 새
  dependency 없이 Windows 계정 경계에서 재사용성과 at-rest 보호를 함께 제공한다.
- 근거: `tools/discord_capture_credential.psm1`, `tools/run_discord_voice_corpus_capture.ps1`,
  `tools/discord_voice_corpus_capture.py`, `tests/tools/test_run_discord_voice_corpus_capture_launcher.py`,
  `tests/voice/test_discord_voice_corpus_capture.py`, commit `314a358`.
- 영향: 최초 유효 credential만 한 번 hidden prompt로 저장한다. 같은 Windows 사용자 권한의 악성
  process는 CurrentUser DPAPI 자체를 호출할 수 있으므로 이 설계의 위협 경계 밖이며, 다른 OS principal,
  accidental plaintext persistence와 ambiguous failure deletion은 fail-closed한다.

## 2026-08-28 — Discord corpus capture admission과 모델 진단을 분리

- 상태: source 구현·guided live capture 완료, 첫 사후 모델 진단 FAIL
- 결정: Discord corpus는 고정 phrase를 텍스트로 하나씩 안내하고 prompt 이후 시작한 한 발화만
  transport/shape/activity/duplicate gate로 저장한다. 평가 대상 STT의 결과는 capture admission, clip 선별,
  자동 retry나 삭제에 사용하지 않는다.
- 결정: exact 10개가 수집된 뒤 pinned loopback STT에서 각 WAV를 정확히 한 번만 판독한다. pre/post health,
  nonempty, similarity, same-index unique-best, normalized whole-utterance exact와 critical entity/action exact를
  aggregate-only로 판정한다. cleanup과 호스트 복구 뒤에만 봇이 고정 성공/실패 텍스트를 한 번 전송하며
  PASS여도 corpus를 자동 승격하지 않는다.
- 이유: 평가할 모델로 잘 인식된 clip만 admission하면 corpus가 편향되고 실제 실패를 숨긴다. capture 중
  음성 안내는 수집 오디오를 오염시킬 수 있으며, fuzzy similarity만으로는 숫자·entity·반대 동작과 trailing
  contradiction을 놓칠 수 있다.
- 근거: `tools/discord_voice_corpus_capture.py`, `tools/discord_corpus_model_diagnostic.py`,
  `tools/discord_capture_status_notify.py`, 관련 tests, [[worklog/2026-08-28]].
- 영향: 실패 staging은 자동 삭제·재녹음·승격하지 않는다. 사람이 읽기 어려운 phrase는 별도 frozen set
  변경으로 해결하고, 같은 attempt의 판정 기준이나 transcript를 보고 사후 조정하지 않는다.

## 2026-08-28 — Discord 자동 진단과 사용자 corpus 선택 승인을 별도 evidence로 유지

- 상태: 구현·명시적 사용자 승인·live 후속 알림 검증 완료
- 결정: 사용자는 operational gate가 정상인 exact guided 10개를 자동 model diagnostic FAIL 상태에서도
  `domain-discord-pcm` 후보로 수동 선택할 수 있다. 이때 자동 report를 수정하지 않고 report SHA와 exact
  capture marker SHA를 함께 기록한 `evelyn.discord-corpus-user-acceptance.v2` sidecar를 원자적으로 만든다.
- 결정: legacy diagnostic v1은 marker digest가 없으므로 receipt에 `explicit_user_pairing`과
  `sameRunCryptographicBinding=false`를 고정한다. content digest는 서명이 아니다. 후속 diagnostic v2는
  같은 canonical corpus read에서 marker SHA를 report에 넣고 verifier가 exact match를 요구한다.
- 결정: receipt scope는 `domain-discord-pcm-10-only`, production promotion은 false다. 원본이 바뀌면
  verifier가 stale로 거부하고, Discord에는 기존 PASS가 아닌 이 한계를 포함한 고정 `accepted` 메시지를 보낸다.
- 이유: 사용자 선택권을 반영하면서 자동 관측 실패를 성공으로 위조하거나 50-item benchmark·restart·cleanup
  gate까지 암묵적으로 면제하지 않기 위해서다.
- 근거: `tools/discord_corpus_user_acceptance.py`, `tools/discord_capture_status_notify.py`, 관련 tests,
  [[worklog/2026-08-28]].
- 영향: accepted guided 10개는 미래 explicit 50-row selection에 넣을 수 있다. 나머지 40개가 없으므로
  assembly, P0-4 promotion과 P0-5는 계속 차단된다.

## 2026-08-28 — 프로젝트 지식과 개인 개발 기록의 소유권 분리

- 상태: 승인·로컬 적용 완료
- 결정: `docs/`는 Evelyn의 프로젝트 지식과 검증 근거의 원본으로 유지한다. 프로젝트를 넘어 이어지는
  개인 성과·재사용 가능한 배움·개인 작업 방식 결정은 저장소 밖의 별도 로컬 Developer Vault가 소유한다.
- 결정: 개인 Vault는 프로젝트 상세를 복사하지 않고 원본 문서 링크만 남긴다. Evelyn runtime memory와
  private transcript·audio·log·runtime artifact는 어느 개발 문서 Vault에도 기록하지 않는다.
- 이유: 코드·테스트와 함께 버전 관리해야 하는 프로젝트 사실과, 프로젝트가 끝난 뒤에도 이어져야 하는
  개인 개발 이력의 수명과 독자가 다르기 때문이다. 소유권을 나누면 중복과 상태 불일치를 피할 수 있다.
- 근거: 사용자 승인, [[00_EVELYN_HOME]], 루트 `AGENTS.md`, 전역 Codex 작업 지침.
- 영향: Evelyn 작업 문서는 기존 working-memory loop를 유지한다. 의미 있는 개인 개발 결과가 있을 때만
  별도 Vault에 짧은 요약과 프로젝트 원본 링크를 남기며, 새 내용이 없으면 기록하지 않는다.

## 2026-08-28 — private archive는 단독 writer와 전 sink 증명 전 완료 금지를 사용

- 상태: P1-4 source/offline 핵심 경계 구현, live 검증 대기
- 결정: private archive 파일은 `bot_api`만 변경한다. C:의 SQLite 원본, D:의 검증 replica, DB 밖 anchor와
  OS writer lease를 사용하고 Discord·Control Page·Minecraft는 목적 제한 서명 API로만 접근한다. 기능은
  기본 OFF이며 별도 운영 승인 전 기존 surface를 자동 전환하지 않는다.
- 결정: 일반 Discord 사용자는 guild slash command의 invoker-only ephemeral 화면에서 자기 text/final STT와
  직접 연결된 답변·task·Minecraft 결과만 본다. 전체 열람은 UAC로 고정한 Windows 신원, 등록 Discord DM
  OTP, loopback HTTPS를 모두 통과한 짧은 local admin session에만 준다. 두 capability와 token domain은
  결합하지 않는다.
- 결정: Discord mode는 로컬 마이크를 입력·참여·완료 조건에서 제외하고 실행 중 캡처도 정지한다. 화자는
  gateway의 exact SSRC→Discord user ID mapping으로 확정하고 현재 `display_name`은 표시·기록용 이름 snapshot으로만
  쓴다. mapping이 없으면 STT 전에 보류 후 폐기하며 lone-member/current-speaker 추정은 하지 않는다.
- 결정: 사용자 삭제와 30일 retention은 기준 DB와 D: replica를 함께 redaction하지만, `완전 정화` 표시는
  17개 필수 sink가 동일 deletion generation에 대해 실제 제거와 fresh negative recall을 모두 증명한 뒤에만
  허용한다. owner, lineage 또는 late-writer currentness가 없거나 후보가 손상되면 추측하지 않고
  `manual_review/local_cleanup_pending`으로 남긴다. 사용자가 지정한 법적·운영상 최소 event는 이름과 실제
  UTC 발생시각뿐이며 admin-only table에 자동 투영한다. 이것도 원래 발생시각+30일에 primary compaction과
  D: 검증 복제로 제거하고, 이미 만료된 직접/retention 삭제에서는 다시 만들지 않는다. 특정 법률을 새로
  주장하는 근거가 아니며 삭제 사유는 별도의 content-free tombstone으로만 표현한다.
- 결정: process owner는 삭제 work를 받는 즉시 exact lineage를 freeze하고 성공 뒤에는 release하지 않고 해당
  process 수명 동안 retire한다. 원격 owner receipt는 동일 request/generation/scope에 결박하며 전부 확인되기
  전 memory bundle writer fence와 `local_cleanup_pending`을 풀지 않는다. 시작 복원과 poll은 bounded keyset
  cursor를 사용하되 1,000개 뒤 요청도 순환 도달하게 한다.
- 이유: 단순 DB 행 삭제나 백업 복사는 메모리·cache·음성 임시물·세계 효과 계보의 재등장을 막지 못하며,
  같은 계정의 Discord OTP가 일반 사용자 열람권을 관리자 권한으로 바꾸는 근거도 아니다. 가용성보다
  개인정보 삭제의 거짓 성공 방지를 우선한다.
- 근거: `plan.md` P1-4, `evelyn_core/runtime/evelyn_core/conversation_archive.py`,
  `conversation_archive_purge.py`, `conversation_archive_memory_purge.py`,
  `discord_conversation_archive_runtime.py`, `fast_control_api.py`, 관련 source/offline tests,
  [[worklog/2026-08-28]].
- 영향: 17개 필수 sink owner route와 production process/memory/cognitive writer fence는 연결됐다. lineage가
  불완전하거나 attribution 없는 legacy/global cache와 등록되지 않은 사본은 계속 manual로 남긴다.
  local-private microphone은 archive ON에서 닫힌 상태지만 Discord mode의 완료 조건이 아니다. 이를 별도로
  열 필요가 생길 때만 owner-proof 정책을 결정한다. P1-5는 아래 별도 결정의 권한·승격 경계를 따른다.

## 2026-08-28 — 지식 작업은 내용 없는 실행 계약과 source-grounded draft로 검증

- 상태: 승인·source/offline 구현·전체 회귀 검증 완료, live Qwen 평가 대기
- 결정: 기존 task loop를 유지하고 새 agent framework나 daemon을 만들지 않는다. 각 실행은 principal token,
  skill origin, instruction/context manifest, tool·approval 권한, output schema, evaluator와 선택적 guidance
  version/digest를 `TaskWorkContract` 하나에 결박한다. Control Page에는 원문·goal·principal·evidence·reply가
  없는 process-local terminal `taskRecord` 최신 4건만 보인다.
- 결정: `review|summarize|explain|compare`는 허용된 source와 exact evidence reference로 만든 구조화
  `grounded_draft_ready`까지만 자동 완료로 인정한다. 이는 의미 정확성의 최종 주장과 외부 effect 권한이
  아니다. 고정 24-row 평가는 baseline/candidate를 같은 source·evaluator·tool grant와 실제 입력 digest에
  결박하고, 기존 Qwen broker의 capacity-one `task` admission에서 48회 직렬 실행한다. 취소·120초 row
  deadline은 현재 HTTP invocation을 취소해 끝까지 수거하고 successor를 시작하지 않은 채 content-free
  `incomplete` report를 원자 저장한다.
- 이유: 기존 bounded task loop, broker와 typed evidence가 이미 실행 안전 경계를 제공한다. 실행 계약과
  source-owned 평가만 더하면 결과의 출처·권한·평가 조합을 검토할 수 있고, 새 범용 agent 계층의 중복
  상태와 cancellation owner를 만들 필요가 없다.
- 근거: `../evelyn_core/runtime/evelyn_core/task_loop_runtime.py`,
  `../evelyn_core/runtime/evelyn_core/task_grounded_draft_runtime.py`,
  `../tools/task_agent_eval.py`, 관련 core/runtime/voice/tools tests, `../plan.md` P1-3,
  [[worklog/2026-08-28]].
- 영향: guidance는 system instruction·TaskGrant·approval·verifier보다 낮은 advisory 입력이다. 실제 Qwen
  24-row와 라이브 surface는 별도 승인 전 실행하지 않으며 source/offline 통과를 production 품질 증거로
  해석하지 않는다.

## 2026-08-28 — feedback 개선은 archive sole writer와 사람 소유 승격만 사용

- 상태: 승인·source/offline 구현·전체 회귀 검증 완료, Discord/카나리 live 대기
- 결정: 피드백 원문·교정과 `source_bound_candidate`는 P1-4 기준 DB·replica·삭제 계보가 소유한다. local
  operator가 private 사실·식별자·인용·원문/hash/embedding을 넣지 않았다고 직접 검토하고 일반 규칙을
  다시 작성한 `independent guidance`만 source-free 파생 버전이 된다. 원본 피드백 삭제는 source-bound
  사본과 무결성 계보를 삭제 상태로 만들지만 이미 독립화·승인된 active 버전은 자동 취소하지 않는다.
- 결정: `bot_api`만 correction, version, evaluation, approval, canary, activation, failure, rollback,
  revocation record와 active pointer를 쓴다. fixed eval 전건, action·version·archive generation에 결박된
  새 local-admin OTP, 서버가 수집한 exact 10개 grounded read-only receipt, generation CAS를 차례로
  통과해야 활성화한다. current contract/evaluator에 결박된 고정 실패만 rollback을 열고 revoke는 fresh OTP로
  대상과 descendants를 폐기한다. 재시작·source 삭제·집계 예외의 running canary는 raw durable receipt에서
  보수적으로 실패 종료하며 active pointer는 바꾸지 않는다.
- 결정: Discord `/피드백제출`은 현재 shared session에서 같은 Discord user ID가 실제 전달 완료된 자기 최신
  답변에만 review-only 피드백을 붙인다. text send 성공 또는 voice playback 완료 전 답변, stale session,
  다른 guild/channel/user는 거부하며 Discord에는 generalize/eval/approve/activate API를 제공하지 않는다.
- 이유: 자동 self-healing이 사람의 교정을 새 권한·tool·전역 사실로 승격하거나, 삭제된 source가 다시
  파생 버전에 스며드는 일을 막으면서도 사용자가 요구한 독립 개선 버전의 연속성은 유지해야 한다.
- 근거: `../evelyn_core/runtime/evelyn_core/feedback_improvement.py`,
  `../evelyn_core/runtime/evelyn_core/conversation_archive.py`,
  `../evelyn_core/runtime/evelyn_core/discord_conversation_archive_runtime.py`,
  `../evelyn_core/runtime/evelyn_core/fast_control_api.py`, 관련 archive/Discord/UI tests,
  `../plan.md` P1-5, [[worklog/2026-08-28]].
- 영향: source/offline 전체 회귀는 통과했지만 실제 Discord 전달 경쟁, local admin OTP, real-Qwen eval,
  10건 canary와 production pointer 전환은 live 증거가 아니다. archive 기본 OFF도 유지한다.

## 2026-09-02 — Markdown 장기기억과 선택형 exact archive를 혼동하지 않는다

- 상태: 활성 교정
- 결정: Obsidian-compatible Markdown vault를 Evelyn 장기기억의 durable source로 유지한다. P1-4의 30일
  exact private archive는 기억 기능의 선행조건이 아니라 별도 열람·삭제·감사용 선택 기능이다. 사용자가 그
  목적을 명시적으로 다시 요구하지 않으면 archive·BitLocker·live provision을 재개하지 않는다.
- 이유: 이미 존재하는 요약형 Markdown vault가 장기기억 저장 목적을 소유하는데, 미완료 plan 항목만 따라
  별도 원문 저장소와 host 암호화를 필수 다음 단계로 취급해 불필요한 작업을 만들었다.
- 재발 방지: `진행해`는 현재 합의한 목적 안의 진행만 뜻한다. 선택 기능·보안 설정·외부 효과로 넓히기 전에
  기존 기능이 목적을 충족하는지 확인하고 필요성을 사용자에게 명시적으로 확인한다.
- 영향: archive는 기본 OFF이고 실제 root/key/TLS·BitLocker·service 변경은 없었다. 기억 후속은 기존 Markdown
  vault의 deletion-current lineage와 live recall을 별도 범위로 검증한다.
