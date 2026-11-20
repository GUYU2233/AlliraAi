import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { account, c1, defaults, fixture, gate, json, openAdvanced, openChatAdvanced, openDetails, openLegacy, origin, stamp } from './browser-fixture.ts';

const presets = { defaultPresetId: 'daily', items: [
  { id: 'daily', title: '日常陪伴', description: '轻松聊聊日常，不预设关系。', openingHint: '今天想聊哪件小事？', defaultMode: 'economy' },
  { id: 'listen', title: '倾听整理', description: '先倾听，帮助整理表达。', openingHint: '可以从现在的感受说起。', defaultMode: 'economy' },
  { id: 'focus', title: '专注行动', description: '把目标拆成可执行的小步。', openingHint: '先选一件想推进的小事。', defaultMode: 'economy' },
] };
const daily = { ...c1, id: 'daily1', title: '日常陪伴', instructions: '服务端固定设定' };
const listen = { ...daily, id: 'listen1', title: '倾听整理' };
const run = { id: 'r1', conversationId: daily.id, clientMessageId: 'fixed', userMessageId: 'u1', assistantMessageId: 'a1', status: 'running', errorCode: null, createdAt: stamp, updatedAt: stamp };
const message = { id: 'a1', conversationId: daily.id, runId: run.id, sequence: 2, role: 'assistant', content: '权威终态', status: 'completed', createdAt: stamp };
const event = (id: number, name: string, data: unknown) => `id: ${id}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
async function signIn(page: Page): Promise<void> {
  await page.goto(`${origin}/test/`); await openLegacy(page);
  await page.getByLabel('账号用户名', { exact: true }).fill('account_1');
  await page.getByLabel('账号密码（10–128 字符，最多 256 UTF-8 字节）', { exact: true }).fill('1234567890');
  await page.getByRole('button', { name: '账号登录', exact: true }).click();
  await expect(page.getByRole('button', { name: '新聊天', exact: true })).toBeVisible();
}

// Fully intercepted virtual origin, no real listener or active service traffic.
test('simple defaults need no parameters, daily setup is one POST with no run, and first explicit send uses economy', async ({ page }) => {
  const writes: { path: string; body: unknown }[] = []; let created = false;
  await fixture(page, async (route, path) => {
    if (path === '/companion/presets') { expect(route.request().headers()['authorization']).toBeUndefined(); return json(route, presets); }
    if (route.request().method() === 'POST') writes.push({ path, body: route.request().postDataJSON() });
    if (path === '/companion/conversations') { created = true; return json(route, { conversation: daily, presetId: 'daily', replayed: false }, 201); }
    if (path === '/conversations') return json(route, { items: created ? [daily] : [], nextCursor: null });
    if (path.endsWith('/state')) return json(route, { conversation: daily, activeRun: null, lastSequence: 0 });
    if (path === `/conversations/${daily.id}/runs`) return json(route, run);
    if (path === '/runs/r1/events') return route.fulfill({ contentType: 'text/event-stream', body: event(1, 'run.started', { run }) + event(2, 'run.completed', { run: { ...run, status: 'completed' }, message }) });
    return defaults(route, path);
  });
  await signIn(page);
  await expect(page.getByRole('button', { name: '角色与记忆', exact: true })).toBeHidden();
  await expect(page.getByLabel('新会话标题', { exact: true })).toBeHidden();
  await expect(page.getByLabel('聊天风格', { exact: true })).toBeHidden();
  await expect(page.getByRole('link', { name: '后台管理', exact: true })).toBeHidden();
  await page.getByRole('button', { name: '新聊天', exact: true }).click();
  await expect(page.getByRole('heading', { name: daily.title, exact: true })).toBeVisible();
  await expect(page.getByLabel('生成模式', { exact: true })).toBeHidden();
  await expect(page.getByLabel('聊天开始建议', { exact: true })).toContainText('不是回复');
  await expect(page.locator('.messages .message')).toHaveCount(0);
  expect(writes.filter(item => item.path === '/companion/conversations')).toEqual([{ path: '/companion/conversations', body: { clientSetupId: expect.stringMatching(/^[0-9a-f-]{36}$/), presetId: 'daily' } }]);
  expect(writes.filter(item => item.path.includes('/runs') || item.path.includes('/energy'))).toEqual([]);
  await page.getByLabel('消息内容', { exact: true }).fill('只在我点击后发送');
  await page.getByRole('button', { name: '发送', exact: true }).click();
  await expect(page.locator('.messages .assistant .message-content')).toHaveText('权威终态');
  expect(writes.find(item => item.path.endsWith('/runs'))?.body).toMatchObject({ content: '只在我点击后发送', mode: 'economy' });
  await expect(page.getByLabel('聊天开始建议', { exact: true })).toBeHidden();
  await page.setViewportSize({ width: 375, height: 800 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('optional three styles preserve advanced features, new preset resets old fast selection and settings close releases sensitive view', async ({ page }) => {
  const setups: unknown[] = [];
  await fixture(page, async (route, path) => {
    if (path === '/companion/presets') return json(route, presets);
    if (path === '/companion/conversations') { setups.push(route.request().postDataJSON()); return json(route, { conversation: listen, presetId: 'listen', replayed: false }); }
    if (path.endsWith('/state')) return json(route, { conversation: path.includes('/listen1/') ? listen : c1, activeRun: null, lastSequence: 0 });
    return defaults(route, path);
  });
  await signIn(page); await page.getByRole('button', { name: '会话甲', exact: true }).click();
  await openChatAdvanced(page); await page.getByLabel('生成模式', { exact: true }).selectOption('fast');
  await openDetails(page, '聊天风格选项'); await expect(page.getByLabel('聊天风格', { exact: true }).locator('option')).toHaveCount(3);
  await page.getByLabel('聊天风格', { exact: true }).selectOption('listen');
  await page.getByRole('button', { name: '新聊天', exact: true }).click();
  await expect(page.getByRole('heading', { name: '倾听整理', exact: true })).toBeVisible();
  await expect(page.getByLabel('生成模式', { exact: true })).toHaveValue('economy');
  expect(setups).toEqual([{ clientSetupId: expect.any(String), presetId: 'listen' }]);
  await openAdvanced(page);
  for (const label of ['会话工具', '角色与记忆', '历史导入', '能量用量', '账号']) await expect(page.getByRole('button', { name: label, exact: true })).toBeVisible();
  await page.getByRole('button', { name: '角色与记忆', exact: true }).click();
  await page.getByLabel('准备发送的文本（仅预览）', { exact: true }).fill('关闭设置要清掉的私有草稿');
  await page.locator('details[aria-label="设置与高级工具"] > summary').click();
  await expect(page.getByLabel('功能内容', { exact: true })).toBeEmpty();
  await expect(page.getByLabel('消息内容', { exact: true })).toBeVisible();
  await openAdvanced(page); await page.getByRole('button', { name: '角色与记忆', exact: true }).click();
  await expect(page.getByLabel('准备发送的文本（仅预览）', { exact: true })).toHaveValue('');
});

test('unknown setup result locks style and permits only exact UUID/style manual retry without automatic generation', async ({ page }) => {
  const setups: unknown[] = []; const writes: string[] = []; let created = false;
  await fixture(page, async (route, path) => {
    if (path === '/companion/presets') return json(route, presets);
    if (route.request().method() === 'POST') writes.push(path);
    if (path === '/companion/conversations') { setups.push(route.request().postDataJSON()); created = true; return setups.length === 1 ? route.abort('connectionfailed') : json(route, { conversation: listen, presetId: 'listen', replayed: true }); }
    if (path === '/conversations') return json(route, { items: created ? [listen] : [c1], nextCursor: null });
    if (path.endsWith('/state')) return json(route, { conversation: listen, activeRun: null, lastSequence: 0 });
    return defaults(route, path);
  });
  await signIn(page); await openDetails(page, '聊天风格选项'); await page.getByLabel('聊天风格', { exact: true }).selectOption('listen');
  await page.getByRole('button', { name: '新聊天', exact: true }).click();
  await expect(page.getByRole('button', { name: '重试新聊天', exact: true })).toBeEnabled();
  await expect(page.getByLabel('聊天风格', { exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '新聊天', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '重新读取聊天风格', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '刷新会话', exact: true }).click();
  await expect(page.getByRole('button', { name: '倾听整理', exact: true })).toBeVisible(); expect(setups).toHaveLength(1);
  await page.getByRole('button', { name: '重试新聊天', exact: true }).click();
  await expect(page.getByRole('heading', { name: '倾听整理', exact: true })).toBeVisible();
  expect(setups).toHaveLength(2); expect(setups[1]).toEqual(setups[0]);
  expect(writes.filter(path => path !== '/auth/login')).toEqual(['/companion/conversations', '/companion/conversations']);
  await expect(page.getByLabel('聊天风格', { exact: true })).toBeEnabled();
});

test('late setup cannot select over a newer conversation or feature and old-user setup cannot repopulate a new login', async ({ page }) => {
  const first = gate(), second = gate(); let posts = 0;
  await fixture(page, async (route, path) => {
    if (path === '/companion/presets') return json(route, presets);
    if (path === '/companion/conversations') { const held = ++posts === 1 ? first : second; held.received(); await held.waiting; return json(route, { conversation: daily, presetId: 'daily', replayed: false }).catch(() => {}); }
    if (path.endsWith('/state')) return json(route, { conversation: c1, activeRun: null, lastSequence: 0 });
    return defaults(route, path);
  });
  await signIn(page); await page.getByRole('button', { name: '新聊天', exact: true }).click(); await first.ready;
  await page.getByRole('button', { name: '会话甲', exact: true }).click();
  await openAdvanced(page); await page.getByRole('button', { name: '能量用量', exact: true }).click(); first.release();
  await expect(page.getByRole('button', { name: '新聊天', exact: true })).toBeHidden();
  await expect(page.getByRole('heading', { name: '能量与用量', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '聊天', exact: true }).click(); await expect(page.getByRole('button', { name: '新聊天', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '新聊天', exact: true }).click(); await second.ready;
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await signIn(page); second.release();
  await expect(page.getByRole('heading', { name: '请选择或新建会话', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '新聊天', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: '重试新聊天', exact: true })).toBeHidden();
});

test('unavailable public metadata still permits fixed daily without invented setup; definite errors do not expose raw bodies or recreate automatically', async ({ page }) => {
  let posts = 0;
  await fixture(page, async (route, path) => {
    if (path === '/companion/presets') return route.abort('connectionfailed');
    if (path === '/companion/conversations') { ++posts; return json(route, { error: { code: 'not_found', message: 'secret-server-error' } }, 404); }
    return defaults(route, path);
  });
  await signIn(page); await page.getByRole('button', { name: '新聊天', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '不会替你重新创建' })).toBeVisible();
  expect(posts).toBe(1); await expect(page.getByRole('button', { name: '重试新聊天', exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: '新聊天', exact: true })).toBeEnabled();
  await expect(page.locator('body')).not.toContainText('secret-server-error');
});

test('remembered Cookie setup carries CSRF and no Bearer; 401 clears private state and pending setup', async ({ page }) => {
  let posts = 0;
  await fixture(page, async (route, path) => {
    if (path === '/companion/presets') return json(route, presets);
    if (path === '/auth/browser/session') return json(route, { user: account, csrfToken: 'setup-csrf', expiresAt: stamp });
    if (path === '/companion/conversations') { ++posts; expect(route.request().headers()['authorization']).toBeUndefined(); expect(route.request().headers()['x-csrf-token']).toBe('setup-csrf'); return json(route, { error: { code: 'unauthorized', message: 'private-cookie-body' } }, 401); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.locator('.identity')).toContainText('已记住此设备');
  await page.getByRole('button', { name: '新聊天', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '新聊天', exact: true })).toBeHidden();
  await expect(page.locator('body')).not.toContainText('private-cookie-body'); expect(posts).toBe(1);
});

test('simple chat keeps retry and explicit stop visible without opening technical settings', async ({ page }) => {
  let streams = 0, cancels = 0;
  await fixture(page, async (route, path) => {
    if (path === '/companion/presets') return json(route, presets);
    if (path.endsWith('/state')) return json(route, { conversation: daily, activeRun: run, lastSequence: 2 });
    if (path === '/conversations') return json(route, { items: [daily], nextCursor: null });
    if (path === '/runs/r1/events') { ++streams; expect(new URL(route.request().url()).searchParams.get('after')).toBe('0'); return streams === 1 ? json(route, { error: { code: 'stream_protocol', message: 'private' } }, 400) : route.fulfill({ contentType: 'text/event-stream', body: event(1, 'run.started', { run }) }); }
    if (path === '/runs/r1/cancel') { ++cancels; return json(route, { ...run, status: 'cancelled' }); }
    return defaults(route, path);
  });
  await signIn(page); await page.getByRole('button', { name: '日常陪伴', exact: true }).click();
  await expect(page.getByRole('button', { name: '再试一次', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '停止回复', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '断开订阅', exact: true })).toBeHidden();
  await page.getByRole('button', { name: '再试一次', exact: true }).click();
  await expect.poll(() => streams).toBe(2); expect(cancels).toBe(0);
  await page.getByRole('button', { name: '停止回复', exact: true }).click();
  await expect.poll(() => cancels).toBe(1);
});
