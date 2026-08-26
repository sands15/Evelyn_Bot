import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
import minecraftData from 'minecraft-data';
import Vec3 from 'vec3';

const pathfinderRoot = process.env.MINEFLAYER_PATHFINDER_ROOT || '/app/mindcraft/node_modules/mineflayer-pathfinder';
const {default: goto} = await import(pathToFileURL(join(pathfinderRoot, 'lib', 'goto.js')).href);
const pathfinderModule = (await import(pathToFileURL(join(pathfinderRoot, 'index.js')).href)).default;
const {pathfinder} = pathfinderModule;

function createBot({atGoal = false, isEnd = null} = {}) {
    const bot = new EventEmitter();
    let stopCalls = 0;
    let forcedClearCalls = 0;
    const position = {
        x: 0,
        y: 48,
        z: 0,
        floored() { return this; },
        offset(x, y, z) { return {...this, x: this.x + x, y: this.y + y, z: this.z + z}; },
    };
    bot.entity = {
        position,
    };
    bot.pathfinder = {
        setGoal(goal) {
            if (goal === null) forcedClearCalls++;
            this.goal = goal;
            bot.currentGoal = goal;
        },
        stop() {
            stopCalls++;
            this.goal = null;
        },
    };
    const goal = {
        isEnd: (candidate) => isEnd ? isEnd(candidate) : atGoal,
    };
    return {bot, goal, stopCalls: () => stopCalls, forcedClearCalls: () => forcedClearCalls};
}

function settlementState(promise) {
    let state = 'pending';
    promise.then(
        () => { state = 'resolved'; },
        () => { state = 'rejected'; },
    );
    return () => state;
}

test('goto keeps waiting after an empty partial path result', async () => {
    const {bot, goal} = createBot();
    const pending = goto(bot, goal);
    const state = settlementState(pending);
    bot.emit('path_update', {status: 'partial', path: []});
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(state(), 'pending');
    bot.emit('goal_reached', goal);
    await pending;
    assert.equal(state(), 'resolved');
});

test('goto rejects an empty no-path result before reaching the goal', async () => {
    const {bot, goal, stopCalls, forcedClearCalls} = createBot();
    const pending = goto(bot, goal);
    bot.emit('path_update', {status: 'noPath', path: []});
    await assert.rejects(pending, {name: 'NoPath'});
    assert.equal(bot.pathfinder.goal, null);
    assert.equal(stopCalls(), 0);
    assert.equal(forcedClearCalls(), 1);
});

test('goto clears its owned goal after a non-empty timeout', async () => {
    const {bot, goal, stopCalls, forcedClearCalls} = createBot();
    const pending = goto(bot, goal);
    bot.emit('path_update', {status: 'timeout', path: [{x: 1, y: 48, z: 0}]});
    await assert.rejects(pending, {name: 'Timeout'});
    assert.equal(bot.pathfinder.goal, null);
    assert.equal(stopCalls(), 0);
    assert.equal(forcedClearCalls(), 1);
});

test('goto rejects only its own goal after the second stuck reset', async () => {
    const {bot, goal, forcedClearCalls} = createBot();
    const pending = goto(bot, goal);
    const state = settlementState(pending);

    bot.emit('path_reset', 'stuck');
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(state(), 'pending');
    assert.equal(bot.pathfinder.goal, goal);
    assert.equal(bot.listenerCount('path_reset'), 1);

    bot.emit('path_reset', 'stuck');
    await assert.rejects(pending, {name: 'Timeout'});
    assert.equal(bot.pathfinder.goal, null);
    assert.equal(forcedClearCalls(), 1);
    assert.equal(bot.listenerCount('path_reset'), 0);
});

test('goto ignores stuck resets owned by a foreign goal and preserves it on cleanup', async () => {
    const {bot, goal, forcedClearCalls} = createBot();
    const foreignGoal = {isEnd: () => false};
    const pending = goto(bot, goal);
    const state = settlementState(pending);
    bot.pathfinder.goal = foreignGoal;

    bot.emit('path_reset', 'stuck');
    bot.emit('path_reset', 'stuck');
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.equal(state(), 'pending');
    assert.equal(bot.pathfinder.goal, foreignGoal);

    bot.emit('path_stop');
    await assert.rejects(pending, {name: 'PathStopped'});
    assert.equal(bot.pathfinder.goal, foreignGoal);
    assert.equal(forcedClearCalls(), 0);
    assert.equal(bot.listenerCount('path_reset'), 0);
});

test('goto accepts an empty path only when the goal is already reached', async () => {
    const {bot, goal} = createBot({atGoal: true});
    const pending = goto(bot, goal);
    bot.emit('path_update', {status: 'success', path: []});
    await pending;
});

test('goto rejects empty success when the goal is not reached', async () => {
    const {bot, goal} = createBot();
    const pending = goto(bot, goal);
    bot.emit('path_update', {status: 'success', path: []});
    await assert.rejects(pending, {name: 'NoPath'});
});

test('goto accepts empty success when only the one-block-up position satisfies the goal', async () => {
    const {bot, goal} = createBot({isEnd: (position) => position.y === 49});
    const pending = goto(bot, goal);
    bot.emit('path_update', {status: 'success', path: []});
    await pending;
});

test('injected pathfinder clears a one-block-up completed goal', async () => {
    const bot = new EventEmitter();
    const registry = minecraftData('1.21.11');
    Object.assign(bot, {
        registry,
        entity: {
            position: new Vec3(0.5, 48.2, 0.5),
            velocity: new Vec3(0, 0, 0),
            onGround: true,
            effects: {},
        },
        inventory: {items: () => []},
        controlState: {},
        entities: {},
        clearControlStates: () => {},
        blockAt: () => null,
    });
    pathfinder(bot);
    const goal = {
        isValid: () => true,
        hasChanged: () => false,
        isEnd: (position) => position.y === 49,
    };

    const pending = bot.pathfinder.goto(goal);
    bot.emit('physicsTick');
    await pending;
    assert.equal(bot.pathfinder.goal, null);
});

test('runtime publishes content-free monotonic navigation evidence', async (t) => {
    const directory = await mkdtemp(join(tmpdir(), 'evelyn-navigation-'));
    const statusPath = join(directory, 'status.json');
    const previousStatusPath = process.env.MINDCRAFT_STATUS_PATH;
    process.env.MINDCRAFT_STATUS_PATH = statusPath;
    t.after(async () => {
        if (previousStatusPath === undefined) delete process.env.MINDCRAFT_STATUS_PATH;
        else process.env.MINDCRAFT_STATUS_PATH = previousStatusPath;
        await rm(directory, {recursive: true, force: true});
    });
    const runtimePath = process.env.MINDCRAFT_RUNTIME_PATH || '/app/mindcraft/src/utils/evelyn_runtime.js';
    const {installEvelynRuntime} = await import(
        `${pathToFileURL(runtimePath).href}?navigation=${Date.now()}`
    );
    const bot = new EventEmitter();
    Object.assign(bot, {
        entity: {position: new Vec3(0, 64, 0)},
        entities: {},
        inventory: {items: () => []},
        pathfinder: {goal: {}},
        health: 20,
        food: 20,
    });

    installEvelynRuntime(bot, {agentName: 'Evelyn'});
    bot.emit('path_update', {status: 'partial', path: [new Vec3(1, 64, 0)]});
    bot.emit('path_reset', 'stuck');
    bot.emit('path_update', {status: 'success', path: []});
    bot.entity.position = new Vec3(2, 64, 0);
    bot.emit('goal_reached', {});
    bot.emit('path_update', {status: 'timeout', path: []});
    bot.pathfinder.goal = null;
    bot.emit('spawn');

    const status = JSON.parse(await readFile(statusPath, 'utf8'));
    assert.deepEqual(status.navigation, {
        path_updates: 3,
        nonempty_path_updates: 1,
        success_updates: 1,
        partial_updates: 1,
        timeout_updates: 1,
        no_path_updates: 0,
        goal_reached: 1,
        verified_goal_reached: 1,
        stuck_resets: 1,
        other_resets: 0,
        last_event: 'timeout',
        updated_at: status.navigation.updated_at,
        active: false,
        content_free: true,
    });
    assert.equal(Number.isFinite(status.navigation.updated_at), true);
});
