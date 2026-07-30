# UI Action Target Contract

Document status: **Current**
Last reviewed: 2026-07-31 KST

## Purpose

This contract is the first narrow boundary between Evelyn's read-only screen
observation and a Windows UI mutation. It permits one explicitly confirmed
Windows UI Automation `InvokePattern` call only when the same foreground
`Button` can be reidentified immediately before execution and a named
postcondition can be verified afterward.

It also exposes an explicit read-only discovery step so the operator can choose
an opaque target ID without copying it from a developer tool. Discovery returns
at most 24 named, visible, enabled foreground Buttons and grants no execution
authority.

It is not general desktop automation. It does not authorize coordinates,
keyboard input, text entry, arbitrary commands, background windows, retries, or
rollback.

## Ownership

- `ui_action_target.py` owns target binding, process-memory confirmation tokens,
  bounded target discovery, pre-execution reobservation, outcome verification,
  and the content-free audit.
- `windows_accessibility_invoke.py` owns the fixed PowerShell executor contract.
- `invoke_windows_accessibility_action.ps1` walks the current foreground UIA
  Control View and invokes exactly one matching visible, enabled `Button`.
- `host_ui_action_contract.py`, `host_ui_action_bridge.py`, and
  `host_ui_action_client.py` own the exact-schema Docker/Windows queue.
- `local_io_bridge.py` owns the Host UI Action Bridge task because it runs in the
  signed-in Windows desktop session.
- `fast_control_api.py` and `control_page_server.py` expose the bounded status,
  preview, and apply routes.
- `docs/assets/evelyn-ui-action.js` owns the human confirmation UI.

## Allowed Scope

The current allowlist is intentionally small:

```json
{
  "actions": ["invoke"],
  "controlTypes": ["Button"],
  "postconditions": [
    "target_absent",
    "target_disabled",
    "window_changed"
  ],
  "automaticRetry": false,
  "arbitraryCoordinates": false,
  "arbitraryCommands": false
}
```

Menus, tabs, checkboxes, text fields, keyboard shortcuts, pointer coordinates,
window activation, and process launch are not accepted by this contract.
Discovery is read-only and does not expand this action allowlist.

## Authorization Flow

1. An explicitly armed discovery may read one fresh foreground Windows
   Accessibility observation. It returns at most 24 named, enabled Buttons,
   creates no token, and does not persist target or window text.
2. After the operator selects one returned Button, preview reads a new fresh
   foreground Windows Accessibility observation.
3. The requested opaque `elementId` must identify exactly one named, visible,
   enabled `Button`.
4. The manager binds the preview to:
   - foreground window title and class digest;
   - element ID, control type, automation ID, accessible name, and bounds;
   - exact action and postcondition.
5. A URL-safe confirmation token is held in process memory for at most 30
   seconds. It is not restored after restart.
6. Apply requires the exact token and a Control Page request carrying
   `userConfirmed=true` through the CSRF-protected public endpoint.
7. The token is consumed before revalidation. Expiry, reuse, stale observation,
   changed foreground, changed target, missing target, or disabled target
   prevents execution and cannot reuse the token.
8. The authorization audit writes and `fsync`s `execution_started` before the
   fixed executor is called. If the audit is unavailable, execution does not
   start.
9. The executor rechecks the expected foreground digest, walks at most 600 UIA
   nodes to depth 8, requires one exact element ID, and invokes it once.
10. A new observation must prove the selected postcondition. There is no
   automatic retry.

## Result Semantics

- `verified`: execution occurred and the exact postcondition was observed.
- `execution_failed`: execution did not occur.
- `outcome_unverified`: execution occurred, but the postcondition was not
  proven. This is a failure, not success, and must not be retried
  automatically.
- `authorization_audit_unavailable`: the durable authorization/outcome audit
  could not be completed. No new action is allowed. If this happens after the
  executor returned, the result still preserves whether execution occurred.

Only `verified` has `ok=true`. A non-empty error string or executor success
alone is never sufficient.

## Queue Contract

Docker writes only to `runtime_artifacts/host_ui_action/`.
`host_ui_action.request.v2` has exactly these keys:

```json
{
  "schema": "host_ui_action.request.v2",
  "requestId": "32 lowercase hex characters",
  "createdAt": 1000.0,
  "expiresAt": 1015.0,
  "operation": "preview",
  "action": "invoke",
  "elementId": "20 lowercase hex characters",
  "postcondition": "target_absent",
  "confirmToken": ""
}
```

Discovery uses `operation=discover` and requires `action`, `elementId`,
`postcondition`, and `confirmToken` to all be empty. Preview uses
`operation=preview` with an allowed action, exact element ID, and allowed
postcondition. Apply uses `operation=apply`, sets only `confirmToken`, and
leaves action, element ID, and postcondition empty.

`host_ui_action.response.v2` adds an exact `targets` object alongside the
mutually exclusive `preview` and `result` objects. A successful discovery must
contain `ui_action.targets.v1`, one bounded foreground identity, zero to 24
unique enabled Button targets, and policy markers proving that preview and
explicit confirmation are still required. Disabled, duplicate, malformed, or
over-limit targets fail closed at the Docker client.

Unknown or missing keys, invalid IDs, overlong lifetimes, expired requests,
arbitrary paths, command fields, or unsupported values are rejected before
observation or execution.

The host atomically claims one request at a time. Responses are exact-schema,
bounded, fresh for at most 10 seconds at the client, and deleted by the client
in `finally`. Host cleanup removes abandoned request/processing files after 15
seconds and responses after 30 seconds.

## Control Page API

- `GET /api/control-page/ui-action`
- `POST /api/control-page/ui-action/targets`

  ```json
  {}
  ```

- `POST /api/control-page/ui-action/preview`

  ```json
  {
    "elementId": "20 lowercase hex characters",
    "action": "invoke",
    "postcondition": "target_absent"
  }
  ```

- `POST /api/control-page/ui-action/apply`

  ```json
  {
    "confirmToken": "preview token",
    "userConfirmed": true
  }
  ```

All three POST routes use the existing Control Page CSRF/session boundary.
Discovery, preview, and apply each require the operator to arm a separate
five-second foreground handoff. Discovery fills a transient Button selector;
preview shows the exact selected target name, control type, foreground window,
action, and postcondition. The browser sends one request when the armed
deadline arrives, never before explicit arming. The countdown can be cancelled,
and a callback that wakes more than two seconds late sends no request. Apply
also requires the separate browser confirmation before it can be armed.

The handoff does not activate or choose a window. The operator must return to
the intended foreground window. Apply consumes its token before reobservation,
so a changed foreground or target fails closed and is never retried.

## Reversible Positive Fixture

`evelyn_core/runtime/launchers/show_ui_action_test_fixture.ps1` provides one
named UIA `Button`, `Evelyn Safe Invoke Test`, for an explicitly approved live
positive check. Invoking the target disables it, which proves the
`target_disabled` postcondition. A separate `LinkLabel` resets the target
manually; it is not an allowed Button action target.

The fixture writes only `state`, timestamp, enabled state, expected
postcondition, and privacy/reversibility flags to
`runtime_artifacts/ui_action_fixture/status.json`. It does not call the
production action boundary and does not retain target or window text. Launch
it manually in an STA Windows PowerShell session:

```powershell
powershell.exe -NoProfile -STA -File .\evelyn_core\runtime\launchers\show_ui_action_test_fixture.ps1
```

## Privacy and Retention

- Target/window text and confirmation tokens exist only in transient
  request/response handling and the in-memory discovery/preview UI.
- `status.json`, `authorization.json`, and audit JSONL do not store accessible
  names, window titles, element IDs, commands, coordinates, or screen content.
- Audit rows store timestamps, operation IDs, action/postcondition codes, a
  one-way target digest, reason codes, and executed/verified booleans.
- Audit JSONL keeps the newest seven files and becomes a cleanup candidate after
  30 days or when the set exceeds 20 MiB.

## Current Verification Boundary

The contract has synthetic coverage for stale observations, expiry, token
reuse, process restart, changed windows, changed/disabled targets, duplicate
identities, executor contract mismatch, postcondition failure, response
tampering, bounded discovery, disabled discovered targets, discovery without
authority, CSRF, queue cleanup, and retention.

The contract is deployed in the local Bot API, Control Page, and Windows Local
I/O Bridge. A live negative request with a well-formed nonexistent element ID
crossed the public CSRF boundary and host queue, reobserved the foreground, and
returned `ui_action_target_missing` without calling the executor. The three
queues were empty afterward, the content-free audit recorded only
`process_started` and `action_denied`, and execution count remained zero. The
deployed browser panel reported `running` with no warning/error console logs.

No live UI action has been executed. Positive and broader negative corpus runs
across File Explorer, Chromium, Windows Settings, and WinUI applications are
still required. The Control Page now has explicit, cancellable five-second
foreground handoffs for discovery, preview, and confirmed apply, and the
reversible fixture is available for the first positive check. Discovery and
selection are deployed with synthetic/API coverage; the deployed API rejected
missing CSRF and extra command fields before Host observation, while the
browser rendered the discovery control and disabled pre-discovery selector
without warning/error logs. A valid live discovery has not been sent, the
fixture has not been launched, and no live action has executed. Applications
that expose only a root window remain non-actionable. General rollback and
non-Button actions are outside the current boundary.
