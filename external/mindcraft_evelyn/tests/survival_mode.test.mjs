import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import Vec3 from 'vec3';
import minecraftData from 'minecraft-data';
import prismarineBlock from 'prismarine-block';
import { ActionManager } from '/app/mindcraft/src/agent/action_manager.js';
import {collectBlock} from '/app/mindcraft/src/agent/library/skills.js';
import {
    advanceHostileFleeFailureStreak,
    bootstrapTools,
    buildSurvivalSnapshot,
    acquireFood,
    foodAcquisitionAllowed,
    isSafeFoodPrey,
    classifyCombatTerrain,
    combatEpisodeOutcome,
    combatExperienceContext,
    createEvelynSurvivalMode,
    decisionCanInterrupt,
    digStaircaseStep,
    exitAfterCombatHistoryFlush,
    failureCooldownMs,
    filterMovesAtOrAbove,
    hostileHasClearLine,
    hostileFleeEscapeOptions,
    hostileIsActionable,
    listSurvivalDecisions,
    mergeSurvivalState,
    nearbyAirExitTargets,
    pathWithTimeout,
    recordCombatExperience,
    recoveryHandoffDelayMs,
    runFoodAcquisitionAction,
    runTemporaryShelterAction,
    selectBestFood,
    selectHostileTactic,
    selectSurvivalDecision,
    shelterDecisionAllowed,
    singleZombieMeleeFallbackAllowed,
    startCombatExperience,
    staircaseBaseTargets,
    staircaseTargets,
    SURVIVAL_WAKE_REASONS,
    TEMPORARY_SHELTER_BLOCK_COUNT,
    temporaryShelterLayout,
    temporaryShelterVerified,
    trackCombatDamage,
    verifyHostileOutcome,
    verifySurfaceEscape,
    walkStaircaseStep,
} from '/app/mindcraft/src/agent/evelyn_survival_mode.js';
import {
    loadCombatHistory,
    makeCombatEpisode,
    saveCombatHistoryAtomic,
} from '/app/mindcraft/src/agent/evelyn_combat_experience.js';
import {
    buildEscapeCandidates,
    chooseEscapeCandidate,
    escapeFromHostiles,
    escapeSafetyScore,
    sprintEscapeBurst,
} from '/app/mindcraft/src/agent/evelyn_escape_controller.js';

let wakeHarnessX = 1000;

test('dirt collection tolerates positionless 1.21.11 palette probes while preserving exclusions', async () => {
    const registry = minecraftData('1.21.11');
    const Block = prismarineBlock(registry);
    const stateId = registry.blocksByName.grass_block.minStateId;
    const paletteProbe = Block.fromStateId(stateId, 0);
    const excluded = new Vec3(0, 64, 0);
    assert.equal(paletteProbe.position, null);

    const bot = {
        registry,
        entities: {},
        inventory: {items: () => []},
        output: '',
        findBlocks({matching}) {
            assert.equal(matching(paletteProbe), true);
            const positionedBlock = Block.fromStateId(stateId, 0);
            positionedBlock.position = excluded;
            assert.equal(matching(positionedBlock), false);
            return [];
        },
    };

    assert.equal(await collectBlock(bot, 'dirt', 1, [excluded]), false);
});

async function createWakeHarness(execute = () => {}, options = {}) {
    const x = wakeHarnessX++;
    const position = {
        x,
        y: 64,
        z: 0,
        distanceTo(other) {
            return Math.hypot(other.x - this.x, other.y - this.y, other.z - this.z);
        },
    };
    const items = options.items || [
        {name: 'iron_pickaxe', count: 1},
        {name: 'iron_sword', count: 1},
        {name: 'bread', count: 3},
    ];
    const bot = Object.assign(new EventEmitter(), {
        entity: {position, isInWater: false},
        entities: {},
        health: 20,
        food: 20,
        oxygenLevel: 20,
        interrupt_code: false,
        time: {timeOfDay: options.timeOfDay ?? null},
        equip: async () => {},
        consume: () => new Promise(() => {}),
        blockAt(blockPosition) {
            return {name: blockPosition.y === 63 ? 'stone' : 'air', boundingBox: 'empty'};
        },
        inventory: {items: () => items, slots: items},
        registry: {foodsByName: {bread: {foodPoints: 5}}},
    });
    const agent = {bot, isIdle: () => true, goal_manager: {requestPriorityGoal() {}}};
    if (options.requestInterrupt) agent.requestInterrupt = () => options.requestInterrupt(bot);
    const mode = createEvelynSurvivalMode({execute});
    await mode.update(agent);
    return {agent, bot, mode};
}

test('survival wake listeners filter passive mobs, coalesce danger, and detach on end', async () => {
    const {bot, mode} = await createWakeHarness();
    bot.emit('entitySpawn', {id: 1, name: 'cow', position: {x: bot.entity.position.x + 1, y: 64, z: 0}});
    assert.equal(mode.urgent, false);

    const zombie = {id: 2, name: 'zombie', position: {x: bot.entity.position.x + 3, y: 64, z: 0}};
    bot.entities[zombie.id] = zombie;
    bot.emit('entitySpawn', zombie);
    const firstWakeAt = mode.wakeReceivedAt;
    bot.health = 18;
    bot.emit('health');
    assert.equal(mode.wakeReason, SURVIVAL_WAKE_REASONS.HOSTILE_SPAWN);
    assert.equal(mode.wakeReceivedAt, firstWakeAt);
    assert.equal(bot.listenerCount('entityMoved'), 1);

    bot.emit('end');
    assert.equal(bot.listenerCount('entityMoved'), 0);
    assert.equal(mode.listenerBot, null);
});

test('hostile band crossings, disappearance, and cheap fallback only mark a wake', async () => {
    const {bot, mode} = await createWakeHarness();
    const zombie = {id: 3, name: 'zombie', position: {x: bot.entity.position.x + 25, y: 64, z: 0}};
    bot.entities[zombie.id] = zombie;
    bot.emit('entitySpawn', zombie);
    assert.equal(mode.urgent, false);

    zombie.position.x = bot.entity.position.x + 7;
    bot.emit('entityMoved', zombie);
    assert.equal(mode.wakeReason, SURVIVAL_WAKE_REASONS.HOSTILE_BAND);

    mode.urgent = false;
    mode.wakeReason = null;
    mode.wakeReceivedAt = 0;
    bot.emit('entityGone', zombie);
    assert.equal(mode.wakeReason, SURVIVAL_WAKE_REASONS.HOSTILE_GONE);

    mode.urgent = false;
    mode.wakeReason = null;
    mode.wakeReceivedAt = 0;
    zombie.position.x = bot.entity.position.x + 25;
    bot.emit('entitySpawn', zombie);
    bot.entity.position.x += 10;
    bot.emit('physicsTick');
    assert.equal(mode.wakeReason, SURVIVAL_WAKE_REASONS.FALLBACK);
});

test('health wake bypasses the normal gate and publishes content-free latency', async () => {
    const {agent, bot, mode} = await createWakeHarness();
    const priorSnapshot = bot.evelynSurvivalState.snapshot;
    mode.lastCheckAt = Date.now();
    bot.health = 19;
    bot.emit('health');
    await mode.update(agent);

    assert.equal(bot.evelynSurvivalState.snapshot.health, 19);
    assert.equal(bot.evelynSurvivalState.wake_reason, SURVIVAL_WAKE_REASONS.HEALTH);
    assert.ok(Number.isInteger(bot.evelynSurvivalState.wake_received_at_ms));
    assert.ok(bot.evelynSurvivalState.wake_to_decision_ms >= 0);

    bot.health = 18;
    await mode.update(agent);
    assert.notEqual(bot.evelynSurvivalState.snapshot, priorSnapshot);
    assert.equal(bot.evelynSurvivalState.snapshot.health, 19);
});

test('the Mineflayer health event also wakes on hunger-only changes', async () => {
    const {agent, bot, mode} = await createWakeHarness();
    mode.lastCheckAt = Date.now();
    bot.food = 19;
    bot.emit('health');
    await mode.update(agent);

    assert.equal(bot.evelynSurvivalState.snapshot.hunger, 19);
    assert.equal(bot.evelynSurvivalState.wake_reason, SURVIVAL_WAKE_REASONS.HEALTH);
});

test('hostile and damage wakes request cancellation of an in-flight tool bootstrap', async () => {
    const previous = process.env.MINDCRAFT_DETERMINISTIC_TOOL_BOOTSTRAP;
    process.env.MINDCRAFT_DETERMINISTIC_TOOL_BOOTSTRAP = 'true';
    let releaseExecution;
    const pending = new Promise((resolve) => { releaseExecution = resolve; });
    let interrupts = 0;
    try {
        const {bot, mode} = await createWakeHarness(() => pending, {
            items: [
                {name: 'iron_pickaxe', count: 1},
                {name: 'bread', count: 3},
            ],
            timeOfDay: 6000,
            requestInterrupt(target) {
                interrupts += 1;
                target.interrupt_code = true;
            },
        });
        assert.equal(mode.currentDecision, 'bootstrap_tools');

        const zombie = {
            id: 71,
            name: 'zombie',
            position: {x: bot.entity.position.x + 3, y: 64, z: 0},
        };
        bot.entities[zombie.id] = zombie;
        bot.emit('entitySpawn', zombie);
        assert.equal(interrupts, 1);

        bot.interrupt_code = false;
        bot.health = 19;
        bot.emit('health');
        assert.equal(interrupts, 2);
        assert.equal(bot.interrupt_code, true);
        bot.emit('end');
    } finally {
        releaseExecution();
        await pending;
        if (previous === undefined) delete process.env.MINDCRAFT_DETERMINISTIC_TOOL_BOOTSTRAP;
        else process.env.MINDCRAFT_DETERMINISTIC_TOOL_BOOTSTRAP = previous;
    }
});

test('an event storm starts at most one survival action and records decision latency', async () => {
    let starts = 0;
    const {agent, bot, mode} = await createWakeHarness((_mode, _agent, action) => {
        starts += 1;
        action();
    });
    mode.lastCheckAt = Date.now();
    bot.health = 19;
    bot.food = 14;
    bot.oxygenLevel = 19;
    bot.emit('health');
    bot.emit('breath');
    bot.emit('health');
    await mode.update(agent);
    await mode.update(agent);

    assert.equal(starts, 1);
    assert.equal(mode.inFlight, true);
    assert.ok(Number.isInteger(bot.evelynSurvivalState.action_started_at_ms));
    assert.ok(bot.evelynSurvivalState.decision_to_action_ms >= 0);
});

test('survival snapshot ignores passive mobs near a real hostile', () => {
    const position = {
        x: 0,
        y: 64,
        z: 0,
        distanceTo(other) {
            return Math.hypot(other.x - this.x, other.y - this.y, other.z - this.z);
        },
    };
    const result = buildSurvivalSnapshot({
        entity: {position, isInWater: false},
        entities: {
            1: {id: 1, name: 'cow', position: {x: 1, y: 64, z: 0}},
            2: {id: 2, name: 'zombie', position: {x: 2, y: 64, z: 0}},
        },
        blockAt: () => ({name: 'stone'}),
        inventory: {items: () => [], slots: []},
        registry: {foodsByName: {}},
        health: 20,
        food: 20,
    });

    assert.equal(result.hostileName, 'zombie');
    assert.equal(result.hostileCount, 1);
});

test('combat experience context is typed and unsafe episodes stay content-free', async () => {
    const context = combatExperienceContext({
        health: 20,
        hostileDistance: 3,
        hostileCount: 1,
        hostileName: 'zombie',
        hostiles: [{name: 'zombie'}],
        armorPoints: 15,
        hasMeleeWeapon: true,
        hasShield: true,
        hasBow: false,
        arrowCount: 0,
        foodName: 'bread',
        inWater: false,
    });
    const writes = [];
    const bot = {
        version: '1.21.11',
        evelynCombatHistory: [],
        evelynCombatHistoryWriter: {enqueue: async (history) => writes.push(history)},
    };
    recordCombatExperience(bot, {
        context,
        tactic: 'shield_close',
        before: {health: 20},
        after: {health: 17},
        success: true,
        durationMs: 800,
    });
    await Promise.resolve();

    assert.equal(bot.evelynCombatHistory.length, 1);
    assert.equal(bot.evelynCombatHistory[0].damage, 3);
    assert.equal(bot.evelynCombatHistory[0].verified, true);
    assert.equal('position' in bot.evelynCombatHistory[0], false);
    assert.equal(writes.length, 1);
});

test('combat damage tracks health drops across healing and always detaches', () => {
    const bot = Object.assign(new EventEmitter(), {health: 20});
    const tracker = trackCombatDamage(bot);
    bot.health = 17;
    bot.emit('health');
    bot.health = 20;
    bot.emit('health');
    bot.health = 18;
    bot.emit('health');
    assert.equal(tracker.stop(), 5);
    assert.equal(bot.listenerCount('health'), 0);
    assert.equal(tracker.stop(), 5);
});

test('combat terrain uses cheap local cover evidence and fails closed when unavailable', () => {
    const position = new Vec3(0, 64, 0);
    const names = new Map([
        ['1,65,0', 'stone'],
        ['-1,65,0', 'stone'],
        ['0,65,1', 'stone'],
        ['0,65,-1', 'air'],
    ]);
    const bot = {
        entity: {position, isInWater: false},
        blockAt: (point) => ({name: names.get(`${point.x},${point.y},${point.z}`) || 'air'}),
    };
    assert.equal(classifyCombatTerrain(bot), 'enclosed');
    assert.equal(classifyCombatTerrain({entity: {position, isInWater: true}}), 'water');
    assert.equal(classifyCombatTerrain({entity: {position, isInWater: false}}), 'unknown');
});

test('only executed tactical outcomes affect combat learning', () => {
    assert.equal(combatEpisodeOutcome({
        tactic: 'fight',
        fightReason: 'fallback_defend_self',
        executedPreset: null,
        health: 20,
        success: true,
    }), 'interrupted');
    assert.equal(combatEpisodeOutcome({
        tactic: 'fight',
        fightReason: 'hostiles_cleared',
        executedPreset: 'melee',
        health: 20,
        success: true,
    }), 'success');
    assert.equal(combatEpisodeOutcome({
        tactic: 'fight',
        fightReason: 'bot_dead',
        executedPreset: 'melee',
        health: 0,
        success: false,
    }), 'death');
    assert.equal(combatEpisodeOutcome({
        tactic: 'fight',
        timedOut: true,
        executedPreset: 'melee',
        health: 0,
    }), 'interrupted');
});

test('combat history load merges an immediate episode exactly once', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'evelyn-combat-load-'));
    const historyPath = join(directory, 'combat_history.json');
    const context = {
        mobSet: ['zombie'],
        countBucket: 'single',
        distanceBucket: 'near',
        terrain: 'open',
        healthBucket: 'healthy',
        gear: ['melee'],
    };
    try {
        await saveCombatHistoryAtomic(historyPath, [makeCombatEpisode({
            ...context,
            tactic: 'melee',
            outcome: 'success',
            verified: true,
            damage: 1,
            durationMs: 500,
            minecraftVersion: '1.21.11',
            pluginVersion: '1.7.16',
        })]);
        const bot = {version: '1.21.11'};
        startCombatExperience(bot, historyPath);
        recordCombatExperience(bot, {
            context,
            tactic: 'melee',
            before: {health: 20},
            after: {health: 18},
            success: true,
            durationMs: 700,
        });

        await bot.evelynCombatHistoryLoading;
        await bot.evelynCombatHistoryWriter.flush();
        const restored = await loadCombatHistory(historyPath);
        assert.equal(restored.length, 2);
        assert.deepEqual(restored.map((episode) => episode.damage), [1, 2]);
    } finally {
        await rm(directory, {recursive: true, force: true});
    }
});

test('combat shutdown waits for load, flushes once, and coalesces exit requests', async () => {
    const originalExit = process.exit;
    const order = [];
    let releaseLoad;
    const bot = {
        evelynCombatHistoryLoading: new Promise((resolve) => {
            releaseLoad = () => {
                order.push('load');
                resolve();
            };
        }),
        evelynCombatHistoryWriter: {
            flush: async () => order.push('flush'),
        },
    };
    process.exit = (code) => order.push(`exit:${code}`);
    try {
        const first = exitAfterCombatHistoryFlush(bot, 7, 500);
        const duplicate = exitAfterCombatHistoryFlush(bot, 9, 500);
        assert.equal(duplicate, first);
        await Promise.resolve();
        assert.deepEqual(order, []);

        releaseLoad();
        await first;
        assert.deepEqual(order, ['load', 'flush', 'exit:7']);
    } finally {
        process.exit = originalExit;
    }
});

test('safe tool bootstrap waits for idle instead of interrupting movement', () => {
    assert.equal(decisionCanInterrupt('bootstrap_tools'), false);
    assert.equal(decisionCanInterrupt('handle_hostile'), true);
    assert.equal(decisionCanInterrupt('acquire_food', snapshot({hunger: 14})), false);
    assert.equal(decisionCanInterrupt('acquire_food', snapshot({hunger: 10})), true);
    assert.equal(decisionCanInterrupt('acquire_food', snapshot({health: 10, hunger: 15})), true);
    assert.equal(decisionCanInterrupt('acquire_food', snapshot({health: 11, hunger: 15})), false);
});

test('failed hostile handling retries quickly instead of yielding to planner work', () => {
    assert.equal(failureCooldownMs('handle_hostile', 1), 250);
    assert.equal(failureCooldownMs('handle_hostile', 2), 250);
    assert.equal(failureCooldownMs('handle_hostile', 20), 250);
    assert.equal(failureCooldownMs('acquire_food', 1), 10000);
    assert.equal(failureCooldownMs('acquire_food', 2), 20000);
    assert.equal(failureCooldownMs('acquire_food', 3), 40000);
    assert.equal(failureCooldownMs('acquire_food', 20), 60000);
});

test('escape candidates are scored against every hostile instead of only the nearest one', () => {
    const origin = {x: 0, y: 64, z: 0};
    const hostiles = [
        {position: {x: 4, y: 64, z: 0}},
        {position: {x: 0, y: 64, z: 5}},
        {position: {x: -3, y: 64, z: 2}},
    ];
    const candidates = buildEscapeCandidates(origin, hostiles);
    const selected = chooseEscapeCandidate(candidates, hostiles, {origin});
    assert.ok(selected);
    assert.ok(escapeSafetyScore(selected, hostiles) > escapeSafetyScore(origin, hostiles));
    for (const hostile of hostiles) {
        const before = Math.hypot(origin.x - hostile.position.x, origin.z - hostile.position.z);
        const after = Math.hypot(selected.x - hostile.position.x, selected.z - hostile.position.z);
        assert.ok(after > before, `selected waypoint moved closer to ${JSON.stringify(hostile)}`);
    }
});

test('recovery escape prefers a covered waypoint when distance scores are comparable', () => {
    const hostiles = [{position: {x: 0, y: 64, z: 0}}];
    const candidates = [
        {x: 10, y: 64, z: 0},
        {x: 9.5, y: 64, z: 0},
    ];
    const selected = chooseEscapeCandidate(candidates, hostiles, {
        origin: {x: 5, y: 64, z: 0},
        recoveryMode: true,
        coverScore: (candidate) => candidate.x === 9.5 ? 1 : 0,
    });
    assert.equal(selected.x, 9.5);
});

test('escape controller releases its movement lease when no waypoint is viable', async () => {
    const position = {
        x: 0,
        y: 64,
        z: 0,
        distanceTo(other) {
            return Math.hypot(other.x - this.x, other.y - this.y, other.z - this.z);
        },
    };
    const hostile = {
        distance: 2,
        entity: {position: {x: 2, y: 64, z: 0}},
    };
    const bot = {
        entity: {position},
        entities: {},
        pathfinder: {
            stop() {},
            setMovements() {},
            setGoal() {},
        },
        blockAt: () => null,
    };
    const result = await escapeFromHostiles(bot, {
        hostileProvider: () => [hostile],
        timeoutMs: 100,
    });
    assert.equal(result.verification, 'no_viable_waypoint');
    assert.equal(bot.evelynMovementOwner, null);
});

test('close-range escape sprints immediately and always releases controls', async () => {
    const controls = [];
    const bot = {
        entity: {position: {x: 0, y: 64, z: 0}},
        health: 20,
        pathfinder: {stop() { controls.push(['pathfinder', false]); }},
        clearControlStates() { controls.push(['clear', false]); },
        async lookAt() {},
        setControlState(name, enabled) {
            controls.push([name, enabled]);
        },
    };
    assert.equal(await sprintEscapeBurst(
        bot,
        {x: 9, y: 64, z: 0},
        {durationMs: 1},
    ), true);
    assert.deepEqual(controls.slice(-3), [
        ['forward', false],
        ['sprint', false],
        ['jump', false],
    ]);
});

test('planner ticks preserve the last hostile tactic and verification evidence', () => {
    assert.deepEqual(
        mergeSurvivalState(
            { hostile_tactic: 'flee', hostile_verification: 'threat_returned', last_success: false },
            { phase: 'planner_control', snapshot: { hostileDistance: null } },
        ),
        {
            hostile_tactic: 'flee',
            hostile_verification: 'threat_returned',
            last_success: false,
            phase: 'planner_control',
            snapshot: { hostileDistance: null },
        },
    );
});

test('path floor removes every neighbor below the action minimum Y', () => {
    assert.deepEqual(
        filterMovesAtOrAbove([{ y: 72 }, { y: 73 }, { y: 74 }], 73),
        [{ y: 73 }, { y: 74 }],
    );
});

test('surface escape rejects a pathfinder no-op at the same underground position', () => {
    assert.equal(verifySurfaceEscape({
        reached: true,
        startPosition: {x: 205.3, y: 48, z: -14.4},
        finalPosition: {x: 205.3, y: 48, z: -14.4},
        surfaceY: 59,
    }), false);
});

test('surface escape requires both real movement and leaving the underground band', () => {
    assert.equal(verifySurfaceEscape({
        reached: true,
        startPosition: {x: 205.3, y: 48, z: -14.4},
        finalPosition: {x: 208.3, y: 51, z: -14.4},
        surfaceY: 59,
    }), false);
    assert.equal(verifySurfaceEscape({
        reached: true,
        startPosition: {x: 205.3, y: 48, z: -14.4},
        finalPosition: {x: 208.3, y: 57, z: -14.4},
        surfaceY: 59,
    }), true);
});

test('vertically separated hostiles do not monopolize survival recovery', () => {
    const origin = {x: 0, y: 48, z: 0};
    assert.equal(hostileIsActionable(origin, {x: 8, y: 59, z: 0}, 14), false);
    assert.equal(hostileIsActionable(origin, {x: 12, y: 51, z: 0}, 14), true);
    assert.equal(hostileIsActionable(origin, {x: 0, y: 59, z: 0}, 7), true);
});

test('a solid wall hides a distant same-level hostile from recovery', () => {
    const bot = {
        entity: {position: {x: 0, y: 64, z: 0}},
        blockAt(position) {
            const blocked = position.x === 5;
            return {
                name: blocked ? 'stone' : 'air',
                boundingBox: blocked ? 'block' : 'empty',
            };
        },
    };
    assert.equal(hostileHasClearLine(bot, {
        position: {x: 10, y: 64, z: 0},
        height: 1.8,
    }), false);
});

test('staircase fallback rotates through adjacent one-block-up targets', () => {
    assert.deepEqual(staircaseTargets({x: 205.3, y: 48, z: -14.4}, 0), [
        {index: 0, x: 206, y: 49, z: -15},
        {index: 1, x: 205, y: 49, z: -14},
        {index: 2, x: 204, y: 49, z: -15},
        {index: 3, x: 205, y: 49, z: -16},
    ]);
    assert.equal(staircaseTargets({x: 206, y: 49, z: -15}, 1)[0].index, 1);
});

test('staircase fallback can move sideways to establish the next upward base', () => {
    assert.deepEqual(staircaseBaseTargets({x: 205.8, y: 49.2, z: -14.7}, 1), [
        {index: 1, x: 205, y: 49, z: -14},
        {index: 2, x: 204, y: 49, z: -15},
        {index: 3, x: 205, y: 49, z: -16},
        {index: 0, x: 206, y: 49, z: -15},
    ]);
});

test('blocked upward staircase moves onto an adjacent dry base instead of looping', async () => {
    const names = new Map([
        ['206,49,-15', 'water'],
        ['205,49,-14', 'air'],
        ['204,49,-15', 'air'],
        ['205,49,-16', 'water'],
        ['206,48,-15', 'stone'],
        ['205,48,-14', 'stone'],
        ['205,50,-14', 'air'],
    ]);
    const bot = {
        entity: {position: {x: 205.8, y: 49.2, z: -14.7}},
        pathfinder: {stop() {}},
        clearControlStates() {},
        blockAt(position) {
            const name = names.get(`${position.x},${position.y},${position.z}`) || 'water';
            return {
                name,
                boundingBox: name === 'stone' ? 'block' : 'empty',
                diggable: name !== 'air',
            };
        },
        async lookAt() {},
        setControlState(name, enabled) {
            if (name === 'jump' && enabled) {
                this.entity.position = {x: 205.5, y: 49.2, z: -13.5};
            }
        },
    };
    const result = await digStaircaseStep(bot);
    assert.equal(result.progress, true);
    assert.equal(result.verification, 'staircase_base_progress');
    assert.deepEqual(result.target, {index: 1, x: 205, y: 49, z: -14});
});

test('water recovery selects a reachable dry pocket with solid support', () => {
    const names = new Map([
        ['2,47,0', 'stone'],
        ['2,48,0', 'air'],
        ['2,49,0', 'air'],
    ]);
    const bot = {
        entity: {position: {x: 0.3, y: 47.2, z: 0.3}},
        blockAt(position) {
            const name = names.get(`${position.x},${position.y},${position.z}`) || 'water';
            return {
                name,
                boundingBox: name === 'stone' ? 'block' : 'empty',
            };
        },
    };
    assert.deepEqual(nearbyAirExitTargets(bot, 3)[0], {
        x: 2,
        y: 48,
        z: 0,
        distance: 2,
        rise: 1,
    });
});

test('staircase fallback performs one controlled jump and releases controls', async () => {
    const controls = [];
    const bot = {
        entity: {position: {x: 205.3, y: 48, z: -14.4}},
        pathfinder: {stop() { controls.push(['pathfinder', false]); }},
        clearControlStates() { controls.push(['clear', false]); },
        async lookAt() {},
        setControlState(name, enabled) {
            controls.push([name, enabled]);
            if (name === 'jump' && enabled) {
                this.entity.position = {x: 206.5, y: 49, z: -14.5};
            }
        },
    };
    assert.equal(await walkStaircaseStep(
        bot,
        {x: 206, y: 49, z: -15},
        {timeoutMs: 10, pollMs: 1},
    ), true);
    assert.deepEqual(controls.slice(-2), [['forward', false], ['jump', false]]);
});

test('planner actions cannot interrupt an active Evelyn survival recovery', async () => {
    const manager = new ActionManager({
        cleanKill: () => {},
        requestInterrupt: () => {},
        clearBotLogs: () => {},
        bot: {
            interrupt_code: false,
            output: '',
            emit: () => {},
        },
        isIdle() { return true; },
        self_prompter: {
            isActive: () => false,
        },
    });
    manager.executing = true;
    manager.currentActionLabel = 'mode:evelyn_survival';
    let called = false;
    const result = await manager.runAction('action:goToBed', async () => { called = true; });
    assert.equal(called, false);
    assert.equal(result.interrupted, true);
    assert.match(result.message, /retained priority/);
});

function snapshot(overrides = {}) {
    return {
        connected: true,
        health: 20,
        hunger: 20,
        timeOfDay: null,
        underground: false,
        hostileDistance: null,
        hostileName: null,
        hostileId: null,
        hostileCount: 0,
        foodName: null,
        hasPickaxe: true,
        hasMeleeWeapon: false,
        weaponPower: 0,
        armorPoints: 0,
        hasShield: false,
        hasBow: false,
        arrowCount: 0,
        inWater: false,
        sheltered: false,
        ...overrides,
    };
}

test('temporary shelter uses sixteen wall blocks plus a supported two-block roof', () => {
    const center = new Vec3(20, 64, -7);
    const layout = temporaryShelterLayout(center);
    const keys = layout.targets.map((position) => position.toString());

    assert.equal(TEMPORARY_SHELTER_BLOCK_COUNT, 18);
    assert.equal(layout.targets.length, TEMPORARY_SHELTER_BLOCK_COUNT);
    assert.equal(new Set(keys).size, TEMPORARY_SHELTER_BLOCK_COUNT);
    assert.equal(keys.includes(center.toString()), false);
    assert.equal(keys.includes(center.offset(0, 1, 0).toString()), false);
    assert.equal(keys.includes(center.offset(0, 2, 0).toString()), true);
    assert.equal(keys.includes(center.offset(1, 2, 0).toString()), true);
});

test('night shelter never outranks an active threat or critical food recovery', () => {
    const dusk = snapshot({timeOfDay: 11000});
    assert.equal(shelterDecisionAllowed(dusk), true);
    assert.equal(selectSurvivalDecision(dusk), 'shelter_until_safe_dawn');
    assert.equal(
        selectSurvivalDecision({...dusk, hunger: 10}),
        'acquire_food',
    );
    assert.equal(
        selectSurvivalDecision({...dusk, health: 9, hunger: 10, foodName: 'cooked_beef'}),
        'eat_inventory_food',
    );
    assert.equal(
        selectSurvivalDecision({...dusk, hostileCount: 1, hostileDistance: 8}),
        'handle_hostile',
    );
    assert.equal(shelterDecisionAllowed({...dusk, inWater: true}), false);
    assert.equal(shelterDecisionAllowed({...dusk, timeOfDay: 10000}), false);
    assert.equal(shelterDecisionAllowed({...dusk, timeOfDay: 13000}), false);
    assert.equal(shelterDecisionAllowed({...dusk, underground: true}), false);
    assert.equal(shelterDecisionAllowed({...dusk, timeOfDay: 500, sheltered: true}), true);
});

test('tree canopy is not mistaken for underground during the shelter window', () => {
    const makeBot = (x, overhead) => ({
        entity: {position: new Vec3(x, 65, 0), isInWater: false},
        entities: {},
        blockAt: (position) => {
            if (position.y === 64) return {name: 'grass_block'};
            if (overhead === 'tree' && (position.y === 69 || position.y === 70)) return {name: 'oak_log'};
            if (overhead === 'tree' && position.y === 71) return {name: 'oak_leaves'};
            if (overhead === 'stone' && position.y === 71) return {name: 'stone'};
            return {name: 'air'};
        },
        inventory: {items: () => [], slots: []},
        registry: {foodsByName: {}},
        health: 20,
        food: 20,
        time: {timeOfDay: 12444},
    });

    const underTree = buildSurvivalSnapshot(makeBot(9100, 'tree'));
    const underground = buildSurvivalSnapshot(makeBot(9200, 'stone'));

    assert.equal(underTree.underground, false);
    assert.equal(selectSurvivalDecision(underTree), 'shelter_until_safe_dawn');
    assert.equal(underground.underground, true);
    assert.equal(selectSurvivalDecision(underground), null);
});

test('an empty vine column is not a surface but a solid cave roof is', () => {
    const registry = minecraftData('1.21.11');
    const Block = prismarineBlock(registry);
    const realBlock = (name, position) => {
        const block = Block.fromStateId(registry.blocksByName[name].minStateId, 0);
        block.position = position;
        return block;
    };
    const makeBot = (x, overheadName) => ({
        entity: {position: new Vec3(x, 65, 0), isInWater: false},
        entities: {},
        blockAt: (position) => {
            if (position.y === 64) return realBlock('grass_block', position);
            if (overheadName === 'stone' && position.y === 71) {
                return realBlock('stone', position);
            }
            if (overheadName !== 'stone' && position.y >= 66 && position.y <= 71) {
                return realBlock(overheadName, position);
            }
            return realBlock('air', position);
        },
        inventory: {items: () => [], slots: []},
        registry,
        health: 20,
        food: 20,
        time: {timeOfDay: 12000},
    });

    for (const [x, name] of [[9300, 'vine'], [9301, 'short_grass'], [9302, 'water']]) {
        const exposed = buildSurvivalSnapshot(makeBot(x, name));
        assert.equal(exposed.surfaceY, 65);
        assert.equal(exposed.underground, false);
        assert.equal(selectSurvivalDecision(exposed), 'shelter_until_safe_dawn');
    }
    const belowStone = buildSurvivalSnapshot(makeBot(9400, 'stone'));

    assert.equal(belowStone.surfaceY, 72);
    assert.equal(belowStone.underground, true);
    assert.equal(selectSurvivalDecision(belowStone), null);
});

function temporaryShelterHarness({
    initialDirtCount = TEMPORARY_SHELTER_BLOCK_COUNT,
    onPlacement = null,
    onMove = null,
    shelterOffset = null,
    additionalShelterOffsets = [],
    unbuildableStart = false,
} = {}) {
    const center = new Vec3(3000, 64, 0);
    const shelterCenter = shelterOffset ? center.plus(shelterOffset) : center;
    const shelterCenters = shelterOffset
        ? [shelterCenter, ...additionalShelterOffsets.map((offset) => center.plus(offset))]
        : [center];
    const blocks = new Map();
    const key = (position) => `${position.x},${position.y},${position.z}`;
    if (!unbuildableStart || shelterOffset) {
        for (const candidateCenter of shelterCenters) {
            for (let dx = -1; dx <= 1; dx++) {
                for (let dz = -1; dz <= 1; dz++) {
                    blocks.set(key(candidateCenter.offset(dx, -1, dz)), 'stone');
                }
            }
        }
    }
    if (unbuildableStart) {
        for (let dx = -1; dx <= 1; dx++) {
            for (let dz = -1; dz <= 1; dz++) {
                if ((dx + dz) % 2 === 0) {
                    blocks.set(key(center.offset(dx, -1, dz)), 'oak_leaves');
                }
            }
        }
    }
    let dirtCount = initialDirtCount;
    let placements = 0;
    let verifiedBeforeExit = false;
    let interrupts = 0;
    let blockReads = 0;
    let configuredMovements = null;
    const movementGoals = [];
    const pathfinderGoals = [];
    const dirt = {name: 'dirt', get count() { return dirtCount; }};
    const bot = Object.assign(new EventEmitter(), {
        entity: {position: center.clone(), isInWater: false},
        entities: {},
        health: 20,
        food: 20,
        oxygenLevel: 20,
        interrupt_code: false,
        time: {timeOfDay: 11000},
        registry: shelterOffset ? minecraftData('1.21.11') : {foodsByName: {}},
        inventory: {
            items: () => dirtCount > 0 ? [dirt] : [],
            slots: [],
        },
        modes: {behavior_log: ''},
        pathfinder: {
            thinkTimeout: 0,
            stop() {},
            setMovements(movements) { configuredMovements = movements; },
            async goto(goal) {
                pathfinderGoals.push(goal);
                const goals = Array.isArray(goal?.goals) ? goal.goals : [goal];
                const selectedGoal = goals.find((candidate) => candidate?.isEnd?.(shelterCenter)) || goals[0];
                const destination = new Vec3(selectedGoal.x, selectedGoal.y, selectedGoal.z);
                movementGoals.push(destination);
                bot.entity.position = destination.clone();
                onMove?.({bot, goal: selectedGoal});
            },
        },
        collectBlock: {cancelTask() {}},
        blockAt(position) {
            blockReads += 1;
            const point = floored(position);
            const name = blocks.get(key(point)) || 'air';
            return {
                name,
                boundingBox: ['air', 'short_grass'].includes(name) ? 'empty' : 'block',
                position: point,
            };
        },
        async equip() {},
        async lookAt() {},
        async placeBlock(reference, face) {
            const target = reference.position.plus(face);
            blocks.set(key(target), 'dirt');
            dirtCount -= 1;
            placements += 1;
            onPlacement?.({bot: this, placements, target});
            if (placements === TEMPORARY_SHELTER_BLOCK_COUNT) {
                verifiedBeforeExit = temporaryShelterVerified(this, shelterCenter);
                this.time.timeOfDay = 0;
            }
        },
        canDigBlock: () => true,
        async dig(block) {
            blocks.set(key(block.position), 'air');
        },
    });
    const agent = {
        bot,
        isIdle: () => true,
        goal_manager: {requestPriorityGoal() {}},
        requestInterrupt() {
            interrupts += 1;
            bot.interrupt_code = true;
            bot.pathfinder.stop();
            bot.collectBlock.cancelTask();
        },
    };
    return {
        agent,
        bot,
        center,
        shelterCenter,
        movementGoals,
        pathfinderGoals,
        configuredMovements: () => configuredMovements,
        blockReads: () => blockReads,
        placements: () => placements,
        interrupts: () => interrupts,
        verifiedBeforeExit: () => verifiedBeforeExit,
        addDirt(count = 1) {
            dirtCount += count;
        },
        setBlock(position, name) {
            blocks.set(key(floored(position)), name);
        },
    };
}

function floored(position) {
    return position instanceof Vec3
        ? position.floored()
        : new Vec3(Math.floor(position.x), Math.floor(position.y), Math.floor(position.z));
}

test('temporary shelter verifies all eighteen placements before opening a dawn exit', async () => {
    const harness = temporaryShelterHarness();
    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {pollMs: 1, clearStableMs: 0},
    );

    assert.equal(result.success, true);
    assert.equal(result.verification, 'shelter_dawn_exit_verified');
    assert.equal(harness.placements(), TEMPORARY_SHELTER_BLOCK_COUNT);
    assert.equal(harness.verifiedBeforeExit(), true);
    assert.equal(temporaryShelterVerified(harness.bot, harness.center), false);
    assert.equal(harness.bot.blockAt(harness.center).name, 'air');
    assert.equal(harness.bot.blockAt(harness.center.offset(0, 1, 0)).name, 'air');
});

test('only a verified dawn exit increments the process shelter success count', async () => {
    const harness = temporaryShelterHarness();
    harness.bot.evelynSurvivalState = {shelter_success_count: 2};
    let execution = null;
    const mode = createEvelynSurvivalMode({
        execute: (_mode, _agent, action) => {
            execution = action();
            return execution;
        },
    });

    await mode.update(harness.agent);
    await execution;
    assert.equal(harness.bot.evelynSurvivalState.shelter_success_count, 3);

    mode.lastCheckAt = 0;
    await mode.update(harness.agent);
    assert.equal(harness.bot.evelynSurvivalState.shelter_success_count, 3);
});

test('temporary shelter reports a missing enclosure block during the dawn hold', async () => {
    let harness;
    harness = temporaryShelterHarness({
        onPlacement: ({placements}) => {
            if (placements !== TEMPORARY_SHELTER_BLOCK_COUNT) return;
            setTimeout(() => {
                harness.setBlock(temporaryShelterLayout(harness.center).targets[0], 'air');
            }, 5);
        },
    });

    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {pollMs: 1, clearStableMs: 50},
    );

    assert.equal(result.success, false);
    assert.equal(result.progress, true);
    assert.equal(result.verification, 'shelter_breached_missing_block');
});

test('temporary shelter gathers a partial shortage despite a vertically separated hostile', async () => {
    const harness = temporaryShelterHarness({
        initialDirtCount: 14,
        onPlacement: ({bot, placements}) => {
            if (placements === TEMPORARY_SHELTER_BLOCK_COUNT) delete bot.entities[77];
        },
    });
    harness.bot.entities[77] = {
        id: 77,
        name: 'zombie',
        type: 'hostile',
        position: harness.center.offset(0, -16, 0),
    };
    const requested = [];
    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {
            pollMs: 1,
            clearStableMs: 0,
            collectBlock: async (_bot, blockName, count) => {
                requested.push({blockName, count});
                harness.addDirt(Math.min(2, count));
                return true;
            },
        },
    );

    assert.equal(result.success, true);
    assert.deepEqual(requested, [
        {blockName: 'dirt', count: 4},
        {blockName: 'dirt', count: 2},
    ]);
    assert.equal(harness.placements(), TEMPORARY_SHELTER_BLOCK_COUNT);
});

test('temporary shelter gathers visible reachable dirt one block at a time before generic pathing', async () => {
    const harness = temporaryShelterHarness({initialDirtCount: 0});
    harness.bot.registry = minecraftData('1.21.11');
    const targets = Array.from({length: TEMPORARY_SHELTER_BLOCK_COUNT}, (_, index) => (
        harness.center.offset(2 + index, -1, 0)
    ));
    for (const target of targets) harness.setBlock(target, 'grass_block');
    harness.bot.canSeeBlock = () => true;
    harness.bot.findBlocks = ({matching, count}) => targets
        .map((position) => harness.bot.blockAt(position))
        .filter(matching)
        .slice(0, count)
        .map((block) => block.position);
    let directCollections = 0;
    harness.bot.collectBlock = {
        movements: {safeToBreak: () => true},
        cancelTask() {},
        async collect(block, options) {
            assert.deepEqual(options, {blocksFirst: true});
            harness.setBlock(block.position, 'air');
            harness.addDirt();
            harness.bot.entity.position = block.position.clone();
            directCollections += 1;
        },
    };
    let genericCollections = 0;

    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {
            pollMs: 1,
            clearStableMs: 0,
            collectBlock: async () => {
                genericCollections += 1;
                return false;
            },
        },
    );

    assert.equal(result.success, true);
    assert.equal(result.verification, 'shelter_dawn_exit_verified');
    assert.equal(directCollections, TEMPORARY_SHELTER_BLOCK_COUNT);
    assert.equal(genericCollections, 0);
    assert.equal(harness.movementGoals.length, TEMPORARY_SHELTER_BLOCK_COUNT);
    assert.equal(harness.bot.entity.position.equals(harness.center), true);
});

test('temporary shelter telemetry distinguishes diggable dirt hidden from direct sight', async () => {
    const harness = temporaryShelterHarness({initialDirtCount: 0});
    const target = harness.center.offset(2, -1, 0);
    harness.setBlock(target, 'grass_block');
    harness.bot.canSeeBlock = () => false;
    harness.bot.findBlocks = ({matching}) => {
        assert.equal(matching({name: 'grass_block', position: null}), true);
        return matching(harness.bot.blockAt(target)) ? [target] : [];
    };
    harness.bot.collectBlock = {
        movements: {safeToBreak: () => true},
        cancelTask() {},
    };

    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {
            pollMs: 1,
            gatherTimeoutMs: 5,
            collectBlock: async () => {
                while (!harness.bot.interrupt_code) {
                    await new Promise((resolve) => setTimeout(resolve, 1));
                }
                return false;
            },
        },
    );

    assert.equal(result.success, false);
    assert.equal(result.verification, 'shelter_gather_timeout_generic_collect_not_visible');
});

test('temporary shelter disables collectBlock scaffolding while gathering exact materials', async () => {
    const harness = temporaryShelterHarness({initialDirtCount: 0});
    harness.bot.registry = minecraftData('1.21.11');
    const dirtId = harness.bot.registry.itemsByName.dirt.id;
    const cobblestoneId = harness.bot.registry.itemsByName.cobblestone.id;
    const originalMovements = {
        scafoldingBlocks: [dirtId, cobblestoneId],
        safeToBreak: () => true,
    };
    harness.bot.collectBlock = {
        movements: originalMovements,
        cancelTask() {},
    };
    let gatheringScaffolding = [];

    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {
            pollMs: 1,
            clearStableMs: 0,
            collectBlock: async (bot, _blockName, count) => {
                gatheringScaffolding = [...bot.collectBlock.movements.scafoldingBlocks];
                harness.addDirt(count);
                if (gatheringScaffolding.includes(dirtId)) harness.addDirt(-1);
                return true;
            },
        },
    );

    assert.equal(result.success, true);
    assert.equal(gatheringScaffolding.includes(dirtId), false);
    assert.equal(gatheringScaffolding.includes(cobblestoneId), false);
    assert.equal(harness.bot.collectBlock.movements, originalMovements);
    assert.equal(harness.placements(), TEMPORARY_SHELTER_BLOCK_COUNT);
});

test('temporary shelter return path disables scaffolding to preserve construction materials', async () => {
    const harness = temporaryShelterHarness({initialDirtCount: 14});
    harness.bot.registry = minecraftData('1.21.11');
    const dirtId = harness.bot.registry.itemsByName.dirt.id;
    const cobblestoneId = harness.bot.registry.itemsByName.cobblestone.id;
    let activeMovements = null;
    let returnScaffolding = [];
    harness.bot.pathfinder.setMovements = (movements) => { activeMovements = movements; };
    harness.bot.pathfinder.goto = async (goal) => {
        returnScaffolding = [...activeMovements.scafoldingBlocks];
        if (returnScaffolding.includes(dirtId)) harness.addDirt(-1);
        harness.bot.entity.position = new Vec3(goal.x, goal.y, goal.z);
    };

    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {
            pollMs: 1,
            clearStableMs: 0,
            collectBlock: async (_bot, _blockName, count) => {
                harness.addDirt(count);
                harness.bot.entity.position = harness.center.offset(2, 0, 0);
                return true;
            },
        },
    );

    assert.equal(result.success, true);
    assert.equal(returnScaffolding.includes(dirtId), false);
    assert.equal(returnScaffolding.includes(cobblestoneId), false);
    assert.equal(harness.placements(), TEMPORARY_SHELTER_BLOCK_COUNT);
});

test('temporary shelter stops before placement when a cave hostile becomes actionable during gathering', async () => {
    const harness = temporaryShelterHarness({initialDirtCount: 14});
    const hostile = {
        id: 78,
        name: 'zombie',
        type: 'hostile',
        position: harness.center.offset(0, -16, 0),
    };
    harness.bot.entities[hostile.id] = hostile;
    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {
            pollMs: 1,
            clearStableMs: 0,
            collectBlock: async () => {
                harness.addDirt(2);
                hostile.position = harness.center.offset(2, 0, 0);
                return true;
            },
        },
    );

    assert.equal(result.success, false);
    assert.equal(result.progress, true);
    assert.equal(result.interrupted, true);
    assert.equal(result.verification, 'shelter_gather_hostile_detected');
    assert.equal(harness.placements(), 0);
    assert.equal(harness.interrupts(), 1);
});

test('temporary shelter rejects a successful collection result without inventory progress', async () => {
    const harness = temporaryShelterHarness({initialDirtCount: 14});
    let collections = 0;
    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {
            pollMs: 1,
            clearStableMs: 0,
            collectBlock: async () => {
                collections += 1;
                return true;
            },
        },
    );

    assert.equal(result.success, false);
    assert.equal(result.verification, 'shelter_material_unavailable');
    assert.equal(collections, 1);
    assert.equal(harness.placements(), 0);
});

test('temporary shelter reports partial inventory gained before a gather timeout as progress', async () => {
    const harness = temporaryShelterHarness({initialDirtCount: 14});
    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {
            pollMs: 1,
            gatherTimeoutMs: 5,
            collectBlock: async () => {
                harness.addDirt();
                while (!harness.bot.interrupt_code) {
                    await new Promise((resolve) => setTimeout(resolve, 1));
                }
                return true;
            },
        },
    );

    assert.equal(result.success, false);
    assert.equal(result.progress, true);
    assert.equal(result.verification, 'shelter_gather_timeout_generic_collect_probe_unavailable');
    assert.equal(harness.placements(), 0);
});

test('temporary shelter stops before another collection when the first gain is interrupted', async () => {
    const harness = temporaryShelterHarness({initialDirtCount: 14});
    let collections = 0;
    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {
            pollMs: 1,
            collectBlock: async () => {
                collections += 1;
                harness.addDirt();
                harness.bot.interrupt_code = true;
                return true;
            },
        },
    );

    assert.equal(result.success, false);
    assert.equal(result.progress, true);
    assert.equal(result.interrupted, true);
    assert.equal(result.verification, 'shelter_build_interrupted');
    assert.equal(collections, 1);
    assert.equal(harness.placements(), 0);
});

test('temporary shelter clears safe surface vegetation before placing a wall', async () => {
    const harness = temporaryShelterHarness();
    const firstWall = temporaryShelterLayout(harness.center).targets[0];
    harness.setBlock(firstWall, 'short_grass');

    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {pollMs: 1, clearStableMs: 0},
    );

    assert.equal(result.success, true);
    assert.equal(harness.placements(), TEMPORARY_SHELTER_BLOCK_COUNT);
});

test('temporary shelter moves from leaf and air supports to the nearest flat site', async () => {
    const harness = temporaryShelterHarness({
        shelterOffset: new Vec3(4, -2, 0),
        unbuildableStart: true,
    });
    const startSupports = temporaryShelterLayout(harness.center).supports
        .map((position) => harness.bot.blockAt(position).name);

    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {pollMs: 1, clearStableMs: 0},
    );

    assert.ok(startSupports.includes('oak_leaves'));
    assert.ok(startSupports.includes('air'));
    assert.equal(result.success, true);
    assert.equal(result.verification, 'shelter_dawn_exit_verified');
    assert.deepEqual(harness.movementGoals, [harness.shelterCenter]);
    assert.equal(harness.placements(), TEMPORARY_SHELTER_BLOCK_COUNT);
    assert.equal(harness.verifiedBeforeExit(), true);
    assert.ok(harness.blockReads() < 5000);
    for (const doorway of temporaryShelterLayout(harness.shelterCenter).doorway) {
        assert.equal(harness.bot.blockAt(doorway).name, 'air');
    }
});

test('temporary shelter lets pathfinder choose a reachable valid site instead of Euclidean nearest', async () => {
    const nearerOffset = new Vec3(3, 0, 0);
    const reachableOffset = new Vec3(-6, 0, 0);
    const harness = temporaryShelterHarness({
        shelterOffset: reachableOffset,
        additionalShelterOffsets: [nearerOffset],
        unbuildableStart: true,
    });

    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {pollMs: 1, clearStableMs: 0},
    );

    const [composite] = harness.pathfinderGoals;
    const alternatives = composite.goals.map(({x, y, z}) => new Vec3(x, y, z));
    assert.equal(result.success, true);
    assert.equal(composite.constructor.name, 'GoalCompositeAny');
    assert.deepEqual(alternatives[0], harness.center.plus(nearerOffset));
    assert.ok(alternatives.some((candidate) => candidate.equals(harness.shelterCenter)));
    assert.deepEqual(harness.movementGoals, [harness.shelterCenter]);
    assert.equal(harness.verifiedBeforeExit(), true);
});

test('temporary shelter retries an exact site when the composite path reports no path', async () => {
    const shelterOffset = new Vec3(4, -2, 0);
    const harness = temporaryShelterHarness({shelterOffset, unbuildableStart: true});
    let attempts = 0;
    harness.bot.pathfinder.goto = async (goal) => {
        harness.pathfinderGoals.push(goal);
        attempts += 1;
        if (attempts === 1) throw new Error('NoPath');
        const destination = new Vec3(goal.x, goal.y, goal.z);
        harness.movementGoals.push(destination);
        harness.bot.entity.position = destination;
    };

    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {pollMs: 1, clearStableMs: 0},
    );

    assert.equal(result.success, true);
    assert.equal(result.verification, 'shelter_dawn_exit_verified');
    assert.equal(harness.pathfinderGoals[0].constructor.name, 'GoalCompositeAny');
    assert.equal(harness.pathfinderGoals[1].constructor.name, 'GoalBlock');
    assert.deepEqual(harness.movementGoals, [harness.shelterCenter]);
});

test('temporary shelter still fails closed when no bounded flat site exists', async () => {
    const harness = temporaryShelterHarness({unbuildableStart: true});
    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {pollMs: 1, clearStableMs: 0},
    );

    assert.deepEqual(result, {
        success: false,
        progress: false,
        verification: 'shelter_site_unbuildable',
    });
    assert.equal(harness.movementGoals.length, 0);
    assert.equal(harness.placements(), 0);
});

test('temporary shelter expands its bounded site search once when the local radius is unbuildable', async () => {
    const harness = temporaryShelterHarness({
        shelterOffset: new Vec3(12, 0, 0),
        unbuildableStart: true,
    });
    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {pollMs: 1, clearStableMs: 0},
    );

    assert.equal(result.success, true);
    assert.deepEqual(harness.movementGoals, [harness.shelterCenter]);
    assert.equal(harness.placements(), TEMPORARY_SHELTER_BLOCK_COUNT);
});

test('temporary shelter path floor permits the first step toward a site two blocks uphill', async () => {
    const harness = temporaryShelterHarness({
        shelterOffset: new Vec3(2, 2, 0),
        unbuildableStart: true,
    });
    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {pollMs: 1, clearStableMs: 0},
    );
    const movements = harness.configuredMovements();
    const stepPenalty = (y) => movements.exclusionAreasStep.reduce(
        (total, exclusion) => total + exclusion({position: {y}}),
        0,
    );

    assert.equal(result.success, true);
    assert.deepEqual(harness.movementGoals, [harness.shelterCenter]);
    assert.equal(stepPenalty(63), 0);
    assert.ok(stepPenalty(62) > 0);
});

test('survival path stops after two stuck resets, cleans its listener, and permits the next path', async () => {
    const target = new Vec3(5, 64, 0);
    const goal = {isEnd: (position) => position.equals(target)};
    let activeGoal = null;
    let attempts = 0;
    let stopping = false;
    let stops = 0;
    const bot = Object.assign(new EventEmitter(), {
        entity: {position: new Vec3(0, 64, 0)},
        pathfinder: {
            thinkTimeout: 0,
            get goal() { return activeGoal; },
            setMovements() {},
            setGoal(nextGoal) {
                activeGoal = nextGoal;
                if (nextGoal === null) stopping = false;
            },
            goto(nextGoal) {
                if (stopping) return Promise.reject(new Error('stale stop state'));
                this.setGoal(nextGoal);
                attempts += 1;
                if (attempts === 1) return new Promise(() => {});
                bot.entity.position = target.clone();
                return Promise.resolve();
            },
            stop() {
                stops += 1;
                stopping = true;
            },
        },
    });

    let settled = false;
    const first = pathWithTimeout(bot, goal, {}, 1000);
    first.then(() => { settled = true; });
    assert.equal(bot.listenerCount('path_reset'), 1);
    bot.emit('path_reset', 'stuck');
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(settled, false);
    assert.equal(stops, 0);

    bot.emit('path_reset', 'stuck');
    assert.equal(await first, false);
    assert.equal(stops, 1);
    assert.equal(activeGoal, null);
    assert.equal(bot.listenerCount('path_reset'), 0);

    assert.equal(await pathWithTimeout(bot, goal, {}, 1000), true);
    assert.equal(attempts, 2);
    assert.equal(bot.listenerCount('path_reset'), 0);
});

test('survival path failure preserves a newer preemption goal owned by another controller', async () => {
    const originalGoal = {isEnd: () => false};
    const emergencyGoal = {isEnd: () => false};
    let activeGoal = null;
    let rejectPath;
    let stops = 0;
    const bot = Object.assign(new EventEmitter(), {
        entity: {position: new Vec3(0, 64, 0)},
        pathfinder: {
            thinkTimeout: 0,
            get goal() { return activeGoal; },
            setMovements() {},
            goto(goal) {
                activeGoal = goal;
                return new Promise((_, reject) => { rejectPath = reject; });
            },
            setGoal(goal) {
                activeGoal = goal;
                rejectPath?.(new Error('GoalChanged'));
            },
            stop() { stops += 1; },
        },
    });

    const pending = pathWithTimeout(bot, originalGoal, {}, 1000);
    bot.pathfinder.setGoal(emergencyGoal);

    assert.equal(await pending, false);
    assert.equal(activeGoal, emergencyGoal);
    assert.equal(stops, 0);
    assert.equal(bot.listenerCount('path_reset'), 0);
});

test('survival path verifies the resolved goal position and always removes its reset listener', async () => {
    const target = new Vec3(8, 65, -2);
    const goal = {isEnd: (position) => position.equals(target)};
    const makeBot = (position) => {
        let stops = 0;
        const bot = Object.assign(new EventEmitter(), {
            entity: {position},
            pathfinder: {
                thinkTimeout: 20,
                setMovements() {},
                async goto() {},
                stop() { stops += 1; },
                setGoal() {},
            },
        });
        return {bot, stops: () => stops};
    };
    const reached = makeBot(target.offset(0, -1, 0));
    const missed = makeBot(target.offset(1, 0, 0));

    assert.equal(await pathWithTimeout(reached.bot, goal, {}, 1000), true);
    assert.equal(reached.stops(), 0);
    assert.equal(reached.bot.listenerCount('path_reset'), 0);

    assert.equal(await pathWithTimeout(missed.bot, goal, {}, 1000), false);
    assert.equal(missed.stops(), 1);
    assert.equal(missed.bot.listenerCount('path_reset'), 0);
});

test('temporary shelter site movement preserves the health interrupt boundary', async () => {
    const harness = temporaryShelterHarness({
        shelterOffset: new Vec3(4, -2, 0),
        unbuildableStart: true,
        onMove: ({bot}) => { bot.health -= 1; },
    });
    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 11000}),
        {pollMs: 1, clearStableMs: 0},
    );

    assert.equal(result.interrupted, true);
    assert.equal(result.verification, 'shelter_build_interrupted');
    assert.equal(harness.placements(), 0);
});

test('a restarted bot exits a verified shelter during any safe daytime window', async () => {
    const harness = temporaryShelterHarness();
    const layout = temporaryShelterLayout(harness.center);
    for (const target of layout.targets) harness.setBlock(target, 'dirt');
    harness.bot.time.timeOfDay = 5000;

    const result = await runTemporaryShelterAction(
        harness.agent,
        snapshot({timeOfDay: 5000, sheltered: true}),
        {pollMs: 1, clearStableMs: 0},
    );

    assert.equal(result.success, true);
    assert.equal(harness.placements(), 0);
    assert.equal(temporaryShelterVerified(harness.bot, harness.center), false);
});

test('a completed shelter hides an adjacent occluded hostile from tactical handling', () => {
    const harness = temporaryShelterHarness();
    const layout = temporaryShelterLayout(harness.center);
    for (const target of layout.targets) harness.setBlock(target, 'dirt');
    harness.bot.entities[7] = {
        id: 7,
        name: 'zombie',
        position: harness.center.offset(2, 0, 0),
    };

    const state = buildSurvivalSnapshot(harness.bot);

    assert.equal(state.sheltered, true);
    assert.equal(state.hostileDistance, null);
    assert.equal(state.hostileCount, 0);
    assert.equal(selectSurvivalDecision(state), 'shelter_until_safe_dawn');
});

test('a hostile spawn interrupts shelter placement before any later block is placed', async () => {
    const hostile = {id: 91, name: 'zombie', position: new Vec3(3002, 64, 0)};
    const harness = temporaryShelterHarness({
        onPlacement: ({bot, placements}) => {
            if (placements !== 1) return;
            bot.entities[hostile.id] = hostile;
            bot.emit('entitySpawn', hostile);
        },
    });
    let execution = null;
    const mode = createEvelynSurvivalMode({
        execute: (_mode, _agent, action) => {
            execution = action();
            return execution;
        },
    });

    await mode.update(harness.agent);
    await execution;

    assert.equal(harness.interrupts(), 1);
    assert.equal(harness.placements(), 1);
    assert.equal(mode.inFlight, false);
    assert.equal(mode.currentDecision, null);
    assert.equal(harness.bot.evelynSurvivalState.last_success, false);
    assert.equal(harness.bot.evelynSurvivalState.shelter_success_count, 0);
});

test('hostile flight preempts eating and planning', () => {
    assert.equal(
        selectSurvivalDecision(snapshot({ hostileDistance: 8, foodName: 'bread', hunger: 0 })),
        'handle_hostile',
    );
});

test('critical state retains fallback decisions behind hostile flight', () => {
    assert.deepEqual(
        listSurvivalDecisions(snapshot({ hostileDistance: 16.8, hunger: 0, health: 10, underground: true })),
        ['handle_hostile', 'escape_to_surface'],
    );
});

test('available safe food is eaten before other recovery', () => {
    assert.equal(
        selectSurvivalDecision(snapshot({ foodName: 'bread', hunger: 6, underground: true })),
        'eat_inventory_food',
    );
});

test('carried food is not consumed while a hostile or water owns survival', () => {
    const critical = snapshot({foodName: 'bread', health: 10, hunger: 15});
    assert.deepEqual(
        listSurvivalDecisions({...critical, hostileCount: 1, hostileDistance: 8}),
        ['handle_hostile'],
    );
    assert.equal(selectSurvivalDecision({...critical, inWater: true}), null);
});

test('critical underground agent escapes before searching for food', () => {
    assert.equal(
        selectSurvivalDecision(snapshot({ hunger: 0, health: 10, underground: true })),
        'escape_to_surface',
    );
});

test('surface hunger without food uses deterministic acquisition', () => {
    assert.equal(
        selectSurvivalDecision(snapshot({hunger: 14, foodName: null})),
        'acquire_food',
    );
});

test('a fully equipped agent safely reserves food during daylight', () => {
    const equipped = snapshot({
        timeOfDay: 6000,
        hasPickaxe: true,
        hasMeleeWeapon: true,
        hunger: 20,
        health: 20,
        foodName: null,
    });
    assert.equal(selectSurvivalDecision(equipped), 'acquire_food');
    assert.equal(selectSurvivalDecision({...equipped, hasPickaxe: false}), null);
    assert.equal(selectSurvivalDecision({...equipped, timeOfDay: 13000}), null);
});

test('a full agent keeps newly acquired reserve food instead of consuming it', async () => {
    const origin = new Vec3(13000, 64, 0);
    const crop = {
        name: 'melon',
        type: 1,
        position: origin.offset(1, 0, 0),
        getProperties: () => ({}),
    };
    const items = [];
    let consumeCalls = 0;
    const bot = Object.assign(new EventEmitter(), {
        entity: {position: origin, isInWater: false},
        entities: {},
        health: 20,
        food: 20,
        time: {timeOfDay: 6000},
        interrupt_code: false,
        registry: minecraftData('1.21.11'),
        inventory: {items: () => items, slots: items},
        findBlocks: ({matching}) => matching(crop) ? [crop.position] : [],
        blockAt(position) {
            if (position.equals?.(crop.position)) return crop;
            return position.y <= 63
                ? {name: 'stone', boundingBox: 'block', position}
                : {name: 'air', boundingBox: 'empty', position};
        },
        consume: async () => { consumeCalls += 1; throw new Error('Food is full'); },
        pvp: {stop() {}},
        pathfinder: {stop() {}},
        collectBlock: {
            cancelTask() {},
            async collect() { items.push({name: 'melon_slice', count: 1}); },
        },
    });

    const result = await acquireFood({bot, requestInterrupt() {}});

    assert.equal(result.success, true);
    assert.equal(result.verification, 'food_crop_verified');
    assert.equal(items[0].name, 'melon_slice');
    assert.equal(consumeCalls, 0);
});

test('critical health without food starts safe acquisition before hunger becomes low', () => {
    const critical = snapshot({health: 10, hunger: 15, foodName: null});
    assert.equal(selectSurvivalDecision(critical), 'acquire_food');
    assert.equal(
        selectSurvivalDecision({...critical, hostileCount: 1, hostileDistance: 8}),
        'handle_hostile',
    );
    assert.equal(selectSurvivalDecision({...critical, underground: true}), 'escape_to_surface');
    assert.equal(selectSurvivalDecision({...critical, inWater: true}), null);
    assert.equal(foodAcquisitionAllowed({...critical, inWater: true}), false);
});

test('food acquisition fails closed around hostiles, underground, and water', () => {
    assert.equal(foodAcquisitionAllowed(snapshot({hunger: 6})), true);
    assert.equal(foodAcquisitionAllowed(snapshot({hunger: 6, hostileCount: 1, hostileDistance: 8})), false);
    assert.equal(foodAcquisitionAllowed(snapshot({hunger: 6, hostileDistance: 20})), false);
    assert.equal(foodAcquisitionAllowed(snapshot({hunger: 6, underground: true})), false);
    assert.equal(foodAcquisitionAllowed(snapshot({hunger: 6, inWater: true})), false);
    assert.equal(selectSurvivalDecision(snapshot({hunger: 6, hostileDistance: 8})), 'handle_hostile');
    assert.equal(selectSurvivalDecision(snapshot({hunger: 6, underground: true})), 'escape_to_surface');
});

test('food prey is limited to adult safe land animals', () => {
    assert.equal(isSafeFoodPrey({name: 'cow', metadata: []}), true);
    assert.equal(isSafeFoodPrey({name: 'pig', metadata: []}), true);
    assert.equal(isSafeFoodPrey({name: 'sheep', metadata: []}), true);
    assert.equal(isSafeFoodPrey({name: 'cow', metadata: {[16]: true}}), false);
    assert.equal(isSafeFoodPrey({name: 'chicken', metadata: []}), false);
    assert.equal(isSafeFoodPrey({name: 'rabbit', metadata: []}), false);
    assert.equal(isSafeFoodPrey({name: 'cod', metadata: []}), false);
    assert.equal(isSafeFoodPrey({name: 'cow'}), false);
});

test('food approach follows moving prey and fails closed when the goal is not reached', async () => {
    const origin = new Vec3(12000, 64, 0);
    const prey = {
        id: 77,
        name: 'cow',
        type: 'animal',
        metadata: [],
        position: origin.offset(13, 0, 0),
    };
    let approachGoal = null;
    let stops = 0;
    const bot = Object.assign(new EventEmitter(), {
        entity: {position: origin.clone(), isInWater: false},
        entities: {[prey.id]: prey},
        health: 20,
        food: 6,
        time: {timeOfDay: 6000},
        interrupt_code: false,
        registry: minecraftData('1.21.11'),
        inventory: {items: () => [], slots: []},
        findBlocks: () => [],
        nearestEntity: (predicate) => predicate(prey) ? prey : null,
        blockAt(position) {
            const point = floored(position);
            const solid = point.y <= 63;
            return {
                name: solid ? 'stone' : 'air',
                boundingBox: solid ? 'block' : 'empty',
                position: point,
            };
        },
        pathfinder: {
            thinkTimeout: 0,
            setMovements() {},
            async goto(goal) { approachGoal = goal; },
            stop() { stops += 1; },
            setGoal() {},
        },
        pvp: {stop() {}},
        collectBlock: {cancelTask() {}},
    });
    const agent = {bot, requestInterrupt() {}};

    const result = await acquireFood(agent);

    assert.equal(approachGoal.constructor.name, 'GoalFollow');
    assert.equal(approachGoal.entity, prey);
    assert.equal(result.success, false);
    assert.equal(result.progress, false);
    assert.equal(result.verification, 'food_approach_unreached');
    assert.equal(stops, 1);
});

function foodActionHarness() {
    const calls = {interrupt: 0, pvp: 0, pathfinder: 0, collect: 0};
    const position = {
        x: 0,
        y: 64,
        z: 0,
        distanceTo(other) {
            return Math.hypot(other.x - this.x, other.y - this.y, other.z - this.z);
        },
    };
    const bot = {
        entity: {position, isInWater: false},
        entities: {},
        health: 20,
        interrupt_code: false,
        blockAt: (blockPosition) => ({
            name: 'air',
            boundingBox: 'empty',
            position: blockPosition,
        }),
        pvp: {stop: () => { calls.pvp += 1; }},
        pathfinder: {stop: () => { calls.pathfinder += 1; }},
        collectBlock: {cancelTask: () => { calls.collect += 1; }},
    };
    const agent = {
        bot,
        requestInterrupt() {
            calls.interrupt += 1;
            bot.interrupt_code = true;
            bot.pvp.stop();
            bot.pathfinder.stop();
            bot.collectBlock.cancelTask();
        },
    };
    return {agent, bot, calls};
}

test('food action ignores a vertically separated non-actionable hostile', async () => {
    const {agent, bot, calls} = foodActionHarness();
    bot.entities[2] = {
        name: 'skeleton',
        position: {x: 10, y: 71, z: 0},
    };

    const result = await runFoodAcquisitionAction(agent, async () => true, {
        timeoutMs: 100,
        pollMs: 2,
    });

    assert.equal(result.completed, true);
    assert.equal(result.reason, null);
    assert.equal(calls.interrupt, 0);
});

test('food action aborts and cleans up as soon as a hostile appears', async () => {
    const {agent, bot, calls} = foodActionHarness();
    setTimeout(() => {
        bot.entities[2] = {name: 'zombie', position: {x: 20, y: 64, z: 0}};
    }, 5);
    const result = await runFoodAcquisitionAction(agent, async () => {
        while (!bot.interrupt_code) await new Promise((resolve) => setTimeout(resolve, 2));
        return false;
    }, {timeoutMs: 100, pollMs: 2});

    assert.equal(result.completed, false);
    assert.equal(result.reason, 'hostile_detected');
    assert.equal(calls.interrupt, 1);
    assert.ok(calls.pvp >= 1);
    assert.ok(calls.pathfinder >= 1);
    assert.ok(calls.collect >= 1);
});

test('food action timeout requests cooperative cancellation and cleanup', async () => {
    const {agent, bot, calls} = foodActionHarness();
    const result = await runFoodAcquisitionAction(agent, async () => {
        while (!bot.interrupt_code) await new Promise((resolve) => setTimeout(resolve, 2));
        return false;
    }, {timeoutMs: 12, pollMs: 2});

    assert.equal(result.completed, false);
    assert.equal(result.reason, 'timeout');
    assert.equal(calls.interrupt, 1);
    assert.ok(calls.pvp >= 1);
    assert.ok(calls.pathfinder >= 1);
    assert.ok(calls.collect >= 1);
});

test('food action timeout returns even when the operation never settles', async () => {
    const {agent, calls} = foodActionHarness();
    const startedAt = Date.now();
    const result = await runFoodAcquisitionAction(
        agent,
        () => new Promise(() => {}),
        {timeoutMs: 12, pollMs: 2},
    );

    assert.equal(result.completed, false);
    assert.equal(result.reason, 'timeout');
    assert.ok(Date.now() - startedAt < 250);
    assert.equal(calls.interrupt, 1);
});

test('unknown Mineflayer hostile type aborts a pending food action', async () => {
    const {agent, bot} = foodActionHarness();
    setTimeout(() => {
        bot.entities[3] = {
            name: 'future_hostile',
            type: 'hostile',
            position: {x: 2, y: 64, z: 0},
        };
    }, 5);
    const result = await runFoodAcquisitionAction(
        agent,
        () => new Promise(() => {}),
        {timeoutMs: 100, pollMs: 2},
    );

    assert.equal(result.completed, false);
    assert.equal(result.reason, 'hostile_detected');
});

test('inventory consumption is monitored and yields immediately to a hostile', async () => {
    const {agent, bot, calls} = foodActionHarness();
    const bread = {name: 'bread', count: 1};
    bot.food = 0;
    bot.registry = {foodsByName: {bread: {foodPoints: 5}}};
    bot.inventory = {
        items: () => [bread],
        slots: [bread],
        findInventoryItem: (name) => name === 'bread' ? bread : null,
    };
    bot.equip = async () => {};
    bot.consume = () => new Promise(() => {});
    setTimeout(() => {
        bot.entities[4] = {
            name: 'future_hostile',
            type: 'hostile',
            position: {x: 2, y: 64, z: 0},
        };
    }, 5);

    const result = await acquireFood(agent);

    assert.equal(result.success, false);
    assert.equal(result.interrupted, true);
    assert.equal(result.verification, 'inventory_food_verified_hostile_detected');
    assert.equal(calls.interrupt, 1);
});

test('no local food source hands planner control back for a bounded search window', () => {
    assert.equal(
        recoveryHandoffDelayMs(
            'acquire_food',
            {verification: 'food_source_unavailable'},
            1,
        ),
        30000,
    );
    assert.equal(
        recoveryHandoffDelayMs('acquire_food', {verification: 'food_hunt_timeout'}, 1),
        0,
    );
});

test('underground water escape preempts planning even with full hunger', () => {
    assert.equal(
        selectSurvivalDecision(snapshot({hunger: 20, health: 20, underground: true, inWater: true})),
        'escape_to_surface',
    );
});

test('safe agent without a pickaxe leaves tool recovery to the planner by default', () => {
    assert.equal(
        selectSurvivalDecision(snapshot({ hasPickaxe: false, hunger: 18, health: 20 })),
        null,
    );
});

test('deterministic tool bootstrap remains available as an explicit fallback', () => {
    const enabled = {enableToolBootstrap: true};
    const safe = {timeOfDay: 6000};
    assert.equal(
        selectSurvivalDecision(snapshot({...safe, hasPickaxe: false, hasMeleeWeapon: true}), enabled),
        'bootstrap_tools',
    );
    assert.equal(
        selectSurvivalDecision(snapshot({...safe, hasPickaxe: true, hasMeleeWeapon: false}), enabled),
        'bootstrap_tools',
    );
    assert.equal(
        selectSurvivalDecision(snapshot({...safe, hasPickaxe: true, hasMeleeWeapon: true}), enabled),
        'acquire_food',
    );
    for (const unsafe of [
        {timeOfDay: 13000},
        {...safe, inWater: true},
        {...safe, underground: true},
        {...safe, hostileCount: 1, hostileDistance: 8},
        {...safe, hostileDistance: 20},
    ]) {
        assert.equal(
            listSurvivalDecisions(snapshot({...unsafe, hasMeleeWeapon: false}), enabled)
                .includes('bootstrap_tools'),
            false,
        );
    }
});

test('deterministic tool bootstrap makes a pickaxe and sword from the same three logs', async () => {
    const slots = [];
    const crafted = [];
    let collectedLogs = 0;
    const addItem = (name, count) => {
        const current = slots.find((item) => item?.name === name);
        if (current) current.count += count;
        else slots.push({name, count});
    };
    const consumeItem = (name, count) => {
        const current = slots.find((item) => item?.name === name);
        assert.ok(current?.count >= count, `missing ${name}`);
        current.count -= count;
    };
    const logPosition = new Vec3(0, 64, 1);
    const bot = {
        inventory: {
            slots,
            items: () => slots.filter(Boolean),
        },
        findBlocks({matching}) {
            return matching({name: 'oak_log'}) ? [logPosition] : [];
        },
        blockAt(position) {
            return {name: 'oak_log', position};
        },
        collectBlock: {
            async collect() {
                collectedLogs += 1;
                addItem('oak_log', 1);
            },
        },
    };
    const craftRecipe = async (_bot, name, count) => {
        crafted.push([name, count]);
        if (name === 'oak_planks') {
            consumeItem('oak_log', count);
            addItem(name, count * 4);
        } else if (name === 'stick') {
            consumeItem('oak_planks', count * 2);
            addItem(name, count * 4);
        } else if (name === 'crafting_table') {
            consumeItem('oak_planks', count * 4);
            addItem(name, count);
        } else if (name === 'wooden_pickaxe') {
            consumeItem('oak_planks', count * 3);
            consumeItem('stick', count * 2);
            addItem(name, count);
        } else if (name === 'wooden_sword') {
            consumeItem('oak_planks', count * 2);
            consumeItem('stick', count);
            addItem(name, count);
        }
        return true;
    };

    assert.equal(await bootstrapTools(bot, craftRecipe), true);
    assert.equal(collectedLogs, 3);
    assert.deepEqual(crafted, [
        ['oak_planks', 3],
        ['stick', 1],
        ['crafting_table', 1],
        ['wooden_pickaxe', 1],
        ['wooden_sword', 1],
    ]);
    assert.equal(slots.find((item) => item.name === 'wooden_pickaxe')?.count, 1);
    assert.equal(slots.find((item) => item.name === 'wooden_sword')?.count, 1);
    assert.equal(slots.find((item) => item.name === 'oak_log')?.count, 0);
    assert.equal(slots.find((item) => item.name === 'oak_planks')?.count, 1);
    assert.equal(slots.find((item) => item.name === 'stick')?.count, 1);
});

test('deterministic tool bootstrap stops when a crafting step reports failure', async () => {
    const slots = [{name: 'oak_log', count: 3}];
    const attempted = [];
    const bot = {
        interrupt_code: false,
        inventory: {slots},
    };
    const craftRecipe = async (_bot, name) => {
        attempted.push(name);
        return name !== 'crafting_table';
    };

    assert.equal(await bootstrapTools(bot, craftRecipe), false);
    assert.deepEqual(attempted, ['oak_planks', 'stick', 'crafting_table']);
});

test('deterministic tool bootstrap resumes from prepared table planks and sticks', async () => {
    const slots = [
        {name: 'crafting_table', count: 1},
        {name: 'oak_planks', count: 6},
        {name: 'stick', count: 4},
    ];
    const attempted = [];
    const consume = (name, count) => {
        const item = slots.find((slot) => slot.name === name);
        assert.ok(item?.count >= count, `missing ${name}`);
        item.count -= count;
    };
    const bot = {
        interrupt_code: false,
        inventory: {slots},
        collectBlock: {collect: async () => assert.fail('prepared materials must skip log collection')},
    };
    const craftRecipe = async (_bot, name) => {
        attempted.push(name);
        if (name === 'wooden_pickaxe') {
            consume('oak_planks', 3);
            consume('stick', 2);
        } else if (name === 'wooden_sword') {
            consume('oak_planks', 2);
            consume('stick', 1);
        } else {
            assert.fail(`unexpected prerequisite craft: ${name}`);
        }
        slots.push({name, count: 1});
        return true;
    };

    assert.equal(await bootstrapTools(bot, craftRecipe), true);
    assert.deepEqual(attempted, ['wooden_pickaxe', 'wooden_sword']);
});

test('deterministic tool bootstrap accepts a collected log when pickup navigation times out', async () => {
    const slots = [];
    let forcedClears = 0;
    const bot = {
        interrupt_code: false,
        inventory: {slots},
        entity: {position: new Vec3(0, 64, 0)},
        findBlocks: () => [new Vec3(0, 64, 1)],
        blockAt: (position) => ({name: 'oak_log', position}),
        pathfinder: {
            goal: {},
            setGoal(goal) {
                this.goal = goal;
                if (goal === null) forcedClears++;
            },
        },
        collectBlock: {
            async collect() {
                bot.pathfinder.goal = {};
                const item = slots.find((slot) => slot.name === 'oak_log');
                if (item) item.count++;
                else slots.push({name: 'oak_log', count: 1});
                const error = new Error('pickup path timed out');
                error.name = 'Timeout';
                throw error;
            },
        },
    };
    const craftRecipe = async (_bot, name, count) => {
        if (name === 'oak_planks') {
            slots.find((slot) => slot.name === 'oak_log').count -= count;
            slots.push({name, count: count * 4});
        } else if (name === 'stick') {
            slots.find((slot) => slot.name === 'oak_planks').count -= 2;
            slots.push({name, count: 4});
        } else if (name === 'crafting_table') {
            slots.find((slot) => slot.name === 'oak_planks').count -= 4;
            slots.push({name, count: 1});
        } else if (name === 'wooden_pickaxe') {
            slots.find((slot) => slot.name === 'oak_planks').count -= 3;
            slots.find((slot) => slot.name === 'stick').count -= 2;
            slots.push({name, count: 1});
        } else if (name === 'wooden_sword') {
            slots.find((slot) => slot.name === 'oak_planks').count -= 2;
            slots.find((slot) => slot.name === 'stick').count -= 1;
            slots.push({name, count: 1});
        }
        return true;
    };

    assert.equal(await bootstrapTools(bot, craftRecipe), true);
    assert.equal(forcedClears, 3);
});

function bootstrapCraftRecipeFor(slots) {
    const item = (name) => slots.find((slot) => slot?.name === name);
    const add = (name, count) => {
        const current = item(name);
        if (current) current.count += count;
        else slots.push({name, count});
    };
    const take = (name, count) => { item(name).count -= count; };
    return async (_bot, name, count) => {
        if (name === 'oak_planks') {
            take('oak_log', count);
            add(name, count * 4);
        } else if (name === 'stick') {
            take('oak_planks', count * 2);
            add(name, count * 4);
        } else if (name === 'crafting_table') {
            take('oak_planks', count * 4);
            add(name, count);
        } else if (name === 'wooden_pickaxe') {
            take('oak_planks', count * 3);
            take('stick', count * 2);
            add(name, count);
        } else if (name === 'wooden_sword') {
            take('oak_planks', count * 2);
            take('stick', count);
            add(name, count);
        }
        return true;
    };
}

function bootstrapPathHarness(positions, navigate, collectedLogCount = 3) {
    const slots = [];
    const goals = [];
    const collected = [];
    let calls = 0;
    const bot = Object.assign(new EventEmitter(), {
        interrupt_code: false,
        registry: minecraftData('1.21.11'),
        entities: {},
        inventory: {slots, items: () => slots.filter(Boolean)},
        entity: {position: new Vec3(0, 64, 0)},
        visibleLog: null,
        world: {
            raycast: () => bot.visibleLog ? {position: bot.visibleLog, face: 0} : null,
        },
        findBlocks: () => positions,
        blockAt: (position) => ({name: 'oak_log', position}),
        pathfinder: {
            thinkTimeout: 5000,
            goal: null,
            setMovements() {},
            async goto(goal) {
                this.goal = goal;
                goals.push(goal);
                calls++;
                await navigate({bot, goal, calls});
            },
            stop() { this.goal = null; },
            setGoal(goal) { this.goal = goal; },
        },
        collectBlock: {
            movements: {},
            async collect(block) {
                collected.push(block.position);
                const log = slots.find((slot) => slot.name === 'oak_log');
                if (log) log.count += collectedLogCount;
                else slots.push({name: 'oak_log', count: collectedLogCount});
            },
        },
    });
    return {bot, collected, goals, calls: () => calls, slots};
}

function noBootstrapPath() {
    return Object.assign(new Error('no bootstrap path'), {name: 'NoPath'});
}

test('tool bootstrap approaches a three-high trunk without collateral collection', async () => {
    const slots = [];
    const remainingLogs = [66, 65, 64].map((y) => new Vec3(4, y, 0));
    const collected = [];
    const approaches = [];
    const collectionMovements = {canDig: true, allow1by1towers: true};
    let bottomDirectlyReachable = false;
    let movements = null;
    let pathCalls = 0;
    const key = (position) => `${position.x},${position.y},${position.z}`;
    const add = (name, count) => {
        const item = slots.find((slot) => slot?.name === name);
        if (item) item.count += count;
        else slots.push({name, count});
    };
    const bot = Object.assign(new EventEmitter(), {
        interrupt_code: false,
        registry: minecraftData('1.21.11'),
        entities: {},
        inventory: {slots, items: () => slots.filter(Boolean)},
        entity: {position: new Vec3(0, 64, 0)},
        visibleLog: null,
        world: {
            raycast: () => bot.visibleLog ? {position: bot.visibleLog, face: 0} : null,
        },
        findBlocks({matching, count}) {
            if (typeof matching !== 'function') return [];
            return remainingLogs
                .filter((position) => matching({name: 'oak_log', position}))
                .slice(0, count);
        },
        blockAt(position) {
            const found = remainingLogs.some((candidate) => candidate.equals(position));
            return {name: found ? 'oak_log' : 'air', position};
        },
        canDigBlock(block) {
            return bottomDirectlyReachable && block.position.equals(new Vec3(4, 64, 0));
        },
        canSeeBlock(block) {
            return bottomDirectlyReachable && block.position.equals(new Vec3(4, 64, 0));
        },
        pathfinder: {
            thinkTimeout: 5000,
            goal: null,
            setMovements(value) { movements = value; },
            async goto(goal) {
                this.goal = goal;
                pathCalls++;
                const blocksBefore = remainingLogs.map(key);
                const inventoryBefore = slots.map(({name, count}) => [name, count]);
                if (movements.canDig || movements.allow1by1towers) {
                    remainingLogs.shift();
                    add('oak_log', 1);
                }
                approaches.push({
                    canDig: movements.canDig,
                    allow1by1towers: movements.allow1by1towers,
                    allowParkour: movements.allowParkour,
                    maxDropDown: movements.maxDropDown,
                    scaffoldingCount: movements.scafoldingBlocks.length,
                    downhillPenalty: movements.exclusionAreasStep.reduce(
                        (total, exclusion) => total + exclusion({position: {y: -64}}),
                        0,
                    ),
                    dedicated: movements !== collectionMovements,
                    blocksUnchanged: JSON.stringify(remainingLogs.map(key)) === JSON.stringify(blocksBefore),
                    inventoryUnchanged: JSON.stringify(slots.map(({name, count}) => [name, count]))
                        === JSON.stringify(inventoryBefore),
                });
                const target = goal.goals?.[0]?.pos || goal.pos;
                if (pathCalls === 3) {
                    bot.entity.position = new Vec3(3.75, 64, -0.31);
                    bottomDirectlyReachable = true;
                    throw noBootstrapPath();
                }
                bot.visibleLog = target;
                bot.entity.position = target.offset(-1, 0, 0);
            },
            stop() { this.goal = null; },
            setGoal(goal) { this.goal = goal; },
        },
        collectBlock: {
            movements: collectionMovements,
            async collect(block) {
                const index = remainingLogs.findIndex((candidate) => candidate.equals(block.position));
                assert.notEqual(index, -1, 'collection must receive an existing log');
                collected.push(remainingLogs[index]);
                remainingLogs.splice(index, 1);
                add('oak_log', 1);
            },
        },
    });

    assert.equal(await bootstrapTools(bot, bootstrapCraftRecipeFor(slots)), true);
    assert.deepEqual(approaches, Array.from({length: 3}, () => ({
        canDig: false,
        allow1by1towers: false,
        allowParkour: false,
        maxDropDown: 1,
        scaffoldingCount: 0,
        downhillPenalty: 0,
        dedicated: true,
        blocksUnchanged: true,
        inventoryUnchanged: true,
    })));
    assert.deepEqual(collected.map(({x, y, z}) => [x, y, z]), [
        [4, 66, 0],
        [4, 65, 0],
        [4, 64, 0],
    ]);
    assert.equal(remainingLogs.length, 0);
    assert.equal(slots.find((slot) => slot.name === 'wooden_pickaxe')?.count, 1);
    assert.equal(slots.find((slot) => slot.name === 'wooden_sword')?.count, 1);
    assert.deepEqual({
        phase: bot.evelynSurvivalState.bootstrap_phase,
        candidates: bot.evelynSurvivalState.bootstrap_candidate_count,
        before: bot.evelynSurvivalState.bootstrap_logs_before,
        after: bot.evelynSurvivalState.bootstrap_logs_after,
    }, {phase: 'complete', candidates: 1, before: 2, after: 3});
});

test('tool bootstrap collects a connected trunk in one blocks-first batch', async () => {
    const slots = [];
    const remainingLogs = [66, 65, 64].map((y) => new Vec3(4, y, 0));
    const collected = [];
    let globalSearches = 0;
    let veinSearches = 0;
    let collectCalls = 0;
    const bot = {
        interrupt_code: false,
        inventory: {slots, items: () => slots.filter(Boolean)},
        findBlocks() {
            globalSearches++;
            return globalSearches === 1 ? remainingLogs.slice() : [];
        },
        blockAt(position) {
            const isLog = remainingLogs.some((candidate) => candidate.equals(position));
            return {name: isLog ? 'oak_log' : 'air', type: isLog ? 1 : 0, position};
        },
        collectBlock: {
            findFromVein(block, maxBlocks, maxDistance, floodRadius) {
                veinSearches++;
                assert.equal(block.position.equals(remainingLogs[0]), true);
                assert.deepEqual([maxBlocks, maxDistance, floodRadius], [3, 4, 1]);
                return remainingLogs.slice(0, maxBlocks).map((position) => bot.blockAt(position));
            },
            async collect(blocks, options) {
                collectCalls++;
                assert.equal(Array.isArray(blocks), true);
                assert.deepEqual(options, {blocksFirst: true});
                collected.push(...blocks.map(({position}) => position));
                remainingLogs.length = 0;
                slots.push({name: 'oak_log', count: blocks.length});
            },
        },
    };

    assert.equal(await bootstrapTools(bot, bootstrapCraftRecipeFor(slots)), true);
    assert.equal(globalSearches, 1);
    assert.equal(veinSearches, 1);
    assert.equal(collectCalls, 1);
    assert.deepEqual(collected.map(({y}) => y), [66, 65, 64]);
});

test('tool bootstrap rejects failed-path logs that are not both diggable and visible', async () => {
    for (const reachability of [
        {diggable: false, visible: true},
        {diggable: true, visible: false},
    ]) {
        const position = new Vec3(4, 64, 0);
        const harness = bootstrapPathHarness([position], async () => { throw noBootstrapPath(); });
        harness.bot.canDigBlock = () => reachability.diggable;
        harness.bot.canSeeBlock = () => reachability.visible;

        assert.equal(await bootstrapTools(
            harness.bot,
            bootstrapCraftRecipeFor(harness.slots),
        ), false);
        assert.equal(harness.collected.length, 0);
        assert.equal(harness.calls(), 2);
        assert.equal(harness.bot.evelynSurvivalState.bootstrap_phase, 'no_candidates');
    }
});

test('tool bootstrap falls back from the composite goal to the second exact log goal', async () => {
    const positions = [1, 6, 11, 16].map((x) => new Vec3(x, 64, 0));
    const harness = bootstrapPathHarness(positions, async ({bot, goal, calls}) => {
        if (calls <= 2) throw noBootstrapPath();
        assert.equal(goal.constructor.name, 'GoalLookAtBlock');
        assert.equal(goal.pos.equals(positions[1]), true);
        bot.visibleLog = positions[1];
        bot.entity.position = positions[1].offset(-1, 0, 0);
    });

    assert.equal(await bootstrapTools(harness.bot, bootstrapCraftRecipeFor(harness.slots)), true);
    assert.deepEqual(harness.goals.map((goal) => goal.constructor.name), [
        'GoalCompositeAny',
        'GoalLookAtBlock',
        'GoalLookAtBlock',
    ]);
    assert.equal(harness.collected[0].equals(positions[1]), true);
});

test('tool bootstrap remembers two exhausted batches and skips them on its next invocation', async () => {
    const positions = Array.from({length: 12}, (_, index) => new Vec3(1 + (index * 5), 64, 0));
    let reachable = false;
    const compositeBatches = [];
    const harness = bootstrapPathHarness(positions, async ({bot, goal}) => {
        if (goal.constructor.name === 'GoalCompositeAny') {
            compositeBatches.push(goal.goals.map((candidate) => candidate.pos.x));
        }
        if (!reachable) throw noBootstrapPath();
        const target = goal.goals[0].pos;
        bot.visibleLog = target;
        bot.entity.position = target.offset(-1, 0, 0);
    });
    const craftRecipe = bootstrapCraftRecipeFor(harness.slots);

    assert.equal(await bootstrapTools(harness.bot, craftRecipe), false);
    assert.equal(harness.calls(), 10);
    const remembered = harness.bot.evelynBootstrapFailedLogClusters.map((position) => position.x);
    assert.deepEqual(remembered, positions.slice(0, 8).map((position) => position.x));

    reachable = true;
    assert.equal(await bootstrapTools(harness.bot, craftRecipe), true);
    assert.equal(harness.calls(), 11);
    assert.equal(compositeBatches.length, 3);
    assert.equal(compositeBatches[2].every((x) => !remembered.includes(x)), true);
    assert.deepEqual(harness.bot.evelynBootstrapFailedLogClusters, []);
});

test('tool bootstrap does not cycle back after more than thirty-two failed clusters', async () => {
    const positions = Array.from({length: 41}, (_, index) => new Vec3(
        (index % 7) * 6 - 18,
        64,
        Math.floor(index / 7) * 6 - 15,
    ));
    const reachable = positions.at(-1);
    const harness = bootstrapPathHarness(positions, async ({bot, goal}) => {
        const target = goal.constructor.name === 'GoalCompositeAny'
            ? goal.goals.find((candidate) => candidate.pos.equals(reachable))
            : (goal.pos.equals(reachable) ? goal : null);
        if (!target) throw noBootstrapPath();
        bot.visibleLog = reachable;
        bot.entity.position = reachable.offset(-1, 0, 0);
    });
    const craftRecipe = bootstrapCraftRecipeFor(harness.slots);

    for (let attempt = 0; attempt < 5; attempt++) {
        assert.equal(await bootstrapTools(harness.bot, craftRecipe), false);
    }
    assert.equal(harness.bot.evelynBootstrapFailedLogClusters.length, 40);
    assert.equal(await bootstrapTools(harness.bot, craftRecipe), true);
    assert.equal(harness.collected[0].equals(reachable), true);
    assert.deepEqual(harness.bot.evelynBootstrapFailedLogClusters, []);
});

test('tool bootstrap interrupt stops the exact-goal fallback without excluding the batch', async () => {
    const positions = [1, 6, 11, 16].map((x) => new Vec3(x, 64, 0));
    const harness = bootstrapPathHarness(positions, async ({bot, calls}) => {
        if (calls === 2) bot.interrupt_code = true;
        throw noBootstrapPath();
    });

    assert.equal(await bootstrapTools(harness.bot, bootstrapCraftRecipeFor(harness.slots)), false);
    assert.equal(harness.calls(), 2);
    assert.equal(harness.collected.length, 0);
    assert.deepEqual(harness.bot.evelynBootstrapFailedLogClusters, []);
});

test('tool bootstrap applies failed-cluster exclusions before the nearest-block result cap', async () => {
    const slots = [];
    const firstCluster = new Vec3(0, 64, 0);
    const secondCluster = new Vec3(10, 64, 0);
    const reachable = new Vec3(20, 64, 0);
    const positions = [
        ...Array.from({length: 32}, (_, index) => new Vec3(1, 64 + index, 0)),
        ...Array.from({length: 32}, (_, index) => new Vec3(11, 64 + index, 0)),
        reachable,
    ];
    let paletteChecks = 0;
    let collected = null;
    const bot = {
        interrupt_code: false,
        evelynBootstrapFailedLogClusters: [firstCluster, secondCluster],
        inventory: {slots, items: () => slots.filter(Boolean)},
        findBlocks({matching, count}) {
            if (typeof matching !== 'function') return [];
            if (matching({name: 'oak_log', position: null})) paletteChecks++;
            return positions
                .filter((position) => matching({name: 'oak_log', position}))
                .slice(0, count);
        },
        blockAt: (position) => ({name: 'oak_log', position}),
        collectBlock: {
            async collect(block) {
                collected = block.position;
                slots.push({name: 'oak_log', count: 3});
            },
        },
    };

    assert.equal(await bootstrapTools(bot, bootstrapCraftRecipeFor(slots)), true);
    assert.equal(collected.equals(reachable), true);
    assert.ok(paletteChecks > 0);
    assert.deepEqual(bot.evelynBootstrapFailedLogClusters, []);
});

test('tool bootstrap preemption during a no-progress collection does not poison the candidate', async () => {
    const position = new Vec3(1, 64, 0);
    const bot = {
        interrupt_code: false,
        inventory: {slots: []},
        findBlocks: ({matching}) => typeof matching === 'function' ? [position] : [],
        blockAt: (value) => ({name: 'oak_log', position: value}),
        collectBlock: {
            async collect() { bot.interrupt_code = true; },
        },
    };

    assert.equal(await bootstrapTools(bot, async () => true), false);
    assert.deepEqual(bot.evelynBootstrapFailedLogClusters, []);
});

test('tool bootstrap waits for delayed inventory slot updates after collection', async () => {
    const slots = [];
    const position = new Vec3(1, 64, 0);
    let collections = 0;
    const bot = {
        interrupt_code: false,
        inventory: {slots, items: () => slots.filter(Boolean)},
        findBlocks: ({matching}) => typeof matching === 'function' ? [position] : [],
        blockAt: (value) => ({name: 'oak_log', position: value}),
        collectBlock: {
            async collect() {
                collections++;
                setTimeout(() => {
                    const log = slots.find((slot) => slot.name === 'oak_log');
                    if (log) log.count++;
                    else slots.push({name: 'oak_log', count: 1});
                }, 10);
            },
        },
    };

    assert.equal(await bootstrapTools(bot, bootstrapCraftRecipeFor(slots)), true);
    assert.equal(collections, 3);
    assert.equal(slots.find((slot) => slot.name === 'wooden_pickaxe')?.count, 1);
});

test('tool bootstrap bounds inventory settling when collection makes no progress', async () => {
    const position = new Vec3(1, 64, 0);
    const bot = {
        interrupt_code: false,
        inventory: {slots: [], items: () => []},
        findBlocks: ({matching}) => typeof matching === 'function' ? [position] : [],
        blockAt: (value) => ({name: 'oak_log', position: value}),
        collectBlock: {collect: async () => {}},
    };
    const originalSetTimeout = globalThis.setTimeout;
    const delays = [];
    globalThis.setTimeout = (callback, delayMs) => {
        delays.push(delayMs);
        return originalSetTimeout(callback, 0);
    };

    try {
        assert.equal(await bootstrapTools(bot, async () => true), false);
    } finally {
        globalThis.setTimeout = originalSetTimeout;
    }
    assert.deepEqual(delays, Array.from({length: 20}, () => 50));
    assert.equal(bot.evelynSurvivalState.bootstrap_phase, 'no_candidates');
});

test('tool bootstrap retries a mined no-delta trunk but remains bounded', async () => {
    const remaining = [66, 65, 64].map((y) => new Vec3(1, y, 0));
    const collected = [];
    const bot = {
        interrupt_code: false,
        inventory: {slots: [], items: () => []},
        findBlocks: ({matching}) => typeof matching === 'function' ? remaining : [],
        blockAt(position) {
            return {
                name: remaining.some((candidate) => candidate.equals(position)) ? 'oak_log' : 'air',
                position,
            };
        },
        collectBlock: {
            async collect(block) {
                collected.push(block.position);
                const index = remaining.findIndex((candidate) => candidate.equals(block.position));
                assert.notEqual(index, -1);
                remaining.splice(index, 1);
            },
        },
    };
    const originalSetTimeout = globalThis.setTimeout;
    const delays = [];
    globalThis.setTimeout = (callback, delayMs) => {
        delays.push(delayMs);
        return originalSetTimeout(callback, 0);
    };

    try {
        assert.equal(await bootstrapTools(bot, async () => true), false);
    } finally {
        globalThis.setTimeout = originalSetTimeout;
    }
    assert.deepEqual(collected.map(({y}) => y), [66, 65]);
    assert.deepEqual(delays, Array.from({length: 40}, () => 50));
    assert.equal(remaining.length, 1);
    assert.deepEqual(bot.evelynBootstrapFailedLogClusters, []);
});

test('deterministic tool bootstrap approaches the easiest separated log with one composite goal', async () => {
    const slots = [];
    const first = new Vec3(1, 64, 0);
    const second = new Vec3(8, 64, 0);
    const attempts = [];
    const selectedGoals = [];
    const bot = Object.assign(new EventEmitter(), {
        interrupt_code: false,
        registry: minecraftData('1.21.11'),
        entities: {},
        inventory: {slots},
        entity: {position: new Vec3(0, 64, 0)},
        world: {
            raycast: () => ({position: second, face: 0}),
        },
        findBlocks: () => [first, new Vec3(1, 65, 0), second],
        blockAt: (position) => ({name: 'oak_log', position}),
        pathfinder: {
            thinkTimeout: 5000,
            goal: null,
            setMovements: () => {},
            async goto(goal) {
                this.goal = goal;
                selectedGoals.push(goal);
                bot.entity.position = new Vec3(7, 64, 0);
            },
            stop() { this.goal = null; },
            setGoal(goal) { this.goal = goal; },
        },
        collectBlock: {
            movements: {},
            async collect(block) {
                attempts.push(block.position.x);
                const item = slots.find((slot) => slot.name === 'oak_log');
                if (item) item.count++;
                else slots.push({name: 'oak_log', count: 1});
            },
        },
    });
    const craftRecipe = async (_bot, name, count) => {
        const countOf = (itemName) => slots.find((slot) => slot.name === itemName);
        const add = (itemName, amount) => {
            const item = countOf(itemName);
            if (item) item.count += amount;
            else slots.push({name: itemName, count: amount});
        };
        if (name === 'oak_planks') {
            countOf('oak_log').count -= count;
            add(name, count * 4);
        } else if (name === 'stick') {
            countOf('oak_planks').count -= 2;
            add(name, 4);
        } else if (name === 'crafting_table') {
            countOf('oak_planks').count -= 4;
            add(name, 1);
        } else if (name === 'wooden_pickaxe') {
            countOf('oak_planks').count -= 3;
            countOf('stick').count -= 2;
            add(name, 1);
        } else if (name === 'wooden_sword') {
            countOf('oak_planks').count -= 2;
            countOf('stick').count -= 1;
            add(name, 1);
        }
        return true;
    };

    assert.equal(await bootstrapTools(bot, craftRecipe), true);
    assert.deepEqual(attempts, [second.x, second.x, second.x]);
    assert.equal(selectedGoals.length, 3);
    assert.equal(selectedGoals.every((goal) => goal.constructor.name === 'GoalCompositeAny'), true);
});

test('deterministic tool bootstrap cleans a navigation timeout without masking other errors', async (t) => {
    const createBot = (errorName, addLog = false) => {
        const position = new Vec3(1, 64, 0);
        const slots = [];
        let clears = 0;
        const bot = {
            interrupt_code: false,
            inventory: {slots},
            entity: {position: new Vec3(0, 64, 0)},
            findBlocks: () => [position],
            blockAt: (value) => ({name: 'oak_log', position: value}),
            pathfinder: {
                goal: null,
                setGoal(goal) { this.goal = goal; if (goal === null) clears++; },
            },
            collectBlock: {
                async collect() {
                    bot.pathfinder.goal = {};
                    if (addLog) slots.push({name: 'oak_log', count: 1});
                    const error = new Error(errorName);
                    error.name = errorName;
                    throw error;
                },
            },
        };
        return {bot, clears: () => clears, slots};
    };

    await t.test('recoverable timeout', async () => {
        const {bot, clears} = createBot('Timeout');
        assert.equal(await bootstrapTools(bot, async () => true), false);
        assert.equal(clears(), 1);
    });
    await t.test('unexpected runtime error', async () => {
        const {bot, clears} = createBot('TypeError');
        await assert.rejects(bootstrapTools(bot, async () => true), {name: 'TypeError'});
        assert.equal(clears(), 1);
    });
    await t.test('unexpected runtime error with inventory progress', async () => {
        const {bot, clears, slots} = createBot('TypeError', true);
        await assert.rejects(bootstrapTools(bot, async () => true), {name: 'TypeError'});
        assert.equal(slots[0]?.count, 1);
        assert.equal(clears(), 1);
    });
});

test('deterministic tool bootstrap stops at the first cooperative interrupt checkpoint', async () => {
    const slots = [];
    let crafted = 0;
    const bot = {
        interrupt_code: false,
        inventory: {slots},
        findBlocks: () => [new Vec3(0, 64, 1)],
        blockAt: (position) => ({name: 'oak_log', position}),
        collectBlock: {
            async collect() {
                slots.push({name: 'oak_log', count: 1});
                bot.interrupt_code = true;
            },
        },
    };

    assert.equal(await bootstrapTools(bot, async () => { crafted += 1; }), false);
    assert.equal(crafted, 0);
});

test('safe equipped agent leaves control to the planner', () => {
    assert.equal(selectSurvivalDecision(snapshot()), null);
});

test('healthy melee agent avoids unprotected night combat', () => {
    assert.equal(selectHostileTactic(snapshot({
        hostileDistance: 5,
        hostileName: 'zombie',
        hostileId: 41,
        hostileCount: 1,
        hasMeleeWeapon: true,
        health: 20,
        hunger: 20,
        timeOfDay: 6000,
    })), 'fight');
    assert.equal(selectHostileTactic(snapshot({
        hostileDistance: 5,
        hostileName: 'zombie',
        hostileId: 41,
        hostileCount: 1,
        hasMeleeWeapon: true,
        health: 20,
        hunger: 20,
        timeOfDay: 13000,
    })), 'flee');
});

test('unarmed, critically weak, and boss-grade threats are fled from', () => {
    const base = {
        hostileDistance: 5,
        hostileName: 'zombie',
        hostileId: 41,
        hostileCount: 1,
        hasMeleeWeapon: true,
        health: 20,
        hunger: 20,
    };
    assert.equal(selectHostileTactic(snapshot({ ...base, hasMeleeWeapon: false })), 'flee');
    assert.equal(selectHostileTactic(snapshot({ ...base, health: 6 })), 'flee');
    assert.equal(selectHostileTactic(snapshot({
        ...base,
        hostileName: 'warden',
        hostiles: [{id: 41, name: 'warden', distance: 5}],
    })), 'flee');
});

test('tactical flight preserves direct recovery sprint until the threat is outside the safe radius', () => {
    for (const pressure of [
        {hasMeleeWeapon: false, weaponPower: 0, hostileName: 'husk'},
        {
            hasMeleeWeapon: true,
            weaponPower: 6,
            hostileCount: 2,
            hostiles: [{name: 'husk'}, {name: 'zombie'}],
        },
        {hasMeleeWeapon: true, weaponPower: 6, hostileName: 'witch'},
    ]) {
        assert.deepEqual(hostileFleeEscapeOptions(snapshot({
            hostileDistance: 15.6,
            hostileCount: 1,
            ...pressure,
        }), 0), {
            failureCount: 1,
            forceSprint: true,
            stopOnStall: false,
        });
    }

    assert.deepEqual(hostileFleeEscapeOptions(snapshot({
        health: 6,
        hostileDistance: 5,
        hostileName: 'husk',
        hostileCount: 1,
        hasMeleeWeapon: true,
        weaponPower: 6,
    }), 0), {
        failureCount: 1,
        forceSprint: true,
        stopOnStall: false,
    });
});

test('failed single-zombie flight has one narrow emergency melee fallback', () => {
    const candidate = snapshot({
        hostileDistance: 5,
        hostileName: 'zombie',
        hostileId: 41,
        hostileCount: 1,
        hasMeleeWeapon: true,
        weaponPower: 4,
        health: 20,
        timeOfDay: 13000,
    });
    assert.equal(singleZombieMeleeFallbackAllowed(candidate, 1), false);
    assert.equal(singleZombieMeleeFallbackAllowed(candidate, 2), true);
    assert.equal(singleZombieMeleeFallbackAllowed({...candidate, health: 10}, 0), true);
    assert.equal(singleZombieMeleeFallbackAllowed({...candidate, hostileDistance: 3}, 0), true);

    for (const unsafe of [
        {...candidate, hostileName: 'skeleton'},
        {...candidate, hostileCount: 2},
        {...candidate, hostileDistance: 8.01},
        {...candidate, hasMeleeWeapon: false},
        {...candidate, inWater: true},
        {...candidate, health: 0},
    ]) {
        assert.equal(singleZombieMeleeFallbackAllowed(unsafe, 2), false);
    }
});

test('emergency melee counts only consecutive flee failures against the same target', () => {
    const flee = (hostileId, extra = {}) => ({
        tactic: 'flee',
        before: {hostileId, hostileName: 'zombie'},
        after: {hostileId, hostileName: 'zombie'},
        ...extra,
    });
    let streak = advanceHostileFleeFailureStreak({}, flee(41), false);
    assert.deepEqual(streak, {targetKey: '41:zombie', count: 1});
    streak = advanceHostileFleeFailureStreak(streak, flee(41), false);
    assert.deepEqual(streak, {targetKey: '41:zombie', count: 2});
    assert.deepEqual(
        advanceHostileFleeFailureStreak(streak, {tactic: 'fight', before: flee(41).before}, false),
        {targetKey: null, count: 0},
    );
    assert.deepEqual(
        advanceHostileFleeFailureStreak(streak, flee(42), false),
        {targetKey: '42:zombie', count: 1},
    );
    assert.deepEqual(
        advanceHostileFleeFailureStreak(streak, flee(41), true),
        {targetKey: null, count: 0},
    );
    assert.deepEqual(
        advanceHostileFleeFailureStreak({}, flee(41, {
            verification: 'emergency_melee_handoff',
        }), false),
        {targetKey: '41:zombie', count: 2},
    );
    assert.deepEqual(
        advanceHostileFleeFailureStreak({}, flee(41, {
            verification: 'emergency_melee_handoff',
            after: {hostileId: 42, hostileName: 'zombie'},
        }), false),
        {targetKey: '41:zombie', count: 1},
    );
});

test('external flee completion is rejected while a hostile remains close', () => {
    const before = snapshot({ hostileDistance: 6, hostileName: 'husk', hostileId: 9, hostileCount: 1 });
    const after = snapshot({ hostileDistance: 7, hostileName: 'husk', hostileId: 9, hostileCount: 1 });
    assert.equal(verifyHostileOutcome('flee', before, after, true), false);
});

test('flee succeeds only after the immediate threat is gone or beyond the safe radius', () => {
    const before = snapshot({ hostileDistance: 6, hostileName: 'husk', hostileId: 9, hostileCount: 1 });
    assert.equal(verifyHostileOutcome('flee', before, snapshot(), true), true);
    assert.equal(verifyHostileOutcome('flee', before, snapshot({
        hostileDistance: 20,
        hostileName: 'husk',
        hostileId: 9,
        hostileCount: 0,
    }), true), true);
});

test('fight completion requires both a safe radius and the original target to be gone', () => {
    const before = snapshot({ hostileDistance: 3, hostileName: 'zombie', hostileId: 12, hostileCount: 1 });
    assert.equal(verifyHostileOutcome('fight', before, snapshot(), true), true);
    assert.equal(verifyHostileOutcome('fight', before, snapshot({
        hostileDistance: 20,
        hostileName: 'zombie',
        hostileId: 12,
        hostileCount: 0,
    }), true), false);
    assert.equal(verifyHostileOutcome('fight', before, snapshot(), false), false);
});

test('unsafe foods are never selected', () => {
    const bot = {
        registry: {
            foodsByName: {
                bread: { foodPoints: 5 },
                rotten_flesh: { foodPoints: 4 },
            },
        },
        inventory: {
            items: () => [
                { name: 'rotten_flesh' },
                { name: 'bread' },
            ],
        },
    };
    assert.equal(selectBestFood(bot).name, 'bread');
});
