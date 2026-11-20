import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AlliraClient, AlliraAdminClient, AlliraApiError } from '../dist/index.js';

const session = { authenticated: true, csrfToken: 'csrf-test-value', expiresAt: '2026-01-01T01:00:00Z' };
const client = fetch => new AlliraAdminClient({ baseUrl: 'https://allira.example/api/v1/', fetch });

test('admin login and session refresh cache the latest CSRF, use cookies and never developer Bearer', async () => {
  const calls = [];
  const sdk = client(async (url, init) => {
    calls.push({ url, ...init });
    if (url.endsWith('/session')) return Response.json({ ...session, csrfToken: calls.length === 1 ? 'first-csrf' : 'refreshed-csrf' });
    return Response.json({ ok: true, revoked: 2 });
  });
  const controller = new AbortController();
  const options = { signal: controller.signal };
  assert.equal((await sdk.login({ code: 'single-use-test-code', userId: 'must-not-forward', accessToken: 'must-not-forward' }, options)).authenticated, true);
  await sdk.revokeUserSessions('user /+', options);
  assert.deepEqual(await sdk.getSession(options), { ...session, csrfToken: 'refreshed-csrf' });
  await sdk.deleteConversation('c /+', options);
  assert.deepEqual(calls.map(c => [c.method, c.url]), [
    ['POST', 'https://allira.example/api/v1/admin/session'],
    ['POST', 'https://allira.example/api/v1/admin/users/user%20%2F%2B/revoke-sessions'],
    ['GET', 'https://allira.example/api/v1/admin/session'],
    ['POST', 'https://allira.example/api/v1/admin/conversations/c%20%2F%2B/delete'],
  ]);
  assert.deepEqual(JSON.parse(calls[0].body), { code: 'single-use-test-code' });
  assert.equal(calls[0].headers.get('X-CSRF-Token'), null);
  assert.equal(calls[1].headers.get('X-CSRF-Token'), 'first-csrf');
  assert.equal(calls[2].headers.get('X-CSRF-Token'), null);
  assert.equal(calls[3].headers.get('X-CSRF-Token'), 'refreshed-csrf');
  assert.equal(calls[2].headers.get('Origin'), null);
  for (const index of [0, 1, 3]) assert.equal(calls[index].headers.get('Origin'), 'https://allira.example');
  for (const call of calls) {
    assert.equal(call.credentials, 'same-origin');
    assert.equal(call.headers.get('Authorization'), null);
    assert.equal(call.signal, controller.signal);
  }
});

test('all admin read routes encode filters and pagination while preserving DTOs', async () => {
  const calls = [];
  const overview = { users: 3, conversations: 5, messages: 8, runs: { running: 1, completed: 2, failed: 3, cancelled: 4 }, activeSessions: 6, environment: 'development', provider: 'mock', maxConcurrentRuns: 8 };
  const page = { items: [{ ownerId: 'owner', messageCount: 2 }], nextCursor: 'next' };
  const sdk = client(async (url, init) => {
    calls.push({ url, ...init });
    return Response.json(url.endsWith('/overview') ? overview : page);
  });
  const controller = new AbortController();
  const options = { cursor: 'cursor /+', limit: 2, signal: controller.signal };
  assert.deepEqual(await sdk.overview({ signal: controller.signal }), overview);
  assert.deepEqual(await sdk.listUsers(options), page);
  assert.deepEqual(await sdk.listConversations({ ...options, userId: 'user /+' }), page);
  assert.deepEqual(await sdk.listMessages('c /+', options), page);
  assert.deepEqual(await sdk.listRuns({ ...options, status: 'failed', userId: 'user /+' }), page);
  assert.deepEqual(await sdk.getRun('run /+', { signal: controller.signal }), page);
  assert.deepEqual(await sdk.listAudit(options), page);
  assert.deepEqual(calls.map(c => c.url), [
    'https://allira.example/api/v1/admin/overview',
    'https://allira.example/api/v1/admin/users?cursor=cursor+%2F%2B&limit=2',
    'https://allira.example/api/v1/admin/conversations?cursor=cursor+%2F%2B&limit=2&userId=user+%2F%2B',
    'https://allira.example/api/v1/admin/conversations/c%20%2F%2B/messages?cursor=cursor+%2F%2B&limit=2',
    'https://allira.example/api/v1/admin/runs?cursor=cursor+%2F%2B&limit=2&status=failed&userId=user+%2F%2B',
    'https://allira.example/api/v1/admin/runs/run%20%2F%2B',
    'https://allira.example/api/v1/admin/audit?cursor=cursor+%2F%2B&limit=2',
  ]);
  for (const call of calls) {
    assert.equal(call.method, 'GET');
    assert.equal(call.headers.get('Authorization'), null);
    assert.equal(call.headers.get('X-CSRF-Token'), null);
    assert.equal(call.headers.get('Origin'), null);
    assert.equal(call.credentials, 'same-origin');
    assert.equal(call.body, undefined);
    assert.equal(call.signal, controller.signal);
  }
});

test('admin cancellation and logout are explicit CSRF-protected POSTs and logout clears memory', async () => {
  const calls = [];
  const sdk = client(async (url, init) => { calls.push({ url, ...init }); return Response.json(url.endsWith('/session') ? session : { ok: true }); });
  await sdk.getSession();
  await sdk.cancelRun('run /+');
  await sdk.logout();
  assert.deepEqual(calls.slice(1).map(c => [c.method, c.url]), [
    ['POST', 'https://allira.example/api/v1/admin/runs/run%20%2F%2B/cancel'],
    ['POST', 'https://allira.example/api/v1/admin/logout'],
  ]);
  for (const call of calls.slice(1)) {
    assert.equal(call.headers.get('X-CSRF-Token'), session.csrfToken);
    assert.equal(call.headers.get('Origin'), 'https://allira.example');
    assert.equal(call.body, undefined);
  }
  await assert.rejects(sdk.cancelRun('r'), TypeError);
  assert.equal(calls.length, 3);
});

test('admin writes require login or session restoration without hidden authentication requests', async () => {
  let count = 0;
  const sdk = client(async () => { count++; return Response.json({ ok: true }); });
  for (const write of [() => sdk.logout(), () => sdk.revokeUserSessions('u'), () => sdk.deleteConversation('c'), () => sdk.cancelRun('r')]) {
    await assert.rejects(write(), error => error instanceof TypeError && /login\(\).*getSession\(\)/.test(error.message));
  }
  assert.equal(count, 0);
});

test('admin 401 clears cached CSRF and returns the shared API error', async () => {
  let count = 0;
  const sdk = client(async () => ++count === 1 ? Response.json(session) : Response.json({ error: { code: 'unauthorized', message: 'Admin session required' } }, { status: 401 }));
  await sdk.getSession();
  await assert.rejects(sdk.overview(), error => error instanceof AlliraApiError && error.status === 401 && error.code === 'unauthorized');
  await assert.rejects(sdk.deleteConversation('c'), TypeError);
  assert.equal(count, 2);
});

for (const [status, code] of [[404, 'not_found'], [403, 'csrf_invalid'], [409, 'run_in_progress']]) {
  test(`admin ${status} errors preserve envelopes without retry or code disclosure`, async () => {
    let count = 0;
    const sdk = client(async () => ++count === 1 ? Response.json(session) : Response.json({ error: { code, message: 'Request refused' } }, { status }));
    await sdk.getSession();
    await assert.rejects(sdk.deleteConversation('c'), error => error instanceof AlliraApiError && error.status === status && error.code === code && error.message === 'Request refused');
    assert.equal(count, 2);
  });
}

test('admin non-JSON errors use the same fallback as chat errors', async () => {
  await assert.rejects(client(async () => new Response('not JSON', { status: 502 })).listUsers(), error => error instanceof AlliraApiError && error.code === 'http_error' && error.status === 502);
});

test('admin aborted requests preserve the original reason and never fetch', async () => {
  let count = 0;
  const sdk = client(async () => { count++; return Response.json(session); });
  const controller = new AbortController();
  const reason = new DOMException('Stop', 'AbortError');
  controller.abort(reason);
  for (const request of [() => sdk.login({ code: 'unused' }, { signal: controller.signal }), () => sdk.getSession({ signal: controller.signal }), () => sdk.overview({ signal: controller.signal }), () => sdk.cancelRun('r', { signal: controller.signal })]) {
    await assert.rejects(request(), error => error === reason);
  }
  assert.equal(count, 0);
});

test('admin instances and developer clients cannot share or create one another\'s credentials', async () => {
  const calls = [];
  const fetch = async (url, init) => { calls.push({ url, ...init }); return Response.json(url.endsWith('/admin/session') ? session : { items: [], nextCursor: null }); };
  const admin = client(fetch);
  const separateAdmin = client(fetch);
  const developer = new AlliraClient({ baseUrl: 'https://allira.example', accessToken: 'developer-test-bearer', fetch });
  await admin.getSession();
  await developer.listConversations();
  await assert.rejects(separateAdmin.deleteConversation('c'), TypeError);
  await admin.listUsers();
  assert.equal(calls[1].headers.get('Authorization'), 'Bearer developer-test-bearer');
  assert.equal(calls[1].headers.get('X-CSRF-Token'), null);
  assert.equal(calls[2].headers.get('Authorization'), null);
  assert.equal(calls.length, 3);
});

test('admin base URLs use the same suffix and validation rules as chat', async () => {
  const urls = [];
  const sdk = new AlliraAdminClient({ baseUrl: 'http://localhost:8000/prefix/', fetch: async url => { urls.push(url); return Response.json({}); } });
  await sdk.listUsers();
  await sdk.listConversations();
  await sdk.listRuns();
  assert.deepEqual(urls, ['http://localhost:8000/prefix/api/v1/admin/users', 'http://localhost:8000/prefix/api/v1/admin/conversations', 'http://localhost:8000/prefix/api/v1/admin/runs']);
  for (const baseUrl of ['ftp://example.com', 'https://example.com/?x=1', 'https://example.com/#x']) {
    assert.throws(() => new AlliraAdminClient({ baseUrl }), TypeError);
  }
});
