function _updateSearchExecution(bot, patch = {}) {
    if (!bot) return null;
    const previous = bot._voyagerSearchExecution && typeof bot._voyagerSearchExecution === "object"
        ? bot._voyagerSearchExecution
        : {};
    bot._voyagerSearchExecution = {
        ...previous,
        ...patch,
        recordedAt: new Date().toISOString(),
    };
    return bot._voyagerSearchExecution;
}

function _currentSearchBudgetSpent(bot) {
    const startedAt = bot && bot._voyagerSearchExecution ? bot._voyagerSearchExecution.startedAt : null;
    if (!startedAt) return null;
    const startedAtMs = Date.parse(startedAt);
    if (!Number.isFinite(startedAtMs)) return null;
    return Number(((Date.now() - startedAtMs) / 1000).toFixed(2));
}

function _failureCategory(reason) {
    const normalized = String(reason || "");
    if (!normalized) return null;
    if (normalized.includes("stalled")) return "stuck";
    if (normalized.includes("timeout")) return "timeout";
    if (normalized.includes("exhausted")) return "exhausted";
    if (normalized.includes("missing_after_search") || normalized.includes("target_missing")) return "missing_target";
    if (normalized === "surface_not_found") return "wrong_domain";
    if (normalized === "search_budget_exhausted") return "budget_exhausted";
    return "failed";
}

function _searchPolicyBucket(bot, goalType = "generic") {
    if (!bot._voyagerSearchPolicy || typeof bot._voyagerSearchPolicy !== "object") {
        bot._voyagerSearchPolicy = {};
    }
    const key = goalType || "generic";
    if (!bot._voyagerSearchPolicy[key] || typeof bot._voyagerSearchPolicy[key] !== "object") {
        bot._voyagerSearchPolicy[key] = {
            radiusBonus: 0,
            timeBudgetScale: 1,
            progressTimeoutScale: 1,
            forceDomain: null,
            consecutiveFailures: 0,
            successes: 0,
            failures: 0,
            lastFailureCategory: null,
            lastFailureReason: null,
            updatedAt: null,
        };
    }
    return bot._voyagerSearchPolicy[key];
}

function _applySearchOutcomePolicy(bot, goalType, success, reason, failureCategory) {
    const policy = _searchPolicyBucket(bot, goalType);
    if (success) {
        policy.successes = Number(policy.successes || 0) + 1;
        policy.consecutiveFailures = 0;
        policy.radiusBonus = Math.max(0, Number(policy.radiusBonus || 0) - 1);
        policy.timeBudgetScale = Math.max(1, Number((Number(policy.timeBudgetScale || 1) - 0.05).toFixed(2)));
        policy.progressTimeoutScale = Math.min(1, Number((Number(policy.progressTimeoutScale || 1) + 0.05).toFixed(2)));
    } else {
        policy.failures = Number(policy.failures || 0) + 1;
        policy.consecutiveFailures = Number(policy.consecutiveFailures || 0) + 1;
        policy.lastFailureCategory = failureCategory || null;
        policy.lastFailureReason = reason || null;
        if (["exhausted", "missing_target"].includes(failureCategory)) {
            policy.radiusBonus = Math.min(2, Number(policy.radiusBonus || 0) + 1);
            policy.timeBudgetScale = Math.min(1.5, Number((Number(policy.timeBudgetScale || 1) + 0.1).toFixed(2)));
        }
        if (failureCategory === "stuck") {
            policy.progressTimeoutScale = Math.min(1.5, Number((Number(policy.progressTimeoutScale || 1) + 0.15).toFixed(2)));
            policy.timeBudgetScale = Math.min(1.5, Number((Number(policy.timeBudgetScale || 1) + 0.05).toFixed(2)));
        }
        if (failureCategory === "timeout") {
            policy.progressTimeoutScale = Math.min(1.25, Number((Number(policy.progressTimeoutScale || 1) + 0.05).toFixed(2)));
            policy.timeBudgetScale = Math.min(1.5, Number((Number(policy.timeBudgetScale || 1) + 0.1).toFixed(2)));
        }
        if (["wood", "food"].includes(goalType) && ["surface_not_found", "surface_recovery_exhausted"].includes(reason)) {
            policy.forceDomain = "surface";
        } else if (goalType === "ore" && reason === "surface_not_found") {
            policy.forceDomain = "underground";
        }
    }
    policy.updatedAt = new Date().toISOString();
    return {
        ...policy,
    };
}

function _successOutcomeCategory(plan, reason) {
    if (reason === "already_surface_stable" || reason === "surface_reached") return "recovered_domain";
    if (reason === "candidate_found") return "candidate_acquired";
    if (reason === "food_candidate_reached") return "food_candidate_reached";
    if (reason === "harvest_attempt_started") return "harvest_started";
    if (reason === "ore_harvest_attempt_started") return "ore_harvest_started";
    if (reason === "reached_waypoint") {
        if (plan && ["wood_scout", "food_scout", "ore_scout"].includes(plan.mode)) {
            return "scout_repositioned";
        }
        return "waypoint_reached";
    }
    return "search_success";
}

function _countermeasureApplied(plan) {
    if (!plan) return null;
    if (plan.goalType === "recovery" || plan.mode === "surface_recovery") return "surface_recovery";
    if (plan.mode === "guarded_probe") return "guarded_probe";
    return null;
}

function _canAttemptBlockActionAfterPartialMove(moveResult, maxDistance = 12) {
    if (!moveResult || moveResult.success) return false;
    const finalDistance = Number(moveResult.finalDistance);
    const progressDistance = Number(moveResult.progressDistance);
    if (!Number.isFinite(finalDistance)) return false;
    if (finalDistance > maxDistance) return false;
    return !Number.isFinite(progressDistance) || progressDistance >= 3;
}

function _recordSearchStart(bot, helper, plan, options = {}) {
    const progressBefore = evaluateSearchProgress(bot, {
        ...options,
        goalType: plan && plan.goalType ? plan.goalType : options.goalType || "generic",
    });
    return _updateSearchExecution(bot, {
        helper,
        goalType: plan && plan.goalType ? plan.goalType : options.goalType || "generic",
        mode: plan && plan.mode ? plan.mode : null,
        profileDomain: plan && plan.profile ? plan.profile.domain || null : null,
        radius: plan && typeof plan.radius === "number" ? plan.radius : null,
        timeBudgetSec: plan && typeof plan.timeBudgetSec === "number" ? plan.timeBudgetSec : null,
        progressTimeoutSec: plan && typeof plan.progressTimeoutSec === "number" ? plan.progressTimeoutSec : null,
        status: "started",
        reason: null,
        attemptedTargets: 0,
        maxAlternateTargets: plan && Array.isArray(plan.alternateTargets) ? plan.alternateTargets.length : 0,
        replans: 0,
        stuckEvents: 0,
        outcomeCategory: null,
        failureCategory: null,
        countermeasureApplied: _countermeasureApplied(plan),
        searchPolicy: _searchPolicyBucket(bot, plan && plan.goalType ? plan.goalType : options.goalType || "generic"),
        progressBefore,
        progressAfter: null,
        progressDelta: null,
        startedAt: new Date().toISOString(),
    });
}

function _recordSearchResult(bot, patch = {}) {
    return _updateSearchExecution(bot, {
        status: patch.success ? "success" : "failed",
        ...patch,
    });
}

function _finalizeSearchResult(bot, plan, patch = {}) {
    const progressAfter = evaluateSearchProgress(bot, {
        goalType: plan && plan.goalType ? plan.goalType : patch.goalType || "generic",
    });
    const current = bot && bot._voyagerSearchExecution && typeof bot._voyagerSearchExecution === "object"
        ? bot._voyagerSearchExecution
        : {};
    const progressBefore = current.progressBefore && typeof current.progressBefore === "object"
        ? current.progressBefore
        : null;
    const beforeScore = progressBefore && typeof progressBefore.score === "number" ? progressBefore.score : null;
    const afterScore = progressAfter && typeof progressAfter.score === "number" ? progressAfter.score : null;
    const success = !!patch.success;
    const reason = patch.reason || null;
    const failureCategory = success ? null : (patch.failureCategory || _failureCategory(reason));
    const searchPolicy = _applySearchOutcomePolicy(
        bot,
        plan && plan.goalType ? plan.goalType : patch.goalType || "generic",
        success,
        reason,
        failureCategory
    );
    return _recordSearchResult(bot, {
        progressAfter,
        progressDelta: beforeScore !== null && afterScore !== null ? Number((afterScore - beforeScore).toFixed(2)) : null,
        budgetSpentSec: _currentSearchBudgetSpent(bot),
        outcomeCategory: success ? _successOutcomeCategory(plan, reason) : patch.outcomeCategory || _failureCategory(reason),
        failureCategory,
        countermeasureApplied: patch.countermeasureApplied || current.countermeasureApplied || _countermeasureApplied(plan),
        searchPolicy,
        ...patch,
    });
}

function _incrementSearchReplans(bot, amount = 1) {
    const current = bot && bot._voyagerSearchExecution && typeof bot._voyagerSearchExecution === "object"
        ? bot._voyagerSearchExecution
        : {};
    const nextValue = Number(current.replans || 0) + Number(amount || 0);
    return _updateSearchExecution(bot, {
        replans: nextValue,
    });
}

async function searchAndMove(bot, options = {}) {
    const plan = searchPlan(bot, options);
    const baseReplans = Number(bot && bot._voyagerSearchExecution ? bot._voyagerSearchExecution.replans || 0 : 0);
    if (options.continueExecution) {
        _updateSearchExecution(bot, {
            helper: options.helperName || "searchAndMove",
            goalType: plan && plan.goalType ? plan.goalType : options.goalType || "generic",
            mode: plan && plan.mode ? plan.mode : null,
            profileDomain: plan && plan.profile ? plan.profile.domain || null : null,
            radius: plan && typeof plan.radius === "number" ? plan.radius : null,
            timeBudgetSec: plan && typeof plan.timeBudgetSec === "number" ? plan.timeBudgetSec : null,
            progressTimeoutSec: plan && typeof plan.progressTimeoutSec === "number" ? plan.progressTimeoutSec : null,
            maxAlternateTargets: plan && Array.isArray(plan.alternateTargets) ? plan.alternateTargets.length : 0,
        });
    } else {
        _recordSearchStart(bot, options.helperName || "searchAndMove", plan, options);
    }
    if (plan.mode === "stabilize") {
        _finalizeSearchResult(bot, plan, {
            success: true,
            reason: "already_surface_stable",
            attemptedTargets: 0,
        });
        return {
            success: true,
            reason: "already_surface_stable",
            plan,
        };
    }
    if (plan.target && plan.target.position) {
        const targetChain = [plan.target, ...(Array.isArray(plan.alternateTargets) ? plan.alternateTargets : [])]
            .filter((candidate) => candidate && candidate.position);
        let lastMoveResult = null;
        let stuckEvents = 0;
        for (const candidate of targetChain) {
            const candidateIndex = targetChain.indexOf(candidate);
            const moveResult = await _moveTowardPosition(
                bot,
                candidate.position,
                plan.timeBudgetSec,
                options.reach || 2,
                plan.progressTimeoutSec
            );
            stuckEvents += Number(moveResult.stallEvents || 0);
            if (moveResult.success) {
                _finalizeSearchResult(bot, plan, {
                    success: true,
                    reason: moveResult.reason,
                    attemptedTargets: candidateIndex + 1,
                    replans: baseReplans + candidateIndex,
                    stuckEvents,
                    selectedTargetType: candidate.type || null,
                    selectedTargetDistance: candidate.distance || null,
                    moveTelemetry: moveResult,
                });
                return {
                    ...moveResult,
                    plan,
                    target: candidate,
                    attemptedTargets: targetChain.length,
                };
            }
            lastMoveResult = moveResult;
            const normalizedReason = _movementFailureReason(plan.mode, moveResult.reason);
            if (!["surface_recovery_stalled", "surface_recovery_timeout", "wood_scout_stalled", "wood_scout_timeout", "food_scout_stalled", "food_scout_timeout", "ore_scout_stalled", "ore_scout_timeout"].includes(normalizedReason)) {
                _finalizeSearchResult(bot, plan, {
                    success: false,
                    reason: normalizedReason,
                    attemptedTargets: candidateIndex + 1,
                    replans: baseReplans + candidateIndex,
                    stuckEvents,
                    selectedTargetType: candidate.type || null,
                    selectedTargetDistance: candidate.distance || null,
                    moveTelemetry: moveResult,
                });
                return {
                    ...moveResult,
                    reason: normalizedReason,
                    plan,
                    target: candidate,
                    attemptedTargets: targetChain.length,
                };
            }
        }
        const exhaustedReason = plan.mode === "surface_recovery"
            ? "surface_recovery_exhausted"
            : plan.mode === "wood_scout"
                ? "wood_scout_exhausted"
                : plan.mode === "food_scout"
                    ? "food_scout_exhausted"
                    : plan.mode === "ore_scout"
                        ? "ore_scout_exhausted"
                        : _movementFailureReason(plan.mode, lastMoveResult ? lastMoveResult.reason : "movement_timeout");
        _finalizeSearchResult(bot, plan, {
            success: false,
            reason: exhaustedReason,
            attemptedTargets: targetChain.length,
            replans: baseReplans + Math.max(0, targetChain.length - 1),
            stuckEvents,
            selectedTargetType: plan.target && plan.target.type ? plan.target.type : null,
            selectedTargetDistance: plan.target && plan.target.distance ? plan.target.distance : null,
            moveTelemetry: lastMoveResult,
        });
        return {
            ...(lastMoveResult || { success: false, reason: "movement_timeout" }),
            reason: exhaustedReason,
            plan,
            target: plan.target,
            attemptedTargets: targetChain.length,
        };
    }
    const direction = new Vec3(plan.direction.x, plan.direction.y, plan.direction.z);
    const discovered = await exploreUntil(bot, direction, plan.timeBudgetSec, () => {
        const snapshot = perceiveSearchState(bot, {
            ...options,
            goalType: plan.goalType,
        });
        if (plan.goalType === "recovery" && snapshot.domain === "surface") {
            return {
                type: "state",
                state: "surface",
                position: bot.entity.position.clone(),
            };
        }
        return snapshot.candidates.primary[0] || false;
    });
    if (!discovered) {
        const failedReason = plan.mode === "surface_recovery"
            ? "surface_not_found"
            : plan.mode === "wood_scout"
                ? "wood_scout_exhausted"
                : plan.mode === "food_scout"
                    ? "food_scout_exhausted"
                    : plan.mode === "ore_scout"
                        ? "ore_scout_exhausted"
                        : "search_budget_exhausted";
        _finalizeSearchResult(bot, plan, {
            success: false,
            reason: failedReason,
            attemptedTargets: 0,
        });
        return {
            success: false,
            reason: failedReason,
            plan,
        };
    }
    const successReason = discovered.type === "state" ? "surface_reached" : "candidate_found";
    _finalizeSearchResult(bot, plan, {
        success: true,
        reason: successReason,
        attemptedTargets: 0,
        selectedTargetType: discovered.type || null,
        selectedTargetDistance: discovered.distance || null,
    });
    return {
        success: true,
        reason: successReason,
        plan,
        target: discovered.type === "state" ? null : discovered,
        discovered,
    };
}

async function searchAndHarvest(bot, options = {}) {
    const normalized = {
        ...options,
        goalType: options.goalType || "wood",
        helperName: "searchAndHarvest",
    };
    const moveResult = await searchAndMove(bot, normalized);
    const partialHarvestAttempt = _canAttemptBlockActionAfterPartialMove(moveResult, 12);
    if (!moveResult.success && !partialHarvestAttempt) {
        return moveResult;
    }
    let snapshot = perceiveSearchState(bot, normalized);
    let target = moveResult.target || snapshot.candidates.blocks[0] || null;
    if ((!target || !target.blockName) && moveResult.reason === "surface_reached") {
        _incrementSearchReplans(bot, 1);
        const followUp = await searchAndMove(bot, {
            ...normalized,
            continueExecution: true,
            maxSearchBudgetSec: Math.max(10, Number(normalized.maxSearchBudgetSec || 14)),
        });
        if (!followUp.success) {
            return followUp;
        }
        snapshot = perceiveSearchState(bot, normalized);
        target = followUp.target || snapshot.candidates.blocks[0] || null;
    }
    if (!target || !target.blockName) {
        _finalizeSearchResult(bot, moveResult.plan, {
            success: false,
            reason: "harvest_target_missing_after_search",
            targetBlockName: null,
        });
        return {
            success: false,
            reason: "harvest_target_missing_after_search",
            plan: moveResult.plan,
        };
    }
    await mineBlock(bot, target.blockName, Number(options.quantity || 1));
    _finalizeSearchResult(bot, moveResult.plan, {
        success: true,
        reason: "harvest_attempt_started",
        partialMoveAttempt: partialHarvestAttempt || null,
        targetBlockName: target.blockName,
        selectedTargetType: target.type || "block",
        selectedTargetDistance: target.distance || null,
    });
    return {
        success: true,
        reason: "harvest_attempt_started",
        plan: moveResult.plan,
        target,
    };
}

async function searchAndCollectFood(bot, options = {}) {
    const normalized = {
        ...options,
        goalType: "food",
        helperName: "searchAndCollectFood",
    };
    const moveResult = await searchAndMove(bot, normalized);
    if (!moveResult.success) {
        return moveResult;
    }
    let snapshot = perceiveSearchState(bot, normalized);
    let target = moveResult.target || snapshot.candidates.primary[0] || null;
    if (!target && (moveResult.reason === "surface_reached" || moveResult.reason === "reached_waypoint")) {
        _incrementSearchReplans(bot, 1);
        const followUp = await searchAndMove(bot, {
            ...normalized,
            continueExecution: true,
            maxSearchBudgetSec: Math.max(10, Number(normalized.maxSearchBudgetSec || 14)),
        });
        if (!followUp.success) {
            return followUp;
        }
        snapshot = perceiveSearchState(bot, normalized);
        target = followUp.target || snapshot.candidates.primary[0] || null;
    }
    _finalizeSearchResult(bot, moveResult.plan, {
        success: !!target,
        reason: target ? "food_candidate_reached" : "food_candidate_missing_after_search",
        selectedTargetType: target ? target.type || null : null,
        selectedTargetDistance: target ? target.distance || null : null,
        targetEntityName: target ? target.entityName || null : null,
        targetBlockName: target ? target.blockName || null : null,
    });
    return {
        success: !!target,
        reason: target ? "food_candidate_reached" : "food_candidate_missing_after_search",
        plan: moveResult.plan,
        target,
    };
}

async function searchForOre(bot, options = {}) {
    const normalized = {
        ...options,
        goalType: "ore",
        helperName: "searchForOre",
    };
    const moveResult = await searchAndMove(bot, normalized);
    const partialOreAttempt = _canAttemptBlockActionAfterPartialMove(moveResult, 10);
    if (!moveResult.success && !partialOreAttempt) {
        return moveResult;
    }
    let snapshot = perceiveSearchState(bot, normalized);
    let target = moveResult.target || snapshot.candidates.blocks[0] || null;
    if ((!target || !target.blockName) && (moveResult.reason === "reached_waypoint" || moveResult.reason === "candidate_found")) {
        _incrementSearchReplans(bot, 1);
        const followUp = await searchAndMove(bot, {
            ...normalized,
            continueExecution: true,
            maxSearchBudgetSec: Math.max(10, Number(normalized.maxSearchBudgetSec || 12)),
        });
        if (!followUp.success) {
            return followUp;
        }
        snapshot = perceiveSearchState(bot, normalized);
        target = followUp.target || snapshot.candidates.blocks[0] || null;
    }
    if (!target || !target.blockName) {
        _finalizeSearchResult(bot, moveResult.plan, {
            success: false,
            reason: "ore_target_missing_after_search",
            targetBlockName: null,
        });
        return {
            success: false,
            reason: "ore_target_missing_after_search",
            plan: moveResult.plan,
        };
    }
    await mineBlock(bot, target.blockName, Number(options.quantity || 1));
    _finalizeSearchResult(bot, moveResult.plan, {
        success: true,
        reason: "ore_harvest_attempt_started",
        partialMoveAttempt: partialOreAttempt || null,
        targetBlockName: target.blockName,
        selectedTargetType: target.type || "block",
        selectedTargetDistance: target.distance || null,
    });
    return {
        success: true,
        reason: "ore_harvest_attempt_started",
        plan: moveResult.plan,
        target,
    };
}

async function recoverToSurface(bot, options = {}) {
    return searchAndMove(bot, {
        ...options,
        goalType: "recovery",
        helperName: "recoverToSurface",
    });
}

async function searchAndAct(bot, options = {}) {
    const goalType = options.goalType || "generic";
    if (goalType === "recovery") {
        return recoverToSurface(bot, options);
    }
    if (goalType === "wood") {
        return searchAndHarvest(bot, options);
    }
    if (goalType === "food") {
        return searchAndCollectFood(bot, options);
    }
    if (goalType === "ore") {
        return searchForOre(bot, options);
    }
    return searchAndMove(bot, options);
}
