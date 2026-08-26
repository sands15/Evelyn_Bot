import {mkdtemp, readFile, rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import test from 'node:test';
import assert from 'node:assert/strict';
import {
    appendCombatEpisode,
    createCombatHistoryWriter,
    deserializeCombatHistory,
    defaultCombatPreset,
    loadCombatHistory,
    makeCombatEpisode,
    saveCombatHistoryAtomic,
    scoreCombatTactics,
    selectCombatPreset,
    serializeCombatHistory,
} from '../src/agent/evelyn_combat_experience.js';

const runtime = {minecraftVersion: '1.21.11', pluginVersion: '1.7.16'};

function context(overrides = {}) {
    return {
        mobSet: ['zombie'],
        countBucket: 'single',
        distanceBucket: 'near',
        terrain: 'open',
        healthBucket: 'healthy',
        gear: ['melee'],
        ...overrides,
    };
}

function episode(overrides = {}) {
    return makeCombatEpisode({
        ...context(),
        tactic: 'melee',
        outcome: 'success',
        verified: true,
        damage: 1,
        durationMs: 1000,
        ...runtime,
        ...overrides,
    });
}

test('episodes retain only bounded typed combat statistics', () => {
    const first = episode({mobSet: ['zombie', 'zombie'], gear: ['melee', 'not-a-gear']});
    const history = appendCombatEpisode([
        {...first, tactic: 'a transcript disguised as a tactic'},
        first,
    ], episode({outcome: 'failure', verified: false}), 2);

    assert.deepEqual(first.mobSet, ['zombie']);
    assert.deepEqual(first.gear, ['melee']);
    assert.equal(Object.isFrozen(first), true);
    assert.equal(history.length, 2);
    assert.deepEqual(history.map((entry) => entry.outcome), ['success', 'failure']);
});

test('safe defaults disengage from weak, crowded, unknown, and boss situations', () => {
    assert.equal(defaultCombatPreset(context()), 'melee');
    assert.equal(defaultCombatPreset(context({healthBucket: 'wounded'})), 'disengage');
    assert.equal(defaultCombatPreset(context({countBucket: 'crowd'})), 'disengage');
    assert.equal(defaultCombatPreset(context({mobSet: ['new_untrusted_mob']})), 'disengage');
    assert.equal(defaultCombatPreset(context({mobSet: ['warden'], gear: ['armor', 'melee', 'shield']})), 'disengage');
    assert.equal(defaultCombatPreset(context({mobSet: ['creeper'], gear: ['ranged']})), 'bow');
    assert.equal(defaultCombatPreset(context({mobSet: ['skeleton'], gear: ['melee', 'shield']})), 'shield_close');
    assert.equal(defaultCombatPreset(context({mobSet: ['creeper'], gear: ['ranged'], terrain: 'unknown'})), 'disengage');
    assert.equal(defaultCombatPreset(context({mobSet: ['skeleton'], gear: ['melee', 'shield'], terrain: 'unknown'})), 'disengage');
    assert.equal(defaultCombatPreset(context({gear: ['melee', 'ranged'], terrain: 'unknown'})), 'melee');
});

test('a tactic needs two verified successes from the exact runtime version', () => {
    const current = context({gear: ['melee', 'ranged']});
    const one = episode({...current, tactic: 'bow'});
    const two = episode({...current, tactic: 'bow', damage: 0});

    assert.equal(selectCombatPreset(current, [one], runtime), 'melee');
    assert.equal(selectCombatPreset(current, [
        one,
        episode({...current, tactic: 'bow', verified: false}),
    ], runtime), 'melee');
    assert.equal(selectCombatPreset(current, [one, two], runtime), 'bow');
    assert.equal(selectCombatPreset(current, [
        one,
        episode({...current, tactic: 'bow', minecraftVersion: '1.20.4'}),
    ], runtime), 'melee');

    const ranking = scoreCombatTactics(current, [one, two], runtime);
    assert.equal(ranking[0].verifiedSuccesses, 2);
    assert.equal(ranking[0].promoted, true);
});

test('repeated failures quarantine a promoted tactic and selection stays deterministic', () => {
    const current = context({gear: ['melee', 'ranged']});
    const records = [
        episode({...current, tactic: 'melee'}),
        episode({...current, tactic: 'melee', damage: 0}),
        episode({...current, tactic: 'melee', outcome: 'failure', verified: false, damage: 4}),
        episode({...current, tactic: 'melee', outcome: 'death', verified: false, damage: 20}),
    ];
    const ranking = scoreCombatTactics(current, records, runtime)[0];

    assert.equal(ranking.quarantined, true);
    assert.equal(ranking.promoted, false);
    assert.equal(selectCombatPreset(current, records, runtime), 'disengage');
    assert.deepEqual(
        Array.from({length: 20}, () => selectCombatPreset(current, records, runtime)),
        Array(20).fill('disengage'),
    );
});

test('quarantine requires two new verified successes before re-promotion', () => {
    const current = context({gear: ['melee', 'ranged']});
    const quarantined = [
        episode({...current, tactic: 'melee'}),
        episode({...current, tactic: 'melee', damage: 0}),
        episode({...current, tactic: 'melee', outcome: 'failure', verified: false}),
        episode({...current, tactic: 'melee', outcome: 'death', verified: false, damage: 20}),
    ];
    const once = [...quarantined, episode({...current, tactic: 'melee', damage: 0})];
    const twice = [...once, episode({...current, tactic: 'melee', damage: 0})];

    assert.equal(scoreCombatTactics(current, once, runtime)[0].recoverySuccesses, 1);
    assert.equal(scoreCombatTactics(current, once, runtime)[0].quarantined, true);
    assert.equal(selectCombatPreset(current, once, runtime), 'disengage');
    assert.equal(scoreCombatTactics(current, twice, runtime)[0].quarantined, false);
    assert.equal(scoreCombatTactics(current, twice, runtime)[0].promoted, true);
    assert.equal(selectCombatPreset(current, twice, runtime), 'melee');
});

test('interrupted episodes persist but never affect promotion, failure, or quarantine evidence', () => {
    const current = context({gear: ['melee', 'ranged']});
    const interrupted = episode({
        ...current,
        tactic: 'bow',
        outcome: 'interrupted',
        verified: true,
        damage: 12,
    });
    const restored = deserializeCombatHistory(serializeCombatHistory([interrupted]));
    const records = [
        episode({...current, tactic: 'bow'}),
        interrupted,
        episode({...current, tactic: 'bow', damage: 0}),
    ];
    const ranking = scoreCombatTactics(current, records, runtime)[0];

    assert.equal(restored[0].outcome, 'interrupted');
    assert.equal(ranking.verifiedSuccesses, 2);
    assert.equal(ranking.failures, 0);
    assert.equal(ranking.consecutiveFailures, 0);
    assert.equal(ranking.quarantined, false);
    assert.equal(ranking.promoted, true);

    const interruptedBetweenFailures = scoreCombatTactics(current, [
        episode({...current, tactic: 'bow', outcome: 'failure', verified: false}),
        interrupted,
        episode({...current, tactic: 'bow', outcome: 'death', verified: false, damage: 20}),
    ], runtime)[0];
    assert.equal(interruptedBetweenFailures.failures, 2);
    assert.equal(interruptedBetweenFailures.consecutiveFailures, 2);
    assert.equal(interruptedBetweenFailures.quarantined, true);
});

test('experience cannot override hard safety fences', () => {
    const crowd = context({countBucket: 'crowd'});
    assert.equal(selectCombatPreset(crowd, [
        episode({...crowd}),
        episode({...crowd, damage: 0}),
    ], runtime), 'disengage');

    const unknownCreeper = context({
        mobSet: ['creeper'],
        gear: ['ranged'],
        terrain: 'unknown',
    });
    assert.equal(selectCombatPreset(unknownCreeper, [
        episode({...unknownCreeper, tactic: 'bow'}),
        episode({...unknownCreeper, tactic: 'bow', damage: 0}),
    ], runtime), 'disengage');
});

test('bounded JSON persistence rejects schema drift and restores typed episodes', async () => {
    const serialized = serializeCombatHistory(Array.from({length: 300}, (_, index) => (
        episode({damage: index})
    )));
    const restored = deserializeCombatHistory(serialized);

    assert.equal(restored.length, 256);
    assert.equal(restored[0].damage, 44);
    assert.deepEqual(deserializeCombatHistory('{broken'), []);
    assert.deepEqual(deserializeCombatHistory(JSON.stringify({schemaVersion: 2, episodes: [episode()]})), []);

    const directory = await mkdtemp(join(tmpdir(), 'evelyn-combat-'));
    const filePath = join(directory, 'history.json');
    try {
        await saveCombatHistoryAtomic(filePath, restored);
        assert.deepEqual(await loadCombatHistory(filePath), restored);
        assert.equal(JSON.parse(await readFile(filePath, 'utf8')).schemaVersion, 1);

        const writer = createCombatHistoryWriter(filePath);
        writer.enqueue([episode({damage: 8})]);
        writer.enqueue([episode({damage: 3})]);
        await writer.flush();
        assert.equal((await loadCombatHistory(filePath))[0].damage, 3);
    } finally {
        await rm(directory, {recursive: true, force: true});
    }
});
