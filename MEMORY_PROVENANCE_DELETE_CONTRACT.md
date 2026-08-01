# Memory Provenance and Deletion Contract

The canonical current contract is
[`docs/MEMORY_PROVENANCE_DELETION_CONTRACT.md`](docs/MEMORY_PROVENANCE_DELETION_CONTRACT.md).
This root file is retained as a stable compatibility pointer.

Current invariants:

- Markdown is durable memory; SQLite schema v6, FTS, vectors, retrieval cache,
  graph data, and hot context are rebuildable derivatives.
- Public memory and recall expose `memory.provenance.v1` with sanitized source
  references and declared `derivedFrom` edges.
- user edits, permanent deletion, exact-metadata provenance backfill, and
  user-selected repair for signal-free historical notes use separate
  conflict-safe preview/apply contracts with CSRF protection.
- provenance backfill tokens expire after 120 seconds, are single-use, and bind
  the target hash, every source hash, selection mode, and the full graph
  fingerprint.
- ambiguous backfill candidates cannot be applied, and no candidate is applied
  automatically.
- provenance coverage and rejected derived-write counts are content-free
  aggregates; manual source selection never uses body similarity, embeddings,
  or LLM inference.
- legacy or malformed forward-rejection aggregates are durably rewritten to
  the closed content-free schema under the audit lease; if that rewrite fails,
  the audit fails closed with the stable deletion-integrity error.
- deletion is tombstone-first; tombstones and provenance audit reports never
  store title, body, transcript, source path/ref, evidence hash, or content
  hash.
- deletion-ledger identifiers are fixed machine IDs or domain-separated opaque
  hashes; user-authored front-matter IDs and free-form type labels are never
  persisted verbatim in content-free deletion artifacts.
- content-free memory receipts apply the same ID projection and restrict
  retrieval mode to a closed enum; custom providers and corrupted cache rows
  cannot export a free-form identifier or mode label. Legacy evidence/turn IDs
  and explicit-confirmation source references use separate domain-separated
  opaque projections at the producer, final receipt, and durable turn summary.
- recall renders one deduplicated note set; snippets, sources, provenance,
  versioned retrieval-cache payloads, and receipt note IDs describe exactly
  that set, including task-like procedural additions.
- derivation-revocation state is canonical content-free JSON. Noncanonical or
  ambiguous IDs fail closed; a canonical target already absent from the live
  graph is retained only long enough for reconciliation to remove the stale
  entry.
- permanent deletion uses a strict chained local ledger; local verification
  detects corruption and single-artifact rollback, while past journal+head pair
  replay protection requires a verified signed head and external anchor.
- new consolidation and recomposition writes must declare their source note IDs
  through `derived_from`.
- memory-bearing Main, Voice, Fast, cognitive-state, route-planning, and memory
  writeback LLM requests revalidate the exact root-bound deletion position
  immediately before HTTP admission and retain the deletion lease until the
  response has been consumed. Memory-derived background state is revalidated
  again before it is persisted.
- provenance-correction v2 events persist only ledger IDs; recovery and undo
  restore application IDs through an exact live-graph mapping and fail closed
  on missing or ambiguous mappings. Persisted provenance coverage uses only the
  closed note/source type enums. V2 JSONL rows, local heads, signed anchors, and
  writer markers require duplicate-free exact schemas and canonical bytes;
  immutable legacy v1 rows remain raw compatibility anchors.
- Bot API chat, Fast streaming/non-streaming HTTP, and the public Control Page
  proxy preserve deletion-integrity failures as the exact content-free HTTP 503
  response with `Cache-Control: no-store`.
