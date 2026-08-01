# Conversation Ingress Recovery Contract

## Scope

`conversation_ingress_recovery.py` is the owner-local durable boundary before
LLM, tool, network delivery, or playback work starts. It closes the gap that a
completed-turn checkpoint cannot close by itself:

- a process may die after accepting a user turn but before a completed turn is
  committed;
- an HTTP or Discord delivery may succeed while its acknowledgement is lost;
- the same stable source delivery may arrive again after a restart.

The module is deliberately independent of Discord, Control Page, Fast Control,
and local voice wiring. Those integrations must be added at their respective
single-owner boundaries. This file describes the core contract they must obey.

## Stable key and binding

An entry key is the SHA-256 projection of this exact tuple:

```text
(surface, owner scope, source delivery ID)
```

Examples of future source delivery IDs are a Discord message ID, a Control Page
`requestId`, or `(bridgeInstanceId, turnId)` represented as one bounded string.
The key is bound to the NFKC/whitespace-normalized final accepted text hash.

- First valid `claim()` durably writes `phase=accepted` and returns
  `shouldProcess=true`.
- The same key and same normalized text returns the existing receipt with
  `shouldProcess=false`.
- The same key and different text fails closed with
  `conversation_ingress_binding_mismatch`.
- No pending, in-flight, ambiguous, terminal-committing, or completed entry ever
  returns `shouldProcess=true` again.

The journal-generated `turnId` is authoritative for later continuity binding.
An integration must reuse it instead of allocating a second turn ID.

## State machine

```text
accepted
  -> response_ready
       -> terminal_committing -> completed
       -> delivery_inflight
            -> delivery_succeeded -> terminal_committing -> completed
            -> delivery_ambiguous -> terminal_committing -> completed
```

Streaming delivery has one additional safe entry path because the final answer
and memory receipt do not exist before the first externally visible delta:

```text
claim
  -> mark_stream_delivery_inflight   # durable before first delta/write
  -> bind_response                   # final text + receipt; phase is preserved
  -> mark_delivery_succeeded         # only after clear EOF/delivery success
  -> begin_terminal_commit -> completed
```

An assistant-less `delivery_inflight` entry is valid only for this streaming
window. On restart it becomes assistant-less `delivery_ambiguous`; it is not
replayable and does not start generation or delivery again. `bind_response()`
may later bind the exact final answer to an in-flight or ambiguous entry without
erasing that delivery truth. `mark_delivery_succeeded()` refuses any entry whose
final response binding is still absent. The non-stream
`mark_response_ready() -> mark_delivery_inflight()` path is unchanged.

`response_ready` durably binds all three of the following:

- final assistant text;
- assistant text hash;
- an exact sanitized `conversation.memory-receipt-ref.v1` and the combined
  assistant/receipt binding hash.

`begin_terminal_commit()` records the expected positive continuity generation
before the completed-turn checkpoint is committed. It accepts only
`delivery_succeeded`; `response_ready` and `delivery_ambiguous` cannot collapse
into a replayable terminal record. `complete()` accepts only the same assistant
binding and generation, then returns a durable terminal receipt. Repeating
either call with the same binding is idempotent; changing the assistant text,
receipt, delivery reference, or generation fails closed.

## Memory deletion and replay boundary

The ingress journal is not a replacement for the memory deletion/exposure
guard.

- A valid `bound` or `not_used` receipt can be projected as `replayable=true`
  only after the entry reaches `completed`.
- A missing or malformed receipt is stored as exact `unattributed` and remains
  `replayable=false` even when the turn is completed.
- An answer without positive delivery success, including an ambiguous delivery,
  cannot enter terminal completion and remains `replayable=false`.
- `replay_record_for()` rejects non-terminal and unattributed entries.
- Before replaying a `bound` answer to a user, the integration must run the
  existing deletion/exposure guard against the stored note IDs and memory
  version. Deletion or revocation after the original answer therefore blocks a
  stale cached replay.

The core only preserves the content-free receipt projection. It does not read,
write, or modify the memory vault, deletion journal, provenance exact-set, or
memory evidence payloads.

## Restart rules

Startup verifies the journal before exposing any record.

- `accepted`, `response_ready`, `delivery_succeeded`, and
  `terminal_committing` remain non-runnable recovery records.
- `delivery_inflight` becomes `delivery_ambiguous` with the fixed error code
  `conversation_ingress_delivery_ambiguous_after_restart`.
- Every recovered non-completed record exposes `automaticReplay=false`.
- No LLM, tool, network send, playback, or other side effect is invoked by the
  recovery module.
- An owner integration may turn `accepted` into one unanswered-user continuity
  record, reconcile `terminal_committing` against the verified checkpoint, or
  tell the user delivery was ambiguous. It must never silently rerun the work.

## Durable file contract

Each owner uses a distinct journal/head pair. Recommended paths are:

```text
runtime_artifacts/conversation_continuity/ingress.json
runtime_artifacts/fast_control_continuity/ingress.json
```

The journal schema is `conversation.ingress-recovery.v1`; the content-free head
schema is `conversation.ingress-recovery-head.v1`.

- Both JSON objects use exact key sets. Unknown, missing, duplicate, malformed,
  or non-finite fields fail closed.
- Every journal has a canonical SHA-256 self-hash, generation, and previous
  hash.
- The journal is durably atomically replaced first; the head is durably
  atomically replaced second. Both writes use file `fsync` and durable rename
  semantics from `runtime_artifact_io.atomic_json_write(durable=True)`.
- An exact both-missing fresh owner durably bootstraps an empty generation-one
  journal and matching head before reporting `verified/current` readiness.
  Bootstrap write failure leaves the owner unavailable; it is never exposed as
  claim-ready on an unprotected in-memory genesis state.
- A fresh process may repair only the exact crash window where the journal is
  one generation ahead of the head and points to the current head hash. A
  generation-one journal linked to the genesis hash may bootstrap a missing
  head. Other rollback, orphan, or hash mismatches are unavailable/fail-closed.

## Privacy and retention

The journal may contain only:

- bounded final accepted text;
- bounded final assistant text;
- their hashes and combined assistant/receipt binding hash;
- stable source/turn identifiers, phase, timestamps, delivery reference;
- the compact content-free memory receipt reference;
- a fixed allowlisted error code and continuity generation.

It never contains raw audio, audio encodings, partial STT, validation
transcripts, system prompts, tool evidence, arbitrary exception text, or full
HTTP/Discord response payloads.

Default bounds match the short-lived Main continuity privacy window:

- maximum age: 15 minutes;
- hard age ceiling: 30 minutes;
- maximum content: 2,000 normalized characters for each final text;
- maximum entries: 128 (hard ceiling 1,024);
- maximum serialized journal size: 1 MiB (hard ceiling 4 MiB).

Expired entries are removed durably. At capacity, only the oldest completed
entry may be evicted; pending truth is never discarded to admit new work. If no
completed entry is available, a new claim fails closed with
`conversation_ingress_capacity_exhausted`.

## Required integration tests

Every owner integration must inject real process exits at these boundaries:

1. immediately after durable claim;
2. after response preparation;
3. after delivery/playback starts;
4. after external delivery succeeds but before continuity commit;
5. after continuity commit but before journal completion;
6. after terminal completion but before the HTTP/client acknowledgement.

For each source, the same source ID must produce one LLM/tool execution, one
delivery at most, and one history turn across a fresh process. A mismatched text
binding must be rejected, pending/in-flight work must never auto-run, and a
completed retry may return cached text only after its compact memory receipt
passes the current deletion/exposure guard.
