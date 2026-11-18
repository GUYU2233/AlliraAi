// Run on the development server only, after building packages/client.
// It creates a development conversation and never prints session credentials.
import assert from 'node:assert/strict';
import { AlliraClient, AlliraApiError } from '../packages/client/dist/index.js';

const baseUrl = process.env.ALLIRA_SMOKE_BASE_URL ?? 'http://127.0.0.1:8000';
const bootstrap = new AlliraClient({ baseUrl });
const session = await bootstrap.createDevSession({ userId: 'sdk-smoke' });
const client = new AlliraClient({ baseUrl, accessToken: session.accessToken });
const chat = await client.createConversation({ title: 'SDK smoke test', instructions: 'Be kind.' });
const clientMessageId = crypto.randomUUID();
const input = { clientMessageId, content: '你好，测试流式对话🌍' };
const run = await client.createRun(chat.id, input);
const events = [];
for await (const event of client.streamRunEvents(run.id)) events.push(event);
assert.equal(events[0].type, 'run.started');
assert.equal(events.at(-1).type, 'run.completed');
assert.equal(events.at(-1).data.message.content, 'Mock response.');
assert.deepEqual(events.map(event => event.id), [1, 2, 3, 4]);
const replay = await client.createRun(chat.id, input);
assert.equal(replay.id, run.id);
const resumed = [];
for await (const event of client.streamRunEvents(run.id, { after: 2 })) resumed.push(event);
assert.deepEqual(resumed, events.slice(2));
const page = await client.listMessages(chat.id);
assert.equal(page.items.length, 2);
assert.equal(page.items[0].content, input.content);
assert.equal(page.items[1].content, 'Mock response.');
const finished = await client.getRun(run.id);
assert.equal(finished.status, 'completed');
assert.deepEqual(await client.cancelRun(run.id), finished);
await assert.rejects(client.createRun(chat.id, { ...input, content: 'different' }), error =>
  error instanceof AlliraApiError && error.status === 409 && error.code === 'idempotency_conflict');
console.log(JSON.stringify({
  result: 'passed', transport: 'real HTTP Fetch + SSE',
  status: finished.status, eventCount: events.length, messageCount: page.items.length,
  verified: ['unicode', 'canonical terminal', 'replay', 'idempotency', 'pagination', 'terminal immutability'],
}));
