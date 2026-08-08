import fs from 'node:fs';
import { randomUUID } from 'node:crypto';
import path from 'node:path';
import minecraftData from 'minecraft-data';
import { getCommand } from '../agent/commands/index.js';
import { itemMatchesTarget } from '../agent/evelyn_world_state.js';
import {
    assertMindcraftHistoryCurrent,
    bindMindcraftRecoveryIssuance,
    isMindcraftHistoryBoundaryError,
    withMindcraftHistoryExposure,
} from '../utils/evelyn_history_boundary.js';

const DEFAULT_BROKER_URL = 'http://bot_api:8798/internal/mindcraft-llm';
const DEFAULT_BROKER_TOKEN_FILE = '/mindcraft-llm-broker/token';
const BROKER_REQUEST_SCHEMA = 'mindcraft.llm-request.v1';
const BROKER_RESULT_SCHEMA = 'mindcraft.llm-result.v1';
const BROKER_DELIVERY_LEASE_SCHEMA = 'mindcraft.llm-delivery-lease.v1';
const BROKER_DELIVERY_ACK_SCHEMA = 'mindcraft.llm-delivery-ack.v1';
const BROKER_TOKEN_HEADER = 'X-Evelyn-Mindcraft-LLM-Token';
const BROKER_MAX_FRAME_BYTES = 256 * 1024;
const BROKER_REQUEST_TIMEOUT_MS = 100 * 1000;
const BROKER_ACK_TIMEOUT_MS = 10 * 1000;
const DEFAULT_GOAL_STATE_PATH = '/app/runtime_artifacts/mindcraft/goal_manager_state.json';
const RECOVERY_PLAN_TTL_MS = 5 * 60 * 1000;
const ACTION_MODE_TTL_MS = 15 * 60 * 1000;
const ALWAYS_ALLOWED_COMMANDS = new Set(['!stop', '!stats', '!inventory', '!nearbyBlocks', '!craftable']);
const FORBIDDEN_RECOVERY_COMMANDS = new Set(['!goal', '!endGoal']);
const BLOCKED_ACTION_PATTERN = /!(?:newAction|setMode|attackPlayer|digDown|clearChat)\b/i;
const SLASH_COMMAND_PATTERN = /(^|\s)\/[a-z][a-z0-9_-]*/i;
const FAILURE_PATTERN = /(?:do not have (?:the )?resources|not enough|no [^\n]* nearby|collected 0|(?:^|\s)failed\b|could not|can't\b|cannot\b|requires a crafting table|was not found|path (?:failed|timed out)|unable to)/i;
const SEVERE_FAILURE_PATTERN = /(?:invalid (?:block|item|command)|not found on the minecraft wiki|\bundefined\b|unknown command|was given \d+ args|requires \d+ args|incorrectly formatted|param [^\n]* must be)/i;
const PROGRESS_PATTERN = /(?:successfully|collected [1-9]\d*|you now have|you have reached|moved away|crafted [1-9]\d*|found non-destructive path)/i;
const OUTCOME_EVIDENCE_PATTERN = /(?:action output|inventory:|nearby_blocks|craftable_items|found [^\n]* at|you have all items|required to craft|successfully|collected|failed|invalid|not enough|do not have|no [^\n]* nearby|undefined)/i;
const COMPLEX_REQUEST_PATTERN = /(?:multi[- ]step|long[- ]term|strategy|strategic|debug|novel|unfamiliar|complex|계획|전략|복잡|처음 보는|원인 분석)/i;

let registryNames = null;

function positiveNumber(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
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

function compactTurns(turns, maxTurns = 8, maxCharsPerTurn = 1800) {
    return (Array.isArray(turns) ? turns : [])
        .slice(-maxTurns)
        .map((turn) => ({
            role: ['system', 'assistant', 'user'].includes(String(turn?.role))
                ? String(turn.role)
                : 'user',
            content: String(turn?.content || '').slice(-maxCharsPerTurn)
        }));
}

function formatTurns(turns) {
    return compactTurns(turns, 12, 2400)
        .map((turn) => `${turn.role.toUpperCase()}: ${turn.content}`)
        .join('\n');
}

function cleanResponseContent(content) {
    if (typeof content !== 'string' || !content.trim()) {
        throw brokerError('mindcraft_llm_result_invalid');
    }
    return content
        .replace(/<think>[\s\S]*?<\/think>/gi, '')
        .replace(/^```(?:text)?\s*/i, '')
        .replace(/\s*```$/i, '')
        .trim();
}

function enforceCommandPolicy(content) {
    const normalized = String(content || '').replace(/`/g, '').trim();
    if (SLASH_COMMAND_PATTERN.test(normalized) || BLOCKED_ACTION_PATTERN.test(normalized)) {
        console.warn('[Evelyn Mindcraft] Planner output violated command policy; replacing with !stop');
        return '!stop';
    }
    return normalized;
}

function brokerError(code) {
    const error = new Error(code);
    error.code = code;
    return error;
}

function exactKeys(value, keys) {
    return value && typeof value === 'object' && !Array.isArray(value) &&
        Object.keys(value).sort().join('\n') === [...keys].sort().join('\n');
}

function notUsedReceiptRef() {
    return {
        schema: 'conversation.memory-receipt-ref.v1',
        state: 'not_used',
        memoryVersion: 0,
        suppliedNoteIds: [],
        suppliedNoteCount: 0,
        contentFree: true,
    };
}

function receiptIsNotUsed(value) {
    return exactKeys(value, [
        'schema', 'state', 'memoryVersion', 'suppliedNoteIds',
        'suppliedNoteCount', 'contentFree',
    ]) && value.schema === 'conversation.memory-receipt-ref.v1' &&
        value.state === 'not_used' && value.memoryVersion === 0 &&
        Array.isArray(value.suppliedNoteIds) && value.suppliedNoteIds.length === 0 &&
        value.suppliedNoteCount === 0 && value.contentFree === true;
}

function brokerUrl() {
    try {
        const parsed = new URL(process.env.MINDCRAFT_LLM_BROKER_URL || DEFAULT_BROKER_URL);
        if (
            !['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password ||
            parsed.pathname !== '/internal/mindcraft-llm' || parsed.search || parsed.hash
        ) {
            throw new Error();
        }
        return parsed.href;
    } catch {
        throw brokerError('mindcraft_llm_broker_url_invalid');
    }
}

function brokerToken() {
    const tokenPath = process.env.MINDCRAFT_LLM_BROKER_TOKEN_FILE || DEFAULT_BROKER_TOKEN_FILE;
    try {
        if (!path.isAbsolute(tokenPath)) throw new Error();
        const token = fs.readFileSync(tokenPath, 'utf8').trim();
        if (!/^[A-Za-z0-9_-]{43,128}$/.test(token)) throw new Error();
        return token;
    } catch {
        throw brokerError('mindcraft_llm_broker_token_invalid');
    }
}

function brokerMessages(messages) {
    if (!Array.isArray(messages) || !messages.length || messages.length > 24) {
        throw brokerError('mindcraft_llm_request_invalid');
    }
    return messages.map((message) => {
        const role = String(message?.role || '');
        const content = String(message?.content || '');
        if (!['assistant', 'system', 'user'].includes(role) || !content) {
            throw brokerError('mindcraft_llm_request_invalid');
        }
        return {role, content, memoryReceiptRef: notUsedReceiptRef()};
    });
}

function validateBrokerFrame(frame, requestId) {
    if (
        !exactKeys(frame, ['schema', 'requestId', 'content', 'memoryReceiptRef', 'deliveryLease']) ||
        frame.schema !== BROKER_RESULT_SCHEMA || frame.requestId !== requestId ||
        typeof frame.content !== 'string' || !frame.content.trim() ||
        !receiptIsNotUsed(frame.memoryReceiptRef)
    ) {
        throw brokerError('mindcraft_llm_result_invalid');
    }
    const lease = frame.deliveryLease;
    if (
        !exactKeys(lease, ['schema', 'leaseId', 'ttlMs', 'contentFree']) ||
        lease.schema !== BROKER_DELIVERY_LEASE_SCHEMA ||
        !/^[0-9a-f]{64}$/.test(String(lease.leaseId || '')) ||
        !Number.isSafeInteger(lease.ttlMs) || lease.ttlMs <= 0 || lease.contentFree !== true
    ) {
        throw brokerError('mindcraft_llm_result_invalid');
    }
    return frame;
}

async function readBrokerFrame(response, requestId) {
    const mediaType = String(response.headers?.get?.('content-type') || '')
        .split(';', 1)[0]
        .trim()
        .toLowerCase();
    if (mediaType !== 'application/x-ndjson') {
        await response.body?.cancel?.().catch(() => {});
        throw brokerError('mindcraft_llm_result_invalid');
    }
    const reader = response.body?.getReader?.();
    if (!reader) throw brokerError('mindcraft_llm_result_invalid');
    try {
        let bytes = new Uint8Array();
        while (true) {
            const {done, value} = await reader.read();
            if (done) throw brokerError('mindcraft_llm_result_invalid');
            const chunk = value instanceof Uint8Array ? value : new Uint8Array(value || []);
            const combined = new Uint8Array(bytes.length + chunk.length);
            combined.set(bytes);
            combined.set(chunk, bytes.length);
            if (combined.length > BROKER_MAX_FRAME_BYTES) {
                throw brokerError('mindcraft_llm_result_invalid');
            }
            const newline = combined.indexOf(10);
            if (newline < 0) {
                bytes = combined;
                continue;
            }
            let frame;
            try {
                frame = JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(combined.slice(0, newline)));
            } catch {
                throw brokerError('mindcraft_llm_result_invalid');
            }
            return {
                frame: validateBrokerFrame(frame, requestId),
                reader,
                trailing: combined.slice(newline + 1),
            };
        }
    } catch (error) {
        await reader.cancel().catch(() => {});
        throw error;
    }
}

async function drainBrokerStream(reader, trailing) {
    if (trailing.length) throw brokerError('mindcraft_llm_result_invalid');
    while (true) {
        const {done, value} = await reader.read();
        if (done) return;
        if (value?.length) throw brokerError('mindcraft_llm_result_invalid');
    }
}

async function postBrokerAck(url, token, requestId, leaseId, outcome) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), BROKER_ACK_TIMEOUT_MS);
    try {
        const response = await fetch(`${url}/ack`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                [BROKER_TOKEN_HEADER]: token,
            },
            body: JSON.stringify({
                schema: BROKER_DELIVERY_ACK_SCHEMA,
                requestId,
                leaseId,
                outcome,
                contentFree: true,
            }),
            redirect: 'error',
            signal: controller.signal,
        });
        const payload = await response.json().catch(() => ({}));
        if (
            !response.ok || !exactKeys(payload, ['ok', 'contentFree']) ||
            payload.ok !== true || payload.contentFree !== true
        ) {
            throw brokerError('mindcraft_llm_delivery_ack_invalid');
        }
    } finally {
        clearTimeout(timer);
    }
}

async function completeBrokerDelivery(url, token, requestId, reader, trailing, leaseId, outcome) {
    await Promise.all([
        postBrokerAck(url, token, requestId, leaseId, outcome),
        drainBrokerStream(reader, trailing),
    ]);
}

async function requestBroker(requestKind, messages, consume) {
    assertMindcraftHistoryCurrent();
    const url = brokerUrl();
    const token = brokerToken();
    const requestId = randomUUID();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), BROKER_REQUEST_TIMEOUT_MS);
    let reader = null;
    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                [BROKER_TOKEN_HEADER]: token,
            },
            body: JSON.stringify({
                schema: BROKER_REQUEST_SCHEMA,
                requestId,
                requestKind,
                messages: brokerMessages(messages),
                historyReceiptRef: notUsedReceiptRef(),
            }),
            redirect: 'error',
            signal: controller.signal,
        });
        if (!response.ok) {
            await response.body?.cancel?.().catch(() => {});
            throw brokerError('mindcraft_llm_broker_failed');
        }
        const opened = await readBrokerFrame(response, requestId);
        reader = opened.reader;
        let result;
        try {
            assertMindcraftHistoryCurrent();
            result = consume(opened.frame.content);
            assertMindcraftHistoryCurrent();
        } catch (error) {
            try {
                await completeBrokerDelivery(
                    url, token, requestId, reader, opened.trailing,
                    opened.frame.deliveryLease.leaseId, 'discarded',
                );
            } catch {
                await reader.cancel().catch(() => {});
            }
            throw error;
        }
        await completeBrokerDelivery(
            url, token, requestId, reader, opened.trailing,
            opened.frame.deliveryLease.leaseId, 'delivered',
        );
        assertMindcraftHistoryCurrent();
        return result;
    } catch (error) {
        await reader?.cancel().catch(() => {});
        if (isMindcraftHistoryBoundaryError(error) || String(error?.code || '').startsWith('mindcraft_')) {
            throw error;
        }
        throw brokerError('mindcraft_llm_broker_failed');
    } finally {
        clearTimeout(timer);
    }
}

function parseRoute(content) {
    const cleaned = String(content || '')
        .replace(/<think>[\s\S]*?<\/think>/gi, '')
        .replace(/```(?:json)?/gi, '')
        .trim();
    const match = cleaned.match(/\{[\s\S]*?\}/);
    if (!match) return 'local';
    try {
        return JSON.parse(match[0]).route === 'codex' ? 'codex' : 'local';
    } catch {
        return 'local';
    }
}

export function extractCommand(content) {
    const match = String(content || '').replace(/`/g, '').match(/![A-Za-z][A-Za-z0-9_]*(?:\([^\r\n]*?\))?/);
    return match ? match[0].trim() : null;
}

function commandName(command) {
    return String(command || '').match(/^![A-Za-z][A-Za-z0-9_]*/)?.[0] || null;
}

function normalizeCommand(command) {
    return String(command || '').replace(/\s+/g, ' ').trim();
}

function firstStringArgument(command) {
    const match = String(command || '').match(/^[^(]+\(\s*["']([^"']+)["']/);
    return match ? match[1] : null;
}

function readGoalPolicy() {
    const statePath = process.env.MINDCRAFT_GOAL_MANAGER_STATE_PATH || DEFAULT_GOAL_STATE_PATH;
    try {
        const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
        const current = state?.currentSubgoal;
        return {
            autonomyState: state?.autonomyState,
            executionSequence: Number(state?.executionSequence || 0),
            lastExecution: state?.lastExecution || null,
            currentSubgoal: current
                ? {
                    id: current.id,
                    kind: current.kind,
                    target: current.target,
                    success: current.success,
                    attempts: current.attempts,
                    actionBudget: current.actionBudget,
                    observationStreak: Number(current.observationStreak || 0),
                    allowedCommands: Array.isArray(current.allowedCommands) ? current.allowedCommands : [],
                    allowedTargets: Array.isArray(current.allowedTargets)
                        ? current.allowedTargets
                        : [current.target].filter(Boolean),
                    relocationRequired: Boolean(current.relocationRequired)
                }
                : null
        };
    } catch {
        return null;
    }
}

function recoveryTargetAllowed(command, policy) {
    const target = firstStringArgument(command);
    if (!target) return true;
    const allowedTargets = policy?.currentSubgoal?.allowedTargets || [];
    if (!allowedTargets.length) return false;
    return allowedTargets.some((allowed) => (
        itemMatchesTarget(target, allowed) ||
        itemMatchesTarget(allowed, target)
    ));
}

function parseCommandArguments(command) {
    const open = String(command || '').indexOf('(');
    if (open < 0) return [];
    if (!String(command).endsWith(')')) return null;
    const inner = String(command).slice(open + 1, -1).trim();
    if (!inner) return [];
    try {
        const parsed = JSON.parse(`[${inner}]`);
        return Array.isArray(parsed) ? parsed : null;
    } catch {
        return null;
    }
}

function validateCommandSignature(command) {
    const name = commandName(command);
    const definition = getCommand(name);
    if (!definition) return {ok: false, reason: `unknown_command:${name}`};

    const args = parseCommandArguments(command);
    if (!args) return {ok: false, reason: 'invalid_argument_syntax'};
    const params = Object.values(definition.params || {});
    if (args.length !== params.length) {
        return {
            ok: false,
            reason: `invalid_argument_count:${name}:${args.length}:${params.length}`
        };
    }

    for (let index = 0; index < params.length; index++) {
        const param = params[index];
        const value = args[index];
        if (param.type === 'int' && (!Number.isInteger(value))) {
            return {ok: false, reason: `invalid_argument_type:${name}:${index}:int`};
        }
        if (param.type === 'float' && (!Number.isFinite(value))) {
            return {ok: false, reason: `invalid_argument_type:${name}:${index}:float`};
        }
        if (param.type === 'boolean' && typeof value !== 'boolean') {
            return {ok: false, reason: `invalid_argument_type:${name}:${index}:boolean`};
        }
        if (
            ['string', 'BlockName', 'ItemName', 'BlockOrItemName'].includes(param.type) &&
            typeof value !== 'string'
        ) {
            return {ok: false, reason: `invalid_argument_type:${name}:${index}:string`};
        }
        if (typeof value === 'number' && Array.isArray(param.domain)) {
            const [minimum, maximum, endpoints = '[)'] = param.domain;
            const aboveMinimum = endpoints[0] === '[' ? value >= minimum : value > minimum;
            const belowMaximum = endpoints[1] === ']' ? value <= maximum : value < maximum;
            if (!aboveMinimum || !belowMaximum) {
                return {ok: false, reason: `argument_out_of_range:${name}:${index}`};
            }
        }
    }
    return {ok: true, args, params};
}

function minecraftRegistryNames() {
    if (registryNames) return registryNames;
    try {
        const data = minecraftData(process.env.MINECRAFT_VERSION || '1.21.11');
        registryNames = new Set([
            ...(data?.blocksArray || []).map((entry) => entry.name),
            ...(data?.itemsArray || []).map((entry) => entry.name)
        ]);
    } catch (error) {
        console.warn('[Evelyn Mindcraft] Minecraft registry validation unavailable:', error?.message || error);
        registryNames = new Set();
    }
    return registryNames;
}

function allowedCommands(systemMessage) {
    const found = String(systemMessage || '').match(/![A-Za-z][A-Za-z0-9_]*/g) || [];
    return new Set([...found, ...ALWAYS_ALLOWED_COMMANDS]);
}

export function validateActionResponse(content, systemMessage) {
    const policySafe = enforceCommandPolicy(content);
    if (policySafe === '!stop' && extractCommand(content) !== '!stop') {
        return {ok: false, reason: 'command_policy_violation', command: '!stop'};
    }

    const command = extractCommand(policySafe);
    if (!command) {
        return {ok: false, reason: 'missing_command', command: null};
    }
    if (BLOCKED_ACTION_PATTERN.test(command) || SLASH_COMMAND_PATTERN.test(command)) {
        return {ok: false, reason: 'blocked_command', command: '!stop'};
    }

    const name = commandName(command);
    const allowed = allowedCommands(systemMessage);
    if (allowed.size > ALWAYS_ALLOWED_COMMANDS.size && !allowed.has(name)) {
        return {ok: false, reason: `unknown_command:${name}`, command: null};
    }
    const signature = validateCommandSignature(command);
    if (!signature.ok) {
        return {ok: false, reason: signature.reason, command: null};
    }

    const names = minecraftRegistryNames();
    if (names.size > 0) {
        for (let index = 0; index < signature.params.length; index++) {
            const param = signature.params[index];
            const value = signature.args[index];
            if (
                ['BlockName', 'ItemName', 'BlockOrItemName'].includes(param.type) &&
                typeof value === 'string' &&
                !names.has(value)
            ) {
                return {ok: false, reason: `invalid_registry_name:${value}`, command: null};
            }
        }
    }

    return {ok: true, reason: 'valid', command: normalizeCommand(command)};
}

export function parseSubgoalCandidates(content) {
    const cleaned = String(content || '')
        .replace(/<think>[\s\S]*?<\/think>/gi, '')
        .replace(/```(?:json)?/gi, '')
        .trim();
    const firstBrace = cleaned.indexOf('{');
    const lastBrace = cleaned.lastIndexOf('}');
    if (firstBrace < 0 || lastBrace <= firstBrace) return [];
    try {
        const payload = JSON.parse(cleaned.slice(firstBrace, lastBrace + 1));
        return Array.isArray(payload?.candidates)
            ? payload.candidates.filter((candidate) => candidate && typeof candidate === 'object').slice(0, 3)
            : [];
    } catch {
        return [];
    }
}

export function classifyRequest(turns, systemMessage) {
    const prompt = String(systemMessage || '').toLowerCase();
    const recentTurns = compactTurns(turns, 40, 1200);

    if (prompt.includes('update your memory by summarizing') || prompt.includes('unwrapped memory text')) {
        return 'memory';
    }
    if (prompt.includes("outputting only 'respond' or 'ignore'")) {
        return 'classifier';
    }
    if (prompt.includes('determine what goal to target next')) {
        return 'goal';
    }
    if (prompt.includes('analyze and summarize the view')) {
        return 'vision';
    }
    for (let index = recentTurns.length - 1; index >= 0; index--) {
        const turn = recentTurns[index];
        const content = turn.content.toLowerCase();
        if (
            content.includes('next response must contain a command') ||
            content.includes('your next response must contain a command') ||
            content.includes('self-prompting with the goal') ||
            content.includes('set and pursue this survival goal')
        ) {
            return 'action';
        }
        if (turn.role === 'user') break;
    }
    return 'chat';
}

function attemptsFromTurns(turns) {
    const attempts = [];
    let active = null;
    for (const turn of compactTurns(turns, 20, 2400)) {
        const role = turn.role;
        if (role === 'assistant') {
            if (active) attempts.push(active);
            const command = extractCommand(turn.content);
            active = {
                command: command ? normalizeCommand(command) : null,
                name: commandName(command),
                result: '',
                rawAssistant: turn.content
            };
        } else if (active) {
            active.result += `${active.result ? '\n' : ''}${turn.content}`;
        }
    }
    if (active) attempts.push(active);
    return attempts.map((attempt) => {
        const severe = SEVERE_FAILURE_PATTERN.test(attempt.result);
        const progress = PROGRESS_PATTERN.test(attempt.result);
        const failed = severe || (FAILURE_PATTERN.test(attempt.result) && !progress);
        return {...attempt, severe, progress, failed};
    });
}

export function analyzeRecentTurns(turns) {
    const attempts = attemptsFromTurns(turns);
    const recent = attempts.slice(-6);
    const latest = recent.at(-1) || null;
    const reasons = [];

    if (latest && !latest.command) {
        reasons.push('previous_action_response_missing_command');
    }
    if (latest?.severe) {
        reasons.push('severe_action_failure');
    }

    let consecutiveFailures = 0;
    for (let index = recent.length - 1; index >= 0; index--) {
        if (!recent[index].failed) break;
        consecutiveFailures += 1;
    }
    if (consecutiveFailures >= 2) {
        reasons.push(`consecutive_failures:${consecutiveFailures}`);
    }

    const failedRecent = recent.filter((attempt) => attempt.failed && attempt.command);
    const exactCounts = new Map();
    const nameCounts = new Map();
    for (const attempt of failedRecent) {
        exactCounts.set(attempt.command, (exactCounts.get(attempt.command) || 0) + 1);
        nameCounts.set(attempt.name, (nameCounts.get(attempt.name) || 0) + 1);
    }
    if ([...exactCounts.values()].some((count) => count >= 2)) {
        reasons.push('same_failed_command_repeated');
    }
    if ([...nameCounts.values()].some((count) => count >= 3)) {
        reasons.push('same_failed_action_family_repeated');
    }

    const latestCommand = latest?.command ? normalizeCommand(latest.command) : null;
    let sameCommandRun = 0;
    if (latestCommand) {
        for (let index = recent.length - 1; index >= 0; index--) {
            const command = recent[index].command ? normalizeCommand(recent[index].command) : null;
            if (command !== latestCommand || !recent[index].result.trim()) break;
            sameCommandRun += 1;
        }
    }
    if (sameCommandRun >= 4) {
        reasons.push(`same_command_loop:${sameCommandRun}`);
    }

    return {
        shouldEscalate: reasons.length > 0,
        reason: reasons.join(',') || 'none',
        attempts,
        latest
    };
}

function shouldConsultRouter(kind, turns) {
    if (!['action', 'chat'].includes(kind)) return false;
    const recent = compactTurns(turns, 4, 1400);
    const humanOrSystem = recent
        .filter((turn) => turn.role !== 'assistant')
        .map((turn) => turn.content)
        .join('\n');
    if (/self-prompting with the goal/i.test(humanOrSystem)) return false;
    return COMPLEX_REQUEST_PATTERN.test(humanOrSystem);
}

export function parseRecoveryPlan(content, systemMessage, policy = null) {
    const cleaned = String(content || '').replace(/```(?:json)?/gi, '').trim();
    let parsed = null;
    const firstBrace = cleaned.indexOf('{');
    const lastBrace = cleaned.lastIndexOf('}');
    if (firstBrace >= 0 && lastBrace > firstBrace) {
        try {
            parsed = JSON.parse(cleaned.slice(firstBrace, lastBrace + 1));
        } catch {
            parsed = null;
        }
    }

    const rawSteps = Array.isArray(parsed?.steps)
        ? parsed.steps.map((step) => typeof step === 'string' ? step : step?.command)
        : (cleaned.match(/![A-Za-z][A-Za-z0-9_]*(?:\([^\r\n]*?\))?/g) || []);
    const steps = [];
    for (const raw of rawSteps) {
        const validation = validateActionResponse(raw, systemMessage);
        const name = commandName(validation.command);
        const allowedCommands = policy?.currentSubgoal?.allowedCommands || [];
        const policyAllowed = (
            !policy ||
            (
                policy.currentSubgoal &&
                allowedCommands.includes(name) &&
                recoveryTargetAllowed(validation.command, policy)
            )
        );
        if (
            validation.ok &&
            !FORBIDDEN_RECOVERY_COMMANDS.has(name) &&
            policyAllowed &&
            !steps.includes(validation.command)
        ) {
            steps.push(validation.command);
        }
        if (steps.length >= 4) break;
    }
    if (steps.length < 1) return null;

    return {
        reason: String(parsed?.reason || 'Broker recovery plan').slice(0, 300),
        steps,
        successSignals: Array.isArray(parsed?.success_signals) ? parsed.success_signals.slice(0, 4) : [],
        abortSignals: Array.isArray(parsed?.abort_signals) ? parsed.abort_signals.slice(0, 4) : []
    };
}

export class EvelynPlanner {
    static prefix = 'evelyn-planner';

    constructor() {
        this.codexEnabled = /^(?:1|true|yes|on)$/i.test(
            String(process.env.MINDCRAFT_CODEX_ENABLED || '')
        );
        this.codexCooldownMs = positiveNumber(process.env.MINDCRAFT_CODEX_COOLDOWN_SEC, 30) * 1000;
        this.lastCodexAt = 0;
        this.recoveryPlan = null;
        this.recoveryPlanInFlight = false;
        this.probeIndex = 0;
        this.actionModeUntil = 0;
    }

    persistPlannerState() {
        // History-derived recovery state stays process-local until it can be
        // bound to the same deletion/edit boundary as the model request.
    }

    clearRecoveryPlan() {
        this.recoveryPlan = null;
        this.persistPlannerState();
    }

    resetHistoryDerivedState() {
        this.recoveryPlan = null;
        this.actionModeUntil = 0;
        this.probeIndex = 0;
        this.persistPlannerState();
    }

    async proposeSubgoals(context) {
        const prompt = [
            'You choose short, verifiable Minecraft survival subgoals for Evelyn.',
            'The ultimate goal is fixed, but the next subgoal must be derived from the current world state.',
            'Propose 2 or 3 candidates. Prefer the smallest prerequisite that unlocks later progress.',
            'Reject decorative work, redundant food crafting, and goals already satisfied.',
            'Every candidate must be achievable with normal non-operator player actions and verifiable from inventory, stats, or dimension.',
            'Allowed kinds: obtain, craft, smelt, equip, enter_dimension, maintain.',
            'Allowed success predicates:',
            '{"kind":"inventory","target":"exact_item_or_#tag","count":1}',
            '{"kind":"stat","field":"health_or_hunger","gte":16}',
            '{"kind":"dimension","equals":"nether_or_end"}',
            '{"kind":"entity_defeated","target":"ender_dragon","count":1}',
            'Useful inventory tags: #logs, #planks, #food, #pickaxes, #weapons, #armor, #fuel, #iron, #diamonds, #ender_pearls, #blaze_rods, #eyes_of_ender.',
            'Return JSON only with this schema:',
            '{"candidates":[{"id":"short_id","kind":"obtain","target":"#logs","quantity":3,"reason":"brief","success":{"kind":"inventory","target":"#logs","count":3},"action_budget":8,"unlock_score":5,"risk":"low"}]}'
        ].join('\n');
        const candidates = await requestBroker(
            'subgoal',
            [
                {role: 'system', content: prompt},
                {role: 'user', content: JSON.stringify(context)}
            ],
            (content) => parseSubgoalCandidates(content),
        );
        console.log(`[Evelyn Mindcraft] local subgoal candidates=${candidates.length}`);
        return candidates;
    }

    async proposeStrategicSubgoals(context, reason = 'local_subgoal_exhausted') {
        if (!this.codexEnabled) return this.proposeSubgoals(context);
        if (Date.now() - this.lastCodexAt < this.codexCooldownMs) return [];
        const prompt = [
            'You are the strategic Minecraft survival goal planner for Evelyn.',
            `Escalation reason: ${reason}.`,
            'Choose 2 or 3 small, verifiable next subgoals toward the fixed ultimate goal.',
            'Use normal non-operator survival only. Do not output commands or a fixed full quest.',
            'Every subgoal must be verifiable using one of these predicates:',
            '{"kind":"inventory","target":"exact_item_or_#tag","count":1}',
            '{"kind":"stat","field":"health_or_hunger","gte":16}',
            '{"kind":"dimension","equals":"nether_or_end"}',
            '{"kind":"entity_defeated","target":"ender_dragon","count":1}',
            'Allowed kinds: obtain, craft, smelt, equip, enter_dimension, defeat, maintain.',
            'Return JSON only:',
            '{"candidates":[{"id":"short_id","kind":"obtain","target":"item_or_tag","quantity":1,"reason":"brief","success":{"kind":"inventory","target":"item_or_tag","count":1},"action_budget":8,"unlock_score":5,"risk":"low"}]}',
            'CURRENT CONTEXT:',
            JSON.stringify(context)
        ].join('\n');
        this.lastCodexAt = Date.now();
        this.persistPlannerState();
        const candidates = await requestBroker(
            'subgoal',
            [{role: 'system', content: prompt}],
            (content) => parseSubgoalCandidates(cleanResponseContent(content)),
        );
        console.log(`[Evelyn Mindcraft] strategic subgoal candidates=${candidates.length}`);
        return candidates;
    }

    async chooseRoute(turns) {
        if (!this.codexEnabled) return 'local';
        const recent = compactTurns(turns, 5, 900);
        try {
            return await requestBroker(
                'router',
                [
                    {
                        role: 'system',
                        content: [
                            'Route one ambiguous Minecraft request.',
                            'Use local for a clear short action or ordinary conversation.',
                            'Use codex for novel multi-step strategy, difficult recovery, or debugging.',
                            'Return JSON only: {"route":"local"} or {"route":"codex"}.'
                        ].join(' ')
                    },
                    {role: 'user', content: JSON.stringify(recent)}
                ],
                (content) => parseRoute(cleanResponseContent(content)),
            );
        } catch (error) {
            if (isMindcraftHistoryBoundaryError(error)) throw error;
            console.warn('[Evelyn Mindcraft] Router unavailable; using local planner:', error?.message || error);
            return 'local';
        }
    }

    async requestLocal(turns, systemMessage, stopSeq, kind = 'chat', extraInstruction = '') {
        const system = [String(systemMessage || ''), String(extraInstruction || '')]
            .filter(Boolean)
            .join('\n\n');
        const brokerKind = ['action', 'classifier', 'memory'].includes(kind) ? kind : 'chat';
        return requestBroker(
            brokerKind,
            [
                {role: 'system', content: system},
                ...compactTurns(turns)
            ],
            (raw) => {
                const content = enforceCommandPolicy(cleanResponseContent(raw));
                if (kind === 'memory' && content === '!stop') {
                    throw brokerError('mindcraft_memory_summary_unavailable');
                }
                return content;
            },
        );
    }

    updateRecoveryPlan() {
        const plan = this.recoveryPlan;
        if (!plan) return null;
        if (Date.now() - plan.createdAt > RECOVERY_PLAN_TTL_MS) {
            this.clearRecoveryPlan();
            return 'recovery_plan_expired';
        }
        const execution = plan.pendingExecution;
        if (!plan.lastIssued || !execution) return null;
        const executionCommandCode = String(
            execution.commandCode || commandName(execution.command) || ''
        );
        plan.pendingExecution = null;
        plan.pendingIssuance = null;
        if (!executionCommandCode || executionCommandCode !== commandName(plan.lastIssued)) {
            this.clearRecoveryPlan();
            return 'recovery_step_failed:invalid_execution';
        }
        if (execution.failed || execution.relevant === false) {
            this.clearRecoveryPlan();
            return `recovery_step_failed:${executionCommandCode}`;
        }

        plan.stepIndex += 1;
        plan.lastIssued = null;
        if (plan.stepIndex >= plan.steps.length) {
            this.clearRecoveryPlan();
            console.log('[Evelyn Mindcraft] recovery plan completed');
            return 'recovery_plan_completed';
        }
        this.persistPlannerState();
        return `recovery_step_completed:${plan.stepIndex}`;
    }

    async createRecoveryPlan(turns, systemMessage, reason) {
        const policy = readGoalPolicy();
        if (!policy?.currentSubgoal) {
            throw new Error('Goal manager has no active short-term goal for recovery');
        }
        const prompt = [
            'You are the recovery planner for Evelyn, a non-operator Minecraft survival bot.',
            `The local planner is stuck. Escalation reason: ${reason}.`,
            'Create a short recovery plan with 1 to 4 executable steps.',
            'Every step must be exactly one documented !command using normal-player actions.',
            'Never use slash commands, cheats, JavaScript, !goal, !endGoal, !newAction, !setMode, !attackPlayer, or !digDown.',
            'Every command and target must stay inside the active short-term goal policy.',
            'Do not repeat a failed command unless the state has materially changed.',
            'The goal manager alone decides whether a short-term or ultimate goal is complete.',
            'Use the structured active-goal policy and recent world observations below.',
            'Return JSON only with this schema:',
            '{"reason":"brief diagnosis","steps":["!command(...)"],"success_signals":["..."],"abort_signals":["..."]}',
            'ACTIVE GOAL POLICY:',
            JSON.stringify(policy),
            'AVAILABLE CONTEXT AND COMMANDS:',
            String(systemMessage || ''),
            'RECENT TURNS:',
            formatTurns(turns)
        ].join('\n\n');

        this.lastCodexAt = Date.now();
        this.persistPlannerState();
        const parsed = await requestBroker(
            'recovery',
            [{role: 'system', content: prompt}],
            (content) => {
                const plan = parseRecoveryPlan(
                    cleanResponseContent(content), systemMessage, policy,
                );
                if (!plan) throw brokerError('mindcraft_recovery_plan_invalid');
                return plan;
            },
        );
        if (!parsed) {
            throw new Error('Recovery response did not contain a valid documented command plan');
        }
        this.recoveryPlan = {
            ...parsed,
            goalId: policy.currentSubgoal.id,
            createdAt: Date.now(),
            stepIndex: 0,
            lastIssued: null,
            lastIssuedAt: null,
            pendingIssuance: null,
            pendingExecution: null,
        };
        this.persistPlannerState();
        console.log(
            `[Evelyn Mindcraft] planner route=broker reason=${reason} recovery_steps=${parsed.steps.length}`
        );
        return this.recoveryPlan;
    }

    safeProbe(systemMessage, reason) {
        const policy = readGoalPolicy();
        if (policy?.currentSubgoal?.relocationRequired) {
            const relocation = validateActionResponse('!moveAway(16)', systemMessage);
            if (relocation.ok) return relocation.command;
        }
        const candidates = /resource|craft|inventory/i.test(reason)
            ? ['!inventory', '!craftable', '!nearbyBlocks', '!stats']
            : ['!nearbyBlocks', '!inventory', '!stats', '!craftable'];
        for (let offset = 0; offset < candidates.length; offset++) {
            const command = candidates[(this.probeIndex + offset) % candidates.length];
            const validation = validateActionResponse(command, systemMessage);
            if (validation.ok) {
                this.probeIndex = (this.probeIndex + offset + 1) % candidates.length;
                return validation.command;
            }
        }
        return '!stop';
    }

    async runRecoveryStep(turns, systemMessage, stopSeq) {
        const plan = this.recoveryPlan;
        if (!plan) throw new Error('Recovery plan is unavailable');
        const currentStep = plan.steps[plan.stepIndex];
        const policy = readGoalPolicy();
        if (!policy?.currentSubgoal) {
            throw new Error('Recovery step has no active goal policy');
        }
        if (plan.goalId !== policy.currentSubgoal.id) {
            throw new Error(
                `Recovery plan goal changed: ${plan.goalId || 'unbound'} -> ${policy.currentSubgoal.id}`
            );
        }
        const validation = validateActionResponse(currentStep, systemMessage);
        const name = commandName(validation.command);
        if (
            !validation.ok ||
            FORBIDDEN_RECOVERY_COMMANDS.has(name) ||
            !policy.currentSubgoal.allowedCommands.includes(name) ||
            !recoveryTargetAllowed(validation.command, policy)
        ) {
            throw new Error(`Recovery step validation failed: ${validation.reason}`);
        }
        plan.lastIssued = validation.command;
        plan.lastIssuedAt = Date.now();
        plan.pendingExecution = null;
        let token;
        token = bindMindcraftRecoveryIssuance(turns, validation.command, (execution) => {
            if (this.recoveryPlan !== plan || plan.pendingIssuance !== token) return false;
            if (!execution) {
                this.clearRecoveryPlan();
                return true;
            }
            plan.pendingExecution = execution;
            return true;
        });
        plan.pendingIssuance = token;
        this.persistPlannerState();
        console.log(
            `[Evelyn Mindcraft] planner route=local gate=recovery step=${plan.stepIndex + 1}/${plan.steps.length}`
        );
        return validation.command;
    }

    async recover(turns, systemMessage, stopSeq, reason) {
        const cooldownRemaining = this.codexCooldownMs - (Date.now() - this.lastCodexAt);
        if (!this.recoveryPlan && cooldownRemaining > 0) {
            const probe = this.safeProbe(systemMessage, reason);
            console.log(
                `[Evelyn Mindcraft] planner route=local gate=recovery_cooldown remaining_ms=${Math.ceil(cooldownRemaining)}`
            );
            return probe;
        }
        try {
            if (!this.recoveryPlan) {
                if (this.recoveryPlanInFlight) return '';
                this.recoveryPlanInFlight = true;
                try {
                    await this.createRecoveryPlan(turns, systemMessage, reason);
                } finally {
                    this.recoveryPlanInFlight = false;
                }
            }
            return await this.runRecoveryStep(turns, systemMessage, stopSeq);
        } catch (error) {
            if (isMindcraftHistoryBoundaryError(error)) throw error;
            console.error('[Evelyn Mindcraft] Recovery planning failed:', error?.message || error);
            this.clearRecoveryPlan();
            return this.safeProbe(systemMessage, reason);
        }
    }

    async sendActionRequest(turns, systemMessage, stopSeq) {
        const analysis = analyzeRecentTurns(turns);
        const recoveryUpdate = this.updateRecoveryPlan();
        if (recoveryUpdate?.startsWith('recovery_step_failed')) {
            return this.recover(turns, systemMessage, stopSeq, recoveryUpdate);
        }
        if (this.recoveryPlan) {
            if (this.recoveryPlan.pendingIssuance) return '';
            try {
                return await this.runRecoveryStep(turns, systemMessage, stopSeq);
            } catch (error) {
                if (isMindcraftHistoryBoundaryError(error)) throw error;
                console.warn('[Evelyn Mindcraft] stale recovery plan discarded:', error?.message || error);
                this.clearRecoveryPlan();
            }
        }
        const goalPolicy = readGoalPolicy();
        if (Number(goalPolicy?.currentSubgoal?.observationStreak || 0) >= 2) {
            return this.recover(turns, systemMessage, stopSeq, 'observation_budget_exhausted');
        }
        if (!recoveryUpdate && analysis.shouldEscalate) {
            return this.recover(turns, systemMessage, stopSeq, analysis.reason);
        }

        if (shouldConsultRouter('action', turns)) {
            const route = await this.chooseRoute(turns);
            console.log(`[Evelyn Mindcraft] planner gate=router decision=${route}`);
            if (route === 'codex') {
                return this.recover(turns, systemMessage, stopSeq, 'router_complex_request');
            }
        }

        try {
            const local = await this.requestLocal(turns, systemMessage, stopSeq, 'action');
            const validation = validateActionResponse(local, systemMessage);
            if (!validation.ok) {
                return this.recover(
                    turns,
                    systemMessage,
                    stopSeq,
                    `local_output_invalid:${validation.reason}`
                );
            }
            console.log('[Evelyn Mindcraft] planner route=local gate=simple');
            return validation.command;
        } catch (error) {
            if (isMindcraftHistoryBoundaryError(error)) throw error;
            console.error('[Evelyn Mindcraft] Local planner failed; escalating to recovery:', error?.message || error);
            return this.recover(turns, systemMessage, stopSeq, 'local_request_failed');
        }
    }

    async sendRequest(turns, systemMessage, stopSeq = '***') {
        return withMindcraftHistoryExposure(
            turns,
            () => this._sendRequest(turns, systemMessage, stopSeq),
        );
    }

    async _sendRequest(turns, systemMessage, stopSeq = '***') {
        let kind = classifyRequest(turns, systemMessage);
        const latest = compactTurns(turns, 1, 1200)[0] || null;
        const latestIsUser = latest?.role === 'user';
        if (kind === 'action') {
            this.actionModeUntil = Date.now() + ACTION_MODE_TTL_MS;
        } else if (latestIsUser) {
            this.actionModeUntil = 0;
        } else if (kind === 'chat' && Date.now() < this.actionModeUntil) {
            kind = 'action';
        }
        if (kind === 'action') {
            return this.sendActionRequest(turns, systemMessage, stopSeq);
        }

        if (shouldConsultRouter(kind, turns)) {
            const route = await this.chooseRoute(turns);
            console.log(`[Evelyn Mindcraft] planner gate=router kind=${kind} decision=${route}`);
        }

        try {
            const result = await this.requestLocal(turns, systemMessage, stopSeq, kind);
            console.log(`[Evelyn Mindcraft] planner route=local gate=utility kind=${kind}`);
            return result;
        } catch (error) {
            if (isMindcraftHistoryBoundaryError(error)) throw error;
            console.error('[Evelyn Mindcraft] Local utility request failed:', error?.message || error);
            if (kind === 'memory') throw new Error('mindcraft_memory_summary_unavailable');
            if (kind === 'classifier') return 'ignore';
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
