# Host Vision Bridge Contract

Document status: **Current**
Last reviewed: 2026-07-31 KST

## Purpose

Docker services cannot capture the interactive Windows desktop. The Host Vision
Bridge is the narrow capability boundary that lets Evelyn request one
evidence-bearing observation from the Windows host without granting a container
an arbitrary host command, path, or screen-stream capability.

The bridge supports the goal that screen claims must come from current evidence.
It does not make every pixel-model or OCR result trustworthy.

## Ownership

- `host_vision_client.py`: writes one bounded request and consumes one response.
- `host_vision_contract.py`: owns schemas, exact keys, size limits, and TTLs.
- `host_vision_bridge.py`: claims requests, captures the screen, calls the
  deployed Vision service, records evidence metadata, and deletes transient
  files.
- `windows_foreground_context.py`: reads only the active window title and class.
- `windows_accessibility.py` and `invoke_windows_accessibility.ps1`: read a
  bounded, allowlisted Control View from the foreground Windows UI Automation
  tree and discard raw runtime IDs after deriving opaque element IDs.
- `windows_native_ocr.py` and `invoke_windows_ocr.ps1`: run the fixed Windows
  Runtime OCR operation against bridge-owned screenshots.
- `vision_runtime.py` and `vision_quality.py`: produce `vision.evidence.v2` and
  decide whether the result is actionable.

The Host Supervisor owns the bridge process. Docker communicates only through
`runtime_artifacts/host_vision/`.

## Request Contract

`host_vision.request.v1` has exactly these keys:

```json
{
  "schema": "host_vision.request.v1",
  "requestId": "32 lowercase hex characters",
  "createdAt": 1000.0,
  "expiresAt": 1180.0,
  "userText": "bounded user request",
  "runOcr": false
}
```

Invariants:

- Unknown or missing keys are rejected.
- `requestId` must match the filename.
- The request is at most 8 KiB, `userText` is at most 512 characters, and the
  request lifetime is at most 180 seconds.
- The request cannot specify a command, executable, argv, working directory,
  capture path, output path, URL, or model.
- The bridge atomically moves a request from `requests/` to `processing/`
  before handling it.

## Response Contract

`host_vision.response.v1` contains one bounded observation plus structured
evidence:

```json
{
  "schema": "host_vision.response.v1",
  "requestId": "32 lowercase hex characters",
  "createdAt": 1000.0,
  "expiresAt": 1180.0,
  "observation": "ephemeral prompt context",
  "evidence": {
    "schema": "vision.evidence.v2",
    "state": "observed",
    "evidence_available": true,
    "scene_available": true,
    "ocr_available": false,
    "confidence": "low",
    "actionable": false,
    "freshness": "live",
    "observedAt": 1000.0,
    "expiresAt": 1015.0,
    "ageSec": 0.2,
    "maxAgeSec": 15.0
  },
  "errorCode": "",
  "latencyMs": 0.0,
  "screenshotDeleted": true,
  "sceneChars": 0,
  "ocrChars": 0
}
```

The client rejects unknown keys, wrong schemas or IDs, responses older than 15
seconds, expired or overlong response lifetimes, oversized observations, and
contradictory evidence. The response file may remain in the queue for cleanup
for up to 180 seconds, but it stops being consumable evidence after 15 seconds.
Failure remains an explicit failed or unavailable result; it is never converted
into a successful screen claim.

## Evidence Sources and Trust

The bridge combines three different sources without pretending they have equal
confidence:

- Foreground window metadata is a structured Windows observation. Only the
  bounded title and class name are read; PID, process path, command line, and
  other windows are not collected.
- Windows UI Automation is the preferred exact-text source. The fixed observer
  reads only the foreground Control View and allowlisted structural controls
  such as Window, Button, Menu, Tab, Text, List, and Header. It does not read
  Edit or Document controls, Value/Invoke patterns, process IDs, paths, command
  lines, or background windows, and it cannot focus or activate an element.
- A UI Automation observation is fresh for at most five seconds, contains at
  most 120 elements, and is usable only while its title/class still matches the
  separately observed foreground window. Raw runtime IDs are never returned by
  the Python boundary; a one-way 20-character element ID is derived instead.
- Per-turn combined evidence is fresh for at most 15 seconds from screenshot
  capture completion. The bridge and client recompute freshness from timestamps
  instead of trusting a serialized `freshness=live` claim.
- When foreground metadata and UI Automation identify different windows, both
  conflicting structured sources are discarded. Screenshot scene/native OCR
  may be used only as a low-confidence, non-actionable fallback.
- Exact-text sufficiency is request-specific. A title request requires a window
  title or named title-like control; a button, menu, tab, checkbox, or radio
  request requires a named control of that type. Merely having some accessible
  text does not authorize a different requested claim.
- SmolVLM scene output is accepted only after quality checks. Empty, repeated,
  request-echo, and identity-only (`Evelyn` or `이블린`) outputs are rejected.
- Windows OCR uses the signed-in user's Windows Runtime OCR languages and tiled
  high-resolution input. Its text is currently unscored, so it is supporting
  low-confidence context and is not sufficient for exact-text actions.

`vision_ocr` is satisfied only when exact UI Automation text is foreground
bound, fresh, and sufficient for the requested control type. Native OCR remains
supporting context. When the user asks for an exact title, Fast Control copies
the title from the fixed observation before Main LLM generation. When exact
evidence is absent, it returns a deterministic refusal before calling the Main
LLM.

## Privacy and Retention

- Screenshots and OCR tiles are transient files under the bridge-owned
  `screenshots/` directory.
- Normal completion deletes the screenshot and every OCR tile immediately.
- The client deletes its request and response in `finally`.
- Stale or invalid evidence has its observation text and scene/OCR counts
  removed before the bridge writes a response and again before the client
  exposes a result.
- Bridge cleanup removes stale requests, processing files, and responses after
  180 seconds and stale screenshots after 300 seconds.
- `status.json` contains heartbeat, counters, latency, evidence metadata,
  character counts, and deletion state only. It does not contain screenshot
  pixels, accessibility names, OCR text, scene text, user text, or prompts.
- Raw screenshots and OCR text must not be added to reports, logs, benchmarks,
  or source control.

## Build and Start

The standard Windows launcher owns the complete path:

```powershell
$env:EVELYN_DOCKER_BUILD = "true"
$env:CONTROL_PAGE_AUTO_OPEN = "false" # optional
powershell -ExecutionPolicy Bypass `
  -File .\evelyn_core\runtime\launchers\start_local_background.ps1
```

When the project path contains non-ASCII characters, the launcher invokes
`build_local_docker_images.ps1`. It maps the project to an unused temporary
drive, builds only the allowlisted `bot_api`, `control_page`, and `vision`
images, verifies that the mapping still belongs to this project, and removes
it. Existing drive mappings are never reused or removed.

Before replacing a newly built Bot API image, the launcher stops the current
container with a 15-second grace period and waits for its Minecraft owner claim
to disappear. It refuses to recreate the Bot API while ownership is ambiguous.

## Verified Live Behavior

On 2026-07-30, the deployed local runtime was exercised through the actual
Control Page:

- The fixed UI Automation observer read the foreground SDL window title
  `테라리아: 모래는 OP다`.
- The Control Page exact-title question returned exactly
  `테라리아: 모래는 OP다`, with no LLM-added spacing, explanation, or
  reformatting. Host Vision recorded
  `reason_code=live_accessibility_observation`, `actionable=true`.
- The same SDL application exposed no named Button control. An exact button
  request therefore returned the deterministic no-evidence reply rather than
  borrowing the window title or inventing a button.
- Host scene and OCR requests reported `screenshotDeleted=true`.
- After each request, the request, processing, response, and screenshot queues
  were empty; only metadata-only `status.json` remained.

No click or other UI mutation is authorized by this read-only contract.
Applications that expose only a root window, including the tested SDL window,
continue to fail closed for button/menu requests. The separate
`UI_ACTION_TARGET_CONTRACT.md` now permits only an explicitly confirmed,
reobserved foreground `Button` `InvokePattern` with a verified postcondition.
That action contract has synthetic coverage only; a multi-application accuracy
corpus and live action verification are still required.
