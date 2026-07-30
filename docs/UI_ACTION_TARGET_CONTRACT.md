# UI Action Target Contract

Document status: **Current**
Last reviewed: 2026-07-31 KST

## Purpose

This contract is the first narrow boundary between Evelyn's read-only screen
observation and a Windows UI mutation. It permits one explicitly confirmed
Windows UI Automation `InvokePattern` call only when the same foreground
`Button` can be reidentified immediately before execution and a named
postcondition can be verified afterward.

It is not general desktop automation. It does not authorize coordinates,
keyboard input, text entry, arbitrary commands, background windows, retries, or
rollback.

## Ownership

- `ui_action_target.py` owns target binding, process-memory confirmation tokens,
  pre-execution reobservation, outcome verification, and the content-free audit.
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

## Authorization Flow

1. Preview reads a fresh foreground Windows Accessibility observation.
2. The requested opaque `elementId` must identify exactly one named, visible,
   enabled `Button`.
3. The manager binds the preview to:
   - foreground window title and class digest;
   - element ID, control type, automation ID, accessible name, and bounds;
   - exact action and postcondition.
4. A URL-safe confirmation token is held in process memory for at most 30
   seconds. It is not restored after restart.
5. Apply requires the exact token and a Control Page request carrying
   `userConfirmed=true` through the CSRF-protected public endpoint.
6. The token is consumed before revalidation. Expiry, reuse, stale observation,
   changed foreground, changed target, missing target, or disabled target
   prevents execution and cannot reuse the token.
7. The authorization audit writes and `fsync`s `execution_started` before the
   fixed executor is called. If the audit is unavailable, execution does not
   start.
8. The executor rechecks the expected foreground digest, walks at most 600 UIA
   nodes to depth 8, requires one exact element ID, and invokes it once.
9. A new observation must prove the selected postcondition. There is no
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
`host_ui_action.request.v1` has exactly these keys:

```json
{
  "schema": "host_ui_action.request.v1",
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

Apply uses `operation=apply`, sets only `confirmToken`, and leaves action,
element ID, and postcondition empty. Unknown or missing keys, invalid IDs,
overlong lifetimes, expired requests, arbitrary paths, command fields, or
unsupported values are rejected before observation or execution.

The host atomically claims one request at a time. Responses are exact-schema,
bounded, fresh for at most 10 seconds at the client, and deleted by the client
in `finally`. Host cleanup removes abandoned request/processing files after 15
seconds and responses after 30 seconds.

## Control Page API

- `GET /api/control-page/ui-action`
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

Both mutations use the existing Control Page CSRF/session boundary. Preview
shows the exact target name, control type, foreground window, action, and
postcondition. Apply remains a separate browser confirmation and is never
scheduled automatically.

## Privacy and Retention

- Target/window text and confirmation tokens exist only in transient
  request/response handling and the in-memory preview UI.
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
tampering, CSRF, queue cleanup, and retention.

No live UI action has been executed. Positive and negative corpus runs across
File Explorer, Chromium, Windows Settings, and WinUI applications are still
required. Applications that expose only a root window remain non-actionable.
Rollback and non-Button actions are outside the current boundary.
