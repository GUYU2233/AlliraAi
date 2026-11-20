import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AlliraAdminClient, AlliraClient, AlliraSocialClient, AlliraOidcClient, AlliraApiError } from '../dist/index.js';

// Synthetic Fetch fixtures only: no actual API, identity service, model, cookie jar or credentials.
const baseUrl = 'https://allira.example/api/v1';
const time = '2026-11-19T12:00:00Z';
const proof = label => ({ authenticated: true, csrfToken: `fixture-admin-${label}`, expiresAt: time });
const ok = () => Response.json({ ok: true });
const errorResponse = (status, code) => Response.json({ error: { code, message: 'Fixture request refused' } }, { status });
const apiError = (status, code) => error => error instanceof AlliraApiError && error.status === status && error.code === code;
const sdk = (fetch, options = {}) => new AlliraAdminClient({ baseUrl, fetch, ...options });
const deferred = () => { let resolve; let reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function assertCookie(call, method = 'GET', csrfToken) {
  assert.equal(call.method, method);
  assert.equal(call.credentials, 'same-origin');
  assert.equal(call.mode, 'same-origin');
  assert.equal(call.headers.has('Authorization'), false);
  assert.equal(call.headers.has('Cookie'), false);
  assert.equal(call.headers.has('X-Allira-OIDC-CSRF'), false);
  assert.equal(call.headers.has('X-Allira-Social-CSRF'), false);
  assert.equal(call.headers.get('X-CSRF-Token') === (csrfToken ?? null), true);
  assert.equal(call.headers.get('Origin'), method === 'GET' ? null : 'https://allira.example');
}
const oldUser = { userId: 'acct_fixture', conversationCount: 2, runCount: 3, activeSessions: 1 };
const user = { ...oldUser, createdAt: time, accountType: 'registered', username: null, displayName: 'Literal <b>name</b>', identityStatus: 'oidc', maskedPhone: '+86***0000', phoneVerified: true, passwordEnabled: false, walletBalance: 0 };
const device = { id: 'fixture-device', createdAt: time, lastSeenAt: time, expiresAt: time, label: 'Browser <b>literal</b>', type: 'browser' };
const routeOverview = { items: [{ mode: 'economy', provider: 'mock', configured: true, energyCost: 1, fallbackTo: null }], limits: { runTimeoutSeconds: 90, maxConcurrentRuns: 3, maxPromptCharacters: 20000, maxOutputCharacters: 12000, upstreamMaxTokens: 256, runRatePerMinute: 20 }, configurationOnly: true };
const oldDiagnostics = { version: '0.4.0', startedAt: time, uptimeSeconds: 10, schemaVersion: 4, database: { ok: true, journalMode: 'wal', databaseBytes: 4096, walBytes: 0 }, runs: { running: 0, inFlight: 0, closing: 0, capacity: 3 }, sessions: { bearer: 1, browser: 1, admin: 1 }, backup: { configured: false, latestAt: null, ageSeconds: null, status: 'missing' }, warnings: [] };
const diagnostics = { ...oldDiagnostics, identity: { backend: 'oidc', configured: true, enabled: false, simulated: false, linkedAccounts: 2, legacyAccounts: 1, developerUsers: 3, pendingFlows: 0, serviceProbe: 'not_performed', administration: 'separate' }, identityBackup: { configured: true, latestAt: time, ageSeconds: 30, status: 'recent', databaseBytes: 8192, restoreStatus: 'recorded', restoreVerifiedAt: time, checksumVerified: false } };

for (const identity of ['all', 'oidc', 'legacy', 'developer']) {
  test(`admin account filters preserve literal query, opaque cursor and ${identity} identity`, async () => {
    const { signal } = new AbortController();
    let calls = 0;
    const admin = sdk(async (url, init) => {
      calls++;
      assert.equal(url, `${baseUrl}/admin/users?cursor=opaque+%2F%2B%25&limit=2&q=a_%25%2B%2F+%E5%90%8D&identity=${identity}`);
      assertCookie(init);
      assert.equal(init.body, undefined);
      assert.equal(init.signal, signal);
      return Response.json({ items: [user], nextCursor: 'unchanged /+%' });
    });
    assert.deepEqual(await admin.listUsers({ q: 'a_%+/ 名', identity, cursor: 'opaque /+%', limit: 2, signal, phone: 'must-not-forward', accessToken: 'must-not-forward' }), { items: [user], nextCursor: 'unchanged /+%' });
    assert.equal(calls, 1);
  });
}

test('admin old user pages and omitted filters remain unchanged, not invented identity defaults', async () => {
  const urls = [];
  const admin = sdk(async url => { urls.push(url); return Response.json({ items: [oldUser], nextCursor: null }); });
  const result = await admin.listUsers();
  assert.deepEqual(result, { items: [oldUser], nextCursor: null });
  for (const key of ['accountType', 'createdAt', 'identityStatus', 'phoneVerified', 'walletBalance']) assert.equal(Object.hasOwn(result.items[0], key), false);
  await admin.listUsers({ q: '', identity: 'all' });
  assert.deepEqual(urls, [`${baseUrl}/admin/users`, `${baseUrl}/admin/users?q=&identity=all`]);
});

test('admin device pages escape owner IDs and pagination without fetching identity or credentials', async () => {
  const { signal } = new AbortController();
  let calls = 0;
  const admin = sdk(async (url, init) => {
    calls++;
    assert.equal(url, `${baseUrl}/admin/users/owner%20%2F%2B/sessions?cursor=opaque+%2F%2B&limit=1`);
    assertCookie(init);
    assert.equal(init.signal, signal);
    assert.equal(init.body, undefined);
    return Response.json({ items: [device], nextCursor: 'next /+' });
  });
  assert.deepEqual(await admin.listUserSessions('owner /+', { cursor: 'opaque /+', limit: 1, signal }), { items: [device], nextCursor: 'next /+' });
  assert.equal(calls, 1);
});

for (const revoked of [0, 1]) {
  test(`admin owner-scoped single device revoke preserves revoked=${revoked} and uses only captured admin CSRF`, async () => {
    const calls = [];
    const { signal } = new AbortController();
    const session = proof('revoke');
    const admin = sdk(async (url, init) => { calls.push({ url, ...init }); return Response.json(url.endsWith('/admin/session') ? session : { ok: true, revoked }); }, { accessToken: () => assert.fail('must not read Bearer') });
    await admin.getSession();
    assert.deepEqual(await admin.revokeUserSession('owner /+', 'device /+', { signal, accessToken: 'ignored', csrfToken: 'ignored', code: 'ignored' }), { ok: true, revoked });
    assert.equal(calls[1].url, `${baseUrl}/admin/users/owner%20%2F%2B/sessions/device%20%2F%2B/revoke`);
    assertCookie(calls[1], 'POST', session.csrfToken);
    assert.equal(calls[1].body, undefined);
    assert.equal(calls[1].signal, signal);
    assert.equal(calls.length, 2);
  });
}

test('admin single device revoke requires explicit initialization, with no hidden session GET', async () => {
  let calls = 0;
  const admin = sdk(async () => { calls++; return ok(); });
  await assert.rejects(admin.revokeUserSession('owner', 'device'), TypeError);
  assert.equal(calls, 0);
});

test('admin routes are a single configuration-only read with existing public ModelRoute shape, never a probe', async () => {
  const { signal } = new AbortController();
  let calls = 0;
  const admin = sdk(async (url, init) => {
    calls++;
    assert.equal(url, `${baseUrl}/admin/routes`);
    assertCookie(init);
    assert.equal(init.signal, signal);
    assert.equal(init.body, undefined);
    return Response.json(routeOverview);
  });
  assert.deepEqual(await admin.getRoutes({ signal, probe: true, accessToken: 'ignored' }), routeOverview);
  assert.equal(calls, 1);
});

for (const value of [oldDiagnostics, diagnostics, { ...diagnostics, identityBackup: { ...diagnostics.identityBackup, latestAt: null, ageSeconds: null, databaseBytes: null, restoreStatus: 'not_reported', restoreVerifiedAt: null, status: 'missing' } }]) {
  test(`admin diagnostics preserves ${value.identity ? value.identityBackup.restoreStatus : 'old missing identity'} metadata without claiming verification`, async () => {
    let calls = 0;
    const admin = sdk(async (url, init) => { calls++; assert.equal(url, `${baseUrl}/admin/diagnostics`); assertCookie(init); return Response.json(value); });
    const result = await admin.getDiagnostics();
    assert.deepEqual(result, value);
    if (!value.identity) { assert.equal(Object.hasOwn(result, 'identity'), false); assert.equal(Object.hasOwn(result, 'identityBackup'), false); }
    assert.equal(calls, 1);
  });
}

for (const [status, code, invoke] of [
  [400, 'invalid_cursor', admin => admin.listUsers({ q: 'changed', cursor: 'old' })],
  [400, 'invalid_cursor', admin => admin.listUserSessions('owner', { cursor: 'bad' })],
  [401, 'unauthorized', admin => admin.listUserSessions('owner')],
  [403, 'csrf_forbidden', admin => admin.revokeUserSession('owner', 'device')],
  [404, 'not_found', admin => admin.getRoutes()],
  [404, 'not_found', admin => admin.getDiagnostics()],
  [409, 'fixture_conflict', admin => admin.revokeUserSession('owner', 'device')],
  [429, 'rate_limited', admin => admin.listUsers({ identity: 'oidc' })],
  [503, 'fixture_unavailable', admin => admin.getDiagnostics()],
]) {
  test(`admin control ${status} ${code} remains a typed error, never an empty/default result or automatic retry`, async () => {
    let calls = 0;
    const admin = sdk(async () => ++calls === 1 ? Response.json(proof('errors')) : errorResponse(status, code));
    await admin.getSession();
    await assert.rejects(invoke(admin), apiError(status, code));
    assert.equal(calls, 2);
  });
}

test('admin clear is local, invalidates pending GET and requires explicit restoration for future writes', async () => {
  const waiting = deferred();
  let calls = 0;
  const admin = sdk(async () => { calls++; return waiting.promise; });
  const pending = admin.getSession();
  assert.equal(admin.clear(), undefined);
  waiting.resolve(Response.json(proof('obsolete')));
  assert.equal((await pending).authenticated, true);
  await assert.rejects(admin.revokeUserSession('owner', 'device'), TypeError);
  assert.equal(calls, 1);
});

for (const first of ['GET', 'POST']) {
  for (const second of ['GET', 'POST']) {
    test(`late admin ${first} session response cannot overwrite newer ${second} restoration/login`, async () => {
      const waiting = deferred();
      const calls = [];
      const latest = proof('newer');
      const admin = sdk(async (url, init) => { calls.push({ url, ...init }); if (calls.length === 1) return waiting.promise; return Response.json(url.endsWith('/admin/session') ? latest : { ok: true, revoked: 1 }); });
      const older = first === 'GET' ? admin.getSession() : admin.login({ code: 'fixture-old-code' });
      await (second === 'GET' ? admin.getSession() : admin.login({ code: 'fixture-new-code' }));
      waiting.resolve(Response.json(proof('older')));
      await older;
      await admin.revokeUserSession('owner', 'device');
      assertCookie(calls.at(-1), 'POST', latest.csrfToken);
      assert.equal(calls.length, 3);
    });
  }
}

test('starting a new admin login immediately drops old write authorization without fetching any hidden exchange', async () => {
  const waiting = deferred();
  let calls = 0;
  const latest = proof('latest-login');
  const admin = sdk(async (url) => { calls++; if (calls === 1) return Response.json(proof('old-login')); if (calls === 2) return waiting.promise; return ok(); });
  await admin.getSession();
  const login = admin.login({ code: 'fixture-new-code' });
  await assert.rejects(admin.revokeUserSession('owner', 'device'), TypeError);
  assert.equal(calls, 2);
  waiting.resolve(Response.json(latest));
  await login;
  await admin.revokeUserSession('owner', 'device');
  assert.equal(calls, 3);
});

for (const status of [200, 401, 403]) {
  test(`admin logout invalidates at invocation and its late ${status} result cannot clear a newer login`, async () => {
    const waiting = deferred();
    const calls = [];
    const old = proof('logout-old');
    const latest = proof('logout-new');
    const admin = sdk(async (url, init) => { calls.push({ url, ...init }); if (url.endsWith('/admin/logout')) return waiting.promise; return Response.json(url.endsWith('/admin/session') ? (calls.length === 1 ? old : latest) : { ok: true, revoked: 1 }); });
    await admin.getSession();
    const logout = admin.logout();
    const observed = status === 200 ? logout : assert.rejects(logout, apiError(status, status === 401 ? 'unauthorized' : 'csrf_forbidden'));
    assertCookie(calls[1], 'POST', old.csrfToken);
    await assert.rejects(admin.revokeUserSession('owner', 'device'), TypeError);
    await admin.login({ code: 'fixture-new-code' });
    waiting.resolve(status === 200 ? ok() : errorResponse(status, status === 401 ? 'unauthorized' : 'csrf_forbidden'));
    await observed;
    await admin.revokeUserSession('owner', 'device');
    assertCookie(calls.at(-1), 'POST', latest.csrfToken);
    assert.equal(calls.length, 4);
  });
}

test('a refresh started before logout cannot restore admin CSRF after logout has begun', async () => {
  const refresh = deferred();
  const logoutResponse = deferred();
  const calls = [];
  const old = proof('before-logout');
  const admin = sdk(async (url, init) => { calls.push({ url, ...init }); if (url.endsWith('/admin/logout')) return logoutResponse.promise; return calls.length === 1 ? Response.json(old) : refresh.promise; });
  await admin.getSession();
  const restoring = admin.getSession();
  const logout = admin.logout();
  assertCookie(calls[2], 'POST', old.csrfToken);
  refresh.resolve(Response.json(old));
  await restoring;
  await assert.rejects(admin.revokeUserSession('owner', 'device'), TypeError);
  logoutResponse.resolve(ok());
  await logout;
  assert.equal(calls.length, 3);
});

for (const next of ['GET', 'POST']) {
  test(`an old admin read 401 cannot invalidate a newer ${next} session`, async () => {
    const waiting = deferred();
    const calls = [];
    const latest = proof('after-old-401');
    const admin = sdk(async (url, init) => { calls.push({ url, ...init }); if (url.endsWith('/admin/routes')) return waiting.promise; return Response.json(url.endsWith('/admin/session') ? latest : { ok: true, revoked: 1 }); });
    await admin.getSession();
    const read = admin.getRoutes();
    const refused = assert.rejects(read, apiError(401, 'unauthorized'));
    await (next === 'GET' ? admin.getSession() : admin.login({ code: 'fixture-new-code' }));
    waiting.resolve(errorResponse(401, 'unauthorized'));
    await refused;
    await admin.revokeUserSession('owner', 'device');
    assertCookie(calls.at(-1), 'POST', latest.csrfToken);
  });
}

test('current admin 401 clears memory and an explicit new restore is required without hidden fallback', async () => {
  let calls = 0;
  const admin = sdk(async () => ++calls === 1 ? Response.json(proof('current')) : errorResponse(401, 'unauthorized'));
  await admin.getSession();
  await assert.rejects(admin.getRoutes(), apiError(401, 'unauthorized'));
  await assert.rejects(admin.revokeUserSession('owner', 'device'), TypeError);
  assert.equal(calls, 2);
});

test('failed admin logout stays locally invalidated instead of resurrecting its old CSRF', async () => {
  let calls = 0;
  const admin = sdk(async () => ++calls === 1 ? Response.json(proof('failed-logout')) : errorResponse(403, 'csrf_forbidden'));
  await admin.getSession();
  await assert.rejects(admin.logout(), apiError(403, 'csrf_forbidden'));
  await assert.rejects(admin.revokeUserSession('owner', 'device'), TypeError);
  assert.equal(calls, 2);
});

test('pre-aborted admin controls never fetch, while invoking logout still invalidates cached authorization', async () => {
  let calls = 0;
  const admin = sdk(async () => { calls++; return Response.json(proof('aborts')); });
  await admin.getSession();
  const controller = new AbortController();
  const reason = new DOMException('Fixture abort', 'AbortError');
  controller.abort(reason);
  const options = { signal: controller.signal };
  for (const invoke of [() => admin.listUsers({ ...options, q: 'q' }), () => admin.listUserSessions('owner', options), () => admin.revokeUserSession('owner', 'device', options), () => admin.getRoutes(options), () => admin.getDiagnostics(options), () => admin.login({ code: 'fixture-code' }, options), () => admin.getSession(options), () => admin.logout(options)]) await assert.rejects(invoke(), error => error === reason);
  await assert.rejects(admin.revokeUserSession('owner', 'device'), TypeError);
  assert.equal(calls, 1);
});

for (const method of ['GET', 'POST']) {
  test(`an aborted parsed admin ${method} initialization cannot cache its CSRF`, async () => {
    const controller = new AbortController();
    const reason = new DOMException('Fixture parse abort', 'AbortError');
    let calls = 0;
    const admin = sdk(async () => { calls++; return { ok: true, status: 200, json: async () => { controller.abort(reason); return proof('parse-abort'); } }; });
    const result = method === 'GET' ? admin.getSession({ signal: controller.signal }) : admin.login({ code: 'fixture-code' }, { signal: controller.signal });
    await assert.rejects(result, error => error === reason);
    await assert.rejects(admin.revokeUserSession('owner', 'device'), TypeError);
    assert.equal(calls, 1);
  });
}

for (const invoke of [admin => admin.getRoutes(), admin => admin.revokeUserSession('owner', 'device')]) {
  test('admin controls preserve transport exception identity and never retry a failed request', async () => {
    const transportError = new TypeError('Fixture transport failure');
    let calls = 0;
    const admin = sdk(async () => ++calls === 1 ? Response.json(proof('transport')) : Promise.reject(transportError));
    await admin.getSession();
    await assert.rejects(invoke(admin), error => error === transportError);
    assert.equal(calls, 2);
  });
}

for (const method of ['GET', 'POST']) {
  test(`an in-flight aborted admin ${method} preserves the original signal reason and never retries`, async () => {
    const controller = new AbortController();
    const reason = new DOMException('Fixture in-flight abort', 'AbortError');
    let calls = 0;
    const admin = sdk(async (url, init) => {
      calls++;
      if (calls === 1) return Response.json(proof('in-flight'));
      return new Promise((resolve, reject) => init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true }));
    });
    await admin.getSession();
    const pending = method === 'GET' ? admin.listUserSessions('owner', { signal: controller.signal }) : admin.revokeUserSession('owner', 'device', { signal: controller.signal });
    const observed = assert.rejects(pending, error => error === reason);
    controller.abort(reason);
    await observed;
    assert.equal(calls, 2);
  });
}

test('an old admin login 401 cannot clear a later explicit restoration', async () => {
  const waiting = deferred();
  const calls = [];
  const latest = proof('restored-after-login');
  const admin = sdk(async (url, init) => { calls.push({ url, ...init }); if (calls.length === 1) return waiting.promise; return Response.json(url.endsWith('/admin/session') ? latest : { ok: true, revoked: 1 }); });
  const login = admin.login({ code: 'fixture-obsolete-code' });
  const observed = assert.rejects(login, apiError(401, 'unauthorized'));
  await admin.getSession();
  waiting.resolve(errorResponse(401, 'unauthorized'));
  await observed;
  await admin.revokeUserSession('owner', 'device');
  assertCookie(calls.at(-1), 'POST', latest.csrfToken);
});

test('a late admin login result cannot authorize writes after local clear', async () => {
  const waiting = deferred();
  let calls = 0;
  const admin = sdk(async () => { calls++; return waiting.promise; });
  const login = admin.login({ code: 'fixture-obsolete-code' });
  admin.clear();
  waiting.resolve(Response.json(proof('cleared-login')));
  await login;
  await assert.rejects(admin.revokeUserSession('owner', 'device'), TypeError);
  assert.equal(calls, 1);
});

test('admin authorization and clear stay isolated from ordinary, social, OIDC and other admin instances', async () => {
  const calls = [];
  const fetch = async (url, init) => { calls.push({ url, ...init }); return Response.json(url.endsWith('/admin/session') ? proof('isolated') : { ok: true, revoked: 1 }); };
  const admin = sdk(fetch, { accessToken: () => assert.fail('must not read injected token'), csrfToken: 'must-not-use' });
  const other = sdk(fetch);
  const ordinary = new AlliraClient({ baseUrl, accessToken: 'fixture-ordinary', fetch });
  const social = new AlliraSocialClient({ baseUrl, fetch });
  const oidc = new AlliraOidcClient({ baseUrl, fetch });
  await admin.getSession();
  await assert.rejects(other.revokeUserSession('owner', 'device'), TypeError);
  await assert.rejects(social.start('wechat'), TypeError);
  await assert.rejects(oidc.start(), TypeError);
  await ordinary.listConversations();
  ordinary.clearBrowserSession(); social.clearSession(); oidc.clear(); other.clear();
  await admin.revokeUserSession('owner', 'device');
  assertCookie(calls.at(-1), 'POST', proof('isolated').csrfToken);
  assert.equal(calls[1].headers.get('Authorization') === 'Bearer fixture-ordinary', true);
  admin.clear();
  await assert.rejects(admin.revokeUserSession('owner', 'device'), TypeError);
  assert.equal(calls.length, 3);
});
