import { test, expect } from '@playwright/test';
import type { Page as BrowserPage, Route } from '@playwright/test';
import { fixture, origin, openAdvanced, openChatAdvanced, openDeveloper, openDetails } from './browser-fixture.ts';
const stamp = '2026-01-01T00:00:00Z';
const conversation = (id: string, title: string) => ({ id, title, instructions: null, createdAt: stamp, updatedAt: stamp });
const c1 = conversation('c1', '会话甲');
const c2 = conversation('c2', '会话乙');
const running = { id: 'r1', conversationId: 'c1', clientMessageId: 'uuid', userMessageId: 'u1', assistantMessageId: 'a1', status: 'running', errorCode: null, createdAt: stamp, updatedAt: stamp };
const assistant = { id: 'a1', conversationId: 'c1', runId: 'r1', sequence: 2, role: 'assistant', content: 'Mock response.', status: 'completed', createdAt: stamp };
const json = (route: Route, data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
const event = (id: number, type: string, data: unknown) => `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
async function login(page: BrowserPage) {
  await page.goto(`${origin}/test/`); await openDeveloper(page);
  await page.getByLabel('开发用户 ID').fill('ui-fixture-user');
  await page.getByRole('button', { name: '开发登录', exact: true }).click();
  await page.getByRole('button', { name: '会话甲', exact: true }).click();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  await openAdvanced(page); await openChatAdvanced(page); await openDetails(page, '自定义新会话');
}
function chatDefaults(route: Route, path: string) {
  if (path === '/info') return json(route, { environment: 'development', provider: 'mock', allowDevAuth: true, consoleEnabled: true, adminEnabled: true });
  if (path === '/terms') return json(route, { version: '2026-11-19', adultOnly: true, content: '仅限成年人测试。' });
  if (path === '/models') return json(route, { items: [] });
  if (path === '/dev/session') return json(route, { accessToken: 'fixture-only-bearer', tokenType: 'Bearer' });
  if (path === '/session/logout') return json(route, { ok: true });
  if (path === '/conversations') return json(route, { items: [c1, c2], nextCursor: null });
  if (path.endsWith('/messages/recent')) return json(route, { items: [], nextCursor: null });
  return json(route, { error: { code: 'not_found', message: 'never display raw secret' } }, 404);
}

test('chat keeps drafts, replays from zero without duplication, and renders unsafe text inertly', async ({ page }) => {
  let created = false;
  let subscriptions = 0;
  await fixture(page, async (route, path) => {
    if (path.endsWith('/state')) return json(route, { conversation: path.includes('/c2/') ? c2 : c1, activeRun: created && path.includes('/c1/') ? running : null, lastSequence: 0 });
    if (path === '/conversations/c1/runs') { created = true; return json(route, running); }
    if (path === '/runs/r1/events') {
      subscriptions++;
      expect(new URL(route.request().url()).searchParams.get('after')).toBe('0');
      const partial = event(1, 'run.started', { run: running }) + event(2, 'message.delta', { messageId: 'a1', delta: 'Mock ' });
      const terminal = event(3, 'message.delta', { messageId: 'a1', delta: 'response.' }) + event(4, 'run.completed', { run: { ...running, status: 'completed' }, message: assistant });
      return route.fulfill({ contentType: 'text/event-stream', body: partial + (subscriptions > 1 ? terminal : '') });
    }
    return chatDefaults(route, path);
  });
  await login(page);
  const unsafe = '<img src=x onerror="globalThis.pwned=1">';
  await page.getByLabel('消息内容', { exact: true }).fill(unsafe);
  await page.getByRole('button', { name: '会话乙', exact: true }).click();
  await page.getByLabel('消息内容', { exact: true }).fill('乙的草稿');
  await page.getByRole('button', { name: '会话甲', exact: true }).click();
  await expect(page.getByLabel('消息内容', { exact: true })).toHaveValue(unsafe);
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.locator('.messages')).toContainText('Mock');
  await expect(page.getByRole('button', { name: '断开订阅', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '断开订阅', exact: true }).click();
  await page.getByRole('button', { name: '恢复订阅', exact: true }).click();
  await expect(page.locator('.messages .message-content')).toHaveText(['Mock response.']);
  await expect(page.locator('.run-status')).toContainText('已完成');
  expect(await page.evaluate(() => 'pwned' in globalThis)).toBe(false);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  await page.setViewportSize({ width: 375, height: 800 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('ambiguous POST retry preserves UUID and exact original text despite edited draft', async ({ page }) => {
  const bodies: unknown[] = [];
  let created = false;
  await fixture(page, async (route, path) => {
    if (path.endsWith('/state')) return json(route, { conversation: c1, activeRun: created ? running : null, lastSequence: 0 });
    if (path === '/conversations/c1/runs') {
      bodies.push(route.request().postDataJSON());
      if (bodies.length === 1) return route.abort('connectionfailed');
      created = true; return json(route, running);
    }
    if (path === '/runs/r1/events') return route.fulfill({ contentType: 'text/event-stream', body: event(1, 'run.started', { run: running }) + event(2, 'run.completed', { run: { ...running, status: 'completed' }, message: assistant }) });
    return chatDefaults(route, path);
  });
  await login(page);
  await page.getByLabel('消息内容', { exact: true }).fill('  原文\n不能改  ');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.getByRole('button', { name: '使用同一编号和原文重试' })).toBeEnabled();
  await page.getByLabel('消息内容', { exact: true }).fill('另一份草稿');
  await page.getByRole('button', { name: '使用同一编号和原文重试' }).click();
  await expect(page.locator('.run-status')).toContainText('已完成');
  expect(bodies).toHaveLength(2);
  expect(bodies[0]).toEqual(bodies[1]);
  expect(bodies[0]).toMatchObject({ content: '  原文\n不能改  ', clientMessageId: expect.stringMatching(/^[0-9a-f-]{36}$/) });
  await expect(page.getByLabel('消息内容', { exact: true })).toHaveValue('另一份草稿');
});

test('late selected-conversation response cannot overwrite a newer selection', async ({ page }) => {
  let release!: () => void;
  let received!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const ready = new Promise<void>(resolve => { received = resolve; });
  await fixture(page, async (route, path) => {
    if (path === '/conversations/c1/state') {
      received(); await gate;
      return json(route, { conversation: c1, activeRun: null, lastSequence: 0 }).catch(() => {});
    }
    if (path === '/conversations/c2/state') return json(route, { conversation: c2, activeRun: null, lastSequence: 0 });
    return chatDefaults(route, path);
  });
  await page.goto(`${origin}/test/`); await openDeveloper(page);
  await page.getByLabel('开发用户 ID').fill('user');
  await page.getByRole('button', { name: '开发登录', exact: true }).click();
  await page.getByRole('button', { name: '会话甲', exact: true }).click();
  await ready;
  await page.getByRole('button', { name: '会话乙', exact: true }).click();
  await expect(page.getByRole('heading', { name: '会话乙', exact: true })).toBeVisible();
  release();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  await expect(page.getByRole('heading', { name: '会话乙', exact: true })).toBeVisible();
});

test('a stale generating message with no active state recovers the real terminal snapshot', async ({ page }) => {
  const reads: string[] = [];
  let release!: () => void; let received!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const ready = new Promise<void>(resolve => { received = resolve; });
  await fixture(page, async (route, path) => {
    if (path.endsWith('/state')) { reads.push('state'); return json(route, { conversation: c1, activeRun: null, lastSequence: 2 }); }
    if (path.endsWith('/messages/recent')) { reads.push('recent'); received(); await gate; return json(route, { items: [{ ...assistant, status: 'generating', content: '过时的本地片段' }], nextCursor: null }); }
    if (path === '/runs/r1') { reads.push('run'); return json(route, { ...running, status: 'completed' }); }
    if (path === '/runs/r1/events') { reads.push('events'); expect(new URL(route.request().url()).searchParams.get('after')).toBe('0'); return route.fulfill({ contentType: 'text/event-stream', body: event(1, 'run.started', { run: running }) + event(2, 'message.delta', { messageId: 'a1', delta: '不能叠加的增量' }) + event(3, 'run.completed', { run: { ...running, status: 'completed' }, message: assistant }) }); }
    return chatDefaults(route, path);
  });
  await page.goto(`${origin}/test/`); await openDeveloper(page);
  await page.getByLabel('开发用户 ID').fill('snapshot-race');
  await page.getByRole('button', { name: '开发登录', exact: true }).click();
  await page.getByRole('button', { name: '会话甲', exact: true }).click();
  await ready;
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeDisabled();
  release();
  await expect(page.locator('.run-status')).toContainText('已完成');
  await expect(page.locator('.messages .assistant .message-content')).toHaveText('Mock response.');
  await expect(page.locator('.messages')).not.toContainText('过时的本地片段');
  expect(reads.slice(0, 4)).toEqual(['state', 'recent', 'run', 'events']);
});

test('a newer run started in another window supersedes the older active-state snapshot', async ({ page }) => {
  const newer = { ...running, id: 'r2', clientMessageId: 'uuid2', userMessageId: 'u2', assistantMessageId: 'a2' };
  const newerMessage = { ...assistant, id: 'a2', runId: 'r2', sequence: 4, content: '第二个任务终态' };
  const replays: string[] = [];
  let release!: () => void; let received!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const ready = new Promise<void>(resolve => { received = resolve; });
  await fixture(page, async (route, path) => {
    if (path.endsWith('/state')) return json(route, { conversation: c1, activeRun: running, lastSequence: 2 });
    if (path.endsWith('/messages/recent')) { received(); await gate; return json(route, { items: [assistant, { ...newerMessage, status: 'generating', content: '第二个任务片段' }], nextCursor: null }); }
    if (path === '/runs/r2') return json(route, newer);
    if (path.endsWith('/events')) { replays.push(path); expect(new URL(route.request().url()).searchParams.get('after')).toBe('0'); return route.fulfill({ contentType: 'text/event-stream', body: event(1, 'run.started', { run: newer }) + event(2, 'run.completed', { run: { ...newer, status: 'completed' }, message: newerMessage }) }); }
    return chatDefaults(route, path);
  });
  await page.goto(`${origin}/test/`); await openDeveloper(page);
  await page.getByLabel('开发用户 ID').fill('newer-run-race');
  await page.getByRole('button', { name: '开发登录', exact: true }).click();
  await page.getByRole('button', { name: '会话甲', exact: true }).click();
  await ready; release();
  await expect(page.locator('.messages .assistant .message-content')).toHaveText(['Mock response.', '第二个任务终态']);
  await expect(page.locator('.run-status')).toContainText('已完成');
  expect(replays).toEqual(['/runs/r2/events']);
});

test('a late create response cannot hijack a newer selected conversation', async ({ page }) => {
  let release!: () => void;
  let received!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const ready = new Promise<void>(resolve => { received = resolve; });
  await fixture(page, async (route, path) => {
    if (path === '/conversations' && route.request().method() === 'POST') {
      received(); await gate;
      return json(route, conversation('c3', '迟到的新会话')).catch(() => {});
    }
    if (path.endsWith('/state')) return json(route, { conversation: path.includes('/c2/') ? c2 : c1, activeRun: null, lastSequence: 0 });
    return chatDefaults(route, path);
  });
  await login(page);
  await page.getByLabel('新会话标题', { exact: true }).fill('迟到的新会话');
  await page.getByRole('button', { name: '新建会话', exact: true }).click();
  await ready;
  await page.getByRole('button', { name: '会话乙', exact: true }).click();
  await expect(page.getByRole('heading', { name: '会话乙', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '新建会话', exact: true })).toBeDisabled();
  release();
  await expect(page.getByRole('button', { name: '新建会话', exact: true })).toBeEnabled();
  await expect(page.getByRole('heading', { name: '会话乙', exact: true })).toBeVisible();
});

test('late old-user responses cannot populate a newly signed-in user view', async ({ page }) => {
  let release!: () => void;
  let received!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const ready = new Promise<void>(resolve => { received = resolve; });
  let sessions = 0;
  await fixture(page, async (route, path) => {
    if (path === '/dev/session') return json(route, { accessToken: `user-${++sessions}`, tokenType: 'Bearer' });
    if (path === '/conversations') {
      if (route.request().headers()['authorization'] === 'Bearer user-1') { received(); await gate; return json(route, { items: [c1], nextCursor: null }).catch(() => {}); }
      return json(route, { items: [c2], nextCursor: null });
    }
    if (path.endsWith('/state')) return json(route, { conversation: c2, activeRun: null, lastSequence: 0 });
    return chatDefaults(route, path);
  });
  await page.goto(`${origin}/test/`); await openDeveloper(page);
  await page.getByLabel('开发用户 ID').fill('user1');
  await page.getByRole('button', { name: '开发登录', exact: true }).click();
  await ready;
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await openDeveloper(page); await page.getByLabel('开发用户 ID').fill('user2');
  await page.getByRole('button', { name: '开发登录', exact: true }).click();
  await expect(page.getByRole('button', { name: '会话乙', exact: true })).toBeVisible();
  release();
  await page.getByRole('button', { name: '会话乙', exact: true }).click();
  await expect(page.getByRole('heading', { name: '会话乙', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '会话甲', exact: true })).toHaveCount(0);
});

test('recent and earlier messages remain chronological without stealing an upward reading position', async ({ page }) => {
  const msg = (sequence: number) => ({ ...assistant, id: `msg${sequence}`, sequence, role: 'user', content: `消息${sequence}：${'阅读正文 '.repeat(20)}` });
  const all = Array.from({ length: 100 }, (_, i) => msg(i + 1));
  await fixture(page, async (route, path) => {
    if (path.endsWith('/state')) return json(route, { conversation: c1, activeRun: null, lastSequence: 100 });
    if (path.endsWith('/messages/recent')) {
      const older = new URL(route.request().url()).searchParams.has('before');
      return json(route, { items: older ? all.slice(0, 50) : all.slice(50), nextCursor: older ? null : 'msg51' });
    }
    return chatDefaults(route, path);
  });
  await login(page);
  await expect(page.locator('.messages .message')).toHaveCount(50);
  await page.locator('.messages').evaluate(node => { node.scrollTop = 120; });
  const before = await page.locator('.messages').evaluate(node => ({ height: node.scrollHeight, top: node.scrollTop }));
  await page.getByRole('button', { name: '加载更早消息', exact: true }).click();
  await expect(page.locator('.messages .message')).toHaveCount(100);
  const after = await page.locator('.messages').evaluate(node => ({ height: node.scrollHeight, top: node.scrollTop }));
  expect(Math.abs(after.top - (before.top + after.height - before.height))).toBeLessThanOrEqual(1);
  await expect(page.locator('.messages .message').first()).toHaveAttribute('data-message-id', 'msg1');
  await expect(page.locator('.messages .message').last()).toHaveAttribute('data-message-id', 'msg100');
  await page.getByRole('button', { name: '刷新会话状态', exact: true }).click();
  await expect(page.locator('.messages .message')).toHaveCount(50);
});

test('page switch discovers an active run and never calls cancel; explicit stop adopts cancelled snapshot', async ({ page }) => {
  let cancelled = 0;
  const stopped = { ...running, status: 'cancelled' };
  const snapshot = { ...assistant, content: '服务端停止快照', status: 'cancelled' };
  await fixture(page, async (route, path) => {
    if (path.endsWith('/state')) return json(route, { conversation: path.includes('/c2/') ? c2 : c1, activeRun: path.includes('/c1/') && !cancelled ? running : null, lastSequence: 2 });
    if (path === '/runs/r1/events') return route.fulfill({ contentType: 'text/event-stream', body: cancelled ? event(1, 'run.started', { run: running }) + event(2, 'run.cancelled', { run: stopped, message: snapshot }) : event(1, 'run.started', { run: running }) + event(2, 'message.delta', { messageId: 'a1', delta: '本地部分' }) });
    if (path === '/runs/r1/cancel') { cancelled++; return json(route, stopped); }
    return chatDefaults(route, path);
  });
  await page.goto(`${origin}/test/`); await openDeveloper(page);
  await page.getByLabel('开发用户 ID').fill('user');
  await page.getByRole('button', { name: '开发登录', exact: true }).click();
  await page.getByRole('button', { name: '会话甲', exact: true }).click();
  await expect(page.locator('.messages')).toContainText('本地部分');
  await openChatAdvanced(page); await openDetails(page, '修改会话');
  await expect(page.getByRole('button', { name: '保存标题和设定', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '会话乙', exact: true }).click();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  expect(cancelled).toBe(0);
  await page.getByRole('button', { name: '会话甲', exact: true }).click();
  await expect(page.locator('.messages')).toContainText('本地部分');
  await page.getByRole('button', { name: '停止回复', exact: true }).click();
  await expect(page.locator('.messages .message-content')).toHaveText(['服务端停止快照']);
  await expect(page.locator('.run-status')).toContainText('已停止');
  expect(cancelled).toBe(1);
});

test('admin discards the old fragment, restores Access by GET, confirms writes and gates content', async ({ page }) => {
  let exchanges = 0;
  let sessionReads = 0;
  let sensitiveReads = 0;
  let writes = 0;
  await fixture(page, async (route, path) => {
    expect(route.request().headers()['authorization']).toBeUndefined();
    if (path === '/admin/session' && route.request().method() === 'GET') {
      sessionReads++;
      return json(route, { authenticated: true, csrfToken: 'fixture-csrf', expiresAt: stamp, authentication: 'cloudflare-access', identity: { email: 'fixture-admin@example.invalid' }, logoutUrl: '/cdn-cgi/access/logout' });
    }
    if (path === '/admin/session' && route.request().method() === 'POST') {
      exchanges++;
      return json(route, { error: { code: 'disabled', message: 'secret-raw-error' } }, 403);
    }
    if (path === '/admin/overview') return json(route, { users: 1, conversations: 1, messages: 1, runs: { running: 0, completed: 0, failed: 0, cancelled: 0 }, activeSessions: 1, environment: 'development', provider: 'mock', maxConcurrentRuns: 2 });
    if (path === '/admin/conversations') return json(route, { items: [{ ...c1, ownerId: 'user', messageCount: 1 }], nextCursor: null });
    if (path === '/admin/conversations/c1/messages') { sensitiveReads++; return json(route, { items: [{ ...assistant, content: '<script>globalThis.pwned=1</script>' }], nextCursor: null }); }
    if (path === '/admin/users') return json(route, { items: [{ userId: 'user', conversationCount: 1, runCount: 0, activeSessions: 1 }], nextCursor: null });
    if (path === '/admin/users/user/revoke-sessions') {
      writes++; expect(route.request().headers()['x-csrf-token']).toBe('fixture-csrf');
      return json(route, { ok: true, revoked: 1 });
    }
    return json(route, { error: { code: 'not_found', message: 'secret-raw-error' } }, 404);
  });
  await page.goto(`${origin}/admin/#code=fixture-link`);
  await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
  expect(await page.evaluate(() => location.hash)).toBe('');
  expect(exchanges).toBe(0);
  expect(sessionReads).toBe(1);
  await expect(page.getByRole('button', { name: '登录管理', exact: true })).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('fixture-link');
  await expect(page.locator('body')).not.toContainText('secret-raw-error');
  await page.getByRole('button', { name: '会话', exact: true }).click();
  await expect(page.getByRole('button', { name: '查看消息', exact: true })).toBeVisible();
  expect(sensitiveReads).toBe(0);
  await page.getByRole('button', { name: '查看消息', exact: true }).click();
  await expect(page.getByLabel('显式查看的会话消息')).toContainText('<script>globalThis.pwned=1</script>');
  expect(await page.evaluate(() => 'pwned' in globalThis)).toBe(false);
  await page.getByRole('button', { name: '用户', exact: true }).click();
  await expect(page.getByRole('button', { name: '撤销用户登录', exact: true })).toBeDisabled();
  expect(writes).toBe(0);
  await page.getByLabel('确认撤销此用户当前全部登录').check();
  await page.getByRole('button', { name: '撤销用户登录', exact: true }).click();
  await expect(page.locator('body')).toContainText('已撤销 1 个用户登录');
  expect(writes).toBe(1);
  expect(exchanges).toBe(0);
  expect(sessionReads).toBe(1);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});
