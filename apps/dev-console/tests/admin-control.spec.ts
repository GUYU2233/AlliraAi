import { test, expect } from '@playwright/test';
import type { Page, Route } from '@playwright/test';
import { fixture, defaults, json, origin, stamp, gate } from './browser-fixture.ts';

const csrf = 'fixture-admin-control-csrf';
const primary = { userId: 'acct-primary', conversationCount: 2, runCount: 1, activeSessions: 3, createdAt: stamp, accountType: 'registered', username: 'old_primary', displayName: '<img src=x onerror=globalThis.pwned=1>', identityStatus: 'oidc', maskedPhone: '+86138****4321', phoneVerified: true, passwordEnabled: false, walletBalance: 17 };
const secondary = { ...primary, userId: 'acct-secondary', displayName: '第二个用户', username: null, activeSessions: 1 };
const device = (id: string, type = 'bearer') => ({ id, type, label: `测试设备 ${id}`, createdAt: stamp, lastSeenAt: stamp, expiresAt: stamp });
const overview = { users: 2, conversations: 2, messages: 4, runs: { running: 0, completed: 1, failed: 0, cancelled: 0 }, activeSessions: 4, environment: 'development', provider: 'mock', maxConcurrentRuns: 2 };
const routes = { items: [{ mode: 'economy', provider: 'mock', configured: true, energyCost: 1, fallbackTo: null }, { mode: 'fast', provider: 'openai', configured: false, energyCost: 5, fallbackTo: 'economy' }], limits: { runTimeoutSeconds: 60, maxConcurrentRuns: 2, maxPromptCharacters: 12000, maxOutputCharacters: 3000, upstreamMaxTokens: 1000, runRatePerMinute: 20 }, configurationOnly: true };
const diagnostics = { version: 'fixture-admin-v8', startedAt: stamp, uptimeSeconds: 40, schemaVersion: 7, database: { ok: true, journalMode: 'wal', databaseBytes: 4096, walBytes: 0 }, runs: { running: 0, inFlight: 0, closing: 0, capacity: 2 }, sessions: { bearer: 3, browser: 1, admin: 1 }, backup: { configured: true, latestAt: stamp, ageSeconds: 30, status: 'recent' }, warnings: ['private-path-issuer-phone-never-render'] };
const identity = { backend: 'oidc', configured: true, enabled: false, simulated: false, linkedAccounts: 2, legacyAccounts: 1, developerUsers: 1, pendingFlows: 0, serviceProbe: 'not_performed', administration: 'separate' };
const identityBackup = { configured: true, latestAt: stamp, ageSeconds: 20, status: 'recent', databaseBytes: 8192, restoreStatus: 'recorded', restoreVerifiedAt: stamp, checksumVerified: false };

function adminWrite(route: Route, token = csrf): void {
  expect(route.request().method()).toBe('POST');
  expect(route.request().headers()['authorization']).toBeUndefined();
  expect(route.request().headers()['origin']).toBe(origin);
  expect(route.request().headers()['x-csrf-token']).toBe(token);
  expect(route.request().headers()['x-allira-oidc-csrf']).toBeUndefined();
}
async function setup(page: Page, api: (route: Route, path: string) => Promise<unknown>): Promise<void> {
  await fixture(page, async (route, path) => {
    expect(route.request().headers()['authorization']).toBeUndefined();
    if (path === '/admin/session') return json(route, { authenticated: true, csrfToken: csrf, expiresAt: stamp });
    if (path === '/admin/overview') return json(route, overview);
    if (path === '/admin/logout') { adminWrite(route); return json(route, { ok: true }); }
    return api(route, path);
  });
  await page.goto(`${origin}/admin/`);
  await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
}
async function users(page: Page): Promise<void> {
  await page.getByRole('button', { name: '用户', exact: true }).click();
  await expect(page.getByRole('table', { name: '用户身份与业务摘要', exact: true })).toBeVisible();
}
const userRow = (page: Page, owner = primary.userId) => page.getByRole('table', { name: '用户身份与业务摘要', exact: true }).getByRole('row').filter({ has: page.getByRole('cell', { name: owner, exact: true }) });

test('admin user filters use literal normalized query and identity with bound pagination, masked optional fields and inert text', async ({ page }) => {
  const calls: { q: string | null; identity: string | null; cursor: string | null }[] = []; let sensitive = 0;
  await setup(page, async (route, path) => {
    const url = new URL(route.request().url());
    if (path === '/admin/users') {
      calls.push({ q: url.searchParams.get('q'), identity: url.searchParams.get('identity'), cursor: url.searchParams.get('cursor') });
      if (url.searchParams.get('q')) return json(route, { items: [], nextCursor: null });
      return url.searchParams.get('cursor') ? json(route, { items: [{ userId: 'old-unknown', conversationCount: 0, runCount: 0, activeSessions: 0 }], nextCursor: null }) : json(route, { items: [primary], nextCursor: 'users-next' });
    }
    if (path.includes('/sessions') || path.includes('/messages')) ++sensitive;
    return defaults(route, path);
  });
  await users(page); await expect(userRow(page)).toBeVisible();
  await expect(userRow(page)).toContainText('+86138****4321'); await expect(userRow(page)).toContainText('17');
  await expect(userRow(page)).toContainText('<img src=x onerror=globalThis.pwned=1>');
  expect(await page.evaluate(() => 'pwned' in globalThis)).toBe(false); expect(sensitive).toBe(0);
  await page.getByRole('button', { name: '加载更多', exact: true }).click();
  await expect(userRow(page, 'old-unknown')).toContainText('未知（旧服务未提供）');
  expect(calls.slice(0, 2)).toEqual([{ q: null, identity: 'all', cursor: null }, { q: null, identity: 'all', cursor: 'users-next' }]);
  await page.getByLabel('搜索用户', { exact: true }).fill('  %_\\  '); await page.getByLabel('身份类型', { exact: true }).selectOption('legacy');
  await page.getByRole('button', { name: '筛选用户', exact: true }).click(); await expect(page.getByLabel('管理内容')).toContainText('暂无记录。');
  expect(calls.at(-1)).toEqual({ q: '%_\\', identity: 'legacy', cursor: null });
  await expect(page.getByRole('button', { name: '加载更多', exact: true })).toBeHidden();
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('invalid user searches never go to the server and filter edits invalidate old rows and cursor', async ({ page }) => {
  let gets = 0;
  await setup(page, async (route, path) => path === '/admin/users' ? (++gets, json(route, { items: [primary], nextCursor: 'cursor-old' })) : defaults(route, path));
  await users(page); await expect(userRow(page)).toBeVisible();
  for (const value of ['a'.repeat(101), 'a\u200bb', '😀'.repeat(101)]) {
    await page.getByLabel('搜索用户', { exact: true }).fill(value); await page.getByRole('button', { name: '筛选用户', exact: true }).click();
    await expect(page.getByLabel('管理内容')).toContainText('搜索文字不合法'); await expect(userRow(page)).toHaveCount(0);
  }
  expect(gets).toBe(1);
});

test('devices are explicit, paginated and confirmed; single revoke zero/one and all revoke retain business rows', async ({ page }) => {
  let reads = 0, single = 0, all = 0, remaining = 3; const cursors: (string | null)[] = [];
  await setup(page, async (route, path) => {
    if (path === '/admin/users') return json(route, { items: [{ ...primary, activeSessions: remaining }], nextCursor: null });
    if (path === '/admin/users/acct-primary/sessions') {
      ++reads; const cursor = new URL(route.request().url()).searchParams.get('cursor'); cursors.push(cursor);
      if (!remaining) return json(route, { items: [], nextCursor: null });
      return cursor ? json(route, { items: [device('browser-last', 'browser')], nextCursor: null }) : json(route, { items: [device('bearer-first')], nextCursor: 'devices-next' });
    }
    if (path === '/admin/users/acct-primary/sessions/bearer-first/revoke') { adminWrite(route); ++single; remaining = 2; return json(route, { ok: true, revoked: 1 }); }
    if (path === '/admin/users/acct-primary/sessions/browser-last/revoke') { adminWrite(route); ++single; return json(route, { ok: true, revoked: 0 }); }
    if (path === '/admin/users/acct-primary/revoke-sessions') { adminWrite(route); ++all; remaining = 0; return json(route, { ok: true, revoked: 2 }); }
    return defaults(route, path);
  });
  await users(page); await expect(userRow(page)).toBeVisible(); expect(reads).toBe(0);
  await userRow(page).getByRole('button', { name: '查看设备', exact: true }).click();
  const detail = page.getByLabel('用户设备会话', { exact: true });
  await expect(detail).toContainText('内存 Bearer'); await expect(detail.getByRole('button', { name: '撤销此设备登录', exact: true })).toBeDisabled(); expect(single).toBe(0);
  await detail.getByRole('button', { name: '加载更多设备', exact: true }).click(); await expect(detail).toContainText('浏览器 Cookie'); expect(cursors).toEqual([null, 'devices-next']);
  const browser = detail.getByRole('row').filter({ hasText: 'browser-last' });
  await browser.getByLabel('确认仅撤销此设备的本地登录').check(); await browser.getByRole('button', { name: '撤销此设备登录', exact: true }).click();
  await expect(page.locator('.notice')).toContainText('已撤销 0 个用户登录');
  const bearer = detail.getByRole('row').filter({ hasText: 'bearer-first' });
  await bearer.getByLabel('确认仅撤销此设备的本地登录').check(); await bearer.getByRole('button', { name: '撤销此设备登录', exact: true }).click();
  await expect(page.locator('.notice')).toContainText('已撤销 1 个用户登录'); await expect(userRow(page)).toContainText('17');
  await userRow(page).getByLabel('确认撤销此用户当前全部登录').check(); await userRow(page).getByRole('button', { name: '撤销用户登录', exact: true }).click();
  await expect(detail).toContainText('已显示 0 个有效本地设备。'); expect(all).toBe(1); expect(single).toBe(2);
  await expect(page.getByLabel('管理内容')).toContainText('不是封禁'); await expect(page.getByLabel('管理内容')).toContainText('不阻止重新开发登录');
});

test('ambiguous revocation never retries itself and only an explicit matching device refresh unlocks a new confirmation', async ({ page }) => {
  let writes = 0;
  await setup(page, async (route, path) => {
    if (path === '/admin/users') return json(route, { items: [primary], nextCursor: null });
    if (path === '/admin/users/acct-primary/sessions') return json(route, { items: [device('one')], nextCursor: null });
    if (path.endsWith('/sessions/one/revoke')) { adminWrite(route); ++writes; return route.abort(); }
    return defaults(route, path);
  });
  await users(page); await userRow(page).getByRole('button', { name: '查看设备', exact: true }).click();
  const detail = page.getByLabel('用户设备会话', { exact: true });
  await detail.getByLabel('确认仅撤销此设备的本地登录').check(); await detail.getByRole('button', { name: '撤销此设备登录', exact: true }).click();
  await expect(page.locator('.notice')).toContainText('撤销结果不确定，未自动重试');
  await detail.getByLabel('确认仅撤销此设备的本地登录').check(); await expect(detail.getByRole('button', { name: '撤销此设备登录', exact: true })).toBeDisabled();
  expect(writes).toBe(1); await detail.getByRole('button', { name: '刷新设备列表', exact: true }).click();
  await expect(detail.getByLabel('确认仅撤销此设备的本地登录')).not.toBeChecked();
  await detail.getByLabel('确认仅撤销此设备的本地登录').check(); await expect(detail.getByRole('button', { name: '撤销此设备登录', exact: true })).toBeEnabled(); expect(writes).toBe(1);
});

test('late user queries and device owners cannot replace a new query, owner or view', async ({ page }) => {
  const queryGate = gate(), ownerGate = gate(); let deviceGets = 0;
  await setup(page, async (route, path) => {
    if (path === '/admin/users') {
      const q = new URL(route.request().url()).searchParams.get('q');
      if (q === 'slow') { queryGate.received(); await queryGate.waiting; return json(route, { items: [{ ...primary, userId: 'late-query-secret' }], nextCursor: 'late-cursor' }).catch(() => {}); }
      return json(route, { items: [primary, secondary], nextCursor: null });
    }
    if (path === '/admin/users/acct-primary/sessions') { ++deviceGets; ownerGate.received(); await ownerGate.waiting; return json(route, { items: [device('late-device-secret')], nextCursor: null }).catch(() => {}); }
    if (path === '/admin/users/acct-secondary/sessions') { ++deviceGets; return json(route, { items: [device('second-now')], nextCursor: null }); }
    if (path === '/admin/routes') return json(route, routes);
    return defaults(route, path);
  });
  await users(page); await page.getByLabel('搜索用户', { exact: true }).fill('slow'); await page.getByRole('button', { name: '筛选用户', exact: true }).click(); await queryGate.ready;
  await page.getByLabel('搜索用户', { exact: true }).fill('new'); await page.getByRole('button', { name: '筛选用户', exact: true }).click(); await expect(userRow(page)).toBeVisible(); queryGate.release();
  await expect(page.getByLabel('管理内容')).not.toContainText('late-query-secret');
  await userRow(page).getByRole('button', { name: '查看设备', exact: true }).click(); await ownerGate.ready;
  await userRow(page, secondary.userId).getByRole('button', { name: '查看设备', exact: true }).click(); await expect(page.getByLabel('用户设备会话', { exact: true })).toContainText('second-now');
  ownerGate.release(); await page.getByRole('button', { name: '路由配置', exact: true }).click(); await expect(page.getByLabel('管理内容')).not.toContainText('late-device-secret'); expect(deviceGets).toBe(2);
});

test('late revocation results cannot annotate or refresh a different selected owner', async ({ page }) => {
  const delayed = gate(), delivered = gate(); let ownerRefreshes = 0;
  await setup(page, async (route, path) => {
    if (path === '/admin/users') { if (new URL(route.request().url()).searchParams.has('q')) ++ownerRefreshes; return json(route, { items: [primary, secondary], nextCursor: null }); }
    if (path === '/admin/users/acct-primary/sessions') return json(route, { items: [device('old-write')], nextCursor: null });
    if (path === '/admin/users/acct-secondary/sessions') return json(route, { items: [device('current-owner')], nextCursor: null });
    if (path.endsWith('/sessions/old-write/revoke')) { adminWrite(route); delayed.received(); await delayed.waiting; await json(route, { ok: true, revoked: 99 }).catch(() => {}); delivered.release(); return; }
    return defaults(route, path);
  });
  await users(page); await userRow(page).getByRole('button', { name: '查看设备', exact: true }).click();
  const detail = page.getByLabel('用户设备会话', { exact: true });
  await detail.getByLabel('确认仅撤销此设备的本地登录').check(); await detail.getByRole('button', { name: '撤销此设备登录', exact: true }).click(); await delayed.ready;
  await userRow(page, secondary.userId).getByRole('button', { name: '查看设备', exact: true }).click(); await expect(detail).toContainText('current-owner');
  delayed.release(); await delivered.waiting; await page.getByRole('button', { name: '关闭设备', exact: true }).click();
  await expect(page.locator('.notice')).not.toContainText('已撤销 99'); await expect(detail).toBeEmpty(); expect(ownerRefreshes).toBe(0);
});

test('failed read-only routes and late route or diagnostics results never leak raw data into another view', async ({ page }) => {
  const routeGate = gate(), diagnosticGate = gate(); let reads = 0;
  await setup(page, async (route, path) => {
    if (path === '/admin/users') return json(route, { items: [], nextCursor: null });
    if (path === '/admin/routes') {
      if (++reads === 1) return json(route, { error: { code: 'not_found', message: 'private-route-config-never-render' } }, 404);
      routeGate.received(); await routeGate.waiting; return json(route, routes).catch(() => {});
    }
    if (path === '/admin/diagnostics') { diagnosticGate.received(); await diagnosticGate.waiting; return json(route, { ...diagnostics, version: 'late-diagnostic-private' }).catch(() => {}); }
    return defaults(route, path);
  });
  await page.getByRole('button', { name: '路由配置', exact: true }).click(); await expect(page.locator('.notice')).toContainText('记录不存在'); await expect(page.locator('body')).not.toContainText('private-route-config-never-render');
  await page.getByRole('button', { name: '刷新路由配置', exact: true }).click(); await routeGate.ready;
  await page.getByRole('button', { name: '健康诊断', exact: true }).click(); await diagnosticGate.ready;
  await users(page); routeGate.release(); diagnosticGate.release(); await expect(page.getByLabel('管理内容')).toContainText('暂无记录。');
  await expect(page.getByLabel('管理内容')).not.toContainText('configurationOnly=true'); await expect(page.getByLabel('管理内容')).not.toContainText('late-diagnostic-private');
});

test('routes are a read-only configuration summary, never an upstream probe or editable credential form', async ({ page }) => {
  let reads = 0, posts = 0;
  await setup(page, async (route, path) => {
    if (route.request().method() !== 'GET') ++posts;
    if (path === '/admin/routes') { ++reads; return json(route, routes); }
    return defaults(route, path);
  });
  await page.getByRole('button', { name: '路由配置', exact: true }).click(); const output = page.getByLabel('只读路由配置', { exact: true });
  await expect(output).toContainText('configurationOnly=true'); await expect(output).toContainText('12000'); await expect(output).toContainText('未调用模型');
  await expect(output.getByRole('textbox')).toHaveCount(0); expect(posts).toBe(0); expect(reads).toBe(1);
  await page.getByRole('button', { name: '刷新路由配置', exact: true }).click(); await expect.poll(() => reads).toBe(2); expect(posts).toBe(0);
});

test('identity diagnostics are optional and distinguish SQLite, PostgreSQL and historical restore evidence without raw warnings', async ({ page }) => {
  let reads = 0;
  await setup(page, async (route, path) => path === '/admin/diagnostics' ? json(route, ++reads === 1 ? diagnostics : { ...diagnostics, identity, identityBackup: { ...identityBackup, status: ['recent', 'missing', 'stale', 'invalid'][Math.min(reads - 2, 3)] } }) : defaults(route, path));
  await page.getByRole('button', { name: '健康诊断', exact: true }).click(); const output = page.getByLabel('脱敏健康诊断', { exact: true });
  await expect(output).toContainText('身份诊断未知：旧服务未提供 identity'); await expect(output).toContainText('身份备份未知：旧服务未提供 identityBackup');
  await expect(output).not.toContainText('private-path-issuer-phone-never-render');
  for (const label of ['近期', '缺失', '陈旧', '无效']) {
    await page.getByRole('button', { name: '刷新健康诊断', exact: true }).click();
    await expect(output).toContainText('仅有历史报告（recorded），本次未验证恢复');
    const pg = output.locator('h3', { hasText: '身份数据库（Logto PostgreSQL）' }); await expect(pg).toBeVisible();
    await expect(output).toContainText('业务数据库（SQLite）'); await expect(output).toContainText('未验证（checksumVerified=false）'); await expect(output).toContainText(label);
  }
  await expect(output).toContainText('未执行（not_performed）'); await expect(output).toContainText('独立 Logto Console');
  await expect(output).toContainText('运行门禁'); await expect(output).toContainText('已关闭');
});

test('401 clears private admin views while 403 and 429 remain generic and never reveal response bodies', async ({ page }) => {
  let status = 403;
  await setup(page, async (route, path) => path === '/admin/users' ? json(route, { error: { code: 'private-code', message: 'private-raw-secret-do-not-render' } }, status) : defaults(route, path));
  await users(page); await expect(page.locator('.notice')).toContainText('没有权限'); await expect(page.locator('body')).not.toContainText('private-raw-secret-do-not-render');
  status = 429; await page.getByRole('button', { name: '刷新列表', exact: true }).click(); await expect(page.locator('.notice')).toContainText('请求过于频繁');
  status = 401; await page.getByRole('button', { name: '刷新列表', exact: true }).click(); await expect(page.getByLabel('管理内容')).toBeEmpty(); await expect(page.getByRole('heading', { name: 'Cloudflare Access 管理登录', exact: true })).toBeVisible();
});

test('logout clears content immediately and late old user GET or logout 401 cannot clear a newly restored session', async ({ page }) => {
  const oldUsers = gate(), oldLogout = gate(); let sessionReads = 0, nextToken = 'old-admin-csrf';
  await fixture(page, async (route, path) => {
    expect(route.request().headers()['authorization']).toBeUndefined();
    if (path === '/admin/session') { ++sessionReads; return json(route, { authenticated: true, csrfToken: nextToken, expiresAt: stamp }); }
    if (path === '/admin/overview') return json(route, overview);
    if (path === '/admin/users') { oldUsers.received(); await oldUsers.waiting; return json(route, { items: [{ ...primary, userId: 'old-private-owner' }], nextCursor: null }).catch(() => {}); }
    if (path === '/admin/logout') { adminWrite(route, 'old-admin-csrf'); oldLogout.received(); await oldLogout.waiting; return json(route, { error: { code: 'unauthorized', message: 'private-old-logout' } }, 401); }
    if (path === '/admin/routes') return json(route, routes);
    return defaults(route, path);
  });
  await page.goto(`${origin}/admin/`); await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
  await users(page); await oldUsers.ready; await page.getByRole('button', { name: '注销管理', exact: true }).click(); await oldLogout.ready;
  await expect(page.getByLabel('管理内容')).toBeEmpty(); nextToken = 'new-admin-csrf';
  await page.getByRole('button', { name: '检查已有管理登录', exact: true }).click(); await expect(page.getByRole('heading', { name: '用户', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '路由配置', exact: true }).click(); await expect(page.getByLabel('只读路由配置', { exact: true })).toContainText('configurationOnly=true');
  oldUsers.release(); oldLogout.release(); await expect(page.getByRole('button', { name: '注销管理', exact: true })).toBeVisible(); await expect(page.locator('body')).not.toContainText('old-private-owner'); await expect(page.locator('body')).not.toContainText('private-old-logout'); expect(sessionReads).toBe(2);
});
