# Evelyn Context Pipeline Target

## Purpose

The main LLM must not be used as Evelyn's memory store. Each turn should assemble only the memory, state, and skill context that the main LLM needs for the current decision. This lets the main Gemma 4 context stay small, with a 2048-token default target, while leaving VRAM headroom for vision and Minecraft agent work.

Memory storage, indexing, cache, and invalidation details are owned by
`docs/EVELYN_MEMORY_VAULT_ARCHITECTURE.md`. This document owns the turn-time
context policy and prompt assembly pipeline.

## Target Flow

```text
User / Discord / Voice / Minecraft event
  -> Input Normalizer
  -> Router / Context Policy
  -> Context Gatherers
       -> pinned memory
       -> conversation state summary
       -> retrieved long-term memory
       -> runtime state
       -> vision result
       -> skill / capability graph
  -> Context Builder
  -> Main LLM
  -> Response / Action / Tool call
  -> Memory & State Writer
```

The weak point in the current runtime is the middle section: router output, context gathering, and prompt assembly are not yet a first-class pipeline.

## Router Contract

The router should become a context policy producer, not only a route classifier. The target policy shape is:

```json
{
  "intent": "chat | question | minecraft_task | vision_question | memory_update | control",
  "needs_main_llm": true,
  "needs_memory": true,
  "needs_runtime_state": true,
  "needs_minecraft_state": false,
  "needs_vision": false,
  "needs_skill_graph": false,
  "needs_long_context": false,
  "priority": "latency | accuracy | action",
  "context_focus": ["current_goal", "recent_user_preference", "active_task"],
  "response_mode": "short | normal | detailed | action_only"
}
```

The policy decides what context enters the main LLM call. The main LLM should receive a compact case file, not raw access to every memory layer.

## Context Layers

### 1. Pinned Memory

Short, always-on rules and durable user/project preferences.

Examples:

- The current project is Evelyn in `C:\Evelyn`.
- The Minecraft architecture target is the generation after OpenHA/CrossAgent.
- For that architecture direction, prioritize function, intelligence, performance, and extensibility aggressively.

This layer must stay short.

### 2. Conversation State Summary

A compact summary of the current local conversation state.

Examples:

- Current topic: reducing main LLM context and building a supporting memory pipeline.
- Open decision: main context default should move toward 2048.
- Current implementation phase: document and define ContextPacket / ContextPolicy contracts.

This layer replaces long raw history when context is tight.

### 3. Retrieved Long-Term Memory

Relevant memory selected by the current turn. It should be scored by relevance, recency, and importance. It should not dump all memory into the prompt.

The source should be the memory vault/index facade described in
`docs/EVELYN_MEMORY_VAULT_ARCHITECTURE.md`, not raw file scanning during the hot
path.

### 4. Runtime State

Live structured state, separate from long-term memory.

Examples:

- active source: voice, Discord, control page, Minecraft service
- session key and current room owner
- current Minecraft inventory, position, goal, recent failures
- current vision target or attachment summary
- live service status if the user asks operational questions

### 5. Skill / Capability Graph

Procedural and capability knowledge from Evelyn, Voyager, Odyssey, OpenHA-style action spaces, and later internal planners.

The graph should provide only the relevant nodes for the current goal. For example, a `craft_stone_pickaxe` goal should provide the required materials, tools, and known executable skills, not the whole library.

## 2048 Context Budget Target

The target default budget for the main LLM is:

```text
system / behavior rules       200-300 tokens
pinned memory                 150-250
conversation summary          200-350
retrieved memory              200-350
runtime state                 150-300
skill / vision snippets       200-400
recent raw turns              300-500
current user input            100-200
response budget               configured separately
```

The recent raw turns section is intentionally limited. The higher-value context should be summary, runtime state, and retrieved facts.

## Main Prompt Shape

The main LLM message assembly should converge on this shape:

```text
[System]
Evelyn behavior rules.

[Pinned Memory]
- Stable project/user rules.

[Conversation State]
Current local topic, recent decisions, open tasks.

[Runtime State]
Live source/session/Minecraft/vision/service state.

[Retrieved Memory]
Only the few memories relevant to this turn.

[Skill / Capability Context]
Only relevant action or recipe graph snippets.

[Recent Turns]
Small raw history window.

[Current User Input]
The current normalized input.
```

## Minecraft Planning Context

Minecraft requests should not send long natural-language wiki or skill-library dumps to the main LLM. A planning layer should first produce compact structured context:

```text
Goal: craft stone pickaxe
Inventory: 3 logs, 0 planks, 0 cobblestone
Known capabilities:
- craft_planks(log -> planks)
- craft_crafting_table(planks -> crafting_table)
- craft_wooden_pickaxe(...)
- mine_cobblestone(requires wooden_pickaxe)
- craft_stone_pickaxe(...)
Recommended plan:
1. craft planks
2. craft crafting table
3. craft wooden pickaxe
4. mine 6 cobblestone
5. craft stone pickaxe and stone axe
```

The main LLM should judge, explain, or select actions from this compact plan. It should not be the source of truth for recipe logic.

## Memory Writer Contract

After the response/action, a writer should decide what gets saved:

```json
{
  "write_pinned_memory": false,
  "update_conversation_summary": true,
  "update_runtime_state": true,
  "store_long_term_memory": false,
  "store_minecraft_failure": false
}
```

Important decisions should not require the user to explicitly say "remember this" every time. The writer should separate durable memory, session summary, runtime state, and failure telemetry.

## Implementation Phases

### Phase 1: Contracts and Builder Skeleton

- Add `ContextPolicy`.
- Add `ContextPacket`.
- Add deterministic rendering of a packet into main LLM messages.
- Keep existing runtime behavior unchanged except for optional helper availability.

### Phase 2: Wire Existing Memory Context Through the Builder

- Wrap the current `build_memory_context` result as packet sections.
- Preserve existing prompt content while making the assembly path explicit.
- Add metrics for context section sizes.

### Phase 3: Router Context Policy

- Extend router output with policy fields.
- Keep fallback policy for low-latency paths.
- Use policy to decide retrieved memory, runtime state, vision, and skill graph inclusion.

### Phase 4: Runtime State and Memory Writer

- Add structured conversation state summary.
- Add explicit runtime state packet.
- Add post-turn writer decisions for summary, runtime, durable facts, open questions, and Minecraft failures.

### Phase 5: Minecraft and Vision Context

- Add compact skill/capability graph snippets.
- Add image/vision result sections.
- Use benchmark tasks to compare current Voyager-style flow against the new context-planned flow.

## Non-Goals for Phase 1

- Do not rewrite the whole router.
- Do not replace the existing memory implementation.
- Do not change Discord, voice, or Minecraft behavior broadly.
- Do not import all Odyssey/OpenHA data into prompts.

Phase 1 is the stable contract that later phases will wire into.

## Current Implementation Status

- Phase 1 is implemented in `evelyn_core/runtime/evelyn_core/context_pipeline.py`.
- Phase 2 wraps the existing `build_memory_context()` output as a `ContextPacket` retrieved-memory section inside `prepare_llm_messages()`.
- The current Phase 2 wiring is intentionally narrow:
  - it keeps the existing memory retrieval logic,
  - preserves the legacy memory context text as a block,
  - routes prompt assembly through `ContextBuilder`,
  - records basic `context_pipeline` metadata on the turn metrics.
- Route/cognitive results are now adapted into `ContextPolicy` by `build_context_policy_for_turn()`.
- Router LLM output now requests a `context_policy` object and normalizes it through `ContextPolicy.from_mapping()` before prompt assembly.
- Router route response budget now defaults to `220` tokens so the policy JSON is not truncated.
- Runtime state, skill graph hints, and vision hints now have first-class packet sections.
- Per-turn screen observation uses the fail-closed `vision.evidence.v1` contract:
  a request, hint, capture attempt, or failure string cannot mark a vision tool
  executed; scene and OCR availability are evaluated separately.
- Memory writing now has an explicit `MemoryWriterDecision` contract before summary/fact/open-question updates are scheduled.
- Minecraft context now pulls compact live state plus matching Voyager skill snippets into the skill/capability section when the policy asks for it.
- Minecraft context also reads Odyssey-style JSON capability data when `ODYSSEY_CAPABILITY_JSON_DIR` is available, adding compact action / recipe / tool / smelt / collect snippets instead of raw library dumps.
- Discord attachment metadata is forwarded as `[Attached Visual Inputs]`; OpenAI/vLLM content-array payloads now convert those image URLs into `image_url` entries for actual multimodal main-model calls.
- Context pipeline benchmark rows are appended to `runtime_artifacts/benchmarks/context_pipeline_benchmarks.jsonl` so router/context sections, timing marks, answer length, Minecraft usage, and requested-versus-observed vision state can be compared across turns.
- `MAIN_LLM_CONTEXT` now defaults to `2048` in the launcher environment.
- Still incomplete: a persistent OpenHA/CrossAgent action-interface index and a benchmark dashboard/summary job are not wired yet.
