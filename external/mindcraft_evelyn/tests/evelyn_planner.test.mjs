import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {
    EvelynPlanner,
    analyzeRecentTurns,
    classifyRequest,
    parseSubgoalCandidates,
    parseRecoveryPlan,
    validateActionResponse,
} from '/app/mindcraft/src/models/evelyn_planner.js';
import { CodexGateway } from '/app/mindcraft/src/models/codex_gateway.js';
import { Prompter } from '/app/mindcraft/src/models/prompter.js';

const ACTION_SYSTEM = [
    'Available documented commands:',
    '!inventory !nearbyBlocks !stats !craftable !moveAway',
    '!searchForBlock !collectBlocks !craftRecipe !getCraftingPlan !searchWiki'
].join(' ');

function selfPrompt() {
    return {
        role: 'system',
        content: 'You are self-prompting with the goal: survive. Your next response MUST contain a command with this syntax: !commandName. Respond:'
    };
}

function goalPolicyFixture(overrides = {}) {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-goal-policy-'));
    const statePath = path.join(directory, 'goal_manager_state.json');
    const previous = process.env.MINDCRAFT_GOAL_MANAGER_STATE_PATH;
    const state = {
        version: 1,
        autonomyState: 'active',
        executionSequence: 0,
        lastExecution: null,
        currentSubgoal: {
            id: 'obtain_logs',
            kind: 'obtain',
            target: '#logs',
            success: {kind: 'inventory', target: '#logs', count: 3},
            attempts: 0,
            actionBudget: 8,
            allowedCommands: [
                '!inventory', '!nearbyBlocks', '!stats', '!craftable',
                '!searchForBlock', '!collectBlocks'
            ],
            allowedTargets: ['#logs']
        },
        ...overrides
    };
    const write = (next = state) => fs.writeFileSync(statePath, JSON.stringify(next));
    write();
    process.env.MINDCRAFT_GOAL_MANAGER_STATE_PATH = statePath;
    return {
        state,
        write,
        cleanup() {
            if (previous === undefined) delete process.env.MINDCRAFT_GOAL_MANAGER_STATE_PATH;
            else process.env.MINDCRAFT_GOAL_MANAGER_STATE_PATH = previous;
            fs.rmSync(directory, {recursive: true, force: true});
        }
    };
}

test('request classification separates actions, memory summaries, and ordinary chat', () => {
    assert.equal(classifyRequest([selfPrompt()], ACTION_SYSTEM), 'action');
    assert.equal(
        classifyRequest([], 'Update your memory by summarizing and respond only with the unwrapped memory text'),
        'memory'
    );
    assert.equal(
        classifyRequest([{role: 'user', content: 'hello'}], 'Speak briefly to players'),
        'chat'
    );
});

test('action validation extracts one command and rejects invalid registry names', () => {
    assert.deepEqual(
        validateActionResponse('I will look nearby. !searchForBlock("oak_log", 32)', ACTION_SYSTEM),
        {ok: true, reason: 'valid', command: '!searchForBlock("oak_log", 32)'}
    );
    assert.equal(
        validateActionResponse('!searchForBlock("wood", 32)', ACTION_SYSTEM).reason,
        'invalid_registry_name:wood'
    );
    assert.equal(
        validateActionResponse('I already crafted it!', ACTION_SYSTEM).reason,
        'missing_command'
    );
    assert.equal(
        validateActionResponse('!inventedCommand("oak_log")', ACTION_SYSTEM).reason,
        'unknown_command:!inventedCommand'
    );
    assert.equal(
        validateActionResponse('!craftable("bread", 2)', ACTION_SYSTEM).reason,
        'invalid_argument_count:!craftable:2:0'
    );
    assert.equal(
        validateActionResponse('!collectBlocks("oak_log", "two")', ACTION_SYSTEM).reason,
        'invalid_argument_type:!collectBlocks:1:int'
    );
    assert.equal(
        validateActionResponse('!craftRecipe("dye", 1)', ACTION_SYSTEM).reason,
        'invalid_registry_name:dye'
    );
    assert.equal(
        validateActionResponse('!attack("ender_dragon")', `${ACTION_SYSTEM} !attack`).ok,
        true
    );
    assert.equal(
        validateActionResponse('!attackPlayer("Steve")', `${ACTION_SYSTEM} !attackPlayer`).reason,
        'command_policy_violation'
    );
});

test('subgoal candidate parser accepts strict JSON and caps the candidate count', () => {
    const candidates = parseSubgoalCandidates(JSON.stringify({
        candidates: [
            {id: 'one', kind: 'obtain', target: '#logs'},
            {id: 'two', kind: 'craft', target: 'crafting_table'},
            {id: 'three', kind: 'obtain', target: 'cobblestone'},
            {id: 'four', kind: 'obtain', target: 'iron_ingot'}
        ]
    }));
    assert.equal(candidates.length, 3);
    assert.equal(candidates[0].id, 'one');
    assert.deepEqual(parseSubgoalCandidates('not json'), []);
});

test('strategic subgoal escalation uses one Codex JSON proposal', async () => {
    const planner = new EvelynPlanner();
    planner.codexEnabled = true;
    let calls = 0;
    planner.lastCodexAt = 0;
    planner.codex.sendPrompt = async (_prompt, label) => {
        calls += 1;
        assert.equal(label, 'mindcraft-strategic-subgoal');
        return JSON.stringify({
            candidates: [{
                id: 'obtain_blaze_rods',
                kind: 'obtain',
                target: '#blaze_rods',
                quantity: 6,
                success: {kind: 'inventory', target: '#blaze_rods', count: 6}
            }]
        });
    };
    const result = await planner.proposeStrategicSubgoals({
        ultimate_goal: 'Defeat the Ender Dragon',
        world_state: {dimension: 'nether'}
    });
    assert.equal(calls, 1);
    assert.equal(result[0].id, 'obtain_blaze_rods');
});

test('Codex is disabled before token lookup or network access by default', async () => {
    const previous = process.env.MINDCRAFT_CODEX_ENABLED;
    const previousFetch = globalThis.fetch;
    let fetchCalls = 0;
    delete process.env.MINDCRAFT_CODEX_ENABLED;
    globalThis.fetch = async () => {
        fetchCalls += 1;
        throw new Error('network must not be reached');
    };
    try {
        await assert.rejects(
            new CodexGateway().sendPrompt('untrusted chat'),
            /mindcraft_codex_disabled/
        );
        assert.equal(fetchCalls, 0);

        const planner = new EvelynPlanner();
        planner.codex.sendPrompt = async () => {
            throw new Error('Codex must not be called');
        };
        planner.proposeSubgoals = async () => [{id: 'local_subgoal'}];
        assert.equal(await planner.chooseRoute([{role: 'user', content: 'complex strategy'}]), 'local');
        assert.equal((await planner.proposeStrategicSubgoals({}))[0].id, 'local_subgoal');
    } finally {
        globalThis.fetch = previousFetch;
        if (previous === undefined) delete process.env.MINDCRAFT_CODEX_ENABLED;
        else process.env.MINDCRAFT_CODEX_ENABLED = previous;
    }
});

test('repeated failed action families force escalation', () => {
    const turns = [
        {role: 'assistant', content: '!searchWiki("desert biome blocks")'},
        {role: 'system', content: 'desert biome blocks was not found on the Minecraft Wiki.'},
        {role: 'assistant', content: '!searchWiki("desert biome materials")'},
        {role: 'system', content: 'desert biome materials was not found on the Minecraft Wiki.'},
        {role: 'assistant', content: '!searchWiki("desert biome resources")'},
        {role: 'system', content: 'desert biome resources was not found on the Minecraft Wiki.'},
        selfPrompt(),
    ];
    const analysis = analyzeRecentTurns(turns);
    assert.equal(analysis.shouldEscalate, true);
    assert.match(analysis.reason, /same_failed_action_family_repeated|consecutive_failures/);
});

test('material progress prevents a false failure escalation', () => {
    const turns = [
        {role: 'assistant', content: '!collectBlocks("oak_log", 1)'},
        {role: 'system', content: 'No more oak_log nearby to collect. Collected 1 oak_log.'},
        selfPrompt(),
    ];
    assert.equal(analyzeRecentTurns(turns).shouldEscalate, false);
});

test('repeating the same successful command still counts as a stuck loop', () => {
    const turns = [];
    for (let index = 0; index < 4; index++) {
        turns.push({role: 'assistant', content: '!craftRecipe("sandstone", 1)'});
        turns.push({
            role: 'system',
            content: `Successfully crafted sandstone, you now have ${index + 1} sandstone.`
        });
    }
    turns.push(selfPrompt());

    const analysis = analyzeRecentTurns(turns);
    assert.equal(analysis.shouldEscalate, true);
    assert.match(analysis.reason, /same_command_loop:4/);
});

test('Codex recovery JSON keeps only valid documented commands', () => {
    const plan = parseRecoveryPlan(
        JSON.stringify({
            reason: 'The requested block name was invalid.',
            steps: [
                '!nearbyBlocks',
                '!searchForBlock("wood", 32)',
                '!searchForBlock("oak_log", 32)',
                '/give @s oak_log'
            ],
            success_signals: ['oak_log found']
        }),
        ACTION_SYSTEM
    );
    assert.deepEqual(plan.steps, ['!nearbyBlocks', '!searchForBlock("oak_log", 32)']);
});

test('recovery policy rejects autonomous goal controls and unrelated targets', () => {
    const fixture = goalPolicyFixture();
    try {
        const plan = parseRecoveryPlan(
            JSON.stringify({
                steps: [
                    '!goal("find food")',
                    '!endGoal',
                    '!searchForBlock("sandstone", 32)',
                    '!nearbyBlocks',
                    '!searchForBlock("oak_log", 32)'
                ]
            }),
            `${ACTION_SYSTEM} !goal !endGoal`,
            {
                currentSubgoal: fixture.state.currentSubgoal
            }
        );
        assert.deepEqual(plan.steps, ['!nearbyBlocks', '!searchForBlock("oak_log", 32)']);
    } finally {
        fixture.cleanup();
    }
});

test('a single scoped recovery action is a valid plan', () => {
    const fixture = goalPolicyFixture();
    try {
        const plan = parseRecoveryPlan(
            JSON.stringify({steps: ['!searchForBlock("oak_log", 32)']}),
            ACTION_SYSTEM,
            {currentSubgoal: fixture.state.currentSubgoal}
        );
        assert.deepEqual(plan.steps, ['!searchForBlock("oak_log", 32)']);
    } finally {
        fixture.cleanup();
    }
});

test('observation exhaustion falls back to bounded relocation during Codex cooldown', () => {
    const fixture = goalPolicyFixture();
    try {
        fixture.state.currentSubgoal.observationStreak = 2;
        fixture.state.currentSubgoal.relocationRequired = true;
        fixture.write();
        const planner = new EvelynPlanner();
        assert.equal(
            planner.safeProbe(ACTION_SYSTEM, 'observation_budget_exhausted'),
            '!moveAway(16)'
        );
    } finally {
        fixture.cleanup();
    }
});

test('simple action bypasses Router and uses local Qwen directly', async () => {
    const planner = new EvelynPlanner();
    let routerCalls = 0;
    planner.chooseRoute = async () => {
        routerCalls += 1;
        return 'codex';
    };
    planner.requestLocal = async () => '!inventory';

    const result = await planner.sendRequest([selfPrompt()], ACTION_SYSTEM);
    assert.equal(result, '!inventory');
    assert.equal(routerCalls, 0);
});

test('local utility failures preserve memory and ignore bot chatter', async () => {
    const planner = new EvelynPlanner();
    planner.codexEnabled = true;
    let codexCalls = 0;
    planner.codex.sendRequest = async () => {
        codexCalls += 1;
        return '지금은 안전하게 판단할 수 없어 멈출게. !stop';
    };
    planner.requestLocal = async () => {
        throw new Error('local unavailable');
    };

    await assert.rejects(
        planner.sendRequest([], 'Update your memory by summarizing and respond only with the unwrapped memory text'),
        /mindcraft_memory_summary_unavailable/
    );
    assert.equal(
        await planner.sendRequest([], "Decide by outputting only 'respond' or 'ignore'"),
        'ignore'
    );
    assert.equal(codexCalls, 0);

    const prompter = Object.create(Prompter.prototype);
    prompter.agent = {history: {memory: 'keep-me'}};
    prompter.profile = {saving_memory: 'save'};
    prompter.chat_model = {sendRequest: async () => { throw new Error('local unavailable'); }};
    prompter.checkCooldown = async () => {};
    prompter.replaceStrings = async () => 'save';
    prompter._saveLog = async () => {};
    assert.equal(await prompter.promptMemSaving([]), 'keep-me');
});

test('policy-violating memory summaries keep the previous summary', async () => {
    const previousFetch = globalThis.fetch;
    const planner = new EvelynPlanner();
    planner.codexEnabled = true;
    let codexCalls = 0;
    planner.codex.sendRequest = async () => {
        codexCalls += 1;
        return '지금은 안전하게 판단할 수 없어 멈출게. !stop';
    };
    globalThis.fetch = async () => ({
        ok: true,
        json: async () => ({
            choices: [{message: {content: 'Never run /kill while remembering this turn.'}}]
        })
    });
    try {
        const prompter = Object.create(Prompter.prototype);
        prompter.agent = {history: {memory: 'keep-me'}};
        prompter.profile = {saving_memory: 'Update your memory by summarizing'};
        prompter.chat_model = planner;
        prompter.checkCooldown = async () => {};
        prompter.replaceStrings = async () => 'Update your memory by summarizing';
        prompter._saveLog = async () => {};
        assert.equal(await prompter.promptMemSaving([]), 'keep-me');
        assert.equal(codexCalls, 0);
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test('self-prompt action mode remains active across query-command followups', async () => {
    const planner = new EvelynPlanner();
    planner.requestLocal = async (_turns, _system, _stop, kind) => {
        assert.equal(kind, 'action');
        return '!inventory';
    };

    assert.equal(await planner.sendRequest([selfPrompt()], ACTION_SYSTEM), '!inventory');
    assert.equal(
        await planner.sendRequest([
            selfPrompt(),
            {role: 'assistant', content: '!inventory'},
            {role: 'system', content: 'INVENTORY: Nothing'},
        ], ACTION_SYSTEM),
        '!inventory'
    );
});

test('a direct user message clears latched self-prompt action mode', async () => {
    const planner = new EvelynPlanner();
    planner.requestLocal = async (_turns, _system, _stop, kind) => {
        assert.equal(kind, 'action');
        return '!inventory';
    };
    assert.equal(await planner.sendRequest([selfPrompt()], ACTION_SYSTEM), '!inventory');

    planner.requestLocal = async (_turns, _system, _stop, kind) => {
        assert.equal(kind, 'chat');
        return 'hello';
    };
    assert.equal(
        await planner.sendRequest([
            selfPrompt(),
            {role: 'assistant', content: '!inventory'},
            {role: 'system', content: 'INVENTORY: Nothing'},
            {role: 'user', content: 'hello'},
        ], 'Speak briefly to players'),
        'hello'
    );
});

test('stuck action requests one Codex plan and lets Qwen execute its first step', async () => {
    const fixture = goalPolicyFixture();
    try {
    const planner = new EvelynPlanner();
    planner.codexEnabled = true;
    let codexCalls = 0;
    planner.codex.sendPrompt = async () => {
        codexCalls += 1;
        return JSON.stringify({
            reason: 'Repeated wiki searches are not progressing.',
            steps: ['!nearbyBlocks', '!inventory']
        });
    };
    planner.requestLocal = async () => {
        throw new Error('Recovery execution must not let Qwen replace the exact Codex step');
    };

    const turns = [
        {role: 'assistant', content: '!searchWiki("desert blocks")'},
        {role: 'system', content: 'desert blocks was not found on the Minecraft Wiki.'},
        {role: 'assistant', content: '!searchWiki("desert materials")'},
        {role: 'system', content: 'desert materials was not found on the Minecraft Wiki.'},
        selfPrompt(),
    ];
    const result = await planner.sendRequest(turns, ACTION_SYSTEM);
    assert.equal(result, '!nearbyBlocks');
    assert.equal(codexCalls, 1);
    assert.equal(planner.recoveryPlan.steps.length, 2);
    } finally {
        fixture.cleanup();
    }
});

test('failed Codex recovery enters cooldown instead of retrying every planner tick', async () => {
    const fixture = goalPolicyFixture();
    try {
    const planner = new EvelynPlanner();
    planner.codexEnabled = true;
    let codexCalls = 0;
    planner.codex.sendPrompt = async () => {
        codexCalls += 1;
        throw new Error('temporary Codex failure');
    };
    const turns = [
        {role: 'assistant', content: '!searchWiki("desert blocks")'},
        {role: 'system', content: 'desert blocks was not found on the Minecraft Wiki.'},
        selfPrompt(),
    ];

    assert.equal(await planner.sendRequest(turns, ACTION_SYSTEM), '!nearbyBlocks');
    assert.equal(await planner.sendRequest(turns, ACTION_SYSTEM), '!inventory');
    assert.equal(codexCalls, 1);
    } finally {
        fixture.cleanup();
    }
});

test('recovery steps advance only after a matching structured execution outcome', () => {
    const fixture = goalPolicyFixture();
    try {
    const planner = new EvelynPlanner();
    planner.recoveryPlan = {
        reason: 'test',
        steps: ['!inventory'],
        createdAt: Date.now(),
        stepIndex: 0,
        lastIssued: '!inventory',
        lastObservedExecutionSequence: 0
    };

    assert.equal(planner.updateRecoveryPlan(), null);
    assert.notEqual(planner.recoveryPlan, null);

    fixture.state.executionSequence = 1;
    fixture.state.lastExecution = {
        sequence: 1,
        command: '!inventory',
        result: 'INVENTORY: oak_log: 1',
        relevant: true,
        failed: false,
        goalProgress: false
    };
    fixture.write();
    assert.equal(planner.updateRecoveryPlan(), 'recovery_plan_completed');
    assert.equal(planner.recoveryPlan, null);
    } finally {
        fixture.cleanup();
    }
});

test('active recovery plan survives an agent process restart', () => {
    const fixture = goalPolicyFixture();
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-planner-'));
    const statePath = path.join(directory, 'planner_state.json');
    process.env.MINDCRAFT_PLANNER_STATE_PATH = statePath;
    try {
        const first = new EvelynPlanner();
        first.recoveryPlan = {
            reason: 'test restart recovery',
            goalId: 'obtain_logs',
            steps: ['!nearbyBlocks', '!inventory'],
            successSignals: [],
            abortSignals: [],
            createdAt: Date.now(),
            stepIndex: 0,
            lastIssued: '!nearbyBlocks'
        };
        first.lastCodexAt = Date.now();
        first.persistPlannerState();

        const restored = new EvelynPlanner();
        assert.deepEqual(restored.recoveryPlan.steps, ['!nearbyBlocks', '!inventory']);
        assert.equal(restored.recoveryPlan.lastIssued, '!nearbyBlocks');
        assert.ok(restored.lastCodexAt > 0);
    } finally {
        delete process.env.MINDCRAFT_PLANNER_STATE_PATH;
        fs.rmSync(directory, {recursive: true, force: true});
        fixture.cleanup();
    }
});
