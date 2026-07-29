import test from 'node:test';
import assert from 'node:assert/strict';
import {EventEmitter} from 'node:events';
import goto from '/app/mindcraft/node_modules/mineflayer-pathfinder/lib/goto.js';

function createBot({atGoal = false} = {}) {
    const bot = new EventEmitter();
    bot.entity = {
        position: {
            floored: () => ({x: 0, y: 48, z: 0}),
        },
    };
    bot.pathfinder = {
        setGoal(goal) {
            bot.currentGoal = goal;
        },
    };
    const goal = {
        isEnd: () => atGoal,
    };
    return {bot, goal};
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
    const {bot, goal} = createBot();
    const pending = goto(bot, goal);
    bot.emit('path_update', {status: 'noPath', path: []});
    await assert.rejects(pending, {name: 'NoPath'});
});

test('goto accepts an empty path only when the goal is already reached', async () => {
    const {bot, goal} = createBot({atGoal: true});
    const pending = goto(bot, goal);
    bot.emit('path_update', {status: 'success', path: []});
    await pending;
});
