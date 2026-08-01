# Minecraft World-Action Lease

Document status: **Current implementation contract**
Last reviewed: 2026-08-01 KST

## Purpose

Minecraft is an external, persistent world. Starting the agent, changing its
goal, or leaving it running after the approving process disappears must not be
treated like an ordinary read-only tool call.

Evelyn therefore requires one short-lived, process-owned lease before a
Minecraft runner may start or accept a goal.

## Contract

- One active lease exists globally. The Bot API is the single owner in the
  split Docker topology.
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
- A live owner refreshes the status and owner-claim heartbeats every 5 seconds.
- While no lease exists, the owner probes the deferred Minecraft service every
  30 seconds instead of on every heartbeat. Lease expiry is still checked on
  every 5-second tick, and explicit status or mutation requests are immediate.
  The Mindcraft service keeps its independent 5-second authorization guard.
- The owner also holds `owner_claim.json`. A second process cannot initialize
  as owner while that claim is fresh; a stale claim is eligible for takeover
  only after 15 seconds or three watchdog intervals, whichever is longer.
- Mindcraft and the legacy Voyager service reject `/start` and `/goal` unless
  the request proof exactly matches the active lease artifact and the
  process-rotated capability token.
- The service treats a heartbeat older than 15 seconds as unauthorized and
  stops an active runner through its own guard loop.
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
  `connect`, `goal`, and `disconnect` requests.
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

Mutations require `X-Evelyn-Minecraft-Lease-Token`. The dispatcher accepts no
command, argv, shell, or working-directory fields, and requests carrying a
browser `Origin` header are rejected. A Discord-process restart closes only its
remote poller; a Bot API owner restart rotates the token, discards the lease,
and reconciles any stale runner.

Authentication is checked before mutation error-state projection. An
unauthenticated `401` response contains only the fixed authorization error and
does not expose `leaseStatus` or any lease metadata. Authenticated failures may
carry the current sanitized `leaseStatus` so the remote can invalidate stale
authorization before raising the fixed error.

## Artifact and privacy contract

Active public status:

`runtime_artifacts/minecraft_world_lease/status.json`

Single-owner claim:

`runtime_artifacts/minecraft_world_lease/owner_claim.json`

Local audit journal:

`runtime_artifacts/minecraft_world_lease/events/*.jsonl`

Private process capability:

`runtime_artifacts/secrets/minecraft_world_lease.json`

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

Audit journals follow the runtime retention policy: 30 days, 20 MiB, while
preserving the newest seven files. The active status file is not a durable
authorization source; a new owner process overwrites it with
`authorization_required`.

## Verification boundary

Unit coverage includes lease issue, expiry, restart non-restore, competing-owner
rejection, stale-claim takeover, stale runner cleanup, status failure,
cross-guild rejection, authenticated typed delegation, token rotation, proof
mismatch, stale heartbeat, stop verification, retry exhaustion, privacy, and
retention. It also verifies that standby service probes are throttled without
slowing the owner-claim heartbeat and that only internal background status
failures are omitted from repetitive logs.

Live Docker/Minecraft verification is still required. A passing unit suite does
not prove that container scheduling, shared-volume heartbeat propagation,
Mindcraft child termination, or the real Microsoft-authenticated game session
meets the timing contract.

The 2026-08-01 durable-audit increment above is the current source contract.
The final source snapshot passed the bundled-Python Minecraft suite (115 tests,
7 environment skips), runtime suite (513 tests, 4 opt-in/environment skips),
and 39 adjacent Discord/Mindcraft/UI tests. No live Minecraft
connect/goal/stop E2E has been performed for it.
