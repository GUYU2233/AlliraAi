import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isDeepStrictEqual } from 'node:util';
import { AlliraClient, AlliraAdminClient, AlliraApiError } from '../dist/index.js';

// All Bearer, CSRF and CDK values are fake fixtures and are never printed.
const stamp = '2026-11-19T00:00:00Z';
const wallet = { balance: 25, unit: 'energy', economyCost: 1, fastCost: 3 };
const run = { id: 'run-1', conversationId: 'conversation-1', clientMessageId: '11111111-1111-4111-8111-111111111111', userMessageId: 'message-1', assistantMessageId: 'message-2', status: 'running', errorCode: null, createdAt: stamp, updatedAt: stamp };
const usage = { runId: run.id, requestedMode: 'fast', actualMode: 'economy', cost: 1, state: 'settled', promptCharacters: 42, outputCharacters: 17, firstTokenMs: 12.5, durationMs: 22.5, createdAt: stamp, updatedAt: stamp };
const adminSession = { authenticated: true, csrfToken: 'fake-csrf-value', expiresAt: '2026-11-19T01:00:00Z' };
const issued = { items: [{ code: 'fake-single-use-redemption', prefix: 'fixture /+', energy: 10, expiresAt: 1800000000 }] };
const cdk = { prefix: 'fixture /+', energy: 10, expiresAt: 1800000000, redeemedBy: null, redeemedAt: null, disabled: false, createdAt: stamp };
const overview = { reservedEnergy: 3, settledEnergy: 2, refundedEnergy: 1, runs: 5, promptCharacters: 42, outputCharacters: 17, firstTokenMsAverage: null, measurement: 'characters_not_tokens' };
const bodyIs = (actual, expected) => assert.equal(isDeepStrictEqual(JSON.parse(actual), expected), true, 'request body must match the exact wire fields');

function chatRecorder(responses) {
  const calls = [];
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example', accessToken: 'fake-account-session', fetch: async (url, init) => {
    calls.push({ url, ...init });
    return Response.json(responses[calls.length - 1], { status: url.endsWith('/runs') ? 202 : 200 });
  } });
  return { sdk, calls };
}

test('energy wallet, ledger, redemption and run usage preserve wire fields and filters', async () => {
  const ledger = { items: [{ id: 'entry-1', delta: -3, balanceAfter: 22, kind: 'run.reserved', referenceId: run.id, createdAt: stamp }, { id: 'entry-2', delta: 2, balanceAfter: 24, kind: 'run.route_refunded', referenceId: run.id, createdAt: stamp }], nextCursor: 'entry-2' };
  const { sdk, calls } = chatRecorder([wallet, ledger, wallet, usage, { items: [], nextCursor: null }]);
  const controller = new AbortController();
  const options = { signal: controller.signal };
  assert.deepEqual(await sdk.getEnergy(options), wallet);
  assert.deepEqual(await sdk.listEnergyLedger({ ...options, cursor: 'cursor /+', limit: 2 }), ledger);
  assert.deepEqual(await sdk.redeemEnergy({ code: 'fake-redemption-code', accessToken: 'must-not-forward' }, options), wallet);
  assert.deepEqual(await sdk.getRunUsage('run /+', options), usage);
  assert.deepEqual(await sdk.listEnergyLedger(options), { items: [], nextCursor: null });
  assert.deepEqual(calls.map(call => [call.method, call.url]), [
    ['GET', 'https://allira.example/api/v1/energy'],
    ['GET', 'https://allira.example/api/v1/energy/ledger?cursor=cursor+%2F%2B&limit=2'],
    ['POST', 'https://allira.example/api/v1/energy/redeem'],
    ['GET', 'https://allira.example/api/v1/runs/run%20%2F%2B/usage'],
    ['GET', 'https://allira.example/api/v1/energy/ledger'],
  ]);
  bodyIs(calls[2].body, { code: 'fake-redemption-code' });
  for (const index of [0, 1, 3, 4]) assert.equal(calls[index].body, undefined);
  for (const call of calls) {
    assert.equal(call.headers.get('Authorization') === 'Bearer fake-account-session', true);
    assert.equal(call.headers.has('X-CSRF-Token'), false);
    assert.equal(call.signal, controller.signal);
  }
});

test('run creation supports optional economy/fast without changing existing Run responses or default bodies', async () => {
  const { sdk, calls } = chatRecorder([run, run, run]);
  const controller = new AbortController();
  const input = { clientMessageId: run.clientMessageId, content: 'Synthetic message' };
  for (const mode of [undefined, 'economy', 'fast']) {
    const payload = mode === undefined ? input : { ...input, mode };
    assert.deepEqual(await sdk.createRun('c /+', payload, { signal: controller.signal }), run);
  }
  for (const [index, mode] of [undefined, 'economy', 'fast'].entries()) {
    assert.deepEqual(JSON.parse(calls[index].body), mode === undefined ? input : { ...input, mode });
    assert.equal(calls[index].url, 'https://allira.example/api/v1/conversations/c%20%2F%2B/runs');
    assert.equal(calls[index].signal, controller.signal);
  }
});

for (const state of ['reserved', 'settled', 'refunded']) {
  test(`usage ${state} supports null timings and keeps actual routing distinct from requested mode`, async () => {
    const value = { ...usage, state, firstTokenMs: null, durationMs: null, outputCharacters: 0 };
    const { sdk } = chatRecorder([value]);
    assert.deepEqual(await sdk.getRunUsage(run.id), value);
  });
}

for (const [status, code, operation] of [
  [402, 'insufficient_energy', sdk => sdk.createRun('c', { clientMessageId: run.clientMessageId, content: 'Synthetic' })],
  [409, 'cdk_redeemed', sdk => sdk.redeemEnergy({ code: 'fake-already-used-code' })],
  [410, 'cdk_expired', sdk => sdk.redeemEnergy({ code: 'fake-expired-code' })],
  [404, 'not_found', sdk => sdk.getRunUsage('missing')],
  [429, 'rate_limited', sdk => sdk.redeemEnergy({ code: 'fake-rate-limited-code' })],
]) {
  test(`energy ${status} ${code} errors do not retry a charge or redemption`, async () => {
    let count = 0;
    const sdk = new AlliraClient({ baseUrl: 'https://allira.example', fetch: async () => {
      count++;
      return Response.json({ error: { code, message: 'Request refused' } }, { status });
    } });
    await assert.rejects(operation(sdk), error => error instanceof AlliraApiError && error.status === status && error.code === code && error.message === 'Request refused');
    assert.equal(count, 1);
  });
}

test('admin CDK issuance, redacted listing, disabling and character usage stats use cookie and latest CSRF only', async () => {
  const calls = [];
  const page = { items: [cdk], nextCursor: 'next-prefix' };
  const responses = [adminSession, issued, page, { ok: true }, overview];
  const sdk = new AlliraAdminClient({ baseUrl: 'https://allira.example/api/v1/', accessToken: 'must-not-forward', fetch: async (url, init) => {
    calls.push({ url, ...init });
    return Response.json(responses[calls.length - 1]);
  } });
  const controller = new AbortController();
  const options = { signal: controller.signal };
  await sdk.getSession(options);
  const result = await sdk.issueCdks({ energy: 10, count: 2, expiresInDays: 7, code: 'must-not-forward' }, options);
  assert.equal(isDeepStrictEqual(result, issued), true);
  const listed = await sdk.listCdks({ ...options, cursor: 'cursor /+', limit: 2 });
  assert.deepEqual(listed, page);
  assert.equal(Object.hasOwn(listed.items[0], 'code'), false);
  assert.deepEqual(await sdk.disableCdk('fixture /+', options), { ok: true });
  assert.deepEqual(await sdk.getUsageOverview(options), overview);
  assert.deepEqual(calls.map(call => [call.method, call.url]), [
    ['GET', 'https://allira.example/api/v1/admin/session'],
    ['POST', 'https://allira.example/api/v1/admin/cdks'],
    ['GET', 'https://allira.example/api/v1/admin/cdks?cursor=cursor+%2F%2B&limit=2'],
    ['POST', 'https://allira.example/api/v1/admin/cdks/fixture%20%2F%2B/disable'],
    ['GET', 'https://allira.example/api/v1/admin/usage'],
  ]);
  bodyIs(calls[1].body, { energy: 10, count: 2, expiresInDays: 7 });
  assert.equal(calls[3].body, undefined);
  for (const call of calls) {
    assert.equal(call.credentials, 'same-origin');
    assert.equal(call.headers.has('Authorization'), false);
    assert.equal(call.signal, controller.signal);
    if (call.method === 'POST') {
      assert.equal(call.headers.get('X-CSRF-Token') === adminSession.csrfToken, true);
      assert.equal(call.headers.get('Origin'), 'https://allira.example');
    } else {
      assert.equal(call.headers.has('X-CSRF-Token'), false);
      assert.equal(call.headers.has('Origin'), false);
      assert.equal(call.body, undefined);
    }
  }
});

test('CDK issuance preserves optional defaults and listing cursor without manufacturing plaintext codes', async () => {
  const calls = [];
  const sdk = new AlliraAdminClient({ baseUrl: 'https://allira.example', fetch: async (url, init) => {
    calls.push({ url, ...init });
    return Response.json(url.endsWith('/session') ? adminSession : init.method === 'POST' ? issued : { items: [{ ...cdk, redeemedBy: 'account-1', redeemedAt: stamp, disabled: true }], nextCursor: null });
  } });
  await sdk.getSession();
  await sdk.issueCdks({ energy: 1 });
  const page = await sdk.listCdks();
  bodyIs(calls[1].body, { energy: 1 });
  assert.equal(calls[2].url, 'https://allira.example/api/v1/admin/cdks');
  assert.equal(page.items[0].redeemedBy, 'account-1');
  assert.equal(Object.hasOwn(page.items[0], 'code'), false);
});

test('CDK writes require prior independent admin initialization and never bootstrap with user credentials', async () => {
  let calls = 0;
  const sdk = new AlliraAdminClient({ baseUrl: 'https://allira.example', fetch: async () => { calls++; return Response.json({}); } });
  await assert.rejects(sdk.issueCdks({ energy: 10 }), TypeError);
  await assert.rejects(sdk.disableCdk('fixture'), TypeError);
  assert.equal(calls, 0);
});

for (const [status, code] of [[401, 'unauthorized'], [403, 'csrf_invalid'], [404, 'not_found'], [409, 'cdk_redeemed']]) {
  test(`admin CDK ${status} preserves envelope and never reissues automatically`, async () => {
    let calls = 0;
    const sdk = new AlliraAdminClient({ baseUrl: 'https://allira.example', fetch: async () => ++calls === 1 ? Response.json(adminSession) : Response.json({ error: { code, message: 'Request refused' } }, { status }) });
    await sdk.getSession();
    await assert.rejects(sdk.issueCdks({ energy: 10 }), error => error instanceof AlliraApiError && error.status === status && error.code === code && error.message === 'Request refused');
    assert.equal(calls, 2);
    if (status === 401) {
      await assert.rejects(sdk.disableCdk('fixture'), TypeError);
      assert.equal(calls, 2);
    }
  });
}

test('aborted energy, usage and CDK requests preserve the reason without requests', async () => {
  let calls = 0;
  let tokens = 0;
  const fetch = async () => { calls++; return Response.json({}); };
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example', accessToken: () => { tokens++; return 'fake-unused-session'; }, fetch });
  const admin = new AlliraAdminClient({ baseUrl: 'https://allira.example', fetch });
  const controller = new AbortController();
  const reason = new DOMException('Synthetic abort', 'AbortError');
  controller.abort(reason);
  const options = { signal: controller.signal };
  for (const request of [() => sdk.getEnergy(options), () => sdk.listEnergyLedger(options), () => sdk.redeemEnergy({ code: 'fake-unused-code' }, options), () => sdk.getRunUsage('id', options), () => admin.issueCdks({ energy: 1 }, options), () => admin.listCdks(options), () => admin.disableCdk('fixture', options), () => admin.getUsageOverview(options)]) {
    await assert.rejects(request(), error => error === reason);
  }
  assert.equal(calls, 0);
  assert.equal(tokens, 0);
});
