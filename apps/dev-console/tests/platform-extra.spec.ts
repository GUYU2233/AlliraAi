import { test, expect } from '@playwright/test';
import { fixture, defaults, loginAccount, origin, json, gate, stamp } from './browser-fixture.ts';

test('late recovery code rotation is wiped after feature change and does not repopulate detached view', async ({ page }) => {
  const delayed = gate();
  await fixture(page, async (route, path) => {
    if (path === '/account/recovery' && route.request().method() === 'POST') { delayed.received(); await delayed.waiting; return json(route, { codes: ['private-late-code'], createdAt: stamp }).catch(() => {}); }
    return defaults(route, path);
  });
  await loginAccount(page); await page.getByRole('button', { name: '账号', exact: true }).click();
  await page.getByLabel('签发恢复码的当前密码', { exact: true }).fill('1234567890');
  await page.getByLabel('确认替换全部旧恢复码，并自行离线保存新码', { exact: true }).check();
  await page.getByRole('button', { name: '签发并替换恢复码', exact: true }).click(); await delayed.ready;
  await page.getByRole('button', { name: '能量用量', exact: true }).click(); delayed.release();
  await expect(page.getByLabel('能量余额')).toContainText('当前能量');
  await page.getByRole('button', { name: '账号', exact: true }).click();
  await expect(page.getByLabel('本次恢复码')).toBeEmpty(); await expect(page.locator('body')).not.toContainText('private-late-code');
});

test('admin diagnostics is read-only, backup recent is not restore evidence, and late diagnostic view is ignored', async ({ page }) => {
  let gets = 0; const delayed = gate();
  await fixture(page, async (route, path) => {
    if (path === '/admin/session') return json(route, { authenticated: true, csrfToken: 'fixture-admin-csrf', expiresAt: stamp });
    if (path === '/admin/overview') return json(route, { users: 1, conversations: 1, messages: 2, runs: { running: 0, completed: 1, failed: 0, cancelled: 0 }, activeSessions: 1, environment: 'development', provider: 'mock', maxConcurrentRuns: 1 });
    if (path === '/admin/diagnostics') {
      expect(route.request().method()).toBe('GET'); expect(route.request().headers()['authorization']).toBeUndefined(); ++gets;
      if (gets === 2) { delayed.received(); await delayed.waiting; }
      return json(route, { version: 'fixture-v4', startedAt: stamp, uptimeSeconds: 10, schemaVersion: 4, database: { ok: true, journalMode: 'wal', databaseBytes: 4096, walBytes: 0 }, runs: { running: 0, inFlight: 0, closing: 0, capacity: 4 }, sessions: { bearer: 1, browser: 1, admin: 1 }, backup: { configured: true, latestAt: stamp, ageSeconds: 3, status: 'recent' }, warnings: ['测试静态诊断提示'] }).catch(() => {});
    }
    if (path === '/admin/users') return json(route, { items: [], nextCursor: null });
    return defaults(route, path);
  });
  await page.goto(`${origin}/admin/`);
  await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '健康诊断', exact: true }).click();
  await expect(page.getByLabel('管理内容')).toContainText('fixture-v4');
  await expect(page.getByLabel('管理内容')).toContainText('不代表已校验可恢复');
  await page.getByRole('button', { name: '健康诊断', exact: true }).click(); await delayed.ready;
  await page.getByRole('button', { name: '用户', exact: true }).click(); delayed.release();
  await expect(page.getByRole('heading', { name: '用户', exact: true })).toBeVisible();
  await expect(page.getByLabel('管理内容')).not.toContainText('fixture-v4'); expect(gets).toBe(2);
});
