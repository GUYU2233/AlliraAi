import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isDeepStrictEqual } from 'node:util';
import { AlliraAdminClient, AlliraApiError, AlliraClient } from '../dist/index.js';

const baseUrl = 'https://allira.example/api/v1';
const time = '2026-11-19T00:00:00Z';
const token = 'fixture-bearer';
const csrf = 'fixture-account-csrf';
const oldPassword = 'fixture-current-password';
const newPassword = 'fixture-replacement-password';
const user = { id: 'account-1', username: 'sample', displayName: 'Sample', createdAt: time, termsVersion: '2026-11-19', isAdult: true };
const id = 'conversation /+?#';
const conversation = { id, title: 'Example', instructions: null, createdAt: time, updatedAt: time };
const conversationPath = `/conversations/${encodeURIComponent(id)}`;
const archive = {
  format: 'allira.conversation.v1', conversation, character: null,
  memory: { conversationId: id, characterId: null, coreMemory: '', relationship: '', summary: '', summarizedThrough: 0, updatedAt: time },
  messages: [{ id: 'message-1', conversationId: id, runId: 'run-1', sequence: 1, role: 'user', content: 'Example text', status: 'completed', createdAt: time }],
  importSource: { format: 'text', content: 'Example archived source', archivedMessages: 1 },
};
const accountExport = {
  format: 'allira.account.v1', exportedAt: time, user, conversations: [archive], characters: [],
  wallet: null, ledger: [], usage: [], warnings: ['Private archive, not a restore package.'],
};
const session = { id: '83bea767-f7dd-4a56-9a53-d32ca3f6d798', createdAt: time, lastSeenAt: time, expiresAt: time, label: 'Browser', current: true, type: 'browser' };
const sessions = { items: [session, { ...session, id: 'f40cf7d3-749f-4eb7-85f8-799fa84d10cc', label: 'SDK', current: false, type: 'bearer' }] };
const forkId = 'caf6172c-38d7-49d1-84ad-4fb55f731eb9';
const recoveryCodes = { codes: Array.from({ length: 8 }, (_, i) => `fixture-recovery-code-${i}`), createdAt: time };
const errorResponse = (status, code) => Response.json({ error: { code, message: 'Fixture error' } }, { status });
const apiError = (status, code) => error => error instanceof AlliraApiError && error.status === status && error.code === code;
const sdk = (fetch, options = {}) => new AlliraClient({ baseUrl, accessToken: token, fetch, ...options });
const assertBearer = call => {
  assert.equal(call.credentials, 'omit');
  assert.equal(call.headers.get('Authorization') === `Bearer ${token}`, true);
  assert.equal(call.headers.has('X-CSRF-Token'), false);
};
const assertBody = (call, expected) => assert.equal(isDeepStrictEqual(JSON.parse(call.body), expected), true);

test('account recovery status and code rotation use the protected route and exact password-only body', async () => {
  const calls = [];
  const initialStatus = { enabled: false, remainingCodes: 0, updatedAt: null };
  const updatedStatus = { enabled: true, remainingCodes: 8, updatedAt: time };
  const client = sdk(async (url, init) => {
    calls.push({ url, ...init });
    return Response.json([initialStatus, recoveryCodes, updatedStatus][calls.length - 1]);
  });
  const { signal } = new AbortController();
  assert.deepEqual(await client.getRecoveryStatus({ signal }), initialStatus);
  const rotated = await client.rotateRecoveryCodes({ currentPassword: oldPassword, unrelated: 'ignore' }, { signal });
  assert.equal(isDeepStrictEqual(rotated, recoveryCodes), true);
  assert.deepEqual(await client.getRecoveryStatus({ signal }), updatedStatus);
  assert.deepEqual(calls.map(c => c.method), ['GET', 'POST', 'GET']);
  assertBody(calls[1], { currentPassword: oldPassword });
  assert.equal(calls[0].body, undefined);
  for (const call of calls) {
    assert.equal(call.url, `${baseUrl}/account/recovery`);
    assert.equal(call.signal, signal);
    assertBearer(call);
  }
});

test('public account recovery sends exactly the new credentials and leaves token management to the caller', async () => {
  const calls = [];
  let tokenReads = 0;
  const client = sdk(async (url, init) => {
    calls.push({ url, ...init });
    return Response.json(init.method === 'POST' ? { ok: true } : user);
  }, { accessToken: async () => { tokenReads++; return token; } });
  const { signal } = new AbortController();
  const input = { username: user.username, recoveryCode: recoveryCodes.codes[0], newPassword };
  assert.deepEqual(await client.recoverAccount({ ...input, currentPassword: oldPassword, accessToken: token }, { signal }), { ok: true });
  assert.equal(tokenReads, 0);
  const [call] = calls;
  assert.equal(call.url, `${baseUrl}/auth/recover`);
  assert.equal(call.method, 'POST');
  assert.equal(call.signal, signal);
  assert.equal(call.credentials, 'omit');
  assert.equal(call.headers.has('Authorization'), false);
  assert.equal(call.headers.has('X-CSRF-Token'), false);
  assertBody(call, input);
  await client.getAccount();
  assert.equal(tokenReads, 1);
  assertBearer(calls[1]);
});

test('list/revoke account device sessions keeps summaries unchanged and encodes the device ID', async () => {
  const calls = [];
  const client = sdk(async (url, init) => {
    calls.push({ url, ...init });
    return Response.json(init.method === 'GET' ? sessions : { ok: true });
  });
  const { signal } = new AbortController();
  assert.deepEqual(await client.listAccountSessions({ signal }), sessions);
  assert.deepEqual(await client.revokeAccountSession('session/ +?', { signal }), { ok: true });
  assert.deepEqual(calls.map(c => [c.method, c.url]), [
    ['GET', `${baseUrl}/account/sessions`], ['POST', `${baseUrl}/account/sessions/session%2F%20%2B%3F/revoke`],
  ]);
  for (const call of calls) {
    assertBearer(call);
    assert.equal(call.body, undefined);
    assert.equal(call.signal, signal);
  }
});

test('browser recovery and device writes reuse the same CSRF channel, never the Bearer callback', async () => {
  const calls = [];
  const client = sdk(async (url, init) => {
    calls.push({ url, ...init });
    return Response.json([
      { user, csrfToken: csrf, expiresAt: time }, recoveryCodes, sessions, { ok: true },
    ][calls.length - 1]);
  }, { browserSession: true, accessToken: () => assert.fail('cookie route must not read Bearer') });
  await client.getBrowserSession();
  await client.rotateRecoveryCodes({ currentPassword: oldPassword });
  await client.listAccountSessions();
  await client.revokeAccountSession(session.id);
  for (const call of calls) {
    assert.equal(call.headers.has('Authorization'), false);
    assert.equal(call.credentials, 'same-origin');
    assert.equal(call.headers.get('X-CSRF-Token') === csrf, call.method === 'POST');
    if (call.method === 'POST') assert.equal(call.headers.get('Origin'), new URL(baseUrl).origin);
  }
});

test('search URL-encodes the query and returns opaque cursors unchanged, including an empty recent search', async () => {
  const calls = [];
  const hit = { conversation, matchedMessageId: 'message-2', snippet: '<literal>你好 &=' };
  const cursor = 'opaque /+?=&你好';
  const first = { items: [hit], nextCursor: cursor };
  const second = { items: [{ ...hit, matchedMessageId: null }], nextCursor: null };
  const client = sdk(async (url, init) => {
    calls.push({ url, ...init });
    return Response.json(calls.length === 1 ? first : second);
  });
  const { signal } = new AbortController();
  const q = '  搜索 /+&?=  ';
  const page = await client.searchConversations({ q, signal, limit: 2 });
  assert.deepEqual(page, first);
  assert.deepEqual(await client.searchConversations({ q, cursor: page.nextCursor, signal }), second);
  await client.searchConversations({ q: '', signal });
  assert.deepEqual(calls.map(c => [...new URL(c.url).searchParams]), [
    [['q', q], ['limit', '2']], [['q', q], ['cursor', cursor]], [['q', '']],
  ]);
  for (const call of calls) {
    assert.equal(new URL(call.url).pathname, '/api/v1/conversation-search');
    assert.equal(call.method, 'GET');
    assert.equal(call.signal, signal);
    assert.equal(call.body, undefined);
    assertBearer(call);
  }
});

for (const format of ['json', 'markdown']) {
  test(`conversation ${format} export preserves filename and serialized content`, async () => {
    const content = format === 'json' ? JSON.stringify(archive) : '# Example\n\n```html\n<strong>literal</strong>\n```\n';
    const dto = { format, filename: format === 'json' ? 'conversation.json' : 'conversation.md', content };
    const { signal } = new AbortController();
    const client = sdk(async (url, init) => {
      assert.equal(url, `${baseUrl}${conversationPath}/export?format=${format}`);
      assert.equal(init.method, 'GET');
      assert.equal(init.signal, signal);
      assert.equal(init.body, undefined);
      assertBearer(init);
      return Response.json(dto);
    });
    assert.deepEqual(await client.exportConversation(id, { format }, { signal }), dto);
  });
}

test('fork accepts the first 201 and an explicit replay with identical UUID, source and parameters', async () => {
  const calls = [];
  const copied = { conversation: { ...conversation, id: 'fork-target' }, copiedMessages: 4 };
  const client = sdk(async (url, init) => {
    calls.push({ url, ...init });
    return Response.json({ ...copied, replayed: calls.length > 1 }, { status: 201 });
  });
  const input = { clientForkId: forkId, throughMessageId: 'msg-4', title: '  Exact title  ' };
  const { signal } = new AbortController();
  assert.deepEqual(await client.forkConversation(id, input, { signal }), { ...copied, replayed: false });
  assert.equal(calls.length, 1);
  assert.deepEqual(await client.forkConversation(id, input, { signal }), { ...copied, replayed: true });
  assert.equal(calls.length, 2);
  for (const call of calls) {
    assert.equal(call.url, `${baseUrl}${conversationPath}/fork`);
    assert.equal(call.method, 'POST');
    assert.equal(call.signal, signal);
    assertBearer(call);
    assertBody(call, input);
  }
});

test('fork preserves omitted fields versus explicit null, and never forwards unrelated input', async () => {
  const bodies = [];
  const client = sdk(async (_url, { body }) => {
    bodies.push(JSON.parse(body));
    return Response.json({ conversation, replayed: false, copiedMessages: 0 }, { status: 201 });
  });
  await client.forkConversation(id, { clientForkId: forkId, unrelated: 'ignore' });
  await client.forkConversation(id, { clientForkId: forkId, throughMessageId: null, title: null });
  assert.deepEqual(bodies, [{ clientForkId: forkId }, { clientForkId: forkId, throughMessageId: null, title: null }]);
});

test('typed account export preserves nested archives and nullable wallet; erase is explicit password-confirmed POST', async () => {
  const calls = [];
  const client = sdk(async (url, init) => {
    calls.push({ url, ...init });
    return Response.json(init.method === 'GET' ? accountExport : { ok: true });
  });
  const { signal } = new AbortController();
  assert.deepEqual(await client.exportAccountData({ signal }), accountExport);
  assert.deepEqual(await client.eraseAccount({ currentPassword: oldPassword, confirmation: 'DELETE MY ACCOUNT', accessToken: token }, { signal }), { ok: true });
  assert.deepEqual(calls.map(c => [c.method, c.url]), [
    ['GET', `${baseUrl}/account/export`], ['POST', `${baseUrl}/account/erase`],
  ]);
  assert.equal(calls[0].body, undefined);
  assertBody(calls[1], { currentPassword: oldPassword, confirmation: 'DELETE MY ACCOUNT' });
  for (const call of calls) {
    assertBearer(call);
    assert.equal(call.signal, signal);
  }
});

for (const [status, code, invoke] of [
  [401, 'invalid_credentials', client => client.recoverAccount({ username: user.username, recoveryCode: recoveryCodes.codes[0], newPassword })],
  [429, 'rate_limited', client => client.recoverAccount({ username: user.username, recoveryCode: recoveryCodes.codes[0], newPassword })],
  [400, 'invalid_cursor', client => client.searchConversations({ q: 'different', cursor: 'old-cursor' })],
  [400, 'invalid_fork_point', client => client.forkConversation(id, { clientForkId: forkId, throughMessageId: 'user-msg' })],
  [404, 'not_found', client => client.forkConversation(id, { clientForkId: forkId })],
  [409, 'idempotency_conflict', client => client.forkConversation(id, { clientForkId: forkId, title: 'changed' })],
  [409, 'run_in_progress', client => client.exportConversation(id, { format: 'json' })],
  [413, 'export_too_large', client => client.exportConversation(id, { format: 'markdown' })],
  [413, 'account_export_too_large', client => client.exportAccountData()],
  [409, 'account_busy', client => client.eraseAccount({ currentPassword: oldPassword, confirmation: 'DELETE MY ACCOUNT' })],
]) {
  test(`account/conversation tools preserve ${status} ${code} without hidden retry or fallback`, async () => {
    let calls = 0;
    const client = sdk(async () => { calls++; return errorResponse(status, code); });
    await assert.rejects(invoke(client), apiError(status, code));
    assert.equal(calls, 1);
  });
}

test('account tools propagate a pre-abort without reading credentials or making a request', async () => {
  const client = sdk(async () => assert.fail('must not fetch'), { accessToken: () => assert.fail('must not read') });
  const controller = new AbortController();
  const reason = new DOMException('Fixture abort', 'AbortError');
  controller.abort(reason);
  const { signal } = controller;
  for (const invoke of [
    () => client.listAccountSessions({ signal }),
    () => client.exportAccountData({ signal }),
    () => client.forkConversation(id, { clientForkId: forkId }, { signal }),
  ]) await assert.rejects(invoke(), error => error === reason);
});

const diagnostics = {
  version: '0.4.0', startedAt: time, uptimeSeconds: 120, schemaVersion: 4,
  database: { ok: true, journalMode: 'wal', databaseBytes: 4096, walBytes: 0 },
  runs: { running: 0, inFlight: 1, closing: 1, capacity: 3 },
  sessions: { bearer: 1, browser: 2, admin: 1 },
  backup: { configured: false, latestAt: null, ageSeconds: null, status: 'missing' },
  warnings: ['Backup is not configured.'],
};

test('admin diagnostics is cookie-only GET with no CSRF bootstrap, preserves counts and nullable backup metadata', async () => {
  const { signal } = new AbortController();
  let calls = 0;
  const admin = new AlliraAdminClient({ baseUrl, fetch: async (url, init) => {
    calls++;
    assert.equal(url, `${baseUrl}/admin/diagnostics`);
    assert.equal(init.method, 'GET');
    assert.equal(init.credentials, 'same-origin');
    assert.equal(init.signal, signal);
    assert.equal(init.headers.has('Authorization'), false);
    assert.equal(init.headers.has('X-CSRF-Token'), false);
    assert.equal(init.body, undefined);
    return Response.json(diagnostics);
  } });
  assert.deepEqual(await admin.getDiagnostics({ signal }), diagnostics);
  assert.equal(calls, 1);
});

for (const [status, code] of [[401, 'unauthorized'], [404, 'not_found']]) {
  test(`unavailable diagnostics preserves ${status} and does not authenticate or retry`, async () => {
    let calls = 0;
    const admin = new AlliraAdminClient({ baseUrl, fetch: async () => { calls++; return errorResponse(status, code); } });
    await assert.rejects(admin.getDiagnostics(), apiError(status, code));
    assert.equal(calls, 1);
  });
}
