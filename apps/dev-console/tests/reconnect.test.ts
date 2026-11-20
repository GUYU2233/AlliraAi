import test from 'node:test';
import assert from 'node:assert/strict';
import { AlliraApiError, StreamInterruptedError, StreamProtocolError } from '../../../packages/client/src/index.ts';
import { delay, maxReconnects, retryableStreamError, retryDelay } from '../src/reconnect.ts';

test('automatic stream retry is bounded and limited to network/EOF/server errors', () => {
  assert.equal(maxReconnects, 5);
  for (const error of [new TypeError('network'), new StreamInterruptedError(4), new AlliraApiError(503, 'unavailable', 'private')]) assert.equal(retryableStreamError(error), true);
  for (const error of [new StreamProtocolError('invalid'), new DOMException('Aborted', 'AbortError'), ...[400, 401, 403, 404, 409, 429].map(status => new AlliraApiError(status, 'no', 'private'))]) assert.equal(retryableStreamError(error), false);
  for (let attempt = 1; attempt <= 5; ++attempt) {
    const low = retryDelay(attempt, 0), high = retryDelay(attempt, 1);
    assert.ok(low > 0 && high >= low && high <= 6_000);
  }
});

test('an abort cancels the backoff timer instead of reconnecting a stale view', async () => {
  const controller = new AbortController();
  const waiting = delay(5_000, controller.signal);
  controller.abort();
  await assert.rejects(waiting, { name: 'AbortError' });
  assert.throws(() => delay(0, controller.signal), { name: 'AbortError' });
});
