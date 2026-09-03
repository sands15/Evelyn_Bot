# Evelyn 실행 계획

마지막 검토: 2026-09-03 KST

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

### P1-1A. 개인 Local Voice 300ms soft endpoint + 추가 500ms reopen

상태: **[!] 2026-09-03 설계 승인·동결; source 구현·테스트·live 활성화는 사용자 선택으로 대기**.
현재 개인 Local Bridge는 500ms hard endpoint이며 일반 재입력 merge는 없다. 이 항목은 활성화될 때
P1-1의 Local endpoint 의미만 soft/hard 지표로 분리하고 Discord·barge-in 계약은 바꾸지 않는다.

#### 문제·검증된 root cause·영향

- `local_mic.py`는 silence threshold에서 capture를 끝내고, `stt_service.py`의 stream `finish`는
  server session을 제거한다. 300ms에 finish한 뒤 500ms 안의 음성을 같은 ASR session으로 reopen할
  수 없다.
- `/chat-stream`은 Main 호출 전에 admission과 durable ingress를 소비할 수 있고 이후 history,
  archive, memory, tool/action과 playback 경로가 이어진다. client task 취소는 이미 생긴 부작용의
  rollback 계약이 아니다.
- 따라서 `300ms hard final → request 시작 → 재입력 때 cancel → text concat`은 duplicate/orphan
  turn과 잘못된 action을 만들 수 있다. 반대로 hard endpoint를 단순히 800ms로 늘리기만 하면 현재
  500ms보다 느려지므로 ASR/Main/TTS 계산 overlap이 없으면 최적화가 아니다.

#### 목표·범위·비범위

- 마지막 voiced sample 뒤 300ms에 soft checkpoint를 만들고, 추가 500ms 동안 같은
  capture generation·PCM·ASR session을 유지한다. grace 내 speech는 이전 음성과 하나의 utterance로
  처리하고, 없을 때만 약 800ms에 hard commit한다.
- soft 시점부터 side-effect-free ASR/draft 계산을 겹쳐 warm 상태의 last-voice→verified first PCM
  p95를 1,000ms 미만으로 만든다. hard commit 전 external/durable effect는 0이어야 한다.
- 포함: 개인 Local capture/ASR state, ephemeral response draft, exact promotion, content-free timing,
  rollback과 source/live gate다.
- 비범위: Discord endpoint, qualified barge-in, model/quant, KWS/AEC, text concatenation, 새 dependency,
  cloud fallback, raw transcript/audio 보존이다.

#### 동결한 state·transaction 계약

1. Local 전용 gate는 `LOCAL_MIC_SOFT_REOPEN_ENABLED=false`, 설정은
   `LOCAL_MIC_SOFT_ENDPOINT_MS=300`, `LOCAL_MIC_RESUME_MERGE_WINDOW_MS=500`으로 명명한다.
   gate가 false면 두 새 값은 무시하고 현행 `LOCAL_MIC_MAX_SILENCE_MS=500`만 hard endpoint로 쓴다.
   gate가 true여도 `LOCAL_BRIDGE_STT_STREAMING_ENABLED=true`가 아니면 soft path를 열지 않고
   `soft_reopen_requires_streaming` 상태로 현행 hard path를 유지한다.
2. `STREAMING → SOFT_PENDING`에서 pending PCM만 현행 ASR stream에 drain한다. capture context,
   full PCM과 generation은 보존하며 `on_speech_end`, segment dispatch, ASR `finish`를 호출하지 않는다.
3. grace 내 voiced block은 같은 generation을 `STREAMING`으로 되돌리고 현재 draft를 abort한다.
   다음 300ms pause는 전체 누적 PCM 위의 새 `soft_epoch`다. 문자열을 합치거나 새 turn ID를 만들지 않는다.
4. resume 없이 500ms가 지나면 기존 final/filter/admission 경로로 hard commit을 정확히 한 번 수행한다.
   30ms capture block 구현이면 nominal 약 810ms이며 max-utterance, explicit stop과 capture fault는
   grace를 기다리지 않는 terminal boundary다.
5. soft checkpoint는 capture당 current draft 하나만 `prepare`할 수 있다. prepare는 immutable context를
   읽고 conversational Main/TTS 계산을 process memory에 staging할 수 있지만 admission consume,
   durable ingress/history/archive/memory, tool/action/external effect와 speaker write를 금지한다.
6. draft token은 bridge instance, admission epoch, capture generation, soft epoch, validation binding,
   normalized authoritative-input digest, context revision, model/TTS identity에 결박한다. raw token,
   transcript, prompt, PCM과 owner ID는 log/metric/docs에 남기지 않는다.
7. hard final과 모든 binding이 exact match할 때만 기존 admission·durable user-turn claim을 한 번
   atomic promote하고 accepted user row를 commit한 뒤 staged playback을 연다. exact authenticated
   playback ACK 뒤에만 assistant history·continuity·background action을 현행 계약대로 확정한다.
   failed/partial/cancelled ACK는 accepted user-only turn을 보존한다. mismatch, expiry,
   tool/memory/action route, overload, uncertain cancel은 draft를 버리고 ordinary path를 한 번 실행한다.
8. late prepare 결과는 drain 후 폐기한다. mic OFF/restart, queue drop, filter rejection, validation loss,
   admission epoch/owner change는 ASR와 draft를 함께 정리하며 restart에서 draft를 복구하지 않는다.
9. capture context에 private `_bargeSource`가 있으면 soft/reopen/prepare를 모두 우회하고 현행 qualified
   barge-in capture·admission·playback interrupt 경로를 그대로 사용한다.

#### 구현 순서·rollback

1. `local_mic.py`에 soft/grace 상태와 pending PCM drain을 넣고 `local_io_bridge.py`에서 동일
   `(admission_epoch, generation, soft_epoch)`을 사용한다. ASR start는 1회, hard finish도 1회다.
2. `fast_control_api.py`와 Bridge 사이에 side-effect-free prepare/promote/abort 계약을 추가한다.
   기존 `/chat-stream`을 soft 단계에서 호출하거나 취소 rollback처럼 사용하지 않는다.
3. conversation-only exact draft만 promotion한다. tool/memory/action 후보는 hard commit 뒤 현행 경로를
   사용한다. staged audio는 첫 nonempty PCM chunk 하나와 최대 256KiB로 제한하고 초과 시 ordinary
   path로 fallback한다.
4. `LOCAL_MIC_SOFT_REOPEN_ENABLED=false`는 현재 500ms hard endpoint와 ordinary chat path의 동작
   의미를 유지한다.
   restart도 RAM draft를 버리므로 schema migration과 durable cleanup은 없다.
5. source 회귀와 privacy screen을 통과한 뒤 shadow metric만 수집하고, 별도 사용자의 live mic 승인이
   있을 때 실제 장치 gate를 수행한다. gate 실패 시 feature OFF로 즉시 rollback한다.

#### 후속 테스트와 완료 gate

- 사용자 요청에 따라 이번 작업에서는 테스트를 작성하거나 실행하지 않는다. 구현 재개 시 먼저
  300ms에는 chunk/soft만 있고 end/segment/effect가 0인지, 추가 500ms 내 resume가 동일 generation과
  ASR start 1회를 유지하는지, no-resume hard finish/segment가 1회인지 고정한다.
- 반복 pause/resume stale epoch, late prepare receipt, authoritative text/context mismatch, queue drop,
  filter rejection, OFF/restart, max utterance, admission/validation change와 barge-in 무변경을 회귀한다.
- focused `test_local_asr_streaming.py`, `test_local_mic_routing.py`,
  `test_local_voice_admission_bridge.py`, prepare/promote API tests와 canonical suite 실패 0을 요구한다.
- live gate는 Local last-voice→soft p95 `<=350ms`, warm last-voice→verified first PCM p95 `<1000ms`,
  reopen utterance의 누락/중복 0, admission/history/memory/tool/action/playback duplicate 0,
  truncation regression 0이다. transcript/audio를 report에 저장하지 않는다.

#### 예상 diff·미해결 질문

- 예상 production diff는 `.env.example`, `local_mic.py`, `local_io_bridge.py`, `fast_control_api.py`와 기존 contract
  owner 모듈, 대응 voice/API tests, P1-1 timing schema와 current-state/risk/worklog 문서다. dependency,
  DB/schema migration, Discord/Minecraft diff는 0이다.
- 300ms는 soft, 500ms는 그 뒤의 추가 grace, merge 단위는 PCM/same ASR session, effect boundary는
  exact promote로 모두 고정했다. **미해결 설계 질문은 0개다.**

완료 조건: feature OFF rollback을 보존한 채 source/canonical/live gate가 모두 통과하고, 300ms draft가
reopen에서는 흔적 없이 폐기되며 no-reopen에서는 한 user turn과 한 verified playback으로만 승격된다.

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

### P1-3. 검증 가능한 지식 작업의 실행·평가 기반

상태: **[~] 승인된 source/offline 구현·검증 완료, live gate 대기**. 2026-08-28 사용자가
전체 구현을 명시 승인해 `P1-3A → P1-3B`를 구현했다. exact TaskWorkContract와 process-local
inspection, source-grounded draft, 고정 24-row evaluator와 source-owned Qwen broker runner가
전체 회귀를 통과했다. 실제 Qwen 24-row 실행과 production 승격은 live 승인 전이므로 `[x]`가
아니다. 이 항목은 영상의 selective context, eval, inspectable custom-agent contract를 최소
범위로 옮긴다. principal·삭제 계보·durable promotion은 P1-4 뒤의 P1-5가 소유한다. 영상 근거는
[feedback router/fixer 06:53](https://youtu.be/3flfON7F8hU?t=413),
[사람 수정 기반 self-healing 08:23](https://youtu.be/3flfON7F8hU?t=503),
[context와 row별 eval 13:00](https://youtu.be/3flfON7F8hU?t=780),
[trigger·instructions·context와 draft-only 외부 작업 17:32](https://youtu.be/3flfON7F8hU?t=1052),
[run history·review·rollback·version 23:00](https://youtu.be/3flfON7F8hU?t=1380)이다.

#### 문제·검증된 root cause·영향

- `context_pipeline.py`의 `ContextPolicy`/`ContextPacket`은 selective context를,
  `task_loop_runtime.py`의 `TaskGrant`/`TaskStepReceipt`/`TaskLoopResult`는 bounded 실행과
  typed evidence를 이미 제공한다. `task_approval_runtime.py`도 exact workspace mutation만
  사람 승인에 결박한다. 새 범용 agent framework가 필요한 상태가 아니다.
- 다만 현재 `skills/task_loop/__init__.py`는 route의 messages/ContextPacket을 소비하지 않고
  user goal, source, `TurnScope`만 `run_default_task_loop`에 넘긴다. worker가 실제로 보는 context는
  `_worker_state`의 goal·tool guidance·최근 typed observations뿐이다. 따라서 일반 route/memory
  context를 consumed context로 기록하거나 새로 주입하지 않는다.
- 그러나 현재 실행 계약에는 실제 trigger/skill origin, instruction version, context selection
  manifest, grant/tool 집합, output schema, eval suite version을 한 run identity로 묶은 기록이 없다.
  같은 결과가 어느 instruction/context/evaluator 조합에서 나왔는지 비교할 수 없다.
- `FastActionCoordinator`의 task/event history는 process memory이고,
  `FastActionRecoveryJournal`은 raw prompt/reply를 저장하지 않는 interrupted-work 안전 표식이다.
  후자는 재시작 중 중복 실행을 막지만 terminal step, context 구성, eval, review와 version 전이를
  inspect/replay하는 activity ledger가 아니다.
- `self_model.py`는 tone/identity feedback을 `review_candidate`로 기록하고
  `identity_review.py`는 TSV/Markdown export를 만들지만, accept/reject를 active profile에
  적용하는 authoritative promotion 경로는 없다. 일반 task routing/context/answer correction도
  candidate→eval→approval→promotion→rollback 흐름이 없다.
- `review|summarize|explain|compare`는 raw receipt만으로 semantic completion을 주장하지 않게
  막혀 있다. 이 안전 fence는 맞지만, source-grounded draft와 고정 eval이 없어서 실제 지식 작업에
  쓸 수 있는 다음 상태로 진행하지 못한다.

#### 목표·포함 범위·명시적 비범위

- 목표는 기존 `/작업` 경로를 `trigger → exact task contract → 실제 worker context → typed tool
  receipt → grounded draft → review/eval`로 관측 가능하게 만드는 것이다.
- 포함: 모든 text/voice/Control Page `task_executor` terminal의 content-free 계약 projection,
  source-grounded review/summarize/explain/compare draft와 fixed synthetic eval이다. text/voice는 현재
  turn envelope에만 record를 반환하고, 최근 history panel은 기존 FastAction owner가 있는 Fast Control에만
  제공한다. 여기서 만들지 않는 것은 **공개 task contract의** cross-surface store와 durable run history다.
  사용자가 요청한 대화문·최종 음성 전사·Minecraft 공동 기록은 public task record와 섞지 않고,
  P1-4의 별도 private archive가 principal·참여 시간·삭제 계보를 갖춰 소유한다.
- 비범위: 새 daemon·범용 agent framework·자유형 shell·임의 MCP 연결, long-context 전체 dump,
  production source code 자동 수정, active tool allowlist/permission/approval/evaluator 변경,
  자동 external send/deploy/delete, manager·사용자 음성/문체 복제나 impersonation이다.
- Voice는 동일 `task_executor` contract를 호출하는 ingress일 뿐 별도 권한이 아니다. 이메일·Discord
  전송 같은 외부 효과는 계속 draft-only이고 기존 explicit delivery authorization 없이는 실행하지 않는다.
- independent하지 않은 단계는 병렬화하지 않는다. production Qwen broker는 capacity-one을 유지하고,
  eval의 모델 row도 직렬 실행한다. 병렬화는 content-free deterministic validator 최대 4개에만 허용한다.
- 10KiB 초과 파일은 실제 수요가 확인될 때만 별도 bounded large-read grant로 재설계한다.
  이 계획은 기본 6-step 권한이나 context budget을 넓히지 않는다.

#### 선택한 해법과 버린 대안

- 선택: 실행 권위는 기존 `TurnScope` currentness, coarse `TaskGrant`, signed Host HMAC/boot/TTL/
  one-shot, sandbox·mutation authority, exact approval와 receipt verifier의 합성이다. 새 task contract는
  이 권위들의 content-free 관측 projection일 뿐 어느 단계도 승인하거나 확장하지 않는다.
- 선택: 기존 task ID와 `TaskLoopResult`/FastAction projection을 재사용한다. 새 AgentRun ID, observer
  callback, append-only journal, external anchor와 별도 UI framework는 만들지 않는다.
- 선택: semantic 결과는 `completed`를 흉내 내지 않고 `grounded_draft_ready`로 표시한다.
  exact evidence reference와 deterministic structural validation은 출처 연결을 증명하지만 자연어
  claim의 절대적 진실을 증명하지 않으므로 `semanticVerified:false`를 유지한다.
- 버림: process-local task history를 먼저 durable GitHub형 ledger로 일반화하면 principal·삭제 lineage·
  single-writer/CAS/anchor를 해결하기 전에 사용자 가치가 없는 기반부터 커진다. recovery journal도
  interrupted-work 안전 책임을 유지하며 activity log로 확장하지 않는다.

#### P1-3A — exact TaskWorkContract와 process-local inspection

1. `task_loop_runtime.py`에 별도 run ID 없이 기존 `task_id`에 결박되는
   `evelyn.task-work-contract.v1` frozen dataclass를 추가한다. 내부 계약은 route/source enum,
   allowlisted `skillOriginClass(internal|bundled|external)`만 사용하고 module/file origin 원문은
   버린다. current principal equality token과 `grantId`는 `repr=False` internal owner binding이며
   public projection이나 artifact에 내보내지 않는다.
2. instruction identity는 `build_task_worker_payload`의 source-owned system instruction version/digest와
   실제 `_worker_state`에 들어간 tool-guidance name 집합으로 고정한다. consumed context manifest는
   `goalPresent:true`, step 번호, observation count와 tool/code만 기록한다. route messages,
   `ContextPacket`, memory, file/web body는 worker가 소비하지 않으므로 기록·주입하지 않는다.
3. authority projection은 `TurnScope` currentness, `TaskGrant` tool/budget, Host receipt의
   attempted/executed/observed/verified/outcome/code, approval state를 관측한다. HMAC, boot secret,
   raw receipt/evidence/args는 복제하지 않으며 이 projection은 authorize/complete 권한이 없다.
4. `TaskLoopResult`에 contract와 exact `public_task_record()`를 붙인다. public status allowlist는
   현행 `completed|failed|blocked|uncertain|awaiting_approval|budget_exhausted|cancelled`와 P1-3B의
   `grounded_draft_ready`뿐이다. record는 status/code, step/model count와 step별 tool/flag/outcome/code,
   contract/eval version, `processLocal:true`, `durable:false`만 가진다.
5. `skills/task_loop/__init__.py`, `voice_route_execution.py`와
   `voice_execution_dependency_composition.py`는 현재 `SkillContext`의 principal owner를 내부 계약에
   전달하고 terminal public record를 `SkillResult.metadata["taskRecord"]`로 반환한다. Main/Voice turn
   envelope는 현재 turn의 이 content-free side channel만 전달하고 보관하거나 TTS로 읽지 않는다.
   Fast Control direct task는
   authenticated local principal을 사용한다. 다른 principal의 task/feedback/promotion에 이 token을
   재사용하는 기능은 P1-3에 없다.
6. `fast_action_runtime.py`와 `fast_control_api.py`는 terminal `TaskLoopResult`의 public record만 기존
   FastAction task에 붙인다. text/voice record를 이 coordinator로 복제하지 않는다. `docs/index.html`과
   기존 `evelyn-task-approval.js/.css`는 Fast Control 최신 4건의
   process-local record만 렌더링하고 module path, principal, goal/context/evidence, reply를 읽지 않는다.
   full diff는 기존 transient approval preview에만 남는다.
7. process restart 뒤 terminal history를 복원하거나 private task를 replay하지 않는다. 기존
   `FastActionRecoveryJournal`만 interrupted work를 outcome-unverified로 닫고 자동 재실행을 막는다.
   durable history가 실제 promotion 요구로 확인되기 전에는 journal/anchor/single-writer를 추가하지 않는다.

P1-3A 먼저 실패할 회귀는 raw summary/evidence/principal/module path가 public record로 나오는 경우,
route-level ContextPacket을 consumed로 표시하는 경우, 외부 caller가 반환 record를 바꿔 내부 task state를
변조하는 경우, 새 status가 기존 allowlist 검사를 우회하는 경우다. focused는 task contract/result,
task skill/route composition, FastAction integration, recovery 비회귀와 UI parse/privacy tests다.
broad는 canonical `python -m pytest -q` 기능 실패 0개다. 승인된 representative surface에서
Control Page/text 각 read-only task 3건의 schema·step code·raw leakage 0을 확인한다. Voice live는
P1-1에서 surface가 이미 승인된 경우에만 같은 3건을 추가한다.

#### P1-3B — source-grounded 지식 draft와 fixed eval

1. `task_loop_runtime.py`에 내부 `grounded_draft` terminal transition을 추가한다. candidate output은
   `evelyn.task-grounded-draft.v1` exact schema로 `kind(review|summarize|explain|compare)`, bounded
   section/claim, 각 claim의 `stepId/evidenceRef`만 가진다. evidenceRef는 현재 run의 verified read/diff/
   search receipt가 실제로 소유한 fragment에만 결박하며 fabricated/cross-run/stale reference는 거부한다.
2. `main_llm_runtime.py`의 registered-route status/terminal finalizer와 Fast Control/Voice의 기존
   task outcome adapter는 `grounded_draft_ready`를 success가 아닌 reviewable terminal로 동일하게
   처리한다. structural validation을 통과한 draft만 source label과 함께 표시한다. 명시적 링크
   요청일 때만 verified web receipt의 allowlisted `https` URL을 표시하고 redirect/credential/local/
   private URL은 제거한다. TTS에는 source body/URL을 읽지 않고 content-free 안내만 보낸다.
3. 모델 자유문이 claim truth나 실행 완료 권한이 되지 않는다. 모든 evidenceRef가 exact여도 결과는
   `grounded_draft_ready`, `semanticVerified:false`, `humanReviewRequired:true`다. behavioral mutation은
   독립 Host evaluator가 생기기 전까지 기존 `semanticVerified:false`와 자동 재시도 금지를 유지한다.
4. 새 `tools/task_agent_eval.py`와 `tests/tools/test_task_agent_eval.py`는 private data 없는 fixed 24-row
   corpus를 쓴다: grounded review/summarize/explain/compare 12, permission·prompt-injection·private
   leakage 8, timeout/cancel/restart/approval 4다. baseline/candidate는 source/model/evaluator/corpus,
   tool grant와 input case가 같고 비교 대상 instruction/guidance version·digest만 달라야 한다.
   temperature 0과 현행 timeout으로 한 row씩 격리 실행한다.
5. report에는 case opaque ID, binding, status/code, evidence coverage, unauthorized-effect/privacy flag,
   latency/context byte count와 aggregate만 atomic JSON으로 쓴다. prompt/output/source content는
   Git/docs/report에 넣지 않는다. 모델 row는 Qwen broker에서 직렬, deterministic validation만 최대 4개다.
6. gate는 schema parse 24/24, grounded 12건 evidence coverage 100%, fabricated/cross-run ref 0,
   unauthorized tool/mutation/send 0, private/raw leakage 0, timeout/error 0이다. candidate는 safety row
   전건 통과, expected predicate가 baseline 이상, latency/context p95가 baseline보다 10% 넘게 악화되지
   않아야 한다. 하나라도 실패하면 candidate를 promotion 입력으로 쓰지 않는다.

eval owner는 exact `evalRunId`, baseline/candidate contract digest와 suite version이다. row별 현행
6-step/120초 task deadline을 유지하고 전체 24-row run은 60분에서 중단한다. 취소·timeout은 현재
row와 admission을 회수하고 `incomplete` aggregate만 atomic 보존하며, successor row나 promotion을
자동 시작하지 않는다.

P1-3B focused는 task loop/main finalizer/url policy/eval tool tests와 기존 task completion false-green
회귀다. broad는 canonical suite 기능 실패 0개다. 대표 Control Page와 Voice에서 각 네 kind 1건씩
exact evidence 연결, 잘못된 ref 거부, 링크 opt-in, TTS raw body 비노출을 확인한다.

#### 전체 rollback·완료 조건·예상 diff

- P1-3A rollback은 contract/public record/panel wiring을 disable하고 기존 task loop/FastAction/recovery
  동작으로 돌아간다. P1-3B rollback은 grounded kind admission을 닫아 기존 fixed noncompleted outcome을
  유지한다. 저장 schema migration이나 durable artifact cleanup은 없다.
- 예상 diff는 `task_loop_runtime.py`, `skills/task_loop/__init__.py`, `voice_route_execution.py`,
  `voice_execution_dependency_composition.py`, `fast_action_runtime.py`, `fast_control_api.py`,
  `main_llm_runtime.py`, `voice_orchestration.py`, 기존 task-approval UI 3개, 새 eval tool/test 2개,
  `tests/core/test_task_loop_runtime.py`, `tests/core/test_task_route_orchestration.py`,
  `tests/core/test_ask_llm_once_runtime.py`, `tests/voice/test_voice_turn_orchestrator.py`,
  `tests/runtime/test_fast_action_runtime.py`, `tests/runtime/test_fast_task_loop_integration.py`,
  `tests/runtime/test_task_approval_ui_contract.py`, current-state/risk/decision/NOW/worklog/plan 문서다.
  새 runtime framework,
  dependency, DB migration, external anchor/MCP, model/timeout/default step budget diff는 0이다.
- 완료는 process-local contract projection과 24-row eval/grounded draft가 통과하고 대표 승인 surface에서
  raw leakage·unauthorized effect·false semantic completion이 0일 때만 선언한다. durable feedback/
  promotion/rollback은 P1-3 완료로 주장하지 않는다.
- trigger, 실제 consumed context, authority projection, output schema, eval corpus/gate, timeout·취소,
  live 비범위와 단계별 파일 범위를 위에서 고정했다. **미해결 설계 질문은 0개다.**

### P1-4. 운영 보안·30일 private archive·삭제/백업 내구성

상태: **[~] source/offline 구현 보존·live/production 우선순위 중단**. 2026-08-28 구현 승인은 이력으로
유효하지만, 2026-09-02 기존 Markdown 장기기억과 별도인 선택 기능을 현재 필수 작업으로 취급한 것은 오판이었다.
exact 30일 원문 기록·사용자별 열람/삭제가 명시적으로 다시 요구되지 않으면 BitLocker/provision/live를 재개하지 않는다.
이번 승인은 source/offline 구현·검증만 열며, 이 항목은 P1-3의 content-free task contract를 대신하거나
P0-4/P0-5, P1-1/P1-2의 선행 순서를 우회하지 않는다. 아래에서 `private archive`는 Git·`docs/`·
일반 runtime log가 아니라, 접근권한과 삭제 계보를 가진 **비공개 기준 기록 저장소**를 뜻한다.
Discord 열람은 server slash command의 invoker-only ephemeral 응답과 180초 삭제 시도로 확정했다.
deaf 상태는 참여 불가, D: 단독 장애는 10분 유예 뒤 새 기록 차단으로 확정했으며 미해결 설계 질문은 0개다.
선행 항목과 별도 live 승인 전에는 Discord/microphone/Minecraft/Docker 운영 runtime을 기동하거나
private archive를 production ON으로 바꾸지 않는다.

#### 문제·현행 근거·선택한 최소 구조

- 현재 `FastActionCoordinator` 기록은 process-local이고, memory DB는 사람별 참여 구간과
  message/reply lineage를 소유하지 않는다. `RoomSpeakerActivityStore`도 약 2.5초의 최근 화자 선택용
  임시 상태이므로 30일 열람권의 근거가 될 수 없다.
- 현재 Discord voice-state handler는 Evelyn 본인 외 구성원의 join/leave/mute 변화를 무시한다.
  따라서 “사용자가 실제 참여한 시간”을 사후 추측하지 않고 별도 interval event로 수집해야 한다.
- 현재 compose는 `bot_api`, Control Page, Discord가 같은 `runtime_artifacts`를 쓰기 가능하게 mount한다.
  이 상태에서 어느 process가 최종 writer인지 증명할 수 없으므로 private archive는 별도 root로 격리한다.
- 선택: 기존 `bot_api`를 archive·보존기간 정리·D: 백업·삭제·복구의 **유일 writer**로 둔다.
  `유일 writer`는 파일을 실제로 변경할 수 있는 process가 하나뿐이라는 뜻이다. 새 daemon이나 DB server,
  queue, dependency는 만들지 않고 Python 표준 `sqlite3`와 기존 API lifespan을 사용한다.
- Discord process는 파일을 직접 열지 않고 별도 목적 제한 HMAC 인증의 ingest API로 typed event만 보낸다.
  여기서 HMAC은 비밀키로 “허가된 발신자가 보낸 내용이며 중간에 바뀌지 않았다”를 확인하는 코드다.
  광범위한 internal-control token을 재사용하지 않는다. Control Page도 read/delete API만 호출한다.
- `bot_api` 시작 시 OS writer lease를 한 번 얻고, boot generation·단조 event sequence·idempotency key로
  중복/늦은 callback을 거부한다. `lease`는 다른 process가 동시에 writer가 되지 못하게 잡는 운영체제
  잠금이고, `generation`은 재시작 전후의 낡은 요청을 구분하는 세대 번호다.

#### 확정된 제품 정책과 기록 범위

- 기본은 `local_private`다. 로컬 소유자 한 명만 사용하고 제3자 Discord 전송·공동 기록은 없다.
  Discord mode를 명시적으로 켠 세션만 `discord_shared`가 되며, 다른 사용자에게 제공하는 범위는
  **대화와 Minecraft**뿐이다. mode는 capture 시점에 고정하며, 켰다고 과거 local 기록을 올리거나
  껐다고 이미 생긴 Discord 기록을 local-only로 재분류하지 않는다.
- `local_private`는 소유자 이외 사람의 음성을 “저장만 안 하는” 모드가 아니다. 제3자 화자로 판정된
  segment는 STT/LLM/memory 전에 버리고 raw buffer도 지운다. 화자를 소유자로 확인할 수 없는 공동 환경은
  local 입력 admission을 닫으며, “로컬 공동 모드”나 speaker-biometric을 이번 범위에 추가하지 않는다.
- Discord mode는 새 전역 toggle을 만들지 않고 기존 Discord ingress와 current voice-capture consent를
  `(local operator, guild, text/voice channel, boot generation, TTL)`에 결박한 `discord_shared` session으로
  해석한다. bot leave/restart/명시 OFF/TTL 만료 시 닫히며 stale session은 자동 복원하지 않는다.
- `discord_shared`에서는 로컬 마이크를 입력·참여·완료 조건에서 완전히 제외하고 Discord voice만 듣는다.
  실행 중이던 로컬 캡처도 정지한다. 음성 packet의 화자는 Discord gateway의 정확한 SSRC→user ID mapping으로
  확정하고, 그 member의 현재 `display_name`을 기록·화면의 화자명 snapshot으로 쓴다. user ID mapping이 아직
  없으면 잠깐 대기한 뒤 STT 전에 폐기하며, 최근 발화자나 채널의 유일한 사람으로 추정하지 않는다.
- Discord mode를 켤 때와 bot이 voice channel에 들어갈 때 보존 범위, 30일, raw audio 비저장,
  사용자별 열람 범위, `/기록열람`·`/기록삭제` 방법을 channel에 명시한다. 사용자는 1회용
  동의 action으로 참여를 시작하고 언제든 철회할 수 있다. 안내·동의를 확인할 수 없으면 그 사용자의
  audio를 STT하지 않고 전사·열람 interval도 만들지 않는다. 녹음/전사 중이라는 운영 상태도 계속 보인다.
- 저장하는 기준 기록은 다음뿐이다.
  1. Discord/local에서 확정된 사용자 text와 Evelyn의 text 답변.
  2. Discord voice에서 bot의 녹음 안내가 활성인 동안 확정된 화자별 final STT와 Evelyn 답변 text.
     Evelyn이 답하지 않은 final STT도 대화 기록에는 포함하되 partial STT와 원음은 포함하지 않는다.
  3. 정확한 voice 참여 가능 구간, mute/deaf 전이, guild/channel과 record/turn/reply 연결.
  4. Discord mode에서 요청된 Minecraft 명령, 허가·실행·검증 결과와 세계에 남은 effect receipt.
  5. feedback 원본, candidate/version의 상태와 P1-5의 비식별 독립 여부를 판정하는 lineage.
- 저장하지 않는 것은 raw PCM/Opus/audio, partial STT, TTS audio, 내부 prompt/추론, credential,
  tool의 private argument·파일 원문, 임의 debug dump다. `final STT`는 음성을 문자로 바꾸는 과정이
  끝나 더 이상 수정하지 않는 최종 전사이고, `lineage`는 어느 원본에서 어떤 파생물이 나왔는지를
  잇는 출처 관계다.
- 각 record는 무작위 record ID, mode/surface/type, UTC 시작·종료, opaque principal, guild/channel,
  parent/reply/task/action ID, private body, schema/generation을 가진다. body의 저장 중 암호화는 아래의
  검증된 C:/D: volume encryption 경계가 담당하고 화면 시간만 KST로 변환한다.
  Discord 표시 이름은 사람이 읽는 화자명일 뿐 권한 근거가 아니며, 내부 권한·소유권·삭제 대상은 안정적인
  Discord user ID와 별도 opaque mapping으로 판정한다.

#### 참여 시간과 사용자별 열람권

- 물리적으로 channel에 있었던 `presence interval`과 실제 열람권을 만드는 `eligible interval`을 따로
  기록한다. 둘 다 `(guild, voice channel, Discord user, [join, leave))`의 반열린 구간이며,
  `[join, leave)`는 들어온 시각은 포함하고 나간 시각은 포함하지 않는다는 뜻이다. presence는 진단용이고
  권한은 `presence ∧ 안내 동의 current ∧ mute 아님`인 eligible interval만 사용한다.
- 사용자 요구대로 `self_mute`, server `mute`, Stage `suppress`가 켜지는 순간 eligible interval을 닫고
  해제되는 순간 새 구간을 연다. 음소거 중 듣기가 가능해도 이 정책상 voice 참여자로 인정하지 않는다.
  channel presence는 별도로 남아 “나갔다”고 잘못 기록하지 않는다. gateway disconnect/restart처럼 상태를
  모르는 틈은 `unknown`으로 두고 열람권을 주지 않으며, 재접속 뒤 실제 channel state부터 새 구간을 연다.
- `self_deaf` 또는 server `deaf`도 참여 불가로 확정한다. 켜지는 순간 eligible interval을 닫고 해제 뒤
  안내 동의와 channel state가 current일 때만 새 구간을 연다.
- 음소거 중 본인이 text chat을 보내면 **그 chat record와 직접 연결된 Evelyn 답변·task·Minecraft
  결과만** 본인 기록으로 인정한다. 그 한 메시지가 음소거 전체 시간이나 같은 시각의 무관한 다른 사람
  voice 기록 열람권을 만들지는 않는다.
- 최신 사용자 결정에 따라 eligible interval은 열람의 필요조건일 뿐, 그 시간대 전체 기록 열람권이 아니다.
  일반 사용자는 **본인이 작성한 text·본인 final STT와 그 발화에 직접 연결된 Evelyn 답변·task·Minecraft
  결과만** 볼 수 있다. 같은 시간에 있던 타인의 발화·전사·답변, 참여 전·퇴장 후 기록, 타인의 DM/개인
  memory, 내부 prompt·tool secret·관리 기록은 볼 수 없다.
- Discord role, 닉네임, 서버 `Administrator` 권한은 기록 소유권이 아니다. 모든 요청은 실제
  interaction author의 Discord user ID로 다시 계산한다. gateway snapshot이나 최근 화자 cache에서
  참여 시간을 추정하지 않는다.
- 일반 사용자의 Discord 열람 명령은 `admin.control` 세션을 만들거나 열지 않는다. 호출마다 exact caller와
  본인 발화/reply linkage를 다시 계산하는 짧은 read handle만 쓰고, 지속 관리자 권한은 발급하지 않는다.
- 앞서 확정한 “로컬 관리자만 전체 기록 열람”은 아래의 별도 local Control Page `admin.control` 경로에만
  남는다. 관리자 본인이 Discord에서 `/기록열람`을 실행해도 일반 사용자 범위만 적용되며 전체 열람권으로
  승격하지 않는다.

#### 일반 사용자 열람·삭제 명령

- `/기록열람 [시작] [끝]`과 `/기록삭제 [시작] [끝]`은 global application command로 두되 `GUILD` context만
  등록·허용한다. `BOT_DM`과 다른 사람/GDM인 `PRIVATE_CHANNEL`은 등록과 handler 양쪽에서 거부한다.
  3초 안에 content-free
  ACK/defer하고 Discord interaction token의 15분 수명을 Evelyn 권한 session으로 재사용하지 않는다.
- 열람은 command를 실행한 현재 guild의 본인 발화·직접 연결 결과로 고정한다. 삭제는 기간을 생략하면
  사용자 요구대로 그 Discord user에게 귀속된 모든 guild·전체 기간을 뜻하며, apply 전 guild별 대상 수를
  ephemeral preview로 보여 주고 같은 caller의 1회용 확인을 받는다.
- 열람/삭제 command 원문, OTP, 열람 결과 message body는 다시 대화·memory·feedback archive에 넣지
  않는다. 삭제 요청 자체는 아래의 content-free tombstone만 남기므로 “전체 삭제” 직후 새 개인정보
  record가 생기는 순환을 만들지 않는다.
- 일반 사용자에게 OTP를 다시 보내는 것은 같은 Discord 계정을 두 번 확인할 뿐 별도 인증이 되지
  않으므로 사용하지 않는다. 현재 Gateway session에서 받은 exact interaction `user.id`를 owner로 고정하고
  설치 owner나 role을 caller로 대신 쓰지 않는다. 삭제는 대상 수·기간·파생 범위를 먼저 보여 준 뒤
  같은 caller와 preview generation/affected-set fingerprint에 묶인 60초짜리 1회용 확인을 한 번 더 받는다.
  대상이 바뀌면 409로 preview부터 다시 시작한다.
- 한 번에 한 page만 보내며 모든 page/component에서 caller ID, `GUILD` context, exact guild,
  query/snapshot generation을 다시 검증한다. component에는 짧은 opaque handle만 넣고 내용·권한 claim을
  넣지 않는다. invoker-only ephemeral flag로 응답하고 180초 뒤 유효한 interaction token으로 삭제하되,
  token은 장기 session/기록으로 보존하지 않아 restart 뒤 exact 180초 삭제를 보장하지 않는다. 결과를
  `removed|token_expired|not_found|not_controllable`로 구분한다. 사용자가 직접 닫거나 client가 restart되면
  180초보다 먼저 사라질 수 있으며, 복사·스크린숏·클라이언트 탈취와 Discord 사업자 내부 보존까지
  막았다고 주장하지 않는다.
- Discord의 **ephemeral**은 server interaction을 호출한 사람에게만 보이는 응답 형식이다. 명령은 server
  channel에서 실행하지만 응답 본문은 호출자 이외의 channel 사용자에게 표시하지 않고 일반 DM도 만들지 않는다.
- 이 명령은 Evelyn이 소유한 local/D: 기록을 지우는 기능이다. 사용자가 Discord에 직접 작성한 원본
  guild/DM message, 이미 다른 사람에게 재생된 음성, Discord 사업자의 내부 사본까지 삭제한다고 주장하지
  않는다. Evelyn이 작성한 DM/ephemeral 응답의 surface deletion 결과도 local purge와 별도 상태로 보고한다.

#### 삭제 계약과 30일 보존

- 정상 보존 상한은 각 record의 `endedAt`부터 30×24시간이다. 열람했다고 기간을 연장하지 않는다.
  새 scheduler는 만들지 않고 `bot_api` startup 및 매시간 bounded batch에서 만료된 가장 오래된 record부터
  순차 삭제한다. TTL 만료도 사용자 요청과 **같은** freeze→direct/derived→D:→검증 상태 machine을 쓰고
  tombstone reason만 `retention_expired`로 다르다. 장시간 꺼져 있었다면 startup catch-up을 끝내기 전 새
  archive read/write를 열지 않는다.
- 사용자 직접 삭제가 30일 정리보다 우선한다. 요청을 받은 즉시 해당 principal/generation을 freeze하고
  진행 중 STT/LLM/summary/memory/index/backup/outbound worker를 cancel 또는 drain한다. 모든 late commit은
  삭제 generation/currentness를 다시 검사해 stale이면 버린다. `freeze`는 삭제 중인 원본을 새 작업의
  입력으로 쓰지 못하게 닫는 상태이고, `drain`은 이미 시작한 작업이 안전하게 끝나거나 취소됐음을 확인하는
  절차다.
- 공동 기록에서 한 사용자가 삭제하면 다른 참여자의 독립 발언은 유지한다. 요청자 발언·text·final STT의
  body, 대상 기간의 참여/mute event와 신원 연결을 지우고 canonical row type 자체를 새 tombstone으로
  바꾼다. 최종 placeholder allowlist는 새 무작위 placeholder ID, minute-rounded UTC 시각, 대화 내 상대 순서,
  삭제 reason뿐이며 과거 parent/reply/task/action/principal ID는 제거한다. UI는 그 자리를 정확히
  **`사용자의 요청으로 삭제됨`**으로 렌더링한다. 일부 기간 삭제면 남은 기간에 필요한 principal mapping만
  유지하고, 기간 없는 전체 삭제면 그 사용자에게 귀속된 마지막 mapping도 제거한다.
  placeholder 자체에는 이름, Discord ID/message ID, 원문 hash를 넣지 않는다.
- 요청자의 내용을 인용·요약해 드러내는 Evelyn 답변/다른 row는 proven independent fragment만 남기고
  삭제 source를 다시 입력하지 않은 surviving-source rebuild만 허용한다. 나머지는
  `삭제된 사용자 기록에 의존하여 숨김`으로 격리/삭제한다. 독립임을 증명할 fragment lineage가 없으면
  유지 쪽으로 추정하지 않는다. 다른 사용자가 별도로 쓴 독립 원문은 유지하되 그 안의 요청자 직접 인용은
  exact span을 증명할 때만 지우며, 분리 불가능하면 해당 row를 격리하고 local purge 완료를 보류한다.
- index/search cache/embedding/summary/memory/TTS cache와 private feedback처럼 복원·추론 가능한 파생물을
  모두 제거하거나 삭제된 source 없이 재구축한다. `embedding`은 문장을 숫자 벡터로 바꾼 검색용 파생물,
  `cache`는 빠른 재사용을 위한 임시 사본, `derived`는 원본에서 계산·요약된 파생물이다.
- Minecraft의 이미 일어난 세계 변화는 기록 삭제로 되돌리지 않는다. 세계 안전에 필요한 최소 action/result
  receipt는 남길 수 있지만 요청자의 이름·ID·자연어 명령은 지우고 content-free event로 바꾼다.
- 기존 내용 HMAC/hash와 원본을 가리키는 무결성 row도 함께 제거한다. cleanup 중 tombstone에는 D:나 늦은
  callback에 삭제를 재적용할 최소 opaque record/dependent ID set, 삭제 generation/head도 임시로 둔다.
  모든 sink가 닫히면 그 target set을 제거하고 무작위 삭제 요청 ID, 접수/완료 시각, 삭제 범주,
  primary/D: 처리 상태만 가진 최종 content-free tombstone으로 compact하며 UI에는
  “사용자의 삭제 요청에 따라 삭제됨”으로 표시한다. `tombstone`은 원문 없이 삭제 사실과 재등장 금지만
  남기는 묘표다. 일반 삭제 증명에 이름·Discord ID를 보존하지 않으며, 특정 법률이 exact 항목의 별도
  보존을 요구할 때만 그 법적 근거·기간·격리 위치를 별도 결정한다.
- 이 프로젝트에서 사용자가 지정한 **법적·운영상 최소정보 정책**은 특정 법률의 존재를 새로 주장하는
  예외가 아니라 위 별도 결정 자체다. user/admin 삭제 시 30일이 지나지 않은 사건의 `owner name + 실제
  UTC 발생시각`만 admin-only `legal_minimal_events` table에 자동 투영하고 body·principal/Discord ID·
  record/event ID의 외부 projection·삭제 reason·hash는 넣지 않는다. 보존 종료는 원래 발생시각+30일이며
  같은 oldest-first durable audit→primary compaction→D: replica 검증으로 제거한다. retention 삭제와 이미
  30일이 지난 직접 삭제는 최소정보를 새로 만들지 않는다. 향후 특정 법률이 다른 항목·기간을 요구하면
  별도 사용자 결정과 migration 없이는 이 범위를 넓히지 않는다.
- 삭제는 `logical_deleted → local_cleanup_pending → local_fully_purged`로 진행한다. 첫 상태부터 모든
  query에서 즉시 숨기되, primary·D:·아래 열거 sink의 application-recoverable direct/derived copy가 0이고
  수동 검토/격리 row도 0일 때만 local 완료라고 알린다. Discord surface는 별도
  `removed|permission_denied|token_expired|not_controllable` receipt로 보고하며 local 완료와 합치지 않는다.
  `logical_deleted`는 우선 차단됨, `local_fully_purged`는 Evelyn이 소유·열거한 local 사본에서 재생 불가가
  검증됨이라는 뜻이다. 사용자 screenshot, Discord 내부 사본, 알 수 없는 외부 복사본까지 지웠다는 뜻은 아니다.
- 삭제 sink allowlist는 archive DB/WAL/SHM/temp/staging, `bot_memory` hot/daily/vault 원문,
  memory deletion journal·continuity/checkpoint/ingress journal, search/prompt/tool cache와 embedding,
  persona/cognitive/autonomy/open-question/feedback state, outbound retry payload, STT/TTS 작업 buffer·debug audio,
  D: replica·등록 export다. 각 owner가 동일 deletion generation의 purge receipt를 반환해야 하며 하나라도
  미완료면 `local_cleanup_pending`이다. archive enable 동안 `VOICE_DEBUG_SAVE_AUDIO=false`를 admission에서
  강제하고 기존 debug audio가 있으면 같은 삭제 대상에 넣는다.

#### 저장 위치·D: 백업·무결성

- 기준 원본 SQLite는 `C:\ProgramData\Evelyn\private-audit\conversation.sqlite3`, 백업은
  `D:\EvelynBackup\private-audit\conversation.sqlite3`로 고정한다. `docs/`, Git, 일반 log,
  `runtime_artifacts`에는 private body를 쓰지 않는다.
- Linux container인 `bot_api`가 Windows SID/volume/reparse/DACL/BitLocker를 직접 확인했다고 주장하지 않는다.
  UAC로 상승한 one-shot Windows host launcher가 drive letter가 아닌 volume identity, reparse 부재,
  NTFS owner/DACL, C:/D: BitLocker를 검사해 boot/paths/volume IDs에 묶인 짧은 HMAC attestation을 발급하고,
  `bot_api`는 exact mount source와 그 증거가 일치할 때만 archive를 연다. 현재 확인된 것은 C:와 D:가 서로
  다른 물리 disk라는 점뿐이며 encryption은 권한 부족으로 미검증이므로 preflight 실패 시 기능을 켜지 않는다.
- P1-4 production backup topology는 위 D:의 **최신 검증 replica 한 개**로 고정한다. SQLite online-backup
  API로 일관된 staging DB를 만들고 generation/hash를 검증한 뒤 교체한다. 서로 다른 disk의 두 파일을
  한 번에 atomic commit할 수 없으므로 primary commit → D: copy → verify 상태를 명시한다.
- 삭제 때 SQLite `secure_delete`, WAL checkpoint/truncate만 믿지 않는다. 살아 있는 row만 새 DB에 쓰고
  fsync→atomic replace한 뒤 이전 DB/WAL/SHM/temp/staging과 D: 이전 replica를 제거하며, 모든 열거 파일의
  raw-byte canary scan과 새 DB query negative recall을 통과시킨다. SSD wear-leveling까지 물리 법과학적
  소거를 보장하지 않으므로 BitLocker volume encryption과 application-recoverable 범위로 완료 의미를 제한한다.
- 백업 복원은 protected anchor의 `minimumRestorableGeneration`보다 오래된 DB를 무조건 거부하고 최신
  tombstone·삭제 cutover witness·generation을 먼저 대조한다. test에서는 의도적으로 만든 pre-delete fixture를
  restore하려 해 거부되는지 확인하며 production에 오래된 snapshot을 남겨 두지 않는다.
- Windows Volume Shadow Copy, 다른 backup agent,
  cloud sync, 수동 export가 private archive root를 추가 복제한다면 그 사본을 같은 삭제/reconcile 계약에
  등록하기 전 기능을 켜지 않는다. 등록되지 않은 숨은 backup이 있으면서 “모든 backup 삭제”를 주장하지 않는다.
- HMAC key는 repository와 DB/D: backup 밖의 OS-protected secret으로 두고 domain을 conversation,
  deletion, feedback-version으로 분리한다. 최신 generation/digest를 별도 protected anchor에 기록한다.
  `anchor`는 DB와 백업을 함께 과거판으로 되돌리는 공격을 알아내기 위해 저장 묶음 밖에 둔 최신 머리표다.
  삭제 시 이전 원문 hash와 연결된 head를 버리기 전에 content-free cutover witness와 최소 복원 generation을
  새 anchor에 확정하고, 그 뒤 tombstone chain/checkpoint로 전환한다.
- 이번 local anchor는 stale/accidental restore와 key 없는 외부 변조를 탐지하지만, DB·backup·anchor 쓰기와
  key까지 탈취한 악성 `bot_api`/host administrator를 막는 WORM은 아니다. 그 위협까지 막으려면 별도 credential의
  append-only remote/WORM anchor가 필요하며 개인용 local-first 범위에는 추가하지 않는다.

#### “기록 저장소 고장”의 정확한 뜻과 동작

`기록 저장소 고장`은 단순히 LLM·Discord·네트워크가 느린 경우가 아니라 아래 중 하나를 뜻한다.

| 상태 | 실제 예 | Evelyn의 동작 |
|---|---|---|
| `primary_write_rejected` | commit 전 C: 없음/가득 참/read-only, DACL 거부, SQLite begin 실패 | 검증된 snapshot read만 허용하고 새 text/voice/Minecraft처럼 기록을 만들어야 하는 기능은 차단한다. 기록 없는 우회 실행은 하지 않는다. |
| `commit_unknown` | SQLite commit/WAL 결과 불명, process crash/timeout 중 응답 유실 | 본문 read와 mutation을 모두 닫고 transaction ID/head/idempotency를 reconcile하기 전 성공·실패 어느 쪽도 단정하지 않는다. |
| `primary_unreadable` | SQLite corruption, schema/generation 불일치, 파일 I/O 오류 | 본문 read/write/delete/restore를 모두 fail-closed하고 content-free health만 보인다. |
| `writer_lease_lost` | 두 번째 writer 발견 또는 OS lock 상실 | unlocked read/write fallback을 금지하고 새 verified reader snapshot/lease를 얻기 전 본문을 닫는다. mutation은 503/retryable이다. |
| `anchor_unavailable` | protected anchor path/서비스 일시 접근 불가 또는 stale | 새 read/write를 닫고 local chain이 검증될 때 삭제 tombstone만 `pending_anchor`로 받을 수 있으나 완료라고 말하지 않는다. |
| `integrity_blocked` | HMAC key 없음, row/head/anchor mismatch, 과거 backup replay 의심 | unsigned fallback·자동 재서명·자동 backup 덮어쓰기를 금지하고 수동 복구 전 본문을 노출하지 않는다. |
| `authorization_key_failed` | admin-session/user-view-handle signing key 없음·손상 | 해당 관리자 session·개인 handle 발급과 기존 token 수용을 모두 중단한다. archive key 장애와 섞지 않는다. |
| `backup_pending` | D: 분리/가득 참/read-only 또는 copy I/O 실패 | 검증된 primary read와 최대 10분의 primary-only 기록을 degraded 표시로 허용한다. 시작시각을 영속화해 restart로 초기화하지 않고 10분 뒤 새 기록을 차단한다. |
| `backup_integrity_blocked` | D: snapshot hash/generation/volume identity 불일치 | grace를 주지 않고 D:를 격리하며 restore/export를 금지한다. primary가 독립 검증될 때 read만 허용한다. |
| `local_cleanup_pending` | exact 삭제 scope의 D: 또는 derived purge/검증 미완료 | 해당 범위는 어디서도 보여 주지 않고 “삭제 접수, 정리 대기”로 알리며 local 완료라고 말하지 않는다. |
| `derived_only_failed` | search index/cache만 손상 | stale 결과를 격리하고 검증된 기준 원본+tombstone에서 재구축한다. 기준 원본을 index로 덮지 않는다. |

`fail-closed`는 안전 조건을 증명하지 못하면 기능을 허용하지 않는 방식, `degraded`는 일부 안전한 기능만
명시적으로 남긴 저하 상태, `WAL`은 SQLite가 commit 전에 변경을 적는 write-ahead log다. 본문 노출은
`사용자 scope 유효 ∧ primary 무결성 정상 ∧ 삭제 chain 최신 ∧ tombstone filter 최신 ∧ reader lease 유효`일
때만 허용한다. `reader lease`는 sole writer가 확정한 exact immutable snapshot/generation을 읽는 동안만
유효한 단기 읽기 권리다. 여러 장애가 겹치면 integrity/unknown-commit/lease fault가 backup grace보다 항상
우선한다. 장애 복구는 의도적으로 만든 pre-delete fixture의 restore가 거부되고 현재 D: replica에서 삭제
문장·고유 구절·principal이 재등장하지 않는 negative-recall 검사를 포함한다.

#### 로컬 관리자 OTP와 “관리자 세션”

- `127.0.0.1` 접속 자체는 관리자 증명이 아니다. 전체 기록 진입은 UAC로 상승한 one-shot Windows host
  launcher가 현재 token의 SID와 Administrators/elevated 상태를 검사하고, DACL-protected host secret으로
  `(SID, host, boot, browser bootstrap nonce, expiry)`를 서명해 Control Page/`bot_api`에 넘긴 경우만 시작한다.
  Linux container는 SID를 직접 판정하지 않고 attestation의 purpose/TTL/replay와 exact mount를 검증한다.
- Discord out-of-band confirmation secret은 그 Windows 관리자 증명을 대체하지 않고, 미리 등록한 Discord
  계정도 현재 소유하고 있다는 두 번째 확인으로만 쓴다. code는 bot→등록된 1:1 DM으로 보내고 사용자는
  **로컬 Control Page에만** 입력한다. 같은 PC의 Discord client를 쓰면 독립 장치 MFA나 NIST AAL2를
  달성했다고 주장하지 않는다.
- OTP 표시 코드는 ASCII 영문 대문자 26자+소문자 26자+숫자 10자에서 뽑은 정확히 4자다.
  대소문자를 그대로 비교하고 공백·Unicode 유사문자·자동 대문자화는 허용하지 않는다.
  `secrets.choice`처럼 암호학적으로 안전하고 균등한 난수원으로 각 자리를 뽑는다. 가능한 값은
  `62^4 = 14,776,336`개뿐이므로 단독 password로는 쓰지 않는다.
- 내부 challenge ID/nonce는 128-bit 이상 무작위 값이고 Windows SID, host/boot generation,
  등록 Discord user ID, 요청 capability에 묶는다. 화면 코드는 60초, 최대 3회, 한 session에 1개,
  새 발급 시 이전 코드 폐기, 발급 3회/10분·10회/24시간 제한, one-use, restart/잠금/logout 시 challenge
  폐기다. 실패 횟수/rate-limit은 `(등록 Discord ID, Windows SID, host)`와 global scope로 무결성 보호해
  영속화하며 새 code나 process restart로 초기화하지 않는다. 원문 코드는 저장·log하지 않고 secret-keyed
  digest만 constant-time으로 비교한다. DM 실패 시 공개 channel fallback 없이 challenge를 폐기한다.
- 등록 Discord ID의 최초 등록·변경·복구는 UAC-elevated host launcher와 새 confirmation을 모두 요구하고,
  변경 즉시 기존 challenge/admin session을 전부 폐기한다. Discord role·nickname이나 server admin을
  등록 증거로 사용하지 않는다.
- **관리자 세션**은 OTP 성공 뒤 `bot_api`가 “이 로컬 browser는 잠시 admin.control 작업을 해도 된다”고
  기억하는 server-side 임시 권한이다. password나 기록 사본이 아니며 절대 5분/idle 2분, process restart,
  Windows lock/logout, 명시 logout 중 먼저 온 시점에 끝난다. host launcher가 고정·신뢰한 loopback HTTPS만
  쓰고 browser에는 로그인 직후 교체한 128-bit 이상 `__Host-` opaque cookie를 `Secure; HttpOnly;
  SameSite=Strict; Path=/`로 준다. CSRF·host/origin을 검증하고 server에는 keyed digest만 둔다. 다른 사용자
  기록 삭제·P1-5 promotion은 같은 session이어도 exact 대상 preview와 새 one-use step-up 확인을 요구한다.
- `admin.control` session과 일반 사용자의 per-command `memory.user_view` handle은 signing domain, token schema,
  verifier, endpoint를 분리해 상호 수용하지 않는다. 개인 view handle은 정확한 Discord user/guild/본인
  발화·직접 reply/query generation에만 결박되고 절대로 전체 기록을 볼 수 없다. 같은 사람이 local admin이면서
  record owner여도 두 권한은 합치지 않는다.

#### 단계별 구현 범위·검증·rollback

1. `conversation_archive.py` 하나에 schema/store/retention/access/redaction/reconcile을 먼저 모으고,
   `fast_control_api.py` lifespan에 sole-owner/lock/API를 배선한다. 실제 요구가 생기기 전 파일을 더 쪼개지 않는다.
2. `discord_app_composition_runtime.py`에서 member voice-state transition,
   `discord_text_turn.py`에서 확정 text/reply,
   `voice_member_audio_pipeline_runtime.py`에서 final STT만 typed event로 만든다. 현재 transient speaker
   activity를 권한 DB로 재사용하지 않는다.
3. `discord_command_handlers.py`에 admin predicate와 분리된 exact-self global application command를 추가하고,
   `GUILD+ephemeral`만 register/sync/handler에서 허용하며 `BOT_DM|PRIVATE_CHANNEL`을 거부한다.
   `discord_runtime_status.py`의 shared status file에는 participant ID를 쓰지 않고, private participant
   transition/heartbeat는 목적 제한 HMAC API로 `bot_api`에 직접 보낸다.
4. Minecraft는 command 접수만 기록하지 않고 `mindcraft_service.py`의 verified world-effect projector가
   최종 action/result typed event를 `bot_api`에 보낸다. `memory.py`, `memory_deletion_journal.py`,
   `memory_deletion_outbound.py`, `cross_surface_continuity.py`, `cognitive_state_runtime.py`,
   `voice_debug_audio.py`에는 deletion generation/purge receipt와 late-commit fence만 추가하고 별도 archive를
   만들지 않는다.
5. 새 one-shot `tools/evelyn_private_archive_host.ps1`가 UAC/SID, volume/reparse/DACL/BitLocker preflight,
   host attestation과 admin browser bootstrap을 소유한다. `control_page_http.py`, `control_page_server.py`,
   `docs/index.html`과 최소 archive UI asset은 loopback HTTPS, admin session, read/delete preview만 배선한다.
   UI source는 `docs/`에 있을 수 있지만 private body/session/log는 절대 파일로 쓰지 않는다.
6. `docker-compose.fast-control.yml`에서 host source C:/D:/anchor를 각각
   `/run/evelyn-private-audit/primary|backup|anchor`로, key/host-attestation은 별도 `/run/secrets/...`로 mount한다.
   private primary/D:/anchor RW는 `bot_api`에만 주고 Control Page/Discord에는 주지 않는다. 기존 unrelated
   `runtime_artifacts` mount를 이 작업에서 정리하지 않는다.
7. focused tests는 archive schema/access/30일 prune, mute/deaf/chat/gateway gap,
   selected Discord surface exact-owner/preview/replay,
   shared partial deletion·인용 redaction·derived cascade, D: outage/reconcile/restore, OTP brute-force/rate-limit/
   audience confusion, lease/crash/anchor rollback을 먼저 실패시킨다. 새
   `tests/runtime/test_conversation_archive.py`와 기존
   `tests/discord_io/test_discord_app_composition_runtime.py`,
   `tests/discord_io/test_discord_text_turn.py`,
   `tests/discord_io/test_discord_command_handlers.py`,
   `tests/voice/test_voice_member_audio_pipeline_runtime.py`,
   `tests/runtime/test_discord_runtime_status.py`, `tests/runtime/test_docker_compose_contract.py`,
   `tests/runtime/test_memory_deletion_api.py`,
   `tests/core/test_control_page_http.py`를 확장하고 새 host-launcher contract test를 둔다.
   broad는 canonical pytest 기능 실패 0개다.
8. 별도 승인된 test guild/fake transcript와 disposable Minecraft world에서만 live 검증한다. 실제 private
   원문을 test report/docs/Git에 남기지 않고, Discord surface 삭제 성공과 로컬/D: 완전 삭제를 서로 다른
   결과로 보고한다.
- rollout은 기본 OFF → local test principal → Discord test guild 순서다. rollback은 새 capture/admission을
  닫고 기존 기능으로 돌아가되 이미 수집한 private DB를 임의 삭제하지 않고 read/delete 전용으로 격리한다.
  schema/data cleanup은 별도 사용자 승인과 verified backup deletion을 요구한다.
- 기존 continuity/memory deletion HMAC key/external anchor의 repository 밖 배치, Codex action route의
  tool-registry/secret-canary gate, Qwen-ASR/Transformers/Mineflayer 보안 release 호환 smoke, Discord/Minecraft
  live Runtime Health privacy 검사는 이 단계에서도 유지한다. 강제 dependency upgrade는 하지 않는다.

완료 조건: sole writer/mount가 증명되고, 30일 oldest-first prune과 기간 없는 전체 삭제, 참여 interval·
capture 동의·mute/deaf/chat 예외, Discord self-scope, local admin OTP, raw-audio OFF, shared partial deletion,
primary+D: local negative recall,
restart/rollback/forged/replay 거부가 source/offline tests와 별도 승인 live test에서 통과해야 한다.
public health에는 원문·경로·credential이 0이고, 미검증 action route와 integrity fault는 계속 fail-closed다.

#### 동결된 Discord 열람 surface

- `server slash command + invoker-only ephemeral + 180초 삭제 시도` 하나만 구현 대상으로 둔다.
- 일반 DM 열람·DM 자동삭제 fallback은 만들지 않는다. guild/public 응답이나 다른 사람/GDM도 허용하지 않는다.
- `self_deaf`/server `deaf` 참여 불가와 D: 장애 10분 유예 후 새 기록 차단도 사용자 결정으로 동결했다.
- trigger/context, 본인 발화 scope, 180초·restart 한계, 삭제·백업·OTP·failure·rollback·검증 범위를 모두
  위에서 고정했으며 **미해결 설계 질문은 0개다.**

#### 2026-08-28 source/offline 구현 체크포인트

- 기본 OFF인 P1-4 source 기반을 구현했다. `bot_api` 단독 SQLite writer, C: 기준 원본·D: 검증 replica·
  외부 anchor, schema/generation/HMAC/OS lease, 30일 oldest-first retention, 사용자·관리자 삭제 preview와
  content-free tombstone, Discord self-only ephemeral 열람·삭제, 별도 8800 loopback HTTPS 관리자 화면,
  UAC host attestation·Discord DM OTP·짧은 관리자 session을 배선했다.
- Discord text/final STT/Evelyn reply와 voice presence·eligible·mute/deaf/unknown transition, local text·답변,
  Minecraft command→grant→verified effect/result에 typed lineage를 연결했다. 일반 사용자는 자기 발화와 직접
  파생 결과만 보고, 관리자는 전용 local session에서만 전체 record·참여·voice transition·법정 최소 정보를
  bounded keyset page로 읽는다. 브라우저 cursor는 관리자 session·종류·archive generation·180초 TTL에
  결박된 64자 opaque handle이며 DB cursor와 내부 event ID를 노출하지 않는다.
- 삭제 완료는 17개 필수 sink 전부의 실제 purge와 fresh negative recall이 증명될 때만 승격한다. 17개
  logical owner route, process-local prompt/tool cache의 exact target metadata, memory/cognitive/ingress/search/
  STT/TTS writer·task fence를 연결했다. `bot_api`와 Discord/local-only process는 content-free work/receipt만
  교환하고, 동일 request/generation/scope의 remote receipt가 모두 확인되기 전 memory bundle writer fence와
  `local_cleanup_pending`을 풀지 않는다. attribution이 없는 legacy/global cache나 불완전 lineage는 삭제 성공을
  추측하지 않고 `manual_review`에 남긴다. 완료된 exact process lineage는 process 종료까지 retired 상태다.
- 수정 후 변경 영향 전체는 `1061 passed, 1 skipped, 203 subtests passed`였다. canonical 1차 실행은
  `4969 passed, 23 skipped, 1502 subtests passed, 8 failed`에서 구조·호환 회귀 8개를 찾았고, 수정 뒤 그 실패와
  인접 경로 재검증 `58 passed, 6 subtests passed`가 통과했다. 변경·신규 Python 전체 compile, JavaScript·
  PowerShell 구문, Compose config, diff check도 통과했다. 같은 5분짜리 canonical clean run은 반복하지 않았고
  Discord gateway, microphone, Minecraft, Docker service와 실제 C:/D: 운영 volume은 시작하지 않았다.
- 따라서 상태는 `[~]`를 유지한다. Discord mode는 로컬 마이크를 물리·논리 입력에서 제외하고 gateway가
  정확히 연결한 Discord user ID만 화자 권한 근거로 쓰며, 현재 `display_name`은 표시용 snapshot으로 쓴다.
  mapping이 없는 single-member/current-speaker 추정 경로도 제거했다. local-private microphone은 archive ON에서
  계속 fail-closed하지만 Discord mode의 완료 gate가 아니다. 남은 완료 gate는 별도 승인 live test guild에서
  gateway mapping·ephemeral과 장치/복제 장애를 검증하는 것이다. 이 P1-4 checkpoint 뒤 사용자가 P1-5 전체
  구현을 승인해 source/offline 구현·검증은 아래 P1-5 상태로 이어졌다.

#### 이 설계에서 쓰는 용어

| 용어 | 이 계획에서의 정확한 뜻 |
|---|---|
| 기준 원본(canonical/primary) | 충돌할 때 최종 판단 기준이 되는 C:의 단 하나의 원본 DB. |
| replica/snapshot/backup | 원본을 복구하기 위한 D: 검증 사본. production에는 최신 replica 한 개만 둔다. |
| record/archive | 한 대화·전사·참여 구간·Minecraft 결과 단위와 그 비공개 모음. 일반 log와 다르다. |
| principal/opaque ID | 권한 판정 대상 계정과, 이름 대신 내부에서 쓰는 의미 없는 무작위 식별자. |
| scope/audience/capability | 각각 허용 범위, token을 받아야 할 기능, 그 안에서 가능한 exact 동작. |
| presence/eligible interval | voice channel에 있었던 시간과, 동의·비음소거 조건까지 충족해 열람권이 생긴 시간. |
| final/partial STT | 확정된 음성 전사와 아직 바뀔 수 있는 중간 전사. 전자만 저장한다. |
| lineage/derived | 원본→답변·요약·memory의 출처 연결과, 원본에서 생긴 파생물. |
| hash/digest/HMAC | 내용의 짧은 지문과, 비밀키까지 사용해 발신·변조 여부를 확인하는 지문. |
| anchor/cutover witness | DB 밖의 최신 머리표와, 삭제 전 chain에서 삭제 후 chain으로 정당하게 넘어갔다는 내용 없는 증거. |
| tombstone | 원문 대신 삭제 사실·재등장 금지만 남긴 묘표. |
| generation/currentness | 재시작·수정 세대 번호와 요청이 아직 최신 세대를 대상으로 하는지의 판정. |
| lease | 동시에 다른 writer/reader가 잘못된 세대를 쓰거나 읽지 못하게 하는 단기 잠금/권리. |
| idempotency/reconcile | 같은 요청 재전송이 한 번만 반영되게 하는 성질과, crash 뒤 실제 반영 여부를 대조하는 절차. |
| TTL | 생성 뒤 유효한 최대 시간. 만료되면 다시 사용할 수 없다. |
| redact/quarantine/revoke/purge | 부분 가림, 격리, 권한·후보 취소, 원본과 파생 사본의 검증된 제거. |
| fail-closed/degraded | 안전을 증명하지 못하면 닫는 상태와, 안전한 일부 기능만 남긴 저하 상태. |
| OTP/OOB/challenge/nonce/admin session | 1회 코드, 별도 전달 경로, 인증 과제, 재사용 방지 난수, local admin 확인 뒤의 짧은 임시 권한. 일반 사용자 열람에는 admin session을 만들지 않는다. |
| user-view handle | Discord 명령 한 번과 해당 page에만 유효하며 본인 발화·직접 답변 범위만 읽는 짧은 권한표. |
| interaction/Gateway/ACK/defer | Discord 명령 event, bot의 실시간 연결, 3초 내 접수 응답/처리 연기 응답. |
| ephemeral/자동삭제 DM | 전자는 guild interaction에서 호출자만 보는 선택된 Discord 형식, 후자는 이번 설계에서 버린 평범한 DM의 best-effort 삭제 방식. |
| DACL/BitLocker/reparse point | Windows 파일 접근 목록, volume 전체 암호화, 다른 경로로 우회 연결되는 filesystem 지점. |
| negative recall | 삭제한 문장·ID·고유 구절을 모든 열거 저장소에서 다시 검색해 0건임을 확인하는 검사. |
| promotion/canary/CAS/rollback | 개선 후보 승격, 제한 시험, 예상 version일 때만 교체, 검증된 이전 version으로 복귀. |

### P1-5. 사람 교정 기반 feedback candidate와 안전한 version promotion

상태: **[~] 승인된 source/offline 구현·검증 완료, live gate 대기**. P1-3의 fixed
eval/TaskWorkContract와 P1-4의 principal/key/external-anchor/deletion-current 경계를 재사용해
`bot_api` sole writer, local Control Page `admin.control`, 동일 C:/D: 삭제 계약으로 구현했다. exact
correction→독립 guidance→fixed eval→action-bound OTP approval→서버 소유 10건 canary→CAS activation과
failure/rollback/revoke가 전체 회귀를 통과했다. 실제 Discord feedback, real-Qwen eval과 local canary는
live 미검증이므로 `[x]`가 아니다.

#### 2026-08-28 Discord feedback live 사전검증

- 승인 범위에서 기존 DPAPI credential을 메모리에서만 사용해 Discord REST를 read-only로 확인했다. bot 인증과
  guild members/message content/presence intent는 유효했다. bot이 참여한 2개 guild와 global registry 모두에서
  `기록열람|기록삭제|기록동의|기록철회|피드백제출`은 0개였다.
- source는 다섯 command를 `bot.tree.add_command()`로 process-local 등록하지만 Discord API publisher/sync가 없다.
  global registry에는 다른 기존 command 51개가 있으므로 단순 global `tree.sync()`는 삭제 위험이 있어 금지한다.
  exact test guild에만 다섯 command를 게시하고 exact scope만 회수하며 기존 global/guild command가 그대로인지
  전후 대조하는 sync/clear 진입점을 먼저 구현·검증해야 한다.
- Docker engine과 Evelyn listener는 OFF였고 archive directory/key/TLS/host attestation은 준비되지 않았다.
  기존 host launcher는 preflight/attestation만 하며 provisioner가 아니다. 기본 container launcher는 현재
  142개 dirty source entry를 revision gate로 거부하므로 current-source snapshot/image provenance 절차도 필요하다.
- local bridge process는 없고 마지막 content-free snapshot은 mic disabled/capture stopped였지만 오래됐으며,
  durable capture consent는 `revoking`이고 owner heartbeat도 stale이었다. Discord-only input gate는 새 runtime의
  exact mic-OFF ACK와 consent inactive reconcile을 요구한다. 오래된 OFF snapshot만으로 통과시키지 않는다.
- 따라서 same-user success, wrong user/channel, stale session과 delivery failure의 실제 ephemeral interaction은
  시작하지 않았다. Discord command/permission과 archive data는 변경하지 않았으며 live 상태는 `[~]`를 유지한다.
  다음 시도는 위 prerequisite를 source/offline 검증한 뒤 content-free workflow count delta와 ephemeral outcome만
  증거로 남긴다. transcript, correction, guild/user ID, credential과 screenshot은 문서에 남기지 않는다.

#### 2026-08-28 exact guild command publish/restore 구현·live 체크포인트

- 위 사전검증의 publisher 부재는 해결했다. 지정 guild에만 다섯 command를 임시 게시하고 exact returned
  command ID/shape를 private v2 ownership ledger로 소유해 production clear·fallback·restart recovery가 자기
  명령만 회수한다. global sync, bulk overwrite, 이름 기반 삭제는 사용하지 않으며 global과 모든 other guild
  registry를 전후 canonical 비교한다.
- 대상 서버 이름은 bounded strict UTF-8 stdin으로만 받고 full membership을 ordinal exact-unique로 해석한다.
  stale ledger target 불일치에서는 mutation 전에 중단하며 validate child가 publish 직전 same API session에서
  exact name과 numeric target의 동일성을 다시 확인한다. 이름·ID·credential·command body는 공개 출력과 문서에
  남기지 않는다.
- 승인된 live run은 대상/global/다른 guild `0/51/0`에서 대상 command 5개 게시를 확인한 뒤 exact-ID로 모두
  회수했다. 결과는 published/restored verified, recovery 불필요였고 독립 사후 read도 대상 managed/전체 `0/0`,
  global `51`, 다른 guild `0`, 보호 run directory `0`이었다.
- 최종 canonical은 `5153 passed, 18 skipped`, 실패 0건이다. 기존 gateway·Qwen 테스트의 50~100ms localhost
  wall-clock flake 2건은 제품 timeout을 바꾸지 않고 lane 재획득 event와 controlled clock 검증으로 분리했다.
- command registry gate만 live 통과했다. 2026-09-01 archive test provisioner, exact current-source image provenance와
  fresh local-mic OFF reconcile은 source/offline focused·canonical을 통과했다. 실제 host provision/build/mic ACK와
  Gateway same-user/wrong-user/stale-session/delivery-failure/ephemeral 180초 interaction은 여전히 남아 있으므로
  P1-4/P1-5 상태는 `[~]`다.

#### 2026-09-02 host provision live NO-GO

- 실제 C:/D:는 fixed NTFS·healthy·분리 disk지만 둘 다 BitLocker OFF였다. provisioner는 root/owner marker/
  key·TLS 생성 0으로 fail-close했고 Docker·Gateway·production archive는 시작하거나 변경하지 않았다.
- cleanup 불확실성에서 command recovery ledger를 보존하도록 launcher를 `6df290c`에서 수정했고 회귀
  `10 passed`를 통과했다. volume 암호화·recovery는 별도 사용자 결정이며 자동으로 켜지 않는다.
- 현재 launcher의 operator PASS는 시나리오별 workflow delta와 ephemeral 삭제 결과를 자동 증명하지 않는다.
  volume gate 해결 뒤에도 same/wrong/stale/delivery/ephemeral을 각각 확인하기 전 완료 처리하지 않는다.

#### 적용할 개념과 고정 경계

- 영상의 router/fixer와 self-healing은 `사람의 exact correction → scoped guidance candidate →
  fixed eval → exact approval → isolated canary → active version`으로만 해석한다. production source,
  tool grant, approval policy, evaluator와 safety/system instruction은 candidate 대상이 아니다.
- feedback은 exact task ID만으로 받지 않는다. authenticated `issuerPrincipalRef`, task owner principal,
  surface/session currentness와 feedback nonce를 함께 결박한다. 다른 Discord 사용자·room·session의
  task에 feedback을 달 수 없다. 첫 버전에서 actionable `correct` candidate 생성과 global
  `task_executor` guidance의 승인·활성화는 authenticated privileged local Control Page operator만
  할 수 있다. Discord/voice feedback은 same-principal 확인 뒤 review-only scoped signal로만 남기며
  runtime guidance candidate를 만들거나 승인·활성화하지 못한다.
- category는 `answer_quality|context_selection|task_routing|tone_identity|tool_failure|
  permission_safety` allowlist다. `tone_identity`는 기존 identity review queue로만 보내고,
  `permission_safety`와 evaluator/tool/approval/source 변경 요구는 `human_engineering_required`로
  끝낸다. agent가 category나 owner를 승격하지 못한다.
- `correct`는 사람이 직접 쓴 bounded correction을 candidate guidance로 사용하며 Evelyn/Qwen이
  재작성하거나 source patch를 만들지 않는다. guidance는
  `build_task_worker_payload`의 safety/system·TaskGrant·tool/approval/output verifier 뒤에 놓이는
  비권위 planner input이다.
- feedback에서 바로 만든 것은 `source_bound candidate`다. 이는 특정 사용자의 원문·교정과 연결된
  임시 개선 후보라는 뜻이며 active version이 될 수 없다. local operator가 source-specific 사실·이름·
  인용·말투·Discord/task ID·원문/embedding/hash를 모두 제거해 일반 규칙으로 다시 작성한 뒤에만
  `independent guidance` 심사를 시작한다. 모델이 혼자 “익명화됨”을 선언하거나 자동 분리하지 않는다.
- baseline/candidate eval은 source/model/evaluator/corpus/tool grant/input case가 같고 guidance
  version/digest만 달라야 한다. P1-3의 24-row gate를 전부 통과하지 못하면 approval preview를
  만들지 않는다.

#### 구현 상태·privacy·canary·rollback

- 예정 정상 상태는 `captured → owner_verified → routed → source_bound_candidate → generalized →
  privacy_reviewed → independent_candidate → eval_passed → awaiting_approval → approval_granted →
  canary_running → canary_passed → active`다. `generalized`는 local operator가 사용자 특이 내용을 제거해
  새 일반 규칙을 쓴 상태, `privacy_reviewed`는 이름/ID/인용뿐 아니라 의미적 바꿔쓰기·희귀 사실·말투·
  추론 가능성까지 사람이 검토하고 privacy fixture를 통과한 상태다. active parent는
  canary 동안 그대로 유지하고, exact local-operator read-only/grounded task 10건만 candidate pointer로
  실행한다. 그래서 canary 실패는 active rollback이 아니라 candidate 폐기이며 자동 retry하지 않는다.
- 원본 삭제나 30일 만료는 어느 pre-active 상태에서도 `source_deleted → purge_pending → revoked` terminal
  branch로 간다. 모든 eval/approval/canary callback은 deletion generation을 재확인하고 이 branch를 다시
  정상 상태로 전진시키지 못한다. 이미 독립 active가 된 version은 source 삭제 branch와 분리한다.
- activation은 candidate/eval/owner/parent generation에 묶인 one-use approval 뒤 canary 10/10,
  unauthorized effect/privacy/structural failure 0, ledger integrity current일 때만 atomic CAS로 수행한다.
  promotion 가능한 version은 반드시 위의 independent guidance이며, immutable base와 이전 두 verified
  independent active version을 보존한다. `promotion`은 검증된 후보를 실제 기본 규칙으로 승격하는 것,
  `canary`는 전체 적용 전에 제한된 10건에서 시험하는 것, `CAS`는 예상한 이전 version이 그대로일 때만
  pointer를 한 번 바꾸는 원자적 비교-교환이다. activation 뒤 새 고정 실패는 `activeVersionId`, guidance digest,
  task ID, contract/evaluator
  version, fixed failure code, current principal과 ledger integrity에 결박된 exact failure receipt가 있을
  때만 rollback을 한 번 허용한다. generic tool/network failure는 rollback 근거가 아니다.
- rollback target은 exact parent로 고정하지 않는다. evaluator/authority contract가 current이고
  privacy-independent proof가 current인 generation 중 가장 최신 verified version을 atomic CAS로 고른다.
  그런 version이 없으면 immutable base로 돌아가며, base도 current contract를 만족하지 못하면 candidate
  admission과 promoted guidance 적용을 모두 fail-closed한다.
  rollback 뒤 자동 재승격·재시도는 없다.
- 이전 version이 뒤늦게 source-dependent로 판정되면 그 version을 parent/입력으로 삼은 모든 descendant와
  rollback 후보를 primary와 D: ancestry 전체에서 연쇄 quarantine한다. 살아 있는 independent ancestor를
  다시 24-row eval한 뒤에만 rollback 대상으로 복귀시킨다.
- durable store는 P1-4에서 정한 repository 밖 key와 domain-separated external anchor를 사용한다.
  single writer, OS lock, boot generation, monotonic event sequence, CAS head, prune checkpoint와 late
  callback fence를 모두 요구한다. `atomic_json_write`만으로 concurrent append 성공을 주장하지 않는다.
- private feedback은 raw original task/reply/transcript를 복제하지 않지만 사용자가 실제 입력한 bounded
  correction body와 `feedbackId`, opaque principal, source task ID, correction revision, candidate lineage,
  deletion-current generation을 private archive에 가진다. correction/삭제가 current가 아니면 source-bound
  eval·일반화 심사·approval을 거부한다.
- 사용자가 원본 feedback 삭제를 요청하면 원본 correction, principal/source mapping, source-bound candidate,
  eval input, private lineage, C:와 D: 사본을 삭제하고 P1-4의 tombstone만 남긴다. 아직 source-bound인 후보는
  revoke한다. 반면 삭제 전에 local operator가 독립 규칙으로 다시 작성하고 별도 24-row eval·approval·canary를
  통과한 active version은 취소하거나 rollback하지 않는다. 그 version은 사용자 원본의 파생 사본이 아니라
  독립된 operator-authored rule과 자체 eval/approval receipt만 가진다.
- active version 안에 사용자 이름·ID·인용·전사·source hash/embedding·사용자 특이 사실이 남았거나
  독립성을 증명하는 심사 receipt가 없으면 “개선 버전 유지” 예외를 적용하지 않는다. 즉 원본 삭제 후에도
  남는 것은 일반화된 규칙뿐이며, 삭제된 사용자 내용을 복원하거나 추론할 수 있는 version은 rollback/revoke한다.
- independent proof는 source ID/hash를 보존하지 않는 content-free operator attestation, privacy-eval version/
  결과와 guidance digest만 가진다. feedback 삭제 cascade는 source-bound eval output/judge rationale,
  approval preview/browser cache, canary trace, version diff/package, failure/rollback receipt와 pending queue도
  검사해 사용자 원문·고유 특징이 있으면 지운다. independent version의 자체 synthetic eval/approval/canary
  receipt만 source-free임을 다시 검증한 뒤 유지한다.
- public UI는 principal, correction/guidance 원문, path/origin, context byte count를 노출하지 않는다.
  full candidate guidance는 authenticated local approval preview에서만 transient하게 보여 준다.

#### owner·삭제·검증 gate

- issuer proof는 P1-4의 Windows-authenticated local Control Page `admin.control` session과 action-bound
  one-use step-up이며 Discord OTP나 Discord administrator role만으로는 발급하지 않는다.
- correction/delete/version journal과 primary/D: reconcile은 P1-4의 `bot_api`가 single-writer로 소유하고
  conversation/deletion/feedback-version HMAC domain을 서로 분리한다. feedback retention은 source record와
  같은 30일 상한·기간 없는 전체 삭제를 따르며 TTL도 같은 deletion branch/cascade를 탄다. independent
  version에는 새 30일짜리 사용자 원본 링크를 복제하지 않는다.
- 구현 범위는 P1-4의 `conversation_archive.py`/`fast_control_api.py`/Control Page approval UI,
  `self_model.py`, `identity_review.py`, `task_loop_runtime.py`의 guidance payload와 별도 feedback/version focused
  tests다. Discord/voice는 review-only capture까지만 배선하고 promotion endpoint를 갖지 않는다.
- 먼저 실패할 회귀는 source-bound candidate activation, 삭제 뒤 feedback/source hash 재등장,
  independent version의 사용자 식별·인용, Discord OTP의 promotion 권한 승격, stale eval/approval replay다.
  privacy fixture는 exact name/ID/quote/unique phrase/embedding negative recall을 primary, D:, cache, restored
  snapshot에서 검사한다.

P1-4의 archive/access/deletion 경계를 구현·검증하기 전에는 feedback 원문 저장, candidate 생성, canary 또는
active pointer 변경을 구현·실행하지 않는다. 완료 조건은 P1-3 eval 전건, same-principal/
local-operator authority, source-bound 삭제 cascade와 independent-version 존속, canary 10/10,
forged/stale/replay 거부, version-bound failure receipt와 newest verified rollback 선택이 source/offline 및
별도 승인된 read-only live session에서 모두 통과하는 것이다.

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
- [Discord Interactions — context, caller, ephemeral, 3초/15분 계약](https://docs.discord.com/developers/interactions/receiving-and-responding)
- [Discord user-installable app — BOT_DM과 guild ephemeral 차이](https://docs.discord.com/developers/tutorials/developing-a-user-installable-app)
- [NIST SP 800-63B — out-of-band secret, rate limit, session](https://pages.nist.gov/800-63-4/sp800-63b.html)
- [개인정보 보호법 제21조 — 불필요 개인정보 파기·복구 방지·법정 보존 분리](https://www.law.go.kr/lsLinkCommonInfo.do?lsJoLnkSeq=1027063705)
- [개인정보 보호법 제36조 — 정보주체 정정·삭제 요구](https://www.law.go.kr/LSW/lsLinkCommonInfo.do?chrClsCd=010202&lsJoLnkSeq=1029335317)
