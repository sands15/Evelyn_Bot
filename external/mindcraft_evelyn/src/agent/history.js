import { existsSync } from 'node:fs';
import settings from './settings.js';
import {
    attachMindcraftHistorySnapshot,
    mindcraftHistoryBoundaryError,
    mindcraftHistorySnapshotIsCurrent,
    MINDCRAFT_HISTORY_BUSY,
    MINDCRAFT_HISTORY_STALE,
} from '../utils/evelyn_history_boundary.js';

const HISTORY_SCHEMA = 'mindcraft.history.ephemeral.v1';
const MAX_TURN_CHARS = 8_000;

function fixedHistoryError(code) {
    const error = new Error(code);
    error.code = code;
    return error;
}

export class History {
    constructor(agent) {
        this.agent = agent;
        this.name = agent.name;
        this.memory_fp = `./bots/${this.name}/memory.json`;
        this.history_dir = `./bots/${this.name}/histories`;
        this.turns = [];
        this.memory = '';
        this.max_messages = Math.max(1, Number(settings.max_messages) || 8);
        this.generation = 0;
        this.activeExposures = 0;
    }

    getHistory() {
        const turns = JSON.parse(JSON.stringify(this.turns));
        return attachMindcraftHistorySnapshot(turns, this);
    }

    isGenerationCurrent(generation) {
        return Number.isSafeInteger(generation) && generation === this.generation;
    }

    isSnapshotCurrent(turns) {
        return mindcraftHistorySnapshotIsCurrent(turns);
    }

    beginExposure(generation) {
        if (!this.isGenerationCurrent(generation)) {
            throw mindcraftHistoryBoundaryError(MINDCRAFT_HISTORY_STALE);
        }
        this.activeExposures += 1;
        let released = false;
        return () => {
            if (released) return;
            released = true;
            this.activeExposures = Math.max(0, this.activeExposures - 1);
        };
    }

    bumpGeneration() {
        if (this.generation >= Number.MAX_SAFE_INTEGER) {
            throw fixedHistoryError('mindcraft_history_generation_exhausted');
        }
        this.generation += 1;
    }

    add(name, content) {
        let role = 'assistant';
        let normalized = String(content || '');
        if (!normalized || normalized.length > MAX_TURN_CHARS) {
            throw fixedHistoryError('mindcraft_history_content_rejected');
        }
        if (name === 'system') {
            role = 'system';
        } else if (name !== this.name) {
            role = 'user';
            normalized = `${name}: ${normalized}`;
            if (normalized.length > MAX_TURN_CHARS) {
                throw fixedHistoryError('mindcraft_history_content_rejected');
            }
        }
        this.turns.push({role, content: normalized});
        this.turns = this.turns.slice(-this.max_messages);
        this.bumpGeneration();
    }

    save() {
        console.log(`[Evelyn Mindcraft] ephemeral history checkpoint generation=${this.generation} turns=${this.turns.length}`);
        return {
            schema: HISTORY_SCHEMA,
            memoryGeneration: this.generation,
            turnCount: this.turns.length,
            contentFree: true,
        };
    }

    load() {
        if (existsSync(this.memory_fp)) {
            throw fixedHistoryError('mindcraft_history_persistence_disabled');
        }
        console.log('[Evelyn Mindcraft] persistent history disabled');
        return null;
    }

    resetHistoryDerivedState() {
        const goalManager = this.agent.goal_manager;
        if (typeof goalManager?.resetHistoryDerivedState === 'function') {
            goalManager.resetHistoryDerivedState();
        }
        if (typeof this.agent.resetHistoryDerivedState === 'function') {
            this.agent.resetHistoryDerivedState();
        }
        const models = new Set([
            this.agent.prompter?.chat_model,
            this.agent.prompter?.code_model,
        ]);
        for (const model of models) {
            if (typeof model?.resetHistoryDerivedState === 'function') {
                model.resetHistoryDerivedState();
            }
        }
        const selfPrompter = this.agent.self_prompter;
        if (selfPrompter) {
            selfPrompter.interrupt = true;
            selfPrompter.state = 0;
            selfPrompter.prompt = '';
        }
    }

    clear() {
        if (this.activeExposures > 0) {
            throw mindcraftHistoryBoundaryError(MINDCRAFT_HISTORY_BUSY);
        }
        this.resetHistoryDerivedState();
        this.turns = [];
        this.memory = '';
        this.agent.last_sender = null;
        this.bumpGeneration();
        return {generation: this.generation, contentFree: true, persistent: false};
    }
}
