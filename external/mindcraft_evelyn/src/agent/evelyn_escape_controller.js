import pf from 'mineflayer-pathfinder';
import Vec3 from 'vec3';

const HAZARD_BLOCKS = new Set([
    'bubble_column', 'cactus', 'campfire', 'fire', 'lava', 'magma_block',
    'powder_snow', 'soul_campfire', 'sweet_berry_bush', 'water', 'withering_rose',
]);
const PASSABLE_BLOCKS = new Set(['air', 'cave_air', 'void_air']);
const DEFAULT_SAFE_DISTANCE = 18;
const DEFAULT_RANGE = 24;
const ESCAPE_DIRECTION_TTL_MS = 2500;
const ESCAPE_DIRECTION_WEIGHT = 4;
const ESCAPE_HINT_WEIGHT = 24;
const MAX_SPRINT_CORRIDOR_SAMPLES = 24;
const MAX_SPRINT_SUPPORT_DEPTH = 2;
const MAX_SAFE_SPRINT_DROP = 1.25;
const DANGEROUS_DOWNWARD_VELOCITY = -0.3;
const FALL_RISK_VERIFICATION = 'fall_risk';
const INCOMPLETE_BURST_VERIFICATION = 'escape_burst_incomplete';
const RECENT_ESCAPE_DIRECTIONS = new WeakMap();
export const MAX_INTERRUPT_OPTOUT_MS = 1200;

function positionOf(value) {
    const position = value?.entity?.position || value?.position || value;
    if (
        !position ||
        !Number.isFinite(Number(position.x)) ||
        !Number.isFinite(Number(position.y)) ||
        !Number.isFinite(Number(position.z))
    ) return null;
    return {
        x: Number(position.x),
        y: Number(position.y),
        z: Number(position.z),
    };
}

function horizontalDistance(left, right) {
    return Math.hypot(Number(left.x) - Number(right.x), Number(left.z) - Number(right.z));
}

function horizontalDirection(from, to) {
    const dx = Number(to?.x) - Number(from?.x);
    const dz = Number(to?.z) - Number(from?.z);
    const length = Math.hypot(dx, dz);
    if (!Number.isFinite(length) || length < 0.001) return null;
    return {x: dx / length, z: dz / length};
}

export function escapeSafetyScore(position, hostiles) {
    const origin = positionOf(position);
    const threats = (hostiles || []).map(positionOf).filter(Boolean);
    if (!origin || !threats.length) return Number.POSITIVE_INFINITY;
    const distances = threats.map((hostile) => horizontalDistance(origin, hostile));
    const minimum = Math.min(...distances);
    const average = distances.reduce((sum, distance) => sum + distance, 0) / distances.length;
    return minimum * 4 + average;
}

export function buildEscapeCandidates(originValue, hostiles, {
    recoveryMode = false,
    radii = [5, 9],
} = {}) {
    const origin = positionOf(originValue);
    const threats = (hostiles || []).map(positionOf).filter(Boolean);
    if (!origin || !threats.length) return [];

    let repelX = 0;
    let repelZ = 0;
    for (const hostile of threats) {
        const dx = origin.x - hostile.x;
        const dz = origin.z - hostile.z;
        const distanceSquared = Math.max(1, dx * dx + dz * dz);
        repelX += dx / distanceSquared;
        repelZ += dz / distanceSquared;
    }
    const baseAngle = Math.abs(repelX) + Math.abs(repelZ) > 0.0001
        ? Math.atan2(repelZ, repelX)
        : 0;
    const offsets = recoveryMode
        ? [0, Math.PI / 4, -Math.PI / 4, Math.PI / 2, -Math.PI / 2, Math.PI]
        : [0, Math.PI / 4, -Math.PI / 4, Math.PI / 2, -Math.PI / 2];
    const verticalOffsets = recoveryMode ? [0, 2, 4] : [0];

    const candidates = [];
    for (const radius of radii) {
        for (const offset of offsets) {
            for (const verticalOffset of verticalOffsets) {
                const angle = baseAngle + offset;
                candidates.push({
                    x: Math.floor(origin.x + Math.cos(angle) * radius),
                    y: Math.floor(origin.y + verticalOffset),
                    z: Math.floor(origin.z + Math.sin(angle) * radius),
                    radius,
                    angle,
                });
            }
        }
    }
    return candidates;
}

export function chooseEscapeCandidate(candidates, hostiles, {
    origin = null,
    recoveryMode = false,
    surfaceY = null,
    isViable = () => true,
    coverScore = () => 0,
    preferredDirection = null,
    directionWeight = 0,
} = {}) {
    const start = positionOf(origin);
    const preferred = horizontalDirection({x: 0, z: 0}, preferredDirection);
    const boundedDirectionWeight = Math.max(0, Number(directionWeight) || 0);
    return (candidates || [])
        .filter((candidate) => isViable(candidate))
        .map((candidate) => {
            const safety = escapeSafetyScore(candidate, hostiles);
            const cover = Number(coverScore(candidate) || 0);
            const direction = start && preferred
                ? horizontalDirection(start, candidate)
                : null;
            const commitment = direction
                ? (direction.x * preferred.x + direction.z * preferred.z) * boundedDirectionWeight
                : 0;
            const upward = (
                recoveryMode &&
                Number.isFinite(Number(surfaceY)) &&
                start
            )
                ? Math.max(0, Math.min(Number(surfaceY), candidate.y) - start.y)
                : 0;
            return {
                candidate,
                score: safety + cover * (recoveryMode ? 12 : 4) + upward * 3 + commitment,
            };
        })
        .sort((left, right) => right.score - left.score)[0]?.candidate || null;
}

function liveHostiles(bot, range) {
    const origin = bot?.entity?.position;
    if (!origin) return [];
    return Object.values(bot.entities || {})
        .filter((entity) => entity?.position && entity !== bot.entity)
        .map((entity) => ({entity, distance: origin.distanceTo(entity.position)}))
        .filter(({entity, distance}) => (
            entity?.type === 'mob' &&
            Number.isFinite(distance) &&
            distance <= range &&
            !['armor_stand'].includes(String(entity.name || '').toLowerCase())
        ))
        .sort((left, right) => left.distance - right.distance);
}

function isPassable(block) {
    return Boolean(block && (PASSABLE_BLOCKS.has(block.name) || block.boundingBox === 'empty'));
}

function isSafeStandingCell(bot, x, y, z) {
    const feet = bot.blockAt(new Vec3(x, y, z));
    const head = bot.blockAt(new Vec3(x, y + 1, z));
    const floor = bot.blockAt(new Vec3(x, y - 1, z));
    return Boolean(
        feet && head && floor &&
        isPassable(feet) && isPassable(head) && !isPassable(floor) &&
        !HAZARD_BLOCKS.has(feet.name) &&
        !HAZARD_BLOCKS.has(head.name) &&
        !HAZARD_BLOCKS.has(floor.name)
    );
}

function sprintSupportY(bot, positionValue) {
    const position = positionOf(positionValue);
    if (!position || typeof bot?.blockAt !== 'function') return undefined;
    const x = Math.floor(position.x);
    const z = Math.floor(position.z);
    const baseY = Math.floor(position.y);
    for (let depth = 0; depth <= MAX_SPRINT_SUPPORT_DEPTH; depth++) {
        const standingY = baseY - depth;
        if (isSafeStandingCell(bot, x, standingY, z)) return standingY;
    }
    return null;
}

function findStandingCandidate(bot, candidate, minimumY) {
    for (const offset of [0, 1, -1, 2]) {
        const y = Math.floor(candidate.y + offset);
        if (y < minimumY) continue;
        if (isSafeStandingCell(bot, candidate.x, y, candidate.z)) return {...candidate, y};
    }
    return null;
}

export function sprintEscapeCorridorIsViable(bot, originValue, candidateValue, minimumY = -Infinity) {
    const origin = positionOf(originValue);
    const candidate = positionOf(candidateValue);
    if (!origin || !candidate || typeof bot?.blockAt !== 'function') return false;
    const distance = horizontalDistance(origin, candidate);
    const steps = Math.ceil(distance);
    if (!Number.isFinite(distance) || steps < 1 || steps > MAX_SPRINT_CORRIDOR_SAMPLES) return false;

    let standingY = Math.floor(origin.y);
    let previousCell = null;
    for (let step = 1; step <= steps; step++) {
        const ratio = step / steps;
        const x = Math.floor(origin.x + (candidate.x - origin.x) * ratio);
        const z = Math.floor(origin.z + (candidate.z - origin.z) * ratio);
        const cell = `${x},${z}`;
        if (cell === previousCell) continue;
        previousCell = cell;
        const nextY = [standingY, standingY + 1, standingY - 1]
            .find((y) => y >= minimumY && isSafeStandingCell(bot, x, y, z));
        if (nextY === undefined) return false;
        standingY = nextY;
    }
    return standingY === Math.floor(candidate.y);
}

function lineHasCover(bot, candidate, hostileValue) {
    const hostile = positionOf(hostileValue);
    if (!hostile) return 0;
    const dx = hostile.x - candidate.x;
    const dy = hostile.y - candidate.y;
    const dz = hostile.z - candidate.z;
    const steps = Math.max(2, Math.min(12, Math.ceil(Math.hypot(dx, dy, dz))));
    for (let step = 1; step < steps; step++) {
        const ratio = step / steps;
        const block = bot.blockAt(new Vec3(
            Math.floor(candidate.x + dx * ratio),
            Math.floor(candidate.y + 1 + dy * ratio),
            Math.floor(candidate.z + dz * ratio),
        ));
        if (block && !isPassable(block)) return 1;
    }
    return 0;
}

export function escapeCoverScore(bot, candidate, hostiles) {
    return (hostiles || []).reduce(
        (score, hostile) => score + lineHasCover(bot, candidate, hostile),
        0,
    );
}

function configureEscapeMovements(bot, minimumY) {
    const movements = new pf.Movements(bot);
    const belowFloor = (block) => block.position.y < minimumY ? 100 : 0;
    movements.canDig = false;
    movements.allow1by1towers = false;
    movements.allowParkour = false;
    movements.maxDropDown = 1;
    movements.exclusionAreasStep.push(belowFloor);
    movements.exclusionAreasBreak.push(belowFloor);
    movements.exclusionAreasPlace.push(belowFloor);
    for (const name of HAZARD_BLOCKS) {
        const id = bot.registry?.blocksByName?.[name]?.id;
        if (Number.isInteger(id)) movements.blocksToAvoid.add(id);
    }
    return movements;
}

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function boundedInterruptOptOutMs(value) {
    const milliseconds = Number(value);
    return Number.isFinite(milliseconds)
        ? Math.min(MAX_INTERRUPT_OPTOUT_MS, Math.max(0, milliseconds))
        : 0;
}

function escapeAbortReason(bot, interruptOptOutUntil = 0) {
    if (!bot?.entity?.position) return 'disconnected';
    if (Number.isFinite(Number(bot.health)) && Number(bot.health) <= 0) return 'bot_dead';
    if (bot.interrupt_code && Date.now() >= interruptOptOutUntil) return 'interrupted';
    return null;
}

function requestedAbortReason(provider) {
    if (typeof provider !== 'function') return null;
    const reason = provider();
    return typeof reason === 'string' && reason ? reason : null;
}

export async function sprintEscapeBurst(bot, candidate, {
    durationMs = 2000,
    threatProvider = null,
    safeDistance = DEFAULT_SAFE_DISTANCE,
    interruptOptOutMs = 0,
    onAbort = null,
    abortReasonProvider = null,
} = {}) {
    if (
        !bot?.entity?.position ||
        !candidate ||
        typeof bot?.setControlState !== 'function'
    ) return false;
    const startedAt = Date.now();
    const interruptOptOutUntil = startedAt + boundedInterruptOptOutMs(interruptOptOutMs);
    const initialAbortReason = escapeAbortReason(bot, interruptOptOutUntil) ||
        requestedAbortReason(abortReasonProvider);
    if (initialAbortReason) {
        onAbort?.(initialAbortReason);
        return false;
    }
    const initialPosition = positionOf(bot.entity.position);
    let lastSafePositionY = initialPosition.y;
    let lastSafeSupportY = sprintSupportY(bot, initialPosition);
    const supportGuardEnabled = lastSafeSupportY !== undefined;
    if (supportGuardEnabled && lastSafeSupportY === null) {
        onAbort?.(FALL_RISK_VERIFICATION);
        return false;
    }
    const destination = {
        x: Number(candidate.x) + 0.5,
        y: Number(candidate.y),
        z: Number(candidate.z) + 0.5,
    };
    bot.pathfinder?.stop?.();
    bot.pathfinder?.setGoal?.(null);
    bot.clearControlStates?.();
    let burstAbortReason = null;
    let controlsReleased = false;
    let wakeGuard = null;
    const guardWake = new Promise((resolve) => { wakeGuard = resolve; });
    const releaseControls = () => {
        if (controlsReleased) return;
        controlsReleased = true;
        bot.setControlState('forward', false);
        bot.setControlState('sprint', false);
        bot.setControlState('jump', false);
    };
    const interruptBurst = (reason) => {
        if (burstAbortReason) return;
        burstAbortReason = reason;
        releaseControls();
        onAbort?.(reason);
        wakeGuard();
    };
    const guardSprint = () => {
        const position = positionOf(bot?.entity?.position);
        if (!position) return;
        const velocityY = Number(bot.entity?.velocity?.y);
        const supportY = sprintSupportY(bot, position);
        const supportDroppedTooFar = (
            supportY !== undefined && supportY !== null &&
            lastSafeSupportY !== null && supportY < lastSafeSupportY - 1
        );
        const dangerousVelocity = (
            Number.isFinite(velocityY) && velocityY <= DANGEROUS_DOWNWARD_VELOCITY &&
            (supportY === null || supportY === undefined || supportDroppedTooFar)
        );
        if (
            dangerousVelocity ||
            position.y < lastSafePositionY - MAX_SAFE_SPRINT_DROP ||
            (supportGuardEnabled && supportY === null) ||
            supportDroppedTooFar
        ) {
            interruptBurst(FALL_RISK_VERIFICATION);
            return;
        }
        if (
            supportY !== undefined && supportY !== null &&
            position.y - supportY <= 0.1
        ) {
            lastSafePositionY = position.y;
            lastSafeSupportY = supportY;
        }
    };
    bot.on?.('physicsTick', guardSprint);
    try {
        await bot.lookAt?.(
            new Vec3(destination.x, bot.entity.position.y + 1.5, destination.z),
            true,
        );
        guardSprint();
        if (burstAbortReason) return false;
        controlsReleased = false;
        bot.setControlState('forward', true);
        bot.setControlState('sprint', true);
        bot.setControlState('jump', true);
        const deadline = startedAt + Math.max(0, Number(durationMs) || 0);
        while (Date.now() < deadline) {
            const requestedReason = escapeAbortReason(bot, interruptOptOutUntil) ||
                requestedAbortReason(abortReasonProvider);
            if (requestedReason) {
                interruptBurst(requestedReason);
                return false;
            }
            guardSprint();
            if (burstAbortReason) return false;
            if (horizontalDistance(bot.entity.position, destination) <= 1) break;
            await Promise.race([
                delay(Math.min(100, deadline - Date.now())),
                guardWake,
            ]);
            if (burstAbortReason) return false;
            const abortReason = escapeAbortReason(bot, interruptOptOutUntil) ||
                requestedAbortReason(abortReasonProvider);
            if (abortReason) interruptBurst(abortReason);
            if (abortReason && abortReason !== 'interrupted') return false;
            if (Date.now() >= deadline) break;
            if (abortReason) return false;
            if (horizontalDistance(bot.entity.position, destination) <= 1) break;
            const threats = threatProvider?.() || [];
            if (!threats.some((entry) => Number(entry?.distance ?? Infinity) <= safeDistance)) break;
        }
        const finalPosition = positionOf(bot.entity?.position);
        const initialRemaining = horizontalDistance(initialPosition, destination);
        const finalRemaining = finalPosition
            ? horizontalDistance(finalPosition, destination)
            : Number.POSITIVE_INFINITY;
        const progressed = finalRemaining <= initialRemaining - 0.5;
        const reached = finalRemaining <= 1;
        const threatStillClose = !reached && (threatProvider?.() || [])
            .some((entry) => Number(entry?.distance ?? Infinity) <= safeDistance);
        if (
            typeof abortReasonProvider === 'function' &&
            !reached && !progressed && threatStillClose
        ) {
            interruptBurst(INCOMPLETE_BURST_VERIFICATION);
            return false;
        }
        return true;
    } finally {
        if (typeof bot.off === 'function') bot.off('physicsTick', guardSprint);
        else bot.removeListener?.('physicsTick', guardSprint);
        releaseControls();
    }
}

export async function escapeFromHostiles(bot, {
    failureCount = 0,
    safeDistance = DEFAULT_SAFE_DISTANCE,
    range = DEFAULT_RANGE,
    timeoutMs = 8000,
    burstMs = 2000,
    hostileProvider = null,
    surfaceY = null,
    interruptOptOutMs = 0,
    forceSprint = false,
    stopOnStall = true,
    directionHint = null,
    stableMs = 0,
    abortReasonProvider = null,
} = {}) {
    if (!bot?.entity?.position || !bot?.pathfinder) {
        return {success: false, strategy: 'escape', verification: 'not_connected', bursts: 0};
    }
    const owner = 'evelyn_hostile_escape';
    if (bot.evelynMovementOwner && bot.evelynMovementOwner !== owner) {
        return {success: false, strategy: 'escape', verification: 'movement_busy', bursts: 0};
    }

    const startedAt = Date.now();
    const initialPosition = positionOf(bot.entity.position);
    const initialThreats = (hostileProvider?.() || liveHostiles(bot, range))
        .filter((entry) => Number(entry?.distance ?? Infinity) <= range);
    const initialHealth = Number(bot.health);
    let minimumHealth = initialHealth;
    let recoveryMode = (
        Number(failureCount || 0) >= 1 ||
        initialThreats.length >= 2 ||
        (Number.isFinite(initialHealth) && initialHealth <= 10)
    );
    let strategy = recoveryMode ? 'break_los_surface' : 'multi_hostile_escape';
    const interruptOptOutUntil = startedAt + boundedInterruptOptOutMs(interruptOptOutMs);
    const initialScore = escapeSafetyScore(initialPosition, initialThreats);
    let previousScore = initialScore;
    let stagnantBursts = 0;
    let bursts = 0;
    let lastCandidate = null;
    let pathfinderFallbackVerification = null;
    let safeSince = null;
    const stableWindowMs = Number.isFinite(Number(stableMs))
        ? Math.max(0, Number(stableMs))
        : 0;
    let preferredDirection = horizontalDirection({x: 0, z: 0}, directionHint);
    const recentDirection = RECENT_ESCAPE_DIRECTIONS.get(bot);
    if (!preferredDirection && forceSprint && recoveryMode && recentDirection?.expiresAt > startedAt) {
        preferredDirection = recentDirection;
    } else if (recentDirection) {
        RECENT_ESCAPE_DIRECTIONS.delete(bot);
    }

    bot.evelynMovementOwner = owner;
    try {
        while (Date.now() - startedAt < timeoutMs) {
            const abortReason = escapeAbortReason(bot, interruptOptOutUntil) ||
                requestedAbortReason(abortReasonProvider);
            if (abortReason) {
                return {
                    success: false,
                    strategy,
                    verification: abortReason,
                    bursts,
                    initialScore,
                    finalScore: previousScore,
                };
            }
            const threats = (hostileProvider?.() || liveHostiles(bot, range))
                .filter((entry) => Number(entry?.distance ?? Infinity) <= range);
            const escapeDistance = stableWindowMs > 0 && safeSince === null
                ? range
                : safeDistance;
            const immediate = threats.filter((entry) => Number(entry?.distance ?? Infinity) <= escapeDistance);
            if (!immediate.length) {
                if (stableWindowMs === 0) {
                    RECENT_ESCAPE_DIRECTIONS.delete(bot);
                    return {
                        success: true,
                        strategy,
                        verification: 'safe_radius',
                        bursts,
                        initialScore,
                        finalScore: escapeSafetyScore(bot.entity.position, threats),
                    };
                }
                if (safeSince === null) safeSince = Date.now();
                const stableForMs = Date.now() - safeSince;
                if (stableForMs >= stableWindowMs) {
                    RECENT_ESCAPE_DIRECTIONS.delete(bot);
                    return {
                        success: true,
                        strategy,
                        verification: 'stable_safe_radius',
                        bursts,
                        initialScore,
                        finalScore: escapeSafetyScore(bot.entity.position, threats),
                    };
                }
                const remainingTimeoutMs = startedAt + timeoutMs - Date.now();
                await delay(Math.min(100, stableWindowMs - stableForMs, remainingTimeoutMs));
                continue;
            }
            safeSince = null;

            const origin = positionOf(bot.entity.position);
            const minimumY = Math.floor(origin.y) - 1;
            const rawCandidates = buildEscapeCandidates(origin, immediate, {
                recoveryMode,
                radii: recoveryMode ? [4, 7, 10, 14] : [5, 9],
            });
            const urgentDirectSprint = (
                Number(immediate[0]?.distance ?? Infinity) <= 8 ||
                Number(bot.health || 20) <= 10
            );
            const directSprintRequested = (
                urgentDirectSprint ||
                (!pathfinderFallbackVerification && forceSprint)
            );
            const standingCandidates = rawCandidates
                .map((candidate) => findStandingCandidate(bot, candidate, minimumY))
                .filter(Boolean);
            const sprintCandidates = directSprintRequested
                ? standingCandidates.filter((candidate) => (
                    sprintEscapeCorridorIsViable(bot, origin, candidate, minimumY)
                ))
                : standingCandidates;
            const directSprint = directSprintRequested && sprintCandidates.length > 0;
            const candidate = chooseEscapeCandidate(
                directSprint ? sprintCandidates : standingCandidates,
                immediate,
                {
                    origin,
                    recoveryMode,
                    surfaceY,
                    coverScore: (value) => escapeCoverScore(bot, value, immediate),
                    preferredDirection,
                    directionWeight: directionHint
                        ? ESCAPE_HINT_WEIGHT
                        : (forceSprint && recoveryMode ? ESCAPE_DIRECTION_WEIGHT : 0),
                },
            );
            if (!candidate) {
                return {
                    success: false,
                    strategy,
                    verification: pathfinderFallbackVerification || 'no_viable_waypoint',
                    bursts,
                    initialScore,
                    finalScore: previousScore,
                };
            }

            lastCandidate = candidate;
            if (forceSprint && recoveryMode) {
                preferredDirection = horizontalDirection(origin, candidate);
                if (preferredDirection) {
                    RECENT_ESCAPE_DIRECTIONS.set(bot, {
                        ...preferredDirection,
                        expiresAt: Date.now() + ESCAPE_DIRECTION_TTL_MS,
                    });
                }
            }
            bursts += 1;
            if (directSprint) {
                let burstAbortReason = null;
                const completed = await sprintEscapeBurst(bot, candidate, {
                    durationMs: Math.min(burstMs, startedAt + timeoutMs - Date.now()),
                    threatProvider: () => hostileProvider?.() || liveHostiles(bot, range),
                    safeDistance: escapeDistance,
                    interruptOptOutMs: Math.max(0, interruptOptOutUntil - Date.now()),
                    onAbort: (reason) => { burstAbortReason = reason; },
                    abortReasonProvider,
                });
                if (!completed) {
                    if (
                        burstAbortReason === INCOMPLETE_BURST_VERIFICATION &&
                        interruptOptOutUntil > Date.now()
                    ) continue;
                    const retryableBurstFailure = (
                        !burstAbortReason ||
                        burstAbortReason === FALL_RISK_VERIFICATION ||
                        burstAbortReason === INCOMPLETE_BURST_VERIFICATION
                    );
                    const explicitAbortReason = retryableBurstFailure
                        ? (
                            escapeAbortReason(bot, interruptOptOutUntil) ||
                            requestedAbortReason(abortReasonProvider)
                        )
                        : burstAbortReason;
                    const threatStillClose = (hostileProvider?.() || liveHostiles(bot, range))
                        .some((entry) => Number(entry?.distance ?? Infinity) <= escapeDistance);
                    if (
                        !explicitAbortReason &&
                        threatStillClose &&
                        Date.now() - startedAt < timeoutMs
                    ) {
                        pathfinderFallbackVerification = burstAbortReason || 'escape_burst_aborted';
                        continue;
                    }
                    return {
                        success: false,
                        strategy,
                        verification: (
                            explicitAbortReason ||
                            burstAbortReason ||
                            'escape_burst_aborted'
                        ),
                        bursts,
                        initialScore,
                        finalScore: previousScore,
                        lastCandidate,
                    };
                }
            } else {
                bot.pathfinder.setMovements(configureEscapeMovements(bot, minimumY));
                bot.pathfinder.setGoal(new pf.goals.GoalNear(candidate.x, candidate.y, candidate.z, 1), true);
                const burstDeadline = Math.min(Date.now() + burstMs, startedAt + timeoutMs);
                while (Date.now() < burstDeadline) {
                    await delay(Math.min(100, burstDeadline - Date.now()));
                    const abortReason = escapeAbortReason(bot, interruptOptOutUntil) ||
                        requestedAbortReason(abortReasonProvider);
                    if (abortReason) {
                        return {
                            success: false,
                            strategy,
                            verification: abortReason,
                            bursts,
                            initialScore,
                            finalScore: previousScore,
                            lastCandidate,
                        };
                    }
                    const currentThreats = (hostileProvider?.() || liveHostiles(bot, range));
                    if (!currentThreats.some((entry) => Number(entry?.distance ?? Infinity) <= escapeDistance)) break;
                }
                bot.pathfinder.stop();
            }

            const afterThreats = (hostileProvider?.() || liveHostiles(bot, range));
            const score = escapeSafetyScore(bot.entity.position, afterThreats);
            const currentHealth = Number(bot.health);
            if (Number.isFinite(currentHealth)) minimumHealth = Math.min(minimumHealth, currentHealth);
            if (score <= previousScore + 1.5) stagnantBursts += 1;
            else stagnantBursts = 0;
            previousScore = score;
            if (!recoveryMode && (
                (Number.isFinite(initialHealth) && initialHealth - minimumHealth >= 4) ||
                stagnantBursts >= 2
            )) {
                recoveryMode = true;
                strategy = 'break_los_surface';
                stagnantBursts = 0;
            } else if (recoveryMode && stagnantBursts >= 2 && stopOnStall) {
                return {
                    success: false,
                    strategy,
                    verification: 'escape_stalled',
                    bursts,
                    initialScore,
                    finalScore: previousScore,
                    lastCandidate,
                };
            } else if (recoveryMode && stagnantBursts >= 2) {
                stagnantBursts = 0;
            }
        }
        return {
            success: false,
            strategy,
            verification: 'partial_escape',
            bursts,
            initialScore,
            finalScore: previousScore,
            lastCandidate,
        };
    } finally {
        try {
            bot.pathfinder?.stop?.();
            bot.pathfinder?.setGoal?.(null);
        } finally {
            if (bot.evelynMovementOwner === owner) bot.evelynMovementOwner = null;
        }
    }
}
