# Evelyn Memory Vault Architecture

## Purpose

This document defines the target memory architecture for Evelyn.

The goal is not to make memory "bigger". The goal is to make memory:

1. readable and editable by a human,
2. searchable and cacheable by the runtime,
3. safe for the realtime voice path,
4. able to evolve from raw events into durable knowledge and procedures.

This document is the source of truth for Evelyn's future long-term memory
storage design. It complements, rather than replaces:

- `EVELYN_ASSISTANT_TARGET_ARCHITECTURE.md`
- `CONTEXT_PIPELINE_TARGET.md`
- `EVELYN_CURRENT_STRUCTURE_LAYER_MAPPING.md`
- `evelyn-dialogue-ux-fastpath-2026-05-28.md`

Those documents describe the assistant layers, context assembly, current code
mapping, and dialogue UX. This document owns the memory store, indexes, cache
layers, and writeback/consolidation rules.

---

## Design Verdict

Evelyn should use an Obsidian-compatible memory vault as the human-readable
source of truth, backed by runtime indexes and caches.

The target shape is:

```text
Markdown vault = durable source of truth
SQLite metadata = fast map, state, freshness, links, cache invalidation
Vector index = fuzzy semantic search
Graph links = relationship search and navigation
Hot cache = realtime memory speed
Prompt cache = LLM latency reduction
```

The memory vault itself is not the runtime cache. It is the durable,
inspectable memory source that generates cache and retrieval layers.

---

## Why This Fits Evelyn

Evelyn is not a simple Q&A bot. It has:

- a persistent character style,
- user preferences,
- active projects,
- voice/TTS decisions,
- Minecraft/Voyager state and failures,
- procedural lessons from repeated tool and runtime operations.

A pure vector database would hide too much of this from the user and make bad
memories hard to inspect. A pure Markdown folder would be readable but too slow
and noisy for realtime recall. Evelyn needs both:

- Obsidian-style notes for human control and long-term continuity,
- machine indexes and caches for fast turn-time behavior.

---

## Current Memory State

Current memory lives mainly under `bot_memory/guild_<id>/` and is reflected in
the README as:

```text
raw_transcript.jsonl
rolling_summary.txt
durable_facts.jsonl
open_questions.jsonl
cognitive_state.json
```

This is useful and should not be ripped out in one pass. The first migration
should wrap and mirror this existing memory into the new vault shape. The
existing files can continue serving as compatibility inputs while the new
writer/indexer is introduced.

---

## Target Directory Layout

The target filesystem layout is:

```text
bot_memory/
  memory_vault/
    core/
      identity.md
      user_profile.md
      conversation_style.md
      safety_and_privacy.md
      project_preferences.md

    daily/
      2026-05-28.md

    episodes/
      2026-05-28-tts-stability.md
      2026-05-28-relic-readme.md
      2026-05-28-voyager-recovery.md

    concepts/
      tts-latency.md
      wake-handling-policy.md
      memory-vault-architecture.md
      minecraft-agent-planning.md

    procedures/
      generate-resume-pdf.md
      update-relic-readme.md
      test-evelyn-tts.md
      recover-voyager-runtime.md

    projects/
      evelyn.md
      relic.md
      voyager-minecraft.md

    archive/
      superseded/
      old-daily/

  memory_index/
    memory.sqlite
    embeddings/
    graph_links.jsonl
    retrieval_cache.jsonl
    prompt_blocks/
      core_prompt.txt
      style_prompt.txt
      user_profile_prompt.txt
    hot_context.json
```

The vault should remain Obsidian-compatible:

- normal Markdown files,
- stable note slugs,
- wikilinks where useful,
- front matter for metadata,
- no opaque binary-only memory as the source of truth.

---

## Memory Types

### 1. Core Memory

Stable facts and policies that should rarely change.

Examples:

- Evelyn's character stance.
- The user's preferred interaction style.
- The `[질문]` visible-text policy.
- Privacy and restart rules.
- Long-lived model preferences.

Core memory is cache-friendly and should feed the stable prompt prefix.

### 2. Daily Memory

Raw-ish human-readable daily notes.

Daily notes preserve continuity without pretending every event is durable.
They are useful for review and later consolidation, but they should not be
dumped directly into prompts.

### 3. Episodic Memory

Specific events that matter.

Examples:

- "User confirmed TTS is stable on May 28."
- "RELIC README and resume were generated and pushed."
- "Voyager action execution changed inventory, but completion propagation was unreliable."

An episode should answer:

- what happened,
- when it happened,
- why it mattered,
- what changed afterward,
- links to related concepts/procedures.

### 4. Semantic / Concept Memory

Consolidated knowledge and decisions.

Examples:

- TTS latency strategy.
- Wake handling policy.
- Minecraft planning architecture.
- Memory vault architecture.

Concept notes are the main source for retrieval when the user asks for project
direction, design reasoning, or "what did we decide?"

### 5. Procedural Memory

How to do repeatable work.

Examples:

- How to update RELIC README and push.
- How to generate `resume.pdf`.
- How to verify Evelyn TTS without leaving test processes.
- How to recover Voyager runtime without code changes.

Procedural memory is important because it prevents repeated operational
mistakes. It should be retrieved for tasks, not casual conversation.

### 6. Runtime State

Live state is not long-term memory.

Examples:

- current Discord room owner,
- current active voice session,
- current Minecraft inventory,
- current model service health,
- current TTS queue.

Runtime state may be stored in JSON for durability, but it should not become
semantic memory unless an important event is extracted from it.

---

## Note Schema

Each note should use lightweight front matter:

```yaml
---
id: mem_20260528_tts_stability
type: episode
status: active
created_at: 2026-05-28T02:00:00+09:00
updated_at: 2026-05-28T02:00:00+09:00
importance: 0.82
decay: normal
confidence: high
source: conversation
projects: [evelyn, tts]
tags: [tts, latency, voice, user-confirmed]
links:
  - concept:tts-latency
  - procedure:test-evelyn-tts
supersedes: []
superseded_by: null
---
```

The body should stay readable:

```markdown
# TTS Stability Confirmed

## What Happened
The user listened to the latest TTS flow and said it no longer felt like it was
cutting off.

## Why It Matters
TTS internals should not be touched again unless live use reveals a new issue.

## Follow-Up
- Keep cached audio fast path for fixed wake replies.
- Do not restart TTS architecture work prematurely.
```

---

## SQLite Metadata

Markdown is the truth, but SQLite is the fast map.

Suggested tables:

```sql
notes(
  id text primary key,
  path text not null,
  type text not null,
  status text not null,
  title text not null,
  created_at text not null,
  updated_at text not null,
  importance real not null,
  confidence text not null,
  content_hash text not null,
  memory_version integer not null
);

note_tags(
  note_id text not null,
  tag text not null
);

note_projects(
  note_id text not null,
  project text not null
);

links(
  source_id text not null,
  target_id text not null,
  relation text not null,
  confidence real not null,
  created_at text not null
);

retrieval_cache(
  query_hash text primary key,
  policy_hash text not null,
  note_ids_json text not null,
  created_at text not null,
  expires_at text not null,
  memory_version integer not null
);

prompt_block_cache(
  block_key text primary key,
  content text not null,
  content_hash text not null,
  created_at text not null,
  updated_at text not null,
  memory_version integer not null
);
```

SQLite should allow the runtime to answer:

- What changed since the last index build?
- Which notes are active?
- Which notes are superseded?
- Which prompt blocks are stale?
- Which notes relate to the current project?

---

## Cache Layers

The cache strategy matters as much as the storage strategy.

### 1. Core Prompt Cache

Purpose: avoid rebuilding stable prompt prefix every turn.

Contains:

- identity,
- conversation style,
- user preferences,
- safety/privacy policies,
- stable project preferences.

Invalidation:

- any `core/` note changes,
- user explicitly changes a durable preference,
- safety or restart policy changes.

Runtime rule:

- generated at startup and after core memory updates,
- reused for prompt-cache-friendly LLM calls,
- should be stable and placed near the front of the prompt.

### 2. Hot Memory Cache

Purpose: keep active topics fast.

Contains:

- recent active project context,
- current open decisions,
- recent user-confirmed constraints,
- recent failures or unresolved questions.

Examples:

- TTS is stable; do not touch internals unless the user reports a live issue.
- RELIC README/resume work was recently completed and pushed.
- Voyager has action execution evidence but result propagation remains weak.

Invalidation:

- TTL expiry,
- active project changes,
- note status changes,
- explicit user correction.

### 3. Retrieval Cache

Purpose: avoid repeating expensive vector/graph search for the same query.

Key shape:

```text
query_hash + context_policy_hash + memory_version -> note_ids
```

Invalidation:

- memory version changes,
- note is archived/superseded,
- embedding index version changes,
- policy shape changes.

### 4. Prompt Assembly Cache

Purpose: reuse the final memory block for similar turns.

Key shape:

```text
turn_type + active_project + context_focus + memory_version -> rendered block
```

This works well with llama.cpp prompt caching because stable prefix sections can
stay fixed, while dynamic retrieved memory remains short and late in the prompt.

### 5. Embedding / Graph Index Cache

Purpose: avoid re-embedding and relinking unchanged notes.

Invalidation:

- content hash changes,
- front matter changes,
- linked note is archived or superseded,
- embedding model changes.

---

## Turn-Time Retrieval Flow

```text
Accepted turn
  -> classify turn type
  -> build ContextPolicy
  -> attach Core Prompt Cache
  -> check Hot Memory Cache
  -> if needed: keyword search + vector search + graph expansion
  -> rerank by relevance, recency, importance, confidence
  -> render compact memory block
  -> store Prompt Assembly Cache
  -> call main LLM
```

Retrieval must be bounded.

Default target:

```text
core prompt cache          stable prefix
hot memory                 100-250 tokens
retrieved memory           150-350 tokens
procedural snippet         100-250 tokens if task-like
```

Turn-time recall should have a timeout. If memory retrieval is slow, Evelyn
should answer with available context or ask a focused follow-up rather than
blocking first audio.

---

## Post-Turn Writeback Flow

Memory writeback should happen after the answer is already stable.

```text
Turn completed
  -> append raw event
  -> decide writeback action
  -> update rolling summary if needed
  -> create episode if importance is high
  -> update semantic/concept notes if repeated pattern emerges
  -> update procedure if a workflow was learned
  -> update SQLite metadata
  -> update vector/graph indexes
  -> invalidate affected caches
```

Writeback decision output:

```json
{
  "append_daily": true,
  "create_episode": false,
  "update_core": false,
  "update_concept": false,
  "update_procedure": false,
  "archive_or_supersede": [],
  "cache_invalidation": ["hot_context"]
}
```

---

## Consolidation Rules

Do not turn every utterance into a permanent note.

Promotion rules:

- Raw transcript becomes daily memory by default.
- Daily memory becomes an episode only if it changed a decision, preference,
  project state, bug diagnosis, or future procedure.
- Multiple related episodes become a concept note.
- A repeated successful workflow becomes a procedure note.
- A wrong old memory becomes `superseded`, not silently overwritten.

Decay rules:

- low-importance chat fades from hot cache quickly,
- project decisions decay slowly,
- user preferences do not decay unless contradicted,
- safety and restart policies do not decay automatically.

---

## Human Editing and Cache Invalidation

Human editability is a core requirement.

If the user edits a Markdown note manually:

1. content hash changes,
2. SQLite metadata is updated,
3. note embeddings are rebuilt,
4. graph links for that note are recomputed,
5. related prompt/retrieval caches are invalidated.

The system should never assume the index is truth if the Markdown file changed.

---

## Relationship Model

Graph links should stay simple at first.

Recommended relation types:

- `supports`
- `contradicts`
- `supersedes`
- `related_to`
- `derived_from`
- `procedure_for`
- `preference_for`
- `failure_of`
- `fix_for`

Example:

```json
{
  "source": "episode_20260528_tts_stability",
  "target": "concept_tts_latency",
  "relation": "supports",
  "confidence": 0.9
}
```

---

## Integration With Existing Docs

### `EVELYN_ASSISTANT_TARGET_ARCHITECTURE.md`

That document remains the top-level assistant architecture.
Its Memory Layer section should point here for storage, cache, and writeback
details.

### `CONTEXT_PIPELINE_TARGET.md`

That document remains the prompt/context assembly contract.
This memory vault supplies:

- pinned memory,
- retrieved long-term memory,
- procedural snippets,
- memory writer decisions.

### `EVELYN_CURRENT_STRUCTURE_LAYER_MAPPING.md`

That document remains the current-code mapping.
Its memory layer section should point here as the target boundary for a future
memory facade.

### `evelyn-dialogue-ux-fastpath-2026-05-28.md`

That document remains the dialogue UX and fast path reference.
Its memory/persona section should use this document as the detailed memory
source/index/cache design.

No existing document should be deleted yet. The current consolidation is:

- this document owns detailed memory-vault architecture,
- existing docs link to this document instead of duplicating memory storage
  details.

---

## Migration Plan

### Current Implementation Status

As of the current `structural-change` pass, the vault is no longer only a
target document. The runtime has an activation path:

```text
activate_memory_vault_for_guild(guild_id)
  -> bootstrap core/project/concept/procedure Markdown notes
  -> mirror legacy guild JSONL summaries/facts/questions
  -> consolidate large daily Markdown notes into episode notes
  -> rebuild SQLite/FTS/vector/graph indexes
  -> refresh memory_index/hot_context.json and prompt_blocks/core_prompt.txt
```

The compatibility files under `bot_memory/guild_<id>/` are still written and
read. The new invariant is that maintenance and recall should make
`bot_memory/memory_vault/` and `bot_memory/memory_index/` active generated
runtime artifacts, not optional future folders.

Sub LLM dependency handling is explicit:

- The sub LLM is the preferred worker for heavier summary, semantic
  consolidation, and deeper memory-state reasoning.
- Vault maintenance probes the configured sub LLM health endpoint and reports
  the result under `dependencies.sub_llm`.
- If the sub LLM is unavailable, maintenance continues in
  `deterministic_memory_vault_maintenance` mode. It may mirror, index, cache,
  and consolidate with deterministic extraction, but it must report that
  semantic consolidation is disabled.
- Maintenance must not auto-start or restart the sub LLM. Startup/supervision
  remains owned by the launcher/runtime layer.

Semantic consolidation is a separate worker step:

```text
run_semantic_memory_consolidation_once(guild_id)
  -> require dependencies.sub_llm.available
  -> read daily Markdown note
  -> ask the sub LLM for JSON notes
  -> write episode/concept/procedure/project Markdown notes
  -> rebuild indexes
```

The worker is intentionally JSON-contract based. If the sub LLM is unavailable,
returns invalid JSON, or produces no useful notes, the deterministic
maintenance path remains valid and the failure is reported in the maintenance
result instead of being hidden.

Derived-memory revocation and recomposition use a separate fail-closed path:

- notes with a revoked source and another live source remain quarantined until
  they can be rebuilt from live source notes only;
- the old derived body and revoked source body are never sent to the sub LLM;
- ordinary vault maintenance remains gated at 900 seconds by default;
- a maintenance result with `pendingNoteIds` uses
  `MEMORY_DERIVATION_RETRY_INTERVAL_SEC` (60 seconds by default) for the next
  eligible non-realtime maintenance opportunity;
- the retry log contains only guild ID, pending count, and retry delay, never
  note IDs or note content;
- a clear recomposition result keeps the ordinary maintenance interval.

The short retry is demand-triggered rather than a permanent polling loop.
Realtime voice turns do not launch full vault maintenance, so startup or a
later non-realtime turn remains the recovery opportunity when a session stays
voice-only.

### Phase 0: Documentation and Compatibility

- Add this document.
- Link it from top-level architecture/context docs.
- Do not change runtime behavior.

### Phase 1: Read-Only Vault Mirror

- Mirror existing `bot_memory/guild_<id>/` summaries/facts into Markdown notes.
- Build SQLite metadata from Markdown.
- Do not make the vault the runtime source yet.

### Phase 2: Index and Retrieval Adapter

- Add a memory facade that can retrieve from:
  - current legacy memory,
  - SQLite metadata,
  - Markdown note bodies,
  - optional vector index.
- Keep output compatible with existing `build_memory_context`.

### Phase 3: Hot Cache and Prompt Block Cache

- Cache core prompt blocks.
- Cache active project memory blocks.
- Add `memory_version` invalidation.
- Record cache hit/miss metrics in turn traces.

### Phase 4: Writeback Split

- Separate raw log append, episode creation, concept update, and procedure
  update.
- Run heavy summarization/index updates after the voice hot path.

### Phase 5: Graph Links and A-MEM-Style Evolution

- Add relationship extraction.
- Add note-link recommendations.
- Allow new memories to update tags/context of old notes.
- Keep all automatic rewrites reviewable and reversible.

---

## Non-Goals

- Do not block first audio on memory maintenance.
- Do not replace all memory code in one pass.
- Do not dump whole Markdown notes into prompts.
- Do not let an LLM silently rewrite core memory without a trace.
- Do not make vector search the only way to find memory.
- Do not store private secrets in Obsidian-compatible notes unless explicitly
  approved.

---

## First Implementation Target

The first code-facing target should be a small memory facade:

```python
class MemoryRecallRequest:
    turn_id: str
    turn_type: str
    active_project: str | None
    query: str
    context_focus: list[str]
    budget_tokens: int

class MemoryRecallResult:
    hot_context: str
    retrieved_notes: list[str]
    procedural_snippets: list[str]
    cache_hit: bool
    memory_version: int
```

This facade should be wired behind the existing context pipeline instead of
being called directly from the realtime voice path.

The guiding rule remains:

> The memory system may make Evelyn smarter over time, but it must not make her
> slower to answer the current turn.
