import {mkdir, open, readFile, rename, rm, stat} from 'node:fs/promises';
import {basename, dirname, join} from 'node:path';
import {randomUUID} from 'node:crypto';

export const MAX_COMBAT_EPISODES = 256;
export const COMBAT_HISTORY_SCHEMA_VERSION = 1;
const MAX_COMBAT_HISTORY_BYTES = 256 * 1024;

export const COMBAT_PRESETS = Object.freeze({
    disengage: Object.freeze({id: 'disengage', disposition: 'flee', mode: null}),
    melee: Object.freeze({id: 'melee', disposition: 'fight', mode: 'melee'}),
    bow: Object.freeze({id: 'bow', disposition: 'fight', mode: 'bow'}),
    shield_close: Object.freeze({id: 'shield_close', disposition: 'fight', mode: 'melee'}),
});

const HOSTILES = new Set([
    'blaze', 'bogged', 'breeze', 'cave_spider', 'creeper', 'drowned',
    'elder_guardian', 'ender_dragon', 'enderman', 'evoker', 'ghast',
    'guardian', 'hoglin', 'husk', 'parched', 'phantom', 'piglin_brute',
    'pillager', 'ravager', 'shulker', 'skeleton', 'spider', 'stray', 'vex',
    'vindicator', 'warden', 'witch', 'wither', 'wither_skeleton', 'zoglin',
    'zombie',
]);
const BOSSES = new Set(['elder_guardian', 'ender_dragon', 'ravager', 'warden', 'wither']);
const COMMON_MELEE = new Set(['drowned', 'husk', 'spider', 'zombie']);
const RANGED = new Set(['bogged', 'parched', 'pillager', 'skeleton', 'stray']);
const GEARS = new Set(['armor', 'food', 'melee', 'ranged', 'shield']);
const COUNT_BUCKETS = new Set(['single', 'pair', 'crowd']);
const DISTANCE_BUCKETS = new Set(['contact', 'near', 'far']);
const TERRAINS = new Set(['open', 'cover', 'enclosed', 'water', 'unknown']);
const HEALTH_BUCKETS = new Set(['critical', 'wounded', 'healthy']);
const OUTCOMES = new Set(['success', 'failure', 'death', 'interrupted']);
const VERSION_TOKEN = /^[0-9A-Za-z._+-]{1,32}$/;
const FAILURE_QUARANTINE = 2;

function choice(value, allowed, name) {
    if (!allowed.has(value)) throw new TypeError(`invalid_${name}`);
    return value;
}

function boundedNumber(value, min, max, name) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) {
        throw new TypeError(`invalid_${name}`);
    }
    return value;
}

function version(value, name) {
    if (typeof value !== 'string' || !VERSION_TOKEN.test(value)) {
        throw new TypeError(`invalid_${name}`);
    }
    return value;
}

function normalizedMobSet(value) {
    if (!Array.isArray(value) || value.length === 0 || value.length > 8) {
        throw new TypeError('invalid_mob_set');
    }
    return Object.freeze([...new Set(value.map((mob) => (
        typeof mob === 'string' && HOSTILES.has(mob.toLowerCase())
            ? mob.toLowerCase()
            : 'unknown'
    )))].sort());
}

function normalizedGear(value) {
    if (!Array.isArray(value) || value.length > GEARS.size) throw new TypeError('invalid_gear');
    return Object.freeze([...new Set(value.filter((item) => GEARS.has(item)))].sort());
}

export function normalizeCombatContext(input = {}) {
    return Object.freeze({
        mobSet: normalizedMobSet(input.mobSet),
        countBucket: choice(input.countBucket, COUNT_BUCKETS, 'count_bucket'),
        distanceBucket: choice(input.distanceBucket, DISTANCE_BUCKETS, 'distance_bucket'),
        terrain: choice(input.terrain, TERRAINS, 'terrain'),
        healthBucket: choice(input.healthBucket, HEALTH_BUCKETS, 'health_bucket'),
        gear: normalizedGear(input.gear),
    });
}

export function makeCombatEpisode(input = {}) {
    const context = normalizeCombatContext(input);
    return Object.freeze({
        ...context,
        tactic: choice(input.tactic, new Set(Object.keys(COMBAT_PRESETS)), 'tactic'),
        outcome: choice(input.outcome, OUTCOMES, 'outcome'),
        verified: input.verified === true,
        damage: boundedNumber(input.damage, 0, 1000, 'damage'),
        durationMs: boundedNumber(input.durationMs, 0, 600000, 'duration'),
        minecraftVersion: version(input.minecraftVersion, 'minecraft_version'),
        pluginVersion: version(input.pluginVersion, 'plugin_version'),
    });
}

export function appendCombatEpisode(history, input, limit = MAX_COMBAT_EPISODES) {
    const clean = [];
    for (const item of (Array.isArray(history) ? history : []).slice(-MAX_COMBAT_EPISODES)) {
        try { clean.push(makeCombatEpisode(item)); } catch { /* discard malformed persisted data */ }
    }
    clean.push(makeCombatEpisode(input));
    const size = Math.max(1, Math.min(MAX_COMBAT_EPISODES, Math.floor(Number(limit) || MAX_COMBAT_EPISODES)));
    return Object.freeze(clean.slice(-size));
}

export function serializeCombatHistory(history) {
    const clean = [];
    for (const item of (Array.isArray(history) ? history : []).slice(-MAX_COMBAT_EPISODES)) {
        try { clean.push(makeCombatEpisode(item)); } catch { /* discard malformed persisted data */ }
    }
    return JSON.stringify({schemaVersion: COMBAT_HISTORY_SCHEMA_VERSION, episodes: clean.slice(-MAX_COMBAT_EPISODES)});
}

export function deserializeCombatHistory(serialized) {
    if (typeof serialized !== 'string' || Buffer.byteLength(serialized, 'utf8') > MAX_COMBAT_HISTORY_BYTES) {
        return Object.freeze([]);
    }
    try {
        const parsed = JSON.parse(serialized);
        if (parsed?.schemaVersion !== COMBAT_HISTORY_SCHEMA_VERSION || !Array.isArray(parsed.episodes)) {
            return Object.freeze([]);
        }
        const clean = [];
        for (const item of parsed.episodes) {
            try { clean.push(makeCombatEpisode(item)); } catch { /* discard malformed persisted data */ }
        }
        return Object.freeze(clean.slice(-MAX_COMBAT_EPISODES));
    } catch {
        return Object.freeze([]);
    }
}

async function writeCombatHistoryAtomic(filePath, serialized) {
    if (typeof filePath !== 'string' || !filePath) throw new TypeError('invalid_history_path');
    const directory = dirname(filePath);
    const temporary = join(directory, `.${basename(filePath)}.${process.pid}.${randomUUID()}.tmp`);
    await mkdir(directory, {recursive: true});
    let handle;
    try {
        handle = await open(temporary, 'wx', 0o600);
        await handle.writeFile(serialized, 'utf8');
        await handle.sync();
        await handle.close();
        handle = undefined;
        await rename(temporary, filePath);
    } catch (error) {
        await handle?.close().catch(() => {});
        await rm(temporary, {force: true}).catch(() => {});
        throw error;
    }
}

export async function saveCombatHistoryAtomic(filePath, history) {
    const serialized = serializeCombatHistory(history);
    await writeCombatHistoryAtomic(filePath, serialized);
}

export async function loadCombatHistory(filePath) {
    try {
        const metadata = await stat(filePath);
        if (!metadata.isFile() || metadata.size > MAX_COMBAT_HISTORY_BYTES) return Object.freeze([]);
        return deserializeCombatHistory(await readFile(filePath, 'utf8'));
    } catch (error) {
        if (error?.code === 'ENOENT') return Object.freeze([]);
        throw error;
    }
}

export function createCombatHistoryWriter(filePath) {
    let queued = Promise.resolve();
    return Object.freeze({
        enqueue(history) {
            const serialized = serializeCombatHistory(history);
            queued = queued.catch(() => {}).then(() => writeCombatHistoryAtomic(filePath, serialized));
            return queued;
        },
        flush() {
            return queued;
        },
    });
}

function contextKey(input) {
    const context = normalizeCombatContext(input);
    return JSON.stringify([
        context.mobSet, context.countBucket, context.distanceBucket,
        context.terrain, context.healthBucket, context.gear,
    ]);
}

function safeTactics(input) {
    const context = normalizeCombatContext(input);
    const mobs = context.mobSet;
    if (
        context.healthBucket !== 'healthy' ||
        context.countBucket !== 'single' ||
        context.terrain === 'water' ||
        mobs.includes('unknown') ||
        mobs.some((mob) => BOSSES.has(mob))
    ) return ['disengage'];

    const hasMelee = context.gear.includes('melee');
    const hasRanged = context.gear.includes('ranged') &&
        context.distanceBucket !== 'contact' && context.terrain !== 'unknown';
    if (context.terrain === 'unknown' && mobs.some((mob) => RANGED.has(mob) || mob === 'creeper')) {
        return ['disengage'];
    }
    if (mobs.includes('creeper')) return hasRanged && context.terrain !== 'enclosed'
        ? ['disengage', 'bow']
        : ['disengage'];
    if (mobs.some((mob) => RANGED.has(mob))) {
        const tactics = ['disengage'];
        if (hasRanged) tactics.push('bow');
        if (hasMelee && context.gear.includes('shield')) tactics.push('shield_close');
        return tactics;
    }
    if (!mobs.every((mob) => COMMON_MELEE.has(mob))) return ['disengage'];
    const tactics = ['disengage'];
    if (hasMelee) tactics.push('melee');
    if (hasRanged) tactics.push('bow');
    return tactics;
}

export function defaultCombatPreset(input) {
    const safe = safeTactics(input);
    if (safe.includes('shield_close')) return 'shield_close';
    if (safe.includes('melee')) return 'melee';
    if (safe.includes('bow')) return 'bow';
    return 'disengage';
}

export function scoreCombatTactics(context, episodes, runtime = {}) {
    const key = contextKey(context);
    const minecraftVersion = version(runtime.minecraftVersion, 'minecraft_version');
    const pluginVersion = version(runtime.pluginVersion, 'plugin_version');
    const safe = new Set(safeTactics(context));
    const grouped = new Map();

    for (const input of Array.isArray(episodes) ? episodes : []) {
        let episode;
        try { episode = makeCombatEpisode(input); } catch { continue; }
        if (
            episode.minecraftVersion !== minecraftVersion ||
            episode.pluginVersion !== pluginVersion ||
            contextKey(episode) !== key ||
            !safe.has(episode.tactic)
        ) continue;
        const group = grouped.get(episode.tactic) || [];
        group.push(episode);
        grouped.set(episode.tactic, group);
    }

    const ranked = [];
    for (const [tactic, records] of grouped) {
        const evidence = records.filter((record) => (
            (record.outcome === 'success' && record.verified) ||
            record.outcome === 'failure' || record.outcome === 'death'
        ));
        const successes = evidence.filter((record) => record.outcome === 'success').length;
        const deaths = evidence.filter((record) => record.outcome === 'death').length;
        const failures = evidence.length - successes;
        let consecutiveFailures = 0;
        let recoverySuccesses = 0;
        let quarantined = false;
        for (const record of evidence) {
            if (record.outcome === 'success') {
                consecutiveFailures = 0;
                if (quarantined) {
                    recoverySuccesses += 1;
                    if (recoverySuccesses >= 2) {
                        quarantined = false;
                        recoverySuccesses = 0;
                    }
                }
            } else {
                consecutiveFailures += 1;
                recoverySuccesses = 0;
                if (consecutiveFailures >= FAILURE_QUARANTINE) quarantined = true;
            }
        }
        const count = evidence.length || 1;
        const averageDamage = evidence.reduce((sum, record) => sum + record.damage, 0) / count;
        const averageDurationMs = evidence.reduce((sum, record) => sum + record.durationMs, 0) / count;
        ranked.push({
            tactic,
            verifiedSuccesses: successes,
            failures,
            deaths,
            consecutiveFailures,
            recoverySuccesses,
            quarantined,
            promoted: successes >= 2 && successes > failures && !quarantined,
            score: Number((
                successes / count * 1000 - deaths / count * 500 -
                averageDamage * 10 - averageDurationMs / 10000
            ).toFixed(3)),
        });
    }
    return ranked.sort((left, right) => (
        right.score - left.score || (left.tactic < right.tactic ? -1 : left.tactic > right.tactic ? 1 : 0)
    ));
}

export function selectCombatPreset(context, episodes, runtime) {
    const ranked = scoreCombatTactics(context, episodes, runtime);
    const promoted = ranked.find((entry) => entry.promoted)?.tactic;
    if (promoted) return promoted;
    const fallback = defaultCombatPreset(context);
    return ranked.some((entry) => entry.tactic === fallback && entry.quarantined)
        ? 'disengage'
        : fallback;
}
