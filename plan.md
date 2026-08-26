# Evelyn 실행 계획

마지막 검토: 2026-08-26 KST

## 이 파일의 역할

- 앞으로 Evelyn의 실행 계획과 우선순위는 이 파일에서 관리한다.
- `docs/01_NOW.md`는 짧은 현재 상태, `docs/CURRENT_STATE.md`는 검증된 구현 상태,
  `docs/ACTIVE_RISKS.md`는 미해결 위험의 근거로 유지한다. 이 계획은 그 문서들을
  대체하지 않는다.
- 사용자가 구현을 승인한 뒤 작업을 시작할 때만 해당 항목을 `[~]`로 바꾸고,
  코드·테스트·필요한 실환경 증거가 모두 갖춰진 뒤에만 `[x]`로 바꾼다.
- 한 번에 가장 높은 우선순위의 미완료 항목 하나를 끝낸다. 새 기능은 현재 P0/P1
  완료 조건을 우회해 끼워 넣지 않는다.

상태 표기: `[ ]` 대기, `[D]` 설계 중, `[R]` 설계 동결·승인 대기,
`[~]` 승인된 구현/검증 진행, `[x]` 완료, `[!]` 외부 승인 또는 환경 대기.

## 절대 승인 게이트

### 승인 전

- 이 파일의 모든 구현 항목은 기본적으로 **미승인**이다. 계획에 적혀 있다는 사실,
  우선순위 지정, 설계 검토, 조사 계속 요청은 구현 권한이 아니다.
- 사용자가 대상 항목 ID와 함께 구현을 명시적으로 승인하기 전에는 코드, 테스트,
  설정, dependency, schema, migration, launcher, runtime 상태를 변경하지 않는다.
- 승인 전 허용 범위는 read-only 조사, 현행 실패 재현, 설계 문서와 `plan.md` 수정뿐이다.
  외부 서비스 기동이나 저장소/runtime artifact를 바꾸는 검증은 별도 승인이 필요하다.
- 이전에 승인되어 이미 실행 중인 검증은 새 구현으로 확장하지 않고 결과 관찰과 안전한
  원상복구만 허용한다.

### 설계 완료 조건

각 구현 항목은 승인 요청 전에 아래 내용을 `plan.md`에 고정한다. 하나라도 미정이면
구현 단계로 넘기지 않는다.

1. 문제와 검증된 root cause, 사용자에게 미치는 영향.
2. 목표, 포함 범위, 명시적 비범위와 제거할 임시 경로.
3. 선택한 해법과 버린 대안, 선택 이유.
4. 변경할 파일·함수·계약과 단계별 기계적 변경 목록.
5. 입력·출력·상태 전이, owner/currentness, timeout·취소·재시작·실패 처리.
6. 보안·privacy·내구성·호환성·성능 경계와 rollback 방법.
7. 먼저 실패해야 하는 회귀, focused/전체 테스트, 필요한 live 검증과 수치형 완료 조건.
8. 미해결 질문 0개와 예상 diff 범위.

설계가 이 조건을 만족하면 해당 항목을 **설계 동결·승인 대기**로 표시하고 사용자에게
승인을 요청한다. 승인은 사용자가 해당 항목을 특정해 “구현 승인”, “구현해”처럼
명시적으로 실행을 허용한 경우에만 성립한다.

### 승인 후 구현

- 승인된 설계를 위에서 아래로 그대로 옮긴다. 구현 단계에서는 새 기능, 새 추상화,
  opportunistic refactor, dependency 추가, 범위 확대나 별도 설계 선택을 하지 않는다.
- 각 설계 단계는 정해진 코드 변경과 대응 검증에 일대일로 연결한다. 구현 중 선택지가
  생기면 임의로 고르지 않고 즉시 중단한다.
- 새 사실, 예상 밖 부작용, 계약 충돌, 추가 파일·migration·외부 동작 필요가 발견되면
  설계 단계로 돌아가 이 파일을 갱신하고 다시 승인을 받는다.
- 테스트 실패는 승인된 설계의 명백한 오타·배선 오류만 기계적으로 수정할 수 있다.
  root cause나 계약이 달라지면 구현을 멈추고 재설계한다.
- 승인 범위 구현과 검증이 끝나면 실제 diff가 동결 설계와 일치하는지 대조한 뒤 완료를
  보고한다. 다음 항목은 자동으로 시작하지 않는다.

## 현재 판단

Evelyn에 지금 가장 필요한 것은 기능 추가가 아니라 **이미 구현된 핵심 대화 경로의
실패를 없애고 실제 장치·Discord에서 끝까지 검증하는 것**이다.

근거:

- Main LLM finalist attempt 5가 독립 실행 중이므로 이 실험이 끝나기 전에는 source
  identity를 바꿀 수 있는 코드 수정을 하지 않는다.
- 정규 테스트의 마지막 실행에는 2개 실패가 남았고, 2026-08-26 재실행에서도 둘 다
  재현됐다.
- 현재 작업 트리는 342개 수정 파일과 113개 미추적 기본 항목, 총 455개 상태 항목을
  포함한다. 새 기능을 더하기 전에 복구 가능한 기준점이 필요하다.
- Local/Discord 음성, 상태형 한국어 ASR, 실제 스피커 재생, 재시작 연속성은 source와
  offline 회귀가 많지만 한 번의 대표 실환경 E2E로 아직 닫히지 않았다.
- GPU1의 현행 Qwen3-14B Q4는 ASR 동시 부하를 통과했지만, 사용자가 요청한 Qwen3.8은
  공식 14B가 아니라 27B가 최소 dense 모델이다. 24GB RTX 3090에서는 표준 Q4_K_M만
  현실적인 후보이며 승격 전에 exact artifact·새 llama.cpp·동시부하·역할별 품질을 검증해야 한다.

## 실행 순서

### P0-1. 진행 중인 Main 지연 finalist를 안전하게 종결

- [x] attempt 5가 스스로 종료할 때까지 실험 소스·Docker·GPU owner를 변경하지 않는다.
- [x] 결과를 fresh-process verifier로 다시 열어 receipt, cleanup, host restoration,
  품질·오류·cache·GPU·통계 gate를 전부 확인한다.
- [x] `eligible`일 때만 graph-on 기본값을 유지한다. `inconclusive` 또는 실패면 자동
  승격하지 않고 최초 실패 gate의 root cause만 수정한다.
- [x] 결과와 원상복구 상태를 current-state/worklog에 기록한다.

완료 조건: signed terminal artifact가 독립 검증을 통과하고, Docker 원래 상태와
GPU/프로세스/임시 파일 cleanup이 확인되며, production 서비스는 의도한 상태를 유지한다.

#### Attempt 5 terminal 실패와 Attempt 6 복구 설계

상태: **[x] Attempt 7 terminal artifact·fresh-process verifier·host restoration 검증 완료**.

##### 문제·root cause·영향

- attempt 5는 warm 200/200, ABBA 20/20, restart eligible 40/40, soak 1,000/1,000을
  완주했고 exact-owned terminal cleanup은 process/GPU allocation/artifact 모두 0이었다.
  Docker도 initial OFF로 복구됐지만 terminal artifact는 `cleanup_required`, 최초 실패는
  `hostFinalizationError=gpu_restore_timeout`이었다.
- host guard가 pre-run GPU0 free memory와 post-run free memory를 256MiB 이내로 요구한다.
  실행 소유 resource가 0이어도 unrelated WDDM app의 allocation이 변하면 180초씩 두 번
  기다린 뒤 실패한다. 이는 owner cleanup과 외부 desktop activity를 같은 조건으로 오판한다.
- driver는 signed receipt와 timing aggregate를 host restoration 성공 뒤에만 state에 넣는다.
  따라서 attempt 5처럼 restoration이 먼저 실패하면 완료한 1,400 observation의 signed receipt도
  artifact에 남지 않아 fresh process가 측정 품질을 다시 열 수 없다.
- driver source가 ignored `runtime_artifacts/validation/run_main_latency_finalist_attempt2.py`에
  있고 tracked test/identity가 이를 직접 참조한다. P0-3이 runtime artifact를 제외하면 clean
  bundle checkout에서 finalist driver 자체를 재현할 수 없다.

##### 목표·범위·비범위

- 목표는 exact-owned cleanup과 Docker 원상복구를 유지하면서 unrelated WDDM 변동을 bounded
  idle gate로 분리하고, receipt를 host proof 전 atomic 보존하며, driver를 tracked source로
  만든 뒤 identical attempt 6을 한 번 실행하는 것이다.
- 포함: host lifecycle free-memory 판정, driver 조기 receipt persist, driver source 이동,
  identity/test/wrapper 갱신, focused+canonical tests, attempt 6과 fresh-process verifier다.
- 비범위: latency candidate/config/corpus/sample 수 변경, WDDM gate 제거, owned cleanup 완화,
  Docker/GPU 자동 승격, attempt 5 artifact 수정·사후 업그레이드, Qwen/P0-2/P0-3 변경이다.
- 기존 attempt 5 artifact/journal/progress/wrapper/driver는 evidence로 보존한다. attempt 6의
  launcher-error와 owned lab temp만 성공 후 exact cleanup하며 이전 artifact는 삭제하지 않는다.

##### 선택한 해법·버린 대안

- 선택: terminal cleanup의 exact-owned process/GPU allocation/artifact `0/0/0`와 global Docker
  empty/original-state를 resource leak 권위로 유지한다. 별도 WDDM host gate는 동일 driver model/
  total VRAM, 연속 3회 utilization `<=10%`, free memory ratio `>=75%`를 요구하고 baseline/post
  free MiB를 evidence에 그대로 남긴다.
- 선택: runner receipt compile 직후 `preservedSignedReceipt`와 content-free
  `preservedTimingDiagnostics`를 atomic persist한다. host proof/evaluation 성공 뒤에만 canonical
  `receipt`/`evaluation`과 `completed`를 쓰므로 보존 receipt가 promotion 권한이 되지 않는다.
- 선택: ignored driver를 내용 보존하여 `tools/main_latency_finalist_driver.py`로 만들고 identity와
  test는 그 tracked path만 참조한다. runtime wrapper는 artifact/launcher orchestration만 담당한다.
- 버림: GPU baseline tolerance만 수 GiB로 키우면 임의 숫자이며 hardware 크기에 비례하지 않는다.
  WDDM 검사를 없애면 global idle 증거가 사라진다. attempt 5 receipt를 추측 생성하거나 artifact를
  나중에 completed로 바꾸는 것은 서명·currentness를 위조한다. 같은 ignored driver를 P0-3 capsule에
  예외 포함하면 source/runtime 경계를 계속 흐린다.

##### 고정 파일·기계적 변경

1. `tools/main_latency_host_lifecycle.py`에 `GPU_MIN_FREE_RATIO = 0.75`를 추가한다.
   post observation은 기존 driver model/total VRAM/utilization 조건과 이 ratio를 모두 만족해야 하며
   pre-run `baselineFreeMiB`와 post `postFreeMinMiB` 기록은 유지한다. exact-owned cleanup과 Docker
   global-empty 검사 순서는 바꾸지 않는다.
2. ignored driver의 exact current contents를 `tools/main_latency_finalist_driver.py`로 옮긴다.
   `compile_runner_receipt` 성공 직후 preserved receipt/timing을 state에 넣고 `persist()`한 다음
   terminal cleanup과 host restoration으로 진행한다. host failure는 계속 `cleanup_required`이며
   canonical receipt/evaluation/offline verification은 만들지 않는다.
3. `tools/main_latency_owned_lab_worker.py`의 harness identity path와
   `tests/tools/test_main_latency_finalist_driver.py`의 import path를 tracked driver로 바꾼다.
4. driver test에 host finalization exception 전후 artifact를 읽어 preserved receipt/timing은 있고
   canonical receipt/evaluation은 없으며 status가 `cleanup_required`인 회귀를 추가한다.
   host lifecycle test에는 baseline보다 4GiB 낮아도 75% 이상이면 통과하고 75% 미만이면 timeout인
   두 경계를 추가한다.
5. ignored `run_main_latency_finalist_attempt6.ps1`은 attempt 5 wrapper를 그대로 복제하되 tracked
   driver path, attempt `6`, attempt6 artifact/journal/progress/log/error와 scheduled task
   `EvelynMainLatencyFinalistAttempt6`만 사용한다.

##### 상태·timeout·취소·재시작·rollback

- 상태는 `source_fixed → focused_green → canonical_green → attempt6_preflight → measured →
  receipt_preserved → owned_cleanup → host_restored → evaluated → offline_verified → terminal`이다.
  preserved receipt는 `evaluated` 또는 promotion을 뜻하지 않는다.
- attempt 6은 attempt 5와 동일한 max runtime 4시간, startup 900초, sample 30초, host restore
  180초를 유지한다. active source·Docker·GPU owner를 실행 중 바꾸거나 자동 재시작하지 않는다.
- 취소/timeout/crash는 exact scheduled task, wrapper, driver, owned lab marker에 결박해 정리하며
  user process/container를 종료하지 않는다. Docker initial state를 모르면 변경하지 않는다.
- source test 실패 시 attempt 6을 시작하지 않는다. attempt 6 실패 시 graph-on 자동 승격 없이
  현재 config를 유지하고 terminal artifact를 보존한다. rollback은 변경 전 source checkpoint가
  아니라 P0-3 전 dirty tree이므로 `git reset`을 쓰지 않고 exact changed files만 recovery capsule로
  복구한다.

##### 보안·호환성·성능·검증 gate

- report는 기존 content-free aggregate만 보존한다. prompt/output/audio/transcript/token/credential은
  추가하지 않는다. authority journal과 HMAC domain, candidate/run/source identities는 유지한다.
- 먼저 실패할 회귀: host proof 전에 exception이면 signed receipt가 사라지는 현행 동작,
  tracked checkout에 driver가 없거나 identity가 ignored path를 가리키는 현행 동작, free ratio
  75% 미만을 clean으로 받는 동작을 각각 고정한다.
- focused는 finalist driver, host lifecycle, fixed adapter, worker identity, lab contract,
  verifier 9개 latency tool suite 실패 0개다. broad는 canonical `python -m pytest -q` 기능 실패
  0개다. P0-2의 알려진 두 실패는 이 변경 전에 먼저 수정되지 않으므로 focused 범위에서는 별도이며,
  canonical gate는 P0-2 완료 뒤 실행한다.
- attempt 6은 attempt 5와 동일한 warm 200/200, ABBA 20/20, restart 30/30, soak 1,000이다.
  exact-owned cleanup `0/0/0`, Docker original OFF, WDDM 3회 util `<=10%`/free `>=75%`, signed host
  proof, evaluator `eligible/candidate_passed/passed`, fresh-process verifier `verified`가 모두 필요하다.
- 예상 source diff는 tracked driver 1개 추가, host lifecycle/worker identity 2개 수정, tests 2개 수정,
  plan/NOW/decision/worklog 문서다. dependency/schema/API/model diff는 0이다. runtime에는 attempt6
  wrapper와 content-free artifacts만 생긴다. **미해결 설계 질문은 0개다.**

Attempt 6은 workload 전 preflight에서 종료되어 이 완료 조건을 충족하지 못했다. 아래 Attempt 7
설계가 이 실행 절차를 대체하며, P0-1은 계속 열린 상태다.

#### Attempt 6 preflight 실패와 Attempt 7 진단·재시도 설계

상태: **[~] 구현 및 제한된 live rerun 승인됨·source focused 검증 통과**.

##### 검증된 사실·root cause 경계

- attempt 6은 `hostLifecyclePrepared=true`, startup exact-owned cleanup `0/0/0`,
  `measurementPreflightVerified=true` 뒤 identity discovery에서
  `LabIdentityDiscoveryError/lab_isolation_preflight_failed`로 종료했다. warm/ABBA/restart/soak
  observation은 0이고 Docker는 initial OFF, final OFF, GPU restore clean이다.
- worker의 identity discovery는 Docker global-empty 확인 뒤 production/GPU0 container 부재와
  WDDM 3회 utilization `<=10%`/free `>=75%`를 다시 검사한다. 이 조건들을 하나의 boolean과
  generic `lab_isolation_preflight_failed`로 합치며 driver는 discovery를 한 번만 호출한다.
- 종료 후 GPU0 utilization 20%를 관측했으므로 transient WDDM activity와 일치하지만, artifact가
  어느 subcheck인지 보존하지 않아 이것을 exact 원인으로 승격하지 않는다. tracked driver 파일은
  존재하고 read-only audit는 source read를 막지 않으며 Attempt 6 artifact SHA-256은
  `5AFCF236E3916BD0E64D3003C4BA10747595482A359A93DC072586A62794B2FD`다.

##### 선택한 최소 해법·비범위

1. `tools/main_latency_lab_contract.py`의 허용 preflight code에
   `lab_gpu_idle_preflight_failed` 하나를 추가한다. 기존 generic code와 evaluator fail-closed
   의미는 바꾸지 않는다.
2. `tools/main_latency_owned_lab_worker.py`에서 production/container 검사는 기존 generic code를
   유지하고, `_gpu_idle(...) == false`일 때만 새 exact code를 반환한다. 3회, `<=10%`, `>=75%`,
   0.2초 간격과 baseline capture는 전혀 완화하지 않는다.
3. `tools/main_latency_finalist_driver.py`는 identity discovery가 정확히 새 GPU-idle code로 실패한
   경우에만 15초 간격, 최대 3회 호출한다. 다른 code는 첫 실패에서 즉시 종료한다. artifact에는
   content-free `identityDiscoveryAttemptCount`와 마지막 code만 남기며 prompt/output/process path는
   추가하지 않는다. 호출별 기존 180초 worker timeout을 포함한 상한은 570초다.
4. Attempt 6 artifact/task/wrapper는 evidence로 보존한다. 새 ignored Attempt 7 wrapper는 attempt,
   artifact/log 이름만 7로 바꾸고 120초 foreground 이탈과 workload 200/200, ABBA 20,
   restart 30/30, soak 1,000, candidate/config/corpus를 동일하게 유지한다.
- 비범위: idle/free gate 완화 또는 제거, generic isolation 오류 재시도, model/config/corpus/sample
  변경, Attempt 6 artifact 덮어쓰기, P0-2/P0-3/Qwen 변경, Discord/mic/Minecraft 기동이다.

##### 검증·실패·rollback gate

- 먼저 실패를 고정한다: GPU-idle false가 generic code인 현행 동작, 첫 GPU-idle 실패 뒤 즉시
  terminal 되는 현행 driver, generic isolation 실패를 재시도하면 안 되는 계약을 각각 test한다.
- focused 9개 latency tool suite 기능 실패 0개와 wrapper PowerShell 5.1 parse를 요구한다.
  canonical suite는 이미 동결한 순서대로 P0-2 후 수행한다.
- Attempt 7은 첫 discovery 성공이면 1회, transient GPU-idle 뒤 성공이면 2~3회여야 한다. 3회 모두
  GPU-idle 실패하거나 다른 code가 나오면 workload 전에 fail-closed하고 Docker OFF로 복구한다.
- 최종 승격 gate는 이전과 동일하게 full workload, exact-owned cleanup `0/0/0`, Docker OFF,
  signed host proof, `eligible/candidate_passed/passed`, fresh-process `verified`다. 실패 artifact는
  수정하지 않고 graph-on 자동 승격이나 다음 P0 항목을 시작하지 않는다.
- 예상 source diff는 contract/worker/driver 3개와 test 2개, plan/NOW/worklog이고 dependency,
  schema migration, external API, model diff는 0이다. **미해결 설계 질문은 0개다.**

완료 결과: Attempt 7은 warm `200×2`, restart-ready `30×2`, ABBA `20`, soak `1,000`을 완주했다.
fresh-process verifier는 `verified`, evaluator는 `eligible/candidate_passed/passed`였다. graph-off/on
warm p50/p95/p99는 `238.7/260.7/290.1 ms` 대 `201.85/219.1/239.8 ms`, p95 delta의 95% CI는
`[-45.7,-26.7] ms`, effect size는 `-3.0166`이었다. error·quality·cache·GPU gate는 모두 통과했고
exact-owned cleanup은 process/GPU allocation/artifact `0/0/0`, Docker는 `OFF→OFF`, production은
OFF였다. exact scheduled task도 결과 보존 뒤 제거했다. 이로써 P0-1 terminal gate를 닫는다.

### P0-2. 실제 회귀 2개 수정 및 전체 테스트 녹색화

상태: **[x] 2026-08-27 구현·focused·관련 묶음·canonical 검증 완료**.

- [x] Qwen broker에서 queue 대기 시간과 inference timeout을 실제로 분리한다.
  큐에서 오래 기다린 두 번째 요청도 slot을 받은 뒤 자기 inference 예산을 온전히
  받고 성공해야 한다.
- [x] Main admission gateway에서 upstream header timeout 뒤 요청·연결·lane 소유권을
  완전히 회수한다. 바로 다음 REALTIME 요청이 앞선 hung 요청의 영향을 받아 504가
  되면 안 된다.
- [x] 두 focused test를 먼저 통과시킨 뒤 관련 admission/cancel/restart 묶음과 정규
  `python -m pytest -q`를 실행한다.

완료 조건: 아래 두 테스트와 전체 정규 suite의 기능 실패가 0개다.

- `tests/runtime/test_mindcraft_llm_broker.py::MindcraftLlmBrokerTests::test_qwen_queue_wait_does_not_spend_inference_timeout`
- `tests/runtime/test_main_llm_admission_gateway.py::MainLlmAdmissionGatewayTests::test_upstream_header_timeout_releases_lane_with_receipt`

완료 결과: Qwen inference clock은 queue와 marker claim 뒤 시작해 검증된 upstream 결과에서 끝나며
session/owner/delivery cleanup과 분리됐다. Main gateway는 단일 cancel-safe polling fence와 EOF 종료를
사용해 timeout operation을 수거한 뒤 successor lane을 연다. exact 두 회귀, 관련 파일 35개(+subtests
14), 인접 reservation/warmup/specialist 29개(+subtests 9)가 통과했다. canonical은
`4573 passed, 22 skipped, 1391 subtests passed`로 기능 실패 0개였다.

### P0-3. 복구 가능한 소스 기준점 만들기

상태: **[x] 2026-08-27 immutable checkpoint/tag/bundle과 clean-clone canonical 검증 완료**.
commit `d1c8863b...`, annotated tag `evelyn-recovery-2026-08-26`, bundle SHA-256
`dd8e4bc5...8dcb`가 같은 tree `931ccee6...eecd`를 가리킨다. 새 bundle clone은 root/submodule
clean 상태에서 canonical `4573 passed, 22 skipped, 1391 subtests passed`를 통과했고 remote push는 0개다.

#### 문제·검증된 원인·영향

- 현재 branch는 `codex/omnivoice-tts-cutover`, HEAD는
  `b5de2920f8287f105a8b1007b5a7daf475d910f8`이며 upstream이 없다. `source` remote도
  별도 dirty local repository이므로 안전한 외부 복구점으로 사용할 수 없다.
- 기본 status 455개는 tracked modification 342개와 untracked 기본 항목 113개다.
  펼친 file 기준으로는 untracked 143개이며, tracked diff는 약 64,269 insertions와
  4,270 deletions다. 이전 checkpoint `d5941fb`는 현재 HEAD의 ancestor라 이후 작업을
  복구하지 못한다.
- parent overlay가 source of truth인 `external/mindcraft` submodule 안에도 tracked 수정
  4개와 untracked overlay/scratch/`node_modules.bak_before_link`가 섞여 있다. 이를 그대로
  commit하면 parent의 overlay 계약과 submodule pointer가 어긋난다.
- root의 `temp_*.mjs` 7개와 `external/mindcraft_evelyn/evelyn-ms-code.mjs`는 조사/auth
  probe다. `evelyn_core/evelin/`은 runtime이 사용하지 않는 raw Live2D 원본·사용권 자료로
  source Git에 넣으면 privacy·license·용량 경계를 깨뜨린다.
- 따라서 지금의 disk 한 벌이 손상되면 구현·테스트·문서를 다른 checkout에서 재현할 수
  없고, 정리 과정에서 사용자 작업을 잃을 위험이 있다.
- 2026-08-27 pinned clean checkout을 Docker와 같은 11개 GNU patch 순서와 parent overlay로
  재구성하자 `survival_hostile_reflex.test.mjs`의 기존 계약 2개가 결정적으로 실패했다.
  `evelyn_survival_mode.js`의 `shouldDeferHostileExecution` 11줄이 `execute(...)` 전에 반환해
  ActionManager의 bounded admission 시도와 resolved denial 관측을 모두 건너뛴 것이 원인이다.
  임시 clean tree에서 그 11줄만 제거하면 exact 2개와 hostile-reflex 25개, 나머지 Docker Node
  묶음과 verifier가 모두 통과했다. 실제 parent source는 변경하지 않았다.
- 실제 Dockerfile은 fuzz-tolerant GNU `patch -p1`을 사용하므로 아래의 literal
  `git apply --check`는 현재 patch chain의 faithful gate가 아니다. source 의미 변경 금지 범위에
  위 11줄 회귀 수정을 추가하고 gate를 GNU dry-run으로 정정할지 승인되기 전에는 destructive
  hygiene, stage, commit, tag, bundle 단계로 진행하지 않는다.

#### 목표·범위·비범위

- 목표는 현재 의도된 source/config/test/docs/tool과 승인된 ActionManager admission 계약 복구를
  한 개의 안전 checkpoint commit으로 고정하고, annotated tag와 검증된 Git bundle로 다른
  checkout에서 재현하는 것이다.
- 포함: 모든 의도된 tracked 수정, 분류된 untracked source/config/test/docs/tool,
  `.gitignore` hygiene 3개, patch whitespace 정규화, parent overlay와 submodule clean-state
  일치 검증, 외부 recovery capsule, clean-checkout 전체 회귀다.
- 비범위: 342개 파일의 과거 의도 추측에 의한 commit 분할, refactor/포맷 전면 적용,
  승인된 11줄 삭제 이외 model/runtime 동작 변경, raw/private 자산의 Git 저장, dirty `source` remote push,
  submodule 새 commit/pointer, ignored runtime artifact·credential·log 백업이다.
- 제거할 임시 경로는 root `temp_check.mjs`, `temp_print.mjs`, `temp_repro.mjs`,
  `temp_repro2.mjs`, `temp_repro_manual.mjs`, `temp_trace.mjs`, `temp_trace2.mjs`,
  `external/mindcraft_evelyn/evelyn-ms-code.mjs`와 submodule 내부의 exact scratch/overlay
  복사본/`node_modules.bak_before_link`, `_tmp_ms_profiles`의 2-byte generated cache placeholder
  3개뿐이다. recovery hash 검증 전에는 하나도 제거하지 않는다.

#### 선택한 해법과 버린 대안

- 선택: repository 밖의 access-restricted recovery capsule을 먼저 만들고 검증한 뒤,
  분류표에 따른 hygiene와 단일 checkpoint commit, tag, bundle, temporary clean clone 검증을
  순서대로 수행한다. 대규모 누적 변경의 작성 시점을 추측해 재구성하지 않는 것이 가장
  작은 end-state 해법이다.
- 버림: `git stash`, `reset`, checkout restore는 untracked/raw/submodule 상태를 완전하게
  보존하지 못하고 사용자 작업을 잃을 수 있다. 342개 파일을 임의의 기능 commit으로
  나누면 검증되지 않은 history를 만든다. dirty local remote로 push하거나 submodule 내부를
  직접 commit하면 독립 복구점과 parent overlay 계약을 만들지 못한다. raw asset을 Git/LFS에
  넣는 것은 runtime 요구가 없고 privacy/license 범위를 넓힌다.

#### 고정 변경 목록

1. 작업 시작 currentness를 branch/HEAD/submodule commit과 P0-1/P0-2 gate에 결박한다.
   하나라도 설계 값과 다르면 중단하고 재설계한다.
2. `C:\Users\Admin\Documents\Evelyn Recovery\2026-08-26\<timestamp>\`에 현재 HEAD의
   `git diff --binary`, 모든 nonignored untracked 파일, submodule binary diff와 필요한
   non-generated untracked 파일을 복사한다. `node_modules.bak_before_link`는 path/size/hash
   manifest만 남기며 dependency bytes는 보관하지 않는다.
3. capsule manifest에 branch, HEAD, submodule pinned commit, path, byte size, SHA-256를
   기록한다. ignored runtime artifacts, `.env`, credential, transcript/audio/log는 제외하고
   Windows DACL을 현재 사용자와 SYSTEM으로 제한한다. 원본/복사본 count와 hash가 모두
   일치해야 다음 단계로 간다.
4. pinned submodule의 temporary clean checkout에 parent patch/overlay를 적용해 Dockerfile과 같은
   11개 GNU `patch --dry-run -p1`/apply 순서와 기존 overlay Node tests를 통과시킨다. 일치한 경우에만
   `external/mindcraft`의 exact tracked 4개와 exact untracked scratch/overlay 복사본,
   `node_modules.bak_before_link`를 정리해 pinned commit의 clean state로 돌린다. global
   `safe.directory`는 바꾸지 않고 command-local 설정만 쓴다.
5. `.gitignore`에 정확히 `/temp_*.mjs`,
   `/external/mindcraft_evelyn/*-ms-code.mjs`, `/evelyn_core/evelin/`을 추가한다.
   `external/mindcraft_evelyn/combat.patch` 9곳,
   `docs/KOREAN_ASR_TARGET_ARCHITECTURE.md` 2곳, `docs/worklog/2026-08-17.md` EOF 1곳의
   non-semantic whitespace만 제거한다. `latency.patch`의 GNU unified-diff context whitespace는
   capsule SHA-256 `224ffd8c...6f90e`와 byte-exact하게 보존하고 `.gitattributes`의 exact path에만
   `text eol=lf -whitespace`를 둔다. `evelyn_survival_mode.js`에서는 `execute(...)` 전 반환하는
   `shouldDeferHostileExecution` 11줄만 삭제한다. pinned submodule을 dirty하게 만들지 않도록
   `evelyn.patch`가 `_tmp_ms_profiles/` ignore를 build tree에 적용하고 hygiene test는 pin 자체가
   아니라 이 overlay 계약을 검증한다. 그 밖의 source 의미나 formatting은 바꾸지 않는다.
6. raw `evelyn_core/evelin/`은 capsule의 restricted copy가 검증된 뒤 working tree에서만
   제외하고 Git에 넣지 않는다. capsule manifest의 의도된 450개 source/config/test/docs/tools,
   exact `.gitattributes` correction과 clean clone의 required mount root를 만드는
   `bot_profiles/.gitkeep`만 stage하되 scratch/auth probe/raw asset/submodule dirty state는 stage하지
   않는다.
7. 아래 검증을 통과하면 exact staged tree로 checkpoint commit 하나를 만들고 annotated tag
   `evelyn-recovery-2026-08-26`를 붙인다. 같은 external recovery directory에 Git bundle을
   만들고 `git bundle verify`, `git fsck --strict`, temporary clean clone의 tree/status와
   전체 suite를 검증한다. `source` remote에는 push하지 않는다.

#### 상태·실패·취소·재시작 계약

- 상태는 `audited → capsule_created → capsule_verified → hygiene_verified → staged →
  tests_passed → commit_created → tag_created → bundle_verified → clone_verified` 단방향이다.
  manifest의 completed stage와 hash만 재시작 기준이며 partial stage를 성공으로 간주하지 않는다.
- owner는 시작 시 exact branch/HEAD/submodule tuple이다. 각 destructive 경계와 commit 직전에
  다시 읽고 달라지면 중단한다. 별도 작업자의 파일 변경을 자동으로 stage하거나 삭제하지 않는다.
- capsule 생성/hash/overlay/test/bundle/clone 중 오류·timeout·취소가 나면 다음 단계로 가지
  않는다. commit 전 실패는 capsule을 보존하고 원본을 reset하지 않는다. cleanup 일부가
  끝났다면 capsule manifest로 exact path만 복원한다.
- commit 뒤 검증 실패는 commit/tag/bundle을 보존한 채 failed gate로 보고한다. amend, squash,
  force update, remote push로 숨기지 않는다. 재시작은 마지막 verified stage부터만 진행한다.

#### 안전·호환성·rollback

- private/raw 파일은 repository와 docs에 내용·이름 목록을 확장 기록하지 않고 restricted
  external capsule에만 둔다. secret/runtime artifact는 capsule에서도 제외한다.
- submodule source of truth는 pinned commit + parent overlay/patch로 유지한다. 새 dependency,
  schema, migration, launcher/runtime 상태 변화는 없다.
- rollback은 commit 전 capsule exact restore, commit 뒤 immutable checkpoint/tag/bundle 보존이다.
  dirty `source` remote는 읽기 기준도 rollback 대상으로도 사용하지 않는다.

#### 검증·수치형 완료 gate

- 먼저 실패를 고정할 회귀: raw/scratch/auth probe가 staged되면 실패, submodule이 dirty하거나
  overlay가 pinned checkout에 적용되지 않으면 실패, capsule path/count/SHA-256 또는 DACL이
  다르면 실패, tracked/untracked 의도 항목이 빠지면 실패한다.
- focused: repository hygiene/Compose/service-manifest/shutdown tests, Mindcraft overlay Node tests,
  semantic patch whitespace의 exact path attribute를 적용한 `git diff --check`를 통과한다.
- broad: P0-2 완료 뒤 canonical `python -m pytest -q` 기능 실패 0개. checkpoint 이후 bundle
  clone에서도 같은 suite 실패 0개, `git status --short` 0개, submodule status clean,
  `git fsck --strict` 오류 0개, bundle verify 성공, committed tree hash 동일이다.
- 완료 산출물은 checkpoint commit 1개, annotated tag 1개, verified bundle 1개, restricted
  recovery capsule 1개다. remote 변경은 0개다.

#### 예상 diff·미해결 질문

- 이 항목 자체가 새로 만드는 영구 source diff는 `.gitignore` 3개 pattern,
  `.gitattributes` exact patch-path 1개, `bot_profiles/.gitkeep`, non-semantic whitespace-only 12곳,
  `evelyn_survival_mode.js` 조기 반환 11줄 삭제,
  `evelyn.patch` auth-cache ignore hunk, hygiene test의 overlay-source assertion과
  Mindcraft runtime contract test의 generated submodule-copy assertion 제거,
  `plan.md`, `docs/01_NOW.md`,
  `docs/02_DECISIONS.md`, `docs/worklog/2026-08-27.md`뿐이다. 기존 342개 수정과 분류된
  untracked source는 의미 변경 없이 checkpoint에 포함한다. DB/schema/dependency diff는 0이다.
- 임시 파일의 exact 제거 목록, raw asset 처리, submodule 복구법, checkpoint 수, tag/bundle 위치,
  검증과 rollback을 모두 위에서 고정했다. **미해결 설계 질문은 0개다.**

완료 조건: 위 수치 gate를 모두 통과해 현재 구현이 bundle의 clean checkout에서 재현되고,
repository와 pinned submodule이 clean하며, capsule·commit·tag·bundle의 복구 경로가 모두 남는다.

### P0-4. revised STT image를 GPU1에서 headless 검증·승격

상태: **[~] 2026-08-27 old/new STT image diagnostic overlap warmup 2+measured 20 PASS;
private corpus·cancel/successor·cold restart·promotion 대기**.
P0-1 terminal 복구, P0-2 canonical green, P0-3 checkpoint/tag/bundle 선행 gate는 모두 닫혔다. 이 단계는
microphone, speaker, Discord, Minecraft를 시작하지 않고 현행 Qwen3-14B와 revised STT image의
기준선을 먼저 고정한다. image-ready 단계의 diagnostic A/B는 통과했지만 이 결과만으로는
production 승격 자격이 없다. 고정 private corpus directory가 현재 absent(`0/50`)이므로 합성
자료로 대체하지 않고 외부 입력 gate로 유지하며 P0-5 Qwen3.8은 계속 차단한다.

#### 목표·범위·비범위

- 목표는 이미 구현된 Qwen3-ASR streaming/batch source를 새 `docker/Dockerfile.stt` image로
  build/load하고 physical GPU1 RTX 3090에서 quality, latency, VRAM, cancellation, restart와
  현행 Qwen3-14B 동시부하를 검증해 rollback 가능한 production STT 기준선으로 승격하는 것이다.
- 포함: STT image만 build, exact image/source/model/backend/GPU identity, 기존 batch와 streaming
  API의 private 50-item replay, 현행 `tools/gpu1_latency_benchmark.py` warmup 2+measured 20 A/B,
  cancel/successor smoke, 3회 cold restart, exact cleanup과 이전 image rollback이다.
- 비범위: microphone/speaker/Discord 연결, endpoint silence 0.60 변경, playback/continuity UI,
  packet-time Discord decode, ASR model·memory fraction 변경, 새 scheduler/dependency, Qwen3.8과
  Minecraft 기동이다. 남은 실제 음성 E2E는 P1-1이 소유한다.

#### 고정 실행·상태·rollback

1. P0-3 tag/clean tree, Docker original desired state, 기존 STT image ID, streaming flags,
   `Qwen/Qwen3-ASR-1.7B`, physical GPU1 UUID와 GPU1 baseline을 기록한다. 운영 rollback image의
   exact 기준은 legacy `backend=transformers`, health backend field 없음, vLLM module 없음,
   memory utilization N/A다. revised image의 exact 목표는 `backend=vllm`,
   `max_model_len=8192`, `gpu_memory_utilization=0.35`, `max_num_seqs=1`,
   `limit_mm_per_prompt.audio=1`, `maxAudioSec=30`이다. 어느 phase identity든 다르면
   시작하지 않는다.
2. 기존 image에서 현행 Qwen3-14B+STT overlap을 warmup 2+measured 20으로 먼저 측정한다.
   phase 사이에는 exact containers를 내리고 GPU1 memory가 baseline ±256MiB로 연속 3회 복귀해야 한다.
3. `docker-compose.fast-control.yml`의 `stt` service만 build한다. 이전 image ID에 rollback tag를
   붙이고 새 image ID/source hash/dependency/model cache identity를 다시 읽은 뒤 `stt`만 시작한다.
   health는 ready이고 model/GPU와 revised backend·engine capacity·30초 audio 경계가 actual applied
   config와 exact일 때만 통과한다. `image_ready`에서 old/new 2+20 diagnostic overlap을
   실행해 capacity·latency·VRAM 근거를 먼저 남길 수 있지만 그 상태를 promotion으로 오인하지 않는다.
4. 기존 P1-1 계약의 `tools/voice_asr_benchmark.py`와 test만 먼저 구현해 private positive 40개,
   negative 10개를 batch/streaming으로 replay한다. raw PCM/transcript는 report/docs에 쓰지 않고
   사용자가 별도 보존을 요구하지 않으면 run 종료 시 삭제한다.
5. private corpus 통과 뒤 cancel 중 물리 worker drain, timeout 뒤 successor, batch fallback을
   headless API로 검증하고 cold restart 3회를 실행한다. 이미 통과한 diagnostic overlap은
   exact source/image/model/GPU/baseline binding이 유지될 때만 해당 수치 gate의 근거로 보존한다.
6. 모든 gate가 통과하면 새 STT image ID를 production 기준선으로 기록한다. 실패하면 old image ID로
   되돌려 health와 fixed smoke를 확인하고 새 failed image/report는 증거로 보존한다.
- 상태는 `preflight → old_baseline → image_built → image_ready → diagnostic_overlap_passed`까지를
  promotion 전 진단으로 인정한다. 승격은 그 영수증과 `corpus_passed → cancel_passed →
  restart_passed → cleanup_passed`가 모두 같은 binding에서 닫힌 뒤에만 `promoted → restored`로
  진행하며 atomic content-free report만 재시작 기준이다.

#### 수치형 완료 gate

- positive 40개는 batch/streaming final usable 40/40, streaming CER는 같은 새 image의 batch보다
  나쁘지 않고 manifest entity token exact `>=95%`, stable-prefix rollback과 malformed final은 0이다.
  negative 10개의 accepted turn과 unauthorized high-impact route는 0이다.
- 새 image overlap은 STT final p95 `<=1,200ms`이고 같은 session old-image baseline보다 10% 넘게
  악화되지 않는다. Fast Main TTFT p95 `<=1,000ms`, Qwen3-14B p95 `<=6,000ms`, GPU1 min free
  `>=2,048MiB`, STT/Qwen/Main/GPU error·timeout·OOM은 0이다.
- cancel/successor smoke는 orphan session/worker 0, successor 성공 100%다. cold restart는 health,
  exact model/backend/GPU identity와 첫 request가 3/3 성공해야 한다.
- 종료 후 Docker desired state와 GPU1 baseline ±256MiB 연속 3회, owned process/container/temp/audio
  0개를 확인한다. source test만으로 live 완료를 주장하지 않는다.
- 실제 source diff는 GPU overlap tool/test, private ASR corpus tool/test, diagnostic Compose와 계약 test,
  STT Dockerfile-specific context, two-stage recipe, direct dependency pins와 dependency hygiene test,
  `stt_service.py`의 vLLM engine capacity·actual-config fail-close, current-state/dependency/benchmark/
  NOW/decision/worklog/plan 문서다. production STT endpoint, ASR model, memory fraction `0.35`,
  public transcription schema/API, launcher/admission 동작 diff는 0이다. image recipe와 diagnostic override는
  private corpus·cancel·restart·cleanup gate를 통과하기 전 production 기준선을 바꾸지 않는다.
  **미해결 설계 질문은 0개다.**

완료 조건: revised STT image가 headless corpus, Qwen3-14B overlap, restart와 cleanup gate를 모두
통과해 exact rollback image와 함께 기준선으로 고정된다.

### P0-5. GPU1 Qwen을 Qwen3.8-27B Q4_K_M으로 교체

상태: **[!] 2026-08-27 전체 구현·제한된 live 검증 승인됨, P0-4 promotion gate로 차단**. P0-1의 terminal
복구, P0-2 전체 suite 녹색, P0-3 checkpoint/tag/bundle과 P0-4 revised STT 기준선 완료가
선행 gate다. 승인 범위에는
아래에 고정한 외부 model 다운로드, side-by-side llama.cpp build, Docker GPU1/STT A/B와
승격·복구만 포함한다. Minecraft server/bot, Discord, microphone, speaker는 기동하지 않는다.
P0-1 Attempt 7 terminal/host proof, P0-2 회귀와 canonical, P0-3 annotated tag/bundle은 모두
검증됐다. P0-5는 P0-4 old/new diagnostic overlap PASS만으로 시작하지 않으며
private corpus·cancel/successor·restart·cleanup·promotion gate가 닫힐 때까지 model/build/source/
Docker 변경을 시작하지 않는다.

#### 문제·검증된 원인·영향

- 현행 `minecraft_llm`은 GPU1 RTX 3090 24GB에서
  `Qwen3-14B-Q4_K_M.gguf`를 llama.cpp로 구동하며 action/chat/classifier/memory/recovery/
  subgoal, specialist, `/작업` task를 함께 처리한다. GPU1의 Qwen+Qwen ASR 승인 overlap은
  peak 14,039MiB, min free 10,284MiB, Qwen p95 2,233.2ms, STT p95 626.1ms였다.
- 공식 Qwen3.8 collection에는 14B가 없고 최소 dense 모델은 27B다. 공식 BF16 tree는
  약 55.6GB라 이 호스트 RAM 64GB에서 local quantization까지 안전하게 수행할 여유가 없다.
  Q5/Q6/Q8도 24GB에서 ASR과 안정적으로 공존할 여유가 없으므로 표준 Q4_K_M 한 종류만
  승격 후보로 삼는다.
- 선택 artifact는 community quant이므로 이름만 믿어서는 안 된다. repository revision과
  SHA-256을 고정하고 GGUF metadata, 모델 역할별 출력, GPU1 동시 부하를 모두 검증해야 한다.
- 현행 llama.cpp checkout/build는 commit
  `47e1de77aa0f06bf73cfd8c5281d95979f89fcbe`로 Qwen3.8 발표 전 빌드이며, Qwen3.8은 구
  CUDA build에서 잘못된 출력이 보고됐다. server 실행 파일만 바꾸고 matching `libggml*.so`를
  남기는 방식도 ABI mismatch 위험이 있다.
- 새 GGUF는 16,810,714,336 bytes로 현행 9,001,752,960-byte GGUF보다
  7,808,961,376 bytes 크다. 종전 overlap에
  단순 대입하면 약 21GiB 사용으로 예상되지만 이는 추정일 뿐이며 24GB 적합성은 live peak와
  timeout으로만 판정한다.

#### 목표·범위·비범위

- 목표는 GPU1의 공용 Qwen backend를 exact-pinned Qwen3.8-27B Q4_K_M과 별도 SM86
  llama.cpp build로 교체하되, 현행 역할·API·GPU 번호·context·non-thinking 계약과 ASR
  동시 사용을 보존하는 것이다.
- 포함: pinned model 다운로드와 SHA 검사, pinned llama.cpp detached worktree와 matching
  CUDA build, Qwen 전용 read-only mount, model 이름/config/profile/test 갱신, synthetic
  20-case 역할 계약 A/B, 기존 GPU1 overlap benchmark, 3회 restart, 승격과 exact rollback이다.
- 비범위: Q3/Q5/Q6/Q8 자동 대안, 자체 BF16 quantization, MTP/speculative decode, vision/mmproj,
  262K context, thinking/reasoning, timeout 완화, Qwen broker/admission 재설계, ASR model/config
  변경, router/sub/Main 교체, Minecraft/Discord/음성 live E2E, 기존 모델/build 삭제다.
- partial download와 failed candidate report만 구현 과정의 임시 경로다. 성공 또는 rollback 뒤
  `.partial`은 제거하고 aggregate report는 `runtime_artifacts/validation/qwen38/`에만 둔다.
  model/build와 report는 Git 또는 `docs/`에 넣지 않는다.

#### 선택한 해법과 버린 대안

- 선택 model은 `lmstudio-community/Qwen3.8-27B-GGUF` revision
  `5a7da681f60570ab5b439a587e912d2e5eddb582`의
  `Qwen3.8-27B-Q4_K_M.gguf`, SHA-256
  `e00082f779fa385cee8c68a3ec8833a75778cc87272240b942f74e0b8243e520` 하나다.
  원본 계보 확인 기준은 official `Qwen/Qwen3.8-27B` revision
  `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0`이다.
  출처는 [official collection](https://huggingface.co/collections/Qwen/qwen38),
  [official pinned model card](https://huggingface.co/Qwen/Qwen3.8-27B/blob/1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0/README.md),
  [pinned GGUF](https://huggingface.co/lmstudio-community/Qwen3.8-27B-GGUF/blob/5a7da681f60570ab5b439a587e912d2e5eddb582/Qwen3.8-27B-Q4_K_M.gguf)다.
- 선택 runtime은 llama.cpp commit
  `4d19b287691e8f47fc303be420f630c40ec45684`의 detached side-by-side worktree
  `C:\Users\Admin\llama.cpp-qwen38-4d19b287`와 그 안의 SM86 Release `build/`다.
  model은 같은 root의 `models/qwen3.8-27b/Qwen3.8-27B-Q4_K_M.gguf`에 둔다.
- Compose는 `minecraft_llm`에만
  `${EVELYN_QWEN38_LLAMA_CPP_DIR:-${USERPROFILE}/llama.cpp-qwen38-4d19b287}:/qwen-llama:ro`
  를 mount하고 `/qwen-llama/build/bin/llama-server`와 같은 build의 shared library를 쓴다.
  Main/router/sub LLM의 `/llama` mount와 binary는 바꾸지 않는다. 이는 이미 Main에서 검증한
  side-by-side build 패턴을 재사용하는 가장 작은 격리다.
- 버림: 현행 `C:\Users\Admin\llama.cpp`를 pull/build하거나 기존 GGUF를 덮어쓰면 네 LLM의
  rollback 단위를 결합한다. 공식 BF16을 host에서 quantize하면 RAM 여유가 부족하다. Unsloth
  dynamic/UD quant, MTP, Q3 fallback은 추가 format·quality 판단을 요구한다. Q5 이상은 ASR과의
  VRAM gate를 구조적으로 위협한다. context 확대와 timeout 증가는 모델 교체 실패를 숨긴다.

#### 고정 artifact·build 절차

1. 시작 owner/currentness를 P0-3 annotated tag
   `evelyn-recovery-2026-08-26^{}`, clean root/submodule status, Docker 원래 상태, GPU UUID와
   physical index `1`, 현행 Qwen GGUF SHA-256, 현행 llama.cpp commit으로 기록한다. tag와 HEAD가
   다르거나 P0 gate가 하나라도 닫히지 않으면 중단하고 재설계한다.
2. 기존 llama.cpp repository에서 exact commit만 fetch한 뒤 위 exact path에 detached worktree를
   만든다. WSL/Linux에서 `Release`, `GGML_CUDA=ON`,
   `CMAKE_CUDA_ARCHITECTURES=86`으로 `llama-server`와 matching libraries를 같은 `build/`에
   build한다. `CMakeCache.txt`, Git HEAD, binary/shared-library SHA-256을
   `build/evelyn-qwen38-build-identity.json`에 기록하고 다시 읽어 일치하지 않으면 중단한다.
3. pinned resolve URL에서 model을 같은 directory의 `.partial`로 받는다. 완료 뒤 SHA-256이 위
   값과 exact match할 때만 atomic rename한다. GGUF metadata의 architecture `qwen35`, quantized
   tensor type Q4_K_M, context 262144, 약 27B parameter 계열을 검사한다. hash 또는 metadata가
   다르면 파일을 model로 채택하지 않는다.
4. 새 build/model을 production Compose에 연결하기 전에 GPU1에서 candidate만 격리 기동한다.
   `/health`, `/props`, 한국어 1회, JSON 1회, non-thinking/no-`<think>` 1회를 smoke한다. load/OOM,
   malformed output, model identity mismatch가 있으면 현행 default를 건드리지 않고 실패한다.
5. 현행 14B와 후보 27B를 동시에 올리지 않고 같은 고정 synthetic corpus를 순차 실행한다.
   baseline aggregate와 candidate aggregate가 모두 기록된 뒤에만 비교하며 원문 prompt/output,
   token, transcript는 report/docs에 저장하지 않는다.
6. 후보만 모든 gate를 통과하면 아래 source/config/test 변경을 기계적으로 적용한다. focused와
   canonical suite를 통과한 뒤 Qwen+STT+Fast Main overlap과 restart를 실행한다. 모두 통과한
   경우에만 새 Qwen을 default로 승격한다.

#### 고정 production/config/test 변경

1. `.env.example`에 optional `EVELYN_QWEN38_LLAMA_CPP_DIR` 한 항목과 Qwen-only
   side-by-side/SM86 설명을 추가한다. secret이나 실제 사용자 절대경로는 넣지 않는다.
2. `docker-compose.fast-control.yml`의 `minecraft_llm`만 `/qwen-llama:ro`를 사용한다. startup은
   build identity의 exact commit/SM86, server와 shared libraries 존재, model SHA-256을 먼저
   fail-close 검사한 뒤 기존 UUID epoch를 atomic publish하고 server를 exec한다.
3. server option은 `-ngl 999`, `-c 6144`, `-np 1`, threads `8`, batch `512`, ubatch `128`,
   K/V `q8_0`, cache-prompt, Jinja, `--no-mmproj`, reasoning off/budget `0`/format `none`,
   `enable_thinking=false`를 그대로 유지한다. port `9823`, GPU physical index `1`, network,
   healthcheck, epoch와 admission owner도 바꾸지 않는다.
4. `docker-compose.fast-control.yml`의 Bot API/Discord model env,
   `evelyn_core/runtime/evelyn_core/config.py`, `runtime_config_schema.py`,
   `evelyn_core/runtime/evelyn_core/mindcraft_llm_broker.py`,
   `external/mindcraft_evelyn/profiles/evelyn.json`,
   `tools/gpu1_latency_benchmark.py`의 model 식별자를 모두 exact
   `Qwen3.8-27B-Q4_K_M.gguf`로 바꾼다. URL, kind, timeout과 prompt 계약은 바꾸지 않는다.
5. `tools/qwen_model_candidate_validator.py`와
   `tests/tools/test_qwen_model_candidate_validator.py`를 dependency 없이 추가한다. tool은 같은
   endpoint에 한 모델씩 실행하고 content-free aggregate를 atomic JSON으로 쓰며 baseline/candidate
   두 report의 exact source/model/build/GPU binding을 검사한 뒤 비교 verdict를 만든다.
6. `tests/runtime/test_docker_compose_contract.py`,
   `tests/mindcraft/test_mindcraft_runtime_contract.py`,
   `tests/core/test_specialist_llm_runtime.py`, `tests/runtime/test_mindcraft_llm_broker.py`,
   `tests/tools/test_gpu1_latency_benchmark.py`의 exact model/build/mount/fail-close 계약만 갱신한다.
   기존 Qwen queue/inference timeout 회귀는 수정하거나 완화하지 않는다.
7. live 통과 뒤 `docs/CURRENT_STATE.md`, `docs/ACTIVE_RISKS.md`,
   `docs/EVELYN_DOCKER_RUNTIME_QUICKSTART.md`, `docs/MINDCRAFT_MIGRATION.md`, worklog과
   `docs/01_NOW.md`에 aggregate와 rollback 상태만 기록한다. 과거 worklog는 고치지 않는다.

#### 입력·출력·역할별 품질 계약

- validator corpus는 private data가 없는 고정 20건이다: Mindcraft action 3, chat 2,
  classifier 2, memory 2, recovery 2, subgoal 2, general specialist 2, Minecraft specialist 2,
  `/작업` task 3. 실제 production payload builder와 broker kind를 사용하고 temperature `0`,
  현행 role별 max token과 timeout을 사용한다.
- action은 allowlisted normal-player `!` command 또는 safe no-action만, classifier는 exact enum만,
  memory/recovery/subgoal은 기존 parser/schema만, task는 기존 JSON schema/action allowlist만
  통과로 인정한다. 모든 역할에서 slash/cheat/server-control, 승인 없는 mutation, `<think>`·reasoning
  leakage, malformed JSON, 빈 출력, private/raw echo는 1건만 있어도 실패다.
- baseline과 candidate 각각 20/20 parse/format/safety 통과가 필수다. candidate는 고정 expected
  predicate 통과 수가 baseline보다 낮아서는 안 되고, task 3/3, action 3/3, 나머지 각 kind
  전건 통과여야 한다. 자유문 prose의 문체를 수동 점수로 승격 근거로 사용하지 않는다.
- report는 schema version, timestamp, source commit, model repository/revision/SHA, build commit/
  binary/library hashes, GPU UUID/index, case별 opaque ID·pass/fixed failure code·latency와 aggregate만
  가진다. prompt/output/audio/transcript/token/credential은 기록하지 않는다.

#### 상태·timeout·실패·취소·재시작 계약

- 상태는 `preflight_verified → source_built → artifact_verified → isolated_passed →
  baseline_recorded → candidate_recorded → quality_passed → source_changed → tests_passed →
  overlap_passed → restart_passed → promoted → host_restored` 단방향이다. atomic report의 마지막
  verified state와 identity만 재시작 기준이며 partial state를 성공으로 해석하지 않는다.
- download/build는 명시적 process PID와 exact path를 owner로 기록한다. 취소·timeout 시 그 owner만
  종료하고 기존 llama.cpp/model과 Docker desired state는 건드리지 않는다. `.partial`은 hash 검사
  전 model 경로로 rename하지 않는다.
- Qwen inference timeout은 현행 6,000ms, queue timeout은 30초로 유지하며 서로의 budget을 소비하지
  않는다. model health start period는 120초 계약을 유지한다. timeout/OOM/driver reset이면 sample과
  phase는 실패하고 자동 retry·Q3 fallback·timeout 연장을 하지 않는다.
- 각 A/B phase는 이전 exact container/process가 종료되고 GPU1 memory가 preflight baseline ±256MiB로
  3회 돌아온 뒤 다음 model을 시작한다. 3회 restart는 매번 UUID epoch가 바뀌고 stale broker marker가
  거부되며 첫 request가 exact model로 성공해야 한다.
- 실패가 source 변경 전이면 candidate artifact/report만 보존하고 현행 default는 그대로 둔다.
  source 변경 뒤 실패면 변경한 model/build pointer를 현행 14B와 기존 `/llama/build`로 되돌리고
  Qwen+Bot API exact containers만 재시작해 health, epoch change, fixed smoke를 확인한다.

#### 보안·내구성·호환성·성능·rollback

- community artifact는 TLS URL, repository revision, filename, SHA-256 세 겹으로 결박한다. model,
  build, report는 repository 밖에 두고 Git/docs에는 content나 private prompt를 넣지 않는다.
- 기존 14B GGUF와 commit `47e1de77...` build를 P1-1과 P1-2가 끝날 때까지 삭제·덮어쓰기하지
  않는다. 삭제는 별도 설계·승인을 요구한다. host free disk preflight는 model/build/partial과
  rollback copy를 포함해 최소 40GB다.
- API path, OpenAI-compatible message shape, role kind, context 6144, output token cap, timeout,
  admission FIFO/epoch, GPU1 assignment은 호환 계약이다. model 이름만 exact 새 식별자로 변한다.
- 성능은 단독 tokens/s가 아니라 Fast Main+Qwen+STT 동시 사용자 SLO로 판정한다. 품질 또는 safety를
  낮춰 VRAM/latency를 통과시키지 않는다.
- rollback target은 `Qwen3-14B-Q4_K_M.gguf`, 기존 `/llama/build`, 기존 model config/profile와
  그 source checkpoint다. rollback 뒤 health/smoke와 GPU baseline 복귀가 확인되기 전에는
  복구 완료로 보고하지 않는다.

#### 먼저 실패할 회귀·검증·수치형 완료 gate

- 먼저 실패할 회귀: 새 model 문자열/mount/build identity/model SHA가 없거나 한 곳이라도 14B
  default가 남으면 실패한다. Qwen service가 Main 전용 build를 쓰거나 router/sub mount를 바꾸면
  실패한다. reasoning/MTP/mmproj/context/timeout이 변하면 실패한다. 잘못된 SHA·SM arch·commit,
  stale epoch, malformed role output, private report content는 각각 fail-close해야 한다.
- focused: 위 변경 파일의 unit/contract tests, Qwen broker queue-vs-inference timeout 회귀,
  production Compose render와 service manifest/shutdown tests, Mindcraft overlay Node tests를 모두
  통과한다. broad: canonical `python -m pytest -q` 기능 실패 0개와 submodule clean이다.
- isolated quality A/B는 baseline/candidate 각 20/20 format/safety와 candidate expected predicate가
  baseline 이상이어야 한다. Qwen request timeout/error/OOM은 0개다.
- overlap은 기존 `tools/gpu1_latency_benchmark.py`를 각 model에 warmup 2회+measured 20회로 실행한다.
  candidate gate는 Fast Main TTFT p95 `<=1,000ms`, Qwen p95 `<=6,000ms`, STT final p95
  `<=1,200ms`, Qwen/STT/Main/GPU error·timeout/OOM `0`, GPU1 min free `>=2,048MiB`다.
  candidate STT p95는 같은 session baseline보다 10% 넘게 악화되면 안 된다.
- 3회 cold restart에서 health 성공 3/3, exact epoch 교체 3/3, 첫 request 성공 3/3,
  stale marker 거부 3/3이어야 한다. cleanup 뒤 Docker desired state, GPU1 preflight baseline ±256MiB
  3회, owned process/container/partial file 0개를 확인한다.
- 하나라도 실패하면 Qwen3.8을 default로 승격하지 않거나 즉시 14B로 rollback한다. Q4 실패를 이유로
  다른 quant를 자동 선택하지 않는다. 새 후보는 새 설계와 승인을 요구한다.

#### 예상 diff·미해결 질문

- 예상 production/config diff는 `.env.example`, `docker-compose.fast-control.yml`, runtime config/
  schema/broker 3개, Mindcraft profile 1개, benchmark default 1개다. 새 validator/tool test 2개와
  기존 contract test 5개, 검증 뒤 current-state/risk/quickstart/migration/NOW/decision/worklog/plan
  문서만 바뀐다. DB/schema migration, package dependency, network API, prompt, timeout diff는 0이다.
- artifact, quant, build commit/path/arch, runtime option, 역할 corpus, gate, promotion, rollback과
  보존/삭제 정책을 모두 위에서 고정했다. **미해결 설계 질문은 0개다.**

완료 조건: exact Qwen3.8 Q4_K_M과 pinned SM86 build가 20-case 역할 계약, 전체 회귀,
GPU1 overlap, 3회 restart, cleanup gate를 모두 통과해 default로 승격되고, 기존 Qwen3-14B로의
검증된 rollback 경로와 artifact가 그대로 남는다.

### P1-1. 음성 대화를 대표 실환경 E2E로 완성

상태: **[R] P0-4 headless STT는 `image_ready` diagnostic 2+20만 검증됨;
private corpus·승격과 실제 음성 E2E는 대기**. 현재 diagnostic image report는 재사용하되
`corpus_passed`는 아직 없다. P0-4가 이를 닫은 뒤 남은 endpoint 계측, microphone, speaker,
Discord 연결과 실제 장치 청취는 별도 구현/live 승인 전 시작하지 않는다.

#### 문제·검증된 원인·영향

- 현재 Bot API durable lease와 Local 500ms chunk/Qwen streaming, Discord completed-PCM batch,
  content-free `voice-p0.v1` 11-step runner는 구현돼 있다. 그러나 report는 playback 중심의
  단일 `latencyMs`만 가져 speech start, first partial, endpoint, final, barge stop을 분리하지
  못해 사용자가 느끼는 지연과 truncation을 판정할 수 없다.
- `evelyn_voice/client.py`의 기본 `VOICE_END_SILENCE_SEC=0.82`는 Discord의 목표
  last-voiced→endpoint p95 `<=700ms`보다 이미 120ms 길다. 현재 기본값으로는 network/model과
  무관하게 목표가 수치상 불가능하다.
- 현재 UI의 `heard:true`는 소리가 난 사실만 확인하며 발음·운율·화자 유사도를 통과했다는
  청취 증거가 아니다. 고정 corpus의 streaming-vs-batch CER/entity/negative-admission 증거와
  실제 Local↔Discord handoff/reconnect/restart trace도 없다.
- 따라서 source/offline tests가 많아도 실제 사용에서 늦은 endpoint, 잘린 발화, echo 수락,
  이중 capture, stale/duplicate playback·continuity를 완료로 오판할 수 있다.

#### 목표·범위·비범위

- 목표는 Local과 Discord 각각의 대표 11-step 음성 suite를 실제 장치에서 완주하고,
  content-free timing, 고정 private corpus 정확도, capture owner, playback/continuity 증거를
  하나의 report v2 계약으로 fail-close하는 것이다.
- 포함: Discord endpoint 기본값 0.60초, 기존 Local streaming과 Discord completed-PCM 경로의
  계측, composite 청취 확인, 50-item private benchmark, barge-in/echo/전환/reconnect/process
  restart, STT image/GPU health·OOM·cleanup 검증이다.
- 비범위: Discord packet-time partial streaming, 새 KWS/AEC, ASR/TTS model 변경, context-term
  injection, prefetch, 새 GPU scheduler/broker/dependency, voice/persona tuning, Minecraft다.
  기존 Discord batch endpoint와 Local streaming/fallback 구조를 재설계하지 않는다.
- `voice-p0.v1` report는 읽을 수 있게 유지하되 P1-1 완료 증거로는 인정하지 않는다.
  새 검증 출력만 `voice-p0.v2`로 기록한다.

#### 선택한 해법과 버린 대안

- 선택: 현행 single-owner/streaming/batch 구조에 monotonic content-free timing만 얇게 추가하고,
  Discord silence 기본값을 0.60초로 낮춘다. 기존 validation runner를 v2로 확장하고 기존
  scoreboard 함수를 재사용한 한 개의 standalone benchmark tool로 exact gate를 계산한다.
- 0.60초는 700ms gate에 100ms scheduling headroom을 남기면서 현재 0.82초에서의 지연 원인을
  직접 제거한다. truncation 0/40 gate가 너무 공격적인 endpoint 변경을 막는다.
- 버림: 목표 숫자만 0.82초 위로 올리는 것은 사용자 지연을 해결하지 않는다. packet-time
  Discord decode와 새 scheduler는 이번 대표 E2E에 필요 없고 실패 면적을 늘린다. transcript/raw
  audio를 report/docs에 넣거나 사람 청취 없이 자동 점수만 쓰는 방식은 privacy와 음질 증거를
  충족하지 못한다. 여러 새 UI boolean이나 새 telemetry framework도 필요 없다.

#### 고정 production/config/UI 변경

1. `.env.example`에 `VOICE_END_SILENCE_SEC=0.60`을 명시하고
   `evelyn_voice/client.py`의 fallback 기본도 0.60으로 맞춘다. utterance start,
   `last_voice_like_at`, endpoint와 callback monotonic timestamp에서
   `lastVoiceToEndpointMs`, `endpointToFinalMs`를 계산한다.
2. `evelyn_core/runtime/evelyn_core/local_mic.py`의 `_begin_capture`,
   `_finish_stream_capture`, `_flush_active_segment`에서 speech start, last voiced, endpoint를
   content-free callback metadata로 전달한다. wall-clock이나 audio/transcript는 넣지 않는다.
3. `evelyn_core/runtime/evelyn_core/local_io_bridge.py`의
   `_start_local_asr_capture`, `_push_local_asr_capture`, `_finish_local_asr_capture`와 worker에
   `(admission_epoch, capture_generation)` keyed bounded timing record를 둔다. first nonempty
   partial, endpoint, final, fallback/rollback을 exact current record에만 기록하고 terminal 뒤
   즉시 제거한다.
4. `evelyn_core/runtime/evelyn_core/voice_audio_ingress_runtime.py`는 Discord nested metadata에서
   allowlist된 content-free timing만 보존한다.
   `voice_transcript_finalize_runtime.py`는 exact validation observer에 endpoint→final을 붙인다.
5. `local_io_bridge.py`의 `_barge_in_worker`와
   `evelyn_core/runtime/evelyn_core/tts_interrupt_runtime.py`의
   `run_voice_tts_interrupt_gate_from_runtime`/
   `stop_active_tts_playback_from_runtime`에서 qualification 직전부터 verified playback stop
   result까지 `qualifiedBargeToStopMs`를 기록한다. stop 실패를 성공 latency로 쓰지 않는다.
6. `evelyn_core/runtime/evelyn_core/voice_validation.py`를 `voice-p0.v2`로 올리고 step별 optional
   `timings`(`speechStartToFirstPartialMs`, `lastVoiceToEndpointMs`,
   `endpointToFinalMs`, `qualifiedBargeToStopMs`)와 surface별 p50/p95 aggregate를 추가한다.
   Local speech step은 first-partial/endpoint/final, Discord speech step은 endpoint/final,
   barge step은 qualification/stop timing을 필수로 한다. 해당 step의 필수 timing 누락과 gate
   초과는 warning이 아니라 attempt 실패다.
7. `docs/assets/evelyn-voice-validation.js`의 기존 boolean confirm을 유지하면서 label을
   `재생 품질 통과(들림·발음·운율·화자)`로 바꾼다. 별도 UI state/schema는 만들지 않는다.
8. `tools/voice_asr_benchmark.py` 하나를 추가해 `tools/ko_stt_scoreboard.py`의
   `score_transcript`와 기존 `stt_client`를 재사용한다. dependency는 추가하지 않는다.

#### 고정 private corpus와 benchmark 계약

- manifest와 audio는 `runtime_artifacts/validation/voice_asr/`에만 두고 Git/docs/report에는
  raw audio, transcript, 개인 식별 정보 대신 aggregate/count/hash/identity만 남긴다.
- corpus는 정확히 50개다. positive 40개는 기존 `_suite_steps()`의 exact 10 speech prompt를
  clean capture한 것, 같은 10개를 fan/keyboard far-field에서 capture한 것, 아래 domain 10개를
  clean capture한 것, 같은 domain 10개를 동의된 Discord session의 completed Opus-decoded PCM
  경계에서 capture한 것이다.
- domain phrase 10개는 다음으로 동결한다.

  1. 이블린, 다이아몬드 곡괭이를 찾아줘
  2. 이블린, 참나무 원목을 열두 개 모아줘
  3. 이블린, 제작대에서 빵 세 개를 만들어줘
  4. 이블린, 크리퍼와 스켈레톤을 피해줘
  5. 이블린, Control Page 상태를 확인해줘
  6. 이블린, Discord 음성 연결을 다시 확인해줘
  7. 이블린, Main LLM과 Qwen ASR 상태를 알려줘
  8. 이블린, GPU 일 번의 VRAM을 확인해줘
  9. 이블린, 마인크래프트 Voyager 상태만 보여줘
  10. 이블린, 오후 세 시 이십오 분에 열두 개를 세어줘

- negative 10개는 silence 2, fan/keyboard 2, music 2, OmniVoice echo-only 2,
  mid-sentence/near-miss wake 2다. 모두 accepted turn과 high-impact action이 0이어야 한다.
- 각 positive는 Qwen batch baseline과 500ms paced chunk/2초 partial cadence streaming에
  입력한다. batch/stream CER, first nonempty partial, endpoint/final, retry, truncation,
  stable-prefix rollback과 manifest entity token exact를 계산한다. Discord 10개는 completed-PCM
  endpoint도 별도 측정한다. 최소 entity occurrence는 20개다.
- raw Discord capture는 `VOICE_DEBUG_SAVE_AUDIO=true`를 명시 승인된 corpus capture 구간에만
  켜고 즉시 false로 복구한다. aggregate/hash가 만들어지고 검증되면 private audio를 삭제하며,
  사용자가 별도 보존을 요구하지 않는 한 retention은 0이다.

#### 상태·owner·timeout·취소·재시작 계약

- validation 상태는 `preflight → image_ready → corpus_passed → local_passed →
  discord_passed → restart_passed → restored` 단방향이다. exact source commit, STT image ID,
  Qwen model/backend, physical GPU UUID, voice lease epoch, validation attempt ID에 결박한다.
- Local/Discord capture lease owner 수는 항상 0 또는 1이다. mode 전환은 old physical capture
  stop과 exact lease release가 확인된 뒤에만 successor를 시작한다. late driver start와 old
  stream event는 generation/currentness 불일치로 폐기한다.
- 현행 attempt 최대 3회와 TTL 30분을 유지한다. timeout/currentness change/취소는 active ASR
  stream을 cancel하고 physical worker 종료 뒤 exact lease를 해제한다. 자동 4차 retry나 다른
  surface 승격은 없다. 실패 report는 content-free 상태와 수치만 보존한다.
- process restart는 accepted user-only durable turn 뒤 수행하고, 복구 후 exact latest room owner의
  successor만 assistant projection/continuity를 한 번 commit한다. stale/duplicate playback이나
  older/tie/expired recovery는 fail-close한다.

#### 안전·privacy·성능·rollback

- report/health/docs에는 raw PCM, transcript, prompt, credential, channel/user ID를 넣지 않는다.
  timing은 monotonic duration과 aggregate만 노출한다. negative는 tool/high-impact route에 도달하면
  즉시 실패다.
- 실제 run 전 original Docker desired state, previous STT image ID, streaming flags,
  `VOICE_END_SILENCE_SEC`, GPU UUID/VRAM을 snapshot한다. 실패 시 previous image로 되돌리고 두
  streaming flag를 false로 만들 수 있으며 endpoint 회귀/절단이면 silence를 0.82초로 복원한다.
  mic/Discord desired state와 Docker 상태도 exact snapshot으로 돌린다.
- model, batch fallback, single inference lock, 30초 audio cap, 60초 stream TTL, 4-session cap,
  GPU1 memory fraction 0.35는 바꾸지 않는다. container 내부 `cuda:0` 표기 대신 physical GPU1
  UUID/identity로 device를 검증한다.

#### 테스트와 승인 후 live 실행 순서

1. 구현 전 회귀를 먼저 추가한다: 0.82초 default가 700ms gate에 부적격, v1/missing timing
   report가 완료에 부적격, wrong generation timing 폐기, fallback/cancel record cleanup,
   stop 실패가 barge latency 성공으로 기록되지 않음을 고정한다.
2. 수정/추가할 tests는 `tests/voice/test_voice_utterance.py`,
   `tests/voice/test_local_mic_segment_runtime.py`, `tests/voice/test_local_asr_streaming.py`,
   `tests/voice/test_voice_audio_ingress_runtime.py`,
   `tests/voice/test_voice_transcript_finalize_runtime.py`,
   `tests/voice/test_tts_interrupt_runtime.py`, `tests/runtime/test_voice_validation.py`,
   `tests/runtime/test_voice_validation_api.py`,
   `tests/ui/test_control_page_voice_validation.py`, 새
   `tests/tools/test_voice_asr_benchmark.py`다.
3. focused suite, 관련 admission/cancel/restart/continuity suite, canonical
   `python -m pytest -q`를 차례로 통과한다. source tests만으로 live 완료를 주장하지 않는다.
4. 승인된 live preflight에서 P0-3 tag와 clean tree, P0-2 green suite, attempt 5 restoration을
   확인한다. revised STT image만 build/load하고 exact source/image/model, `backend=vllm`, physical
   GPU1 UUID, memory 0.35, health ready를 확인한다. cancel/restart/successor smoke에서 orphan
   session/OOM 0을 요구한다.
5. private 50-item benchmark를 실행한다. normal core는 mic/Discord OFF로 시작하고 공식
   runtime checker를 통과시킨다.
6. 사용자 동의 아래 Local ON으로 11-step suite와 composite 청취를 수행한다. start 중 즉시
   OFF late-cancel, Local→Discord→Local 전환을 포함한다.
7. 사용자 동의 아래 Discord ON/target voice에서 11-step suite를 수행하고 기존
   `/voice reconnect`, Discord OFF/ON과 실제 voice-server rejoin/rearm을 검증한다.
8. 대화 중 process restart 뒤 follow-up을 수행해 visible user/assistant projection,
   continuity commit, playback이 각각 정확히 1개인지 확인한다. 마지막에 original desired
   state/image/config를 복구하고 raw capture를 제거한다.

#### 수치형 완료 gate

- Local speech-start→first-partial p95 `<=2500ms`(2초 cadence 유지), Local
  last-voiced→endpoint p95 `<=500ms`, Discord `<=700ms`.
- endpoint→final p95는 Local `<=900ms`, Discord `<=1200ms`.
- qualified barge-in→verified TTS stop p95는 Local `<=200ms`, Discord `<=300ms`.
- positive 40개에서 offline retry `<=20%`, truncation `<=1%`이므로 실제 허용 truncation은
  0개, stable-prefix rollback 0개, raw leakage 0개다.
- streaming CER는 동일 audio의 batch Qwen baseline보다 나쁘지 않고, manifest entity token
  exact `>=95%`; negative 10개 accepted turn 0개, unauthorized high-impact action 0개다.
- Local/Discord 각각 현재 11개 step을 attempt 최대 3회 안에 11/11 통과한다. capture owner
  최대 1, stale/duplicate playback·visible projection·continuity commit 0, OOM/orphan stream 0,
  GPU minimum free memory `>=2048MiB`, final latency regression 0이다.
- full pytest 기능 실패 0, final report에 exact source/STT image/model/backend/physical GPU identity와
  state restoration 성공이 있고, 실제 장치 청취 confirm이 true여야 한다.

#### 예상 diff·미해결 질문

- 예상은 production/config/UI 9개 파일, 기존 test 9개, 새 benchmark tool/test 2개와
  `docs/KOREAN_ASR_TARGET_ARCHITECTURE.md`, `docs/CURRENT_STATE.md`,
  `docs/ACTIVE_RISKS.md`, `docs/EVELYN_DOCKER_RUNTIME_QUICKSTART.md`, `docs/01_NOW.md`,
  `docs/worklog/2026-08-26.md` 갱신이다. report v2 외 DB/schema migration은 없고 dependency
  추가도 없다. 실제 변경 파일이 늘거나 packet-time 구현이 필요해지면 즉시 중단·재설계한다.
- endpoint 값, timing schema, corpus 구성·문장, 수치 gate, owner/취소/restart, private retention,
  live 순서와 rollback을 모두 위에서 고정했다. **미해결 설계 질문은 0개다.**

완료 조건: 모든 source/offline 수치 gate와 승인된 Local/Discord live gate가 통과하고,
`말하기 → STT → Main → 첫 PCM → 재생 완료 → continuity commit`이 surface별 한 trace로
관측되며 final state가 원래 Docker/mic/Discord/image/config로 복구된다.

### P1-2. 안전한 Minecraft 승인 E2E 닫기

- [!] 운영 월드는 현재 안전 조건이 없으므로 재접속하지 않는다. 사용자 승인된 fresh
  world/fixture에서만 실행한다.
- [ ] Discord 승인 → lease → connect → functional readiness → 실제 world effect →
  verified outcome → stop을 한 세션에서 확인한다.
- [ ] 식량 부족·제작대 없음 fixture에서 통나무 → 판자 → 제작대 → 빵 → 섭취 체인을
  실제 inventory 변화로 검증한다.
- [ ] effect 중 disconnect/restart/reconnect와 위협 중 restart에서 오래된 grant,
  action result, cursor가 재사용되지 않는지 확인한다.

완료 조건: 성공·실패·취소·재시작이 동일 lease/actionRun 증거로 닫히고, cleanup 후
Minecraft/Voyager 운영 bot이 의도한 OFF 상태다.

### P1-3. `/작업` 기능을 실제 지식 작업에 쓸 수 있게 만들기

- [ ] `review|summarize|explain|compare`가 raw receipt만으로 성공을 주장하지 않도록
  유지하면서, goal-bound source evidence만 인용하는 최소 semantic evaluator를 설계한다.
- [ ] 명시적 링크 요청에는 allowlisted URL을 결과에 표시할 수 있는 정책을 추가한다.
- [ ] 10KiB를 넘는 파일 수요가 실제로 확인될 때만 별도 bounded large-read grant를
  추가한다. 기본 step budget을 무작정 늘리지 않는다.
- [ ] behavioral mutation은 독립 Host evaluator가 생기기 전까지
  `semanticVerified:false`와 자동 재시도 금지를 유지한다.

완료 조건: 읽기/비교/요약의 결과가 exact source evidence와 연결되고, 모델 문장만으로
완료가 승격되지 않으며, 대표 Control Page와 Voice E2E가 통과한다.

### P1-4. 운영 보안·내구성 마감

- [ ] continuity와 memory deletion의 HMAC key/external anchor를 repository 밖에 배치하고
  Windows DACL/owner 및 Docker read-only mount를 실제로 검증한다.
- [ ] Codex action route는 tool registry와 secret-canary 비노출이 실제 image에서
  증명되기 전까지 비활성으로 유지한다.
- [ ] Qwen-ASR/Transformers와 Mineflayer 계열 보안 릴리스를 추적하되, 호환 smoke 없이
  강제 upgrade나 `npm audit fix --force`를 실행하지 않는다.
- [ ] Runtime Health가 Discord/Minecraft를 실제 기동한 상태에서도 원문·경로·credential을
  노출하지 않고 정확한 desired/off/degraded 상태를 보이는지 확인한다.

완료 조건: restart/rollback 공격과 권한 경계의 live 증거가 남고, 미검증 action route는
계속 fail-closed하며, public health privacy 검사가 통과한다.

## P2 — P0/P1 이후에만 수행

- [ ] Live2D 꼬리 속도·굴곡의 최종 육안 튜닝.
- [ ] UI Automation Button의 되돌릴 수 있는 live corpus와 접근성 검증.
- [ ] Vision cold-cache build 비용을 측정한 뒤 dependency base image 또는 persistent
  cache 중 하나만 선택.
- [ ] 저장공간 dry-run 보고를 검토하고, 실제 누적이 확인될 때만 별도 승인으로 정리.
- [ ] `main.py`는 2,500줄/선언형 wiring 경계를 유지한다. 줄 수만 줄이기 위한 registry,
  factory, 새 bootstrap 계층은 만들지 않는다.

## 보류 원칙

- 한 번 승인된 동결 계획에는 같은 범위·안전 경계·완료 gate 안에서 필요한 실패 진단,
  회귀 수정, 새 attempt와 검증 반복이 포함된다. 새로운 제품 판단, 외부 효과, 모델·임계값·
  범위 변경이 생길 때만 다시 설계를 동결하고 승인을 받는다.
- 실제 실패나 사용 수요가 없는 새 daemon, queue, plugin, 추상화는 추가하지 않는다.
- live Discord, microphone, Minecraft, Docker 운영 서비스는 사용자 승인 없이 시작하지 않는다.
- source/offline test 통과를 live 검증으로 보고하지 않는다.
- 성능 최적화는 STT final부터 실제 첫 PCM까지의 사용자 체감 SLO와 품질·안전·cleanup을
  함께 통과할 때만 채택한다.

## 주요 근거

- `docs/01_NOW.md`
- `docs/CURRENT_STATE.md`
- `docs/ACTIVE_RISKS.md`
- `docs/worklog/2026-08-26.md`
- `docs/MAIN_LLM_LATENCY_TARGET_ARCHITECTURE.md`
- `docs/KOREAN_ASR_TARGET_ARCHITECTURE.md`
- `docs/CONVERSATION_CONTINUITY_CONTRACT.md`
- `docs/MINECRAFT_AUTONOMY_READINESS_CONTRACT.md`
