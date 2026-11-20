import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isDeepStrictEqual } from 'node:util';
import { AlliraAdminClient, AlliraApiError, AlliraClient, AlliraOidcClient, AlliraSocialClient } from '../dist/index.js';

// Backend Fetch fixtures only: not an issuer, real Logto, callback, SMS or browser acceptance test.
const baseUrl = 'https://allira.example:8443/api/v1';
const origin = new URL(baseUrl).origin;
const browserCsrfToken = 'fixture-ordinary-browser-csrf';
const stamp = '2026-11-19T10:00:00Z';
const pending = { provider: 'logto', maskedPhone: '+86 138****0000', expiresAt: stamp, purpose: 'login' };
const session = { csrfToken: 'fixture-oidc-csrf', enabled: true, simulated: false, pending };
const replacement = { ...session, csrfToken: 'fixture-new-oidc-csrf', pending: { ...pending, purpose: 'erase' } };
const user = { id: 'account-oidc', username: 'internal_fixture', displayName: 'Fixture', createdAt: stamp, termsVersion: '2026-11-19', isAdult: true };
const account = { accessToken: 'fixture-oidc-account', tokenType: 'Bearer', user };
const confirmation = { acceptedTermsVersion: '2026-11-19', isAdult: true, confirmIdentity: true };
const migration = { ...confirmation, username: 'old_fixture', currentPassword: 'fixture-old-password' };
const authorization = { authorizationUrl: 'https://identity.example/oidc/auth?state=fixture-only' };
const same = (actual, expected) => assert.equal(isDeepStrictEqual(actual, expected), true, 'private fixture values match without logging them');
const errorResponse = (status, code) => Response.json({ error: { code, message: 'Synthetic OIDC refusal' } }, { status });
const apiError = (status, code) => error => error instanceof AlliraApiError && error.status === status && error.code === code;
const beforeGet = error => error instanceof TypeError && error.message === 'Call OIDC getSession() before a write request';
function recording(responses, options = {}) {
  const calls = [];
  const client = new AlliraOidcClient({ baseUrl, ...options, fetch: async (url, init) => {
    calls.push({ url, ...init });
    const result = responses[calls.length - 1];
    return result instanceof Response ? result : Response.json(result);
  } });
  return { client, calls };
}
function assertCookie(call, csrfToken = session.csrfToken) {
  assert.equal(call.credentials, 'same-origin');
  assert.equal(call.mode, 'same-origin');
  assert.equal(call.headers.get('Accept'), 'application/json');
  for (const name of ['Authorization', 'Cookie']) assert.equal(call.headers.has(name), false);
  const erase = call.method === 'POST' && (call.url.endsWith('/erase') ||
    (call.url.endsWith('/start') && JSON.parse(call.body).purpose === 'erase'));
  assert.equal(call.headers.get('X-CSRF-Token') === browserCsrfToken, erase);
  if (!erase) assert.equal(call.headers.has('X-CSRF-Token'), false);
  assert.equal(call.headers.get('X-Allira-OIDC-CSRF') === csrfToken, call.method === 'POST');
  assert.equal(call.headers.get('Origin'), call.method === 'POST' ? origin : null);
}
function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}
const writes = [
  { name: 'start default', path: '/start', body: {}, result: authorization, call: (client, opts) => client.start(undefined, opts) },
  { name: 'start login', path: '/start', body: { purpose: 'login' }, result: authorization, call: (client, opts) => client.start({ purpose: 'login', authorizationUrl: 'ignore', issuer: 'ignore', accessToken: 'ignore' }, opts) },
  { name: 'start erase', path: '/start', body: { purpose: 'erase' }, result: authorization, call: (client, opts) => client.start({ purpose: 'erase', userId: 'ignore', sessionToken: 'ignore' }, { ...opts, browserCsrfToken }) },
  { name: 'complete', path: '/complete', body: confirmation, result: account, ending: true, call: (client, opts) => client.complete({ ...confirmation, phone: 'ignore', subject: 'ignore', accessToken: 'ignore' }, opts) },
  { name: 'migrate', path: '/migrate', body: migration, result: account, ending: true, call: (client, opts) => client.migrate({ ...migration, userId: 'ignore', provider: 'ignore', newPassword: 'ignore' }, opts) },
  { name: 'erase', path: '/erase', body: { confirmation: 'DELETE MY ACCOUNT' }, result: { ok: true }, ending: true, call: (client, opts) => client.erase({ confirmation: 'DELETE MY ACCOUNT', currentPassword: 'ignore', userId: 'ignore' }, { ...opts, browserCsrfToken }) },
  { name: 'cancel', path: '/cancel', body: {}, result: { ok: true }, ending: true, call: (client, opts) => client.cancel(opts) },
];

test('OIDC GET restores only transition metadata and independent cookie CSRF', async () => {
  const empty = { ...session, pending: null };
  const { client, calls } = recording([empty]);
  const { signal } = new AbortController();
  same(await client.getSession({ signal }), empty);
  assert.equal(calls.length, 1);
  const [call] = calls;
  assert.equal(call.url, `${baseUrl}/auth/oidc/session`);
  assert.equal(call.method, 'GET');
  assert.equal(call.signal, signal);
  assert.equal(call.body, undefined);
  assert.equal(call.headers.has('Content-Type'), false);
  assertCookie(call);
});

for (const operation of writes) {
  test(`OIDC ${operation.name} uses its exact route, allowlisted body, signal and independent CSRF`, async () => {
    const { client, calls } = recording([session, operation.result]);
    const { signal } = new AbortController();
    await client.getSession();
    same(await operation.call(client, { signal }), operation.result);
    assert.equal(calls.length, 2);
    const call = calls[1];
    assert.equal(call.url, `${baseUrl}/auth/oidc${operation.path}`);
    assert.equal(call.method, 'POST');
    assert.equal(call.signal, signal);
    assert.equal(call.headers.get('Content-Type'), 'application/json');
    same(JSON.parse(call.body), operation.body);
    assertCookie(call);
  });
}

test('all OIDC writes reject locally before explicit GET and never bootstrap implicitly', async () => {
  const { client, calls } = recording([]);
  for (const operation of writes) await assert.rejects(operation.call(client), beforeGet);
  assert.equal(calls.length, 0);
});

for (const name of ['start erase', 'erase']) {
  test(`OIDC ${name} requires explicit ordinary browser CSRF with no hidden GET or POST`, async () => {
    const { client, calls } = recording([session, authorization]);
    await client.getSession();
    const input = name === 'start erase' ? { purpose: 'erase' } : { confirmation: 'DELETE MY ACCOUNT' };
    const call = options => name === 'start erase' ? client.start(input, options) : client.erase(input, options);
    for (const browserCsrfToken of [undefined, '', '   ', null, 123]) {
      await assert.rejects(call({ browserCsrfToken }), error => error instanceof TypeError && error.message === 'OIDC erase requires an explicit browserCsrfToken from getBrowserSession()');
    }
    await assert.rejects(call(undefined), error => error instanceof TypeError && error.message === 'OIDC erase requires an explicit browserCsrfToken from getBrowserSession()');
    assert.equal(calls.length, 1);
    await client.start(); // Missing ordinary proof did not consume independent transition CSRF.
    assertCookie(calls[1]);
  });
}

test('ordinary CSRF is forwarded only by explicit erase calls and is never cached or reused for login writes', async () => {
  const { client, calls } = recording([session, authorization, authorization, authorization]);
  await client.getSession();
  await client.start({ purpose: 'erase' }, { browserCsrfToken });
  await assert.rejects(client.erase({ confirmation: 'DELETE MY ACCOUNT' }), error => error instanceof TypeError && error.message === 'OIDC erase requires an explicit browserCsrfToken from getBrowserSession()');
  await client.start({ purpose: 'login' }, { browserCsrfToken, accessToken: 'ignore' });
  await client.start();
  assert.equal(calls.length, 4);
  for (const call of calls) assertCookie(call);
});

test('login completion, migration and cancel ignore arbitrary ordinary CSRF and Authorization request options', async () => {
  for (const operation of writes.filter(item => !['start erase', 'erase'].includes(item.name))) {
    const { client, calls } = recording([session, operation.result]);
    await client.getSession();
    await operation.call(client, { browserCsrfToken, accessToken: 'ignore', headers: { Authorization: 'Bearer ignore', 'X-CSRF-Token': 'ignore' } });
    assertCookie(calls[1]);
    assert.equal(calls[1].headers.has('Authorization'), false);
    assert.equal(calls[1].headers.has('X-CSRF-Token'), false);
  }
});

for (const authorization of ['Bearer fixture-ordinary', 'Basic invalid', 'not-a-bearer']) {
  test(`fresh erase ignores injected Authorization ${authorization.split(' ')[0]} without switching credentials`, async () => {
    for (const operation of writes.filter(item => ['start erase', 'erase'].includes(item.name))) {
      const { client, calls } = recording([session, operation.result], { accessToken: authorization });
      await client.getSession();
      await operation.call(client, { headers: { Authorization: authorization } });
      assertCookie(calls[1]);
      assert.equal(calls[1].headers.has('Authorization'), false);
    }
  });
}

test('old pending without purpose becomes login, never erase, and no raw pending extras escape the parser', async () => {
  const { purpose, ...oldPending } = pending;
  const { client } = recording([{ ...session, pending: { ...oldPending, accessToken: 'ignore', subject: 'ignore' } }]);
  same(await client.getSession(), session);
});

test('erase-purpose pending remains explicit and still does not become an ordinary account session', async () => {
  const { client } = recording([replacement]);
  const result = await client.getSession();
  same(result, replacement);
  assert.equal('accessToken' in result, false);
  assert.equal('user' in result, false);
});

for (const [name, invalid] of [
  ['null response', null], ['array response', []], ['missing CSRF', { ...session, csrfToken: undefined }],
  ['empty CSRF', { ...session, csrfToken: '' }], ['disabled success DTO', { ...session, enabled: false }],
  ['nonboolean simulation', { ...session, simulated: 'false' }], ['missing pending', { ...session, pending: undefined }],
  ['wrong provider', { ...session, pending: { ...pending, provider: 'other' } }],
  ['unknown purpose', { ...session, pending: { ...pending, purpose: 'other' } }],
  ['invalid masked phone', { ...session, pending: { ...pending, maskedPhone: null } }],
  ['invalid expiry', { ...session, pending: { ...pending, expiresAt: 1 } }],
]) {
  test(`OIDC session rejects ${name} without raw payload output or caching the response`, async () => {
    const { client, calls } = recording([invalid]);
    await assert.rejects(client.getSession(), error => error instanceof TypeError && error.message === 'Invalid OIDC session response');
    await assert.rejects(client.start(), beforeGet);
    assert.equal(calls.length, 1);
  });
}

for (const [status, code] of [[404, 'not_found'], [403, 'oidc_disabled'], [503, 'oidc_unavailable']]) {
  test(`OIDC GET ${status} ${code} stays a typed API error, not a fabricated enabled session`, async () => {
    const { client, calls } = recording([errorResponse(status, code)]);
    await assert.rejects(client.getSession(), apiError(status, code));
    await assert.rejects(client.start(), beforeGet);
    assert.equal(calls.length, 1);
  });
}

test('OIDC ignores JavaScript Bearer and CSRF injection rather than forwarding ordinary credentials', async () => {
  const { client, calls } = recording([session, authorization], {
    accessToken: () => assert.fail('OIDC must not read an ordinary Bearer'), csrfToken: 'ignore', browserSession: true,
  });
  await assert.rejects(client.start(), beforeGet);
  await client.getSession();
  await client.start();
  for (const call of calls) assertCookie(call);
});

test('ordinary, social, admin and OIDC credentials are isolated per instance and header namespace', async () => {
  const seen = [];
  const fetcher = async (url, init) => {
    seen.push({ url, ...init });
    if (url.endsWith('/auth/browser/session')) return Response.json({ user, csrfToken: 'ordinary-fixture', expiresAt: stamp });
    if (url.endsWith('/auth/social/session')) return Response.json({ csrfToken: 'social-fixture', expiresAt: stamp, pending: null });
    if (url.endsWith('/admin/session')) return Response.json({ authenticated: true, csrfToken: 'admin-fixture', expiresAt: stamp });
    if (url.endsWith('/auth/oidc/session')) return Response.json(session);
    return Response.json(authorization);
  };
  const ordinary = new AlliraClient({ baseUrl, browserSession: true, fetch: fetcher });
  const social = new AlliraSocialClient({ baseUrl, fetch: fetcher });
  const admin = new AlliraAdminClient({ baseUrl, fetch: fetcher });
  const client = new AlliraOidcClient({ baseUrl, fetch: fetcher });
  const second = new AlliraOidcClient({ baseUrl, fetch: fetcher });
  await ordinary.getBrowserSession(); await social.getSession(); await admin.getSession();
  await assert.rejects(client.start(), beforeGet);
  assert.equal(seen.length, 3);
  await client.getSession(); await client.start();
  await assert.rejects(second.start(), beforeGet);
  assertCookie(seen[4]);
  assert.equal(seen.length, 5);
});

for (const operation of writes.filter(item => item.ending)) {
  test(`successful OIDC ${operation.name} clears transition memory and requires a new explicit GET`, async () => {
    const { client, calls } = recording([session, operation.result, replacement, authorization]);
    await client.getSession();
    same(await operation.call(client), operation.result);
    await assert.rejects(client.start(), beforeGet);
    assert.equal(calls.length, 2);
    await client.getSession(); await client.start();
    assertCookie(calls[3], replacement.csrfToken);
  });
}

test('successful completion returns a caller-owned AccountSession without replacing another client Bearer', async () => {
  const { client } = recording([session, account]);
  const calls = [];
  const ordinary = new AlliraClient({ baseUrl, accessToken: 'fixture-original', fetch: async (url, init) => { calls.push(init); return Response.json({ userId: user.id }); } });
  await client.getSession();
  same(await client.complete(confirmation), account);
  await ordinary.getSession();
  assert.equal(calls[0].headers.get('Authorization') === 'Bearer fixture-original', true);
  assert.equal(calls[0].credentials, 'omit');
});

test('clear sends no HTTP, discards only OIDC memory and invalidates a pending GET', async () => {
  const gate = deferred();
  const client = new AlliraOidcClient({ baseUrl, fetch: async () => gate.promise });
  const restore = client.getSession();
  assert.equal(client.clear(), undefined);
  gate.resolve(Response.json(session));
  await restore;
  await assert.rejects(client.start(), beforeGet);
});

test('a late older GET cannot overwrite a newer OIDC restore', async () => {
  const gate = deferred(); let calls = 0; const seen = [];
  const client = new AlliraOidcClient({ baseUrl, fetch: async (url, init) => {
    calls++; seen.push({ url, ...init });
    if (calls === 1) return gate.promise;
    return Response.json(calls === 2 ? replacement : authorization);
  } });
  const old = client.getSession();
  await client.getSession();
  gate.resolve(Response.json(session)); await old;
  await client.start();
  assertCookie(seen[2], replacement.csrfToken);
});

test('a late 401 cannot clear a newer distinct OIDC restore', async () => {
  const gate = deferred(); let calls = 0; const seen = [];
  const client = new AlliraOidcClient({ baseUrl, fetch: async (url, init) => {
    seen.push({ url, ...init }); calls++;
    if (calls === 2) return gate.promise;
    return Response.json(calls === 1 ? session : calls === 3 ? replacement : authorization);
  } });
  await client.getSession(); const old = client.start();
  await client.getSession();
  gate.resolve(errorResponse(401, 'oidc_session_required'));
  await assert.rejects(old, apiError(401, 'oidc_session_required'));
  await client.start(); assertCookie(seen[3], replacement.csrfToken);
});

for (const operation of writes.filter(item => item.ending)) {
  test(`successful ${operation.name} invalidates an older in-flight GET using the consumed transition`, async () => {
    const end = deferred(); const restore = deferred(); let calls = 0;
    const client = new AlliraOidcClient({ baseUrl, fetch: async () => {
      calls++;
      if (calls === 1) return Response.json(session);
      return calls === 2 ? end.promise : restore.promise;
    } });
    await client.getSession(); const ending = operation.call(client); const restoring = client.getSession();
    end.resolve(Response.json(operation.result)); await ending;
    restore.resolve(Response.json(session)); await restoring;
    await assert.rejects(client.start(), beforeGet);
    assert.equal(calls, 3);
  });
}

test('late success from an old transition cannot clear a newer distinct session', async () => {
  const gate = deferred(); let calls = 0; const seen = [];
  const client = new AlliraOidcClient({ baseUrl, fetch: async (url, init) => {
    calls++; seen.push({ url, ...init });
    if (calls === 2) return gate.promise;
    return Response.json(calls === 1 ? session : calls === 3 ? replacement : authorization);
  } });
  await client.getSession(); const old = client.cancel(); await client.getSession();
  gate.resolve(Response.json({ ok: true })); await old;
  await client.start(); assertCookie(seen[3], replacement.csrfToken);
});

test('current OIDC 401 clears matching local CSRF without ordinary-Bearer fallback', async () => {
  const { client, calls } = recording([session, errorResponse(401, 'oidc_pending_required')]);
  await client.getSession();
  await assert.rejects(client.complete(confirmation), apiError(401, 'oidc_pending_required'));
  await assert.rejects(client.start(), beforeGet);
  assert.equal(calls.length, 2);
});

for (const [status, code] of [
  [400, 'invalid_request'], [403, 'origin_forbidden'], [403, 'csrf_forbidden'], [403, 'registration_disabled'],
  [404, 'not_found'], [409, 'identity_link_required'], [409, 'account_busy'], [429, 'rate_limited'], [503, 'oidc_unavailable'],
]) {
  test(`OIDC rejection ${status} ${code} propagates once without POST retry and retains existing transition CSRF`, async () => {
    const { client, calls } = recording([session, errorResponse(status, code), authorization]);
    await client.getSession();
    await assert.rejects(client.erase({ confirmation: 'DELETE MY ACCOUNT' }, { browserCsrfToken }), apiError(status, code));
    assert.equal(calls.length, 2);
    await client.start(); assertCookie(calls[2]);
  });
}

test('busy fresh erase is manually retryable with the exact body and same transition CSRF', async () => {
  const { client, calls } = recording([replacement, errorResponse(409, 'account_busy'), { ok: true }]);
  await client.getSession();
  const input = { confirmation: 'DELETE MY ACCOUNT' };
  await assert.rejects(client.erase(input, { browserCsrfToken }), apiError(409, 'account_busy'));
  same(await client.erase(input, { browserCsrfToken }), { ok: true });
  same(calls[1].body, calls[2].body);
  assertCookie(calls[2], replacement.csrfToken);
  await assert.rejects(client.start(), beforeGet);
});

test('all OIDC methods honor pre-abort without fetching or initializing state', async () => {
  const controller = new AbortController(); const reason = new DOMException('Synthetic stop', 'AbortError'); controller.abort(reason);
  const { client, calls } = recording([]);
  await assert.rejects(client.getSession({ signal: controller.signal }), error => error === reason);
  for (const operation of writes) await assert.rejects(operation.call(client, { signal: controller.signal }), error => error === reason);
  assert.equal(calls.length, 0);
});

for (const name of ['start login', 'erase']) {
  test(`OIDC ${name} preserves in-flight abort identity without resending`, async () => {
    const controller = new AbortController(); const reason = new DOMException('Synthetic stop', 'AbortError'); let calls = 0;
    const client = new AlliraOidcClient({ baseUrl, fetch: async (url, init) => {
      if (++calls === 1) return Response.json(session);
      return new Promise((resolve, reject) => {
        init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true }); controller.abort(reason);
      });
    } });
    await client.getSession();
    await assert.rejects(writes.find(item => item.name === name).call(client, { signal: controller.signal }), error => error === reason);
    assert.equal(calls, 2);
  });
}

test('abort after OIDC GET parsing prevents late CSRF caching', async () => {
  const controller = new AbortController(); const reason = new DOMException('Synthetic stop', 'AbortError');
  const client = new AlliraOidcClient({ baseUrl, fetch: async () => ({ ok: true, status: 200, json: async () => { controller.abort(reason); return session; } }) });
  await assert.rejects(client.getSession({ signal: controller.signal }), error => error === reason);
  await assert.rejects(client.start(), beforeGet);
});

for (const name of ['start login', 'complete', 'erase']) {
  test(`OIDC ${name} preserves transport failure identity and does not replay a sensitive POST`, async () => {
    const reason = new TypeError('Synthetic transport failure'); let calls = 0;
    const client = new AlliraOidcClient({ baseUrl, fetch: async () => { if (++calls === 1) return Response.json(session); throw reason; } });
    await client.getSession();
    await assert.rejects(writes.find(item => item.name === name).call(client), error => error === reason);
    assert.equal(calls, 2);
  });
}

test('authorization URL is returned as untrusted data with no callback fetch or automatic navigation', async () => {
  const unsafe = { authorizationUrl: 'javascript:fixtureOnly()' };
  const { client, calls } = recording([session, unsafe]);
  await client.getSession(); same(await client.start(), unsafe);
  assert.equal(calls.length, 2);
  assert.equal(calls.every(call => call.url.startsWith(`${baseUrl}/auth/oidc/`)), true);
});

test('OIDC base URL normalization retains same-origin cookie policy and never doubles api/v1', async () => {
  const { client, calls } = recording([session, authorization], { baseUrl: 'https://allira.example:8443/api/v1/' });
  await client.getSession(); await client.start();
  assert.equal(calls[1].url, `${baseUrl}/auth/oidc/start`); assertCookie(calls[1]);
});

// Additive protocol fixtures only. These do not prove the server's account-migration or issuer behavior.
const phoneMigrationSession = { ...session, pending: { ...pending, purpose: 'phone_migration' } };

test('phone migration pending keeps its explicit purpose without becoming ordinary authentication', async () => {
  const { client, calls } = recording([phoneMigrationSession]);
  const restored = await client.getSession();
  same(restored, phoneMigrationSession);
  assert.equal('accessToken' in restored, false);
  assert.equal(calls.length, 1); assertCookie(calls[0]);
});

test('phone migration start requires explicit OIDC GET but no ordinary browser session or CSRF', async () => {
  const { client, calls } = recording([session, authorization]);
  const { signal } = new AbortController();
  await assert.rejects(client.start({ purpose: 'phone_migration' }, { signal }), beforeGet);
  assert.equal(calls.length, 0);
  await client.getSession();
  same(await client.start({ purpose: 'phone_migration', phone: 'ignore', confirmPhoneMigration: true }, { signal }), authorization);
  assert.equal(calls[1].url, `${baseUrl}/auth/oidc/start`);
  same(JSON.parse(calls[1].body), { purpose: 'phone_migration' });
  assert.equal(calls[1].signal, signal); assertCookie(calls[1]);
});

test('phone migration consent preserves true and false while omission keeps the prior complete body', async () => {
  for (const consent of [undefined, false, true]) {
    const input = { ...confirmation, confirmPhoneMigration: consent };
    const expected = consent === undefined ? confirmation : input;
    const current = consent === true ? phoneMigrationSession : session;
    const { client, calls } = recording([current, account]);
    await client.getSession(); same(await client.complete(input), account);
    same(JSON.parse(calls[1].body), expected); assertCookie(calls[1]);
    await assert.rejects(client.start(), beforeGet); assert.equal(calls.length, 2);
  }
});

test('phone migration completion allowlists explicit consent and returns the caller-owned legacy-ID DTO', async () => {
  const oldAccount = { ...account, user: { ...user, id: 'acct_existing_passwordless_fixture' } };
  const { client, calls } = recording([phoneMigrationSession, oldAccount]);
  await client.getSession();
  const result = await client.complete({ ...confirmation, confirmPhoneMigration: true, userId: 'ignore', phone: 'ignore', subject: 'ignore', passwordEnabled: false });
  same(result, oldAccount);
  same(JSON.parse(calls[1].body), { ...confirmation, confirmPhoneMigration: true });
  assertCookie(calls[1]); assert.equal(calls.length, 2);
});

test('phone migration requires server approval rather than treating login or missing consent as a successful merge', async () => {
  for (const [current, input] of [
    [session, { ...confirmation, confirmPhoneMigration: true }],
    [phoneMigrationSession, confirmation],
    [phoneMigrationSession, { ...confirmation, confirmPhoneMigration: false }],
  ]) {
    const { client, calls } = recording([current, errorResponse(409, 'identity_link_required')]);
    await client.getSession();
    await assert.rejects(client.complete(input), apiError(409, 'identity_link_required'));
    assert.equal(calls.length, 2); same(JSON.parse(calls[1].body), input); assertCookie(calls[1]);
    assert.equal(calls.some(call => call.url.endsWith('/migrate') || call.url.endsWith('/auth/browser/session')), false);
  }
});

test('phone migration does not coerce invalid JavaScript consent values past typed server refusals', async () => {
  for (const consent of ['true', 1, null]) {
    const { client, calls } = recording([phoneMigrationSession, errorResponse(422, 'validation_error')]);
    await client.getSession();
    await assert.rejects(client.complete({ ...confirmation, confirmPhoneMigration: consent }), apiError(422, 'validation_error'));
    same(JSON.parse(calls[1].body).confirmPhoneMigration, consent);
    assert.equal(calls.length, 2); assertCookie(calls[1]);
  }
});

test('phone migration never replaces the explicit password-proven migration route or retries it automatically', async () => {
  const { client, calls } = recording([phoneMigrationSession, errorResponse(409, 'identity_link_required'), session, account]);
  await client.getSession();
  await assert.rejects(client.complete({ ...confirmation, confirmPhoneMigration: true }), apiError(409, 'identity_link_required'));
  assert.equal(calls.length, 2);
  await client.getSession(); same(await client.migrate(migration), account);
  assert.equal(calls[3].url, `${baseUrl}/auth/oidc/migrate`);
  same(JSON.parse(calls[3].body), migration); assertCookie(calls[3]);
});

test('phone migration ignores ordinary CSRF and Bearer injection on both start and complete', async () => {
  const { client, calls } = recording([phoneMigrationSession, authorization, account], { accessToken: () => assert.fail('No ordinary Bearer callback') });
  await client.getSession();
  const options = { browserCsrfToken, headers: { Authorization: 'Bearer ignore', 'X-CSRF-Token': browserCsrfToken } };
  await client.start({ purpose: 'phone_migration' }, options);
  await client.complete({ ...confirmation, confirmPhoneMigration: true }, options);
  for (const call of calls) assertCookie(call);
  assert.equal(calls.length, 3);
});

test('phone migration start and completion keep abort and transport identity without sensitive POST replay', async () => {
  for (const operation of [
    (client, options) => client.start({ purpose: 'phone_migration' }, options),
    (client, options) => client.complete({ ...confirmation, confirmPhoneMigration: true }, options),
  ]) {
    const controller = new AbortController(); const reason = new DOMException('Synthetic stop', 'AbortError'); controller.abort(reason);
    const aborted = recording([]);
    await assert.rejects(operation(aborted.client, { signal: controller.signal }), error => error === reason);
    assert.equal(aborted.calls.length, 0);
    const failure = new TypeError('Synthetic transport failure'); let count = 0;
    const client = new AlliraOidcClient({ baseUrl, fetch: async () => {
      if (++count === 1) return Response.json(phoneMigrationSession);
      throw failure;
    } });
    await client.getSession(); await assert.rejects(operation(client), error => error === failure);
    assert.equal(count, 2);
  }
});

test('phone migration completion invalidates a late restore of the consumed transition', async () => {
  const gate = deferred(); const restore = deferred(); let count = 0;
  const client = new AlliraOidcClient({ baseUrl, fetch: async () => {
    if (++count === 1) return Response.json(phoneMigrationSession);
    return count === 2 ? gate.promise : restore.promise;
  } });
  await client.getSession();
  const completing = client.complete({ ...confirmation, confirmPhoneMigration: true }); const restoring = client.getSession();
  gate.resolve(Response.json(account)); same(await completing, account);
  restore.resolve(Response.json(phoneMigrationSession)); await restoring;
  await assert.rejects(client.start({ purpose: 'phone_migration' }), beforeGet);
  assert.equal(count, 3);
});
