// Explicit, one-run real-relay verification. NEVER included in automated tests.
// Run only on the remote host with ALLIRA_REAL_SMOKE=1 after deliberate authorization.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { AlliraClient } from '../packages/client/dist/index.js';

let stage = 'authorization';
let client;
let conversation;
let run;
try {
  assert.equal(process.env.ALLIRA_REAL_SMOKE, '1', 'Explicit real-request authorization is required.');
  const baseUrl = process.env.ALLIRA_BASE_URL ?? 'http://127.0.0.1:8000';
  const url = new URL(baseUrl);
  assert.ok(['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname));
  const bootstrap = new AlliraClient({ baseUrl });
  stage = 'provider-check';
  const info = await bootstrap.getInfo();
  assert.equal(info.provider, 'openai');
  assert.equal(info.allowDevAuth, true);
  const session = await bootstrap.createDevSession({ userId: `relay-smoke-${randomUUID()}` });
  client = new AlliraClient({ baseUrl, accessToken: session.accessToken });
  const initial = await client.getEnergy();
  conversation = await client.createConversation({ title: 'Relay transport verification' });
  const input = {
    clientMessageId: randomUUID(), content: '请只回复“连接正常”，不要添加其他内容。', mode: 'economy',
  };
  stage = 'one-real-run';
  run = await client.createRun(conversation.id, input);
  let terminal;
  let eventCount = 0;
  for await (const event of client.streamRunEvents(run.id, { signal: AbortSignal.timeout(90000) })) {
    eventCount += 1;
    if (['run.completed', 'run.failed', 'run.cancelled'].includes(event.type)) terminal = event.data;
  }
  assert.equal(terminal?.run.status, 'completed');
  assert.ok(terminal.message.content.trim().length > 0);
  stage = 'accounting-and-idempotency';
  const usage = await client.getRunUsage(run.id);
  assert.equal(usage.actualMode, 'economy');
  assert.equal(usage.state, 'settled');
  const replay = await client.createRun(conversation.id, input);
  assert.equal(replay.id, run.id);
  const final = await client.getEnergy();
  assert.equal(final.balance, initial.balance - usage.cost);
  const messages = await client.listRecentMessages(conversation.id);
  assert.equal(messages.items.length, 2);
  assert.equal(messages.items[1].content, terminal.message.content);
  assert.equal((await client.cancelRun(run.id)).status, 'completed');
  await client.deleteConversation(conversation.id);
  conversation = undefined;
  await client.logout();
  client = undefined;
  console.log(JSON.stringify({
    result: 'passed', transport: 'real relay via API + Fetch SDK + SSE', provider: info.provider,
    realRuns: 1, status: terminal.run.status, eventCount,
    outputCharacters: usage.outputCharacters, energyDebit: usage.cost,
    firstPublicTextMs: usage.firstTokenMs,
    verified: ['canonical terminal', 'strict replay without second charge', 'usage settlement', 'history', 'cleanup'],
  }));
} catch {
  // Do not log upstream bodies, keys, bearer sessions, chat content or assertion actual/expected.
  if (client && run) {
    try { await client.cancelRun(run.id); } catch { /* Best-effort cleanup, not a retry. */ }
  }
  if (client && conversation) {
    try { await client.deleteConversation(conversation.id); } catch { /* Keep diagnostics server-side. */ }
  }
  if (client) {
    try { await client.logout(); } catch { /* Never reveal a session. */ }
  }
  console.error(JSON.stringify({ result: 'failed', stage, detail: 'Sensitive diagnostic values omitted; no request retry was performed.' }));
  process.exitCode = 1;
}
