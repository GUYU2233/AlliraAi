import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AlliraApiError, AlliraClient, StreamInterruptedError, StreamProtocolError } from '../dist/index.js';

const baseUrl = 'https://allira.example:8443/api/v1';
const origin = new URL(baseUrl).origin;
const time = '2026-11-19T00:00:00Z';
const user = { id: 'account-1', username: 'sample', displayName: 'Sample', createdAt: time, termsVersion: '2026-11-19', isAdult: true };
const session = { user, csrfToken: 'fixture-csrf', expiresAt: time };
const bearer = 'fixture-bearer';
const errorResponse = (status, code) => Response.json({ error: { code, message: 'Fixture error' } }, { status });
const apiError = (status, code) => error => error instanceof AlliraApiError && error.status === status && error.code === code;
const cookieClient = (fetch, options = {}) => new AlliraClient({ baseUrl, fetch, browserSession: true, ...options });
const unexpectedToken = () => assert.fail('request must not read a Bearer token');
const assertNoAuth = call => assert.equal(call.headers.has('Authorization'), false);
const assertCsrf = (call, value = session.csrfToken) => {
  assert.equal(call.headers.get('X-CSRF-Token') === value, true);
  assert.equal(call.headers.get('Origin'), origin);
  assertNoAuth(call);
};

function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}

async function collect(stream) {
  const events = [];
  for await (const event of stream) events.push(event);
  return events;
}

const run = {
  id: 'run/one', conversationId: 'conversation-1', clientMessageId: '746c958b-5864-4af1-96d3-80a7c5759d15',
  userMessageId: 'message-1', assistantMessageId: 'message-2', status: 'running', errorCode: null, createdAt: time, updatedAt: time,
};
const message = {
  id: run.assistantMessageId, conversationId: run.conversationId, runId: run.id, sequence: 2,
  role: 'assistant', content: 'Mock final', status: 'completed', createdAt: time,
};
const frame = (id, type, data) => new Response(`id: ${id}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`, {
  headers: { 'Content-Type': 'text/event-stream' },
});

for (const browserSession of [false, true]) {
  test(`explicit cookie exchange uses Bearer plus same-origin without CSRF (browserSession=${browserSession})`, async () => {
    const calls = [];
    let tokenReads = 0;
    const sdk = new AlliraClient({
      baseUrl, browserSession, accessToken: async () => { tokenReads++; return bearer; },
      fetch: async (url, init) => {
        calls.push({ url, ...init });
        return Response.json(calls.length === 1 ? session : calls.length === 2 ? { userId: user.id } : { ok: true });
      },
    });
    const { signal } = new AbortController();
    const result = await sdk.exchangeBrowserSession({ signal });
    assert.deepEqual(result.user, user);
    assert.equal(result.csrfToken === session.csrfToken, true);
    assert.equal(result.expiresAt, time);
    await sdk.getSession({ signal });
    await sdk.logoutBrowserSession({ signal });
    assert.deepEqual(calls.map(c => [c.method, c.url, c.credentials]), [
      ['POST', `${baseUrl}/auth/browser/session`, 'same-origin'],
      ['GET', `${baseUrl}/session`, browserSession ? 'same-origin' : 'omit'],
      ['POST', `${baseUrl}/auth/browser/logout`, 'same-origin'],
    ]);
    assert.equal(tokenReads, browserSession ? 1 : 2);
    assert.equal(calls[0].headers.get('Authorization') === `Bearer ${bearer}`, true);
    assert.equal(calls[0].headers.get('Origin'), origin);
    assert.equal(calls[0].headers.has('X-CSRF-Token'), false);
    assert.equal(calls[0].headers.has('Content-Type'), false);
    assert.equal(calls[0].body, undefined);
    assert.equal(calls[0].mode, 'same-origin');
    assert.equal(calls[1].headers.has('Authorization'), !browserSession);
    assert.equal(calls[1].headers.has('X-CSRF-Token'), false);
    assertCsrf(calls[2]);
    for (const call of calls) assert.equal(call.signal, signal);
  });
}

test('an explicit exchange without an account token fails locally rather than using an ambient cookie', async () => {
  const sdk = cookieClient(async () => assert.fail('must not fetch'), { accessToken: async () => undefined });
  await assert.rejects(sdk.exchangeBrowserSession(), { name: 'TypeError', message: 'exchangeBrowserSession requires an account accessToken' });
});

test('browser GET can restore before writes; chat writes then forward in-memory CSRF and Origin', async () => {
  const calls = [];
  const sdk = cookieClient(async (url, init) => {
    calls.push({ url, ...init });
    return Response.json(url.endsWith('/auth/browser/session') ? session : {});
  }, { accessToken: unexpectedToken });
  await assert.rejects(sdk.createConversation({}), TypeError);
  await assert.rejects(sdk.logoutBrowserSession(), TypeError);
  assert.equal(calls.length, 0);
  await sdk.getAccount();
  await sdk.getBrowserSession();
  await sdk.createConversation({ title: 'Fixture' });
  await sdk.cancelRun(run.id);
  assert.deepEqual(calls.map(c => [c.method, c.url]), [
    ['GET', `${baseUrl}/account`], ['GET', `${baseUrl}/auth/browser/session`],
    ['POST', `${baseUrl}/conversations`], ['POST', `${baseUrl}/runs/run%2Fone/cancel`],
  ]);
  for (const call of calls) {
    assert.equal(call.credentials, 'same-origin');
    assert.equal(call.mode, 'same-origin');
    assertNoAuth(call);
  }
  for (const call of calls.slice(0, 2)) {
    assert.equal(call.headers.has('X-CSRF-Token'), false);
    assert.equal(call.headers.has('Origin'), false);
  }
  assertCsrf(calls[2]);
  assertCsrf(calls[3]);
  assert.deepEqual(JSON.parse(calls[2].body), { title: 'Fixture' });
  assert.equal(calls[3].body, undefined);
});

test('public calls omit ambient cookies and Bearer even in an initialized browser client', async () => {
  const calls = [];
  const sdk = cookieClient(async (url, init) => {
    calls.push({ url, ...init });
    return Response.json(url.endsWith('/auth/browser/session') ? session : {});
  }, { accessToken: unexpectedToken });
  await sdk.getBrowserSession();
  await sdk.getInfo();
  await sdk.getTerms();
  await sdk.getModelRoutes();
  await sdk.createDevSession({ userId: 'fixture-user' });
  await sdk.register({ username: user.username, password: 'fixture-password', acceptedTermsVersion: '2026-11-19', isAdult: true });
  await sdk.login({ username: user.username, password: 'fixture-password' });
  await sdk.recoverAccount({ username: user.username, recoveryCode: 'fixture-recovery', newPassword: 'fixture-new-password' });
  const publicCalls = calls.slice(1);
  assert.equal(publicCalls.length, 7);
  for (const call of publicCalls) {
    assert.equal(call.credentials, 'omit');
    assertNoAuth(call);
    assert.equal(call.headers.has('X-CSRF-Token'), false);
    assert.equal(call.headers.has('Origin'), false);
  }
});

for (const [path, close] of [
  ['/auth/browser/logout', sdk => sdk.logoutBrowserSession()],
  ['/session/logout', sdk => sdk.logout()],
  ['/account/logout-all', sdk => sdk.logoutAll()],
  ['/account/password', sdk => sdk.changePassword({ currentPassword: 'fixture-password', newPassword: 'fixture-new-password' })],
  ['/account/erase', sdk => sdk.eraseAccount({ currentPassword: 'fixture-password', confirmation: 'DELETE MY ACCOUNT' })],
]) {
  test(`browser session-ending write ${path} clears only local CSRF on success`, async () => {
    const calls = [];
    const sdk = cookieClient(async (url, init) => {
      calls.push({ url, ...init });
      return Response.json(url.endsWith('/auth/browser/session') ? session : { ok: true });
    }, { accessToken: unexpectedToken });
    await sdk.getBrowserSession();
    await close(sdk);
    assert.equal(calls[1].url, `${baseUrl}${path}`);
    assert.equal(calls[1].method, 'POST');
    assert.equal(calls[1].credentials, 'same-origin');
    assertCsrf(calls[1]);
    await assert.rejects(sdk.createConversation({}), TypeError);
    assert.equal(calls.length, 2);
  });
}

test('cookie 401 clears CSRF; GET restore works afterward without retries or Bearer fallback', async () => {
  const calls = [];
  const sdk = cookieClient(async (url, init) => {
    calls.push({ url, ...init });
    if (url.endsWith('/account')) return errorResponse(401, 'unauthorized');
    return Response.json(url.endsWith('/auth/browser/session') ? session : { ok: true });
  }, { accessToken: unexpectedToken });
  await sdk.getBrowserSession();
  await assert.rejects(sdk.getAccount(), apiError(401, 'unauthorized'));
  await assert.rejects(sdk.logoutBrowserSession(), TypeError);
  assert.equal(calls.length, 2);
  await sdk.getBrowserSession();
  await sdk.logoutBrowserSession();
  assertCsrf(calls[3]);
  assert.equal(calls.length, 4);
});

test('browser write failures preserve the envelope, do not retry, and do not clear CSRF on 403 or 409', async () => {
  const errors = [[403, 'csrf_forbidden'], [409, 'account_busy']];
  let requests = 0;
  const sdk = cookieClient(async (url, init) => {
    if (url.endsWith('/auth/browser/session')) return Response.json(session);
    assertCsrf(init);
    const [status, code] = errors[requests++];
    return errorResponse(status, code);
  });
  await sdk.getBrowserSession();
  for (const [status, code] of errors) {
    await assert.rejects(sdk.eraseAccount({ currentPassword: 'fixture-password', confirmation: 'DELETE MY ACCOUNT' }), apiError(status, code));
  }
  assert.equal(requests, 2);
});

test('clearing local browser CSRF makes no request and cannot be undone by an in-flight restore', async () => {
  const response = deferred();
  let calls = 0;
  const sdk = cookieClient(() => { calls++; return response.promise; });
  const restoring = sdk.getBrowserSession();
  sdk.clearBrowserSession();
  response.resolve(Response.json(session));
  await restoring;
  await assert.rejects(sdk.logoutBrowserSession(), TypeError);
  assert.equal(calls, 1);
});

test('a late restore cannot override CSRF from a newer explicit exchange', async () => {
  const pendingRestore = deferred();
  const calls = [];
  const newer = { ...session, csrfToken: 'newer-fixture-csrf' };
  const sdk = cookieClient(async (url, init) => {
    calls.push({ url, ...init });
    if (init.method === 'GET') return pendingRestore.promise;
    return Response.json(url.endsWith('/auth/browser/session') ? newer : { ok: true });
  }, { accessToken: bearer });
  const older = sdk.getBrowserSession();
  await sdk.exchangeBrowserSession();
  pendingRestore.resolve(Response.json(session));
  await older;
  await sdk.logoutBrowserSession();
  assertCsrf(calls[2], newer.csrfToken);
});

test('an old in-flight 401 cannot clear CSRF loaded during a newer restore', async () => {
  const pendingError = deferred();
  const calls = [];
  const sdk = cookieClient(async (url, init) => {
    calls.push({ url, ...init });
    if (url.endsWith('/account')) return pendingError.promise;
    return Response.json(url.endsWith('/auth/browser/session') ? session : { ok: true });
  });
  await sdk.getBrowserSession();
  const old = sdk.getAccount();
  await sdk.getBrowserSession();
  pendingError.resolve(errorResponse(401, 'unauthorized'));
  await assert.rejects(old, apiError(401, 'unauthorized'));
  await sdk.logoutBrowserSession();
  assertCsrf(calls[3]);
});

test('pre-aborted browser calls neither fetch nor read Bearer; aborted restore does not cache CSRF', async () => {
  const controller = new AbortController();
  const reason = new DOMException('Fixture abort', 'AbortError');
  let calls = 0;
  const pendingRestore = deferred();
  const sdk = cookieClient(() => { calls++; return pendingRestore.promise; }, { accessToken: unexpectedToken });
  const restoring = sdk.getBrowserSession({ signal: controller.signal });
  controller.abort(reason);
  pendingRestore.resolve(Response.json(session));
  await assert.rejects(restoring, error => error === reason);
  await assert.rejects(sdk.exchangeBrowserSession({ signal: controller.signal }), error => error === reason);
  await assert.rejects(sdk.logoutBrowserSession(), TypeError);
  assert.equal(calls, 1);
});

test('separate browser clients do not share an in-memory CSRF token', async () => {
  const a = cookieClient(async () => Response.json(session));
  const b = cookieClient(async () => assert.fail('second instance must not write without restoring'));
  await a.getBrowserSession();
  await assert.rejects(b.logoutBrowserSession(), TypeError);
});

test('cookie SSE resumes explicitly after an EOF without Bearer, CSRF, or a hidden request', async () => {
  const calls = [];
  const sdk = cookieClient(async (url, init) => {
    calls.push({ url, ...init });
    return calls.length === 1
      ? frame(5, 'message.delta', { messageId: message.id, delta: 'Mock' })
      : frame(6, 'run.completed', { run: { ...run, status: 'completed' }, message });
  }, { accessToken: unexpectedToken });
  const { signal } = new AbortController();
  await assert.rejects(collect(sdk.streamRunEvents(run.id, { after: 4, signal })), error =>
    error instanceof StreamInterruptedError && error.lastEventId === 5);
  assert.equal(calls.length, 1);
  const events = await collect(sdk.streamRunEvents(run.id, { after: 5, signal }));
  assert.deepEqual(events, [{ id: 6, type: 'run.completed', data: { run: { ...run, status: 'completed' }, message } }]);
  assert.deepEqual(calls.map(c => c.url), [4, 5].map(after => `${baseUrl}/runs/run%2Fone/events?after=${after}`));
  for (const call of calls) {
    assert.equal(call.method, 'GET');
    assert.equal(call.credentials, 'same-origin');
    assert.equal(call.headers.get('Accept'), 'text/event-stream');
    assert.equal(call.headers.has('X-CSRF-Token'), false);
    assertNoAuth(call);
    assert.equal(call.signal, signal);
  }
});

test('cookie SSE rejects mismatched terminal IDs and clears CSRF only on an HTTP 401', async () => {
  let requests = 0;
  const sdk = cookieClient(async url => {
    if (url.endsWith('/auth/browser/session')) return Response.json(session);
    if (url.endsWith('/cancel')) return Response.json(run);
    if (++requests === 1) return frame(6, 'run.completed', { run: { ...run, id: 'another-run', status: 'completed' }, message });
    return errorResponse(401, 'unauthorized');
  });
  await sdk.getBrowserSession();
  await assert.rejects(collect(sdk.streamRunEvents(run.id, { after: 5 })), StreamProtocolError);
  await sdk.cancelRun(run.id);
  await assert.rejects(collect(sdk.streamRunEvents(run.id, { after: 5 })), apiError(401, 'unauthorized'));
  await assert.rejects(sdk.cancelRun(run.id), TypeError);
  assert.equal(requests, 2);
});
