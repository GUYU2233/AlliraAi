import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AlliraClient, StreamInterruptedError, StreamProtocolError } from '../dist/index.js';

const run = {
  id: 'run-1', conversationId: 'conversation-1', clientMessageId: 'test-client-id',
  userMessageId: 'user-1', assistantMessageId: 'assistant-1', status: 'running',
  errorCode: null, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z',
};
const message = {
  id: run.assistantMessageId, conversationId: run.conversationId, runId: run.id,
  sequence: 2, role: 'assistant', content: 'Canonical text', status: 'completed', createdAt: run.createdAt,
};
const terminal = { run: { ...run, status: 'completed' }, message };
const frame = (id, type, data) => `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
async function collect(stream) { const events = []; for await (const event of stream) events.push(event); return events; }
function response(bytes, chunkSize = 3, onCancel = () => {}) {
  let offset = 0;
  return new Response(new ReadableStream({
    pull(controller) {
      if (offset >= bytes.length) { controller.close(); return; }
      controller.enqueue(bytes.slice(offset, offset += chunkSize));
    },
    cancel: onCancel,
  }), { headers: { 'Content-Type': 'text/event-stream' } });
}
function stream(text, options = {}, runId = run.id) {
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example', fetch: async () => response(new TextEncoder().encode(text)) });
  return sdk.streamRunEvents(runId, options);
}
function protocol(error) {
  assert.ok(error instanceof StreamProtocolError);
  assert.doesNotMatch(error.message, /sensitive-payload/);
  return true;
}

for (const [name, type, data] of [
  ['null payload', 'run.started', null],
  ['array payload', 'run.started', []],
  ['primitive payload', 'run.started', 'sensitive-payload'],
  ['absent run', 'run.started', {}],
  ['partial run', 'run.started', { run: { id: run.id } }],
  ['null run', 'run.started', { run: null }],
  ['wrong run field type', 'run.started', { run: { ...run, assistantMessageId: 3 } }],
  ['absent error code', 'run.started', { run: { ...run, errorCode: undefined } }],
  ['invalid run status', 'run.started', { run: { ...run, status: 'sensitive-payload' } }],
  ['non-running start', 'run.started', { run: { ...run, status: 'completed' } }],
  ['null delta payload', 'message.delta', null],
  ['absent delta message id', 'message.delta', { delta: 'sensitive-payload' }],
  ['wrong delta message id type', 'message.delta', { messageId: 3, delta: 'sensitive-payload' }],
  ['wrong delta text type', 'message.delta', { messageId: run.assistantMessageId, delta: {} }],
  ['absent terminal message', 'run.completed', { run: terminal.run }],
  ['partial terminal message', 'run.completed', { ...terminal, message: { id: message.id } }],
  ['wrong content type', 'run.completed', { ...terminal, message: { ...message, content: 3 } }],
  ['non-integer message sequence', 'run.completed', { ...terminal, message: { ...message, sequence: 1.5 } }],
  ['zero message sequence', 'run.completed', { ...terminal, message: { ...message, sequence: 0 } }],
  ['unsafe message sequence', 'run.completed', { ...terminal, message: { ...message, sequence: Number.MAX_SAFE_INTEGER + 1 } }],
  ['wrong terminal role', 'run.completed', { ...terminal, message: { ...message, role: 'user' } }],
  ['wrong terminal run status', 'run.completed', { ...terminal, run: { ...run, status: 'failed' } }],
  ['wrong terminal message status', 'run.completed', { ...terminal, message: { ...message, status: 'generating' } }],
]) {
  test(`SSE rejects ${name} without exposing payload`, async () => {
    await assert.rejects(collect(stream(frame(1, type, data))), protocol);
  });
}

for (const [name, text] of [
  ['started request run id', frame(1, 'run.started', { run: { ...run, id: 'sensitive-payload' } })],
  ['terminal request run id', frame(1, 'run.completed', { ...terminal, run: { ...terminal.run, id: 'sensitive-payload' } })],
  ['terminal message run id', frame(1, 'run.completed', { ...terminal, message: { ...message, runId: 'sensitive-payload' } })],
  ['terminal message conversation id', frame(1, 'run.completed', { ...terminal, message: { ...message, conversationId: 'sensitive-payload' } })],
  ['terminal assistant message id', frame(1, 'run.completed', { ...terminal, message: { ...message, id: 'sensitive-payload' } })],
  ['started to delta id', frame(1, 'run.started', { run }) + frame(2, 'message.delta', { messageId: 'sensitive-payload', delta: 'text' })],
  ['delta to delta id', frame(1, 'message.delta', { messageId: run.assistantMessageId, delta: 'text' }) + frame(2, 'message.delta', { messageId: 'sensitive-payload', delta: 'text' })],
  ['started to terminal conversation id', frame(1, 'run.started', { run }) + frame(2, 'run.completed', { run: { ...terminal.run, conversationId: 'sensitive-payload' }, message: { ...message, conversationId: 'sensitive-payload' } })],
  ['started to terminal assistant id', frame(1, 'run.started', { run }) + frame(2, 'run.completed', { run: { ...terminal.run, assistantMessageId: 'sensitive-payload' }, message: { ...message, id: 'sensitive-payload' } })],
  ['started to terminal user message id', frame(1, 'run.started', { run }) + frame(2, 'run.completed', { ...terminal, run: { ...terminal.run, userMessageId: 'sensitive-payload' } })],
  ['started to terminal client message id', frame(1, 'run.started', { run }) + frame(2, 'run.completed', { ...terminal, run: { ...terminal.run, clientMessageId: 'sensitive-payload' } })],
  ['resumed delta to terminal assistant id', frame(5, 'message.delta', { messageId: 'sensitive-payload', delta: 'text' }) + frame(6, 'run.completed', terminal)],
]) {
  test(`SSE rejects mismatched ${name}`, async () => {
    await assert.rejects(collect(stream(text)), protocol);
  });
}

test('SSE resume after a processed start may begin with deltas and end with a canonical snapshot', async () => {
  const events = await collect(stream(
    frame(5, 'message.delta', { messageId: message.id, delta: 'Part 1' }) +
    frame(6, 'message.delta', { messageId: message.id, delta: 'Part 2' }) +
    frame(7, 'run.completed', terminal), { after: 4 },
  ));
  assert.deepEqual(events.map(event => [event.id, event.type]), [[5, 'message.delta'], [6, 'message.delta'], [7, 'run.completed']]);
  assert.deepEqual(events[2].data.message, message);
});

test('SSE resume may contain a terminal snapshot only', async () => {
  const events = await collect(stream(frame(9, 'run.completed', terminal), { after: 8 }));
  assert.equal(events.length, 1);
  assert.equal(events[0].id, 9);
});

test('SSE maintains its own validation snapshot when a consumer mutates a yielded run', async () => {
  const iterator = stream(frame(1, 'run.started', { run }) + frame(2, 'run.completed', terminal));
  const first = await iterator.next();
  first.value.data.run.conversationId = 'consumer-local-change';
  first.value.data.run.assistantMessageId = 'consumer-local-change';
  const second = await iterator.next();
  assert.equal(second.value.type, 'run.completed');
  assert.equal((await iterator.next()).done, true);
});

for (const [name, bytes] of [
  ['invalid continuation byte', new Uint8Array([...new TextEncoder().encode('data: sensitive-payload'), 0xc3, 0x28, 0x0a, 0x0a])],
  ['truncated multibyte character at EOF', new Uint8Array([...new TextEncoder().encode(': sensitive-payload'), 0xf0, 0x9f])],
]) {
  test(`SSE rejects fatal UTF-8 ${name} and releases the reader`, async () => {
    const result = response(bytes, bytes.length);
    const sdk = new AlliraClient({ baseUrl: 'https://allira.example', fetch: async () => result });
    await assert.rejects(collect(sdk.streamRunEvents(run.id)), error => protocol(error) && /UTF-8/.test(error.message));
    // A closed stream still has to release its reader after decoder flush fails.
    assert.equal(result.body.locked, false);
  });
}

test('SSE protocol errors do not echo unknown event names, ids, or raw JSON', async () => {
  for (const text of [
    frame(1, 'sensitive-payload', {}),
    frame('sensitive-payload', 'run.started', { run }),
    'id: 1\nevent: run.started\ndata: sensitive-payload\n\n',
  ]) {
    await assert.rejects(collect(stream(text)), protocol);
  }
});

test('SSE shape validation is applied before yield and cancels an open stream', async () => {
  let cancelled = false;
  const sdk = new AlliraClient({ baseUrl: 'https://allira.example', fetch: async () => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode(frame(1, 'message.delta', { messageId: message.id, delta: null }))); },
    cancel() { cancelled = true; },
  })) });
  const received = [];
  await assert.rejects(async () => { for await (const event of sdk.streamRunEvents(run.id)) received.push(event); }, StreamProtocolError);
  assert.deepEqual(received, []);
  assert.equal(cancelled, true);
});

test('a valid resumed delta followed by EOF remains interrupted, never complete', async () => {
  await assert.rejects(collect(stream(frame(7, 'message.delta', { messageId: message.id, delta: '' }), { after: 6 })), error => error instanceof StreamInterruptedError && error.lastEventId === 7);
});
