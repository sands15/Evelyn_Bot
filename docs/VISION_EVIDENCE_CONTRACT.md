# Vision Evidence Contract

Document status: **Current**
Last reviewed: 2026-07-30 KST

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
  "schema": "vision.evidence.v1",
  "state": "observed|unreliable|unavailable|failed|unknown",
  "reason_code": "live_observation",
  "evidence_available": true,
  "scene_available": true,
  "ocr_available": true,
  "confidence": "normal",
  "actionable": true,
  "freshness": "live"
}
```

The following invariants are fail-closed:

- Only `state=observed` with `evidence_available=true` can satisfy a vision
  tool requirement.
- At least one of `scene_available` or `ocr_available` must also be true.
- `vision_ocr` additionally requires `ocr_available=true`; a scene description
  alone is not OCR evidence.
- Missing, unknown-schema, or internally contradictory evidence is normalized
  to unavailable evidence.
- `actionable=true` is impossible when usable evidence is absent.
- A capture or analysis exception must preserve the text turn, mark the
  required tool `failed_or_unavailable`, and instruct the model not to claim
  screen contents.

## Prompt and Tool Boundary

The main-LLM context contains a `VISION_EVIDENCE_GATE` provenance summary.
Tool decisions are derived from the structured evidence object, never from
whether a vision-context string happens to be non-empty.

The actual scene/OCR observation may be present in the ephemeral prompt. Tool
decision evidence and context benchmark rows contain only provenance,
availability, confidence, freshness, and latency fields; they do not duplicate
screen scene or OCR text.

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
- missing and contradictory contracts failing closed;
- unexpected runtime exceptions degrading without losing the text turn;
- benchmark serialization without scene/OCR content.

Real Windows capture plus the deployed vision service still requires a live E2E
check before release.
