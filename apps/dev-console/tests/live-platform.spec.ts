import { test, expect, chromium } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import type { BrowserContext, Page, Download } from '@playwright/test';
import { openAdvanced, openDetails } from './ui.ts';

test.skip(process.env.ALLIRA_CONSOLE_LIVE !== '1', '需 root 明确确认现有服务已部署为 Mock 0.4');
const origin = 'http://127.0.0.1:8000';
const passwordLabel = '账号密码（10–128 字符，最多 256 UTF-8 字节）';
const rememberLabel = '记住此设备（可选，使用独立 HttpOnly Cookie，不保存 Bearer）';
async function downloadContents(download: Download): Promise<string> {
  const stream = await download.createReadStream();
  if (!stream) throw new Error('下载不可读取。');
  const chunks: Buffer[] = []; for await (const chunk of stream) chunks.push(Buffer.from(chunk));
  return Buffer.concat(chunks).toString('utf8');
}
async function signIn(page: Page, username: string, password: string, remember = false): Promise<void> {
  await page.goto(`${origin}/test/`);
  await page.getByLabel('账号用户名', { exact: true }).fill(username);
  await page.getByLabel(passwordLabel, { exact: true }).fill(password);
  if (remember) await page.getByLabel(rememberLabel, { exact: true }).check();
  await page.getByRole('button', { name: '账号登录', exact: true }).click();
  await expect(page.getByRole('button', { name: '聊天', exact: true })).toBeVisible(); await openAdvanced(page);
}

test('existing Mock 0.4: Cookie restore/CSRF, recovery/device revoke, search/export/fork, data erase and public-only PWA cache', async ({ browser, request }) => {
  test.setTimeout(120_000);
  expect(await (await request.get('/api/v1/info')).json()).toMatchObject({ provider: 'mock', consoleEnabled: true, adminEnabled: true });
  const profile = await mkdtemp(join(tmpdir(), 'allira-platform-pwa-'));
  // A fresh non-incognito profile makes installability a real assertion, not an incognito restriction.
  const main = await chromium.launchPersistentContext(profile, { channel: 'chromium', headless: true, serviceWorkers: 'allow', acceptDownloads: true });
  const second = await browser.newContext({ serviceWorkers: 'allow', acceptDownloads: true });
  const management = await browser.newContext({ serviceWorkers: 'allow', acceptDownloads: true });
  const contexts: BrowserContext[] = [main, second, management];
  for (const context of contexts) context.setDefaultTimeout(10_000);
  const page = await main.newPage(); const other = await second.newPage(); const admin = await management.newPage();
  let password = randomUUID(), newPassword = randomUUID(), recoveryCode = '';
  let exported = '', archive = '';
  const username = `platform_${Date.now()}`;
  const originalTitle = `平台会话_${Date.now()}`, forkTitle = `平台分叉_${Date.now()}`;
  const errors: string[] = [];
  for (const item of [page, other, admin]) item.on('pageerror', error => errors.push(error.name));
  let phase = '注册与显式设备记忆';
  try {
    await page.goto(`${origin}/test/`);
    await page.getByLabel('账号用户名', { exact: true }).fill(username);
    await page.getByLabel(passwordLabel, { exact: true }).fill(password);
    await page.getByLabel('账号显示名称（注册）', { exact: true }).fill('平台验收账号');
    await page.getByLabel(rememberLabel, { exact: true }).check();
    await page.getByLabel('我已满 18 岁', { exact: true }).check();
    await page.getByLabel('我已阅读并接受当前版本协议（注册必选）', { exact: true }).check();
    await page.getByRole('button', { name: '注册账号', exact: true }).click();
    await expect(page.locator('.identity')).toContainText('已记住此设备');
    await page.evaluate(async () => { await Promise.race([(async () => { await navigator.serviceWorker.ready; if (!navigator.serviceWorker.controller) await new Promise<void>(resolve => navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true })); })(), new Promise<never>((_, reject) => setTimeout(() => reject(new Error('Service Worker 尚未就绪。')), 8_000))]); });
    await openAdvanced(page); await openDetails(page, '自定义新会话'); await page.getByLabel('新会话标题', { exact: true }).fill(originalTitle);
    await page.getByRole('button', { name: '新建会话', exact: true }).click();
    await expect(page.getByRole('heading', { name: originalTitle, exact: true })).toBeVisible();
    await page.getByLabel('消息内容', { exact: true }).fill('# 安全 Markdown\n\n**保留格式**\n\n![只显示替代](https://tracking.invalid/image.png)\n\n<script>globalThis.platformXss=1</script>\n\n```js\nconst x = 1;\n```');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await expect(page.locator('.run-status')).toContainText('已完成');
    await expect(page.locator('.messages .user strong')).toHaveText('保留格式');
    await expect(page.locator('.messages img, .messages script')).toHaveCount(0);
    expect(await page.evaluate(() => 'platformXss' in globalThis)).toBe(false);
    await page.getByLabel('消息内容', { exact: true }).fill('仅内存的私有草稿，不得缓存');
    await page.reload();
    await expect(page.locator('.identity')).toContainText('已记住此设备');
    await page.getByRole('button', { name: originalTitle, exact: true }).click();
    await expect(page.getByLabel('消息内容', { exact: true })).toHaveValue('');
    await expect(page.locator('.messages .assistant')).toContainText('Mock response.');
    expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);

    phase = '检索导出和分叉';
    await openAdvanced(page); await page.getByRole('button', { name: '会话工具', exact: true }).click();
    await page.getByLabel('会话检索关键词', { exact: true }).fill(originalTitle);
    await page.getByRole('button', { name: '检索我的会话', exact: true }).click();
    await expect(page.getByLabel('会话检索结果')).toContainText(originalTitle);
    await page.getByRole('button', { name: '选择导出或分叉', exact: true }).click();
    const conversationDownload = page.waitForEvent('download');
    await page.getByRole('button', { name: '下载此会话', exact: true }).click();
    exported = await downloadContents(await conversationDownload);
    if (!exported.includes('allira.conversation.v1') || !exported.includes('保留格式')) throw new Error('会话归档缺少字段。'); exported = '';
    await page.getByRole('button', { name: '读取最近可选分叉点', exact: true }).click();
    await expect(page.getByLabel('分叉截止消息').locator('option')).toHaveCount(2);
    await page.getByLabel('分叉截止消息').selectOption({ index: 1 });
    await page.getByLabel('新分叉标题', { exact: true }).fill(forkTitle);
    await page.getByLabel('确认从已完成对话创建新会话', { exact: true }).check();
    await page.getByRole('button', { name: '创建分叉会话', exact: true }).click();
    await expect(page.getByRole('heading', { name: forkTitle, exact: true })).toBeVisible();
    await expect(page.locator('.messages .assistant')).toContainText('Mock response.');

    phase = '恢复码显式签发与设备撤销';
    await openAdvanced(page); await page.getByRole('button', { name: '账号', exact: true }).click();
    await page.getByLabel('签发恢复码的当前密码', { exact: true }).fill(password);
    await page.getByLabel('确认替换全部旧恢复码，并自行离线保存新码', { exact: true }).check();
    const rotation = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/account/recovery' && response.request().method() === 'POST');
    await page.getByRole('button', { name: '签发并替换恢复码', exact: true }).click();
    const rotationResponse = await rotation; if (!rotationResponse.ok()) throw new Error('恢复码签发失败。');
    const codes = (await rotationResponse.json()).codes as string[]; recoveryCode = codes[0]; codes.fill('');
    await page.getByRole('button', { name: '隐藏并清除本次恢复码', exact: true }).click();
    if (typeof recoveryCode !== 'string' || recoveryCode.length < 16) throw new Error('恢复码无效。');
    await signIn(other, username, password);
    await other.getByRole('button', { name: '账号', exact: true }).click();
    await other.getByRole('button', { name: '读取登录设备', exact: true }).click();
    const rememberedRow = other.getByRole('row').filter({ hasText: '浏览器 Cookie' });
    await expect(rememberedRow).toHaveCount(1);
    await rememberedRow.getByLabel('确认撤销此登录设备', { exact: true }).check();
    await rememberedRow.getByRole('button', { name: '撤销此设备登录', exact: true }).click();
    await page.getByRole('button', { name: '能量用量', exact: true }).click();
    await expect(page.getByRole('button', { name: '账号登录', exact: true })).toBeVisible();

    phase = '恢复码重设密码并撤销旧登录';
    await page.getByText('忘记密码：使用恢复码', { exact: true }).click();
    await page.getByLabel('恢复账号用户名', { exact: true }).fill(username);
    await page.getByLabel('单次恢复码', { exact: true }).fill(recoveryCode);
    await page.getByLabel('恢复后的新密码', { exact: true }).fill(newPassword);
    await page.getByLabel('确认重设密码并撤销全部旧码和登录', { exact: true }).check();
    await page.getByRole('button', { name: '使用恢复码重设密码', exact: true }).click();
    recoveryCode = ''; password = '';
    await expect(page.getByLabel('单次恢复码', { exact: true })).toHaveValue('');
    await expect(page.getByRole('status').filter({ hasText: '不会自动登录' })).toBeVisible();
    await other.getByRole('button', { name: '能量用量', exact: true }).click();
    await expect(other.getByRole('button', { name: '账号登录', exact: true })).toBeVisible();
    await signIn(page, username, newPassword, true);
    await openAdvanced(page); await page.getByRole('button', { name: '账号', exact: true }).click();
    await page.getByRole('button', { name: '读取恢复码状态', exact: true }).click();
    await expect(page.getByText('未启用可用恢复码。', { exact: true })).toBeVisible();

    phase = '账号数据导出与独立管理诊断';
    await page.getByLabel('确认下载含敏感聊天内容的账号数据', { exact: true }).check();
    const accountDownload = page.waitForEvent('download');
    await page.getByRole('button', { name: '下载账号完整数据', exact: true }).click();
    archive = await downloadContents(await accountDownload);
    const data = JSON.parse(archive);
    if (data.format !== 'allira.account.v1' || data.conversations.length !== 2 || /accessToken|csrfToken|passwordHash|recoveryCode/.test(archive)) throw new Error('账号归档结构或凭据排除不正确。');
    archive = '';
    let issued: { url: string };
    try { issued = JSON.parse(execFileSync('/opt/allira/workspace/server/.venv/bin/python', ['-m', 'allira.admin_cli', '--env-file', '/etc/allira/dev.env', 'issue-login', '--base-url', origin, '--json'], { cwd: '/opt/allira/workspace/server', encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })); }
    catch { throw new Error('管理测试签发失败。'); }
    try { await admin.goto(issued.url); } catch { throw new Error('管理导航失败。'); } finally { issued.url = ''; }
    await admin.getByRole('button', { name: '登录管理', exact: true }).click();
    await expect(admin.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
    await admin.getByRole('button', { name: '健康诊断', exact: true }).click();
    await expect(admin.getByLabel('管理内容')).toContainText('数据库结构版本');
    await expect(admin.getByLabel('管理内容')).toContainText('不代表已校验可恢复');
    await admin.getByRole('button', { name: '注销管理', exact: true }).click();

    phase = 'Cookie 登录后的公开缓存与离线数据隔离';
    const paths = await page.evaluate(async () => {
      const values: string[] = [];
      for (const name of await caches.keys()) for (const item of await (await caches.open(name)).keys()) values.push(new URL(item.url).pathname);
      return values;
    });
    if (paths.length !== 6 || paths.some(path => /api|admin|recovery|account/.test(path))) throw new Error('公开缓存边界错误。');
    expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
    const manifest = await (await request.get('/test/manifest.webmanifest')).json();
    const widths = await page.evaluate(async icons => { const values: number[] = []; for (const icon of icons) { const image = new Image(); image.src = icon.src; await image.decode(); values.push(image.naturalWidth); } return values; }, manifest.icons as { src: string }[]);
    if (widths.join(',') !== '192,512') throw new Error('PWA 图标解码失败。');
    const cdp = await main.newCDPSession(page);
    await cdp.send('Page.enable');
    const installability = await cdp.send('Page.getInstallabilityErrors');
    if (installability.installabilityErrors.length) throw new Error('非隐私浏览器未满足安装资格。');
    await main.setOffline(true); await cdp.send('Network.enable');
    await cdp.send('Network.overrideNetworkState', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    if (!await page.evaluate(async () => { try { await fetch('/api/v1/auth/browser/session', { cache: 'no-store', signal: AbortSignal.timeout(2000) }); return false; } catch { return true; } })) throw new Error('离线私有 API 应失败。');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'AlliraAi 聊天', exact: true })).toBeVisible();
    await expect(page.getByRole('status').filter({ hasText: '当前离线' }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeHidden();
    await main.setOffline(false);
    await cdp.send('Network.overrideNetworkState', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    await page.reload();
    await expect(page.locator('.identity')).toContainText('已记住此设备');
    await openAdvanced(page); await page.getByRole('button', { name: '账号', exact: true }).click();

    phase = '账号删除确认与登录失效';
    await page.getByText('危险操作：删除账号', { exact: true }).click();
    await page.getByLabel('删除账号的当前密码', { exact: true }).fill(newPassword);
    await page.getByLabel('逐字输入 DELETE MY ACCOUNT', { exact: true }).fill('DELETE MY ACCOUNT');
    await page.getByLabel('确认永久删除账号和在线数据且无法撤销', { exact: true }).check();
    await page.getByRole('button', { name: '永久删除账号和在线数据', exact: true }).click(); newPassword = '';
    await expect(page.getByRole('button', { name: '账号登录', exact: true })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('button', { name: '账号登录', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeHidden();
    expect(errors).toEqual([]);
  } catch { throw new Error(`真实 Mock 0.4 浏览器阶段失败：${phase}。原始错误已隐藏，不附加凭据或聊天正文。`); }
  finally {
    password = ''; newPassword = ''; recoveryCode = ''; exported = ''; archive = '';
    await Promise.allSettled(contexts.map(context => context.close()));
    const target = await realpath(profile), parent = await realpath(tmpdir());
    if (dirname(target) !== parent || !basename(target).startsWith('allira-platform-pwa-')) throw new Error('临时浏览器目录校验失败。');
    await rm(target, { recursive: true, force: true });
  }
});
