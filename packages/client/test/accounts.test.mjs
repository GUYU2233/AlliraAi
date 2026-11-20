import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isDeepStrictEqual } from 'node:util';
import { AlliraClient, AlliraApiError } from '../dist/index.js';

// Every credential in this file is a synthetic fixture. Never print credential values.
const user = { id: 'account-1', username: 'fixture_user', displayName: 'Fixture', createdAt: '2026-11-19T00:00:00Z', termsVersion: '2026-11-19', isAdult: true };
const session = { accessToken: 'fake-account-session', tokenType: 'Bearer', user };
const registration = { username: 'fixture_user', password: 'fake-register-password', acceptedTermsVersion: '2026-11-19', isAdult: true };
const login = { username: 'fixture_user', password: 'fake-login-password' };
const terms = { version: '2026-11-19', adultOnly: true, content: 'Synthetic terms fixture.' };
const routes = { items: [{ mode: 'economy', provider: 'mock', configured: true, energyCost: 1, fallbackTo: null }, { mode: 'fast', provider: 'mock', configured: true, energyCost: 3, fallbackTo: 'economy' }] };

const bodyIs = (actual, expected) => assert.equal(isDeepStrictEqual(JSON.parse(actual), expected), true, 'request body must match the exact wire fields');

test('public terms, routes and account exchanges never evaluate or attach a configured Bearer', async () => {
  const calls = [];
  const sdk = new AlliraClient({
    baseUrl: 'https://allira.example',
    accessToken: () => { throw new Error('public request must not read credentials'); },
    fetch: async (url, init) => {
      calls.push({ url, ...init });
      const value = url.endsWith('/terms') ? terms : url.endsWith('/models') ? routes : session;
      return Response.json(value, { status: url.endsWith('/register') ? 201 : 200 });
    },
  });
  const controller = new AbortController();
  const options = { signal: controller.signal };
  assert.deepEqual(await sdk.getTerms(options), terms);
  assert.deepEqual(await sdk.getModelRoutes(options), routes);
  const created = await sdk.register({ ...registration, displayName: 'Fixture', accessToken: 'must-not-forward', admin: true }, options);
  const signedIn = await sdk.login({ ...login, accessToken: 'must-not-forward', code: 'must-not-forward' }, options);
  assert.equal(created.accessToken === session.accessToken, true);
  assert.deepEqual(created.user, user);
  assert.equal(signedIn.accessToken === session.accessToken, true);
  assert.deepEqual(calls.map(call => [call.method, call.url]), [
    ['GET', 'https://allira.example/api/v1/terms'],
    ['GET', 'https://allira.example/api/v1/models'],
    ['POST', 'https://allira.example/api/v1/auth/register'],
    ['POST', 'https://allira.example/api/v1/auth/login'],
  ]);
  bodyIs(calls[2].body, { ...registration, displayName: 'Fixture' });
  bodyIs(calls[3].body, login);
  for (const call of calls) {
    assert.equal(call.headers.has('Authorization'), false);
    assert.equal(call.headers.has('X-CSRF-Token'), false);
    assert.equal(call.signal, controller.signal);
  }
});

test('account registration omits an absent optional displayName', async () => {
  let calls = 0;
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example', fetch: async (_url, init) => {
    calls++;
    bodyIs(init.body, registration);
    assert.equal(Object.hasOwn(JSON.parse(init.body), 'displayName'), false);
    return Response.json(session, { status: 201 });
  } });
  await sdk.register(registration);
  assert.equal(calls, 1);
});

test('account maintenance uses fresh Bearer and exact bodies; password and logout-all do not own token state', async () => {
  const calls = [];
  let reads = 0;
  let token = 'fake-original-session';
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example/api/v1', accessToken: async () => { reads++; return token; }, fetch: async (url, init) => {
    calls.push({ url, ...init });
    if (url.endsWith('/logout-all')) return Response.json({ ok: true, revoked: 4 });
    if (url.endsWith('/password')) return Response.json({ ok: true });
    return Response.json(user);
  } });
  const controller = new AbortController();
  const options = { signal: controller.signal };
  assert.deepEqual(await sdk.getAccount(options), user);
  token = 'fake-updated-session';
  assert.deepEqual(await sdk.updateAccount({ displayName: 'New name', password: 'must-not-forward' }, options), user);
  assert.deepEqual(await sdk.changePassword({ currentPassword: 'fake-current-password', newPassword: 'fake-new-password', accessToken: 'must-not-forward' }, options), { ok: true });
  assert.deepEqual(await sdk.logoutAll(options), { ok: true, revoked: 4 });
  await sdk.getAccount(options);
  assert.equal(reads, 5);
  assert.deepEqual(calls.map(call => [call.method, call.url]), [
    ['GET', 'https://allira.example/api/v1/account'],
    ['POST', 'https://allira.example/api/v1/account/update'],
    ['POST', 'https://allira.example/api/v1/account/password'],
    ['POST', 'https://allira.example/api/v1/account/logout-all'],
    ['GET', 'https://allira.example/api/v1/account'],
  ]);
  bodyIs(calls[1].body, { displayName: 'New name' });
  bodyIs(calls[2].body, { currentPassword: 'fake-current-password', newPassword: 'fake-new-password' });
  for (const index of [0, 3, 4]) assert.equal(calls[index].body, undefined);
  assert.equal(calls[0].headers.get('Authorization') === 'Bearer fake-original-session', true);
  for (const call of calls.slice(1)) assert.equal(call.headers.get('Authorization') === 'Bearer fake-updated-session', true);
  for (const call of calls) assert.equal(call.signal, controller.signal);
});

test('account exchanges never silently replace a caller-owned token', async () => {
  const calls = [];
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example', accessToken: 'fake-configured-session', fetch: async (url, init) => {
    calls.push({ url, ...init });
    return Response.json(url.endsWith('/account') ? user : session);
  } });
  await sdk.register(registration);
  await sdk.login(login);
  await sdk.getAccount();
  assert.equal(calls[0].headers.has('Authorization'), false);
  assert.equal(calls[1].headers.has('Authorization'), false);
  assert.equal(calls[2].headers.get('Authorization') === 'Bearer fake-configured-session', true);
});

for (const [status, code, operation] of [
  [401, 'invalid_credentials', sdk => sdk.login(login)],
  [409, 'username_taken', sdk => sdk.register(registration)],
  [422, 'validation_error', sdk => sdk.register({ ...registration, isAdult: false })],
  [429, 'rate_limited', sdk => sdk.changePassword({ currentPassword: 'fake-current-password', newPassword: 'fake-new-password' })],
]) {
  test(`account ${status} errors preserve the envelope without hidden retry or authentication`, async () => {
    let calls = 0;
    const sdk = new AlliraClient({ baseUrl: 'https://allira.example', fetch: async () => {
      calls++;
      return Response.json({ error: { code, message: 'Request refused' } }, { status });
    } });
    await assert.rejects(operation(sdk), error => error instanceof AlliraApiError && error.status === status && error.code === code && error.message === 'Request refused');
    assert.equal(calls, 1);
  });
}

test('pre-aborted account and public requests preserve the reason without fetching or reading tokens', async () => {
  let calls = 0;
  let reads = 0;
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example', accessToken: () => { reads++; return 'fake-unused-session'; }, fetch: async () => { calls++; return Response.json({}); } });
  const controller = new AbortController();
  const reason = new DOMException('Synthetic abort', 'AbortError');
  controller.abort(reason);
  const options = { signal: controller.signal };
  for (const request of [() => sdk.getTerms(options), () => sdk.getModelRoutes(options), () => sdk.register(registration, options), () => sdk.login(login, options), () => sdk.getAccount(options), () => sdk.updateAccount({ displayName: 'Name' }, options), () => sdk.changePassword({ currentPassword: 'fake-current-password', newPassword: 'fake-new-password' }, options), () => sdk.logoutAll(options)]) {
    await assert.rejects(request(), error => error === reason);
  }
  assert.equal(calls, 0);
  assert.equal(reads, 0);
});
