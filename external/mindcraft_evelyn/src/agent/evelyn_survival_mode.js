import pf from 'mineflayer-pathfinder';
import Vec3 from 'vec3';
import * as skills from './library/skills.js';
import * as world from './library/world.js';
import * as mc from '../utils/mcdata.js';
import {
    armorPointsFromNames,
    assessCombat,
    fightWithCustomPvp,
    stopCombatControllers,
    weaponPowerFromInventory,
} from './evelyn_combat.js';
import { escapeFromHostiles } from './evelyn_escape_controller.js';

const HOSTILE_AVOID_DISTANCE = 18;
const HOSTILE_FIGHT_DISTANCE = 8;
const HOSTILE_STABLE_MS = 2000;
const HOSTILE_ACTION_TIMEOUT_MS = 25000;
const HOSTILE_ESCAPE_TIMEOUT_MS = 20000;
const CRITICAL_HUNGER = 6;
const FOOD_ACQUIRE_HUNGER = 14;
const CRITICAL_HEALTH = 10;
const SAFE_BOOTSTRAP_HUNGER = 8;
const SAFE_BOOTSTRAP_HEALTH = 12;
const CHECK_INTERVAL_MS = 1500;
const ACTION_TIMEOUT_MS = 30000;
const ESCAPE_FAIL_HANDOFF_VERIFICATIONS = new Set([
    'staircase_blocked',
    'water_exit_blocked',
    'surface_unknown',
]);
const ESCAPE_FAIL_HANDOFF_LIMIT = 3;
const ESCAPE_FAIL_HANDOFF_DELAY_MS = 25000;
const SURFACE_OFFSETS = [
    [0, 0], [4, 0], [-4, 0], [0, 4], [0, -4],
    [8, 0], [-8, 0], [0, 8], [0, -8],
];
const STAIRCASE_DIRECTIONS = [
    [1, 0], [0, 1], [-1, 0], [0, -1],
];
const NON_SOLID_BLOCKS = new Set([
    'air', 'cave_air', 'void_air', 'water', 'lava',
]);
const UNSAFE_STAIR_BLOCKS = new Set([
    'bedrock', 'barrier', 'end_portal', 'end_portal_frame',
    'lava', 'water', 'sand', 'gravel', 'suspicious_sand', 'suspicious_gravel',
]);
const FALLBACK_FOODS = new Set([
    'apple', 'baked_potato', 'beef', 'beetroot', 'beetroot_soup', 'bread',
    'carrot', 'cooked_beef', 'cooked_chicken', 'cooked_cod', 'cooked_mutton',
    'cooked_porkchop', 'cooked_rabbit', 'cooked_salmon', 'cookie', 'dried_kelp',
    'glow_berries', 'golden_apple', 'golden_carrot', 'melon_slice', 'mushroom_stew',
    'mutton', 'porkchop', 'potato', 'pumpkin_pie', 'rabbit', 'rabbit_stew',
    'salmon', 'sweet_berries', 'tropical_fish',
]);
const UNSAFE_FOODS = new Set([
    'chicken', 'poisonous_potato', 'pufferfish', 'rotten_flesh', 'spider_eye',
    'suspicious_stew',
]);
const CROP_NAMES = new Set([
    'beetroots', 'carrots', 'melon', 'potatoes', 'sweet_berry_bush', 'wheat',
]);
const MELEE_WEAPON_NAMES = new Set(['mace', 'trident']);
const surfaceCache = new Map();

export function filterMovesAtOrAbove(moves, minimumY) {
    return moves.filter((move) => move.y >= minimumY);
}

function nowSeconds() {
    return Date.now() / 1000;
}

function inventoryCounts(bot) {
    return world.getInventoryCounts(bot);
}

function foodValue(bot, item) {
    const food = bot.registry?.foodsByName?.[item.name];
    return Number(food?.foodPoints || food?.food || 0);
}

export function selectBestFood(bot) {
    const foods = bot.inventory.items().filter((item) => {
        if (UNSAFE_FOODS.has(item.name)) return false;
        return Boolean(bot.registry?.foodsByName?.[item.name]) || FALLBACK_FOODS.has(item.name);
    });
    foods.sort((left, right) => foodValue(bot, right) - foodValue(bot, left));
    return foods[0] || null;
}

function nearestHostile(bot, range = 24) {
    return nearbyHostiles(bot, range)[0]?.entity || null;
}

function surfaceYAt(bot, x, z) {
    const blockX = Math.floor(x);
    const blockZ = Math.floor(z);
    const cacheKey = `${blockX},${blockZ}`;
    const cached = surfaceCache.get(cacheKey);
    if (cached && cached.expiresAt > Date.now()) return cached.value;
    for (let y = 319; y >= -64; y--) {
        const block = bot.blockAt(new Vec3(blockX, y, blockZ));
        if (!block) continue;
        if (block.name !== 'air' && block.name !== 'cave_air' && block.name !== 'void_air') {
            const value = y + 1;
            surfaceCache.set(cacheKey, { value, expiresAt: Date.now() + 5000 });
            return value;
        }
    }
    surfaceCache.set(cacheKey, { value: null, expiresAt: Date.now() + 2000 });
    return null;
}

function currentSurfaceY(bot) {
    const position = bot.entity?.position;
    if (!position) return null;
    return surfaceYAt(bot, position.x, position.z);
}

function hasPickaxe(inventory) {
    return Object.keys(inventory).some((name) => name.endsWith('_pickaxe') && inventory[name] > 0);
}

function hasMeleeWeapon(inventory) {
    return Object.keys(inventory).some((name) => (
        (name.endsWith('_sword') || name.endsWith('_axe') || MELEE_WEAPON_NAMES.has(name)) &&
        Number(inventory[name] || 0) > 0
    ));
}

export function hostileIsActionable(origin, entityPosition, distance) {
    if (!origin || !entityPosition || !Number.isFinite(distance)) return false;
    const verticalDistance = Math.abs(Number(entityPosition.y) - Number(origin.y));
    return distance <= 8 || verticalDistance <= 5;
}

export function hostileHasClearLine(bot, entity) {
    const origin = bot?.entity?.position;
    const target = entity?.position;
    if (!origin || !target) return false;
    if (typeof bot.blockAt !== 'function') return true;
    const start = {
        x: Number(origin.x),
        y: Number(origin.y) + 1.55,
        z: Number(origin.z),
    };
    const end = {
        x: Number(target.x),
        y: Number(target.y) + Math.min(1.2, Number(entity.height || 1.8) * 0.6),
        z: Number(target.z),
    };
    const distance = Math.hypot(end.x - start.x, end.y - start.y, end.z - start.z);
    const steps = Math.max(2, Math.ceil(distance * 2));
    for (let index = 1; index < steps - 1; index++) {
        const ratio = index / steps;
        const block = bot.blockAt(new Vec3(
            Math.floor(start.x + ((end.x - start.x) * ratio)),
            Math.floor(start.y + ((end.y - start.y) * ratio)),
            Math.floor(start.z + ((end.z - start.z) * ratio)),
        ));
        if (block?.boundingBox === 'block') return false;
    }
    return true;
}

function nearbyHostiles(bot, range = 24) {
    const position = bot.entity?.position;
    if (!position) return [];
    return Object.values(bot.entities || {})
        .filter((entity) => entity?.position && entity !== bot.entity && mc.isHostile(entity))
        .map((entity) => ({ entity, distance: position.distanceTo(entity.position) }))
        .filter(({ entity, distance }) => (
            Number.isFinite(distance) &&
            distance <= range &&
            hostileIsActionable(position, entity.position, distance) &&
            (distance <= 4 || hostileHasClearLine(bot, entity))
        ))
        .sort((left, right) => left.distance - right.distance);
}

export function selectHostileTactic(snapshot) {
    const assessment = assessCombat(snapshot);
    return assessment.tactic === 'none' ? null : assessment.tactic;
}

export function verifyHostileOutcome(tactic, before, after, actionSucceeded = true) {
    if (!actionSucceeded || !after?.connected || after.health <= 0) return false;
    const safeFromImmediateThreat = (
        after.hostileDistance === null || after.hostileDistance > HOSTILE_AVOID_DISTANCE
    );
    if (tactic === 'flee') return safeFromImmediateThreat;
    if (tactic === 'fight') {
        const targetGone = before?.hostileId === null || before?.hostileId === undefined ||
            before.hostileId !== after.hostileId;
        return safeFromImmediateThreat && targetGone;
    }
    return false;
}

export function verifySurfaceEscape({ reached, startPosition, finalPosition, surfaceY, finalInWater = null, requireOutOfWater = false }) {
    if (!reached || !startPosition || !finalPosition || !Number.isFinite(surfaceY)) return false;
    const dx = Number(finalPosition.x) - Number(startPosition.x);
    const dy = Number(finalPosition.y) - Number(startPosition.y);
    const dz = Number(finalPosition.z) - Number(startPosition.z);
    if (![dx, dy, dz].every(Number.isFinite)) return false;
    if (requireOutOfWater && finalInWater !== false) return false;
    const moved = Math.hypot(dx, dy, dz) >= 1;
    const outsideUndergroundBand = Number(finalPosition.y) >= Number(surfaceY) - 3;
    return moved && outsideUndergroundBand;
}

export function staircaseTargets(origin, directionIndex = 0) {
    if (!origin) return [];
    return STAIRCASE_DIRECTIONS.map((_, offset) => {
        const index = (Number(directionIndex || 0) + offset) % STAIRCASE_DIRECTIONS.length;
        const [dx, dz] = STAIRCASE_DIRECTIONS[index];
        return {
            index,
            x: Math.floor(Number(origin.x)) + dx,
            y: Math.floor(Number(origin.y)) + 1,
            z: Math.floor(Number(origin.z)) + dz,
        };
    });
}

export function staircaseBaseTargets(origin, directionIndex = 0) {
    if (!origin) return [];
    return STAIRCASE_DIRECTIONS.map((_, offset) => {
        const index = (Number(directionIndex || 0) + offset) % STAIRCASE_DIRECTIONS.length;
        const [dx, dz] = STAIRCASE_DIRECTIONS[index];
        return {
            index,
            x: Math.floor(Number(origin.x)) + dx,
            y: Math.floor(Number(origin.y)),
            z: Math.floor(Number(origin.z)) + dz,
        };
    });
}

export function listSurvivalDecisions(snapshot, { enableToolBootstrap = false } = {}) {
    if (!snapshot?.connected) return [];
    const decisions = [];
    if (snapshot.hostileDistance !== null && snapshot.hostileDistance <= HOSTILE_AVOID_DISTANCE) {
        decisions.push('handle_hostile');
    }
    if (snapshot.foodName && (snapshot.hunger <= 14 || snapshot.health <= 12)) {
        decisions.push('eat_inventory_food');
    }
    if (
        snapshot.underground &&
        (
            snapshot.inWater ||
            snapshot.hunger <= CRITICAL_HUNGER ||
            snapshot.health <= CRITICAL_HEALTH
        )
    ) {
        decisions.push('escape_to_surface');
    }
    if (
        enableToolBootstrap &&
        !snapshot.hasPickaxe &&
        snapshot.hunger >= SAFE_BOOTSTRAP_HUNGER &&
        snapshot.health >= SAFE_BOOTSTRAP_HEALTH
    ) {
        decisions.push('bootstrap_tools');
    }
    return decisions;
}

export function selectSurvivalDecision(snapshot, options) {
    return listSurvivalDecisions(snapshot, options)[0] || null;
}

export function decisionCanInterrupt(decision) {
    return decision !== 'bootstrap_tools';
}

export function failureCooldownMs(decision, failures) {
    if (decision === 'handle_hostile') {
        return 250;
    }
    return Math.min(60000, 5000 * (2 ** Math.min(Number(failures || 0), 3)));
}

function shouldHandoffEscapeFailure(decision, details, failures) {
    return (
        decision === 'escape_to_surface' &&
        Number(failures || 0) >= ESCAPE_FAIL_HANDOFF_LIMIT &&
        !details?.progress &&
        ESCAPE_FAIL_HANDOFF_VERIFICATIONS.has(String(details?.verification || ''))
    );
}

export function mergeSurvivalState(previous, next) {
    return { ...(previous || {}), ...(next || {}) };
}

export function buildSurvivalSnapshot(bot) {
    const position = bot.entity?.position;
    const surfaceY = currentSurfaceY(bot);
    const hostiles = nearbyHostiles(bot);
    const hostile = hostiles[0] || null;
    const food = selectBestFood(bot);
    const inventory = inventoryCounts(bot);
    const equipmentNames = ['head', 'torso', 'legs', 'feet']
        .map((destination) => {
            const slot = bot.getEquipmentDestSlot?.(destination);
            return Number.isInteger(slot) ? bot.inventory?.slots?.[slot]?.name : null;
        })
        .filter(Boolean);
    const offhandSlot = bot.getEquipmentDestSlot?.('off-hand');
    const offhandName = Number.isInteger(offhandSlot)
        ? bot.inventory?.slots?.[offhandSlot]?.name
        : null;
    const arrowCount = ['arrow', 'spectral_arrow', 'tipped_arrow']
        .reduce((total, name) => total + Number(inventory[name] || 0), 0);
    return {
        connected: Boolean(position),
        position: position ? { x: position.x, y: position.y, z: position.z } : null,
        health: Number.isFinite(bot.health) ? bot.health : 20,
        hunger: Number.isFinite(bot.food) ? bot.food : 20,
        surfaceY,
        underground: Boolean(position && surfaceY !== null && position.y < surfaceY - 3),
        hostileDistance: hostile?.distance ?? null,
        hostileName: hostile?.entity?.name || null,
        hostileId: hostile?.entity?.id ?? null,
        hostileCount: hostiles.filter(({ distance }) => distance <= HOSTILE_AVOID_DISTANCE).length,
        hostiles: hostiles
            .filter(({ distance }) => distance <= HOSTILE_AVOID_DISTANCE)
            .map(({entity, distance}) => ({
                id: entity.id,
                name: entity.name,
                distance,
                health: Number.isFinite(entity.health) ? entity.health : null,
            })),
        foodName: food?.name || null,
        hasPickaxe: hasPickaxe(inventory),
        hasMeleeWeapon: hasMeleeWeapon(inventory),
        weaponPower: weaponPowerFromInventory(inventory),
        armorPoints: armorPointsFromNames(equipmentNames),
        hasShield: offhandName === 'shield' || Number(inventory.shield || 0) > 0,
        hasBow: Number(inventory.bow || 0) > 0 || Number(inventory.crossbow || 0) > 0,
        rangedWeapon: Number(inventory.bow || 0) > 0
            ? 'bow'
            : (Number(inventory.crossbow || 0) > 0 ? 'crossbow' : null),
        arrowCount,
        inWater: Boolean(bot.entity?.isInWater),
        inventory,
    };
}

function configureSafeMovements(bot, minimumY) {
    const movements = new pf.Movements(bot);
    const originalGetNeighbors = movements.getNeighbors.bind(movements);
    movements.getNeighbors = (node) => filterMovesAtOrAbove(originalGetNeighbors(node), minimumY);
    const belowFloor = (block) => block.position.y < minimumY ? 100 : 0;
    movements.canDig = true;
    movements.allow1by1towers = true;
    movements.allowParkour = false;
    movements.maxDropDown = 1;
    movements.digCost = 2;
    movements.placeCost = 1;
    movements.exclusionAreasStep.push(belowFloor);
    movements.exclusionAreasBreak.push(belowFloor);
    movements.exclusionAreasPlace.push(belowFloor);
    for (const name of ['lava', 'fire', 'cactus', 'sweet_berry_bush']) {
        const id = bot.registry?.blocksByName?.[name]?.id;
        if (Number.isInteger(id)) movements.blocksToAvoid.add(id);
    }
    for (const name of [
        'blaze', 'cave_spider', 'creeper', 'drowned', 'enderman', 'evoker', 'ghast',
        'guardian', 'hoglin', 'husk', 'phantom', 'piglin_brute', 'pillager', 'ravager',
        'skeleton', 'spider', 'stray', 'vex', 'vindicator', 'warden', 'witch',
        'wither_skeleton', 'zombie', 'zombie_villager',
    ]) movements.entitiesToAvoid.add(name);
    return movements;
}

async function pathWithTimeout(bot, goal, movements, timeoutMs = ACTION_TIMEOUT_MS) {
    const previousThinkTimeout = bot.pathfinder.thinkTimeout;
    bot.pathfinder.thinkTimeout = Math.max(Number(previousThinkTimeout || 0), 15000);
    bot.pathfinder.setMovements(movements);
    let timeout;
    try {
        await Promise.race([
            bot.pathfinder.goto(goal),
            new Promise((_, reject) => {
                timeout = setTimeout(() => reject(new Error('survival path timeout')), timeoutMs);
            }),
        ]);
        return true;
    } catch (error) {
        bot.pathfinder.stop();
        console.warn('[Evelyn Survival] path failed:', error?.message || error);
        return false;
    } finally {
        clearTimeout(timeout);
        bot.pathfinder.thinkTimeout = previousThinkTimeout;
    }
}

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function runExternalHostileAction(bot, action, timeoutMs = HOSTILE_ACTION_TIMEOUT_MS) {
    let timeout;
    let operation;
    try {
        operation = Promise.resolve().then(action);
        const result = await Promise.race([
            operation.then((value) => ({ timedOut: false, value: Boolean(value) })),
            new Promise((resolve) => {
                timeout = setTimeout(() => resolve({ timedOut: true, value: false }), timeoutMs);
            }),
        ]);
        if (!result.timedOut) return result;

        bot.pathfinder?.stop();
        stopCombatControllers(bot);
        bot.interrupt_code = true;
        await Promise.race([operation.catch(() => false), delay(1000)]);
        return result;
    } finally {
        clearTimeout(timeout);
    }
}

async function remainsSafeFromHostiles(bot, stableMs = HOSTILE_STABLE_MS) {
    const deadline = Date.now() + stableMs;
    while (Date.now() < deadline) {
        const hostile = nearestHostile(bot, HOSTILE_AVOID_DISTANCE);
        if (hostile) return false;
        await delay(250);
    }
    return !nearestHostile(bot, HOSTILE_AVOID_DISTANCE);
}

function blockIsSolidSupport(block) {
    return Boolean(block?.name && !NON_SOLID_BLOCKS.has(block.name));
}

function blockCanBeCleared(bot, block) {
    if (!block?.name || NON_SOLID_BLOCKS.has(block.name)) return true;
    if (UNSAFE_STAIR_BLOCKS.has(block.name) || block.diggable === false) return false;
    return typeof bot.canDigBlock !== 'function' || bot.canDigBlock(block);
}

async function clearStairBlock(bot, block) {
    if (!block?.name || NON_SOLID_BLOCKS.has(block.name)) return true;
    if (!blockCanBeCleared(bot, block)) return false;
    try {
        await bot.dig(block, true);
        return true;
    } catch (error) {
        console.warn('[Evelyn Survival] staircase dig failed:', block.name, error?.message || error);
        return false;
    }
}

function reachedStairTarget(position, target) {
    return Boolean(
        position &&
        Number(position.y) >= Number(target.y) - 0.05 &&
        Math.hypot(
            Number(position.x) - (Number(target.x) + 0.5),
            Number(position.z) - (Number(target.z) + 0.5),
        ) <= 1.1
    );
}

export async function walkStaircaseStep(
    bot,
    target,
    {timeoutMs = 4000, pollMs = 50} = {},
) {
    const start = bot?.entity?.position;
    if (!start || !target || typeof bot?.setControlState !== 'function') return false;
    const startY = Number(start.y);
    bot.pathfinder?.stop?.();
    bot.clearControlStates?.();
    try {
        await bot.lookAt?.(
            new Vec3(Number(target.x) + 0.5, Number(target.y) + 0.5, Number(target.z) + 0.5),
            true,
        );
        bot.setControlState('forward', true);
        bot.setControlState('jump', true);
        const deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
            const position = bot.entity?.position;
            if (reachedStairTarget(position, target)) return true;
            if (position && Number(position.y) < startY - 0.5) return false;
            await delay(pollMs);
        }
        return reachedStairTarget(bot.entity?.position, target);
    } finally {
        bot.setControlState('forward', false);
        bot.setControlState('jump', false);
    }
}

export function nearbyAirExitTargets(bot, radius = 4) {
    const origin = bot?.entity?.position;
    if (!origin) return [];
    const baseX = Math.floor(Number(origin.x));
    const baseY = Math.floor(Number(origin.y));
    const baseZ = Math.floor(Number(origin.z));
    const targets = [];
    for (let y = baseY; y <= baseY + 2; y++) {
        for (let dx = -radius; dx <= radius; dx++) {
            for (let dz = -radius; dz <= radius; dz++) {
                const horizontal = Math.hypot(dx, dz);
                if (horizontal < 0.75 || horizontal > radius) continue;
                const x = baseX + dx;
                const z = baseZ + dz;
                const support = bot.blockAt(new Vec3(x, y - 1, z));
                const feet = bot.blockAt(new Vec3(x, y, z));
                const head = bot.blockAt(new Vec3(x, y + 1, z));
                if (
                    !blockIsSolidSupport(support) ||
                    !NON_SOLID_BLOCKS.has(feet?.name) ||
                    !NON_SOLID_BLOCKS.has(head?.name) ||
                    feet?.name === 'water' ||
                    head?.name === 'water'
                ) continue;
                let corridorClear = true;
                const samples = Math.max(2, Math.ceil(horizontal * 2));
                for (let sample = 1; sample < samples; sample++) {
                    const ratio = sample / samples;
                    const sampleX = Math.floor(
                        Number(origin.x) + ((x + 0.5 - Number(origin.x)) * ratio)
                    );
                    const sampleZ = Math.floor(
                        Number(origin.z) + ((z + 0.5 - Number(origin.z)) * ratio)
                    );
                    for (const sampleY of [y, y + 1]) {
                        if (bot.blockAt(new Vec3(sampleX, sampleY, sampleZ))?.boundingBox === 'block') {
                            corridorClear = false;
                        }
                    }
                }
                if (!corridorClear) continue;
                targets.push({x, y, z, distance: horizontal, rise: y - baseY});
            }
        }
    }
    return targets.sort((left, right) => (
        (right.rise - left.rise) ||
        (left.distance - right.distance)
    ));
}

async function swimToNearbyAir(bot) {
    for (const target of nearbyAirExitTargets(bot)) {
        if (await walkStaircaseStep(bot, target, {timeoutMs: 5000, pollMs: 50})) {
            return {
                progress: true,
                verification: 'water_exit_progress',
                target,
            };
        }
    }
    return {progress: false, verification: 'water_exit_blocked'};
}

export async function digStaircaseStep(bot) {
    const origin = bot.entity?.position;
    if (!origin || !bot.pathfinder) {
        return {progress: false, verification: 'staircase_not_connected'};
    }
    const directionIndex = Number(bot.evelynSurfaceEscapeDirection || 0);
    for (const target of staircaseTargets(origin, directionIndex)) {
        const support = bot.blockAt(new Vec3(target.x, target.y - 1, target.z));
        const feet = bot.blockAt(new Vec3(target.x, target.y, target.z));
        const head = bot.blockAt(new Vec3(target.x, target.y + 1, target.z));
        if (
            !blockIsSolidSupport(support) ||
            !blockCanBeCleared(bot, feet) ||
            !blockCanBeCleared(bot, head)
        ) continue;

        if (!await clearStairBlock(bot, head)) continue;
        if (!await clearStairBlock(bot, feet)) continue;

        const reached = await walkStaircaseStep(bot, target);
        const final = bot.entity?.position;
    const progressed = Boolean(
            reached &&
            final &&
            final.y >= target.y - 0.05 &&
            (() => {
                const horizontalDistance = Math.hypot(final.x - target.x, final.z - target.z);
                return horizontalDistance >= 0.4 && horizontalDistance <= 1.25;
            })()
        );
        if (progressed) {
            bot.evelynSurfaceEscapeDirection = (target.index + 1) % STAIRCASE_DIRECTIONS.length;
            return {
                progress: true,
                verification: 'staircase_upward_progress',
                target,
                position: {x: final.x, y: final.y, z: final.z},
            };
        }
    }

    // A dry cave pocket can have a solid floor at the bot's level but no
    // adjacent block one level higher. Move or tunnel one block sideways
    // first so the next pass has a real staircase base instead of retrying
    // the same impossible upward targets forever.
    for (const target of staircaseBaseTargets(origin, directionIndex)) {
        const support = bot.blockAt(new Vec3(target.x, target.y - 1, target.z));
        const feet = bot.blockAt(new Vec3(target.x, target.y, target.z));
        const head = bot.blockAt(new Vec3(target.x, target.y + 1, target.z));
        if (
            !blockIsSolidSupport(support) ||
            feet?.name === 'water' ||
            feet?.name === 'lava' ||
            head?.name === 'water' ||
            head?.name === 'lava' ||
            !blockCanBeCleared(bot, feet) ||
            !blockCanBeCleared(bot, head)
        ) continue;

        if (!await clearStairBlock(bot, head)) continue;
        if (!await clearStairBlock(bot, feet)) continue;

        const reached = await walkStaircaseStep(bot, target);
        const final = bot.entity?.position;
        const horizontal = final
            ? Math.hypot(Number(final.x) - Number(origin.x), Number(final.z) - Number(origin.z))
            : 0;
        if (reached && final && horizontal >= 0.4 && Number(final.y) >= Number(origin.y) - 0.5) {
            bot.evelynSurfaceEscapeDirection = target.index;
            return {
                progress: true,
                verification: 'staircase_base_progress',
                target,
                position: {x: final.x, y: final.y, z: final.z},
            };
        }
    }
    return {progress: false, verification: 'staircase_blocked'};
}

async function handleHostile(bot, snapshot, {failureCount = 0} = {}) {
    const tactic = selectHostileTactic(snapshot);
    const assessment = assessCombat(snapshot);
    if (!tactic) {
        return { success: true, tactic: 'none', verification: 'threat_disappeared' };
    }

    let escapeDetails = null;
    const external = tactic === 'fight'
        ? await runExternalHostileAction(
            bot,
            async () => {
                const result = await fightWithCustomPvp(bot, {
                    snapshotProvider: () => buildSurvivalSnapshot(bot),
                    hostileProvider: () => nearbyHostiles(bot, HOSTILE_AVOID_DISTANCE),
                    timeoutMs: HOSTILE_ACTION_TIMEOUT_MS - 1000,
                });
                if (result.reason === 'custom_pvp_unavailable') {
                    return skills.defendSelf(bot, HOSTILE_FIGHT_DISTANCE);
                }
                return result.success;
            },
        )
        : await (async () => {
            escapeDetails = await escapeFromHostiles(bot, {
                failureCount,
                safeDistance: HOSTILE_AVOID_DISTANCE,
                range: 24,
                timeoutMs: HOSTILE_ESCAPE_TIMEOUT_MS,
                hostileProvider: () => nearbyHostiles(bot, 24),
                surfaceY: snapshot.surfaceY,
            });
            return {timedOut: false, value: escapeDetails.success};
        })();
    const after = buildSurvivalSnapshot(bot);
    const verified = !external.timedOut && verifyHostileOutcome(tactic, snapshot, after, external.value);
    const stable = verified ? await remainsSafeFromHostiles(bot) : false;
    return {
        success: verified && stable,
        tactic,
        assessment,
        strategy: escapeDetails?.strategy || (tactic === 'fight' ? 'custom_pvp' : null),
        verification: external.timedOut
            ? 'timeout'
            : (
                !verified
                    ? (escapeDetails?.verification || 'unsafe_after_action')
                    : (stable ? 'stable_safe' : 'threat_returned')
            ),
        escape: escapeDetails,
        before: {
            hostileName: snapshot.hostileName,
            hostileId: snapshot.hostileId,
            hostileDistance: snapshot.hostileDistance,
            hostileCount: snapshot.hostileCount,
            health: snapshot.health,
        },
        after: {
            hostileName: after.hostileName,
            hostileId: after.hostileId,
            hostileDistance: after.hostileDistance,
            hostileCount: after.hostileCount,
            health: after.health,
        },
    };
}

function surfaceGoals(bot) {
    const position = bot.entity?.position;
    if (!position) return [];
    const goals = [];
    for (const [dx, dz] of SURFACE_OFFSETS) {
        const x = Math.floor(position.x + dx);
        const z = Math.floor(position.z + dz);
        const y = surfaceYAt(bot, x, z);
        if (y === null || y < position.y - 1) continue;
        goals.push(new pf.goals.GoalNear(x, y, z, 1));
    }
    return goals;
}

async function escapeToSurface(bot) {
    const position = bot.entity?.position;
    if (!position) {
        return {success: false, verification: 'not_connected'};
    }
    if (bot.entity?.isInWater) {
        const startPosition = {x: position.x, y: position.y, z: position.z};
        const startSurfaceY = currentSurfaceY(bot);
        const waterExit = await swimToNearbyAir(bot);
        const finalPosition = bot.entity?.position
            ? {x: bot.entity.position.x, y: bot.entity.position.y, z: bot.entity.position.z}
            : null;
        const finalSurfaceY = bot.entity?.position ? currentSurfaceY(bot) : null;
        const finalInWater = bot.entity?.isInWater;
        const success = verifySurfaceEscape({
            reached: waterExit.progress,
            startPosition,
            finalPosition,
            surfaceY: finalSurfaceY,
            finalInWater,
            requireOutOfWater: true,
        });
        if (success) {
            bot.evelynSurfaceEscapeStrategy = null;
            bot.evelynSurfaceEscapeDirection = 0;
            return {
                success,
                progress: true,
                verification: 'surface_reached',
                strategy: 'swim_to_air',
                target: waterExit.target || null,
                before: {position: startPosition, surfaceY: startSurfaceY},
                after: {position: finalPosition, surfaceY: finalSurfaceY},
            };
        }
        if (waterExit.progress && finalInWater) {
            bot.evelynSurfaceEscapeStrategy = 'staircase';
        }
    }
    const goals = surfaceGoals(bot);
    if (!goals.length) {
        return {success: false, verification: 'surface_unknown'};
    }
    const minimumY = Math.floor(position.y) - 1;
    const goal = goals.length === 1 ? goals[0] : new pf.goals.GoalCompositeAny(goals);
    const startPosition = {x: position.x, y: position.y, z: position.z};
    const startSurfaceY = currentSurfaceY(bot);
    if (bot.evelynSurfaceEscapeStrategy !== 'staircase') {
        const movements = configureSafeMovements(bot, minimumY);
        const reached = await pathWithTimeout(bot, goal, movements);
        const directPosition = bot.entity?.position
            ? {x: bot.entity.position.x, y: bot.entity.position.y, z: bot.entity.position.z}
            : null;
        const directSurfaceY = bot.entity?.position ? currentSurfaceY(bot) : null;
        if (verifySurfaceEscape({
            reached,
            startPosition,
            finalPosition: directPosition,
            surfaceY: directSurfaceY,
        })) {
            bot.evelynSurfaceEscapeStrategy = null;
            bot.evelynSurfaceEscapeDirection = 0;
            return {
                success: true,
                progress: true,
                verification: 'surface_reached',
                strategy: 'pathfinder',
                before: {position: startPosition, surfaceY: startSurfaceY},
                after: {position: directPosition, surfaceY: directSurfaceY},
            };
        }
        bot.evelynSurfaceEscapeStrategy = 'staircase';
    }

    const staircase = await digStaircaseStep(bot);
    const finalPosition = bot.entity?.position
        ? {x: bot.entity.position.x, y: bot.entity.position.y, z: bot.entity.position.z}
        : null;
    const finalSurfaceY = bot.entity?.position ? currentSurfaceY(bot) : null;
    const success = verifySurfaceEscape({
        reached: staircase.progress,
        startPosition,
        finalPosition,
        surfaceY: finalSurfaceY,
    });
    if (success) {
        bot.evelynSurfaceEscapeStrategy = null;
        bot.evelynSurfaceEscapeDirection = 0;
    }
    return {
        success,
        progress: Boolean(staircase.progress),
        verification: success
            ? 'surface_reached'
            : staircase.verification,
        strategy: 'staircase',
        target: staircase.target || null,
        before: {position: startPosition, surfaceY: startSurfaceY},
        after: {position: finalPosition, surfaceY: finalSurfaceY},
    };
}

function cropIsMature(block) {
    if (!block || !CROP_NAMES.has(block.name)) return false;
    if (block.name === 'melon') return true;
    const age = Number(block.getProperties?.().age);
    if (!Number.isFinite(age)) return block.name === 'sweet_berry_bush';
    if (block.name === 'beetroots') return age >= 3;
    if (block.name === 'sweet_berry_bush') return age >= 2;
    return age >= 7;
}

async function craftAndEatAvailableFood(bot) {
    let food = selectBestFood(bot);
    if (food) return skills.consume(bot, food.name);
    const inventory = inventoryCounts(bot);
    if ((inventory.wheat || 0) >= 3) {
        await skills.craftRecipe(bot, 'bread', 1);
        food = selectBestFood(bot);
        if (food) return skills.consume(bot, food.name);
    }
    return false;
}

async function acquireFood(bot) {
    if (await craftAndEatAvailableFood(bot)) return true;
    const blocks = world.getNearestBlocksWhere(bot, cropIsMature, 48, 8);
    if (!blocks.length) return false;
    for (const block of blocks) {
        try {
            await bot.collectBlock.collect(block);
        } catch (error) {
            console.warn('[Evelyn Survival] crop collection failed:', error?.message || error);
            continue;
        }
        if (await craftAndEatAvailableFood(bot)) return true;
    }
    return false;
}

function nearestLog(bot) {
    return world.getNearestBlocksWhere(
        bot,
        (block) => block?.name?.endsWith('_log'),
        48,
        1,
    )[0] || null;
}

async function bootstrapTools(bot) {
    let inventory = inventoryCounts(bot);
    if (hasPickaxe(inventory)) return true;
    const totalLogs = () => Object.entries(inventory)
        .filter(([name]) => name.endsWith('_log'))
        .reduce((sum, [, count]) => sum + Number(count || 0), 0);
    while (totalLogs() < 3) {
        const logBlock = nearestLog(bot);
        if (!logBlock) return false;
        await bot.collectBlock.collect(logBlock);
        inventory = inventoryCounts(bot);
    }
    for (const [logName, count] of Object.entries(inventory)) {
        if (!logName.endsWith('_log') || Number(count || 0) < 1) continue;
        const planks = `${logName.replace(/_log$/, '')}_planks`;
        await skills.craftRecipe(bot, planks, Number(count));
    }
    await skills.craftRecipe(bot, 'stick', 1);
    await skills.craftRecipe(bot, 'crafting_table', 1);
    await skills.craftRecipe(bot, 'wooden_pickaxe', 1);
    return hasPickaxe(inventoryCounts(bot));
}

async function performDecision(agent, decision, snapshot, context = {}) {
    const bot = agent.bot;
    if (decision === 'handle_hostile') return handleHostile(bot, snapshot, context);
    if (decision === 'eat_inventory_food') return skills.consume(bot, snapshot.foodName);
    if (decision === 'escape_to_surface') return escapeToSurface(bot);
    if (decision === 'acquire_food') return acquireFood(bot);
    if (decision === 'bootstrap_tools') return bootstrapTools(bot);
    return true;
}

function logDecision(agent, message) {
    const line = `[Evelyn Survival] ${message}`;
    console.log(line);
    if (agent.bot.modes) agent.bot.modes.behavior_log += `${line}\n`;
}

export function createEvelynSurvivalMode({ execute }) {
    const enableToolBootstrap = /^(?:1|true|yes)$/i.test(
        String(process.env.MINDCRAFT_DETERMINISTIC_TOOL_BOOTSTRAP || '')
    );
    return {
        name: 'evelyn_survival',
        description: 'Deterministic non-operator food, hostile avoidance, and critical surface recovery.',
        interrupts: ['all'],
        on: true,
        active: false,
        inFlight: false,
        lastCheckAt: 0,
        failures: {},
        cooldownUntil: {},
        update: async function (agent) {
            const now = Date.now();
            if (this.inFlight) return;
            if (now - this.lastCheckAt < CHECK_INTERVAL_MS) return;
            this.lastCheckAt = now;
            const snapshot = buildSurvivalSnapshot(agent.bot);
            if (snapshot.hunger <= FOOD_ACQUIRE_HUNGER && !snapshot.foodName) {
                agent.goal_manager?.requestPriorityGoal?.('food', snapshot);
            }
            if (
                !snapshot.hasMeleeWeapon ||
                !snapshot.hasPickaxe ||
                !snapshot.foodName
            ) {
                agent.goal_manager?.requestPriorityGoal?.('minimum_kit', snapshot);
            }
            const decisions = listSurvivalDecisions(snapshot, {enableToolBootstrap});
            const decision = decisions.find((candidate) => Number(this.cooldownUntil[candidate] || 0) <= now) || null;
            const deferredDecision = decision && !decisionCanInterrupt(decision) && !agent.isIdle();
            agent.bot.evelynSurvivalState = mergeSurvivalState(agent.bot.evelynSurvivalState, {
                phase: deferredDecision ? 'planner_control' : (decision || 'planner_control'),
                snapshot,
                failures: { ...this.failures },
                cooldown_until: { ...this.cooldownUntil },
                updated_at: nowSeconds(),
            });
            if (!decision || deferredDecision) return;

            this.inFlight = true;
            execute(this, agent, async () => {
                logDecision(agent, `starting ${decision}`);
                let success = false;
                let error = null;
                let details = null;
                try {
                    const result = await performDecision(agent, decision, snapshot, {
                        failureCount: Number(this.failures[decision] || 0),
                    });
                    details = result && typeof result === 'object' ? result : null;
                    success = Boolean(details ? details.success : result);
                } catch (caught) {
                    error = String(caught?.message || caught);
                    console.error('[Evelyn Survival] decision failed:', decision, error);
                } finally {
                    this.inFlight = false;
                }
                const completedAt = Date.now();
                const progressed = Boolean(details?.progress);
                let plannerHandoffUntil = 0;
                if (success) {
                    this.failures[decision] = 0;
                    this.cooldownUntil[decision] = completedAt + 3000;
                } else if (progressed) {
                    this.failures[decision] = 0;
                    this.cooldownUntil[decision] = completedAt + 1000;
                } else {
                const failures = Number(this.failures[decision] || 0) + 1;
                this.failures[decision] = failures;
                const handoff = shouldHandoffEscapeFailure(decision, details, failures);
                    this.cooldownUntil[decision] = handoff
                        ? completedAt + ESCAPE_FAIL_HANDOFF_DELAY_MS
                        : completedAt + failureCooldownMs(decision, failures);
                    plannerHandoffUntil = handoff ? completedAt + ESCAPE_FAIL_HANDOFF_DELAY_MS : 0;
                }
                agent.bot.evelynSurvivalState = mergeSurvivalState(agent.bot.evelynSurvivalState, {
                    phase: plannerHandoffUntil ? 'planner_control' : (success ? 'reassess' : decision),
                    last_decision: decision,
                    last_success: success,
                    last_error: error,
                    hostile_tactic: details?.tactic || null,
                    hostile_strategy: details?.strategy || null,
                    hostile_assessment: details?.assessment || null,
                    hostile_verification: details?.verification || null,
                    hostile_before: details?.before || null,
                    hostile_after: details?.after || null,
                    recovery_strategy: details?.strategy || null,
                    recovery_verification: details?.verification || null,
                    recovery_progress: progressed,
                    recovery_handoff_until: plannerHandoffUntil,
                    snapshot: buildSurvivalSnapshot(agent.bot),
                    failures: { ...this.failures },
                    cooldown_until: { ...this.cooldownUntil },
                    updated_at: nowSeconds(),
                });
                const detailText = details?.tactic
                    ? ` tactic=${details.tactic} verification=${details.verification}` +
                        (details?.assessment
                            ? ` readiness=${details.assessment.capacity} threat=${details.assessment.threat} reason=${details.assessment.reason}`
                            : '')
                    : (details?.verification ? ` verification=${details.verification}` : '');
                const outcome = success ? 'succeeded' : (progressed ? 'progressed' : 'failed');
                logDecision(agent, `${decision} ${outcome}${detailText}`);
            }, 1);
        },
    };
}
