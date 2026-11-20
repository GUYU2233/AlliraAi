import { test, expect } from '@playwright/test';
import { account, accountSession, defaults, fixture, gate, json, openAdvanced, openDetails, openLegacy, origin } from './browser-fixture.ts';

const options = { phone: { enabled: true, simulated: true, reason: null, countryCallingCodes: ['+86'] }, social: [{ provider: 'wechat', enabled: true, simulated: true, reason: null }, { provider: 'qq', enabled: true, simulated: true, reason: null }], legacyPasswordEnabled: true, registrationEnabled: true };
const proof = (id: string) => ({ challengeId: id, maskedPhone: '+86 138****8000', expiresAt: new Date(Date.now() + 300_000).toISOString(), resendAfterSeconds: 60 });

// Route-only race coverage at the unused virtual origin. No API/SMS/OAuth process is contacted.
test('a delayed social pending GET cannot replace a newer legacy account or resurrect a consumed transition', async ({ page }) => {
  const delayed = gate(); let writes = 0;
  await fixture(page, async (route, path) => {
    if (path === '/auth/options') return json(route, options);
    if (path === '/auth/social/session') { delayed.received(); await delayed.waiting; return json(route, { csrfToken: 'late-social-csrf', expiresAt: proof('never').expiresAt, pending: { provider: 'wechat', displayName: '不应重现的社交身份', requiresPhoneBinding: true, expiresAt: proof('never').expiresAt } }).catch(() => {}); }
    if (path.startsWith('/auth/social/') && route.request().method() === 'POST') ++writes;
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await delayed.ready;
  await openLegacy(page); await page.getByLabel('账号用户名', { exact: true }).fill('account_1'); await page.getByLabel('账号密码（10–128 字符，最多 256 UTF-8 字节）', { exact: true }).fill('1234567890'); await page.getByRole('button', { name: '账号登录', exact: true }).click();
  await expect(page.locator('.identity')).toContainText(account.displayName); delayed.release();
  await openAdvanced(page); await page.getByRole('button', { name: '能量用量', exact: true }).click(); await expect(page.getByRole('heading', { name: '能量与用量', exact: true })).toBeVisible();
  await expect(page.locator('body')).not.toContainText('不应重现的社交身份'); expect(writes).toBe(0);
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible(); await expect(page.getByRole('button', { name: '验证手机号并关联社交身份', exact: true })).toBeHidden();
  await expect(page.locator('body')).not.toContainText('不应重现的社交身份'); expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('a delayed phone challenge is discarded after changing number or purpose and cannot enable submit', async ({ page }) => {
  const delayed = gate(); let sends = 0; let finishes = 0;
  await fixture(page, async (route, path) => {
    if (path === '/auth/options') return json(route, { ...options, social: [] });
    if (path === '/auth/phone/challenges') { ++sends; delayed.received(); await delayed.waiting; return json(route, proof('stale-proof')).catch(() => {}); }
    if (path === '/auth/phone/login' || path === '/auth/phone/register') { ++finishes; return json(route, accountSession); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await page.getByLabel('登录手机号', { exact: true }).fill('13800138000'); await page.getByRole('button', { name: '发送手机验证码', exact: true }).click(); await delayed.ready;
  // Busy native inputs cannot be edited by the user. Switching to another auth scope aborts the request first.
  await openLegacy(page); await page.getByLabel('登录手机号', { exact: true }).fill('13900139000'); await page.getByLabel('手机号操作', { exact: true }).selectOption('register'); delayed.release();
  await expect(page.getByRole('button', { name: '验证手机号并注册', exact: true })).toBeDisabled(); await expect(page.getByLabel('六位手机验证码', { exact: true })).toHaveValue('');
  await page.getByLabel('手机注册或关联：我已满 18 岁', { exact: true }).check(); await page.getByLabel('手机注册或关联：接受当前服务协议', { exact: true }).check();
  expect(sends).toBe(1); expect(finishes).toBe(0); await expect(page.locator('body')).not.toContainText('stale-proof');
});

test('optional phone password login remains available in register view without requiring adult consent or sending an OTP', async ({ page }) => {
  const writes: string[] = [];
  await fixture(page, async (route, path) => {
    if (path === '/auth/options') return json(route, { ...options, social: [] });
    if (path.startsWith('/auth/phone/') && route.request().method() === 'POST') { writes.push(path); expect(route.request().headers()['authorization']).toBeUndefined(); }
    if (path === '/auth/phone/password') { expect(route.request().postDataJSON()).toEqual({ phone: '+8613800138000', password: '1234567890' }); return json(route, accountSession); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await page.getByLabel('手机号操作', { exact: true }).selectOption('register'); await page.getByLabel('登录手机号', { exact: true }).fill('13800138000'); await openDetails(page, '手机号密码登录'); await page.getByLabel('手机号登录密码', { exact: true }).fill('1234567890'); await page.getByRole('button', { name: '使用手机号和密码登录', exact: true }).click();
  await expect(page.locator('.identity')).toContainText(account.displayName); expect(writes).toEqual(['/auth/phone/password']);
});
