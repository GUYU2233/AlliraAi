import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import type { Page, Route } from '@playwright/test';
import { account, accountSession, c1, defaults, fixture, gate, json, openAdvanced, openDetails, openDeveloper, origin } from './browser-fixture.ts';

const expiresAt = '2028-01-01T00:00:00Z';
const issuer = 'https://logto.example.invalid';
const options = (enabled = true, authorizationOrigin: string | null = issuer) => ({ backend: 'oidc', oidc: { enabled, provider: 'logto', simulated: false, reason: enabled ? null : 'unconfigured', authorizationOrigin }, phone: { enabled: true, simulated: true, reason: null, countryCallingCodes: ['+86'] }, social: [{ provider: 'wechat', enabled: true, simulated: true, reason: null }], legacyPasswordEnabled: true, registrationEnabled: true });
const transition = (purpose: 'login' | 'erase' | 'phone_migration' | null = 'login') => ({ enabled: true, simulated: false, csrfToken: 'fixture-oidc-csrf', pending: purpose ? { provider: 'logto', maskedPhone: '+86 138****8000', expiresAt, purpose } : null });
const browserSession = { user: account, csrfToken: 'fixture-ordinary-csrf', expiresAt };
async function consent(page: Page): Promise<void> {
  await page.getByLabel('托管登录：我已满 18 岁', { exact: true }).check();
  await page.getByLabel('托管登录：接受当前服务协议', { exact: true }).check();
  await page.getByLabel('确认使用上方已验证手机号身份进入 AlliraAi，不静默合并其他账号', { exact: true }).check();
}
function oidcWrite(route: Route, ordinaryCsrf?: string): void {
  const headers = route.request().headers();
  expect(headers.authorization).toBeUndefined(); expect(headers['x-allira-oidc-csrf']).toBe('fixture-oidc-csrf');
  expect(headers['x-csrf-token']).toBe(ordinaryCsrf); expect(headers.origin).toBe(origin);
}
async function setup(page: Page, api: (route: Route, path: string) => Promise<unknown>, configured = options()): Promise<void> {
  await fixture(page, async (route, path) => path === '/auth/options' ? json(route, configured) : api(route, path));
}
async function accountPage(page: Page): Promise<void> { await openAdvanced(page); await page.getByRole('button', { name: '账号', exact: true }).click(); await expect(page.getByLabel('显示名称', { exact: true })).toHaveValue(account.displayName); }

const phoneMigrationConsent = '确认把本次新鲜验证的同一手机号关联到原无密码账号，保留历史并撤销旧登录';

// Fully intercepted at virtual :49183. No real issuer, API, SMS provider or server is contacted.
test('OIDC mode hides every local credential flow; GET pending is not login and explicit consent exchanges one ordinary Cookie session', async ({ page }) => {
  const writes: string[] = []; let ordinary = false;
  await setup(page, async (route, path) => {
    if (path === '/auth/oidc/session') return json(route, transition());
    if (path.startsWith('/auth/oidc/') && route.request().method() === 'POST') { oidcWrite(route); writes.push(path); }
    if (path === '/auth/oidc/complete') { expect(route.request().postDataJSON()).toEqual({ acceptedTermsVersion: '2026-11-19', isAdult: true, confirmIdentity: true }); return json(route, accountSession); }
    if (path === '/auth/browser/session' && route.request().method() === 'POST') { writes.push(path); expect(route.request().headers().authorization).toBe(`Bearer ${accountSession.accessToken}`); ordinary = true; return json(route, browserSession); }
    if (path === '/auth/browser/session' && ordinary) return json(route, browserSession);
    if (path === '/account/identity' || path.startsWith('/auth/social/') || path.startsWith('/auth/phone/')) throw new Error('local identity flow should not be called');
    return defaults(route, path);
  });
  await page.setViewportSize({ width: 375, height: 812 }); await page.goto(`${origin}/test/?code=ignored#token=ignored`);
  await expect(page.getByRole('button', { name: '确认身份并进入聊天', exact: true })).toBeEnabled();
  await expect(page.getByLabel('登录手机号', { exact: true })).toBeHidden(); await expect(page.getByLabel('账号用户名', { exact: true })).toBeHidden(); await expect(page.getByLabel('恢复账号用户名', { exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: '聊天', exact: true })).toBeHidden(); expect(writes).toEqual([]);
  await page.getByRole('button', { name: '确认身份并进入聊天', exact: true }).click(); expect(writes).toEqual([]);
  await consent(page); await page.getByLabel('托管登录：记住此设备（共享设备请勿勾选）', { exact: true }).check(); await page.getByRole('button', { name: '确认身份并进入聊天', exact: true }).click();
  await expect(page.locator('.identity')).toContainText('已记住此设备'); expect(writes).toEqual(['/auth/oidc/complete', '/auth/browser/session']);
  await accountPage(page); await expect(page.getByRole('heading', { name: '托管身份与账号安全', exact: true })).toBeVisible();
  for (const label of ['当前密码', '签发恢复码的当前密码', '删除账号的当前密码', '账号安全六位验证码', '绑定手机号']) await expect(page.getByLabel(label, { exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: '读取登录设备', exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: '下载账号完整数据', exact: true })).toBeVisible();
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('409 collision only reveals explicit old account proof and migration preserves returned owner and existing chat', async ({ page }) => {
  const writes: { path: string; body: unknown }[] = [];
  await setup(page, async (route, path) => {
    if (path === '/auth/oidc/session') return json(route, transition());
    if (path.startsWith('/auth/oidc/') && route.request().method() === 'POST') { oidcWrite(route); writes.push({ path, body: route.request().postDataJSON() }); }
    if (path === '/auth/oidc/complete') return json(route, { error: { code: 'identity_link_required', message: 'private-old-owner-and-secret' } }, 409);
    if (path === '/auth/oidc/migrate') return json(route, accountSession);
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.getByRole('button', { name: '确认身份并进入聊天', exact: true })).toBeEnabled(); await consent(page); await page.getByRole('button', { name: '确认身份并进入聊天', exact: true }).click();
  await expect(page.getByLabel('关联旧账号用户名', { exact: true })).toBeVisible(); await expect(page.locator('body')).not.toContainText('private-old-owner-and-secret');
  await page.getByLabel('关联旧账号用户名', { exact: true }).fill('account_1'); await page.getByLabel('关联旧账号当前密码', { exact: true }).fill('1234567890');
  await page.getByRole('button', { name: '验证旧账号并明确关联', exact: true }).click(); expect(writes).toHaveLength(1);
  await page.getByLabel('确认把本次已验证身份关联到我证明的旧账号，保留原账号数据', { exact: true }).check(); await page.getByRole('button', { name: '验证旧账号并明确关联', exact: true }).click();
  await expect(page.locator('.identity')).toContainText(account.displayName); await expect(page.getByLabel('关联旧账号当前密码', { exact: true })).toHaveValue('');
  await page.getByRole('button', { name: c1.title, exact: true }).click(); await expect(page.getByRole('heading', { name: c1.title, exact: true })).toBeVisible();
  expect(writes[1]).toEqual({ path: '/auth/oidc/migrate', body: { acceptedTermsVersion: '2026-11-19', isAdult: true, confirmIdentity: true, username: 'account_1', currentPassword: '1234567890' } });
});

test('an unbound old username account can explicitly migrate before any new-account completion is attempted', async ({ page }) => {
  let completes = 0; let migrations = 0;
  await setup(page, async (route, path) => {
    if (path === '/auth/oidc/session') return json(route, transition());
    if (path === '/auth/oidc/complete') { ++completes; return json(route, accountSession); }
    if (path === '/auth/oidc/migrate') { ++migrations; oidcWrite(route); expect(route.request().postDataJSON()).toEqual({ acceptedTermsVersion: '2026-11-19', isAdult: true, confirmIdentity: true, username: 'unbound_old', currentPassword: '1234567890' }); return json(route, accountSession); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.getByRole('button', { name: '确认身份并进入聊天', exact: true })).toBeEnabled(); await expect(page.getByLabel('关联旧账号用户名', { exact: true })).toBeHidden();
  await openDetails(page, '我有旧账号，保留历史'); await consent(page); await expect(page.getByRole('button', { name: '确认身份并进入聊天', exact: true })).toBeDisabled();
  await page.getByLabel('关联旧账号用户名', { exact: true }).fill('unbound_old'); await page.getByLabel('关联旧账号当前密码', { exact: true }).fill('1234567890'); await page.getByLabel('确认把本次已验证身份关联到我证明的旧账号，保留原账号数据', { exact: true }).check();
  await page.getByRole('button', { name: '验证旧账号并明确关联', exact: true }).click(); await expect(page.locator('.identity')).toContainText(account.displayName);
  expect(completes).toBe(0); expect(migrations).toBe(1); await page.getByRole('button', { name: c1.title, exact: true }).click(); await expect(page.getByRole('heading', { name: c1.title, exact: true })).toBeVisible();
});

test('phone-only migration has an explicit fresh-purpose start and never borrows an ordinary CSRF or Bearer', async ({ page }) => {
  let starts = 0;
  await setup(page, async (route, path) => {
    if (path === '/auth/oidc/session') return json(route, transition(null));
    if (path === '/auth/oidc/start') { ++starts; oidcWrite(route); expect(route.request().postDataJSON()).toEqual({ purpose: 'phone_migration' }); return json(route, { authorizationUrl: 'https://evil.invalid/never-follow' }); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.getByRole('button', { name: '原来仅用手机号登录，保留历史', exact: true })).toBeEnabled(); expect(starts).toBe(0);
  await page.getByRole('button', { name: '原来仅用手机号登录，保留历史', exact: true }).click(); await expect(page.getByRole('status').filter({ hasText: '已阻止跳转' })).toBeVisible(); expect(starts).toBe(1);
});

test('phone-only fresh pending requires its own unchecked migration consent and returns original owner without username proof', async ({ page }) => {
  let posts = 0;
  await setup(page, async (route, path) => {
    if (path === '/auth/oidc/session') return json(route, transition('phone_migration'));
    if (path === '/auth/oidc/complete') { ++posts; oidcWrite(route); expect(route.request().postDataJSON()).toEqual({ acceptedTermsVersion: '2026-11-19', isAdult: true, confirmIdentity: true, confirmPhoneMigration: true }); return json(route, accountSession); }
    if (path === '/auth/oidc/migrate') throw new Error('phone-only migration must not collect a username password');
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.getByLabel(phoneMigrationConsent, { exact: true })).toBeVisible(); await expect(page.getByLabel(phoneMigrationConsent, { exact: true })).not.toBeChecked(); await expect(page.getByLabel('关联旧账号当前密码', { exact: true })).toBeHidden();
  await consent(page); await page.getByRole('button', { name: '确认身份并进入聊天', exact: true }).click(); expect(posts).toBe(0); await expect(page.getByRole('status').filter({ hasText: '请独立确认' })).toBeVisible();
  await page.getByLabel(phoneMigrationConsent, { exact: true }).check(); await page.getByRole('button', { name: '重新读取托管认证状态', exact: true }).click(); await expect(page.getByLabel(phoneMigrationConsent, { exact: true })).not.toBeChecked();
  await consent(page); await page.getByLabel(phoneMigrationConsent, { exact: true }).check(); await page.getByRole('button', { name: '确认身份并进入聊天', exact: true }).click(); await expect(page.locator('.identity')).toContainText(account.displayName); expect(posts).toBe(1);
  await page.getByRole('button', { name: c1.title, exact: true }).click(); await expect(page.getByRole('heading', { name: c1.title, exact: true })).toBeVisible();
});

test('ordinary login cannot opt itself into phone-only migration; cancellation is required before a new purpose', async ({ page }) => {
  const paths: string[] = []; let pending = true;
  await setup(page, async (route, path) => {
    if (path === '/auth/oidc/session') return json(route, transition(pending ? 'login' : null));
    if (path.startsWith('/auth/oidc/') && route.request().method() === 'POST') { oidcWrite(route); paths.push(path); }
    if (path === '/auth/oidc/cancel') { pending = false; return json(route, { ok: true }); }
    if (path === '/auth/oidc/start') { expect(route.request().postDataJSON()).toEqual({ purpose: 'phone_migration' }); return json(route, { authorizationUrl: 'https://evil.invalid/never-follow' }); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.getByRole('button', { name: '确认身份并进入聊天', exact: true })).toBeEnabled(); await expect(page.getByLabel(phoneMigrationConsent, { exact: true })).toBeHidden(); await expect(page.getByRole('button', { name: '原来仅用手机号登录，保留历史', exact: true })).toBeHidden();
  await page.getByRole('button', { name: '取消本次托管认证', exact: true }).click(); await expect(page.getByRole('button', { name: '原来仅用手机号登录，保留历史', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '原来仅用手机号登录，保留历史', exact: true }).click(); await expect(page.getByRole('status').filter({ hasText: '已阻止跳转' })).toBeVisible(); expect(paths).toEqual(['/auth/oidc/cancel', '/auth/oidc/start']);
});

test('phone-only unknown completion stays single-post and a late refresh cannot restore migration consent into a newer user', async ({ page }) => {
  const delayed = gate(); let reads = 0; let posts = 0;
  await setup(page, async (route, path) => {
    if (path === '/auth/oidc/session') { ++reads; if (reads > 1) { delayed.received(); await delayed.waiting; } return json(route, transition('phone_migration')).catch(() => {}); }
    if (path === '/auth/oidc/complete') { ++posts; oidcWrite(route); return route.abort('connectionfailed'); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.getByLabel(phoneMigrationConsent, { exact: true })).toBeVisible(); await consent(page); await page.getByLabel(phoneMigrationConsent, { exact: true }).check(); await page.getByRole('button', { name: '确认身份并进入聊天', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '不会自动重试' })).toBeVisible(); await expect(page.getByRole('button', { name: '确认身份并进入聊天', exact: true })).toBeDisabled(); expect(posts).toBe(1);
  await page.getByRole('button', { name: '重新读取托管认证状态', exact: true }).click(); await delayed.ready; await openDeveloper(page); await page.getByLabel('开发用户 ID', { exact: true }).fill('phone-migration-race'); await page.getByRole('button', { name: '开发登录', exact: true }).click(); await expect(page.getByRole('button', { name: '聊天', exact: true })).toBeVisible(); delayed.release();
  await openAdvanced(page); await page.getByRole('button', { name: '能量用量', exact: true }).click(); await expect(page.locator('body')).not.toContainText('专用新鲜认证'); await page.getByRole('button', { name: '退出登录', exact: true }).click(); await expect(page.getByLabel(phoneMigrationConsent, { exact: true })).toBeHidden(); expect(posts).toBe(1);
});

test('hosted navigation uses only configured issuer origin and rejects unexpected origin without following returned URL', async ({ page }) => {
  let starts = 0; const visits: string[] = [];
  await setup(page, async (route, path) => {
    if (path === '/auth/oidc/session') return json(route, transition(null));
    if (path === '/auth/oidc/start') { ++starts; oidcWrite(route); expect(route.request().postDataJSON()).toEqual({ purpose: 'login' }); return json(route, { authorizationUrl: 'https://evil.invalid/login?state=private' }); }
    return defaults(route, path);
  });
  page.on('request', request => { if (new URL(request.url()).origin !== origin) visits.push(request.url()); });
  await page.goto(`${origin}/test/`); await page.getByRole('button', { name: '使用手机号登录 / 注册', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '已阻止跳转' })).toBeVisible(); expect(starts).toBe(1); expect(visits).toEqual([]);
  await expect(page.getByRole('button', { name: '使用手机号登录 / 注册', exact: true })).toBeDisabled(); await expect(page.locator('body')).not.toContainText('state=private');
});

test('lost OIDC completion is never posted again automatically; only a manual GET may unlock a new explicit operation', async ({ page }) => {
  await page.clock.install(); let posts = 0; let reads = 0;
  await setup(page, async (route, path) => {
    if (path === '/auth/oidc/session') { ++reads; return json(route, transition()); }
    if (path === '/auth/oidc/complete') { oidcWrite(route); ++posts; return route.abort('connectionfailed'); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.getByRole('button', { name: '确认身份并进入聊天', exact: true })).toBeEnabled(); await consent(page); await page.getByRole('button', { name: '确认身份并进入聊天', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '不会自动重试' })).toBeVisible(); await page.clock.runFor(120_000); expect(posts).toBe(1); expect(reads).toBe(1);
  await expect(page.getByRole('button', { name: '确认身份并进入聊天', exact: true })).toBeDisabled(); await page.getByRole('button', { name: '重新读取托管认证状态', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认身份并进入聊天', exact: true })).toBeEnabled(); await expect(page.getByLabel('托管登录：我已满 18 岁', { exact: true })).not.toBeChecked(); expect(posts).toBe(1); expect(reads).toBe(2);
});

test('late hosted pending GET cannot resurrect a newer developer account or disclose stale identity after logout', async ({ page }) => {
  const delayed = gate(); let posts = 0;
  await setup(page, async (route, path) => {
    if (path === '/auth/oidc/session') { delayed.received(); await delayed.waiting; const value = transition(); value.pending!.maskedPhone = '不应显示的旧手机号'; return json(route, value).catch(() => {}); }
    if (path.startsWith('/auth/oidc/') && route.request().method() === 'POST') ++posts;
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await delayed.ready; await openDeveloper(page); await page.getByLabel('开发用户 ID', { exact: true }).fill('isolated-oidc-race'); await page.getByRole('button', { name: '开发登录', exact: true }).click();
  await expect(page.getByRole('button', { name: '聊天', exact: true })).toBeVisible(); delayed.release(); await openAdvanced(page); await page.getByRole('button', { name: '能量用量', exact: true }).click();
  await expect(page.locator('body')).not.toContainText('不应显示的旧手机号'); await page.getByRole('button', { name: '退出登录', exact: true }).click(); await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
  await expect(page.locator('body')).not.toContainText('不应显示的旧手机号'); expect(posts).toBe(0);
});

test('disabled hosted configuration never falls back to local username registration or SMS and never starts OIDC', async ({ page }) => {
  const paths: string[] = [];
  await setup(page, async (route, path) => { if (path.startsWith('/auth/oidc/') || path.startsWith('/auth/phone/') || path.startsWith('/auth/social/')) paths.push(path); return defaults(route, path); }, options(false, null));
  await page.goto(`${origin}/test/`); await expect(page.getByRole('button', { name: '使用手机号登录 / 注册', exact: true })).toBeDisabled();
  await expect(page.getByLabel('账号用户名', { exact: true })).toBeHidden(); await expect(page.getByLabel('登录手机号', { exact: true })).toBeHidden(); await expect(page.getByRole('button', { name: '注册账号', exact: true })).toBeHidden();
  await expect(page.locator('body')).toContainText('不会伪装可用'); expect(paths).toEqual([]);
});

test('erase-purpose callback waits for ordinary browser restore, opens account only, and requires literal plus consent with separate CSRF', async ({ page }) => {
  const restored = gate(); const writes: string[] = []; let erased = false;
  await setup(page, async (route, path) => {
    if (path === '/auth/browser/session') { if (erased) return defaults(route, path); restored.received(); await restored.waiting; return json(route, browserSession); }
    if (path === '/auth/oidc/session') return json(route, transition('erase'));
    if (path === '/auth/oidc/erase') { oidcWrite(route, 'fixture-ordinary-csrf'); expect(route.request().postDataJSON()).toEqual({ confirmation: 'DELETE MY ACCOUNT' }); writes.push(path); erased = true; return json(route, { ok: true }); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await restored.ready; expect(writes).toEqual([]); await expect(page.getByRole('heading', { name: '账号维护', exact: true })).toHaveCount(0); restored.release();
  await expect(page.getByRole('heading', { name: '账号维护', exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: '确认身份并进入聊天', exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: '永久删除 AlliraAi 业务数据', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '永久删除 AlliraAi 业务数据', exact: true }).click(); expect(writes).toEqual([]); await page.getByLabel('删除业务数据：逐字输入 DELETE MY ACCOUNT', { exact: true }).fill('DELETE MY ACCOUNT'); await page.getByRole('button', { name: '永久删除 AlliraAi 业务数据', exact: true }).click(); expect(writes).toEqual([]);
  await page.getByLabel('确认永久删除 AlliraAi 业务数据，理解这不会删除 Logto 全局账号或物理擦除历史备份', { exact: true }).check(); await page.getByRole('button', { name: '永久删除 AlliraAi 业务数据', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible(); await expect(page.locator('body')).toContainText('Logto 全局身份未删除'); expect(writes).toEqual(['/auth/oidc/erase']);
});

test('activity conflict preserves erase proof for an explicit retry and leaving account wipes deletion consent', async ({ page }) => {
  let posts = 0;
  await setup(page, async (route, path) => {
    if (path === '/auth/browser/session') return json(route, browserSession);
    if (path === '/auth/oidc/session') return json(route, transition('erase'));
    if (path === '/auth/oidc/erase') { oidcWrite(route, 'fixture-ordinary-csrf'); ++posts; return json(route, { error: { code: 'run_in_progress', message: 'private-run-owner' } }, 409); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.getByRole('heading', { name: '账号维护', exact: true })).toBeVisible();
  await page.getByLabel('删除业务数据：逐字输入 DELETE MY ACCOUNT', { exact: true }).fill('DELETE MY ACCOUNT'); await page.getByLabel('确认永久删除 AlliraAi 业务数据，理解这不会删除 Logto 全局账号或物理擦除历史备份', { exact: true }).check(); await page.getByRole('button', { name: '永久删除 AlliraAi 业务数据', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '主动重试' })).toBeVisible(); await expect(page.getByRole('button', { name: '永久删除 AlliraAi 业务数据', exact: true })).toBeEnabled(); expect(posts).toBe(1); await expect(page.locator('body')).not.toContainText('private-run-owner');
  await page.getByRole('button', { name: '能量用量', exact: true }).click(); await page.getByRole('button', { name: '账号', exact: true }).click(); await openDetails(page, '托管身份删除业务数据');
  await expect(page.getByLabel('删除业务数据：逐字输入 DELETE MY ACCOUNT', { exact: true })).toHaveValue(''); await expect(page.getByLabel('确认永久删除 AlliraAi 业务数据，理解这不会删除 Logto 全局账号或物理擦除历史备份', { exact: true })).not.toBeChecked(); await expect(page.getByRole('button', { name: '永久删除 AlliraAi 业务数据', exact: true })).toBeDisabled(); expect(posts).toBe(1);
});

test('memory-only hosted login cannot silently exchange a browser cookie just to start business deletion', async ({ page }) => {
  const posts: string[] = [];
  await setup(page, async (route, path) => {
    if (path === '/auth/oidc/session') return json(route, transition());
    if (route.request().method() === 'POST') posts.push(path);
    if (path === '/auth/oidc/complete') return json(route, accountSession);
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.getByRole('button', { name: '确认身份并进入聊天', exact: true })).toBeEnabled(); await consent(page); await page.getByRole('button', { name: '确认身份并进入聊天', exact: true }).click(); await accountPage(page); await openDetails(page, '托管身份删除业务数据');
  await expect(page.getByRole('button', { name: '前往 Logto 重新认证以删除业务数据', exact: true })).toBeDisabled(); await expect(page.locator('body')).toContainText('不会偷偷兑换'); expect(posts).toEqual(['/auth/oidc/complete']);
});

test('fresh erasure start reads an ordinary Cookie proof explicitly and sends both independent CSRF headers only for erase purpose', async ({ page }) => {
  let ordinaryReads = 0; let starts = 0;
  await setup(page, async (route, path) => {
    if (path === '/auth/browser/session') { ++ordinaryReads; expect(route.request().headers().authorization).toBeUndefined(); return json(route, { ...browserSession, csrfToken: ordinaryReads === 1 ? 'fixture-ordinary-csrf' : 'fresh-ordinary-csrf' }); }
    if (path === '/auth/oidc/session') return json(route, transition(null));
    if (path === '/auth/oidc/start') { ++starts; oidcWrite(route, 'fresh-ordinary-csrf'); expect(route.request().postDataJSON()).toEqual({ purpose: 'erase' }); return json(route, { authorizationUrl: 'https://evil.invalid/forbidden' }); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await accountPage(page); await openDetails(page, '托管身份删除业务数据'); expect(ordinaryReads).toBe(1); expect(starts).toBe(0);
  await page.getByRole('button', { name: '前往 Logto 重新认证以删除业务数据', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '已阻止跳转' })).toBeVisible(); expect(ordinaryReads).toBe(2); expect(starts).toBe(1);
  await expect(page.locator('body')).not.toContainText('fresh-ordinary-csrf');
});

test('an expired ordinary session blocks final erasure and clears private account state instead of sending an OIDC-only delete', async ({ page }) => {
  let ordinaryReads = 0; let deletes = 0;
  await setup(page, async (route, path) => {
    if (path === '/auth/browser/session') { ++ordinaryReads; return ordinaryReads === 1 ? json(route, browserSession) : json(route, { error: { code: 'unauthorized', message: 'private-cookie-proof' } }, 401); }
    if (path === '/auth/oidc/session') return json(route, transition('erase'));
    if (path === '/auth/oidc/erase') { ++deletes; return json(route, { ok: true }); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.getByRole('heading', { name: '账号维护', exact: true })).toBeVisible();
  await page.getByLabel('删除业务数据：逐字输入 DELETE MY ACCOUNT', { exact: true }).fill('DELETE MY ACCOUNT'); await page.getByLabel('确认永久删除 AlliraAi 业务数据，理解这不会删除 Logto 全局账号或物理擦除历史备份', { exact: true }).check();
  await page.getByRole('button', { name: '永久删除 AlliraAi 业务数据', exact: true }).click(); await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
  expect(ordinaryReads).toBe(2); expect(deletes).toBe(0); await expect(page.locator('body')).not.toContainText('private-cookie-proof'); await expect(page.getByLabel('删除业务数据：逐字输入 DELETE MY ACCOUNT', { exact: true })).toHaveCount(0);
});

test('a delayed ordinary CSRF read cannot start a destructive flow after the account feature has been left', async ({ page }) => {
  const delayed = gate(); let ordinaryReads = 0; let posts = 0;
  await setup(page, async (route, path) => {
    if (path === '/auth/browser/session') { ++ordinaryReads; if (ordinaryReads > 1) { delayed.received(); await delayed.waiting; } return json(route, browserSession).catch(() => {}); }
    if (path === '/auth/oidc/session') return json(route, transition(null));
    if (path.startsWith('/auth/oidc/') && route.request().method() === 'POST') ++posts;
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await accountPage(page); await openDetails(page, '托管身份删除业务数据'); await page.getByRole('button', { name: '前往 Logto 重新认证以删除业务数据', exact: true }).click();
  await delayed.ready; await page.getByRole('button', { name: '能量用量', exact: true }).click(); delayed.release(); await expect(page.getByRole('heading', { name: '能量与用量', exact: true })).toBeVisible();
  await page.getByRole('button', { name: '账号', exact: true }).click(); await openDetails(page, '托管身份删除业务数据'); await expect(page.getByLabel('删除业务数据：逐字输入 DELETE MY ACCOUNT', { exact: true })).toHaveValue(''); expect(posts).toBe(0);
});

test('a real public-shell worker may expose the final 200 while CDP still proves the original main-frame GET 303 callback and exact public target', async ({ browser }) => {
  const context = await browser.newContext({ serviceWorkers: 'allow' });
  let callbacks = 0, pendingReads = 0, external = 0, completePosts = 0;
  const publicFiles = new Map<string, { body: Buffer; contentType: string }>();
  const manifest = JSON.parse(await readFile(new URL('../.test-dist/test/manifest.webmanifest', import.meta.url), 'utf8'));
  for (const [path, file] of [['/test/', 'test/index.html'], ['/test/sw.js', 'test/sw.js'], ['/test/manifest.webmanifest', 'test/manifest.webmanifest']]) publicFiles.set(path, { body: await readFile(new URL(`../.test-dist/${file}`, import.meta.url)), contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.webmanifest') ? 'application/manifest+json' : 'text/html' });
  const html = publicFiles.get('/test/')!.body.toString('utf8');
  const assets = [...html.matchAll(/(?:src|href)="(\/console-assets\/[^"?#]+)"/g)].map(match => match[1]);
  for (const path of [...assets, ...manifest.icons.map((icon: { src: string }) => icon.src)]) publicFiles.set(path, { body: await readFile(new URL(`../.test-dist/assets${path.slice('/console-assets'.length)}`, import.meta.url)), contentType: path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : 'image/png' });
  const page = await context.newPage(), cdp = await context.newCDPSession(page);
  let redirected = false;
  try {
    // Context routes include worker-owned requests. No request is continued to a listener.
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) { ++external; await route.abort(); return; }
      if (url.pathname === '/api/v1/auth/oidc/callback') { ++callbacks; redirected = true; await route.fulfill({ status: 303, headers: { location: `${origin}/test/`, 'set-cookie': 'oidc-callback-fixture=1; Path=/api/v1/auth/oidc; HttpOnly; SameSite=Lax' }, body: '' }); return; }
      if (url.pathname === '/api/v1/auth/options') { await json(route, options()); return; }
      if (url.pathname === '/api/v1/auth/oidc/session') { ++pendingReads; if (redirected) expect(route.request().headers().cookie).toContain('oidc-callback-fixture=1'); await json(route, transition(redirected ? 'login' : null)); return; }
      if (url.pathname === '/api/v1/auth/oidc/complete' && route.request().method() === 'POST') ++completePosts;
      if (url.pathname.startsWith('/api/v1/')) { await defaults(route, url.pathname.slice('/api/v1'.length)); return; }
      const file = publicFiles.get(url.pathname); if (!file || url.search) { await route.abort(); return; }
      await route.fulfill({ body: file.body, contentType: file.contentType });
    });
    await page.goto(`${origin}/test/`);
    await page.evaluate(async () => { await navigator.serviceWorker.ready; if (!navigator.serviceWorker.controller) await new Promise<void>(resolve => navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true })); });
    const frameId = (await cdp.send('Page.getFrameTree')).frameTree.frame.id;
    const sent = new Map<string, { method: string; navigation: boolean; frameId?: string; type?: string }>();
    const raw = { count: 0, status: -1, method: 0, navigation: 0, mainFrame: 0, document: 0, toPublic: 0, fromSW: 0 };
    const final = { status: -1, fromSW: 0 }, visibleCallback = { status: -1, fromSW: 0 };
    page.on('response', response => { const url = new URL(response.url()); if (url.origin === origin && url.pathname === '/api/v1/auth/oidc/callback') { visibleCallback.status = response.status(); visibleCallback.fromSW = Number(response.fromServiceWorker()); } });
    cdp.on('Network.requestWillBeSent', event => {
      const url = new URL(event.request.url), prior = event.redirectResponse;
      if (prior) {
        const previous = new URL(prior.url);
        if (previous.origin === origin && previous.pathname === '/api/v1/auth/oidc/callback') {
          const request = sent.get(event.requestId); ++raw.count; raw.status = prior.status; raw.method = Number(request?.method === 'GET'); raw.navigation = Number(request?.navigation); raw.mainFrame = Number(request?.frameId === frameId); raw.document = Number(request?.type === 'Document'); raw.fromSW = Number(!!prior.fromServiceWorker);
          raw.toPublic = Number(url.origin === origin && url.pathname === '/test/' && !url.search && !url.hash && event.request.method === 'GET' && event.type === 'Document' && event.frameId === frameId);
        }
      }
      if (url.origin === origin && url.pathname === '/api/v1/auth/oidc/callback') sent.set(event.requestId, { method: event.request.method, navigation: event.requestId === event.loaderId && event.type === 'Document', frameId: event.frameId, type: event.type });
    });
    cdp.on('Network.responseReceived', event => { const url = new URL(event.response.url); if (url.origin === origin && url.pathname === '/test/' && !url.search && !url.hash) { final.status = event.response.status; final.fromSW = Number(!!event.response.fromServiceWorker); } });
    await cdp.send('Network.enable');
    await page.goto(`${origin}/api/v1/auth/oidc/callback?fixture=synthetic`); await page.waitForURL(`${origin}/test/`);
    await expect(page.getByRole('button', { name: '确认身份并进入聊天', exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeHidden();
    expect(raw).toEqual({ count: 1, status: 303, method: 1, navigation: 1, mainFrame: 1, document: 1, toPublic: 1, fromSW: 0 });
    expect(final).toEqual({ status: 200, fromSW: 1 }); expect(visibleCallback).toEqual({ status: 200, fromSW: 1 });
    expect(callbacks).toBe(1); expect(pendingReads).toBeGreaterThan(1); expect(external).toBe(0); expect(completePosts).toBe(0);
    expect((await context.cookies(`${origin}/api/v1/auth/oidc/session`)).some(cookie => cookie.name === 'oidc-callback-fixture' && cookie.httpOnly)).toBe(true);
  } finally { await cdp.detach(); await context.close(); publicFiles.clear(); }
});
