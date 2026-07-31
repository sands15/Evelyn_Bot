import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'fs';
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

function loadInitialState(agentName) {
    try {
        const existing = JSON.parse(readFileSync(STATUS_PATH, 'utf8'));
        if (existing && typeof existing === 'object') return existing;
    } catch (_) {}
    return {
        runtime: 'mindcraft',
        agent_name: agentName,
        running: true,
        connected: false,
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
    const state = loadInitialState(agentName);
    const goalManagerMode = String(
        process.env.MINDCRAFT_GOAL_MANAGER_MODE || 'gated'
    ).trim().toLowerCase();
    state.runtime = 'mindcraft';
    state.agent_name = agentName;
    state.goal = process.env.MINDCRAFT_GOAL || state.goal || null;
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
        state.survival_controller = bot.evelynSurvivalState || state.survival_controller || null;
        state.goal_manager = bot.evelynGoalState || state.goal_manager || null;
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
                state.last_blocked_command = text.slice(0, 120);
                state.last_blocked_command_at = Date.now() / 1000;
                console.warn(
                    '[Evelyn Mindcraft] blocked outbound chat:',
                    text.trimStart().startsWith('/') ? '[slash command]' : text.slice(0, 120)
                );
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
                    state.last_blocked_command = `[whisper:${String(username || '').slice(0, 32)}]`;
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
    bot.on('kicked', (reason) => {
        state.connected = false;
        state.connection_state = 'kicked';
        state.phase = 'stopped';
        try {
            state.last_error = typeof reason === 'string' ? reason : JSON.stringify(reason);
        } catch {
            state.last_error = String(reason || 'kicked');
        }
        writeStatus();
    });
    bot.on('end', (reason) => {
        state.connected = false;
        state.running = false;
        state.connection_state = 'disconnected';
        state.phase = 'stopped';
        if (reason) {
            try {
                state.last_error = typeof reason === 'string' ? reason : JSON.stringify(reason);
            } catch {
                state.last_error = String(reason);
            }
        }
        writeStatus();
    });
    bot.on('error', (error) => {
        state.last_error = String(error?.message || error);
        writeStatus();
    });

    const timer = setInterval(writeStatus, 1000);
    timer.unref();
    process.once('SIGTERM', () => {
        state.running = false;
        state.connected = false;
        state.connection_state = 'stopping';
        state.phase = 'stopped';
        writeStatus();
    });
    writeStatus();
}
