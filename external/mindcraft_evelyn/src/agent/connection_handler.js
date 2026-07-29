import { inspect } from 'node:util';
import { sendOutputToServer } from './mindserver_proxy.js';

const ERROR_DEFINITIONS = {
    name_conflict: {
        keywords: ['name_taken', 'duplicate_login', 'already connected', 'already logged in', 'username is already'],
        msg: 'Name Conflict: The name is already in use or you are already logged in.',
        isFatal: true
    },
    access_denied: {
        keywords: ['whitelist', 'not white-listed', 'banned', 'suspended', 'verify'],
        msg: 'Access Denied: You are not whitelisted or banned.',
        isFatal: true
    },
    server_full: {
        keywords: ['server is full', 'full server'],
        msg: 'Connection Failed: The server is full.',
        isFatal: false
    },
    version_mismatch: {
        keywords: ['outdated', 'version', 'client'],
        msg: 'Version Mismatch: Client and server versions do not match.',
        isFatal: true
    },
    maintenance: {
        keywords: ['maintenance', 'updating', 'closed', 'restarting'],
        msg: 'Connection Failed: Server is under maintenance or restarting.',
        isFatal: false
    },
    network_error: {
        keywords: ['timeout', 'timed out', 'connection lost', 'reset', 'refused', 'keepalive'],
        msg: 'Network Error: Connection timed out or was lost.',
        isFatal: false
    },
    behavior: {
        keywords: ['flying', 'spam', 'speed', 'moved too quickly'],
        msg: 'Kicked: Removed from server due to movement or packet behavior.',
        isFatal: true
    }
};

export const log = (agentName, msg) => {
    console.error(msg);
    try {
        sendOutputToServer(agentName || 'system', msg);
    } catch {
        // The local console remains authoritative when MindServer is unavailable.
    }
};

function flattenReason(value, seen = new Set()) {
    if (value === null || value === undefined) return '';
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (value instanceof Error) {
        return [value.name, value.message, value.code].filter(Boolean).join(': ');
    }
    if (typeof value !== 'object' || seen.has(value)) return '';
    seen.add(value);
    const preferred = [
        value.text,
        value.translate,
        value.message,
        value.reason,
        value.code,
        value.value
    ];
    const preferredText = preferred
        .map((entry) => flattenReason(entry, seen))
        .filter(Boolean)
        .join(' | ');
    if (preferredText) return preferredText;
    if (Array.isArray(value.extra)) {
        const extra = value.extra.map((entry) => flattenReason(entry, seen)).filter(Boolean).join(' ');
        if (extra) return extra;
    }
    try {
        return JSON.stringify(value);
    } catch {
        return inspect(value, {depth: 4, breakLength: Infinity});
    }
}

export function formatDisconnectReason(reason) {
    return flattenReason(reason).trim() || 'Unknown reason (empty payload)';
}

export function parseKickReason(reason) {
    const readable = formatDisconnectReason(reason);
    const normalized = readable.toLowerCase();
    for (const [type, definition] of Object.entries(ERROR_DEFINITIONS)) {
        if (definition.keywords.some((keyword) => normalized.includes(keyword))) {
            return {type, msg: definition.msg, isFatal: definition.isFatal, raw: readable};
        }
    }
    return {
        type: 'other',
        msg: `Disconnected: ${readable}`,
        isFatal: true,
        raw: readable
    };
}

export function handleDisconnection(agentName, reason, event = 'Disconnected') {
    const parsed = parseKickReason(reason);
    const finalMsg = `[LoginGuard] ${event}: ${parsed.msg} | raw=${parsed.raw}`;
    log(agentName, finalMsg);
    return {...parsed, msg: finalMsg, event};
}

export function validateNameFormat(name) {
    if (!name || !/^[a-zA-Z0-9_]{3,16}$/.test(name)) {
        return {
            success: false,
            msg: `[LoginGuard] Invalid name '${name}'. Must be 3-16 alphanumeric/underscore characters.`
        };
    }
    return {success: true};
}
