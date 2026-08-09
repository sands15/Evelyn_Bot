# Vision Evidence Contract

Document status: **Current**
Last reviewed: 2026-08-09 KST

## Purpose

Evelyn must distinguish requesting a screen observation from actually obtaining
usable visual evidence. Policy hints, capture attempts, failure messages, and
background instructions are context, but they are not observations.

This contract covers the per-turn local screen capture path used by the main LLM.
Background vision-watch context remains explicitly soft context with its own TTL.

## Runtime Contract

`vision_runtime.py` records this object under
`metrics.meta.vision_evidence`:

```json
{
  "schema": "vision.evidence.v2",
  "state": "observed|unreliable|unavailable|failed|unknown",
  "reason_code": "live_observation|live_accessibility_observation",
  "evidence_available": true,
  "scene_available": true,
  "ocr_available": true,
  "confidence": "normal",
  "actionable": true,
  "freshness": "live",
  "observedAt": 1000.0,
  "expiresAt": 1015.0,
  "ageSec": 0.25,
  "maxAgeSec": 15.0
}
```

The following invariants are fail-closed:

- Only `state=observed`, `evidence_available=true`, `freshness=live`, and a
  valid unexpired timestamp window can satisfy a vision tool requirement.
- `observedAt` is recorded immediately after the screenshot capture returns.
  `expiresAt-observedAt` cannot exceed 15 seconds. A missing, non-finite,
  future, inverted, overlong, or expired window is fail-closed.
- `vision.evidence.v1` observed payloads are legacy evidence without a
  trustworthy capture timestamp. They normalize to
  `unreliable/legacy_evidence_without_timestamp` and cannot satisfy a tool.
- At least one of `scene_available` or `ocr_available` must also be true.
- `vision_ocr` additionally requires `ocr_available=true` and
  `actionable=true`; a scene description or unscored OCR string alone is not
  exact-text evidence.
- `reason_code=live_accessibility_observation` is emitted only when a fresh
  Windows UI Automation observation matches the separately captured foreground
  title/class and contains the named control type required by the request.
- Accessibility text from a mismatched foreground window is discarded.
  The conflicting foreground metadata is also discarded. A screenshot/native
  OCR fallback may remain scene evidence, but it is marked low confidence and
  non-actionable, so it cannot satisfy `vision_ocr`.
- Missing, unknown-schema, or internally contradictory evidence is normalized
  to unavailable evidence.
- `actionable=true` is impossible when usable evidence is absent.
- A capture or analysis exception must preserve the text turn, mark the
  required tool `failed_or_unavailable`, and instruct the model not to claim
  screen contents.
- If analysis completes after the 15-second capture window, scene/OCR/title
  text is removed before the Host Bridge response, client result, and LLM
  context are assembled. Only content-free reason, age, counts, and deletion
  status may remain.

## Prompt and Tool Boundary

The main-LLM context contains a `VISION_EVIDENCE_GATE` provenance summary.
Tool decisions are derived from the structured evidence object, never from
whether a vision-context string happens to be non-empty.

The actual scene/OCR observation may be present in the ephemeral prompt. Tool
decision evidence and context benchmark rows contain only provenance,
availability, confidence, freshness, and latency fields; they do not duplicate
screen scene or OCR text.

Background vision-watch analysis and capture failures use only fixed
`vision_analysis_failed:<exception-type>` or `vision_watch_failed:<exception-type>`
markers. Non-200 upstream response bodies are not read, and exception messages or
paths do not enter its durable state, Control Page projection, startup detail/log,
or soft LLM context.

## Observability

`context_pipeline_benchmarks.jsonl` records:

- `vision_requested`
- `vision_evidence_available`
- `vision_evidence_state`
- `vision_scene_available`
- `vision_ocr_available`
- `vision_actionable`
- `marks.vision_ready`

`vision_context=true` is retained as a compatibility field meaning that a
vision section was assembled. It does **not** prove that an observation was
available.

## Verification

The focused tests cover:

- disabled capture;
- black-frame capture failure;
- analysis failure and capture cleanup;
- successful live scene/OCR evidence;
- successful analysis with no usable evidence;
- scene-only evidence failing the OCR requirement;
- unscored native OCR remaining non-actionable;
- foreground-window evidence preserving a grounded application observation
  when an identity-only scene result is rejected;
- fresh, foreground-bound, request-sufficient UI Automation evidence becoming
  actionable exact text;
- changed-window and missing-control accessibility observations failing closed
  or falling back to non-actionable native OCR;
- source-conflict fallback discarding foreground/UIA state;
- capture-age expiry sanitizing stale scene/OCR text;
- legacy v1, missing timestamp, future/inverted/overlong timestamp, and stale
  host response rejection;
- exact window-title replies bypassing Main LLM generation, while native OCR
  cannot enter that deterministic copy path;
- missing and contradictory contracts failing closed;
- unexpected runtime exceptions degrading without losing the text turn;
- benchmark serialization without scene/OCR content.

The 2026-07-30 live Windows E2E verified both sides of the exact-text gate. A
foreground SDL title was copied verbatim as `테라리아: 모래는 OP다` with
`live_accessibility_observation`, while a button request against the same
root-only accessibility tree returned the deterministic no-evidence reply
before Main LLM generation. See
`docs/HOST_VISION_BRIDGE_CONTRACT.md` for the host boundary and privacy proof.
