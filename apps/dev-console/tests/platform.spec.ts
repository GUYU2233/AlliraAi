import { test, expect } from '@playwright/test';
import { fixture, defaults, loginAccount, openLegacy, openAdvanced, account, accountSession, origin, c1, json, gate, stamp } from './browser-fixture.ts';

const browser = { user: account, csrfToken: 'fixture-csrf', expiresAt: stamp };
async function rememberedLogin(page: import('@playwright/test').Page): Promise<void> {
  await page.goto(`${origin}/test/`); await openLegacy(page);
  await page.getByLabel('账号用户名', { exact: true }).fill('account_1');
  await page.getByLabel('账号密码（10–128 字符，最多 256 UTF-8 字节）', { exact: true }).fill('1234567890');
  await page.getByLabel('记住此设备（可选，使用独立 HttpOnly Cookie，不保存 Bearer）', { exact: true }).check();
  await page.getByRole('button', { name: '账号登录', exact: true }).click();
  await expect(page.locator('.identity')).toContainText('已记住此设备');
  await openAdvanced(page);
}

test('remember is opt-in, exchanges Bearer once, uses Cookie+CSRF without Bearer and restores only from session GET', async ({ page }) => {
  let exchanged = false;
  const writes: string[] = [];
  await fixture(page, async (route, path) => {
    if (path === '/auth/browser/session') {
      if (route.request().method() === 'POST') { expect(route.request().headers()['authorization']).toBe(`Bearer ${accountSession.accessToken}`); exchanged = true; writes.push(path); return json(route, browser); }
      return exchanged ? json(route, browser) : defaults(route, path);
    }
    if (path === '/conversations' && exchanged) expect(route.request().headers()['authorization']).toBeUndefined();
    if (path === '/account/update') { expect(route.request().headers()['authorization']).toBeUndefined(); expect(route.request().headers()['x-csrf-token']).toBe('fixture-csrf'); writes.push(path); return json(route, { ...account, displayName: '设备账号' }); }
    if (path === '/auth/browser/logout') { expect(route.request().headers()['authorization']).toBeUndefined(); expect(route.request().headers()['x-csrf-token']).toBe('fixture-csrf'); writes.push(path); exchanged = false; return json(route, { ok: true }); }
    return defaults(route, path);
  });
  await rememberedLogin(page);
  await page.getByRole('button', { name: '账号', exact: true }).click();
  await page.getByLabel('显示名称', { exact: true }).fill('设备账号');
  await page.getByRole('button', { name: '保存显示名称', exact: true }).click();
  await expect(page.locator('.identity')).toContainText('设备账号');
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  await page.reload();
  await expect(page.locator('.identity')).toContainText('已记住此设备');
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
  expect(writes).toEqual(['/auth/browser/session', '/account/update', '/auth/browser/logout']);
});

test('uncertain Cookie exchange does one POST then GET verification, never persists its temporary Bearer', async ({ page }) => {
  let exchanges = 0, gets = 0;
  await fixture(page, async (route, path) => {
    if (path === '/auth/browser/session') {
      if (route.request().method() === 'POST') { exchanges++; return route.abort('connectionfailed'); }
      gets++; return exchanges ? json(route, browser) : defaults(route, path);
    }
    return defaults(route, path);
  });
  await rememberedLogin(page);
  expect(exchanges).toBe(1); expect(gets).toBe(2);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('late Cookie restore cannot replace a newer memory account login or its Bearer', async ({ page }) => {
  const delayed = gate(); const headers: (string | undefined)[] = [];
  await fixture(page, async (route, path) => {
    if (path === '/auth/browser/session') { delayed.received(); await delayed.waiting; return json(route, { ...browser, user: { ...account, id: 'old-user', displayName: '旧浏览器用户' } }).catch(() => {}); }
    if (path === '/conversations') headers.push(route.request().headers()['authorization']);
    return defaults(route, path);
  });
  await loginAccount(page); await delayed.ready; delayed.release();
  await expect(page.locator('.identity')).toContainText('账号甲');
  await expect(page.locator('body')).not.toContainText('旧浏览器用户');
  expect(headers).toEqual([`Bearer ${accountSession.accessToken}`]);
});

test('recovery plaintext is explicit once, cleared on feature switch; devices and destructive operations require confirmation', async ({ page }) => {
  const writes: string[] = [];
  await fixture(page, async (route, path) => {
    if (path === '/account/recovery') {
      if (route.request().method() === 'POST') { writes.push(path); expect(route.request().postDataJSON()).toEqual({ currentPassword: '1234567890' }); return json(route, { codes: ['fixture-one-time-code'], createdAt: stamp }); }
      return json(route, { enabled: true, remainingCodes: 1, updatedAt: stamp });
    }
    if (path === '/account/sessions') return json(route, { items: [{ id: 's1', label: '此浏览器', type: 'bearer', current: true, createdAt: stamp, lastSeenAt: stamp, expiresAt: stamp }] });
    if (path === '/account/sessions/s1/revoke') { writes.push(path); return json(route, { ok: true }); }
    if (path === '/account/export') { writes.push(path); return json(route, { error: { code: 'account_busy', message: 'raw-private-error' } }, 409); }
    return defaults(route, path);
  });
  await loginAccount(page); await page.getByRole('button', { name: '账号', exact: true }).click();
  await expect(page.getByLabel('本次恢复码')).toBeEmpty();
  expect(writes).toEqual([]);
  await page.getByRole('button', { name: '签发并替换恢复码', exact: true }).click(); expect(writes).toEqual([]);
  await page.getByLabel('签发恢复码的当前密码', { exact: true }).fill('1234567890');
  await page.getByLabel('确认替换全部旧恢复码，并自行离线保存新码', { exact: true }).check();
  await page.getByRole('button', { name: '签发并替换恢复码', exact: true }).click();
  await expect(page.getByLabel('本次恢复码')).toContainText('fixture-one-time-code');
  await expect(page.getByLabel('签发恢复码的当前密码', { exact: true })).toHaveValue('');
  await page.getByRole('button', { name: '能量用量', exact: true }).click(); await page.getByRole('button', { name: '账号', exact: true }).click();
  await expect(page.locator('body')).not.toContainText('fixture-one-time-code');
  await page.getByRole('button', { name: '下载账号完整数据', exact: true }).click(); expect(writes).toEqual(['/account/recovery']);
  await page.getByLabel('确认下载含敏感聊天内容的账号数据', { exact: true }).check();
  await page.getByRole('button', { name: '下载账号完整数据', exact: true }).click();
  await expect(page.locator('.notice.error')).toContainText('状态冲突');
  await expect(page.locator('body')).not.toContainText('raw-private-error');
  await page.getByRole('button', { name: '读取登录设备', exact: true }).click();
  await page.getByRole('button', { name: '撤销当前设备登录', exact: true }).click(); expect(writes).toEqual(['/account/recovery', '/account/export']);
  await page.getByLabel('确认撤销此登录设备', { exact: true }).check();
  await page.getByRole('button', { name: '撤销当前设备登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
  expect(writes.at(-1)).toBe('/account/sessions/s1/revoke');
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('account erase requires password plus exact literal plus checkbox and logs out only after success', async ({ page }) => {
  let erased = 0;
  await fixture(page, async (route, path) => {
    if (path === '/account/erase') { erased++; expect(route.request().postDataJSON()).toEqual({ currentPassword: '1234567890', confirmation: 'DELETE MY ACCOUNT' }); return json(route, { ok: true }); }
    return defaults(route, path);
  });
  await loginAccount(page); await page.getByRole('button', { name: '账号', exact: true }).click();
  await page.getByText('危险操作：删除账号', { exact: true }).click();
  await page.getByRole('button', { name: '永久删除账号和在线数据', exact: true }).click(); expect(erased).toBe(0);
  await page.getByLabel('删除账号的当前密码', { exact: true }).fill('1234567890');
  await page.getByLabel('逐字输入 DELETE MY ACCOUNT', { exact: true }).fill('DELETE MY ACCOUNT');
  await page.getByRole('button', { name: '永久删除账号和在线数据', exact: true }).click(); expect(erased).toBe(0);
  await page.getByLabel('确认永久删除账号和在线数据且无法撤销', { exact: true }).check();
  await page.getByRole('button', { name: '永久删除账号和在线数据', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible(); expect(erased).toBe(1);
});

test('public recovery masks and clears code, never signs in or sends existing Cookie/Bearer', async ({ page }) => {
  let recovered = 0;
  await fixture(page, async (route, path) => {
    if (path === '/auth/recover') { recovered++; expect(route.request().headers()['authorization']).toBeUndefined(); expect(route.request().postDataJSON()).toEqual({ username: 'account_1', recoveryCode: 'saved-code', newPassword: 'abcdefghij' }); return json(route, { ok: true }); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await openLegacy(page); await page.getByText('忘记密码：使用恢复码', { exact: true }).click();
  await page.getByLabel('恢复账号用户名', { exact: true }).fill('account_1');
  await page.getByLabel('单次恢复码', { exact: true }).fill('saved-code');
  await expect(page.getByLabel('单次恢复码', { exact: true })).toHaveAttribute('type', 'password');
  await page.getByLabel('恢复后的新密码', { exact: true }).fill('abcdefghij');
  await page.getByLabel('确认重设密码并撤销全部旧码和登录', { exact: true }).check();
  await page.getByRole('button', { name: '使用恢复码重设密码', exact: true }).click();
  await expect(page.getByLabel('单次恢复码', { exact: true })).toHaveValue('');
  await expect(page.locator('.notice').filter({ hasText: '不会自动登录' })).toBeVisible(); expect(recovered).toBe(1);
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
});

test('search resets cursor and ignores late query; export downloads only explicitly; fork retries immutable UUID/settings', async ({ page }) => {
  const delayed = gate(); const query: string[] = []; const forks: unknown[] = []; let exports = 0;
  await fixture(page, async (route, path) => {
    if (path === '/conversation-search') {
      const params = new URL(route.request().url()).searchParams; const q = params.get('q')!; query.push(q);
      if (q === '旧关键词') { delayed.received(); await delayed.waiting; return json(route, { items: [{ conversation: { ...c1, title: '旧查询不应出现' }, snippet: '旧正文', matchedMessageId: null }], nextCursor: 'old-cursor' }).catch(() => {}); }
      expect(params.get('cursor')).toBeNull(); return json(route, { items: [{ conversation: c1, snippet: '<img src=x onerror=alert(1)>', matchedMessageId: 'm1' }], nextCursor: null });
    }
    if (path === '/conversations/c1/export') { exports++; return json(route, { format: 'json', filename: 'chat.json', content: '{"inert":"<script>"}' }); }
    if (path === '/conversations/c1/fork') { forks.push(route.request().postDataJSON()); if (forks.length === 1) return route.abort('connectionfailed'); return json(route, { conversation: { ...c1, id: 'forked', title: '分叉结果' }, replayed: true, copiedMessages: 2 }); }
    if (path === '/conversations/forked/state') return json(route, { conversation: { ...c1, id: 'forked', title: '分叉结果' }, activeRun: null, lastSequence: 0 });
    return defaults(route, path);
  });
  await loginAccount(page); await page.getByRole('button', { name: '会话工具', exact: true }).click();
  await page.getByLabel('会话检索关键词', { exact: true }).fill('旧关键词'); await page.getByRole('button', { name: '检索我的会话', exact: true }).click(); await delayed.ready;
  await page.getByLabel('会话检索关键词', { exact: true }).fill('新关键词'); await page.getByRole('button', { name: '检索我的会话', exact: true }).click();
  await expect(page.getByLabel('会话检索结果')).toContainText('会话甲'); delayed.release();
  await expect(page.getByLabel('会话检索结果')).not.toContainText('旧查询不应出现'); await expect(page.locator('img')).toHaveCount(0); expect(query).toEqual(['旧关键词', '新关键词']);
  expect(exports).toBe(0); await page.getByRole('button', { name: '选择导出或分叉', exact: true }).click();
  const download = page.waitForEvent('download'); await page.getByRole('button', { name: '下载此会话', exact: true }).click(); await download; expect(exports).toBe(1);
  await page.getByLabel('新分叉标题', { exact: true }).fill('原始标题'); await page.getByRole('button', { name: '创建分叉会话', exact: true }).click(); expect(forks).toEqual([]);
  await page.getByLabel('确认从已完成对话创建新会话', { exact: true }).check(); await page.getByRole('button', { name: '创建分叉会话', exact: true }).click();
  await expect(page.getByRole('button', { name: '按同一编号与参数重试分叉', exact: true })).toBeEnabled();
  await page.getByLabel('新分叉标题', { exact: true }).fill('不能改变原请求');
  await page.getByRole('button', { name: '按同一编号与参数重试分叉', exact: true }).click();
  await expect(page.getByRole('heading', { name: '分叉结果', exact: true })).toBeVisible();
  expect(forks).toHaveLength(2); expect(forks[0]).toEqual(forks[1]); expect(forks[0]).toMatchObject({ clientForkId: expect.stringMatching(/^[0-9a-f-]{36}$/), title: '原始标题' });
});
