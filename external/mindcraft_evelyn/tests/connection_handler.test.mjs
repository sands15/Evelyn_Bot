import test from 'node:test';
import assert from 'node:assert/strict';
import {
    formatDisconnectReason,
    parseKickReason
} from '/app/mindcraft/src/agent/connection_handler.js';

test('nested disconnect payloads retain useful text instead of object placeholders', () => {
    const reason = {
        value: {
            translate: 'disconnect.timeout',
            extra: [{text: 'KeepAlive response missing'}]
        }
    };
    const formatted = formatDisconnectReason(reason);
    assert.match(formatted, /disconnect\.timeout/);
    assert.doesNotMatch(formatted, /\[object Object\]/);
    const parsed = parseKickReason(reason);
    assert.equal(parsed.type, 'network_error');
    assert.match(parsed.raw, /disconnect\.timeout/);
});

test('Error disconnect payloads preserve their message and code', () => {
    const formatted = formatDisconnectReason(
        Object.assign(new Error('socket reset'), {code: 'ECONNRESET'})
    );
    assert.match(formatted, /socket reset/);
    assert.match(formatted, /ECONNRESET/);
});
