# Minecraft Bot Target Architecture

Last updated: 2026-05-19
Status: target blueprint before implementation
Scope: Evelyn Voyager Minecraft bot only

## 1. Purpose

This document defines the target architecture for the Minecraft bot before further implementation.
It is not a description of the current code. It is the desired end state that future code changes
should converge toward.

The design goal is to stop growing the bot through one-off patches and move to a structure with:

- deterministic progression where the world state already implies the next step,
- explicit execution and verification contracts,
- narrow LLM usage only where open-ended reasoning is actually needed,
- clean separation between normal task execution and recovery behavior.

This document should be read together with:

- `CURRENT_EVELYN_ARCHITECTURE.md` for the current live stack shape
- `docs/MINECRAFT_AGENT_CODE_FIRST_ARCHITECTURE.md` for the concrete code-first planner,
  capability selector, verifier, and LLM fallback implementation design
- `docs/GROWTH_ORIENTED_BOT_ARCHITECTURE.md` for the higher-level philosophy
- `docs/recovery/VOYAGER_BRIDGE_RECOVERY.md` for the current recovery context

## 2. Core design principles

1. Inventory and recipe state outrank free-form language reasoning.
2. Action success is defined by world effect, not by code generation success or function return values.
3. The bot must know exactly which layer decided, executed, verified, failed, or recovered.
4. Recovery is a separate path, not a hidden side effect inside the normal rollout loop.
5. Repeatedly useful logic should move downward into deterministic infrastructure.
6. LLM generation remains available, but only after deterministic planning and verification boundaries are respected.

## 3. Main problem statement

The current Minecraft bot is no longer blocked mainly by raw connectivity.
The more important weakness is control-flow reliability:

- task completion can succeed in-world but fail to propagate upward,
- critic and bookkeeping can disagree with actual world effects,
- action generation is still carrying too much planning responsibility,
- recovery concerns can bleed into the normal task loop,
- old inferred state can remain alive longer than fresh observation deserves.

The target architecture fixes those failure classes directly.

## 4. Target layer model

The bot should be split into the following layers.

### Layer A. Observation collector

Responsibility:

- Gather raw live state from Voyager env and bridge observation.
- Record inventory, equipment, health, hunger, position, nearby blocks, nearby entities, time of day, biome, and recent errors.
- Attach a fresh observation id and timestamp to every read.

Rules:

- This layer does not decide what to do.
- This layer does not infer success or failure.
- Every downstream layer must be able to point to the exact observation id it used.

### Layer B. State normalizer

Responsibility:

- Convert raw observation into canonical structured state.
- Normalize item names, capability tiers, recipe-relevant counts, tool ownership, armor ownership, and environmental facts.
- Mark state confidence when fields are missing or degraded.

Output shape:

- `InventoryState`
- `CapabilityState`
- `EnvironmentState`
- `RiskState`
- `BotTaskState`

Rules:

- This layer owns canonical names and derived capabilities.
- No higher layer should re-derive item tiers or recipe facts ad hoc.

### Layer C. Deterministic progression planner

Responsibility:

- Derive the next required subgoal from current inventory, capabilities, recipes, and the active objective.
- Prefer deterministic prerequisite expansion over LLM planning.
- Answer questions like:
  - what tool is missing,
  - what materials are missing,
  - whether a crafting table or furnace is required,
  - whether the current task is blocked by food, shelter, or safety.

Expected behavior:

- If the bot needs a stone pickaxe and already has enough logs and stone, the planner should produce the next craft/mining subgoal directly.
- If the bot lacks basic safety or food, the planner should emit a survival prerequisite before deeper progression.
- If deterministic state already implies the next step, the planner must not ask the LLM.

Rules:

- This layer is the primary owner of early-game progression.
- LLM planning is fallback only when the state does not imply a clear deterministic next task.

### Layer D. Plan and step bookkeeper

Responsibility:

- Convert a subgoal into an explicit tracked execution object.
- Maintain the chain:
  - `goal_id`
  - `plan_id`
  - `step_id`
  - `action_id`
  - `observation_id_before`
  - `observation_id_after`
  - `effect_check_id`
  - `done_reason`

Rules:

- No action runs without a tracked step id.
- No completion is surfaced without a recorded effect check result.
- Bookkeeping status must be explicit:
  - `pending`
  - `running`
  - `effect_verified`
  - `partial`
  - `failed`
  - `blocked`
  - `recovery_required`
  - `completed`

This layer is the fix for the current "the world changed, but the task loop did not close correctly" class of failures.

### Layer E. Action executor

Responsibility:

- Execute the chosen step through one of two paths:
  - deterministic primitive or known reusable skill,
  - LLM-generated action code when deterministic execution is insufficient.

Execution policy:

- Prefer stable primitives and proven skills first.
- Use LLM-generated code only when the task is novel, ambiguous, or genuinely open-ended.
- The executor reports execution artifacts, but it does not decide task success.

Rules:

- Code generation success is not task success.
- JavaScript function completion is not task success.
- Executor output is only an execution attempt record.

### Layer F. World-effect verifier

Responsibility:

- Compare before and after state against the step's declared success predicate.
- Decide whether the intended effect happened in the game world.

Examples:

- Craft torches:
  - success means torch count increased and required ingredients decreased as expected.
- Mine iron:
  - success means raw iron count increased or target ore blocks were actually removed.
- Move to target:
  - success means position entered the target region or target block became reachable.
- Place shelter block:
  - success means relevant placed blocks appear in the world or placement memory confirms them.

Rules:

- This layer is the source of truth for task progress.
- Critic cannot override a verified world effect into failure.
- If signals disagree, the result is `unknown` or `partial`, not fake certainty.

### Layer G. Outcome critic

Responsibility:

- Interpret the verified outcome and decide what to do next.
- Produce one constrained classification:
  - `success`
  - `partial`
  - `fail`
  - `unknown`

- Produce a bounded reason code, such as:
  - `effect_verified`
  - `missing_prerequisite`
  - `unsafe_environment`
  - `search_exhausted`
  - `execution_error`
  - `observation_degraded`
  - `timeout`
  - `bridge_fault`

Rules:

- Free-form critic narration is secondary.
- The primary output is structured, small, and debuggable.
- Critic should not silently invent state that the verifier did not observe.

### Layer H. Recovery manager

Responsibility:

- Handle recovery-only behavior outside the normal task loop.
- Distinguish between:
  - service fault,
  - runner fault,
  - bridge fault,
  - Minecraft world danger,
  - task-local blockage.

Rules:

- Recovery does not modify bot code automatically.
- Recovery can restart or reconnect the failing runtime layer when allowed.
- Repeated code-level issues are logged to the issue list instead of patched inside recovery.
- Returning from recovery must create a fresh observation and a fresh step decision, not reuse stale assumptions.

## 4A. Executable Task Contract Taxonomy

The planner must not emit arbitrary natural-language intentions.
It may emit only tasks whose success contract is already known to the verifier.

### Allowed deterministic task families

- `Obtain N <item>`
- `Have N <item>`
- `Craft N <item>`
- `Smelt N <input> into <output>`
- `Mine N <block_or_ore>`
- `Place N <block>`
- `Equip <item>`
- `Move N blocks away from current position`
- `Reach a surface position`
- `Acquire N edible food item`
- `Establish a lit temporary shelter`

### Forbidden qualitative planner outputs

The planner must not directly emit vague state-intention tasks such as:

- `Retreat to a safe position`
- `Find food source`
- `Build a temporary shelter`
- `Recover and stabilize`
- `Explore for something useful`

These may exist as internal recovery intents, but they must be lowered into executable primitives before entering the normal rollout loop.

## 4B. Requested structural implementation direction

The next implementation wave should converge on the following four changes as one connected design,
not as isolated patches.

### 1. Replace task-list progression with a capability graph

The planner should stop treating progression primarily as a flat list of preferred task names.
Instead it should operate on capability nodes and prerequisite edges, for example:

- `food_security`
- `local_crafting_access`
- `light_reserve`
- `iron_pickaxe`
- `diamond_pickaxe`
- `diamond_armor`

Rules:

- A capability node may depend on materials, tools, world access, or lower-tier capabilities.
- The planner should emit the nearest missing executable prerequisite, not a vague long-horizon intention.
- Previously completed or already-owned capabilities must dominate raw material heuristics.
- The graph should be objective-aware, so different long-term goals can choose different branches.

### 2. Make the world-effect verifier stronger than the critic

The verifier should be authoritative whenever the task belongs to a deterministic effect family.

Short-term deterministic domains:

- inventory delta tasks
- recipe result tasks
- mining result tasks
- movement threshold tasks
- single-block placement tasks

Rules:

- If the verifier returns `success`, the critic must not downgrade it.
- If the verifier returns `fail` in a deterministic domain, the critic must not upgrade it to success.
- The critic is fallback only for `unknown` or genuinely high-level qualitative tasks.

### 3. Replace code-warehouse skill saving with a proved routine registry

Skill persistence should stop saving every successful generated function as if it were reusable knowledge.

Rules:

- A routine is only registry-worthy after repeated bounded success.
- Save gating should require deterministic evidence when available.
- Skills should be grouped by semantic family and deduplicated aggressively.
- Version spam should collapse into one active representative plus policy metadata.
- The registry should prefer stable reusable routines, not ad hoc one-off code.

### 4. Separate long-horizon objectives into objective templates

The planner should not overload one default progression path for every run.

Examples:

- `progression`
- `armor_progression`
- `base_establishment`
- `nether_preparation`
- `exploration`

Rules:

- Each objective template declares its target capabilities.
- The capability graph resolves the next missing node under the active objective.
- Objective changes should redirect planning without rewriting low-level capability logic.

## 4C. First implementation slice

The first implementation slice should not attempt full base-building intelligence immediately.
It should deliver the following minimum integrated behavior:

1. Objective template inference for explicit armor/base/progression intents.
2. Capability-aware planning for iron pickaxe, diamond pickaxe, and diamond armor progression.
3. Verifier-first authority for deterministic inventory and movement effects.
4. Proved-routine skill gating with family deduplication.

This slice is sufficient to move the system away from one-off patches while keeping risk bounded.

## 4D. Root fix for repeated reset/task wait churn

The next structural slice must treat repeated `reset_start` churn as a control-flow bug,
not a cosmetic UI issue.

### Root cause

The current upstream loop still binds normal task turnover to a reset-shaped execution entry:

- select task
- enter rollout
- call `reset()`
- re-observe
- render prompt
- act

Even when the world and bot session are healthy, the system re-enters a reset-phase boundary for
every task. That creates planner churn, phase churn, and misleading status output.

### Required structural change

Normal execution must use a persistent world session.

Rules:

- The environment session is bootstrapped once at the start of learn/inference.
- Normal task turnover must not call `env.reset()`.
- Task boundaries should enter a task-session start phase, not a reset phase.
- `env.reset()` becomes recovery-only for boundaries such as death, bridge desync, runtime failure,
  or unrecoverable stuck state.

### Required control-flow shape

The target loop is:

- bootstrap persistent session once
- select objective node
- start task session from current observation
- execute
- verify
- advance objective node

The loop must not be:

- bootstrap
- task
- reset
- task
- reset

### Minimum implementation contract

This slice is considered correct only if:

1. `learn()` bootstraps the environment once, then starts each new task from the persistent session.
2. `inference()` does the same across subgoals after the initial bootstrap.
3. `reset_start` is reserved for actual environment resets.
4. Normal task turnover uses a separate phase such as `task_session_start`.

### Lowering rules

Examples of the required lowering:

- `Retreat to a safe position`
  becomes either:
  - `Reach a surface position`
  - `Move 24 blocks away from current position`

- `Find food source`
  becomes:
  - `Acquire 1 edible food item`

- `Build a temporary shelter`
  becomes:
  - `Establish a lit temporary shelter`

### Contract ownership

- The deterministic planner owns task selection.
- The task-contract layer owns task canonicalization and lowering.
- The verifier owns success predicates for every allowed task family.
- The critic must not become the first component to define what success means for a task family.

### Design consequence

If a task string cannot be matched to a verifier rule, it is not a valid planner output.
That task must be rejected or lowered before execution.

## 5. State model

The target state model should be explicit and versioned.

### InventoryState

- canonical item counts
- derived counts: logs, planks, generic stone, fuel, food
- equipment ownership
- free inventory slots

### CapabilityState

- best tool tier by type
- best armor tier by slot
- can craft crafting table
- can craft furnace
- can smelt
- can mine required ore tier
- can survive night safely

### EnvironmentState

- position
- biome
- time of day
- nearby blocks
- nearby entities
- likely domain: surface, cave, shelter, exposed, underwater, trapped

### RiskState

- hostile pressure
- hunger risk
- health risk
- fall risk
- drowning risk
- darkness risk
- path instability

### BotTaskState

- active objective
- active subgoal
- current plan status
- retry budget
- last verified progress
- recovery requirement flag

## 6. Success and failure contracts

Every executable step must declare these fields before it runs:

- `intent`
- `required_preconditions`
- `expected_world_effect`
- `success_predicate`
- `partial_success_predicate`
- `failure_signals`
- `retry_policy`
- `escalation_policy`

Examples:

### Example: craft stone pickaxe

- Preconditions:
  - crafting table available or craftable
  - sticks available or craftable
  - generic stone count at least 3
- Success predicate:
  - stone pickaxe count increased by at least 1
- Partial predicate:
  - sticks or crafting table were produced, but the pickaxe was not yet produced
- Failure signals:
  - no crafting table access
  - missing stone after expected mining
  - execution error with no inventory change

### Example: gather food

- Preconditions:
  - target food source visible or search profile selected
- Success predicate:
  - edible food count increases or hunger rises after eating
- Partial predicate:
  - food source found but not yet harvested
- Failure signals:
  - search budget exhausted
  - hostile pressure exceeds threshold

## 7. LLM boundary

The LLM should not be the first responder for everything.

### Deterministic first

The following should be deterministic whenever possible:

- prerequisite expansion,
- recipe reasoning,
- tool tier reasoning,
- inventory sufficiency checks,
- early-game bootstrapping order,
- completion verification,
- retry and escalation rules,
- recovery routing.

### LLM fallback only

The LLM remains useful for:

- novel action synthesis when no stable primitive exists,
- open-ended search heuristics when deterministic signals are weak,
- generating candidate procedures worth promoting into reusable skills,
- high-level strategy when multiple valid paths remain genuinely ambiguous.

### Forbidden LLM authority

The LLM must not be the final authority for:

- whether an action really succeeded,
- whether inventory changed,
- whether the bot is safe enough to continue,
- whether a runtime layer is healthy,
- whether a checkpoint is trustworthy.

## 8. Logging and trace schema

The target trace contract should be small but complete.

Required ids:

- `session_key`
- `goal_id`
- `plan_id`
- `step_id`
- `action_id`
- `observation_id_before`
- `observation_id_after`
- `effect_check_id`
- `recovery_id`

Required fields per step:

- chosen by which layer
- deterministic or LLM path
- input observation summary
- expected effect
- actual effect summary
- outcome classification
- done reason
- retry count
- escalation target

The trace must make it obvious where progress was lost:

- planner picked the wrong step,
- executor failed to run,
- verifier saw no world change,
- critic misclassified,
- recovery interrupted,
- state became stale.

## 9. Proposed ownership in the current codebase

This is the intended convergence path, not a claim that the code already looks like this.

### Likely Evelyn-owned integration surface

- `evelyn_core/runtime/evelyn_core/voyager_service.py`
- `evelyn_core/runtime/evelyn_core/upstream_voyager_runner.py`
- `evelyn_core/runtime/evelyn_core/codex_gateway_server.py`

### Likely Voyager runtime surfaces to reshape

- `third_party/Voyager/voyager/voyager.py`
- `third_party/Voyager/voyager/agents/action.py`
- `third_party/Voyager/voyager/agents/critic.py`
- `third_party/Voyager/voyager/agents/curriculum.py`
- `third_party/Voyager/voyager/agents/inventory_planner.py`

### Intended code movement direction

- Grow `inventory_planner.py` into the primary deterministic prerequisite engine.
- Reduce ad hoc completion reasoning inside `critic.py` and move factual success checks into a verifier layer.
- Keep `action.py` focused on execution path selection and code generation, not task truth.
- Make `voyager.py` orchestrate clear step lifecycle transitions rather than implicit loop behavior.

## 10. Implementation sequence

Implementation should follow this order.

### Phase 1. Step bookkeeping contract

Deliverables:

- explicit plan and step lifecycle states
- stable ids for goal, plan, step, action, and effect checks
- durable status propagation from action attempt to surfaced result

Why first:

- This fixes the current blind spot where real world progress may not close the loop correctly.

### Phase 2. World-effect verifier

Deliverables:

- step-specific success predicates
- before and after observation comparison
- structured outcome record for craft, mine, move, place, eat, and smelt paths

Why second:

- Without this, critic and retries are still built on guesswork.

### Phase 3. Deterministic inventory-first planner

Deliverables:

- recipe and capability prerequisite expansion
- survival prerequisite gating
- deterministic early-game progression path

Why third:

- This removes repeated low-level rediscovery from the LLM loop.

### Phase 4. Critic simplification

Deliverables:

- constrained outcome labels
- bounded reason codes
- less free-form narrative authority

Why fourth:

- Once verification is factual, critic can become simpler and safer.

### Phase 5. Recovery boundary hardening

Deliverables:

- normal-path vs recovery-path split
- layer-specific health and recovery actions
- issue logging for code-level faults

Why fifth:

- Recovery should become reliable after normal execution semantics are clear.

### Phase 6. Skill promotion and higher-level strategy

Deliverables:

- promote verified repeated solutions into stable skills
- keep LLM novelty at the strategic edge, not at the mechanical core

## 11. Non-goals

The target architecture is not trying to:

- remove Voyager-style open-ended generation completely,
- hand-script the whole game as a rigid finite-state machine,
- let recovery mutate code automatically,
- treat one-off prompt tweaks as architecture.

## 12. Definition of done for this structural pass

This structural pass is only done when all of the following are true:

1. A task can succeed in-world and that success reliably propagates to the surfaced result.
2. A failed action can be distinguished from a failed verifier, failed critic, or failed recovery layer.
3. Early-game progression no longer depends mainly on free-form LLM judgment.
4. Recovery no longer pollutes the normal task loop.
5. Logs are sufficient to explain why the bot repeated, stopped, escalated, or claimed completion.

Until then, code changes should be judged by whether they move the bot closer to this blueprint,
not by whether they patch one narrow symptom.
