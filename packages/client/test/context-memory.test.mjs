import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AlliraApiError, AlliraClient } from '../dist/index.js';

// Synthetic fixtures only: no API, database, model, browser storage or live secrets.
const baseUrl = 'https://allira.example:8443/api/v1';
const origin = new URL(baseUrl).origin;
const stamp = '2026-11-19T04:00:00Z';
const cardInput = { category: 'preference', title: 'Tea', content: 'Prefers jasmine tea.' };
const card = { id: 'card-1', conversationId: 'conversation-1', ...cardInput, sourceMessageId: null, enabled: true, createdAt: stamp, updatedAt: stamp };
const block = { id: 'block-1', conversationId: 'conversation-1', kind: 'extractive_pair', sourceMessageIds: ['message-1', 'message-2'], startSequence: 1, endSequence: 2, summary: 'Synthetic pair excerpt', enabled: true, createdAt: stamp };
const messages = [
  { id: 'message-1', conversationId: 'conversation-1', runId: 'run-1', sequence: 1, role: 'user', content: 'Synthetic original question', status: 'completed', createdAt: stamp },
  { id: 'message-2', conversationId: 'conversation-1', runId: 'run-1', sequence: 2, role: 'assistant', content: 'Synthetic exact original reply', status: 'completed', createdAt: stamp },
];
const source = { block, messages, warnings: [] };
const preview = {
  conversationId: 'conversation-1', measurement: 'characters_not_tokens', characterLimit: 1000,
  totalCharacters: 72, remainingCharacters: 928,
  sections: [{ kind: 'memory_cards', characters: 36, messages: 1 }, { kind: 'current_message', characters: 36, messages: 1 }],
  messages: [{ role: 'system', content: 'Synthetic memory context' }, { role: 'user', content: 'Synthetic draft' }],
  includedCardIds: [card.id], includedBlockIds: [block.id], pendingBlockIds: ['pending-block-1'],
  excludedCardCount: 2, omittedBlockCount: 3, omittedRecentPairCount: 4, warnings: ['Synthetic budget warning'],
};
const session = {
  user: { id: 'account-1', username: 'sample', displayName: 'Sample', createdAt: stamp, termsVersion: '2026-11-19', isAdult: true },
  csrfToken: 'fixture-context-csrf', expiresAt: stamp,
};

function recording(responses, options = {}) {
  const calls = [];
  const sdk = new AlliraClient({
    baseUrl, accessToken: 'fixture-context-bearer', ...options,
    fetch: async (url, init) => {
      calls.push({ url, ...init });
      const result = responses[calls.length - 1];
      return result instanceof Response ? result : Response.json(result);
    },
  });
  return { sdk, calls };
}

const operations = [
  ['listMemoryCards', (sdk, options) => sdk.listMemoryCards('conversation /+?#', options), 'GET', '/conversations/conversation%20%2F%2B%3F%23/memory/cards', { items: [card] }],
  ['createMemoryCard', (sdk, options) => sdk.createMemoryCard('conversation /+?#', cardInput, options), 'POST', '/conversations/conversation%20%2F%2B%3F%23/memory/cards', card, cardInput],
  ['updateMemoryCard', (sdk, options) => sdk.updateMemoryCard('conversation /+?#', 'card /+?#', { sourceMessageId: null, enabled: false }, options), 'POST', '/conversations/conversation%20%2F%2B%3F%23/memory/cards/card%20%2F%2B%3F%23/update', card, { sourceMessageId: null, enabled: false }],
  ['deleteMemoryCard', (sdk, options) => sdk.deleteMemoryCard('conversation /+?#', 'card /+?#', options), 'POST', '/conversations/conversation%20%2F%2B%3F%23/memory/cards/card%20%2F%2B%3F%23/delete', { ok: true }],
  ['listMemoryBlocks', (sdk, options) => sdk.listMemoryBlocks('conversation /+?#', options), 'GET', '/conversations/conversation%20%2F%2B%3F%23/memory/blocks', { items: [block], nextCursor: null }],
  ['updateMemoryBlock', (sdk, options) => sdk.updateMemoryBlock('conversation /+?#', 'block /+?#', { enabled: false }, options), 'POST', '/conversations/conversation%20%2F%2B%3F%23/memory/blocks/block%20%2F%2B%3F%23/update', block, { enabled: false }],
  ['getMemoryBlockSource', (sdk, options) => sdk.getMemoryBlockSource('conversation /+?#', 'block /+?#', options), 'GET', '/conversations/conversation%20%2F%2B%3F%23/memory/blocks/block%20%2F%2B%3F%23/source', source],
  ['previewContext', (sdk, options) => sdk.previewContext('conversation /+?#', { content: 'Synthetic draft' }, options), 'POST', '/conversations/conversation%20%2F%2B%3F%23/context/preview', preview, { content: 'Synthetic draft' }],
];

for (const [name, operation, method, path, result, body] of operations) {
  test(`traceable memory ${name} preserves HTTP, encoded identifiers, JSON, Bearer and signal`, async () => {
    const { sdk, calls } = recording([name === 'createMemoryCard' ? Response.json(result, { status: 201 }) : result]);
    const { signal } = new AbortController();
    assert.deepEqual(await operation(sdk, { signal }), result);
    assert.equal(calls.length, 1);
    const call = calls[0];
    assert.equal(call.url, `${baseUrl}${path}`);
    assert.equal(call.method, method);
    assert.equal(call.signal, signal);
    assert.equal(call.credentials, 'omit');
    assert.equal(call.mode, undefined);
    assert.equal(call.headers.get('Authorization') === 'Bearer fixture-context-bearer', true);
    assert.equal(call.headers.get('Accept'), 'application/json');
    assert.equal(call.headers.has('Origin'), false);
    assert.equal(call.headers.has('X-CSRF-Token'), false);
    assert.equal(call.headers.get('Content-Type'), body === undefined ? null : 'application/json');
    assert.deepEqual(call.body === undefined ? undefined : JSON.parse(call.body), body);
  });
}

test('memory cards keep omitted defaults and explicit null/false while excluding server-managed fields', async () => {
  const { sdk, calls } = recording([card, card, card]);
  await sdk.createMemoryCard('c', { ...cardInput, id: 'ignored', conversationId: 'other', createdAt: 'ignored' });
  await sdk.createMemoryCard('c', { ...cardInput, sourceMessageId: null, enabled: false, updatedAt: 'ignored' });
  await sdk.updateMemoryCard('c', 'card', { ...cardInput, sourceMessageId: 'message-1', enabled: true, id: 'ignored', createdAt: 'ignored' });
  assert.deepEqual(calls.map(call => JSON.parse(call.body)), [
    cardInput,
    { ...cardInput, sourceMessageId: null, enabled: false },
    { ...cardInput, sourceMessageId: 'message-1', enabled: true },
  ]);
});

test('memory card updates reject empty/undefined-only patches locally and accept each defined field', async () => {
  const { sdk, calls } = recording(Array(7).fill(card));
  for (const input of [{}, null, undefined, { category: undefined }, { title: undefined }, { content: undefined }, { sourceMessageId: undefined }, { enabled: undefined }, { id: 'not-a-patch' }]) {
    assert.throws(() => sdk.updateMemoryCard('c', 'card', input), { name: 'TypeError', message: 'updateMemoryCard requires category, title, content, sourceMessageId or enabled' });
  }
  assert.equal(calls.length, 0);
  const patches = [{ category: 'boundary' }, { title: '' }, { content: '' }, { sourceMessageId: null }, { sourceMessageId: 'message-1' }, { enabled: false }, { enabled: true }];
  for (const patch of patches) await sdk.updateMemoryCard('c', 'card', patch);
  assert.deepEqual(calls.map(call => JSON.parse(call.body)), patches);
});

test('memory block list escapes opaque cursor/query once, preserves empty query and server pagination', async () => {
  const page = { items: [block], nextCursor: 'next /+?&=' };
  const { sdk, calls } = recording([page, { items: [], nextCursor: null }, { items: [], nextCursor: null }]);
  const { signal } = new AbortController();
  const q = '摘要 /+?&= #';
  const cursor = 'opaque /+?&= #';
  assert.deepEqual(await sdk.listMemoryBlocks('c /+', { q, cursor, limit: 2, signal }), page);
  const url = new URL(calls[0].url);
  assert.equal(url.pathname, '/api/v1/conversations/c%20%2F%2B/memory/blocks');
  assert.deepEqual([...url.searchParams], [['cursor', cursor], ['limit', '2'], ['q', q]]);
  assert.equal(calls[0].signal, signal);
  await sdk.listMemoryBlocks('c', { q: '' });
  await sdk.listMemoryBlocks('c');
  assert.equal(calls[1].url, `${baseUrl}/conversations/c/memory/blocks?q=`);
  assert.equal(calls[2].url, `${baseUrl}/conversations/c/memory/blocks`);
});

test('block toggle and context preview send only their allowed fields without changing private text', async () => {
  const content = 'Synthetic private draft\n<script>do not execute</script>\r\n“茶” 😀';
  const { sdk, calls } = recording([block, { ...preview, messages: [{ role: 'user', content }] }]);
  await sdk.updateMemoryBlock('c', 'block', { enabled: false, summary: 'ignored', sourceMessageIds: ['ignored'] });
  const result = await sdk.previewContext('c', { content, model: 'ignored', clientMessageId: 'ignored', commit: true });
  assert.deepEqual(JSON.parse(calls[0].body), { enabled: false });
  assert.deepEqual(JSON.parse(calls[1].body), { content });
  assert.equal(result.messages[0].content, content);
  assert.equal(result.measurement, 'characters_not_tokens');
  assert.deepEqual(result.includedCardIds, preview.includedCardIds);
  assert.deepEqual(result.includedBlockIds, preview.includedBlockIds);
  assert.deepEqual(result.pendingBlockIds, preview.pendingBlockIds);
  assert.equal(calls.length, 2); // No hidden run, summarize, energy or persistence request.
});

test('block sources preserve exact ordered original messages and legacy warnings without inventing sources', async () => {
  const legacy = { ...block, id: 'legacy-1', kind: 'legacy_summary', sourceMessageIds: [] };
  const legacySource = { block: legacy, messages: [], warnings: ['Synthetic legacy summary has no exact source messages'] };
  const { sdk, calls } = recording([source, legacySource]);
  assert.deepEqual(await sdk.getMemoryBlockSource('c', block.id), source);
  assert.deepEqual(await sdk.getMemoryBlockSource('c', legacy.id), legacySource);
  assert.equal(calls.length, 2);
});

test('cookie memory operations use same-origin without Bearer; every POST including preview requires CSRF', async () => {
  let tokenReads = 0;
  const { sdk, calls } = recording([session, ...operations.map(entry => entry[4])], {
    browserSession: true, accessToken: () => { tokenReads++; throw new Error('must not read Bearer'); },
  });
  await sdk.getBrowserSession();
  const { signal } = new AbortController();
  for (const [, operation] of operations) await operation(sdk, { signal });
  assert.equal(tokenReads, 0);
  assert.equal(calls.length, operations.length + 1);
  for (const call of calls.slice(1)) {
    assert.equal(call.credentials, 'same-origin');
    assert.equal(call.mode, 'same-origin');
    assert.equal(call.signal, signal);
    assert.equal(call.headers.has('Authorization'), false);
    assert.equal(call.headers.get('Origin'), call.method === 'POST' ? origin : null);
    assert.equal(call.headers.get('X-CSRF-Token') === (call.method === 'POST' ? session.csrfToken : null), true);
  }
});

test('cookie writes without loaded CSRF fail before fetching, including read-only context preview', async () => {
  const { sdk, calls } = recording([], { browserSession: true });
  for (const [, operation, method] of operations) {
    if (method === 'POST') await assert.rejects(operation(sdk, {}), { name: 'TypeError' });
  }
  assert.equal(calls.length, 0);
});

test('pre-aborted traceable memory operations preserve reason without reading auth or fetching', async () => {
  let tokens = 0;
  const { sdk, calls } = recording([], { accessToken: () => { tokens++; return 'unused'; } });
  const controller = new AbortController();
  const reason = new DOMException('Synthetic memory abort', 'AbortError');
  controller.abort(reason);
  for (const [, operation] of operations) await assert.rejects(operation(sdk, { signal: controller.signal }), error => error === reason);
  assert.equal(calls.length, 0);
  assert.equal(tokens, 0);
});

test('in-flight memory abort and non-HTTP transport errors keep identity and never retry preview', async () => {
  const controller = new AbortController();
  const reason = new DOMException('Synthetic in-flight abort', 'AbortError');
  let calls = 0;
  const sdk = new AlliraClient({ baseUrl, fetch: async (_url, init) => {
    calls++;
    return new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true });
      controller.abort(reason);
    });
  } });
  await assert.rejects(sdk.previewContext('c', { content: 'Synthetic' }, { signal: controller.signal }), error => error === reason);
  const networkError = new TypeError('Synthetic transport interruption');
  const failing = new AlliraClient({ baseUrl, fetch: async () => { calls++; throw networkError; } });
  await assert.rejects(failing.previewContext('c', { content: 'Synthetic' }), error => error === networkError);
  assert.equal(calls, 2);
});

for (const [index, status, code] of [
  [0, 404, 'not_found'], [1, 422, 'validation_error'], [2, 409, 'run_in_progress'],
  [3, 403, 'forbidden'], [4, 400, 'invalid_cursor'], [5, 404, 'not_found'],
  [6, 404, 'not_found'], [7, 413, 'context_too_large'], [7, 401, 'unauthorized'],
  [7, 429, 'rate_limited'], [7, 503, 'service_unavailable'],
  [1, 409, 'memory_limit_exceeded'], [7, 409, 'run_in_progress'],
  [7, 422, 'unsafe_control_character'], [7, 422, 'content_blocked'],
]) {
  test(`traceable memory ${operations[index][0]} preserves ${status} ${code} with no retry`, async () => {
    const { sdk, calls } = recording([Response.json({ error: { code, message: 'Synthetic request refused' } }, { status })]);
    await assert.rejects(operations[index][1](sdk, {}), error => error instanceof AlliraApiError && error.status === status && error.code === code && error.message === 'Synthetic request refused');
    assert.equal(calls.length, 1);
  });
}

test('cookie CSRF failure is surfaced unchanged and does not fallback to Bearer or retry', async () => {
  let tokens = 0;
  const { sdk, calls } = recording([session, Response.json({ error: { code: 'csrf_failed', message: 'Synthetic CSRF failure' } }, { status: 403 })], {
    browserSession: true, accessToken: () => { tokens++; throw new Error('must not read Bearer'); },
  });
  await sdk.getBrowserSession();
  await assert.rejects(sdk.previewContext('c', { content: 'Synthetic' }), error => error instanceof AlliraApiError && error.status === 403 && error.code === 'csrf_failed');
  assert.equal(tokens, 0);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].headers.has('Authorization'), false);
});

test('conversation and nested account exports preserve optional memory arrays without requiring them on old archives', async () => {
  const archive = {
    format: 'allira.conversation.v1',
    conversation: { id: 'conversation-1', title: 'Synthetic', instructions: null, createdAt: stamp, updatedAt: stamp },
    character: null,
    memory: { conversationId: 'conversation-1', characterId: null, coreMemory: '', relationship: '', summary: '', summarizedThrough: 0, updatedAt: stamp },
    messages, importSource: null,
  };
  const withMemory = { ...archive, memoryCards: [card], memoryBlocks: [block] };
  const accountArchive = { format: 'allira.account.v1', exportedAt: stamp, user: session.user, conversations: [archive, withMemory], characters: [], wallet: null, ledger: [], usage: [], warnings: [] };
  const envelopes = [archive, withMemory].map(value => ({ format: 'json', filename: 'synthetic.json', content: JSON.stringify(value) }));
  const { sdk, calls } = recording([...envelopes, accountArchive]);
  assert.deepEqual(JSON.parse((await sdk.exportConversation('c', { format: 'json' })).content), archive);
  assert.deepEqual(JSON.parse((await sdk.exportConversation('c', { format: 'json' })).content), withMemory);
  assert.deepEqual(await sdk.exportAccountData(), accountArchive);
  assert.equal(calls.length, 3);
});
