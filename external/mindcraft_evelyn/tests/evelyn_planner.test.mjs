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
import { Prompter } from '/app/mindcraft/src/models/prompter.js';
import { Agent } from '/app/mindcraft/src/agent/agent.js';
import { handleDisconnection } from '/app/mindcraft/src/agent/connection_handler.js';
import convoManager from '/app/mindcraft/src/agent/conversation.js';
import { EvelynGoalManager } from '/app/mindcraft/src/agent/evelyn_goal_manager.js';
import { History } from '/app/mindcraft/src/agent/history.js';
import { setSettings } from '/app/mindcraft/src/agent/settings.js';
import {
    claimMindcraftRecoveryIssuance,
    discardMindcraftRecoveryIssuance,
} from '/app/mindcraft/src/utils/evelyn_history_boundary.js';

setSettings({max_messages: 8});

test('disconnect output excludes server-provided reason text', () => {
    const privateKnown = 'PRIVATE_MINECRAFT_KICK_KNOWN_token-7f19';
    const privateUnknown = 'PRIVATE_MINECRAFT_KICK_UNKNOWN_token-8a20';
    const logs = [];
    const originalError = console.error;
    console.error = (...args) => logs.push(args.map(String).join(' '));
    try {
        const known = handleDisconnection(
            'Evelyn',
            {text: `Timed out while contacting ${privateKnown}`},
            'Kicked'
        );
        const unknown = handleDisconnection('Evelyn', {text: privateUnknown}, 'Kicked');

        assert.deepEqual(known, {
            type: 'network_error',
            msg: '[LoginGuard] Kicked: Network Error: Connection timed out or was lost.',
            isFatal: false,
            event: 'Kicked'
        });
        assert.deepEqual(unknown, {
            type: 'other',
            msg: '[LoginGuard] Kicked: Unclassified connection failure.',
            isFatal: true,
            event: 'Kicked'
        });
        const visible = JSON.stringify({known, unknown, logs});
        assert.doesNotMatch(visible, new RegExp(privateKnown));
        assert.doesNotMatch(visible, new RegExp(privateUnknown));
    } finally {
        console.error = originalError;
    }
});

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

function brokerFetchFixture() {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'evelyn-mindcraft-broker-'));
    const tokenPath = path.join(directory, 'token');
    const endpoint = 'http://broker.test/internal/mindcraft-llm';
    const token = 'A'.repeat(48);
    const previous = {
        fetch: globalThis.fetch,
        url: process.env.MINDCRAFT_LLM_BROKER_URL,
        tokenFile: process.env.MINDCRAFT_LLM_BROKER_TOKEN_FILE,
    };
    const requests = [];
    const acknowledgements = [];
    const pending = [];
    const streams = new Map();
    const cancelled = new Set();
    const encoder = new TextEncoder();
    let leaseSequence = 0;
    let ackPayload = {ok: true, contentFree: true};

    fs.writeFileSync(tokenPath, token);
    process.env.MINDCRAFT_LLM_BROKER_URL = endpoint;
    process.env.MINDCRAFT_LLM_BROKER_TOKEN_FILE = tokenPath;
    globalThis.fetch = async (url, options = {}) => {
        const href = String(url);
        const body = JSON.parse(String(options.body || '{}'));
        if (href === `${endpoint}/ack`) {
            acknowledgements.push(body);
            const controller = streams.get(body.leaseId);
            assert.ok(controller, 'ACK must reference an open broker lease');
            controller.close();
            return new Response(JSON.stringify(ackPayload), {
                status: 200,
                headers: {'Content-Type': 'application/json'},
            });
        }
        assert.equal(href, endpoint);
        requests.push({href, body, headers: options.headers});
        return new Promise((resolve, reject) => pending.push({body, resolve, reject}));
    };

    return {
        endpoint,
        token,
        requests,
        acknowledgements,
        cancelled,
        setAckPayload(value) {
            ackPayload = value;
        },
        async waitForPending(expected = 1) {
            for (let attempt = 0; attempt < 100 && pending.length < expected; attempt += 1) {
                await new Promise((resolve) => setImmediate(resolve));
            }
            assert.equal(pending.length, expected);
        },
        releaseNext(content, memoryReceiptRef = {
            schema: 'conversation.memory-receipt-ref.v1',
            state: 'not_used',
            memoryVersion: 0,
            suppliedNoteIds: [],
            suppliedNoteCount: 0,
            contentFree: true,
        }) {
            const next = pending.shift();
            assert.ok(next);
            const leaseId = (++leaseSequence).toString(16).padStart(64, '0');
            const response = new Response(new ReadableStream({
                start(controller) {
                    streams.set(leaseId, controller);
                    controller.enqueue(encoder.encode(`${JSON.stringify({
                        schema: 'mindcraft.llm-result.v1',
                        requestId: next.body.requestId,
                        content,
                        memoryReceiptRef,
                        deliveryLease: {
                            schema: 'mindcraft.llm-delivery-lease.v1',
                            leaseId,
                            ttlMs: 660000,
                            contentFree: true,
                        },
                    })}\n`));
                },
                cancel() {
                    cancelled.add(leaseId);
                },
            }), {
                status: 200,
                headers: {'Content-Type': 'application/x-ndjson; charset=utf-8'},
            });
            next.resolve(response);
        },
        rejectNext(error = new Error('broker unavailable')) {
            const next = pending.shift();
            assert.ok(next);
            next.reject(error);
        },
        cleanup() {
            for (const controller of streams.values()) {
                try { controller.close(); } catch {}
            }
            globalThis.fetch = previous.fetch;
            if (previous.url === undefined) delete process.env.MINDCRAFT_LLM_BROKER_URL;
            else process.env.MINDCRAFT_LLM_BROKER_URL = previous.url;
            if (previous.tokenFile === undefined) delete process.env.MINDCRAFT_LLM_BROKER_TOKEN_FILE;
            else process.env.MINDCRAFT_LLM_BROKER_TOKEN_FILE = previous.tokenFile;
            fs.rmSync(directory, {recursive: true, force: true});
        },
    };
}

async function settleBrokerRequest(promise) {
    let timer;
    try {
        return await Promise.race([
            promise,
            new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error('broker_test_timeout')), 1000);
            }),
        ]);
    } finally {
        clearTimeout(timer);
    }
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

test('strategic subgoal escalation uses one broker JSON proposal', async () => {
    const broker = brokerFetchFixture();
    const planner = new EvelynPlanner();
    planner.codexEnabled = true;
    planner.lastCodexAt = 0;
    try {
        const request = planner.proposeStrategicSubgoals({
            ultimate_goal: 'Defeat the Ender Dragon',
            world_state: {dimension: 'nether'}
        });
        await broker.waitForPending();
        broker.releaseNext(JSON.stringify({
            candidates: [{
                id: 'obtain_blaze_rods',
                kind: 'obtain',
                target: '#blaze_rods',
                quantity: 6,
                success: {kind: 'inventory', target: '#blaze_rods', count: 6}
            }]
        }));
        const result = await request;
        assert.equal(result[0].id, 'obtain_blaze_rods');
        assert.equal(broker.requests.length, 1);
        assert.equal(broker.requests[0].body.requestKind, 'subgoal');
    } finally {
        broker.cleanup();
    }
});

test('strategic routing remains disabled before network access by default', async () => {
    const previous = process.env.MINDCRAFT_CODEX_ENABLED;
    const previousFetch = globalThis.fetch;
    let fetchCalls = 0;
    delete process.env.MINDCRAFT_CODEX_ENABLED;
    globalThis.fetch = async () => {
        fetchCalls += 1;
        throw new Error('network must not be reached');
    };
    try {
        const planner = new EvelynPlanner();
        planner.proposeSubgoals = async () => [{id: 'local_subgoal'}];
        assert.equal(await planner.chooseRoute([{role: 'user', content: 'complex strategy'}]), 'local');
        assert.equal((await planner.proposeStrategicSubgoals({}))[0].id, 'local_subgoal');
        assert.equal(fetchCalls, 0);
    } finally {
        globalThis.fetch = previousFetch;
        if (previous === undefined) delete process.env.MINDCRAFT_CODEX_ENABLED;
        else process.env.MINDCRAFT_CODEX_ENABLED = previous;
    }
});

test('MINDCRAFT_CODEX_ENABLED routes complex work only through the broker', async () => {
    const previousEnabled = process.env.MINDCRAFT_CODEX_ENABLED;
    const previousGatewayUrl = process.env.MINDCRAFT_CODEX_GATEWAY_URL;
    process.env.MINDCRAFT_CODEX_ENABLED = 'true';
    process.env.MINDCRAFT_CODEX_GATEWAY_URL = 'http://direct.invalid/codex/action';
    const broker = brokerFetchFixture();
    try {
        const planner = new EvelynPlanner();
        const request = planner.sendRequest(
            [{role: 'user', content: 'make a complex multi-step strategy'}],
            'Speak briefly to players',
        );
        await broker.waitForPending();
        broker.releaseNext('{"route":"codex"}');
        await broker.waitForPending();
        broker.releaseNext('broker-only complex response');
        assert.equal(await request, 'broker-only complex response');
        assert.deepEqual(
            broker.requests.map((entry) => entry.body.requestKind),
            ['router', 'chat'],
        );
        assert.ok(broker.requests.every((entry) => entry.href === broker.endpoint));
    } finally {
        broker.cleanup();
        if (previousEnabled === undefined) delete process.env.MINDCRAFT_CODEX_ENABLED;
        else process.env.MINDCRAFT_CODEX_ENABLED = previousEnabled;
        if (previousGatewayUrl === undefined) delete process.env.MINDCRAFT_CODEX_GATEWAY_URL;
        else process.env.MINDCRAFT_CODEX_GATEWAY_URL = previousGatewayUrl;
    }
});

test('embedding remains local when the legacy Codex flag is enabled', async () => {
    const previousEnabled = process.env.MINDCRAFT_CODEX_ENABLED;
    const previousFetch = globalThis.fetch;
    let fetchCalls = 0;
    process.env.MINDCRAFT_CODEX_ENABLED = 'true';
    globalThis.fetch = async () => {
        fetchCalls += 1;
        throw new Error('embedding must remain local');
    };
    try {
        const vector = await new EvelynPlanner().embed('oak log oak');
        assert.equal(vector.length, 128);
        assert.ok(Math.abs(Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0)) - 1) < 1e-12);
        assert.equal(fetchCalls, 0);
    } finally {
        globalThis.fetch = previousFetch;
        if (previousEnabled === undefined) delete process.env.MINDCRAFT_CODEX_ENABLED;
        else process.env.MINDCRAFT_CODEX_ENABLED = previousEnabled;
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
    const previousFetch = globalThis.fetch;
    let fetchCalls = 0;
    globalThis.fetch = async () => {
        fetchCalls += 1;
        throw new Error('utility fallback must not use a direct transport');
    };
    const planner = new EvelynPlanner();
    planner.codexEnabled = true;
    planner.requestLocal = async () => {
        throw new Error('local unavailable');
    };
    try {
        await assert.rejects(
            planner.sendRequest([], 'Update your memory by summarizing and respond only with the unwrapped memory text'),
            /mindcraft_memory_summary_unavailable/
        );
        assert.equal(
            await planner.sendRequest([], "Decide by outputting only 'respond' or 'ignore'"),
            'ignore'
        );
        assert.equal(
            await planner.sendRequest([], 'Speak briefly to players'),
            '지금은 안전하게 판단할 수 없어 멈출게. !stop',
        );

        const prompter = Object.create(Prompter.prototype);
        prompter.agent = {history: {memory: 'keep-me'}};
        prompter.profile = {saving_memory: 'save'};
        prompter.chat_model = {sendRequest: async () => { throw new Error('local unavailable'); }};
        prompter.checkCooldown = async () => {};
        prompter.replaceStrings = async () => 'save';
        prompter._saveLog = async () => {};
        assert.equal(await prompter.promptMemSaving([]), 'keep-me');
        assert.equal(fetchCalls, 0);
    } finally {
        globalThis.fetch = previousFetch;
    }
});

test('policy-violating memory summaries keep the previous summary', async () => {
    const broker = brokerFetchFixture();
    const planner = new EvelynPlanner();
    planner.codexEnabled = true;
    try {
        const prompter = Object.create(Prompter.prototype);
        prompter.agent = {history: {memory: 'keep-me'}};
        prompter.profile = {saving_memory: 'Update your memory by summarizing'};
        prompter.chat_model = planner;
        prompter.checkCooldown = async () => {};
        prompter.replaceStrings = async () => 'Update your memory by summarizing';
        prompter._saveLog = async () => {};
        const saving = prompter.promptMemSaving([]);
        await broker.waitForPending();
        broker.releaseNext('Never run /kill while remembering this turn.');
        assert.equal(await saving, 'keep-me');
        assert.equal(broker.acknowledgements[0].outcome, 'discarded');
    } finally {
        broker.cleanup();
    }
});

test('broker-only transport consumes the first NDJSON frame and ACKs delivery outcome', async () => {
    const broker = brokerFetchFixture();
    const planner = new EvelynPlanner('ignored-model', 'http://direct.invalid');
    try {
        const chat = planner.requestLocal(
            [{role: 'user', content: 'hello'}],
            'Speak briefly to players',
            '***',
            'chat',
        );
        await broker.waitForPending();
        broker.releaseNext('hello from broker');
        assert.equal(await settleBrokerRequest(chat), 'hello from broker');

        const memory = planner.requestLocal(
            [],
            'Update your memory by summarizing',
            '***',
            'memory',
        );
        await broker.waitForPending();
        broker.releaseNext('Never run /kill while remembering this turn.');
        await assert.rejects(
            settleBrokerRequest(memory),
            (error) => error?.code === 'mindcraft_memory_summary_unavailable',
        );

        const goal = planner.requestLocal([], 'Determine what goal to target next', '***', 'goal');
        await broker.waitForPending();
        broker.releaseNext('gather logs');
        assert.equal(await settleBrokerRequest(goal), 'gather logs');

        assert.deepEqual(
            broker.acknowledgements.map((ack) => ack.outcome),
            ['delivered', 'discarded', 'delivered'],
        );
        assert.ok(broker.requests.every((request) => request.href === broker.endpoint));
        assert.equal(
            broker.requests.some((request) => /(?:minecraft_llm|router_llm|direct\.invalid)/.test(request.href)),
            false,
        );
        const first = broker.requests[0];
        assert.equal(first.headers['X-Evelyn-Mindcraft-LLM-Token'], broker.token);
        assert.deepEqual(Object.keys(first.body).sort(), [
            'historyReceiptRef', 'messages', 'requestId', 'requestKind', 'schema',
        ]);
        assert.equal(first.body.schema, 'mindcraft.llm-request.v1');
        assert.equal(first.body.requestKind, 'chat');
        assert.deepEqual(
            broker.requests.map((request) => request.body.requestKind),
            ['chat', 'memory', 'chat'],
        );
        assert.equal(first.body.historyReceiptRef.state, 'not_used');
        assert.ok(first.body.messages.every((message) => message.memoryReceiptRef.state === 'not_used'));
        for (const acknowledgement of broker.acknowledgements) {
            assert.equal(acknowledgement.schema, 'mindcraft.llm-delivery-ack.v1');
            assert.equal(acknowledgement.contentFree, true);
        }
    } finally {
        broker.cleanup();
    }
});

test('broker ACK or receipt mismatch fails closed', async () => {
    const broker = brokerFetchFixture();
    const planner = new EvelynPlanner();
    try {
        broker.setAckPayload({ok: true, contentFree: false});
        const badAck = planner.requestLocal([], 'Speak briefly to players', '***', 'chat');
        await broker.waitForPending();
        broker.releaseNext('must not escape after a bad ACK');
        await assert.rejects(
            settleBrokerRequest(badAck),
            (error) => error?.code === 'mindcraft_llm_delivery_ack_invalid',
        );
        assert.equal(broker.acknowledgements.length, 1);

        broker.setAckPayload({ok: true, contentFree: true});
        const badReceipt = planner.requestLocal([], 'Speak briefly to players', '***', 'chat');
        await broker.waitForPending();
        broker.releaseNext('must not escape after a bad receipt', {
            schema: 'conversation.memory-receipt-ref.v1',
            state: 'unattributed',
            memoryVersion: 0,
            suppliedNoteIds: [],
            suppliedNoteCount: 0,
            contentFree: true,
        });
        await assert.rejects(
            settleBrokerRequest(badReceipt),
            (error) => error?.code === 'mindcraft_llm_result_invalid',
        );
        assert.equal(broker.acknowledgements.length, 1);
        assert.equal(broker.cancelled.size, 1);
    } finally {
        broker.cleanup();
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

test('stuck action requests one broker recovery plan and executes its first step', async () => {
    const fixture = goalPolicyFixture();
    const broker = brokerFetchFixture();
    try {
        const planner = new EvelynPlanner();
        planner.codexEnabled = true;
        planner.requestLocal = async () => {
            throw new Error('Recovery execution must not replace the exact broker step');
        };
        const turns = [
            {role: 'assistant', content: '!searchWiki("desert blocks")'},
            {role: 'system', content: 'desert blocks was not found on the Minecraft Wiki.'},
            {role: 'assistant', content: '!searchWiki("desert materials")'},
            {role: 'system', content: 'desert materials was not found on the Minecraft Wiki.'},
            selfPrompt(),
        ];
        const request = planner.sendRequest(turns, ACTION_SYSTEM);
        await broker.waitForPending();
        broker.releaseNext(JSON.stringify({
            reason: 'Repeated wiki searches are not progressing.',
            steps: ['!nearbyBlocks', '!inventory']
        }));
        assert.equal(await request, '!nearbyBlocks');
        assert.equal(broker.requests.length, 1);
        assert.equal(broker.requests[0].body.requestKind, 'recovery');
        assert.equal(planner.recoveryPlan.steps.length, 2);
    } finally {
        broker.cleanup();
        fixture.cleanup();
    }
});

test('failed broker recovery enters cooldown instead of retrying every planner tick', async () => {
    const fixture = goalPolicyFixture();
    const broker = brokerFetchFixture();
    try {
        const planner = new EvelynPlanner();
        planner.codexEnabled = true;
        const turns = [
            {role: 'assistant', content: '!searchWiki("desert blocks")'},
            {role: 'system', content: 'desert blocks was not found on the Minecraft Wiki.'},
            selfPrompt(),
        ];

        const first = planner.sendRequest(turns, ACTION_SYSTEM);
        await broker.waitForPending();
        broker.rejectNext(new Error('temporary broker failure'));
        assert.equal(await first, '!nearbyBlocks');
        assert.equal(await planner.sendRequest(turns, ACTION_SYSTEM), '!inventory');
        assert.equal(broker.requests.length, 1);
    } finally {
        broker.cleanup();
        fixture.cleanup();
    }
});

test('recovery steps consume only their exact one-shot execution issuance', async () => {
    const fixture = goalPolicyFixture();
    try {
    const planner = new EvelynPlanner();
    const issued = '!searchForBlock("oak_log", 32)';
    const turns = [selfPrompt()];
    planner.recoveryPlan = {
        reason: 'test',
        goalId: 'obtain_logs',
        steps: [issued],
        createdAt: Date.now(),
        stepIndex: 0,
        lastIssued: null,
        lastIssuedAt: null,
        pendingIssuance: null,
        pendingExecution: null,
    };

    assert.equal(await planner.runRecoveryStep(turns, ACTION_SYSTEM), issued);
    assert.equal(planner.updateRecoveryPlan(), null);
    assert.notEqual(planner.recoveryPlan, null);
    assert.equal(
        claimMindcraftRecoveryIssuance(
            turns,
            '!searchForBlock("birch_log", 32)',
        ),
        null,
    );
    assert.equal(claimMindcraftRecoveryIssuance([...turns], issued), null);
    fixture.state.executionSequence = 1;
    fixture.state.lastExecution = {
        sequence: 1,
        commandCode: '!searchForBlock',
        autonomous: false,
        contentFree: true,
        relevant: true,
        failed: false,
    };
    fixture.write();
    assert.equal(planner.updateRecoveryPlan(), null);
    assert.equal(await planner.sendActionRequest([...turns], ACTION_SYSTEM), '');

    const receipt = claimMindcraftRecoveryIssuance(turns, issued);
    assert.ok(receipt);
    const execution = Object.freeze({
        sequence: 1,
        commandCode: '!searchForBlock',
        autonomous: true,
        contentFree: true,
        relevant: true,
        failed: false,
        goalProgress: false
    });
    assert.equal(receipt.complete(execution), true);
    assert.equal(receipt.complete(execution), false);
    assert.equal(planner.updateRecoveryPlan(), 'recovery_plan_completed');
    assert.equal(planner.recoveryPlan, null);
    assert.equal(planner.updateRecoveryPlan(), null);

    const staleTurns = [selfPrompt()];
    planner.recoveryPlan = {
        reason: 'clear invalidation',
        goalId: 'obtain_logs',
        steps: [issued],
        createdAt: Date.now(),
        stepIndex: 0,
        lastIssued: null,
        lastIssuedAt: null,
        pendingIssuance: null,
        pendingExecution: null,
    };
    assert.equal(await planner.runRecoveryStep(staleTurns, ACTION_SYSTEM), issued);
    planner.clearRecoveryPlan();
    const staleReceipt = claimMindcraftRecoveryIssuance(staleTurns, issued);
    assert.ok(staleReceipt);
    assert.equal(staleReceipt.complete(execution), false);
    assert.equal(planner.recoveryPlan, null);

    const abandonedTurns = [selfPrompt()];
    planner.recoveryPlan = {
        reason: 'discarded before execution',
        goalId: 'obtain_logs',
        steps: [issued],
        createdAt: Date.now(),
        stepIndex: 0,
        lastIssued: null,
        lastIssuedAt: null,
        pendingIssuance: null,
        pendingExecution: null,
    };
    assert.equal(await planner.runRecoveryStep(abandonedTurns, ACTION_SYSTEM), issued);
    assert.equal(discardMindcraftRecoveryIssuance(abandonedTurns), true);
    assert.equal(discardMindcraftRecoveryIssuance(abandonedTurns), false);
    assert.equal(planner.recoveryPlan, null);
    } finally {
        fixture.cleanup();
    }
});

test('concurrent recovery planning has one in-flight owner', async () => {
    const fixture = goalPolicyFixture();
    const broker = brokerFetchFixture();
    try {
        const planner = new EvelynPlanner();
        const firstTurns = [selfPrompt()];
        const secondTurns = [selfPrompt()];
        const first = planner.recover(firstTurns, ACTION_SYSTEM, '***', 'first');
        await broker.waitForPending();
        assert.equal(
            await planner.recover(secondTurns, ACTION_SYSTEM, '***', 'second'),
            '',
        );
        assert.equal(broker.requests.length, 1);
        broker.releaseNext(JSON.stringify({
            reason: 'single owner',
            steps: ['!inventory'],
        }));
        assert.equal(await first, '!inventory');
        assert.equal(discardMindcraftRecoveryIssuance(firstTurns), true);
        assert.equal(planner.recoveryPlan, null);
    } finally {
        broker.cleanup();
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
    const broker = brokerFetchFixture();
    const previousCwd = process.cwd();
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
        await broker.waitForPending();
        const firstBody = JSON.stringify(broker.requests[0].body);
        assert.match(firstBody, new RegExp(privateUser));
        assert.match(firstBody, new RegExp(privateAssistant));
        assert.throws(
            () => live.clear(),
            (error) => error?.code === 'mindcraft_history_busy',
        );
        broker.releaseNext('first safe response');
        assert.equal(await firstRequest, 'first safe response');

        const changedSnapshot = live.getHistory();
        const changedRequest = planner.sendRequest(changedSnapshot, 'Speak briefly to players');
        await broker.waitForPending();
        live.add('system', 'current state changed after request admission');
        broker.releaseNext('STALE_RESPONSE_CANARY');
        await assert.rejects(
            changedRequest,
            (error) => error?.code === 'mindcraft_history_stale',
        );
        assert.deepEqual(
            broker.acknowledgements.map((ack) => ack.outcome),
            ['delivered', 'discarded'],
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

        const fetchCountBeforeOldSnapshot = broker.requests.length;
        await assert.rejects(
            planner.sendRequest(firstSnapshot, 'Speak briefly to players'),
            (error) => error?.code === 'mindcraft_history_stale',
        );
        assert.equal(broker.requests.length, fetchCountBeforeOldSnapshot);

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
        broker.cleanup();
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

        const recorded = await goalManager.recordActionResult(
            rawCommand,
            rawResult,
            {},
            {},
            {autonomous: true},
        );
        assert.equal(recorded.commandCode, '!collectBlocks');
        assert.equal(recorded.contentFree, true);
        assert.equal('command' in recorded, false);
        assert.equal('result' in recorded, false);
        const interrupted = await goalManager.recordActionResult(
            rawCommand,
            undefined,
            {},
            {},
            {autonomous: true},
        );
        assert.equal(interrupted.failed, true);

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
        const policyBroker = brokerFetchFixture();
        try {
            const planRequest = policyPlanner.createRecoveryPlan([], ACTION_SYSTEM, 'test');
            await policyBroker.waitForPending();
            const policyPrompt = policyBroker.requests[0].body.messages[0].content;
            policyBroker.releaseNext(JSON.stringify({steps: ['!inventory']}));
            await planRequest;
            assert.equal(policyPrompt.includes(rawObservation), false);
            assert.match(policyPrompt, /"allowedCommands":\["!inventory","!nearbyBlocks"\]/);
        } finally {
            policyBroker.cleanup();
        }

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
