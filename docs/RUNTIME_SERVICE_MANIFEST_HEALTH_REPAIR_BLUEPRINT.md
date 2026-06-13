# Runtime Service Manifest + Health/Repair Layer Blueprint

Status: temporary design blueprint, first implementation in progress  
Created: 2026-06-09  
Owner context: Evelyn/이블린 runtime stabilization  
Target deadline window: 2026-06-11 20:00 KST for first pass planning/implementation work  

## Purpose

This document captures the proposed design for Evelyn's first-priority
stabilization work:

**Runtime Service Manifest + Health/Repair Layer**

Korean name:

**런타임 서비스 명세 + 상태/복구 계층**

The goal is to make Evelyn know the state of its own runtime services, explain
partial failures clearly, and eventually offer safe repair actions. This is the
foundation for later TTS, memory, UI, vision, and Voyager improvements.

The immediate problem this design addresses is the recent `8799` / `8798`
confusion:

- `8799` is the public Control-Page server.
- `8798` is the Bot API hosted by `main.py`.
- If `8799` is alive but `8798` is down, the page can open while Evelyn's real
  command/chat backend is unavailable.
- Old launcher logic could accidentally treat `8799` as proof that `main.py`
  was running.

This design makes that kind of drift harder to reintroduce.

## Design Thesis

Evelyn should not treat runtime health as scattered helper code.

Instead, service identity, health checking, diagnosis, and repair suggestions
should be formalized into a small runtime layer:

```text
service_manifest.json
 -> runtime_services.py
 -> runtime_health.py
 -> runtime_repair.py
 -> Control-Page state/API/UI
```

The first version should be read-only. It should observe and explain before it
tries to fix. Repair actions should come only after the manifest and health
contracts are stable.

## Goals

1. Define a single canonical service list.
2. Permanently separate `8799 = Control-Page` and `8798 = Bot API`.
3. Replace hard-coded, duplicated port probes with manifest-driven checks.
4. Expose structured service health to `main.py`, `control_page_server.py`, and
   the Control-Page UI.
5. Diagnose common partial-failure states in plain Korean.
6. Preserve existing `/api/control-page/state` compatibility.
7. Add tests that prevent port-role regressions.
8. Lay the groundwork for manual repair actions.

## Non-Goals

The first implementation should not do these:

- Fully rewrite all launchers and process supervision.
- Add automatic restart loops for every service.
- Kill broad process names such as `python.exe`, `node.exe`, or `wsl.exe`.
- Replace existing `/restart` and `/shutdown` behavior immediately.
- Deeply health-check every optional subsystem in the first pass.
- Require Docker, Kubernetes, or a multi-host service registry.
- Break the current Control-Page state schema.

## First-Pass Principle

Phase 1 should answer this question reliably:

> What is alive, what is down, and what should the user do next?

It does not need to repair everything yet.

## Proposed Files

```text
evelyn_core/runtime/
  service_manifest.json

evelyn_core/runtime/evelyn_core/
  runtime_services.py
  runtime_health.py
  runtime_repair.py
  runtime_contracts.py        # optional, only if constants start spreading

tests/runtime/
  test_service_manifest.py
  test_runtime_health.py
  test_runtime_repair.py

tests/core/
  test_control_page_state_runtime_health.py

tests/ui/
  test_control_page_runtime_health_ui.py
```

Suggested responsibility:

- `service_manifest.json`: static service definitions.
- `runtime_services.py`: load, validate, resolve, and cache the manifest.
- `runtime_health.py`: run TCP/HTTP probes and produce structured health.
- `runtime_repair.py`: later safe repair planning and guarded execution.
- `runtime_contracts.py`: optional shared IDs, status strings, and schema keys.

## Service Manifest

`service_manifest.json` is the single source of truth for runtime service
identity.

It should answer:

- What is the service called?
- Is it required?
- Which port owns it?
- How do we check it?
- How do we start it?
- Is repair allowed?
- What environment variables can override it?

### Manifest Location

```text
C:\Evelyn\evelyn_core\runtime\service_manifest.json
```

Reason:

- It belongs to runtime, not docs.
- It is shared by `main.py`, `control_page_server.py`, and launchers.
- It can be loaded by Python without reaching into docs or UI files.

### Initial Manifest Shape

```json
{
  "schema_version": "1.0",
  "runtime_name": "evelyn-local",
  "services": [
    {
      "id": "control_page",
      "label": "Control-Page",
      "kind": "python_http",
      "required": true,
      "host": "127.0.0.1",
      "port": 8799,
      "env": {
        "port": "CONTROL_PAGE_PUBLIC_PORT"
      },
      "checks": [
        {
          "kind": "tcp",
          "timeout_ms": 300
        },
        {
          "kind": "http",
          "path": "/health",
          "timeout_ms": 1000,
          "expect_status": 200,
          "expect_json": {
            "ok": true
          }
        }
      ],
      "launcher": "launchers/start_control_page.ps1",
      "repair": {
        "allowed": true,
        "strategy": "start_if_down",
        "requires_confirm": true,
        "cooldown_sec": 30
      }
    },
    {
      "id": "bot_api",
      "label": "Bot API",
      "kind": "python_http",
      "required": true,
      "host": "127.0.0.1",
      "port": 8798,
      "env": {
        "port": "CONTROL_PAGE_BOT_API_PORT"
      },
      "checks": [
        {
          "kind": "tcp",
          "timeout_ms": 300
        },
        {
          "kind": "http",
          "path": "/api/control-page/state",
          "timeout_ms": 6000,
          "expect_status": 200
        }
      ],
      "launcher": "launchers/start_bot.ps1",
      "repair": {
        "allowed": true,
        "strategy": "start_if_down",
        "requires_confirm": true,
        "cooldown_sec": 30
      }
    }
  ]
}
```

### Initial Service List

| id | port | required | health target | role |
|---|---:|---:|---|---|
| `control_page` | 8799 | yes | `/health` | Public Control-Page server |
| `bot_api` | 8798 | yes | `/api/control-page/state` | `main.py` Bot API |
| `main_llm` | 9820 | yes | `/v1/models` | Main response model |
| `sub_llm` | 9821 | yes | `/v1/models` | Summary/sub model |
| `router_llm` | 9822 | yes | `/v1/models` | Routing model |
| `tts` | 8880 | yes | `/health` or TCP fallback | OmniVoice TTS |
| `vision` | 8891 | no/soft | `/health` | Vision service |
| `voyager` | 8765 | optional | service-specific | Minecraft/Voyager |
| `codex_gateway` | 8787 | optional | service-specific | Codex/Voyager code bridge |

Important:

- `vision`, `voyager`, and `codex_gateway` should not block basic chat unless
  a mode explicitly requires them.
- `control_page`, `bot_api`, `main_llm`, `router_llm`, `sub_llm`, and `tts`
  are core for the usual local assistant experience.

## Manifest Validation Rules

The loader should reject or warn on:

- Duplicate service IDs.
- Duplicate required ports unless explicitly marked `shared_port`.
- Missing `id`, `label`, `host`, or `port`.
- Unknown check kinds.
- Invalid relative launcher path.
- Repair enabled without a launcher or safe strategy.
- `control_page` and `bot_api` mapped to the same effective port.

The `8799` / `8798` split should be a hard test contract.

## `runtime_services.py`

This module owns manifest loading and resolution.

### Responsibilities

- Locate `service_manifest.json`.
- Load JSON.
- Validate schema.
- Apply environment overrides.
- Normalize paths.
- Return stable typed objects.
- Provide a small cache to avoid repeated disk reads.

### Suggested Types

```python
from dataclasses import dataclass
from typing import Any, Literal

ProbeKind = Literal["tcp", "http"]

@dataclass(frozen=True)
class HealthProbeSpec:
    kind: ProbeKind
    host: str
    port: int
    path: str = ""
    method: str = "GET"
    timeout_ms: int = 500
    expect_status: int | None = None
    expect_json: dict[str, Any] | None = None

@dataclass(frozen=True)
class RepairSpec:
    allowed: bool
    strategy: str = "none"
    requires_confirm: bool = True
    cooldown_sec: int = 60

@dataclass(frozen=True)
class ServiceSpec:
    id: str
    label: str
    kind: str
    required: bool
    host: str
    port: int
    checks: tuple[HealthProbeSpec, ...]
    launcher: str | None = None
    repair: RepairSpec | None = None
    aliases: tuple[str, ...] = ()

@dataclass(frozen=True)
class ServiceManifest:
    schema_version: str
    runtime_name: str
    services: tuple[ServiceSpec, ...]
```

### Public API

```python
def load_service_manifest(*, force: bool = False) -> ServiceManifest:
    ...

def get_service(manifest: ServiceManifest, service_id: str) -> ServiceSpec | None:
    ...

def service_port_map(manifest: ServiceManifest) -> dict[str, int]:
    ...

def validate_service_manifest(manifest: ServiceManifest) -> list[ManifestIssue]:
    ...
```

### Environment Overrides

The manifest should allow known environment overrides without spreading them
through the codebase.

Examples:

| Service | Default | Env override |
|---|---:|---|
| `control_page` | 8799 | `CONTROL_PAGE_PUBLIC_PORT` |
| `bot_api` | 8798 | `CONTROL_PAGE_BOT_API_PORT` |
| `main_llm` | 9820 | `MAIN_LLM_PORT` |
| `sub_llm` | 9821 | `SUB_LLM_PORT` |
| `router_llm` | 9822 | `ROUTER_LLM_PORT` |
| `tts` | 8880 | `TTS_PORT` |
| `vision` | 8891 | `VISION_PORT` |

The resolved output should include both default and effective port so the UI can
debug override mistakes.

## `runtime_health.py`

This module owns active state checks.

### Responsibilities

- Run TCP checks.
- Run HTTP checks.
- Measure latency.
- Classify each service.
- Generate diagnostics.
- Summarize overall runtime state.

### State Model

Use a small enum-like set of states:

| State | Meaning |
|---|---|
| `up` | Required checks passed |
| `partial` | TCP open but app health failed or timed out |
| `down` | TCP closed or no checks passed |
| `degraded` | Optional or secondary checks failed |
| `unknown` | Check could not run or manifest invalid |
| `starting` | Within boot grace window, not failed yet |

`partial` is important. It prevents the common mistake where an open port is
treated as a healthy application.

### Probe Result Shape

```json
{
  "kind": "http",
  "ok": false,
  "reason": "timeout",
  "elapsedMs": 1201,
  "target": "http://127.0.0.1:8798/api/control-page/state",
  "status": null,
  "error": "TimeoutError"
}
```

### Service Health Shape

```json
{
  "id": "bot_api",
  "label": "Bot API",
  "required": true,
  "host": "127.0.0.1",
  "port": 8798,
  "state": "down",
  "ready": false,
  "reason": "port_closed",
  "checkedAt": 1781000000.0,
  "elapsedMs": 4,
  "checks": [
    {
      "kind": "tcp",
      "ok": false,
      "reason": "connection_refused"
    }
  ],
  "suggestedActions": [
    {
      "id": "start_bot_api",
      "label": "Bot API 시작",
      "risk": "medium",
      "requiresConfirm": true
    }
  ]
}
```

### Runtime Health Summary Shape

```json
{
  "ok": false,
  "overallState": "degraded",
  "summary": "페이지는 켜져 있지만 Bot API가 꺼져 있어 채팅/명령 처리가 제한됩니다.",
  "manifestVersion": "1.0",
  "checkedAt": 1781000000.0,
  "services": [
    {"id": "control_page", "state": "up", "ready": true},
    {"id": "bot_api", "state": "down", "ready": false}
  ],
  "diagnostics": [
    {
      "code": "BOT_API_DOWN_WITH_CONTROL_PAGE_UP",
      "severity": "error",
      "message": "Control-Page는 켜져 있지만 Bot API(8798)가 꺼져 있습니다.",
      "details": "페이지는 열리지만 채팅, 메모리 명령, runtime 명령은 실패할 수 있습니다.",
      "serviceIds": ["control_page", "bot_api"],
      "suggestedActions": ["start_bot_api"]
    }
  ]
}
```

## Diagnosis Layer

Diagnosis turns raw checks into human-readable explanations.

### Core Diagnosis Codes

| Code | Trigger | Message |
|---|---|---|
| `BOT_API_DOWN_WITH_CONTROL_PAGE_UP` | `control_page=up`, `bot_api=down` | 페이지는 켜져 있지만 Bot API가 꺼져 있음 |
| `CONTROL_PAGE_DOWN` | `control_page=down` | Control-Page 서버가 응답하지 않음 |
| `BOT_API_PARTIAL` | Bot TCP open, HTTP fail | Bot API 포트는 열렸지만 앱 응답이 비정상 |
| `CONTROL_PAGE_PROXY_TIMEOUT` | page proxy times out to bot | Control-Page가 Bot API 응답을 기다리다 타임아웃 |
| `MAIN_LLM_DOWN` | main LLM down | Main LLM이 꺼져 답변 생성 불가 |
| `ROUTER_LLM_DOWN` | router down | 라우팅 품질 저하 또는 fallback 필요 |
| `SUB_LLM_DOWN` | sub down | 요약/보조 판단 기능 제한 |
| `TTS_DOWN` | TTS down | 음성 출력 불가 |
| `VISION_DOWN` | vision down | 화면 인식 기능 제한 |
| `MANIFEST_PORT_COLLISION` | two services share port unexpectedly | 서비스 포트 정의 충돌 |

### Diagnosis Rules

Rules should be explicit and tested.

Important examples:

```text
IF control_page.up AND bot_api.down
THEN BOT_API_DOWN_WITH_CONTROL_PAGE_UP
```

```text
IF bot_api.tcp_ok AND bot_api.http_timeout
THEN BOT_API_PARTIAL
```

```text
IF control_page.proxy_timeout AND bot_api.direct_http_ok
THEN CONTROL_PAGE_PROXY_TIMEOUT
```

The last rule prevents the page from falsely saying "Bot API unavailable" when
the real issue is proxy timeout or payload size.

## Control-Page Integration

The Control-Page should consume `runtime_health.py` output instead of
recreating its own service model.

### Backward Compatibility

Existing state should remain:

```json
{
  "runtime": {
    "services": {
      "botReady": true,
      "mainReady": true,
      "routerReady": true,
      "subReady": true,
      "ttsReady": true
    }
  }
}
```

New state should be added:

```json
{
  "runtime": {
    "serviceHealth": {
      "ok": true,
      "overallState": "up",
      "summary": "전체 핵심 서비스 준비 완료",
      "services": [],
      "diagnostics": []
    }
  }
}
```

### Recommended Endpoints

First pass:

```text
GET /api/control-page/runtime-health
GET /api/control-page/runtime-manifest
```

Later:

```text
POST /api/control-page/runtime-repair
```

### State Embedding

`/api/control-page/state` should include the latest service health:

```json
{
  "runtime": {
    "services": {
      "botReady": true
    },
    "serviceHealth": {
      "overallState": "up"
    }
  }
}
```

This lets the UI migrate gradually. Existing booleans stay alive while new UI
uses richer diagnostics.

## UI Design

The UI should avoid raw developer noise while still being honest.

### Recommended Panel

Add a compact "Runtime Health" block:

```text
Runtime Health

Control-Page    켜짐
Bot API         꺼짐     [시작]
Main LLM        켜짐
Router LLM      켜짐
Sub LLM         켜짐
TTS             켜짐
Vision          제한됨

진단:
페이지는 켜져 있지만 Bot API가 꺼져 있어 채팅/명령 처리가 제한됩니다.
```

### UI Rules

- Do not show `Bot API unavailable` if direct Bot API health is actually OK.
- Do not collapse all errors into one red global state.
- Show required services before optional services.
- Show repair buttons only when an action is available and safe.
- Repair buttons should use icons plus short labels.
- High-risk repair actions should require confirmation.
- Local-only mode should not use Discord-specific error copy.

## Repair Layer

Repair must be conservative.

### First Repair Version

The first repair layer should support only dry-run and suggestions.

Example:

```json
{
  "ok": true,
  "action": "start_bot_api",
  "dryRun": true,
  "wouldRun": "launchers/start_bot.ps1",
  "requiresConfirm": true,
  "risk": "medium"
}
```

### Later Repair Execution

Only after read-only health is stable:

```json
{
  "action": "start_service",
  "serviceId": "bot_api",
  "confirmToken": "..."
}
```

### Safety Rules

1. Never kill broad process names.
2. Never stop OpenClaw/Codex-owned processes.
3. Only touch processes whose command line or port ownership proves Evelyn
   ownership.
4. Prefer start-if-down over restart.
5. Restart requires explicit confirmation.
6. Add cooldowns for repeated repair actions.
7. Log every repair attempt.
8. For visible-runtime processes, launch visible unless the user explicitly
   asked for hidden/background.

### Repair Risk Levels

| Risk | Meaning | Examples |
|---|---|---|
| `low` | no process mutation | refresh health cache, retry proxy |
| `medium` | start missing service | start Bot API, start Control-Page |
| `high` | stop/restart process | restart Bot API, restart TTS |
| `blocked` | not allowed from UI | kill arbitrary process, WSL shutdown |

## Launcher Integration

Launchers should eventually read or mirror manifest definitions.

### Immediate Contracts

- `start_local_background.ps1` must not treat `8799` as proof of `main.py`.
- `start_local_background.ps1` must start Control-Page on `8799`.
- `start_local_background.ps1` must start Bot API on `8798`.
- `start_bot.ps1` must prefer `CONTROL_PAGE_BOT_API_PORT` when present.
- `stop_evelyn_local.ps1` must include both `8798` and `8799`.

### Later Refactor

Eventually, launcher scripts can use a small generated or shared service map:

```powershell
$controlPagePort = Get-EvelynServicePort 'control_page'
$botApiPort = Get-EvelynServicePort 'bot_api'
```

That is optional. The first useful step is tests that enforce the contract.

## API Payload Examples

### All Core Services Ready

```json
{
  "ok": true,
  "overallState": "up",
  "summary": "전체 핵심 서비스 준비 완료",
  "services": [
    {"id": "control_page", "state": "up", "ready": true, "port": 8799},
    {"id": "bot_api", "state": "up", "ready": true, "port": 8798},
    {"id": "main_llm", "state": "up", "ready": true, "port": 9820},
    {"id": "router_llm", "state": "up", "ready": true, "port": 9822},
    {"id": "sub_llm", "state": "up", "ready": true, "port": 9821},
    {"id": "tts", "state": "up", "ready": true, "port": 8880}
  ],
  "diagnostics": []
}
```

### Page Up, Bot API Down

```json
{
  "ok": false,
  "overallState": "degraded",
  "summary": "페이지는 켜져 있지만 Bot API가 꺼져 있습니다.",
  "services": [
    {"id": "control_page", "state": "up", "ready": true, "port": 8799},
    {"id": "bot_api", "state": "down", "ready": false, "port": 8798, "reason": "port_closed"}
  ],
  "diagnostics": [
    {
      "code": "BOT_API_DOWN_WITH_CONTROL_PAGE_UP",
      "severity": "error",
      "message": "Control-Page는 켜져 있지만 Bot API(8798)가 꺼져 있습니다.",
      "suggestedActions": [
        {
          "id": "start_bot_api",
          "label": "Bot API 시작",
          "risk": "medium",
          "requiresConfirm": true
        }
      ]
    }
  ]
}
```

### Bot API Open But HTTP Slow

```json
{
  "ok": false,
  "overallState": "degraded",
  "summary": "Bot API 포트는 열려 있지만 상태 응답이 지연되고 있습니다.",
  "services": [
    {
      "id": "bot_api",
      "state": "partial",
      "ready": false,
      "port": 8798,
      "reason": "http_timeout"
    }
  ],
  "diagnostics": [
    {
      "code": "BOT_API_PARTIAL",
      "severity": "warning",
      "message": "Bot API 포트는 열려 있지만 HTTP 상태 응답이 타임아웃되었습니다.",
      "details": "기동 중이거나 큰 상태 payload 처리로 지연될 수 있습니다."
    }
  ]
}
```

## Testing Plan

### Manifest Tests

File:

```text
tests/runtime/test_service_manifest.py
```

Test cases:

- Manifest loads.
- Required service IDs exist.
- `control_page` default port is `8799`.
- `bot_api` default port is `8798`.
- `control_page` and `bot_api` cannot share a port.
- Environment overrides apply only to allowed fields.
- Unknown probe kind fails validation.

### Health Tests

File:

```text
tests/runtime/test_runtime_health.py
```

Test cases:

- TCP closed -> `down`.
- TCP open + HTTP success -> `up`.
- TCP open + HTTP timeout -> `partial`.
- Required service down -> overall not OK.
- Optional service down -> overall degraded or warning, not hard down.
- `control_page up + bot_api down` produces
  `BOT_API_DOWN_WITH_CONTROL_PAGE_UP`.

### Control-Page Contract Tests

File:

```text
tests/core/test_control_page_state_runtime_health.py
```

Test cases:

- `/api/control-page/state` still includes legacy `runtime.services`.
- State also includes `runtime.serviceHealth`.
- `botReady=true` cannot be paired with a diagnostic saying Bot API is down.
- Local-only mode does not emit Discord-specific fallback copy for local runtime
  failures.

### UI Tests

File:

```text
tests/ui/test_control_page_runtime_health_ui.py
```

Test cases:

- Runtime health block renders required services first.
- Down service renders a concise reason.
- Safe repair suggestion renders a button.
- High-risk action requires confirmation.
- Unknown new fields do not break existing UI.

### Launcher Contract Tests

Existing file can be extended:

```text
tests/runtime/test_shutdown_scripts.py
```

Test cases:

- Local launcher has separate `controlPagePublicPort` and `botApiPort`.
- Local launcher checks `botApiPort` before skipping `main.py`.
- Bot launcher respects `CONTROL_PAGE_BOT_API_PORT`.
- Local stop includes `8798` and `8799`.

## Rollout Plan

### Phase 0: Document and Freeze Terms

Deliverables:

- This blueprint.
- Evaluation document points to this design.
- Service names and port meanings agreed.

Exit criteria:

- `control_page`, `bot_api`, `main_llm`, `router_llm`, `sub_llm`, `tts`,
  `vision` IDs are stable.

### Phase 1: Read-Only Manifest

Deliverables:

- `service_manifest.json`.
- `runtime_services.py`.
- Manifest tests.

Exit criteria:

- Manifest can load and validate.
- No live runtime behavior changes.

### Phase 2: Read-Only Health

Deliverables:

- `runtime_health.py`.
- TCP/HTTP probes.
- Health tests with fake probes.

Exit criteria:

- Health summary can be produced without the Control-Page consuming it yet.

### Phase 3: Control-Page State Integration

Deliverables:

- `control_page_server.py` includes `runtime.serviceHealth`.
- `main.py` can optionally use the same health summary.
- Existing state consumers keep working.

Exit criteria:

- `/api/control-page/state` reports both legacy booleans and structured health.
- No contradictory ready/down messages.

### Phase 4: UI Display

Deliverables:

- Runtime Health panel/block.
- Diagnostics display.
- No repair execution yet.

Exit criteria:

- User can see exactly which core service is missing.
- `8799 up / 8798 down` is explained clearly.

### Phase 5: Dry-Run Repair

Deliverables:

- `runtime_repair.py`.
- `POST /api/control-page/runtime-repair` with `dryRun=true`.
- Suggested actions in diagnostics.

Exit criteria:

- UI can show what would be run.
- No process mutation by default.

### Phase 6: Confirmed Manual Repair

Deliverables:

- Safe start-if-down actions.
- Cooldowns.
- Repair log.
- Confirmation flow.

Exit criteria:

- User can start a missing Bot API from the page.
- High-risk restarts still require explicit confirmation.

## Risks

### Timeout Flapping

If timeouts are too low, services may oscillate between `up`, `partial`, and
`down`.

Mitigation:

- Use separate TCP and HTTP timeouts.
- Add short cache TTL.
- Show last successful check timestamp.
- Use `starting` grace state during boot.

### Manifest Drift

If launchers and manifest diverge, the manifest becomes another stale document.

Mitigation:

- Add tests that inspect launcher scripts.
- Eventually generate launcher constants from manifest or centralize service
  ports in a small shared helper.

### Repair Overreach

Repair actions can damage unrelated processes if too broad.

Mitigation:

- Start with dry-run only.
- Prefer start-if-down.
- Require Evelyn ownership proof before stopping anything.
- Respect OpenClaw/Codex exclusion rules.

### Local/Discord Mode Confusion

Local-only and Discord-enabled modes can have different expectations.

Mitigation:

- Include runtime mode in health summary.
- Use mode-aware diagnostic copy.
- Avoid Discord-specific text in local-only failures.

### Optional Service Noise

Vision, Voyager, and Codex Gateway may be optional. If they are marked too
severe, the page will look broken too often.

Mitigation:

- Distinguish `required`, `soft_required`, and `optional`.
- Make feature-specific panels show optional module failures.
- Keep top-level readiness based on core services.

## Open Questions

1. Should `vision` be required for the default local mode, or only when screen
   analysis is enabled?
2. Should `codex_gateway` appear in the main runtime health block or only under
   Voyager/developer diagnostics?
3. Should repair actions launch windows visibly by default?
4. Should `service_manifest.json` include GPU affinity/expected model metadata
   for LLM services, or should that live in a separate model manifest?
5. Should Tailscale Serve status be part of this layer or a separate remote
   access diagnostic?

Current recommendation:

- Keep Phase 1 focused on local service health only.
- Add Tailscale, GPU, and model metadata later.

## First Implementation Checklist

- [x] Add `evelyn_core/runtime/service_manifest.json`.
- [x] Add `runtime_services.py` loader and validation.
- [x] Add `tests/runtime/test_service_manifest.py`.
- [x] Add `runtime_health.py` with fake-probe-friendly design.
- [x] Add `tests/runtime/test_runtime_health.py`.
- [x] Add `runtime.serviceHealth` to Control-Page state.
- [x] Add state contract tests.
- [x] Add minimal UI display.
- [x] Add dry-run repair design only after health output is stable.
- [x] Add disabled apply endpoint for Phase 6 preparation.
- [x] Add JSONL repair audit log for blocked apply attempts.
- [x] Add confirmed manual repair execution after explicit operator approval.
- [x] Add cooldown persistence for execution phase.
- [x] Add Control-Page preview -> confirm -> apply flow.

## Success Criteria

This design is successful when the following are true:

1. `8799` and `8798` can no longer be accidentally merged without test failure.
2. The page can say "Control-Page is up, Bot API is down" explicitly.
3. The page can distinguish `port_closed` from `http_timeout`.
4. Required and optional services are not treated the same.
5. Existing UI and `/api/control-page/state` consumers do not break.
6. Repair suggestions are conservative and auditable.
7. The user no longer has to ask "근본 원인이 뭐야?" for common runtime states.

## Final Design Summary

The first useful version is not a repair bot. It is a truthful health layer.

The correct first target is:

```text
single manifest
 -> typed loader
 -> structured health summary
 -> clear diagnostics
 -> Control-Page display
```

Only after that should Evelyn gain repair buttons.

This keeps the project moving toward a more mature personal agent without
adding another unstable automation layer too early.
