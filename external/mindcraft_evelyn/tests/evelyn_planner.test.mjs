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
import { Agent } from '/app/mindcraft/src/agent/agent.js';
import convoManager from '/app/mindcraft/src/agent/conversation.js';
import { EvelynGoalManager } from '/app/mindcraft/src/agent/evelyn_goal_manager.js';
import { History } from '/app/mindcraft/src/agent/history.js';
import { setSettings } from '/app/mindcraft/src/agent/settings.js';

setSettings({max_messages: 8});

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
        commandCode: '!inventory',
        contentFree: true,
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

test('planner recovery state is not persisted across restart', () => {
    const fixture = goalPolicyFixture();
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-planner-'));
    const statePath = path.join(directory, 'planner_state.json');
    const previousStatePath = process.env.MINDCRAFT_PLANNER_STATE_PATH;
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

        assert.equal(fs.existsSync(statePath), false);
        const restarted = new EvelynPlanner();
        assert.equal(restarted.recoveryPlan, null);
        assert.equal(restarted.lastCodexAt, 0);
        assert.deepEqual(fs.readdirSync(directory), []);
    } finally {
        if (previousStatePath === undefined) delete process.env.MINDCRAFT_PLANNER_STATE_PATH;
        else process.env.MINDCRAFT_PLANNER_STATE_PATH = previousStatePath;
        fs.rmSync(directory, {recursive: true, force: true});
        fixture.cleanup();
    }
});

test('ephemeral history fences model exposure without persistence', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-history-boundary-'));
    const previousCwd = process.cwd();
    const previousFetch = globalThis.fetch;
    const previousStatePath = process.env.MINDCRAFT_PLANNER_STATE_PATH;
    const originalConsole = {
        error: console.error,
        log: console.log,
        warn: console.warn,
    };
    const privateUser = 'PRIVATE_HISTORY_USER_CANARY';
    const privateAssistant = 'PRIVATE_HISTORY_ASSISTANT_CANARY';
    const privateSender = 'PRIVATE_HISTORY_SENDER_CANARY';
    const privateSelfPrompt = 'PRIVATE_SELF_PROMPT_CANARY';
    const capturedLogs = [];
    const fetchBodies = [];
    const pendingFetches = [];
    const agent = {
        name: 'Evelyn_0428',
        self_prompter: {
            interrupt: false,
            isStopped: () => false,
            prompt: privateSelfPrompt,
            state: 2,
        },
        task: {taskStartTime: null},
        last_sender: privateSender,
    };

    const waitForFetchCount = async (expected) => {
        for (let attempt = 0; attempt < 100 && pendingFetches.length < expected; attempt += 1) {
            await new Promise((resolve) => setImmediate(resolve));
        }
        assert.equal(pendingFetches.length, expected);
    };
    const releaseNextFetch = (content) => {
        const release = pendingFetches.shift();
        assert.ok(release);
        release({
            ok: true,
            status: 200,
            json: async () => ({choices: [{message: {content}}]}),
        });
    };
    const allFileText = (root) => {
        const rows = [];
        const visit = (current) => {
            for (const entry of fs.readdirSync(current, {withFileTypes: true})) {
                const target = path.join(current, entry.name);
                if (entry.isDirectory()) visit(target);
                else if (entry.isFile()) rows.push(fs.readFileSync(target, 'utf8'));
            }
        };
        visit(root);
        return rows.join('\n');
    };

    process.chdir(directory);
    process.env.MINDCRAFT_PLANNER_STATE_PATH = path.join(directory, 'planner-state.json');
    console.log = (...args) => capturedLogs.push(args.join(' '));
    console.warn = (...args) => capturedLogs.push(args.join(' '));
    console.error = (...args) => capturedLogs.push(args.join(' '));
    globalThis.fetch = async (_url, options) => {
        fetchBodies.push(String(options?.body || ''));
        return new Promise((resolve) => pendingFetches.push(resolve));
    };

    try {
        const legacyAgent = {...agent, name: 'Legacy_Evelyn'};
        const legacy = new History(legacyAgent);
        const legacyBytes = JSON.stringify({memory: privateUser, turns: []});
        fs.mkdirSync(path.dirname(legacy.memory_fp), {recursive: true});
        fs.writeFileSync(legacy.memory_fp, legacyBytes, 'utf8');
        assert.throws(
            () => legacy.load(),
            (error) => error?.code === 'mindcraft_history_persistence_disabled',
        );
        assert.equal(fs.readFileSync(legacy.memory_fp, 'utf8'), legacyBytes);
        fs.rmSync(path.dirname(legacy.memory_fp), {recursive: true, force: true});

        const live = new History(agent);
        live.add('Player', privateUser);
        live.add(agent.name, privateAssistant);
        assert.deepEqual(live.save(), {
            schema: 'mindcraft.history.ephemeral.v1',
            memoryGeneration: 2,
            turnCount: 2,
            contentFree: true,
        });
        assert.equal(fs.existsSync(live.memory_fp), false);
        assert.equal(fs.existsSync(live.history_dir), false);

        const planner = new EvelynPlanner();
        agent.prompter = {chat_model: planner, code_model: planner};

        const firstSnapshot = live.getHistory();
        const firstRequest = planner.sendRequest(firstSnapshot, 'Speak briefly to players');
        await waitForFetchCount(1);
        assert.match(fetchBodies[0], new RegExp(privateUser));
        assert.match(fetchBodies[0], new RegExp(privateAssistant));
        assert.throws(
            () => live.clear(),
            (error) => error?.code === 'mindcraft_history_busy',
        );
        releaseNextFetch('first safe response');
        assert.equal(await firstRequest, 'first safe response');

        const changedSnapshot = live.getHistory();
        const changedRequest = planner.sendRequest(changedSnapshot, 'Speak briefly to players');
        await waitForFetchCount(1);
        live.add('system', 'current state changed after request admission');
        releaseNextFetch('STALE_RESPONSE_CANARY');
        await assert.rejects(
            changedRequest,
            (error) => error?.code === 'mindcraft_history_stale',
        );

        planner.recoveryPlan = {steps: ['!inventory']};
        planner.actionModeUntil = Date.now() + 60_000;
        planner.probeIndex = 3;
        const cleared = live.clear();
        assert.equal(cleared.contentFree, true);
        assert.equal(cleared.persistent, false);
        assert.equal(agent.last_sender, null);
        assert.equal(planner.recoveryPlan, null);
        assert.equal(planner.actionModeUntil, 0);
        assert.equal(planner.probeIndex, 0);
        assert.equal(agent.self_prompter.interrupt, true);
        assert.equal(agent.self_prompter.state, 0);
        assert.equal(agent.self_prompter.prompt, '');
        assert.equal(fs.existsSync(process.env.MINDCRAFT_PLANNER_STATE_PATH), false);

        const fetchCountBeforeOldSnapshot = fetchBodies.length;
        await assert.rejects(
            planner.sendRequest(firstSnapshot, 'Speak briefly to players'),
            (error) => error?.code === 'mindcraft_history_stale',
        );
        assert.equal(fetchBodies.length, fetchCountBeforeOldSnapshot);

        const restarted = new History(agent);
        const restored = restarted.load();
        assert.equal(restored, null);
        assert.equal(restarted.memory, '');
        assert.deepEqual(restarted.turns, []);
        assert.equal(restarted.generation, 0);

        const persistedText = allFileText(directory);
        for (const canary of [
            privateUser,
            privateAssistant,
            privateSender,
            privateSelfPrompt,
            'STALE_RESPONSE_CANARY',
        ]) {
            assert.equal(persistedText.includes(canary), false);
            assert.equal(capturedLogs.join('\n').includes(canary), false);
        }
    } finally {
        globalThis.fetch = previousFetch;
        console.error = originalConsole.error;
        console.log = originalConsole.log;
        console.warn = originalConsole.warn;
        process.chdir(previousCwd);
        if (previousStatePath === undefined) delete process.env.MINDCRAFT_PLANNER_STATE_PATH;
        else process.env.MINDCRAFT_PLANNER_STATE_PATH = previousStatePath;
        fs.rmSync(directory, {recursive: true, force: true});
    }
});

test('Agent whole-turn lease fences clear and goal state persists content-free', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-agent-boundary-'));
    const statePath = path.join(directory, 'goal-manager-state.json');
    const rawCommand = '!collectBlocks("PRIVATE_GOAL_TARGET_CANARY", 1)';
    const rawResult = 'PRIVATE_GOAL_RESULT_CANARY';
    const rawReason = 'PRIVATE_GOAL_REASON_CANARY';
    const rawObservation = '!searchWiki("PRIVATE_OBSERVATION_CANARY")';
    const interAgentCanary = 'PRIVATE_INTER_AGENT_CANARY';
    const previousGoalStatePath = process.env.MINDCRAFT_GOAL_MANAGER_STATE_PATH;
    const pendingGates = [];
    const originalSendToBot = convoManager.sendToBot;
    const execution = {
        sequence: 7,
        command: rawCommand,
        result: rawResult,
        autonomous: true,
        relevant: true,
        failed: false,
        worldChanged: true,
        goalProgress: false,
        recordedAt: 123,
    };
    const gateDecision = {
        command: rawCommand,
        reason: rawReason,
        mode: 'gated',
        relevant: false,
        allowed: false,
        decidedAt: 124,
    };
    const deferred = () => {
        let resolve;
        const promise = new Promise((done) => { resolve = done; });
        const gate = {promise, resolve};
        pendingGates.push(gate);
        return gate;
    };
    const waitFor = async (condition) => {
        for (let attempt = 0; attempt < 100 && !condition(); attempt += 1) {
            await new Promise((resolve) => setImmediate(resolve));
        }
        assert.equal(condition(), true);
    };
    const agent = Object.assign(Object.create(Agent.prototype), {
        name: 'Evelyn_0428',
        last_sender: null,
        shut_up: false,
        bot: {modes: {flushBehaviorLog: () => ''}},
        self_prompter: {
            interrupt: false,
            state: 0,
            prompt: '',
            isActive: () => false,
            shouldInterrupt: () => false,
        },
        checkTaskDone: async () => {},
    });

    try {
        const invalidStatePath = path.join(directory, 'state-directory');
        fs.mkdirSync(invalidStatePath);
        assert.throws(
            () => new EvelynGoalManager(agent, {
                statePath: invalidStatePath,
                mode: 'gated',
                ultimateGoal: 'survive',
            }),
            (error) => error?.code === 'mindcraft_goal_state_reset_failed',
        );

        fs.writeFileSync(statePath, JSON.stringify({
            version: 1,
            ultimateGoal: 'survive',
            mode: 'gated',
            autonomyState: 'active',
            executionSequence: 7,
            currentSubgoal: {
                id: 'observe_logs',
                kind: 'obtain',
                target: '#logs',
                success: {kind: 'inventory', target: '#logs', count: 1},
                attempts: 0,
                actionBudget: 8,
                allowedCommands: ['!inventory', '!nearbyBlocks'],
                allowedTargets: ['#logs'],
                observationCounts: {'!inventory': 2, [rawObservation]: 9},
                observationStreak: 2,
                gateRejects: 2,
            },
            recentActions: [execution],
            lastExecution: execution,
            lastGateDecision: gateDecision,
        }));
        const goalManager = new EvelynGoalManager(agent, {
            statePath,
            mode: 'gated',
            ultimateGoal: 'survive',
        });
        agent.goal_manager = goalManager;
        agent.history = new History(agent);

        let durable = JSON.parse(fs.readFileSync(statePath, 'utf8'));
        assert.deepEqual(durable.recentActions, []);
        assert.equal(durable.lastExecution.commandCode, '!collectBlocks');
        assert.equal(durable.lastExecution.contentFree, true);
        assert.equal('command' in durable.lastExecution, false);
        assert.equal('result' in durable.lastExecution, false);
        assert.equal(durable.lastGateDecision.contentFree, true);
        assert.equal('command' in durable.lastGateDecision, false);
        assert.equal('reason' in durable.lastGateDecision, false);
        assert.deepEqual(durable.currentSubgoal.observationCounts, {'!inventory': 2});
        assert.deepEqual(goalManager.state.currentSubgoal.observationCounts, {'!inventory': 2});

        goalManager.state.recentActions = [execution];
        goalManager.state.lastExecution = execution;
        goalManager.state.lastGateDecision = gateDecision;
        goalManager.persist({strict: true});
        const durableText = fs.readFileSync(statePath, 'utf8');
        for (const canary of [rawCommand, rawResult, rawReason, rawObservation]) {
            assert.equal(durableText.includes(canary), false);
            assert.equal(JSON.stringify(agent.bot.evelynGoalState).includes(canary), false);
        }
        assert.deepEqual(agent.bot.evelynGoalState.current_subgoal, {id: 'observe_logs'});
        assert.equal('ultimate_goal' in agent.bot.evelynGoalState, false);
        assert.equal('priority_request' in agent.bot.evelynGoalState, false);
        assert.equal('minimum_kit' in agent.bot.evelynGoalState, false);
        assert.equal(agent.bot.evelynGoalState.content_free, true);

        process.env.MINDCRAFT_GOAL_MANAGER_STATE_PATH = statePath;
        const policyPlanner = new EvelynPlanner();
        let policyPrompt = '';
        policyPlanner.codexEnabled = true;
        policyPlanner.codex.sendPrompt = async (prompt) => {
            policyPrompt = prompt;
            return JSON.stringify({steps: ['!inventory']});
        };
        await policyPlanner.createRecoveryPlan([], ACTION_SYSTEM, 'test');
        assert.equal(policyPrompt.includes(rawObservation), false);
        assert.match(policyPrompt, /"allowedCommands":\["!inventory","!nearbyBlocks"\]/);

        const releasePrepare = deferred();
        let prepareStarted = false;
        goalManager.prepareForPrompt = async () => {
            prepareStarted = true;
            await releasePrepare.promise;
        };
        const releaseRoute = deferred();
        let routeStarted = false;
        agent.prompter = {
            promptConvo: async () => 'safe response',
        };
        agent.routeResponse = async () => {
            routeStarted = true;
            await releaseRoute.promise;
        };
        convoManager.initAgent(agent);
        convoManager.updateAgents([]);

        const turn = agent.handleMessage('system', 'continue safely', 1);
        await waitFor(() => prepareStarted);
        assert.throws(
            () => agent.history.clear(),
            (error) => error?.code === 'mindcraft_history_busy',
        );
        assert.equal(goalManager.state.lastExecution.result, rawResult);

        releasePrepare.resolve();
        await waitFor(() => routeStarted);
        assert.throws(
            () => agent.history.clear(),
            (error) => error?.code === 'mindcraft_history_busy',
        );
        releaseRoute.resolve();
        assert.equal(await turn, false);
        assert.equal(agent.history.activeExposures, 0);

        const cleared = agent.history.clear();
        assert.equal(cleared.persistent, false);
        assert.deepEqual(goalManager.state.recentActions, []);
        assert.equal(goalManager.state.lastExecution, null);
        assert.equal(goalManager.state.lastGateDecision, null);
        assert.deepEqual(goalManager.state.currentSubgoal.observationCounts, {});
        assert.equal(goalManager.state.currentSubgoal.observationStreak, 0);
        assert.equal(goalManager.state.currentSubgoal.gateRejects, 0);
        durable = JSON.parse(fs.readFileSync(statePath, 'utf8'));
        assert.deepEqual(durable.recentActions, []);
        assert.equal(durable.lastExecution, null);
        assert.equal(durable.lastGateDecision, null);
        assert.deepEqual(durable.currentSubgoal.observationCounts, {});
        assert.equal(durable.currentSubgoal.observationStreak, 0);
        assert.equal(durable.currentSubgoal.gateRejects, 0);

        let scheduledHandleCalls = 0;
        agent.handleMessage = async () => { scheduledHandleCalls += 1; };
        agent.isIdle = () => false;
        agent.actions = {currentActionLabel: ''};
        const releasePause = deferred();
        let pauseStarted = false;
        agent.self_prompter.isActive = () => true;
        agent.self_prompter.pause = async () => {
            pauseStarted = true;
            await releasePause.promise;
        };
        const releaseClassifier = deferred();
        let classifierStarted = false;
        agent.prompter.promptShouldRespondToBot = async () => {
            classifierStarted = true;
            return releaseClassifier.promise;
        };
        convoManager.initAgent(agent);
        convoManager.updateAgents([{name: 'OtherBot', in_game: true}]);
        const inbound = convoManager.receiveFromBot('OtherBot', {
            message: 'old queued message',
            start: true,
            end: false,
        });
        await waitFor(() => pauseStarted);
        assert.throws(
            () => agent.history.clear(),
            (error) => error?.code === 'mindcraft_history_busy',
        );
        releasePause.resolve();
        await waitFor(() => classifierStarted);
        assert.throws(
            () => agent.history.clear(),
            (error) => error?.code === 'mindcraft_history_busy',
        );
        releaseClassifier.resolve(true);
        await inbound;
        const queuedConversation = convoManager._getConvo('OtherBot');
        assert.equal(queuedConversation.in_queue.length, 1);
        assert.ok(queuedConversation.inMessageTimer);

        agent.self_prompter.isPaused = () => false;
        convoManager.endConversation('OtherBot');
        agent.history.clear();
        assert.deepEqual(Object.keys(convoManager.convos), []);
        await new Promise((resolve) => setTimeout(resolve, 300));
        assert.equal(scheduledHandleCalls, 0);
        assert.deepEqual(agent.history.turns, []);
        delete agent.handleMessage;

        const releaseOutboundPause = deferred();
        let outboundPauseStarted = false;
        agent.self_prompter.pause = async () => {
            outboundPauseStarted = true;
            await releaseOutboundPause.promise;
        };
        const sentToBots = [];
        convoManager.sendToBot = (...args) => { sentToBots.push(args); };
        const outboundTurn = agent.handleMessage(
            'Player',
            `!startConversation("OtherBot", "${interAgentCanary}")`,
            1,
        );
        await waitFor(() => outboundPauseStarted);
        assert.throws(
            () => agent.history.clear(),
            (error) => error?.code === 'mindcraft_history_busy',
        );
        assert.deepEqual(sentToBots, []);
        releaseOutboundPause.resolve();
        assert.equal(await outboundTurn, true);
        assert.equal(sentToBots.length, 1);
        assert.equal(sentToBots[0][0], 'OtherBot');
        assert.equal(sentToBots[0][1], interAgentCanary);
        assert.equal(agent.history.activeExposures, 0);
        assert.equal(agent.history.clear().persistent, false);
    } finally {
        for (const gate of pendingGates) gate.resolve(false);
        convoManager.sendToBot = originalSendToBot;
        convoManager.resetHistoryDerivedState?.();
        if (previousGoalStatePath === undefined) delete process.env.MINDCRAFT_GOAL_MANAGER_STATE_PATH;
        else process.env.MINDCRAFT_GOAL_MANAGER_STATE_PATH = previousGoalStatePath;
        fs.rmSync(directory, {recursive: true, force: true});
    }
});
