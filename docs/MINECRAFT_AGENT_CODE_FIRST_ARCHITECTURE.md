# Minecraft Agent Code-First Architecture

Last updated: 2026-05-26
Status: target implementation design
Scope: Evelyn / Voyager Minecraft agent execution layer

## 1. Purpose

This document turns the current Minecraft bot target architecture into a concrete code-first execution
design. It exists because reducing LLM calls only works if the replacement code layer is strong enough.

The goal is not to remove LLMs from the Minecraft agent. The goal is to move LLMs out of the default
"decide every next move" path and into bounded fallback roles:

- unresolved goal interpretation,
- novel action generation,
- ambiguous failure explanation,
- higher-level strategy when multiple valid deterministic plans remain.

If the deterministic layers are shallow, this architecture becomes worse than an LLM-first bot.
The implementation must therefore build durable state, planning, selection, verification, and recovery
contracts before reducing LLM authority further.

## 2. Design Principle

The normal loop must be:

```text
User Goal
  -> Goal Normalizer
  -> WorldState Snapshot
  -> Recipe / Capability Planner
  -> Capability Selector
  -> Action Executor
  -> State Delta Critic
  -> Memory / Skill Update
  -> LLM fallback only if unresolved
```

The normal loop must not be:

```text
User Goal
  -> LLM decides what the bot should do
  -> generated action
  -> loose textual critic
  -> try again
```

The LLM remains part of the system, but it is no longer the source of truth for recipes, inventory,
world effects, completion, or service health.

## 3. Odyssey LLM Role Decomposition

Odyssey-style systems use LLMs for several different jobs. Evelyn should split those jobs into
separate components instead of replacing them with one smaller prompt.

| Odyssey-style role | Evelyn replacement | LLM fallback |
| --- | --- | --- |
| Interpret user goal | `GoalNormalizer` plus objective templates | Abstract or ambiguous goals |
| Decompose task | `InventoryFirstPlanner` and `RecipeGraphPlanner` | Non-recipe long-horizon strategy |
| Select skill/function | `CapabilityIndex` and `CapabilitySelector` | Multiple tied candidates or unknown task family |
| Generate action code | Stable primitive / proved routine first | Codex action gateway for missing behavior |
| Reflect on failure | `ExecutionCritic` with expected-vs-actual deltas | Unknown or conflicting failure signals |
| Save experience | Proved routine registry and benchmark memory | Summarization of reusable lessons |

This preserves Odyssey's useful skill-library direction while removing the requirement that a model
must reason from scratch for every mechanical Minecraft step.

## 4. Required Core Data Models

### 4.1 `WorldState`

`WorldState` is the stable planner-facing state object. It should be derived from raw Voyager /
mineflayer observations and should always include confidence markers for missing or stale fields.

Required fields:

- `observation_id`
- `observed_at`
- `inventory`
- `equipped_item`
- `position`
- `health`
- `food`
- `nearby_blocks`
- `nearby_entities`
- `biome`
- `time_of_day`
- `light_level`
- `risk`
- `active_goal`
- `last_action_result`
- `confidence`

Rules:

- Raw observation data does not go directly into planning.
- Missing fields must be represented as `unknown`, not guessed.
- If critical state is unknown, the planner emits an observe/search step before acting.

### 4.2 `InventoryState`

`InventoryState` normalizes item names and derived counts.

Required derived groups:

- logs
- planks
- sticks
- crafting tables
- furnaces
- generic stone / cobblestone
- coal / charcoal / fuel
- edible food
- tool tiers by type
- armor tiers by slot

Rules:

- No higher layer should manually re-derive tool tiers or recipe materials.
- Tool tier facts are owned here, not inside prompts.

### 4.3 `RecipeGraph`

`RecipeGraph` expands target items into prerequisite items, tools, stations, and actions.

Example:

```text
stone_pickaxe
  requires:
    - 3 cobblestone
    - 2 sticks
    - crafting_table_access
  prerequisites:
    - wooden_pickaxe, if cobblestone is not already available
    - logs -> planks -> sticks
```

Rules:

- The graph must support backward planning from target item to missing prerequisites.
- Recipes must be data-driven, not scattered through one-off `if` branches.
- The planner emits the nearest executable missing prerequisite, not the whole long plan every turn.

### 4.4 `Capability`

`Capability` is the normalized representation of a primitive, Voyager skill, Odyssey function, or
future OpenHA/CrossAgent-style action interface.

Required fields:

- `id`
- `source`
- `task_family`
- `produces`
- `requires_items`
- `requires_tools`
- `requires_blocks`
- `requires_environment`
- `danger_level`
- `expected_delta`
- `failure_modes`
- `executor_ref`
- `promotion_status`
- `success_count`
- `failure_count`

Rules:

- Odyssey JSON is imported into capabilities, not pasted into prompts.
- Voyager skills are imported into capabilities, not called only by natural-language similarity.
- A capability without preconditions or expected delta is not allowed into deterministic selection.

## 5. Component Contracts

### 5.1 `GoalNormalizer`

Input:

- raw user goal,
- current `WorldState`,
- active objective template.

Output:

- canonical objective,
- target item or target capability,
- task family,
- constraints,
- ambiguity flags.

Examples:

- "철 캐러 가" -> `obtain raw_iron`, task family `mine_or_collect`
- "초반 진행해" -> objective template `early_game_progression`
- "다이아 장비 준비" -> objective template `diamond_equipment_progression`

If the goal cannot be mapped to a known objective, the normalizer may call a text-only LLM fallback
to produce a candidate canonical objective. The result still must be validated before execution.

### 5.2 `InventoryFirstPlanner`

Input:

- normalized goal,
- `WorldState`,
- `InventoryState`,
- `RecipeGraph`,
- objective template.

Output:

- next executable subgoal,
- missing prerequisites,
- plan confidence,
- whether fallback reasoning is needed.

Rules:

- Early-game progression is deterministic unless state is missing.
- User's preferred opening sequence is a pinned progression policy:
  1. collect 3 logs,
  2. do not craft a wooden axe,
  3. craft a crafting table,
  4. craft a wooden pickaxe,
  5. dig down about 5 blocks safely to reach stone,
  6. mine exactly 6 stone,
  7. pillar back up with dirt,
  8. craft stone pickaxe and stone axe,
  9. find food,
  10. look for iron.
- The planner must not ask an LLM for recipe facts.

### 5.3 `CapabilityIndex`

Input:

- stable primitives,
- Voyager skill library,
- Odyssey JSON capability files,
- future OpenHA/CrossAgent action interfaces.

Output:

- searchable, scored capability entries.

Index dimensions:

- produced item/capability,
- required item/tool/block,
- task family,
- objective template,
- risk level,
- historical success rate,
- expected world delta.

Rules:

- Capability import is offline or cached at startup.
- Capabilities are canonicalized into one schema.
- Duplicate generated skills collapse into one semantic family.

### 5.4 `CapabilitySelector`

Input:

- next executable subgoal,
- current `WorldState`,
- `CapabilityIndex` candidates.

Output:

- chosen capability,
- ranked alternatives,
- rejection reasons,
- whether code generation is required.

Scoring factors:

- preconditions satisfied,
- expected delta matches subgoal,
- safety/risk,
- historical success,
- fewer side effects,
- lower LLM dependence,
- current world availability.

Rules:

- A known stable primitive outranks an unproved generated routine.
- A proved routine outranks new code generation.
- LLM generation is used only when no capability can satisfy the subgoal.

### 5.5 `ActionExecutor`

Input:

- selected capability,
- step contract,
- `WorldState` before execution.

Output:

- action attempt record,
- logs,
- errors,
- optional generated code artifact.

Rules:

- Executor success is not task success.
- Generated JavaScript completion is not task success.
- Executor only reports that an attempt ran.

### 5.6 `ExecutionCritic`

Input:

- step contract,
- before `WorldState`,
- after `WorldState`,
- action attempt record.

Output:

- `success`,
- `partial`,
- `fail`,
- `unknown`,
- reason code,
- next recovery or retry recommendation.

Primary check:

```text
expected_delta vs actual_delta
```

Examples:

- Expected: `cobblestone +6`; actual: `cobblestone +6` -> success.
- Expected: `torch +4`; actual: `torch +4`, `coal -1`, `stick -1` -> success.
- Expected: `raw_iron +5`; actual: no inventory change and no block removal -> fail.
- Expected: move away 24 blocks; actual: position changed 4 blocks -> partial.
- State missing after action -> unknown, request fresh observation.

Rules:

- If a deterministic verifier returns success, an LLM critic cannot downgrade it.
- If a deterministic verifier returns fail, an LLM critic cannot upgrade it.
- LLM failure analysis is allowed only for `unknown` or conflicting signals.

## 6. Step Contract

Every executable step must be created before an action runs.

Required fields:

- `goal_id`
- `plan_id`
- `step_id`
- `task_family`
- `intent`
- `required_preconditions`
- `expected_world_effect`
- `success_predicate`
- `partial_success_predicate`
- `failure_signals`
- `retry_policy`
- `fallback_policy`
- `observation_id_before`

Example:

```json
{
  "task_family": "mine_item",
  "intent": "obtain 6 cobblestone",
  "required_preconditions": ["has wooden_pickaxe or better", "stone reachable"],
  "expected_world_effect": {"inventory.cobblestone": "+6"},
  "success_predicate": "inventory.cobblestone increased by at least 6",
  "partial_success_predicate": "inventory.cobblestone increased by 1..5",
  "failure_signals": ["no stone reachable", "tool missing", "pathing failed"],
  "retry_policy": {"max_retries": 2, "on_fail": "observe_or_find_stone"},
  "fallback_policy": {"llm_allowed": false}
}
```

## 7. LLM Fallback Boundaries

### Allowed LLM fallback cases

- canonical objective is unknown,
- deterministic planner has multiple plausible strategy branches and no objective template decides,
- no capability can satisfy a known subgoal,
- action generation is required,
- verifier result is `unknown` after fresh observation,
- repeated failures need natural-language failure summarization for future code work.

### Forbidden LLM authority

The LLM must not be the final authority for:

- inventory counts,
- item/tool ownership,
- recipe requirements,
- whether an action succeeded,
- whether a service is healthy,
- whether a repeated generated skill should be promoted.

## 8. Model Placement

Minecraft execution should avoid using the multimodal main model for internal mechanical decisions.

Target placement:

- Main response and vision: Gemma 4 E4B multimodal.
- Router: Gemma 4 E2B text-only 4-bit or smaller.
- Failure summary / memory helper / ambiguous subdecision: Gemma 4 E2B text-only 4-bit first.
- Higher-quality text fallback if needed: Gemma 4 E4B text-only 4-bit.
- Novel action code generation: Codex action gateway.

The main multimodal model may receive final compact summaries, not raw skill libraries or repeated
mechanical planning prompts.

## 9. Implementation Phases

### Phase 1. State and contract foundation

Deliver:

- `WorldState`,
- `InventoryState`,
- `StepContract`,
- canonical item/tool normalization,
- observation confidence fields.

Completion check:

- A test can build a stable state from raw observation fixtures.
- Missing fields are represented as unknown.
- A step cannot execute without a contract.

### Phase 2. Recipe and early-game planner

Deliver:

- `RecipeGraph`,
- deterministic early-game progression,
- user's exact opening sequence as a progression policy,
- prerequisite expansion for wooden tools, stone tools, furnace, iron tools.

Completion check:

- Given no inventory, the planner emits collect logs.
- Given logs, it emits crafting table and wooden pickaxe steps.
- Given wooden pickaxe and no stone, it emits safe stone acquisition.
- Given 6 stone, it emits stone pickaxe and stone axe crafting.

### Phase 3. Capability index and selector

Deliver:

- import stable primitives,
- import Voyager skills,
- import Odyssey JSON capabilities,
- score candidates by preconditions and expected delta,
- reject unusable capabilities with explicit reasons.

Completion check:

- A `craft stone pickaxe` goal finds craft/mine/craft prerequisites without LLM.
- An Odyssey capability is available as a structured candidate, not a prompt dump.
- Duplicate generated routines collapse by semantic family.

### Phase 4. Execution critic and delta verifier

Deliver:

- before/after state comparison,
- inventory delta verifier,
- movement verifier,
- block placement verifier,
- mining/crafting/smelting verifier.

Completion check:

- In-world success closes the task loop even if textual critic output is noisy.
- No inventory change after a mining task is classified as fail or unknown, not success.
- Partial progress is retained and used for the next step.

### Phase 5. Fallback and promotion policy

Deliver:

- bounded LLM fallback gates,
- Codex generation only when no capability can satisfy a subgoal,
- proved routine promotion rules,
- benchmark logging for deterministic vs LLM paths.

Completion check:

- Normal early-game progression does not require LLM planning.
- Unknown or novel tasks still have an escalation path.
- Reusable routines require repeated verified success before promotion.

## 10. Required Tests

Minimum fixture tests:

- empty inventory -> collect logs,
- 3 logs -> craft table and wooden pickaxe,
- wooden pickaxe + no stone -> obtain stone,
- 6 stone + crafting table -> craft stone pickaxe and stone axe,
- failed mining with no delta -> failure reason,
- partial mining delta -> continue remaining amount,
- missing observation fields -> observe before acting,
- Odyssey capability import -> structured capability entry,
- duplicate Voyager generated skills -> one semantic family candidate.

Minimum live checks:

- one deterministic early-game progression run,
- one failed action recovery run,
- one novel action fallback run,
- one successful routine promotion candidate run.

## 11. Risks

### Risk: deterministic rules become brittle

Mitigation:

- use data-driven recipe/capability graphs,
- keep confidence and unknown states explicit,
- keep LLM fallback for genuinely unresolved cases.

### Risk: Odyssey imports become noisy

Mitigation:

- import as structured capabilities,
- require preconditions and expected delta,
- score and deduplicate before selection.

### Risk: verifier blocks real progress

Mitigation:

- support `partial` and `unknown`,
- request fresh observation before hard failure,
- preserve actual world deltas even when a step contract was too narrow.

### Risk: model savings hurt quality

Mitigation:

- use text-only E2B for router/sub decisions first,
- allow text-only E4B fallback where quality matters,
- reserve multimodal E4B for visible user response and vision.

## 12. Definition of Done

This architecture is considered implemented only when:

1. Early-game progression can run through logs, crafting table, wooden pickaxe, stone, stone tools,
   food, and iron search without default LLM planning.
2. Every executed step has a contract and before/after observation ids.
3. Inventory and world-effect deltas decide deterministic success.
4. Odyssey/Voyager skills are selected through structured capabilities, not raw prompt text.
5. LLM fallback is logged with an explicit reason.
6. Repeated successful routines are promoted only after verified world effects.
7. Logs can explain whether a failure came from planning, selection, execution, verification,
   observation, or recovery.

