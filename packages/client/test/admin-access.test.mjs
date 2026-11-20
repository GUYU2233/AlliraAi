import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AlliraAdminClient, AlliraApiError } from '../dist/index.js';

// Synthetic Fetch only: no Cloudflare, API, cookie jar, assertion, browser or real credentials.
const baseUrl = 'https://allira.example/api/v1';
const stamp = '2026-11-19T12:00:00Z';
const accessSession = csrfToken => ({ authenticated: true, csrfToken, expiresAt: stamp, authentication: 'cloudflare-access', identity: { email: '<b>admin</b>@example.invalid' }, logoutUrl: '/cdn-cgi/access/logout' });
const denied = status => Response.json({ error: { code: 'fixture_denied', message: 'Fixture refusal' } }, { status });
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const sdk = fetch => new AlliraAdminClient({ baseUrl, fetch, accessToken: () => assert.fail('ordinary Bearer must not be read') });
function transport(init, method, csrfToken) {
  assert.equal(init.method, method);
  assert.equal(init.credentials, 'same-origin');
  assert.equal(init.mode, 'same-origin');
  assert.equal(init.headers.get('Accept'), 'application/json');
  for (const name of ['Authorization', 'Cookie', 'Cf-Access-Jwt-Assertion', 'Cf-Access-Authenticated-User-Email', 'X-Allira-OIDC-CSRF', 'X-Allira-Social-CSRF']) assert.equal(init.headers.has(name), false);
  assert.equal(init.headers.get('Origin'), method === 'GET' ? null : 'https://allira.example');
  assert.equal(init.headers.get('X-CSRF-Token'), csrfToken ?? null);
  assert.equal(init.body, undefined);
}

test('Access GET initializes independent CSRF and preserves optional identity metadata without code exchange', async () => {
  const calls = [], session = accessSession('fixture-access-csrf');
  const admin = sdk(async (url, init) => { calls.push({ url, init }); return Response.json(url.endsWith('/admin/session') ? session : { ok: true, revoked: 1 }); });
  assert.deepEqual(await admin.getSession({ code: 'ignored', assertion: 'ignored', accessToken: 'ignored' }), session);
  assert.deepEqual(await admin.revokeUserSession('owner', 'device'), { ok: true, revoked: 1 });
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, `${baseUrl}/admin/session`); transport(calls[0].init, 'GET');
  assert.equal(calls[1].url, `${baseUrl}/admin/users/owner/sessions/device/revoke`); transport(calls[1].init, 'POST', session.csrfToken);
});

test('old three-field AdminSession remains unchanged without fabricated Access identity or logout metadata', async () => {
  const session = { authenticated: true, csrfToken: 'fixture-legacy-csrf', expiresAt: stamp };
  const admin = sdk(async (_url, init) => { transport(init, 'GET'); return Response.json(session); });
  const result = await admin.getSession();
  assert.deepEqual(result, session);
  for (const name of ['authentication', 'identity', 'logoutUrl']) assert.equal(Object.hasOwn(result, name), false);
});

test('logout returns only server result, captures CSRF before immediate local invalidation and makes one POST', async () => {
  const ending = deferred(), calls = [], session = accessSession('fixture-before-logout');
  const admin = sdk(async (url, init) => { calls.push({ url, init }); return url.endsWith('/admin/session') ? Response.json(session) : ending.promise; });
  await admin.getSession();
  const logout = admin.logout();
  await assert.rejects(admin.revokeUserSessions('owner'), TypeError);
  assert.equal(calls.length, 2); transport(calls[1].init, 'POST', session.csrfToken);
  assert.equal(calls[1].url, `${baseUrl}/admin/logout`);
  ending.resolve(Response.json({ ok: true }));
  assert.deepEqual(await logout, { ok: true });
  assert.equal(calls.length, 2);
});

test('logout without an explicit GET has no hidden Access initialization or HTTP request', async () => {
  let calls = 0;
  const admin = sdk(async () => { ++calls; return Response.json({ ok: true }); });
  await assert.rejects(admin.logout(), TypeError);
  assert.equal(calls, 0);
});

for (const status of [401, 403, 429, 503]) {
  test(`Access GET ${status} has no code fallback, implicit retry or redirect`, async () => {
    const calls = [];
    const admin = sdk(async (url, init) => { calls.push({ url, init }); return denied(status); });
    await assert.rejects(admin.getSession(), error => error instanceof AlliraApiError && error.status === status);
    assert.equal(calls.length, 1); assert.equal(calls[0].url, `${baseUrl}/admin/session`); transport(calls[0].init, 'GET');
    await assert.rejects(admin.revokeUserSessions('owner'), TypeError);
    assert.equal(calls.length, 1);
  });
}

test('a failed Access GET makes one request and never attempts legacy login', async () => {
  const failure = new TypeError('Synthetic transport failure'); let calls = 0;
  const admin = sdk(async (_url, init) => { ++calls; transport(init, 'GET'); throw failure; });
  await assert.rejects(admin.getSession(), error => error === failure);
  assert.equal(calls, 1);
});

test('uncertain logout is not retried and cannot retain write authorization', async () => {
  let calls = 0;
  const failure = new TypeError('Synthetic transport failure');
  const admin = sdk(async (_url, init) => { ++calls; if (calls === 1) return Response.json(accessSession('fixture-old')); transport(init, 'POST', 'fixture-old'); throw failure; });
  await admin.getSession(); await assert.rejects(admin.logout(), error => error === failure);
  await assert.rejects(admin.revokeUserSessions('owner'), TypeError);
  assert.equal(calls, 2);
});

test('late Access GET cannot overwrite the newest GET CSRF', async () => {
  const older = deferred(); let reads = 0;
  const admin = sdk(async (url, init) => {
    if (url.endsWith('/admin/session')) return ++reads === 1 ? older.promise : Response.json(accessSession('fixture-new'));
    transport(init, 'POST', 'fixture-new'); return Response.json({ ok: true, revoked: 1 });
  });
  const first = admin.getSession(); await admin.getSession(); older.resolve(Response.json(accessSession('fixture-old'))); await first;
  await admin.revokeUserSessions('owner');
  assert.equal(reads, 2);
});

test('local clear invalidates an in-flight Access GET without adding a server request', async () => {
  const pending = deferred(); let calls = 0;
  const admin = sdk(async () => { ++calls; return pending.promise; });
  const reading = admin.getSession(); admin.clear(); pending.resolve(Response.json(accessSession('fixture-obsolete'))); await reading;
  await assert.rejects(admin.revokeUserSessions('owner'), TypeError);
  assert.equal(calls, 1);
});

test('a late logout 401 cannot clear a newly initialized Access CSRF', async () => {
  const ending = deferred(); let reads = 0;
  const admin = sdk(async (url, init) => {
    if (url.endsWith('/admin/session')) return Response.json(accessSession(++reads === 1 ? 'fixture-old' : 'fixture-current'));
    if (url.endsWith('/admin/logout')) { transport(init, 'POST', 'fixture-old'); return ending.promise; }
    transport(init, 'POST', 'fixture-current'); return Response.json({ ok: true, revoked: 1 });
  });
  await admin.getSession(); const logout = admin.logout(); const rejected = assert.rejects(logout, error => error instanceof AlliraApiError && error.status === 401);
  await admin.getSession(); ending.resolve(denied(401)); await rejected;
  await admin.revokeUserSessions('owner');
});

test('current Access 401 clears write authorization and does not exchange the old code', async () => {
  let calls = 0;
  const admin = sdk(async () => ++calls === 1 ? Response.json(accessSession('fixture-current')) : denied(401));
  await admin.getSession(); await assert.rejects(admin.listUsers(), error => error instanceof AlliraApiError && error.status === 401);
  await assert.rejects(admin.revokeUserSessions('owner'), TypeError);
  assert.equal(calls, 2);
});

test('an aborted Access GET is not issued or retried', async () => {
  let calls = 0; const controller = new AbortController(); const reason = new Error('Fixture abort'); controller.abort(reason);
  const admin = sdk(async () => { ++calls; return Response.json(accessSession('unused')); });
  await assert.rejects(admin.getSession({ signal: controller.signal }), error => error === reason);
  assert.equal(calls, 0);
});

test('explicit legacy login remains available to test-only servers but is never attempted by Access GET', async () => {
  const calls = [];
  const admin = sdk(async (url, init) => { calls.push({ url, init }); return Response.json(accessSession('fixture-test-only')); });
  await admin.login({ code: 'fixture-test-only-code' });
  assert.equal(calls.length, 1); assert.equal(calls[0].url, `${baseUrl}/admin/session`);
  assert.equal(calls[0].init.method, 'POST'); assert.equal(calls[0].init.credentials, 'same-origin');
  assert.equal(calls[0].init.headers.get('Origin'), 'https://allira.example'); assert.equal(calls[0].init.headers.has('X-CSRF-Token'), false);
  assert.deepEqual(JSON.parse(calls[0].init.body), { code: 'fixture-test-only-code' });
});
