import { mkdirSync, renameSync, writeFileSync } from 'fs';
import path from 'path';
import Vec3 from 'vec3';

const STATUS_PATH = process.env.MINDCRAFT_STATUS_PATH || '/app/runtime_artifacts/mindcraft/status.json';
const HOSTILE_NAMES = new Set([
    'blaze', 'bogged', 'breeze', 'cave_spider', 'creeper', 'drowned', 'elder_guardian',
    'enderman', 'endermite', 'evoker', 'ghast', 'guardian', 'hoglin', 'husk', 'magma_cube',
    'phantom', 'piglin_brute', 'pillager', 'ravager', 'shulker', 'silverfish', 'skeleton',
    'slime', 'spider', 'stray', 'vex', 'vindicator', 'warden', 'witch', 'wither_skeleton',
    'zoglin', 'zombie', 'zombie_villager'
]);
const SURVIVAL_DECISION_CODES = new Set([
    'acquire_food',
    'bootstrap_tools',
    'eat_inventory_food',
    'escape_to_surface',
    'handle_hostile',
    'planner_control',
    'reassess',
    'shelter_until_safe_dawn',
]);
const SURVIVAL_WAKE_CODES = new Set([
    'health',
    'breath',
    'hostile_spawn',
    'hostile_band',
    'hostile_gone',
    'projectile',
    'fallback',
]);
const SURVIVAL_REFLEX_CODES = new Set(['hostile', 'projectile']);
const SURVIVAL_BOOTSTRAP_PHASE_CODES = new Set([
    'candidate_search',
    'candidate_reached',
    'candidate_unreached',
    'collect_started',
    'collect_finished',
    'no_candidates',
    'interrupted',
    'complete',
]);
const SHELTER_VERIFICATION_CODES = new Set([
    'shelter_breached',
    'shelter_breached_interior',
    'shelter_breached_material_changed',
    'shelter_breached_missing_block',
    'shelter_breached_replaced_block',
    'shelter_breached_support',
    'shelter_build_interrupted',
    'shelter_context_unsafe',
    'shelter_dawn_exit_verified',
    'shelter_disconnected',
    'shelter_enclosure_unverified',
    'shelter_exit_unverified',
    'shelter_gather_damage_taken',
    'shelter_gather_disconnected',
    'shelter_gather_entered_water',
    'shelter_gather_hostile_detected',
    'shelter_gather_return_failed',
    'shelter_gather_timeout',
    'shelter_gather_timeout_direct_collect',
    'shelter_gather_timeout_generic_collect',
    'shelter_gather_timeout_generic_collect_no_candidate',
    'shelter_gather_timeout_generic_collect_not_diggable',
    'shelter_gather_timeout_generic_collect_not_visible',
    'shelter_gather_timeout_generic_collect_probe_unavailable',
    'shelter_gather_timeout_generic_collect_unsafe_candidate',
    'shelter_hold_interrupted',
    'shelter_material_unavailable',
    'shelter_placement_unverified',
    'shelter_return_failed',
    'shelter_site_unbuildable',
]);

function boundedLatencyMs(value) {
    return Number.isFinite(value) && value >= 0 && value <= 600000
        ? Math.round(value)
        : null;
}

function boundedCount(value, maximum) {
    return Number.isSafeInteger(value) && value >= 0 && value <= maximum ? value : null;
}

export function contentFreeSurvivalState(value) {
    if (!value || typeof value !== 'object') return null;
    const phase = SURVIVAL_DECISION_CODES.has(value.phase) ? value.phase : null;
    const lastDecision = SURVIVAL_DECISION_CODES.has(value.last_decision)
        ? value.last_decision
        : null;
    const shelterVerification = lastDecision === 'shelter_until_safe_dawn' &&
        SHELTER_VERIFICATION_CODES.has(value.recovery_verification)
        ? value.recovery_verification
        : null;
    return {
        phase,
        last_decision: lastDecision,
        last_success: typeof value.last_success === 'boolean' ? value.last_success : null,
        shelter_success_count: boundedCount(value.shelter_success_count, Number.MAX_SAFE_INTEGER),
        last_error: value.last_error ? 'survival_action_failed' : null,
        recovery_progress: value.recovery_progress === true,
        shelter_verification: shelterVerification,
        recovery_handoff_until: Number.isFinite(value.recovery_handoff_until)
            ? value.recovery_handoff_until
            : 0,
        wake_reason: SURVIVAL_WAKE_CODES.has(value.wake_reason) ? value.wake_reason : null,
        wake_to_decision_ms: boundedLatencyMs(value.wake_to_decision_ms),
        decision_to_action_ms: boundedLatencyMs(value.decision_to_action_ms),
        reflex_reason: SURVIVAL_REFLEX_CODES.has(value.reflex_reason) ? value.reflex_reason : null,
        reflex_to_action_ms: boundedLatencyMs(value.reflex_to_action_ms),
        bootstrap_phase: SURVIVAL_BOOTSTRAP_PHASE_CODES.has(value.bootstrap_phase)
            ? value.bootstrap_phase
            : null,
        bootstrap_candidate_count: boundedCount(value.bootstrap_candidate_count, 4),
        bootstrap_logs_before: boundedCount(value.bootstrap_logs_before, 64),
        bootstrap_logs_after: boundedCount(value.bootstrap_logs_after, 64),
        last_reflex_at: Number.isFinite(value.last_reflex_at) && value.last_reflex_at >= 0
            ? value.last_reflex_at
            : null,
        updated_at: Number.isFinite(value.updated_at) ? value.updated_at : null,
        content_free: true,
    };
}

export function install12111VelocityCompatibility(bot) {
    if (typeof bot?._client?.on !== 'function') return false;
    const applyNaturalVelocity = (packet) => {
        if (bot.version !== '1.21.11') return;
        const entity = bot.entities?.[packet?.entityId];
        const velocity = packet?.velocity;
        if (
            typeof entity?.velocity?.set !== 'function' ||
            !Number.isFinite(velocity?.x) ||
            !Number.isFinite(velocity?.y) ||
            !Number.isFinite(velocity?.z)
        ) return;
        // 1.21.11 lpVec3 is already measured in blocks per tick.
        entity.velocity.set(velocity.x, velocity.y, velocity.z);
    };
    const attach = () => {
        bot._client.on('spawn_entity', applyNaturalVelocity);
        bot._client.on('entity_velocity', applyNaturalVelocity);
    };
    if (bot.entities) attach();
    else if (typeof bot.once === 'function') bot.once('inject_allowed', attach);
    else return false;
    return true;
}

function loadInitialState(agentName) {
    return {
        runtime: 'mindcraft',
        agent_name: agentName,
        running: true,
        connected: false,
        connected_at: null,
        connection_state: 'starting',
        phase: 'starting',
        blocked_command_count: 0
    };
}

function inventoryCounts(bot) {
    const counts = {};
    for (const item of bot.inventory?.items?.() || []) {
        counts[item.name] = (counts[item.name] || 0) + Number(item.count || 0);
    }
    return counts;
}

function hostileIsActionable(origin, entityPosition, distance) {
    if (!origin || !entityPosition || !Number.isFinite(distance)) return false;
    const verticalDistance = Math.abs(Number(entityPosition.y) - Number(origin.y));
    return distance <= 8 || verticalDistance <= 5;
}

function hostileHasClearLine(bot, entity) {
    const origin = bot?.entity?.position;
    const target = entity?.position;
    if (!origin || !target) return false;
    if (typeof bot.blockAt !== 'function') return true;
    const start = {x: Number(origin.x), y: Number(origin.y) + 1.55, z: Number(origin.z)};
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

function nearbyHostiles(bot) {
    const origin = bot.entity?.position;
    if (!origin) return [];
    return Object.values(bot.entities || {})
        .filter((entity) => entity && entity.position && HOSTILE_NAMES.has(String(entity.name || '').toLowerCase()))
        .map((entity) => {
            const distance = Math.round(origin.distanceTo(entity.position) * 10) / 10;
            return {
                name: entity.name,
                distance,
                vertical_distance: Math.round(Math.abs(origin.y - entity.position.y) * 10) / 10,
                actionable: hostileIsActionable(origin, entity.position, distance) &&
                    (distance <= 4 || hostileHasClearLine(bot, entity))
            };
        })
        .filter((entity) => entity.distance <= 24)
        .sort((left, right) => left.distance - right.distance)
        .slice(0, 8);
}

export function installEvelynRuntime(bot, { agentName }) {
    install12111VelocityCompatibility(bot);
    const state = loadInitialState(agentName);
    const navigation = {
        path_updates: 0,
        nonempty_path_updates: 0,
        success_updates: 0,
        partial_updates: 0,
        timeout_updates: 0,
        no_path_updates: 0,
        goal_reached: 0,
        verified_goal_reached: 0,
        stuck_resets: 0,
        other_resets: 0,
        last_event: null,
        updated_at: null,
    };
    const markNavigation = (counter, event) => {
        navigation[counter] = Math.min(Number.MAX_SAFE_INTEGER, navigation[counter] + 1);
        navigation.last_event = event;
        navigation.updated_at = Date.now() / 1000;
    };
    let pathAttemptOrigin = null;
    let pathAttemptHasNodes = false;
    const clearPathAttempt = () => {
        pathAttemptOrigin = null;
        pathAttemptHasNodes = false;
    };
    bot.on('path_update', (result) => {
        markNavigation('path_updates', 'path_update');
        if (Array.isArray(result?.path) && result.path.length > 0) {
            markNavigation('nonempty_path_updates', 'nonempty_path');
            if (!pathAttemptOrigin && bot.entity?.position) {
                pathAttemptOrigin = {
                    x: Number(bot.entity.position.x),
                    y: Number(bot.entity.position.y),
                    z: Number(bot.entity.position.z),
                };
            }
            pathAttemptHasNodes = true;
        }
        if (result?.status === 'success') markNavigation('success_updates', 'success');
        else if (result?.status === 'partial') markNavigation('partial_updates', 'partial');
        else if (result?.status === 'timeout') {
            markNavigation('timeout_updates', 'timeout');
            clearPathAttempt();
        } else if (result?.status === 'noPath') {
            markNavigation('no_path_updates', 'no_path');
            clearPathAttempt();
        }
    });
    bot.on('goal_reached', () => {
        markNavigation('goal_reached', 'goal_reached');
        const position = bot.entity?.position;
        if (
            pathAttemptHasNodes &&
            pathAttemptOrigin &&
            position &&
            Math.hypot(
                Number(position.x) - pathAttemptOrigin.x,
                Number(position.y) - pathAttemptOrigin.y,
                Number(position.z) - pathAttemptOrigin.z,
            ) >= 0.5
        ) {
            markNavigation('verified_goal_reached', 'verified_goal_reached');
        }
        clearPathAttempt();
    });
    bot.on('path_reset', (reason) => {
        markNavigation(
            reason === 'stuck' ? 'stuck_resets' : 'other_resets',
            reason === 'stuck' ? 'stuck' : 'reset',
        );
        if (reason !== 'stuck') clearPathAttempt();
    });
    const goalManagerMode = String(
        process.env.MINDCRAFT_GOAL_MANAGER_MODE || 'gated'
    ).trim().toLowerCase();
    state.runtime = 'mindcraft';
    state.agent_name = agentName;
    state.running = true;
    state.command_policy = 'outbound_chat_disabled_by_default';
    state.task_contract = {
        schema: 'mindcraft.task-contract.v1',
        ready: goalManagerMode === 'gated',
        goal_manager_mode: goalManagerMode,
        command_gate: 'evelyn_goal_manager',
        effect_verification: 'explicit_postcondition'
    };
    state.updated_at = Date.now() / 1000;

    const writeStatus = () => {
        const position = bot.entity?.position;
        state.updated_at = Date.now() / 1000;
        state.health = Number.isFinite(bot.health) ? bot.health : null;
        state.hunger = Number.isFinite(bot.food) ? bot.food : null;
        state.food_saturation = Number.isFinite(bot.foodSaturation) ? bot.foodSaturation : null;
        state.position = position ? { x: position.x, y: position.y, z: position.z } : null;
        state.inventory = inventoryCounts(bot);
        state.hostiles_nearby = nearbyHostiles(bot);
        state.survival_controller = contentFreeSurvivalState(bot.evelynSurvivalState);
        state.navigation = {
            ...navigation,
            active: bot.pathfinder?.goal != null,
            content_free: true,
        };
        state.goal_manager = bot.evelynGoalState || null;
        const directory = path.dirname(STATUS_PATH);
        const temporary = `${STATUS_PATH}.${process.pid}.tmp`;
        try {
            mkdirSync(directory, { recursive: true });
            writeFileSync(temporary, JSON.stringify(state, null, 2), 'utf8');
            renameSync(temporary, STATUS_PATH);
        } catch (error) {
            console.error('[Evelyn Mindcraft] status write failed:', error?.message || error);
        }
    };

    let chatGuardInstalled = false;
    const installChatGuard = () => {
        if (chatGuardInstalled || typeof bot.chat !== 'function') return;
        const outboundChatAllowed = () => /^(?:1|true|yes)$/i.test(
            String(process.env.MINDCRAFT_ALLOW_OUTBOUND_CHAT || '')
        );
        const originalChat = bot.chat.bind(bot);
        bot.chat = (message) => {
            const text = String(message || '');
            if (!outboundChatAllowed() || text.trimStart().startsWith('/')) {
                state.blocked_command_count = Number(state.blocked_command_count || 0) + 1;
                state.last_blocked_command = text.trimStart().startsWith('/')
                    ? 'slash_command_blocked'
                    : 'outbound_chat_disabled';
                state.last_blocked_command_at = Date.now() / 1000;
                console.warn(`[Evelyn Mindcraft] blocked outbound chat code=${state.last_blocked_command}`);
                writeStatus();
                return;
            }
            return originalChat(text);
        };
        if (typeof bot.whisper === 'function') {
            const originalWhisper = bot.whisper.bind(bot);
            bot.whisper = (username, message) => {
                if (!outboundChatAllowed()) {
                    state.blocked_command_count = Number(state.blocked_command_count || 0) + 1;
                    state.last_blocked_command = 'outbound_whisper_disabled';
                    state.last_blocked_command_at = Date.now() / 1000;
                    console.warn('[Evelyn Mindcraft] blocked outbound whisper');
                    writeStatus();
                    return;
                }
                return originalWhisper(username, message);
            };
        }
        chatGuardInstalled = true;
    };
    installChatGuard();
    bot.once('inject_allowed', installChatGuard);

    bot.on('login', () => {
        state.connection_state = 'authenticated';
        state.phase = 'login';
        writeStatus();
    });
    bot.on('spawn', () => {
        state.connected = true;
        state.connected_at = Date.now() / 1000;
        state.connection_state = 'connected';
        state.phase = 'survival';
        state.last_error = null;
        writeStatus();
    });
    bot.on('death', () => {
        state.phase = 'respawning';
        state.last_death_event = { recorded_at: new Date().toISOString(), position: state.position };
        writeStatus();
    });
    bot.on('kicked', () => {
        state.connected = false;
        state.connected_at = null;
        state.connection_state = 'kicked';
        state.phase = 'stopped';
        state.last_error = 'minecraft_kicked';
        writeStatus();
    });
    bot.on('end', (reason) => {
        state.connected = false;
        state.connected_at = null;
        state.running = false;
        state.connection_state = 'disconnected';
        state.phase = 'stopped';
        if (reason) {
            state.last_error = 'minecraft_disconnected';
        }
        writeStatus();
    });
    bot.on('error', () => {
        state.last_error = 'minecraft_runtime_error';
        writeStatus();
    });

    const timer = setInterval(writeStatus, 1000);
    timer.unref();
    process.once('SIGTERM', () => {
        state.running = false;
        state.connected = false;
        state.connected_at = null;
        state.connection_state = 'stopping';
        state.phase = 'stopped';
        writeStatus();
    });
    writeStatus();
}
