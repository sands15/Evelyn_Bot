import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {createRequire} from 'node:module';
import Vec3 from 'vec3';
import trackerModule from '@nxg-org/mineflayer-tracker';
import {ActionManager} from '/app/mindcraft/src/agent/action_manager.js';
import {initModes} from '/app/mindcraft/src/agent/modes.js';
import {
    contentFreeSurvivalState,
    install12111VelocityCompatibility,
} from '/app/mindcraft/src/utils/evelyn_runtime.js';
import {
    combatEpisodeOutcome,
    createEvelynSurvivalMode,
    hostileEscapeAlreadyStable,
    imminentProjectileThreat,
    projectileLateralDirection,
    startHostilePreemptionReflex,
    startProjectileDefenseReflex,
    trackCombatDeath,
} from '/app/mindcraft/src/agent/evelyn_survival_mode.js';

const require = createRequire(import.meta.url);
const [readLpVec3, writeLpVec3, sizeOfLpVec3] = require(
    '/app/mindcraft/node_modules/minecraft-protocol/src/datatypes/lpVec3.js'
);
const {fromNotchVelocity} = require('/app/mindcraft/node_modules/mineflayer/lib/conversions.js');
const {ProjectileTracker} = trackerModule;

test('1.21.11 vanilla lpVec3 payloads decode and round-trip byte-exactly', () => {
    const payloads = ['f9ff7ffeebed', '59e7800cebed', '51e880011541', '09e98000d8fd'];
    const sample = readLpVec3(Buffer.from(payloads[0], 'hex'), 0);
    assert.equal(sample.size, 6);
    assert.ok(Math.abs(sample.value.y - -0.0783739241897089) < 1e-9);
    assert.ok(Math.abs(sample.value.x) < 1e-12);
    assert.ok(Math.abs(sample.value.z) < 1e-12);

    for (const payload of payloads) {
        const captured = Buffer.from(payload, 'hex');
        const decoded = readLpVec3(captured, 0);
        const encoded = Buffer.alloc(sizeOfLpVec3(decoded.value));
        const end = writeLpVec3(decoded.value, encoded, 0);
        assert.equal(decoded.size, captured.length);
        assert.equal(end, encoded.length);
        assert.deepEqual(encoded, captured);
    }
});

test('1.21.11 velocity compatibility restores projectile and knockback units', () => {
    const client = new EventEmitter();
    const arrow = {
        id: 42,
        name: 'arrow',
        position: new Vec3(8.5, 65.65, 0.5),
        velocity: new Vec3(0, 0, 0),
        isValid: true,
    };
    const bot = Object.assign(new EventEmitter(), {
        version: '1.21.11',
        _client: client,
        entity: {position: new Vec3(0.5, 64, 0.5), height: 1.8, width: 0.6},
        blockAt: () => null,
    });
    let tracker;
    let velocityAfterCore;
    bot.once('inject_allowed', () => {
        bot.entities = {};
        client.on('spawn_entity', (packet) => {
            bot.entities[packet.entityId] = arrow;
        });
        client.on('entity_velocity', (packet) => {
            const velocity = new Vec3(packet.velocity.x, packet.velocity.y, packet.velocity.z);
            arrow.velocity.update(fromNotchVelocity(velocity));
            velocityAfterCore = arrow.velocity.clone();
        });
        tracker = new ProjectileTracker(bot);
    });

    assert.equal(install12111VelocityCompatibility(bot), true);
    bot.emit('inject_allowed');
    assert.equal(tracker.getIncomingProjectiles().length, 0);
    client.emit('spawn_entity', {
        entityId: arrow.id,
        velocity: {x: -1.2, y: 0, z: 0},
    });

    assert.deepEqual(arrow.velocity, new Vec3(-1.2, 0, 0));
    const incoming = tracker.getIncomingProjectiles();
    assert.equal(incoming.length, 1);
    assert.equal(incoming[0].shotInfo.nearestDistance, 0);
    assert.equal(incoming[0].shotInfo.totalTicks, 7);

    const knockback = new Vec3(-0.8, 0.2, 0.1);
    client.emit('entity_velocity', {entityId: arrow.id, velocity: knockback});
    assert.deepEqual(velocityAfterCore, fromNotchVelocity(knockback));
    assert.deepEqual(arrow.velocity, knockback);

    bot.version = '1.21.8';
    const legacyVelocity = new Vec3(800, 1600, -800);
    client.emit('entity_velocity', {entityId: arrow.id, velocity: legacyVelocity});
    assert.deepEqual(arrow.velocity, fromNotchVelocity(legacyVelocity));
});

function hostileBot() {
    const controls = [];
    const bot = Object.assign(new EventEmitter(), {
        entity: {position: new Vec3(0, 64, 0), isInWater: false},
        entities: {
            1: {id: 1, name: 'zombie', type: 'hostile', position: new Vec3(6, 64, 0)},
        },
        health: 20,
        food: 20,
        oxygenLevel: 20,
        interrupt_code: false,
        registry: {foodsByName: {}, blocksByName: {}},
        inventory: {items: () => [], slots: []},
        version: '1.21.11',
        evelynCombatHistory: [],
        evelynCombatHistoryLoaded: true,
        evelynCombatHistoryLoading: Promise.resolve(),
        evelynCombatHistoryWriter: {enqueue: async () => {}, flush: async () => {}},
        pathfinder: {
            stop() {},
            setMovements() {},
            setGoal() {},
        },
        pvp: {stop() {}},
        collectBlock: {cancelTask() {}},
        stopDigging() {},
        clearControlStates() {},
        async lookAt() {},
        setControlState(name, enabled) { controls.push([name, enabled]); },
        blockAt(position) {
            return position.y <= 63
                ? {name: 'stone', boundingBox: 'block', position}
                : {name: 'air', boundingBox: 'empty', position};
        },
    });
    return {bot, controls};
}

function installActionManager(agent, {pollMs = 50, stopWaitMs = 250} = {}) {
    agent.bot.output = '';
    agent.clearBotLogs = () => {
        agent.bot.output = '';
        agent.bot.interrupt_code = false;
    };
    agent.actions = new ActionManager(agent);
    agent.actions.interruptPollMs = pollMs;
    agent.actions.interruptStopWaitMs = stopWaitMs;
    return agent.actions;
}

async function executeThroughActionManager(mode, agent, callback, onCallback = () => {}) {
    mode.active = true;
    try {
        return await agent.actions.runAction(`mode:${mode.name}`, async () => {
            onCallback();
            await callback();
        }, {timeout: -1});
    } finally {
        mode.active = false;
    }
}

test('generic low-health self-preservation yields to emergency melee while fire still preempts', async () => {
    let blockName = 'air';
    let interrupts = 0;
    const bot = Object.assign(new EventEmitter(), {
        entity: {position: new Vec3(0, 64, 0)},
        health: 7,
        lastDamageTaken: 3,
        lastDamageTime: Date.now(),
        interrupt_code: false,
        evelynSurvivalState: {phase: 'handle_hostile'},
        inventory: {findInventoryItem: () => null},
        blockAt(position) {
            return {name: blockName, position};
        },
        clearControlStates() {},
    });
    const agent = {
        bot,
        shut_up: true,
        isIdle: () => false,
        prompter: {getInitModes: () => null},
        self_prompter: {isActive: () => false},
        requestInterrupt() {
            interrupts += 1;
            bot.interrupt_code = true;
        },
        clearBotLogs() {
            bot.interrupt_code = false;
        },
        cleanKill() {},
    };
    agent.actions = new ActionManager(agent);
    agent.actions.executing = true;
    agent.actions.currentActionLabel = 'mode:evelyn_survival';
    agent.actions.interruptPollMs = 2;
    agent.actions.interruptStopWaitMs = 10;
    initModes(agent);
    for (const name of Object.keys(bot.modes.getJson())) {
        bot.modes.setOn(name, name === 'self_preservation');
    }

    bot.health = 4;
    await bot.modes.update();
    assert.equal(interrupts, 0);
    assert.equal(agent.actions.currentActionLabel, 'mode:evelyn_survival');

    blockName = 'fire';
    await bot.modes.update();
    assert.ok(interrupts > 0);
});

test('busy planner handoff starts bounded hostile reflex before serialized callback', async () => {
    const {bot, controls} = hostileBot();
    let interrupts = 0;
    const selectedAt = Date.now() - 5;
    const reflex = startHostilePreemptionReflex({
        bot,
        requestInterrupt() {
            interrupts += 1;
            bot.interrupt_code = true;
            bot.pathfinder.stop();
        },
    }, {surfaceY: 63}, selectedAt);

    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(interrupts, 1);
    assert.ok(controls.some(([name, enabled]) => name === 'sprint' && enabled));
    assert.ok(bot.evelynSurvivalState.decision_to_action_ms >= 0);
    assert.equal(bot.evelynSurvivalState.reflex_reason, 'hostile');
    assert.ok(bot.evelynSurvivalState.reflex_to_action_ms >= 5);
    assert.ok(bot.evelynSurvivalState.last_reflex_at > 0);

    bot.entities = {};
    const result = await reflex;
    assert.equal(result.success, true);
    assert.equal(bot.evelynMovementOwner, null);
    assert.deepEqual(controls.slice(-3), [
        ['forward', false],
        ['sprint', false],
        ['jump', false],
    ]);
});

test('busy hostile P0 hands an event storm to one full tactical action', async () => {
    const {bot, controls} = hostileBot();
    bot.evelynSurvivalState = {last_success: false, last_error: 'stale'};
    let serializedCallback = null;
    let tacticalStarts = 0;
    let interrupts = 0;
    let releaseExecution;
    const queuedExecution = new Promise((resolve) => { releaseExecution = resolve; });
    const mode = createEvelynSurvivalMode({execute(_mode, _agent, callback) {
        tacticalStarts += 1;
        serializedCallback = callback;
        return queuedExecution;
    }});
    const agent = {
        bot,
        isIdle: () => false,
        requestInterrupt() {
            interrupts += 1;
            bot.interrupt_code = true;
        },
        goal_manager: {requestPriorityGoal() {}},
    };

    await mode.update(agent);
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(serializedCallback, null);
    assert.equal(mode.inFlight, false);
    assert.ok(mode.hostileReflexPromise);
    assert.equal(bot.evelynSurvivalState.last_success, null);
    assert.equal(bot.evelynSurvivalState.last_error, null);
    assert.ok(Number.isInteger(bot.evelynSurvivalState.action_started_at_ms));
    assert.ok(controls.some(([name, enabled]) => name === 'forward' && enabled));

    const hostile = bot.entities[1];
    delete bot.entities[hostile.id];
    bot.emit('entityGone', hostile);
    await mode.hostileReflexPromise;
    assert.equal(mode.hostileReflexHandoffPending, true);

    bot.entities[hostile.id] = hostile;
    bot.emit('entitySpawn', hostile);
    for (let index = 0; index < 3; index += 1) {
        hostile.position.x = 12;
        bot.emit('entityMoved', hostile);
        hostile.position.x = 6;
        bot.emit('entityMoved', hostile);
    }
    assert.equal(mode.hostileReflexPromise, null);
    assert.equal(interrupts, 1);

    await mode.update(agent);
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(tacticalStarts, 1);
    assert.equal(typeof serializedCallback, 'function');
    assert.equal(mode.currentDecision, 'handle_hostile');
    assert.equal(mode.inFlight, true);
    assert.equal(mode.hostileReflexHandoffPending, true);
    const admissionGuard = mode.hostileReflexPromise;
    assert.ok(admissionGuard);
    assert.equal(interrupts, 2);
    const guardActionStartedAt = bot.evelynSurvivalState.action_started_at_ms;
    const guardReflexAt = bot.evelynSurvivalState.last_reflex_at;

    await new Promise((resolve) => setTimeout(resolve, 5));
    const tacticalExecution = serializedCallback();
    assert.equal(mode.hostileReflexHandoffPending, true);
    bot.entities = {};
    await tacticalExecution;
    assert.equal(mode.hostileReflexHandoffPending, false);
    assert.equal(mode.hostileReflexPromise, null);
    assert.ok(bot.evelynSurvivalState.action_started_at_ms > guardActionStartedAt);
    assert.ok(bot.evelynSurvivalState.decision_to_action_ms > 0);
    assert.equal(bot.evelynSurvivalState.last_reflex_at, guardReflexAt);
    assert.equal(bot.evelynSurvivalState.reflex_reason, 'hostile');
    releaseExecution();
    await new Promise((resolve) => setTimeout(resolve, 0));
    bot.emit('end');
});

test('one ActionManager stop poll stays guarded until the tactical callback enters', async () => {
    const {bot} = hostileBot();
    bot.entities = {};
    let callbackEntries = 0;
    let managerPolls = 0;
    let managerExecution = null;
    let actions = null;
    const mode = createEvelynSurvivalMode({execute(modeState, agentState, callback) {
        managerExecution = executeThroughActionManager(
            modeState,
            agentState,
            callback,
            () => { callbackEntries += 1; },
        );
        return managerExecution;
    }});
    const agent = {
        bot,
        isIdle: () => true,
        requestInterrupt() {
            bot.interrupt_code = true;
            if (
                actions?.executing &&
                actions.currentActionLabel === 'action:fixture' &&
                managerPolls === 0
            ) {
                managerPolls += 1;
                setTimeout(() => { actions.executing = false; }, 0);
            }
        },
        goal_manager: {requestPriorityGoal() {}},
    };
    actions = installActionManager(agent);

    await mode.update(agent);
    const hostile = {id: 11, name: 'zombie', type: 'hostile', position: new Vec3(6, 64, 0)};
    bot.entities[hostile.id] = hostile;
    mode.hostileReflexHandoffPending = true;
    mode.urgent = true;
    mode.wakeReason = 'fallback_poll';
    mode.wakeReceivedAt = Date.now();
    actions.executing = true;
    actions.currentActionLabel = 'action:fixture';

    await mode.update(agent);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const admissionGuard = mode.hostileReflexPromise;
    assert.ok(admissionGuard);
    assert.equal(callbackEntries, 0);
    assert.equal(managerPolls, 1);

    bot.entities = {};
    await managerExecution;
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(callbackEntries, 1);
    assert.equal(mode.hostileReflexHandoffPending, false);
    assert.ok(bot.evelynSurvivalState.decision_to_action_ms >= 50);
    await admissionGuard;
    bot.emit('end');
});

test('idle event P0 hands a persistent close hostile to full tactical handling', async () => {
    const {bot, controls} = hostileBot();
    bot.entities = {};
    let serializedCallback = null;
    let releaseExecution;
    const queuedExecution = new Promise((resolve) => { releaseExecution = resolve; });
    const mode = createEvelynSurvivalMode({execute(_mode, _agent, callback) {
        serializedCallback = callback;
        return queuedExecution;
    }});
    const agent = {
        bot,
        isIdle: () => true,
        requestInterrupt() { bot.interrupt_code = true; },
        goal_manager: {requestPriorityGoal() {}},
    };
    await mode.update(agent);

    const hostile = {id: 2, name: 'zombie', type: 'hostile', position: new Vec3(6, 64, 0)};
    bot.entities[hostile.id] = hostile;
    bot.emit('entitySpawn', hostile);
    bot.emit('entityMoved', hostile);
    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.ok(mode.hostileReflexPromise);
    assert.equal(bot.evelynSurvivalState.phase, 'handle_hostile');
    assert.equal(bot.evelynSurvivalState.decision_to_action_ms, 0);
    assert.ok(controls.some(([name, enabled]) => name === 'sprint' && enabled));

    delete bot.entities[hostile.id];
    bot.emit('entityGone', hostile);
    await mode.hostileReflexPromise;
    assert.equal(bot.evelynMovementOwner, null);

    bot.entities[hostile.id] = hostile;
    bot.emit('entitySpawn', hostile);
    await mode.update(agent);
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(typeof serializedCallback, 'function');
    assert.equal(mode.currentDecision, 'handle_hostile');
    assert.equal(mode.hostileReflexHandoffPending, true);
    assert.ok(mode.hostileReflexPromise);

    const tacticalExecution = serializedCallback();
    bot.entities = {};
    await tacticalExecution;
    assert.equal(mode.hostileReflexHandoffPending, false);
    releaseExecution();
    await new Promise((resolve) => setTimeout(resolve, 0));
    bot.emit('end');
});

test('hostile P0 suppression clears when reassessment finds no tactical threat', async () => {
    const {bot} = hostileBot();
    bot.entities = {};
    let interrupts = 0;
    const mode = createEvelynSurvivalMode({execute() {}});
    const agent = {
        bot,
        isIdle: () => false,
        requestInterrupt() {
            interrupts += 1;
            bot.interrupt_code = true;
        },
        goal_manager: {requestPriorityGoal() {}},
    };
    await mode.update(agent);
    mode.hostileReflexHandoffPending = true;
    mode.urgent = true;
    mode.wakeReason = 'fallback_poll';
    mode.wakeReceivedAt = Date.now();

    await mode.update(agent);
    assert.equal(mode.hostileReflexHandoffPending, false);

    const hostile = {id: 3, name: 'zombie', type: 'hostile', position: new Vec3(6, 64, 0)};
    bot.entities[hostile.id] = hostile;
    bot.emit('entitySpawn', hostile);
    assert.ok(mode.hostileReflexPromise);
    assert.equal(interrupts, 1);
    bot.entities = {};
    await mode.hostileReflexPromise;
    bot.emit('end');
});

test('disconnect consumes a pending handoff and a late P0 cannot restore it', async () => {
    const {bot} = hostileBot();
    const mode = createEvelynSurvivalMode({execute() {}});
    const agent = {
        bot,
        isIdle: () => false,
        requestInterrupt() { bot.interrupt_code = true; },
        goal_manager: {requestPriorityGoal() {}},
    };

    await mode.update(agent);
    const activeReflex = mode.hostileReflexPromise;
    assert.ok(activeReflex);
    bot.emit('end');
    assert.equal(mode.hostileReflexHandoffPending, false);

    bot.entities = {};
    await activeReflex;
    assert.equal(mode.hostileReflexHandoffPending, false);
    assert.equal(mode.hostileReflexPromise, null);
});

test('a non-hostile decision clears stale handoff suppression before a new hostile wake', async () => {
    const {bot} = hostileBot();
    bot.entities = {};
    bot.time = {timeOfDay: 6000};
    let interrupts = 0;
    let releaseExecution;
    const queuedExecution = new Promise((resolve) => { releaseExecution = resolve; });
    const mode = createEvelynSurvivalMode({execute() {
        return queuedExecution;
    }});
    const agent = {
        bot,
        isIdle: () => true,
        requestInterrupt() {
            interrupts += 1;
            bot.interrupt_code = true;
        },
        goal_manager: {requestPriorityGoal() {}},
    };
    await mode.update(agent);
    bot.time.timeOfDay = 12000;
    mode.hostileReflexHandoffPending = true;
    mode.urgent = true;
    mode.wakeReason = 'fallback_poll';
    mode.wakeReceivedAt = Date.now();

    await mode.update(agent);
    assert.equal(mode.currentDecision, 'shelter_until_safe_dawn');
    assert.equal(mode.inFlight, true);
    assert.equal(mode.hostileReflexHandoffPending, false);

    const hostile = {id: 4, name: 'zombie', type: 'hostile', position: new Vec3(6, 64, 0)};
    bot.entities[hostile.id] = hostile;
    bot.emit('entitySpawn', hostile);
    assert.equal(interrupts, 1);
    releaseExecution();
    await new Promise((resolve) => setTimeout(resolve, 0));
    bot.emit('end');
});

test('surface escape yields to health and hostile wakes before tactical reassessment', async () => {
    const {bot} = hostileBot();
    bot.entities = {};
    let interrupts = 0;
    let tacticalCallback = null;
    let releaseExecution;
    const queuedExecution = new Promise((resolve) => { releaseExecution = resolve; });
    const mode = createEvelynSurvivalMode({execute(_mode, _agent, callback) {
        tacticalCallback = callback;
        return queuedExecution;
    }});
    const agent = {
        bot,
        isIdle: () => true,
        requestInterrupt() {
            interrupts += 1;
            bot.interrupt_code = true;
        },
        goal_manager: {requestPriorityGoal() {}},
    };
    await mode.update(agent);
    mode.inFlight = true;
    mode.currentDecision = 'escape_to_surface';

    bot.health = 18;
    bot.emit('health');
    const hostile = {id: 5, name: 'zombie', type: 'hostile', position: new Vec3(6, 64, 0)};
    bot.entities[hostile.id] = hostile;
    bot.emit('entitySpawn', hostile);
    assert.equal(interrupts, 2);

    mode.inFlight = false;
    mode.currentDecision = null;
    bot.interrupt_code = false;
    await mode.update(agent);
    assert.equal(mode.currentDecision, 'handle_hostile');
    assert.equal(typeof tacticalCallback, 'function');

    bot.interrupt_code = true;
    await tacticalCallback();
    releaseExecution();
    await new Promise((resolve) => setTimeout(resolve, 0));
    bot.emit('end');
});

test('resolved ActionManager denial keeps a static close hostile guarded across cooldown retries', async () => {
    const {bot} = hostileBot();
    const hostile = bot.entities[1];
    bot.entities = {};
    let attempts = 0;
    let callbackEntries = 0;
    const executions = [];
    const mode = createEvelynSurvivalMode({execute(modeState, agentState, callback) {
        attempts += 1;
        const execution = executeThroughActionManager(
            modeState,
            agentState,
            callback,
            () => { callbackEntries += 1; },
        );
        executions.push(execution);
        return execution;
    }});
    const agent = {
        bot,
        isIdle: () => true,
        requestInterrupt() { bot.interrupt_code = true; },
        goal_manager: {requestPriorityGoal() {}},
    };
    const actions = installActionManager(agent);

    await mode.update(agent);
    hostile.position.x = 6;
    bot.entities[hostile.id] = hostile;
    mode.hostileReflexHandoffPending = true;
    mode.cooldownUntil.handle_hostile = Date.now() + 60000;
    mode.urgent = true;
    mode.wakeReason = 'fallback_poll';
    mode.wakeReceivedAt = Date.now();
    actions.executing = true;
    actions.currentActionLabel = 'action:stuck_fixture';

    await mode.update(agent);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const firstGuard = mode.hostileReflexPromise;
    assert.ok(firstGuard);
    await executions[0];
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(attempts, 1);
    assert.equal(callbackEntries, 0);
    assert.equal(mode.inFlight, false);
    assert.equal(mode.currentDecision, null);
    assert.equal(mode.hostileReflexHandoffPending, true);
    assert.equal(mode.urgent, true);
    assert.equal(mode.hostileReflexPromise, firstGuard);

    delete bot.entities[hostile.id];
    bot.emit('entityGone', hostile);
    await firstGuard;
    assert.equal(mode.hostileReflexHandoffPending, true);

    bot.entities[hostile.id] = hostile;
    await mode.update(agent);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const secondGuard = mode.hostileReflexPromise;
    assert.ok(secondGuard);
    await executions[1];
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(attempts, 2);
    assert.equal(callbackEntries, 0);
    assert.equal(mode.inFlight, false);
    assert.equal(mode.hostileReflexHandoffPending, true);
    assert.equal(mode.urgent, true);
    delete bot.entities[hostile.id];
    bot.emit('end');
    await secondGuard;
});

test('an immediate admission-guard failure yields to a deferred tactical callback', async () => {
    const {bot} = hostileBot();
    bot.entities = {};
    let callbackEntered = false;
    let executionDone = null;
    let interrupts = 0;
    const mode = createEvelynSurvivalMode({execute(_mode, _agent, callback) {
        executionDone = new Promise((resolve, reject) => {
            setTimeout(() => {
                callbackEntered = true;
                Promise.resolve(callback()).then(resolve, reject);
            }, 20);
        });
        return executionDone;
    }});
    const agent = {
        bot,
        isIdle: () => false,
        requestInterrupt() {
            interrupts += 1;
            bot.interrupt_code = true;
        },
        goal_manager: {requestPriorityGoal() {}},
    };
    await mode.update(agent);
    const hostile = {id: 6, name: 'zombie', type: 'hostile', position: new Vec3(6, 64, 0)};
    bot.entities[hostile.id] = hostile;
    bot.evelynMovementOwner = 'fixture_other_owner';
    mode.hostileReflexHandoffPending = true;
    mode.urgent = true;
    mode.wakeReason = 'fallback_poll';
    mode.wakeReceivedAt = Date.now();

    await mode.update(agent);
    await executionDone;
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(callbackEntered, true);
    assert.equal(interrupts, 1);
    assert.equal(mode.inFlight, false);
    assert.equal(mode.hostileReflexHandoffPending, false);
    bot.evelynMovementOwner = null;
    bot.emit('end');
});

test('projectile completion restores the pending close-hostile admission guard', async () => {
    const {bot} = hostileBot();
    bot.entities = {};
    const shield = {name: 'shield'};
    const slots = [];
    slots[45] = shield;
    bot.inventory = {items: () => [shield], slots};
    bot.getEquipmentDestSlot = () => 45;
    bot.supportFeature = () => false;
    bot.util = {entity: {isOffHandActive: () => false}};
    bot.activateItem = () => {};
    bot.deactivateItem = () => {};
    const threat = {
        entity: {
            id: 7,
            name: 'arrow',
            position: new Vec3(4, 65, 0),
            velocity: new Vec3(-1, 0, 0),
        },
        shotInfo: {totalTicks: 1},
    };
    bot.projectiles = {projectileAtMe: threat};
    let tacticalCallback = null;
    let releaseExecution;
    let interrupts = 0;
    const queuedExecution = new Promise((resolve) => { releaseExecution = resolve; });
    const mode = createEvelynSurvivalMode({execute(_mode, _agent, callback) {
        tacticalCallback = callback;
        return queuedExecution;
    }});
    const agent = {
        bot,
        isIdle: () => false,
        requestInterrupt() {
            interrupts += 1;
            bot.interrupt_code = true;
        },
        goal_manager: {requestPriorityGoal() {}},
    };

    await mode.update(agent);
    const hostile = {id: 8, name: 'zombie', type: 'hostile', position: new Vec3(12, 64, 0)};
    bot.entities[hostile.id] = hostile;
    mode.hostileReflexHandoffPending = true;
    bot.emit('entitySpawn', hostile);
    await mode.update(agent);
    assert.equal(typeof tacticalCallback, 'function');

    bot.emit('physicsTick');
    const projectileReflex = mode.hostileReflexPromise;
    assert.ok(projectileReflex);
    hostile.position.x = 6;
    bot.emit('entityMoved', hostile);
    assert.equal(mode.hostileReflexPromise, projectileReflex);

    await projectileReflex;
    await new Promise((resolve) => setTimeout(resolve, 60));
    const hostileGuard = mode.hostileReflexPromise;
    assert.ok(hostileGuard);
    assert.notEqual(hostileGuard, projectileReflex);
    assert.ok(interrupts >= 2);

    const tacticalExecution = tacticalCallback();
    bot.entities = {};
    await tacticalExecution;
    assert.equal(mode.hostileReflexHandoffPending, false);
    releaseExecution();
    await hostileGuard;
    bot.emit('end');
});

test('delayed tactical admission rebuilds a swapped hostile snapshot', async () => {
    const {bot} = hostileBot();
    bot.entities = {};
    let tacticalCallback = null;
    let releaseExecution;
    const queuedExecution = new Promise((resolve) => { releaseExecution = resolve; });
    const mode = createEvelynSurvivalMode({execute(_mode, _agent, callback) {
        tacticalCallback = callback;
        return queuedExecution;
    }});
    const agent = {
        bot,
        isIdle: () => true,
        requestInterrupt() { bot.interrupt_code = true; },
        goal_manager: {requestPriorityGoal() {}},
    };

    await mode.update(agent);
    bot.entities = {
        9: {id: 9, name: 'zombie', type: 'hostile', position: new Vec3(12, 64, 0)},
    };
    mode.hostileReflexHandoffPending = true;
    mode.urgent = true;
    mode.wakeReason = 'fallback_poll';
    mode.wakeReceivedAt = Date.now();
    await mode.update(agent);
    assert.equal(typeof tacticalCallback, 'function');

    bot.entities = {
        10: {id: 10, name: 'skeleton', type: 'hostile', position: new Vec3(6, 64, 0)},
    };
    const tacticalExecution = tacticalCallback();
    bot.entities = {};
    await tacticalExecution;

    assert.equal(bot.evelynSurvivalState.hostile_before.hostileName, 'skeleton');
    assert.equal(bot.evelynSurvivalState.hostile_before.hostileId, 10);
    assert.equal(bot.evelynSurvivalState.hostile_before.hostileDistance, 6);
    assert.equal(mode.hostileReflexHandoffPending, false);
    releaseExecution();
    await new Promise((resolve) => setTimeout(resolve, 0));
    bot.emit('end');
});

test('death tracking is cleaned up and overrides timeout classification', () => {
    const bot = new EventEmitter();
    const tracker = trackCombatDeath(bot);
    bot.emit('death');
    assert.equal(tracker.stop(), true);
    assert.equal(bot.listenerCount('death'), 0);
    assert.equal(combatEpisodeOutcome({
        tactic: 'flee',
        timedOut: true,
        health: 20,
        success: false,
        died: true,
    }), 'death');
});

test('death during direct reflex is retained as non-promoted combat evidence', async () => {
    const {bot} = hostileBot();
    const reflex = startHostilePreemptionReflex({
        bot,
        requestInterrupt() { bot.interrupt_code = true; },
    }, {surfaceY: 63}, Date.now());

    await new Promise((resolve) => setTimeout(resolve, 5));
    bot.health = 0;
    bot.emit('health');
    bot.emit('death');
    await reflex;

    const episode = bot.evelynCombatHistory.at(-1);
    assert.equal(episode.outcome, 'death');
    assert.equal(episode.verified, false);
    assert.equal(bot.listenerCount('death'), 0);
});

test('installed tracker signal is admitted only inside the imminent tick window', () => {
    const {bot} = hostileBot();
    const threat = {
        entity: {
            name: 'arrow',
            position: new Vec3(4, 65, 0),
            velocity: new Vec3(-1, 0, 0),
        },
        shotInfo: {totalTicks: 14},
    };
    bot.projectiles = {projectileAtMe: threat};
    assert.equal(imminentProjectileThreat(bot), threat);
    threat.shotInfo.totalTicks = 15;
    assert.equal(imminentProjectileThreat(bot), null);
    threat.shotInfo.totalTicks = 4;
    assert.deepEqual(projectileLateralDirection(bot, threat), {x: 0, z: -1});

    Object.defineProperty(bot.projectiles, 'projectileAtMe', {get() { throw new Error('tracker probe failed'); }});
    assert.equal(imminentProjectileThreat(bot), null);
});

test('incoming projectile selection uses earliest valid tracker impact before the cached getter', () => {
    const {bot} = hostileBot();
    const incoming = (id, ticks) => ({
        entity: {
            id,
            name: 'arrow',
            position: new Vec3(4 + id, 65, 0),
            velocity: new Vec3(-1, 0, 0),
        },
        shotInfo: {totalTicks: ticks},
    });
    const later = incoming(1, 9);
    const sooner = incoming(2, 3);
    let getterReads = 0;
    bot.projectiles = {
        getIncomingProjectiles: () => [later, incoming(3, Number.NaN), sooner],
        get projectileAtMe() {
            getterReads += 1;
            return incoming(4, 1);
        },
    };

    assert.equal(imminentProjectileThreat(bot), sooner);
    assert.equal(getterReads, 0);
    bot.projectiles.getIncomingProjectiles = () => { throw new Error('list unavailable'); };
    assert.equal(imminentProjectileThreat(bot).shotInfo.totalTicks, 1);
    assert.equal(getterReads, 1);
});

test('public reflex telemetry keeps P0 start latency separate from full tactical latency', () => {
    const projected = contentFreeSurvivalState({
        phase: 'handle_hostile',
        wake_reason: 'hostile_spawn',
        wake_to_decision_ms: 1116,
        decision_to_action_ms: 1,
        reflex_reason: 'hostile',
        reflex_to_action_ms: 4.4,
        bootstrap_phase: 'collect_finished',
        bootstrap_candidate_count: 1,
        bootstrap_logs_before: 2,
        bootstrap_logs_after: 3,
        shelter_success_count: 7,
        last_decision: 'shelter_until_safe_dawn',
        recovery_verification: 'shelter_context_unsafe',
        last_reflex_at: 123.5,
        private_snapshot: {position: 'not projected'},
    });

    assert.equal(projected.wake_to_decision_ms, 1116);
    assert.equal(projected.reflex_reason, 'hostile');
    assert.equal(projected.reflex_to_action_ms, 4);
    assert.equal(projected.bootstrap_phase, 'collect_finished');
    assert.equal(projected.bootstrap_candidate_count, 1);
    assert.equal(projected.bootstrap_logs_before, 2);
    assert.equal(projected.bootstrap_logs_after, 3);
    assert.equal(projected.shelter_success_count, 7);
    assert.equal(projected.shelter_verification, 'shelter_context_unsafe');
    assert.equal(projected.last_reflex_at, 123.5);
    assert.equal(contentFreeSurvivalState({reflex_reason: 'private'}).reflex_reason, null);
    const rejectedBootstrap = contentFreeSurvivalState({
        bootstrap_phase: 'oak_log@8,100,0',
        bootstrap_candidate_count: 5,
        bootstrap_logs_before: -1,
        bootstrap_logs_after: 65,
        shelter_success_count: -1,
        target: 'not projected',
    });
    assert.equal(rejectedBootstrap.bootstrap_phase, null);
    assert.equal(rejectedBootstrap.bootstrap_candidate_count, null);
    assert.equal(rejectedBootstrap.bootstrap_logs_before, null);
    assert.equal(rejectedBootstrap.bootstrap_logs_after, null);
    assert.equal(rejectedBootstrap.shelter_success_count, null);
    assert.equal(Object.hasOwn(rejectedBootstrap, 'target'), false);
    assert.equal(contentFreeSurvivalState({
        last_decision: 'shelter_until_safe_dawn',
        recovery_verification: 'private_position_1_2_3',
    }).shelter_verification, null);
    assert.equal(contentFreeSurvivalState({
        last_decision: 'acquire_food',
        recovery_verification: 'shelter_context_unsafe',
    }).shelter_verification, null);
    assert.equal(contentFreeSurvivalState({
        last_decision: 'shelter_until_safe_dawn',
        recovery_verification: 'shelter_breached_missing_block',
    }).shelter_verification, 'shelter_breached_missing_block');
    assert.equal(contentFreeSurvivalState({
        last_decision: 'shelter_until_safe_dawn',
        recovery_verification: 'shelter_gather_timeout_generic_collect_not_visible',
    }).shelter_verification, 'shelter_gather_timeout_generic_collect_not_visible');
    assert.equal(hostileEscapeAlreadyStable('flee', {verification: 'stable_safe_radius'}), true);
    assert.equal(hostileEscapeAlreadyStable('flee', {verification: 'safe_radius'}), false);
    assert.equal(hostileEscapeAlreadyStable('fight', {verification: 'stable_safe_radius'}), false);
});

test('incoming projectile wake raises an offhand shield under the shared P0 single-flight', async () => {
    const {bot} = hostileBot();
    bot.entities = {};
    const shield = {name: 'shield'};
    const slots = [];
    slots[45] = shield;
    bot.inventory = {items: () => [shield], slots};
    bot.getEquipmentDestSlot = () => 45;
    bot.supportFeature = () => false;
    bot.util = {entity: {isOffHandActive: () => false}};
    let trackerReads = 0;
    const threat = {
        entity: {
            id: 9,
            name: 'arrow',
            position: new Vec3(4, 65, 0),
            velocity: new Vec3(-1, 0, 0),
        },
        shotInfo: {totalTicks: 1},
    };
    Object.defineProperty(bot, 'projectiles', {value: {
        get projectileAtMe() {
            trackerReads += 1;
            return trackerReads === 2 ? threat : null;
        },
    }});
    const activations = [];
    let pathfinderStops = 0;
    let equips = 0;
    bot.pathfinder.stop = () => { pathfinderStops += 1; };
    bot.equip = async () => { equips += 1; };
    bot.activateItem = (offhand) => activations.push(['raise', offhand]);
    bot.deactivateItem = () => activations.push(['lower']);
    let interrupts = 0;
    const mode = createEvelynSurvivalMode({execute() {}});
    const agent = {
        bot,
        isIdle: () => false,
        requestInterrupt() {
            interrupts += 1;
            bot.interrupt_code = true;
        },
        goal_manager: {requestPriorityGoal() {}},
    };
    await mode.update(agent);
    mode.hostileReflexHandoffPending = true;
    bot.emit('physicsTick');
    assert.equal(mode.hostileReflexPromise, null);
    await new Promise((resolve) => setTimeout(resolve, 55));
    bot.emit('physicsTick');
    await new Promise((resolve) => setTimeout(resolve, 0));

    const activeReflex = mode.hostileReflexPromise;
    assert.ok(activeReflex);
    assert.equal(mode.hostileReflexHandoffPending, true);
    assert.equal(interrupts, 1);
    assert.equal(bot.evelynSurvivalState.wake_reason, 'projectile');
    assert.equal(bot.evelynSurvivalState.reflex_reason, 'projectile');
    assert.ok(bot.evelynSurvivalState.reflex_to_action_ms >= 0);
    assert.ok(bot.evelynSurvivalState.last_reflex_at > 0);
    bot.emit('physicsTick');
    assert.equal(mode.hostileReflexPromise, activeReflex);

    await activeReflex;
    assert.deepEqual(activations, [['raise', true], ['lower']]);
    assert.ok(pathfinderStops >= 1);
    assert.equal(equips, 0);
    assert.equal(bot.evelynMovementOwner, undefined);
    bot.emit('end');
});

test('projectile event rejection resolves to a fixed failed reflex without an unhandled promise', async () => {
    const {bot} = hostileBot();
    bot.entities = {};
    const threat = {
        entity: {
            id: 10,
            name: 'arrow',
            position: new Vec3(4, 65, 0),
            velocity: new Vec3(-1, 0, 0),
        },
        shotInfo: {totalTicks: 1},
    };
    bot.projectiles = {projectileAtMe: threat};
    bot.setControlState = () => { throw new Error('fixture control failure'); };
    const mode = createEvelynSurvivalMode({execute() {}});
    const agent = {
        bot,
        isIdle: () => false,
        requestInterrupt() { bot.interrupt_code = true; },
        goal_manager: {requestPriorityGoal() {}},
    };
    await mode.update(agent);

    bot.emit('physicsTick');
    const activeReflex = mode.hostileReflexPromise;
    assert.ok(activeReflex);
    assert.deepEqual(await activeReflex, {
        success: false,
        strategy: 'projectile_defense',
        verification: 'projectile_reflex_error',
    });
    assert.equal(mode.hostileReflexPromise, null);
    bot.emit('end');
});

test('hostile event tail failure resolves to a fixed failed reflex without an unhandled promise', async () => {
    const {bot} = hostileBot();
    bot.entities = {};
    bot.evelynCombatHistoryWriter.enqueue = () => undefined;
    const mode = createEvelynSurvivalMode({execute() {}});
    const agent = {
        bot,
        isIdle: () => false,
        requestInterrupt() { bot.interrupt_code = true; },
        goal_manager: {requestPriorityGoal() {}},
    };
    await mode.update(agent);

    const hostile = {id: 12, name: 'zombie', type: 'hostile', position: new Vec3(6, 64, 0)};
    bot.entities[hostile.id] = hostile;
    bot.emit('entitySpawn', hostile);
    const activeReflex = mode.hostileReflexPromise;
    assert.ok(activeReflex);
    bot.entities = {};

    assert.deepEqual(await activeReflex, {
        success: false,
        strategy: 'hostile_preemption_reflex',
        verification: 'reflex_error',
    });
    assert.equal(mode.hostileReflexPromise, null);
    assert.equal(mode.hostileReflexHandoffPending, true);
    bot.emit('end');
});

test('projectile defense without a usable shield reuses collision-checked lateral escape', async () => {
    const {bot, controls} = hostileBot();
    bot.entities = {};
    const shield = {name: 'shield'};
    let equips = 0;
    bot.inventory = {items: () => [shield], slots: []};
    bot.getEquipmentDestSlot = () => 45;
    bot.supportFeature = () => false;
    bot.equip = async () => { equips += 1; };
    const threat = {
        entity: {
            id: 10,
            name: 'arrow',
            position: new Vec3(4, 65, 0),
            velocity: new Vec3(-1, 0, 0),
        },
        shotInfo: {totalTicks: 3},
    };
    bot.projectiles = {projectileAtMe: threat};
    const result = await startProjectileDefenseReflex({
        bot,
        isIdle: () => false,
        requestInterrupt() { bot.interrupt_code = true; },
    }, threat, {escapeTimeoutMs: 20});

    assert.equal(result.strategy, 'projectile_break_los_surface');
    assert.ok(result.lastCandidate.z < 0, `expected a lateral component, got ${JSON.stringify(result.lastCandidate)}`);
    assert.ok(controls.some(([name, enabled]) => name === 'sprint' && enabled));
    assert.equal(equips, 0);
    assert.equal(bot.evelynMovementOwner, null);
});

test('projectile defense never starts a second escape under the existing P0 movement owner', async () => {
    const {bot, controls} = hostileBot();
    bot.entities = {};
    bot.evelynMovementOwner = 'evelyn_hostile_escape';
    let interrupts = 0;
    let pathfinderStops = 0;
    bot.pathfinder.stop = () => { pathfinderStops += 1; };
    const threat = {
        entity: {
            id: 11,
            name: 'arrow',
            position: new Vec3(4, 65, 0),
            velocity: new Vec3(-1, 0, 0),
        },
        shotInfo: {totalTicks: 2},
    };
    const result = await startProjectileDefenseReflex({
        bot,
        isIdle: () => false,
        requestInterrupt() { interrupts += 1; },
    }, threat);

    assert.equal(result.verification, 'escape_in_progress');
    assert.equal(bot.evelynMovementOwner, 'evelyn_hostile_escape');
    assert.equal(interrupts, 0);
    assert.equal(pathfinderStops, 0);
    assert.equal(controls.length, 0);
});
