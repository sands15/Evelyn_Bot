# Evelyn Autonomy Authorization And Outcome Contract

Document status: **Current**
Last reviewed: 2026-08-12 KST

이 문서는 이블린이 “허락된 세계에서 스스로 행동한다”는 목표를 현재
런타임에서 어떻게 제한하고 검증하는지 정의한다. 기능 플래그, 저장된 상태,
서비스 재시작은 사용자 승인으로 간주하지 않는다.

## Assistant 자율 루프 불변식

1. assistant 자율 루프는 현재 프로세스에서 발급된 유효한 grant 없이는
   시작할 수 없다.
2. grant는 guild별 exact action scope이며, 빈 scope와 지원하지 않는 action은
   허용하지 않는다.
3. grant는 기본 1시간, 최대 4시간이고 프로세스 재시작 뒤 복구하지 않는다.
4. `AUTONOMY_ENABLED=true`는 기능 사용 가능 여부일 뿐 자동 실행 승인이 아니다.
5. 저장된 `enabled`와 `allowed_actions`는 재시작 뒤 실행 권한으로 복원하지 않는다.
6. action 실행 직전에 grant와 exact scope를 다시 검사한다.
7. executor가 성공 결과를 반환한 뒤에도 실행 전과 같은 grant ID가 여전히
   유효한지 재검사한다. 실행 중 만료·철회·교체되면 결과가 실제로 발생했더라도
   plan cursor를 진행하지 않는다.
8. 만료·철회·scope 불일치가 발견되면 루프를 중단하고
   `authorization_required`로 전환한다.
9. executor 결과가 성공 상태여도 `verified=true`와 그 action에 허용된 exact
   `evidence_code`가 모두 없으면 계획 cursor를 진행하지 않는다.
10. retry budget 소진은 효과 증거가 아니며 성공·skip으로 바꾸거나 plan cursor를
    진행하지 않는다.
11. 승인·결정·결과 journal을 durable 기록할 수 없으면 모든 grant를 폐기하고
    `authorization_audit_unavailable`로 fail-closed한다.
12. 구현되지 않은 callback은 `executor_callback_unavailable`로 차단하며
   성공 no-op으로 대체하지 않는다.

## 승인 진입점

Discord의 다음 변경성 명령은 허용된 Discord ID 또는 서버 관리자만 실행할 수
있다.

- `자율시작`, `자율정지`
- `마크접속`, `마크종료`, `마크목표`

`자율시작`은 기존 state label과 관계없이 engine stop을 먼저 끝낸다. 보존된
Minecraft route intent가 있으면 route를 다시 연결·검증한다. 그 결과에 따라
assistant 기본 scope와 검증 성공 시에만 Minecraft allowlist를 포함한 grant를
새로 발급한 뒤 engine을 시작한다. 시작 실패 시
grant는 즉시 폐기한다. 기존 engine cleanup, 선택적인 route 재연결 또는 새 start await가
취소돼도 기존 grant를 폐기한 뒤 취소를 재전파한다. `자율정지`는 engine
존재 여부와 관계없이 grant부터 철회하고 executor를 멈추되 process-local route
intent는 유지한다. route 비활성화는 명시적 `마크종료`만 수행하며 intent를
executor cleanup보다 먼저 지워 cleanup 오류가 권한 상태를 되살리지 못하게 한다.
route enable·disable과 lifecycle connect·disconnect는 같은 guild router lock으로 직렬화한다.
engine start·stop은 engine lock 아래 task cancel, executor cleanup, 상태 commit까지 직렬화한다.
disabled stale loop나 실패한 cleanup이 남으면 이를 선택적인 route 재연결과 grant보다 먼저
정리하고 성공한 뒤에만 새 loop를 시작한다.
stale child 취소만 내부 처리하고 start 호출자 자신의 cleanup/route/start 취소는 grant cleanup 뒤 재전파한다.
Minecraft executor는 readiness 확인 뒤 disconnect currentness와 inflight 등록을 같은
admission lock에서 선형화한다. stop이 먼저 완료되면 world action을 dispatch하지 않는다.

현재 Control Page의 Minecraft 변경은 CSRF 보호를 통과한 명시적 사용자 요청
경계에서만 실행한다. Control Page에는 일반 assistant 자율 루프를 시작하는
별도 API가 없다.

## Grant 상태

owner는 `AutonomyAuthorizationManager`다.

- 공개 상태:
  `runtime_artifacts/autonomy_authorization/status.json`
- 로컬 감사 이벤트:
  `runtime_artifacts/autonomy_authorization/events/YYYYMMDD.jsonl`
- schema:
  `autonomy_authorization.status.v1`,
  `autonomy_authorization.event.v1`,
  `autonomy_authorization.decision.v1`

공개 상태에는 issuer identity를 넣지 않는다. 로컬 감사 journal에는 누가
승인을 발급했는지 확인할 수 있도록 정규화된 `issuerRef`를 남긴다. 어느
artifact에도 대화문, raw action arguments, 모델 응답 payload, 음성 데이터,
예외 stack 또는 임의 경로를 기록하지 않는다.

감사 이벤트는 action 이름, 고정 reason code, 결과 상태, 검증 여부와 정규화된
evidence code만 저장한다. 각 이벤트 행은 반환 전에 flush와 `fsync`를 완료한다.
기록 실패 시 새 grant나 실행 허용을 반환하지 않으며 기존 grant도 모두
폐기한다. 기본 retention은 30일 또는 20 MiB이고 최신 7개 파일을 보존한다.

status의 `auditReady`는 현재 journal 기록 가능 여부를 나타낸다. policy에는
`autonomy.outcome-evidence-policy.v1`, `strictActionEvidenceMatch=true`,
`retryExhaustionIsEvidence=false`가 노출된다. Discord `자율상태`는 issuer나
grant ID를 공개하지 않고 현재 guild의 승인 활성 여부, 남은 TTL, audit 상태와
strict evidence policy만 보여준다.

## 결과 검증

계획 진행이 가능한 결과는 다음 조건을 모두 만족해야 한다.

- `status`가 `ok`, `done`, `completed` 중 하나다.
- `verified`가 정확히 `true`다.
- executor가 `autonomy.outcome-evidence-policy.v1`에서 해당 action에
  허용한 exact `evidence_code`를 반환한다.
- 실행 뒤 같은 grant ID가 아직 유효하다.

조건을 만족하지 않는 성공 응답은 `unverified/outcome_unverified`로
정규화한다. plan cursor는 유지하고 다음 cycle에서 재계획한다.

assistant 기본 executor의 대표 증거 코드는 다음과 같다.

- Discord 전송 완료: `discord_send_completed`
- 상태 snapshot 생성: `status_snapshot_built`
- 최근 문맥 요약 생성: `recent_context_payload_built`
- 알림 요약 생성: `summary_payload_built`
- cognitive state 갱신: `cognitive_state_updated`
- proactive gate만 확인한 무동작: `proactive_gate_completed`
- 부작용 없는 idle: `no_side_effect_required`

`assistant:send_followup`의 `discord_send_completed`는 Discord send await가 정상
반환했다는 effect 증거다. 그 뒤 history/session/continuity 또는 선택적 memory/self-state
후처리의 일반 예외는 이 effect를 미전달로 바꾸지 않으며, verified 결과와 현재 grant를
다시 확인한 뒤 plan cursor를 전진시킨다. 같은 프로세스에서는 전송 직후 세운 900초
ping fence가 search-pending과 unresolved maintain 계획의 즉시 재생성을 막는다. send
await 내부 취소·timeout의 원격 전달 여부와 process crash exactly-once는 이 증거의
보장이 아니다.

`maybe_ping_user`는 실제 메시지를 보냈을 때 `discord_send_completed`, 보낼
필요가 없음을 gate에서 확인했을 때 `proactive_gate_completed`만 허용한다.
다른 action의 올바른 코드를 교차 제출해도 검증되지 않는다. Minecraft policy는
각 action별 `minecraft_<action>_completed`를 사용하며, inventory/hazard/hostile/
target/food 부재로 명시적으로 생략하는 일부 action만 고정 absence evidence를
추가로 허용한다. 현재 실제로 배선된 Minecraft route는
`minecraft:find_food_source` 하나이며, 이 action에는
`minecraft_find_food_source_completed`만 허용되고 absence·skip evidence는 없다.

## Minecraft 직접 제어 결과

Minecraft 접속은 위치 값이나 `active` 값만으로 성공하지 않는다.
`connected`, `minecraft_connected`, `voyager_connected` 중 실제 연결
증거가 확인되어야 `minecraft_connected` 결과를 발급한다.

종료는 running과 connection 상태가 모두 false인 것을 확인해야
`minecraft_stopped` 결과를 발급한다. 즉시 확인되지 않으면 제한된 시간 동안
상태를 polling하고, 끝까지 확인되지 않으면 `minecraft_stop_unverified`로
실패한다.

목표 변경은 서비스 응답의 `goal` 또는 `goal_override`가 요청 문자열과 정확히
일치할 때만 `minecraft_goal_confirmed`로 인정한다. 확인 전에 local override를
먼저 저장하지 않는다.

Control Page와 Discord formatter도 위 outcome marker를 다시 검사하므로, 낮은
계층이 불완전한 payload를 반환하더라도 성공 문구를 만들지 않는다.

Minecraft world-action lease의 감사 내구성과 상태 publication readiness도
실행 권한의 일부다.
status/proof를 소비하는 모든 경계는 `auditReady`와 `statusReady`가 모두
정확한 boolean `true`일 때만 승인된 lease로 인정한다. 필드 누락, `false`,
문자열이나 숫자 같은 대체 값은 각각
`minecraft_world_lease_audit_unavailable` 또는
`minecraft_world_lease_status_write_failed`로 거부한다.

Bot API 단일 owner의 권한 근거는 stable `owner_claim.lock`에 process lifetime
동안 유지하는 exclusive OS lock 하나뿐이다. `owner_claim.json`의 heartbeat,
timestamp와 PID는 진단 정보이므로 오래됐다는 이유로 살아 있는 owner를 교체하지
않는다. claim의 process nonce는 status/proof epoch가 현재 owner publication과
일치하는지 fail-closed로 fencing하지만 새 owner를 선출하는 근거는 아니다. 정상
shutdown은 revoke와 shielded runtime cleanup 뒤 kernel lock을 반납하고,
crash·process exit에서는 OS가 lock을 해제한다. 새 owner는 lock 획득 뒤 새
process nonce와 capability token을 발급하고 이전 lease를 복구하지 않는다.
15초 heartbeat freshness는 Mindcraft/Voyager가 stale status를 거부하고 runner를
정지하는 service-side 경계이며 owner takeover 유예가 아니다.

별도의 stable `world_action.lock`은 proof admission과 실제 effect 사이를 owner
handoff와 직렬화한다. Mindcraft/Voyager `/start`·`/goal`은 검증 직전부터 effect
commit까지 이 lock을 유지하고, successor owner는 predecessor token 폐기와 새
claim/status/secret epoch 게시 동안 같은 lock을 유지한다. 따라서 검증 직후 이전
owner가 죽더라도 이미 검증된 proof가 새 epoch의 effect로 넘어갈 수 없다. lock
busy·unavailable은 임의 재시도나 timestamp fallback 없이 503으로 거부한다.
Mindcraft의 background reconcile도 lock 획득 뒤 guarded lease를 읽고 stop 또는
ensure-start effect가 끝날 때까지 같은 capability를 유지한다. endpoint가 이미
획득한 lock을 넘길 때는 acquired 상태와 exact canonical path를 다시 확인하며,
busy·unavailable·위조 capability에서는 자동 시작하지 않는다.

owner 초기화의 `process_started`, lease 발급, runner 시작 확인, goal 실행 전
시도와 실행 후 확인 event는 각 JSONL 행을 flush하고 `fsync`한 뒤에만 성공으로
인정한다. 필요한 event를 내구 기록할 수 없으면 다음 계약을 적용한다.

- 초기화, lease 발급, runner 시작과 goal 변경은 fail-closed한다.
- 활성 lease와 process capability를 제거하고 공유 private capability artifact를
  더 이상 권한 근거로 제공하지 않는다.
- 이미 runner start나 goal effect가 발생한 뒤 확인 event를 잃은 경우에도
  성공으로 반환하지 않고 runner 안전 정지를 시도한다.
- lease 철회, stop, watchdog cleanup과 shutdown은 감사 저장소가 죽어도 안전을
  위해 실행한다. 물리적 정지가 확인되어도 응답과 status에는 audit unavailable과
  `manual_intervention_required`를 남겨 감사된 성공으로 오인하지 않는다.
- public status artifact를 내구 교체하지 못하면 active lease와 delegation
  capability를 제거하고, 이미 실행 중일 가능성이 있는 runtime을 cancellation에도
  중단되지 않는 safety stop으로 정리한다. 결과는
  `minecraft_world_lease_status_write_failed`와
  `manual_intervention_required`이며 stale status 파일은 권한 근거가 아니다.

Bot API의 내부 mutation endpoint는 인증을 먼저 검사한다. 인증되지 않은 401은
고정 오류만 반환하고 `leaseStatus`나 lease metadata를 노출하지 않는다. 인증된
remote consumer는 authoritative status의 두 readiness boolean과 active/lease
형식을 다시 검증하며, status 누락·손상, 오류, transport 예외와 요청 취소 시
기존 active cache를 즉시 inactive error 상태로 지운다.

이 경계의 status와 journal에는 raw goal, transcript, Minecraft chat, action
arguments, token과 임의 예외 원문을 저장하지 않는다.

현재 Voyager 직접 모드의 승인 경계는 owner/admin의 명시적 접속·종료·목표
명령이다. assistant engine grant의 TTL이 Voyager runner를 자동 중지시키는
watchdog으로 아직 연결되어 있지는 않다. 따라서 이 계약의 시간 제한 grant를
Minecraft의 지속 실행까지 확장했다고 해석하면 안 된다.

## Minecraft exact one-shot 자율행동

현재 production `RoutedAutonomyExecutor`에는 typed Minecraft executor가
등록되어 있다. 다만 접속만으로 action 권한이 생기지는 않는다. Discord
`마크접속`이 실제 연결을 확인한 뒤 guild의 route를 활성화하고, `자율시작`이
그 route를 다시 연결·검증한 경우에만 새 grant scope에
`minecraft:find_food_source`를 추가한다. `마크종료`는 route도 비활성화한다.
저장된 route 상태, 이전 grant 또는 terminal gateway 상태는 새 승인을 대신하지
않는다.

허용되는 step은 `domain=minecraft`, `action=find_food_source`, 선택 reason
`low_health_no_food`뿐이다. executor는 이를 exact
`minecraft_autonomy.action-request.v1`로 만들며 `parameters`는 항상 빈 객체다.
raw goal, command, argv, code, 좌표, inventory, transcript와 임의 추가 필드는
재귀적으로 거부한다. owner는 현재 guild lease 아래에서만 `goalRunId`,
`leaseId`, `leaseProcessNonce`를 추가해 request를 bind한다.

성공으로 plan cursor가 진행되는 순서는 다음과 같이 하나로 고정한다.

1. 같은 `actionRunId`와 Minecraft scope grant로 실행 전
   `action_authorized`가 durable 기록된다.
2. lease owner가 exact request와 현재 lease를 확인하고
   `action_dispatch_attempted`를 fsync한 뒤 Mindcraft action gateway에
   dispatch한다. accepted/running 응답의 모든 run·grant·lease·contract 필드는
   bound request와 정확히 같아야 한다.
3. gateway는 `world_action.lock`을 획득한 채 proof와 일곱 dependency readiness를
   검증하고 `mindcraft_food_recovery.v1` binding으로 world-effect projector를
   arm한 뒤 고정 food-recovery task로 runner를 시작한다.
4. gated goal manager는 실제 action result 뒤에만 content-free
   `mindcraft.postcondition-candidate.v1`을 1회 게시한다. projector는 같은
   `goalRunId`, `actionRunId`, action, contract, lease epoch, producer nonce와
   정확히 증가한 sequence를 요구한다. `food_reserve_ready`의 false-to-true,
   autonomous·relevant·actionSucceeded·worldChanged·goalProgress·
   predicateCompleted, `completionDelta=1`, `blockedDelta=0`가 모두 성립해야
   `effect_verified` event를 fsync한다.
5. effect가 검증되면 gateway는 Mindcraft runner를 먼저 정지한다. 정지 실패,
   result/status 내구 저장 실패 또는 guard 상실은 성공 결과로 바꾸지 않는다.
   특히 stop 예외, stop 뒤 생존 또는 생존 확인 실패는 active binding과
   `world_action.lock`을 유지한 채 gateway를 unavailable로 격리한다. 같은 active
   request의 exact cancel이 정지를 다시 검증하거나 운영자가 개입하기 전에는 새
   action이나 terminal success/failure를 게시하지 않는다.
   그 뒤에만 exact `minecraft_autonomy.action-result.v1`의
   `status=completed`, `verified=true`,
   `postconditionCode=food_reserve_ready`,
   `evidenceCode=minecraft_find_food_source_completed`를 반환하고 retained
   `world_action.lock`을 해제한다.
6. owner poller가 exact result를 다시 검증하고 같은 run/goal/lease의
   `action_completed`를 fsync한다. assistant loop는 같은 grant의 실행 후
   `action_authorized` 재검사와 그 뒤의 exact `action_outcome`까지 성공해야만
   cursor를 진행한다.

검증된 완료·취소·실패는 모두 one-shot terminal이다. gateway는 runtime 정지가
확인된 경우에만 active binding을 제거하고 terminal record를 내구 저장한다. 같은 process와
이미 연결 검증을 마친 executor에서만 exact content-free terminal readiness의
`repeatActionReady=true`를 다음 action의 재시작 admission으로 사용할 수 있다.
최초 `connect()`나 새 grant의 근거로는 사용할 수 없다. actionRunId와 goalRunId는
gateway status와 effect journal에서 replay-fenced되며, 재시작 때 accepted/running
record는 이전 Mindcraft 자식의 종료를 durable process identity로 증명한 뒤에만
`minecraft_action_authority_lost_on_restart` 실패로 닫고 재개하지 않는다. identity가
없거나 손상됐거나 stop/liveness를 검증할 수 없으면 gateway는
고정 prior-process 오류를 가진 unavailable 상태로 닫혀 운영자 개입을 요구하며,
terminal/repeat readiness와 새 action을 게시하지 않는다.

projector status와 event에는 원문 goal, command, inventory, position, chat 또는
transcript를 저장하지 않는다. 검증 가능한 것은 binding ID, contract와
postcondition/evidence code, sequence, 고정 boolean transition뿐이다.

## Control Page `autonomy-p0.v1` 관찰 검증

Control Page의 자율행동 검증기는 승인 또는 effect 실행기가 아니다. `start`는
`dryRun=true`인 요청만 받아 content-free preflight 세션을 만들고, `confirm`은
사용자가 수동 관찰 경계를 이해했다는 사실만 기록한다. 검증기는 Discord 명령,
grant 발급·철회, runtime repair, 서비스 시작, world lease 변경, Minecraft goal
또는 요청 큐 write를 호출하지 않는다. 실제 변경은 사용자가 기존 owner/admin
경계에서 별도로 실행하고, 검증기는 그 뒤 생성된 durable status와 JSONL 증거만
관찰한다.

세션은 `runtime_artifacts/autonomy_validation/` 아래에서 30분 동안 복구되며,
단계별 최대 시도는 3회다. 보고서와 투영 event에는 고정 step/action/evidence/error
code, boolean readiness, 시도 횟수와 시간만 남긴다. guild/user/issuer, grant·lease
ID, process nonce, goal, Discord message/chat, transcript, action argument, 좌표,
inventory와 raw exception은 저장하지 않는다. 보고서와 event는 30일 또는 최근
20개까지만 보존한다.

`assistant:*` 트랙은 동일 grant와 동일한 실행별 `actionRunId`에서 실행 전과
실행 후의 `action_authorized` 두 건이 source journal 순서대로 먼저 발생하고,
그 뒤 `action_outcome`이 성공 status,
`verified=true`, `authorizationCurrent=true`와 action별 exact `evidenceCode`를
모두 만족할 때만 통과한다. 실행 전 승인, 실행 후 grant 재검사, outcome은 같은
`actionRunId`를 공유하므로 서로 다른 두 실행의 증거를 합칠 수 없다. Minecraft
cleanup도 `lease_revoked`와 `runtime_stop_verified`가 동일 lease에 속할 때만
인정한다. producer가 재시작된 경우에는 non-restoration policy, 새 process epoch,
inactive authority와 새 epoch의 verified global stop을 함께 요구한다. 단계 retry도
별도 `step_retry_started` 감사 event가 durable하게 기록되지 않으면 실패한다.
최신 JSONL의 미종결 행은 2초의 동시-write grace 안에서만 보류하고 그 이후에는
손상으로 판정한다. Minecraft 트랙에서
`goal_verified`는 goal echo 증거일 뿐 world effect가 아니며, readiness `ready`도
실행 준비 증거일 뿐이다. 따라서 gated readiness와 별도의 trusted explicit
postcondition 투영이 모두 없으면 효과 단계는 통과하지 않는다.

production route와 durable postcondition observer는 이제 연결되어 있다.
검증기는 Minecraft scope grant, 실행 전·후 authorization, owner의 dispatch
attempt/verification, projector의 `effect_verified`, owner의 `action_completed`,
assistant의 exact outcome을 같은 grant·lease·actionRunId·goalRunId·contract로
상관시킨다. 이 전체 증거가 실제로 관찰된 경우에만
`minecraft_autonomy_route_unwired`와
`minecraft_postcondition_observer_unavailable` blocker가 사라진다. 단순히
코드가 배선되어 있거나 readiness가 ready라는 사실만으로 blocker를 제거하지
않는다.

## 검증 범위와 남은 증거

단위·composition 테스트는 다음을 검증한다.

- restart 비복구, TTL 만료, exact scope, grant 교체와 철회
- 실제 새 Python 프로세스의 crash/restart grant 비복구
- 민감 payload가 없는 상태와 JSONL 감사 이벤트
- audit journal의 flush/fsync와 write 실패 시 grant 전부 fail-closed
- verified action 뒤 outcome append 실패 또는 post-check race의 non-current outcome 시
  unverified 결과, cursor 유지와 engine 중단
- callback 부재와 evidence 누락의 fail-closed 처리
- action별 evidence 교차 제출 거부와 전체 supported action policy coverage
- 실행 중 grant 교체·만료 시 cursor 유지 및 원래 grant ID 감사
- retry budget 소진의 미검증·cursor 유지
- 미검증 결과와 미검증 skip이 plan cursor를 진행하지 않음
- Minecraft 접속·종료·목표 변경의 긍정/부정 outcome
- `minecraft:find_food_source` exact request/result, guild·grant·run·goal·lease
  correlation과 raw payload 거부
- action gateway dispatch/poll/cancel, replay fence, effect false-to-true 검증,
  terminal runtime stop과 동일 actionRunId validation correlation
- lifetime owner lock의 live-owner 경쟁 거부, crash release, nonce/token 회전과
  refresh/status/release adversarial interleaving
- 변경성 Discord 명령의 owner/admin 권한 검사
- 감사 journal retention 기본값

실제 Discord 메시지 전송, 장시간 grant 만료, Minecraft 연결·종료·목표 변경과
승인된 `minecraft:find_food_source`가 실제 world에서 `food_reserve_ready`를
만드는 과정을 한 세션에서 수행하는 live E2E는 별도 운영 증거가 필요하다. 현재
구현은 route와 observer가 source에 연결된 상태이지, Microsoft 인증 Minecraft와
Discord를 사용한 실제 E2E 통과가 확인된 상태는 아니다. 이 검증이 끝나기 전에는
자율행동 P0를 운영 완료로 판정하지 않는다. 직전 world-action lease의
2026-08-01 durable-audit snapshot은 bundled Python에서 Minecraft 115개
(skip 7), runtime 513개(skip 4), 인접 Discord/Mindcraft/UI 39개 회귀를
통과했다. 다만 실제 Minecraft E2E 증거는 아니므로 운영 완료 근거로 사용하지
않는다. 현재 lifetime lock도 Docker Desktop bind mount를 공유하는 실제 두
컨테이너 사이의 exclusion과 SIGKILL crash release는 별도 live 증거가 필요하다.
현재 increment의 source 회귀는 Minecraft 156개(skip 8), runtime 518개(skip 4)를
통과했다. 후속 정리에서 stale opaque note ID 기대값, Windows SQLite 연결 수명,
Voyager 경량 import의 선택 `requests` 결합을 각각 수정했고, 전체 discover
2,482개도 실패 없이 통과했다(skip 18). 이는 source-level 증거이며 실제
Minecraft 연결이나 컨테이너 간 lifetime lock의 live 증거를 대신하지 않는다.
후속 auto-reconcile TOCTOU 증분은 Mindcraft 18개와 Minecraft 157개(skip 8),
저장소 전체 2,503개(skip 18)를 통과했다. shutdown handoff 중 이전 epoch의
ensure-start 차단과 forged lock capability 거부를 합성 경합으로 검증했지만, 실제
두 프로세스/컨테이너와 Minecraft world effect를 사용한 live 증거는 아니다.
이전 source 회귀 수치는 이번 one-shot action gateway·effect projector 경로의
실제 Minecraft 행동 성공을 증명하지 않는다.
