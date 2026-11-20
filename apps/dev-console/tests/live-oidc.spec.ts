import { test, chromium } from '@playwright/test';
import { constants } from 'node:fs';
import { lstat, mkdtemp, open, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, isAbsolute, join } from 'node:path';
import type { BrowserContext, Page, Response } from '@playwright/test';
import type { AccountSession, AuthOptions, EnergyWallet, Message, OidcSession, Page as ApiPage, Session } from '../../../packages/client/src/index.ts';
import { openAdvanced, openDetails } from './ui.ts';

// Root alone prepares the real OSS issuer, sole Mock API, private fixtures and production build.
// The only mocked edge here is the upstream's official mock-social transport; OIDC is never forged.
test.skip(process.env.ALLIRA_CONSOLE_LIVE !== '1', '仅在 root 明确真实 Logto OSS / 单一 Mock API ROOT_READY 后执行约定的一次验收');
const origin = 'http://127.0.0.1:8000';
const issuerOrigin = 'http://127.0.0.1:3001';
const apiRoot = '/api/v1';
const mockSms = '/tmp/logto/mock_sms_record.txt';
const rememberLabel = '托管登录：记住此设备（共享设备请勿勾选）';
type RetainedFixture = { userId: string; conversationId: string; phone: string; expectedWallet?: number };
type LegacyFixture = RetainedFixture & { username: string; password: string };
type NewIdentities = { newPhone: string; socialPhone: string; socialSubject: string };
// Diagnostics contain only compile-time labels, allowlisted categories and numeric counters.
// Never attach a caught error/cause, request, URL, DOM or fixture value to the final Error.
const substeps = {
  notStarted: '尚未开始', mockInfo: '配置：确认 Mock', authOptions: '配置：确认真实托管模式',
  fixtureRead: '私有 fixture：读取', privatePath: '私有文件：绝对路径与 realpath', privateStat: '私有文件：权限和类型', privateOpen: '私有文件：无符号链接打开', privateRead: '私有文件：读取字节', privateParse: '私有 fixture：解析', privateValidate: '私有 fixture：字段与互异性',
  profileCreate: '浏览器：临时 profile', contextCreate: '浏览器：独立 context', pageCreate: '浏览器：创建页面', routeBoundary: '测试运输：检查官方 mock 边界',
  pageOpen: '主入口：打开公开页面', startClick: '主入口：显式开始托管认证', hostedWait: '托管 UI：等待登录页', createLink: '托管 UI：切换注册',
  phoneInputWait: '托管 UI：等待手机号输入', countryInspect: '托管 UI：读取默认国家', countryOpen: '托管 UI：展开国家', countrySearch: '托管 UI：搜索国家', countryChoose: '托管 UI：选择中国区号', countryVerify: '托管 UI：核对中国区号', phoneFill: '托管 UI：填写本次手机号',
  smsBaseline: '短信：发送前记录基线', smsSend: '托管 UI：明确发送验证码', smsResponse: '短信：等待成功响应', smsCodePage: '托管 UI：等待验证码页', smsOpen: '短信：私有记录打开', smsStat: '短信：私有记录权限', smsRead: '短信：读取新记录', smsParse: '短信：解析新记录', smsMatch: '短信：核对新鲜度号码用途', smsPoll: '短信：有界等待新记录', smsTimeout: '短信：新记录等待到期', smsDigit: '托管 UI：逐格填写验证码',
  callbackCdp: '回调：建立独立网络观测', callbackNetwork: '回调：启用原始网络事件', callbackFrame: '回调：核对主框架', callbackResponse: '回调：等待原始重定向', callbackStatus: '回调：核对原始 303 与固定公开目标', callbackPage: '回调：等待固定公开页', pendingRead: '回调：读取私有 pending', pendingValidate: '回调：核对用途和非登录状态', pendingUi: '回调：等待明确确认入口',
  adult: '确认：明确成年', terms: '确认：当前协议', identity: '确认：使用已验证身份', remember: '确认：主动记住设备',
  businessAction: '业务：显式表单动作', businessResponse: '业务：等待响应', businessStatus: '业务：核对响应状态', businessJson: '业务：解析 DTO', loginWait: '普通会话：等待已记住设备', logoutWait: '普通会话：等待退出', sessionRead: '普通会话：只读核验',
  reload: '普通会话：刷新', ownerCheck: '普通会话：核对 owner', advancedOpen: '账号：展开高级入口', accountOpen: '账号：显式打开', accountHeading: '账号：等待托管安全页', accountLocalHidden: '账号：旧凭据表单保持隐藏', accountControls: '账号：业务设备和导出仍可见',
  migrationOpen: '迁移：主动展开旧账号证明', migrationUsername: '迁移：填写旧用户名', migrationPassword: '迁移：填写旧密码', migrationConsent: '迁移：明确关联旧账号', migrationResult: '迁移：核对原 owner', migrationHistory: '迁移：核对原完整消息', migrationWallet: '迁移：核对原能量', chatHome: '迁移：返回聊天', phoneMigrationReject: '手机迁移：无独立确认不得提交', phoneMigrationUnchecked: '手机迁移：确认默认未勾选', phoneMigrationConsent: '手机迁移：独立确认同号原账号',
  socialStart: '社交：显式开始托管认证', socialButton: '社交：选择官方 mock connector', socialPhonePage: '社交：等待强制补手机', socialNoEarlyCallback: '社交：补手机前无业务回调', socialResult: '社交：核对新业务身份',
  pwaReady: '公开壳：等待 Service Worker', pwaKeys: '公开壳：读取缓存路径', pwaAllowlist: '公开壳：核对六项公开 allowlist', pwaIcons: '公开壳：解码 PNG 尺寸', pwaCdp: '公开壳：建立安装资格核验', pwaInstall: '公开壳：核对 Chromium 安装资格', pwaOffline: '离线：设置真实网络断开', pwaPrivateFail: '离线：私有请求必须失败', pwaReload: '离线：公开壳刷新', pwaNoIdentity: '离线：不得恢复私有身份', pwaOnline: '联网：恢复网络', pwaRestore: '联网：恢复普通 Cookie',
  eraseOwner: '删除：读取原 owner', eraseOpen: '删除：展开业务数据控制', eraseStart: '删除：显式开始 fresh 认证', erasePending: '删除：核对用途及原普通会话', eraseNoPhrase: '删除：无逐字短语不得提交', erasePhrase: '删除：填写逐字短语', eraseNoConsent: '删除：未勾选不得提交', eraseConsent: '删除：明确业务范围与不可撤销', eraseResponse: '删除：明确最终提交', eraseExpired: '删除：普通会话必须失效',
  finalCounters: '最终：核对安全计数', finalStorage: '最终：无浏览器 Storage 留存', cleanupContexts: '清理：关闭本次 contexts', cleanupProfilePath: '清理：校验本次临时 profile 路径', cleanupProfileRemove: '清理：删除本次临时 profile',
} as const;
type FailureCategory = 'timeout' | 'strict_locator' | 'invariant' | 'json_parse' | 'io' | 'aborted' | 'unknown';
const diagnostic = { substep: substeps.notStarted as string, actions: 0, owner: 0, smsResponses: 0, smsPolls: 0, freshSmsRecords: 0, digitsFilled: 0, callbackResponses: 0, businessResponses: 0, callbackStatus: -1, callbackMethodEnum: 0, callbackNavigation: 0, callbackMainFrame: 0, callbackFromSW: 0, callbackResourceEnum: 0, callbackRedirectDepth: 0, callbackRawResponses: 0, callbackRawStatus: -1, callbackRawMethodEnum: 0, callbackRawNavigation: 0, callbackRawMainFrame: 0, callbackRawFromSW: 0, callbackRawResourceEnum: 0, callbackRawToPublic: 0, callbackFinalStatus: -1, callbackFinalFromSW: 0 };
// Stable numeric mappings only; no method, resource, hostname, URL, header or response body is logged.
const resourceTypes = ['document', 'stylesheet', 'image', 'media', 'font', 'script', 'texttrack', 'xhr', 'fetch', 'eventsource', 'websocket', 'manifest', 'other'] as const;
type ResourceCategory = typeof resourceTypes[number];
function resourceCategory(value: string): ResourceCategory { return resourceTypes.find(item => item === value) ?? 'other'; }
function resourceEnum(value: string): number { return resourceTypes.indexOf(resourceCategory(value)) + 1; }
function methodEnum(value: string): number { return value === 'GET' ? 1 : value === 'POST' ? 2 : value === 'OPTIONS' ? 3 : value === 'HEAD' ? 4 : 5; }
function mark(key: keyof typeof substeps): void { diagnostic.substep = substeps[key]; }
async function step<T>(key: keyof typeof substeps, action: () => Promise<T>): Promise<T> { mark(key); const result = await action(); ++diagnostic.actions; return result; }
class AcceptanceError extends Error { constructor(readonly category: FailureCategory = 'invariant') { super('脱敏验收条件不满足。'); this.name = 'AcceptanceError'; } }
function failureCategory(error: unknown): FailureCategory {
  if (error instanceof AcceptanceError) return error.category;
  if (!(error instanceof Error)) return 'unknown';
  if (error.name === 'TimeoutError') return 'timeout';
  if (error.name === 'SyntaxError') return 'json_parse';
  if (error.name === 'AbortError') return 'aborted';
  if (error.message.includes('strict mode violation')) return 'strict_locator';
  if ('code' in error && typeof error.code === 'string' && ['ENOENT', 'EACCES', 'EPERM', 'ELOOP', 'ENOTDIR', 'EISDIR', 'EMFILE', 'EBADF', 'EIO', 'ESTALE', 'EINVAL'].includes(error.code)) return 'io';
  return 'unknown';
}
// Attach a rejection handler immediately so a failed action cannot leak a later orphan wait error.
function observed<T>(waiting: Promise<T>): Promise<T> { void waiting.catch(() => {}); return waiting; }
function requireTrue(value: unknown): asserts value { if (!value) throw new AcceptanceError(); }
function normalizePhone(value: string): string { requireTrue(/^\+861[3-9][0-9]{9}$/.test(value) && value.length === 14); return value; }
async function privateBytes(path: string, maximum = 16384): Promise<Buffer> {
  mark('privatePath'); requireTrue(isAbsolute(path) && await realpath(path) === path);
  mark('privateStat'); const info = await lstat(path), parent = await lstat(dirname(path));
  requireTrue(info.isFile() && info.nlink === 1 && info.uid === process.getuid?.() && (info.mode & 0o777) === 0o600 && info.size <= maximum);
  requireTrue(parent.isDirectory() && parent.uid === info.uid && (parent.mode & 0o777) === 0o700);
  const handle = await step('privateOpen', () => open(path, constants.O_RDONLY | constants.O_NOFOLLOW));
  try { mark('privateStat'); const opened = await handle.stat(); requireTrue(opened.ino === info.ino && opened.dev === info.dev); return await step('privateRead', () => handle.readFile()); }
  finally { await handle.close(); }
}
async function privateJson(path: string): Promise<Record<string, unknown>> {
  const bytes = await privateBytes(path);
  try { mark('privateParse'); const item: unknown = JSON.parse(bytes.toString('utf8')); requireTrue(item && typeof item === 'object' && !Array.isArray(item)); return item as Record<string, unknown>; }
  finally { bytes.fill(0); }
}
async function legacyFixture(path: string): Promise<LegacyFixture> {
  const value = await privateJson(path); mark('privateValidate');
  for (const name of ['username', 'password', 'userId', 'conversationId', 'phone']) requireTrue(typeof value[name] === 'string' && value[name].length > 0 && value[name].length <= 256);
  requireTrue(value.expectedWallet === undefined || typeof value.expectedWallet === 'number' && Number.isInteger(value.expectedWallet));
  return { username: value.username as string, password: value.password as string, userId: value.userId as string, conversationId: value.conversationId as string, phone: normalizePhone(value.phone as string), expectedWallet: value.expectedWallet as number | undefined };
}
async function phoneOnlyFixture(path: string): Promise<RetainedFixture> {
  const value = await privateJson(path); mark('privateValidate');
  for (const name of ['userId', 'conversationId', 'phone']) requireTrue(typeof value[name] === 'string' && value[name].length > 0 && value[name].length <= 256);
  requireTrue(value.passwordless === undefined || value.passwordless === true);
  requireTrue(value.expectedWallet === undefined || typeof value.expectedWallet === 'number' && Number.isInteger(value.expectedWallet));
  return { userId: value.userId as string, conversationId: value.conversationId as string, phone: normalizePhone(value.phone as string), expectedWallet: value.expectedWallet as number | undefined };
}
async function newIdentities(path: string): Promise<NewIdentities> {
  const value = await privateJson(path); mark('privateValidate');
  requireTrue(typeof value.newPhone === 'string' && typeof value.socialPhone === 'string' && typeof value.socialSubject === 'string' && /^[A-Za-z0-9_-]{8,120}$/.test(value.socialSubject));
  return { newPhone: normalizePhone(value.newPhone), socialPhone: normalizePhone(value.socialPhone), socialSubject: value.socialSubject };
}
async function responseFor<T>(page: Page, path: string, method: string, action: () => Promise<unknown>, status?: number): Promise<T> {
  const waiting = observed(page.waitForResponse(response => new URL(response.url()).pathname === `${apiRoot}${path}` && response.request().method() === method));
  await step('businessAction', action); const response = await step('businessResponse', () => waiting); ++diagnostic.businessResponses;
  mark('businessStatus'); requireTrue(status === undefined ? response.ok() : response.status() === status);
  return step('businessJson', () => response.json() as Promise<T>);
}
async function cookieRead<T>(page: Page, path: string, key: keyof typeof substeps = 'sessionRead'): Promise<T> {
  return step(key, () => page.evaluate(async resource => { const response = await fetch(`/api/v1${resource}`, { credentials: 'same-origin', cache: 'no-store' }); if (!response.ok) throw new Error('私有只读核验失败。'); return response.json(); }, path) as Promise<T>);
}
async function consent(page: Page): Promise<void> {
  await step('adult', () => page.getByLabel('托管登录：我已满 18 岁', { exact: true }).check());
  await step('terms', () => page.getByLabel('托管登录：接受当前服务协议', { exact: true }).check());
  await step('identity', () => page.getByLabel('确认使用上方已验证手机号身份进入 AlliraAi，不静默合并其他账号', { exact: true }).check());
  await step('remember', () => page.getByLabel(rememberLabel, { exact: true }).check());
}
async function loggedIn(page: Page): Promise<void> { await step('loginWait', () => page.locator('.identity').filter({ hasText: '已记住此设备' }).waitFor()); }
async function loggedOut(page: Page): Promise<void> { await step('logoutWait', () => page.getByRole('heading', { name: '登录后开始聊天', exact: true }).waitFor()); requireTrue(!await page.getByRole('button', { name: '退出登录', exact: true }).isVisible()); }
async function accountPage(page: Page): Promise<void> {
  await step('advancedOpen', () => openAdvanced(page));
  await step('accountOpen', () => page.getByRole('button', { name: '账号', exact: true }).click());
  await step('accountHeading', () => page.getByRole('heading', { name: '托管身份与账号安全', exact: true }).waitFor());
}

// Set by the upstream maintainer's exact hosted-UI contract, not by URL or credential injection.
async function chinesePhone(page: Page, phone: string): Promise<void> {
  const identifier = page.locator('input[name="identifier"][type="tel"]');
  await step('phoneInputWait', () => identifier.waitFor());
  const country = page.getByRole('button', { name: '+1', exact: true });
  if (await step('countryInspect', () => country.isVisible())) {
    await step('countryOpen', () => country.click());
    await step('countrySearch', () => page.locator('input[name="country-code-search"]').fill('86'));
    await step('countryChoose', () => page.getByRole('button', { name: '+86', exact: true }).click());
  }
  mark('countryVerify'); requireTrue(await page.getByRole('button', { name: '+86', exact: true }).isVisible());
  await step('phoneFill', () => identifier.fill(normalizePhone(phone).slice(3)));
}
async function freshSms(phone: string, sentAt: number, before: { mtimeMs: number; ino: number; bytes: string } | null, expectedType: string): Promise<string> {
  const deadline = Date.now() + 15_000;
  while (Date.now() < deadline) {
    ++diagnostic.smsPolls; let buffer: Buffer | null = null;
    try {
      const handle = await step('smsOpen', () => open(mockSms, constants.O_RDONLY | constants.O_NOFOLLOW));
      try {
        mark('smsStat'); const info = await handle.stat(); const parent = await lstat(dirname(mockSms));
        requireTrue(info.isFile() && info.uid === process.getuid?.() && info.nlink === 1 && (info.mode & 0o777) === 0o600 && info.size <= 16384);
        requireTrue(parent.isDirectory() && parent.uid === info.uid && (parent.mode & 0o777) === 0o700);
        buffer = await step('smsRead', () => handle.readFile()); const text = buffer.toString('utf8');
        mark('smsMatch'); const changed = !before || info.ino !== before.ino || info.mtimeMs !== before.mtimeMs || text !== before.bytes;
        if (info.mtimeMs + 2 >= sentAt && changed) {
          mark('smsParse'); const item: unknown = JSON.parse(text);
          if (item && typeof item === 'object') {
            mark('smsMatch'); const value = item as Record<string, unknown>;
            if (value.phone === phone.slice(1) && value.type === expectedType && typeof value.code === 'string' && value.code.length === 6 && /^[0-9]{6}$/.test(value.code)) { ++diagnostic.freshSmsRecords; return value.code; }
          }
        }
      } finally { await handle.close(); }
    } catch (error) { if (!(error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT')) throw error; }
    finally { buffer?.fill(0); }
    await step('smsPoll', () => new Promise<void>(resolve => setTimeout(resolve, 100)));
  }
  mark('smsTimeout'); throw new AcceptanceError('timeout');
}
async function smsBefore(): Promise<{ mtimeMs: number; ino: number; bytes: string } | null> {
  mark('smsBaseline');
  try { const bytes = await privateBytes(mockSms); try { mark('smsBaseline'); const stat = await lstat(mockSms); return { mtimeMs: stat.mtimeMs, ino: stat.ino, bytes: bytes.toString('utf8') }; } finally { bytes.fill(0); } }
  catch (error) { if (error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT') return null; throw error; }
}
async function enterOfficialSms(page: Page, phone: string, expectedType: string, issued: Promise<Response>, sentAt: number, before: Awaited<ReturnType<typeof smsBefore>>): Promise<void> {
  const response = await step('smsResponse', () => issued); ++diagnostic.smsResponses; requireTrue(response.ok());
  await step('smsCodePage', () => page.waitForURL(url => url.origin === issuerOrigin && url.pathname.includes('verification-code')));
  let code = await freshSms(phone, sentAt, before, expectedType);
  try { for (let index = 0; index < 6; ++index) { await step('smsDigit', () => page.locator(`input[type="number"][name="passcode_${index}"]`).fill(code[index])); ++diagnostic.digitsFilled; } }
  finally { code = ''; if (before) before.bytes = ''; }
  // Official Logto auto-submits the sixth digit. Never race its disabled Continue button.
}
async function hostedPhone(page: Page, phone: string, create: boolean): Promise<void> {
  await step('hostedWait', () => page.waitForURL(url => url.origin === issuerOrigin && url.pathname === '/sign-in'));
  if (create) await step('createLink', () => page.getByRole('link', { name: 'Create account', exact: true }).click());
  await chinesePhone(page, phone);
  const before = await smsBefore(), sentAt = Date.now();
  const issued = observed(page.waitForResponse(response => new URL(response.url()).origin === issuerOrigin && new URL(response.url()).pathname === '/api/experience/verification/verification-code' && response.request().method() === 'POST'));
  await step('smsSend', () => page.getByRole('button', { name: create ? 'Create account' : 'Sign in', exact: true }).click());
  await enterOfficialSms(page, phone, create ? 'Register' : 'SignIn', issued, sentAt, before);
}
async function callback(page: Page, action: () => Promise<unknown>): Promise<OidcSession> {
  diagnostic.callbackStatus = -1; diagnostic.callbackMethodEnum = 0; diagnostic.callbackNavigation = 0; diagnostic.callbackMainFrame = 0;
  diagnostic.callbackFromSW = 0; diagnostic.callbackResourceEnum = 0; diagnostic.callbackRedirectDepth = 0;
  diagnostic.callbackRawStatus = -1; diagnostic.callbackRawMethodEnum = 0; diagnostic.callbackRawNavigation = 0; diagnostic.callbackRawMainFrame = 0;
  diagnostic.callbackRawFromSW = 0; diagnostic.callbackRawResourceEnum = 0; diagnostic.callbackRawToPublic = 0; diagnostic.callbackFinalStatus = -1; diagnostic.callbackFinalFromSW = 0;
  const cdp = await step('callbackCdp', () => page.context().newCDPSession(page));
  const requests = new Map<string, { method: number; navigation: number; mainFrame: number; resource: number }>();
  let timer: ReturnType<typeof setTimeout> | undefined, settled = false;
  let resolveRedirect!: () => void, rejectRedirect!: (error: AcceptanceError) => void;
  const rawRedirect = observed(new Promise<void>((resolve, reject) => { resolveRedirect = resolve; rejectRedirect = reject; }));
  const visibleResponse = (response: Response): void => {
    try {
      const url = new URL(response.url());
      if (url.origin !== origin) return;
      if (url.pathname === '/test/' && !url.search && !url.hash) { diagnostic.callbackFinalStatus = response.status(); diagnostic.callbackFinalFromSW = Number(response.fromServiceWorker()); }
      if (url.pathname !== `${apiRoot}/auth/oidc/callback`) return;
      ++diagnostic.callbackResponses; const request = response.request(); diagnostic.callbackStatus = response.status();
      diagnostic.callbackMethodEnum = methodEnum(request.method()); diagnostic.callbackNavigation = Number(request.isNavigationRequest());
      diagnostic.callbackFromSW = Number(response.fromServiceWorker()); diagnostic.callbackResourceEnum = resourceEnum(request.resourceType());
      try { diagnostic.callbackMainFrame = Number(request.frame() === page.mainFrame()); } catch { diagnostic.callbackMainFrame = 0; }
      let previous = request.redirectedFrom(); diagnostic.callbackRedirectDepth = 0;
      while (previous && diagnostic.callbackRedirectDepth < 8) { ++diagnostic.callbackRedirectDepth; previous = previous.redirectedFrom(); }
    } catch { if (!settled) { settled = true; rejectRedirect(new AcceptanceError()); } }
  };
  type RawRequestEvent = { requestId: string; loaderId: string; frameId?: string; type?: string; request: { url: string; method: string }; redirectResponse?: { url: string; status: number; fromServiceWorker?: boolean } };
  let mainFrameId = '';
  const requested = (event: RawRequestEvent): void => {
    if (settled) return;
    try {
      const url = new URL(event.request.url), prior = event.redirectResponse;
      if (prior) {
        const previous = new URL(prior.url);
        if (previous.origin === origin && previous.pathname === `${apiRoot}/auth/oidc/callback`) {
          const sent = requests.get(event.requestId); ++diagnostic.callbackRawResponses;
          diagnostic.callbackRawStatus = prior.status; diagnostic.callbackRawMethodEnum = sent?.method ?? 0;
          diagnostic.callbackRawNavigation = sent?.navigation ?? 0; diagnostic.callbackRawMainFrame = sent?.mainFrame ?? 0;
          diagnostic.callbackRawFromSW = Number(!!prior.fromServiceWorker); diagnostic.callbackRawResourceEnum = sent?.resource ?? 0;
          diagnostic.callbackRawToPublic = Number(url.origin === origin && url.pathname === '/test/' && !url.search && !url.hash && !url.username && !url.password);
          settled = true;
          if (diagnostic.callbackRawStatus === 303 && diagnostic.callbackRawMethodEnum === 1 && diagnostic.callbackRawNavigation === 1 && diagnostic.callbackRawMainFrame === 1 && diagnostic.callbackRawResourceEnum === 1 && diagnostic.callbackRawToPublic === 1 && event.request.method === 'GET' && event.type === 'Document' && event.frameId === mainFrameId) resolveRedirect();
          else rejectRedirect(new AcceptanceError());
          return;
        }
      }
      if (url.origin === origin && url.pathname === `${apiRoot}/auth/oidc/callback`) requests.set(event.requestId, { method: methodEnum(event.request.method), navigation: Number(event.type === 'Document' && event.requestId === event.loaderId), mainFrame: Number(event.frameId === mainFrameId), resource: resourceEnum((event.type ?? 'other').toLowerCase()) });
    } catch { if (!settled) { settled = true; rejectRedirect(new AcceptanceError()); } }
  };
  const received = (event: { response: { url: string; status: number; fromServiceWorker?: boolean } }): void => {
    try { const url = new URL(event.response.url); if (url.origin === origin && url.pathname === '/test/' && !url.search && !url.hash) { diagnostic.callbackFinalStatus = event.response.status; diagnostic.callbackFinalFromSW = Number(!!event.response.fromServiceWorker); } }
    catch { if (!settled) { settled = true; rejectRedirect(new AcceptanceError()); } }
  };
  try {
    const frame = await step('callbackFrame', () => cdp.send('Page.getFrameTree')); mainFrameId = frame.frameTree.frame.id;
    cdp.on('Network.requestWillBeSent', requested); cdp.on('Network.responseReceived', received); page.on('response', visibleResponse);
    await step('callbackNetwork', () => cdp.send('Network.enable'));
    await action();
    if (!settled) timer = setTimeout(() => { if (!settled) { settled = true; rejectRedirect(new AcceptanceError('timeout')); } }, 12_000);
    await step('callbackResponse', () => rawRedirect); mark('callbackStatus');
    requireTrue(diagnostic.callbackRawStatus === 303 && diagnostic.callbackRawMethodEnum === 1 && diagnostic.callbackRawNavigation === 1 && diagnostic.callbackRawMainFrame === 1 && diagnostic.callbackRawResourceEnum === 1 && diagnostic.callbackRawToPublic === 1);
    // A worker may expose only the final public-shell 200 as the callback's Page response.
    // The original HTTP redirect above remains mandatory; never accept that 200 as proof.
    await step('callbackPage', () => page.waitForURL(url => url.origin === origin && url.pathname === '/test/' && !url.search && !url.hash));
    const value = await cookieRead<OidcSession>(page, '/auth/oidc/session', 'pendingRead'); mark('pendingValidate'); requireTrue(value.pending?.provider === 'logto'); value.csrfToken = '';
    requireTrue(!await page.getByRole('button', { name: '确认身份并进入聊天', exact: true }).isVisible() || !await page.getByRole('button', { name: '退出登录', exact: true }).isVisible());
    return value;
  } finally {
    if (timer) clearTimeout(timer); requests.clear(); cdp.off('Network.requestWillBeSent', requested); cdp.off('Network.responseReceived', received); page.off('response', visibleResponse);
    await cdp.detach();
  }
}
async function pendingLogin(page: Page, phone: string, create: boolean, purpose: 'login' | 'phone_migration' = 'login'): Promise<void> {
  await step('pageOpen', () => page.goto(`${origin}/test/`));
  await step('startClick', () => page.getByRole('button', { name: purpose === 'phone_migration' ? '原来仅用手机号登录，保留历史' : '使用手机号登录 / 注册', exact: true }).click());
  const pending = await callback(page, () => hostedPhone(page, phone, create)); mark('pendingValidate'); requireTrue(pending.pending?.purpose === purpose);
  await step('pendingUi', () => page.getByRole('button', { name: '确认身份并进入聊天', exact: true }).waitFor()); requireTrue(!await page.getByRole('button', { name: '退出登录', exact: true }).isVisible());
}
async function complete(page: Page): Promise<string> {
  await consent(page); const value = await responseFor<AccountSession>(page, '/auth/oidc/complete', 'POST', () => page.getByRole('button', { name: '确认身份并进入聊天', exact: true }).click());
  const id = value.user.id; value.accessToken = ''; await loggedIn(page); const session = await cookieRead<Session>(page, '/session'); mark('ownerCheck'); requireTrue(session.userId === id); return id;
}
async function migrate(page: Page, fixture: LegacyFixture, collision: boolean): Promise<void> {
  await consent(page);
  if (collision) await responseFor(page, '/auth/oidc/complete', 'POST', () => page.getByRole('button', { name: '确认身份并进入聊天', exact: true }).click(), 409);
  else await step('migrationOpen', () => openDetails(page, '我有旧账号，保留历史'));
  await step('migrationUsername', () => page.getByLabel('关联旧账号用户名', { exact: true }).fill(fixture.username));
  await step('migrationPassword', () => page.getByLabel('关联旧账号当前密码', { exact: true }).fill(fixture.password));
  await step('migrationConsent', () => page.getByLabel('确认把本次已验证身份关联到我证明的旧账号，保留原账号数据', { exact: true }).check());
  const value = await responseFor<AccountSession>(page, '/auth/oidc/migrate', 'POST', () => page.getByRole('button', { name: '验证旧账号并明确关联', exact: true }).click()); mark('migrationResult'); requireTrue(value.user.id === fixture.userId); value.accessToken = '';
  await retained(page, fixture);
}
async function retained(page: Page, fixture: RetainedFixture): Promise<void> {
  await loggedIn(page); const session = await cookieRead<Session>(page, '/session'); mark('ownerCheck'); requireTrue(session.userId === fixture.userId);
  const messages = await cookieRead<ApiPage<Message>>(page, `/conversations/${encodeURIComponent(fixture.conversationId)}/messages/recent?limit=50`, 'migrationHistory'); requireTrue(messages.items.length === 2 && messages.items[0].role === 'user' && messages.items[1].role === 'assistant' && messages.items.every(item => item.conversationId === fixture.conversationId && item.content.length > 0));
  const wallet = await cookieRead<EnergyWallet>(page, '/energy', 'migrationWallet'); if (fixture.expectedWallet !== undefined) requireTrue(wallet.balance === fixture.expectedWallet);
  await step('chatHome', () => page.getByRole('button', { name: '聊天', exact: true }).click());
}
async function migratePhoneOnly(page: Page, fixture: RetainedFixture): Promise<void> {
  await consent(page); const button = page.getByRole('button', { name: '确认身份并进入聊天', exact: true });
  await step('phoneMigrationReject', () => button.click()); requireTrue(!await page.getByRole('button', { name: '退出登录', exact: true }).isVisible());
  const explicit = page.getByLabel('确认把本次新鲜验证的同一手机号关联到原无密码账号，保留历史并撤销旧登录', { exact: true });
  mark('phoneMigrationUnchecked'); requireTrue(!await explicit.isChecked()); await step('phoneMigrationConsent', () => explicit.check());
  const value = await responseFor<AccountSession>(page, '/auth/oidc/complete', 'POST', () => button.click()); mark('migrationResult'); requireTrue(value.user.id === fixture.userId); value.accessToken = '';
  await retained(page, fixture);
}
async function erase(page: Page, phone: string): Promise<void> {
  const beforeOwner = (await cookieRead<Session>(page, '/session', 'eraseOwner')).userId;
  await accountPage(page); await step('eraseOpen', () => openDetails(page, '托管身份删除业务数据'));
  await step('eraseStart', () => page.getByRole('button', { name: '前往 Logto 重新认证以删除业务数据', exact: true }).click());
  const pending = await callback(page, () => hostedPhone(page, phone, false)); mark('erasePending'); requireTrue(pending.pending?.purpose === 'erase');
  await step('accountHeading', () => page.getByRole('heading', { name: '托管身份与账号安全', exact: true }).waitFor()); const session = await cookieRead<Session>(page, '/session'); mark('erasePending'); requireTrue(session.userId === beforeOwner);
  const eraseButton = page.getByRole('button', { name: '永久删除 AlliraAi 业务数据', exact: true }); await step('eraseNoPhrase', () => eraseButton.click()); requireTrue(await page.getByRole('button', { name: '退出登录', exact: true }).isVisible());
  await step('erasePhrase', () => page.getByLabel('删除业务数据：逐字输入 DELETE MY ACCOUNT', { exact: true }).fill('DELETE MY ACCOUNT')); await step('eraseNoConsent', () => eraseButton.click()); requireTrue(await page.getByRole('button', { name: '退出登录', exact: true }).isVisible());
  await step('eraseConsent', () => page.getByLabel('确认永久删除 AlliraAi 业务数据，理解这不会删除 Logto 全局账号或物理擦除历史备份', { exact: true }).check());
  mark('eraseResponse'); await responseFor(page, '/auth/oidc/erase', 'POST', () => eraseButton.click()); await loggedOut(page);
  const expired = await step('eraseExpired', () => page.request.get(`${origin}${apiRoot}/auth/browser/session`)); requireTrue(expired.status() === 401);
}

// No business live runs before ROOT_READY. This case never posts a generation or changes a service.
test('real local Logto OSS hosted phone/social, explicit legacy migrations, dual-CSRF fresh erasure and public-only PWA', async ({ browser, request }) => {
  test.setTimeout(240_000); const contexts: BrowserContext[] = []; let profile = '', phase = 'Mock 与真实 OSS 配置核验';
  const callbackOnly = process.env.ALLIRA_TEST_OIDC_CALLBACK_ONLY === '1'; let callbackOnlyCompleted = false;
  let legacyA: LegacyFixture | null = null, legacyB: LegacyFixture | null = null, legacyC: RetainedFixture | null = null, fresh: NewIdentities | null = null;
  let generated = 0, invalidHeaders = 0, callbackCount = 0, pageErrors = 0, unexpectedExternal = 0, completeA = 0;
  let completePosts = 0, migratePosts = 0, erasePosts = 0, blockedLoopbackMismatch = 0, blockedOther = 0;
  const blockedResources: Record<ResourceCategory, number> = { document: 0, stylesheet: 0, image: 0, media: 0, font: 0, script: 0, texttrack: 0, xhr: 0, fetch: 0, eventsource: 0, websocket: 0, manifest: 0, other: 0 };
  let failure: { phase: string; substep: string; category: FailureCategory; counts: Record<string, number> } | null = null;
  let cleanupFailure: { substep: string; category: FailureCategory } | null = null, cleanupFailures = 0;
  let transportFailure: FailureCategory | null = null, transportFailures = 0;
  const counts = (): Record<string, number> => ({ actions: diagnostic.actions, owner: diagnostic.owner, smsResponses: diagnostic.smsResponses, smsPolls: diagnostic.smsPolls, freshSmsRecords: diagnostic.freshSmsRecords, digitsFilled: diagnostic.digitsFilled, callbackResponses: diagnostic.callbackResponses, businessResponses: diagnostic.businessResponses, callbackStatus: diagnostic.callbackStatus, callbackMethodEnum: diagnostic.callbackMethodEnum, callbackNavigation: diagnostic.callbackNavigation, callbackMainFrame: diagnostic.callbackMainFrame, callbackFromSW: diagnostic.callbackFromSW, callbackResourceEnum: diagnostic.callbackResourceEnum, callbackRedirectDepth: diagnostic.callbackRedirectDepth, callbackRawResponses: diagnostic.callbackRawResponses, callbackRawStatus: diagnostic.callbackRawStatus, callbackRawMethodEnum: diagnostic.callbackRawMethodEnum, callbackRawNavigation: diagnostic.callbackRawNavigation, callbackRawMainFrame: diagnostic.callbackRawMainFrame, callbackRawFromSW: diagnostic.callbackRawFromSW, callbackRawResourceEnum: diagnostic.callbackRawResourceEnum, callbackRawToPublic: diagnostic.callbackRawToPublic, callbackFinalStatus: diagnostic.callbackFinalStatus, callbackFinalFromSW: diagnostic.callbackFinalFromSW, generated, invalidHeaders, callbackCount, pageErrors, unexpectedExternal, completeA, completePosts, migratePosts, erasePosts, transportFailures, blockedLoopbackMismatch, blockedOther, blockedDocument: blockedResources.document, blockedStylesheet: blockedResources.stylesheet, blockedImage: blockedResources.image, blockedMedia: blockedResources.media, blockedFont: blockedResources.font, blockedScript: blockedResources.script, blockedTexttrack: blockedResources.texttrack, blockedXhr: blockedResources.xhr, blockedFetch: blockedResources.fetch, blockedEventsource: blockedResources.eventsource, blockedWebsocket: blockedResources.websocket, blockedManifest: blockedResources.manifest, blockedResourceOther: blockedResources.other });
  try {
    const info = await step('mockInfo', async () => (await request.get(`${apiRoot}/info`)).json()); requireTrue(info.provider === 'mock' && info.consoleEnabled);
    const options: AuthOptions = await step('authOptions', async () => (await request.get(`${apiRoot}/auth/options`)).json()); requireTrue(options.backend === 'oidc' && options.oidc.enabled && options.oidc.provider === 'logto' && options.oidc.authorizationOrigin === issuerOrigin);
    mark('fixtureRead'); fresh = await newIdentities(process.env.ALLIRA_TEST_OIDC_NEW_PHONES ?? '');
    if (!callbackOnly) {
      legacyA = await legacyFixture(process.env.ALLIRA_TEST_OIDC_LEGACY_A ?? ''); legacyB = await legacyFixture(process.env.ALLIRA_TEST_OIDC_LEGACY_B ?? ''); legacyC = await phoneOnlyFixture(process.env.ALLIRA_TEST_OIDC_LEGACY_C ?? '');
      mark('privateValidate'); requireTrue(new Set([legacyA.phone, legacyB.phone, legacyC.phone, fresh.newPhone, fresh.socialPhone]).size === 5 && new Set([legacyA.userId, legacyB.userId, legacyC.userId]).size === 3);
    }
    profile = await step('profileCreate', () => mkdtemp(join(tmpdir(), 'allira-oidc-pwa-')));
    const main = await step('contextCreate', () => chromium.launchPersistentContext(profile, { channel: 'chromium', headless: true, serviceWorkers: 'allow' })); contexts.push(main);
    for (let index = 0; index < (callbackOnly ? 0 : 4); ++index) contexts.push(await step('contextCreate', () => browser.newContext({ serviceWorkers: 'allow' })));
    const pages: Page[] = [];
    for (const context of contexts) {
      context.setDefaultTimeout(12_000);
      await step('routeBoundary', () => context.route('**/*', async route => {
        try {
          const url = new URL(route.request().url()); if (url.origin === origin || url.origin === issuerOrigin) return await route.continue();
          if (url.origin === 'http://mock-social') {
            const rawRedirect = url.searchParams.get('redirect_uri'), state = url.searchParams.get('state'); requireTrue(rawRedirect && state);
            const redirect = new URL(rawRedirect); requireTrue(redirect.origin === issuerOrigin && !redirect.username && !redirect.password && !redirect.hash);
            redirect.searchParams.set('state', state); redirect.searchParams.set('code', 'official-mock-transport'); redirect.searchParams.set('userId', fresh!.socialSubject);
            // Deliberately no phone/email: real Logto must require and verify a phone before issuing OIDC.
            return await route.fulfill({ status: 302, headers: { location: redirect.href }, body: '' });
          }
          ++unexpectedExternal; ++blockedResources[resourceCategory(route.request().resourceType())];
          const hostname = url.hostname.toLowerCase();
          if (hostname === 'localhost' || hostname.endsWith('.localhost') || hostname === '[::1]' || hostname === '::1' || /^127\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$/.test(hostname)) ++blockedLoopbackMismatch;
          else ++blockedOther;
          return await route.abort('blockedbyclient');
        } catch (error) {
          ++transportFailures; transportFailure ??= failureCategory(error);
          // A route callback is outside the main await chain. Never rethrow its original URL-bearing error.
          try { await route.abort('failed'); } catch { /* category/count already retained; no raw error */ }
        }
      }));
      const page = await step('pageCreate', () => context.newPage()); pages.push(page); page.on('pageerror', () => { ++pageErrors; });
      page.on('request', value => {
        try {
          const url = new URL(value.url()), path = url.pathname, headers = value.headers();
          if (url.origin !== origin) return;
          if (/\/conversations\/[^/]+\/runs$/.test(path) && value.method() === 'POST') ++generated;
          if (path === `${apiRoot}/auth/oidc/callback`) ++callbackCount;
          if (value.method() === 'POST') {
            if (path === `${apiRoot}/auth/oidc/complete`) ++completePosts;
            if (path === `${apiRoot}/auth/oidc/migrate`) ++migratePosts;
            if (path === `${apiRoot}/auth/oidc/erase`) ++erasePosts;
          }
          if (path.startsWith(`${apiRoot}/auth/oidc/`) && value.method() === 'POST') {
            const erasePurpose = path.endsWith('/erase') || path.endsWith('/start') && value.postDataJSON()?.purpose === 'erase';
            if (headers.authorization || !headers['x-allira-oidc-csrf'] || headers.origin !== origin || erasePurpose !== !!headers['x-csrf-token']) ++invalidHeaders;
          }
        } catch (error) { ++transportFailures; transportFailure ??= failureCategory(error); }
      });
    }
    const [page, social, oldA, oldB, oldC] = pages; oldA?.on('request', value => {
      try { if (new URL(value.url()).pathname === `${apiRoot}/auth/oidc/complete` && value.method() === 'POST') ++completeA; }
      catch (error) { ++transportFailures; transportFailure ??= failureCategory(error); }
    });

    phase = callbackOnly ? '首 owner 回调诊断（非完整验收）' : '官方 hosted 手机无密码注册与明确普通 Cookie'; diagnostic.owner = 1;
    await pendingLogin(page, fresh.newPhone, true);
    if (callbackOnly) {
      mark('finalCounters'); requireTrue(generated === 0 && invalidHeaders === 0 && pageErrors === 0 && unexpectedExternal === 0 && callbackCount === 1 && completePosts === 0 && migratePosts === 0 && erasePosts === 0 && transportFailures === 0);
      callbackOnlyCompleted = true; return; // finally closes the sole context/profile and reports only numeric diagnostics.
    }
    mark('privateValidate'); requireTrue(legacyA && legacyB && legacyC);
    const mainId = await complete(page); mark('ownerCheck'); requireTrue(mainId !== legacyA.userId && mainId !== legacyB.userId);
    await step('reload', () => page.reload()); await loggedIn(page); const restored = await cookieRead<Session>(page, '/session'); mark('ownerCheck'); requireTrue(restored.userId === mainId);
    await accountPage(page); for (const label of ['当前密码', '签发恢复码的当前密码', '删除账号的当前密码', '账号安全六位验证码', '绑定手机号']) { mark('accountLocalHidden'); requireTrue(!await page.getByLabel(label, { exact: true }).isVisible()); }
    mark('accountControls'); requireTrue(await page.getByRole('button', { name: '读取登录设备', exact: true }).isVisible() && await page.getByRole('button', { name: '下载账号完整数据', exact: true }).isVisible());

    phase = '未绑旧账号在首次 pending 直接证明迁移，不先创建新账号'; diagnostic.owner = 2;
    await pendingLogin(oldA, legacyA.phone, true); await migrate(oldA, legacyA, false); mark('migrationResult'); requireTrue(completeA === 0);

    phase = '已绑同手机号旧账号碰撞后明确证明迁移'; diagnostic.owner = 3;
    await pendingLogin(oldB, legacyB.phone, true); await migrate(oldB, legacyB, true);

    phase = '原无密码手机号账号经专用新鲜用途与独立确认迁移'; diagnostic.owner = 4;
    await pendingLogin(oldC, legacyC.phone, true, 'phone_migration'); await migratePhoneOnly(oldC, legacyC);

    phase = '官方 mock-social 由真实 Logto 强制补手机后才返回'; diagnostic.owner = 5;
    await step('pageOpen', () => social.goto(`${origin}/test/`)); await step('socialStart', () => social.getByRole('button', { name: '使用手机号登录 / 注册', exact: true }).click()); await step('hostedWait', () => social.waitForURL(url => url.origin === issuerOrigin && url.pathname === '/sign-in'));
    const callbackBefore = callbackCount; await step('socialButton', () => social.locator('button').filter({ hasText: 'Continue with Mock Social' }).click()); await step('socialPhonePage', () => social.waitForURL(url => url.origin === issuerOrigin && url.pathname === '/continue/phone'));
    mark('socialNoEarlyCallback'); requireTrue(callbackCount === callbackBefore && !await social.getByRole('button', { name: '退出登录', exact: true }).isVisible());
    const socialPending = await callback(social, async () => {
      await chinesePhone(social, fresh!.socialPhone); const before = await smsBefore(), sentAt = Date.now();
      const issued = observed(social.waitForResponse(response => new URL(response.url()).origin === issuerOrigin && new URL(response.url()).pathname === '/api/experience/verification/verification-code' && response.request().method() === 'POST'));
      await step('smsSend', () => social.getByRole('button', { name: 'Continue', exact: true }).click()); await enterOfficialSms(social, fresh!.socialPhone, 'Register', issued, sentAt, before);
    });
    mark('pendingValidate'); requireTrue(socialPending.pending?.purpose === 'login'); const socialId = await complete(social); mark('socialResult'); requireTrue(![mainId, legacyA.userId, legacyB.userId, legacyC.userId].includes(socialId));

    phase = '六项公开缓存与实际离线私有会话边界'; diagnostic.owner = 1;
    await step('pwaReady', () => page.evaluate(async () => { await Promise.race([(async () => { await navigator.serviceWorker.ready; if (!navigator.serviceWorker.controller) await new Promise<void>(resolve => navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true })); })(), new Promise<never>((_, reject) => setTimeout(() => reject(new Error('公开壳未就绪。')), 8000))]); }));
    const paths = await step('pwaKeys', () => page.evaluate(async () => { const result: string[] = []; for (const name of await caches.keys()) for (const entry of await (await caches.open(name)).keys()) result.push(new URL(entry.url).pathname); return result.sort(); }));
    mark('pwaAllowlist'); requireTrue(paths.length === 6 && new Set(paths).size === 6 && paths.includes('/test/') && paths.includes('/test/manifest.webmanifest') && paths.filter(path => /^\/console-assets\/public-chat-[A-Z0-9]+\.(js|css)$/.test(path)).length === 2 && paths.filter(path => /^\/console-assets\/public-icon-(192|512)-[a-f0-9]{16}\.png$/.test(path)).length === 2);
    const sizes = await step('pwaIcons', () => page.evaluate(async () => { const manifest = await (await fetch('/test/manifest.webmanifest', { credentials: 'omit' })).json(); const values: string[] = []; for (const icon of manifest.icons) { const image = new Image(); image.src = icon.src; await image.decode(); if (`${image.naturalWidth}x${image.naturalHeight}` !== icon.sizes) throw new Error('公开图标尺寸不符。'); values.push(icon.sizes); } return values.sort().join(','); })); requireTrue(sizes === '192x192,512x512');
    const cdp = await step('pwaCdp', () => main.newCDPSession(page)); await step('pwaCdp', () => cdp.send('Page.enable'));
    const installation = await step('pwaInstall', () => cdp.send('Page.getInstallabilityErrors')); requireTrue(installation.installabilityErrors.length === 0);
    await step('pwaOffline', () => main.setOffline(true)); await step('pwaOffline', () => cdp.send('Network.enable')); await step('pwaOffline', () => cdp.send('Network.overrideNetworkState', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 }));
    requireTrue(await step('pwaPrivateFail', () => page.evaluate(async () => { try { await fetch('/api/v1/auth/oidc/session', { cache: 'no-store', signal: AbortSignal.timeout(2000) }); return false; } catch { return true; } })));
    await step('pwaReload', () => page.reload({ waitUntil: 'domcontentloaded' })); mark('pwaNoIdentity'); requireTrue(!await page.getByRole('button', { name: '退出登录', exact: true }).isVisible());
    await step('pwaOnline', () => main.setOffline(false)); await step('pwaOnline', () => cdp.send('Network.overrideNetworkState', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 })); await step('pwaRestore', () => page.reload()); await loggedIn(page);
    const afterOffline = await cookieRead<Session>(page, '/session', 'pwaRestore'); requireTrue(afterOffline.userId === mainId);

    phase = '五类业务账号均经 fresh hosted 重认证与双 CSRF 明确删除';
    diagnostic.owner = 2; await erase(oldA, legacyA.phone); diagnostic.owner = 3; await erase(oldB, legacyB.phone); diagnostic.owner = 4; await erase(oldC, legacyC.phone); diagnostic.owner = 5; await erase(social, fresh.socialPhone); diagnostic.owner = 1; await erase(page, fresh.newPhone);
    mark('finalCounters'); requireTrue(generated === 0 && invalidHeaders === 0 && pageErrors === 0 && unexpectedExternal === 0 && callbackCount === 10 && completeA === 0 && transportFailures === 0);
    for (const item of pages) requireTrue(await step('finalStorage', () => item.evaluate(() => localStorage.length + sessionStorage.length)) === 0);
  } catch (error) {
    // Snapshot only safe values before finally changes the substep. Never store the original Error.
    failure = { phase, substep: diagnostic.substep, category: failureCategory(error), counts: counts() };
  } finally {
    for (const item of [legacyA, legacyB]) if (item) { item.password = ''; item.username = ''; item.userId = ''; item.conversationId = ''; item.phone = ''; }
    if (legacyC) { legacyC.userId = ''; legacyC.conversationId = ''; legacyC.phone = ''; }
    if (fresh) { fresh.newPhone = ''; fresh.socialPhone = ''; fresh.socialSubject = ''; } legacyA = null; legacyB = null; legacyC = null; fresh = null;
    const closed = await step('cleanupContexts', () => Promise.allSettled(contexts.map(context => context.close())));
    for (const result of closed) if (result.status === 'rejected') { ++cleanupFailures; cleanupFailure ??= { substep: substeps.cleanupContexts, category: failureCategory(result.reason) }; }
    if (profile) {
      try {
        mark('cleanupProfilePath'); const target = await realpath(profile), parent = await realpath(tmpdir()); requireTrue(dirname(target) === parent && basename(target).startsWith('allira-oidc-pwa-'));
        await step('cleanupProfileRemove', () => rm(target, { recursive: true, force: true }));
      } catch (error) { ++cleanupFailures; cleanupFailure ??= { substep: diagnostic.substep, category: failureCategory(error) }; }
    }
    // Keep this check in finally: the diagnostic-only return must never conceal a cleanup failure.
    if (failure || cleanupFailure || transportFailure) {
      // All interpolated strings below originate from static labels or the category allowlist.
      const summary = failure ?? { phase: '安全运输或清理', substep: cleanupFailure?.substep ?? substeps.routeBoundary, category: cleanupFailure?.category ?? transportFailure!, counts: counts() };
      throw new Error(`实际 Logto OSS 验收阶段失败：${summary.phase}；子阶段：${summary.substep}；类别：${summary.category}；安全计数：${JSON.stringify(summary.counts)}；清理失败数：${cleanupFailures}；清理类别：${cleanupFailure?.category ?? 'none'}；运输类别：${transportFailure ?? 'none'}。原始 message/stack、HTML、URL、手机号及凭据均未记录。`);
    }
    if (callbackOnlyCompleted) console.log(`首 owner 回调诊断完成；非完整链路验收；安全数字计数：${JSON.stringify(counts())}`);
  }
});
