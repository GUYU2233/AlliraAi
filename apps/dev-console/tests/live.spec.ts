import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { openDeveloper, openChatAdvanced, openDetails } from './ui.ts';

// Opt in only after root deployed the one existing allira-dev API in Mock mode.
// No webServer, extra API, credentials in fixtures, traces, screenshots, or URL logs.
test.skip(process.env.ALLIRA_CONSOLE_LIVE !== '1', '需 root 明确确认已部署现有 Mock 服务');
test('existing loopback API: chat replay, cancel, isolation, admin cookie + CSRF and logout', async ({ browser, request }) => {
  test.setTimeout(60_000);
  const infoResponse = await request.get('/api/v1/info');
  const info = await infoResponse.json();
  expect(info).toMatchObject({ provider: 'mock', allowDevAuth: true, consoleEnabled: true, adminEnabled: true });
  const userId = `console_browser_${Date.now()}`;
  const title = `浏览器测试 ${Date.now()}`;
  const context = await browser.newContext();
  const page = await context.newPage();
  const pageErrors: string[] = [];
  page.on('pageerror', error => pageErrors.push(error.name));
  let conversationId = '';
  let runId = '';
  let cancelledRequests = 0;
  page.on('request', req => {
    if (/\/runs\/[^/]+\/cancel$/.test(new URL(req.url()).pathname)) cancelledRequests++;
  });
  page.on('response', async response => {
    const path = new URL(response.url()).pathname;
    if (path === '/api/v1/conversations' && response.request().method() === 'POST' && response.ok()) conversationId = (await response.json()).id;
    if (/\/conversations\/[^/]+\/runs$/.test(path) && response.request().method() === 'POST' && response.ok()) runId = (await response.json()).id;
  });
  try {
    await page.goto('http://127.0.0.1:8000/test/'); await openDeveloper(page);
    await page.getByLabel('开发用户 ID').fill(userId);
    await page.getByRole('button', { name: '开发登录', exact: true }).click();
    await openDetails(page, '自定义新会话'); await page.getByLabel('新会话标题', { exact: true }).fill(title);
    await page.getByLabel('新会话设定', { exact: true }).fill('只用于 Mock 浏览器测试。');
    await page.getByRole('button', { name: '新建会话', exact: true }).click();
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
    await page.getByLabel('消息内容', { exact: true }).fill('纯文本 <img src=x onerror="globalThis.pwned=1">');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await openChatAdvanced(page); await expect(page.getByRole('button', { name: '断开订阅', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: '断开订阅', exact: true }).click();
    await expect(page.locator('.run-status')).toContainText('已断开');
    expect(cancelledRequests).toBe(0);
    await page.getByRole('button', { name: '恢复订阅', exact: true }).click();
    await expect(page.locator('.messages .assistant .message-content')).toHaveText('Mock response.');
    await expect(page.locator('.run-status')).toContainText('已完成');
    expect(await page.evaluate(() => 'pwned' in globalThis)).toBe(false);
    expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);

    await page.getByLabel('消息内容', { exact: true }).fill('第二条用于显式停止。');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await expect(page.getByRole('button', { name: '停止回复', exact: true })).toBeEnabled();
    await page.getByRole('button', { name: '停止回复', exact: true }).click();
    await expect(page.locator('.run-status')).toContainText('已停止');
    expect(cancelledRequests).toBe(1);

    // A distinct fresh developer session cannot see this user's conversation.
    const other = await browser.newContext();
    const otherPage = await other.newPage();
    await otherPage.goto('http://127.0.0.1:8000/test/');
    await openDeveloper(otherPage); await otherPage.getByLabel('开发用户 ID').fill(`${userId}_other`);
    await otherPage.getByRole('button', { name: '开发登录', exact: true }).click();
    await expect(otherPage.getByText('暂无会话。', { exact: true })).toBeVisible();
    await expect(otherPage.getByRole('button', { name: title, exact: true })).toHaveCount(0);
    await otherPage.getByRole('button', { name: '退出登录', exact: true }).click();
    await other.close();

    // Obtain an opaque CLI link through an in-process pipe. Never print it, store it, or attach traces.
    let issued: { url: string };
    try {
      const raw = execFileSync('/opt/allira/workspace/server/.venv/bin/python', ['-m', 'allira.admin_cli', '--env-file', '/etc/allira/dev.env', 'issue-login', '--base-url', 'http://127.0.0.1:8000', '--json'], { cwd: '/opt/allira/workspace/server', encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
      issued = JSON.parse(raw) as { url: string };
    } catch { throw new Error('签发测试管理链接失败（输出已隐藏）。'); }
    const admin = await browser.newContext();
    const adminPage = await admin.newPage();
    try { await adminPage.goto(issued.url); }
    catch { throw new Error('一次性管理链接导航失败（凭据已隐藏）。'); }
    issued.url = '';
    await expect(adminPage.getByRole('button', { name: '登录管理', exact: true })).toBeEnabled();
    expect(await adminPage.evaluate(() => location.hash)).toBe('');
    await adminPage.getByRole('button', { name: '登录管理', exact: true }).click();
    await expect(adminPage.getByRole('heading', { name: '概览', exact: true })).toBeVisible();
    await adminPage.getByRole('button', { name: '会话', exact: true }).click();
    await adminPage.getByLabel('按用户 ID 筛选会话', { exact: true }).fill(userId);
    await adminPage.getByRole('button', { name: '筛选会话', exact: true }).click();
    await expect(adminPage.getByRole('button', { name: '查看消息', exact: true })).toHaveCount(1);
    await adminPage.getByRole('button', { name: '查看消息', exact: true }).click();
    await expect(adminPage.getByLabel('显式查看的会话消息')).toContainText('Mock response.');
    await adminPage.getByRole('button', { name: '用户', exact: true }).click();
    // Wait for the initial list before counting rows or inspecting the pager.
    await expect(adminPage.getByText(/^已显示 \d+ 条记录。$/)).toBeVisible();
    // This user may be beyond the first page in a persistent development DB.
    let userRow = adminPage.getByRole('row').filter({ has: adminPage.getByRole('cell', { name: userId, exact: true }) });
    for (let pages = 0; await userRow.count() === 0 && pages < 30; pages++) {
      const more = adminPage.getByRole('button', { name: '加载更多', exact: true });
      if (!await more.isVisible()) break;
      await more.click();
      await expect(more).toBeEnabled();
      userRow = adminPage.getByRole('row').filter({ has: adminPage.getByRole('cell', { name: userId, exact: true }) });
    }
    await expect(userRow).toHaveCount(1);
    await userRow.getByLabel('确认撤销此用户当前全部登录').check();
    await userRow.getByRole('button', { name: '撤销用户登录', exact: true }).click();
    await expect(adminPage.locator('body')).toContainText('已撤销');
    await page.getByRole('button', { name: '刷新会话', exact: true }).click();
    await expect(page.getByRole('button', { name: '开发登录', exact: true })).toBeVisible();

    await adminPage.getByRole('button', { name: '会话', exact: true }).click();
    await adminPage.getByLabel('按用户 ID 筛选会话', { exact: true }).fill(userId);
    await adminPage.getByRole('button', { name: '筛选会话', exact: true }).click();
    await adminPage.getByLabel('确认永久删除此会话及消息').check();
    await adminPage.getByRole('button', { name: '删除会话', exact: true }).click();
    await expect(adminPage.locator('body')).toContainText('会话已删除');
    await adminPage.getByRole('button', { name: '注销管理', exact: true }).click();
    await expect(adminPage.getByRole('button', { name: '检查已有管理登录', exact: true })).toBeVisible();
    await admin.close();
    expect(conversationId.length).toBeGreaterThan(0);
    expect(runId.length).toBeGreaterThan(0);
    expect(pageErrors).toEqual([]);
  } finally { await context.close(); }
});
