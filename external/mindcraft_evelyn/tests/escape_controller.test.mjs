import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import minecraftData from 'minecraft-data';
import {
    buildEscapeCandidates,
    chooseEscapeCandidate,
    escapeCoverScore,
    escapeFromHostiles,
    MAX_INTERRUPT_OPTOUT_MS,
    sprintEscapeBurst,
    sprintEscapeCorridorIsViable,
} from '../src/agent/evelyn_escape_controller.js';

function hostile(x, z = 0, distance = Math.hypot(x, z)) {
    return {distance, entity: {position: {x, y: 64, z}}};
}

function escapeHarness({health = 20, threats = [hostile(2)], damageAfterBurst = false} = {}) {
    const controls = [];
    const looks = [];
    let bursts = 0;
    const bot = {
        entity: {position: {x: 0, y: 64, z: 0}},
        health,
        interrupt_code: false,
        registry: {blocksByName: {}},
        pathfinder: {
            goal: {kind: 'stale'},
            stop() {},
            setMovements() {},
            setGoal(goal) { this.goal = goal; },
        },
        blockAt(position) {
            return position.y <= 63
                ? {name: 'stone', boundingBox: 'block', position}
                : {name: 'air', boundingBox: 'empty', position};
        },
        clearControlStates() {},
        async lookAt(target) { looks.push(target); },
        setControlState(name, enabled) {
            controls.push([name, enabled]);
            if (name === 'jump' && !enabled) {
                bursts += 1;
                if (damageAfterBurst && bursts === 1) this.health -= 4;
            }
        },
    };
    return {bot, controls, looks, hostileProvider: () => threats};
}

test('a committed heading prevents a close pair replan from reversing direction', () => {
    const origin = {x: 0, y: 64, z: 0};
    const threats = [
        {distance: 4.2, entity: {name: 'husk', position: {x: -3, y: 64, z: -3}}},
        {distance: 2, entity: {name: 'witch', position: {x: -2, y: 64, z: 0}}},
    ];
    const candidates = buildEscapeCandidates(origin, threats, {
        recoveryMode: true,
        radii: [14],
    });
    const selected = chooseEscapeCandidate(candidates, threats, {
        origin,
        recoveryMode: true,
        preferredDirection: {x: 0, z: 1},
        directionWeight: 4,
    });

    assert.ok(selected.z > 0, `expected continued northbound escape, got ${selected.x},${selected.z}`);
});

test('a committed heading yields when the opposite route is materially safer', () => {
    const selected = chooseEscapeCandidate(
        [{x: 0, y: 64, z: 14}, {x: 0, y: 64, z: -14}],
        [hostile(0, 4, 4)],
        {
            origin: {x: 0, y: 64, z: 0},
            preferredDirection: {x: 0, z: 1},
            directionWeight: 4,
        },
    );

    assert.ok(selected.z < 0);
});

test('successive pair-pressure reflexes retain a safe escape heading', async () => {
    let threats = [
        {distance: 4.2, entity: {name: 'husk', position: {x: -3, y: 64, z: -3}}},
        {distance: 2.8, entity: {name: 'witch', position: {x: -2, y: 64, z: -2}}},
    ];
    const harness = escapeHarness();
    const hostileProvider = () => threats;
    await escapeFromHostiles(harness.bot, {
        failureCount: 1,
        hostileProvider,
        timeoutMs: 100,
        burstMs: 1,
        forceSprint: true,
    });
    const first = harness.looks[0];
    const secondStart = harness.looks.length;
    threats = [
        {distance: 4.2, entity: {name: 'husk', position: {x: -3, y: 64, z: -3}}},
        {distance: 2, entity: {name: 'witch', position: {x: -2, y: 64, z: 0}}},
    ];
    await escapeFromHostiles(harness.bot, {
        failureCount: 1,
        hostileProvider,
        timeoutMs: 100,
        burstMs: 1,
        forceSprint: true,
    });
    const second = harness.looks[secondStart];
    const firstDirection = {x: first.x - 0.5, z: first.z - 0.5};
    const secondDirection = {x: second.x - 0.5, z: second.z - 0.5};

    assert.ok(
        firstDirection.x * secondDirection.x + firstDirection.z * secondDirection.z > 0,
        `escape heading reversed from ${JSON.stringify(firstDirection)} to ${JSON.stringify(secondDirection)}`,
    );
    assert.equal(harness.bot.evelynMovementOwner, null);
});

test('forced sprint rejects a blocked straight corridor but permits one-block terrain', () => {
    const blocked = new Set();
    const bot = {
        blockAt(position) {
            if (blocked.has(`${position.x},${position.y},${position.z}`)) {
                return {name: 'stone', boundingBox: 'block', position};
            }
            return position.y <= 63
                ? {name: 'stone', boundingBox: 'block', position}
                : {name: 'air', boundingBox: 'empty', position};
        },
    };
    const origin = {x: 0, y: 64, z: 0};
    const candidate = {x: 6, y: 64, z: 0};

    assert.equal(sprintEscapeCorridorIsViable(bot, origin, candidate, 63), true);
    blocked.add('3,64,0');
    blocked.add('3,65,0');
    assert.equal(sprintEscapeCorridorIsViable(bot, origin, candidate, 63), false);
});

test('forced sprint falls back to safe pathing when every straight corridor is blocked', async () => {
    let fallbackStarted = false;
    let movementsSet = 0;
    let ownerWhenCleared = null;
    const goals = [];
    const bot = {
        entity: {position: {x: 0, y: 64, z: 0}},
        health: 20,
        interrupt_code: false,
        registry: minecraftData('1.21.11'),
        pathfinder: {
            goal: null,
            stop() {},
            setMovements() { movementsSet += 1; },
            setGoal(goal) {
                this.goal = goal;
                if (!goal) {
                    ownerWhenCleared = bot.evelynMovementOwner;
                    return;
                }
                goals.push(goal);
                fallbackStarted = true;
            },
        },
        blockAt(position) {
            const wall = (
                Math.max(Math.abs(position.x), Math.abs(position.z)) === 1 &&
                (position.y === 64 || position.y === 65)
            );
            return position.y <= 63 || wall
                ? {name: 'stone', boundingBox: 'block', position}
                : {name: 'air', boundingBox: 'empty', position};
        },
    };

    const result = await escapeFromHostiles(bot, {
        failureCount: 1,
        hostileProvider: () => [hostile(2)],
        timeoutMs: 500,
        burstMs: 250,
        forceSprint: true,
        abortReasonProvider: () => fallbackStarted ? 'emergency_melee_handoff' : null,
    });

    assert.equal(result.verification, 'emergency_melee_handoff');
    assert.equal(movementsSet, 1);
    assert.equal(goals.length, 1);
    assert.equal(goals[0].constructor.name, 'GoalNear');
    assert.equal(ownerWhenCleared, 'evelyn_hostile_escape');
    assert.equal(bot.pathfinder.goal, null);
    assert.equal(bot.evelynMovementOwner, null);
});

test('an incomplete raw sprint keeps its lease and falls back to safe pathing', async () => {
    const threats = [hostile(12)];
    const harness = escapeHarness({threats});
    const goals = [];
    harness.bot.registry = minecraftData('1.21.11');
    harness.bot.pathfinder.stop = function () { this.goal = null; };
    harness.bot.pathfinder.setGoal = function (goal) {
        this.goal = goal;
        if (!goal) return;
        goals.push(goal);
        threats.length = 0;
    };

    const result = await escapeFromHostiles(harness.bot, {
        failureCount: 1,
        hostileProvider: harness.hostileProvider,
        timeoutMs: 500,
        burstMs: 1,
        forceSprint: true,
        abortReasonProvider: () => null,
    });

    assert.equal(result.success, true);
    assert.equal(result.verification, 'safe_radius');
    assert.equal(goals.length, 1);
    assert.equal(goals[0].constructor.name, 'GoalNear');
    assert.equal(harness.bot.pathfinder.goal, null);
    assert.equal(harness.bot.evelynMovementOwner, null);
});

test('an urgent threat promotes path fallback back to raw sprint', async () => {
    const threats = [hostile(12)];
    const harness = escapeHarness({threats});
    let pathGoals = 0;
    let sprintStarts = 0;
    const originalSetControlState = harness.bot.setControlState;
    harness.bot.registry = minecraftData('1.21.11');
    harness.bot.pathfinder.setGoal = function (goal) {
        this.goal = goal;
        if (!goal) return;
        pathGoals += 1;
        threats[0] = hostile(3);
    };
    harness.bot.setControlState = function (name, enabled) {
        originalSetControlState.call(this, name, enabled);
        if (name === 'forward' && enabled && ++sprintStarts === 2) threats.length = 0;
    };

    const result = await escapeFromHostiles(harness.bot, {
        failureCount: 1,
        hostileProvider: harness.hostileProvider,
        timeoutMs: 500,
        burstMs: 1,
        forceSprint: true,
        abortReasonProvider: () => null,
    });

    assert.equal(result.success, true);
    assert.equal(pathGoals, 1);
    assert.equal(sprintStarts, 2);
    assert.equal(harness.bot.pathfinder.goal, null);
    assert.equal(harness.bot.evelynMovementOwner, null);
});

test('a one-shot tactical abort never falls through to pathing', async () => {
    const harness = escapeHarness();
    const goals = [];
    let polls = 0;
    harness.bot.registry = minecraftData('1.21.11');
    harness.bot.pathfinder.setGoal = function (goal) {
        this.goal = goal;
        if (goal) goals.push(goal);
    };

    const result = await escapeFromHostiles(harness.bot, {
        hostileProvider: harness.hostileProvider,
        timeoutMs: 500,
        burstMs: 250,
        forceSprint: true,
        abortReasonProvider: () => (++polls === 2 ? 'emergency_melee_handoff' : null),
    });

    assert.equal(result.verification, 'emergency_melee_handoff');
    assert.equal(goals.length, 0);
    assert.equal(harness.bot.evelynMovementOwner, null);
});

test('flat sprint-jump descent stays viable and stops at the verified waypoint', async () => {
    const controls = [];
    let threatPolls = 0;
    const position = {x: 0.5, y: 64, z: 0.5};
    const bot = Object.assign(new EventEmitter(), {
        entity: {position, velocity: {y: 0}},
        health: 20,
        interrupt_code: false,
        pathfinder: {stop() {}},
        blockAt(blockPosition) {
            return blockPosition.y <= 63
                ? {name: 'stone', boundingBox: 'block', position: blockPosition}
                : {name: 'air', boundingBox: 'empty', position: blockPosition};
        },
        clearControlStates() {},
        async lookAt() {},
        setControlState(name, enabled) {
            controls.push([name, enabled]);
            if (name === 'jump' && enabled) {
                for (const [y, velocityY] of [
                    [64.42, 0.42], [64.75, 0.33], [65.0, 0.16],
                    [65.2492, 0.08], [65.2522, 0], [65.1768, -0.08],
                    [65.0244, -0.15], [64.7967, -0.23], [64.4956, -0.3],
                ]) {
                    position.y = y;
                    this.entity.velocity.y = velocityY;
                    this.emit('physicsTick');
                }
                position.x = 4.5;
                position.y = 64;
                position.z = 0.5;
                this.entity.velocity.y = 0;
                this.emit('physicsTick');
            }
        },
    });
    const startedAt = Date.now();

    assert.equal(await sprintEscapeBurst(bot, {x: 4, y: 64, z: 0}, {
        durationMs: 1000,
        threatProvider() {
            threatPolls += 1;
            return [hostile(2)];
        },
    }), true);

    assert.ok(Date.now() - startedAt < 250);
    assert.equal(threatPolls, 0);
    assert.deepEqual(controls.slice(-3), [
        ['forward', false],
        ['sprint', false],
        ['jump', false],
    ]);
});

test('runtime support loss stops direct flee before its next threat poll and returns fixed evidence', async () => {
    const controls = new Map();
    const calls = [];
    let supported = true;
    let threatPolls = 0;
    const bot = Object.assign(new EventEmitter(), {
        entity: {position: {x: 0, y: 64, z: 0}, velocity: {y: 0}},
        health: 20,
        interrupt_code: false,
        registry: {blocksByName: {}},
        pathfinder: {
            stop() {},
            setMovements() {},
            setGoal() {},
        },
        blockAt(position) {
            return supported && position.y <= 63
                ? {name: 'stone', boundingBox: 'block', position}
                : {name: 'air', boundingBox: 'empty', position};
        },
        clearControlStates() {},
        async lookAt() {},
        setControlState(name, enabled) {
            controls.set(name, enabled);
            calls.push([name, enabled]);
        },
    });
    const resultPromise = escapeFromHostiles(bot, {
        failureCount: 1,
        hostileProvider() {
            threatPolls += 1;
            return [hostile(2)];
        },
        timeoutMs: 1000,
        burstMs: 1000,
        forceSprint: true,
        stopOnStall: false,
    });
    await Promise.resolve();
    const pollsBeforeLoss = threatPolls;

    supported = false;
    bot.emit('physicsTick');

    assert.equal(controls.get('forward'), false);
    assert.equal(controls.get('sprint'), false);
    assert.equal(controls.get('jump'), false);
    assert.equal(threatPolls, pollsBeforeLoss);
    const result = await resultPromise;
    assert.equal(result.verification, 'fall_risk');
    assert.equal(bot.evelynMovementOwner, null);
    assert.deepEqual(calls.slice(-3), [
        ['forward', false],
        ['sprint', false],
        ['jump', false],
    ]);
});

test('a normal sprint-jump onto one-block lower ground is not treated as a fall', async () => {
    const position = {x: 0.5, y: 64, z: 0.5};
    const bot = Object.assign(new EventEmitter(), {
        entity: {position, velocity: {y: 0}},
        health: 20,
        interrupt_code: false,
        pathfinder: {stop() {}},
        blockAt(blockPosition) {
            const floorY = blockPosition.x >= 3 ? 62 : 63;
            return blockPosition.y <= floorY
                ? {name: 'stone', boundingBox: 'block', position: blockPosition}
                : {name: 'air', boundingBox: 'empty', position: blockPosition};
        },
        clearControlStates() {},
        async lookAt() {},
        setControlState(name, enabled) {
            if (name !== 'jump' || !enabled) return;
            for (const [y, velocityY] of [
                [64.42, 0.42], [64.75, 0.33], [65.0, 0.16],
                [65.2522, 0], [65.1768, -0.08], [64.7967, -0.23],
                [64.4956, -0.3],
            ]) {
                if (velocityY <= 0) position.x = 4.5;
                position.y = y;
                this.entity.velocity.y = velocityY;
                this.emit('physicsTick');
            }
            position.y = 63;
            this.entity.velocity.y = 0;
            this.emit('physicsTick');
        },
    });

    assert.equal(await sprintEscapeBurst(bot, {x: 4, y: 63, z: 0}, {
        durationMs: 1000,
        threatProvider: () => [hostile(2)],
    }), true);
});

test('movement ownership is released even when pathfinder cleanup throws', async () => {
    const harness = escapeHarness({threats: []});
    harness.bot.pathfinder.stop = () => { throw new Error('cleanup failed'); };

    await assert.rejects(
        escapeFromHostiles(harness.bot, {hostileProvider: () => [], timeoutMs: 10}),
        /cleanup failed/,
    );
    assert.equal(harness.bot.evelynMovementOwner, null);
});

test('a sudden two-block descent stops direct flee even when lower ground exists', async () => {
    const controls = new Map();
    let threatPolls = 0;
    const bot = Object.assign(new EventEmitter(), {
        entity: {position: {x: 0, y: 64, z: 0}, velocity: {y: 0}},
        health: 20,
        interrupt_code: false,
        pathfinder: {stop() {}},
        blockAt(position) {
            const floorY = position.x >= 3 ? 61 : 63;
            return position.y <= floorY
                ? {name: 'stone', boundingBox: 'block', position}
                : {name: 'air', boundingBox: 'empty', position};
        },
        clearControlStates() {},
        async lookAt() {},
        setControlState(name, enabled) { controls.set(name, enabled); },
    });
    let abortReason = null;
    const burst = sprintEscapeBurst(bot, {x: 8, y: 64, z: 0}, {
        durationMs: 1000,
        threatProvider() {
            threatPolls += 1;
            return [hostile(2)];
        },
        onAbort: (reason) => { abortReason = reason; },
    });
    await Promise.resolve();
    const pollsBeforeDrop = threatPolls;

    bot.entity.position = {x: 3, y: 62, z: 0};
    bot.emit('physicsTick');

    assert.equal(controls.get('forward'), false);
    assert.equal(controls.get('sprint'), false);
    assert.equal(controls.get('jump'), false);
    assert.equal(threatPolls, pollsBeforeDrop);
    assert.equal(await burst, false);
    assert.equal(abortReason, 'fall_risk');
    assert.equal(bot.listenerCount('physicsTick'), 0);
});

test('stable escape resets on a returning threat, replans, then requires two continuous safe seconds', async () => {
    const harness = escapeHarness({threats: []});
    let polls = 0;
    let pathfinderStops = 0;
    harness.bot.pathfinder.stop = () => { pathfinderStops += 1; };
    const hostileProvider = () => {
        polls += 1;
        return polls === 3 ? [hostile(2)] : [];
    };
    const startedAt = Date.now();
    const result = await escapeFromHostiles(harness.bot, {
        hostileProvider,
        stableMs: 2000,
        timeoutMs: 3000,
        burstMs: 20,
        forceSprint: true,
        stopOnStall: false,
    });

    assert.equal(result.verification, 'stable_safe_radius');
    assert.ok(result.bursts >= 1);
    assert.ok(Date.now() - startedAt >= 2050);
    assert.equal(harness.bot.evelynMovementOwner, null);
    assert.ok(pathfinderStops >= 2);
});

test('stable escape does not return before two continuous safe seconds', async () => {
    const harness = escapeHarness({threats: []});
    const startedAt = Date.now();
    const result = await escapeFromHostiles(harness.bot, {
        hostileProvider: () => [],
        stableMs: 2000,
        timeoutMs: 2500,
    });

    assert.equal(result.verification, 'stable_safe_radius');
    assert.ok(Date.now() - startedAt >= 1950);
    assert.equal(harness.bot.evelynMovementOwner, null);
});

test('stable escape builds range headroom before waiting on a chasing hostile', async () => {
    const harness = escapeHarness();
    let moving = false;
    let movingPolls = 0;
    let clearanceReached = false;
    let stationaryPollsAfterClearance = 0;
    const originalSetControlState = harness.bot.setControlState;
    harness.bot.setControlState = function (name, enabled) {
        originalSetControlState.call(this, name, enabled);
        if (name === 'forward') moving = enabled;
    };
    const hostileProvider = () => {
        if (moving) {
            movingPolls += 1;
            if (movingPolls === 1) return [hostile(19)];
            clearanceReached = true;
            return [];
        }
        if (!clearanceReached) return [hostile(17)];
        stationaryPollsAfterClearance += 1;
        return stationaryPollsAfterClearance <= 2 ? [] : [hostile(23)];
    };

    const result = await escapeFromHostiles(harness.bot, {
        hostileProvider,
        safeDistance: 18,
        range: 24,
        stableMs: 150,
        timeoutMs: 700,
        burstMs: 400,
        forceSprint: true,
        stopOnStall: false,
    });

    assert.equal(result.verification, 'stable_safe_radius');
    assert.equal(result.bursts, 1);
    assert.ok(movingPolls >= 2, 'escape must continue past the first 18m crossing');
    assert.equal(harness.bot.evelynMovementOwner, null);
});

test('P0 escape keeps the default immediate safe-radius return and releases its lease', async () => {
    const harness = escapeHarness({threats: []});
    const startedAt = Date.now();
    const result = await escapeFromHostiles(harness.bot, {
        hostileProvider: () => [],
        timeoutMs: 1000,
    });

    assert.equal(result.verification, 'safe_radius');
    assert.ok(Date.now() - startedAt < 100);
    assert.equal(harness.bot.evelynMovementOwner, null);
});

test('two stagnant normal bursts promote the next attempt to recovery', async () => {
    const {bot, hostileProvider} = escapeHarness();
    const result = await escapeFromHostiles(bot, {
        hostileProvider,
        timeoutMs: 1000,
        burstMs: 1,
    });

    assert.equal(result.verification, 'escape_stalled');
    assert.equal(result.strategy, 'break_los_surface');
    assert.equal(result.bursts, 4);
    assert.equal(result.lastCandidate.radius, 14);
});

test('a four-point runtime health drop promotes normal escape to recovery', async () => {
    const {bot, hostileProvider} = escapeHarness({damageAfterBurst: true});
    const result = await escapeFromHostiles(bot, {
        hostileProvider,
        timeoutMs: 1000,
        burstMs: 1,
    });

    assert.equal(result.verification, 'escape_stalled');
    assert.equal(result.strategy, 'break_los_surface');
    assert.equal(result.bursts, 3);
    assert.equal(result.lastCandidate.radius, 14);
});

test('recovery stops after two stagnant bursts instead of consuming the timeout', async () => {
    const {bot, hostileProvider} = escapeHarness();
    const startedAt = Date.now();
    const result = await escapeFromHostiles(bot, {
        failureCount: 1,
        hostileProvider,
        timeoutMs: 1000,
        burstMs: 1,
    });

    assert.equal(result.verification, 'escape_stalled');
    assert.equal(result.bursts, 2);
    assert.equal(result.lastCandidate.radius, 14);
    assert.ok(Date.now() - startedAt < 500);
});

test('multiple initial threats and critical initial health start in recovery', async () => {
    for (const options of [
        {threats: [hostile(2), hostile(0, 3, 3)]},
        {health: 10},
    ]) {
        const {bot, hostileProvider} = escapeHarness(options);
        const result = await escapeFromHostiles(bot, {
            hostileProvider,
            timeoutMs: 1000,
            burstMs: 1,
        });
        assert.equal(result.strategy, 'break_los_surface');
        assert.equal(result.verification, 'escape_stalled');
        assert.equal(result.bursts, 2);
    }
});

test('cover score sums line-of-sight breaks across every immediate threat', () => {
    const covered = new Set(['1,65,0', '0,65,1']);
    const bot = {
        blockAt(position) {
            return covered.has(`${position.x},${position.y},${position.z}`)
                ? {name: 'stone', boundingBox: 'block'}
                : {name: 'air', boundingBox: 'empty'};
        },
    };
    const candidate = {x: 0, y: 64, z: 0};
    const threats = [hostile(4), hostile(0, 4)];

    assert.equal(escapeCoverScore(bot, candidate, threats.slice(0, 1)), 1);
    assert.equal(escapeCoverScore(bot, candidate, threats), 2);
});

test('interrupt aborts a sprint within one poll and releases controls and lease', async () => {
    const {bot, controls, hostileProvider} = escapeHarness();
    setTimeout(() => { bot.interrupt_code = true; }, 5);
    const startedAt = Date.now();
    const result = await escapeFromHostiles(bot, {
        hostileProvider,
        timeoutMs: 1000,
        burstMs: 1000,
    });

    assert.equal(result.verification, 'interrupted');
    assert.ok(Date.now() - startedAt < 300);
    assert.equal(bot.evelynMovementOwner, null);
    assert.deepEqual(controls.slice(-3), [
        ['forward', false],
        ['sprint', false],
        ['jump', false],
    ]);
});

test('tactical contact handoff aborts within one poll, clears a stale goal, and releases controls', async () => {
    const {bot, controls, hostileProvider} = escapeHarness();
    let distance = 5;
    let polls = 0;
    setTimeout(() => { distance = 3; }, 5);
    const startedAt = Date.now();
    const result = await escapeFromHostiles(bot, {
        hostileProvider,
        timeoutMs: 1000,
        burstMs: 1000,
        forceSprint: true,
        abortReasonProvider() {
            polls += 1;
            return distance <= 3 ? 'emergency_melee_handoff' : null;
        },
    });

    assert.equal(result.verification, 'emergency_melee_handoff');
    assert.ok(Date.now() - startedAt < 300);
    assert.ok(polls >= 2);
    assert.equal(bot.pathfinder.goal, null);
    assert.equal(bot.evelynMovementOwner, null);
    assert.deepEqual(controls.slice(-3), [
        ['forward', false],
        ['sprint', false],
        ['jump', false],
    ]);
});

test('a stationary incomplete burst fails but a progressing long burst remains valid', async () => {
    const stationary = escapeHarness();
    let stationaryReason = null;
    assert.equal(await sprintEscapeBurst(stationary.bot, {x: 8, y: 64, z: 0}, {
        durationMs: 1,
        threatProvider: stationary.hostileProvider,
        abortReasonProvider: () => null,
        onAbort: (reason) => { stationaryReason = reason; },
    }), false);
    assert.equal(stationaryReason, 'escape_burst_incomplete');

    const progressing = escapeHarness();
    const setControlState = progressing.bot.setControlState.bind(progressing.bot);
    progressing.bot.setControlState = (name, enabled) => {
        setControlState(name, enabled);
        if (name === 'forward' && enabled) progressing.bot.entity.position.x = 1;
    };
    let progressingReason = null;
    assert.equal(await sprintEscapeBurst(progressing.bot, {x: 8, y: 64, z: 0}, {
        durationMs: 1,
        threatProvider: progressing.hostileProvider,
        abortReasonProvider: () => null,
        onAbort: (reason) => { progressingReason = reason; },
    }), true);
    assert.equal(progressingReason, null);
});

test('P0 callers can explicitly ignore interrupt_code for one bounded reflex burst', async () => {
    const {bot, controls} = escapeHarness();
    bot.interrupt_code = true;

    assert.equal(MAX_INTERRUPT_OPTOUT_MS, 1200);
    assert.equal(await sprintEscapeBurst(bot, {x: 9, y: 64, z: 0}, {durationMs: 1}), false);
    assert.equal(await sprintEscapeBurst(bot, {x: 9, y: 64, z: 0}, {
        durationMs: 1,
        interruptOptOutMs: 10,
    }), true);
    assert.deepEqual(controls.slice(-3), [
        ['forward', false],
        ['sprint', false],
        ['jump', false],
    ]);
});

test('P0 callers can force direct controls while cooperative pathfinder stop repeats', async () => {
    const {bot, controls, hostileProvider} = escapeHarness();
    bot.interrupt_code = true;
    setTimeout(() => { bot.entities = {}; }, 5);
    const result = await escapeFromHostiles(bot, {
        hostileProvider,
        timeoutMs: 200,
        burstMs: 20,
        interruptOptOutMs: 200,
        forceSprint: true,
    });

    assert.ok(result.bursts >= 1);
    assert.ok(controls.some(([name, enabled]) => name === 'sprint' && enabled));
    assert.equal(bot.evelynMovementOwner, null);
});

test('bounded P0 escape keeps reassessing instead of stopping after two stagnant bursts', async () => {
    const {bot, hostileProvider} = escapeHarness({health: 10});
    bot.interrupt_code = true;
    const result = await escapeFromHostiles(bot, {
        hostileProvider,
        timeoutMs: 150,
        burstMs: 20,
        interruptOptOutMs: 150,
        forceSprint: true,
        stopOnStall: false,
    });

    assert.ok(result.bursts > 2);
    assert.equal(bot.evelynMovementOwner, null);
});

test('interrupt opt-out never masks death or disconnection', async () => {
    for (const stop of [
        (bot) => { bot.health = 0; },
        (bot) => { bot.entity = null; },
    ]) {
        const {bot, controls} = escapeHarness();
        bot.interrupt_code = true;
        setTimeout(() => stop(bot), 5);

        assert.equal(await sprintEscapeBurst(bot, {x: 9, y: 64, z: 0}, {
            durationMs: MAX_INTERRUPT_OPTOUT_MS,
            interruptOptOutMs: MAX_INTERRUPT_OPTOUT_MS,
        }), false);
        assert.deepEqual(controls.slice(-3), [
            ['forward', false],
            ['sprint', false],
            ['jump', false],
        ]);
    }
});
