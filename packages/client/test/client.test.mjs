import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AlliraClient, AlliraApiError, StreamInterruptedError, StreamProtocolError } from '../dist/index.js';

const run = {
  id: 'run-1', conversationId: 'conversation-1', clientMessageId: 'aa5af048-3552-4ad2-bf90-378efcdb4b35',
  userMessageId: 'user-1', assistantMessageId: 'assistant-1', status: 'running',
  errorCode: null, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
};
const message = {
  id: 'assistant-1', conversationId: run.conversationId, runId: run.id, sequence: 2,
  role: 'assistant', content: '你好🌍', status: 'completed', createdAt: run.createdAt,
};
const frame = (id, type, data, newline = '\n') =>
  `id: ${id}${newline}event: ${type}${newline}data: ${JSON.stringify(data)}${newline}${newline}`;
const doneFrame = (id = 3, type = 'run.completed', newline = '\n') =>
  frame(id, type, { run: { ...run, status: type.slice(4) }, message }, newline);
const client = (fetch, options = {}) => new AlliraClient({ baseUrl: 'https://allira.example/', fetch, ...options });
function sse(text, chunkSize = 1, onCancel = () => {}) {
  const bytes = new TextEncoder().encode(text);
  let offset = 0;
  return new Response(new ReadableStream({
    pull(controller) {
      if (offset === bytes.length) { controller.close(); return; }
      controller.enqueue(bytes.slice(offset, offset += chunkSize));
    },
    cancel: onCancel,
  }), { headers: { 'Content-Type': 'text/event-stream' } });
}
async function collect(stream) { const events = []; for await (const e of stream) events.push(e); return events; }

test('routes, camelCase payloads, async tokens, pagination, and request signals', async () => {
  const calls = [];
  let token = 'first';
  const sdk = client(async (url, init) => {
    calls.push({ url, ...init });
    return Response.json({ items: [], nextCursor: null });
  }, { accessToken: async () => token });
  const controller = new AbortController();
  const options = { signal: controller.signal };
  await sdk.createDevSession({ userId: 'test-user' }, options);
  await sdk.listConversations({ ...options, cursor: 'cursor /+', limit: 12 });
  token = 'refreshed';
  await sdk.createConversation({ title: 'Work', instructions: null }, options);
  await sdk.getConversation('a/b', options);
  await sdk.listMessages('a/b', { ...options, cursor: '', limit: 3 });
  await sdk.createRun('a/b', { clientMessageId: run.clientMessageId, content: 'Hello' }, options);
  await sdk.getRun('a/b', options);
  await sdk.cancelRun('a/b', options);
  assert.deepEqual(calls.map(c => [c.method, c.url]), [
    ['POST', 'https://allira.example/api/v1/dev/session'],
    ['GET', 'https://allira.example/api/v1/conversations?cursor=cursor+%2F%2B&limit=12'],
    ['POST', 'https://allira.example/api/v1/conversations'],
    ['GET', 'https://allira.example/api/v1/conversations/a%2Fb'],
    ['GET', 'https://allira.example/api/v1/conversations/a%2Fb/messages?cursor=&limit=3'],
    ['POST', 'https://allira.example/api/v1/conversations/a%2Fb/runs'],
    ['GET', 'https://allira.example/api/v1/runs/a%2Fb'],
    ['POST', 'https://allira.example/api/v1/runs/a%2Fb/cancel'],
  ]);
  assert.equal(calls[0].headers.get('Authorization'), null);
  assert.equal(calls[1].headers.get('Authorization'), 'Bearer first');
  assert.equal(calls[2].headers.get('Authorization'), 'Bearer refreshed');
  assert.deepEqual(JSON.parse(calls[0].body), { userId: 'test-user' });
  assert.deepEqual(JSON.parse(calls[2].body), { title: 'Work', instructions: null });
  assert.deepEqual(JSON.parse(calls[5].body), { clientMessageId: run.clientMessageId, content: 'Hello' });
  assert.equal(calls[2].headers.get('Content-Type'), 'application/json');
  assert.equal(calls[1].headers.get('Content-Type'), null);
  for (const c of calls) assert.equal(c.signal, controller.signal);
});

test('base URL supports API URL and rejects non-HTTP/query/fragment URLs', async () => {
  let actual;
  const sdk = new AlliraClient({ baseUrl: 'http://localhost:8000/api/v1/', fetch: async url => { actual = url; return Response.json(run); } });
  await sdk.getRun(run.id);
  assert.equal(actual, 'http://localhost:8000/api/v1/runs/run-1');
  for (const baseUrl of ['ftp://example.com', 'https://example.com/?x=1', 'https://example.com/#x']) {
    assert.throws(() => new AlliraClient({ baseUrl }), TypeError);
  }
});

test('HTTP errors preserve status and envelope; POST is never retried', async () => {
  let count = 0;
  const sdk = client(async () => { count++; return Response.json({ error: { code: 'conflict', message: 'Already running' } }, { status: 409 }); });
  await assert.rejects(sdk.createRun('c', { clientMessageId: run.clientMessageId, content: 'Hi' }), error => {
    assert.ok(error instanceof AlliraApiError);
    assert.equal(error.status, 409); assert.equal(error.code, 'conflict'); assert.equal(error.message, 'Already running'); return true;
  });
  assert.equal(count, 1);
  await assert.rejects(client(async () => new Response('upstream unavailable', { status: 502 })).getRun('r'), error => error.code === 'http_error' && error.status === 502);
});

test('SSE decodes split UTF-8, split CRLF, multiline frames, comments and terminal canonical data', async () => {
  const text = ': heartbeat\r\n\r\n' + frame(1, 'run.started', { run }, '\r\n') +
    'id: 2\r\nevent: message.delta\r\ndata: {"messageId":"assistant-1",\r\ndata: "delta":"你好🌍"}\r\n\r\n' +
    doneFrame(3, 'run.completed', '\r\n') + frame(4, 'message.delta', { messageId: 'assistant-1', delta: 'must not appear' });
  let cancelled = false;
  const events = await collect(client(async () => sse(text, 1, () => { cancelled = true; })).streamRunEvents('r'));
  assert.deepEqual(events.map(e => [e.id, e.type]), [[1, 'run.started'], [2, 'message.delta'], [3, 'run.completed']]);
  assert.equal(events[1].data.delta, '你好🌍');
  assert.equal(events[2].data.message.content, '你好🌍');
  assert.equal(events[2].data.run.status, 'completed');
  assert.equal(cancelled, true);
});

for (const type of ['run.completed', 'run.failed', 'run.cancelled']) {
  test(`terminal ${type} completes without waiting for EOF`, async () => {
    let cancelled = false;
    const sdk = client(async () => new Response(new ReadableStream({
      start(c) { c.enqueue(new TextEncoder().encode(doneFrame(3, type))); },
      cancel() { cancelled = true; },
    })));
    const events = await collect(sdk.streamRunEvents('r'));
    assert.equal(events.length, 1);
    assert.equal(events[0].type, type);
    assert.equal(cancelled, true);
  });
}

test('unconfirmed EOF throws with last id; explicit resume uses after and Bearer', async () => {
  const calls = [];
  const sdk = client(async (url, init) => {
    calls.push({ url, init });
    return sse(calls.length === 1 ? frame(1, 'run.started', { run }) : doneFrame(2));
  }, { accessToken: 'secret' });
  const received = [];
  await assert.rejects(async () => { for await (const e of sdk.streamRunEvents('r')) received.push(e); }, error => error instanceof StreamInterruptedError && error.lastEventId === 1);
  assert.equal(calls.length, 1);
  const resumed = await collect(sdk.streamRunEvents('r', { after: received.at(-1).id }));
  assert.equal(resumed[0].id, 2);
  assert.equal(calls[1].url, 'https://allira.example/api/v1/runs/r/events?after=1');
  assert.equal(calls[1].init.headers.get('Authorization'), 'Bearer secret');
  assert.equal(calls[1].init.headers.get('Accept'), 'text/event-stream');
});

test('incomplete final frame is not delivered; CR-only streams work', async () => {
  await assert.rejects(collect(client(async () => sse('id: 1\nevent: run.completed\ndata: {}\n')).streamRunEvents('r')), StreamInterruptedError);
  const events = await collect(client(async () => sse(doneFrame(1, 'run.completed', '\r'))).streamRunEvents('r'));
  assert.equal(events[0].id, 1);
});

test('invalid SSE ids, JSON and event types fail explicitly', async () => {
  for (const text of [frame('nope', 'run.started', { run }), frame(1, 'unknown', {}), 'id: 1\nevent: run.started\ndata: !\n\n', frame(1, 'run.started', { run }) + doneFrame(1)]) {
    await assert.rejects(collect(client(async () => sse(text)).streamRunEvents('r')), StreamProtocolError);
  }
  for (const after of [-1, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
    await assert.rejects(collect(client(async () => { throw new Error('must not fetch'); }).streamRunEvents('r', { after })), TypeError);
  }
});

test('stream HTTP error envelope remains an API error', async () => {
  const sdk = client(async () => Response.json({ error: { code: 'not_found', message: 'No run' } }, { status: 404 }));
  await assert.rejects(collect(sdk.streamRunEvents('r')), error => error instanceof AlliraApiError && error.code === 'not_found' && error.status === 404);
});

test('pre-aborted requests propagate original reason without fetch', async () => {
  let calls = 0;
  const sdk = client(async () => { calls++; throw new Error('must not fetch'); });
  const controller = new AbortController();
  const reason = new DOMException('Stopped', 'AbortError');
  controller.abort(reason);
  await assert.rejects(sdk.getRun('r', { signal: controller.signal }), error => error === reason);
  await assert.rejects(collect(sdk.streamRunEvents('r', { signal: controller.signal })), error => error === reason);
  assert.equal(calls, 0);
});

test('abort during pending stream read is propagated, not translated to EOF', async () => {
  const controller = new AbortController();
  const reason = new DOMException('Stop streaming', 'AbortError');
  let ready;
  const opened = new Promise(resolve => { ready = resolve; });
  const sdk = client(async (_url, init) => new Response(new ReadableStream({
    start(stream) {
      init.signal.addEventListener('abort', () => stream.error(init.signal.reason));
      ready();
    },
  })));
  const result = collect(sdk.streamRunEvents('r', { signal: controller.signal }));
  await opened;
  controller.abort(reason);
  await assert.rejects(result, error => error === reason);
});

test('transport interruption preserves the original error and does not reconnect', async () => {
  const failure = new TypeError('connection reset');
  let pulls = 0;
  let calls = 0;
  const sdk = client(async () => {
    calls++;
    return new Response(new ReadableStream({
      pull(c) {
        if (pulls++ === 0) c.enqueue(new TextEncoder().encode(frame(1, 'run.started', { run })));
        else c.error(failure);
      },
    }));
  });
  const events = [];
  await assert.rejects(async () => { for await (const event of sdk.streamRunEvents('r')) events.push(event); }, error => error === failure);
  assert.equal(events.length, 1);
  assert.equal(calls, 1);
});

test('sync token callbacks and 202 run responses are supported', async () => {
  const sdk = client(async (_url, init) => {
    assert.equal(init.headers.get('Authorization'), 'Bearer sync-token');
    return Response.json(run, { status: 202 });
  }, { accessToken: () => 'sync-token' });
  assert.deepEqual(await sdk.createRun('c', { clientMessageId: run.clientMessageId, content: 'Hello' }), run);
});

test('consumer break releases stream without hidden reconnect', async () => {
  let cancelled = false;
  let calls = 0;
  const sdk = client(async () => { calls++; return sse(frame(1, 'run.started', { run }) + doneFrame(), 2, () => { cancelled = true; }); });
  for await (const _event of sdk.streamRunEvents('r')) break;
  assert.equal(cancelled, true);
  assert.equal(calls, 1);
});
