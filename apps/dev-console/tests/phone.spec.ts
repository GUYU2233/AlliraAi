import { test, expect } from '@playwright/test';
import type { Page, Route } from '@playwright/test';
import { account, accountSession, defaults, fixture, gate, json, openAdvanced, openDetails, openLegacy, origin } from './browser-fixture.ts';

const options = (phone = true, social = false) => ({ phone: { enabled: phone, simulated: phone, reason: phone ? null : 'sms_unavailable', countryCallingCodes: ['+86'] }, social: ['wechat', 'qq'].map(provider => ({ provider, enabled: social, simulated: social, reason: social ? null : 'social_unavailable' })), legacyPasswordEnabled: true, registrationEnabled: true });
const identity = (passwordEnabled = false) => ({ primaryIdentifier: 'phone', phone: '+8613800138000', maskedPhone: '+86 138****8000', phoneVerified: true, requiresPhoneBinding: false, passwordEnabled, social: [] });
const challenge = (id = 'proof') => ({ challengeId: id, maskedPhone: '+86 138****8000', expiresAt: new Date(Date.now() + 300_000).toISOString(), resendAfterSeconds: 60 });
const socialSession = (pending: null | { provider: string; displayName: string; requiresPhoneBinding: boolean } = null) => ({ csrfToken: 'social-only-csrf', expiresAt: new Date(Date.now() + 300_000).toISOString(), pending: pending && { ...pending, expiresAt: new Date(Date.now() + 300_000).toISOString() } });
async function phoneFixture(page: Page, api: (route: Route, path: string) => Promise<unknown> = async (route, path) => defaults(route, path)): Promise<void> {
  await fixture(page, async (route, path) => {
    if (path === '/auth/options') return json(route, options());
    return api(route, path);
  });
}
async function requestCode(page: Page, register = false): Promise<void> {
  await page.goto(`${origin}/test/`);
  if (register) await page.getByLabel('手机号操作', { exact: true }).selectOption('register');
  await page.getByLabel('登录手机号', { exact: true }).fill('13800138000');
  await page.getByRole('button', { name: '发送手机验证码', exact: true }).click();
  await expect(page.getByRole('button', { name: register ? '验证手机号并注册' : '验证码登录', exact: true })).toBeEnabled();
}
async function legacyLoginHere(page: Page): Promise<void> {
  await openLegacy(page);
  await page.getByLabel('账号用户名', { exact: true }).fill('account_1');
  await page.getByLabel('账号密码（10–128 字符，最多 256 UTF-8 字节）', { exact: true }).fill('1234567890');
  await page.getByRole('button', { name: '账号登录', exact: true }).click();
  await expect(page.getByRole('button', { name: '聊天', exact: true })).toBeVisible();
}

// Every request is intercepted at an unused virtual loopback port; no SMS/OAuth/API is contacted.
test('phone is primary; disabled SMS/social stay honest while optional phone password and legacy login remain available without legacy registration', async ({ page }) => {
  const posts: string[] = [];
  await fixture(page, async (route, path) => {
    if (path === '/auth/options') return json(route, options(false));
    if (route.request().method() === 'POST') posts.push(path);
    if (path === '/auth/phone/password') { expect(route.request().headers()['authorization']).toBeUndefined(); expect(route.request().postDataJSON()).toEqual({ phone: '+8613800138000', password: '1234567890' }); return json(route, accountSession); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`);
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '账号登录', exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: '发送手机验证码', exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: '微信登录', exact: true })).toBeHidden();
  await expect(page.getByRole('button', { name: 'QQ 登录', exact: true })).toBeHidden();
  await expect(page.locator('body')).toContainText('短信通道未开通');
  await openLegacy(page); await expect(page.getByRole('button', { name: '账号登录', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: '注册账号', exact: true })).toBeHidden();
  await page.getByLabel('登录手机号', { exact: true }).fill('13800138000'); await openDetails(page, '手机号密码登录');
  await page.getByLabel('手机号登录密码', { exact: true }).fill('1234567890'); await page.getByRole('button', { name: '使用手机号和密码登录', exact: true }).click();
  await expect(page.locator('.identity')).toContainText('账号甲'); expect(posts).toEqual(['/auth/phone/password']);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('phone registration requires explicit adult/current terms, permits no password, normalizes phone and clears all secrets', async ({ page }) => {
  const writes: { path: string; body: Record<string, unknown> }[] = [];
  await phoneFixture(page, async (route, path) => {
    if (path.startsWith('/auth/phone/')) { expect(route.request().headers()['authorization']).toBeUndefined(); expect(route.request().headers()['x-csrf-token']).toBeUndefined(); writes.push({ path, body: route.request().postDataJSON() }); }
    if (path === '/auth/phone/challenges') return json(route, challenge(), 202);
    if (path === '/auth/phone/register') return json(route, accountSession, 201);
    return defaults(route, path);
  });
  await requestCode(page, true);
  await expect(page.getByLabel('可选站内密码', { exact: true })).toBeHidden();
  await page.getByLabel('六位手机验证码', { exact: true }).fill('001234');
  await expect(page.getByLabel('六位手机验证码', { exact: true })).toHaveAttribute('type', 'password');
  await page.getByRole('button', { name: '验证手机号并注册', exact: true }).click(); expect(writes).toHaveLength(1);
  await page.getByLabel('手机注册或关联：我已满 18 岁', { exact: true }).check();
  await page.getByLabel('手机注册或关联：接受当前服务协议', { exact: true }).check();
  await page.getByRole('button', { name: '验证手机号并注册', exact: true }).click();
  await expect(page.getByRole('button', { name: '新聊天', exact: true })).toBeVisible();
  expect(writes).toEqual([{ path: '/auth/phone/challenges', body: { phone: '+8613800138000', purpose: 'register' } }, { path: '/auth/phone/register', body: { challengeId: 'proof', code: '001234', acceptedTermsVersion: '2026-11-19', isAdult: true } }]);
  for (const name of ['authPhone', 'authPhoneCode', 'optionalPhonePassword', 'phoneLoginPassword']) expect(await page.locator(`[name=${name}]`).inputValue()).toBe('');
  await expect(page.locator('body')).not.toContainText('001234');
  await page.setViewportSize({ width: 375, height: 800 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('challenge follows exact phone/purpose, enforces local cooldown/expiry and never submits an old challenge', async ({ page }) => {
  await page.clock.install(); let sent = 0; let finishes = 0;
  await phoneFixture(page, async (route, path) => {
    if (path === '/auth/phone/challenges') { ++sent; return json(route, challenge(`proof-${sent}`)); }
    if (path === '/auth/phone/login' || path === '/auth/phone/register') { ++finishes; return json(route, accountSession); }
    return defaults(route, path);
  });
  await requestCode(page); await expect(page.getByRole('button', { name: /发送手机验证码/ })).toBeDisabled();
  await page.getByLabel('登录手机号', { exact: true }).fill('13900139000'); await expect(page.getByRole('button', { name: '验证码登录', exact: true })).toBeDisabled();
  await page.getByLabel('手机号操作', { exact: true }).selectOption('register'); await expect(page.getByRole('button', { name: '验证手机号并注册', exact: true })).toBeDisabled();
  await page.clock.runFor(61_000); await page.getByRole('button', { name: '发送手机验证码', exact: true }).click();
  await expect(page.getByRole('button', { name: '验证手机号并注册', exact: true })).toBeEnabled();
  await page.clock.runFor(301_000); await expect(page.getByRole('button', { name: '验证手机号并注册', exact: true })).toBeDisabled(); expect(sent).toBe(2); expect(finishes).toBe(0);
});

test('uncertain and rate-limited SMS errors never auto retry or expose raw credentials; configuration failure does not enable legacy registration', async ({ page }) => {
  let posts = 0; let configFailed = false;
  await fixture(page, async (route, path) => {
    if (path === '/auth/options') return configFailed ? route.abort('connectionfailed') : json(route, options());
    if (path === '/auth/phone/challenges') { ++posts; return posts === 1 ? route.abort('connectionfailed') : json(route, { error: { code: 'rate_limited', message: 'raw-private-phone' } }, 429); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await page.getByLabel('登录手机号', { exact: true }).fill('13800138000'); await page.getByRole('button', { name: '发送手机验证码', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '不会自动重试' })).toBeVisible(); expect(posts).toBe(1);
  await page.getByRole('button', { name: '重新检查登录方式', exact: true }).click(); await page.getByLabel('登录手机号', { exact: true }).fill('13800138000'); await page.getByRole('button', { name: '发送手机验证码', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '操作过于频繁' })).toBeVisible(); expect(posts).toBe(2); await expect(page.locator('body')).not.toContainText('raw-private-phone');
  configFailed = true; await page.getByRole('button', { name: '重新检查登录方式', exact: true }).click(); await openLegacy(page);
  await expect(page.getByRole('button', { name: '账号登录', exact: true })).toBeDisabled(); await expect(page.getByRole('button', { name: '注册账号', exact: true })).toBeHidden();
});

test('phone login uses the existing explicit Cookie exchange once and protected writes use account CSRF only', async ({ page }) => {
  let exchanged = false; const writes: string[] = [];
  await phoneFixture(page, async (route, path) => {
    if (path === '/auth/phone/challenges') return json(route, challenge());
    if (path === '/auth/phone/login') { expect(route.request().headers()['authorization']).toBeUndefined(); writes.push(path); return json(route, accountSession); }
    if (path === '/auth/browser/session') { if (route.request().method() === 'POST') { writes.push(path); exchanged = true; expect(route.request().headers()['authorization']).toBe(`Bearer ${accountSession.accessToken}`); } return exchanged ? json(route, { user: account, csrfToken: 'account-only-csrf', expiresAt: challenge().expiresAt }) : defaults(route, path); }
    if (path === '/account/identity') return json(route, identity());
    if (path === '/account/update') { writes.push(path); expect(route.request().headers()['authorization']).toBeUndefined(); expect(route.request().headers()['x-csrf-token']).toBe('account-only-csrf'); return json(route, { ...account, displayName: '手机设备' }); }
    return defaults(route, path);
  });
  await requestCode(page); await page.getByLabel('六位手机验证码', { exact: true }).fill('001234'); await page.getByLabel('手机登录：记住此设备（共享设备请勿勾选）', { exact: true }).check(); await page.getByRole('button', { name: '验证码登录', exact: true }).click();
  await expect(page.locator('.identity')).toContainText('已记住此设备'); await openAdvanced(page); await page.getByRole('button', { name: '账号', exact: true }).click();
  await expect(page.getByLabel('显示名称', { exact: true })).toHaveValue(account.displayName); await page.getByLabel('显示名称', { exact: true }).fill('手机设备'); await page.getByRole('button', { name: '保存显示名称', exact: true }).click();
  await expect(page.locator('.identity')).toContainText('手机设备'); expect(writes).toEqual(['/auth/phone/login', '/auth/browser/session', '/account/update']);
});

test('late phone challenge and late login cannot overwrite a newer legacy account or repopulate private input', async ({ page }) => {
  const delayed = gate(); let post = 0;
  await phoneFixture(page, async (route, path) => {
    if (path === '/auth/phone/challenges') return json(route, challenge());
    if (path === '/auth/phone/login') { ++post; delayed.received(); await delayed.waiting; return json(route, { ...accountSession, user: { ...account, displayName: '迟到手机号账号' } }).catch(() => {}); }
    return defaults(route, path);
  });
  await requestCode(page); await page.getByLabel('六位手机验证码', { exact: true }).fill('001234'); await page.getByRole('button', { name: '验证码登录', exact: true }).click(); await delayed.ready;
  await legacyLoginHere(page); delayed.release(); await expect(page.locator('.identity')).toContainText('账号甲'); await expect(page.locator('body')).not.toContainText('迟到手机号账号'); expect(post).toBe(1);
  expect(await page.locator('[name=authPhoneCode]').inputValue()).toBe('');
});

test('social pending is not account auth; first binding needs phone proof, adult, terms and explicit association with independent CSRF', async ({ page }) => {
  const writes: string[] = []; let pending = true;
  await fixture(page, async (route, path) => {
    if (path === '/auth/options') return json(route, options(true, true));
    if (path === '/auth/social/session') return json(route, socialSession(pending ? { provider: 'wechat', displayName: '<img src=x onerror=alert(1)>', requiresPhoneBinding: true } : null));
    if (path.startsWith('/auth/social/') && route.request().method() === 'POST') { writes.push(path); expect(route.request().headers()['authorization']).toBeUndefined(); expect(route.request().headers()['x-csrf-token']).toBe('social-only-csrf'); }
    if (path === '/auth/social/phone/challenges') return json(route, challenge('social-proof'));
    if (path === '/auth/social/bind-phone') { expect(route.request().postDataJSON()).toEqual({ challengeId: 'social-proof', code: '001234', acceptedTermsVersion: '2026-11-19', isAdult: true, confirmBind: true }); pending = false; return json(route, accountSession); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.locator('body')).toContainText('尚未登录，必须验证手机号并明确关联'); await expect(page.getByRole('button', { name: '新聊天', exact: true })).toBeHidden(); expect(writes).toEqual([]); await expect(page.locator('img')).toHaveCount(0);
  await page.getByLabel('登录手机号', { exact: true }).fill('13800138000'); await page.getByRole('button', { name: '发送手机验证码', exact: true }).click(); await page.getByLabel('六位手机验证码', { exact: true }).fill('001234');
  await page.getByLabel('手机注册或关联：我已满 18 岁', { exact: true }).check(); await page.getByLabel('手机注册或关联：接受当前服务协议', { exact: true }).check();
  await page.getByRole('button', { name: '验证手机号并关联社交身份', exact: true }).click(); expect(writes).toEqual(['/auth/social/phone/challenges']);
  await page.getByLabel('确认验证手机号并关联此微信或 QQ 身份；手机号已有账号时关联至该账号，不合并其他账号数据', { exact: true }).check(); await page.getByRole('button', { name: '验证手机号并关联社交身份', exact: true }).click();
  await expect(page.getByRole('button', { name: '新聊天', exact: true })).toBeVisible(); expect(writes).toEqual(['/auth/social/phone/challenges', '/auth/social/bind-phone']); expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('linked social callback requires explicit completion, cancellation is explicit, and an unsafe provider URL never navigates', async ({ page }) => {
  let pending = true; const writes: string[] = [];
  await fixture(page, async (route, path) => {
    if (path === '/auth/options') return json(route, options(true, true));
    if (path === '/auth/social/session') return json(route, socialSession(pending ? { provider: 'qq', displayName: '已关联 QQ', requiresPhoneBinding: false } : null));
    if (path.startsWith('/auth/social/') && route.request().method() === 'POST') { writes.push(path); expect(route.request().headers()['x-csrf-token']).toBe('social-only-csrf'); expect(route.request().headers()['authorization']).toBeUndefined(); }
    if (path === '/auth/social/cancel') { pending = false; return json(route, { ok: true }); }
    if (path === '/auth/social/qq/start') return json(route, { authorizeUrl: 'https://evil.test/steal?token=private', expiresAt: challenge().expiresAt });
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.getByRole('button', { name: '继续社交账号登录', exact: true })).toBeVisible(); expect(writes).toEqual([]);
  await page.getByRole('button', { name: '取消本次社交授权', exact: true }).click(); await expect(page.getByRole('button', { name: '继续社交账号登录', exact: true })).toBeHidden();
  await page.getByRole('button', { name: 'QQ 登录', exact: true }).click(); await expect(page.getByRole('status').filter({ hasText: '授权地址不符合' })).toBeVisible(); expect(page.url()).toBe(`${origin}/test/`); expect(writes).toEqual(['/auth/social/cancel', '/auth/social/qq/start']);
  await expect(page.locator('body')).not.toContainText('private');
});

test('social linked completion is one explicit POST and never reads a hash as an account token', async ({ page }) => {
  let complete = 0;
  await fixture(page, async (route, path) => {
    if (path === '/auth/options') return json(route, options(true, true));
    if (path === '/auth/social/session') return json(route, socialSession({ provider: 'qq', displayName: 'QQ 账号', requiresPhoneBinding: false }));
    if (path === '/auth/social/complete') { ++complete; expect(route.request().headers()['x-csrf-token']).toBe('social-only-csrf'); return json(route, accountSession); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/#access_token=never-read`); await expect(page.getByRole('button', { name: '继续社交账号登录', exact: true })).toBeVisible(); expect(complete).toBe(0);
  await expect(page.getByRole('button', { name: '新聊天', exact: true })).toBeHidden(); await page.getByRole('button', { name: '继续社交账号登录', exact: true }).click(); await expect(page.locator('.identity')).toContainText('账号甲'); expect(complete).toBe(1);
});
