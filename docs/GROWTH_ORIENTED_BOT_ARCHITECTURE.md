# Growth-Oriented Bot Architecture

## Purpose

This document describes a bot architecture designed for sustained growth rather than repeated low-level improvisation. The goal is not to build a static scripted bot, and not to leave everything to moment-to-moment LLM generation either. The goal is to separate what should become stable capability from what should remain adaptive, exploratory, and learnable.

In short:

- low-level survival, movement, recovery, and search should become reliable infrastructure,
- mid-level decision policies should remain adjustable,
- high-level strategy, failure interpretation, and skill formation should continue to evolve,
- repeated mistakes should be absorbed into the system instead of rediscovered forever.

This is different from a naive "LLM writes everything every time" architecture. It is also different from a rigid rules-only bot. It is a layered system where growth happens at the right level.

---

## 1. The Core Philosophy of a Growing Bot

A truly growing bot should not be judged only by whether it can produce novel code in the moment. It should be judged by whether experience changes future behavior in a cumulative way.

There are at least four kinds of growth:

1. **Mechanical growth**
   - Becoming less fragile at movement, navigation, search, recovery, and execution.
   - Example: no longer drowning repeatedly because water escape is now part of stable behavior.

2. **Tactical growth**
   - Learning better short-horizon choices for a known goal.
   - Example: choosing to surface first before searching for food because underground food search is usually wasteful.

3. **Strategic growth**
   - Improving task selection, ordering, switching, and long-horizon planning.
   - Example: deciding to prioritize shelter, food, and basic wood before diverse-item collection when the environment is hostile.

4. **Reflective growth**
   - Interpreting failures, extracting lessons, and converting lessons into reusable policy or skill.
   - Example: after multiple failed underground wood searches, promoting a new policy: "wood acquisition should trigger surface-orientation mode first."

A lot of systems overemphasize novelty and underemphasize accumulation. They look intelligent because they keep generating new code, but they are not actually improving fast enough because the same low-level mistakes reoccur. A growth-oriented bot must preserve adaptation while reducing repeated rediscovery.

---

## 2. Where the Original Voyager Approach Is Strong

The original Voyager-style idea is compelling because it treats the agent as an open-ended code-generating explorer.

Its strengths are real:

- The agent can invent new programs on demand.
- The agent can generalize from prompts rather than fixed state machines.
- Successful code can be stored as reusable skill.
- Exploration feels open-ended rather than hand-authored.
- The system encourages emergence.

This makes the bot feel alive. It can surprise the operator. It can sometimes solve tasks in ways that a manually engineered policy never anticipated.

That flexibility is valuable and should not be discarded.

However, the original style also tends to push too much responsibility onto one layer: the action-generation layer. The same LLM that should be spending effort on problem solving also keeps having to reinvent basic fieldcraft:

- how to search,
- how to recover,
- how to avoid getting stuck,
- how to tell whether progress is real,
- how to switch from local search to broader exploration,
- how to avoid wasting time in obviously bad domains.

This causes several familiar failure modes:

- local search loops,
- overconservative failure,
- random directional exploration,
- repeated death by the same environmental hazard,
- poor distinction between "not found nearby" and "wrong search domain,"
- fragile action code that is technically plausible but strategically wasteful.

So the problem is not that Voyager grows too much. The problem is that too much of its growth burden is concentrated in the wrong layer.

---

## 3. What a Better Growing Bot Should Preserve

A revised architecture should preserve these Voyager strengths:

1. **Open-ended action generation**
   - The bot should still be able to propose new solutions.

2. **Skill formation from success**
   - Repeatedly useful behaviors should still become reusable artifacts.

3. **Failure-aware iteration**
   - The bot should still revise its behavior after bad outcomes.

4. **Curriculum-like expansion**
   - The bot should still broaden what it can reliably do over time.

5. **Environmental adaptation**
   - The bot should not become a brittle hand-scripted automaton.

The change is not to remove growth. The change is to relocate growth into the levels where it compounds best.

---

## 4. The Main Design Principle: Stable Lower Layers, Adaptive Upper Layers

The central architectural decision is this:

- **Low-level competencies should stabilize.**
- **High-level decision making should remain adaptive.**

That means the bot should stop re-learning some categories of behavior from scratch every run.

### Low-level capabilities that should become stable

These are not where we want creative variability every time:

- safe movement primitives,
- pathfinding guardrails,
- stuck detection,
- danger-aware retreat,
- surface recovery,
- domain-aware search (surface vs underground vs recovery),
- progress measurement,
- time/effort budgeting,
- standard failure normalization.

These are closer to the bot's body than its mind. If the body is unreliable, the mind wastes its intelligence compensating for basic dysfunction.

### Mid-level capabilities that should remain configurable

These should be policy-driven rather than fully fixed:

- whether wood search prefers ridgelines or plains first,
- whether food search tolerates longer surface travel when hunger is moderate,
- whether recovery mode prioritizes light, altitude, or shortest path,
- how quickly search radius escalates,
- how much risk is acceptable by time of day.

### High-level capabilities that should remain learnable

This is where open-ended growth matters most:

- selecting the next task,
- deciding when a task is ill-timed,
- interpreting failures,
- synthesizing countermeasures,
- discovering reusable procedures,
- deciding which discoveries deserve skill promotion,
- adapting strategy to world history and recent outcomes.

This creates a bot whose foundation becomes more reliable while its strategic intelligence continues to grow.

---

## 5. Proposed Layered Architecture

The architecture should be divided into at least six layers.

### Layer 1: Perception Layer

The perception layer converts raw observations into structured search and risk information.

#### Responsibilities

- Read current inventory, health, hunger, location, nearby blocks, nearby entities, time of day, recent chat/system messages, and recent death/failure data.
- Infer domain-level facts such as:
  - underground vs surface,
  - exposure vs shelter,
  - nearby food candidates,
  - nearby wood candidates,
  - nearby ore candidates,
  - likely escape routes,
  - hazard signals.
- Produce candidate targets and candidate directions instead of only raw observations.

#### Why it matters

Without this layer, the action LLM keeps inferring from noisy text snapshots and makes shallow decisions. A perception layer compresses the world into actionable structure.

#### Example outputs

- `surfaceExitCandidates`
- `woodTargets`
- `foodTargets`
- `oreTargets`
- `recoveryTargets`
- `hazards`
- `terrainRisk`
- `mobPressure`
- `searchDomain`

This layer should be deterministic or mostly deterministic. It should not depend heavily on open-ended language generation.

---

### Layer 2: Search Profile Layer

This layer defines policy profiles for different intentions.

Examples:

- wood search,
- food search,
- ore search,
- shelter search,
- recovery search,
- escape-to-surface search.

Each profile defines:

- preferred search domain,
- target signal types,
- avoided signal types,
- direction scoring heuristics,
- search radius ladder,
- movement budget,
- stuck threshold,
- escalation policy,
- fail-fast conditions,
- success conditions.

#### Example

A wood search profile might prefer:

- surface > cave,
- leaves/logs/grass/treeline hints,
- moderate elevation gain for visibility,
- avoidance of deep water and cliffs,
- expanding radii `[32, 64, 96, 128]`,
- fallback to surface recovery if currently underground.

A recovery profile might prefer:

- surface exits,
- light,
- flat ground,
- low hostility,
- distance from water and fall risk,
- progressive escalation from safe to balanced to urgent.

This layer is policy, not execution.

---

### Layer 3: Search Planner Layer

The search planner receives:

- current goal,
- perception outputs,
- selected profile,
- recent failed attempts,
- recent death-derived countermeasures,
- time/risk budget,
- progress history.

It outputs a concrete search plan.

#### Example plan object

```json
{
  "goalType": "wood",
  "mode": "surface_recovery_then_scan",
  "waypoints": [
    {"x": -20, "y": 76, "z": -10, "reason": "higher visibility ridge"},
    {"x": -5, "y": 74, "z": 12, "reason": "treeline candidate"}
  ],
  "searchRadius": 64,
  "timeBudgetSec": 30,
  "progressExpectation": "must improve wood signal score within 10 sec",
  "fallback": "expand_radius"
}
```

#### What this fixes

This removes the current bad pattern where the LLM must improvise raw `exploreUntil` calls with guessed directions and guessed timeouts. Instead, search becomes a planned sequence shaped by structured policy.

---

### Layer 4: Motion Executor Layer

This layer executes a plan physically.

#### Responsibilities

- Translate waypoint plans into pathfinder goals.
- Monitor whether the bot is actually moving.
- Detect local path failure.
- Detect oscillation or repeated micro-movement.
- Abort when environment violates safety limits.
- Emit standardized outcome objects.

#### Outcome examples

- `reached_waypoint`
- `blocked_by_terrain`
- `stuck_no_progress`
- `hazard_interrupt`
- `target_seen`
- `budget_exhausted`

This layer should be responsible for robust execution, not strategy invention.

---

### Layer 5: Progress Evaluator Layer

This layer determines whether the bot is making meaningful progress relative to the search objective.

This is critical because movement is not the same thing as progress.

#### Examples

For wood search, progress may mean:

- stronger nearby leaf/log signal,
- transition from underground to surface,
- improved visibility,
- reduced distance to candidate wood target.

For food search, progress may mean:

- animal or crop candidates appearing,
- movement into plains/forest edge/shoreline domains,
- reduced hunger risk via discovered path to food.

For recovery search, progress may mean:

- increased light,
- reduced hazard density,
- surface emergence,
- flatter terrain,
- less mob pressure.

#### Why this matters

Without a progress evaluator, the bot can move for 30 seconds and still be effectively stuck. With this layer, lack of improvement becomes machine-readable and can trigger replanning.

---

### Layer 6: Reflective Adaptation Layer

This is the actual growth layer.

It should interpret outcomes over time and modify future behavior.

#### Inputs

- death events,
- repeated failure reasons,
- search exhaustion histories,
- environmental context,
- successful task traces,
- reusable code fragments,
- policy effectiveness statistics.

#### Outputs

- countermeasure synthesis,
- search profile adjustments,
- task ordering adjustments,
- skill candidate promotion,
- domain-specific heuristics,
- reminders or hard guards for specific hazards.

This is where growth becomes cumulative rather than performative.

---

## 6. Growth as Policy Evolution, Not Just Code Generation

The most important conceptual shift is that growth should not mean only "the bot writes new code." Growth should also mean:

- better selecting among profiles,
- better estimating which search domain fits the goal,
- better deciding when a task is unrealistic in the current state,
- better turning failures into durable policy changes,
- better deciding when to save a skill versus when to revise a policy.

In other words, there are at least three kinds of reusable learning objects:

1. **Skill artifacts**
   - concrete reusable procedures,
2. **Policy updates**
   - rules or weights that guide future choices,
3. **Countermeasure memories**
   - failure-to-protection mappings.

Voyager strongly emphasizes the first. A mature growing bot should use all three.

---

## 7. Death and Failure as Structured Feedback

A growth-oriented bot should not treat death as only an unfortunate interruption. Death is one of the clearest signals that the system's current policy was wrong for the environment.

### Required pipeline

1. Detect death or severe failure.
2. Classify likely cause.
3. Determine whether cause is tactical, structural, or accidental.
4. Synthesize a countermeasure.
5. Apply the countermeasure at the right layer.
6. Decide whether the lesson is temporary, profile-level, or skill-level.

### Example

If the bot dies underwater:

- not just `cause = drowning`,
- but also:
  - search domain mismatch,
  - inadequate oxygen escape policy,
  - poor underwater work cutoff,
  - insufficient route risk evaluation.

Then the system may produce:

- temporary countermeasure: avoid submerged work this rollout,
- profile-level update: water-heavy routes score lower for food search,
- future skill hint: surface-first shoreline scanning is preferred.

This is much more useful than only retrying with a slightly rewritten function.

---

## 8. The Right Boundary Between Engine and Growth

A major risk in redesigning the system is over-engineering. If too much intelligence is moved into fixed code, the bot stops feeling like it is learning. If too little is moved into stable code, the bot keeps repeating avoidable mistakes.

A good rule of thumb is:

### Engine-owned

- navigation safety primitives,
- recovery primitives,
- structured search planning,
- stuck detection,
- progress measurement,
- normalized failure categories.

### LLM-owned or growth-owned

- task proposal,
- choosing which search profile to use,
- deciding when to escalate or switch tasks,
- interpreting ambiguous outcomes,
- selecting risk tolerance,
- deciding which successful traces become skills,
- synthesizing new strategy from repeated evidence.

### Shared boundary

The LLM should not micromanage every move, but it should be able to influence profile selection and parameters.

For example:

```json
{
  "goalType": "food",
  "profile": "surface_forage",
  "riskTolerance": "medium",
  "maxSearchBudgetSec": 45,
  "fallbackPreference": "animals_before_crops"
}
```

This allows growth without forcing the model to re-implement search logic every time.

---

## 9. New API Philosophy: Intent-Level APIs Instead of Direction-Level APIs

The old exploration style relies on low-level interfaces like:

- choose direction,
- choose timeout,
- choose callback,
- hope it works.

This is too low-level for reliable open-ended learning.

The new API philosophy should be:

- the LLM declares intent,
- the engine executes search intelligently.

### Bad low-level pattern

```javascript
await exploreUntil(bot, new Vec3(1, 0, 1), 15, () => nearestLog())
```

### Better intent-level pattern

```javascript
await searchAndHarvest(bot, {
  goalType: "wood",
  target: "any_log",
  quantity: 1,
  riskTolerance: "medium"
})
```

### Why this is better

Because the intelligence of search should be in one improving system, not improvised per task. This also creates much better learning surfaces: the bot can compare profiles, budgets, and outcomes across tasks.

---

## 10. How Growth Should Actually Be Measured

A system that only checks task completion is too coarse. A growing bot should be evaluated on whether it improves across multiple dimensions.

### Metrics worth tracking

1. **Repeated failure reduction**
   - Is the same class of error happening less often?

2. **Search efficiency**
   - Time-to-target by goal type.

3. **Recovery quality**
   - Time from death or hazard to stable state.

4. **Task appropriateness**
   - Fewer obviously mistimed tasks.

5. **Policy adaptation success**
   - Do countermeasures reduce recurrence?

6. **Skill usefulness**
   - Are saved skills reused successfully?

7. **Behavioral stability under novelty**
   - Does the bot remain coherent in new terrain?

Without measurement, growth remains mostly aesthetic.

---

## 11. Recommended Implementation Path for Evelyn

This should not be rewritten all at once. A staged migration is safer.

### Phase 1: Build search infrastructure

Create a `search/` subsystem containing:

- perception,
- profiles,
- planner,
- executor,
- progress evaluator.

Start with only:

- wood,
- food,
- recovery.

These are the highest pain points and have the most obvious domain distinctions.

### Phase 2: Introduce intent-level APIs

Add wrappers such as:

- `searchAndHarvest`
- `searchAndCollectFood`
- `recoverToSurface`
- `searchForOre`

The action layer should be encouraged, then eventually required, to use these for relevant goals.

### Phase 3: Connect reflective adaptation

Integrate:

- death-derived countermeasures,
- repeated-failure policy updates,
- profile parameter adjustments,
- search-budget adaptation.

### Phase 4: Promote durable discoveries

Only after the search system becomes stable should successful repeated patterns become explicit reusable skills.

This avoids saving fragile or low-quality behaviors too early.

---

## 12. Final Position

The difference between a merely reactive bot and a truly growing bot is not how often it writes new code. It is whether experience becomes durable capability.

A strong growth-oriented bot should:

- stop repeating basic low-level mistakes,
- preserve open-ended strategic adaptation,
- use failure as structured input,
- convert repeated lessons into policy,
- convert strong procedures into skills,
- keep exploration creative at the strategic layer while making execution reliable at the operational layer.

That is the architecture described here.

It does not reject the spirit of Voyager. It tries to mature it.

The original vision says: let the bot grow by writing actions.

This design says: let the bot still grow by writing actions, but also let it grow by stabilizing its body, refining its policies, remembering its failures properly, and climbing upward from repeated survival mistakes into reusable intelligence.

That is a more durable definition of growth.
