import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isDeepStrictEqual } from 'node:util';
import { AlliraApiError, AlliraClient } from '../dist/index.js';

// Synthetic Fetch fixtures only. No SMS, API, relay, credentials, or private data are accessed.
const baseUrl = 'https://allira.example:8443/api/v1';
const origin = new URL(baseUrl).origin;
const stamp = '2026-11-19T08:00:00Z';
const phone = '+8613800000000';
const code = '001234';
const password = 'fixture-phone-password';
const challengeId = '88a37bc4-e99b-45ce-95c8-76972f33954d';
const challenge = { challengeId, maskedPhone: '+86 138****0000', expiresAt: stamp, resendAfterSeconds: 60 };
const user = { id: 'account-phone', username: 'internal_fixture', displayName: 'Fixture', createdAt: stamp, termsVersion: '2026-11-19', isAdult: true };
const accountSession = { accessToken: 'fixture-phone-session', tokenType: 'Bearer', user };
const browserSession = { user, csrfToken: 'fixture-phone-csrf', expiresAt: stamp };
const identity = { primaryIdentifier: 'phone', phone, maskedPhone: challenge.maskedPhone, phoneVerified: true, requiresPhoneBinding: false, passwordEnabled: false, social: [{ provider: 'qq', linkedAt: stamp }] };
const options = { phone: { enabled: false, simulated: false, reason: 'disabled', countryCallingCodes: ['+86'] }, social: ['wechat', 'qq'].map(provider => ({ provider, enabled: false, simulated: false, reason: 'disabled' })), legacyPasswordEnabled: true, registrationEnabled: true };
const normalizedOptions = { ...options, backend: 'legacy', oidc: { enabled: false, provider: 'logto', simulated: false, reason: null, authorizationOrigin: null } };
const register = { challengeId, code, acceptedTermsVersion: '2026-11-19', isAdult: true };
const assertBody = (call, expected) => assert.equal(isDeepStrictEqual(JSON.parse(call.body), expected), true, 'body must contain only the exact published fields');
const same = (actual, expected) => assert.equal(isDeepStrictEqual(actual, expected), true, 'fixture result must match without printing private fields');
const errorResponse = (status, errorCode) => Response.json({ error: { code: errorCode, message: 'Synthetic identity refusal' } }, { status });
const apiError = (status, errorCode) => error => error instanceof AlliraApiError && error.status === status && error.code === errorCode;
function recording(responses, clientOptions = {}) {
  const calls = [];
  const client = new AlliraClient({ baseUrl, accessToken: 'fixture-original-bearer', ...clientOptions, fetch: async (url, init) => {
    calls.push({ url, ...init });
    const response = responses[calls.length - 1];
    return response instanceof Response ? response : Response.json(response);
  } });
  return { client, calls };
}
const operations = [
  { name: 'getAuthOptions', path: '/auth/options', method: 'GET', public: true, result: options, call: (client, opts) => client.getAuthOptions(opts) },
  { name: 'requestPhoneChallenge', path: '/auth/phone/challenges', method: 'POST', public: true, status: 202, result: challenge, body: { phone, purpose: 'login' }, call: (client, opts) => client.requestPhoneChallenge({ phone, purpose: 'login', code: 'ignore', userId: 'ignore' }, opts) },
  { name: 'registerPhone', path: '/auth/phone/register', method: 'POST', public: true, status: 201, result: accountSession, body: { ...register, password, displayName: 'Fixture' }, call: (client, opts) => client.registerPhone({ ...register, password, displayName: 'Fixture', phone: 'ignore', accessToken: 'ignore' }, opts) },
  { name: 'loginPhone', path: '/auth/phone/login', method: 'POST', public: true, result: accountSession, body: { challengeId, code }, call: (client, opts) => client.loginPhone({ challengeId, code, phone: 'ignore', password: 'ignore' }, opts) },
  { name: 'loginPhonePassword', path: '/auth/phone/password', method: 'POST', public: true, result: accountSession, body: { phone, password }, call: (client, opts) => client.loginPhonePassword({ phone, password, challengeId: 'ignore', code: 'ignore' }, opts) },
  { name: 'getAccountIdentity', path: '/account/identity', method: 'GET', result: identity, call: (client, opts) => client.getAccountIdentity(opts) },
  { name: 'requestBindPhoneChallenge', path: '/account/phone/challenges', method: 'POST', status: 202, result: challenge, body: { phone, currentPassword: password }, call: (client, opts) => client.requestBindPhoneChallenge({ phone, currentPassword: password, userId: 'ignore', purpose: 'register' }, opts) },
  { name: 'bindPhone', path: '/account/phone/bind', method: 'POST', result: identity, body: { challengeId, code, currentPassword: password }, call: (client, opts) => client.bindPhone({ challengeId, code, currentPassword: password, phone: 'ignore', ownerId: 'ignore' }, opts) },
  { name: 'requestPhoneSecurityChallenge', path: '/account/phone/security/challenges', method: 'POST', status: 202, result: challenge, body: { purpose: 'set_password' }, call: (client, opts) => client.requestPhoneSecurityChallenge({ purpose: 'set_password', phone: 'ignore', currentPassword: 'ignore' }, opts) },
  { name: 'setPasswordByPhone', path: '/account/phone/password', method: 'POST', result: { ok: true }, body: { challengeId, code, newPassword: password }, call: (client, opts) => client.setPasswordByPhone({ challengeId, code, newPassword: password, phone: 'ignore', currentPassword: 'ignore' }, opts) },
  { name: 'eraseAccountByPhone', path: '/account/phone/erase', method: 'POST', result: { ok: true }, body: { challengeId, code, confirmation: 'DELETE MY ACCOUNT' }, call: (client, opts) => client.eraseAccountByPhone({ challengeId, code, confirmation: 'DELETE MY ACCOUNT', phone: 'ignore', currentPassword: 'ignore' }, opts) },
];

for (const operation of operations) {
  test(`${operation.name} uses the frozen route, status, exact body and selected authentication`, async () => {
    let tokenReads = 0;
    const { client, calls } = recording([Response.json(operation.result, { status: operation.status ?? 200 })], { accessToken: async () => { tokenReads++; return 'fixture-fresh-bearer'; } });
    const { signal } = new AbortController();
    same(await operation.call(client, { signal }), operation.name === 'getAuthOptions' ? normalizedOptions : operation.result);
    assert.equal(calls.length, 1);
    assert.equal(tokenReads, operation.public ? 0 : 1);
    const [call] = calls;
    assert.equal(call.url, `${baseUrl}${operation.path}`);
    assert.equal(call.method, operation.method);
    assert.equal(call.signal, signal);
    assert.equal(call.credentials, 'omit');
    assert.equal(call.mode, undefined);
    assert.equal(call.headers.get('Accept'), 'application/json');
    assert.equal(call.headers.has('Authorization'), !operation.public);
    if (!operation.public) assert.equal(call.headers.get('Authorization') === 'Bearer fixture-fresh-bearer', true);
    for (const header of ['X-CSRF-Token', 'Origin']) assert.equal(call.headers.has(header), false);
    assert.equal(call.headers.has('Content-Type'), operation.body !== undefined);
    if (operation.body) assertBody(call, operation.body);
    else assert.equal(call.body, undefined);
  });
}

test('all five public phone methods omit browser cookies and never read a configured Bearer', async () => {
  const publicOperations = operations.filter(operation => operation.public);
  const { client, calls } = recording([browserSession, ...publicOperations.map(operation => operation.result)], { browserSession: true, accessToken: () => assert.fail('public and cookie calls must not read Bearer') });
  await client.getBrowserSession();
  for (const operation of publicOperations) await operation.call(client);
  assert.equal(calls.length, 6);
  for (const call of calls.slice(1)) {
    assert.equal(call.credentials, 'omit');
    assert.equal(call.mode, undefined);
    for (const header of ['Authorization', 'X-CSRF-Token', 'Origin']) assert.equal(call.headers.has(header), false);
  }
});

test('phone registration supports omitted or explicit optional passwords and never invents one', async () => {
  const { client, calls } = recording([accountSession, accountSession, accountSession]);
  await client.registerPhone(register);
  await client.registerPhone({ ...register, password: undefined, displayName: undefined });
  await client.registerPhone({ ...register, password, displayName: 'Named' });
  assertBody(calls[0], register);
  assertBody(calls[1], register);
  assertBody(calls[2], { ...register, password, displayName: 'Named' });
});

test('phone strings and leading-zero codes are forwarded unchanged for authoritative server validation', async () => {
  const { client, calls } = recording([challenge, accountSession, challenge]);
  await client.requestPhoneChallenge({ phone: '13800000000', purpose: 'register' });
  await client.loginPhone({ challengeId, code });
  await client.requestPhoneSecurityChallenge({ purpose: 'erase', phone: '+8613900000000' });
  assertBody(calls[0], { phone: '13800000000', purpose: 'register' });
  assertBody(calls[1], { challengeId, code });
  assertBody(calls[2], { purpose: 'erase' });
});

test('phone login and registration results never replace an existing application-owned Bearer', async () => {
  const { client, calls } = recording([accountSession, accountSession, accountSession, user]);
  await client.registerPhone(register);
  await client.loginPhone({ challengeId, code });
  await client.loginPhonePassword({ phone, password });
  await client.getAccount();
  assert.equal(calls.length, 4);
  for (const call of calls.slice(0, 3)) assert.equal(call.headers.has('Authorization'), false);
  assert.equal(calls[3].headers.get('Authorization') === 'Bearer fixture-original-bearer', true);
});

test('protected phone routes reuse browser CSRF without reading Bearer and only terminal account writes clear it', async () => {
  for (const operation of operations.filter(item => !item.public)) {
    const { client, calls } = recording([browserSession, operation.result, { ok: true }], { browserSession: true, accessToken: () => assert.fail('cookie calls must not read Bearer') });
    await client.getBrowserSession();
    same(await operation.call(client), operation.result);
    const call = calls[1];
    assert.equal(call.credentials, 'same-origin');
    assert.equal(call.mode, 'same-origin');
    assert.equal(call.headers.has('Authorization'), false);
    assert.equal(call.headers.get('X-CSRF-Token') === browserSession.csrfToken, operation.method === 'POST');
    assert.equal(call.headers.get('Origin'), operation.method === 'POST' ? origin : null);
    if (['setPasswordByPhone', 'eraseAccountByPhone'].includes(operation.name)) {
      await assert.rejects(client.requestPhoneSecurityChallenge({ purpose: 'erase' }), TypeError);
      assert.equal(calls.length, 2);
    } else {
      await client.requestPhoneSecurityChallenge({ purpose: 'erase' });
      assert.equal(calls[2].headers.get('X-CSRF-Token') === browserSession.csrfToken, true);
    }
  }
});

test('phone cookie writes require ordinary browser session initialization and do not request it implicitly', async () => {
  const { client, calls } = recording([], { browserSession: true, accessToken: () => assert.fail('must not read Bearer') });
  for (const operation of operations.filter(item => !item.public && item.method === 'POST')) await assert.rejects(operation.call(client), TypeError);
  assert.equal(calls.length, 0);
});

test('missing Bearer on a protected identity read never falls back to an ambient cookie', async () => {
  const { client, calls } = recording([errorResponse(401, 'unauthorized')], { accessToken: undefined });
  await assert.rejects(client.getAccountIdentity(), apiError(401, 'unauthorized'));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].credentials, 'omit');
  assert.equal(calls[0].headers.has('Authorization'), false);
});

for (const [status, errorCode, name] of [
  [401, 'invalid_phone_code', 'loginPhone'], [401, 'invalid_credentials', 'loginPhonePassword'],
  [403, 'csrf_failed', 'bindPhone'], [404, 'not_found', 'getAccountIdentity'],
  [409, 'phone_in_use', 'registerPhone'], [422, 'validation_error', 'requestPhoneChallenge'],
  [429, 'rate_limited', 'requestPhoneSecurityChallenge'], [503, 'sms_unavailable', 'requestPhoneChallenge'],
]) {
  test(`phone ${name} propagates ${status} without retry, replacement challenge or login`, async () => {
    const operation = operations.find(item => item.name === name);
    const { client, calls } = recording([errorResponse(status, errorCode)]);
    await assert.rejects(operation.call(client), apiError(status, errorCode));
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, `${baseUrl}${operation.path}`);
  });
}

test('pre-aborted phone methods preserve the original reason before any credential lookup or fetch', async () => {
  let reads = 0;
  const { client, calls } = recording([], { accessToken: () => { reads++; return 'unused'; } });
  const controller = new AbortController();
  const reason = new DOMException('Synthetic phone abort', 'AbortError');
  controller.abort(reason);
  for (const operation of operations) await assert.rejects(operation.call(client, { signal: controller.signal }), error => error === reason);
  assert.equal(reads, 0);
  assert.equal(calls.length, 0);
});

for (const name of ['loginPhone', 'bindPhone']) {
  test(`${name} preserves in-flight abort identity and does not resubmit an OTP`, async () => {
    const controller = new AbortController();
    const reason = new DOMException('Synthetic in-flight phone abort', 'AbortError');
    let calls = 0;
    const client = new AlliraClient({ baseUrl, fetch: async (_url, init) => {
      calls++;
      return new Promise((_resolve, reject) => {
        init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true });
        controller.abort(reason);
      });
    } });
    await assert.rejects(operations.find(item => item.name === name).call(client, { signal: controller.signal }), error => error === reason);
    assert.equal(calls, 1);
  });
}

for (const name of ['requestPhoneChallenge', 'setPasswordByPhone']) {
  test(`${name} preserves a non-HTTP transport failure without resending the request`, async () => {
    const reason = new TypeError('Synthetic identity transport failure');
    let calls = 0;
    const client = new AlliraClient({ baseUrl, fetch: async () => { calls++; throw reason; } });
    await assert.rejects(operations.find(item => item.name === name).call(client), error => error === reason);
    assert.equal(calls, 1);
  });
}

for (const name of ['setPasswordByPhone', 'eraseAccountByPhone']) {
  test(`${name} rejection retains browser CSRF but does not automatically retry the sensitive write`, async () => {
    const { client, calls } = recording([browserSession, errorResponse(409, 'account_busy'), challenge], { browserSession: true });
    await client.getBrowserSession();
    await assert.rejects(operations.find(item => item.name === name).call(client), apiError(409, 'account_busy'));
    await client.requestPhoneSecurityChallenge({ purpose: 'erase' });
    assert.equal(calls.length, 3);
    assert.equal(calls[2].headers.get('X-CSRF-Token') === browserSession.csrfToken, true);
  });
}

test('account export keeps optional identity compatible with old, null and current archives', async () => {
  const archive = { format: 'allira.account.v1', exportedAt: stamp, user, conversations: [], characters: [], wallet: null, ledger: [], usage: [], warnings: [] };
  const { client } = recording([archive, { ...archive, identity: null }, { ...archive, identity }]);
  assert.equal(Object.hasOwn(await client.exportAccountData(), 'identity'), false);
  assert.equal((await client.exportAccountData()).identity, null);
  same((await client.exportAccountData()).identity, identity);
});

test('disabled auth metadata is returned without sending a challenge or any hidden authentication request', async () => {
  const { client, calls } = recording([options]);
  same(await client.getAuthOptions(), normalizedOptions);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `${baseUrl}/auth/options`);
});
