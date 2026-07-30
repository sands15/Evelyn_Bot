# Host Vision Bridge Contract

Document status: **Current**
Last reviewed: 2026-07-30 KST

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
- `windows_native_ocr.py` and `invoke_windows_ocr.ps1`: run the fixed Windows
  Runtime OCR operation against bridge-owned screenshots.
- `vision_runtime.py` and `vision_quality.py`: produce `vision.evidence.v1` and
  decide whether the result is actionable.

The Host Supervisor owns the bridge process. Docker communicates only through
`runtime_artifacts/host_vision/`.

## Request Contract

`host_vision.request.v1` has exactly these keys:

```json
{
  "schema": "host_vision.request.v1",
  "requestId": "32 lowercase hex characters",
  "createdAt": 0.0,
  "expiresAt": 0.0,
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
  "createdAt": 0.0,
  "expiresAt": 0.0,
  "observation": "ephemeral prompt context",
  "evidence": {
    "schema": "vision.evidence.v1",
    "state": "observed",
    "evidence_available": true,
    "scene_available": true,
    "ocr_available": false,
    "confidence": "low",
    "actionable": false,
    "freshness": "live"
  },
  "errorCode": "",
  "latencyMs": 0.0,
  "screenshotDeleted": true,
  "sceneChars": 0,
  "ocrChars": 0
}
```

The client rejects unknown keys, wrong schemas or IDs, expired responses,
oversized observations, and contradictory evidence. Failure remains an explicit
failed or unavailable result; it is never converted into a successful screen
claim.

## Evidence Sources and Trust

The bridge combines three different sources without pretending they have equal
confidence:

- Foreground window metadata is a structured Windows observation. Only the
  bounded title and class name are read; PID, process path, command line, and
  other windows are not collected.
- SmolVLM scene output is accepted only after quality checks. Empty, repeated,
  request-echo, and identity-only (`Evelyn` or `이블린`) outputs are rejected.
- Windows OCR uses the signed-in user's Windows Runtime OCR languages and tiled
  high-resolution input. Its text is currently unscored, so it is supporting
  low-confidence context and is not sufficient for exact-text actions.

`vision_ocr` is satisfied only when OCR exists **and** the combined evidence is
actionable. When the user asks for an exact title or button and that condition
is absent, the Fast Control path returns a deterministic refusal before calling
the Main LLM.

## Privacy and Retention

- Screenshots and OCR tiles are transient files under the bridge-owned
  `screenshots/` directory.
- Normal completion deletes the screenshot and every OCR tile immediately.
- The client deletes its request and response in `finally`.
- Bridge cleanup removes stale requests, processing files, and responses after
  180 seconds and stale screenshots after 300 seconds.
- `status.json` contains heartbeat, counters, latency, evidence metadata,
  character counts, and deletion state only. It does not contain screenshot
  pixels, OCR text, scene text, user text, or prompts.
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

- A general screen question answered that Minecraft was open, matching the
  visible foreground application and structured Windows title.
- An exact-title/button question received the deterministic no-evidence reply
  because OCR was not actionable; an earlier hallucinated title/button answer
  was no longer possible.
- Host scene and OCR requests reported `screenshotDeleted=true`.
- After each request, the request, processing, response, and screenshot queues
  were empty; only metadata-only `status.json` remained.

The remaining limitation is exact UI semantics. A future Windows UI Automation
or accessibility-tree source needs its own scored, window-bound evidence
contract before it can authorize button-name or click-target claims.
