import { test, expect, chromium } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { openAdvanced, openChatAdvanced, openDetails } from './ui.ts';

test.skip(process.env.ALLIRA_CONSOLE_LIVE !== '1', '需 root 明确确认现有服务已部署为 Mock');

test('existing Mock API: accounts, roles/memory/import, routing, energy/CDK and session revocation', async ({ browser, request }) => {
  test.setTimeout(90_000);
  expect(await (await request.get('/api/v1/info')).json()).toMatchObject({ provider: 'mock', consoleEnabled: true, adminEnabled: true });
  const context = await browser.newContext();
  const second = await browser.newContext();
  const adminContext = await browser.newContext();
  for (const item of [context, second, adminContext]) item.setDefaultTimeout(10_000);
  const page = await context.newPage();
  const other = await second.newPage();
  const admin = await adminContext.newPage();
  const username = `mvp_${Date.now()}`;
  let password = randomUUID();
  let replacement = randomUUID();
  let code = '';
  let phase = '注册';
  let runId = '';
  const pageErrors: string[] = [];
  for (const item of [page, other, admin]) item.on('pageerror', error => pageErrors.push(error.name));
  page.on('response', async response => {
    if (/\/conversations\/[^/]+\/runs$/.test(new URL(response.url()).pathname) && response.request().method() === 'POST' && response.ok()) runId = (await response.json()).id;
  });
  const login = async (target: typeof page, value: string) => {
    await target.goto('http://127.0.0.1:8000/test/');
    await target.getByLabel('账号用户名', { exact: true }).fill(username);
    await target.getByLabel('账号密码（10–128 字符，最多 256 UTF-8 字节）', { exact: true }).fill(value);
    await target.getByRole('button', { name: '账号登录', exact: true }).click();
    await expect(target.getByRole('button', { name: '聊天', exact: true })).toBeVisible(); await openAdvanced(target);
  };
  try {
    await page.goto('http://127.0.0.1:8000/test/');
    await page.getByLabel('账号用户名', { exact: true }).fill(username);
    await page.getByLabel('账号密码（10–128 字符，最多 256 UTF-8 字节）', { exact: true }).fill(password);
    await page.getByLabel('账号显示名称（注册）', { exact: true }).fill('MVP 浏览器账号');
    await page.getByLabel('我已满 18 岁', { exact: true }).check();
    await page.getByLabel('我已阅读并接受当前版本协议（注册必选）', { exact: true }).check();
    await page.getByRole('button', { name: '注册账号', exact: true }).click();
    await expect(page.getByRole('button', { name: '聊天', exact: true })).toBeVisible(); await openAdvanced(page);
    await login(other, password);
    await openDetails(page, '自定义新会话'); await page.getByLabel('新会话标题', { exact: true }).fill('MVP 正常会话');
    await page.getByRole('button', { name: '新建会话', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'MVP 正常会话', exact: true })).toBeVisible();

    phase = '角色与记忆';
    await page.getByRole('button', { name: '角色与记忆', exact: true }).click();
    await expect(page.getByRole('button', { name: '保存当前会话记忆', exact: true })).toBeEnabled();
    await page.getByLabel('角色名称', { exact: true }).fill('MVP 验证角色');
    await page.getByLabel('角色描述', { exact: true }).fill('仅测试纯文本 <script>inert</script>');
    await page.getByLabel('角色设定', { exact: true }).fill('用于 Mock，简短回答。');
    await page.getByRole('button', { name: '保存角色', exact: true }).click();
    await expect(page.getByRole('button', { name: 'MVP 验证角色', exact: true })).toBeVisible();
    await page.getByLabel('绑定角色', { exact: true }).selectOption({ label: 'MVP 验证角色' });
    await page.getByLabel('核心记忆', { exact: true }).fill('保留核心事实。');
    await page.getByLabel('关系背景', { exact: true }).fill('测试朋友。');
    await page.getByRole('button', { name: '保存当前会话记忆', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: '核心记忆与关系已保存' })).toBeVisible();
    await page.getByRole('button', { name: '聊天', exact: true }).click();
    await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
    await openChatAdvanced(page); await page.getByLabel('生成模式', { exact: true }).selectOption('fast');
    await page.getByLabel('消息内容', { exact: true }).fill('请简短回复。');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await expect(page.locator('.run-status')).toContainText('已完成');
    await expect(page.locator('.messages .assistant .message-content')).toHaveText('Mock response.');
    await page.getByRole('button', { name: '角色与记忆', exact: true }).click();
    await expect(page.getByLabel('核心记忆', { exact: true })).toHaveValue('保留核心事实。');
    await page.getByRole('button', { name: '生成规则摘要', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: '规则摘要已更新' })).toBeVisible();

    phase = '打开历史导入';
    const source = JSON.stringify([{ role: 'user', content: '历史 <img src=x onerror="globalThis.pwned=1">' }, { role: 'assistant', content: '历史答复。' }]);
    await page.getByRole('button', { name: '历史导入', exact: true }).click();
    phase = '选择历史格式';
    await page.getByLabel('历史格式', { exact: true }).selectOption('json');
    phase = '填写历史正文';
    await page.getByLabel('粘贴历史正文', { exact: true }).fill(source);
    phase = '历史预览';
    await page.getByRole('button', { name: '预览历史', exact: true }).click();
    await expect(page.getByLabel('导入预览结果', { exact: true })).toContainText('历史答复。');
    await page.getByLabel('导入新会话标题', { exact: true }).fill('MVP 导入会话');
    await page.getByLabel('导入后的核心记忆', { exact: true }).fill('导入核心。');
    await page.getByLabel('确认导入会创建新会话并保存历史', { exact: true }).check();
    phase = '提交导入';
    await page.getByRole('button', { name: '确认导入为新会话', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'MVP 导入会话', exact: true })).toBeVisible();
    await expect(page.locator('.messages')).toContainText('历史答复。');
    phase = '显式导入原文';
    await page.getByRole('button', { name: '历史导入', exact: true }).click();
    await page.getByRole('button', { name: '查看当前会话导入原文', exact: true }).click();
    await expect(page.getByLabel('显式查看的导入原文', { exact: true })).toContainText(source);
    await page.getByRole('button', { name: '关闭导入原文', exact: true }).click();
    expect(await page.evaluate(() => 'pwned' in globalThis)).toBe(false);

    phase = '独立管理认证';
    let issued: { url: string };
    try { issued = JSON.parse(execFileSync('/opt/allira/workspace/server/.venv/bin/python', ['-m', 'allira.admin_cli', '--env-file', '/etc/allira/dev.env', 'issue-login', '--base-url', 'http://127.0.0.1:8000', '--json'], { cwd: '/opt/allira/workspace/server', encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })); }
    catch { throw new Error('管理测试签发失败。'); }
    try { await admin.goto(issued.url); } catch { throw new Error('管理测试链接导航失败。'); }
    issued.url = '';
    expect(await admin.evaluate(() => location.hash)).toBe('');
    await admin.getByRole('button', { name: '登录管理', exact: true }).click();
    await expect(admin.getByRole('heading', { name: '概览', exact: true })).toBeVisible();

    phase = '兑换码签发与能量';
    await admin.getByRole('button', { name: '兑换码', exact: true }).click();
    await admin.getByLabel('每个码的整数能量', { exact: true }).fill('7');
    await admin.getByLabel('签发数量', { exact: true }).fill('1');
    await admin.getByLabel('确认签发以上数量和能量的兑换码', { exact: true }).check();
    // Read through the response in memory, then clear the complete-code DOM before assertions.
    const issueResponse = admin.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/admin/cdks' && response.request().method() === 'POST');
    await admin.getByRole('button', { name: '签发兑换码', exact: true }).click();
    const issuedResponse = await issueResponse;
    if (!issuedResponse.ok()) throw new Error('管理测试签发请求失败。');
    code = (await issuedResponse.json()).items[0].code;
    await admin.getByRole('button', { name: '清除完整兑换码', exact: true }).click();
    if (typeof code !== 'string' || code.length < 16) throw new Error('管理测试签发未返回有效兑换码。');
    await page.getByRole('button', { name: '能量用量', exact: true }).click();
    await expect(page.getByLabel('能量余额', { exact: true })).toContainText('当前能量');
    const before = Number(await page.getByLabel('能量余额', { exact: true }).locator('dd').first().textContent());
    await page.getByLabel('能量兑换码', { exact: true }).fill(code);
    await page.getByRole('button', { name: '兑换能量', exact: true }).click();
    code = '';
    await expect(page.getByLabel('能量兑换码', { exact: true })).toHaveValue('');
    await expect(page.getByLabel('能量余额', { exact: true }).locator('dd').first()).toHaveText(String(before + 7));
    await page.getByLabel('生成任务编号', { exact: true }).fill(runId);
    await page.getByRole('button', { name: '查看任务用量', exact: true }).click();
    await expect(page.getByLabel('生成任务用量', { exact: true })).toContainText('已结算');
    await expect(page.getByLabel('生成任务用量', { exact: true })).toContainText('输入字符');
    await admin.getByRole('button', { name: '能量用量', exact: true }).click();
    await expect(admin.getByLabel('管理内容', { exact: true })).toContainText('不是 Token');
    await admin.getByRole('button', { name: '注销管理', exact: true }).click();

    phase = '清理测试会话与角色';
    await page.getByRole('button', { name: '聊天', exact: true }).click();
    for (const title of ['MVP 导入会话', 'MVP 正常会话']) {
      phase = `清理测试会话：${title}`;
      await page.getByRole('button', { name: title, exact: true }).click();
      await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
      // Native details deliberately retains its expansion across selections.
      await openChatAdvanced(page); await openDetails(page, '修改会话');
      await page.getByLabel('确认永久删除此会话及其消息', { exact: true }).check();
      await page.getByRole('button', { name: '删除此会话', exact: true }).click();
      await expect(page.getByRole('button', { name: title, exact: true })).toHaveCount(0);
    }
    phase = '清理测试角色';
    await page.getByRole('button', { name: '角色与记忆', exact: true }).click();
    await page.getByRole('button', { name: 'MVP 验证角色', exact: true }).click();
    await page.getByLabel('确认删除此角色', { exact: true }).check();
    await page.getByRole('button', { name: '删除角色', exact: true }).click();
    await expect(page.getByRole('button', { name: 'MVP 验证角色', exact: true })).toHaveCount(0);

    phase = '账号改密与全部会话撤销';
    await page.getByRole('button', { name: '账号', exact: true }).click();
    await expect(page.getByLabel('显示名称', { exact: true })).toBeEnabled();
    await page.getByLabel('显示名称', { exact: true }).fill('MVP 已修改名称');
    await page.getByRole('button', { name: '保存显示名称', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: '显示名称已更新' })).toBeVisible();
    await page.getByLabel('当前密码', { exact: true }).fill(password);
    await page.getByLabel('新密码', { exact: true }).fill(replacement);
    await page.getByLabel('确认改密会撤销全部登录', { exact: true }).check();
    await page.getByRole('button', { name: '修改密码并退出全部登录', exact: true }).click();
    password = '';
    await expect(page.getByRole('button', { name: '账号登录', exact: true })).toBeVisible();
    await other.getByRole('button', { name: '能量用量', exact: true }).click();
    await expect(other.getByRole('button', { name: '账号登录', exact: true })).toBeVisible();
    await login(page, replacement); replacement = '';
    await page.getByRole('button', { name: '账号', exact: true }).click();
    await page.getByLabel('确认退出此账号全部会话', { exact: true }).check();
    await page.getByRole('button', { name: '退出全部账号登录', exact: true }).click();
    await expect(page.getByRole('button', { name: '账号登录', exact: true })).toBeVisible();
    expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
    expect(pageErrors).toEqual([]);
  } catch { throw new Error(`真实 Mock MVP 浏览器阶段失败：${phase}。原始错误已隐藏，未附加凭据或响应正文。`); }
  finally { password = ''; replacement = ''; code = ''; await Promise.allSettled([context.close(), second.close(), adminContext.close()]); }
});

test('existing Mock API: PWA caches only the public shell and offline reload never restores auth', async ({ request }) => {
  test.setTimeout(45_000);
  expect(await (await request.get('/api/v1/info')).json()).toMatchObject({ provider: 'mock', consoleEnabled: true });
  // Chromium incognito deliberately cannot install apps; a temporary non-incognito
  // profile is required to distinguish app defects from that browser restriction.
  const profile = await mkdtemp(join(tmpdir(), 'allira-public-pwa-'));
  const context = await chromium.launchPersistentContext(profile, { channel: 'chromium', headless: true, serviceWorkers: 'allow' });
  const page = context.pages()[0];
  context.setDefaultTimeout(10_000);
  let phase = '加载公开页面';
  try {
    await page.goto('http://127.0.0.1:8000/test/');
    phase = '等待 Service Worker 激活与控制';
    await page.evaluate(async () => {
      await Promise.race([
        (async () => { await navigator.serviceWorker.ready; if (!navigator.serviceWorker.controller) await new Promise<void>(resolve => navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true })); })(),
        new Promise<never>((_, reject) => setTimeout(() => reject(new Error('Service Worker 尚未就绪。')), 8_000)),
      ]);
    });
    phase = '开发登录与能量页';
    await page.getByLabel('开发用户 ID', { exact: true }).fill(`pwa_${Date.now()}`);
    await page.getByRole('button', { name: '开发登录', exact: true }).click();
    await expect(page.getByRole('button', { name: '聊天', exact: true })).toBeVisible(); await openAdvanced(page);
    await page.getByRole('button', { name: '能量用量', exact: true }).click();
    await expect(page.getByLabel('能量余额', { exact: true })).toContainText('当前能量');
    await page.getByRole('button', { name: '退出登录', exact: true }).click();
    phase = '检查六项公开缓存';
    const paths = await page.evaluate(async () => {
      const result: string[] = [];
      for (const name of await caches.keys()) for (const value of await (await caches.open(name)).keys()) result.push(new URL(value.url).pathname);
      return result.sort();
    });
    expect(paths).toHaveLength(6);
    expect(paths).toContain('/test/'); expect(paths).toContain('/test/manifest.webmanifest');
    expect(paths.filter(path => /^\/console-assets\/public-chat-[A-Z0-9]+\.(js|css)$/.test(path))).toHaveLength(2);
    expect(paths.filter(path => /^\/console-assets\/public-icon-(192|512)-[a-f0-9]{16}\.png$/.test(path))).toHaveLength(2);
    // Validate CSP-compatible icon decoding and Chromium's installability report,
    // not merely offline CacheStorage presence.
    phase = '图标解码与安装资格';
    const manifest = await (await request.get('/test/manifest.webmanifest')).json();
    expect(await page.evaluate(async icons => {
      const sizes: number[] = [];
      for (const icon of icons) { const image = new Image(); image.src = icon.src; await image.decode(); sizes.push(image.naturalWidth); }
      return sizes;
    }, manifest.icons as { src: string }[])).toEqual([192, 512]);
    const cdp = await context.newCDPSession(page);
    await cdp.send('Page.enable');
    const installability = await cdp.send('Page.getInstallabilityErrors');
    expect(installability.installabilityErrors).toEqual([]);
    expect(paths.some(path => /admin|api|sw\.js/.test(path))).toBe(false);
    expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
    phase = '实际离线 API 与公开壳刷新';
    await context.setOffline(true);
    // Chrome 159 can block requests yet leave navigator.onLine unchanged when
    // only the persistent context's legacy network emulation is used. Override
    // the browser's connectivity state separately; still prove real API failure.
    await cdp.send('Network.enable');
    await cdp.send('Network.overrideNetworkState', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    expect(await page.evaluate(async () => {
      try { await fetch('/api/v1/info', { cache: 'no-store', signal: AbortSignal.timeout(2000) }); return false; }
      catch { return true; }
    })).toBe(true);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'AlliraAi 聊天', exact: true })).toBeVisible();
    await expect(page.getByRole('status').filter({ hasText: '当前离线' }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeHidden();
    await page.getByLabel('账号用户名', { exact: true }).fill('offline_account');
    await page.getByLabel('账号密码（10–128 字符，最多 256 UTF-8 字节）', { exact: true }).fill('1234567890');
    await page.getByRole('button', { name: '账号登录', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: '当前离线，不能登录或注册' })).toBeVisible();
    await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeHidden();
    await context.setOffline(false);
    phase = '关闭临时浏览器';
  } catch { throw new Error(`独立 PWA 浏览器阶段失败：${phase}。原始错误已隐藏。`); }
  finally {
    await Promise.allSettled([context.close()]);
    // Only remove the exact temporary browser profile we just created and verified.
    const target = await realpath(profile); const parent = await realpath(tmpdir());
    if (dirname(target) !== parent || !basename(target).startsWith('allira-public-pwa-')) throw new Error('临时浏览器目录校验失败。');
    await rm(target, { recursive: true, force: true });
  }
});
