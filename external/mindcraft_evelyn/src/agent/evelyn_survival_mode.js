import pf from 'mineflayer-pathfinder';
import Vec3 from 'vec3';
import * as skills from './library/skills.js';
import * as world from './library/world.js';
import { isKnownHostile } from './evelyn_world_state.js';
import { isHuntable } from '../utils/mcdata.js';
import {
    armorPointsFromNames,
    assessCombat,
    fightWithCustomPvp,
    stopCombatControllers,
    weaponPowerFromInventory,
} from './evelyn_combat.js';
import {
    appendCombatEpisode,
    createCombatHistoryWriter,
    loadCombatHistory,
    normalizeCombatContext,
    selectCombatPreset,
} from './evelyn_combat_experience.js';
import { escapeFromHostiles } from './evelyn_escape_controller.js';

const HOSTILE_AVOID_DISTANCE = 18;
const HOSTILE_FIGHT_DISTANCE = 8;
const HOSTILE_STABLE_MS = 2000;
const HOSTILE_ACTION_TIMEOUT_MS = 25000;
const HOSTILE_ESCAPE_TIMEOUT_MS = 20000;
const HOSTILE_REFLEX_TIMEOUT_MS = 1100;
const EMERGENCY_MELEE_HANDOFF = 'emergency_melee_handoff';
const PROJECTILE_IMMINENT_TICKS = 14;
const PROJECTILE_WAKE_INTERVAL_MS = 50;
const PROJECTILE_SHIELD_MAX_HOLD_MS = 1000;
const PROJECTILE_SHIELD_GRACE_MS = 150;
const MINECRAFT_TICK_MS = 50;
const CRITICAL_HUNGER = 6;
const FOOD_ACQUIRE_HUNGER = 14;
const CRITICAL_HEALTH = 10;
const SAFE_BOOTSTRAP_HUNGER = 8;
const SAFE_BOOTSTRAP_HEALTH = 12;
const CHECK_INTERVAL_MS = 1500;
const CHEAP_WAKE_INTERVAL_MS = 150;
const ACTION_TIMEOUT_MS = 30000;
const BOOTSTRAP_INVENTORY_SETTLE_POLLS = 20;
const BOOTSTRAP_INVENTORY_SETTLE_POLL_MS = 50;
const FOOD_HUNT_TIMEOUT_MS = 15000;
const FOOD_HUNT_POLL_MS = 100;
const SHELTER_BLOCK_NAME = 'dirt';
const SHELTER_SUCCESS_VERIFICATION = 'shelter_dawn_exit_verified';
const SHELTER_BUILD_TIME = 11000;
const SHELTER_BUILD_DEADLINE = 13000;
const SHELTER_GATHER_TIMEOUT_MS = 45000;
const SHELTER_POLL_MS = 100;
const SHELTER_CLEAR_STABLE_MS = 2000;
const SHELTER_SITE_RADIUS = 8;
const SHELTER_FALLBACK_SITE_RADIUS = 16;
const PATH_STUCK_RESET_LIMIT = 2;
const RECOVERABLE_NAVIGATION_ERRORS = new Set(['GoalChanged', 'NoPath', 'PathStopped', 'Timeout']);
export const TEMPORARY_SHELTER_BLOCK_COUNT = 18;
const UNSAFE_SHELTER_REPLACEABLES = new Set([
    'fire', 'lava', 'powder_snow', 'soul_fire', 'water',
]);
const COMBAT_HISTORY_PATH = process.env.MINDCRAFT_COMBAT_HISTORY_PATH
    || '/app/runtime_artifacts/mindcraft/combat_history.json';
const COMBAT_PLUGIN_VERSION = '1.7.16';
export const SURVIVAL_WAKE_REASONS = Object.freeze({
    HEALTH: 'health',
    BREATH: 'breath',
    HOSTILE_SPAWN: 'hostile_spawn',
    HOSTILE_BAND: 'hostile_band',
    HOSTILE_GONE: 'hostile_gone',
    PROJECTILE: 'projectile',
    FALLBACK: 'fallback',
});
const ESCAPE_FAIL_HANDOFF_VERIFICATIONS = new Set([
    'staircase_blocked',
    'water_exit_blocked',
    'surface_unknown',
]);
const ESCAPE_FAIL_HANDOFF_LIMIT = 3;
const ESCAPE_FAIL_HANDOFF_DELAY_MS = 25000;
const FOOD_FAIL_HANDOFF_DELAY_MS = 30000;
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
const RANGED_ESCAPE_HOSTILES = new Set([
    'blaze', 'bogged', 'elder_guardian', 'evoker', 'ghast', 'guardian',
    'parched', 'pillager', 'shulker', 'skeleton', 'stray', 'witch',
]);
const DAMAGING_PROJECTILES = new Set(['arrow', 'fireball', 'firework_rocket', 'trident']);
const CROP_NAMES = new Set([
    'beetroots', 'carrots', 'melon', 'potatoes', 'sweet_berry_bush', 'wheat',
]);
const SAFE_FOOD_PREY = new Set(['cow', 'pig', 'sheep']);
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

function foodEvidence(bot) {
    const items = bot.inventory?.items?.() || [];
    const safeFoodCount = items.reduce((total, item) => {
        if (!item?.name || UNSAFE_FOODS.has(item.name)) return total;
        const edible = Boolean(bot.registry?.foodsByName?.[item.name]) || FALLBACK_FOODS.has(item.name);
        return edible ? total + Number(item.count || 0) : total;
    }, 0);
    const wheatCount = items
        .filter((item) => item?.name === 'wheat')
        .reduce((total, item) => total + Number(item.count || 0), 0);
    return {
        hunger: Number.isFinite(bot.food) ? bot.food : 20,
        safeFoodCount,
        wheatCount,
    };
}

function compareFoodEvidence(before, after) {
    const hungerIncreased = after.hunger > before.hunger;
    const safeFoodAcquired = after.safeFoodCount > before.safeFoodCount;
    const wheatAcquired = after.wheatCount > before.wheatCount;
    return {
        success: hungerIncreased || safeFoodAcquired,
        progress: hungerIncreased || safeFoodAcquired || wheatAcquired,
    };
}

function nearestHostile(bot, range = 24) {
    return nearbyHostiles(bot, range)[0]?.entity || null;
}

function blockDefinesSurface(block) {
    return Boolean(
        block?.name &&
        block.name !== 'air' &&
        block.name !== 'cave_air' &&
        block.name !== 'void_air' &&
        block.boundingBox !== 'empty' &&
        !block.name.endsWith('_leaves') &&
        !block.name.endsWith('_log')
    );
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
        if (blockDefinesSurface(block)) {
            const value = y + 1;
            surfaceCache.set(cacheKey, { value, expiresAt: Date.now() + 5000 });
            return value;
        }
    }
    surfaceCache.set(cacheKey, { value: null, expiresAt: Date.now() + 2000 });
    return null;
}

function boundedSurfaceYAt(bot, x, z, originY, radius) {
    const blockX = Math.floor(x);
    const blockZ = Math.floor(z);
    const centerY = Math.floor(originY);
    const limit = Math.max(0, Math.floor(Number(radius) || 0));
    const top = Math.min(320, centerY + limit);
    const bottom = Math.max(-63, centerY - limit);
    for (let surfaceY = top; surfaceY >= bottom; surfaceY--) {
        if (blockDefinesSurface(bot.blockAt(new Vec3(blockX, surfaceY - 1, blockZ)))) {
            return surfaceY;
        }
    }
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
    const sheltered = temporaryShelterVerified(bot, position);
    return Object.values(bot.entities || {})
        .filter((entity) => entity?.position && entity !== bot.entity && entityIsHostile(entity))
        .map((entity) => ({ entity, distance: position.distanceTo(entity.position) }))
        .filter(({ entity, distance }) => (
            Number.isFinite(distance) &&
            distance <= range &&
            hostileIsActionable(position, entity.position, distance) &&
            (hostileHasClearLine(bot, entity) || (distance <= 4 && !sheltered))
        ))
        .sort((left, right) => left.distance - right.distance);
}

function entityIsHostile(entity) {
    return Boolean(
        entity &&
        (
            isKnownHostile(entity.name) ||
            String(entity.type || '').toLowerCase() === 'hostile'
        )
    );
}

function hostileWakeBand(bot, entity) {
    const origin = bot?.entity?.position;
    if (!origin || !entity?.position || !entityIsHostile(entity)) return null;
    const distance = origin.distanceTo(entity.position);
    if (!Number.isFinite(distance)) return null;
    if (temporaryShelterVerified(bot, origin) && !hostileHasClearLine(bot, entity)) return 3;
    if (
        distance <= HOSTILE_AVOID_DISTANCE &&
        (
            !hostileIsActionable(origin, entity.position, distance) ||
            (distance > 4 && !hostileHasClearLine(bot, entity))
        )
    ) return 3;
    if (distance <= 4) return 0;
    if (distance <= HOSTILE_FIGHT_DISTANCE) return 1;
    if (distance <= HOSTILE_AVOID_DISTANCE) return 2;
    return 3;
}

export function imminentProjectileThreat(bot, maxTicks = PROJECTILE_IMMINENT_TICKS) {
    if (!bot?.entity?.position) return null;
    const boundedTicks = Math.max(0, Number(maxTicks) || 0);
    const validThreat = (threat) => {
        const ticks = Number(threat?.shotInfo?.totalTicks);
        return (
            threat?.entity?.position &&
            threat.entity.isValid !== false &&
            DAMAGING_PROJECTILES.has(String(threat.entity.name || '').toLowerCase()) &&
            Number.isFinite(ticks) &&
            ticks >= 0 &&
            ticks <= boundedTicks
        );
    };
    try {
        if (typeof bot.projectiles?.getIncomingProjectiles === 'function') {
            const threats = bot.projectiles.getIncomingProjectiles();
            if (Array.isArray(threats)) {
                return threats
                    .filter(validThreat)
                    .sort((left, right) => left.shotInfo.totalTicks - right.shotInfo.totalTicks)[0] || null;
            }
        }
    } catch {
        // Fall back to the plugin's cached getter below.
    }
    try {
        const threat = bot.projectiles?.projectileAtMe;
        return validThreat(threat) ? threat : null;
    } catch {
        return null;
    }
}

function offhandShieldEquipped(bot) {
    try {
        if (bot.supportFeature?.('doesntHaveOffHandSlot')) return false;
        const slot = bot.getEquipmentDestSlot?.('off-hand');
        return Number.isInteger(slot) && bot.inventory?.slots?.[slot]?.name === 'shield';
    } catch {
        return false;
    }
}

export function projectileLateralDirection(bot, threat) {
    const projectile = threat?.entity;
    const origin = bot?.entity?.position;
    if (!projectile?.position || !origin) return null;
    let velocityX = Number(projectile.velocity?.x);
    let velocityZ = Number(projectile.velocity?.z);
    let speed = Math.hypot(velocityX, velocityZ);
    if (!Number.isFinite(speed) || speed < 0.001) {
        velocityX = Number(origin.x) - Number(projectile.position.x);
        velocityZ = Number(origin.z) - Number(projectile.position.z);
        speed = Math.hypot(velocityX, velocityZ);
    }
    if (!Number.isFinite(speed) || speed < 0.001) return null;
    const x = -velocityZ / speed;
    const z = velocityX / speed;
    return {x: Object.is(x, -0) ? 0 : x, z: Object.is(z, -0) ? 0 : z};
}

function attachSurvivalWakeListeners(mode, bot, agent) {
    if (mode.listenerBot === bot || typeof bot?.on !== 'function') return;
    mode.detachWakeListeners?.();
    startCombatExperience(bot);

    const hostileBands = new Map();
    for (const entity of Object.values(bot.entities || {})) {
        const band = hostileWakeBand(bot, entity);
        if (band !== null) hostileBands.set(entity.id, band);
    }
    mode.lastObservedHealth = bot.health;
    mode.lastObservedFood = bot.food;
    mode.lastObservedOxygen = bot.oxygenLevel;

    const markWake = (reason) => {
        const receivedAt = Date.now();
        mode.dirty = true;
        if (!mode.urgent) {
            mode.urgent = true;
            mode.wakeReason = reason;
            mode.wakeReceivedAt = receivedAt;
        }
        return receivedAt;
    };
    const startCriticalReflex = (entity = null, detectedAt = Date.now()) => {
        if (mode.hostileReflexPromise) return;
        let band = entity ? hostileWakeBand(bot, entity) : null;
        if (band === null) {
            band = Object.values(bot.entities || {}).reduce(
                (closest, candidate) => Math.min(closest, hostileWakeBand(bot, candidate) ?? 3),
                3,
            );
        }
        if (mode.inFlight) {
            if (
                mode.currentDecision === 'handle_hostile' &&
                mode.hostileReflexHandoffPending &&
                band <= 1
            ) {
                mode.startHostileAdmissionGuard?.();
                return;
            }
            if (
                ['escape_to_surface', 'shelter_until_safe_dawn', 'bootstrap_tools'].includes(mode.currentDecision) &&
                band <= 2
            ) {
                agent.requestInterrupt?.();
            }
            return;
        }
        if (mode.hostileReflexHandoffPending) {
            mode.startHostileAdmissionGuard?.();
            return;
        }
        if (band > 1) return;
        const reflex = startHostilePreemptionReflex(
            agent,
            {surfaceY: null},
            Date.now(),
            detectedAt,
        );
        if (!reflex) return;
        mode.hostileReflexPromise = reflex.catch(() => ({
            success: false,
            strategy: 'hostile_preemption_reflex',
            verification: 'reflex_error',
        })).finally(() => {
            if (mode.hostileReflexPromise) mode.hostileReflexPromise = null;
            if (mode.listenerBot !== bot) return;
            mode.hostileReflexHandoffPending = true;
            markWake(SURVIVAL_WAKE_REASONS.FALLBACK);
        });
    };
    const startProjectileReflex = (threat, detectedAt) => {
        if (mode.hostileReflexPromise) return false;
        const reflex = startProjectileDefenseReflex(agent, threat, {detectedAt});
        if (!reflex) return false;
        mode.hostileReflexPromise = reflex.catch(() => ({
            success: false,
            strategy: 'projectile_defense',
            verification: 'projectile_reflex_error',
        })).finally(() => {
            if (mode.hostileReflexPromise) mode.hostileReflexPromise = null;
            if (mode.listenerBot !== bot) return;
            markWake(SURVIVAL_WAKE_REASONS.FALLBACK);
            mode.startHostileAdmissionGuard?.();
        });
        return true;
    };
    const handlers = {
        health: () => {
            if (bot.health === mode.lastObservedHealth && bot.food === mode.lastObservedFood) return;
            const previousHealth = Number(mode.lastObservedHealth);
            mode.lastObservedHealth = bot.health;
            mode.lastObservedFood = bot.food;
            const detectedAt = markWake(SURVIVAL_WAKE_REASONS.HEALTH);
            if (Number(bot.health) < previousHealth) {
                if (
                    mode.inFlight &&
                    ['escape_to_surface', 'shelter_until_safe_dawn', 'bootstrap_tools'].includes(mode.currentDecision)
                ) {
                    agent.requestInterrupt?.();
                } else {
                    startCriticalReflex(null, detectedAt);
                }
            }
        },
        breath: () => {
            if (bot.oxygenLevel === mode.lastObservedOxygen) return;
            mode.lastObservedOxygen = bot.oxygenLevel;
            markWake(SURVIVAL_WAKE_REASONS.BREATH);
        },
        entitySpawn: (entity) => {
            const band = hostileWakeBand(bot, entity);
            if (band === null) return;
            hostileBands.set(entity.id, band);
            if (band <= 2) {
                const detectedAt = markWake(SURVIVAL_WAKE_REASONS.HOSTILE_SPAWN);
                startCriticalReflex(entity, detectedAt);
            }
        },
        entityMoved: (entity) => {
            const band = hostileWakeBand(bot, entity);
            if (band === null) return;
            const previous = hostileBands.get(entity.id) ?? 3;
            hostileBands.set(entity.id, band);
            if (band !== previous && (band <= 2 || previous <= 2)) {
                const detectedAt = markWake(SURVIVAL_WAKE_REASONS.HOSTILE_BAND);
                startCriticalReflex(entity, detectedAt);
            }
        },
        entityGone: (entity) => {
            const previous = hostileBands.get(entity?.id);
            hostileBands.delete(entity?.id);
            if (previous !== undefined && previous <= 2) {
                markWake(SURVIVAL_WAKE_REASONS.HOSTILE_GONE);
            }
        },
        physicsTick: () => {
            const now = Date.now();
            const projectile = imminentProjectileThreat(bot);
            if (projectile) {
                const detectedAt = markWake(SURVIVAL_WAKE_REASONS.PROJECTILE);
                if (startProjectileReflex(projectile, detectedAt)) return;
            }
            if (now - mode.lastCheapWakeCheckAt < CHEAP_WAKE_INTERVAL_MS) return;
            mode.lastCheapWakeCheckAt = now;
            for (const [entityId, previous] of hostileBands) {
                const entity = bot.entities?.[entityId];
                if (!entity) {
                    hostileBands.delete(entityId);
                    continue;
                }
                const band = hostileWakeBand(bot, entity);
                if (band === null) {
                    hostileBands.delete(entityId);
                    continue;
                }
                hostileBands.set(entity.id, band);
                if (band !== previous && (band <= 2 || previous <= 2)) {
                    const detectedAt = markWake(SURVIVAL_WAKE_REASONS.FALLBACK);
                    startCriticalReflex(entity, detectedAt);
                    break;
                }
            }
        },
        end: () => mode.detachWakeListeners?.(),
    };
    for (const [event, handler] of Object.entries(handlers)) bot.on(event, handler);
    mode.listenerBot = bot;
    mode.detachWakeListeners = () => {
        if (mode.listenerBot !== bot) return;
        for (const [event, handler] of Object.entries(handlers)) bot.off?.(event, handler);
        hostileBands.clear();
        mode.hostileReflexHandoffPending = false;
        mode.startHostileAdmissionGuard = null;
        mode.listenerBot = null;
        mode.detachWakeListeners = null;
    };
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

function flooredVec(position) {
    if (!position) return null;
    if (typeof position.floored === 'function') return position.floored();
    const coordinates = [position.x, position.y, position.z].map(Number);
    if (!coordinates.every(Number.isFinite)) return null;
    return new Vec3(...coordinates.map(Math.floor));
}

function blockIsAir(block) {
    return ['air', 'cave_air', 'void_air'].includes(String(block?.name || ''));
}

function blockIsSafeEmpty(block) {
    return Boolean(
        blockIsAir(block) ||
        (
            block?.name &&
            block.boundingBox === 'empty' &&
            !UNSAFE_SHELTER_REPLACEABLES.has(block.name)
        )
    );
}

function blockIsSolid(block) {
    if (!block?.name) return false;
    if (block.boundingBox !== undefined) return block.boundingBox === 'block';
    return !NON_SOLID_BLOCKS.has(block.name);
}

export function temporaryShelterLayout(center) {
    const origin = flooredVec(center);
    if (!origin) return null;
    const walls = [];
    for (let y = 0; y <= 1; y++) {
        for (let dx = -1; dx <= 1; dx++) {
            for (let dz = -1; dz <= 1; dz++) {
                if (dx === 0 && dz === 0) continue;
                walls.push(origin.offset(dx, y, dz));
            }
        }
    }
    const roofScaffold = origin.offset(1, 2, 0);
    const roof = origin.offset(0, 2, 0);
    const supports = [];
    for (let dx = -1; dx <= 1; dx++) {
        for (let dz = -1; dz <= 1; dz++) {
            supports.push(origin.offset(dx, -1, dz));
        }
    }
    return {
        center: origin,
        targets: [...walls, roofScaffold, roof],
        supports,
        doorway: [origin.offset(1, 1, 0), origin.offset(1, 0, 0)],
    };
}

function temporaryShelterPlan(bot, center) {
    const layout = temporaryShelterLayout(center);
    if (!layout || typeof bot?.blockAt !== 'function') return null;
    if (!blockIsSafeEmpty(bot.blockAt(layout.center)) || !blockIsSafeEmpty(bot.blockAt(layout.center.offset(0, 1, 0)))) {
        return null;
    }
    if (!layout.supports.every((position) => blockIsSolid(bot.blockAt(position)))) return null;
    const missing = [];
    for (const position of layout.targets) {
        const block = bot.blockAt(position);
        if (block?.name === SHELTER_BLOCK_NAME) continue;
        if (!blockIsSafeEmpty(block)) return null;
        missing.push(position);
    }
    return {...layout, missing};
}

function nearbyTemporaryShelterPlans(bot, origin, radius = SHELTER_SITE_RADIUS) {
    const start = flooredVec(origin);
    const limit = Math.max(0, Math.floor(Number(radius) || 0));
    if (!start || typeof bot?.blockAt !== 'function') return [];
    const candidates = [];
    for (let dx = -limit; dx <= limit; dx++) {
        for (let dz = -limit; dz <= limit; dz++) {
            if (dx * dx + dz * dz > limit * limit) continue;
            const y = boundedSurfaceYAt(bot, start.x + dx, start.z + dz, start.y, limit);
            if (!Number.isFinite(y)) continue;
            const distanceSquared = dx * dx + dz * dz + (y - start.y) * (y - start.y);
            if (distanceSquared > limit * limit) continue;
            const plan = temporaryShelterPlan(bot, new Vec3(start.x + dx, y, start.z + dz));
            if (!plan) continue;
            candidates.push({plan, distanceSquared, dx, dz});
        }
    }
    return candidates
        .sort((left, right) => (
            left.distanceSquared - right.distanceSquared ||
            left.dx - right.dx ||
            left.dz - right.dz
        ))
        .map((candidate) => candidate.plan);
}

export function temporaryShelterVerified(bot, center = bot?.entity?.position) {
    const plan = temporaryShelterPlan(bot, center);
    return Boolean(plan && plan.missing.length === 0);
}

function temporaryShelterBreachVerification(bot, center) {
    const layout = temporaryShelterLayout(center);
    if (!layout || typeof bot?.blockAt !== 'function') return 'shelter_breached';
    if (
        !blockIsSafeEmpty(bot.blockAt(layout.center)) ||
        !blockIsSafeEmpty(bot.blockAt(layout.center.offset(0, 1, 0)))
    ) return 'shelter_breached_interior';
    if (!layout.supports.every((position) => blockIsSolid(bot.blockAt(position)))) {
        return 'shelter_breached_support';
    }
    const targets = layout.targets.map((position) => bot.blockAt(position));
    if (targets.some((block) => blockIsSafeEmpty(block))) return 'shelter_breached_missing_block';
    if (targets.some((block) => block?.name === 'grass_block')) {
        return 'shelter_breached_material_changed';
    }
    if (targets.some((block) => block?.name !== SHELTER_BLOCK_NAME)) {
        return 'shelter_breached_replaced_block';
    }
    return null;
}

function shelterRiskTime(timeOfDay) {
    const time = Number(timeOfDay);
    return Number.isFinite(time) && time >= SHELTER_BUILD_TIME && time < SHELTER_BUILD_DEADLINE;
}

function safeDawnTime(timeOfDay) {
    const time = Number(timeOfDay);
    return Number.isFinite(time) && time >= 0 && time < SHELTER_BUILD_TIME;
}

export function shelterDecisionAllowed(snapshot) {
    if (
        !snapshot?.connected ||
        !Number.isFinite(Number(snapshot?.timeOfDay)) ||
        snapshot?.inWater ||
        Number(snapshot?.hostileCount || 0) > 0 ||
        (snapshot?.hostileDistance !== null && snapshot?.hostileDistance !== undefined)
    ) return false;
    if (snapshot.sheltered) return true;
    return Boolean(
        !snapshot.underground &&
        Number(snapshot.health) >= SAFE_BOOTSTRAP_HEALTH &&
        Number(snapshot.hunger) >= SAFE_BOOTSTRAP_HUNGER &&
        shelterRiskTime(snapshot.timeOfDay)
    );
}

function inventoryItemCount(bot, name) {
    return (bot?.inventory?.items?.() || []).reduce(
        (total, item) => total + (item?.name === name ? Number(item.count || 0) : 0),
        0,
    );
}

function disableShelterScaffolding(movements) {
    if (movements && Array.isArray(movements.scafoldingBlocks)) {
        movements.scafoldingBlocks = [];
        movements.allow1by1towers = false;
    }
    return movements;
}

function shelterDirtCandidate(bot, exclude) {
    if (
        typeof bot?.findBlocks !== 'function' ||
        typeof bot?.blockAt !== 'function' ||
        typeof bot?.canDigBlock !== 'function' ||
        typeof bot?.canSeeBlock !== 'function'
    ) return {block: null, phase: 'generic_collect_probe_unavailable'};
    const excluded = new Set((exclude || []).map((position) => position.toString()));
    let materialCandidates = 0;
    let safeCandidates = 0;
    const candidates = world.getNearestBlocksWhere(
        bot,
        (block) => {
            if (
                !['dirt', 'grass_block'].includes(block?.name)
            ) return false;
            // Mineflayer 1.21.11 probes the palette with positionless blocks first.
            if (!block.position) return true;
            if (excluded.has(block.position.toString())) return false;
            materialCandidates += 1;
            if (bot.collectBlock?.movements?.safeToBreak?.(block) === false) return false;
            safeCandidates += 1;
            return true;
        },
        8,
        32,
    );
    const diggable = candidates.filter((block) => bot.canDigBlock(block));
    const block = diggable.find((candidate) => bot.canSeeBlock(candidate)) || null;
    if (block) return {block, phase: 'direct_collect'};
    if (materialCandidates === 0) return {block: null, phase: 'generic_collect_no_candidate'};
    if (safeCandidates === 0) return {block: null, phase: 'generic_collect_unsafe_candidate'};
    if (diggable.length === 0) return {block: null, phase: 'generic_collect_not_diggable'};
    return {block: null, phase: 'generic_collect_not_visible'};
}

async function returnToShelterCenter(bot, center, timeoutMs) {
    const current = flooredVec(bot.entity?.position);
    if (!current) return false;
    if (current.equals(center)) return true;
    return pathWithTimeout(
        bot,
        new pf.goals.GoalBlock(center.x, center.y, center.z),
        disableShelterScaffolding(
            configureSafeMovements(bot, Math.min(current.y, center.y) - 1),
        ),
        timeoutMs,
    );
}

function shelterReference(bot, target) {
    for (const face of [
        new Vec3(0, 1, 0), new Vec3(0, -1, 0),
        new Vec3(1, 0, 0), new Vec3(-1, 0, 0),
        new Vec3(0, 0, 1), new Vec3(0, 0, -1),
    ]) {
        const reference = bot.blockAt(target.minus(face));
        if (blockIsSolid(reference)) return {reference, face};
    }
    return null;
}

async function placeShelterDirt(bot, target, pollMs) {
    if (bot.interrupt_code) return false;
    const block = bot.blockAt(target);
    if (block?.name === SHELTER_BLOCK_NAME) return true;
    if (!blockIsSafeEmpty(block)) return false;
    if (!blockIsAir(block)) {
        if (bot.canDigBlock?.(block) === false) return false;
        await bot.dig(block, true);
        await delay(pollMs);
        if (bot.interrupt_code || !blockIsAir(bot.blockAt(target))) return false;
    }
    const placement = shelterReference(bot, target);
    const item = (bot.inventory?.items?.() || []).find((entry) => entry?.name === SHELTER_BLOCK_NAME);
    if (!placement || !item) return false;
    try {
        await bot.equip(item, 'hand');
        if (bot.interrupt_code) return false;
        await bot.lookAt?.(placement.reference.position.offset(0.5, 0.5, 0.5), true);
        if (bot.interrupt_code) return false;
        await bot.placeBlock(placement.reference, placement.face);
    } catch {
        // Mineflayer can report a placement error after the server accepted the block.
    }
    await delay(pollMs);
    return !bot.interrupt_code && bot.blockAt(target)?.name === SHELTER_BLOCK_NAME;
}

async function openShelterDoor(bot, doorway, pollMs) {
    for (const position of doorway) {
        if (bot.interrupt_code) return false;
        const block = bot.blockAt(position);
        if (blockIsAir(block)) continue;
        if (block?.name !== SHELTER_BLOCK_NAME || bot.canDigBlock?.(block) === false) return false;
        await bot.dig(block, true);
        await delay(pollMs);
        if (!blockIsAir(bot.blockAt(position))) return false;
    }
    return doorway.every((position) => blockIsAir(bot.blockAt(position)));
}

export async function runTemporaryShelterAction(
    agent,
    snapshot,
    {
        pollMs = SHELTER_POLL_MS,
        gatherTimeoutMs = SHELTER_GATHER_TIMEOUT_MS,
        clearStableMs = SHELTER_CLEAR_STABLE_MS,
        collectBlock = skills.collectBlock,
    } = {},
) {
    const bot = agent.bot;
    const buildThreatNearby = () => nearbyHostiles(bot, 24).length > 0;
    const origin = flooredVec(bot?.entity?.position);
    if (!origin) return {success: false, progress: false, verification: 'shelter_disconnected'};
    const startingHealth = Number(bot.health);
    let plan = temporaryShelterPlan(bot, origin);
    if (!plan) {
        let candidates = nearbyTemporaryShelterPlans(bot, origin);
        if (candidates.length === 0) {
            candidates = nearbyTemporaryShelterPlans(bot, origin, SHELTER_FALLBACK_SITE_RADIUS);
        }
        if (candidates.length === 0) {
            return {success: false, progress: false, verification: 'shelter_site_unbuildable'};
        }
        if (
            bot.entity?.isInWater ||
            buildThreatNearby() ||
            Number(bot.health) < SAFE_BOOTSTRAP_HEALTH ||
            Number(bot.food) < SAFE_BOOTSTRAP_HUNGER
        ) {
            return {success: false, progress: false, verification: 'shelter_context_unsafe'};
        }
        const goal = new pf.goals.GoalCompositeAny(candidates.map(({center}) => (
            new pf.goals.GoalBlock(center.x, center.y, center.z)
        )));
        const minimumY = Math.min(origin.y, ...candidates.map(({center}) => center.y)) - 1;
        let reached = await pathWithTimeout(
            bot,
            goal,
            disableShelterScaffolding(configureSafeMovements(bot, minimumY)),
            10000,
        );
        if (!reached && !bot.interrupt_code) {
            for (const {center} of candidates.slice(0, 4)) {
                reached = await pathWithTimeout(
                    bot,
                    new pf.goals.GoalBlock(center.x, center.y, center.z),
                    disableShelterScaffolding(configureSafeMovements(bot, minimumY)),
                    5000,
                );
                if (reached || bot.interrupt_code) break;
            }
        }
        if (!reached || bot.interrupt_code) {
            return {success: false, progress: false, verification: 'shelter_return_failed'};
        }
        if (Number(bot.health) < startingHealth || buildThreatNearby()) {
            return {success: false, progress: false, interrupted: true, verification: 'shelter_build_interrupted'};
        }
        const reachedPosition = flooredVec(bot.entity?.position);
        const selected = candidates.find(({center}) => (
            reachedPosition && (
                center.equals(reachedPosition) ||
                center.equals(reachedPosition.offset(0, 1, 0))
            )
        ));
        plan = selected ? temporaryShelterPlan(bot, selected.center) : null;
        if (!plan) return {success: false, progress: false, verification: 'shelter_site_unbuildable'};
    }
    const center = plan.center;

    if (plan.missing.length > 0) {
        if (
            bot.entity?.isInWater ||
            buildThreatNearby() ||
            Number(bot.health) < SAFE_BOOTSTRAP_HEALTH ||
            Number(bot.food) < SAFE_BOOTSTRAP_HUNGER
        ) {
            return {success: false, progress: false, verification: 'shelter_context_unsafe'};
        }
        const requiredMaterials = plan.missing.length;
        const startingMaterials = inventoryItemCount(bot, SHELTER_BLOCK_NAME);
        let gatherPhase = 'candidate_search';
        let gatherFailureVerification = 'shelter_material_unavailable';
        const gatherUnsafe = () => Boolean(
            bot.interrupt_code ||
            !bot.entity?.position ||
            bot.entity.isInWater ||
            Number(bot.health) < startingHealth ||
            buildThreatNearby()
        );
        if (startingMaterials < requiredMaterials) {
            const collectionPlugin = bot.collectBlock;
            const previousCollectMovements = collectionPlugin?.movements;
            let gathered;
            try {
                if (previousCollectMovements) {
                    collectionPlugin.movements = disableShelterScaffolding(
                        Object.create(previousCollectMovements),
                    );
                }
                gathered = await runFoodAcquisitionAction(
                    agent,
                    async () => {
                        while (inventoryItemCount(bot, SHELTER_BLOCK_NAME) < requiredMaterials) {
                            if (gatherUnsafe()) return false;
                            const before = inventoryItemCount(bot, SHELTER_BLOCK_NAME);
                            const candidate = shelterDirtCandidate(bot, plan.supports);
                            const directBlock = candidate.block;
                            gatherPhase = candidate.phase;
                            let collected;
                            if (directBlock) {
                                try {
                                    await bot.collectBlock.collect(directBlock, {blocksFirst: true});
                                    collected = true;
                                } catch {
                                    collected = false;
                                }
                            } else {
                                collected = await collectBlock(
                                    bot,
                                    SHELTER_BLOCK_NAME,
                                    requiredMaterials - before,
                                    plan.supports,
                                );
                            }
                            if (!collected || inventoryItemCount(bot, SHELTER_BLOCK_NAME) <= before) {
                                return false;
                            }
                            if (!await returnToShelterCenter(bot, center, 5000)) {
                                gatherFailureVerification = 'shelter_gather_return_failed';
                                return false;
                            }
                        }
                        return true;
                    },
                    {timeoutMs: gatherTimeoutMs, pollMs, hostileCheck: buildThreatNearby},
                );
            } finally {
                if (collectionPlugin) collectionPlugin.movements = previousCollectMovements;
            }
            if (!gathered.completed) {
                return {
                    success: false,
                    progress: inventoryItemCount(bot, SHELTER_BLOCK_NAME) > startingMaterials,
                    interrupted: true,
                    verification: gathered.reason === 'timeout'
                        ? `shelter_gather_timeout_${gatherPhase}`
                        : `shelter_gather_${gathered.reason}`,
                };
            }
            if (gatherUnsafe()) {
                return {
                    success: false,
                    progress: inventoryItemCount(bot, SHELTER_BLOCK_NAME) > startingMaterials,
                    interrupted: true,
                    verification: 'shelter_build_interrupted',
                };
            }
            if (!gathered.value) {
                return {
                    success: false,
                    progress: inventoryItemCount(bot, SHELTER_BLOCK_NAME) > startingMaterials,
                    verification: gatherFailureVerification,
                };
            }
        }
        if (gatherUnsafe()) {
            return {
                success: false,
                progress: inventoryItemCount(bot, SHELTER_BLOCK_NAME) > startingMaterials,
                interrupted: true,
                verification: 'shelter_build_interrupted',
            };
        }
        if (!await returnToShelterCenter(bot, center, 10000) || bot.interrupt_code) {
            return {success: false, progress: false, verification: 'shelter_return_failed'};
        }
        plan = temporaryShelterPlan(bot, center);
        if (!plan || inventoryItemCount(bot, SHELTER_BLOCK_NAME) < plan.missing.length) {
            return {success: false, progress: false, verification: 'shelter_material_unavailable'};
        }
        for (const target of plan.missing) {
            if (
                bot.interrupt_code ||
                Number(bot.health) < startingHealth ||
                buildThreatNearby()
            ) {
                return {success: false, progress: false, interrupted: true, verification: 'shelter_build_interrupted'};
            }
            if (!await placeShelterDirt(bot, target, pollMs)) {
                if (bot.interrupt_code) {
                    return {success: false, progress: false, interrupted: true, verification: 'shelter_build_interrupted'};
                }
                return {success: false, progress: false, verification: 'shelter_placement_unverified'};
            }
        }
    }

    if (!temporaryShelterVerified(bot, center)) {
        return {success: false, progress: false, verification: 'shelter_enclosure_unverified'};
    }
    let clearSince = null;
    while (bot.entity?.position && !bot.interrupt_code) {
        const breachVerification = temporaryShelterBreachVerification(bot, center);
        if (breachVerification) {
            return {success: false, progress: true, verification: breachVerification};
        }
        if (safeDawnTime(bot.time?.timeOfDay) && !nearbyKnownHostile(bot)) {
            if (clearSince === null) clearSince = Date.now();
            if (Date.now() - clearSince >= Math.max(0, Number(clearStableMs) || 0)) break;
        } else {
            clearSince = null;
        }
        await delay(pollMs);
    }
    if (!bot.entity?.position || bot.interrupt_code) {
        return {success: false, progress: true, interrupted: true, verification: 'shelter_hold_interrupted'};
    }
    if (!await openShelterDoor(bot, plan.doorway, pollMs)) {
        return {success: false, progress: true, verification: 'shelter_exit_unverified'};
    }
    return {
        success: true,
        progress: true,
        strategy: 'temporary_dirt_shelter',
        verification: SHELTER_SUCCESS_VERIFICATION,
    };
}

export function foodAcquisitionAllowed(snapshot) {
    return Boolean(
        snapshot?.connected &&
        Number(snapshot?.hostileCount || 0) === 0 &&
        (snapshot?.hostileDistance === null || snapshot?.hostileDistance === undefined) &&
        !snapshot?.underground &&
        !snapshot?.inWater
    );
}

export function listSurvivalDecisions(snapshot, { enableToolBootstrap = false } = {}) {
    if (!snapshot?.connected) return [];
    const decisions = [];
    if (snapshot.hostileDistance !== null && snapshot.hostileDistance <= HOSTILE_AVOID_DISTANCE) {
        decisions.push('handle_hostile');
    }
    if (
        snapshot.foodName &&
        (snapshot.hunger <= 14 || snapshot.health <= 12) &&
        Number(snapshot.hostileCount || 0) === 0 &&
        (snapshot.hostileDistance === null || snapshot.hostileDistance === undefined) &&
        !snapshot.inWater
    ) {
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
    if (snapshot.sheltered && shelterDecisionAllowed(snapshot)) {
        decisions.push('shelter_until_safe_dawn');
    }
    if (
        !snapshot.foodName &&
        (
            snapshot.hunger <= FOOD_ACQUIRE_HUNGER ||
            snapshot.health <= CRITICAL_HEALTH ||
            (
                snapshot.hasPickaxe &&
                snapshot.hasMeleeWeapon &&
                safeDawnTime(snapshot.timeOfDay)
            )
        ) &&
        foodAcquisitionAllowed(snapshot)
    ) {
        decisions.push('acquire_food');
    }
    if (!snapshot.sheltered && shelterDecisionAllowed(snapshot)) {
        decisions.push('shelter_until_safe_dawn');
    }
    if (
        enableToolBootstrap &&
        (!snapshot.hasPickaxe || !snapshot.hasMeleeWeapon) &&
        snapshot.inWater === false &&
        snapshot.underground === false &&
        Number(snapshot.hostileCount) === 0 &&
        snapshot.hostileDistance === null &&
        Number.isFinite(snapshot.timeOfDay) &&
        safeDawnTime(snapshot.timeOfDay) &&
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

export function decisionCanInterrupt(decision, snapshot = null) {
    if (decision === 'bootstrap_tools') return false;
    if (decision === 'acquire_food') {
        return (
            Number(snapshot?.hunger ?? FOOD_ACQUIRE_HUNGER) <= 10 ||
            Number(snapshot?.health ?? 20) <= CRITICAL_HEALTH
        );
    }
    return true;
}

export function failureCooldownMs(decision, failures) {
    if (decision === 'handle_hostile') {
        return 250;
    }
    if (decision === 'acquire_food') {
        return Math.min(60000, 5000 * (2 ** Math.min(Number(failures || 0), 4)));
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

export function recoveryHandoffDelayMs(decision, details, failures) {
    if (shouldHandoffEscapeFailure(decision, details, failures)) {
        return ESCAPE_FAIL_HANDOFF_DELAY_MS;
    }
    if (
        decision === 'acquire_food' &&
        String(details?.verification || '') === 'food_source_unavailable'
    ) {
        return FOOD_FAIL_HANDOFF_DELAY_MS;
    }
    return 0;
}

export function mergeSurvivalState(previous, next) {
    return { ...(previous || {}), ...(next || {}) };
}

export function classifyCombatTerrain(bot, position = bot?.entity?.position) {
    if (bot?.entity?.isInWater) return 'water';
    if (!position || typeof bot?.blockAt !== 'function') return 'unknown';
    const x = Math.floor(Number(position.x));
    const y = Math.floor(Number(position.y)) + 1;
    const z = Math.floor(Number(position.z));
    if (![x, y, z].every(Number.isFinite)) return 'unknown';
    const blocks = [[1, 0], [-1, 0], [0, 1], [0, -1]].map(([dx, dz]) => (
        bot.blockAt(new Vec3(x + dx, y, z + dz))
    ));
    if (blocks.some((block) => !block?.name)) return 'unknown';
    const solidCount = blocks.filter((block) => !NON_SOLID_BLOCKS.has(block.name)).length;
    if (solidCount >= 3) return 'enclosed';
    if (solidCount >= 1) return 'cover';
    return 'open';
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
        timeOfDay: Number.isFinite(bot.time?.timeOfDay) ? Number(bot.time.timeOfDay) : null,
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
        sheltered: temporaryShelterVerified(bot, position),
        combatTerrain: classifyCombatTerrain(bot, position),
        inventory,
    };
}

export function combatExperienceContext(snapshot) {
    const count = Math.max(1, Number(snapshot?.hostileCount || 1));
    const distance = Number(snapshot?.hostileDistance);
    const listedMobs = (snapshot?.hostiles || [])
        .map((hostile) => hostile?.name)
        .filter(Boolean)
        .slice(0, 8);
    if (!listedMobs.length && snapshot?.hostileName) listedMobs.push(snapshot.hostileName);
    return normalizeCombatContext({
        mobSet: listedMobs,
        countBucket: count >= 3 ? 'crowd' : (count === 2 ? 'pair' : 'single'),
        distanceBucket: Number.isFinite(distance) && distance <= 3
            ? 'contact'
            : (Number.isFinite(distance) && distance <= 8 ? 'near' : 'far'),
        terrain: snapshot?.inWater ? 'water' : (snapshot?.combatTerrain || 'unknown'),
        healthBucket: Number(snapshot?.health) <= 10
            ? 'critical'
            : (Number(snapshot?.health) < 16 ? 'wounded' : 'healthy'),
        gear: [
            Number(snapshot?.armorPoints) > 0 ? 'armor' : null,
            snapshot?.foodName ? 'food' : null,
            snapshot?.hasMeleeWeapon ? 'melee' : null,
            snapshot?.hasBow && Number(snapshot?.arrowCount) > 0 ? 'ranged' : null,
            snapshot?.hasShield ? 'shield' : null,
        ].filter(Boolean),
    });
}

function combatRuntimeVersion(bot) {
    return {
        minecraftVersion: String(bot?.version || process.env.MINECRAFT_VERSION || 'unknown'),
        pluginVersion: COMBAT_PLUGIN_VERSION,
    };
}

function combatContextKey(context) {
    return JSON.stringify([
        context.mobSet,
        context.countBucket,
        context.healthBucket,
    ]);
}

export function trackCombatDamage(bot) {
    let previous = Number(bot?.health);
    let damage = 0;
    const onHealth = () => {
        const current = Number(bot?.health);
        if (Number.isFinite(previous) && Number.isFinite(current) && current < previous) {
            damage += previous - current;
        }
        if (Number.isFinite(current)) previous = current;
    };
    bot?.on?.('health', onHealth);
    let stopped = false;
    return Object.freeze({
        stop() {
            if (!stopped) {
                stopped = true;
                bot?.off?.('health', onHealth);
            }
            return damage;
        },
    });
}

export function trackCombatDeath(bot) {
    let died = false;
    const onDeath = () => { died = true; };
    bot?.on?.('death', onDeath);
    let stopped = false;
    return Object.freeze({
        stop() {
            if (!stopped) {
                stopped = true;
                bot?.off?.('death', onDeath);
            }
            return died;
        },
    });
}

export function exitAfterCombatHistoryFlush(bot, code = 0, timeoutMs = 1000) {
    if (bot?.evelynCombatExitPromise) return bot.evelynCombatExitPromise;
    const boundedTimeout = Math.max(100, Math.min(2000, Number(timeoutMs) || 1000));
    const flush = Promise.resolve(bot?.evelynCombatHistoryLoading)
        .catch(() => {})
        .then(() => bot?.evelynCombatHistoryWriter?.flush?.())
        .catch((error) => {
            console.warn('[Evelyn Combat] shutdown flush failed:', error?.message || error);
        });
    let timeoutHandle;
    const timeout = new Promise((resolve) => {
        timeoutHandle = setTimeout(resolve, boundedTimeout);
    });
    const exitPromise = Promise.race([flush, timeout]).finally(() => {
        clearTimeout(timeoutHandle);
        process.exit(code);
    });
    if (bot) bot.evelynCombatExitPromise = exitPromise;
    return exitPromise;
}

export function startCombatExperience(bot, historyPath = COMBAT_HISTORY_PATH) {
    if (!bot.evelynCombatHistoryLoading) {
        bot.evelynCombatHistory = Array.isArray(bot.evelynCombatHistory)
            ? bot.evelynCombatHistory
            : [];
        bot.evelynCombatHistoryLoaded = false;
        bot.evelynCombatHistoryPending = [];
        bot.evelynCombatHistoryWriter = createCombatHistoryWriter(historyPath);
        bot.evelynCombatHistoryLoading = loadCombatHistory(historyPath)
            .then((history) => {
                const pendingEpisodes = bot.evelynCombatHistoryPending;
                let combined = history;
                for (const episode of pendingEpisodes) {
                    combined = appendCombatEpisode(combined, episode);
                }
                bot.evelynCombatHistory = combined;
                bot.evelynCombatHistoryPending = [];
                bot.evelynCombatHistoryLoaded = true;
                if (pendingEpisodes.length) {
                    bot.evelynCombatHistoryWriter.enqueue(combined).catch((error) => {
                        console.warn('[Evelyn Combat] history merge write failed:', error?.message || error);
                    });
                }
            })
            .catch((error) => {
                console.warn('[Evelyn Combat] history load failed:', error?.message || error);
                bot.evelynCombatHistoryPending = [];
                bot.evelynCombatHistoryLoaded = true;
                if (bot.evelynCombatHistory.length) {
                    bot.evelynCombatHistoryWriter.enqueue(bot.evelynCombatHistory).catch((writeError) => {
                        console.warn('[Evelyn Combat] history recovery write failed:', writeError?.message || writeError);
                    });
                }
            });
    }
}

export function recordCombatExperience(bot, {
    context,
    tactic,
    before,
    after,
    success,
    outcome = null,
    verified = success === true,
    damage = null,
    durationMs,
}) {
    const resolvedOutcome = outcome || (
        Number(after?.health) <= 0 ? 'death' : (success ? 'success' : 'failure')
    );
    const resolvedDamage = damage !== null && damage !== undefined && Number.isFinite(Number(damage))
        ? Number(damage)
        : Math.max(0, Number(before?.health || 0) - Number(after?.health || 0));
    bot.evelynCombatHistory = appendCombatEpisode(bot.evelynCombatHistory, {
        ...context,
        tactic,
        outcome: resolvedOutcome,
        verified: verified === true,
        damage: Math.max(0, Math.min(1000, resolvedDamage)),
        durationMs: Math.max(0, Math.min(600000, Number(durationMs || 0))),
        ...combatRuntimeVersion(bot),
    });
    if (bot.evelynCombatHistoryLoading && bot.evelynCombatHistoryLoaded !== true) {
        bot.evelynCombatHistoryPending.push(bot.evelynCombatHistory.at(-1));
        return;
    }
    bot.evelynCombatHistoryWriter?.enqueue(bot.evelynCombatHistory).catch((error) => {
        console.warn('[Evelyn Combat] history write failed:', error?.message || error);
    });
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

export async function pathWithTimeout(bot, goal, movements, timeoutMs = ACTION_TIMEOUT_MS) {
    const previousThinkTimeout = bot.pathfinder.thinkTimeout;
    let timeout;
    let rejectStuck;
    let stuckResets = 0;
    const reachedGoal = () => {
        const position = flooredVec(bot.entity?.position);
        return Boolean(position && (
            goal?.isEnd?.(position) ||
            goal?.isEnd?.(position.offset(0, 1, 0))
        ));
    };
    const stuckFailure = new Promise((_, reject) => { rejectStuck = reject; });
    const onPathReset = (reason) => {
        if (reason !== 'stuck' || reachedGoal()) return;
        const activeGoal = bot.pathfinder.goal;
        if (activeGoal !== undefined && activeGoal !== goal) return;
        if (++stuckResets < PATH_STUCK_RESET_LIMIT) return;
        rejectStuck(new Error('survival path repeatedly stuck'));
    };
    bot.on('path_reset', onPathReset);
    try {
        bot.pathfinder.thinkTimeout = Math.max(Number(previousThinkTimeout || 0), 15000);
        bot.pathfinder.setMovements(movements);
        await Promise.race([
            bot.pathfinder.goto(goal),
            stuckFailure,
            new Promise((_, reject) => {
                timeout = setTimeout(() => reject(new Error('survival path timeout')), timeoutMs);
            }),
        ]);
        if (!reachedGoal()) throw new Error('survival path ended before reaching goal');
        return true;
    } catch (error) {
        const activeGoal = bot.pathfinder.goal;
        if (activeGoal === undefined || activeGoal === goal) {
            bot.pathfinder.stop();
            bot.pathfinder.setGoal?.(null);
        }
        console.warn('[Evelyn Survival] path failed:', error?.message || error);
        return false;
    } finally {
        clearTimeout(timeout);
        bot.removeListener('path_reset', onPathReset);
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
            operation.then((value) => ({ timedOut: false, value })),
            new Promise((resolve) => {
                timeout = setTimeout(() => resolve({ timedOut: true, value: null }), timeoutMs);
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

export function combatEpisodeOutcome({
    tactic,
    timedOut = false,
    fightReason = '',
    executedPreset = null,
    health = 20,
    success = false,
    died = false,
}) {
    const nonEvidence = timedOut || (
        tactic === 'fight' && (
            !executedPreset ||
            [
                'combat_context_changed', 'interrupted', 'combat_timeout',
                'custom_pvp_error', 'custom_pvp_unavailable', 'fallback_defend_self',
                'shield_unavailable', 'melee_weapon_missing',
            ].includes(String(fightReason || ''))
        )
    );
    if (died || (Number(health) <= 0 && !nonEvidence)) return 'death';
    if (nonEvidence) return 'interrupted';
    return success ? 'success' : 'failure';
}

export function hostileFleeEscapeOptions(snapshot, failureCount = 0) {
    const failures = Math.max(0, Number(failureCount) || 0);
    const distance = Number(snapshot?.hostileDistance);
    const directSprint = (
        Number.isFinite(distance) &&
        distance <= HOSTILE_AVOID_DISTANCE
    );
    return {
        failureCount: directSprint ? Math.max(1, failures) : failures,
        forceSprint: directSprint,
        stopOnStall: !directSprint,
    };
}

function hostileFleeTargetKey(snapshot) {
    const id = Number(snapshot?.hostileId);
    const name = String(snapshot?.hostileName || '').toLowerCase();
    return Number.isInteger(id) && name ? `${id}:${name}` : null;
}

export function advanceHostileFleeFailureStreak(previous = {}, details = null, success = false) {
    const targetKey = details?.tactic === 'flee' && success !== true
        ? hostileFleeTargetKey(details?.before)
        : null;
    if (!targetKey) return {targetKey: null, count: 0};
    const sameTargetHandoff = (
        details?.verification === EMERGENCY_MELEE_HANDOFF &&
        hostileFleeTargetKey(details?.after) === targetKey
    );
    return {
        targetKey,
        count: sameTargetHandoff
            ? 2
            : (
                previous?.targetKey === targetKey
                    ? Math.min(2, Math.max(0, Number(previous?.count) || 0) + 1)
                    : 1
            ),
    };
}

export function singleZombieMeleeFallbackAllowed(snapshot, fleeFailureCount = 0) {
    const distance = Number(snapshot?.hostileDistance);
    const health = Number(snapshot?.health);
    const failures = Math.max(0, Number(fleeFailureCount) || 0);
    return (
        snapshot?.hostileCount === 1 &&
        String(snapshot?.hostileName || '').toLowerCase() === 'zombie' &&
        snapshot?.hasMeleeWeapon === true &&
        snapshot?.inWater !== true &&
        Number.isFinite(distance) &&
        distance <= HOSTILE_FIGHT_DISTANCE &&
        Number.isFinite(health) &&
        health > 0 &&
        (failures >= 2 || health <= CRITICAL_HEALTH || distance <= 3)
    );
}

export function hostileEscapeAlreadyStable(tactic, escapeDetails) {
    return tactic === 'flee' && escapeDetails?.verification === 'stable_safe_radius';
}

async function handleHostile(bot, snapshot, {failureCount = 0, fleeFailureCount = 0} = {}) {
    startCombatExperience(bot);
    let tactic = selectHostileTactic(snapshot);
    const assessment = assessCombat(snapshot);
    if (!tactic) {
        return { success: true, tactic: 'none', verification: 'threat_disappeared' };
    }

    const runtimeVersion = combatRuntimeVersion(bot);
    const combatPlan = (currentSnapshot) => {
        const context = combatExperienceContext(currentSnapshot);
        return {
            context,
            contextKey: combatContextKey(context),
            preset: selectCombatPreset(context, bot.evelynCombatHistory, runtimeVersion),
        };
    };
    const initialPlan = combatPlan(snapshot);
    const experienceContext = initialPlan.context;
    const preset = initialPlan.preset;
    const emergencyMelee = tactic === 'flee' && singleZombieMeleeFallbackAllowed(snapshot, fleeFailureCount);
    if (emergencyMelee) tactic = 'fight';
    else if (tactic === 'fight' && preset === 'disengage') tactic = 'flee';
    const startedAt = Date.now();
    const damageTracker = trackCombatDamage(bot);
    const deathTracker = trackCombatDeath(bot);

    let escapeDetails = null;
    let external;
    let verificationSnapshot;
    let verified = false;
    let stable = false;
    let success = false;
    let after;
    let damage = 0;
    let died = false;
    try {
        external = tactic === 'fight'
            ? await runExternalHostileAction(
                bot,
                async () => {
                    const result = await fightWithCustomPvp(bot, {
                        snapshotProvider: () => ({
                            ...buildSurvivalSnapshot(bot),
                            ...(emergencyMelee ? {singleZombieEmergencyMelee: true} : {}),
                        }),
                        hostileProvider: () => nearbyHostiles(bot, HOSTILE_AVOID_DISTANCE),
                        combatPlanProvider: (currentSnapshot) => {
                            const plan = combatPlan(currentSnapshot);
                            return {
                                preset: emergencyMelee ? 'melee' : plan.preset,
                                contextKey: plan.contextKey,
                            };
                        },
                        timeoutMs: HOSTILE_ACTION_TIMEOUT_MS - 1000,
                    });
                    if (!emergencyMelee && result.reason === 'custom_pvp_unavailable') {
                        return {
                            success: await skills.defendSelf(bot, HOSTILE_FIGHT_DISTANCE),
                            reason: 'fallback_defend_self',
                            executedPreset: null,
                        };
                    }
                    return result;
                },
            )
            : await (async () => {
                escapeDetails = await escapeFromHostiles(bot, {
                    ...hostileFleeEscapeOptions(snapshot, failureCount),
                    safeDistance: HOSTILE_AVOID_DISTANCE,
                    range: 24,
                    timeoutMs: HOSTILE_ESCAPE_TIMEOUT_MS,
                    hostileProvider: () => nearbyHostiles(bot, 24),
                    surfaceY: snapshot.surfaceY,
                    stableMs: HOSTILE_STABLE_MS,
                    abortReasonProvider: () => (
                        singleZombieMeleeFallbackAllowed(buildSurvivalSnapshot(bot), 0)
                            ? EMERGENCY_MELEE_HANDOFF
                            : null
                    ),
                });
                return {timedOut: false, value: escapeDetails.success};
            })();
        const actionSucceeded = typeof external.value === 'object'
            ? external.value?.success === true
            : external.value === true;
        verificationSnapshot = buildSurvivalSnapshot(bot);
        verified = !external.timedOut && verifyHostileOutcome(
            tactic,
            snapshot,
            verificationSnapshot,
            actionSucceeded,
        );
        stable = verified
            ? (
                hostileEscapeAlreadyStable(tactic, escapeDetails) ||
                await remainsSafeFromHostiles(bot)
            )
            : false;
        success = verified && stable;
        after = verified ? buildSurvivalSnapshot(bot) : verificationSnapshot;
    } finally {
        damage = damageTracker.stop();
        died = deathTracker.stop();
    }
    if (died) success = false;
    const fightReason = tactic === 'fight' ? String(external.value?.reason || '') : '';
    const executedPreset = tactic === 'fight' ? external.value?.executedPreset || null : 'disengage';
    const outcome = combatEpisodeOutcome({
        tactic,
        timedOut: external.timedOut,
        fightReason,
        executedPreset,
        health: after?.health,
        success,
        died,
    });
    recordCombatExperience(bot, {
        context: experienceContext,
        tactic: tactic === 'flee' ? 'disengage' : (executedPreset || preset),
        before: snapshot,
        after,
        success,
        outcome,
        verified: outcome === 'success',
        damage,
        durationMs: Date.now() - startedAt,
    });
    return {
        success,
        tactic,
        combatPreset: executedPreset || preset,
        assessment,
        strategy: escapeDetails?.strategy || (tactic === 'fight' ? 'custom_pvp' : null),
        verification: external.timedOut
            ? 'timeout'
            : (
                !verified
                    ? (fightReason || escapeDetails?.verification || 'unsafe_after_action')
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

export function startHostilePreemptionReflex(
    agent,
    snapshot,
    decisionSelectedAt = Date.now(),
    detectedAt = decisionSelectedAt,
) {
    const bot = agent?.bot;
    if (!bot?.entity?.position || typeof agent?.requestInterrupt !== 'function') return null;
    const actionStartedAt = Date.now();
    const threats = nearbyHostiles(bot, 24);
    const nearest = threats[0] || null;
    const context = combatExperienceContext({
        ...snapshot,
        health: bot.health,
        hunger: bot.food,
        hostileDistance: nearest?.distance ?? null,
        hostileName: nearest?.entity?.name || null,
        hostileCount: threats.length,
        hostiles: threats.map(({entity, distance}) => ({name: entity.name, distance})),
        combatTerrain: classifyCombatTerrain(bot),
    });
    startCombatExperience(bot);
    const damageTracker = trackCombatDamage(bot);
    const deathTracker = trackCombatDeath(bot);
    agent.requestInterrupt();
    bot.evelynSurvivalState = mergeSurvivalState(bot.evelynSurvivalState, {
        phase: 'handle_hostile',
        last_success: null,
        last_error: null,
        action_started_at_ms: actionStartedAt,
        decision_to_action_ms: Math.max(0, actionStartedAt - Number(decisionSelectedAt || actionStartedAt)),
        reflex_reason: 'hostile',
        reflex_to_action_ms: Math.max(0, actionStartedAt - Number(detectedAt || actionStartedAt)),
        last_reflex_at: actionStartedAt / 1000,
    });
    return (async () => {
        let result;
        let damage = 0;
        let died = false;
        try {
            result = await escapeFromHostiles(bot, {
                failureCount: 1,
                safeDistance: HOSTILE_AVOID_DISTANCE,
                range: 24,
                timeoutMs: HOSTILE_REFLEX_TIMEOUT_MS,
                burstMs: 200,
                hostileProvider: () => nearbyHostiles(bot, 24),
                surfaceY: snapshot?.surfaceY,
                interruptOptOutMs: HOSTILE_REFLEX_TIMEOUT_MS,
                forceSprint: true,
                stopOnStall: false,
            });
        } catch {
            result = {
                success: false,
                strategy: 'hostile_preemption_reflex',
                verification: 'reflex_error',
            };
        } finally {
            damage = damageTracker.stop();
            died = deathTracker.stop();
        }
        recordCombatExperience(bot, {
            context,
            tactic: 'disengage',
            before: {health: Number(bot.health) + damage},
            after: {health: bot.health},
            success: false,
            outcome: died ? 'death' : 'interrupted',
            verified: false,
            damage,
            durationMs: Date.now() - actionStartedAt,
        });
        return result;
    })();
}

export function startProjectileDefenseReflex(agent, threat = null, {
    decisionSelectedAt = Date.now(),
    detectedAt = decisionSelectedAt,
    shieldHoldMs = null,
    escapeTimeoutMs = HOSTILE_REFLEX_TIMEOUT_MS,
} = {}) {
    const bot = agent?.bot;
    const incoming = threat || imminentProjectileThreat(bot);
    const ticks = Number(incoming?.shotInfo?.totalTicks);
    if (
        !bot?.entity?.position ||
        typeof agent?.requestInterrupt !== 'function' ||
        !incoming?.entity?.position ||
        !Number.isFinite(ticks) ||
        ticks < 0 ||
        ticks > PROJECTILE_IMMINENT_TICKS
    ) return null;

    const actionStartedAt = Date.now();
    if (bot.evelynMovementOwner === 'evelyn_hostile_escape') {
        bot.evelynSurvivalState = mergeSurvivalState(bot.evelynSurvivalState, {
            phase: 'handle_hostile',
            hostile_strategy: 'projectile_existing_escape',
            wake_reason: SURVIVAL_WAKE_REASONS.PROJECTILE,
        });
        return delay(PROJECTILE_WAKE_INTERVAL_MS).then(() => ({
            success: false,
            strategy: 'projectile_existing_escape',
            verification: 'escape_in_progress',
        }));
    }
    const wasIdle = typeof agent.isIdle === 'function' && agent.isIdle();
    if (!wasIdle) agent.requestInterrupt();
    bot.pathfinder?.stop?.();
    stopCombatControllers(bot);
    bot.evelynSurvivalState = mergeSurvivalState(bot.evelynSurvivalState, {
        phase: 'handle_hostile',
        last_success: null,
        last_error: null,
        hostile_strategy: 'projectile_defense',
        wake_reason: SURVIVAL_WAKE_REASONS.PROJECTILE,
        action_started_at_ms: actionStartedAt,
        decision_to_action_ms: Math.max(0, actionStartedAt - Number(decisionSelectedAt || actionStartedAt)),
        reflex_reason: 'projectile',
        reflex_to_action_ms: Math.max(0, actionStartedAt - Number(detectedAt || actionStartedAt)),
        last_reflex_at: actionStartedAt / 1000,
    });

    return (async () => {
        const shieldReady = (
            offhandShieldEquipped(bot) &&
            typeof bot.activateItem === 'function' &&
            typeof bot.lookAt === 'function'
        );
        if (shieldReady) {
            let activated = false;
            try {
                const look = Promise.resolve(bot.lookAt(incoming.entity.position, true)).catch(() => false);
                if (!bot.util?.entity?.isOffHandActive?.()) {
                    bot.activateItem(true);
                    activated = true;
                }
                await Promise.race([look, delay(PROJECTILE_WAKE_INTERVAL_MS)]);
                const predictedHoldMs = Math.min(
                    PROJECTILE_SHIELD_MAX_HOLD_MS,
                    Math.max(PROJECTILE_SHIELD_GRACE_MS, ticks * MINECRAFT_TICK_MS + PROJECTILE_SHIELD_GRACE_MS),
                );
                const holdMs = shieldHoldMs === null
                    ? predictedHoldMs
                    : Math.max(0, Math.min(PROJECTILE_SHIELD_MAX_HOLD_MS, Number(shieldHoldMs) || 0));
                await delay(holdMs);
                return {
                    success: true,
                    strategy: 'projectile_shield',
                    verification: 'shield_raised',
                    incomingTicks: ticks,
                };
            } catch {
                // Fall through to the existing collision-checked escape controller.
            } finally {
                if (activated) {
                    try { bot.deactivateItem?.(); } catch {
                        // Shield release is best-effort after the bounded P0 action.
                    }
                }
            }
        }

        const startedAt = Date.now();
        const hostileProvider = () => {
            const hostiles = nearbyHostiles(bot, 24);
            const current = imminentProjectileThreat(bot) || (
                Date.now() - startedAt < CHEAP_WAKE_INTERVAL_MS ? incoming : null
            );
            const origin = bot.entity?.position;
            if (!current?.entity?.position || !origin) return hostiles;
            return [{
                entity: current.entity,
                distance: origin.distanceTo(current.entity.position),
            }, ...hostiles];
        };
        const escape = await escapeFromHostiles(bot, {
            failureCount: 1,
            safeDistance: HOSTILE_AVOID_DISTANCE,
            range: 24,
            timeoutMs: Math.min(HOSTILE_REFLEX_TIMEOUT_MS, Math.max(1, Number(escapeTimeoutMs) || 1)),
            burstMs: 200,
            hostileProvider,
            surfaceY: currentSurfaceY(bot),
            interruptOptOutMs: wasIdle ? 0 : HOSTILE_REFLEX_TIMEOUT_MS,
            forceSprint: true,
            stopOnStall: false,
            directionHint: projectileLateralDirection(bot, incoming),
        });
        return {
            ...escape,
            strategy: `projectile_${escape.strategy || 'escape'}`,
        };
    })();
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
    if (food) return Number(bot.food) >= 20 ? true : skills.consume(bot, food.name);
    const inventory = inventoryCounts(bot);
    if ((inventory.wheat || 0) >= 3) {
        await skills.craftRecipe(bot, 'bread', 1);
        food = selectBestFood(bot);
        if (food) return Number(bot.food) >= 20 ? true : skills.consume(bot, food.name);
    }
    return false;
}

function nearbyKnownHostile(bot, range = HOSTILE_AVOID_DISTANCE) {
    const origin = bot?.entity?.position;
    if (!origin) return false;
    return Object.values(bot.entities || {}).some((entity) => {
        if (!entity?.position || entity === bot.entity || !entityIsHostile(entity)) return false;
        const distance = origin.distanceTo(entity.position);
        return Number.isFinite(distance) && distance <= range;
    });
}

function nearbyActionableHostile(bot, range = 24) {
    return nearbyHostiles(bot, range).length > 0;
}

function stopFoodAcquisitionActivity(bot) {
    stopCombatControllers(bot);
    bot?.pathfinder?.stop?.();
    bot?.collectBlock?.cancelTask?.();
}

export async function runFoodAcquisitionAction(
    agent,
    action,
    {
        timeoutMs = FOOD_HUNT_TIMEOUT_MS,
        pollMs = FOOD_HUNT_POLL_MS,
        hostileCheck = nearbyActionableHostile,
    } = {},
) {
    const bot = agent.bot;
    const startedAt = Date.now();
    const startingHealth = Number(bot.health);
    let abortReason = null;
    let resolveAbort;
    const aborted = new Promise((resolve) => {
        resolveAbort = resolve;
    });
    const abort = (reason) => {
        if (abortReason) return;
        abortReason = reason;
        if (typeof agent.requestInterrupt === 'function') agent.requestInterrupt();
        else {
            bot.interrupt_code = true;
            stopFoodAcquisitionActivity(bot);
        }
        resolveAbort({kind: 'aborted'});
    };
    const check = () => {
        if (!bot.entity?.position) abort('disconnected');
        else if (bot.entity.isInWater) abort('entered_water');
        else if (hostileCheck(bot)) abort('hostile_detected');
        else if (
            Number.isFinite(startingHealth) &&
            Number.isFinite(bot.health) &&
            Number(bot.health) < startingHealth
        ) abort('damage_taken');
        else if (Date.now() - startedAt >= Math.max(1, Number(timeoutMs) || FOOD_HUNT_TIMEOUT_MS)) {
            abort('timeout');
        }
    };
    check();
    if (abortReason) return {completed: false, reason: abortReason, value: false};
    const monitor = setInterval(check, Math.max(1, Number(pollMs) || FOOD_HUNT_POLL_MS));
    const operation = Promise.resolve()
        .then(action)
        .then(
            (value) => ({kind: 'completed', value}),
            (error) => ({kind: 'failed', error}),
        );
    try {
        const outcome = await Promise.race([operation, aborted]);
        if (outcome.kind === 'aborted') {
            return {completed: false, reason: abortReason, value: false};
        }
        if (outcome.kind === 'failed') throw outcome.error;
        check();
        return {completed: !abortReason, reason: abortReason, value: outcome.value};
    } catch (error) {
        if (abortReason) return {completed: false, reason: abortReason, value: false};
        throw error;
    } finally {
        clearInterval(monitor);
        if (abortReason) stopFoodAcquisitionActivity(bot);
    }
}

export function isSafeFoodPrey(entity) {
    return Boolean(
        entity?.metadata &&
        SAFE_FOOD_PREY.has(String(entity?.name || '').toLowerCase()) &&
        isHuntable(entity)
    );
}

function foodResult(before, bot, verification) {
    const evidence = compareFoodEvidence(before, foodEvidence(bot));
    return {...evidence, verification};
}

async function monitoredCraftAndEat(agent, before, verification) {
    const action = await runFoodAcquisitionAction(
        agent,
        () => craftAndEatAvailableFood(agent.bot),
        {timeoutMs: 10000},
    );
    const result = foodResult(before, agent.bot, verification);
    if (action.completed) return result;
    return {
        success: false,
        progress: result.progress,
        interrupted: true,
        verification: `${verification}_${action.reason}`,
    };
}

export async function acquireFood(agent) {
    const bot = agent.bot;
    const before = foodEvidence(bot);
    let result = await monitoredCraftAndEat(agent, before, 'inventory_food_verified');
    if (result.progress || result.interrupted) return result;

    let current = buildSurvivalSnapshot(bot);
    if (!foodAcquisitionAllowed(current)) {
        return {success: false, progress: false, verification: 'food_acquisition_unsafe'};
    }

    const crop = world.getNearestBlocksWhere(bot, cropIsMature, 24, 4)[0] || null;
    if (crop) {
        const cropAction = await runFoodAcquisitionAction(agent, async () => {
            if (crop.name === 'sweet_berry_bush') {
                await skills.useToolOnBlock(bot, 'hand', crop);
                return skills.pickupNearbyItems(bot);
            }
            return bot.collectBlock.collect(crop);
        });
        if (!cropAction.completed) {
            return {
                success: false,
                progress: false,
                verification: `food_crop_${cropAction.reason}`,
            };
        }
        result = await monitoredCraftAndEat(agent, before, 'food_crop_verified');
        if (result.progress || result.interrupted) return result;
    }

    current = buildSurvivalSnapshot(bot);
    const preySafe = (
        foodAcquisitionAllowed(current) &&
        current.health >= CRITICAL_HEALTH &&
        current.combatTerrain === 'open'
    );
    if (preySafe) {
        const prey = world.getNearestEntityWhere(bot, isSafeFoodPrey, 32);
        if (prey) {
            const distance = bot.entity.position.distanceTo(prey.position);
            if (distance > 12) {
                const movements = configureSafeMovements(
                    bot,
                    Math.floor(Number(bot.entity.position.y)) - 1,
                );
                movements.canDig = false;
                movements.allow1by1towers = false;
                const approach = await runFoodAcquisitionAction(
                    agent,
                    () => pathWithTimeout(
                        bot,
                        new pf.goals.GoalFollow(prey, 4),
                        movements,
                        12000,
                    ),
                    {timeoutMs: 12500},
                );
                if (!approach.completed || !approach.value) {
                    return {
                        success: false,
                        progress: false,
                        verification: `food_approach_${approach.reason || 'unreached'}`,
                    };
                }
            }
            current = buildSurvivalSnapshot(bot);
            const huntStillSafe = (
                foodAcquisitionAllowed(current) &&
                current.health >= CRITICAL_HEALTH &&
                current.combatTerrain === 'open' &&
                isSafeFoodPrey(prey) &&
                bot.entity.position.distanceTo(prey.position) <= 12
            );
            if (!huntStillSafe) {
                return {success: false, progress: false, verification: 'food_hunt_context_changed'};
            }
            const hunt = await runFoodAcquisitionAction(agent, () => skills.attackEntity(bot, prey, true));
            stopCombatControllers(bot);
            if (!hunt.completed) {
                return {
                    success: false,
                    progress: false,
                    verification: `food_hunt_${hunt.reason}`,
                };
            }
            result = await monitoredCraftAndEat(agent, before, 'food_hunt_verified');
            if (result.progress || result.interrupted) return result;
        }
    }
    return {success: false, progress: false, verification: 'food_source_unavailable'};
}

function nearbyLogCandidates(bot, excludedClusters = []) {
    const isOutsideFailedClusters = (block) => !block?.position || excludedClusters.every(
        (position) => Math.hypot(
            position.x - block.position.x,
            position.z - block.position.z,
        ) >= 4,
    );
    const blocks = world.getNearestBlocksWhere(
        bot,
        (block) => {
            if (!block?.name?.endsWith('_log')) return false;
            return isOutsideFailedClusters(block);
        },
        48,
        64,
    ).filter((block) => block?.position && isOutsideFailedClusters(block));
    const candidates = [];
    for (const block of blocks) {
        if (candidates.every((candidate) => Math.hypot(
            candidate.position.x - block.position.x,
            candidate.position.z - block.position.z,
        ) >= 4)) candidates.push(block);
        if (candidates.length === 4) break;
    }
    return candidates;
}

function freshLogCandidate(bot, candidate) {
    const fresh = bot.blockAt?.(candidate.position);
    return fresh?.name === candidate.name && fresh?.type === candidate.type ? fresh : null;
}

function directlyReachableLogCandidate(bot, candidates) {
    for (const candidate of candidates) {
        const fresh = freshLogCandidate(bot, candidate);
        if (fresh && bot.canDigBlock?.(fresh) === true && bot.canSeeBlock?.(fresh) === true) {
            return fresh;
        }
    }
    return null;
}

async function easiestLogCandidate(bot, candidates) {
    if (candidates.length === 0) return null;
    if (!bot.pathfinder || !bot.collectBlock?.movements || typeof bot.on !== 'function') {
        return candidates[0];
    }
    if (bot.interrupt_code || bot.evelynMovementOwner) return null;
    const directCandidate = directlyReachableLogCandidate(bot, candidates);
    if (directCandidate) return directCandidate;
    const movements = configureSafeMovements(
        bot,
        Number.NEGATIVE_INFINITY,
    );
    movements.canDig = false;
    movements.allow1by1towers = false;
    movements.scafoldingBlocks = [];
    const goals = candidates.map((block) => new pf.goals.GoalLookAtBlock(block.position, bot.world));
    const goal = goals.length === 1 ? goals[0] : new pf.goals.GoalCompositeAny(goals);
    if (await pathWithTimeout(bot, goal, movements, 10000)) {
        const position = flooredVec(bot.entity?.position);
        const index = goals.findIndex((candidate) => position && (
            candidate.isEnd(position) || candidate.isEnd(position.offset(0, 1, 0))
        ));
        if (index >= 0 && !bot.interrupt_code && !bot.evelynMovementOwner) {
            const fresh = freshLogCandidate(bot, candidates[index]);
            if (fresh) return fresh;
        }
    }
    if (bot.interrupt_code || bot.evelynMovementOwner) return null;
    const reachedAfterComposite = directlyReachableLogCandidate(bot, candidates);
    if (reachedAfterComposite) return reachedAfterComposite;
    for (let index = 0; index < goals.length; index++) {
        if (bot.interrupt_code || bot.evelynMovementOwner) return null;
        const reached = await pathWithTimeout(bot, goals[index], movements, 5000);
        if (bot.interrupt_code || bot.evelynMovementOwner) return null;
        if (reached) {
            const fresh = freshLogCandidate(bot, candidates[index]);
            if (fresh) return fresh;
        }
        const reachedAfterExact = directlyReachableLogCandidate(bot, candidates);
        if (reachedAfterExact) return reachedAfterExact;
    }
    return null;
}

function traceBootstrap(bot, bootstrapPhase, candidateCount, logsBefore, logsAfter) {
    const update = {bootstrap_phase: bootstrapPhase};
    if (Number.isSafeInteger(candidateCount) && candidateCount >= 0) {
        update.bootstrap_candidate_count = Math.min(candidateCount, 4);
    }
    if (Number.isSafeInteger(logsBefore) && logsBefore >= 0) {
        update.bootstrap_logs_before = Math.min(logsBefore, 64);
    }
    if (Number.isSafeInteger(logsAfter) && logsAfter >= 0) {
        update.bootstrap_logs_after = Math.min(logsAfter, 64);
    }
    bot.evelynSurvivalState = mergeSurvivalState(bot.evelynSurvivalState, update);
}

function nearbyCraftingTable(bot) {
    try {
        return world.getNearestBlock(bot, 'crafting_table', 16);
    } catch {
        return null;
    }
}

export async function bootstrapTools(bot, craftRecipe = skills.craftRecipe) {
    const failedLogClusters = Array.isArray(bot.evelynBootstrapFailedLogClusters)
        ? bot.evelynBootstrapFailedLogClusters
        : [];
    bot.evelynBootstrapFailedLogClusters = failedLogClusters;
    const clearFailedLogClusters = () => { failedLogClusters.length = 0; };
    const rememberFailedLogClusters = (positions) => {
        failedLogClusters.push(...positions.filter(Boolean));
    };
    let inventory = inventoryCounts(bot);
    const needsPickaxe = !hasPickaxe(inventory);
    const needsMeleeWeapon = !hasMeleeWeapon(inventory);
    const requiredToolPlanks = (needsPickaxe ? 3 : 0) + (needsMeleeWeapon ? 2 : 0);
    const requiredSticks = (needsPickaxe ? 2 : 0) + (needsMeleeWeapon ? 1 : 0);
    const totalPlanks = () => Object.entries(inventory)
        .filter(([name]) => name.endsWith('_planks'))
        .reduce((sum, [, count]) => sum + Number(count || 0), 0);
    const totalLogs = () => Object.entries(inventory)
        .filter(([name]) => name.endsWith('_log'))
        .reduce((sum, [, count]) => sum + Number(count || 0), 0);
    traceBootstrap(bot, 'candidate_search', 0, totalLogs(), totalLogs());
    if (bot.interrupt_code) {
        traceBootstrap(bot, 'interrupted');
        return false;
    }
    if (hasPickaxe(inventory) && hasMeleeWeapon(inventory)) {
        clearFailedLogClusters();
        traceBootstrap(bot, 'complete');
        return true;
    }
    const missingStickRecipePlanks = Number(inventory.stick || 0) >= requiredSticks ? 0 : 2;
    const missingTablePlanks = Number(inventory.crafting_table || 0) >= 1 || nearbyCraftingTable(bot) ? 0 : 4;
    const preparedWood = totalPlanks() >= requiredToolPlanks + missingStickRecipePlanks + missingTablePlanks;
    if (preparedWood) clearFailedLogClusters();
    if (!preparedWood) {
        let failedBatches = 0;
        while (totalLogs() < 3) {
            const candidates = nearbyLogCandidates(bot, failedLogClusters);
            traceBootstrap(bot, 'candidate_search', candidates.length, totalLogs(), totalLogs());
            if (candidates.length === 0) {
                clearFailedLogClusters();
                traceBootstrap(bot, 'no_candidates', 0);
                return false;
            }
            const logBlock = await easiestLogCandidate(bot, candidates);
            if (!logBlock) {
                if (bot.interrupt_code || bot.evelynMovementOwner) {
                    traceBootstrap(bot, 'interrupted', candidates.length);
                    return false;
                }
                traceBootstrap(bot, 'candidate_unreached', candidates.length);
                rememberFailedLogClusters(candidates.map((candidate) => candidate.position));
                if (++failedBatches >= 2) return false;
                continue;
            }
            traceBootstrap(bot, 'candidate_reached', candidates.length, totalLogs(), totalLogs());
            const before = totalLogs();
            let collectionTarget = logBlock;
            let collectionOptions;
            if (typeof bot.collectBlock?.findFromVein === 'function') {
                const connectedLogs = bot.collectBlock
                    .findFromVein(logBlock, 3 - before, 4, 1)
                    .map((candidate) => freshLogCandidate(bot, candidate))
                    .filter(Boolean)
                    .slice(0, 3 - before);
                if (connectedLogs.length > 0) {
                    collectionTarget = connectedLogs;
                    collectionOptions = {blocksFirst: true};
                }
            }
            traceBootstrap(bot, 'collect_started', candidates.length, before, before);
            let collectionError = null;
            try {
                await bot.collectBlock.collect(collectionTarget, collectionOptions);
            } catch (err) {
                collectionError = err;
            }
            const inventoryMayStillSettle = (
                !collectionError || RECOVERABLE_NAVIGATION_ERRORS.has(collectionError.name)
            );
            for (
                let poll = 0;
                inventoryMayStillSettle && poll < BOOTSTRAP_INVENTORY_SETTLE_POLLS;
                poll++
            ) {
                inventory = inventoryCounts(bot);
                if (totalLogs() > before || bot.interrupt_code || bot.evelynMovementOwner) break;
                await delay(BOOTSTRAP_INVENTORY_SETTLE_POLL_MS);
            }
            inventory = inventoryCounts(bot);
            const failedGoal = collectionError ? bot.pathfinder?.goal : null;
            if (failedGoal && bot.pathfinder?.goal === failedGoal && !bot.evelynMovementOwner) {
                bot.pathfinder.setGoal?.(null);
            }
            traceBootstrap(bot, 'collect_finished', candidates.length, before, totalLogs());
            if (collectionError && !RECOVERABLE_NAVIGATION_ERRORS.has(collectionError.name)) {
                throw collectionError;
            }
            if (totalLogs() <= before) {
                if (bot.interrupt_code || bot.evelynMovementOwner) {
                    traceBootstrap(bot, 'interrupted', candidates.length);
                    return false;
                }
                if (freshLogCandidate(bot, logBlock)) {
                    rememberFailedLogClusters([logBlock.position]);
                }
                if (++failedBatches >= 2) return false;
                continue;
            }
            if (bot.interrupt_code || bot.evelynMovementOwner) {
                traceBootstrap(bot, 'interrupted', candidates.length);
                return false;
            }
        }
        for (const [logName, count] of Object.entries(inventory)) {
            if (!logName.endsWith('_log') || Number(count || 0) < 1) continue;
            const planks = `${logName.replace(/_log$/, '')}_planks`;
            if (!await craftRecipe(bot, planks, Number(count))) return false;
            if (bot.interrupt_code) return false;
        }
        inventory = inventoryCounts(bot);
        clearFailedLogClusters();
    }
    if (Number(inventory.stick || 0) < requiredSticks) {
        if (!await craftRecipe(bot, 'stick', 1)) return false;
        if (bot.interrupt_code) return false;
        inventory = inventoryCounts(bot);
    }
    if (Number(inventory.crafting_table || 0) < 1) {
        if (!await craftRecipe(bot, 'crafting_table', 1)) return false;
        if (bot.interrupt_code) return false;
    }
    if (!hasPickaxe(inventoryCounts(bot)) && !await craftRecipe(bot, 'wooden_pickaxe', 1)) return false;
    if (bot.interrupt_code) return false;
    if (!hasMeleeWeapon(inventoryCounts(bot)) && !await craftRecipe(bot, 'wooden_sword', 1)) return false;
    if (bot.interrupt_code) return false;
    inventory = inventoryCounts(bot);
    const success = hasPickaxe(inventory) && hasMeleeWeapon(inventory);
    if (success) clearFailedLogClusters();
    if (success) traceBootstrap(bot, 'complete');
    return success;
}

async function performDecision(agent, decision, snapshot, context = {}) {
    const bot = agent.bot;
    if (decision === 'handle_hostile') return handleHostile(bot, snapshot, context);
    if (decision === 'eat_inventory_food') {
        return monitoredCraftAndEat(agent, foodEvidence(bot), 'inventory_food_verified');
    }
    if (decision === 'escape_to_surface') return escapeToSurface(bot);
    if (decision === 'acquire_food') return acquireFood(agent, snapshot, context);
    if (decision === 'shelter_until_safe_dawn') return runTemporaryShelterAction(agent, snapshot);
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
        currentDecision: null,
        hostileReflexPromise: null,
        hostileReflexHandoffPending: false,
        startHostileAdmissionGuard: null,
        hostileFleeFailureStreak: {targetKey: null, count: 0},
        checking: false,
        dirty: true,
        urgent: false,
        wakeReason: null,
        wakeReceivedAt: 0,
        lastCheapWakeCheckAt: 0,
        listenerBot: null,
        detachWakeListeners: null,
        lastCheckAt: 0,
        failures: {},
        cooldownUntil: {},
        update: async function (agent) {
            const now = Date.now();
            attachSurvivalWakeListeners(this, agent.bot, agent);
            if (this.hostileReflexPromise || this.inFlight || this.checking) return;
            if (!this.urgent && now - this.lastCheckAt < CHECK_INTERVAL_MS) return;
            this.checking = true;
            this.lastCheckAt = now;
            const wakeReceivedAt = this.urgent ? this.wakeReceivedAt : null;
            const wakeReason = this.urgent ? this.wakeReason : null;
            this.urgent = false;
            this.dirty = false;
            this.wakeReason = null;
            this.wakeReceivedAt = 0;
            let snapshot;
            try {
                snapshot = buildSurvivalSnapshot(agent.bot);
            } finally {
                this.checking = false;
            }
            if (
                !snapshot.foodName &&
                (snapshot.hunger <= FOOD_ACQUIRE_HUNGER || snapshot.health <= CRITICAL_HEALTH)
            ) {
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
            const decision = this.hostileReflexHandoffPending && decisions[0] === 'handle_hostile'
                ? 'handle_hostile'
                : decisions.find((candidate) => Number(this.cooldownUntil[candidate] || 0) <= now) || null;
            const decisionSelectedAt = Date.now();
            const wakeToDecisionMs = wakeReceivedAt === null
                ? null
                : Math.max(0, decisionSelectedAt - wakeReceivedAt);
            const deferredDecision = decision && !decisionCanInterrupt(decision, snapshot) && !agent.isIdle();
            const shelterSuccessCount = (
                Number.isSafeInteger(agent.bot.evelynSurvivalState?.shelter_success_count) &&
                agent.bot.evelynSurvivalState.shelter_success_count >= 0
            ) ? agent.bot.evelynSurvivalState.shelter_success_count : 0;
            agent.bot.evelynSurvivalState = mergeSurvivalState(agent.bot.evelynSurvivalState, {
                phase: deferredDecision ? 'planner_control' : (decision || 'planner_control'),
                shelter_success_count: shelterSuccessCount,
                last_success: decision ? null : agent.bot.evelynSurvivalState?.last_success,
                last_error: decision ? null : agent.bot.evelynSurvivalState?.last_error,
                snapshot,
                failures: { ...this.failures },
                cooldown_until: { ...this.cooldownUntil },
                wake_reason: wakeReason,
                wake_received_at_ms: wakeReceivedAt,
                decision_selected_at_ms: decisionSelectedAt,
                action_started_at_ms: null,
                wake_to_decision_ms: wakeToDecisionMs,
                decision_to_action_ms: null,
                updated_at: nowSeconds(),
            });
            if (!decision || deferredDecision) {
                this.hostileReflexHandoffPending = false;
                this.startHostileAdmissionGuard = null;
                return;
            }
            if (decision !== 'handle_hostile') {
                this.hostileReflexHandoffPending = false;
                this.startHostileAdmissionGuard = null;
            }

            if (
                decision === 'handle_hostile' &&
                !this.hostileReflexHandoffPending &&
                !agent.isIdle()
            ) {
                const reflex = startHostilePreemptionReflex(
                    agent,
                    snapshot,
                    decisionSelectedAt,
                    wakeReceivedAt ?? decisionSelectedAt,
                );
                if (reflex) {
                    this.hostileReflexPromise = reflex.catch(() => ({
                        success: false,
                        strategy: 'hostile_preemption_reflex',
                        verification: 'reflex_error',
                    })).finally(() => {
                        this.hostileReflexPromise = null;
                        if (this.listenerBot !== agent.bot) return;
                        this.hostileReflexHandoffPending = true;
                        this.dirty = true;
                        if (!this.urgent) {
                            this.urgent = true;
                            this.wakeReason = SURVIVAL_WAKE_REASONS.FALLBACK;
                            this.wakeReceivedAt = Date.now();
                        }
                    });
                }
                return;
            }

            this.inFlight = true;
            this.currentDecision = decision;
            let actionAdmitted = false;
            let admissionGuardTimer = null;
            const cancelAdmissionGuardTimer = () => {
                if (admissionGuardTimer === null) return;
                clearTimeout(admissionGuardTimer);
                admissionGuardTimer = null;
            };
            const scheduleAdmissionGuard = (delayMs = 0) => {
                if (
                    decision !== 'handle_hostile' ||
                    actionAdmitted ||
                    !this.hostileReflexHandoffPending ||
                    this.startHostileAdmissionGuard !== scheduleAdmissionGuard
                ) return false;
                if (admissionGuardTimer !== null) return true;
                admissionGuardTimer = setTimeout(() => {
                    admissionGuardTimer = null;
                    if (
                        actionAdmitted ||
                        !this.hostileReflexHandoffPending ||
                        this.startHostileAdmissionGuard !== scheduleAdmissionGuard
                    ) return;
                    if (this.hostileReflexPromise) {
                        scheduleAdmissionGuard(MINECRAFT_TICK_MS);
                        return;
                    }
                    if (!nearbyHostiles(agent.bot, HOSTILE_FIGHT_DISTANCE).length) {
                        scheduleAdmissionGuard(MINECRAFT_TICK_MS);
                        return;
                    }
                    const reflex = startHostilePreemptionReflex(
                        agent,
                        snapshot,
                        Date.now(),
                        Date.now(),
                    );
                    if (!reflex) {
                        scheduleAdmissionGuard(MINECRAFT_TICK_MS);
                        return;
                    }
                    const tracked = reflex.catch(() => ({
                        success: false,
                        strategy: 'hostile_admission_guard',
                        verification: 'reflex_error',
                    })).finally(() => {
                        if (this.hostileReflexPromise === tracked) this.hostileReflexPromise = null;
                        this.dirty = true;
                        if (!this.urgent) {
                            this.urgent = true;
                            this.wakeReason = SURVIVAL_WAKE_REASONS.FALLBACK;
                            this.wakeReceivedAt = Date.now();
                        }
                        scheduleAdmissionGuard(MINECRAFT_TICK_MS);
                    });
                    this.hostileReflexPromise = tracked;
                }, Math.max(0, Number(delayMs) || 0));
                return true;
            };
            if (decision === 'handle_hostile') {
                this.startHostileAdmissionGuard = scheduleAdmissionGuard;
            }
            const execution = execute(this, agent, async () => {
                const actionStartedAt = Date.now();
                actionAdmitted = true;
                let actionSnapshot = snapshot;
                if (decision === 'handle_hostile') {
                    this.startHostileAdmissionGuard = null;
                    cancelAdmissionGuardTimer();
                    const activeReflex = this.hostileReflexPromise;
                    if (activeReflex) await activeReflex;
                    this.hostileReflexHandoffPending = false;
                    actionSnapshot = buildSurvivalSnapshot(agent.bot);
                }
                agent.bot.evelynSurvivalState = mergeSurvivalState(agent.bot.evelynSurvivalState, {
                    action_started_at_ms: actionStartedAt,
                    decision_to_action_ms: Math.max(0, actionStartedAt - decisionSelectedAt),
                });
                logDecision(agent, `starting ${decision}`);
                let success = false;
                let error = null;
                let details = null;
                try {
                    const result = await performDecision(agent, decision, actionSnapshot, {
                        failureCount: Number(this.failures[decision] || 0),
                        fleeFailureCount: (
                            this.hostileFleeFailureStreak.targetKey === hostileFleeTargetKey(actionSnapshot)
                                ? this.hostileFleeFailureStreak.count
                                : 0
                        ),
                    });
                    details = result && typeof result === 'object' ? result : null;
                    success = Boolean(details ? details.success : result);
                } catch (caught) {
                    error = String(caught?.message || caught);
                    console.error('[Evelyn Survival] decision failed:', decision, error);
                } finally {
                    this.inFlight = false;
                    this.currentDecision = null;
                }
                const completedAt = Date.now();
                const progressed = Boolean(details?.progress);
                if (decision === 'handle_hostile') {
                    this.hostileFleeFailureStreak = advanceHostileFleeFailureStreak(
                        this.hostileFleeFailureStreak,
                        details,
                        success,
                    );
                }
                let plannerHandoffUntil = 0;
                if (success) {
                    this.failures[decision] = 0;
                    this.cooldownUntil[decision] = completedAt + (decision === 'handle_hostile' ? 250 : 3000);
                } else if (progressed) {
                    this.failures[decision] = 0;
                    this.cooldownUntil[decision] = completedAt + 1000;
                } else {
                    const failures = Number(this.failures[decision] || 0) + 1;
                    this.failures[decision] = failures;
                    const handoffDelayMs = recoveryHandoffDelayMs(decision, details, failures);
                    this.cooldownUntil[decision] = handoffDelayMs > 0
                        ? completedAt + handoffDelayMs
                        : completedAt + failureCooldownMs(decision, failures);
                    plannerHandoffUntil = handoffDelayMs > 0 ? completedAt + handoffDelayMs : 0;
                }
                agent.bot.evelynSurvivalState = mergeSurvivalState(agent.bot.evelynSurvivalState, {
                    phase: plannerHandoffUntil ? 'planner_control' : (success ? 'reassess' : decision),
                    shelter_success_count: (
                        decision === 'shelter_until_safe_dawn' &&
                        success &&
                        details?.verification === SHELTER_SUCCESS_VERIFICATION
                    ) ? Math.min(Number.MAX_SAFE_INTEGER, shelterSuccessCount + 1) : shelterSuccessCount,
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
            }, decision === 'shelter_until_safe_dawn' ? -1 : 1);
            if (decision === 'handle_hostile' && !actionAdmitted) {
                scheduleAdmissionGuard();
            }
            const finishExecution = () => {
                if (actionAdmitted || decision !== 'handle_hostile') {
                    cancelAdmissionGuardTimer();
                    if (this.startHostileAdmissionGuard === scheduleAdmissionGuard) {
                        this.startHostileAdmissionGuard = null;
                    }
                }
                this.inFlight = false;
                this.currentDecision = null;
                if (
                    decision === 'handle_hostile' &&
                    !actionAdmitted &&
                    this.hostileReflexHandoffPending
                ) {
                    this.dirty = true;
                    if (!this.urgent) {
                        this.urgent = true;
                        this.wakeReason = SURVIVAL_WAKE_REASONS.FALLBACK;
                        this.wakeReceivedAt = Date.now();
                    }
                    scheduleAdmissionGuard();
                }
            };
            if (execution && typeof execution.then === 'function') {
                void execution.catch(() => {}).finally(finishExecution);
            } else if (!actionAdmitted) {
                finishExecution();
            }
        },
    };
}
