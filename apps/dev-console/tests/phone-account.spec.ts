import { test, expect } from '@playwright/test';
import type { Page, Route } from '@playwright/test';
import { account, accountSession, c1, defaults, fixture, gate, json, loginAccount, openAdvanced, openDetails, openLegacy, origin } from './browser-fixture.ts';

const options = (enabled = true) => ({ phone: { enabled, simulated: enabled, reason: enabled ? null : 'sms_unavailable', countryCallingCodes: ['+86'] }, social: [], legacyPasswordEnabled: true, registrationEnabled: enabled });
const legacy = { primaryIdentifier: 'legacy_username', phone: null, maskedPhone: null, phoneVerified: false, requiresPhoneBinding: true, passwordEnabled: true, social: [] };
const bound = (passwordEnabled = true) => ({ ...legacy, primaryIdentifier: 'phone', phone: '+8613800138000', maskedPhone: '+86 138****8000', phoneVerified: true, requiresPhoneBinding: false, passwordEnabled });
const proof = (id = 'account-proof') => ({ challengeId: id, maskedPhone: '+86 138****8000', expiresAt: new Date(Date.now() + 300_000).toISOString(), resendAfterSeconds: 60 });
const phonePaths = { bindChallenge: '/account/phone/challenges', bind: '/account/phone/bind', securityChallenge: '/account/phone/security/challenges', setPassword: '/account/phone/password', erase: '/account/phone/erase' };
async function setup(page: Page, api: (route: Route, path: string) => Promise<unknown>): Promise<void> { await fixture(page, async (route, path) => path === '/auth/options' ? json(route, options()) : api(route, path)); }
async function openAccount(page: Page): Promise<void> { await loginAccount(page); await page.getByRole('button', { name: '账号', exact: true }).click(); await expect(page.getByLabel('显示名称', { exact: true })).toHaveValue(account.displayName); }
async function security(page: Page): Promise<void> { await openDetails(page, '手机验证敏感操作'); }
async function sendSecurity(page: Page): Promise<void> { await page.getByRole('button', { name: '发送账号安全验证码', exact: true }).click(); await expect(page.getByLabel('账号安全六位验证码', { exact: true })).toBeEnabled(); await expect(page.getByRole('button', { name: /通过手机/ })).toBeEnabled(); }

// No listener, SMS provider or ordinary API is contacted. All API requests are intercepted.
test('legacy account binding requires current password twice and explicit consent, retains immutable owner and existing conversations', async ({ page }) => {
  let identity = legacy; const writes: { path: string; body: unknown }[] = []; const ownerHeaders: string[] = [];
  await setup(page, async (route, path) => {
    if (path === '/account/identity') return json(route, identity);
    if (path === phonePaths.bindChallenge || path === phonePaths.bind) { writes.push({ path, body: route.request().postDataJSON() }); ownerHeaders.push(route.request().headers()['authorization']); }
    if (path === phonePaths.bindChallenge) return json(route, proof(), 202);
    if (path === phonePaths.bind) { identity = bound(); return json(route, identity); }
    return defaults(route, path);
  });
  await openAccount(page); await page.getByLabel('绑定手机号', { exact: true }).fill('13800138000');
  await page.getByRole('button', { name: '发送绑定手机验证码', exact: true }).click(); expect(writes).toEqual([]);
  await page.getByLabel('绑定手机的当前密码', { exact: true }).fill('1234567890'); await page.getByRole('button', { name: '发送绑定手机验证码', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认绑定此手机号', exact: true })).toBeEnabled(); await expect(page.getByLabel('绑定手机的当前密码', { exact: true })).toHaveValue('');
  await page.getByLabel('绑定手机的当前密码', { exact: true }).fill('1234567890'); await page.getByLabel('绑定手机的六位验证码', { exact: true }).fill('001234');
  await page.getByRole('button', { name: '确认绑定此手机号', exact: true }).click(); expect(writes).toHaveLength(1);
  await page.getByLabel('确认将此手机号绑定到当前账号，保留当前账号数据且不合并其他账号', { exact: true }).check(); await page.getByRole('button', { name: '确认绑定此手机号', exact: true }).click();
  await expect(page.getByLabel('绑定手机号', { exact: true })).toBeHidden(); await expect(page.locator('body')).toContainText('手机号已绑定，原账号与聊天保持不变。'); await expect(page.locator('.identity')).toContainText(account.displayName);
  expect(writes).toEqual([{ path: phonePaths.bindChallenge, body: { phone: '+8613800138000', currentPassword: '1234567890' } }, { path: phonePaths.bind, body: { challengeId: 'account-proof', code: '001234', currentPassword: '1234567890' } }]);
  expect(ownerHeaders).toEqual([`Bearer ${accountSession.accessToken}`, `Bearer ${accountSession.accessToken}`]);
  await page.getByRole('button', { name: '聊天', exact: true }).click(); await page.getByRole('button', { name: c1.title, exact: true }).click(); await expect(page.getByRole('heading', { name: c1.title, exact: true })).toBeVisible();
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('binding conflict is generic and never switches owner; changing the phone invalidates proof and consent', async ({ page }) => {
  let sent = 0; let boundPosts = 0;
  await setup(page, async (route, path) => {
    if (path === '/account/identity') return json(route, legacy);
    if (path === phonePaths.bindChallenge) { ++sent; return json(route, proof()); }
    if (path === phonePaths.bind) { ++boundPosts; return json(route, { error: { code: 'phone_in_use', message: 'private-other-account-id' } }, 409); }
    return defaults(route, path);
  });
  await openAccount(page); await page.getByLabel('绑定手机号', { exact: true }).fill('13800138000'); await page.getByLabel('绑定手机的当前密码', { exact: true }).fill('1234567890'); await page.getByRole('button', { name: '发送绑定手机验证码', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认绑定此手机号', exact: true })).toBeEnabled(); await page.getByLabel('绑定手机的当前密码', { exact: true }).fill('1234567890'); await page.getByLabel('绑定手机的六位验证码', { exact: true }).fill('001234'); await page.getByLabel('确认将此手机号绑定到当前账号，保留当前账号数据且不合并其他账号', { exact: true }).check(); await page.getByRole('button', { name: '确认绑定此手机号', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '不会合并账号' })).toBeVisible(); await expect(page.locator('body')).not.toContainText('private-other-account-id'); await expect(page.locator('.identity')).toContainText(account.displayName);
  await page.getByLabel('绑定手机号', { exact: true }).fill('13900139000'); await expect(page.getByRole('button', { name: '确认绑定此手机号', exact: true })).toBeDisabled(); await expect(page.getByLabel('绑定手机的六位验证码', { exact: true })).toHaveValue(''); await expect(page.getByLabel('确认将此手机号绑定到当前账号，保留当前账号数据且不合并其他账号', { exact: true })).not.toBeChecked(); expect(sent).toBe(1); expect(boundPosts).toBe(1);
});

test('passwordless account exposes no current-password recovery or erase, purpose-bound OTP sets a password only after confirmation and clears auth', async ({ page }) => {
  const writes: { path: string; body: unknown }[] = [];
  await setup(page, async (route, path) => {
    if (path === '/account/identity') return json(route, bound(false));
    if (path === phonePaths.securityChallenge || path === phonePaths.setPassword) writes.push({ path, body: route.request().postDataJSON() });
    if (path === phonePaths.securityChallenge) return json(route, proof('password-proof'));
    if (path === phonePaths.setPassword) return json(route, { ok: true, revoked: 2 });
    return defaults(route, path);
  });
  await openAccount(page); await expect(page.getByLabel('当前密码', { exact: true })).toBeHidden(); await expect(page.getByLabel('签发恢复码的当前密码', { exact: true })).toBeHidden(); await expect(page.getByLabel('删除账号的当前密码', { exact: true })).toBeHidden(); await expect(page.getByRole('button', { name: '读取登录设备', exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: '下载账号完整数据', exact: true })).toBeVisible();
  await security(page); await sendSecurity(page); await page.getByLabel('账号安全六位验证码', { exact: true }).fill('001234'); await page.getByLabel('手机验证后的新密码', { exact: true }).fill('1234567890'); await page.getByRole('button', { name: '通过手机设置密码并退出全部登录', exact: true }).click(); expect(writes).toHaveLength(1);
  await page.getByLabel('确认设置密码并撤销此账号全部登录', { exact: true }).check(); await page.getByRole('button', { name: '通过手机设置密码并退出全部登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible(); await expect(page.locator('body')).toContainText('站内密码已设置，全部登录已撤销');
  expect(writes).toEqual([{ path: phonePaths.securityChallenge, body: { purpose: 'set_password' } }, { path: phonePaths.setPassword, body: { challengeId: 'password-proof', code: '001234', newPassword: '1234567890' } }]); await expect(page.locator('[name=securityPhonePassword]')).toHaveCount(0); expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('passwordless erase requires its own OTP, exact deletion phrase and checkbox, with account Cookie CSRF and no ordinary Bearer', async ({ page }) => {
  let browser = false; const writes: { path: string; body: unknown }[] = [];
  await setup(page, async (route, path) => {
    if (path === '/auth/browser/session') return json(route, { user: account, csrfToken: 'account-security-csrf', expiresAt: proof().expiresAt });
    if (path === '/account/identity') return json(route, bound(false));
    if (path === phonePaths.securityChallenge || path === phonePaths.erase) { writes.push({ path, body: route.request().postDataJSON() }); expect(route.request().headers()['authorization']).toBeUndefined(); expect(route.request().headers()['x-csrf-token']).toBe('account-security-csrf'); expect(route.request().headers()['origin']).toBe(origin); }
    if (path === phonePaths.securityChallenge) return json(route, proof('erase-proof'));
    if (path === phonePaths.erase) return json(route, { ok: true });
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.locator('.identity')).toContainText('已记住此设备'); browser = true; await openAdvanced(page); await page.getByRole('button', { name: '账号', exact: true }).click(); await expect(page.getByLabel('显示名称', { exact: true })).toHaveValue(account.displayName);
  await security(page); await page.getByLabel('手机安全验证用途', { exact: true }).selectOption('erase'); await sendSecurity(page); await page.getByLabel('账号安全六位验证码', { exact: true }).fill('001234');
  await page.getByRole('button', { name: '通过手机永久删除账号和在线数据', exact: true }).click(); expect(writes).toHaveLength(1); await page.getByLabel('手机删除：逐字输入 DELETE MY ACCOUNT', { exact: true }).fill('DELETE MY ACCOUNT'); await page.getByRole('button', { name: '通过手机永久删除账号和在线数据', exact: true }).click(); expect(writes).toHaveLength(1);
  await page.getByLabel('手机删除：确认永久删除账号和在线数据且无法撤销', { exact: true }).check(); await page.getByRole('button', { name: '通过手机永久删除账号和在线数据', exact: true }).click(); await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible(); expect(browser).toBe(true);
  expect(writes).toEqual([{ path: phonePaths.securityChallenge, body: { purpose: 'erase' } }, { path: phonePaths.erase, body: { challengeId: 'erase-proof', code: '001234', confirmation: 'DELETE MY ACCOUNT' } }]); await expect(page.locator('body')).toContainText('不等同物理擦除');
});

test('security purpose change rejects old proof, uncertain writes do not repeat, and leaving the account clears all sensitive input', async ({ page }) => {
  await page.clock.install(); let sends = 0; let commits = 0;
  await setup(page, async (route, path) => {
    if (path === '/account/identity') return json(route, bound(false));
    if (path === phonePaths.securityChallenge) { ++sends; return json(route, proof(`proof-${sends}`)); }
    if (path === phonePaths.erase) { ++commits; return route.abort('connectionfailed'); }
    return defaults(route, path);
  });
  await openAccount(page); await security(page); await sendSecurity(page); await page.getByLabel('账号安全六位验证码', { exact: true }).fill('001234'); await page.getByLabel('手机验证后的新密码', { exact: true }).fill('1234567890'); await page.getByLabel('确认设置密码并撤销此账号全部登录', { exact: true }).check();
  await page.getByLabel('手机安全验证用途', { exact: true }).selectOption('erase'); await expect(page.getByRole('button', { name: '通过手机永久删除账号和在线数据', exact: true })).toBeDisabled(); await expect(page.getByLabel('账号安全六位验证码', { exact: true })).toHaveValue(''); await expect(page.getByLabel('手机验证后的新密码', { exact: true })).toHaveValue('');
  await page.clock.runFor(61_000); await sendSecurity(page); await page.getByLabel('账号安全六位验证码', { exact: true }).fill('001234'); await page.getByLabel('手机删除：逐字输入 DELETE MY ACCOUNT', { exact: true }).fill('DELETE MY ACCOUNT'); await page.getByLabel('手机删除：确认永久删除账号和在线数据且无法撤销', { exact: true }).check(); await page.getByRole('button', { name: '通过手机永久删除账号和在线数据', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '不会自动重试' })).toBeVisible(); await expect(page.getByRole('button', { name: '通过手机永久删除账号和在线数据', exact: true })).toBeDisabled(); await page.clock.runFor(60_000); expect(commits).toBe(1); expect(sends).toBe(2);
  await page.getByRole('button', { name: '能量用量', exact: true }).click(); await expect(page.getByLabel('账号安全六位验证码', { exact: true })).toHaveCount(0); await expect(page.locator('body')).not.toContainText('001234'); expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('late binding and security proof responses cannot populate a later feature, and protected invalid-phone-code 401 clears the account', async ({ page }) => {
  const delayed = gate(); let identity = legacy;
  await setup(page, async (route, path) => {
    if (path === '/account/identity') return json(route, identity);
    if (path === phonePaths.bindChallenge) { delayed.received(); await delayed.waiting; return json(route, proof()).catch(() => {}); }
    if (path === phonePaths.securityChallenge) return json(route, proof());
    if (path === phonePaths.setPassword) return json(route, { error: { code: 'invalid_phone_code', message: 'private-raw-security-proof' } }, 401);
    return defaults(route, path);
  });
  await openAccount(page); await page.getByLabel('绑定手机号', { exact: true }).fill('13800138000'); await page.getByLabel('绑定手机的当前密码', { exact: true }).fill('1234567890'); await page.getByRole('button', { name: '发送绑定手机验证码', exact: true }).click(); await delayed.ready; await page.getByRole('button', { name: '能量用量', exact: true }).click(); delayed.release(); await expect(page.getByRole('heading', { name: '能量与用量', exact: true })).toBeVisible(); await expect(page.getByLabel('绑定手机的六位验证码', { exact: true })).toHaveCount(0);
  identity = bound(false); await page.getByRole('button', { name: '账号', exact: true }).click(); await security(page); await sendSecurity(page); await page.getByLabel('账号安全六位验证码', { exact: true }).fill('001234'); await page.getByLabel('手机验证后的新密码', { exact: true }).fill('1234567890'); await page.getByLabel('确认设置密码并撤销此账号全部登录', { exact: true }).check(); await page.getByRole('button', { name: '通过手机设置密码并退出全部登录', exact: true }).click(); await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible(); await expect(page.locator('body')).not.toContainText('private-raw-security-proof');
});

test('disabled SMS never requests a bind or sensitive OTP while keeping existing account/device/export access intact', async ({ page }) => {
  const posts: string[] = [];
  await fixture(page, async (route, path) => {
    if (path === '/auth/options') return json(route, options(false));
    if (path === '/account/identity') return json(route, legacy);
    if (path.startsWith('/account/phone/') && route.request().method() === 'POST') posts.push(path);
    return defaults(route, path);
  });
  await openAccount(page); await expect(page.getByRole('button', { name: '发送绑定手机验证码', exact: true })).toBeDisabled(); await expect(page.locator('body')).toContainText('短信通道未开通，暂时无法绑定手机号'); await expect(page.getByRole('button', { name: '读取登录设备', exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: '下载账号完整数据', exact: true })).toBeVisible(); expect(posts).toEqual([]);
});
