// Intent-level search helpers (Phase 1)
//
// Use these when the task is really "find something" or "recover first".
// Prefer them over hand-written random exploreUntil loops when the goal is:
// - wood
// - food
// - recovery / surface escape
//
// Available helpers:
//
// 1) searchPlan(bot, options)
//    Returns a structured plan object describing:
//    - goalType
//    - domain
//    - mode (targeted / surface_recovery / expanding_probe / guarded_probe / stabilize)
//    - radius
//    - time budget
//    - visible target candidate if one already exists
//
// 2) searchAndMove(bot, options)
//    Runs a goal-aware search movement step.
//
//    Example:
//    await searchAndMove(bot, {
//      goalType: "food",
//      maxSearchBudgetSec: 18,
//    });
//
// 3) searchAndHarvest(bot, options)
//    Best first use: wood gathering.
//
//    Example:
//    await searchAndHarvest(bot, {
//      goalType: "wood",
//      quantity: 1,
//      maxSearchBudgetSec: 24,
//    });
//
// 4) searchAndCollectFood(bot, options)
//    Moves toward food candidates found by the food profile.
//
// 5) searchForOre(bot, options)
//    Use this when underground ore is the real target and direct ore blocks are not already visible.
//
// 6) recoverToSurface(bot, options)
//    Use this when the current task domain is wrong or after hazardous failures.
//
// Important guidance:
// - For wood/food tasks underground, recoverToSurface or searchAndMove({goalType: ...}) should happen before long probing.
// - recoverToSurface now tries to pick a nearby sky-visible standable waypoint before falling back to upward probing.
// - searchAndHarvest("wood") can use that recovery step first, then continue into a surface wood search.
// - if no log is directly visible on the surface, wood search can pick a scouting waypoint with better nearby leaf/treeline signals before falling back to raw probes.
// - food search can also pick scouting waypoints that look more promising for animals/crops/open grassland instead of probing blindly.
// - ore search can scout underground waypoints with better exposed-ore / cave-wall / target-y-band signals before giving up.
// - targeted scout/recovery moves now fail early with stalled/timeout reasons when distance is not improving, so the higher-level planner can replan instead of burning the whole budget.
// - Let the runtime pick the probe mode; do not hardcode many exploreUntil directions unless there is a special case.
// - Keep your task logic focused on intent, not raw wandering.
//
// Good pattern:
//
// const recovery = await recoverToSurface(bot, { maxSearchBudgetSec: 12 });
// if (!recovery.success) throw new Error(recovery.reason);
//
// const result = await searchAndHarvest(bot, {
//   goalType: "wood",
//   quantity: 1,
//   maxSearchBudgetSec: 24,
// });
// if (!result.success) throw new Error(result.reason || "WOOD_SEARCH_FAILED");
//
// Less preferred pattern:
// - stacking many ad-hoc exploreUntil probes with guessed directions
// - searching for wood or food underground without first correcting the search domain
