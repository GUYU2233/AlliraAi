import { test, chromium } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { constants } from 'node:fs';
import { lstat, open, realpath } from 'node:fs/promises';
import { dirname, isAbsolute } from 'node:path';
import type { BrowserContext, Page, Request } from '@playwright/test';
import type { AdminDiagnostics, AdminRouteOverview, AdminUser, ApiInfo, AuthOptions, Page as ApiPage, RevokeSessionsResult, Session } from '../../../packages/client/src/index.ts';

// No webServer, model call, Logto request or database inspection. Root owns deployment and cleanup.
test.skip(process.env.ALLIRA_CONSOLE_LIVE !== '1', '仅在 root 备份并部署现有单一 Mock API、关闭身份通道且明确 ROOT_READY 后执行约定的一次管理验收');
const origin = 'http://127.0.0.1:8000';
const apiRoot = '/api/v1';
type Probe = { id: string; type: 'bearer'; accessToken: string };
type Fixture = {
  primary: { userId: string; identityStatus: 'oidc'; walletBalance: number; conversationCount: number; username: string; maskedPhone: string; probeSessions: [Probe, Probe]; browserSessionId: string };
  secondary: { userId: string; identityStatus: 'oidc'; probeSessions: [Probe] };
  developer: { userId: string };
};
type Category = 'timeout' | 'strict_locator' | 'invariant' | 'json_parse' | 'io' | 'aborted' | 'unknown';
class SafeFailure extends Error { constructor(readonly category: Category = 'invariant') { super('脱敏管理验收条件不满足。'); } }
const steps = {
  begin: '尚未开始', mock: '门禁：现有 Mock API', channels: '门禁：认证与模拟渠道关闭', fixture: '私有 fixture：权限与字段', cli: '独立管理：隔离 CLI 签发', browser: '浏览器：独立 context 与同源边界', loginOpen: '独立管理：打开一次性链接并清除地址凭据', loginClick: '独立管理：显式消费一次性链接', restore: '独立管理：Cookie 恢复', users: '用户：脱敏身份与业务摘要', filter: '用户：身份与字面查询筛选', devices: '设备：显式读取本地会话', revokeOne: '设备：确认撤销单个登录', probeOne: '设备：单个撤销后的隔离核验', revokeAll: '设备：确认撤销所有本地登录', probeAll: '设备：全部撤销后的隔离核验', preserved: '用户：核对聊天数量与能量保留', routes: '路由：只读配置摘要', diagnostics: '诊断：认证门禁和独立备份边界', logout: '独立管理：注销与立即清理', expired: '独立管理：注销后 Cookie 失效', counters: '最终：私有内容与请求边界', cleanup: '清理：关闭本次 context',
} as const;
let currentStep: string = steps.begin;
const numbers = { actions: 0, pageErrors: 0, external: 0, invalidHeaders: 0, prohibitedRequests: 0, generationPosts: 0, loginPosts: 0, logoutPosts: 0, singlePosts: 0, allPosts: 0, deviceGets: 0, privateMessageGets: 0, probeReads: 0, successfulProbes: 0, rejectedProbes: 0, cleanupFailures: 0 };
function requireTrue(value: unknown): asserts value { if (!value) throw new SafeFailure(); }
function category(error: unknown): Category {
  if (error instanceof SafeFailure) return error.category;
  if (!(error instanceof Error)) return 'unknown';
  if (error.name === 'TimeoutError') return 'timeout';
  if (error.name === 'SyntaxError') return 'json_parse';
  if (error.name === 'AbortError') return 'aborted';
  if (error.message.includes('strict mode violation')) return 'strict_locator';
  if ('code' in error && typeof error.code === 'string' && ['ENOENT', 'EACCES', 'EPERM', 'ELOOP', 'ENOTDIR', 'EISDIR', 'EMFILE', 'EBADF', 'EIO', 'ESTALE', 'EINVAL'].includes(error.code)) return 'io';
  return 'unknown';
}
async function at<T>(key: keyof typeof steps, action: () => Promise<T>): Promise<T> { currentStep = steps[key]; const result = await action(); ++numbers.actions; return result; }
function observed<T>(promise: Promise<T>): Promise<T> { void promise.catch(() => {}); return promise; }
async function fixtureFile(path: string): Promise<Fixture> {
  requireTrue(isAbsolute(path) && await realpath(path) === path);
  const before = await lstat(path), parent = await lstat(dirname(path));
  requireTrue(before.isFile() && before.nlink === 1 && before.uid === process.getuid?.() && (before.mode & 0o777) === 0o600 && before.size > 0 && before.size <= 32768);
  requireTrue(parent.isDirectory() && parent.uid === before.uid && (parent.mode & 0o777) === 0o700);
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  let bytes: Buffer | undefined;
  try {
    const opened = await handle.stat(); requireTrue(opened.ino === before.ino && opened.dev === before.dev);
    bytes = await handle.readFile(); const value = JSON.parse(bytes.toString('utf8')) as Fixture;
    const string = (item: unknown, maximum = 256): item is string => typeof item === 'string' && item.length > 0 && item.length <= maximum;
    requireTrue(value && value.primary && value.secondary && value.developer);
    for (const user of [value.primary, value.secondary, value.developer]) requireTrue(string(user.userId));
    requireTrue(new Set([value.primary.userId, value.secondary.userId, value.developer.userId]).size === 3);
    requireTrue(value.primary.identityStatus === 'oidc' && value.secondary.identityStatus === 'oidc' && string(value.primary.username) && string(value.primary.browserSessionId));
    requireTrue(string(value.primary.maskedPhone, 32) && /^[+\d* -]{4,32}$/.test(value.primary.maskedPhone) && value.primary.maskedPhone.includes('*'));
    requireTrue(Number.isSafeInteger(value.primary.walletBalance) && Number.isSafeInteger(value.primary.conversationCount) && value.primary.conversationCount > 0);
    requireTrue(Array.isArray(value.primary.probeSessions) && value.primary.probeSessions.length === 2 && Array.isArray(value.secondary.probeSessions) && value.secondary.probeSessions.length === 1);
    const probes = [...value.primary.probeSessions, ...value.secondary.probeSessions];
    for (const probe of probes) requireTrue(probe.type === 'bearer' && string(probe.id) && string(probe.accessToken, 4096) && !/\s/.test(probe.accessToken));
    requireTrue(new Set(probes.map(item => item.id)).size === 3 && new Set(probes.map(item => item.accessToken)).size === 3);
    requireTrue(!probes.some(item => item.id === value.primary.browserSessionId));
    return value;
  } finally { bytes?.fill(0); await handle.close(); }
}
function issueLink(): string {
  // Deliberately do not inherit process.env: the CLI neither needs nor reads protected identity/model settings.
  const output = execFileSync('/opt/allira/workspace/server/.venv/bin/python', ['-m', 'allira.admin_cli', '--env-file', '/dev/null', 'issue-login', '--base-url', origin, '--json'], {
    cwd: '/opt/allira/workspace/server', timeout: 15000, maxBuffer: 16384, stdio: ['ignore', 'pipe', 'pipe'],
    env: { PATH: '/opt/allira/tools/node24/bin:/usr/local/bin:/usr/bin:/bin', HOME: process.env.HOME ?? '/home/allira', LANG: 'C.UTF-8', ALLIRA_ENVIRONMENT: 'development', ALLIRA_DATABASE_PATH: '/var/lib/allira/dev.sqlite3', ALLIRA_AUTH_BACKEND: 'oidc', ALLIRA_ADMIN_ENABLED: 'true', ALLIRA_OIDC_ENABLED: 'false', ALLIRA_PROVIDER: 'mock' },
  });
  try {
    const value = JSON.parse(output.toString('utf8')) as { url?: unknown };
    requireTrue(typeof value.url === 'string'); const url = new URL(value.url);
    requireTrue(url.origin === origin && url.pathname === '/admin/' && !url.username && !url.password && !url.search && /^#code=[A-Za-z0-9_-]{20,200}$/.test(url.hash));
    return url.href;
  } finally { output.fill(0); }
}
async function readPublic<T>(path: string): Promise<T> {
  const response = await fetch(`${origin}${apiRoot}${path}`, { headers: { Accept: 'application/json' }, redirect: 'error', signal: AbortSignal.timeout(10000) });
  requireTrue(response.ok); return await response.json() as T;
}
async function responseFor<T>(page: Page, path: string, method: string, action: () => Promise<unknown>, status = 200): Promise<T> {
  const waiting = observed(page.waitForResponse(response => new URL(response.url()).origin === origin && new URL(response.url()).pathname === `${apiRoot}${path}` && response.request().method() === method));
  await action(); const response = await waiting; requireTrue(response.status() === status); return await response.json() as T;
}
async function probe(proof: Probe, expected: 200 | 401, owner: string): Promise<void> {
  const response = await fetch(`${origin}${apiRoot}/session`, { headers: { Accept: 'application/json', Authorization: `Bearer ${proof.accessToken}` }, redirect: 'error', signal: AbortSignal.timeout(10000) });
  ++numbers.probeReads; requireTrue(response.status === expected);
  if (expected === 200) { const value = await response.json() as Session; requireTrue(value.userId === owner); ++numbers.successfulProbes; }
  else { await response.body?.cancel(); ++numbers.rejectedProbes; }
}
const userRow = (page: Page, owner: string) => page.getByRole('table', { name: '用户身份与业务摘要', exact: true }).getByRole('row').filter({ has: page.getByRole('cell', { name: owner, exact: true }) });

// Actual admin mutations are exactly one single-session revoke, one all-session revoke, and logout.
test('real single Mock API admin controls preserve business data and isolate local session revocation', async () => {
  test.setTimeout(90000);
  let context: BrowserContext | undefined, fixture: Fixture | undefined, link = '', failure: string | null = null;
  let csrf = '', transportFailures = 0;
  let deviceOrder: string[] = [];
  try {
    const info = await at('mock', () => readPublic<ApiInfo>('/info')); requireTrue(info.provider === 'mock' && info.environment === 'development' && info.adminEnabled === true);
    const options = await at('channels', () => readPublic<AuthOptions>('/auth/options'));
    requireTrue(options.backend === 'oidc' && options.oidc?.enabled === false && options.oidc.simulated === false && options.phone.enabled === false && options.social.every(item => item.enabled === false && item.simulated === false));
    fixture = await at('fixture', () => fixtureFile(process.env.ALLIRA_TEST_ADMIN_CONTROL_FIXTURE ?? ''));
    await at('probeOne', () => probe(fixture!.primary.probeSessions[0], 200, fixture!.primary.userId));
    await at('probeOne', () => probe(fixture!.primary.probeSessions[1], 200, fixture!.primary.userId));
    await at('probeOne', () => probe(fixture!.secondary.probeSessions[0], 200, fixture!.secondary.userId));
    link = await at('cli', async () => issueLink());
    context = await at('browser', () => chromium.launchPersistentContext('', { channel: 'chromium', headless: true, viewport: { width: 1100, height: 800 }, serviceWorkers: 'block', acceptDownloads: false }));
    // Chromium's temporary profile is owned and removed by Playwright on close; no user profile is opened.
    await context.route('**/*', async route => {
      try {
        const url = new URL(route.request().url());
        if (url.origin !== origin) { ++numbers.external; await route.abort(); return; }
        if (url.pathname.startsWith(`${apiRoot}/`) && !url.pathname.startsWith(`${apiRoot}/admin/`)) { ++numbers.prohibitedRequests; await route.abort(); return; }
        await route.continue();
      } catch { ++transportFailures; await route.abort().catch(() => {}); }
    });
    const page = context.pages()[0] ?? await context.newPage();
    page.on('pageerror', () => { ++numbers.pageErrors; });
    page.on('request', (request: Request) => {
      try {
        const url = new URL(request.url()); if (url.origin !== origin || !url.pathname.startsWith(`${apiRoot}/`)) return;
        const path = url.pathname.slice(apiRoot.length), method = request.method(), headers = request.headers();
        if (/\/runs$/.test(path) && method === 'POST') ++numbers.generationPosts;
        if (/\/messages(?:\/recent)?$/.test(path)) ++numbers.privateMessageGets;
        if (/^\/admin\/users\/[^/]+\/sessions$/.test(path)) ++numbers.deviceGets;
        const wrongIdentityProof = headers.authorization || headers['x-allira-oidc-csrf'];
        if (wrongIdentityProof) ++numbers.invalidHeaders;
        if (method !== 'GET') {
          if (path === '/admin/session') ++numbers.loginPosts;
          else {
            if (headers.origin !== origin || !csrf || headers['x-csrf-token'] !== csrf) ++numbers.invalidHeaders;
            if (/^\/admin\/users\/[^/]+\/sessions\/[^/]+\/revoke$/.test(path)) ++numbers.singlePosts;
            else if (/^\/admin\/users\/[^/]+\/revoke-sessions$/.test(path)) ++numbers.allPosts;
            else if (path === '/admin/logout') ++numbers.logoutPosts;
            else ++numbers.prohibitedRequests;
          }
        }
      } catch { ++transportFailures; }
    });
    await at('loginOpen', async () => {
      await page.goto(link); link = '';
      await page.waitForFunction(() => location.pathname === '/admin/' && location.hash === '');
      await page.getByRole('heading', { name: '独立管理登录', exact: true }).waitFor();
      const login = page.getByRole('button', { name: '登录管理', exact: true }); await login.waitFor(); await page.waitForFunction(() => { const button = [...document.querySelectorAll('button')].find(item => item.textContent === '登录管理'); return button && !button.disabled; });
      requireTrue(numbers.loginPosts === 0);
    });
    await at('loginClick', async () => {
      const session = await responseFor<{ authenticated: true; csrfToken: string }>(page, '/admin/session', 'POST', () => page.getByRole('button', { name: '登录管理', exact: true }).click());
      requireTrue(session.authenticated === true && typeof session.csrfToken === 'string'); csrf = session.csrfToken; session.csrfToken = '';
      await page.getByRole('heading', { name: '概览', exact: true }).waitFor(); requireTrue(numbers.loginPosts === 1);
    });
    await at('restore', async () => {
      const session = await responseFor<{ authenticated: true; csrfToken: string }>(page, '/admin/session', 'GET', () => page.reload());
      requireTrue(session.authenticated === true && typeof session.csrfToken === 'string'); csrf = session.csrfToken; session.csrfToken = '';
      await page.getByRole('heading', { name: '概览', exact: true }).waitFor(); requireTrue(numbers.loginPosts === 1);
    });
    const filter = async (query: string, identity: string): Promise<ApiPage<AdminUser>> => {
      await page.getByLabel('搜索用户', { exact: true }).fill(query); await page.getByLabel('身份类型', { exact: true }).selectOption(identity);
      const result = await responseFor<ApiPage<AdminUser>>(page, '/admin/users', 'GET', () => page.getByRole('button', { name: '筛选用户', exact: true }).click());
      if (result.items.length) await userRow(page, result.items[0].userId).waitFor();
      else await page.getByText('暂无记录。', { exact: true }).waitFor();
      return result;
    };
    await at('users', async () => {
      await responseFor<ApiPage<AdminUser>>(page, '/admin/users', 'GET', () => page.getByRole('button', { name: '用户', exact: true }).click());
      requireTrue(numbers.deviceGets === 0 && numbers.privateMessageGets === 0);
    });
    await at('filter', async () => {
      const oidc = await filter(fixture!.primary.userId, 'oidc'); requireTrue(oidc.items.length === 1 && oidc.items[0].userId === fixture!.primary.userId && oidc.items[0].identityStatus === 'oidc');
      const row = userRow(page, fixture!.primary.userId); await row.waitFor(); const text = await row.textContent(); requireTrue(text?.includes(fixture!.primary.maskedPhone) && text.includes(String(fixture!.primary.walletBalance)));
      requireTrue(oidc.items[0].walletBalance === fixture!.primary.walletBalance && oidc.items[0].conversationCount === fixture!.primary.conversationCount);
      const developer = await filter(fixture!.developer.userId, 'developer'); requireTrue(developer.items.length === 1 && developer.items[0].userId === fixture!.developer.userId && developer.items[0].identityStatus === 'developer');
      const literal = await filter('%_\\', 'all'); requireTrue(literal.items.length === 0);
      const originalName = await filter(fixture!.primary.username, 'oidc'); requireTrue(originalName.items.some(item => item.userId === fixture!.primary.userId));
      await filter(fixture!.primary.userId, 'oidc');
    });
    const detail = page.getByLabel('用户设备会话', { exact: true });
    await at('devices', async () => {
      requireTrue(numbers.deviceGets === 0);
      const listed = await responseFor<ApiPage<{ id: string; type: 'bearer' | 'browser' }>>(page, `/admin/users/${encodeURIComponent(fixture!.primary.userId)}/sessions`, 'GET', () => userRow(page, fixture!.primary.userId).getByRole('button', { name: '查看设备', exact: true }).click());
      requireTrue(listed.nextCursor === null && listed.items.length === 3); deviceOrder = listed.items.map(item => item.id);
      await detail.getByText('已显示 3 个有效本地设备。', { exact: true }).waitFor();
      requireTrue(listed.items.some(item => item.id === fixture!.primary.browserSessionId && item.type === 'browser'));
      requireTrue(fixture!.primary.probeSessions.every(proof => listed.items.some(item => item.id === proof.id && item.type === 'bearer')));
      requireTrue((await detail.textContent())?.includes('浏览器 Cookie') && (await detail.textContent())?.includes('内存 Bearer'));
    });
    const first = fixture.primary.probeSessions[0];
    await at('revokeOne', async () => {
      // IDs are not rendered: use the order returned by the explicit UI read, never manufacture a Cookie from a device ID.
      const index = deviceOrder.indexOf(first.id); requireTrue(index >= 0);
      const row = detail.getByRole('table').getByRole('row').nth(index + 1), go = row.getByRole('button', { name: '撤销此设备登录', exact: true });
      requireTrue(await go.isDisabled()); await row.getByLabel('确认仅撤销此设备的本地登录', { exact: true }).check();
      const refreshed = observed(page.waitForResponse(response => new URL(response.url()).pathname === `${apiRoot}/admin/users/${encodeURIComponent(fixture!.primary.userId)}/sessions` && response.request().method() === 'GET'));
      const result = await responseFor<RevokeSessionsResult>(page, `/admin/users/${encodeURIComponent(fixture!.primary.userId)}/sessions/${encodeURIComponent(first.id)}/revoke`, 'POST', () => go.click()); requireTrue(result.revoked === 1);
      const fresh = await (await refreshed).json() as ApiPage<{ id: string }>; requireTrue(fresh.items.length === 2 && !fresh.items.some(item => item.id === first.id));
      await detail.getByText('已显示 2 个有效本地设备。', { exact: true }).waitFor();
    });
    await at('probeOne', async () => {
      await probe(first, 401, fixture!.primary.userId); await probe(fixture!.primary.probeSessions[1], 200, fixture!.primary.userId); await probe(fixture!.secondary.probeSessions[0], 200, fixture!.secondary.userId);
    });
    await at('revokeAll', async () => {
      const row = userRow(page, fixture!.primary.userId), go = row.getByRole('button', { name: '撤销用户登录', exact: true }); requireTrue(await go.isDisabled());
      await row.getByLabel('确认撤销此用户当前全部登录', { exact: true }).check();
      const refreshed = observed(page.waitForResponse(response => new URL(response.url()).pathname === `${apiRoot}/admin/users/${encodeURIComponent(fixture!.primary.userId)}/sessions` && response.request().method() === 'GET'));
      const result = await responseFor<RevokeSessionsResult>(page, `/admin/users/${encodeURIComponent(fixture!.primary.userId)}/revoke-sessions`, 'POST', () => go.click()); requireTrue(result.revoked === 2);
      const fresh = await (await refreshed).json() as ApiPage<unknown>; requireTrue(fresh.items.length === 0);
      await detail.getByText('已显示 0 个有效本地设备。', { exact: true }).waitFor();
    });
    await at('probeAll', async () => {
      await probe(first, 401, fixture!.primary.userId); await probe(fixture!.primary.probeSessions[1], 401, fixture!.primary.userId); await probe(fixture!.secondary.probeSessions[0], 200, fixture!.secondary.userId);
    });
    await at('preserved', async () => {
      const result = await filter(fixture!.primary.userId, 'oidc'); requireTrue(result.items.length === 1 && result.items[0].walletBalance === fixture!.primary.walletBalance && result.items[0].conversationCount === fixture!.primary.conversationCount && result.items[0].activeSessions === 0 && result.items[0].identityStatus === 'oidc');
    });
    await at('routes', async () => {
      const value = await responseFor<AdminRouteOverview>(page, '/admin/routes', 'GET', () => page.getByRole('button', { name: '路由配置', exact: true }).click());
      requireTrue(value.configurationOnly === true && value.items.length > 0 && value.limits.maxConcurrentRuns > 0);
      const output = page.getByLabel('只读路由配置', { exact: true }); await output.getByText('仅配置摘要（configurationOnly=true），未调用模型，不是可用性、额度、报价或容量验证。', { exact: true }).waitFor(); requireTrue(await output.getByRole('textbox').count() === 0);
    });
    await at('diagnostics', async () => {
      const value = await responseFor<AdminDiagnostics>(page, '/admin/diagnostics', 'GET', () => page.getByRole('button', { name: '健康诊断', exact: true }).click());
      requireTrue(value.identity?.backend === 'oidc' && value.identity.enabled === false && value.identity.simulated === false && value.identity.serviceProbe === 'not_performed' && value.identity.administration === 'separate' && value.identityBackup?.checksumVerified === false);
      const output = page.getByLabel('脱敏健康诊断', { exact: true }); await output.getByRole('heading', { name: '身份数据库（Logto PostgreSQL）', exact: true }).waitFor();
      requireTrue((await output.textContent())?.includes('业务数据库（SQLite）') && (await output.textContent())?.includes('未验证（checksumVerified=false）'));
      if (value.identityBackup.restoreStatus === 'recorded') requireTrue((await output.textContent())?.includes('仅有历史报告（recorded），本次未验证恢复'));
    });
    await at('logout', async () => {
      await responseFor<{ ok: true }>(page, '/admin/logout', 'POST', async () => {
        await page.getByRole('button', { name: '注销管理', exact: true }).click(); requireTrue(await page.getByLabel('管理内容').textContent() === '');
      }); csrf = '';
    });
    await at('expired', async () => {
      const response = observed(page.waitForResponse(response => new URL(response.url()).pathname === `${apiRoot}/admin/session` && response.request().method() === 'GET'));
      await page.getByRole('button', { name: '检查已有管理登录', exact: true }).click(); requireTrue((await response).status() === 401); await page.getByRole('heading', { name: '独立管理登录', exact: true }).waitFor(); requireTrue(await page.getByLabel('管理内容').textContent() === '');
    });
    await at('counters', async () => {
      requireTrue(numbers.pageErrors === 0 && numbers.external === 0 && numbers.invalidHeaders === 0 && numbers.prohibitedRequests === 0 && numbers.generationPosts === 0 && numbers.privateMessageGets === 0 && transportFailures === 0);
      requireTrue(numbers.loginPosts === 1 && numbers.logoutPosts === 1 && numbers.singlePosts === 1 && numbers.allPosts === 1 && numbers.probeReads === 9 && numbers.successfulProbes === 6 && numbers.rejectedProbes === 3);
      requireTrue(await page.evaluate(() => localStorage.length + sessionStorage.length === 0));
      const rendered = await page.textContent('body'); requireTrue(!csrf || !rendered?.includes(csrf));
      for (const item of [...fixture!.primary.probeSessions, ...fixture!.secondary.probeSessions]) requireTrue(!rendered?.includes(item.accessToken));
    });
  } catch (error) { failure = `实际管理验收失败；阶段：${currentStep}；类别：${category(error)}；安全数字计数：${JSON.stringify({ ...numbers, transportFailures })}。原始错误、账号、设备 ID、凭据、路径与响应均已隐藏。`; }
  finally {
    link = ''; csrf = ''; deviceOrder.fill(''); deviceOrder = [];
    if (fixture) { for (const item of [...fixture.primary.probeSessions, ...fixture.secondary.probeSessions]) { item.accessToken = ''; item.id = ''; } fixture.primary.username = ''; fixture.primary.maskedPhone = ''; fixture.primary.browserSessionId = ''; fixture.primary.userId = ''; fixture.secondary.userId = ''; fixture.developer.userId = ''; fixture = undefined; }
    try { currentStep = steps.cleanup; await context?.close(); } catch { ++numbers.cleanupFailures; if (!failure) failure = `实际管理验收失败；阶段：${steps.cleanup}；类别：unknown；安全数字计数：${JSON.stringify(numbers)}。原始错误已隐藏。`; }
    if (failure) throw new Error(failure);
  }
});
