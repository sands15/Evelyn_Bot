# Memory Provenance and Deletion Contract

The canonical current contract is
[`docs/MEMORY_PROVENANCE_DELETION_CONTRACT.md`](docs/MEMORY_PROVENANCE_DELETION_CONTRACT.md).
This root file is retained as a stable compatibility pointer.

Current invariants:

- Markdown is durable memory; SQLite schema v6, FTS, vectors, retrieval cache,
  graph data, and hot context are rebuildable derivatives.
- Public memory and recall expose `memory.provenance.v1` with sanitized source
  references and declared `derivedFrom` edges.
- user edits, permanent deletion, and exact-metadata provenance backfill use
  separate conflict-safe preview/apply contracts with CSRF protection.
- provenance backfill tokens expire after 120 seconds, are single-use, and bind
  the target hash, every source hash, and the full graph fingerprint.
- ambiguous backfill candidates cannot be applied, and no candidate is applied
  automatically.
- deletion is tombstone-first; tombstones and provenance audit reports never
  store title, body, transcript, source path/ref, evidence hash, or content
  hash.
- new consolidation and recomposition writes must declare their source note IDs
  through `derived_from`.
