import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
    EvelynGoalManager,
    foodRecoveryCandidate,
    minimumKitCandidate,
    minimumKitStatus,
} from '../src/agent/evelyn_goal_manager.js';
import {
    buildWorldState,
    hostileIsActionable,
    inventoryCountForTarget,
    isKnownHostile,
    itemMatchesTarget
} from '../src/agent/evelyn_world_state.js';

function fakeBot(items = [], overrides = {}) {
    const position = {
        x: 0,
        y: 64,
        z: 0,
        distanceTo(other) {
            return Math.hypot(other.x - this.x, other.y - this.y, other.z - this.z);
        }
    };
    return {
        entity: {position},
        inventory: {
            items: () => items.map(([name, count]) => ({name, count}))
        },
        entities: {},
        health: 20,
        food: 20,
        foodSaturation: 5,
        game: {dimension: 'minecraft:overworld'},
        time: {timeOfDay: 1000},
        heldItem: null,
        ...overrides
    };
}

function fakeAgent(bot, candidates, onPropose = () => {}) {
    return {
        bot,
        prompter: {
            chat_model: {
                proposeSubgoals: async () => {
                    onPropose();
                    return candidates;
                }
            }
        }
    };
}

function worldEffectBinding(overrides = {}) {
    return {
        goalRunId: 'goal-run-1',
        actionRunId: 'action-run-1',
        actionKey: 'minecraft:find_food_source',
        contractCode: 'mindcraft_food_recovery.v1',
        leaseId: 'lease-1',
        leaseProcessNonce: 'lease-process-1',
        producerNonce: 'producer-nonce-1',
        ...overrides,
    };
}

function foodReserveSubgoal() {
    return {
        id: 'restore_food_reserve',
        kind: 'obtain',
        target: '#food',
        quantity: 3,
        success: {kind: 'inventory', target: '#food', count: 3},
        allowedTargets: ['#food', 'bread'],
        allowedCommands: ['!collectBlocks'],
        actionBudget: 8,
        attempts: 0,
    };
}

test('world state groups inventory into progression tags', () => {
    const snapshot = buildWorldState(fakeBot([
        ['oak_log', 2],
        ['crimson_stem', 1],
        ['bread', 3]
    ]));
    assert.equal(inventoryCountForTarget(snapshot.inventory, '#logs'), 3);
    assert.equal(snapshot.inventorySummary.food, 3);
    assert.equal(itemMatchesTarget('oak_log', '#logs'), true);
    assert.equal(itemMatchesTarget('sandstone', '#logs'), false);
});

test('unsafe raw chicken does not satisfy the food reserve', () => {
    const snapshot = buildWorldState(fakeBot([
        ['chicken', 3],
        ['cooked_chicken', 1],
    ]));
    assert.equal(snapshot.inventorySummary.food, 1);
});

test('food recovery guides only safe land prey', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-safe-food-prey-'));
    try {
        const bot = fakeBot([], {food: 6});
        const manager = new EvelynGoalManager(fakeAgent(bot, []), {
            statePath: path.join(directory, 'state.json'),
            mode: 'gated',
            ultimateGoal: 'survive',
        });
        await manager.initialize();
        manager.requestPriorityGoal('food', manager.captureSnapshot());
        await manager.prepareForPrompt();

        const targets = manager.state.currentSubgoal.allowedTargets;
        for (const name of ['cow', 'pig', 'sheep']) assert.ok(targets.includes(name));
        for (const name of ['chicken', 'cod', 'rabbit', 'salmon']) assert.equal(targets.includes(name), false);
        assert.equal(manager.state.currentSubgoal.allowedCommands.includes('!attack'), false);
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('survival hostile classification ignores passive mobs', () => {
    assert.equal(isKnownHostile('zombie'), true);
    for (const name of ['cow', 'sheep', 'pig', 'villager']) {
        assert.equal(isKnownHostile(name), false);
    }
});

test('world state marks vertically separated mobs as non-actionable threats', () => {
    const origin = {x: 0, y: 48, z: 0};
    assert.equal(hostileIsActionable(origin, {x: 8, y: 59, z: 0}, 14), false);
    const snapshot = buildWorldState(fakeBot([], {
        entities: {
            1: {name: 'husk', position: {x: 8, y: 75, z: 0}},
        },
    }));
    assert.equal(snapshot.hostilesNearby.length, 1);
    assert.equal(snapshot.hostilesNearby[0].actionable, false);
    assert.equal(snapshot.hostilesNearby[0].verticalDistance, 11);
});

test('non-actionable vertical threats do not claim planner movement ownership', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-vertical-threat-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        manager.lastSnapshot = {
            inventory: {},
            hostilesNearby: [{name: 'husk', distance: 14, verticalDistance: 11, actionable: false}],
        };
        const gate = manager.gateCommand('!searchForBlock("wheat", 32)', {autonomous: true});
        assert.notEqual(gate.reason, 'survival_recovery_owns_movement');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('survival recovery ownership is treated as goal mismatch and can block repetition', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-survival-recovery-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        manager.state.currentSubgoal = {
            id: 'restore_food',
            kind: 'obtain',
            target: '#food',
            success: {kind: 'inventory', target: '#food', count: 3},
            allowedCommands: ['!searchForBlock', '!collectBlocks', '!goToPosition'],
            allowedTargets: ['#food'],
            attempts: 0,
            actionBudget: 8,
            observationStreak: 0,
            gateRejects: 0,
        };
        manager.agent.bot.evelynSurvivalState = {
            phase: 'escape_to_surface',
            cooldown_until: {},
        };
        const firstGate = manager.gateCommand(
            '!searchForBlock("wheat", 32)',
            {autonomous: true},
        );
        assert.equal(firstGate.allowed, false);
        assert.equal(firstGate.reason, 'survival_recovery_owns_movement');
        manager.gateCommand('!searchForBlock("wheat", 32)', {autonomous: true});
        manager.gateCommand('!searchForBlock("wheat", 32)', {autonomous: true});
        assert.equal(manager.state.currentSubgoal, null);
        assert.equal(manager.state.blockedSubgoals.at(-1)?.reason, 'repeated_irrelevant_commands');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('hostile recovery without an actionable hostile releases planner movement', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-hostile-release-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        manager.state.currentSubgoal = {
            id: 'restore_food',
            kind: 'obtain',
            target: '#food',
            success: {kind: 'inventory', target: '#food', count: 3},
            allowedCommands: ['!searchForBlock', '!collectBlocks', '!goToPosition'],
            allowedTargets: ['#food'],
            attempts: 0,
            actionBudget: 8,
            observationStreak: 0,
            gateRejects: 0,
        };
        manager.lastSnapshot = {
            inventory: {},
            hostilesNearby: [
                {name: 'husk', distance: 14, verticalDistance: 11, actionable: false},
            ],
        };
        manager.agent.bot.evelynSurvivalState = {
            phase: 'handle_hostile',
            snapshot: {
                hostiles: [
                    {name: 'husk', distance: 14, verticalDistance: 11, actionable: false},
                ],
                hostileCount: 0,
            },
            cooldown_until: {},
        };

        const gate = manager.gateCommand(
            '!searchForBlock("wheat", 32)',
            {autonomous: true},
        );

        assert.equal(gate.allowed, true);
        assert.equal(gate.reason, 'relevant');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('repeated safe recovery failures can release movement ownership for planner actions', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-recovery-release-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        manager.state.currentSubgoal = {
            id: 'restore_food',
            kind: 'obtain',
            target: '#food',
            success: {kind: 'inventory', target: '#food', count: 3},
            allowedCommands: ['!searchForBlock', '!collectBlocks', '!goToPosition'],
            allowedTargets: ['#food'],
            attempts: 0,
            actionBudget: 8,
            observationStreak: 0,
            gateRejects: 0,
        };
        manager.agent.bot.evelynSurvivalState = {
            phase: 'escape_to_surface',
            failures: {escape_to_surface: 20},
            snapshot: {hostiles: [], hostileCount: 0},
            cooldown_until: {},
        };
        const gate = manager.gateCommand('!searchForBlock("wheat", 32)', {autonomous: true});
        assert.equal(gate.allowed, true);
        assert.equal(gate.reason, 'relevant');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('high recovery failures outside explicit recovery phase still release movement ownership', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-recovery-release-planner-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        manager.state.currentSubgoal = {
            id: 'restore_food',
            kind: 'obtain',
            target: '#food',
            success: {kind: 'inventory', target: '#food', count: 3},
            allowedCommands: ['!searchForBlock', '!collectBlocks', '!goToPosition'],
            allowedTargets: ['#food'],
            attempts: 0,
            actionBudget: 8,
            observationStreak: 0,
            gateRejects: 0,
        };
        manager.agent.bot.evelynSurvivalState = {
            phase: 'planner_control',
            failures: {escape_to_surface: 20},
            snapshot: {hostiles: [], hostileCount: 0},
            cooldown_until: {},
        };
        const gate = manager.gateCommand('!searchForBlock("wheat", 32)', {autonomous: true});
        assert.equal(gate.allowed, true);
        assert.equal(gate.reason, 'relevant');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('snapshot hostile keeps survival recovery ownership after repeated failures', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-hostile-snapshot-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        manager.agent.bot.evelynSurvivalState = {
            phase: 'handle_hostile',
            failures: {handle_hostile: 20},
            snapshot: {
                hostiles: [{name: 'zombie', distance: 3, actionable: true}],
                hostileCount: 1,
            },
            cooldown_until: {},
        };

        const gate = manager.gateCommand('!searchForBlock("wheat", 32)', {autonomous: true});

        assert.equal(gate.allowed, false);
        assert.equal(gate.reason, 'survival_recovery_owns_movement');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('food recovery owns planner actions only while active or critically hungry or hurt', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-food-owner-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        manager.state.currentSubgoal = {
            ...foodReserveSubgoal(),
            allowedCommands: ['!searchForBlock', '!attack'],
            allowedTargets: ['#food', 'wheat', 'cow'],
            gateRejects: 0,
        };
        manager.lastSnapshot = {inventory: {}, hostilesNearby: []};

        manager.agent.bot.evelynSurvivalState = {
            phase: 'acquire_food',
            snapshot: {hunger: 0},
            cooldown_until: {},
        };
        const criticalMove = manager.gateCommand('!searchForBlock("wheat", 32)', {autonomous: true});
        assert.equal(criticalMove.allowed, false);
        assert.equal(criticalMove.reason, 'survival_recovery_owns_movement');

        manager.state.currentSubgoal.gateRejects = 0;
        const criticalAttack = manager.gateCommand('!attack("cow")', {autonomous: true});
        assert.equal(criticalAttack.allowed, false);
        assert.equal(criticalAttack.reason, 'survival_recovery_owns_movement');

        manager.state.currentSubgoal.gateRejects = 0;
        manager.agent.bot.evelynSurvivalState = {
            phase: 'acquire_food',
            snapshot: {health: 10, hunger: 15},
            cooldown_until: {},
        };
        const criticalHealthMove = manager.gateCommand(
            '!searchForBlock("wheat", 32)',
            {autonomous: true},
        );
        assert.equal(criticalHealthMove.allowed, false);
        assert.equal(criticalHealthMove.reason, 'survival_recovery_owns_movement');

        manager.state.currentSubgoal.gateRejects = 0;
        manager.agent.actions = {currentActionLabel: 'mode:evelyn_survival'};
        manager.agent.bot.evelynSurvivalState = {
            phase: 'acquire_food',
            snapshot: {hunger: 12},
            cooldown_until: {},
        };
        const activeMove = manager.gateCommand('!searchForBlock("wheat", 32)', {autonomous: true});
        assert.equal(activeMove.allowed, false);
        assert.equal(activeMove.reason, 'survival_recovery_owns_movement');

        manager.state.currentSubgoal.gateRejects = 0;
        manager.agent.actions.currentActionLabel = '';
        manager.agent.bot.evelynSurvivalState = {
            phase: 'acquire_food',
            snapshot: {hunger: 12},
            cooldown_until: {},
        };
        const handedOffMove = manager.gateCommand('!searchForBlock("wheat", 32)', {autonomous: true});
        assert.equal(handedOffMove.allowed, true);
        assert.equal(handedOffMove.reason, 'relevant');

        manager.state.currentSubgoal.gateRejects = 0;
        manager.agent.bot.evelynSurvivalState = {
            phase: 'planner_control',
            snapshot: {hunger: 0},
            cooldown_until: {acquire_food: Date.now() + 30000},
            recovery_handoff_until: Date.now() / 1000 + 30,
        };
        const criticalSearchHandoff = manager.gateCommand(
            '!searchForBlock("wheat", 32)',
            {autonomous: true},
        );
        assert.equal(criticalSearchHandoff.allowed, true);
        assert.equal(criticalSearchHandoff.reason, 'relevant');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('zero hunger requests food priority instead of defaulting to full hunger', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-zero-hunger-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot([], {food: 0}), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        await manager.initialize();
        manager.requestPriorityGoal('food', manager.captureSnapshot());
        assert.equal(manager.state.priorityRequest?.kind, 'food');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('critical health requests food priority before hunger becomes low', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-critical-health-food-'));
    try {
        const bot = fakeBot([], {health: 20, food: 15});
        const manager = new EvelynGoalManager(
            fakeAgent(bot, []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        await manager.initialize();
        assert.equal(foodRecoveryCandidate(manager.captureSnapshot()), null);

        bot.health = 10;
        const critical = manager.captureSnapshot();
        assert.equal(foodRecoveryCandidate(critical)?.target, '#food');
        manager.requestPriorityGoal('food', critical);
        assert.equal(manager.state.priorityRequest?.kind, 'food');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('recent failed food goals suppress immediate priority reinsertion', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-food-backoff-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot([], {food: 0}), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        await manager.initialize();
        manager.state.blockedSubgoals = [{
            id: 'restore_food_reserve',
            signature: 'obtain:#food',
            blockedAt: Date.now() / 1000,
            attempts: 8,
            reason: 'action_budget_exhausted',
        }];

        manager.requestPriorityGoal('food', manager.captureSnapshot());
        manager.requestPriorityGoal('minimum_kit', manager.captureSnapshot());
        assert.equal(manager.state.priorityRequest, null);

        manager.state.priorityRequest = {kind: 'food', requestedAt: Date.now() / 1000};
        await manager.prepareForPrompt();
        assert.equal(manager.state.currentSubgoal, null);

        manager.state.blockedSubgoals[0].blockedAt = Date.now() / 1000 - 31;
        manager.requestPriorityGoal('food', manager.captureSnapshot());
        assert.equal(manager.state.priorityRequest?.kind, 'food');

        manager.state.priorityRequest = null;
        manager.state.blockedSubgoals[0] = {
            ...manager.state.blockedSubgoals[0],
            blockedAt: Date.now() / 1000,
            reason: 'repeated_irrelevant_commands',
        };
        manager.requestPriorityGoal('food', manager.captureSnapshot());
        assert.equal(manager.state.priorityRequest, null);
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('minimum kit fills food, weapon, and pickaxe capabilities in dependency order', () => {
    const empty = buildWorldState(fakeBot([], {food: 6}));
    assert.deepEqual(minimumKitStatus(empty).missing, ['food', 'weapon', 'pickaxe']);
    assert.equal(minimumKitCandidate(empty).target, '#food');

    const fed = buildWorldState(fakeBot([['bread', 3]]));
    assert.equal(minimumKitCandidate(fed).target, '#logs');

    const withWorkbench = buildWorldState(fakeBot([
        ['bread', 3],
        ['oak_log', 3],
        ['crafting_table', 1],
    ]));
    assert.equal(minimumKitCandidate(withWorkbench).target, 'wooden_sword');

    const armed = buildWorldState(fakeBot([
        ['bread', 3],
        ['wooden_sword', 1],
        ['crafting_table', 1],
        ['oak_log', 3],
    ]));
    assert.equal(minimumKitCandidate(armed).target, 'wooden_pickaxe');
});

test('urgent wheat recovery advances through log, planks, table, and bread', () => {
    const starving = {food: 6};
    assert.equal(
        foodRecoveryCandidate(buildWorldState(fakeBot([['wheat', 9]], starving))).target,
        '#logs',
    );

    const planks = foodRecoveryCandidate(buildWorldState(fakeBot([
        ['wheat', 9],
        ['stripped_crimson_stem', 1],
    ], starving)));
    assert.equal(planks.target, 'crimson_planks');
    assert.deepEqual(planks.success, {kind: 'inventory', target: '#planks', count: 4});

    const table = foodRecoveryCandidate(buildWorldState(fakeBot([
        ['wheat', 9],
        ['crimson_planks', 4],
    ], starving)));
    assert.equal(table.target, 'crafting_table');

    const bread = foodRecoveryCandidate(buildWorldState(fakeBot([
        ['wheat', 9],
        ['crafting_table', 1],
    ], starving)));
    assert.equal(bread.target, 'bread');
    assert.equal(bread.quantity, 3);
    assert.deepEqual(bread.success, {kind: 'inventory', target: '#food', count: 3});

    assert.equal(
        foodRecoveryCandidate(buildWorldState(fakeBot([['wheat', 2]], starving))).target,
        '#food',
    );
});

test('food priority persists across the verified workbench and bread chain', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-food-chain-'));
    const items = [['wheat', 9]];
    const replaceItems = (...next) => items.splice(0, items.length, ...next);
    try {
        const bot = fakeBot(items, {food: 6});
        let manager = new EvelynGoalManager(
            fakeAgent(bot, []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        await manager.initialize();

        const advance = async (command, result, nextItems) => {
            const before = manager.captureSnapshot();
            replaceItems(...nextItems);
            const after = manager.captureSnapshot();
            await manager.recordActionResult(
                command,
                result,
                before,
                after,
                {autonomous: true},
            );
            assert.equal(manager.state.currentSubgoal, null);
            manager.requestPriorityGoal('food', after);
            await manager.prepareForPrompt();
        };

        manager.requestPriorityGoal('food', manager.captureSnapshot());
        await manager.prepareForPrompt();
        assert.equal(manager.state.currentSubgoal.id, 'obtain_food_recovery_log');
        assert.ok(manager.state.currentSubgoal.allowedCommands.includes('!collectBlocks'));

        const unchanged = manager.captureSnapshot();
        await manager.recordActionResult(
            '!collectBlocks("oak_log", 1)',
            'Collected one oak log.',
            unchanged,
            unchanged,
            {autonomous: true},
        );
        assert.equal(manager.state.currentSubgoal.id, 'obtain_food_recovery_log');
        assert.equal(manager.state.completedSubgoals.length, 0);

        await advance(
            '!collectBlocks("oak_log", 1)',
            'Collected one oak log.',
            [['wheat', 9], ['oak_log', 1]],
        );
        assert.equal(manager.state.currentSubgoal.id, 'craft_food_recovery_planks');
        assert.ok(manager.state.currentSubgoal.allowedCommands.includes('!craftRecipe'));

        manager = new EvelynGoalManager(
            fakeAgent(bot, []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        await manager.initialize();
        assert.equal(manager.state.currentSubgoal.id, 'craft_food_recovery_planks');

        await advance(
            '!craftRecipe("oak_planks", 1)',
            'Crafted four oak planks.',
            [['wheat', 9], ['oak_planks', 4]],
        );
        assert.equal(manager.state.currentSubgoal.id, 'craft_food_recovery_table');

        await advance(
            '!craftRecipe("crafting_table", 1)',
            'Crafted one crafting table.',
            [['wheat', 9], ['crafting_table', 1]],
        );
        assert.equal(manager.state.currentSubgoal.id, 'craft_emergency_bread');
        assert.equal(manager.state.currentSubgoal.quantity, 3);

        const beforeBread = manager.captureSnapshot();
        replaceItems(['crafting_table', 1], ['bread', 3]);
        await manager.recordActionResult(
            '!craftRecipe("bread", 3)',
            'Crafted three bread.',
            beforeBread,
            manager.captureSnapshot(),
            {autonomous: true},
        );

        assert.equal(manager.state.currentSubgoal, null);
        assert.deepEqual(
            manager.state.completedSubgoals.slice(-4).map(({id}) => id),
            [
                'obtain_food_recovery_log',
                'craft_food_recovery_planks',
                'craft_food_recovery_table',
                'craft_emergency_bread',
            ],
        );
        assert.ok(fs.existsSync(path.join(directory, 'state.json')));
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('critical food request preempts a normal progression subgoal', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-food-priority-'));
    try {
        const bot = fakeBot([], {food: 6});
        const manager = new EvelynGoalManager(
            fakeAgent(bot, []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        manager.state.currentSubgoal = {
            ...minimumKitCandidate(buildWorldState(fakeBot([['bread', 3]]))),
            allowedCommands: ['!collectBlocks'],
            allowedTargets: ['#logs'],
            attempts: 0,
            actionBudget: 8,
        };
        manager.requestPriorityGoal('food', manager.captureSnapshot());
        await manager.prepareForPrompt();
        assert.equal(manager.state.currentSubgoal.target, '#food');
        assert.equal(manager.state.blockedSubgoals.at(-1).reason, 'preempted_by_survival_priority');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('death clears stale work and requests minimum-kit recovery', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-death-recovery-'));
    try {
        const listeners = {};
        const bot = fakeBot([], {
            on: (event, callback) => {
                listeners[event] = callback;
            },
        });
        const manager = new EvelynGoalManager(
            fakeAgent(bot, []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.prepareForPrompt();
        assert.ok(manager.state.currentSubgoal);
        listeners.death();
        assert.equal(manager.state.currentSubgoal, null);
        assert.equal(manager.state.priorityRequest.kind, 'minimum_kit');
        assert.equal(manager.state.deathCount, 1);
        assert.equal(manager.state.blockedSubgoals.at(-1).reason, 'preempted_by_death_recovery');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('routine fallback skips model proposal and audits unrelated commands', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-shadow-'));
    try {
        const bot = fakeBot();
        let proposalCalls = 0;
        const manager = new EvelynGoalManager(
            fakeAgent(bot, [{
                id: 'obtain_logs',
                kind: 'obtain',
                target: '#logs',
                quantity: 3,
                reason: 'Unlock basic tools',
                success: {kind: 'inventory', target: '#logs', count: 3},
                action_budget: 6,
                unlock_score: 5,
                risk: 'low'
            }], () => proposalCalls++),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'shadow',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.prepareForPrompt();

        assert.equal(proposalCalls, 0);
        assert.equal(manager.state.currentSubgoal.id, 'obtain_initial_logs');
        const relevant = manager.gateCommand(
            'I will gather wood. !collectBlocks("oak_log", 3)',
            {autonomous: true}
        );
        assert.equal(relevant.relevant, true);
        const gate = manager.gateCommand('!craftRecipe("sandstone", 1)', {autonomous: true});
        assert.equal(gate.allowed, true);
        assert.equal(gate.relevant, false);
        assert.match(gate.reason, /command_outside_subgoal|unrelated_target/);
        const unsafeCombat = manager.gateCommand('!attack("zombie")', {autonomous: true});
        assert.equal(unsafeCombat.allowed, false);
        assert.equal(unsafeCombat.relevant, false);
        assert.ok(fs.existsSync(path.join(directory, 'state.json')));
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('novel progression still calls model proposal', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-novel-'));
    try {
        let proposalCalls = 0;
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot([
                ['oak_log', 3],
                ['crafting_table', 1],
                ['wooden_sword', 1],
                ['wooden_pickaxe', 1],
                ['bread', 3],
            ]), [{
                id: 'obtain_iron',
                kind: 'obtain',
                target: 'raw_iron',
                quantity: 3,
                success: {kind: 'inventory', target: 'raw_iron', count: 3},
                unlock_score: 5,
                risk: 'low',
            }], () => proposalCalls++),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'shadow',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );

        await manager.initialize();
        await manager.prepareForPrompt();

        assert.equal(proposalCalls, 1);
        assert.equal(manager.state.currentSubgoal.id, 'obtain_iron');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('gated mode abandons a subgoal after repeated irrelevant model commands', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-rejects-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.prepareForPrompt();
        for (let index = 0; index < 3; index++) {
            manager.gateCommand('!craftRecipe("sandstone", 1)', {autonomous: true});
        }
        assert.equal(manager.state.currentSubgoal, null);
        assert.equal(manager.state.blockedSubgoals.at(-1).reason, 'repeated_irrelevant_commands');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('gated mode blocks unrelated autonomous work but not user commands', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-gated-'));
    try {
        const bot = fakeBot();
        const manager = new EvelynGoalManager(
            fakeAgent(bot, []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.prepareForPrompt();
        assert.equal(manager.state.currentSubgoal.target, '#logs');

        assert.equal(
            manager.gateCommand('!craftRecipe("sandstone", 1)', {autonomous: true}).allowed,
            false
        );
        assert.equal(
            manager.gateCommand('!craftRecipe("sandstone", 1)', {autonomous: false}).allowed,
            true
        );
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('subgoal completes only when the post-action world state satisfies its predicate', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-verify-'));
    try {
        const bot = fakeBot();
        const manager = new EvelynGoalManager(
            fakeAgent(bot, []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'shadow',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.prepareForPrompt();
        const before = buildWorldState(fakeBot());
        const stillEmpty = buildWorldState(fakeBot());
        await manager.recordActionResult(
            '!collectBlocks("oak_log", 3)',
            'Could not collect oak_log.',
            before,
            stillEmpty,
            {autonomous: true}
        );
        assert.notEqual(manager.state.currentSubgoal, null);

        const withLogs = buildWorldState(fakeBot([['oak_log', 3]]));
        await manager.recordActionResult(
            '!collectBlocks("oak_log", 3)',
            'Collected 3 oak_log.',
            stillEmpty,
            withLogs,
            {autonomous: true}
        );
        assert.equal(manager.state.currentSubgoal, null);
        assert.equal(manager.state.completedSubgoals.at(-1).id, 'obtain_initial_logs');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('world effect candidate is content-free, action-bound, and emitted once', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-effect-candidate-'));
    try {
        const bot = fakeBot([], {food: 6});
        const manager = new EvelynGoalManager(
            fakeAgent(bot, []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
                worldEffectBinding: worldEffectBinding(),
            },
        );
        await manager.initialize();
        manager.state.currentSubgoal = foodReserveSubgoal();
        const before = buildWorldState(fakeBot([], {food: 6}));
        const after = buildWorldState(fakeBot([['bread', 3]], {food: 6}));

        await manager.recordActionResult(
            '!collectBlocks("bread", 3)',
            'Collected three bread.',
            before,
            after,
            {autonomous: true},
        );

        const candidate = bot.evelynGoalState.postcondition_candidate;
        assert.ok(candidate);
        assert.equal(manager.state.autonomyState, 'manual_pause');
        assert.equal(
            manager.state.manualPauseReason,
            'world_effect_candidate_published',
        );
        assert.deepEqual(
            manager.gateCommand('!collectBlocks("bread", 1)', {autonomous: true}),
            {
                allowed: false,
                relevant: false,
                reason: 'autonomy_not_active',
            },
        );
        assert.deepEqual(
            manager.gateCommand('!endGoal', {autonomous: true}),
            {
                allowed: false,
                relevant: false,
                reason: 'autonomy_not_active',
            },
        );
        assert.deepEqual(candidate, {
            schema: 'mindcraft.postcondition-candidate.v1',
            producerNonce: 'producer-nonce-1',
            goalRunId: 'goal-run-1',
            actionRunId: 'action-run-1',
            actionKey: 'minecraft:find_food_source',
            contractCode: 'mindcraft_food_recovery.v1',
            leaseId: 'lease-1',
            leaseProcessNonce: 'lease-process-1',
            candidateSequence: 1,
            executionSequence: 1,
            observedAt: candidate.observedAt,
            evidenceCode: 'mindcraft_explicit_postcondition_candidate',
            postconditionCode: 'food_reserve_ready',
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
        assert.ok(Number.isFinite(candidate.observedAt));
        for (const forbidden of [
            'goal', 'command', 'result', 'inventory', 'position',
            'coordinates', 'target', 'predicate',
        ]) {
            assert.equal(Object.hasOwn(candidate, forbidden), false);
        }

        const firstCandidate = structuredClone(candidate);
        manager.state.currentSubgoal = foodReserveSubgoal();
        await manager.recordActionResult(
            '!collectBlocks("cooked_beef", 3)',
            'Collected three cooked beef.',
            before,
            buildWorldState(fakeBot([['cooked_beef', 3]], {food: 6})),
            {autonomous: true},
        );
        assert.deepEqual(
            bot.evelynGoalState.postcondition_candidate,
            firstCandidate,
        );
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('lease-bound world effect run never restores prior action state', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-effect-fresh-'));
    try {
        const statePath = path.join(directory, 'state.json');
        fs.writeFileSync(statePath, JSON.stringify({
            version: 1,
            ultimateGoal: 'Defeat the Ender Dragon',
            mode: 'gated',
            autonomyState: 'manual_pause',
            manualPauseReason: 'world_effect_candidate_published',
            currentSubgoal: foodReserveSubgoal(),
            completedSubgoals: [],
            blockedSubgoals: [],
            recentActions: [],
            executionSequence: 99,
        }));

        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot([], {food: 6}), []),
            {
                statePath,
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
                worldEffectBinding: worldEffectBinding({
                    goalRunId: 'goal-run-fresh',
                    actionRunId: 'action-run-fresh',
                }),
            },
        );

        assert.equal(manager.state.autonomyState, 'active');
        assert.equal(manager.state.manualPauseReason, null);
        assert.equal(manager.state.executionSequence, 0);
        assert.equal(manager.state.currentSubgoal, null);
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('world effect environment binding is typed and fails closed', async () => {
    const environment = {
        MINDCRAFT_WORLD_EFFECT_GOAL_RUN_ID: 'goal-run-env',
        MINDCRAFT_WORLD_EFFECT_ACTION_RUN_ID: 'action-run-env',
        MINDCRAFT_WORLD_EFFECT_ACTION_KEY: 'minecraft:find_food_source',
        MINDCRAFT_WORLD_EFFECT_CONTRACT_CODE: 'mindcraft_food_recovery.v1',
        MINDCRAFT_WORLD_EFFECT_LEASE_ID: 'lease-env',
        MINDCRAFT_WORLD_EFFECT_LEASE_PROCESS_NONCE: 'lease-process-env',
        MINDCRAFT_WORLD_EFFECT_PRODUCER_NONCE: 'producer-env',
    };
    const previous = Object.fromEntries(
        Object.keys(environment).map((key) => [key, process.env[key]])
    );
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-effect-env-'));
    try {
        Object.assign(process.env, environment);
        const bot = fakeBot([], {food: 6});
        const manager = new EvelynGoalManager(
            fakeAgent(bot, []),
            {
                statePath: path.join(directory, 'valid-state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
            },
        );
        await manager.initialize();
        manager.state.currentSubgoal = foodReserveSubgoal();
        await manager.recordActionResult(
            '!collectBlocks("bread", 3)',
            'Collected three bread.',
            buildWorldState(fakeBot([], {food: 6})),
            buildWorldState(fakeBot([['bread', 3]], {food: 6})),
            {autonomous: true},
        );
        assert.equal(
            bot.evelynGoalState.postcondition_candidate.goalRunId,
            'goal-run-env',
        );

        const invalidBot = fakeBot([], {food: 6});
        const invalidManager = new EvelynGoalManager(
            fakeAgent(invalidBot, []),
            {
                statePath: path.join(directory, 'invalid-state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
                worldEffectBinding: worldEffectBinding({
                    actionKey: 'minecraft:gather_logs',
                }),
            },
        );
        await invalidManager.initialize();
        invalidManager.state.currentSubgoal = foodReserveSubgoal();
        await invalidManager.recordActionResult(
            '!collectBlocks("bread", 3)',
            'Collected three bread.',
            buildWorldState(fakeBot([], {food: 6})),
            buildWorldState(fakeBot([['bread', 3]], {food: 6})),
            {autonomous: true},
        );
        assert.equal(
            invalidBot.evelynGoalState.postcondition_candidate,
            null,
        );
    } finally {
        for (const [key, value] of Object.entries(previous)) {
            if (value === undefined) delete process.env[key];
            else process.env[key] = value;
        }
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('world effect candidate requires every semantic action condition', async () => {
    const scenarios = [
        {
            name: 'manual action',
            current: foodReserveSubgoal(),
            command: '!collectBlocks("bread", 3)',
            result: 'Collected three bread.',
            before: buildWorldState(fakeBot([], {food: 6})),
            after: buildWorldState(fakeBot([['bread', 3]], {food: 6})),
            autonomous: false,
        },
        {
            name: 'failed action result',
            current: foodReserveSubgoal(),
            command: '!collectBlocks("bread", 3)',
            result: 'Could not collect bread.',
            before: buildWorldState(fakeBot([], {food: 6})),
            after: buildWorldState(fakeBot([['bread', 3]], {food: 6})),
            autonomous: true,
        },
        {
            name: 'unchanged world',
            current: foodReserveSubgoal(),
            command: '!collectBlocks("bread", 3)',
            result: 'No change.',
            before: buildWorldState(fakeBot([], {food: 6})),
            after: buildWorldState(fakeBot([], {food: 6})),
            autonomous: true,
        },
        {
            name: 'irrelevant action',
            current: foodReserveSubgoal(),
            command: '!collectBlocks("sandstone", 1)',
            result: 'Collected sandstone.',
            before: buildWorldState(fakeBot([], {food: 6})),
            after: buildWorldState(fakeBot([['bread', 3]], {food: 6})),
            autonomous: true,
        },
        {
            name: 'different completed predicate',
            current: {
                ...foodReserveSubgoal(),
                id: 'obtain_food_recovery_log',
                target: '#logs',
                success: {kind: 'inventory', target: '#logs', count: 1},
                allowedTargets: ['#logs', 'oak_log'],
            },
            command: '!collectBlocks("oak_log", 1)',
            result: 'Collected one oak log.',
            before: buildWorldState(fakeBot([], {food: 6})),
            after: buildWorldState(fakeBot([['oak_log', 1]], {food: 6})),
            autonomous: true,
        },
    ];

    for (const scenario of scenarios) {
        const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-effect-reject-'));
        try {
            const bot = fakeBot([], {food: 6});
            const manager = new EvelynGoalManager(
                fakeAgent(bot, []),
                {
                    statePath: path.join(directory, 'state.json'),
                    mode: 'gated',
                    ultimateGoal: 'Defeat the Ender Dragon',
                    worldEffectBinding: worldEffectBinding(),
                },
            );
            await manager.initialize();
            manager.state.currentSubgoal = scenario.current;
            await manager.recordActionResult(
                scenario.command,
                scenario.result,
                scenario.before,
                scenario.after,
                {autonomous: scenario.autonomous},
            );
            assert.equal(
                bot.evelynGoalState.postcondition_candidate,
                null,
                scenario.name,
            );
        } finally {
            fs.rmSync(directory, {recursive: true, force: true});
        }
    }
});

test('initial and periodic predicate completion never publish effect candidates', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-effect-passive-'));
    try {
        const initialBot = fakeBot([['bread', 3]], {food: 6});
        const initialManager = new EvelynGoalManager(
            fakeAgent(initialBot, []),
            {
                statePath: path.join(directory, 'initial-state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
                worldEffectBinding: worldEffectBinding(),
            },
        );
        initialManager.state.currentSubgoal = foodReserveSubgoal();
        await initialManager.initialize();
        assert.equal(initialManager.state.currentSubgoal, null);
        assert.equal(
            initialBot.evelynGoalState.postcondition_candidate,
            null,
        );

        const periodicBot = fakeBot([['bread', 3]], {food: 6});
        const periodicAgent = fakeAgent(periodicBot, []);
        periodicAgent.actions = {executing: false};
        const periodicManager = new EvelynGoalManager(
            periodicAgent,
            {
                statePath: path.join(directory, 'periodic-state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
                worldEffectBinding: worldEffectBinding({
                    goalRunId: 'goal-run-2',
                    actionRunId: 'action-run-2',
                }),
            },
        );
        await periodicManager.initialize();
        periodicManager.state.currentSubgoal = foodReserveSubgoal();
        await periodicManager.update();
        assert.equal(periodicManager.state.currentSubgoal, null);
        assert.equal(
            periodicBot.evelynGoalState.postcondition_candidate,
            null,
        );
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('bound in-flight action retains predicate until result evidence is recorded', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-effect-inflight-'));
    try {
        const items = [];
        const bot = fakeBot(items, {food: 6});
        const agent = fakeAgent(bot, []);
        agent.actions = {executing: true};
        const manager = new EvelynGoalManager(
            agent,
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon',
                worldEffectBinding: worldEffectBinding({
                    goalRunId: 'goal-run-inflight',
                    actionRunId: 'action-run-inflight',
                }),
            },
        );
        await manager.initialize();
        manager.state.currentSubgoal = foodReserveSubgoal();
        const before = manager.captureSnapshot();

        items.push(['bread', 3]);
        await manager.update();
        assert.notEqual(manager.state.currentSubgoal, null);

        agent.actions.executing = false;
        const after = manager.captureSnapshot();
        await manager.recordActionResult(
            '!collectBlocks("bread", 3)',
            'Collected three bread.',
            before,
            after,
            {autonomous: true},
        );

        assert.equal(manager.state.currentSubgoal, null);
        assert.equal(
            bot.evelynGoalState.postcondition_candidate?.actionRunId,
            'action-run-inflight',
        );
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('ender dragon completion requires a recent autonomous combat action', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-dragon-'));
    try {
        const listeners = {};
        const bot = fakeBot([], {
            game: {dimension: 'minecraft:the_end'},
            on: (event, callback) => {
                listeners[event] = callback;
            }
        });
        const manager = new EvelynGoalManager(
            fakeAgent(bot, [{
                id: 'defeat_dragon',
                kind: 'defeat',
                target: 'ender_dragon',
                quantity: 1,
                reason: 'Complete the ultimate goal',
                success: {kind: 'entity_defeated', target: 'ender_dragon', count: 1},
                action_budget: 12,
                unlock_score: 5,
                risk: 'high'
            }]),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'shadow',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.prepareForPrompt();
        assert.equal(
            manager.gateCommand('!attack("ender_dragon")', {autonomous: true}).allowed,
            true
        );
        const snapshot = manager.captureSnapshot();
        await manager.recordActionResult(
            '!attack("ender_dragon")',
            'Fighting ender_dragon.',
            snapshot,
            snapshot,
            {autonomous: true}
        );
        listeners.entityDead({name: 'ender_dragon'});
        assert.ok(manager.state.ultimateGoalCompletedAt);
        assert.match(manager.promptContext(), /Use !endGoal now/);
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('successful nonterminal dragon combat cannot arm completion across a restart', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-dragon-restart-'));
    try {
        const statePath = path.join(directory, 'state.json');
        const candidate = {
            id: 'defeat_dragon',
            kind: 'defeat',
            target: 'ender_dragon',
            quantity: 1,
            reason: 'Complete the ultimate goal',
            success: {kind: 'entity_defeated', target: 'ender_dragon', count: 1},
            action_budget: 12,
            unlock_score: 5,
            risk: 'high'
        };
        const first = new EvelynGoalManager(
            fakeAgent(fakeBot([], {game: {dimension: 'minecraft:the_end'}}), [candidate]),
            {statePath, mode: 'gated', ultimateGoal: 'Defeat the Ender Dragon'}
        );
        await first.initialize();
        await first.prepareForPrompt();
        const snapshot = first.captureSnapshot();
        await first.recordActionResult(
            '!attack("ender_dragon")',
            'Fighting ender_dragon.',
            snapshot,
            snapshot,
            {autonomous: true}
        );
        assert.ok(first.state.lastDragonCombatAt);
        assert.equal(first.state.ultimateGoalCompletedAt, null);

        const listeners = {};
        const restarted = new EvelynGoalManager(
            fakeAgent(fakeBot([], {
                game: {dimension: 'minecraft:the_end'},
                on: (event, callback) => {
                    listeners[event] = callback;
                }
            }), [candidate]),
            {statePath, mode: 'gated', ultimateGoal: 'Defeat the Ender Dragon'}
        );
        await restarted.initialize();
        assert.equal(restarted.state.lastDragonCombatAt, null);

        listeners.entityDead({name: 'ender_dragon'});
        assert.equal(restarted.state.ultimateGoalCompletedAt, null);
        assert.deepEqual(
            restarted.gateCommand('!endGoal', {autonomous: true}),
            {
                allowed: false,
                relevant: false,
                reason: 'goal_manager_has_not_verified_ultimate_completion',
            },
        );
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('dragon death during the awaited attack completes the ultimate goal', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-dragon-order-'));
    try {
        const listeners = {};
        const bot = fakeBot([], {
            game: {dimension: 'minecraft:the_end'},
            on: (event, callback) => {
                listeners[event] = callback;
            }
        });
        const manager = new EvelynGoalManager(
            fakeAgent(bot, [{
                id: 'defeat_dragon',
                kind: 'defeat',
                target: 'ender_dragon',
                quantity: 1,
                success: {kind: 'entity_defeated', target: 'ender_dragon', count: 1},
                action_budget: 12,
                unlock_score: 5,
                risk: 'high'
            }]),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.prepareForPrompt();
        const before = manager.captureSnapshot();

        listeners.entityDead({name: 'ender_dragon'});
        const after = manager.captureSnapshot();
        assert.equal(Number(before.defeatedEntities.ender_dragon || 0), 0);
        assert.equal(after.defeatedEntities.ender_dragon, 1);
        assert.equal(manager.state.ultimateGoalCompletedAt, null);
        await manager.recordActionResult(
            '!attack("ender_dragon")',
            'Successfully killed ender_dragon.',
            before,
            after,
            {autonomous: true}
        );

        assert.ok(manager.state.ultimateGoalCompletedAt);
        assert.equal(manager.state.autonomyState, 'completed');
        assert.match(manager.promptContext(), /Use !endGoal now/);
        assert.deepEqual(
            manager.gateCommand('!endGoal', {autonomous: true}),
            {
                allowed: true,
                relevant: true,
                reason: 'ultimate_goal_verified_complete',
            },
        );
        assert.deepEqual(
            manager.gateCommand('!stats', {autonomous: true}),
            {
                allowed: false,
                relevant: false,
                reason: 'autonomy_not_active',
            },
        );

        const completedAt = manager.state.ultimateGoalCompletedAt;
        const restarted = new EvelynGoalManager(
            fakeAgent(fakeBot([], {game: {dimension: 'minecraft:the_end'}}), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await restarted.initialize();
        assert.equal(restarted.state.lastDragonCombatAt, null);
        assert.equal(restarted.state.ultimateGoalCompletedAt, completedAt);
        assert.equal(restarted.state.autonomyState, 'completed');
        assert.equal(restarted.gateCommand('!endGoal', {autonomous: true}).allowed, true);
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('failed or cancelled dragon combat cannot arm completion across a restart', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-dragon-failed-'));
    try {
        const statePath = path.join(directory, 'state.json');
        const candidate = {
            id: 'defeat_dragon',
            kind: 'defeat',
            target: 'ender_dragon',
            quantity: 1,
            reason: 'Complete the ultimate goal',
            success: {kind: 'entity_defeated', target: 'ender_dragon', count: 1},
            action_budget: 12,
            unlock_score: 5,
            risk: 'high'
        };
        const first = new EvelynGoalManager(
            fakeAgent(fakeBot([], {game: {dimension: 'minecraft:the_end'}}), [candidate]),
            {statePath, mode: 'gated', ultimateGoal: 'Defeat the Ender Dragon'}
        );
        await first.initialize();
        await first.prepareForPrompt();
        const snapshot = first.captureSnapshot();
        await first.recordActionResult(
            '!attack("ender_dragon")',
            'No ender_dragon nearby.',
            snapshot,
            snapshot,
            {autonomous: true}
        );
        await first.recordActionResult(
            '!attack("ender_dragon")',
            undefined,
            snapshot,
            snapshot,
            {autonomous: true}
        );

        const listeners = {};
        const restartedBot = fakeBot([], {
            game: {dimension: 'minecraft:the_end'},
            on: (event, callback) => {
                listeners[event] = callback;
            }
        });
        const restarted = new EvelynGoalManager(
            fakeAgent(restartedBot, [candidate]),
            {statePath, mode: 'gated', ultimateGoal: 'Defeat the Ender Dragon'}
        );
        await restarted.initialize();
        listeners.entityDead({name: 'ender_dragon'});

        assert.equal(restarted.state.lastDragonCombatAt, null);
        assert.equal(restarted.state.ultimateGoalCompletedAt, null);
        assert.equal(restarted.state.autonomyState, 'active');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('autonomous goal control is blocked even in shadow mode until verified completion', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-owner-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'shadow',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.prepareForPrompt();
        assert.equal(manager.gateCommand('!goal("get food")', {autonomous: true}).allowed, false);
        assert.equal(manager.gateCommand('!endGoal', {autonomous: true}).allowed, false);
        manager.state.ultimateGoalCompletedAt = Date.now() / 1000;
        assert.equal(manager.gateCommand('!endGoal', {autonomous: true}).allowed, true);
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('verified endGoal bypasses recovery and unsafe-unarmed gates while other control stays closed', () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-end-recovery-'));
    try {
        const agent = fakeAgent(fakeBot(), []);
        agent.bot.evelynSurvivalState = {
            phase: 'escape_to_surface',
            cooldown_until: {},
        };
        const manager = new EvelynGoalManager(agent, {
            statePath: path.join(directory, 'state.json'),
            mode: 'gated',
            ultimateGoal: 'Defeat the Ender Dragon',
        });
        manager.state.ultimateGoalCompletedAt = Date.now() / 1000;
        manager.state.autonomyState = 'completed';

        const recoveryGate = manager.gateCommand('!endGoal', {autonomous: true});
        agent.bot.evelynSurvivalState = null;
        manager.lastSnapshot = {
            inventory: {},
            hostilesNearby: [{name: 'zombie', distance: 3, actionable: true}],
        };
        const unsafeUnarmedGate = manager.gateCommand('!endGoal', {autonomous: true});
        const verifiedEndGate = {
            allowed: true,
            relevant: true,
            reason: 'ultimate_goal_verified_complete',
        };
        assert.deepEqual(
            [recoveryGate, unsafeUnarmedGate],
            [verifiedEndGate, verifiedEndGate],
        );
        assert.deepEqual(
            manager.gateCommand('!goal("keep going")', {autonomous: true}),
            {allowed: false, relevant: false, reason: 'autonomy_not_active'},
        );

        manager.state.ultimateGoalCompletedAt = null;
        manager.state.autonomyState = 'active';
        assert.deepEqual(
            manager.gateCommand('!endGoal', {autonomous: true}),
            {allowed: false, relevant: false, reason: 'survival_recovery_owns_movement'},
        );

        manager.state.autonomyState = 'manual_pause';
        assert.deepEqual(
            manager.gateCommand('!endGoal', {autonomous: true}),
            {allowed: false, relevant: false, reason: 'autonomy_not_active'},
        );
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('failed searches require a bounded relocation before another search', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-relocate-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'shadow',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.prepareForPrompt();
        const snapshot = manager.captureSnapshot();
        for (let attempt = 0; attempt < 2; attempt++) {
            await manager.recordActionResult(
                '!searchForBlock("oak_log", 32)',
                'No oak_log nearby.',
                snapshot,
                snapshot,
                {autonomous: true}
            );
        }
        assert.equal(manager.state.currentSubgoal.relocationRequired, true);
        assert.equal(
            manager.gateCommand('!searchForBlock("oak_log", 32)', {autonomous: true}).allowed,
            false
        );
        assert.equal(manager.gateCommand('!moveAway(24)', {autonomous: true}).allowed, true);
        await manager.recordActionResult(
            '!moveAway(24)',
            'Moved away.',
            snapshot,
            snapshot,
            {autonomous: true}
        );
        assert.equal(manager.state.currentSubgoal.relocationRequired, true);
        assert.equal(manager.state.lastExecution.failed, true);
        assert.equal(manager.state.lastExecution.relocationDistance, 0);
        const movedBot = fakeBot([], {
            entity: {
                position: {
                    x: 24,
                    y: 64,
                    z: 0,
                    distanceTo(other) {
                        return Math.hypot(other.x - this.x, other.y - this.y, other.z - this.z);
                    }
                }
            }
        });
        await manager.recordActionResult(
            '!moveAway(24)',
            'Moved away.',
            snapshot,
            buildWorldState(movedBot),
            {autonomous: true}
        );
        assert.equal(manager.state.currentSubgoal.relocationRequired, false);
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('world movement alone does not count as goal progress', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-progress-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'shadow',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.prepareForPrompt();
        const before = manager.captureSnapshot();
        const movedBot = fakeBot([], {
            entity: {
                position: {
                    x: 20,
                    y: 64,
                    z: 0,
                    distanceTo(other) {
                        return Math.hypot(other.x - this.x, other.y - this.y, other.z - this.z);
                    }
                }
            }
        });
        const after = buildWorldState(movedBot);
        await manager.recordActionResult(
            '!collectBlocks("oak_log", 3)',
            'Path moved but no logs were collected.',
            before,
            after,
            {autonomous: true}
        );
        assert.equal(manager.state.lastExecution.worldChanged, true);
        assert.equal(manager.state.lastExecution.goalProgress, false);
        assert.equal(manager.state.lastProgressAt, null);
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('goal manager resumes unexpected stops but preserves a user pause', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-resume-'));
    try {
        let starts = 0;
        const bot = fakeBot();
        const agent = fakeAgent(bot, []);
        agent.isIdle = () => true;
        agent.self_prompter = {
            loop_active: false,
            isStopped: () => true,
            start: (goal) => {
                starts += 1;
                assert.equal(goal, 'Defeat the Ender Dragon');
            }
        };
        const manager = new EvelynGoalManager(
            agent,
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.update();
        assert.equal(starts, 1);

        const snapshot = manager.captureSnapshot();
        await manager.recordActionResult(
            '!endGoal',
            'Goal ended by user.',
            snapshot,
            snapshot,
            {autonomous: false}
        );
        manager.lastUpdateAt = 0;
        await manager.update();
        assert.equal(manager.state.autonomyState, 'manual_pause');
        assert.equal(starts, 1);
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('two observations require an action before another query', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-observe-'));
    try {
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), []),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'shadow',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.prepareForPrompt();
        const snapshot = manager.captureSnapshot();
        for (const command of ['!inventory', '!nearbyBlocks']) {
            assert.equal(manager.gateCommand(command, {autonomous: true}).allowed, true);
            await manager.recordActionResult(
                command,
                'Observation complete.',
                snapshot,
                snapshot,
                {autonomous: true}
            );
        }
        const third = manager.gateCommand('!stats', {autonomous: true});
        assert.equal(third.allowed, false);
        assert.equal(third.reason, 'observation_streak_exhausted');
        assert.equal(manager.state.currentSubgoal.relocationRequired, true);
        assert.equal(manager.state.currentSubgoal.gateRejects, 0);
        assert.equal(
            manager.gateCommand('!moveAway(16)', {autonomous: true}).allowed,
            true
        );
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('historically blocked prerequisites remain retryable instead of leaving no goal', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-retry-'));
    try {
        let proposalCalls = 0;
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot([['bread', 3]]), [{
                id: 'obtain_logs',
                kind: 'obtain',
                target: '#logs',
                quantity: 3,
                reason: 'Retry the missing prerequisite in a new area.',
                success: {kind: 'inventory', target: '#logs', count: 3},
                action_budget: 8,
                unlock_score: 5,
                risk: 'low'
            }], () => proposalCalls++),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'gated',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        manager.state.blockedSubgoals = [
            {
                id: 'old_logs_1',
                signature: 'obtain:#logs',
                blockedAt: Date.now() / 1000 - 60,
                attempts: 8,
                reason: 'action_budget_exhausted'
            },
            {
                id: 'old_logs_2',
                signature: 'obtain:#logs',
                blockedAt: Date.now() / 1000 - 30,
                attempts: 8,
                reason: 'action_budget_exhausted'
            }
        ];
        await manager.initialize();
        await manager.prepareForPrompt();
        assert.equal(proposalCalls, 1);
        assert.equal(manager.state.currentSubgoal.target, '#logs');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});
