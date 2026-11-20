import { test, expect, chromium } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { mkdtemp, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import type { BrowserContext, Page } from '@playwright/test';
import type { CompanionStart, ContextPreview, EnergyWallet, MemoryBlock, MemoryBlockSource, MemoryCard, Run } from '../../../packages/client/src/index.ts';
import { openAdvanced, openDetails } from './ui.ts';

test.skip(process.env.ALLIRA_CONSOLE_LIVE !== '1', '只在 root 明确 Mock 0.5 / recent window=1 READY 后执行约定的一次验收');
const origin = 'http://127.0.0.1:8000';
const passwordLabel = '账号密码（10–128 字符，最多 256 UTF-8 字节）';
const rememberLabel = '记住此设备（可选，使用独立 HttpOnly Cookie，不保存 Bearer）';
async function registration(page: Page, username: string, password: string): Promise<void> {
  await page.goto(`${origin}/test/`);
  await page.getByLabel('账号用户名', { exact: true }).fill(username);
  await page.getByLabel(passwordLabel, { exact: true }).fill(password);
  await page.getByLabel(rememberLabel, { exact: true }).check();
  await page.getByLabel('我已满 18 岁', { exact: true }).check();
  await page.getByLabel('我已阅读并接受当前版本协议（注册必选）', { exact: true }).check();
  await page.getByRole('button', { name: '注册账号', exact: true }).click();
  await expect(page.locator('.identity')).toContainText('已记住此设备');
}
async function responseFor<T>(page: Page, path: string, method: string, action: () => Promise<unknown>): Promise<T> {
  const waiting = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1${path}` && response.request().method() === method);
  await action(); const response = await waiting;
  if (!response.ok()) throw new Error('验收请求未成功，响应正文已隐藏。');
  return response.json() as Promise<T>;
}
async function walletSnapshot(page: Page): Promise<EnergyWallet> {
  return page.evaluate(async () => {
    const response = await fetch('/api/v1/energy', { credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) throw new Error('余额只读核验失败。');
    return response.json();
  });
}
async function wallet(page: Page): Promise<number> { return (await walletSnapshot(page)).balance; }
async function privateCounts(page: Page, conversationId: string): Promise<{ messages: number; blocks: number }> {
  return page.evaluate(async id => {
    const responses = await Promise.all(['/messages/recent?limit=50', '/memory/blocks?limit=20'].map(path => fetch(`/api/v1/conversations/${encodeURIComponent(id)}${path}`, { credentials: 'same-origin', cache: 'no-store' })));
    if (responses.some(response => !response.ok)) throw new Error('私有只读核验失败。');
    const [messages, blocks] = await Promise.all(responses.map(response => response.json()));
    return { messages: messages.items.length, blocks: blocks.items.length };
  }, conversationId);
}
async function erase(page: Page, password: string): Promise<void> {
  await openAdvanced(page); await page.getByRole('button', { name: '账号', exact: true }).click();
  await page.getByText('危险操作：删除账号', { exact: true }).click();
  await page.getByLabel('删除账号的当前密码', { exact: true }).fill(password);
  await page.getByLabel('逐字输入 DELETE MY ACCOUNT', { exact: true }).fill('DELETE MY ACCOUNT');
  await page.getByLabel('确认永久删除账号和在线数据且无法撤销', { exact: true }).check();
  await page.getByRole('button', { name: '永久删除账号和在线数据', exact: true }).click();
  await expect(page.getByRole('button', { name: '账号登录', exact: true })).toBeVisible();
}

test('existing Mock 0.5: simple daily setup, traceable memory/context, style isolation and public-only PWA', async ({ browser, request }) => {
  test.setTimeout(120_000);
  // The root owns the sole API, maintenance env/backup and all deployment builds. Never start a server here.
  expect(await (await request.get('/api/v1/info')).json()).toMatchObject({ provider: 'mock', consoleEnabled: true });
  const profile = await mkdtemp(join(tmpdir(), 'allira-context-pwa-'));
  const main = await chromium.launchPersistentContext(profile, { channel: 'chromium', headless: true, serviceWorkers: 'allow' });
  const otherContext = await browser.newContext({ serviceWorkers: 'allow' });
  const contexts: BrowserContext[] = [main, otherContext];
  for (const context of contexts) context.setDefaultTimeout(10_000);
  const page = await main.newPage(); const other = await otherContext.newPage();
  let password = randomUUID(), otherPassword = randomUUID();
  let phase = '账号与默认折叠';
  const errors: string[] = []; for (const item of [page, other]) item.on('pageerror', error => errors.push(error.name));
  let runs = 0, setupWrites = 0, sourceReads = 0, badCookieWrites = 0;
  page.on('request', req => {
    const path = new URL(req.url()).pathname;
    if (/\/conversations\/[^/]+\/runs$/.test(path) && req.method() === 'POST') ++runs;
    if (path === '/api/v1/companion/conversations' && req.method() === 'POST') ++setupWrites;
    if (/\/memory\/blocks\/[^/]+\/source$/.test(path)) ++sourceReads;
    if (req.method() === 'POST' && (path.includes('/companion/conversations') || path.includes('/memory/') || path.includes('/context/preview'))) {
      if (req.headers()['authorization'] || !req.headers()['x-csrf-token']) ++badCookieWrites;
    }
  });
  try {
    await registration(page, `context_${Date.now()}`, password);
    await expect(page.getByRole('button', { name: '角色与记忆', exact: true })).toBeHidden();
    await expect(page.getByLabel('新会话标题', { exact: true })).toBeHidden();
    await expect(page.getByLabel('聊天风格', { exact: true })).toBeHidden();
    const initialWallet = await walletSnapshot(page), initialEnergy = initialWallet.balance;

    phase = '默认 daily 幂等设置，不创建生成';
    const first = await responseFor<CompanionStart>(page, '/companion/conversations', 'POST', () => page.getByRole('button', { name: '新聊天', exact: true }).click());
    if (first.presetId !== 'daily' || first.replayed) throw new Error('默认预设错误。');
    await expect(page.getByRole('heading', { name: first.conversation.title, exact: true })).toBeVisible();
    await expect(page.getByLabel('生成模式', { exact: true })).toBeHidden();
    await expect(page.locator('.messages .message')).toHaveCount(0);
    if (runs !== 0 || setupWrites !== 1 || await wallet(page) !== initialEnergy) throw new Error('预设不应生成或扣费。');
    const firstText = '追溯测试第一轮：今天读完一小段书。<img src=x onerror="globalThis.contextXss=1">';
    const secondText = '追溯测试第二轮：接下来先休息一会儿。';
    let firstRun: Run | undefined;
    for (const content of [firstText, secondText]) {
      await page.getByLabel('消息内容', { exact: true }).fill(content);
      const run = await responseFor<Run>(page, `/conversations/${first.conversation.id}/runs`, 'POST', () => page.getByRole('button', { name: '发送', exact: true }).click());
      firstRun ??= run;
      await expect(page.locator(`[data-message-id="${run.assistantMessageId}"] .message-meta`)).toHaveText('助手 · 已完成');
      await expect(page.locator('.run-status')).toContainText('已完成');
      await expect(page.getByRole('button', { name: '发送', exact: true })).toBeEnabled();
    }
    await expect(page.locator('.messages .message')).toHaveCount(4);
    const chargedEnergy = await wallet(page);
    if (runs !== 2 || chargedEnergy !== initialEnergy - 2 * initialWallet.economyCost) throw new Error('仅两次显式经济模式生成可结算。');
    if ((await privateCounts(page, first.conversation.id)).blocks !== 0) throw new Error('两轮后摘要应等待显式规划或摘要。');

    phase = '人工确认记忆卡与只读规划回滚';
    await openAdvanced(page); await page.getByRole('button', { name: '角色与记忆', exact: true }).click();
    await expect(page.getByRole('button', { name: '确认保存记忆卡', exact: true })).toBeEnabled();
    const cardText = '人工边界标记：只按明确表述，不自动猜测亲密关系。<script>globalThis.contextXss=1</script>';
    await page.getByLabel('记忆卡分类', { exact: true }).selectOption('boundary');
    await page.getByLabel('记忆卡标题', { exact: true }).fill('用户明确边界');
    await page.getByLabel('记忆卡内容', { exact: true }).fill(cardText);
    await page.getByRole('button', { name: '读取最近可绑定用户消息', exact: true }).click();
    await page.getByLabel('绑定用户消息来源', { exact: true }).selectOption(firstRun!.userMessageId);
    await page.getByLabel('确认以上记忆由我填写并按此内容保存', { exact: true }).check();
    const card = await responseFor<MemoryCard>(page, `/conversations/${first.conversation.id}/memory/cards`, 'POST', () => page.getByRole('button', { name: '确认保存记忆卡', exact: true }).click());
    await expect(page.getByRole('article', { name: '记忆卡：用户明确边界', exact: true })).toContainText(cardText);
    if (sourceReads !== 0) throw new Error('未点选不得读取摘录原文。');
    await page.getByLabel('准备发送的文本（仅预览）', { exact: true }).fill('只预览，不发送。');
    const preview = await responseFor<ContextPreview>(page, `/conversations/${first.conversation.id}/context/preview`, 'POST', () => page.getByRole('button', { name: '预览实际上下文（不会发送）', exact: true }).click());
    if (preview.pendingBlockIds.length !== 1 || !preview.includedCardIds.includes(card.id) || preview.totalCharacters + preview.remainingCharacters !== preview.characterLimit) throw new Error('上下文规划缺少卡片、待摘录对或预算。');
    const output = page.getByLabel('上下文预算结果', { exact: true });
    await expect(output).not.toContainText(cardText);
    await expect(output.locator('details')).not.toHaveAttribute('open', '');
    await output.getByText('显式展开本次实际提示词文本（敏感）', { exact: true }).click();
    await expect(output).toContainText(cardText); await expect(output).toContainText('追溯测试第一轮');
    await expect(output.locator('img,script')).toHaveCount(0);
    if (await wallet(page) !== chargedEnergy || runs !== 2) throw new Error('记忆或预览不应生成扣费。');
    const afterPreview = await privateCounts(page, first.conversation.id);
    if (afterPreview.messages !== 4 || afterPreview.blocks !== 0) throw new Error('只读预览必须回滚待摘录写入并保留聊天。');

    phase = '规则摘录持久化与显式源消息';
    await page.getByRole('button', { name: '生成规则摘要', exact: true }).click();
    await expect(page.getByRole('status').filter({ hasText: '规则摘要已更新' })).toBeVisible();
    const blockPage = await responseFor<{ items: MemoryBlock[] }>(page, `/conversations/${first.conversation.id}/memory/blocks`, 'GET', () => page.getByRole('button', { name: '刷新历史摘录', exact: true }).click());
    if (blockPage.items.length !== 1 || blockPage.items[0].kind !== 'extractive_pair') throw new Error('window=1 应有一对可追溯摘录。');
    const block = blockPage.items[0]; const blockRow = page.getByRole('article', { name: `历史摘录块 ${block.id}`, exact: true });
    const source = await responseFor<MemoryBlockSource>(page, `/conversations/${first.conversation.id}/memory/blocks/${block.id}/source`, 'GET', () => blockRow.getByRole('button', { name: '查看此摘录来源原文', exact: true }).click());
    if (source.messages.length !== 2 || source.messages[0].content !== firstText || source.messages[0].runId !== source.messages[1].runId) throw new Error('摘录来源必须为原始完整对话对。');
    await expect(page.getByLabel('摘录块原文', { exact: true })).toContainText(firstText);
    await page.getByRole('button', { name: '关闭并清除摘录原文', exact: true }).click();
    await expect(page.getByLabel('摘录块原文', { exact: true })).toBeEmpty();
    const beforeToggle = await responseFor<ContextPreview>(page, `/conversations/${first.conversation.id}/context/preview`, 'POST', () => page.getByRole('button', { name: '预览实际上下文（不会发送）', exact: true }).click());
    if (!beforeToggle.includedBlockIds.includes(block.id) || !beforeToggle.includedCardIds.includes(card.id)) throw new Error('启用记忆必须进入本次规划。');

    phase = '停用只改变上下文，不删除原文';
    await blockRow.getByLabel('确认切换此历史摘录状态', { exact: true }).check();
    await responseFor(page, `/conversations/${first.conversation.id}/memory/blocks/${block.id}/update`, 'POST', () => blockRow.getByRole('button', { name: '停用此历史摘录', exact: true }).click());
    await expect(blockRow.getByRole('button', { name: '启用此历史摘录', exact: true })).toBeVisible();
    const cardRow = page.getByRole('article', { name: '记忆卡：用户明确边界', exact: true });
    await cardRow.getByLabel('确认切换此记忆卡状态', { exact: true }).check();
    await responseFor(page, `/conversations/${first.conversation.id}/memory/cards/${card.id}/update`, 'POST', () => cardRow.getByRole('button', { name: '停用此记忆卡', exact: true }).click());
    await expect(cardRow.getByRole('button', { name: '启用此记忆卡', exact: true })).toBeVisible();
    const disabled = await responseFor<ContextPreview>(page, `/conversations/${first.conversation.id}/context/preview`, 'POST', () => page.getByRole('button', { name: '预览实际上下文（不会发送）', exact: true }).click());
    if (disabled.includedCardIds.length || disabled.includedBlockIds.length || disabled.excludedCardCount !== 1 || disabled.omittedBlockCount !== 1 || disabled.messages.some(item => item.content.includes(cardText))) throw new Error('停用记忆不得继续入选。');
    const afterToggle = await privateCounts(page, first.conversation.id);
    if (afterToggle.messages !== 4 || afterToggle.blocks !== 1 || await wallet(page) !== chargedEnergy || runs !== 2) throw new Error('记忆控制不得删除原文、生成或扣费。');
    await page.getByRole('button', { name: '清除上下文预览', exact: true }).click(); await expect(output).toBeEmpty();

    phase = '可选 listen 风格与 owner 隔离';
    await page.getByRole('button', { name: '聊天', exact: true }).click();
    await openDetails(page, '聊天风格选项'); await page.getByLabel('聊天风格', { exact: true }).selectOption('listen');
    const listening = await responseFor<CompanionStart>(page, '/companion/conversations', 'POST', () => page.getByRole('button', { name: '新聊天', exact: true }).click());
    if (listening.presetId !== 'listen' || listening.conversation.id === first.conversation.id || setupWrites !== 2 || runs !== 2) throw new Error('更换风格只准备新的会话。');
    await expect(page.getByRole('heading', { name: listening.conversation.title, exact: true })).toBeVisible();
    await expect(page.locator('.messages .message')).toHaveCount(0);
    await registration(other, `context_other_${Date.now()}`, otherPassword);
    await expect(other.getByText('暂无会话。', { exact: true })).toBeVisible();
    const statuses = await other.evaluate(async id => {
      const sessionResponse = await fetch('/api/v1/auth/browser/session', { credentials: 'same-origin', cache: 'no-store' });
      if (!sessionResponse.ok) throw new Error('隔离账号会话核验失败。');
      const session = await sessionResponse.json();
      const paths = ['/state', '/memory/cards', '/memory/blocks'];
      const values = await Promise.all(paths.map(async path => (await fetch(`/api/v1/conversations/${encodeURIComponent(id)}${path}`, { credentials: 'same-origin', cache: 'no-store' })).status));
      values.push((await fetch(`/api/v1/conversations/${encodeURIComponent(id)}/context/preview`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrfToken }, body: JSON.stringify({ content: '不可读取外部上下文' }) })).status);
      session.csrfToken = ''; return values;
    }, first.conversation.id);
    if (statuses.some(status => status !== 404)) throw new Error('其他 owner 不得读取会话、记忆或预览。');
    if (badCookieWrites || sourceReads !== 1) throw new Error('Cookie 写入凭据或来源显式读取边界错误。');

    phase = '最终哈希六公开资源与实际离线边界';
    await page.evaluate(async () => { await Promise.race([(async () => { await navigator.serviceWorker.ready; if (!navigator.serviceWorker.controller) await new Promise<void>(resolve => navigator.serviceWorker.addEventListener('controllerchange', () => resolve(), { once: true })); })(), new Promise<never>((_, reject) => setTimeout(() => reject(new Error('公开壳未就绪。')), 8_000))]); });
    const paths = await page.evaluate(async () => {
      const values: string[] = []; for (const name of await caches.keys()) for (const item of await (await caches.open(name)).keys()) values.push(new URL(item.url).pathname); return values.sort();
    });
    if (paths.length !== 6 || new Set(paths).size !== 6 || !paths.includes('/test/') || !paths.includes('/test/manifest.webmanifest') || paths.filter(path => /^\/console-assets\/public-chat-[A-Z0-9]+\.(js|css)$/.test(path)).length !== 2 || paths.filter(path => /^\/console-assets\/public-icon-(192|512)-[a-f0-9]{16}\.png$/.test(path)).length !== 2) throw new Error('缓存必须恰为最终六项公开哈希资源，不得含私有响应。');
    if (await page.evaluate(() => localStorage.length + sessionStorage.length)) throw new Error('不得持久化客户端私有状态。');
    const iconSizes = await page.evaluate(async () => {
      const response = await fetch('/test/manifest.webmanifest', { credentials: 'omit' });
      if (!response.ok) throw new Error('公开 manifest 核验失败。');
      const manifest = await response.json(); const values: string[] = [];
      for (const icon of manifest.icons) {
        const image = new Image(); image.src = icon.src; await image.decode();
        if (`${image.naturalWidth}x${image.naturalHeight}` !== icon.sizes) throw new Error('公开 PNG 尺寸不符。');
        values.push(icon.sizes);
      }
      return values.sort();
    });
    if (iconSizes.join(',') !== '192x192,512x512') throw new Error('必须有两张可解码公开安装图标。');
    const cdp = await main.newCDPSession(page); await cdp.send('Page.enable');
    const installability = await cdp.send('Page.getInstallabilityErrors');
    if (installability.installabilityErrors.length) throw new Error('非隐私 profile 安装资格失败。');
    await main.setOffline(true); await cdp.send('Network.enable');
    await cdp.send('Network.overrideNetworkState', { offline: true, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    if (!await page.evaluate(async () => { try { await fetch('/api/v1/auth/browser/session', { cache: 'no-store', signal: AbortSignal.timeout(2000) }); return false; } catch { return true; } })) throw new Error('离线不得缓存私有会话。');
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: 'AlliraAi 聊天', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeHidden();
    await expect(page.getByRole('status').filter({ hasText: '当前离线' }).first()).toBeVisible();
    await main.setOffline(false); await cdp.send('Network.overrideNetworkState', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    await page.reload(); await expect(page.locator('.identity')).toContainText('已记住此设备');
    await expect(page.getByRole('button', { name: '角色与记忆', exact: true })).toBeHidden();
    if (await wallet(page) !== chargedEnergy || runs !== 2) throw new Error('恢复壳不得重新生成或扣费。');

    phase = '确认删除测试账号与登录失效';
    await erase(other, otherPassword); otherPassword = '';
    await erase(page, password); password = '';
    await page.reload(); await expect(page.getByRole('button', { name: '账号登录', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeHidden();
    if (errors.length || await page.evaluate(() => 'contextXss' in globalThis)) throw new Error('页面执行或安全 DOM 核验失败。');
  } catch { throw new Error(`实际 Mock 0.5 验收阶段失败：${phase}。原始错误、凭据及私有正文已隐藏。`); }
  finally {
    password = ''; otherPassword = '';
    await Promise.allSettled(contexts.map(context => context.close()));
    const target = await realpath(profile), parent = await realpath(tmpdir());
    if (dirname(target) !== parent || !basename(target).startsWith('allira-context-pwa-')) throw new Error('临时浏览器目录校验失败。');
    await rm(target, { recursive: true, force: true });
  }
});
