import { test, chromium } from '@playwright/test';
import { randomInt, randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { lstat, mkdtemp, open, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, isAbsolute, join } from 'node:path';
import type { BrowserContext, Page } from '@playwright/test';
import type { AccountIdentity, AuthOptions, Page as ApiPage, Message, PhoneChallenge, Session } from '../../../packages/client/src/index.ts';
import { openAdvanced, openDetails, openLegacy } from './ui.ts';

// Root alone prepares the sole API, private outbox/fixture and deployment. No server is started here.
test.skip(process.env.ALLIRA_CONSOLE_LIVE !== '1', '仅在 root 明确 Mock + test-file SMS + test social READY 后运行约定的一次验收');
const origin = 'http://127.0.0.1:8000';
const rootPath = '/api/v1';
const phoneRemember = '手机登录：记住此设备（共享设备请勿勾选）';
const oldPassword = '账号密码（10–128 字符，最多 256 UTF-8 字节）';
const oldRemember = '记住此设备（可选，使用独立 HttpOnly Cookie，不保存 Bearer）';
type LegacyFixture = { username: string; password: string; userId: string; conversationId: string; phone: string };
function requireTrue(value: unknown): asserts value { if (!value) throw new Error('验收条件不满足；私有数据已隐藏。'); }
function normalizedPhone(value: string): string { requireTrue(/^(?:\+86)?1[3-9][0-9]{9}$/.test(value) && (value.length === 11 || value.length === 14)); return value.startsWith('+86') ? value : `+86${value}`; }
async function privateFile(path: string, maximum = 1024 * 1024): Promise<Buffer> {
  requireTrue(isAbsolute(path) && await realpath(path) === path);
  const info = await lstat(path), parent = await lstat(dirname(path));
  requireTrue(info.isFile() && info.nlink === 1 && (info.mode & 0o777) === 0o600 && info.size <= maximum);
  requireTrue(parent.isDirectory() && (parent.mode & 0o777) === 0o700 && parent.uid === info.uid);
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try { const opened = await handle.stat(); requireTrue(opened.ino === info.ino && opened.dev === info.dev); return await handle.readFile(); }
  finally { await handle.close(); }
}
async function legacyFixture(path: string): Promise<LegacyFixture> {
  const bytes = await privateFile(path, 16384);
  try {
    const value: unknown = JSON.parse(bytes.toString('utf8'));
    requireTrue(value && typeof value === 'object');
    const item = value as Record<string, unknown>;
    for (const name of ['username', 'password', 'userId', 'conversationId', 'phone']) requireTrue(typeof item[name] === 'string' && item[name].length > 0 && item[name].length <= 256);
    return { username: item.username as string, password: item.password as string, userId: item.userId as string, conversationId: item.conversationId as string, phone: normalizedPhone(item.phone as string) };
  } finally { bytes.fill(0); }
}
async function responseFor<T>(page: Page, path: string, method: string, action: () => Promise<unknown>, status?: number): Promise<T> {
  const pending = page.waitForResponse(value => new URL(value.url()).pathname === `${rootPath}${path}` && value.request().method() === method);
  await action(); const response = await pending;
  requireTrue(status === undefined ? response.ok() : response.status() === status);
  return response.json() as Promise<T>;
}
async function cookieRead<T>(page: Page, path: string): Promise<T> {
  return page.evaluate(async resource => {
    const response = await fetch(`/api/v1${resource}`, { credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) throw new Error('私有只读核验失败。');
    return response.json();
  }, path) as Promise<T>;
}
async function accountPage(page: Page): Promise<AccountIdentity> {
  await openAdvanced(page);
  const identity = await responseFor<AccountIdentity>(page, '/account/identity', 'GET', () => page.getByRole('button', { name: '账号', exact: true }).click());
  await page.getByRole('heading', { name: '手机与授权身份', exact: true }).waitFor();
  return identity;
}
async function loggedIn(page: Page): Promise<void> { await page.locator('.identity').filter({ hasText: '已记住此设备' }).waitFor(); }
async function loggedOut(page: Page): Promise<void> { await page.getByRole('heading', { name: '登录后开始聊天', exact: true }).waitFor(); requireTrue(!await page.getByRole('button', { name: '退出登录', exact: true }).isVisible()); }
async function logout(page: Page): Promise<void> { await page.getByRole('button', { name: '退出登录', exact: true }).click(); await loggedOut(page); }
async function eraseByPassword(page: Page, password: string): Promise<void> {
  await accountPage(page); await page.getByText('危险操作：删除账号', { exact: true }).click();
  await page.getByLabel('删除账号的当前密码', { exact: true }).fill(password);
  await page.getByLabel('逐字输入 DELETE MY ACCOUNT', { exact: true }).fill('DELETE MY ACCOUNT');
  await page.getByLabel('确认永久删除账号和在线数据且无法撤销', { exact: true }).check();
  await responseFor(page, '/account/erase', 'POST', () => page.getByRole('button', { name: '永久删除账号和在线数据', exact: true }).click());
  await loggedOut(page);
}
async function oauth(page: Page, provider: 'wechat' | 'qq', requiresBinding: boolean): Promise<void> {
  const callback = page.waitForResponse(value => new URL(value.url()).pathname === `${rootPath}/auth/social/${provider}/callback` && value.request().method() === 'GET');
  await page.getByRole('button', { name: provider === 'wechat' ? '微信登录' : 'QQ 登录', exact: true }).click();
  // Navigation is performed by the real UI through the provider test callback and its single-use state.
  requireTrue((await callback).status() === 303);
  await page.waitForURL(`${origin}/test/`);
  const pending = page.getByText(requiresBinding ? /尚未登录，必须验证手机号并明确关联/ : /已有关联账号，请点击继续完成登录/);
  await pending.waitFor(); requireTrue(!await page.getByRole('button', { name: '新聊天', exact: true }).isVisible());
  requireTrue(page.url() === `${origin}/test/`);
}

// All OTPs are random backend output. They are read only in this server-side test process,
// never through HTTP/debug/browser JavaScript, never printed, never copied to an artifact.
test('existing Mock identity: phone primary, social pending, legacy owner retention, OTP security and public-only offline shell', async ({ browser, request }) => {
  test.setTimeout(200_000);
  const contexts: BrowserContext[] = [];
  let profile = '', phase = 'Mock 与私有夹具检查', outbox = '', fixturePath = '';
  let legacy: LegacyFixture | null = null, password = randomUUID(), optionalPassword = randomUUID();
  const phoneA = `+8613${randomInt(0, 1000000000).toString().padStart(9, '0')}`;
  const phoneB = `+8615${randomInt(0, 1000000000).toString().padStart(9, '0')}`;
  const phoneC = `+8618${randomInt(0, 1000000000).toString().padStart(9, '0')}`;
  const sentAt = new Map<string, number>(); let generated = 0, badAccountWrites = 0, badSocialWrites = 0, badPublicWrites = 0, callbackCount = 0, pageErrors = 0;
  const observe = (page: Page): void => {
    page.on('pageerror', () => { ++pageErrors; });
    page.on('request', value => {
      const path = new URL(value.url()).pathname;
      if (/\/conversations\/[^/]+\/runs$/.test(path) && value.method() === 'POST') ++generated;
      if (path.includes('/auth/social/') && value.method() === 'POST' && (value.headers()['authorization'] || !value.headers()['x-csrf-token'])) ++badSocialWrites;
      if (path.includes('/auth/phone/') && value.method() === 'POST' && (value.headers()['authorization'] || value.headers()['x-csrf-token'])) ++badPublicWrites;
      if (path.includes('/account/') && value.method() === 'POST' && (value.headers()['authorization'] || !value.headers()['x-csrf-token'])) ++badAccountWrites;
      if (/\/auth\/social\/(wechat|qq)\/callback$/.test(path)) ++callbackCount;
    });
  };
  const receiveOtp = async (page: Page, phone: string, purpose: string, path: string, action: () => Promise<unknown>): Promise<string> => {
    const before = await privateFile(outbox); const offset = before.length; before.fill(0);
    const value = await responseFor<PhoneChallenge>(page, path, 'POST', action, 202);
    requireTrue(typeof value.challengeId === 'string' && !('code' in value) && value.resendAfterSeconds === 60);
    sentAt.set(phone, Date.now());
    const deadline = Date.now() + 15_000;
    while (Date.now() < deadline) {
      const bytes = await privateFile(outbox);
      try {
        requireTrue(bytes.length >= offset);
        const tail = bytes.subarray(offset).toString('utf8');
        const rows = tail.slice(0, tail.lastIndexOf('\n') + 1).split('\n').filter(Boolean);
        for (const row of rows) {
          const item: unknown = JSON.parse(row); if (!item || typeof item !== 'object') continue;
          const record = item as Record<string, unknown>;
          if (record.phone === phone && record.purpose === purpose && typeof record.code === 'string' && /^[0-9]{6}$/.test(record.code) && record.code.length === 6 && record.expiresInSeconds === 300) return record.code;
        }
      } finally { bytes.fill(0); }
      await new Promise<void>(resolve => setTimeout(resolve, 100));
    }
    throw new Error('受保护测试短信未在时限内到达。');
  };
  const waitResend = async (phone: string): Promise<void> => {
    const remaining = (sentAt.get(phone) ?? 0) + 61_000 - Date.now();
    if (remaining > 0) await new Promise<void>(resolve => setTimeout(resolve, remaining));
  };
  try {
    const info = await (await request.get('/api/v1/info')).json(); requireTrue(info.provider === 'mock' && info.consoleEnabled);
    const options: AuthOptions = await (await request.get('/api/v1/auth/options')).json();
    requireTrue(options.phone.enabled && options.phone.simulated && options.registrationEnabled && options.legacyPasswordEnabled && options.social.length === 2 && options.social.every(item => item.enabled && item.simulated));
    outbox = process.env.ALLIRA_TEST_SMS_OUTBOX ?? ''; fixturePath = process.env.ALLIRA_TEST_LEGACY_FIXTURE ?? '';
    requireTrue(outbox && fixturePath); legacy = await legacyFixture(fixturePath); const initial = await privateFile(outbox); initial.fill(0);
    requireTrue(legacy.phone !== phoneA && legacy.phone !== phoneB && legacy.phone !== phoneC);
    profile = await mkdtemp(join(tmpdir(), 'allira-identity-pwa-'));
    const main = await chromium.launchPersistentContext(profile, { channel: 'chromium', headless: true, serviceWorkers: 'allow' }); contexts.push(main);
    const socialContext = await browser.newContext({ serviceWorkers: 'allow' }), legacyContext = await browser.newContext({ serviceWorkers: 'allow' }), qqContext = await browser.newContext({ serviceWorkers: 'allow' }); contexts.push(socialContext, legacyContext, qqContext);
    for (const context of contexts) context.setDefaultTimeout(10_000);
    const page = await main.newPage(), social = await socialContext.newPage(), old = await legacyContext.newPage(), qq = await qqContext.newPage(); for (const item of [page, social, old, qq]) observe(item);

    phase = '手机号主入口无密码注册与显式 Cookie';
    await page.goto(`${origin}/test/`); requireTrue(!await page.getByRole('button', { name: '账号登录', exact: true }).isVisible());
    await page.getByLabel('手机号操作', { exact: true }).selectOption('register'); await page.getByLabel('登录手机号', { exact: true }).fill(phoneA);
    let code = await receiveOtp(page, phoneA, 'register', '/auth/phone/challenges', () => page.getByRole('button', { name: '发送手机验证码', exact: true }).click());
    await page.getByLabel('六位手机验证码', { exact: true }).fill(code); code = '';
    await page.getByLabel('手机注册或关联：我已满 18 岁', { exact: true }).check(); await page.getByLabel('手机注册或关联：接受当前服务协议', { exact: true }).check(); await page.getByLabel(phoneRemember, { exact: true }).check();
    await page.getByRole('button', { name: '验证手机号并注册', exact: true }).click(); await loggedIn(page);
    const mainSession = await cookieRead<Session>(page, '/session'), mainIdentity = await accountPage(page); requireTrue(!mainIdentity.passwordEnabled && mainIdentity.phone === phoneA);
    requireTrue(!await page.getByLabel('当前密码', { exact: true }).isVisible() && !await page.getByLabel('删除账号的当前密码', { exact: true }).isVisible());
    await page.reload(); await loggedIn(page); requireTrue((await cookieRead<Session>(page, '/session')).userId === mainSession.userId);

    phase = 'QQ 首次 callback 与手机关联，可选密码并非必填';
    await qq.goto(`${origin}/test/`); await oauth(qq, 'qq', true); await qq.getByLabel('登录手机号', { exact: true }).fill(phoneC);
    code = await receiveOtp(qq, phoneC, 'social_bind', '/auth/social/phone/challenges', () => qq.getByRole('button', { name: '发送手机验证码', exact: true }).click());
    await qq.getByLabel('六位手机验证码', { exact: true }).fill(code); code = ''; await openDetails(qq, '可选设置站内密码'); await qq.getByLabel('可选站内密码', { exact: true }).fill(optionalPassword);
    await qq.getByLabel('手机注册或关联：我已满 18 岁', { exact: true }).check(); await qq.getByLabel('手机注册或关联：接受当前服务协议', { exact: true }).check();
    await qq.getByLabel('确认验证手机号并关联此微信或 QQ 身份；手机号已有账号时关联至该账号，不合并其他账号数据', { exact: true }).check(); await qq.getByLabel(phoneRemember, { exact: true }).check();
    await qq.getByRole('button', { name: '验证手机号并关联社交身份', exact: true }).click(); await loggedIn(qq);
    const qqIdentity = await accountPage(qq); requireTrue(qqIdentity.phone === phoneC && qqIdentity.passwordEnabled && qqIdentity.social.some(item => item.provider === 'qq'));
    await eraseByPassword(qq, optionalPassword); optionalPassword = '';

    phase = '微信首次 callback 必须手机验证关联而非自动登录';
    await social.goto(`${origin}/test/`); await oauth(social, 'wechat', true); await social.getByLabel('登录手机号', { exact: true }).fill(phoneB);
    code = await receiveOtp(social, phoneB, 'social_bind', '/auth/social/phone/challenges', () => social.getByRole('button', { name: '发送手机验证码', exact: true }).click());
    await social.getByLabel('六位手机验证码', { exact: true }).fill(code); code = '';
    await social.getByLabel('手机注册或关联：我已满 18 岁', { exact: true }).check(); await social.getByLabel('手机注册或关联：接受当前服务协议', { exact: true }).check();
    await social.getByLabel('确认验证手机号并关联此微信或 QQ 身份；手机号已有账号时关联至该账号，不合并其他账号数据', { exact: true }).check(); await social.getByLabel(phoneRemember, { exact: true }).check();
    await social.getByRole('button', { name: '验证手机号并关联社交身份', exact: true }).click(); await loggedIn(social);
    const socialSession = await cookieRead<Session>(social, '/session'); requireTrue(socialSession.userId !== mainSession.userId);
    requireTrue((await accountPage(social)).social.some(item => item.provider === 'wechat'));
    await logout(social); await oauth(social, 'wechat', false);
    await social.getByLabel(phoneRemember, { exact: true }).check(); await social.getByRole('button', { name: '继续社交账号登录', exact: true }).click(); await loggedIn(social); requireTrue((await cookieRead<Session>(social, '/session')).userId === socialSession.userId);

    phase = '旧用户名 fixture 登录、绑定手机号与原 owner 原文保留';
    await old.goto(`${origin}/test/`); await openLegacy(old); requireTrue(!await old.getByRole('button', { name: '注册账号', exact: true }).isVisible());
    await old.getByLabel('账号用户名', { exact: true }).fill(legacy.username); await old.getByLabel(oldPassword, { exact: true }).fill(legacy.password); await old.getByLabel(oldRemember, { exact: true }).check(); await old.getByRole('button', { name: '账号登录', exact: true }).click(); await loggedIn(old);
    const beforeSession = await cookieRead<Session>(old, '/session'); requireTrue(beforeSession.userId === legacy.userId);
    const originalMessages = await cookieRead<ApiPage<Message>>(old, `/conversations/${encodeURIComponent(legacy.conversationId)}/messages/recent?limit=50`); requireTrue(originalMessages.items.some(item => item.role === 'user' && item.content.length > 0));
    const identity = await accountPage(old); requireTrue(identity.requiresPhoneBinding && identity.passwordEnabled);
    await old.getByLabel('绑定手机号', { exact: true }).fill(legacy.phone); await old.getByLabel('绑定手机的当前密码', { exact: true }).fill(legacy.password);
    code = await receiveOtp(old, legacy.phone, 'bind', '/account/phone/challenges', () => old.getByRole('button', { name: '发送绑定手机验证码', exact: true }).click());
    await old.getByLabel('绑定手机的当前密码', { exact: true }).fill(legacy.password); await old.getByLabel('绑定手机的六位验证码', { exact: true }).fill(code); code = ''; await old.getByLabel('确认将此手机号绑定到当前账号，保留当前账号数据且不合并其他账号', { exact: true }).check();
    const bound = await responseFor<AccountIdentity>(old, '/account/phone/bind', 'POST', () => old.getByRole('button', { name: '确认绑定此手机号', exact: true }).click()); requireTrue(bound.phone === legacy.phone && !bound.requiresPhoneBinding);
    const retained = await cookieRead<ApiPage<Message>>(old, `/conversations/${encodeURIComponent(legacy.conversationId)}/messages/recent?limit=50`); requireTrue(JSON.stringify(retained.items) === JSON.stringify(originalMessages.items) && (await cookieRead<Session>(old, '/session')).userId === legacy.userId);

    phase = '六项公开壳、实际离线与恢复 Cookie 不缓存私有身份';
    await page.evaluate(async () => { await Promise.race([(async () => { await navigator.serviceWorker.ready; if (!navigator.serviceWorker.controller) await new Promise<void>(resolve => navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true })); })(), new Promise<never>((_, reject) => setTimeout(() => reject(new Error('公开壳未就绪。')), 8_000))]); });
    const paths = await page.evaluate(async () => { const result: string[] = []; for (const name of await caches.keys()) for (const value of await (await caches.open(name)).keys()) result.push(new URL(value.url).pathname); return result.sort(); });
    requireTrue(paths.length === 6 && new Set(paths).size === 6 && paths.includes('/test/') && paths.includes('/test/manifest.webmanifest') && paths.filter(path => /^\/console-assets\/public-chat-[A-Z0-9]+\.(js|css)$/.test(path)).length === 2 && paths.filter(path => /^\/console-assets\/public-icon-(192|512)-[a-f0-9]{16}\.png$/.test(path)).length === 2);
    const sizes = await page.evaluate(async () => { const manifest = await (await fetch('/test/manifest.webmanifest', { credentials: 'omit' })).json(); const result: string[] = []; for (const icon of manifest.icons) { const image = new Image(); image.src = icon.src; await image.decode(); if (`${image.naturalWidth}x${image.naturalHeight}` !== icon.sizes) throw new Error('公开图标尺寸不符。'); result.push(icon.sizes); } return result.sort().join(','); }); requireTrue(sizes === '192x192,512x512');
    const cdp = await main.newCDPSession(page); await cdp.send('Page.enable'); requireTrue((await cdp.send('Page.getInstallabilityErrors')).installabilityErrors.length === 0);
    await main.setOffline(true); await cdp.send('Network.enable'); await cdp.send('Network.overrideNetworkState', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    requireTrue(await page.evaluate(async () => { try { await fetch('/api/v1/account/identity', { cache: 'no-store', signal: AbortSignal.timeout(2000) }); return false; } catch { return true; } }));
    await page.reload({ waitUntil: 'domcontentloaded' }); await page.getByRole('heading', { name: '登录后开始聊天', exact: true }).waitFor(); requireTrue(!await page.getByRole('button', { name: '退出登录', exact: true }).isVisible());
    await main.setOffline(false); await cdp.send('Network.overrideNetworkState', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 }); await page.reload(); await loggedIn(page);

    phase = '用途 set_password OTP 明确确认并撤销所有旧会话';
    await waitResend(phoneA); const stale = await browser.newContext({ serviceWorkers: 'block' }); contexts.push(stale); const cookies = (await main.cookies()).filter(value => value.name === 'allira_user'); requireTrue(cookies.length === 1); await stale.addCookies(cookies);
    await accountPage(page); await openDetails(page, '手机验证敏感操作');
    code = await receiveOtp(page, phoneA, 'set_password', '/account/phone/security/challenges', () => page.getByRole('button', { name: '发送账号安全验证码', exact: true }).click());
    await page.getByLabel('账号安全六位验证码', { exact: true }).fill(code); code = ''; await page.getByLabel('手机验证后的新密码', { exact: true }).fill(password); await page.getByLabel('确认设置密码并撤销此账号全部登录', { exact: true }).check();
    await responseFor(page, '/account/phone/password', 'POST', () => page.getByRole('button', { name: '通过手机设置密码并退出全部登录', exact: true }).click()); await loggedOut(page);
    requireTrue((await stale.request.get(`${origin}/api/v1/auth/browser/session`)).status() === 401);
    await page.getByLabel('登录手机号', { exact: true }).fill(phoneA); await openDetails(page, '手机号密码登录'); await page.getByLabel('手机号登录密码', { exact: true }).fill(password); await page.getByLabel(phoneRemember, { exact: true }).check(); await page.getByRole('button', { name: '使用手机号和密码登录', exact: true }).click(); await loggedIn(page); requireTrue((await cookieRead<Session>(page, '/session')).userId === mainSession.userId);

    phase = 'passwordless erase 专用 OTP 与逐字确认删除';
    await waitResend(phoneB); requireTrue(!(await accountPage(social)).passwordEnabled); await openDetails(social, '手机验证敏感操作'); await social.getByLabel('手机安全验证用途', { exact: true }).selectOption('erase');
    code = await receiveOtp(social, phoneB, 'erase', '/account/phone/security/challenges', () => social.getByRole('button', { name: '发送账号安全验证码', exact: true }).click());
    await social.getByLabel('账号安全六位验证码', { exact: true }).fill(code); code = ''; await social.getByLabel('手机删除：逐字输入 DELETE MY ACCOUNT', { exact: true }).fill('DELETE MY ACCOUNT'); await social.getByLabel('手机删除：确认永久删除账号和在线数据且无法撤销', { exact: true }).check(); await responseFor(social, '/account/phone/erase', 'POST', () => social.getByRole('button', { name: '通过手机永久删除账号和在线数据', exact: true }).click()); await loggedOut(social);

    phase = '已绑定旧账号可用手机号验证码登录且数据未迁移丢失';
    await waitResend(legacy.phone); await logout(old); await old.getByLabel('登录手机号', { exact: true }).fill(legacy.phone);
    code = await receiveOtp(old, legacy.phone, 'login', '/auth/phone/challenges', () => old.getByRole('button', { name: '发送手机验证码', exact: true }).click()); await old.getByLabel('六位手机验证码', { exact: true }).fill(code); code = ''; await old.getByLabel(phoneRemember, { exact: true }).check(); await old.getByRole('button', { name: '验证码登录', exact: true }).click(); await loggedIn(old);
    requireTrue((await cookieRead<Session>(old, '/session')).userId === legacy.userId); const afterLogin = await cookieRead<ApiPage<Message>>(old, `/conversations/${encodeURIComponent(legacy.conversationId)}/messages/recent?limit=50`); requireTrue(JSON.stringify(afterLogin.items) === JSON.stringify(originalMessages.items));

    phase = '明确删除本次测试账号、检查零生成与凭据边界';
    await eraseByPassword(old, legacy.password); legacy.password = ''; await eraseByPassword(page, password); password = '';
    requireTrue(generated === 0 && badAccountWrites === 0 && badSocialWrites === 0 && badPublicWrites === 0 && callbackCount >= 3 && pageErrors === 0);
    for (const item of [page, social, old, qq]) { requireTrue(await item.evaluate(() => localStorage.length + sessionStorage.length) === 0); requireTrue(await item.locator('input[type=password]').evaluateAll(inputs => inputs.every(input => (input as HTMLInputElement).value === ''))); }
    await page.reload(); await loggedOut(page);
  } catch { throw new Error(`实际身份 Mock 验收阶段失败：${phase}。原始错误、手机号、验证码、授权地址及账号正文均已隐藏。`); }
  finally {
    password = ''; optionalPassword = ''; if (legacy) { legacy.password = ''; legacy.phone = ''; legacy.username = ''; legacy.userId = ''; legacy.conversationId = ''; } legacy = null; outbox = ''; fixturePath = ''; sentAt.clear();
    await Promise.allSettled(contexts.map(context => context.close()));
    if (profile) { const target = await realpath(profile), parent = await realpath(tmpdir()); requireTrue(dirname(target) === parent && basename(target).startsWith('allira-identity-pwa-')); await rm(target, { recursive: true, force: true }); }
  }
});
