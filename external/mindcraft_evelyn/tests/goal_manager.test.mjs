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

function fakeAgent(bot, candidates) {
    return {
        bot,
        prompter: {
            chat_model: {
                proposeSubgoals: async () => candidates
            }
        }
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
            hostiles: [
                {name: 'husk', distance: 14, verticalDistance: 11, actionable: false},
            ],
            hostileCount: 0,
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
            hostiles: [],
            hostileCount: 0,
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
            hostiles: [],
            hostileCount: 0,
            cooldown_until: {},
        };
        const gate = manager.gateCommand('!searchForBlock("wheat", 32)', {autonomous: true});
        assert.equal(gate.allowed, true);
        assert.equal(gate.reason, 'relevant');
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

test('shadow mode selects a verifiable subgoal and audits unrelated commands', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-shadow-'));
    try {
        const bot = fakeBot();
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
            }]),
            {
                statePath: path.join(directory, 'state.json'),
                mode: 'shadow',
                ultimateGoal: 'Defeat the Ender Dragon'
            }
        );
        await manager.initialize();
        await manager.prepareForPrompt();

        assert.equal(manager.state.currentSubgoal.id, 'obtain_logs');
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
        const manager = new EvelynGoalManager(
            fakeAgent(fakeBot(), [{
                id: 'obtain_logs',
                kind: 'obtain',
                target: '#logs',
                quantity: 3,
                reason: 'Retry the missing prerequisite in a new area.',
                success: {kind: 'inventory', target: '#logs', count: 3},
                action_budget: 8,
                unlock_score: 5,
                risk: 'low'
            }]),
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
        assert.equal(manager.state.currentSubgoal.target, '#logs');
    } finally {
        fs.rmSync(directory, {recursive: true, force: true});
    }
});
