# Minecraft World-Action Lease

Document status: **Current implementation contract**
Last reviewed: 2026-07-30 KST

## Purpose

Minecraft is an external, persistent world. Starting the agent, changing its
goal, or leaving it running after the approving process disappears must not be
treated like an ordinary read-only tool call.

Evelyn therefore requires one short-lived, process-owned lease before a
Minecraft runner may start or accept a goal.

## Contract

- One active lease exists globally.
- The default TTL is 1 hour; the maximum is 4 hours.
- A lease belongs to one guild/context and one issuing process nonce.
- A bot restart never restores the previous lease.
- A live owner refreshes the status heartbeat every 5 seconds.
- Mindcraft and the legacy Voyager service reject `/start` and `/goal` unless
  the request proof exactly matches the active lease artifact and the
  process-rotated capability token.
- The service treats a heartbeat older than 15 seconds as unauthorized and
  stops an active runner through its own guard loop.
- `/stop` remains always available so authorization failure cannot prevent a
  safe shutdown.
- The owner independently reconciles startup, shutdown, lease expiry, status
  failure, and unexpected runner activity. Unknown status is fail-closed.
- Stop is successful only when both the stop outcome marker and a fresh
  post-stop runtime status show no running or connected session.
- Automatic stop retries are limited to three per ten minutes. Exhaustion is
  surfaced as `manual_intervention_required`.

## Authorization entry points

- Discord mutating commands require the existing owner/admin check and then
  issue or use the process-local lease.
- The monolithic Control Page tool composition issues the lease with source
  `control_page`.
- Fast Control and the Windows Local I/O Bridge do not own the lease. Their old
  lazy-start queue is fail-closed and tells the operator to use an authorized
  entry point.
- Legacy `start_voyager_task.ps1` and `VOYAGER_AUTO_START` no longer start a
  runner directly.

The split Docker Fast Control page still cannot be the lease owner while a
separate Discord process is running. A future central authorization owner or
authenticated delegation channel is required before that surface can grant a
lease without creating two competing watchdogs.

## Artifact and privacy contract

Active public status:

`runtime_artifacts/minecraft_world_lease/status.json`

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

Audit journals follow the runtime retention policy: 30 days, 20 MiB, while
preserving the newest seven files. The active status file is not a durable
authorization source; a new owner process overwrites it with
`authorization_required`.

## Verification boundary

Unit coverage includes lease issue, replacement, expiry, restart non-restore,
stale runner cleanup, status failure, cross-guild rejection, proof mismatch,
stale heartbeat, stop verification, retry exhaustion, privacy, and retention.

Live Docker/Minecraft verification is still required. A passing unit suite does
not prove that container scheduling, shared-volume heartbeat propagation,
Mindcraft child termination, or the real Microsoft-authenticated game session
meets the timing contract.
