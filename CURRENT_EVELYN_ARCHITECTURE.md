# CURRENT_EVELYN_ARCHITECTURE.md

Status note, 2026-06-01:

- This file is the current Minecraft/Voyager architecture snapshot.
- It is not the authoritative full Evelyn assistant voice/LLM pipeline map.
- For the current assistant pipeline, router/main/sub LLM conditions,
  `VoiceTurnOrchestrator`, route policy, delivery, and memory write-behind, see
  `CURRENT_EVELYN_PIPELINE.md`.
- For documentation status, see `docs/DOCUMENTATION_INDEX.md`.

Last updated: 2026-05-12  
Branch baseline: `structural-change`

## 1. High-level shape

Evelyn의 현재 Minecraft autonomy stack은 **하나의 봇 프로세스**가 아니라 **3층 브리지 구조**다.

1. **Action adapter layer**
   - Upstream Voyager `ActionAgent`
   - `evelyn_core/runtime/evelyn_core/codex_gateway_server.py`
   - Codex CLI / configured action backend
   - Primary port: `8787`

2. **Voyager orchestration layer**
   - `evelyn_core/runtime/evelyn_core/voyager_service.py`
   - `evelyn_core/runtime/evelyn_core/upstream_voyager_runner.py`
   - Upstream runtime in `third_party/Voyager`
   - Primary port: `8765`

3. **Minecraft control plane**
   - Upstream Voyager env -> mineflayer HTTP bridge
   - Minecraft server / bot session
   - Primary ports: `3000` (bridge), `25565` (Minecraft server)

핵심은 **세 레이어가 독립적으로 고장날 수 있다**는 점이다.  
Codex gateway가 살아 있어도 runner가 정상이라는 뜻은 아니고, runner가 살아 있어도 Minecraft control plane이 정상이라는 뜻은 아니다.

## 2. Current startup / runtime flow

### Launch entrypoints

- Root shim: `start_voyager.bat`
- Evelyn launcher wrapper: `evelyn_core/start_voyager.bat`
- Service host: `evelyn_core/runtime/launchers/start_voyager_service.ps1`
- Start trigger helper: `evelyn_core/runtime/launchers/start_voyager_task.ps1`

### Runtime sequence

1. Visible launcher starts the Voyager service host.
2. `voyager_service.py` opens the HTTP control surface on `8765`.
3. `/start` spawns `upstream_voyager_runner.py` in the Voyager venv.
4. The runner boots upstream Voyager from `third_party/Voyager`.
5. Voyager action generation routes through the Codex gateway.
6. Voyager executes against the mineflayer bridge / Minecraft runtime.
7. Runner writes status to `runtime_artifacts/voyager/upstream_bridge_status.json`.
8. Service serves `/health`, `/status`, and `/observe` from runner state plus live bridge telemetry.

## 3. Core local ownership boundaries

### Evelyn-owned integration files

- `evelyn_core/runtime/evelyn_core/voyager_service.py`
- `evelyn_core/runtime/evelyn_core/upstream_voyager_runner.py`
- `evelyn_core/runtime/evelyn_core/minecraft_autonomy_client.py`
- `evelyn_core/runtime/evelyn_core/codex_gateway_server.py`
- `main.py` (status merge / bot-facing integration)

### Upstream Voyager files still in the live runtime contract

- `third_party/Voyager/voyager/voyager.py`
- `third_party/Voyager/voyager/agents/action.py`
- `third_party/Voyager/voyager/agents/critic.py`
- `third_party/Voyager/voyager/agents/curriculum.py`
- `third_party/Voyager/voyager/utils/record_utils.py`

즉, 지금 구조에서는 upstream 쪽 파일도 단순 vendor reference가 아니라 **실운영 contract 일부**다.

## 4. State and checkpoint files

### Live status / logs

- `runtime_artifacts/voyager/upstream_bridge_status.json`
- `runtime_artifacts/logs/upstream_bridge_errors.log`
- `runtime_artifacts/logs/voyager_service_errors.log`
- `runtime_artifacts/logs/upstream_bridge_runner.log`

### Resume / checkpoint state

- `bot_memory/upstream_ckpt/events/*`
- `bot_memory/upstream_ckpt/action/chest_memory.json`
- `bot_memory/upstream_ckpt/curriculum/completed_tasks.json`
- `bot_memory/upstream_ckpt/curriculum/failed_tasks.json`
- `bot_memory/upstream_ckpt/skill/skills.json`

현재 runner는 checkpoint 건강도에 꽤 의존하므로, **resume safety는 1급 구조 관심사**로 본다.

## 5. Current behavioral model

### What is already working

- Codex gateway request path is live.
- Voyager service/runner can reach the Minecraft runtime.
- Observation flow (inventory / position / health / hunger) has been seen live.
- Generated in-world action execution has at least one confirmed success trace.
- Reset/start now reuses an already connected bot when possible to avoid unnecessary reconnect churn.

### What still needs explicit hardening

1. **State resume defense**
   - Corrupt / partial checkpoint data must not crash runner restore.
   - Missing observation fields must degrade safely.

2. **Result / critic bookkeeping**
   - Action success, critic decision, rollout completion, and surfaced task result need one explicit chain.
   - `status`만 보면 task가 정말 끝났는지 판단 가능해야 한다.
   - 2026-06-03 partial hardening: Voyager service task recovery boundary now treats
     completion reasons, task results, or verified bookkeeping without an explicit
     `last_success=true` as `task_unverified` / `task_result_unverified` instead of
     implicitly healthy.

3. **Health / recovery separation**
   - Service HTTP health, runner health, bridge health, and Minecraft dependency health must be represented separately.
   - Recovery should target the failing layer instead of treating every fault as one generic Voyager crash.

## 6. Current design intent for the next structural pass

다음 structural pass는 기존 3층 모델을 유지하되, 레이어 간 계약을 더 명확히 조이는 쪽이 맞다.

- **Resume contract:** usable vs degraded vs quarantined checkpoint
- **Task bookkeeping contract:** in-flight vs critic-passed vs critic-failed vs loop-exhausted vs crashed
- **Health contract:** service / runner / bridge / Minecraft dependency reported separately

이 문서를 현재 Evelyn Voyager 경로의 authoritative architecture snapshot으로 사용한다.
