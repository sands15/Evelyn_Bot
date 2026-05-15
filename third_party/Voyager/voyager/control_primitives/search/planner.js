function searchPlan(bot, options = {}) {
    const snapshot = perceiveSearchState(bot, options);
    const profile = snapshot.profile;
    const primary = snapshot.candidates.primary[0] || null;
    const recoveryTargets = snapshot.candidates.recovery || [];
    const woodScoutTargets = snapshot.candidates.woodScout || [];
    const foodScoutTargets = snapshot.candidates.foodScout || [];
    const oreScoutTargets = snapshot.candidates.oreScout || [];
    const recoveryTarget = recoveryTargets[0] || null;
    const woodScoutTarget = woodScoutTargets[0] || null;
    const foodScoutTarget = foodScoutTargets[0] || null;
    const oreScoutTarget = oreScoutTargets[0] || null;
    let mode = "expanding_probe";
    let direction = profile.preferredDirections[0] || { x: 1, y: 0, z: 0 };
    let reason = `Search for ${snapshot.goalType} using the ${snapshot.goalType} profile.`;
    let target = primary;
    let alternateTargets = [];

    if (primary) {
        mode = "targeted";
        reason = `A nearby ${snapshot.goalType} candidate is already visible.`;
    } else if (snapshot.goalType === "wood" && woodScoutTarget) {
        mode = "wood_scout";
        target = woodScoutTarget;
        alternateTargets = woodScoutTargets.slice(1, 4);
        reason = "No nearby logs are visible, but a surface waypoint with stronger tree signals is available.";
    } else if (snapshot.goalType === "food" && foodScoutTarget) {
        mode = "food_scout";
        target = foodScoutTarget;
        alternateTargets = foodScoutTargets.slice(1, 4);
        reason = "No nearby food candidate is visible, but a surface waypoint with better food signals is available.";
    } else if (snapshot.goalType === "ore" && oreScoutTarget) {
        mode = "ore_scout";
        target = oreScoutTarget;
        alternateTargets = oreScoutTargets.slice(1, 4);
        reason = "No nearby ore is visible, but an underground waypoint with better exposed-ore/cave-wall signals is available.";
    } else if (snapshot.goalType === "recovery") {
        mode = snapshot.domain === "surface" ? "stabilize" : "surface_recovery";
        direction = { x: 0, y: 1, z: 0 };
        target = recoveryTarget;
        alternateTargets = recoveryTargets.slice(1, 4);
        reason = snapshot.domain === "surface"
            ? "Already on the surface; stabilize instead of probing."
            : recoveryTarget
                ? "A sky-visible recovery waypoint is available; move there before broader probing."
                : "Recovery mode should regain the surface before anything else.";
    } else if (snapshot.domain === "underground" && ["wood", "food"].includes(snapshot.goalType)) {
        mode = "surface_recovery";
        direction = { x: 0, y: 1, z: 0 };
        target = recoveryTarget;
        alternateTargets = recoveryTargets.slice(1, 4);
        reason = recoveryTarget
            ? `${snapshot.goalType} search is underground; move to a nearby sky-visible surface waypoint first.`
            : `${snapshot.goalType} search is in the wrong domain; recover to the surface first.`;
    } else if (snapshot.hazards.hostileCount > 0 && snapshot.goalType !== "ore") {
        mode = "guarded_probe";
        direction = { x: 1, y: 0, z: 0 };
        reason = "Hostiles are nearby; use a short guarded probe instead of a long roam.";
    }

    return {
        goalType: snapshot.goalType,
        profile,
        domain: snapshot.domain,
        mode,
        direction,
        radius: snapshot.radius,
        timeBudgetSec: Number(options.maxSearchBudgetSec || profile.timeBudgetSec || 12),
        progressTimeoutSec: Number(options.progressTimeoutSec || profile.progressTimeoutSec || 8),
        target,
        alternateTargets,
        reason,
        snapshot,
    };
}
