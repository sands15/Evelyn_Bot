import test from 'node:test';
import assert from 'node:assert/strict';
import { ActionManager } from '/app/mindcraft/src/agent/action_manager.js';
import {
    decisionCanInterrupt,
    digStaircaseStep,
    failureCooldownMs,
    filterMovesAtOrAbove,
    hostileHasClearLine,
    hostileIsActionable,
    listSurvivalDecisions,
    mergeSurvivalState,
    nearbyAirExitTargets,
    selectBestFood,
    selectHostileTactic,
    selectSurvivalDecision,
    staircaseBaseTargets,
    staircaseTargets,
    verifyHostileOutcome,
    verifySurfaceEscape,
    walkStaircaseStep,
} from '/app/mindcraft/src/agent/evelyn_survival_mode.js';
import {
    buildEscapeCandidates,
    chooseEscapeCandidate,
    escapeFromHostiles,
    escapeSafetyScore,
    sprintEscapeBurst,
} from '/app/mindcraft/src/agent/evelyn_escape_controller.js';

test('safe tool bootstrap waits for idle instead of interrupting movement', () => {
    assert.equal(decisionCanInterrupt('bootstrap_tools'), false);
    assert.equal(decisionCanInterrupt('handle_hostile'), true);
});

test('failed hostile handling retries quickly instead of yielding to planner work', () => {
    assert.equal(failureCooldownMs('handle_hostile', 1), 250);
    assert.equal(failureCooldownMs('handle_hostile', 2), 250);
    assert.equal(failureCooldownMs('handle_hostile', 20), 250);
    assert.equal(failureCooldownMs('acquire_food', 1), 10000);
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
    const manager = new ActionManager({});
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
        ...overrides,
    };
}

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

test('critical underground agent escapes before searching for food', () => {
    assert.equal(
        selectSurvivalDecision(snapshot({ hunger: 0, health: 10, underground: true })),
        'escape_to_surface',
    );
});

test('underground water escape preempts planning even with full hunger', () => {
    assert.equal(
        selectSurvivalDecision(snapshot({hunger: 20, health: 20, underground: true, inWater: true})),
        'escape_to_surface',
    );
});

test('food acquisition is delegated to the goal manager', () => {
    assert.equal(
        selectSurvivalDecision(snapshot({ hunger: 0, health: 10, underground: false })),
        null,
    );
});

test('hungry surface agent does not interrupt planner work to acquire food', () => {
    assert.equal(
        selectSurvivalDecision(snapshot({ hunger: 14, health: 20, foodName: null })),
        null,
    );
});

test('safe agent without a pickaxe leaves tool recovery to the planner by default', () => {
    assert.equal(
        selectSurvivalDecision(snapshot({ hasPickaxe: false, hunger: 18, health: 20 })),
        null,
    );
});

test('deterministic tool bootstrap remains available as an explicit fallback', () => {
    assert.equal(
        selectSurvivalDecision(
            snapshot({ hasPickaxe: false, hunger: 18, health: 20 }),
            {enableToolBootstrap: true},
        ),
        'bootstrap_tools',
    );
});

test('safe equipped agent leaves control to the planner', () => {
    assert.equal(selectSurvivalDecision(snapshot()), null);
});

test('healthy armed agent fights one nearby melee hostile', () => {
    assert.equal(selectHostileTactic(snapshot({
        hostileDistance: 5,
        hostileName: 'zombie',
        hostileId: 41,
        hostileCount: 1,
        hasMeleeWeapon: true,
        health: 20,
        hunger: 20,
    })), 'fight');
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
