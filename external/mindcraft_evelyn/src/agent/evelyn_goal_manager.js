import fs from 'node:fs';
import path from 'node:path';
import {randomBytes} from 'node:crypto';
import {
    buildWorldState,
    inventoryCountForTarget,
    itemMatchesTarget,
    worldStateChanged
} from './evelyn_world_state.js';

const DEFAULT_STATE_PATH = '/app/runtime_artifacts/mindcraft/goal_manager_state.json';
const POSTCONDITION_CANDIDATE_SCHEMA = 'mindcraft.postcondition-candidate.v1';
const WORLD_EFFECT_EVIDENCE_CODE = 'mindcraft_explicit_postcondition_candidate';
const DEFAULT_WORLD_EFFECT_PRODUCER_NONCE = randomBytes(16).toString('hex');
const TYPED_IDENTIFIER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9:_\-.]{0,127}$/;
const WORLD_EFFECT_CONTRACTS = new Map([
    [
        'mindcraft_food_recovery.v1',
        {
            postconditionCode: 'food_reserve_ready',
            actionKeys: new Set(['minecraft:find_food_source']),
            matchesPredicate(predicate) {
                return Boolean(
                    predicate?.kind === 'inventory' &&
                    String(predicate?.target || '').toLowerCase() === '#food' &&
                    Number(predicate?.count || 0) >= 3
                );
            }
        }
    ]
]);
const SURVIVAL_RECOVERY_MAX_FAILURES = Math.max(
    1,
    Number(process.env.MINDCRAFT_SURVIVAL_RECOVERY_MAX_FAILURES || 12)
);
const MIN_RELOCATION_PROGRESS_M = Math.max(
    1,
    Number(process.env.MINDCRAFT_MIN_RELOCATION_PROGRESS_M || 2.5)
);
const MOVE_AWAY_MIN_PROGRESS_M = Math.max(
    4,
    Number(process.env.MINDCRAFT_MOVE_AWAY_MIN_PROGRESS_M || MIN_RELOCATION_PROGRESS_M)
);
const MOVE_AWAY_FAILURE_THRESHOLD = 3;
const SEARCH_FAILURE_RELOCATION_THRESHOLD = 2;
const SEARCH_FAILURE_RELOCATION_HARD_THRESHOLD = 1;
const LOG_STALL_FOOD_PRIORITY_TRIGGER = 2;
const FOOD_SEARCH_FAILURE_RELOCATION_THRESHOLD = Math.max(
    2,
    Number(process.env.MINDCRAFT_FOOD_SEARCH_FAILURE_RELOCATION_THRESHOLD || 4)
);
const OBSERVATION_COMMANDS = new Set([
    '!stats', '!inventory', '!nearbyBlocks', '!entities', '!craftable',
    '!getCraftingPlan', '!searchWiki'
]);
const AUTONOMY_CONTROL_COMMANDS = new Set(['!goal', '!endGoal']);
const SAFETY_COMMANDS = new Set(['!stop', '!consume', '!moveAway', '!goToBed']);
const ACTION_COMMANDS = new Set([
    '!searchForBlock', '!searchForEntity', '!collectBlocks', '!craftRecipe', '!smeltItem',
    '!equip', '!goToPosition', '!goToPlace', '!placeHere', '!attack',
    '!useDoor', '!activate'
]);
const DECORATIVE_TARGET = /(?:dye|concrete|terracotta|sandstone|carpet|banner|candle|stained_glass)$/;
const PROGRESSION_TARGET = /(?:^#|log$|stem$|hyphae$|planks$|^stick$|crafting_table$|pickaxe$|axe$|sword$|shield$|furnace$|cobblestone$|stone$|coal$|charcoal$|torch$|iron|diamond|gold|bucket$|flint$|flint_and_steel$|obsidian$|portal$|nether|blaze|ender|stronghold|bow$|crossbow$|arrow$|bed$|fortress|dragon)/;
const TARGET_PREREQUISITES = {
    '#food': [
        'wheat', 'hay_block', 'sweet_berry_bush', 'melon', 'melon_slice',
        'cow', 'pig', 'sheep', 'chicken', 'rabbit', 'cod', 'salmon'
    ],
    '#pickaxes': [
        'oak_log', '#logs', '#planks', 'stick', 'crafting_table', 'cobblestone',
        'wooden_pickaxe', 'stone_pickaxe', 'iron_pickaxe', 'diamond_pickaxe', 'iron_ingot'
    ],
    '#weapons': [
        'oak_log', '#logs', '#planks', 'stick', 'crafting_table',
        'wooden_sword', 'stone_sword', 'iron_sword', 'diamond_sword'
    ],
    '#logs': ['oak_log'],
    '#planks': ['oak_planks', '#logs', 'oak_log'],
    bread: ['wheat', 'crafting_table', '#planks', '#logs'],
    crafting_table: ['#planks', '#logs'],
    wooden_pickaxe: ['stick', '#planks', '#logs', 'crafting_table'],
    wooden_sword: ['stick', '#planks', '#logs', 'crafting_table'],
    stone_pickaxe: ['cobblestone', 'stick', 'crafting_table'],
    iron_pickaxe: ['iron_ingot', 'stick', 'crafting_table', 'furnace', 'raw_iron', '#fuel'],
    raw_iron: ['iron_ore', 'deepslate_iron_ore'],
    iron_ingot: ['raw_iron', 'iron_ore', 'deepslate_iron_ore', 'furnace', '#fuel'],
    diamond: ['diamond_ore', 'deepslate_diamond_ore', 'iron_pickaxe'],
    obsidian: ['water_bucket', 'lava', 'diamond_pickaxe'],
    blaze_rod: ['blaze', '#weapons', '#armor', '#food'],
    ender_pearl: ['enderman', '#weapons', '#armor', '#food'],
    furnace: ['cobblestone'],
    shield: ['iron_ingot', '#planks', '#logs'],
    bucket: ['iron_ingot'],
    flint_and_steel: ['iron_ingot', 'flint'],
    ender_eye: ['ender_pearl', 'blaze_powder', 'blaze_rod'],
    bow: ['stick', 'string'],
    arrow: ['flint', 'stick', 'feather']
};

function nowSeconds() {
    return Date.now() / 1000;
}

function normalizeEpochSeconds(maybeMillisecondsOrSeconds) {
    const value = Number(maybeMillisecondsOrSeconds);
    if (!Number.isFinite(value)) return NaN;
    return value > 1e11 ? value / 1000 : value;
}

function recoveryFailureCount(state) {
    const failures = state?.failures || {};
    const escapeFailures = Number(failures?.escape_to_surface || 0);
    const hostileFailures = Number(failures?.handle_hostile || 0);
    const recoveryFailures = Number(failures?.recovery || 0);
    return Math.max(
        Number.isFinite(escapeFailures) ? escapeFailures : 0,
        Number.isFinite(hostileFailures) ? hostileFailures : 0,
        Number.isFinite(recoveryFailures) ? recoveryFailures : 0
    );
}

function hasActionableHostile(state) {
    if (Array.isArray(state?.hostiles) && state.hostiles.some((hostile) => hostile && hostile.actionable === true)) {
        return true;
    }
    return Number(state?.hostileCount || 0) > 0;
}

function needsFoodResupply(snapshot) {
    if (!snapshot) return false;
    const hunger = Number(snapshot.hunger ?? 20);
    const foodCount = Number(snapshot?.inventory ? inventoryCountForTarget(snapshot.inventory, '#food') : 0);
    return hunger <= 14 || foodCount < 3;
}

function countRecentLogRecoveryFailures(state) {
    if (!state) return 0;
    return (state.blockedSubgoals || []).filter((entry) => {
        if (String(entry?.signature || '') !== 'obtain:#logs') return false;
        const reason = String(entry?.reason || '');
        return ['search_area_exhausted_after_relocations', 'move_away_insufficient_progress', 'action_budget_exhausted'].includes(reason);
    }).length;
}

function shouldBypassSurvivalRecoveryGate(state, snapshot) {
    const failureCount = recoveryFailureCount(state);
    if (
        !Number.isFinite(failureCount) ||
        !Number.isFinite(SURVIVAL_RECOVERY_MAX_FAILURES) ||
        failureCount < SURVIVAL_RECOVERY_MAX_FAILURES
    ) {
        return false;
    }
    const recoveryVerification = String(state?.recovery_verification || '').toLowerCase();
    const staircaseRecoveryStuck = (
        recoveryVerification === 'staircase_blocked' &&
        !hasActionableHostile(state) &&
        !(snapshot?.hostilesNearby || []).some((hostile) => hostile?.actionable === true)
    );
    if (staircaseRecoveryStuck) return true;
    if (hasActionableHostile(state)) return false;
    if ((snapshot?.hostilesNearby || []).some((hostile) => hostile?.actionable === true)) return false;
    return true;
}

function commandName(command) {
    return String(command || '').match(/![A-Za-z][A-Za-z0-9_]*/)?.[0] || null;
}

function firstStringArgument(command) {
    return String(command || '').match(/![A-Za-z][A-Za-z0-9_]*\(\s*["']([^"']+)["']/)?.[1] || null;
}

function firstNumberArgument(command) {
    const match = String(command || '').match(/![A-Za-z][A-Za-z0-9_]*\(\s*(-?\d+(?:\.\d+)?)/);
    return match ? Number(match[1]) : null;
}

function safeText(value, maximum = 240) {
    return String(value || '').trim().slice(0, maximum);
}

function safeId(value, fallback) {
    const normalized = safeText(value, 80)
        .toLowerCase()
        .replace(/[^a-z0-9_:-]+/g, '_')
        .replace(/^_+|_+$/g, '');
    return normalized || fallback;
}

function typedIdentifier(value) {
    const text = String(value || '').trim();
    return TYPED_IDENTIFIER_PATTERN.test(text) ? text : '';
}

function normalizeWorldEffectBinding(options = {}) {
    if (options.worldEffectBinding === false) return null;
    const supplied = (
        options.worldEffectBinding &&
        typeof options.worldEffectBinding === 'object'
    ) ? options.worldEffectBinding : {};
    const value = (key, environmentKey) => (
        supplied[key] ?? process.env[environmentKey]
    );
    const binding = {
        goalRunId: typedIdentifier(
            value('goalRunId', 'MINDCRAFT_WORLD_EFFECT_GOAL_RUN_ID')
        ),
        actionRunId: typedIdentifier(
            value('actionRunId', 'MINDCRAFT_WORLD_EFFECT_ACTION_RUN_ID')
        ),
        actionKey: typedIdentifier(
            value('actionKey', 'MINDCRAFT_WORLD_EFFECT_ACTION_KEY')
        ),
        contractCode: typedIdentifier(
            value('contractCode', 'MINDCRAFT_WORLD_EFFECT_CONTRACT_CODE')
        ),
        leaseId: typedIdentifier(
            value('leaseId', 'MINDCRAFT_WORLD_EFFECT_LEASE_ID')
        ),
        leaseProcessNonce: typedIdentifier(
            value(
                'leaseProcessNonce',
                'MINDCRAFT_WORLD_EFFECT_LEASE_PROCESS_NONCE'
            )
        ),
        producerNonce: typedIdentifier(
            value(
                'producerNonce',
                'MINDCRAFT_WORLD_EFFECT_PRODUCER_NONCE'
            ) || DEFAULT_WORLD_EFFECT_PRODUCER_NONCE
        )
    };
    if (
        Object.values(binding).some((item) => !item) ||
        !WORLD_EFFECT_CONTRACTS.has(binding.contractCode) ||
        !WORLD_EFFECT_CONTRACTS
            .get(binding.contractCode)
            .actionKeys.has(binding.actionKey)
    ) {
        return null;
    }
    return Object.freeze(binding);
}

function survivalRecoveryPhase(phase) {
    return ['escape_to_surface', 'handle_hostile'].includes(String(phase || '').toLowerCase());
}

function survivalRecoveryActive(state) {
    const recoveryPhase = String(state?.phase || '').toLowerCase();
    const isRecoveryPhase = survivalRecoveryPhase(recoveryPhase);
    const failureCount = Number(state?.failures?.[recoveryPhase] || 0);
    const maxFailureCount = Math.max(
        Number.isFinite(failureCount) ? failureCount : 0,
        recoveryFailureCount(state)
    );
    if (
        Number.isFinite(maxFailureCount) &&
        Number.isFinite(SURVIVAL_RECOVERY_MAX_FAILURES) &&
        maxFailureCount >= SURVIVAL_RECOVERY_MAX_FAILURES
    ) {
        const hasHighFailureHostile = hasActionableHostile(state);
        if (!hasHighFailureHostile) return false;
    }
    if (isRecoveryPhase) {
        return recoveryPhase === 'escape_to_surface' || hasActionableHostile(state);
    }
    const now = nowSeconds();
    const handoffUntil = normalizeEpochSeconds(state?.recovery_handoff_until);
    if (Number.isFinite(handoffUntil) && handoffUntil > now) {
        return false;
    }
    const cooldownUntil = state?.cooldown_until || {};
    return (
        Number.isFinite(Number(cooldownUntil.escape_to_surface)) &&
        normalizeEpochSeconds(cooldownUntil.escape_to_surface) > now
    ) || (
        Number.isFinite(Number(cooldownUntil.handle_hostile)) &&
        normalizeEpochSeconds(cooldownUntil.handle_hostile) > now
    );
}

function atomicWriteJson(target, payload) {
    const temporary = `${target}.${process.pid}.tmp`;
    fs.mkdirSync(path.dirname(target), {recursive: true});
    fs.writeFileSync(temporary, JSON.stringify(payload, null, 2), 'utf8');
    fs.renameSync(temporary, target);
}

export function predicateMeasure(predicate, snapshot) {
    if (!predicate || !snapshot) return null;
    if (predicate.kind === 'inventory') {
        return inventoryCountForTarget(snapshot.inventory, predicate.target);
    }
    if (predicate.kind === 'dimension') {
        const expected = String(predicate.equals || '').replace(/^minecraft:/, '').replace(/^the_/, '');
        return snapshot.dimension === expected ? 1 : 0;
    }
    if (predicate.kind === 'stat') {
        const value = Number(snapshot[predicate.field]);
        return Number.isFinite(value) ? value : null;
    }
    if (predicate.kind === 'entity_defeated') {
        return Number(snapshot?.defeatedEntities?.[predicate.target] || 0);
    }
    return null;
}

export function predicateSatisfied(predicate, snapshot) {
    if (!predicate || !snapshot) return false;
    if (predicate.kind === 'inventory') {
        return Number(predicateMeasure(predicate, snapshot)) >= Number(predicate.count || 1);
    }
    if (predicate.kind === 'dimension') {
        return predicateMeasure(predicate, snapshot) === 1;
    }
    if (predicate.kind === 'stat') {
        const value = Number(predicateMeasure(predicate, snapshot));
        if (!Number.isFinite(value)) return false;
        if (Number.isFinite(predicate.gte) && value < predicate.gte) return false;
        if (Number.isFinite(predicate.lte) && value > predicate.lte) return false;
        return Number.isFinite(predicate.gte) || Number.isFinite(predicate.lte);
    }
    if (predicate.kind === 'entity_defeated') {
        return Number(predicateMeasure(predicate, snapshot)) >= Number(predicate.count || 1);
    }
    return false;
}

function normalizePredicate(raw, kind, target, quantity) {
    const source = raw && typeof raw === 'object' ? raw : {};
    const predicateKind = safeText(source.kind || '', 30).toLowerCase();
    if (predicateKind === 'inventory') {
        const predicateTarget = safeText(source.target || target, 80).toLowerCase();
        if (!predicateTarget) return null;
        return {
            kind: 'inventory',
            target: predicateTarget,
            count: Math.max(1, Math.min(64, Number(source.count || quantity || 1)))
        };
    }
    if (predicateKind === 'dimension') {
        const equals = safeText(source.equals || target, 40).toLowerCase();
        return equals ? {kind: 'dimension', equals} : null;
    }
    if (predicateKind === 'stat') {
        const field = ['health', 'hunger'].includes(source.field) ? source.field : null;
        if (!field) return null;
        const predicate = {kind: 'stat', field};
        if (Number.isFinite(Number(source.gte))) predicate.gte = Number(source.gte);
        if (Number.isFinite(Number(source.lte))) predicate.lte = Number(source.lte);
        return Number.isFinite(predicate.gte) || Number.isFinite(predicate.lte) ? predicate : null;
    }
    if (predicateKind === 'entity_defeated') {
        const predicateTarget = safeText(source.target || target, 80).toLowerCase();
        return predicateTarget
            ? {
                kind: 'entity_defeated',
                target: predicateTarget,
                count: Math.max(1, Math.min(16, Number(source.count || 1)))
            }
            : null;
    }
    if (['obtain', 'craft', 'smelt', 'equip'].includes(kind) && target) {
        return {kind: 'inventory', target, count: Math.max(1, Math.min(64, quantity || 1))};
    }
    if (kind === 'enter_dimension' && target) {
        return {kind: 'dimension', equals: target};
    }
    return null;
}

function allowedCommandsFor(kind) {
    const common = [...OBSERVATION_COMMANDS, ...SAFETY_COMMANDS];
    const byKind = {
        obtain: ['!searchForBlock', '!searchForEntity', '!collectBlocks', '!goToPosition', '!goToPlace'],
        craft: ['!craftRecipe', '!searchForBlock', '!collectBlocks', '!goToPosition'],
        smelt: ['!smeltItem', '!searchForBlock', '!collectBlocks', '!goToPosition'],
        equip: ['!equip', '!craftRecipe', '!searchForBlock', '!collectBlocks'],
        locate: ['!searchForBlock', '!searchForEntity', '!goToPosition', '!goToPlace'],
        travel: ['!goToPosition', '!goToPlace', '!searchForBlock', '!searchForEntity'],
        enter_dimension: ['!goToPosition', '!goToPlace', '!placeHere', '!activate'],
        defeat: ['!attack', '!equip', '!consume', '!moveAway', '!goToPosition'],
        maintain: ['!consume', '!moveAway', '!goToBed', '!searchForBlock', '!collectBlocks']
    };
    return [...new Set([...common, ...(byKind[kind] || [])])];
}

function normalizeCandidate(raw, index) {
    if (!raw || typeof raw !== 'object') return null;
    const kind = safeText(raw.kind || raw.type, 30).toLowerCase();
    if (!['obtain', 'craft', 'smelt', 'equip', 'locate', 'travel', 'enter_dimension', 'defeat', 'maintain'].includes(kind)) {
        return null;
    }
    const target = safeText(raw.target, 80).toLowerCase();
    const quantity = Math.max(1, Math.min(64, Number(raw.quantity || raw.count || 1)));
    const success = normalizePredicate(raw.success, kind, target, quantity);
    if (!target || !success) return null;
    if (
        success.kind === 'inventory' &&
        !targetIsRelated(success.target, target)
    ) {
        return null;
    }
    const allowedTargets = [
        target,
        ...(Array.isArray(raw.allowed_targets) ? raw.allowed_targets : []),
        ...(Array.isArray(raw.prerequisite_targets) ? raw.prerequisite_targets : []),
        ...(TARGET_PREREQUISITES[target] || [])
    ]
        .map((value) => safeText(value, 80).toLowerCase())
        .filter(Boolean);
    return {
        id: safeId(raw.id, `${kind}_${target}_${index + 1}`),
        kind,
        target,
        quantity,
        reason: safeText(raw.reason || `Progress toward ${target}`),
        success,
        allowedTargets: [...new Set(allowedTargets)],
        allowedCommands: allowedCommandsFor(kind),
        actionBudget: Math.max(3, Math.min(16, Number(raw.action_budget || raw.actionBudget || 8))),
        unlockScore: Math.max(0, Math.min(5, Number(raw.unlock_score || raw.unlockScore || 1))),
        risk: ['low', 'medium', 'high'].includes(raw.risk) ? raw.risk : 'medium'
    };
}

export function minimumKitStatus(snapshot) {
    const inventory = snapshot?.inventory || {};
    const food = inventoryCountForTarget(inventory, '#food');
    const weapons = inventoryCountForTarget(inventory, '#weapons');
    const pickaxes = inventoryCountForTarget(inventory, '#pickaxes');
    const missing = [];
    if (food < 3) missing.push('food');
    if (weapons < 1) missing.push('weapon');
    if (pickaxes < 1) missing.push('pickaxe');
    return {
        ready: missing.length === 0,
        missing,
        food,
        weapons,
        pickaxes,
    };
}

function planksTargetForInventory(inventory) {
    for (const [rawName, rawCount] of Object.entries(inventory || {})) {
        if (Number(rawCount || 0) < 1) continue;
        const name = String(rawName || '').toLowerCase().replace(/^stripped_/, '');
        for (const suffix of ['_log', '_stem', '_hyphae']) {
            if (name.endsWith(suffix)) {
                return `${name.slice(0, -suffix.length)}_planks`;
            }
        }
    }
    return null;
}

export function foodRecoveryCandidate(snapshot) {
    const inventory = snapshot?.inventory || {};
    const food = inventoryCountForTarget(inventory, '#food');
    const hunger = Number(snapshot?.hunger ?? 20);
    if (food >= 3 || hunger > 14) return null;

    const wheat = Math.max(0, Number(inventory.wheat || 0));
    const breadRecipes = Math.floor(wheat / 3);
    if (breadRecipes < 1) {
        return {
            id: 'restore_food_reserve',
            kind: 'obtain',
            target: '#food',
            quantity: 3,
            reason: 'Restore a safe food reserve before taking progression risks.',
            success: {kind: 'inventory', target: '#food', count: 3},
            action_budget: 8,
            unlock_score: 6,
            risk: 'low'
        };
    }

    if (Number(inventory.crafting_table || 0) < 1) {
        const planks = inventoryCountForTarget(inventory, '#planks');
        if (planks < 4) {
            const planksTarget = planksTargetForInventory(inventory);
            if (planksTarget) {
                return {
                    id: 'craft_food_recovery_planks',
                    kind: 'craft',
                    target: planksTarget,
                    quantity: 1,
                    reason: 'Convert one carried log into planks for the food-recovery workbench.',
                    success: {kind: 'inventory', target: '#planks', count: 4},
                    prerequisite_targets: ['#logs'],
                    action_budget: 4,
                    unlock_score: 8,
                    risk: 'low'
                };
            }
            return {
                id: 'obtain_food_recovery_log',
                kind: 'obtain',
                target: '#logs',
                quantity: 1,
                reason: 'One log unlocks a workbench for converting carried wheat into bread.',
                success: {kind: 'inventory', target: '#logs', count: 1},
                action_budget: 6,
                unlock_score: 8,
                risk: 'low'
            };
        }
        return {
            id: 'craft_food_recovery_table',
            kind: 'craft',
            target: 'crafting_table',
            quantity: 1,
            reason: 'Build the workbench required to convert carried wheat into bread.',
            success: {kind: 'inventory', target: 'crafting_table', count: 1},
            action_budget: 4,
            unlock_score: 8,
            risk: 'low'
        };
    }

    const recipes = Math.min(Math.max(1, 3 - food), breadRecipes);
    return {
        id: 'craft_emergency_bread',
        kind: 'craft',
        target: 'bread',
        quantity: recipes,
        reason: 'Convert carried wheat into immediately edible food.',
        success: {kind: 'inventory', target: '#food', count: food + recipes},
        action_budget: 4,
        unlock_score: 9,
        risk: 'low'
    };
}

export function minimumKitCandidate(snapshot) {
    const inventory = snapshot?.inventory || {};
    const kit = minimumKitStatus(snapshot);
    const logs = inventoryCountForTarget(inventory, '#logs');
    const urgentFood = foodRecoveryCandidate(snapshot);
    if (urgentFood) return urgentFood;
    if ((kit.weapons < 1 || kit.pickaxes < 1) && logs < 3) {
        return {
            id: 'obtain_initial_logs',
            kind: 'obtain',
            target: '#logs',
            quantity: 3,
            reason: 'Logs unlock the minimum weapon and tool kit.',
            success: {kind: 'inventory', target: '#logs', count: 3},
            action_budget: 8,
            unlock_score: 6,
            risk: 'low'
        };
    }
    if ((kit.weapons < 1 || kit.pickaxes < 1) && !inventory.crafting_table) {
        return {
            id: 'craft_crafting_table',
            kind: 'craft',
            target: 'crafting_table',
            quantity: 1,
            reason: 'A crafting table is required for the minimum weapon and tool kit.',
            success: {kind: 'inventory', target: 'crafting_table', count: 1},
            action_budget: 6,
            unlock_score: 6,
            risk: 'low'
        };
    }
    if (kit.weapons < 1) {
        return {
            id: 'craft_first_weapon',
            kind: 'craft',
            target: 'wooden_sword',
            quantity: 1,
            reason: 'Carry at least one melee weapon before resuming progression.',
            success: {kind: 'inventory', target: '#weapons', count: 1},
            action_budget: 6,
            unlock_score: 6,
            risk: 'low'
        };
    }
    if (kit.pickaxes < 1) {
        return {
            id: 'obtain_first_pickaxe',
            kind: 'craft',
            target: 'wooden_pickaxe',
            quantity: 1,
            reason: 'Carry a pickaxe before resuming progression.',
            success: {kind: 'inventory', target: '#pickaxes', count: 1},
            action_budget: 6,
            unlock_score: 5,
            risk: 'low'
        };
    }
    if (kit.food < 3) {
        return {
            id: 'restore_food_reserve',
            kind: 'obtain',
            target: '#food',
            quantity: 3,
            reason: 'Complete the minimum survival reserve before progression.',
            success: {kind: 'inventory', target: '#food', count: 3},
            action_budget: 8,
            unlock_score: 5,
            risk: 'low'
        };
    }
    return null;
}

function fallbackCandidates(snapshot, state = null) {
    const inventory = snapshot?.inventory || {};
    const candidates = [];
    const blockedLogs = (state?.blockedSubgoals || []).filter((entry) => (
        entry?.signature === 'obtain:#logs' &&
        ['action_budget_exhausted', 'repeated_irrelevant_commands', 'move_away_insufficient_progress', 'search_area_exhausted_after_relocations']
            .includes(entry.reason)
    ));
    const hasRecentLogStall = blockedLogs.length >= 3;
    const frequentLogStall = countRecentLogRecoveryFailures(state) >= LOG_STALL_FOOD_PRIORITY_TRIGGER;
    const shouldPrioritizeFood = needsFoodResupply(snapshot);
    if (frequentLogStall && shouldPrioritizeFood) {
        candidates.push({
            id: 'restore_food_reserve',
            kind: 'obtain',
            target: '#food',
            quantity: 3,
            reason: 'Repeated log retrieval failures: secure food reserve before retrying.',
            success: {kind: 'inventory', target: '#food', count: 3},
            unlock_score: 8,
            risk: 'low'
        });
    }
    if (Number(snapshot?.hunger ?? 20) < 14 && inventoryCountForTarget(inventory, '#food') < 3) {
        candidates.push({
            id: 'restore_food_reserve',
            kind: 'obtain',
            target: '#food',
            quantity: 3,
            reason: 'Restore a safe food reserve before taking progression risks.',
            success: {kind: 'inventory', target: '#food', count: 3},
            unlock_score: 5,
            risk: 'low'
        });
    }
    if (inventoryCountForTarget(inventory, '#logs') < 3 && !(frequentLogStall && shouldPrioritizeFood)) {
        if (hasRecentLogStall && inventoryCountForTarget(inventory, '#food') < 3) {
            candidates.push({
                id: 'restore_food_reserve',
                kind: 'obtain',
                target: '#food',
                quantity: 3,
                reason: 'Recent log-stall recovery: restore food first and retry log search.',
                success: {kind: 'inventory', target: '#food', count: 3},
                unlock_score: 5,
                risk: 'low'
            });
        }
        candidates.push({
            id: 'obtain_initial_logs',
            kind: 'obtain',
            target: '#logs',
            quantity: 3,
            reason: 'Logs unlock the crafting table and basic tool chain.',
            success: {kind: 'inventory', target: '#logs', count: 3},
            unlock_score: 5,
            risk: 'low'
        });
    } else if (!inventory.crafting_table) {
        candidates.push({
            id: 'craft_crafting_table',
            kind: 'craft',
            target: 'crafting_table',
            quantity: 1,
            reason: 'A crafting table unlocks normal survival recipes.',
            success: {kind: 'inventory', target: 'crafting_table', count: 1},
            unlock_score: 5,
            risk: 'low'
        });
    } else if (inventoryCountForTarget(inventory, '#weapons') < 1) {
        candidates.push({
            id: 'craft_first_weapon',
            kind: 'craft',
            target: 'wooden_sword',
            quantity: 1,
            reason: 'A melee weapon is required before accepting normal combat risk.',
            success: {kind: 'inventory', target: '#weapons', count: 1},
            unlock_score: 5,
            risk: 'low'
        });
    } else if (inventoryCountForTarget(inventory, '#pickaxes') < 1) {
        candidates.push({
            id: 'obtain_first_pickaxe',
            kind: 'craft',
            target: 'wooden_pickaxe',
            quantity: 1,
            reason: 'A pickaxe unlocks stone and ore collection.',
            success: {kind: 'inventory', target: '#pickaxes', count: 1},
            unlock_score: 5,
            risk: 'low'
        });
    }
    return candidates;
}

function candidateScore(candidate, snapshot, state) {
    if (predicateSatisfied(candidate.success, snapshot)) return Number.NEGATIVE_INFINITY;
    if (DECORATIVE_TARGET.test(candidate.target)) return Number.NEGATIVE_INFINITY;
    const finalGoalText = String(state.ultimateGoal || '').toLowerCase().replace(/\s+/g, '_');
    const finalGoalMentionsTarget = finalGoalText.includes(candidate.target.replace(/^#/, ''));
    const foodTarget = itemMatchesTarget(candidate.target, '#food') || candidate.target === '#food';
    if (
        !PROGRESSION_TARGET.test(candidate.target) &&
        !foodTarget &&
        !['maintain', 'enter_dimension'].includes(candidate.kind) &&
        !finalGoalMentionsTarget
    ) {
        return Number.NEGATIVE_INFINITY;
    }
    if (
        foodTarget &&
        Number(snapshot?.hunger || 0) >= 18 &&
        inventoryCountForTarget(snapshot?.inventory, '#food') >= 3
    ) {
        return Number.NEGATIVE_INFINITY;
    }
    const signature = `${candidate.kind}:${candidate.target}`;
    const recentBlocks = (state.blockedSubgoals || [])
        .filter((entry) => entry.reason !== 'preempted_by_survival_priority')
        .slice(-6);
    const repeatPenalty = recentBlocks.filter((entry) => entry.signature === signature).length * 4;
    const riskPenalty = candidate.risk === 'high' ? 4 : candidate.risk === 'medium' ? 1 : 0;
    const dangerPenalty = (snapshot?.hostilesNearby || []).length > 0 && candidate.risk !== 'low' ? 3 : 0;
    return (
        candidate.unlockScore * 3 -
        riskPenalty -
        dangerPenalty -
        repeatPenalty -
        Math.min(candidate.actionBudget, 12) * 0.05
    );
}

function relatedTargets(target) {
    return new Set([target, ...(TARGET_PREREQUISITES[target] || [])]);
}

function targetIsRelated(commandTarget, subgoalTarget) {
    if (!commandTarget) return true;
    if (String(commandTarget) === String(subgoalTarget)) return true;
    const related = relatedTargets(subgoalTarget);
    for (const target of related) {
        if (itemMatchesTarget(commandTarget, target) || itemMatchesTarget(target, commandTarget)) {
            return true;
        }
    }
    return false;
}

function commandTargetIsRelated(commandTarget, current) {
    if (!commandTarget) return true;
    for (const target of current?.allowedTargets || [current?.target]) {
        if (targetIsRelated(commandTarget, target)) return true;
    }
    return false;
}

function relocationSearchFailureThreshold(current) {
    if (!current || String(current.kind || '') !== 'obtain') return SEARCH_FAILURE_RELOCATION_THRESHOLD;
    if (String(current.target || '').toLowerCase() === '#food') {
        return FOOD_SEARCH_FAILURE_RELOCATION_THRESHOLD;
    }
    return String(current.target || '').toLowerCase() === '#logs'
        ? SEARCH_FAILURE_RELOCATION_HARD_THRESHOLD
        : SEARCH_FAILURE_RELOCATION_THRESHOLD;
}

function normalizedCommand(command) {
    return safeText(command, 240).replace(/\s+/g, ' ');
}

function resultFailed(result) {
    return /(?:could not|can't\b|cannot\b|failed|invalid|not enough|missing the following|no [^\n]* nearby|path (?:not found|failed|timed out)|pathfinding stopped|desired goal was not reached)/i
        .test(String(result || ''));
}

function predicateProgressMade(predicate, before, after) {
    const beforeMeasure = predicateMeasure(predicate, before);
    const afterMeasure = predicateMeasure(predicate, after);
    if (!Number.isFinite(beforeMeasure) || !Number.isFinite(afterMeasure)) return false;
    if (predicate?.kind !== 'stat') return afterMeasure > beforeMeasure;
    const hasMinimum = Number.isFinite(Number(predicate.gte));
    const hasMaximum = Number.isFinite(Number(predicate.lte));
    if (hasMinimum && !hasMaximum) return afterMeasure > beforeMeasure;
    if (hasMaximum && !hasMinimum) return afterMeasure < beforeMeasure;
    if (hasMinimum && hasMaximum) {
        const distance = (value) => (
            value < Number(predicate.gte)
                ? Number(predicate.gte) - value
                : (value > Number(predicate.lte) ? value - Number(predicate.lte) : 0)
        );
        return distance(afterMeasure) < distance(beforeMeasure);
    }
    return false;
}

export class EvelynGoalManager {
    constructor(agent, options = {}) {
        this.agent = agent;
        this.statePath = options.statePath || process.env.MINDCRAFT_GOAL_MANAGER_STATE_PATH || DEFAULT_STATE_PATH;
        this.mode = safeText(options.mode || process.env.MINDCRAFT_GOAL_MANAGER_MODE || 'shadow', 20).toLowerCase();
        if (!['shadow', 'gated', 'off'].includes(this.mode)) this.mode = 'shadow';
        this.ultimateGoal = safeText(options.ultimateGoal || process.env.MINDCRAFT_GOAL, 2000);
        this.worldEffectBinding = normalizeWorldEffectBinding(options);
        this.worldEffectCandidate = null;
        this.worldEffectCandidateSequence = 0;
        this.state = {
            version: 1,
            ultimateGoal: this.ultimateGoal,
            mode: this.mode,
            autonomyState: 'active',
            manualPauseReason: null,
            currentSubgoal: null,
            completedSubgoals: [],
            blockedSubgoals: [],
            recentActions: [],
            executionSequence: 0,
            lastExecution: null,
            priorityRequest: null,
            lastProgressAt: null,
            lastGateDecision: null,
            deathCount: 0,
            lastDeathAt: null,
            ultimateGoalCompletedAt: null,
            updatedAt: nowSeconds()
        };
        this.lastSnapshot = null;
        this.lastUpdateAt = 0;
        this.preparePromise = null;
        // A lease-bound action is a fresh one-shot run. Reusing goal-manager
        // state from an earlier run could restore a terminal/manual pause and
        // silently transfer authority across actionRunId boundaries.
        if (!this.worldEffectBinding) this.restore();
    }

    restore() {
        try {
            const saved = JSON.parse(fs.readFileSync(this.statePath, 'utf8'));
            if (saved?.version === 1 && saved?.ultimateGoal === this.ultimateGoal) {
                this.state = {
                    ...this.state,
                    ...saved,
                    mode: this.mode,
                    ultimateGoal: this.ultimateGoal
                };
                if (!['active', 'manual_pause', 'completed'].includes(this.state.autonomyState)) {
                    this.state.autonomyState = this.state.ultimateGoalCompletedAt ? 'completed' : 'active';
                }
                this.state.executionSequence = Number(this.state.executionSequence || 0);
            }
        } catch (error) {
            if (error?.code !== 'ENOENT') {
                console.warn('[Evelyn Goal] state restore failed:', error?.message || error);
            }
        }
    }

    persist() {
        this.state.updatedAt = nowSeconds();
        try {
            atomicWriteJson(this.statePath, this.state);
        } catch (error) {
            console.warn('[Evelyn Goal] state persistence failed:', error?.message || error);
        }
        this.publish();
    }

    publish() {
        if (!this.agent?.bot) return;
        const current = this.state.currentSubgoal;
        this.agent.bot.evelynGoalState = {
            mode: this.mode,
            autonomy_state: this.state.autonomyState,
            manual_pause_reason: this.state.manualPauseReason,
            ultimate_goal: this.ultimateGoal,
            current_subgoal: current
                ? {
                    id: current.id,
                    kind: current.kind,
                    target: current.target,
                    quantity: current.quantity,
                    reason: current.reason,
                    success: current.success,
                    attempts: current.attempts,
                    action_budget: current.actionBudget,
                    progress_value: current.progressValue,
                    progress_baseline: current.progressBaseline,
                    observation_streak: current.observationStreak,
                    relocation_required: current.relocationRequired,
                    relocations: current.relocations,
                    started_at: current.startedAt
                }
                : null,
            completed_count: this.state.completedSubgoals.length,
            blocked_count: this.state.blockedSubgoals.length,
            last_progress_at: this.state.lastProgressAt,
            last_gate_decision: this.state.lastGateDecision,
            priority_request: this.state.priorityRequest,
            minimum_kit: minimumKitStatus(this.lastSnapshot),
            death_count: Number(this.state.deathCount || 0),
            last_death_at: this.state.lastDeathAt,
            last_execution: this.state.lastExecution,
            postcondition_candidate: this.worldEffectCandidate
                ? {...this.worldEffectCandidate}
                : null,
            ultimate_goal_completed_at: this.state.ultimateGoalCompletedAt,
            updated_at: this.state.updatedAt
        };
    }

    publishPostconditionCandidate({
        current,
        execution,
        autonomous,
        relevant,
        failed,
        changed,
        goalProgress,
        beforeSatisfied,
        afterSatisfied,
        predicateCompleted,
    }) {
        if (this.worldEffectCandidate || !this.worldEffectBinding) return false;
        const contract = WORLD_EFFECT_CONTRACTS.get(
            this.worldEffectBinding.contractCode
        );
        if (
            !contract ||
            !contract.matchesPredicate(current?.success) ||
            autonomous !== true ||
            relevant !== true ||
            failed !== false ||
            changed !== true ||
            goalProgress !== true ||
            beforeSatisfied !== false ||
            afterSatisfied !== true ||
            predicateCompleted !== true ||
            !Number.isInteger(execution?.sequence) ||
            execution.sequence <= 0 ||
            !Number.isFinite(Number(execution?.recordedAt))
        ) {
            return false;
        }
        this.worldEffectCandidateSequence += 1;
        this.worldEffectCandidate = Object.freeze({
            schema: POSTCONDITION_CANDIDATE_SCHEMA,
            producerNonce: this.worldEffectBinding.producerNonce,
            goalRunId: this.worldEffectBinding.goalRunId,
            actionRunId: this.worldEffectBinding.actionRunId,
            actionKey: this.worldEffectBinding.actionKey,
            contractCode: this.worldEffectBinding.contractCode,
            leaseId: this.worldEffectBinding.leaseId,
            leaseProcessNonce: this.worldEffectBinding.leaseProcessNonce,
            candidateSequence: this.worldEffectCandidateSequence,
            executionSequence: execution.sequence,
            observedAt: Number(execution.recordedAt),
            evidenceCode: WORLD_EFFECT_EVIDENCE_CODE,
            postconditionCode: contract.postconditionCode,
            beforeSatisfied: false,
            afterSatisfied: true,
            autonomous: true,
            relevant: true,
            actionSucceeded: true,
            worldChanged: true,
            goalProgress: true,
            predicateCompleted: true,
            completionDelta: 1,
            blockedDelta: 0,
            contentFree: true,
        });
        return true;
    }

    captureSnapshot() {
        return buildWorldState(this.agent?.bot);
    }

    async initialize() {
        if (!this.agent.bot.evelynGoalFacts) {
            this.agent.bot.evelynGoalFacts = {defeatedEntities: {}};
        }
        if (!this.agent.bot.evelynGoalFacts.defeatedEntities) {
            this.agent.bot.evelynGoalFacts.defeatedEntities = {};
        }
        this.agent.bot.on?.('entityDead', (entity) => {
            const name = safeText(entity?.name, 80).toLowerCase();
            if (!name) return;
            const defeated = this.agent.bot.evelynGoalFacts.defeatedEntities;
            defeated[name] = Number(defeated[name] || 0) + 1;
            if (
                name === 'ender_dragon' &&
                Date.now() - Number(this.state.lastDragonCombatAt || 0) <= 120_000
            ) {
                this.state.ultimateGoalCompletedAt = nowSeconds();
                this.state.autonomyState = 'completed';
                this.state.currentSubgoal = null;
                console.log('[Evelyn Goal] ultimate goal verified: ender dragon defeated after active combat');
            }
            this.persist();
        });
        this.agent.bot.on?.('death', () => {
            this.state.deathCount = Number(this.state.deathCount || 0) + 1;
            this.state.lastDeathAt = nowSeconds();
            if (this.state.currentSubgoal) {
                this.blockCurrentSubgoal('preempted_by_death_recovery');
            }
            this.state.priorityRequest = {
                kind: 'minimum_kit',
                requestedAt: nowSeconds(),
                reason: 'death_recovery_minimum_kit'
            };
            this.persist();
        });
        this.lastSnapshot = this.captureSnapshot();
        this.verifyCurrentSubgoal(this.lastSnapshot);
        this.persist();
    }

    verifyCurrentSubgoal(snapshot) {
        const current = this.state.currentSubgoal;
        if (!current || !predicateSatisfied(current.success, snapshot)) return false;
        this.state.completedSubgoals.push({
            id: current.id,
            signature: `${current.kind}:${current.target}`,
            completedAt: nowSeconds(),
            attempts: current.attempts,
            success: current.success
        });
        this.state.completedSubgoals = this.state.completedSubgoals.slice(-30);
        this.state.currentSubgoal = null;
        this.state.lastProgressAt = nowSeconds();
        console.log(`[Evelyn Goal] completed subgoal=${current.id}`);
        return true;
    }

    blockCurrentSubgoal(reason) {
        const current = this.state.currentSubgoal;
        if (!current) return;
        this.state.blockedSubgoals.push({
            id: current.id,
            signature: `${current.kind}:${current.target}`,
            blockedAt: nowSeconds(),
            attempts: current.attempts,
            reason: safeText(reason)
        });
        this.state.blockedSubgoals = this.state.blockedSubgoals.slice(-30);
        console.warn(`[Evelyn Goal] blocked subgoal=${current.id} reason=${reason}`);
        this.state.currentSubgoal = null;
    }

    requestPriorityGoal(kind, snapshot = null) {
        if (this.mode === 'off' || this.state.ultimateGoalCompletedAt) return;
        if (!['food', 'minimum_kit'].includes(kind)) return;
        const world = snapshot || this.captureSnapshot();
        if (kind === 'food') {
            if (
                Number(world?.hunger || 20) > 14 ||
                inventoryCountForTarget(world?.inventory, '#food') >= 3
            ) return;
        } else if (minimumKitStatus(world).ready) {
            return;
        }
        if (kind === 'minimum_kit' && this.state.priorityRequest?.kind === 'food') {
            return;
        }
        const candidate = kind === 'food'
            ? minimumKitCandidate({...world, hunger: Math.min(Number(world?.hunger ?? 20), 14)})
            : minimumKitCandidate(world);
        if (!candidate) return;
        if (
            this.state.currentSubgoal?.target === candidate.target &&
            this.state.currentSubgoal?.success?.target === candidate.success?.target
        ) return;
        if (this.state.priorityRequest?.kind === kind) return;
        this.state.priorityRequest = {
            kind,
            requestedAt: nowSeconds(),
            reason: kind === 'food' ? 'survival_food_reserve_low' : 'minimum_kit_incomplete'
        };
        this.persist();
    }

    async prepareForPrompt() {
        if (this.mode === 'off') return null;
        if (this.preparePromise) return this.preparePromise;
        this.preparePromise = this.prepareForPromptInner();
        try {
            return await this.preparePromise;
        } finally {
            this.preparePromise = null;
        }
    }

    async prepareForPromptInner() {
        if (this.state.ultimateGoalCompletedAt) {
            this.persist();
            return null;
        }
        const snapshot = this.captureSnapshot();
        this.lastSnapshot = snapshot;
        this.verifyCurrentSubgoal(snapshot);
        const priorityCandidate = this.state.priorityRequest?.kind === 'food'
            ? minimumKitCandidate({...snapshot, hunger: Math.min(Number(snapshot?.hunger ?? 20), 14)})
            : (
                this.state.priorityRequest?.kind === 'minimum_kit'
                    ? minimumKitCandidate(snapshot)
                    : null
            );
        if (
            priorityCandidate &&
            (
                this.state.currentSubgoal?.target !== priorityCandidate.target ||
                this.state.currentSubgoal?.success?.target !== priorityCandidate.success?.target
            )
        ) {
            const current = this.state.currentSubgoal;
            if (current) {
                this.state.blockedSubgoals.push({
                    id: current.id,
                    signature: `${current.kind}:${current.target}`,
                    blockedAt: nowSeconds(),
                    attempts: current.attempts,
                    reason: 'preempted_by_survival_priority'
                });
                this.state.blockedSubgoals = this.state.blockedSubgoals.slice(-30);
                this.state.currentSubgoal = null;
            }
        }
        const current = this.state.currentSubgoal;
        if (current && current.attempts >= current.actionBudget) {
            this.blockCurrentSubgoal('action_budget_exhausted');
        }
        if (!this.state.currentSubgoal) {
            let rawCandidates = [];
            if (priorityCandidate) {
                rawCandidates.push(priorityCandidate);
                this.state.priorityRequest = null;
            }
            try {
                const proposer = this.agent?.prompter?.chat_model?.proposeSubgoals;
                if (typeof proposer === 'function' && rawCandidates.length === 0) {
                    rawCandidates = await proposer.call(this.agent.prompter.chat_model, {
                        ultimate_goal: this.ultimateGoal,
                        world_state: snapshot,
                        completed_subgoals: this.state.completedSubgoals.slice(-8),
                        blocked_subgoals: this.state.blockedSubgoals.slice(-8)
                    });
                }
            } catch (error) {
                console.warn('[Evelyn Goal] local subgoal proposal failed:', error?.message || error);
            }
            const normalized = (Array.isArray(rawCandidates) ? rawCandidates : [])
                .map(normalizeCandidate)
                .filter(Boolean);
            if (!normalized.length) {
                normalized.push(...fallbackCandidates(snapshot, this.state).map(normalizeCandidate).filter(Boolean));
            }
            if (!normalized.length) {
                try {
                    const strategicProposer = this.agent?.prompter?.chat_model?.proposeStrategicSubgoals;
                    if (typeof strategicProposer === 'function') {
                        const strategic = await strategicProposer.call(
                            this.agent.prompter.chat_model,
                            {
                                ultimate_goal: this.ultimateGoal,
                                world_state: snapshot,
                                completed_subgoals: this.state.completedSubgoals.slice(-8),
                                blocked_subgoals: this.state.blockedSubgoals.slice(-8)
                            },
                            'no_valid_local_subgoal'
                        );
                        normalized.push(
                            ...(Array.isArray(strategic) ? strategic : [])
                                .map(normalizeCandidate)
                                .filter(Boolean)
                        );
                    }
                } catch (error) {
                    console.warn('[Evelyn Goal] strategic subgoal proposal failed:', error?.message || error);
                }
            }
            const selected = normalized
                .map((candidate) => ({candidate, score: candidateScore(candidate, snapshot, this.state)}))
                .filter((entry) => Number.isFinite(entry.score))
                .sort((left, right) => right.score - left.score)[0]?.candidate || null;
            if (selected) {
                const progressBaseline = predicateMeasure(selected.success, snapshot);
                this.state.currentSubgoal = {
                    ...selected,
                    attempts: 0,
                    gateRejects: 0,
                    progressBaseline,
                    progressValue: progressBaseline,
                    observationStreak: 0,
                    observationCounts: {},
                    searchFailureStreak: 0,
                    moveAwayFailureStreak: 0,
                    relocationRequired: false,
                    relocations: 0,
                    startedAt: nowSeconds(),
                    lastActionAt: null,
                    lastProgressAt: null
                };
                console.log(
                    `[Evelyn Goal] selected subgoal=${selected.id} kind=${selected.kind} target=${selected.target}`
                );
            } else {
                console.warn('[Evelyn Goal] no verifiable subgoal candidate; observation only');
            }
        }
        this.persist();
        return this.state.currentSubgoal;
    }

    promptContext() {
        if (this.mode === 'off') return '';
        if (this.state.ultimateGoalCompletedAt) {
            return [
                `ULTIMATE GOAL: "${this.ultimateGoal}"`,
                `ULTIMATE GOAL VERIFIED COMPLETE AT: ${this.state.ultimateGoalCompletedAt}`,
                'Use !endGoal now.'
            ].join('\n');
        }
        const current = this.state.currentSubgoal;
        if (!current) {
            return [
                `ULTIMATE GOAL: "${this.ultimateGoal}"`,
                'No verified short-term goal is active. Use one observation command only.'
            ].join('\n');
        }
        return [
            `ULTIMATE GOAL: "${this.ultimateGoal}"`,
            `ACTIVE SHORT-TERM GOAL: ${current.kind} ${current.target} x${current.quantity}`,
            `WHY THIS GOAL: ${current.reason}`,
            `SUCCESS PREDICATE: ${JSON.stringify(current.success)}`,
            `ACTION BUDGET: ${current.attempts}/${current.actionBudget}`,
            `ALLOWED COMMAND FAMILIES: ${current.allowedCommands.join(' ')}`,
            `ALLOWED TARGETS: ${(current.allowedTargets || [current.target]).join(' ')}`,
            `OBSERVATION STREAK: ${current.observationStreak || 0}/2`,
            `RELOCATION REQUIRED: ${Boolean(current.relocationRequired)}`,
            'Choose exactly one safe command that advances this short-term goal.',
            current.relocationRequired
                ? 'RELOCATION REQUIRED: issue exactly one !moveAway command now with distance 16~64, then continue search.'
                : 'Do not repeat the same observation more than twice without taking an action.',
            'Never use !goal or !endGoal. Only the goal manager may create, replace, or finish autonomous goals.',
            'Do not pursue decorative or unrelated resources. The goal manager verifies actual world changes.'
        ].join('\n');
    }

    gateCommand(command, {autonomous = false} = {}) {
        if (this.mode === 'off' || !autonomous) return {allowed: true, relevant: true, reason: 'not_autonomous'};
        if (this.state.autonomyState !== 'active') {
            return {
                allowed: false,
                relevant: false,
                reason: 'autonomy_not_active',
            };
        }
        const name = commandName(command);
        const current = this.state.currentSubgoal;
        const survivalRecovery = survivalRecoveryActive(this.agent?.bot?.evelynSurvivalState);
        const bypassSurvivalRecoveryGate = shouldBypassSurvivalRecoveryGate(
            this.agent?.bot?.evelynSurvivalState,
            this.lastSnapshot
        );
        const blockSurvivalRecovery = survivalRecovery && !bypassSurvivalRecoveryGate;
        const actionableHostiles = (this.lastSnapshot?.hostilesNearby || [])
            .filter((hostile) => hostile?.actionable === true);
        const unsafeUnarmed = (
            actionableHostiles.length > 0 &&
            inventoryCountForTarget(this.lastSnapshot?.inventory, '#weapons') < 1
        );
        let relevant = true;
        let reason = 'relevant';
        let hardReject = false;
        if (
            blockSurvivalRecovery &&
            name &&
            !OBSERVATION_COMMANDS.has(name) &&
            name !== '!consume'
        ) {
            relevant = false;
            hardReject = true;
            reason = 'survival_recovery_owns_movement';
        } else if (
            unsafeUnarmed &&
            !blockSurvivalRecovery &&
            name &&
            !SAFETY_COMMANDS.has(name) &&
            !OBSERVATION_COMMANDS.has(name)
        ) {
            relevant = false;
            hardReject = true;
            reason = 'survival_recovery_owns_movement';
        } else if (!name) {
            relevant = false;
            reason = 'missing_command_name';
        } else if (AUTONOMY_CONTROL_COMMANDS.has(name)) {
            const verifiedEnd = name === '!endGoal' && Boolean(this.state.ultimateGoalCompletedAt);
            relevant = verifiedEnd;
            hardReject = !verifiedEnd;
            reason = verifiedEnd
                ? 'ultimate_goal_verified_complete'
                : (
                    name === '!endGoal'
                        ? 'goal_manager_has_not_verified_ultimate_completion'
                        : 'goal_manager_owns_autonomous_goal_control'
                );
        } else if (OBSERVATION_COMMANDS.has(name)) {
            const key = normalizedCommand(command);
            const count = Number(current?.observationCounts?.[key] || 0);
            if (!current) {
                relevant = false;
                hardReject = true;
                reason = 'no_active_subgoal';
            } else if (current.relocationRequired) {
                relevant = false;
                hardReject = true;
                reason = 'relocation_required_before_more_observation';
            } else if (Number(current.observationStreak || 0) >= 2) {
                relevant = false;
                hardReject = true;
                current.relocationRequired = true;
                reason = 'observation_streak_exhausted';
            } else if (count >= 2) {
                relevant = false;
                hardReject = true;
                reason = 'observation_budget_exhausted';
            }
        } else if (SAFETY_COMMANDS.has(name)) {
            if (name === '!moveAway') {
                const distance = firstNumberArgument(command);
                if (!current) {
                    relevant = false;
                    hardReject = true;
                    reason = 'no_active_subgoal';
                } else if (!Number.isFinite(distance) || distance < 16 || distance > 64) {
                    relevant = false;
                    hardReject = true;
                    reason = 'unsafe_relocation_distance';
                } else if (current && !current.relocationRequired) {
                    relevant = false;
                    reason = 'relocation_not_required';
                }
            }
        } else if (!current) {
            relevant = false;
            hardReject = true;
            reason = 'no_active_subgoal';
        } else if (current.relocationRequired) {
            relevant = false;
            hardReject = true;
            reason = 'relocation_required_before_action';
        } else if (!current.allowedCommands.includes(name)) {
            relevant = false;
            reason = `command_outside_subgoal:${name}`;
        } else {
            const target = firstStringArgument(command);
            if (target && ACTION_COMMANDS.has(name) && !commandTargetIsRelated(target, current)) {
                relevant = false;
                reason = `unrelated_target:${target}`;
            }
        }
        const highRiskCommand = name === '!attack';
        if (highRiskCommand && !relevant) hardReject = true;
        const allowed = relevant || (this.mode === 'shadow' && !hardReject);
        this.state.lastGateDecision = {
            command: safeText(command, 180),
            mode: this.mode,
            relevant,
            allowed,
            reason,
            decidedAt: nowSeconds()
        };
        if (!relevant) {
            console.warn(`[Evelyn Goal] ${this.mode} would reject command=${command} reason=${reason}`);
        const countsAsGoalMismatch = (
            reason.startsWith('command_outside_subgoal:') ||
            reason.startsWith('unrelated_target:') ||
            reason === 'survival_recovery_owns_movement'
        );
            if (
                (this.mode === 'gated' || hardReject) &&
                current &&
                countsAsGoalMismatch &&
                !AUTONOMY_CONTROL_COMMANDS.has(name)
            ) {
                current.gateRejects = Number(current.gateRejects || 0) + 1;
                if (current.gateRejects >= 3) {
                    this.blockCurrentSubgoal('repeated_irrelevant_commands');
                }
            }
        }
        this.persist();
        return {allowed, relevant, reason};
    }

    async recordActionResult(command, result, before, after, {autonomous = false} = {}) {
        if (this.mode === 'off') return;
        const name = commandName(command);
        if (!autonomous && name === '!endGoal') {
            this.state.autonomyState = 'manual_pause';
            this.state.manualPauseReason = 'user_end_goal_command';
        } else if (!autonomous && name === '!goal') {
            this.state.autonomyState = 'active';
            this.state.manualPauseReason = null;
        }
        const commandTarget = firstStringArgument(command);
        if (
            autonomous &&
            name === '!attack' &&
            commandTarget === 'ender_dragon'
        ) {
            this.state.lastDragonCombatAt = Date.now();
        }
        const changed = worldStateChanged(before, after);
        const current = this.state.currentSubgoal;
        const related = Boolean(
            current &&
            (
                OBSERVATION_COMMANDS.has(name) ||
                (SAFETY_COMMANDS.has(name) && name !== '!moveAway') ||
                commandTargetIsRelated(commandTarget, current)
            )
        );
        const relocationDistance = (
            name === '!moveAway' &&
            before?.position &&
            after?.position
        )
            ? Math.hypot(
                Number(after.position.x) - Number(before.position.x),
                Number(after.position.z) - Number(before.position.z)
            )
            : null;
        const moveAwayProgressed = (
            name === '!moveAway' &&
            Number.isFinite(relocationDistance) &&
            relocationDistance >= MOVE_AWAY_MIN_PROGRESS_M
        );
        const hasResultText = String(result || '').trim().length > 0;
        const failed = name === '!moveAway'
            ? !moveAwayProgressed
            : resultFailed(result);
        const beforeMeasure = current ? predicateMeasure(current.success, before) : null;
        const afterMeasure = current ? predicateMeasure(current.success, after) : null;
        const beforeSatisfied = current
            ? predicateSatisfied(current.success, before)
            : false;
        const afterSatisfied = current
            ? predicateSatisfied(current.success, after)
            : false;
        const goalProgress = current
            ? predicateProgressMade(current.success, before, after)
            : false;
        if (current && autonomous && OBSERVATION_COMMANDS.has(name)) {
            const key = normalizedCommand(command);
            current.observationStreak = Number(current.observationStreak || 0) + 1;
            current.observationCounts ||= {};
            current.observationCounts[key] = Number(current.observationCounts[key] || 0) + 1;
        } else if (current && autonomous && (ACTION_COMMANDS.has(name) || SAFETY_COMMANDS.has(name))) {
            current.observationStreak = 0;
            current.observationCounts = {};
        }
        if (current && autonomous && related && (ACTION_COMMANDS.has(name) || SAFETY_COMMANDS.has(name))) {
            current.attempts += 1;
            current.lastActionAt = nowSeconds();
            current.progressValue = afterMeasure;
            if (goalProgress) {
                current.lastProgressAt = nowSeconds();
                this.state.lastProgressAt = current.lastProgressAt;
            }
            if (['!searchForBlock', '!searchForEntity'].includes(name)) {
                const searchFailed = failed || !hasResultText;
                const hardPathFailure = /cannot break/i.test(String(result || ''));
                const hasPickaxe = Number(
                    inventoryCountForTarget(before?.inventory || {}, '#pickaxes') || 0
                ) > 0;
                current.searchFailureStreak = searchFailed
                    ? Number(current.searchFailureStreak || 0) + 1
                    : 0;
                if (
                    hardPathFailure &&
                    !hasPickaxe &&
                    String(current.target || '').toLowerCase() === '#logs'
                ) {
                    current.searchFailureStreak = Math.max(
                        current.searchFailureStreak,
                        relocationSearchFailureThreshold(current)
                    );
                }
                const relocationThreshold = relocationSearchFailureThreshold(current);
                if (current.searchFailureStreak >= relocationThreshold) current.relocationRequired = true;
                if (
                    hardPathFailure &&
                    !hasPickaxe &&
                    String(current.target || '').toLowerCase() === '#logs' &&
                    needsFoodResupply(before)
                ) {
                    this.state.priorityRequest = {
                        kind: 'food',
                        requestedAt: nowSeconds(),
                        reason: 'log_search_requires_food_priority'
                    };
                }
            }
            if (name === '!moveAway') {
                if (failed) {
                    current.moveAwayFailureStreak = Number(current.moveAwayFailureStreak || 0) + 1;
                    if (String(current.target || '').toLowerCase() === '#food') {
                        current.relocationRequired = false;
                        current.searchFailureStreak = 0;
                        current.gateRejects = 0;
                    }
                    if (current.moveAwayFailureStreak >= MOVE_AWAY_FAILURE_THRESHOLD) {
                        this.blockCurrentSubgoal('move_away_insufficient_progress');
                        if (String(current.target || '').toLowerCase() === '#logs' && needsFoodResupply(before)) {
                            this.state.priorityRequest = {
                                kind: 'food',
                                requestedAt: nowSeconds(),
                                reason: 'log_relocation_stalled'
                            };
                        }
                    }
                } else {
                    current.moveAwayFailureStreak = 0;
                    current.relocationRequired = false;
                    current.relocations = Number(current.relocations || 0) + 1;
                }
                if (current.relocations >= 3 && !goalProgress) {
                    this.blockCurrentSubgoal('search_area_exhausted_after_relocations');
                    if (String(current.target || '').toLowerCase() === '#logs' && needsFoodResupply(before)) {
                        this.state.priorityRequest = {
                            kind: 'food',
                            requestedAt: nowSeconds(),
                            reason: 'log_relocation_exhausted'
                        };
                    }
                }
            }
        }
        const execution = {
            sequence: Number(this.state.executionSequence || 0) + 1,
            command: safeText(command, 180),
            result: safeText(result, 300),
            autonomous,
            relevant: related,
            failed,
            worldChanged: changed,
            goalProgress,
            relocationDistance,
            recordedAt: nowSeconds()
        };
        this.state.executionSequence = execution.sequence;
        this.state.lastExecution = execution;
        this.state.recentActions.push(execution);
        this.state.recentActions = this.state.recentActions.slice(-20);
        this.lastSnapshot = after;
        const predicateCompleted = this.verifyCurrentSubgoal(after);
        const postconditionCandidatePublished = this.publishPostconditionCandidate({
            current,
            execution,
            autonomous,
            relevant: related,
            failed,
            changed,
            goalProgress,
            beforeSatisfied,
            afterSatisfied,
            predicateCompleted,
        });
        if (postconditionCandidatePublished) {
            // A bound autonomy action is one-shot.  Fence the planner before
            // publishing state so no later command can mutate the world while
            // Python durably verifies this candidate.
            this.state.autonomyState = 'manual_pause';
            this.state.manualPauseReason = 'world_effect_candidate_published';
        }
        if (this.state.currentSubgoal?.attempts >= this.state.currentSubgoal?.actionBudget) {
            this.blockCurrentSubgoal('action_budget_exhausted');
        }
        this.persist();
    }

    async update() {
        const now = Date.now();
        if (this.mode === 'off' || now - this.lastUpdateAt < 1000) return;
        this.lastUpdateAt = now;
        const snapshot = this.captureSnapshot();
        const changed = this.verifyCurrentSubgoal(snapshot);
        this.lastSnapshot = snapshot;
        const selfPrompter = this.agent?.self_prompter;
        const mayResume = (
            this.state.autonomyState === 'active' &&
            !this.state.ultimateGoalCompletedAt &&
            typeof selfPrompter?.isStopped === 'function' &&
            selfPrompter.isStopped() &&
            !selfPrompter.loop_active &&
            (typeof this.agent?.isIdle !== 'function' || this.agent.isIdle()) &&
            Boolean(this.agent?.bot?.entity)
        );
        if (mayResume && typeof selfPrompter.start === 'function') {
            console.warn('[Evelyn Goal] autonomous loop stopped unexpectedly; restarting ultimate goal');
            selfPrompter.start(this.ultimateGoal);
            this.persist();
        } else if (changed) this.persist();
        else this.publish();
    }
}
