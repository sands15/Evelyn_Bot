import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

process.env.MINDCRAFT_SELF_PROMPT_COOLDOWN_MS = '300';
process.env.MINDCRAFT_INTERRUPT_STOP_WAIT_MS = '250';
const [{ActionManager}, {SelfPrompter}, {getCommand, getCommandDocs}, {Agent}] = await Promise.all([
    import('../src/agent/action_manager.js'),
    import('../src/agent/self_prompter.js'),
    import('../src/agent/commands/index.js'),
    import('../src/agent/agent.js'),
]);

const actionManager = new ActionManager({});
assert.equal(actionManager.interruptPollMs, 100);
assert.equal(actionManager.interruptStopWaitMs, 250);
const selfPrompter = new SelfPrompter({});
assert.equal(selfPrompter.cooldown, 300);

let interruptRequests = 0;
let cleanKills = 0;
const busyManager = new ActionManager({
    requestInterrupt() {
        interruptRequests += 1;
    },
    cleanKill() {
        cleanKills += 1;
    },
});
busyManager.executing = true;
busyManager.currentActionLabel = 'action:stuck';
const busyResult = await busyManager.runAction('mode:self_preservation', async () => {});
assert.equal(busyResult.busy, true);
assert.equal(busyResult.interrupted, true);
assert.equal(busyManager.executing, true);
assert.ok(interruptRequests >= 1);
assert.equal(cleanKills, 0);

const rapidManager = new ActionManager({
    cleanKill() {
        cleanKills += 1;
    },
});
rapidManager.last_action_time = Date.now();
rapidManager.recent_action_counter = 5;
const rapidResult = await rapidManager.runAction('action:rapid', async () => {});
assert.equal(rapidResult.busy, true);
assert.equal(rapidResult.interrupted, true);
assert.equal(cleanKills, 0);

const shutdownOrder = [];
let releaseHistoryLoad;
const shutdownBot = {
    entity: {},
    quit() { shutdownOrder.push('quit'); },
    evelynCombatHistoryLoading: new Promise((resolve) => {
        releaseHistoryLoad = () => {
            shutdownOrder.push('history_loaded');
            resolve();
        };
    }),
    evelynCombatHistoryWriter: {
        flush: async () => shutdownOrder.push('combat_history_flushed'),
    },
};
const shutdownAgent = Object.assign(Object.create(Agent.prototype), {
    bot: shutdownBot,
    history: {
        add() { shutdownOrder.push('history_added'); },
        save() { shutdownOrder.push('history_saved'); },
    },
});
const originalExit = process.exit;
process.exit = (code) => shutdownOrder.push(`exit:${code}`);
try {
    const shutdown = shutdownAgent.cleanKill('test shutdown', 7);
    const duplicate = shutdownAgent.cleanKill('duplicate shutdown', 9);
    assert.equal(duplicate, shutdown);
    assert.equal(shutdownAgent._disconnectHandled, true);
    assert.deepEqual(shutdownOrder, [
        'quit', 'history_added', 'history_saved',
    ]);
    releaseHistoryLoad();
    await shutdown;
    assert.deepEqual(shutdownOrder.slice(-3), [
        'history_loaded', 'combat_history_flushed', 'exit:7',
    ]);
} finally {
    process.exit = originalExit;
}

const agent = {
    blocked_actions: [],
    goal_manager: {
        state: {
            currentSubgoal: {allowedCommands: ['!stats', '!stop']},
        },
    },
};
const autonomousDocs = getCommandDocs(agent, true);
assert.match(autonomousDocs, /!stats:/);
assert.match(autonomousDocs, /!stop:/);
assert.doesNotMatch(autonomousDocs, /!attack:/);
const userDocs = getCommandDocs(agent);
assert.match(userDocs, /!attack:/);
assert.match(userDocs, /!followPlayer:/);
assert.match(
    getCommand('!nearbyBlocks').perform.toString(),
    /getNearestBlocks\(bot\)/,
);

const agentSource = await readFile('/app/mindcraft/src/agent/agent.js', 'utf8');
const modesSource = await readFile('/app/mindcraft/src/agent/modes.js', 'utf8');
const prompterSource = await readFile('/app/mindcraft/src/models/prompter.js', 'utf8');
assert.match(agentSource, /promptConvo\(history, self_prompt\)/);
assert.match(agentSource, /MINDCRAFT_MODE_INTERVAL_MS/);
assert.match(agentSource, /this\.bot\.stopDigging\?\.\(\)/);
assert.match(agentSource, /this\.bot\.collectBlock\?\.cancelTask\?\.\(\)/);
assert.match(agentSource, /this\.bot\.pathfinder\?\.stop\?\.\(\)/);
assert.match(agentSource, /this\.bot\.pvp\?\.stop\?\.\(\)/);
assert.match(agentSource, /this\.bot\.swordpvp\?\.stop\?\.\(\)/);
assert.match(agentSource, /this\.bot\.bowpvp\?\.stop\?\.\(\)/);
assert.match(agentSource, /this\._disconnectHandled = true;[\s\S]*this\.bot\.quit\('Evelyn shutdown'\)/);
assert.match(agentSource, /else if \(typeof this\.bot\?\.end === 'function'\) this\.bot\.end\('Evelyn shutdown'\)/);
assert.match(agentSource, /if \(!this\.bot\.evelynMovementOwner\) \{/);
assert.match(modesSource, /else if \(agent\.isIdle\(\) && !bot\.evelynMovementOwner\)/);
assert.doesNotMatch(prompterSource, /stats \+= await getCommand\('!nearbyBlocks'\)/);

const profile = JSON.parse(await readFile('/app/mindcraft/profiles/evelyn.json', 'utf8'));
assert.equal(profile.cooldown, 300);
assert.ok(profile.conversing.indexOf('$COMMAND_DOCS') < profile.conversing.indexOf('$STATS'));

console.log('mindcraft-latency-runtime-ok');
