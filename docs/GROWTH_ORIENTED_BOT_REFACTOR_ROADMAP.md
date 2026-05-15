# Growth-Oriented Bot Refactor Roadmap

## Status

This roadmap is derived from:

- `C:\Evelyn\docs\GROWTH_ORIENTED_BOT_ARCHITECTURE.md`

It turns the architecture direction into concrete implementation phases for Evelyn.

---

## 0. Refactor Goal

We are not trying to make Evelyn more scripted.
We are trying to move repeated low-level survival/search mistakes out of ad-hoc per-turn LLM code and into a reusable runtime layer, while keeping higher-level strategy adaptive.

### Desired result

- low-level search becomes reliable,
- failure recovery becomes structured,
- death/failure creates reusable policy updates,
- the LLM stops improvising weak exploration loops every task,
- growth happens more at the strategy/policy/skill layer than at the raw movement loop layer.

---

## 1. Current Pain Points to Replace

### Current problems

1. `exploreUntil(...)` is too low-level and too random.
2. Wood/food/recovery/ore search use weakly differentiated behavior.
3. The action LLM has to improvise search mechanics instead of using stable runtime capabilities.
4. Failure often gets translated into retry noise instead of policy improvement.
5. Death interpretation exists, but the adaptation path is still shallow.
6. Search success/failure is evaluated too coarsely.
7. The bot can move without making meaningful progress.

### What must change

We need to replace:

- random or semi-random search loops,
- local-search-only behavior,
- fragile task-generated pathing decisions,
- unclear failure semantics,

with:

- structured perception,
- goal-aware planning,
- execution with progress checks,
- reflective adaptation.

---

## 2. Target Refactor Shape

## Runtime layers to introduce

### Layer A. Perception
Location suggestion:
- `third_party/Voyager/voyager/control_primitives/search/perception.js`

Responsibilities:
- gather nearby entities/blocks/terrain signals,
- classify current domain: surface / underground / escape / water-risk,
- generate typed candidates:
  - wood candidates,
  - food candidates,
  - ore candidates,
  - recovery candidates,
  - hazards.

### Layer B. Search Profiles
Location suggestion:
- `third_party/Voyager/voyager/control_primitives/search/profiles.js`

Responsibilities:
- define behavior profiles for:
  - wood,
  - food,
  - ore,
  - recovery,
  - surface-exit.

Each profile should define:
- preferred signals,
- avoided signals,
- initial radius,
- radius ladder,
- movement budget,
- progress expectations,
- escalation behavior,
- hard fail conditions.

### Layer C. Search Planner
Location suggestion:
- `third_party/Voyager/voyager/control_primitives/search/planner.js`

Responsibilities:
- consume perception + profile + current goal + risk state,
- choose a search mode,
- choose waypoint(s),
- define the next search budget,
- define fallback/escalation behavior.

### Layer D. Motion Executor
Location suggestion:
- `third_party/Voyager/voyager/control_primitives/search/executor.js`

Responsibilities:
- execute plan waypoints through pathfinder,
- detect blocked/stuck states,
- stop on danger thresholds,
- emit structured outcomes.

### Layer E. Progress Evaluator
Location suggestion:
- `third_party/Voyager/voyager/control_primitives/search/progress.js`

Responsibilities:
- decide whether the search is actually improving,
- distinguish motion from progress,
- trigger replanning/escalation when needed.

### Layer F. Reflective Adaptation Hooks
Primary integration points:
- `third_party/Voyager/voyager/voyager.py`
- `third_party/Voyager/voyager/agents/curriculum.py`
- optionally `third_party/Voyager/voyager/agents/skill.py`

Responsibilities:
- consume death/failure evidence,
- synthesize countermeasures,
- adjust search profile choice or parameters,
- eventually promote repeated successful procedures into skill artifacts.

---

## 3. API Direction

## Replace low-level exploration calls with intent-level search APIs

### Old style
- `exploreUntil(...)`
- manual direction choice
- manual timeout choice
- task-local retry logic

### New style
Suggested APIs:

- `searchPlan(bot, options)`
- `searchAndMove(bot, options)`
- `searchAndHarvest(bot, options)`
- `searchAndCollectFood(bot, options)`
- `recoverToSurface(bot, options)`
- `searchForOre(bot, options)`

### Example usage

```javascript
await searchAndHarvest(bot, {
  goalType: "wood",
  target: "any_log",
  quantity: 1,
  riskTolerance: "medium",
  maxSearchBudgetSec: 45,
});
```

This should internally handle:
- perception,
- plan selection,
- progress monitoring,
- escalation,
- structured failure result.

---

## 4. What Should Stay Adaptive

To preserve the spirit of a growing bot, the runtime must not freeze all decision making.

The following should remain LLM/growth controlled:

- which profile to use,
- when to abandon a task,
- when to escalate risk tolerance,
- when a repeated pattern should become a skill,
- how to reinterpret repeated failures,
- when to prefer food over wood or recovery over progress,
- which countermeasure matters most right now.

The following should become runtime-owned:

- how to scan,
- how to move safely,
- how to detect stuck states,
- how to expand search radius,
- how to classify hazards,
- how to report standardized outcomes.

---

## 5. Implementation Phases

## Phase 1 — Search Infrastructure Skeleton

Goal:
Introduce the search subsystem without replacing everything yet.

Tasks:
1. Create `search/` directory under Voyager control primitives.
2. Add skeleton modules:
   - `perception.js`
   - `profiles.js`
   - `planner.js`
   - `executor.js`
   - `progress.js`
3. Define shared result objects and error categories.
4. Keep `exploreUntil.js` available as fallback.

Deliverable:
- new search subsystem exists,
- no behavior change required yet.

## Phase 2 — Recovery First

Goal:
Refactor the most failure-sensitive behavior first.

Why first:
Recovery is where current fragility is highest and where structured safety provides the clearest gain.

Tasks:
1. Implement `recoverToSurface()`.
2. Add recovery search profile.
3. Add surface/escape perception candidates.
4. Add basic stuck and no-progress detection.
5. Route death-derived recovery actions through the new recovery primitive.

Deliverable:
- post-death and hazard recovery no longer depends on ad-hoc random exploration.

## Phase 3 — Wood Search Refactor

Goal:
Replace short-range weak wood search with profile-based surface-oriented search.

Tasks:
1. Implement wood profile.
2. Implement wood-target perception:
   - log blocks,
   - leaves,
   - treeline hints,
   - visibility spots.
3. Add search radius ladder and terrain-aware waypoint choice.
4. Add fallback from underground domain into surface recovery before wood search.
5. Update action prompting to prefer `searchAndHarvest(... goalType: "wood")`.

Deliverable:
- wood acquisition becomes reliable and no longer depends on naive local probes.

## Phase 4 — Food Search Refactor

Goal:
Replace current weak food loops with domain-aware surface food search.

Tasks:
1. Implement food profile.
2. Add perception for:
   - nearby animals,
   - crops,
   - village/shoreline/plain hints,
   - hunger urgency.
3. Add urgency scaling:
   - hunger low -> fastest calories first,
   - hunger stable -> broader safe search allowed.
4. Update action prompting to use `searchAndCollectFood()`.

Deliverable:
- food search becomes faster and less likely to stall underground.

## Phase 5 — Progress-Aware Ore Search

Goal:
Keep ore search underground-specific instead of reusing surface logic badly.

Tasks:
1. Implement ore profile.
2. Add perception for:
   - ore exposure,
   - cave walls,
   - target y-bands,
   - lava/fall hazards.
3. Add vertical planning bias.
4. Add fail reasons that distinguish:
   - wrong elevation,
   - no exposure,
   - unsafe cave path,
   - exhausted local vein search.

Deliverable:
- underground search becomes domain-correct and measurable.

## Phase 6 — Integrate Reflective Adaptation

Goal:
Make search behavior improve from repeated evidence.

Tasks:
1. Extend death countermeasure synthesis to suggest profile adjustments.
2. Record repeated failure patterns by goal type.
3. Adjust search parameters when failures repeat.
4. Store policy-level learnings separately from skill code.
5. Define rules for when repeated success should become reusable skill.

Deliverable:
- growth becomes policy-aware instead of only code-aware.

## Phase 7 — De-emphasize Legacy `exploreUntil`

Goal:
Turn `exploreUntil` into compatibility fallback only.

Tasks:
1. Remove it from recommended action-generation patterns.
2. Update action validation so search goals prefer new APIs.
3. Reserve `exploreUntil` for edge fallback only.

Deliverable:
- the old exploration primitive no longer drives the bot's main intelligence.

---

## 6. Required Prompt/Validator Changes

Relevant file:
- `third_party/Voyager/voyager/agents/action.py`

Changes needed:
1. teach the action model to prefer intent-level search helpers,
2. discourage hand-written random exploration when a search API exists,
3. validate that resource-gathering tasks use the new primitives,
4. preserve escape hatches for rare cases where direct low-level control is necessary.

Important:
The model should still be able to compose actions, but it should not need to reinvent search mechanics per task.

---

## 7. Required Data/Telemetry Additions

To make the refactor measurable, store more structured runtime data.

Suggested additions:
- search profile used,
- search budget spent,
- progress score before/after,
- stuck events,
- replans per task,
- search outcome category,
- death/failure reason category,
- countermeasure applied,
- whether a countermeasure reduced recurrence.

This can go into:
- existing runner status,
- bot memory logs,
- voyager issue logs.

---

## 8. Risks

### Risk 1: Over-hardcoding behavior
Mitigation:
- keep profile selection and risk policy adjustable,
- do not bury strategy inside rigid heuristics only.

### Risk 2: Too much migration at once
Mitigation:
- phase by domain,
- recovery → wood → food → ore.

### Risk 3: Prompt/runtime mismatch
Mitigation:
- add new search primitives first,
- then update action prompt/validator together.

### Risk 4: Growth regression
Mitigation:
- keep policy adaptation and skill promotion as explicit top-layer features,
- do not reduce the bot to fixed task macros.

---

## 9. Recommended First Implementation Slice

If starting immediately, the best first slice is:

1. create `search/` subsystem skeleton,
2. implement `recoverToSurface()`,
3. implement `searchAndHarvest(... goalType: "wood")`,
4. wire action prompting so wood/recovery tasks prefer the new APIs,
5. keep `exploreUntil` as fallback only.

Reason:
- highest impact,
- lowest ambiguity,
- directly addresses the current biggest failures,
- creates a reusable pattern for food and ore later.

---

## 10. Concrete Files Expected to Change First

### New files
- `third_party/Voyager/voyager/control_primitives/search/perception.js`
- `third_party/Voyager/voyager/control_primitives/search/profiles.js`
- `third_party/Voyager/voyager/control_primitives/search/planner.js`
- `third_party/Voyager/voyager/control_primitives/search/executor.js`
- `third_party/Voyager/voyager/control_primitives/search/progress.js`
- possibly `third_party/Voyager/voyager/control_primitives/search/index.js`

### Existing files likely to change
- `third_party/Voyager/voyager/control_primitives/exploreUntil.js`
- `third_party/Voyager/voyager/agents/action.py`
- `third_party/Voyager/voyager/voyager.py`
- `third_party/Voyager/voyager/agents/curriculum.py`
- possibly `third_party/Voyager/voyager/agents/skill.py`
- mineflayer-side support if additional perception helpers are needed

---

## 11. Decision Standard for the Refactor

A successful refactor should mean:

- fewer local-search stalls,
- fewer repeated hazard deaths,
- fewer useless underground surface-resource tasks,
- better search-domain selection,
- clearer failure categories,
- better post-failure recovery,
- stronger cumulative adaptation.

If the system only gets more complicated but does not reduce repeated stupidity, the refactor failed.

If the system becomes more reliable while still allowing strategic learning and skill growth, the refactor succeeded.
