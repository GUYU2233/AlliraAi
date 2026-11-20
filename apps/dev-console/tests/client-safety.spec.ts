import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import { fixture, defaults, loginDev, openDeveloper, openChatAdvanced, origin, c1, stamp } from './browser-fixture.ts';

async function harness(page: import('@playwright/test').Page): Promise<void> {
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === '/harness/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><script type="module" src="/harness.js"></script></head><body><main id="app"></main></body></html>' });
    if (url.pathname === '/harness.js') return route.fulfill({ contentType: 'text/javascript', body: await readFile(new URL('../.test-dist/harness.js', import.meta.url)) });
    return route.abort();
  });
  await page.goto(`${origin}/harness/`);
  await page.waitForFunction(() => 'consoleHarness' in window);
}

test('bounded Markdown uses fixed safe DOM, no image fetch, safe links and gesture-only original code copy', async ({ page }) => {
  await harness(page);
  const outside: string[] = [];
  page.on('request', request => { if (new URL(request.url()).origin !== origin) outside.push(request.url()); });
  const source = '# 标题\n\n**加粗** *强调* `行内`\n\n> 引用\n\n1. 第一\n2. 第二\n\n![图片说明](https://tracking.invalid/image.png)\n\n<script>globalThis.pwned=1</script><svg onload="globalThis.pwned=2"></svg>\n\n[正常](https://example.invalid/path?q=1) [脚本](javascript:alert(1)) [数据](data:text/html,abc) [协议省略](//tracking.invalid/) [凭据](https://user:pass@example.invalid/) [相对](/api/v1/account)\n\n```javascript onload=alert(1)\n<script>alert(1)</script>\nconst x = 1;\n```';
  await page.evaluate(source => {
    const w = window as unknown as { consoleHarness: { renderMarkdown(source: string): Node }; copied: string[] };
    w.copied = [];
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text: string) => { w.copied.push(text); } } });
    document.querySelector('#app')!.append(w.consoleHarness.renderMarkdown(source));
  }, source);
  await expect(page.locator('#app h1')).toHaveText('标题');
  await expect(page.locator('#app strong')).toHaveText('加粗');
  await expect(page.locator('#app ol li')).toHaveText(['第一', '第二']);
  await expect(page.locator('#app img, #app svg, #app iframe, #app script')).toHaveCount(0);
  await expect(page.locator('#app a')).toHaveCount(1);
  await expect(page.locator('#app a')).toHaveAttribute('href', 'https://example.invalid/path?q=1');
  await expect(page.locator('#app a')).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(page.locator('#app a')).toHaveAttribute('target', '_blank');
  await expect(page.locator('#app a')).toHaveAttribute('referrerpolicy', 'no-referrer');
  expect(await page.evaluate(() => 'pwned' in globalThis)).toBe(false);
  expect(outside).toEqual([]);
  expect(await page.evaluate(() => (window as unknown as { copied: string[] }).copied)).toEqual([]);
  await page.getByRole('button', { name: '复制代码', exact: true }).click();
  expect(await page.evaluate(() => (window as unknown as { copied: string[] }).copied)).toEqual(['<script>alert(1)</script>\nconst x = 1;\n']);
  await page.evaluate(() => {
    const h = (window as unknown as { consoleHarness: { renderMarkdown(source: string): Node } }).consoleHarness;
    document.querySelector('#app')!.replaceChildren(h.renderMarkdown('**不解析**' + 'x'.repeat(64_001)));
  });
  await expect(page.locator('#app strong')).toHaveCount(0);
  await expect(page.locator('.markdown-plain')).toContainText('**不解析**');
});

test('stream painting retains stable historical nodes, focus and upper scroll; terminal bypasses 100ms timer', async ({ page }) => {
  await harness(page);
  const result = await page.evaluate(async () => {
    type H = { MessageList: new (box: HTMLElement) => { reconcile(messages: unknown[], view: unknown, mode: string): void; schedule(read: () => unknown): void }; beginReplay(run: unknown): { content: string; run: Record<string, unknown>; terminal: unknown } };
    const h = (window as unknown as { consoleHarness: H }).consoleHarness;
    const box = document.createElement('div'); box.style.cssText = 'height:160px;overflow:auto'; document.querySelector('#app')!.append(box);
    const messages = Array.from({ length: 50 }, (_, i) => ({ id: `m${i}`, role: 'user', status: 'completed', content: i === 0 ? '```txt\n历史代码\n```' : `历史 ${i}\n`.repeat(5) }));
    const run = { id: 'r1', assistantMessageId: 'a1', status: 'running' };
    const list = new h.MessageList(box); let view = h.beginReplay(run);
    list.reconcile(messages, view, 'bottom');
    box.scrollTop = 0;
    const first = box.querySelector('[data-message-id="m0"]')!;
    const copy = first.querySelector('button')!; copy.focus({ preventScroll: true });
    const body = box.querySelector('[data-message-id="a1"] .message-content')!;
    let changes = 0; const observer = new MutationObserver(() => { ++changes; }); observer.observe(body, { childList: true, characterData: true, subtree: true });
    for (let i = 0; i < 200; ++i) { view.content += 'a'; list.schedule(() => view); }
    await new Promise(resolve => setTimeout(resolve, 120));
    const painted = body.textContent;
    const beforeTerminal = changes;
    view = { ...view, content: '**权威终态**', run: { ...run, status: 'completed' }, terminal: {} };
    list.schedule(() => view);
    list.reconcile(messages, view, 'preserve');
    const terminalImmediate = body.querySelector('strong')?.textContent;
    await new Promise(resolve => setTimeout(resolve, 120)); observer.disconnect();
    return { stable: first === box.querySelector('[data-message-id="m0"]'), focused: document.activeElement === copy, top: box.scrollTop, painted, beforeTerminal, terminalImmediate, finalText: body.textContent };
  });
  expect(result.stable).toBe(true); expect(result.focused).toBe(true); expect(result.top).toBe(0);
  expect(result.painted).toBe('a'.repeat(200)); expect(result.beforeTerminal).toBeLessThanOrEqual(1);
  expect(result.terminalImmediate).toBe('权威终态'); expect(result.finalText).toBe('权威终态');
});

const run = { id: 'r1', conversationId: 'c1', clientMessageId: 'fixed', userMessageId: 'u1', assistantMessageId: 'a1', status: 'running', errorCode: null, createdAt: stamp, updatedAt: stamp };
const assistant = { id: 'a1', conversationId: 'c1', runId: 'r1', sequence: 2, role: 'assistant', content: '**权威终态**', status: 'completed', createdAt: stamp };
const event = (id: number, name: string, data: unknown) => `id: ${id}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

test('EOF reconnect is GET-only original run after0, resets replay and adopts canonical Markdown', async ({ page }) => {
  const requests: { method: string; path: string; after: string | null }[] = [];
  let streams = 0;
  await fixture(page, async (route, path) => {
    if (path.endsWith('/state')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ conversation: c1, activeRun: run, lastSequence: 2 }) });
    if (path.endsWith('/messages/recent')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [{ ...assistant, content: '旧片段', status: 'generating' }], nextCursor: null }) });
    if (path === '/runs/r1/events') {
      requests.push({ method: route.request().method(), path, after: new URL(route.request().url()).searchParams.get('after') });
      ++streams;
      const body = event(1, 'run.started', { run }) + event(2, 'message.delta', { messageId: 'a1', delta: '不能重复' }) + (streams > 1 ? event(3, 'run.completed', { run: { ...run, status: 'completed' }, message: assistant }) : '');
      return route.fulfill({ contentType: 'text/event-stream', body });
    }
    if (route.request().method() === 'POST' && path.includes('/runs')) throw new Error('Reconnect must not submit a POST');
    return defaults(route, path);
  });
  await loginDev(page);
  await expect(page.locator('.messages strong')).toHaveText('权威终态');
  await expect(page.locator('.messages')).not.toContainText('不能重复');
  expect(requests).toEqual([{ method: 'GET', path: '/runs/r1/events', after: '0' }, { method: 'GET', path: '/runs/r1/events', after: '0' }]);
});

for (const mode of ['manual', 'protocol', '401', 'exhausted'] as const) test(`stream ${mode} never retries indefinitely or posts a new run`, async ({ page }) => {
  await page.clock.install();
  let streams = 0;
  await fixture(page, async (route, path) => {
    if (path.endsWith('/state')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ conversation: c1, activeRun: run, lastSequence: 0 }) });
    if (path === '/runs/r1/events') {
      ++streams;
      if (mode === '401') return route.fulfill({ status: 401, contentType: 'application/json', body: JSON.stringify({ error: { code: 'unauthorized', message: 'secret-never-visible' } }) });
      if (mode === 'protocol') return route.fulfill({ contentType: 'text/event-stream', body: event(1, 'run.completed', { run: { ...run, status: 'completed' }, message: { ...assistant, id: 'wrong' } }) });
      return route.fulfill({ contentType: 'text/event-stream', body: event(1, 'run.started', { run }) });
    }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await openDeveloper(page);
  await page.getByLabel('开发用户 ID', { exact: true }).fill('fixture-dev');
  await page.getByRole('button', { name: '开发登录', exact: true }).click();
  await page.getByRole('button', { name: '会话甲', exact: true }).click();
  if (mode === 'manual') {
    await openChatAdvanced(page);
    await expect(page.locator('.run-status')).toContainText('等待自动恢复');
    await page.getByRole('button', { name: '断开订阅', exact: true }).click();
  } else if (mode === 'protocol') await expect(page.locator('.run-status')).toContainText('协议异常');
  else if (mode === '401') await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
  if (mode === 'exhausted') {
    for (let attempt = 1; attempt <= 5; ++attempt) { await expect.poll(() => streams).toBe(attempt); await page.clock.runFor(7_000); }
    await expect(page.locator('.run-status')).toContainText('5/5');
  }
  await page.clock.runFor(60_000);
  expect(streams).toBe(mode === 'exhausted' ? 6 : 1);
  await expect(page.locator('body')).not.toContainText('secret-never-visible');
});

for (const action of ['switch', 'logout'] as const) test(`stream retry timer is aborted on ${action}`, async ({ page }) => {
  await page.clock.install(); let streams = 0;
  const c2 = { ...c1, id: 'c2', title: '会话乙' };
  await fixture(page, async (route, path) => {
    if (path === '/conversations') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items: [c1, c2], nextCursor: null }) });
    if (path === '/conversations/c1/state') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ conversation: c1, activeRun: run, lastSequence: 0 }) });
    if (path === '/conversations/c2/state') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ conversation: c2, activeRun: null, lastSequence: 0 }) });
    if (path === '/runs/r1/events') { ++streams; return route.fulfill({ contentType: 'text/event-stream', body: event(1, 'run.started', { run }) }); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await openDeveloper(page); await page.getByLabel('开发用户 ID', { exact: true }).fill('fixture-dev');
  await page.getByRole('button', { name: '开发登录', exact: true }).click(); await page.getByRole('button', { name: '会话甲', exact: true }).click();
  await expect(page.locator('.run-status')).toContainText('等待自动恢复');
  if (action === 'switch') await page.getByRole('button', { name: '会话乙', exact: true }).click();
  else await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await page.clock.runFor(60_000); expect(streams).toBe(1);
});

test('offline recovery waits for the online event before GET-only replay', async ({ page }) => {
  await page.clock.install(); let streams = 0;
  await fixture(page, async (route, path) => {
    if (path.endsWith('/state')) return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ conversation: c1, activeRun: run, lastSequence: 0 }) });
    if (path === '/runs/r1/events') {
      ++streams;
      return route.fulfill({ contentType: 'text/event-stream', body: event(1, 'run.started', { run }) + (streams > 1 ? event(2, 'run.completed', { run: { ...run, status: 'completed' }, message: assistant }) : '') });
    }
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await openDeveloper(page); await page.getByLabel('开发用户 ID', { exact: true }).fill('fixture-dev');
  await page.getByRole('button', { name: '开发登录', exact: true }).click(); await page.getByRole('button', { name: '会话甲', exact: true }).click();
  await expect(page.locator('.run-status')).toContainText('等待自动恢复');
  await page.evaluate(() => { Object.defineProperty(navigator, 'onLine', { configurable: true, value: false }); window.dispatchEvent(new Event('offline')); });
  await page.clock.runFor(20_000); expect(streams).toBe(1);
  await page.evaluate(() => { Object.defineProperty(navigator, 'onLine', { configurable: true, value: true }); window.dispatchEvent(new Event('online')); });
  await page.clock.runFor(7_000); await expect(page.locator('.messages strong')).toHaveText('权威终态'); expect(streams).toBe(2);
});
