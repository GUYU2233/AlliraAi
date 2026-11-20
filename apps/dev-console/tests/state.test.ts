import test from 'node:test';
import assert from 'node:assert/strict';
import { applyRunEvent, beginReplay, mergeMessages, newPending, retryPayload, sameScope } from '../src/state.ts';
import type { Message, Run } from '../../../packages/client/src/index.ts';

const run: Run = { id: 'r1', conversationId: 'c1', clientMessageId: 'uuid1', userMessageId: 'u1', assistantMessageId: 'a1', status: 'running', errorCode: null, createdAt: '2026-01-01T00:00:00Z', updatedAt: '2026-01-01T00:00:00Z' };
const message: Message = { id: 'a1', conversationId: 'c1', runId: 'r1', sequence: 2, role: 'assistant', content: 'canonical final', status: 'completed', createdAt: run.createdAt };

test('replay from zero resets partial buffer and ignores duplicate event ids', () => {
  let state = beginReplay(run);
  state = applyRunEvent(state, { id: 1, type: 'run.started', data: { run } });
  state = applyRunEvent(state, { id: 2, type: 'message.delta', data: { messageId: 'a1', delta: 'partial' } });
  assert.equal(state.content, 'partial');
  assert.equal(applyRunEvent(state, { id: 2, type: 'message.delta', data: { messageId: 'a1', delta: 'partial' } }), state);
  state = beginReplay(run);
  assert.equal(state.content, '');
  assert.equal(state.lastEventId, 0);
  state = applyRunEvent(state, { id: 2, type: 'message.delta', data: { messageId: 'a1', delta: 'partial' } });
  assert.equal(state.content, 'partial');
});

test('canonical terminal snapshot replaces deltas and is immutable to late events', () => {
  const partial = applyRunEvent(beginReplay(run), { id: 1, type: 'message.delta', data: { messageId: 'a1', delta: 'wrong prefix' } });
  const terminal = applyRunEvent(partial, { id: 2, type: 'run.completed', data: { run: { ...run, status: 'completed' }, message } });
  assert.equal(terminal.content, 'canonical final');
  assert.equal(terminal.run.status, 'completed');
  assert.equal(applyRunEvent(terminal, { id: 3, type: 'message.delta', data: { messageId: 'a1', delta: 'late' } }), terminal);
});

test('failed and cancelled terminal snapshots are authoritative too', () => {
  for (const status of ['failed', 'cancelled'] as const) {
    const state = applyRunEvent(beginReplay(run), { id: 1, type: status === 'failed' ? 'run.failed' : 'run.cancelled', data: { run: { ...run, status }, message: { ...message, status, content: status } } });
    assert.equal(state.content, status);
    assert.equal(state.run.status, status);
  }
});

test('wrong run/message events cannot corrupt selected assistant', () => {
  const state = beginReplay(run);
  assert.equal(applyRunEvent(state, { id: 1, type: 'run.started', data: { run: { ...run, id: 'other' } } }), state);
  assert.equal(applyRunEvent(state, { id: 2, type: 'message.delta', data: { messageId: 'other', delta: 'x' } }), state);
  assert.equal(applyRunEvent(state, { id: 3, type: 'run.completed', data: { run: { ...run, id: 'other' }, message } }), state);
});

test('auth epoch, selected view epoch, and conversation id reject stale results', () => {
  const current = { auth: 2, view: 5, conversationId: 'c1' };
  assert.equal(sameScope({ ...current }, current), true);
  for (const previous of [{ ...current, auth: 1 }, { ...current, view: 4 }, { ...current, conversationId: 'c2' }]) assert.equal(sameScope(previous, current), false);
});

test('older pages prepend chronologically without duplicate message nodes', () => {
  const first = { ...message, id: 'first', sequence: 1 };
  const stale = { ...message, content: 'old' };
  assert.deepEqual(mergeMessages([first, stale], [message]).map(m => [m.id, m.content]), [['first', message.content], ['a1', message.content]]);
});

test('ambiguous retry is forever bound to exact original content and UUID', () => {
  const pending = newPending('uuid-original', '  原文\n<script>unsafe</script>  ');
  pending.uncertain = true;
  assert.deepEqual(retryPayload(pending), { clientMessageId: 'uuid-original', content: '  原文\n<script>unsafe</script>  ' });
  assert.notEqual(retryPayload(pending), retryPayload(pending));
});

test('ambiguous retry preserves original routing mode as well as content', () => {
  const pending = newPending('uuid-mode', 'original', 'fast');
  pending.uncertain = true;
  const first = retryPayload(pending);
  assert.deepEqual(first, { clientMessageId: 'uuid-mode', content: 'original', mode: 'fast' });
  first.mode = 'economy';
  assert.equal(retryPayload(pending).mode, 'fast');
});
