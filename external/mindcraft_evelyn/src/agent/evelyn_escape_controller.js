import pf from 'mineflayer-pathfinder';
import Vec3 from 'vec3';

const HAZARD_BLOCKS = new Set([
    'bubble_column', 'cactus', 'campfire', 'fire', 'lava', 'magma_block',
    'powder_snow', 'soul_campfire', 'sweet_berry_bush', 'water', 'withering_rose',
]);
const PASSABLE_BLOCKS = new Set(['air', 'cave_air', 'void_air']);
const DEFAULT_SAFE_DISTANCE = 18;
const DEFAULT_RANGE = 24;

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
} = {}) {
    const start = positionOf(origin);
    return (candidates || [])
        .filter((candidate) => isViable(candidate))
        .map((candidate) => {
            const safety = escapeSafetyScore(candidate, hostiles);
            const cover = Number(coverScore(candidate) || 0);
            const upward = (
                recoveryMode &&
                Number.isFinite(Number(surfaceY)) &&
                start
            )
                ? Math.max(0, Math.min(Number(surfaceY), candidate.y) - start.y)
                : 0;
            return {
                candidate,
                score: safety + cover * (recoveryMode ? 12 : 4) + upward * 3,
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

function findStandingCandidate(bot, candidate, minimumY) {
    for (const offset of [0, 1, -1, 2]) {
        const y = Math.floor(candidate.y + offset);
        if (y < minimumY) continue;
        const feet = bot.blockAt(new Vec3(candidate.x, y, candidate.z));
        const head = bot.blockAt(new Vec3(candidate.x, y + 1, candidate.z));
        const floor = bot.blockAt(new Vec3(candidate.x, y - 1, candidate.z));
        if (!feet || !head || !floor) continue;
        if (!isPassable(feet) || !isPassable(head) || isPassable(floor)) continue;
        if (HAZARD_BLOCKS.has(feet.name) || HAZARD_BLOCKS.has(floor.name)) continue;
        return {...candidate, y};
    }
    return null;
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

export async function sprintEscapeBurst(bot, candidate, {
    durationMs = 2000,
    threatProvider = null,
    safeDistance = DEFAULT_SAFE_DISTANCE,
} = {}) {
    if (
        !bot?.entity?.position ||
        !candidate ||
        typeof bot?.setControlState !== 'function'
    ) return false;
    bot.pathfinder?.stop?.();
    bot.clearControlStates?.();
    try {
        await bot.lookAt?.(
            new Vec3(candidate.x + 0.5, bot.entity.position.y + 1.5, candidate.z + 0.5),
            true,
        );
        bot.setControlState('forward', true);
        bot.setControlState('sprint', true);
        bot.setControlState('jump', true);
        const deadline = Date.now() + durationMs;
        while (Date.now() < deadline) {
            await delay(Math.min(100, deadline - Date.now()));
            const threats = threatProvider?.() || [];
            if (
                threats.length &&
                !threats.some((entry) => Number(entry?.distance ?? Infinity) <= safeDistance)
            ) break;
        }
        return true;
    } finally {
        bot.setControlState('forward', false);
        bot.setControlState('sprint', false);
        bot.setControlState('jump', false);
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
} = {}) {
    if (!bot?.entity?.position || !bot?.pathfinder) {
        return {success: false, strategy: 'escape', verification: 'not_connected', bursts: 0};
    }
    const owner = 'evelyn_hostile_escape';
    if (bot.evelynMovementOwner && bot.evelynMovementOwner !== owner) {
        return {success: false, strategy: 'escape', verification: 'movement_busy', bursts: 0};
    }

    const recoveryMode = Number(failureCount || 0) >= 2;
    const strategy = recoveryMode ? 'break_los_surface' : 'multi_hostile_escape';
    const startedAt = Date.now();
    const initialPosition = positionOf(bot.entity.position);
    const initialThreats = (hostileProvider?.() || liveHostiles(bot, range));
    const initialScore = escapeSafetyScore(initialPosition, initialThreats);
    let previousScore = initialScore;
    let stagnantBursts = 0;
    let bursts = 0;
    let lastCandidate = null;

    bot.evelynMovementOwner = owner;
    try {
        while (Date.now() - startedAt < timeoutMs) {
            const threats = (hostileProvider?.() || liveHostiles(bot, range))
                .filter((entry) => Number(entry?.distance ?? Infinity) <= range);
            const immediate = threats.filter((entry) => Number(entry?.distance ?? Infinity) <= safeDistance);
            if (!immediate.length) {
                return {
                    success: true,
                    strategy,
                    verification: 'safe_radius',
                    bursts,
                    initialScore,
                    finalScore: escapeSafetyScore(bot.entity.position, threats),
                };
            }

            const origin = positionOf(bot.entity.position);
            const minimumY = Math.floor(origin.y) - 1;
            const rawCandidates = buildEscapeCandidates(origin, immediate, {
                recoveryMode,
                radii: recoveryMode ? [4, 7, 10] : [5, 9],
            });
            const standingCandidates = rawCandidates
                .map((candidate) => findStandingCandidate(bot, candidate, minimumY))
                .filter(Boolean);
            const nearestThreat = immediate[0];
            const candidate = chooseEscapeCandidate(standingCandidates, immediate, {
                origin,
                recoveryMode,
                surfaceY,
                coverScore: (value) => lineHasCover(bot, value, nearestThreat),
            });
            if (!candidate) {
                return {
                    success: false,
                    strategy,
                    verification: 'no_viable_waypoint',
                    bursts,
                    initialScore,
                    finalScore: previousScore,
                };
            }

            lastCandidate = candidate;
            bursts += 1;
            const closestDistance = Number(immediate[0]?.distance ?? Infinity);
            if (closestDistance <= 8 || Number(bot.health || 20) <= 10) {
                await sprintEscapeBurst(bot, candidate, {
                    durationMs: Math.min(burstMs, startedAt + timeoutMs - Date.now()),
                    threatProvider: () => hostileProvider?.() || liveHostiles(bot, range),
                    safeDistance,
                });
            } else {
                bot.pathfinder.setMovements(configureEscapeMovements(bot, minimumY));
                bot.pathfinder.setGoal(new pf.goals.GoalNear(candidate.x, candidate.y, candidate.z, 1), true);
                const burstDeadline = Math.min(Date.now() + burstMs, startedAt + timeoutMs);
                while (Date.now() < burstDeadline) {
                    await delay(Math.min(250, burstDeadline - Date.now()));
                    const currentThreats = (hostileProvider?.() || liveHostiles(bot, range));
                    if (!currentThreats.some((entry) => Number(entry?.distance ?? Infinity) <= safeDistance)) break;
                }
                bot.pathfinder.stop();
            }

            const afterThreats = (hostileProvider?.() || liveHostiles(bot, range));
            const score = escapeSafetyScore(bot.entity.position, afterThreats);
            if (score <= previousScore + 1.5) stagnantBursts += 1;
            else stagnantBursts = 0;
            previousScore = score;
            if (stagnantBursts >= 2) stagnantBursts = 0;
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
        bot.pathfinder.stop();
        if (bot.evelynMovementOwner === owner) bot.evelynMovementOwner = null;
    }
}
