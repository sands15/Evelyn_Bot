import { AsyncLocalStorage } from 'node:async_hooks';

export const MINDCRAFT_HISTORY_BUSY = 'mindcraft_history_busy';
export const MINDCRAFT_HISTORY_STALE = 'mindcraft_history_stale';

const HISTORY_SNAPSHOT = Symbol('evelyn.mindcraft.history-snapshot.v1');
const exposureContext = new AsyncLocalStorage();
const recoveryIssuances = new WeakMap();

function normalizedCommand(command) {
    return String(command || '').replace(/\s+/g, ' ').trim();
}

export function mindcraftHistoryBoundaryError(code) {
    const error = new Error(code);
    error.code = code;
    return error;
}

export function isMindcraftHistoryBoundaryError(error) {
    return error?.code === MINDCRAFT_HISTORY_BUSY || error?.code === MINDCRAFT_HISTORY_STALE;
}

export function attachMindcraftHistorySnapshot(turns, history) {
    if (!Array.isArray(turns)) throw new TypeError('mindcraft_history_snapshot_invalid');
    const snapshot = Object.freeze({
        generation: history.generation,
        history
    });
    Object.defineProperty(turns, HISTORY_SNAPSHOT, {
        configurable: false,
        enumerable: false,
        writable: false,
        value: snapshot
    });
    return turns;
}

export function mindcraftHistorySnapshotIsCurrent(turns) {
    const snapshot = Array.isArray(turns) ? turns[HISTORY_SNAPSHOT] : null;
    return !snapshot || snapshot.history.isGenerationCurrent(snapshot.generation);
}

export function inheritMindcraftHistorySnapshot(source, target) {
    if (!Array.isArray(target)) throw new TypeError('mindcraft_history_snapshot_invalid');
    const snapshot = Array.isArray(source) ? source[HISTORY_SNAPSHOT] : null;
    if (!snapshot) return target;
    Object.defineProperty(target, HISTORY_SNAPSHOT, {
        configurable: false,
        enumerable: false,
        writable: false,
        value: snapshot,
    });
    return target;
}

export function bindMindcraftRecoveryIssuance(turns, command, complete) {
    const normalized = normalizedCommand(command);
    if (!Array.isArray(turns) || !normalized || typeof complete !== 'function') {
        throw new TypeError('mindcraft_recovery_issuance_invalid');
    }
    if (recoveryIssuances.has(turns)) {
        throw new Error('mindcraft_recovery_issuance_pending');
    }
    const token = Symbol('mindcraft.recovery-issuance');
    recoveryIssuances.set(turns, {command: normalized, complete});
    return token;
}

export function claimMindcraftRecoveryIssuance(turns, command) {
    const issuance = Array.isArray(turns) ? recoveryIssuances.get(turns) : null;
    if (!issuance || issuance.command !== normalizedCommand(command)) return null;
    recoveryIssuances.delete(turns);
    let finished = false;
    const finish = (execution) => {
        if (finished) return false;
        finished = true;
        return issuance.complete(execution);
    };
    return Object.freeze({
        complete: (execution) => finish(execution),
        discard: () => finish(null),
    });
}

export function discardMindcraftRecoveryIssuance(turns) {
    const issuance = Array.isArray(turns) ? recoveryIssuances.get(turns) : null;
    if (!issuance) return false;
    recoveryIssuances.delete(turns);
    return issuance.complete(null);
}

export function assertMindcraftHistoryCurrent() {
    const snapshot = exposureContext.getStore();
    if (snapshot && !snapshot.history.isGenerationCurrent(snapshot.generation)) {
        throw mindcraftHistoryBoundaryError(MINDCRAFT_HISTORY_STALE);
    }
}

export async function withMindcraftHistoryExposure(turns, callback) {
    const snapshot = Array.isArray(turns) ? turns[HISTORY_SNAPSHOT] : null;
    if (!snapshot) return callback();
    const release = snapshot.history.beginExposure(snapshot.generation);
    try {
        return await exposureContext.run(snapshot, async () => {
            assertMindcraftHistoryCurrent();
            const result = await callback();
            assertMindcraftHistoryCurrent();
            return result;
        });
    } finally {
        release();
    }
}
