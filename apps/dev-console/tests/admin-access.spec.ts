import { test, expect } from '@playwright/test';
import type { Route } from '@playwright/test';
import { fixture, json, origin, stamp, gate } from './browser-fixture.ts';

// All requests, including document navigations, are intercepted on virtual loopback 49183.
// No Cloudflare login, issuer, API listener, admin session or real credentials are used.
const overview = { users: 1, conversations: 1, messages: 0, runs: { running: 0, completed: 0, failed: 0, cancelled: 0 }, activeSessions: 1, environment: 'development', provider: 'mock', maxConcurrentRuns: 2 };
const session = (csrfToken = 'fixture-access-csrf', email = 'fixture-admin@example.invalid') => ({ authenticated: true, csrfToken, expiresAt: stamp, authentication: 'cloudflare-access', identity: { email }, logoutUrl: '/cdn-cgi/access/logout' });
const denied = (route: Route, status = 401) => json(route, { error: { code: 'fixture-denied', message: 'private-response-do-not-render' } }, status);
function independent(route: Route): void {
  const headers = route.request().headers();
  for (const name of ['authorization', 'cf-access-jwt-assertion', 'cf-access-authenticated-user-email', 'x-allira-oidc-csrf', 'x-allira-social-csrf']) expect(headers[name]).toBeUndefined();
}
function write(route: Route, token: string): void {
  independent(route); expect(route.request().headers()['origin']).toBe(origin); expect(route.request().headers()['x-csrf-token']).toBe(token);
}

test('startup GET discards any old fragment without code exchange and renders Access identity as inert text', async ({ page }) => {
  const email = '<img src=x onerror=globalThis.pwned=1>@example.invalid'; let reads = 0, exchanges = 0, posts = 0;
  await fixture(page, async (route, path) => {
    independent(route);
    expect(route.request().url()).not.toContain('fixture-old-code'); expect(route.request().postData() ?? '').not.toContain('fixture-old-code');
    if (route.request().method() !== 'GET') { posts++; if (path === '/admin/session') exchanges++; return denied(route, 403); }
    if (path === '/admin/session') { reads++; expect(route.request().headers()['x-csrf-token']).toBeUndefined(); return json(route, session('fixture-private-admin-csrf', email)); }
    if (path === '/admin/overview') return json(route, overview);
    return denied(route, 404);
  });
  await page.goto(`${origin}/admin/#code=fixture-old-code&token=fixture-unused`);
  await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
  expect(await page.evaluate(() => location.hash)).toBe(''); await expect(page.locator('body')).toContainText(email);
  for (const hidden of ['fixture-old-code', 'fixture-unused', 'fixture-private-admin-csrf', 'private-response-do-not-render', 'SSH', 'issue-login']) await expect(page.locator('body')).not.toContainText(hidden);
  await expect(page.getByRole('button', { name: '登录管理', exact: true })).toHaveCount(0);
  await expect(page.locator('img,input[type=password],input[name=code],input[name=token]')).toHaveCount(0);
  expect(await page.evaluate(() => 'pwned' in globalThis)).toBe(false);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  expect(reads).toBe(1); expect(exchanges).toBe(0); expect(posts).toBe(0);
});

test('old three-field DTO has an explicit unknown identity without fabricating an email', async ({ page }) => {
  await fixture(page, async (route, path) => path === '/admin/session' ? json(route, { authenticated: true, csrfToken: 'fixture-old', expiresAt: stamp }) : path === '/admin/overview' ? json(route, overview) : denied(route, 404));
  await page.goto(`${origin}/admin/`); await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
  await expect(page.locator('.identity')).toContainText('未知（Unknown'); await expect(page.locator('.identity')).not.toContainText('@');
});

test('401 shows only Access entry and uses a complete document navigation to fixed /admin/ without query or fragment', async ({ page }) => {
  let reads = 0, exchanges = 0; const documents: string[] = [];
  page.on('request', request => { if (request.isNavigationRequest() && request.frame() === page.mainFrame()) documents.push(new URL(request.url()).pathname + new URL(request.url()).search); });
  await fixture(page, async (route, path) => {
    independent(route);
    if (path === '/admin/session') { if (route.request().method() === 'POST') { exchanges++; return denied(route, 403); } return ++reads === 1 ? denied(route) : json(route, session()); }
    if (path === '/admin/overview') return json(route, overview);
    return denied(route, 404);
  });
  await page.goto(`${origin}/admin/?next=https%3A%2F%2Fevil.invalid#code=fixture-discard`);
  await expect(page.getByRole('heading', { name: 'Cloudflare Access 管理登录', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '通过 Cloudflare Access 登录', exact: true })).toBeEnabled(); expect(reads).toBe(1);
  await expect(page.locator('body')).not.toContainText('private-response-do-not-render'); await expect(page.locator('body')).not.toContainText('fixture-discard');
  await page.getByRole('button', { name: '通过 Cloudflare Access 登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
  expect(documents).toHaveLength(2); expect(documents[1]).toBe('/admin/'); expect(page.url()).toBe(`${origin}/admin/`);
  expect(reads).toBe(2); expect(exchanges).toBe(0);
});

for (const [status, label] of [[403, '没有权限'], [429, '请求过于频繁'], [503, '服务暂时不可用']] as const) {
  test(`startup ${status} is generic and never retries or falls back to POST session`, async ({ page }) => {
    let reads = 0, posts = 0;
    await fixture(page, async (route, path) => { independent(route); if (route.request().method() !== 'GET') posts++; if (path === '/admin/session') { reads++; return denied(route, status); } return denied(route, 404); });
    await page.goto(`${origin}/admin/`); await expect(page.locator('.notice')).toContainText(label);
    await expect(page.locator('body')).not.toContainText('private-response-do-not-render');
    await expect(page.getByRole('button', { name: '通过 Cloudflare Access 登录', exact: true })).toBeEnabled();
    expect(reads).toBe(1); expect(posts).toBe(0); expect(page.url()).toBe(`${origin}/admin/`);
  });
}

test('uncertain startup performs no retry, while an explicit session check is a GET without navigation', async ({ page }) => {
  let reads = 0, posts = 0, documents = 0;
  page.on('request', request => { if (request.isNavigationRequest()) documents++; });
  await fixture(page, async (route, path) => {
    independent(route); if (route.request().method() !== 'GET') posts++;
    if (path === '/admin/session') return ++reads === 1 ? route.abort() : json(route, session());
    if (path === '/admin/overview') return json(route, overview);
    return denied(route, 404);
  });
  await page.goto(`${origin}/admin/`); await expect(page.locator('.notice')).toContainText('网络连接失败'); expect(reads).toBe(1);
  await page.getByRole('button', { name: '检查已有管理登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible(); expect(reads).toBe(2); expect(posts).toBe(0); expect(documents).toBe(1);
});

test('logout clears private UI immediately, posts captured CSRF once and navigates only to fixed Access logout after success', async ({ page }) => {
  const ending = gate(); let posts = 0, navigations = 0;
  await fixture(page, async (route, path) => {
    independent(route);
    if (path === '/admin/session') return json(route, { ...session('fixture-captured', 'private-fixture-email@example.invalid'), logoutUrl: 'https://evil.invalid/logout' });
    if (path === '/admin/overview') return json(route, overview);
    if (path === '/admin/logout') { posts++; write(route, 'fixture-captured'); expect(route.request().postData()).toBeNull(); ending.received(); await ending.waiting; return json(route, { ok: true, logoutUrl: 'https://evil.invalid/logout' }); }
    return denied(route, 404);
  });
  await page.route(`${origin}/cdn-cgi/access/logout`, async route => { expect(route.request().isNavigationRequest()).toBe(true); expect(route.request().method()).toBe('GET'); navigations++; await route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>固定 Access 退出路由</title>' }); });
  await page.goto(`${origin}/admin/`); await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '注销管理', exact: true }).click(); await ending.ready;
  await expect(page.getByLabel('管理内容')).toBeEmpty(); await expect(page.locator('body')).not.toContainText('private-fixture-email'); await expect(page.getByRole('button', { name: '注销管理', exact: true })).toBeHidden();
  expect(posts).toBe(1); expect(navigations).toBe(0);
  ending.release(); await expect(page).toHaveURL(`${origin}/cdn-cgi/access/logout`); expect(navigations).toBe(1); expect(posts).toBe(1);
});

for (const status of [401, 503, 0]) {
  test(`uncertain or failed logout ${status} never navigates, replays or restores private data`, async ({ page }) => {
    let posts = 0, reads = 0, navigations = 0;
    await fixture(page, async (route, path) => {
      independent(route);
      if (path === '/admin/session') { reads++; return json(route, session()); }
      if (path === '/admin/overview') return json(route, overview);
      if (path === '/admin/logout') { posts++; write(route, 'fixture-access-csrf'); return status ? denied(route, status) : route.abort(); }
      return denied(route, 404);
    });
    await page.route(`${origin}/cdn-cgi/access/logout`, async route => { navigations++; await route.abort(); });
    await page.goto(`${origin}/admin/`); await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
    await page.getByRole('button', { name: '注销管理', exact: true }).click();
    await expect(page.locator('.notice')).toContainText('服务端注销结果不确定'); await expect(page.getByLabel('管理内容')).toBeEmpty();
    await expect(page.getByRole('heading', { name: 'Cloudflare Access 管理登录', exact: true })).toBeVisible();
    await expect(page.locator('body')).not.toContainText('private-response-do-not-render'); expect(posts).toBe(1); expect(reads).toBe(1); expect(navigations).toBe(0);
  });
}

test('late successful logout and old user response cannot navigate over a newly restored Access session', async ({ page }) => {
  const ending = gate(), oldUsers = gate(); let reads = 0, writes = 0, navigations = 0;
  const user = { userId: 'new-owner', conversationCount: 1, runCount: 0, activeSessions: 1 };
  await fixture(page, async (route, path) => {
    independent(route);
    if (path === '/admin/session') return json(route, session(++reads === 1 ? 'fixture-old' : 'fixture-current', reads === 1 ? 'old@example.invalid' : 'current@example.invalid'));
    if (path === '/admin/overview') return json(route, overview);
    if (path === '/admin/users' && reads === 1) { oldUsers.received(); await oldUsers.waiting; return json(route, { items: [{ ...user, userId: 'old-private-owner' }], nextCursor: null }).catch(() => {}); }
    if (path === '/admin/users') return json(route, { items: [user], nextCursor: null });
    if (path === '/admin/logout') { write(route, 'fixture-old'); ending.received(); await ending.waiting; return json(route, { ok: true }); }
    if (path === '/admin/users/new-owner/revoke-sessions') { writes++; write(route, 'fixture-current'); return json(route, { ok: true, revoked: 1 }); }
    return denied(route, 404);
  });
  await page.route(`${origin}/cdn-cgi/access/logout`, async route => { navigations++; await route.abort(); });
  await page.goto(`${origin}/admin/`); await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '用户', exact: true }).click(); await oldUsers.ready;
  await page.getByRole('button', { name: '注销管理', exact: true }).click(); await ending.ready;
  await expect(page.getByLabel('管理内容')).toBeEmpty();
  await page.getByRole('button', { name: '检查已有管理登录', exact: true }).click(); await expect(page.getByRole('button', { name: '撤销用户登录', exact: true })).toBeVisible();
  const oldLogoutFinished = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/admin/logout');
  oldUsers.release(); ending.release(); await oldLogoutFinished; await page.getByLabel('确认撤销此用户当前全部登录').check(); await page.getByRole('button', { name: '撤销用户登录', exact: true }).click();
  await expect(page.locator('.notice')).toContainText('已撤销 1 个用户登录'); await expect(page.locator('.identity')).toContainText('current@example.invalid');
  await expect(page.locator('body')).not.toContainText('old-private-owner'); expect(reads).toBe(2); expect(writes).toBe(1); expect(navigations).toBe(0);
});

test('a current business 401 clears protected content without auto-login or legacy POST', async ({ page }) => {
  let reads = 0, exchanges = 0;
  await fixture(page, async (route, path) => {
    independent(route);
    if (path === '/admin/session') { if (route.request().method() !== 'GET') exchanges++; reads++; return json(route, session()); }
    if (path === '/admin/overview') return json(route, overview);
    if (path === '/admin/users') return denied(route);
    return denied(route, 404);
  });
  await page.goto(`${origin}/admin/`); await expect(page.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '用户', exact: true }).click();
  await expect(page.getByLabel('管理内容')).toBeEmpty(); await expect(page.getByRole('button', { name: '通过 Cloudflare Access 登录', exact: true })).toBeVisible();
  await expect(page.locator('body')).not.toContainText('private-response-do-not-render'); expect(reads).toBe(1); expect(exchanges).toBe(0);
});
