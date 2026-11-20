import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { fixture, defaults, loginDev, openAdvanced, account, origin, c1, json, gate, stamp } from './browser-fixture.ts';

const card = { id: 'card1', conversationId: c1.id, category: 'fact', title: '人工确认', content: '我喜欢纯文本', sourceMessageId: null, enabled: true, createdAt: stamp, updatedAt: stamp };
const block = { id: 'block1', conversationId: c1.id, kind: 'extractive_pair', sourceMessageIds: ['u1', 'a1'], startSequence: 1, endSequence: 2, summary: '规则摘录 <script>不是指令</script>', enabled: true, createdAt: stamp };
const message = (id: string, role: string, sequence: number, runId = 'r1', status = 'completed', content = `来源 ${id}`, conversationId = 'c1') => ({ id, conversationId, runId, role, sequence, status, content, createdAt: stamp });
const previewResult = (content: string) => ({ conversationId: c1.id, measurement: 'characters_not_tokens', characterLimit: 24000, totalCharacters: 123, remainingCharacters: 23877, sections: [{ kind: 'memory_cards', characters: 22, messages: 1 }, { kind: 'current_message', characters: content.length, messages: 1 }], messages: [{ role: 'system', content: 'private-actual-prompt <img src=x onerror="globalThis.pwned=1">' }, { role: 'user', content }], includedCardIds: ['card1'], includedBlockIds: ['block1'], pendingBlockIds: ['pending1'], excludedCardCount: 2, omittedBlockCount: 3, omittedRecentPairCount: 4, warnings: ['只用字符衡量，不是 Token。'] });
async function roles(page: Page): Promise<void> {
  await loginDev(page);
  await page.getByRole('button', { name: '角色与记忆', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认保存记忆卡', exact: true })).toBeEnabled();
}

// Every test intercepts all requests on a virtual loopback origin; no actual API or upstream is contacted.
test('memory cards are owner-confirmed, literal safe text, completed-user-pair sources only, and all mutations need confirmation', async ({ page }) => {
  const writes: { path: string; body: unknown }[] = []; let cards: typeof card[] = []; let recentReads = 0;
  await fixture(page, async (route, path) => {
    if (path === '/conversations/c1/memory/cards') {
      if (route.request().method() === 'GET') return json(route, { items: cards });
      const body = route.request().postDataJSON(); writes.push({ path, body }); cards = [{ ...card, ...body }]; return json(route, cards[0]);
    }
    if (path === '/conversations/c1/memory/cards/card1/update') { const body = route.request().postDataJSON(); writes.push({ path, body }); cards = [{ ...cards[0], ...body }]; return json(route, cards[0]); }
    if (path === '/conversations/c1/memory/cards/card1/delete') { writes.push({ path, body: null }); cards = []; return json(route, { ok: true }); }
    if (path === '/conversations/c1/messages/recent') {
      recentReads++; return json(route, { items: [message('u1', 'user', 1), message('a1', 'assistant', 2), message('u2', 'user', 3, 'cancelled'), message('a2', 'assistant', 4, 'cancelled', 'cancelled'), message('u3', 'user', 5, 'incomplete'), message('foreign', 'user', 6, 'foreign', 'completed', '外部用户', 'c2')], nextCursor: null });
    }
    return defaults(route, path);
  });
  await roles(page); const beforeSource = recentReads;
  const hostile = '<script>globalThis.pwned=1</script><img src=x onerror="globalThis.pwned=1">';
  await page.getByLabel('记忆卡标题', { exact: true }).fill('  人工确认  ');
  await page.getByLabel('记忆卡内容', { exact: true }).fill(hostile);
  await page.getByLabel('记忆卡分类', { exact: true }).selectOption('boundary');
  await page.getByRole('button', { name: '确认保存记忆卡', exact: true }).click(); expect(writes).toHaveLength(0);
  await page.getByRole('button', { name: '读取最近可绑定用户消息', exact: true }).click();
  await expect(page.getByLabel('绑定用户消息来源', { exact: true }).locator('option')).toHaveCount(2);
  expect(recentReads).toBe(beforeSource + 1);
  await page.getByLabel('绑定用户消息来源', { exact: true }).selectOption('u1');
  await page.getByLabel('确认以上记忆由我填写并按此内容保存', { exact: true }).check();
  await page.getByRole('button', { name: '确认保存记忆卡', exact: true }).click();
  const row = page.getByRole('article', { name: '记忆卡：人工确认', exact: true });
  await expect(row).toContainText(hostile); expect(writes[0].body).toEqual({ category: 'boundary', title: '人工确认', content: hostile, sourceMessageId: 'u1', enabled: true });
  expect(await page.locator('[aria-label="可追溯记忆与上下文"] script, [aria-label="可追溯记忆与上下文"] img').count()).toBe(0);
  expect(await page.evaluate(() => (globalThis as unknown as { pwned?: number }).pwned)).toBeUndefined();
  await row.getByRole('button', { name: '停用此记忆卡', exact: true }).click(); expect(writes).toHaveLength(1);
  await row.getByLabel('确认切换此记忆卡状态', { exact: true }).check(); await row.getByRole('button', { name: '停用此记忆卡', exact: true }).click();
  await expect(row).toContainText('已停用'); expect(writes[1].body).toEqual({ enabled: false });
  await page.getByLabel('记忆卡内容', { exact: true }).fill('编辑后的人工内容');
  await page.getByLabel('确认以上记忆由我填写并按此内容保存', { exact: true }).check(); await page.getByRole('button', { name: '确认保存记忆卡', exact: true }).click();
  await expect(row).toContainText('编辑后的人工内容');
  await row.getByRole('button', { name: '删除此记忆卡', exact: true }).click(); expect(writes).toHaveLength(3);
  await row.getByLabel('确认删除此记忆卡', { exact: true }).check(); await row.getByRole('button', { name: '删除此记忆卡', exact: true }).click();
  await expect(row).toHaveCount(0); expect(writes).toHaveLength(4);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('uncertain memory write is never retried automatically or unlocked by an unrelated list refresh; static conflicts and budget errors hide raw bodies', async ({ page }) => {
  let posts = 0, cardGets = 0; let status = 0;
  await fixture(page, async (route, path) => {
    if (path === '/conversations/c1/memory/cards') {
      if (route.request().method() === 'GET') { cardGets++; return json(route, { items: [] }); }
      posts++; if (posts === 1) return route.abort('connectionfailed');
      return json(route, { error: { code: status === 409 ? 'memory_limit_exceeded' : 'context_too_large', message: 'raw-private-memory-error' } }, status || 409);
    }
    return defaults(route, path);
  });
  await roles(page);
  await page.getByLabel('记忆卡标题', { exact: true }).fill('需核对'); await page.getByLabel('记忆卡内容', { exact: true }).fill('原文');
  await page.getByLabel('确认以上记忆由我填写并按此内容保存', { exact: true }).check(); await page.getByRole('button', { name: '确认保存记忆卡', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '结果不确定' })).toBeVisible(); expect(posts).toBe(1);
  await page.getByRole('button', { name: '刷新历史摘录', exact: true }).click();
  await page.getByLabel('确认以上记忆由我填写并按此内容保存', { exact: true }).check(); await page.getByRole('button', { name: '确认保存记忆卡', exact: true }).click(); expect(posts).toBe(1);
  await page.getByRole('button', { name: '刷新记忆卡', exact: true }).click(); await expect(page.getByRole('button', { name: '确认保存记忆卡', exact: true })).toBeEnabled(); expect(cardGets).toBe(2);
  status = 409; await page.getByRole('button', { name: '确认保存记忆卡', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '50 张卡' })).toBeVisible(); expect(posts).toBe(2);
  expect(await page.locator('body').textContent()).not.toContain('raw-private-memory-error');
});

test('history blocks are paginated and searched by summary; source is explicit, legacy source is honest, toggles confirmed and preserve original data', async ({ page }) => {
  const queries: string[] = [], sources: string[] = []; let toggles = 0; let currentBlock = { ...block };
  const legacy = { ...block, id: 'legacy', kind: 'legacy_summary', sourceMessageIds: [], summary: '旧版摘要文字' };
  await fixture(page, async (route, path) => {
    if (path === '/conversations/c1/memory/blocks') {
      const url = new URL(route.request().url()); queries.push(url.search);
      return json(route, { items: url.searchParams.has('cursor') ? [legacy] : [currentBlock], nextCursor: url.searchParams.has('cursor') || url.searchParams.get('q') ? null : 'cursor-two' });
    }
    if (path.endsWith('/source')) { sources.push(path); return json(route, path.includes('/legacy/') ? { block: legacy, messages: [], warnings: ['旧版摘要缺少可核对来源。'] } : { block: currentBlock, messages: [message('u1', 'user', 1, 'r1', 'completed', '<img src=x>来源原文'), message('a1', 'assistant', 2)], warnings: [] }); }
    if (path === '/conversations/c1/memory/blocks/block1/update') { toggles++; currentBlock = { ...currentBlock, ...route.request().postDataJSON() }; return json(route, currentBlock); }
    return defaults(route, path);
  });
  await roles(page); await expect(page.getByLabel('历史摘录列表', { exact: true })).toContainText(block.summary); expect(sources).toEqual([]);
  await page.getByRole('button', { name: '更多历史摘录', exact: true }).click(); await expect(page.getByLabel('历史摘录列表', { exact: true })).toContainText('旧版摘要文字');
  expect(queries[1]).toContain('cursor=cursor-two');
  const current = page.getByRole('article', { name: '历史摘录块 block1', exact: true });
  await current.getByRole('button', { name: '查看此摘录来源原文', exact: true }).click(); await expect(page.getByLabel('摘录块原文', { exact: true })).toContainText('<img src=x>来源原文'); expect(sources).toHaveLength(1);
  await page.getByRole('button', { name: '关闭并清除摘录原文', exact: true }).click(); await expect(page.getByLabel('摘录块原文', { exact: true })).toBeEmpty();
  await page.getByRole('article', { name: '历史摘录块 legacy', exact: true }).getByRole('button', { name: '查看此摘录来源原文', exact: true }).click();
  await expect(page.getByLabel('摘录块原文', { exact: true })).toContainText('没有可核对的来源原文');
  await current.getByRole('button', { name: '停用此历史摘录', exact: true }).click(); expect(toggles).toBe(0);
  await current.getByLabel('确认切换此历史摘录状态', { exact: true }).check(); await current.getByRole('button', { name: '停用此历史摘录', exact: true }).click();
  await expect(current).toContainText('已停用'); expect(toggles).toBe(1); await expect(page.getByLabel('摘录块原文', { exact: true })).toBeEmpty();
  await page.getByLabel('历史摘录检索词', { exact: true }).fill('只搜摘要'); await page.getByRole('button', { name: '检索历史摘录', exact: true }).click();
  await expect(page.getByRole('button', { name: '更多历史摘录', exact: true })).toBeHidden(); expect(queries.at(-1)).toContain('q='); expect(queries.at(-1)).not.toContain('cursor=');
  expect(sources).toHaveLength(2); expect(await page.locator('[aria-label="可追溯记忆与上下文"] script, [aria-label="可追溯记忆与上下文"] img').count()).toBe(0);
});

test('Cookie context preview is a CSRF-protected read-only POST, shows exact budgets and IDs, keeps prompt collapsed and clears drafts without generating', async ({ page }) => {
  const writes: string[] = []; let previews = 0;
  await fixture(page, async (route, path) => {
    if (path === '/auth/browser/session') return json(route, { user: account, csrfToken: 'memory-csrf', expiresAt: stamp });
    if (path === '/conversations/c1/context/preview') {
      expect(route.request().method()).toBe('POST'); expect(route.request().headers()['x-csrf-token']).toBe('memory-csrf'); expect(route.request().headers()['authorization']).toBeUndefined();
      writes.push(path); previews++; return json(route, previewResult(route.request().postDataJSON().content));
    }
    if (route.request().method() === 'POST') writes.push(path);
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await expect(page.locator('.identity')).toContainText('已记住此设备');
  await page.getByRole('button', { name: '会话甲', exact: true }).click(); await openAdvanced(page); await page.getByRole('button', { name: '角色与记忆', exact: true }).click();
  expect(previews).toBe(0); await page.getByLabel('准备发送的文本（仅预览）', { exact: true }).fill('  仅预览原文\n');
  await page.getByRole('button', { name: '预览实际上下文（不会发送）', exact: true }).click();
  const output = page.getByLabel('上下文预算结果', { exact: true });
  await expect(output).toContainText('24000'); await expect(output).toContainText('23877'); await expect(output).toContainText('card1'); await expect(output).toContainText('pending1');
  await expect(output).not.toContainText('private-actual-prompt'); await expect(output.locator('details')).not.toHaveAttribute('open', '');
  await output.getByText('显式展开本次实际提示词文本（敏感）', { exact: true }).click(); await expect(output).toContainText('private-actual-prompt'); expect(await output.locator('img').count()).toBe(0);
  await page.getByLabel('准备发送的文本（仅预览）', { exact: true }).fill('另一份预览'); await expect(output).toBeEmpty();
  await page.getByRole('button', { name: '清除上下文预览', exact: true }).click(); await expect(page.getByLabel('准备发送的文本（仅预览）', { exact: true })).toHaveValue('');
  expect(writes).toEqual(['/conversations/c1/context/preview']);
  await page.getByRole('button', { name: '聊天', exact: true }).click(); await expect(page.getByLabel('消息内容', { exact: true })).toHaveValue('');
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('memory preview 413/422/403 errors stay generic, 401 clears private feature and never triggers generation', async ({ page }) => {
  let status = 413; const posts: string[] = [];
  await fixture(page, async (route, path) => {
    if (path === '/conversations/c1/context/preview') { posts.push(path); return json(route, { error: { code: 'private_error_code', message: 'raw-sensitive-prompt' } }, status); }
    return defaults(route, path);
  });
  await roles(page); await page.getByLabel('准备发送的文本（仅预览）', { exact: true }).fill('不会发送');
  for (const errorStatus of [413, 422, 403]) {
    status = errorStatus; await page.getByRole('button', { name: '预览实际上下文（不会发送）', exact: true }).click(); await expect(page.getByRole('button', { name: '预览实际上下文（不会发送）', exact: true })).toBeEnabled();
    await expect(page.getByLabel('上下文预算结果', { exact: true })).toBeEmpty(); await expect(page.locator('body')).not.toContainText('raw-sensitive-prompt');
  }
  status = 401; await page.getByRole('button', { name: '预览实际上下文（不会发送）', exact: true }).click(); await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
  expect(posts).toHaveLength(4); expect(posts.every(path => path.endsWith('/context/preview'))).toBe(true);
  await expect(page.getByLabel('功能内容', { exact: true })).toBeEmpty();
});

test('late block queries, source reads and context previews cannot repopulate a changed query, feature, conversation or user', async ({ page }) => {
  const delayedQuery = gate(), delayedSource = gate(), delayedPreview = gate(); let heldQuery = false;
  const c2 = { ...c1, id: 'c2', title: '会话乙' };
  await fixture(page, async (route, path) => {
    if (path === '/conversations') return json(route, { items: [c1, c2], nextCursor: null });
    if (path === '/conversations/c2/state') return json(route, { conversation: c2, activeRun: null, lastSequence: 0 });
    if (path === '/conversations/c1/memory/blocks') {
      if (new URL(route.request().url()).searchParams.get('q') === '旧检索') { heldQuery = true; delayedQuery.received(); await delayedQuery.waiting; return json(route, { items: [{ ...block, summary: '迟到旧检索私密摘要' }], nextCursor: null }).catch(() => {}); }
      return json(route, { items: [block], nextCursor: null });
    }
    if (path === '/conversations/c1/memory/blocks/block1/source') { delayedSource.received(); await delayedSource.waiting; return json(route, { block, messages: [message('u1', 'user', 1, 'r1', 'completed', '迟到原文私密内容')], warnings: [] }).catch(() => {}); }
    if (path === '/conversations/c1/context/preview') { delayedPreview.received(); await delayedPreview.waiting; return json(route, previewResult('迟到上下文私密内容')).catch(() => {}); }
    return defaults(route, path);
  });
  await roles(page); await page.getByLabel('历史摘录检索词', { exact: true }).fill('旧检索'); await page.getByRole('button', { name: '检索历史摘录', exact: true }).click(); await delayedQuery.ready;
  await page.getByLabel('历史摘录检索词', { exact: true }).fill('新检索'); await page.getByRole('button', { name: '检索历史摘录', exact: true }).click(); delayedQuery.release();
  await expect(page.getByLabel('历史摘录列表', { exact: true })).toContainText(block.summary); await expect(page.locator('body')).not.toContainText('迟到旧检索私密摘要'); expect(heldQuery).toBe(true);
  await page.getByRole('article', { name: '历史摘录块 block1', exact: true }).getByRole('button', { name: '查看此摘录来源原文', exact: true }).click(); await delayedSource.ready;
  await page.getByLabel('准备发送的文本（仅预览）', { exact: true }).fill('待预览'); await page.getByRole('button', { name: '预览实际上下文（不会发送）', exact: true }).click(); await delayedPreview.ready;
  await page.getByRole('button', { name: '聊天', exact: true }).click(); await page.getByRole('button', { name: '会话乙', exact: true }).click();
  await page.getByRole('button', { name: '角色与记忆', exact: true }).click(); delayedSource.release(); delayedPreview.release();
  await expect(page.getByRole('heading', { name: '当前会话：会话乙', exact: true })).toBeVisible();
  await expect(page.getByLabel('摘录块原文', { exact: true })).toBeEmpty(); await expect(page.getByLabel('上下文预算结果', { exact: true })).toBeEmpty();
  await expect(page.locator('body')).not.toContainText('迟到原文私密内容'); await expect(page.locator('body')).not.toContainText('迟到上下文私密内容');
  await page.getByRole('button', { name: '退出登录', exact: true }).click(); await expect(page.getByLabel('功能内容', { exact: true })).toBeEmpty();
});
