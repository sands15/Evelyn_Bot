# Runtime Health Snapshot Contract

Document status: **Current implementation contract**
Last reviewed: 2026-07-31 KST

## Purpose

The Control Page polls `/api/control-page/state` every 1.5 seconds. A state
request must not synchronously repeat every TCP and HTTP readiness probe while
Evelyn is also handling conversation, voice, or authorized actions.

Health information also must not become an unbounded cache that presents an
old `ready=true` value as current fact.

## Contract

Both the Fast Control Bot API and the public Control Page use
`RuntimeHealthSnapshotCache`.

- A successful snapshot is reusable for 2 seconds.
- The first consumer waits for one real collection.
- After 2 seconds, a consumer receives the latest snapshot immediately while
  one background refresh runs.
- Concurrent consumers share that refresh; they do not start duplicate probe
  collections.
- A snapshot may be served during refresh for at most 6 seconds.
- Beyond 6 seconds, the requesting consumer waits for the in-flight or newly
  started refresh.
- If that refresh fails, old readiness fails closed:
  - service state becomes `unknown`;
  - all legacy `*Ready` flags become false;
  - voice capabilities become `unknown` and `ready=false`;
  - blocker `runtime_health_cache_stale` is attached.
- A recent snapshot survives a transient refresh failure, but the public cache
  metadata reports the fixed code `runtime_health_refresh_failed`.
- Exception text is never copied into the public payload.

The 2-second and 6-second boundaries can be configured independently for the
Bot API and Control Page:

- `FAST_RUNTIME_HEALTH_REFRESH_SEC`
- `FAST_RUNTIME_HEALTH_MAX_STALE_SEC`
- `CONTROL_PAGE_RUNTIME_HEALTH_CACHE_TTL_SEC`
- `CONTROL_PAGE_RUNTIME_HEALTH_CACHE_MAX_STALE_SEC`

The maximum stale boundary may not be shorter than the refresh boundary.

## Public metadata

`runtime.controlPlane.healthCache` and
`runtime.serviceHealth.cache` expose bounded freshness evidence:

```json
{
  "schema": "runtime_health.cache.v1",
  "ageSec": 0.42,
  "stale": false,
  "refreshing": false,
  "refreshAfterSec": 2.0,
  "maxStaleSec": 6.0,
  "lastRefreshError": ""
}
```

The public Control Page may expose the compact subset needed for its control
plane status. `checkedAt` remains the collection timestamp; `generatedAt`
remains the response timestamp.

## Public projection boundary

The collector keeps full probe evidence in-process long enough to derive
service state, Minecraft functional readiness, voice capabilities,
diagnostics, and repair suggestions. Both cache owners project that internal
snapshot through `public_runtime_health_snapshot()` before it is attached to a
Control Page response.

The browser contract is `runtime_health.public.v1`. It retains service and
capability state, fixed reason/blocker codes, safe numeric timing/freshness
fields, boolean readiness dependencies, and allowlisted repair actions. It
does not expose:

- probe `target`, response `payload`, or exception `error` fields;
- service host/default-host/environment configuration;
- artifact paths, process IDs, output-device names, or raw upstream status;
- diagnostic details or arbitrary observability/legacy extension fields.

`legacyServices` and `observability.exceptions` are also rebuilt from closed
field lists. This prevents a future producer field from becoming public merely
because it was added to an internal snapshot. Runtime error class names remain
available only through their existing syntax-sanitized observability field;
exception messages and stack traces remain prohibited.

## Fresh operations

The cache is for repeated state composition and the state attached to ordinary
chat responses. It does not replace an explicitly requested investigation:

- `/status` performs a fresh runtime collection.
- runtime investigation tools perform a fresh collection.
- runtime health, repair preview/apply, and override endpoints use
  `force=True`.
- an override clears the Control Page snapshot before recollection.

## Verification boundary

Unit and integration coverage verifies:

- recent snapshot reuse;
- single-flight background refresh;
- maximum-stale blocking refresh;
- recent refresh failure preservation and error redaction;
- stale refresh failure fail-closed transformation;
- state-handler snapshot reuse and additive metadata;
- Control Page proxy and override behavior.
- public projection redaction while preserving computed readiness and repair
  decisions.

The deployed local runtime was measured with the optional model, Discord, and
Minecraft services intentionally deferred. Direct Bot API state polling moved
from p50 605 ms / p95 1,172 ms to p50 4.7 ms / p95 15.48 ms. The public 8799
path moved from p95 376.58 ms to p95 20.09 ms after both cache owners were
deployed. A first cold request may still wait for fresh evidence; this is the
intentional fail-closed boundary, not a readiness shortcut.
