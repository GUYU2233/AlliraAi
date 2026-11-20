import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AlliraClient, AlliraApiError } from '../dist/index.js';

const conversation = { id: 'conversation-1', title: 'Chat', instructions: null, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' };
const info = { environment: 'development', provider: 'mock', allowDevAuth: true, consoleEnabled: true, adminEnabled: true };

test('chat maintenance routes preserve payloads, encoded identifiers, Bearer, and signals', async () => {
  const calls = [];
  const responses = [info, { userId: 'developer' }, { ok: true }, conversation, conversation, { ok: true }, { conversation, activeRun: null, lastSequence: 4 }];
  const sdk = new AlliraClient({
    baseUrl: 'https://allira.example/api/v1/', accessToken: 'test-bearer',
    fetch: async (url, init) => { calls.push({ url, ...init }); return Response.json(responses[calls.length - 1]); },
  });
  const controller = new AbortController();
  const options = { signal: controller.signal };
  assert.deepEqual(await sdk.getInfo(options), info);
  assert.deepEqual(await sdk.getSession(options), { userId: 'developer' });
  assert.deepEqual(await sdk.logout(options), { ok: true });
  assert.deepEqual(await sdk.updateConversation('c /+', { title: '' }, options), conversation);
  await sdk.updateConversation('c /+', { instructions: null }, options);
  await sdk.deleteConversation('c /+', options);
  assert.deepEqual(await sdk.getConversationState('c /+', options), { conversation, activeRun: null, lastSequence: 4 });
  assert.deepEqual(calls.map(c => [c.method, c.url]), [
    ['GET', 'https://allira.example/api/v1/info'],
    ['GET', 'https://allira.example/api/v1/session'],
    ['POST', 'https://allira.example/api/v1/session/logout'],
    ['POST', 'https://allira.example/api/v1/conversations/c%20%2F%2B/update'],
    ['POST', 'https://allira.example/api/v1/conversations/c%20%2F%2B/update'],
    ['POST', 'https://allira.example/api/v1/conversations/c%20%2F%2B/delete'],
    ['GET', 'https://allira.example/api/v1/conversations/c%20%2F%2B/state'],
  ]);
  assert.equal(calls[0].headers.get('Authorization'), null);
  for (const call of calls.slice(1)) assert.equal(call.headers.get('Authorization'), 'Bearer test-bearer');
  for (const call of calls) assert.equal(call.signal, controller.signal);
  assert.deepEqual(JSON.parse(calls[3].body), { title: '' });
  assert.deepEqual(JSON.parse(calls[4].body), { instructions: null });
  assert.equal(calls[2].body, undefined);
  assert.equal(calls[5].body, undefined);
});

test('public info does not evaluate or attach the configured Bearer callback', async () => {
  let calls = 0;
  const sdk = new AlliraClient({
    baseUrl: 'https://allira.example',
    accessToken: () => { throw new Error('public info must not read a developer credential'); },
    fetch: async (_url, init) => { calls++; assert.equal(init.headers.get('Authorization'), null); return Response.json(info); },
  });
  await sdk.getInfo();
  assert.equal(calls, 1);
});

test('update requires an actual field before fetching, and supports both supplied fields', async () => {
  let calls = 0;
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example', fetch: async (_url, init) => {
    calls++; assert.deepEqual(JSON.parse(init.body), { title: 'Changed', instructions: '' }); return Response.json(conversation);
  } });
  for (const input of [{}, { title: undefined }, { instructions: undefined }, null]) {
    assert.throws(() => sdk.updateConversation('c', input), TypeError);
  }
  assert.equal(calls, 0);
  await sdk.updateConversation('c', { title: 'Changed', instructions: '' });
  assert.equal(calls, 1);
});

test('recent messages send before rather than cursor and preserve ascending pages unchanged', async () => {
  const calls = [];
  const page = { items: [{ id: 'message-3', sequence: 3 }, { id: 'message-4', sequence: 4 }], nextCursor: 'message-3' };
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example', fetch: async (url, init) => {
    calls.push({ url, init }); return Response.json(calls.length === 3 ? { items: [], nextCursor: null } : page);
  } });
  const controller = new AbortController();
  assert.deepEqual(await sdk.listRecentMessages('c /+', { before: 'm /+', limit: 2, signal: controller.signal }), page);
  await sdk.listRecentMessages('c');
  assert.deepEqual(await sdk.listRecentMessages('c', { before: '', limit: 1 }), { items: [], nextCursor: null });
  assert.deepEqual(calls.map(c => c.url), [
    'https://allira.example/api/v1/conversations/c%20%2F%2B/messages/recent?before=m+%2F%2B&limit=2',
    'https://allira.example/api/v1/conversations/c/messages/recent',
    'https://allira.example/api/v1/conversations/c/messages/recent?before=&limit=1',
  ]);
  assert.equal(calls[0].init.signal, controller.signal);
});

test('chat deletion conflict and logged-out session preserve API error codes without retry', async () => {
  let count = 0;
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example', fetch: async url => {
    count++;
    return url.endsWith('/delete')
      ? Response.json({ error: { code: 'run_in_progress', message: 'Run in progress' } }, { status: 409 })
      : Response.json({ error: { code: 'unauthorized', message: 'Session expired' } }, { status: 401 });
  } });
  await assert.rejects(sdk.deleteConversation('c'), error => error instanceof AlliraApiError && error.status === 409 && error.code === 'run_in_progress');
  await assert.rejects(sdk.getSession(), error => error instanceof AlliraApiError && error.status === 401);
  assert.equal(count, 2);
});
