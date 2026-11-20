import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AlliraClient, AlliraApiError } from '../dist/index.js';

const stamp = '2026-11-19T00:00:00Z';
const character = { id: 'character-1', name: 'Guide', description: '', instructions: 'Be helpful.', createdAt: stamp, updatedAt: stamp };
const memory = { conversationId: 'conversation-1', characterId: null, coreMemory: 'A fact', relationship: 'New acquaintances', summary: 'A bounded summary', summarizedThrough: 4, updatedAt: stamp };
const conversation = { id: 'conversation-1', title: 'Imported conversation', instructions: null, createdAt: stamp, updatedAt: stamp };
const preview = { messages: [{ role: 'user', content: 'Hello' }, { role: 'assistant', content: 'Welcome' }, { role: 'user', content: 'Unpaired' }], warnings: ['Synthetic import warning'], suggestedTitle: 'Imported conversation', sourceHash: 'fake-source-hash' };
const committed = { conversation, importedMessages: 2, archivedMessages: 1, replayed: false };

function recording(responses) {
  const calls = [];
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example/api/v1/', accessToken: 'fake-account-session', fetch: async (url, init) => {
    calls.push({ url, ...init });
    return Response.json(responses[calls.length - 1]);
  } });
  return { sdk, calls };
}

test('character routes preserve fields, identifiers, pagination, Bearer and signals', async () => {
  const page = { items: [character], nextCursor: 'next /+' };
  const { sdk, calls } = recording([page, character, character, character, { ok: true }]);
  const controller = new AbortController();
  const options = { signal: controller.signal };
  assert.deepEqual(await sdk.listCharacters({ ...options, cursor: 'cursor /+', limit: 2 }), page);
  assert.deepEqual(await sdk.createCharacter({ name: 'Guide', description: '', instructions: 'Be helpful.' }, options), character);
  assert.deepEqual(await sdk.getCharacter('character /+', options), character);
  assert.deepEqual(await sdk.updateCharacter('character /+', { name: 'Renamed', description: '', instructions: '' }, options), character);
  assert.deepEqual(await sdk.deleteCharacter('character /+', options), { ok: true });
  assert.deepEqual(calls.map(call => [call.method, call.url]), [
    ['GET', 'https://allira.example/api/v1/characters?cursor=cursor+%2F%2B&limit=2'],
    ['POST', 'https://allira.example/api/v1/characters'],
    ['GET', 'https://allira.example/api/v1/characters/character%20%2F%2B'],
    ['POST', 'https://allira.example/api/v1/characters/character%20%2F%2B/update'],
    ['POST', 'https://allira.example/api/v1/characters/character%20%2F%2B/delete'],
  ]);
  assert.deepEqual(JSON.parse(calls[1].body), { name: 'Guide', description: '', instructions: 'Be helpful.' });
  assert.deepEqual(JSON.parse(calls[3].body), { name: 'Renamed', description: '', instructions: '' });
  for (const index of [0, 2, 4]) assert.equal(calls[index].body, undefined);
  for (const call of calls) {
    assert.equal(call.headers.get('Authorization') === 'Bearer fake-account-session', true);
    assert.equal(call.signal, controller.signal);
  }
});

test('character defaults and list defaults are left to the server', async () => {
  const { sdk, calls } = recording([character, { items: [], nextCursor: null }]);
  await sdk.createCharacter({ name: 'Guide' });
  assert.deepEqual(await sdk.listCharacters(), { items: [], nextCursor: null });
  assert.deepEqual(JSON.parse(calls[0].body), { name: 'Guide' });
  assert.equal(calls[1].url, 'https://allira.example/api/v1/characters');
});

test('character updates reject empty patches before fetching but accept each defined field', async () => {
  const { sdk, calls } = recording([character, character, character]);
  for (const input of [{}, null, undefined, { name: undefined }, { description: undefined }, { instructions: undefined }]) {
    assert.throws(() => sdk.updateCharacter('c', input), TypeError);
  }
  assert.equal(calls.length, 0);
  for (const patch of [{ name: 'New' }, { description: '' }, { instructions: '' }]) await sdk.updateCharacter('c', patch);
  assert.equal(calls.length, 3);
});

test('conversation memory binds or unbinds characters and summarizes without a request body', async () => {
  const { sdk, calls } = recording([memory, { ...memory, characterId: character.id }, memory, memory, memory]);
  const controller = new AbortController();
  const options = { signal: controller.signal };
  assert.deepEqual(await sdk.getConversationMemory('c /+', options), memory);
  assert.deepEqual(await sdk.updateConversationMemory('c /+', { characterId: character.id, coreMemory: '', relationship: '' }, options), { ...memory, characterId: character.id });
  assert.deepEqual(await sdk.updateConversationMemory('c /+', { characterId: null }, options), memory);
  await sdk.updateConversationMemory('c /+', { coreMemory: 'Updated' }, options);
  assert.deepEqual(await sdk.summarizeConversation('c /+', options), memory);
  assert.deepEqual(calls.map(call => [call.method, call.url]), [
    ['GET', 'https://allira.example/api/v1/conversations/c%20%2F%2B/memory'],
    ['POST', 'https://allira.example/api/v1/conversations/c%20%2F%2B/memory'],
    ['POST', 'https://allira.example/api/v1/conversations/c%20%2F%2B/memory'],
    ['POST', 'https://allira.example/api/v1/conversations/c%20%2F%2B/memory'],
    ['POST', 'https://allira.example/api/v1/conversations/c%20%2F%2B/memory/summarize'],
  ]);
  assert.deepEqual(JSON.parse(calls[1].body), { characterId: character.id, coreMemory: '', relationship: '' });
  assert.deepEqual(JSON.parse(calls[2].body), { characterId: null });
  assert.deepEqual(JSON.parse(calls[3].body), { coreMemory: 'Updated' });
  for (const index of [0, 4]) {
    assert.equal(calls[index].body, undefined);
    assert.equal(calls[index].headers.get('Content-Type'), null);
  }
  for (const call of calls) {
    assert.equal(call.headers.get('Authorization') === 'Bearer fake-account-session', true);
    assert.equal(call.signal, controller.signal);
  }
});

test('memory updates require a defined patch and do not treat null characterId or empty strings as absent', async () => {
  const { sdk, calls } = recording([memory, memory, memory]);
  for (const input of [{}, null, undefined, { characterId: undefined }, { coreMemory: undefined }, { relationship: undefined }]) {
    assert.throws(() => sdk.updateConversationMemory('c', input), TypeError);
  }
  assert.equal(calls.length, 0);
  for (const patch of [{ characterId: null }, { coreMemory: '' }, { relationship: '' }]) await sdk.updateConversationMemory('c', patch);
  assert.equal(calls.length, 3);
});

for (const [format, content] of [
  ['json', JSON.stringify({ messages: [{ role: 'user', content: '你好' }] })],
  ['markdown', '## User\n你好\n\n## Assistant\nHello'],
  ['text', 'User: 你好\r\nAssistant: Hello\nUser: Unpaired'],
]) {
  test(`imports preserve ${format} source, preview warnings, timeline and archived counts`, async () => {
    const source = { format, content, archivedMessages: 1 };
    const { sdk, calls } = recording([preview, committed, source]);
    const controller = new AbortController();
    const options = { signal: controller.signal };
    const input = { format, content };
    const commit = { clientImportId: '11111111-1111-4111-8111-111111111111', ...input, title: '', characterId: null, coreMemory: 'Imported fact', relationship: '' };
    assert.deepEqual(await sdk.previewImport(input, options), preview);
    assert.deepEqual(await sdk.commitImport(commit, options), committed);
    assert.deepEqual(await sdk.getImportSource('c /+', options), source);
    assert.deepEqual(calls.map(call => [call.method, call.url]), [
      ['POST', 'https://allira.example/api/v1/imports/preview'],
      ['POST', 'https://allira.example/api/v1/imports/commit'],
      ['GET', 'https://allira.example/api/v1/conversations/c%20%2F%2B/import-source'],
    ]);
    assert.deepEqual(JSON.parse(calls[0].body), input);
    assert.deepEqual(JSON.parse(calls[1].body), commit);
    assert.equal(calls[2].body, undefined);
    for (const call of calls) {
      assert.equal(call.headers.get('Authorization') === 'Bearer fake-account-session', true);
      assert.equal(call.signal, controller.signal);
    }
  });
}

test('import commits keep application UUIDs and leave explicit replay/idempotency decisions to the caller', async () => {
  const { sdk, calls } = recording([committed, { ...committed, replayed: true }]);
  const input = { clientImportId: '22222222-2222-4222-8222-222222222222', format: 'text', content: 'User: Hello\nAssistant: Hi' };
  assert.equal((await sdk.commitImport(input)).replayed, false);
  assert.equal((await sdk.commitImport(input)).replayed, true);
  assert.equal(calls.length, 2);
  for (const call of calls) assert.deepEqual(JSON.parse(call.body), input);
});

for (const [status, code, operation] of [
  [403, 'forbidden', sdk => sdk.getConversationMemory('other-user-conversation')],
  [404, 'not_found', sdk => sdk.getCharacter('missing')],
  [409, 'run_in_progress', sdk => sdk.summarizeConversation('active')],
  [409, 'idempotency_conflict', sdk => sdk.commitImport({ clientImportId: '33333333-3333-4333-8333-333333333333', format: 'text', content: 'Conflicting source' })],
  [422, 'invalid_import', sdk => sdk.previewImport({ format: 'json', content: 'not parsed in SDK' })],
]) {
  test(`memory/import ${status} ${code} preserves the server error with no retry`, async () => {
    let count = 0;
    const sdk = new AlliraClient({ baseUrl: 'https://allira.example', fetch: async () => {
      count++;
      return Response.json({ error: { code, message: 'Request refused' } }, { status });
    } });
    await assert.rejects(operation(sdk), error => error instanceof AlliraApiError && error.status === status && error.code === code && error.message === 'Request refused');
    assert.equal(count, 1);
  });
}

test('aborted role, memory and import requests preserve the reason without network or auth work', async () => {
  let calls = 0;
  let tokens = 0;
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example', accessToken: () => { tokens++; return 'fake-unused-session'; }, fetch: async () => { calls++; return Response.json({}); } });
  const controller = new AbortController();
  const reason = new DOMException('Synthetic abort', 'AbortError');
  controller.abort(reason);
  const options = { signal: controller.signal };
  const input = { format: 'text', content: 'User: Test', clientImportId: '44444444-4444-4444-8444-444444444444' };
  for (const request of [() => sdk.listCharacters(options), () => sdk.createCharacter({ name: 'Name' }, options), () => sdk.getCharacter('id', options), () => sdk.updateCharacter('id', { description: '' }, options), () => sdk.deleteCharacter('id', options), () => sdk.getConversationMemory('id', options), () => sdk.updateConversationMemory('id', { characterId: null }, options), () => sdk.summarizeConversation('id', options), () => sdk.previewImport(input, options), () => sdk.commitImport(input, options), () => sdk.getImportSource('id', options)]) {
    await assert.rejects(request(), error => error === reason);
  }
  assert.equal(calls, 0);
  assert.equal(tokens, 0);
});
