# Growth-Oriented Bot Refactor Completion Checklist

Last reviewed: 2026-05-15
Branch: `structural-change`
Standard: **strict**

This checklist is intentionally harsher than a normal progress note.
The refactor is **not done** until every required item below is green and live-verified.

---

## Strict completion gate

A phase is only "done" when all of these are true:

1. The code path exists in the live runtime, not just in docs.
2. The old behavior is no longer the default for that task class.
3. Status/telemetry exposes enough evidence to distinguish success, stall, timeout, wrong-domain search, and exhaustion.
4. At least one realistic live path has been verified without relying on optimistic interpretation.
5. Fallback behavior is bounded and does not silently become the main path again.

If any one of those is false, mark the item **partial** or **not done**.

Legend:
- `[x]` done
- `[~]` partial / in progress
- `[ ]` not done

---

## 1) Runtime search subsystem exists

- [x] `search/perception.js` exists in live Voyager control primitives.
- [x] `search/profiles.js` exists.
- [x] `search/planner.js` exists.
- [x] `search/executor.js` exists.
- [x] `search/progress.js` exists.
- [~] The subsystem is clearly the preferred path for target domains, but legacy fallback still exists.

Evidence:
- `third_party/Voyager/voyager/control_primitives/search/`
- `third_party/Voyager/voyager/agents/action.py`

## 2) Recovery-first domain correction

- [x] `recoverToSurface()` exists.
- [x] Underground wood/food search can redirect into surface recovery.
- [x] Recovery now prefers standable sky-visible waypoints before blind probing.
- [~] Live verification of repeated post-death recovery reliability is still incomplete.

## 3) Wood search refactor

- [x] `searchAndHarvest(... goalType: "wood")` exists.
- [x] Wood scout candidates are perception-backed instead of pure random wandering.
- [x] Underground wood search can fall back to surface recovery first.
- [~] Reliability gate not yet proven strict enough to call wood acquisition "done".

## 4) Food search refactor

- [x] `searchAndCollectFood()` exists.
- [x] Food candidates include entities/crops and surface scout waypoints.
- [~] Hunger urgency policy is only partially explicit.
- [~] Live verification that food search avoids long underground stall loops is still needed.

## 5) Ore search refactor

- [x] `searchForOre()` exists.
- [x] Ore scouting is underground-biased and y-band aware.
- [~] Failure categories are better, but still need stricter live proof for wrong-elevation / unsafe-path distinctions.
- [~] Recent live run still showed `Mine 8 iron ore` failure, so this phase is not complete.

Evidence:
- `bot_memory/upstream_bridge_status.json` showed recent `Mine 8 iron ore` failure.

## 6) Prompt and validator migration

- [x] Action prompt teaches intent-level helpers.
- [x] Validator discourages hand-written `exploreUntil(...)` loops.
- [x] Wood/food/ore tasks are nudged toward new helper APIs.
- [~] Legacy exploration still remains as compatibility fallback, so migration is not fully complete.

## 7) Legacy `exploreUntil` de-emphasis

- [x] New helper path is documented as preferred.
- [x] Validator bounds `exploreUntil` usage.
- [~] `exploreUntil` still exists in the live runtime and can still execute as fallback.
- [ ] Legacy path has not yet been reduced to rare edge-case-only behavior with proof.

## 8) Structured telemetry and measurability

- [x] Search execution telemetry is surfaced through runtime status.
- [x] Search metrics now include helper/mode/status attempts.
- [x] Search telemetry now records progress-before/progress-after, budget spent, replans, stuck events, outcome category, failure category, countermeasure tag, and search-policy adjustments.
- [~] Recurrence reduction is not yet measured over time.
- [~] Telemetry exists, but needs live capture review after the next run.

## 9) Reflective adaptation / policy growth

- [x] Death-derived countermeasures exist.
- [x] Policy state storage exists (`policy_state.json`).
- [x] Search-specific repeated failure patterns now update persisted search policy state by goal type.
- [x] There is now an explicit rule path where repeated search failures automatically adjust profile parameters by goal type (radius/time-budget/progress-timeout/domain bias).
- [~] Success-to-skill promotion rules are stricter than before, but still need live proof before this section can be called finished.

## 10) Runtime contracts around status / result bookkeeping

- [~] Search helper telemetry is improving.
- [~] Task/result bookkeeping still needs final hardening.
- [~] Resume contract is still incomplete (`resume_enabled: false` was observed because `missing_observe_history`).
- [~] Health / recovery layers are documented, but not yet fully hardened as a completion gate.

Evidence:
- `CURRENT_EVELYN_ARCHITECTURE.md`
- `bot_memory/upstream_bridge_status.json`

---

## Current strict verdict

**Verdict: NOT COMPLETE**

### Why not complete yet

The refactor has clearly moved beyond the design-only stage, but strict completion still fails on these fronts:

1. **Live reliability proof is incomplete** for wood/food/ore domains.
2. **Legacy exploration fallback still exists** and has not yet been proven to be rare edge-only behavior.
3. **Live proof is still missing** that the new repeated-failure adaptation materially improves later runs.
4. **Resume/result/health contracts are still not fully hardened**.
5. **Recent ore task failure** means ore search cannot be called done under a strict standard.

---

## Recommended next completion targets

1. **Telemetry capture review**
   - Confirm the new search telemetry actually appears in live status payloads.
2. **Ore failure hardening**
   - Tighten failure semantics around ore search and verify with a live rerun.
3. **Live validation of repeated-failure adaptation**
   - Confirm persisted/adaptive search policy actually changes later run behavior in telemetry.
4. **Resume/result contract hardening**
   - Close the gap where runner status can be alive while resume safety is disabled.

Until those are green, this refactor should be reported as **substantial progress, not finished**.
