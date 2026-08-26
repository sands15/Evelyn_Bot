# Minecraft Autonomy Readiness Contract

Document status: **Current**
Last reviewed: 2026-08-01 KST
Schema: `minecraft_autonomy.readiness.v1`

## Purpose

HTTP 200은 Mindcraft sidecar 프로세스의 liveness만 뜻한다. Control Page와
Runtime Health는 실제 Minecraft 자율행동 가능 여부를 별도 exact contract로
판정한다. 계약이 없거나 모순되면 준비 완료로 추측하지 않는다.

## Readiness payload

Mindcraft `/status`와 `/health`는 `functional_readiness`를 제공한다.

```json
{
  "schema": "minecraft_autonomy.readiness.v1",
  "state": "blocked|starting|ready",
  "ready": false,
  "blockers": ["minecraft_not_connected"],
  "dependencies": {
    "worldLeaseAuthorized": true,
    "runnerAlive": true,
    "telemetryFresh": true,
    "minecraftConnected": false,
    "taskContractReady": true,
    "effectObserverReady": true,
    "autonomyActive": true
  },
  "taskContract": {
    "schema": "mindcraft.task-contract.v1",
    "goalManagerMode": "gated",
    "autonomyState": "active",
    "commandGate": "evelyn_goal_manager",
    "effectVerification": "explicit_postcondition"
  },
  "contentFree": true
}
```

`ready=true`는 일곱 dependency가 모두 정확한 boolean `true`일 때만 가능하다.
소비자는 전달된 `ready`, `state`, `blockers`를 신뢰하지 않고 dependency에서
다시 계산한다.

## Fixed blockers

dependency 순서와 blocker 순서는 고정한다.

1. `world_lease_unauthorized`
2. `runner_not_alive`
3. `telemetry_stale`
4. `minecraft_not_connected`
5. `task_contract_unavailable`
6. `effect_observer_unavailable`
7. `autonomy_not_active`

임의 문자열, 누락 필드, 잘못된 순서, 중복 blocker는 계약 전체를
`invalid`로 만든다.

## State calculation

- 모든 dependency가 true: `ready`
- world lease, task contract, effect observer 또는 active autonomy가 false:
  `blocked`
- 그 외 runner/telemetry/connection 준비 중: `starting`

Mindcraft top-level의 `world_lease_authorized`, `running`,
`telemetry_fresh`, `minecraft_connected`는 readiness dependency와 정확히
같아야 한다. 서로 모순되면 legacy 상태로 fallback하지 않고 `invalid`다.

## Task contract

`taskContractReady=true`는 다음을 모두 요구한다.

- schema `mindcraft.task-contract.v1`
- goal manager mode `gated`
- command gate `evelyn_goal_manager`
- effect verification `explicit_postcondition`
- telemetry의 goal-manager mode도 `gated`

`autonomyActive=true`는 task contract와 telemetry의 autonomy state가 모두
`active`일 때만 유효하다.

`effectObserverReady=true`는 단순히 observer 객체가 존재한다는 뜻이 아니다.
현재 Mindcraft process의 world-effect projector가 `auditReady=true`,
`statusReady=true`이고 `idle|armed|verified|rejected` 중 안전한 상태여야 하며,
action gateway의 durable replay fence도 사용 가능해야 한다. observer event나
status를 내구 기록하지 못하거나 gateway 상태가 손상되면 readiness는
`effect_observer_unavailable`로 차단한다.
persisted policy의 `telemetryMaxAgeSec`도 유한한 0.1 이상 숫자여야 한다. bool,
비수치·복합 값, non-finite·overflow 값은 fixed unavailable blocker로 fail-closed한다.

완료·취소·실패한 one-shot action 뒤에는 Mindcraft runner가 정지되어 일반
readiness가 false일 수 있다. 이미 일반 readiness로 `connect()`를 마친 동일
executor만 exact `mindcraft_action_gateway.readiness.v1` terminal projection의
`ready`, `acceptsNewAction`, `repeatActionReady`, `contentFree`가 모두 true이고
`terminalStatus`가 `completed|cancelled|failed`일 때 다음 typed action을 시작할
수 있다. 이 terminal projection은 최초 접속 readiness나 새 executor의
authorization을 대신하지 않는다.

## Goal completion restart boundary

`lastDragonCombatAt`은 같은 Mindcraft process에서 성공한 autonomous dragon
attack과 뒤이은 `entityDead`의 인과를 결박하는 process-local latch다. 일반 child
restart에서는 절대 복원하지 않고 즉시 정리된 state를 다시 저장한다. 반대로 이미
검증된 `ultimateGoalCompletedAt`과 completed autonomy state는 durable하게 복원한다.
검증된 exact `!endGoal`만 completed 상태에서 survival recovery와 unarmed-hostile
movement gate보다 우선해 기존 control-command 경계로 들어가며 SelfPrompter를 한 번
멈춘다. 미검증 `!endGoal`, 다른 completed command와 manual pause는 계속 fail-closed한다.

## Consumers

- `mindcraft_service.py`: runner/telemetry/lease에서 계약을 생산한다.
- `minecraft_autonomy_readiness.py`: exact schema를 검증하고 안전한
  content-free projection만 반환한다.
- `runtime_health.py`: HTTP liveness와 `runtimeReady`를 분리하고 고정
  blocker diagnostic을 만든다.
- `minecraft_autonomy_client.py`: Control Page의 `voyagerReady` probe에 같은
  validator를 사용한다.
- `mindcraft_world_effect.py`: lease와 readiness를 다시 검사하며 exact
  postcondition edge를 content-free durable event/status로 투영한다.

legacy Voyager는 새 계약이 완전히 없고 runtime이 `mindcraft`가 아닐 때만
기존 typed `recovery_state`를 사용할 수 있다. 새 계약이 존재하지만
손상됐으면 legacy fallback은 금지한다.

## Privacy and operations

공개 readiness projection에는 goal, task text, player, position, inventory,
transcript, raw telemetry와 예외 메시지를 넣지 않는다. liveness healthcheck는
sidecar 재시작 판단을 위해 HTTP 200을 유지할 수 있지만, UI와 자동화 기능
준비 판정에는 반드시 functional readiness를 사용한다.

실제 `ready` 판정의 최종 운영 증거는 승인된 world lease 아래에서 runner와
Minecraft 연결, fresh telemetry, task effect를 한 live 세션에서 함께
확인해야 한다.
