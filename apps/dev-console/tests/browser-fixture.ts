import { expect } from '@playwright/test';
import type { Page, Route } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { openDetails, openAdvanced, openChatAdvanced, openDeveloper, openLegacy } from './ui.ts';

// Trustworthy loopback origin for crypto, separate from the active API port. No listener is started.
export const origin = 'http://127.0.0.1:49183';
export const stamp = '2026-01-01T00:00:00Z';
export const c1 = { id: 'c1', title: '会话甲', instructions: null, createdAt: stamp, updatedAt: stamp };
export const account = { id: 'account-fixture', username: 'account_1', displayName: '账号甲', createdAt: stamp, termsVersion: '2026-11-19', isAdult: true };
export const accountSession = { accessToken: 'fixture-account-bearer', tokenType: 'Bearer', user: account };
export const json = (route: Route, data: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
export async function fixture(page: Page, api: (route: Route, path: string) => Promise<unknown>): Promise<void> {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) { await route.abort(); return; }
    const { pathname: path } = url;
    if (path.startsWith('/api/v1/')) { await api(route, path.slice('/api/v1'.length)); return; }
    const asset = path.match(/^\/console-assets\/(?:public-chat-[A-Z0-9]+\.(?:js|css)|admin-[A-Z0-9]+\.(?:js|css)|public-icon-(?:192|512)-[a-f0-9]{16}\.png)$/);
    const file = asset ? `assets${path.slice('/console-assets'.length)}` : {
      '/test/': 'test/index.html', '/admin/': 'admin/index.html',
      '/test/sw.js': 'test/sw.js', '/test/manifest.webmanifest': 'test/manifest.webmanifest',
    }[path];
    if (!file) { await route.abort(); return; }
    const contentType = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.webmanifest') ? 'application/manifest+json' : file.endsWith('.png') ? 'image/png' : 'text/html';
    await route.fulfill({ body: await readFile(new URL(`../.test-dist/${file}`, import.meta.url)), contentType });
  });
}
export function defaults(route: Route, path: string): Promise<unknown> {
  if (path === '/info') return json(route, { environment: 'development', provider: 'mock', allowDevAuth: true, consoleEnabled: true, adminEnabled: true });
  if (path === '/terms') return json(route, { version: '2026-11-19', adultOnly: true, content: '仅限成年人测试，规则摘要与字符统计不代表真实模型效果。' });
  if (path === '/models') return json(route, { items: [{ mode: 'economy', provider: 'mock', configured: true, energyCost: 1, fallbackTo: null }, { mode: 'fast', provider: 'mock', configured: true, energyCost: 5, fallbackTo: 'economy' }] });
  if (path === '/dev/session') return json(route, { accessToken: 'fixture-dev-bearer', tokenType: 'Bearer' });
  if (path === '/auth/login' || path === '/auth/register') return json(route, accountSession);
  if (path === '/auth/browser/session') return json(route, { error: { code: 'unauthorized', message: 'private' } }, 401);
  if (path === '/account') return json(route, account);
  if (path === '/account/logout-all') return json(route, { ok: true, revoked: 2 });
  if (path === '/session/logout') return json(route, { ok: true });
  if (path === '/conversations') return json(route, { items: [c1], nextCursor: null });
  if (path.endsWith('/state')) return json(route, { conversation: c1, activeRun: null, lastSequence: 0 });
  if (path.endsWith('/messages/recent')) return json(route, { items: [], nextCursor: null });
  if (path === '/characters') return json(route, { items: [], nextCursor: null });
  if (/^\/conversations\/[^/]+\/memory\/(?:cards|blocks)$/.test(path) && route.request().method() === 'GET') return json(route, { items: [], ...(path.endsWith('/blocks') ? { nextCursor: null } : {}) });
  if (/^\/conversations\/[^/]+\/memory$/.test(path) && route.request().method() === 'GET') return json(route, { conversationId: path.split('/')[2], characterId: null, coreMemory: '', relationship: '', summary: '', summarizedThrough: 0, updatedAt: stamp });
  if (path === '/energy') return json(route, { balance: 20, unit: 'energy', economyCost: 1, fastCost: 5 });
  if (path === '/energy/ledger') return json(route, { items: [], nextCursor: null });
  return json(route, { error: { code: 'not_found', message: 'private-raw-error-never-display' } }, 404);
}
export { openDetails, openAdvanced, openChatAdvanced, openDeveloper, openLegacy } from './ui.ts';
export async function loginAccount(page: Page): Promise<void> {
  await page.goto(`${origin}/test/`); await openLegacy(page);
  await page.getByLabel('账号用户名', { exact: true }).fill('account_1');
  await page.getByLabel('账号密码（10–128 字符，最多 256 UTF-8 字节）', { exact: true }).fill('1234567890');
  await page.getByRole('button', { name: '账号登录', exact: true }).click();
  await expect(page.getByRole('button', { name: '聊天', exact: true })).toBeVisible();
  await openAdvanced(page);
}
export async function loginDev(page: Page): Promise<void> {
  await page.goto(`${origin}/test/`); await openDeveloper(page);
  await page.getByLabel('开发用户 ID', { exact: true }).fill('fixture-dev');
  await page.getByRole('button', { name: '开发登录', exact: true }).click();
  await page.getByRole('button', { name: '会话甲', exact: true }).click();
  await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
  await openAdvanced(page); await openChatAdvanced(page); await openDetails(page, '自定义新会话');
}
export function gate() {
  let release!: () => void;
  let received!: () => void;
  return { waiting: new Promise<void>(resolve => { release = resolve; }), ready: new Promise<void>(resolve => { received = resolve; }), release: () => release(), received: () => received() };
}
