import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isDeepStrictEqual } from 'node:util';
import { AlliraApiError, AlliraClient } from '../dist/index.js';

// Synthetic metadata only. No issuer, SMS, provider, API or model is contacted.
const baseUrl = 'https://allira.example:8443/api/v1';
const legacy = {
  phone: { enabled: false, simulated: false, reason: 'disabled', countryCallingCodes: ['+86'] },
  social: ['wechat', 'qq'].map(provider => ({ provider, enabled: false, simulated: false, reason: 'disabled' })),
  legacyPasswordEnabled: true, registrationEnabled: true,
};
const disabledOidc = { enabled: false, provider: 'logto', simulated: false, reason: null, authorizationOrigin: null };
const oidc = { ...legacy, backend: 'oidc', legacyPasswordEnabled: false,
  oidc: { enabled: true, provider: 'logto', simulated: false, reason: null, authorizationOrigin: 'https://identity.example:9443' } };
const same = (actual, expected) => assert.equal(isDeepStrictEqual(actual, expected), true, 'metadata matches without dumping response fields');
function fixture(value, extra = {}) {
  const calls = [];
  const client = new AlliraClient({ baseUrl, accessToken: () => assert.fail('public metadata must never read Bearer'), ...extra,
    fetch: async (url, init) => { calls.push({ url, ...init }); return value instanceof Response ? value : Response.json(value); } });
  return { client, calls };
}

for (const browserSession of [false, true]) {
  test(`old-server auth options default to legacy and disabled OIDC in browser mode ${browserSession}`, async () => {
    const before = structuredClone(legacy);
    const { client, calls } = fixture(legacy, { browserSession });
    const { signal } = new AbortController();
    same(await client.getAuthOptions({ signal }), { ...legacy, backend: 'legacy', oidc: disabledOidc });
    same(legacy, before);
    assert.equal(calls.length, 1);
    const [call] = calls;
    assert.equal(call.url, `${baseUrl}/auth/options`);
    assert.equal(call.method, 'GET');
    assert.equal(call.signal, signal);
    assert.equal(call.credentials, 'omit');
    assert.equal(call.mode, undefined);
    assert.equal(call.body, undefined);
    for (const name of ['Authorization', 'Origin', 'X-CSRF-Token', 'X-Allira-OIDC-CSRF']) assert.equal(call.headers.has(name), false);
  });
}

test('OIDC metadata preserves the configured trusted authorization origin without enabling legacy channels', async () => {
  const { client, calls } = fixture(oidc);
  same(await client.getAuthOptions(), oidc);
  assert.equal(calls.length, 1);
});

test('missing authorization origin defaults to null rather than trusting a start URL', async () => {
  const { authorizationOrigin, ...olderOidc } = oidc.oidc;
  const { client } = fixture({ ...oidc, oidc: olderOidc });
  same(await client.getAuthOptions(), { ...oidc, oidc: { ...olderOidc, authorizationOrigin: null } });
});

test('missing OIDC metadata remains disabled even if a partial server declares an OIDC backend', async () => {
  const { client } = fixture({ ...legacy, backend: 'oidc' });
  same(await client.getAuthOptions(), { ...legacy, backend: 'oidc', oidc: disabledOidc });
});

test('OIDC metadata without a backend does not implicitly select the new backend', async () => {
  const { client } = fixture({ ...legacy, oidc: oidc.oidc });
  same(await client.getAuthOptions(), { ...legacy, backend: 'legacy', oidc: oidc.oidc });
});

test('disabled or simulated OIDC metadata is returned honestly without initializing any transition', async () => {
  const value = { ...oidc, oidc: { ...oidc.oidc, enabled: false, simulated: true, reason: 'fixture-unconfigured', authorizationOrigin: null } };
  const { client, calls } = fixture(value);
  same(await client.getAuthOptions(), value);
  assert.equal(calls.length, 1);
});

for (const [name, value] of [
  ['null envelope', null], ['array envelope', []], ['unknown backend', { ...oidc, backend: 'unknown' }],
  ['null OIDC field', { ...oidc, oidc: null }], ['wrong OIDC provider', { ...oidc, oidc: { ...oidc.oidc, provider: 'other' } }],
  ['nonboolean enabled', { ...oidc, oidc: { ...oidc.oidc, enabled: 'true' } }],
  ['nonboolean simulated', { ...oidc, oidc: { ...oidc.oidc, simulated: 1 } }],
  ['invalid reason', { ...oidc, oidc: { ...oidc.oidc, reason: {} } }],
  ['invalid origin', { ...oidc, oidc: { ...oidc.oidc, authorizationOrigin: {} } }],
  ['wrong phone shape', { ...legacy, phone: null }],
  ['wrong country codes', { ...legacy, phone: { ...legacy.phone, countryCallingCodes: [86] } }],
  ['wrong social provider', { ...legacy, social: [{ ...legacy.social[0], provider: 'unknown' }] }],
  ['wrong registration gate', { ...legacy, registrationEnabled: 'yes' }],
]) {
  test(`auth metadata rejects ${name} without including a raw payload or inventing availability`, async () => {
    const { client, calls } = fixture(value);
    await assert.rejects(client.getAuthOptions(), error => error instanceof TypeError && error.message === 'Invalid authentication options response');
    assert.equal(calls.length, 1);
  });
}

for (const [status, code] of [[404, 'not_found'], [403, 'oidc_disabled'], [503, 'oidc_unavailable']]) {
  test(`auth metadata propagates ${status} ${code} instead of fabricating legacy or OIDC readiness`, async () => {
    const { client, calls } = fixture(Response.json({ error: { code, message: 'Synthetic refusal' } }, { status }));
    await assert.rejects(client.getAuthOptions(), error => error instanceof AlliraApiError && error.status === status && error.code === code);
    assert.equal(calls.length, 1);
  });
}

test('public auth metadata honors pre-abort and does not make a hidden request', async () => {
  const controller = new AbortController();
  const reason = new DOMException('Synthetic stop', 'AbortError');
  controller.abort(reason);
  const { client, calls } = fixture(legacy);
  await assert.rejects(client.getAuthOptions({ signal: controller.signal }), error => error === reason);
  assert.equal(calls.length, 0);
});

test('auth metadata preserves the transport error identity with no fallback or retry', async () => {
  const reason = new TypeError('Synthetic network failure');
  let calls = 0;
  const client = new AlliraClient({ baseUrl, fetch: async () => { calls++; throw reason; } });
  await assert.rejects(client.getAuthOptions(), error => error === reason);
  assert.equal(calls, 1);
});
