function _sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function _roundMetric(value, digits = 2) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return null;
    return Number(numeric.toFixed(digits));
}

function evaluateSearchProgress(bot, options = {}) {
    const snapshot = perceiveSearchState(bot, options);
    const primary = snapshot && snapshot.candidates && Array.isArray(snapshot.candidates.primary)
        ? (snapshot.candidates.primary[0] || null)
        : null;
    const blockCount = snapshot && snapshot.candidates && Array.isArray(snapshot.candidates.blocks)
        ? snapshot.candidates.blocks.length
        : 0;
    const entityCount = snapshot && snapshot.candidates && Array.isArray(snapshot.candidates.entities)
        ? snapshot.candidates.entities.length
        : 0;
    const recoveryCount = snapshot && snapshot.candidates && Array.isArray(snapshot.candidates.recovery)
        ? snapshot.candidates.recovery.length
        : 0;
    const scoutCount = snapshot && snapshot.candidates
        ? [
            snapshot.candidates.woodScout,
            snapshot.candidates.foodScout,
            snapshot.candidates.oreScout,
        ].reduce((total, list) => total + (Array.isArray(list) ? list.length : 0), 0)
        : 0;
    const candidateCount = blockCount + entityCount;
    const hostileCount = snapshot && snapshot.hazards ? Number(snapshot.hazards.hostileCount || 0) : 0;
    const profileDomain = snapshot && snapshot.profile ? String(snapshot.profile.domain || "adaptive") : "adaptive";
    let domainFitness = 0;
    if (snapshot.goalType === "recovery") {
        domainFitness = snapshot.domain === "surface" ? 24 : -18;
    } else if (profileDomain === "surface") {
        domainFitness = snapshot.domain === "surface" ? 18 : -24;
    } else if (profileDomain === "underground") {
        domainFitness = snapshot.domain === "underground" ? 18 : -18;
    }
    const proximityBonus = primary && typeof primary.distance === "number"
        ? Math.max(0, Number(snapshot.radius || 0) - primary.distance) * 1.35
        : 0;
    const score = domainFitness + candidateCount * 14 + recoveryCount * 5 + scoutCount * 3 + proximityBonus - hostileCount * 9;
    return {
        goalType: snapshot.goalType,
        domain: snapshot.domain,
        profileDomain,
        radius: snapshot.radius,
        score: _roundMetric(score),
        primaryDistance: primary && typeof primary.distance === "number" ? _roundMetric(primary.distance) : null,
        candidateCount,
        blockCandidateCount: blockCount,
        entityCandidateCount: entityCount,
        recoveryCandidateCount: recoveryCount,
        scoutCandidateCount: scoutCount,
        hostileCount,
        recordedAt: new Date().toISOString(),
    };
}

function _movementFailureReason(mode = "targeted", baseReason = "movement_timeout") {
    if (baseReason === "movement_stalled") {
        if (mode === "surface_recovery") return "surface_recovery_stalled";
        if (mode === "wood_scout") return "wood_scout_stalled";
        if (mode === "food_scout") return "food_scout_stalled";
        if (mode === "ore_scout") return "ore_scout_stalled";
        return "movement_stalled";
    }
    if (baseReason === "movement_timeout") {
        if (mode === "surface_recovery") return "surface_recovery_timeout";
        if (mode === "wood_scout") return "wood_scout_timeout";
        if (mode === "food_scout") return "food_scout_timeout";
        if (mode === "ore_scout") return "ore_scout_timeout";
    }
    return baseReason;
}

async function _moveTowardPosition(bot, positionLike, maxTimeSec = 15, reach = 2, progressTimeoutSec = 8) {
    const position = _toPos(positionLike);
    if (!position) {
        return { success: false, reason: "missing_position" };
    }
    const goal = new GoalNear(position.x, position.y, position.z, reach);
    bot.pathfinder.setGoal(goal);
    const startedAt = Date.now();
    const deadline = startedAt + maxTimeSec * 1000;
    const startDistance = _distance(bot.entity.position, position);
    let bestDistance = startDistance;
    let lastImprovementAt = startedAt;
    let lastMovementAt = startedAt;
    let lastPosition = bot.entity.position.clone();
    const movementStallMs = Math.max(3500, progressTimeoutSec * 500);
    while (Date.now() < deadline) {
        const currentDistance = _distance(bot.entity.position, position);
        if (currentDistance <= reach + 0.5) {
            try {
                bot.pathfinder.setGoal(null);
            } catch (err) {}
            return {
                success: true,
                reason: "reached_waypoint",
                position,
                startDistance: _roundMetric(startDistance),
                finalDistance: _roundMetric(currentDistance),
                bestDistance: _roundMetric(bestDistance),
                progressDistance: _roundMetric(startDistance - Math.min(bestDistance, currentDistance)),
                stallEvents: 0,
                elapsedSec: _roundMetric((Date.now() - startedAt) / 1000),
            };
        }
        const moved = _distance(bot.entity.position, lastPosition);
        if (moved >= 0.75) {
            lastMovementAt = Date.now();
            lastPosition = bot.entity.position.clone();
        }
        if (currentDistance + 0.75 < bestDistance) {
            bestDistance = currentDistance;
            lastImprovementAt = Date.now();
        }
        if (Date.now() - lastMovementAt > movementStallMs || Date.now() - lastImprovementAt > progressTimeoutSec * 1000) {
            try {
                bot.pathfinder.setGoal(null);
            } catch (err) {}
            return {
                success: false,
                reason: "movement_stalled",
                position,
                startDistance: _roundMetric(startDistance),
                finalDistance: _roundMetric(currentDistance),
                bestDistance: _roundMetric(bestDistance),
                progressDistance: _roundMetric(startDistance - Math.min(bestDistance, currentDistance)),
                stallEvents: 1,
                elapsedSec: _roundMetric((Date.now() - startedAt) / 1000),
            };
        }
        await _sleep(250);
    }
    try {
        bot.pathfinder.setGoal(null);
    } catch (err) {}
    return {
        success: false,
        reason: "movement_timeout",
        position,
        startDistance: _roundMetric(startDistance),
        finalDistance: _roundMetric(_distance(bot.entity.position, position)),
        bestDistance: _roundMetric(bestDistance),
        progressDistance: _roundMetric(startDistance - bestDistance),
        stallEvents: 0,
        elapsedSec: _roundMetric((Date.now() - startedAt) / 1000),
    };
}
