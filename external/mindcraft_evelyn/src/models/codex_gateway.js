import { readFileSync } from 'fs';

const DEFAULT_URL = 'http://codex_gateway:8787/codex/action';
const DEFAULT_MODEL = 'gpt-5.5';
const DEFAULT_TOKEN_FILE = '/gateway-token/codex_gateway.token';

function resolveToken() {
    const configured = String(process.env.VOYAGER_CODEX_GATEWAY_TOKEN || '').trim();
    if (configured) return configured;
    const tokenFile = process.env.VOYAGER_CODEX_GATEWAY_TOKEN_FILE || DEFAULT_TOKEN_FILE;
    return readFileSync(tokenFile, 'utf8').trim();
}

function codexEnabled() {
    return /^(?:1|true|yes|on)$/i.test(String(process.env.MINDCRAFT_CODEX_ENABLED || ''));
}

function formatTurns(turns) {
    return (Array.isArray(turns) ? turns : [])
        .map((turn) => `${String(turn?.role || 'user').toUpperCase()}: ${String(turn?.content || '')}`)
        .join('\n');
}

function localEmbedding(text) {
    const vector = new Array(128).fill(0);
    for (const token of String(text || '').toLowerCase().match(/[\p{L}\p{N}_]+/gu) || []) {
        let hash = 2166136261;
        for (const char of token) {
            hash ^= char.codePointAt(0);
            hash = Math.imul(hash, 16777619);
        }
        vector[(hash >>> 0) % vector.length] += 1;
    }
    const norm = Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0)) || 1;
    return vector.map((value) => value / norm);
}

export class CodexGateway {
    static prefix = 'codex-gateway';

    constructor(modelName) {
        this.modelName = modelName || process.env.MINDCRAFT_CODEX_MODEL || DEFAULT_MODEL;
        this.url = process.env.MINDCRAFT_CODEX_GATEWAY_URL || DEFAULT_URL;
        this.timeoutSec = Number(process.env.MINDCRAFT_CODEX_TIMEOUT_SEC || 240);
    }

    async sendPrompt(prompt, source = 'mindcraft-planner') {
        if (!codexEnabled()) {
            throw new Error('mindcraft_codex_disabled');
        }
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), this.timeoutSec * 1000);
        try {
            const response = await fetch(this.url, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${resolveToken()}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    prompt: String(prompt || '').trim(),
                    model: this.modelName,
                    timeout_sec: this.timeoutSec,
                    source
                }),
                signal: controller.signal
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok || payload.ok !== true || !payload.content) {
                throw new Error(payload.error || `Codex Gateway HTTP ${response.status}`);
            }
            return String(payload.content).trim();
        } finally {
            clearTimeout(timer);
        }
    }

    async sendRequest(turns, systemMessage) {
        const prompt = [
            String(systemMessage || '').trim(),
            'Conversation:',
            formatTurns(turns),
            'Return only the next Mindcraft response. Use documented !commands only; never output JavaScript or slash commands.'
        ].filter(Boolean).join('\n\n');
        try {
            return await this.sendPrompt(prompt, 'mindcraft-planner');
        } catch (error) {
            console.error('[Evelyn Mindcraft] Codex Gateway request failed:', error?.message || error);
            return '지금은 안전하게 판단할 수 없어 멈출게. !stop';
        }
    }

    async sendVisionRequest(turns, systemMessage) {
        return this.sendRequest(turns, systemMessage);
    }

    async embed(text) {
        return localEmbedding(text);
    }
}
