# Minecraft World-Action Lease

Document status: **Current implementation contract**
Last reviewed: 2026-08-09 KST

## Purpose

Minecraft is an external, persistent world. Starting the agent, changing its
goal, or leaving it running after the approving process disappears must not be
treated like an ordinary read-only tool call.

Evelyn therefore requires one short-lived, process-owned lease before a
Minecraft runner may start or accept a goal.

## Contract

- One active lease exists globally. The Bot API is the single owner in the
  split Docker topology.
- A stable `owner_claim.lock` held with an exclusive OS lock for the entire
  owner-process lifetime is the sole owner authority. `owner_claim.json` and
  its timestamp are diagnostic heartbeat state, not a takeover primitive. Its
  process nonce additionally fences published status/proof epochs, but does
  not grant another process the right to become owner.
- A live process is never replaced because its claim heartbeat appears old.
  Clean shutdown releases the kernel lock only after shielded cleanup; process
  exit or crash releases it through the OS. A successor that acquires the lock
  rotates the process nonce and capability token and never restores a lease.
- A second stable `world_action.lock` serializes owner epoch publication and
  shutdown with service effects. Mindcraft/Voyager hold it from proof
  validation through the `/start` or `/goal` effect commit; a successor holds
  it while publishing a new epoch, and shutdown waits for it before the final
  stop, artifact fence, and owner-lock release. Service-side busy or
  unavailable locks fail with a fixed 503 error, so an already-validated old
  proof cannot cross handoff or run after a verified shutdown stop.
  Mindcraft `/action` transfers the already-acquired exact lock capability to
  its action gateway and retains that same OS lock from proof validation and
  projector arm through accepted/running, effect verification, terminal
  runtime stop, durable result publication, and final release. Poll and cancel
  operate on the retained capability instead of reacquiring the lock.
  Mindcraft background reconciliation also acquires this lock before reading
  the guarded lease snapshot and keeps it through stop or ensure-start. An
  endpoint may reuse only an already-acquired capability for this exact lock
  path; busy, unavailable, unacquired, or forged capabilities fail closed.
- The default TTL is 1 hour; the maximum is 4 hours.
- A lease belongs to one guild/context and one issuing process nonce.
- An owner-process restart never restores the previous lease.
- Every status/proof consumer requires both `auditReady` and `statusReady` to
  be the exact boolean `true`. Missing, false, or non-boolean values are not
  degraded authorization. An invalid audit boundary is rejected as
  `minecraft_world_lease_audit_unavailable`; an invalid status publication
  boundary is rejected as `minecraft_world_lease_status_write_failed`.
- Owner initialization does not publish a delegation capability until its
  `process_started` audit row has been appended, flushed, and `fsync`ed.
  Lease issue, verified runtime start, goal attempt, and verified goal result
  cross the same durable audit boundary.
- Losing the audit boundary removes the active lease and process capability,
  deletes the shared private capability artifact when possible, and reports
  `manual_intervention_required`. Initialization, lease issue, runner start,
  and goal mutation then fail closed.
- Failure to commit the public status artifact also clears the lease and
  delegation capability, reports `minecraft_world_lease_status_write_failed`
  with `manual_intervention_required`, and force-stops any runtime that may
  already be active. A stale status file by itself is never sufficient proof
  because the private capability is simultaneously withheld.
- If neither the secret nor diagnostic claim can be changed, the owner keeps
  `world_action.lock` closed through the 31-second maximum clock-skew plus
  heartbeat-stale window before releasing the lifetime lock. Cancellation
  cannot shorten that wait. Bot API container replacement and Compose shutdown
  both allow 60 seconds so this fail-closed grace and runtime cleanup can finish.
- Process nonces are exact canonical strings. Whitespace-padded status, claim,
  secret, or proof nonces are rejected instead of normalized differently by
  owner and consumer code.
- A live owner refreshes the status and owner-claim heartbeats every 5 seconds.
- While no lease exists, the owner probes the deferred Minecraft service every
  30 seconds instead of on every heartbeat. Lease expiry is still checked on
  every 5-second tick, and explicit status or mutation requests are immediate.
  The Mindcraft service keeps its independent 5-second authorization guard.
- Mindcraft and the legacy Voyager service reject `/start` and `/goal` unless
  the request proof exactly matches the active lease artifact and the
  process-rotated capability token.
- The service treats a heartbeat older than 15 seconds as unauthorized and
  stops an active runner through its own guard loop. This is a service/status
  safety boundary, not an owner-lock takeover delay.
- `/stop` remains always available so authorization failure cannot prevent a
  safe shutdown.
- Lease revocation, explicit stop, watchdog stop, and owner shutdown remain
  safety-executable when audit storage is unavailable. A confirmed physical
  stop does not become an audited success in that state: the caller/status
  still reports `minecraft_world_lease_audit_unavailable` and
  `manual_intervention_required` for operator review.
- The owner independently reconciles startup, shutdown, lease expiry, status
  failure, and unexpected runner activity. Unknown status is fail-closed.
- Stop is successful only when both the stop outcome marker and a fresh
  post-stop runtime status show no running or connected session.
- Automatic stop retries are limited to three per ten minutes. Exhaustion is
  surfaced as `manual_intervention_required`.

## Typed one-shot action gateway

The only current autonomy action is `minecraft:find_food_source`, bound to
contract `mindcraft_food_recovery.v1`, postcondition `food_reserve_ready`, and
outcome evidence `minecraft_find_food_source_completed`. The request, dispatch,
and result use exact schemas `minecraft_autonomy.action-request.v1`,
`minecraft_autonomy.action-dispatch.v1`, and
`minecraft_autonomy.action-result.v1`. Unknown actions, extra fields, non-empty
parameters, raw goal/command/code/argv, coordinates, inventory, transcript, and
uncorrelated guild/grant/run/lease identifiers fail closed.

Mindcraft exposes only these typed gateway operations:

- `POST /action` with exact `{request, worldLease}` dispatches one bound action.
- `GET /action/{goalRunId}` polls the sanitized accepted/running/terminal
  projection.
- `POST /action/cancel` with exact `{request, worldLease}` cancels only the
  currently active request when every bound field matches.

Dispatch acquires `world_action.lock` before validating the proof and request.
Once admitted, the HTTP handler deliberately does not release it: the gateway
owns the full action interval. It arms a non-restored, durable effect binding,
starts the fixed food-recovery runner, and retains the lock until one of these
terminal paths completes:

- A fresh, same-binding content-free candidate proves the exact false-to-true
  postcondition. The projector fsyncs `effect_verified`, the gateway stops the
  runner, persists the exact verified result, then releases the action lock.
- Timeout, lease/readiness loss, invalid telemetry, runtime exit, audit/status
  failure, or another guard failure disarms the binding and requests a runner
  stop. Only a verified stopped process may produce a fixed failed terminal and
  release the lock.
- Exact cancellation disarms the binding, stops the runner, persists
  `cancelled/minecraft_action_cancelled`, then releases the retained lock. It
  never tries to reacquire its own action lock.

If `stop()` raises, the child still reports alive, or process liveness cannot be
verified, the gateway does not publish a terminal record and does not release
`world_action.lock`. It retains the active request/binding, marks itself
unavailable with a fixed stop error, and rejects new dispatch plus mismatched
cancel. Only an exact cancel for that active request may retry the stop; the
lock is released only after that retry proves the child dead. The same rule is
used during gateway shutdown.

While a gateway action is active, generic Mindcraft `/stop` returns the fixed
world-action-lock-busy failure; cancellation must go through the correlated
action path. `actionRunId` and `goalRunId` are replay-fenced in the gateway
status and world-effect journal. A service restart converts persisted
accepted/running records to
`failed/minecraft_action_authority_lost_on_restart` only after durable process
identity proves that the previous Mindcraft child is no longer alive. It never
resumes the action or restores an armed effect binding. Missing or corrupt
identity, a failed stop, or unverifiable liveness leaves the gateway
unavailable with a fixed prior-process error and requires manual intervention;
it publishes no terminal or repeat-ready state and admits no new action.

The owner writes `action_dispatch_attempted` before transport, then
`action_dispatch_verified` only after validating the exact accepted/running
acknowledgement. Poll completion requires the exact verified result before it
writes `action_completed`. Cancellation writes `action_cancel_attempted` and
requires an exact cancelled acknowledgement before `action_cancel_verified`.
Transport cancellation, timeout, correlation failure, or audit failure does
not become success; the owner revokes the affected lease and performs a
shielded safety stop when the terminal edge cannot be verified.
The Discord-side executor retains the exact in-flight action correlation until
either the bound cancelled acknowledgement or the owner disconnect/lease-revoke
fallback is verified. If both cannot be verified, autonomy stop remains failed
and retryable instead of reporting `idle`.

Published successful, failed, and cancelled terminal actions all leave the
Mindcraft runner verifiably stopped. A further action in the same owner/service process is admitted only
through the exact content-free terminal gateway projection with
`repeatActionReady=true`, and only for an executor that previously passed the
ordinary lease plus seven-dependency readiness boundary. This repeat signal
does not authorize a first connection, a new guild, or a new grant.

Owner shutdown first marks the owner as shutting down so no new action can be
dispatched. It then performs cancellation-resistant cancellation of every
known in-flight action before trying to acquire `world_action.lock` for final
lease revocation and runtime cleanup. This ordering prevents waiting on a lock
held by the very action being cancelled. If exact cancellation cannot be
verified, the owner uses bounded lock waiting for the known action, withholds
delegation authority, crosses the stale-artifact fence when necessary, and
safety-stops the runtime; it reports `minecraft_action_cancel_unverified` or a
fixed lock error instead of an audited success. The remote delegate follows the
same cancellation-first rule and falls back to authorized disconnect when its
tracked action cannot be cancelled.

## Authorization entry points

- Discord mutating commands require the existing owner/admin check and then
  delegate to the Bot API owner.
- The monolithic Control Page tool composition issues the lease with source
  `control_page`.
- The split Fast Control Bot API owns the lease. Its Control Page command
  surface can therefore connect, change a goal, and disconnect without
  competing with Discord.
- Discord uses `MINECRAFT_WORLD_LEASE_OWNER_URL` and the process-rotated token
  from the shared secrets mount. It polls public state and sends only typed
  `connect`, `goal`, `disconnect`, `action`, `action_status`, and
  `cancel_action` requests.
- The remote delegate accepts an active cached lease only from an exact status
  with both readiness booleans true. A missing or invalid `leaseStatus`, an
  error response without a valid authoritative status, transport failure, or
  request cancellation immediately replaces any cached active state with an
  inactive error state.
- The Windows Local I/O Bridge does not own the lease. Its old lazy-start queue
  remains fail-closed and tells the operator to use an authorized entry point.
- Legacy `start_voyager_task.ps1` and `VOYAGER_AUTO_START` no longer start a
  runner directly.

Internal delegation endpoints:

- `GET /internal/minecraft-world-lease` exposes public lease state.
- `POST /internal/minecraft-world-lease/connect`
- `POST /internal/minecraft-world-lease/goal`
- `POST /internal/minecraft-world-lease/disconnect`
- `POST /internal/minecraft-world-lease/action`
- `POST /internal/minecraft-world-lease/action_status`
- `POST /internal/minecraft-world-lease/cancel_action`

Mutations require `X-Evelyn-Minecraft-Lease-Token`. The dispatcher accepts no
command, argv, shell, or working-directory fields, and requests carrying a
browser `Origin` header are rejected. A Discord-process restart closes only its
remote poller; a Bot API owner restart rotates the token, discards the lease,
and reconciles any stale runner.
Every mutation also requires `guildId` to be an exact nonnegative JSON integer;
booleans, floats, strings, missing values, and negative integers are rejected
before any owner method is called.

The action delegation payloads are also exact. `action` accepts only `guildId`
and the unbound typed request; the owner adds the goal-run and lease epoch.
`action_status` requires `guildId`, `goalRunId`, `actionRunId`, `actionKey`, and
`contractCode`; `cancel_action` requires only `guildId` and `actionRunId`, then
resolves the original bound request from owner-held in-flight state. Poll and
cancel cannot substitute caller-supplied lease or result fields.

Authentication is checked before mutation error-state projection. An
unauthenticated `401` response contains only the fixed authorization error and
does not expose `leaseStatus` or any lease metadata. Authenticated failures may
carry the current sanitized `leaseStatus` so the remote can invalidate stale
authorization before raising the fixed error.

## Artifact and privacy contract

Active public status:

`runtime_artifacts/minecraft_world_lease/status.json`

Process-lifetime single-owner authority:

`runtime_artifacts/minecraft_world_lease/owner_claim.lock`

Proof-admission/effect and owner-handoff serialization:

`runtime_artifacts/minecraft_world_lease/world_action.lock`

Diagnostic owner heartbeat and published-epoch fence:

`runtime_artifacts/minecraft_world_lease/owner_claim.json`

Local audit journal:

`runtime_artifacts/minecraft_world_lease/events/*.jsonl`

Private process capability:

`runtime_artifacts/secrets/minecraft_world_lease.json`

Durable content-free Mindcraft action replay fence:

`runtime_artifacts/mindcraft_action_gateway/status.json`

Durable content-free world-effect observer projection and journal:

- `runtime_artifacts/mindcraft_world_effect/status.json`
- `runtime_artifacts/mindcraft_world_effect/events/*.jsonl`

The stable lock files are never replaced or unlinked during handoff. The held OS
lock, not file contents, freshness, PID, or `owner_claim.json`, proves the right
to act as owner; the shorter action lock serializes proof admission/effect with
epoch publication but does not elect an owner. The diagnostic claim may be
atomically refreshed while the lifetime lock remains held. Status/proof
consumers also require its process nonce
to match the published status epoch, so a mismatched old artifact fails closed;
that fencing check never authorizes takeover. A normal shutdown closes the lock
after runtime cleanup; an abnormal process exit relies on kernel lock release.
After acquiring it, a new process publishes a new nonce/token epoch with no
active lease.

The public status contains lease ID, guild/context ID, source, timestamps, and
the process nonce used to reject stale requests. It does not expose the
capability token or issuer reference. The local journal may store a normalized
issuer reference for audit. The private token rotates on owner initialization
and is shared only through the existing runtime secrets mount. No lease
artifact stores the raw goal, arguments, transcript, or Minecraft chat.

`status.json` exposes `auditReady` and `statusReady`, and authorization
consumers accept the lease only when both values are exactly `true`. Each
JSONL event row is considered recorded only after the file stream is flushed
and `fsync` succeeds. On POSIX, creating a new daily journal also requires the
events directory entry to sync before success is returned. Failure to append
or durably sync a required row is therefore an authorization failure, not a
best-effort observability warning.
A status artifact replacement failure is a separate fail-closed boundary: it
withholds the capability and triggers a shielded safety stop instead of leaving
an apparently usable in-memory lease. Audit-loss status and events remain
content-free: they do not copy the raw goal, transcript, Minecraft chat,
arguments, token, or arbitrary exception text.

The action gateway status stores at most the bounded exact dispatch/result
records needed to reject replay. The world-effect artifacts store only binding
identifiers, fixed contract/evidence/postcondition codes, sequence numbers,
timestamps, and boolean transition flags. Neither artifact stores the fixed
internal task text, raw model/Minecraft result, inventory, position, player,
chat, or transcript. Projector status is not usable after restart as an armed
authority: its process nonce rotates and the previous binding is fenced before
the new process reports readiness.

Audit journals follow the runtime retention policy: 30 days, 20 MiB, while
preserving the newest seven files. The active status file is not a durable
authorization source; a new owner process overwrites it with
`authorization_required`.

## Verification boundary

Unit coverage includes lease issue, expiry, restart non-restore, competing-owner
rejection, process-lifetime exclusion, crash-lock release, validation-to-effect
handoff serialization, adversarial owner refresh/status/release interleavings,
stale runner cleanup, status failure,
cross-guild rejection, authenticated typed delegation, token rotation, proof
mismatch, stale heartbeat, stop verification, retry exhaustion, privacy, and
retention. It also verifies that standby service probes are throttled without
slowing the owner-claim heartbeat and that only internal background status
failures are omitted from repetitive logs. Focused action coverage additionally
exercises exact dispatch/poll/cancel correlation, full-interval action-lock
ownership, replay rejection and restart fencing, projector transition guards,
terminal runtime stop, cancellation-first local/remote shutdown, and the
bounded known-action shutdown fallback.

Live Docker/Minecraft verification is still required. A passing unit suite does
not prove that Docker Desktop bind mounts preserve the required cross-container
byte-range lock/POSIX `flock` coherence and crash release, or that container
scheduling, shared-volume heartbeat propagation, Mindcraft child termination,
and the real Microsoft-authenticated game session meet the timing contract.
No user-approved live run has yet proved that `minecraft:find_food_source`
causes the real world to cross `food_reserve_ready`, publishes one matching
effect, returns one verified outcome, and shuts the runner down under the same
grant/action/goal/lease chain. Until that E2E and the cross-container lock test
exist, the one-shot action path is source-implemented but not operationally
complete.

The 2026-08-01 durable-audit boundary remains part of the current source
contract. Its preceding snapshot passed the bundled-Python Minecraft suite
(115 tests, 7 environment skips), runtime suite (513 tests, 4 opt-in/environment
skips), and 39 adjacent Discord/Mindcraft/UI tests. Those counts predate the
process-lifetime lock increment and are not its verification result. No live
cross-container lock handoff or Minecraft connect/goal/stop E2E has been
performed for the current increment.

The current lifetime-lock source passed 156 bundled-Python Minecraft tests
(8 environment skips) and 518 runtime tests (4 skips). A follow-up source
verification cleanup aligned the stale opaque note-ID expectation with the
privacy contract, closed the Windows SQLite connection lifetime across setup
failures and cache-hit early returns, and decoupled Voyager's lightweight text
index import from optional runtime dependencies. Full discovery then passed all
2,482 tests with 18 skips. Python compilation, every Control Page asset
JavaScript syntax check, and `git diff --check` passed. The mixed
bundled/.venv `pip check` still reports six pre-existing platform-tag issues.
These are source checks, not live main/Minecraft/container evidence.

The follow-up auto-reconcile TOCTOU increment passed 18 Mindcraft tests, 157
Minecraft tests with 8 environment skips, and full repository discovery of
2,503 tests with 18 skips. Its adversarial tests cover shutdown handoff during
the guarded read-to-effect interval and forged lock capabilities. They remain
source-level evidence and do not replace a live two-process/container handoff
or a real Minecraft world effect.
