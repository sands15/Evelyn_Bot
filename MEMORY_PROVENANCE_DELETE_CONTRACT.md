# Memory Provenance and Deletion Contract

## Provenance

Public memory cards and recall results expose `memory.provenance.v1`.
The contract records the source category, sanitized source references, parent
memory IDs, evidence hashes, timestamps, confidence, and user confirmation or
edit state. Absolute local paths are reduced to `local:<filename>` before they
leave the runtime.

The generated SQLite index is schema version 4. Markdown remains the durable
source; index, vector, retrieval-cache, and hot-context data are rebuildable.

## Permanent deletion

Control Page deletion is a two-step operation:

1. `POST /api/control-page/memory/{noteId}/delete/preview`
2. `POST /api/control-page/memory/{noteId}/delete/apply`

Preview returns a cryptographically random, single-use confirmation token.
The token expires after two minutes and is bound to the memory root, note ID,
and content hash. Apply fails if the token is invalid, expired, reused, points
to another note, or if the source changed after preview.

A successful apply:

- deletes the Markdown source file;
- removes user confirmation, pin, hide, and edit state;
- removes the note from search and vector indexes;
- invalidates retrieval caches;
- rebuilds hot context;
- writes a content-free tombstone that prevents automatic regeneration.

The tombstone stores only schema, opaque note ID, note type, source category,
content hash, deletion reason, and deletion time. It never stores the title,
body, transcript, source path, or source references.

Bootstrap contract notes, internal management notes, and
legacy-source-managed notes cannot be deleted from Control Page. All mutating
endpoints use the existing Control Page CSRF contract.
