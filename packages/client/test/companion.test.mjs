import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AlliraApiError, AlliraClient } from '../dist/index.js';

// Entirely synthetic Fetch fixtures: no API, model, persistent storage or live secrets.
const baseUrl = 'https://allira.example:8443/api/v1';
const origin = new URL(baseUrl).origin;
const stamp = '2026-11-19T05:00:00Z';
const setupId = 'bb6a43e2-2d4f-4d9e-ac2a-5c04f630b462';
const presets = {
  defaultPresetId: 'daily',
  items: ['daily', 'listen', 'focus'].map(id => ({
    id, title: `Synthetic ${id}`, description: `Synthetic ${id} description`,
    openingHint: 'Synthetic <script>untrusted text</script> “茶” 😀', defaultMode: 'economy',
  })),
};
const conversation = { id: 'conversation-1', title: 'Synthetic daily', instructions: 'Server-owned preset instructions', createdAt: stamp, updatedAt: stamp };
const started = { conversation, presetId: 'daily', replayed: false };
const session = {
  user: { id: 'account-1', username: 'sample', displayName: 'Sample', createdAt: stamp, termsVersion: '2026-11-19', isAdult: true },
  csrfToken: 'fixture-companion-csrf', expiresAt: stamp,
};
const errorResponse = (status, code) => Response.json({ error: { code, message: 'Synthetic companion refusal' } }, { status });
function recording(responses, options = {}) {
  const calls = [];
  const sdk = new AlliraClient({
    baseUrl, accessToken: 'fixture-companion-bearer', ...options,
    fetch: async (url, init) => {
      calls.push({ url, ...init });
      const response = responses[calls.length - 1];
      return response instanceof Response ? response : Response.json(response);
    },
  });
  return { sdk, calls };
}

for (const browserSession of [false, true]) {
  test(`companion presets remain public with credentials omitted in ${browserSession ? 'cookie' : 'Bearer'} mode`, async () => {
    let tokenReads = 0;
    const { sdk, calls } = recording([presets], {
      browserSession, accessToken: () => { tokenReads++; throw new Error('Public request must not read credentials'); },
    });
    const { signal } = new AbortController();
    assert.deepEqual(await sdk.getCompanionPresets({ signal }), presets);
    assert.equal(tokenReads, 0);
    assert.equal(calls.length, 1);
    const [call] = calls;
    assert.equal(call.url, `${baseUrl}/companion/presets`);
    assert.equal(call.method, 'GET');
    assert.equal(call.signal, signal);
    assert.equal(call.credentials, 'omit');
    assert.equal(call.mode, undefined);
    assert.equal(call.body, undefined);
    assert.equal(call.headers.get('Accept'), 'application/json');
    for (const header of ['Authorization', 'X-CSRF-Token', 'Origin', 'Content-Type']) assert.equal(call.headers.has(header), false);
  });
}

test('companion setup accepts 201 and uses the existing per-request Bearer callback and AbortSignal', async () => {
  let tokenReads = 0;
  const { sdk, calls } = recording([Response.json(started, { status: 201 }), { ...started, replayed: true }], {
    accessToken: async () => { tokenReads++; return `fixture-bearer-${tokenReads}`; },
  });
  const { signal } = new AbortController();
  const input = { clientSetupId: setupId, presetId: 'daily' };
  assert.deepEqual(await sdk.startCompanionConversation(input, { signal }), started);
  assert.deepEqual(await sdk.startCompanionConversation(input, { signal }), { ...started, replayed: true });
  assert.equal(tokenReads, 2);
  assert.equal(calls.length, 2); // Explicit caller replay only; no hidden setup, Run, message or charge request.
  for (const [index, call] of calls.entries()) {
    assert.equal(call.url, `${baseUrl}/companion/conversations`);
    assert.equal(call.method, 'POST');
    assert.equal(call.signal, signal);
    assert.equal(call.credentials, 'omit');
    assert.equal(call.mode, undefined);
    assert.equal(call.headers.get('Authorization') === `Bearer fixture-bearer-${index + 1}`, true);
    assert.equal(call.headers.get('Accept'), 'application/json');
    assert.equal(call.headers.get('Content-Type'), 'application/json');
    assert.equal(call.headers.has('X-CSRF-Token'), false);
    assert.equal(call.headers.has('Origin'), false);
    assert.deepEqual(JSON.parse(call.body), input);
  }
});

test('setup body allowlist keeps exactly the caller UUID and optional preset without adding defaults', async () => {
  const { sdk, calls } = recording(Array(5).fill(started));
  await sdk.startCompanionConversation({ clientSetupId: setupId, presetId: 'focus', title: 'ignore', instructions: 'ignore', relationship: 'ignore', mode: 'fast', content: 'ignore', accessToken: 'ignore' });
  await sdk.startCompanionConversation({ clientSetupId: setupId });
  await sdk.startCompanionConversation({ clientSetupId: setupId, presetId: undefined });
  await sdk.startCompanionConversation({ clientSetupId: setupId, presetId: 'listen' });
  await sdk.startCompanionConversation({ clientSetupId: setupId, presetId: 'daily' });
  assert.deepEqual(calls.map(call => JSON.parse(call.body)), [
    { clientSetupId: setupId, presetId: 'focus' }, { clientSetupId: setupId },
    { clientSetupId: setupId }, { clientSetupId: setupId, presetId: 'listen' },
    { clientSetupId: setupId, presetId: 'daily' },
  ]);
});

test('public presets do not initialize browser CSRF or turn a cookie setup into a Bearer request', async () => {
  let tokenReads = 0;
  const { sdk, calls } = recording([presets], {
    browserSession: true, accessToken: () => { tokenReads++; throw new Error('Cookie mode must not read Bearer'); },
  });
  await sdk.getCompanionPresets();
  await assert.rejects(sdk.startCompanionConversation({ clientSetupId: setupId }), {
    name: 'TypeError', message: 'Call exchangeBrowserSession() or getBrowserSession() before a browser session write',
  });
  assert.equal(tokenReads, 0);
  assert.equal(calls.length, 1);
});

test('cookie setup reuses loaded CSRF, same-origin credentials and Origin but never Bearer', async () => {
  let tokenReads = 0;
  const { sdk, calls } = recording([session, presets, Response.json(started, { status: 201 })], {
    browserSession: true, accessToken: () => { tokenReads++; throw new Error('Cookie mode must not read Bearer'); },
  });
  await sdk.getBrowserSession();
  await sdk.getCompanionPresets();
  const { signal } = new AbortController();
  assert.deepEqual(await sdk.startCompanionConversation({ clientSetupId: setupId }, { signal }), started);
  assert.equal(calls.length, 3);
  assert.equal(tokenReads, 0);
  assert.equal(calls[1].credentials, 'omit');
  const call = calls[2];
  assert.equal(call.url, `${baseUrl}/companion/conversations`);
  assert.equal(call.method, 'POST');
  assert.equal(call.credentials, 'same-origin');
  assert.equal(call.mode, 'same-origin');
  assert.equal(call.signal, signal);
  assert.equal(call.headers.get('Origin'), origin);
  assert.equal(call.headers.get('X-CSRF-Token') === session.csrfToken, true);
  assert.equal(call.headers.has('Authorization'), false);
  assert.deepEqual(JSON.parse(call.body), { clientSetupId: setupId });
});

test('a missing default Bearer does not opt into ambient cookie authentication', async () => {
  const { sdk, calls } = recording([errorResponse(401, 'unauthorized')], { accessToken: undefined });
  await assert.rejects(sdk.startCompanionConversation({ clientSetupId: setupId }), error => error instanceof AlliraApiError && error.status === 401);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].credentials, 'omit');
  assert.equal(calls[0].headers.has('Authorization'), false);
  assert.equal(calls[0].headers.has('X-CSRF-Token'), false);
});

const operations = [
  ['getCompanionPresets', sdk => sdk.getCompanionPresets()],
  ['startCompanionConversation', sdk => sdk.startCompanionConversation({ clientSetupId: setupId, presetId: 'daily' })],
];
for (const [name, operation] of operations) {
  test(`${name} preserves a non-HTTP transport error without any retry or extra request`, async () => {
    const reason = new TypeError('Synthetic companion transport interruption');
    let calls = 0;
    const sdk = new AlliraClient({ baseUrl, fetch: async () => { calls++; throw reason; } });
    await assert.rejects(operation(sdk), error => error === reason);
    assert.equal(calls, 1);
  });
}

test('pre-aborted companion methods preserve the abort reason before auth lookup or fetching', async () => {
  let tokenReads = 0;
  const { sdk, calls } = recording([], { accessToken: () => { tokenReads++; return 'unused'; } });
  const controller = new AbortController();
  const reason = new DOMException('Synthetic companion abort', 'AbortError');
  controller.abort(reason);
  await assert.rejects(sdk.getCompanionPresets({ signal: controller.signal }), error => error === reason);
  await assert.rejects(sdk.startCompanionConversation({ clientSetupId: setupId }, { signal: controller.signal }), error => error === reason);
  assert.equal(calls.length, 0);
  assert.equal(tokenReads, 0);
});

test('in-flight companion setup abort forwards its original reason and never replays the POST', async () => {
  const controller = new AbortController();
  const reason = new DOMException('Synthetic in-flight companion abort', 'AbortError');
  let calls = 0;
  const sdk = new AlliraClient({ baseUrl, fetch: async (_url, init) => {
    calls++;
    return new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true });
      controller.abort(reason);
    });
  } });
  await assert.rejects(sdk.startCompanionConversation({ clientSetupId: setupId }, { signal: controller.signal }), error => error === reason);
  assert.equal(calls, 1);
});

for (const [status, code] of [[401, 'unauthorized'], [403, 'forbidden'], [404, 'not_found'], [409, 'idempotency_conflict'], [422, 'validation_error'], [429, 'rate_limited'], [503, 'service_unavailable']]) {
  test(`companion setup surfaces ${status} ${code} without rebuilding a target, generating or retrying`, async () => {
    const { sdk, calls } = recording([errorResponse(status, code)]);
    await assert.rejects(sdk.startCompanionConversation({ clientSetupId: setupId, presetId: 'daily' }), error => error instanceof AlliraApiError && error.status === status && error.code === code && error.message === 'Synthetic companion refusal');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, `${baseUrl}/companion/conversations`);
  });
}

test('public preset HTTP failures keep the usual envelope instead of attempting a login', async () => {
  const { sdk, calls } = recording([errorResponse(503, 'service_unavailable')]);
  await assert.rejects(sdk.getCompanionPresets(), error => error instanceof AlliraApiError && error.status === 503 && error.code === 'service_unavailable');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].credentials, 'omit');
});

test('cookie setup CSRF failure does not trigger Bearer fallback, session exchange or retry', async () => {
  let tokenReads = 0;
  const { sdk, calls } = recording([session, errorResponse(403, 'csrf_failed')], {
    browserSession: true, accessToken: () => { tokenReads++; throw new Error('Cookie mode must not read Bearer'); },
  });
  await sdk.getBrowserSession();
  await assert.rejects(sdk.startCompanionConversation({ clientSetupId: setupId }), error => error instanceof AlliraApiError && error.status === 403 && error.code === 'csrf_failed');
  assert.equal(tokenReads, 0);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].headers.has('Authorization'), false);
});
