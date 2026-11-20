import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isDeepStrictEqual } from 'node:util';
import { AlliraAdminClient, AlliraApiError, AlliraClient, AlliraSocialClient } from '../dist/index.js';

// Synthetic Fetch only. Nothing navigates, sends SMS, contacts providers, or stores credentials.
const baseUrl = 'https://allira.example:8443/api/v1';
const origin = new URL(baseUrl).origin;
const stamp = '2026-11-19T08:00:00Z';
const pending = { provider: 'wechat', displayName: 'Untrusted <script>name</script>', expiresAt: stamp, requiresPhoneBinding: true };
const session = { csrfToken: 'fixture-social-csrf', expiresAt: stamp, pending };
const emptySession = { ...session, pending: null };
const replacement = { csrfToken: 'fixture-new-social-csrf', expiresAt: stamp, pending: { ...pending, provider: 'qq', requiresPhoneBinding: false } };
const user = { id: 'account-1', username: 'internal_fixture', displayName: 'Fixture', createdAt: stamp, termsVersion: '2026-11-19', isAdult: true };
const account = { accessToken: 'fixture-social-account-session', tokenType: 'Bearer', user };
const phone = '+8613800000000';
const challengeId = '746c958b-5864-4af1-96d3-80a7c5759d15';
const code = '001234';
const challenge = { challengeId, maskedPhone: '+86 138****0000', expiresAt: stamp, resendAfterSeconds: 60 };
const bind = { challengeId, code, acceptedTermsVersion: '2026-11-19', isAdult: true, confirmBind: true };
const start = { authorizeUrl: 'https://provider.example/oauth?state=synthetic-state', expiresAt: stamp };
const same = (actual, expected) => assert.equal(isDeepStrictEqual(actual, expected), true, 'private fixture values must match without logging them');
const assertBody = (call, expected) => same(JSON.parse(call.body), expected);
const errorResponse = (status, errorCode) => Response.json({ error: { code: errorCode, message: 'Synthetic social refusal' } }, { status });
const apiError = (status, errorCode) => error => error instanceof AlliraApiError && error.status === status && error.code === errorCode;
function recording(responses, options = {}) {
  const calls = [];
  const client = new AlliraSocialClient({ baseUrl, ...options, fetch: async (url, init) => {
    calls.push({ url, ...init });
    const response = responses[calls.length - 1];
    return response instanceof Response ? response : Response.json(response);
  } });
  return { client, calls };
}
function assertCookie(call, csrfToken = session.csrfToken) {
  assert.equal(call.credentials, 'same-origin');
  assert.equal(call.mode, 'same-origin');
  assert.equal(call.headers.get('Accept'), 'application/json');
  assert.equal(call.headers.has('Authorization'), false);
  assert.equal(call.headers.has('Cookie'), false);
  assert.equal(call.headers.get('X-CSRF-Token') === csrfToken, call.method === 'POST');
  assert.equal(call.headers.get('Origin'), call.method === 'POST' ? origin : null);
}
function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}
const writes = [
  { name: 'start wechat', path: '/auth/social/wechat/start', body: {}, result: start, call: (client, opts) => client.start('wechat', opts) },
  { name: 'start qq', path: '/auth/social/qq/start', body: {}, result: start, call: (client, opts) => client.start('qq', opts) },
  { name: 'requestPhoneChallenge', path: '/auth/social/phone/challenges', body: { phone }, result: challenge, status: 202, call: (client, opts) => client.requestPhoneChallenge({ phone, purpose: 'register', pending: 'ignore', code: 'ignore' }, opts) },
  { name: 'bindPhone', path: '/auth/social/bind-phone', body: bind, result: account, call: (client, opts) => client.bindPhone({ ...bind, phone: 'ignore', provider: 'qq', accessToken: 'ignore' }, opts) },
  { name: 'complete', path: '/auth/social/complete', body: {}, result: account, call: (client, opts) => client.complete(opts) },
  { name: 'cancel', path: '/auth/social/cancel', body: {}, result: { ok: true }, call: (client, opts) => client.cancel(opts) },
];

test('social GET initializes an independent cookie CSRF without creating an ordinary account session', async () => {
  const { client, calls } = recording([emptySession]);
  const { signal } = new AbortController();
  same(await client.getSession({ signal }), emptySession);
  assert.equal(calls.length, 1);
  const [call] = calls;
  assert.equal(call.url, `${baseUrl}/auth/social/session`);
  assert.equal(call.method, 'GET');
  assert.equal(call.signal, signal);
  assert.equal(call.body, undefined);
  assert.equal(call.headers.has('Content-Type'), false);
  assertCookie(call);
});

for (const operation of writes) {
  test(`social ${operation.name} uses exact route/body and cookie-only CSRF after explicit GET`, async () => {
    const { client, calls } = recording([session, Response.json(operation.result, { status: operation.status ?? 200 })]);
    const { signal } = new AbortController();
    await client.getSession({ signal });
    same(await operation.call(client, { signal }), operation.result);
    assert.equal(calls.length, 2);
    const call = calls[1];
    assert.equal(call.url, `${baseUrl}${operation.path}`);
    assert.equal(call.method, 'POST');
    assert.equal(call.signal, signal);
    assert.equal(call.headers.get('Content-Type'), 'application/json');
    assertCookie(call);
    assertBody(call, operation.body);
  });
}

test('all social writes reject locally without an initialized social session, never bootstrap it implicitly', async () => {
  const { client, calls } = recording([]);
  for (const operation of writes) await assert.rejects(operation.call(client), { name: 'TypeError', message: 'Call social getSession() before a write request' });
  assert.equal(calls.length, 0);
});

test('social binding does not require a password and allowlists optional password/displayName when supplied', async () => {
  const { client, calls } = recording([session, account, account, account]);
  await client.getSession();
  await client.bindPhone(bind);
  await client.bindPhone({ ...bind, password: undefined, displayName: undefined });
  await client.bindPhone({ ...bind, password: 'fixture-optional-password', displayName: 'Named', csrfToken: 'ignore', pendingToken: 'ignore', userId: 'ignore' });
  assertBody(calls[1], bind);
  assertBody(calls[2], bind);
  assertBody(calls[3], { ...bind, password: 'fixture-optional-password', displayName: 'Named' });
});

test('social runtime options cannot inject Bearer, account CSRF, cookie values or account mode', async () => {
  let reads = 0;
  const { client, calls } = recording([session, start], { accessToken: () => { reads++; throw new Error('Must never read account Bearer'); }, browserSession: true, csrfToken: 'ignore' });
  await client.getSession({ accessToken: 'ignore', csrfToken: 'ignore' });
  await client.start('wechat', { accessToken: 'ignore', csrfToken: 'ignore', cookie: 'ignore' });
  assert.equal(reads, 0);
  for (const call of calls) assertCookie(call);
  assertBody(calls[1], {});
});

test('social, ordinary browser and admin CSRF channels cannot initialize or overwrite each other', async () => {
  const ordinaryCalls = [];
  const ordinary = new AlliraClient({ baseUrl, browserSession: true, fetch: async (url, init) => { ordinaryCalls.push({ url, ...init }); return Response.json({ user, csrfToken: 'fixture-ordinary-csrf', expiresAt: stamp }); } });
  const adminCalls = [];
  const admin = new AlliraAdminClient({ baseUrl, fetch: async (url, init) => { adminCalls.push({ url, ...init }); return Response.json({ authenticated: true, csrfToken: 'fixture-admin-csrf', expiresAt: stamp }); } });
  const { client, calls } = recording([session, start]);
  await ordinary.getBrowserSession();
  await admin.getSession();
  await assert.rejects(client.start('wechat'), TypeError);
  assert.equal(calls.length, 0);
  await client.getSession();
  ordinary.clearBrowserSession();
  await client.start('wechat');
  assertCookie(calls[1]);
  assert.equal(adminCalls.length, 1);
  await assert.rejects(ordinary.createConversation({}), TypeError);
  const fresh = new AlliraClient({ baseUrl, browserSession: true, fetch: async () => assert.fail('social state must not initialize account CSRF') });
  await assert.rejects(fresh.createConversation({}), TypeError);
  const freshAdmin = new AlliraAdminClient({ baseUrl, fetch: async () => assert.fail('social state must not initialize admin CSRF') });
  await assert.rejects(freshAdmin.cancelRun('run'), TypeError);
});

test('social CSRF is per instance even when two clients use the same base URL', async () => {
  const first = recording([session, start]);
  const second = recording([replacement, start]);
  await first.client.getSession();
  await assert.rejects(second.client.start('qq'), TypeError);
  await second.client.getSession();
  await first.client.start('wechat');
  await second.client.start('qq');
  assertCookie(first.calls[1], session.csrfToken);
  assertCookie(second.calls[1], replacement.csrfToken);
});

test('bind and complete return caller-owned account credentials without changing ordinary token or consuming social CSRF', async () => {
  const { client, calls } = recording([session, account, account, start]);
  const ordinaryCalls = [];
  const ordinary = new AlliraClient({ baseUrl, accessToken: 'fixture-ordinary-bearer', fetch: async (url, init) => { ordinaryCalls.push({ url, ...init }); return Response.json(user); } });
  await client.getSession();
  same(await client.bindPhone(bind), account);
  same(await client.complete(), account);
  await client.start('qq');
  await ordinary.getAccount();
  assert.equal(ordinaryCalls[0].headers.get('Authorization') === 'Bearer fixture-ordinary-bearer', true);
  for (const call of calls) assertCookie(call);
});

test('successful cancel revokes local social CSRF only; clearSession sends no HTTP', async () => {
  const { client, calls } = recording([session, { ok: true }]);
  await client.getSession();
  await client.cancel();
  await assert.rejects(client.complete(), TypeError);
  client.clearSession();
  assert.equal(calls.length, 2);
  assertBody(calls[1], {});
});

test('clearSession prevents a late successful GET from reinitializing local CSRF', async () => {
  const late = deferred();
  let calls = 0;
  const client = new AlliraSocialClient({ baseUrl, fetch: async () => { calls++; return late.promise; } });
  const result = client.getSession();
  client.clearSession();
  late.resolve(Response.json(session));
  same(await result, session);
  await assert.rejects(client.start('wechat'), TypeError);
  assert.equal(calls, 1);
});

test('an older session GET cannot overwrite a newer CSRF result', async () => {
  const late = deferred();
  const calls = [];
  const client = new AlliraSocialClient({ baseUrl, fetch: async (url, init) => {
    calls.push({ url, ...init });
    if (calls.length === 1) return late.promise;
    return Response.json(calls.length === 2 ? replacement : start);
  } });
  const old = client.getSession();
  await client.getSession();
  late.resolve(Response.json(session));
  await old;
  await client.start('qq');
  assertCookie(calls[2], replacement.csrfToken);
});

for (const method of ['GET', 'POST']) {
  test(`late social ${method} 401 cannot clear a newer restored session`, async () => {
    const late = deferred();
    const calls = [];
    const client = new AlliraSocialClient({ baseUrl, fetch: async (url, init) => {
      calls.push({ url, ...init });
      if (calls.length === 1) return Response.json(session);
      if (calls.length === 2) return late.promise;
      return Response.json(calls.length === 3 ? replacement : start);
    } });
    await client.getSession();
    const old = method === 'GET' ? client.getSession() : client.complete();
    await client.getSession();
    late.resolve(errorResponse(401, 'unauthorized'));
    await assert.rejects(old, apiError(401, 'unauthorized'));
    await client.start('qq');
    assertCookie(calls[3], replacement.csrfToken);
  });
}

test('late cancel success does not clear a newer GET session even when its CSRF string is unchanged', async () => {
  const late = deferred();
  const calls = [];
  const client = new AlliraSocialClient({ baseUrl, fetch: async (url, init) => {
    calls.push({ url, ...init });
    if (calls.length === 2) return late.promise;
    return Response.json(calls.length < 4 ? session : start);
  } });
  await client.getSession();
  const cancelling = client.cancel();
  await client.getSession();
  late.resolve(Response.json({ ok: true }));
  await cancelling;
  await client.start('wechat');
  assertCookie(calls[3]);
});

for (const method of ['GET', 'POST']) {
  test(`current social ${method} 401 clears CSRF without fallback, bootstrap, or retry`, async () => {
    const { client, calls } = recording([session, errorResponse(401, 'unauthorized'), replacement, start]);
    await client.getSession();
    await assert.rejects(method === 'GET' ? client.getSession() : client.complete(), apiError(401, 'unauthorized'));
    await assert.rejects(client.start('wechat'), TypeError);
    assert.equal(calls.length, 2);
    await client.getSession();
    await client.start('qq');
    assertCookie(calls[3], replacement.csrfToken);
  });
}

for (const [status, errorCode] of [[400, 'invalid_state'], [403, 'csrf_failed'], [404, 'not_found'], [409, 'phone_binding_required'], [422, 'validation_error'], [429, 'rate_limited'], [503, 'social_unavailable']]) {
  test(`social ${status} surfaces the envelope without retry and retains valid CSRF until an explicit refresh`, async () => {
    const { client, calls } = recording([session, errorResponse(status, errorCode), start]);
    await client.getSession();
    await assert.rejects(client.complete(), apiError(status, errorCode));
    await client.start('wechat');
    assert.equal(calls.length, 3);
    assertCookie(calls[2]);
  });
}

test('cancel failure retains current CSRF and never silently repeats cancellation', async () => {
  const { client, calls } = recording([session, errorResponse(409, 'conflict'), start]);
  await client.getSession();
  await assert.rejects(client.cancel(), apiError(409, 'conflict'));
  await client.start('wechat');
  assert.equal(calls.length, 3);
  assertCookie(calls[2]);
});

test('pre-aborted social methods keep the original reason before checking CSRF or fetching', async () => {
  const { client, calls } = recording([]);
  const controller = new AbortController();
  const reason = new DOMException('Synthetic social abort', 'AbortError');
  controller.abort(reason);
  await assert.rejects(client.getSession({ signal: controller.signal }), error => error === reason);
  for (const operation of writes) await assert.rejects(operation.call(client, { signal: controller.signal }), error => error === reason);
  assert.equal(calls.length, 0);
});

for (const method of ['GET', 'POST']) {
  test(`in-flight social ${method} abort preserves the original reason without repeating the request`, async () => {
    const controller = new AbortController();
    const reason = new DOMException('Synthetic in-flight social abort', 'AbortError');
    let calls = 0;
    const client = new AlliraSocialClient({ baseUrl, fetch: async (_url, init) => {
      calls++;
      if (method === 'POST' && calls === 1) return Response.json(session);
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true });
        controller.abort(reason);
      });
    } });
    if (method === 'POST') await client.getSession();
    await assert.rejects(method === 'GET' ? client.getSession({ signal: controller.signal }) : client.start('wechat', { signal: controller.signal }), error => error === reason);
    assert.equal(calls, method === 'POST' ? 2 : 1);
  });
}

test('GET aborted while decoding its result cannot cache CSRF afterward', async () => {
  const controller = new AbortController();
  const reason = new DOMException('Synthetic social parse abort', 'AbortError');
  let calls = 0;
  const client = new AlliraSocialClient({ baseUrl, fetch: async () => {
    calls++;
    return { ok: true, status: 200, json: async () => { controller.abort(reason); return session; } };
  } });
  await assert.rejects(client.getSession({ signal: controller.signal }), error => error === reason);
  await assert.rejects(client.start('wechat'), TypeError);
  assert.equal(calls, 1);
});

for (const name of ['start wechat', 'bindPhone']) {
  test(`social ${name} transport failure does not replay state or OTP exchanges`, async () => {
    const reason = new TypeError('Synthetic social transport failure');
    let calls = 0;
    const client = new AlliraSocialClient({ baseUrl, fetch: async () => {
      calls++;
      if (calls === 1) return Response.json(session);
      throw reason;
    } });
    await client.getSession();
    await assert.rejects(writes.find(operation => operation.name === name).call(client), error => error === reason);
    assert.equal(calls, 2);
  });
}

test('start returns an untrusted authorization URL without navigation, fetching it, or accepting tokens from it', async () => {
  const untrusted = { authorizeUrl: 'javascript:doNotNavigate()', expiresAt: stamp };
  const { client, calls } = recording([session, untrusted]);
  await client.getSession();
  same(await client.start('wechat'), untrusted);
  assert.equal(calls.length, 2);
  assert.equal(calls.every(call => call.url.startsWith(baseUrl)), true);
});

test('provider segments are encoded even for an untyped invalid value instead of injecting another path', async () => {
  const { client, calls } = recording([session, errorResponse(404, 'not_found')]);
  await client.getSession();
  await assert.rejects(client.start('wechat/../../account?token=ignore'), apiError(404, 'not_found'));
  assert.equal(calls[1].url, `${baseUrl}/auth/social/wechat%2F..%2F..%2Faccount%3Ftoken%3Dignore/start`);
});
