import { test, expect } from '@playwright/test';
import { account, c1, defaults, fixture, gate, json, loginAccount, loginDev, openLegacy, origin, stamp } from './browser-fixture.ts';

test('registration requires current terms/adult, accepts ten-character passwords, and never persists credentials', async ({ page }) => {
  const registrations: Record<string, unknown>[] = [];
  await fixture(page, async (route, path) => {
    if (path === '/auth/register') registrations.push(route.request().postDataJSON());
    return defaults(route, path);
  });
  await page.goto(`${origin}/test/`); await openLegacy(page);
  await page.getByLabel('账号用户名', { exact: true }).fill('account-bad');
  await page.getByLabel('账号密码（10–128 字符，最多 256 UTF-8 字节）', { exact: true }).fill('1234567890');
  await page.getByRole('button', { name: '注册账号', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '用户名为' })).toBeVisible();
  expect(registrations).toHaveLength(0);
  await page.getByLabel('账号用户名', { exact: true }).fill('account_1');
  await page.getByRole('button', { name: '注册账号', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '注册前请确认' })).toBeVisible();
  expect(registrations).toHaveLength(0);
  await page.getByLabel('我已满 18 岁', { exact: true }).check();
  await page.getByLabel('我已阅读并接受当前版本协议（注册必选）', { exact: true }).check();
  await page.getByLabel('账号显示名称（注册）', { exact: true }).fill('🔒'.repeat(61));
  await page.getByRole('button', { name: '注册账号', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '最多 240 UTF-8 字节' })).toBeVisible();
  expect(registrations).toHaveLength(0);
  await page.getByLabel('账号显示名称（注册）', { exact: true }).fill('账号甲');
  await page.getByRole('button', { name: '注册账号', exact: true }).click();
  await expect(page.getByRole('button', { name: '聊天', exact: true })).toBeVisible();
  expect(registrations).toEqual([{ username: 'account_1', password: '1234567890', displayName: '账号甲', acceptedTermsVersion: '2026-11-19', isAdult: true }]);
  expect(await page.locator('[name=accountPassword]').inputValue()).toBe('');
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('account password change and logout-all clear auth; a later 401 cannot leave protected views visible', async ({ page }) => {
  let passwordChanges = 0;
  let logoutAll = 0;
  let expired = false;
  await fixture(page, async (route, path) => {
    if (path === '/account/password') { passwordChanges++; expect(route.request().postDataJSON()).toEqual({ currentPassword: '1234567890', newPassword: '0987654321' }); return json(route, { ok: true }); }
    if (path === '/account/logout-all') logoutAll++;
    if (path === '/energy' && expired) return json(route, { error: { code: 'unauthorized', message: 'private-password-secret' } }, 401);
    return defaults(route, path);
  });
  await loginAccount(page);
  await page.getByRole('button', { name: '账号', exact: true }).click();
  await expect(page.getByLabel('显示名称', { exact: true })).toHaveValue(account.displayName);
  await page.getByLabel('当前密码', { exact: true }).fill('1234567890');
  await page.getByLabel('新密码', { exact: true }).fill('0987654321');
  await page.getByRole('button', { name: '修改密码并退出全部登录', exact: true }).click();
  expect(passwordChanges).toBe(0);
  await page.getByLabel('确认改密会撤销全部登录', { exact: true }).check();
  await page.getByRole('button', { name: '修改密码并退出全部登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
  expect(passwordChanges).toBe(1);
  await loginAccount(page);
  await page.getByRole('button', { name: '账号', exact: true }).click();
  await page.getByLabel('确认退出此账号全部会话', { exact: true }).check();
  await page.getByRole('button', { name: '退出全部账号登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
  expect(logoutAll).toBe(1);
  await loginAccount(page); expired = true;
  await page.getByRole('button', { name: '能量用量', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
  await expect(page.getByLabel('功能内容', { exact: true })).toBeHidden();
  await expect(page.locator('body')).not.toContainText('private-password-secret');
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('late account and role responses cannot overwrite a newer feature or user', async ({ page }) => {
  const accountGate = gate(); const rolesGate = gate();
  await fixture(page, async (route, path) => {
    if (path === '/account') { accountGate.received(); await accountGate.waiting; return json(route, { ...account, displayName: '迟到旧资料' }).catch(() => {}); }
    if (path === '/characters') { rolesGate.received(); await rolesGate.waiting; return json(route, { items: [{ id: 'ch1', name: '迟到旧角色', description: '', instructions: '', createdAt: stamp, updatedAt: stamp }], nextCursor: null }).catch(() => {}); }
    return defaults(route, path);
  });
  await loginAccount(page);
  await page.getByRole('button', { name: '账号', exact: true }).click(); await accountGate.ready;
  await page.getByRole('button', { name: '角色与记忆', exact: true }).click(); await rolesGate.ready;
  await page.getByRole('button', { name: '能量用量', exact: true }).click();
  accountGate.release(); rolesGate.release();
  await expect(page.getByRole('heading', { name: '能量与用量', exact: true })).toBeVisible();
  await expect(page.getByLabel('功能内容', { exact: true })).not.toContainText('迟到旧');
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await expect(page.getByRole('heading', { name: '登录后开始聊天', exact: true })).toBeVisible();
});

test('role CRUD and conversation memory use safe text and 409 errors do not leak response body', async ({ page }) => {
  let role = { id: 'ch1', name: '<script>inert</script>', description: '描述', instructions: '设定', createdAt: stamp, updatedAt: stamp };
  let created = false; let removed = false; let conflict = false;
  let memory = { conversationId: c1.id, characterId: null as string | null, coreMemory: '', relationship: '', summary: '', summarizedThrough: 0, updatedAt: stamp };
  await fixture(page, async (route, path) => {
    if (path === '/characters' && route.request().method() === 'POST') { created = true; role = { ...role, ...route.request().postDataJSON() }; return json(route, role); }
    if (path === '/characters') return json(route, { items: created && !removed ? [role] : [], nextCursor: null });
    if (path === '/characters/ch1/delete') { removed = true; return json(route, { ok: true }); }
    if (path === '/conversations/c1/memory' && route.request().method() === 'POST') {
      if (conflict) return json(route, { error: { code: 'conversation_busy', message: 'private-memory-secret' } }, 409);
      memory = { ...memory, ...route.request().postDataJSON() }; return json(route, memory);
    }
    if (path === '/conversations/c1/memory') return json(route, memory);
    if (path.endsWith('/memory/summarize')) return json(route, { ...memory, summary: '规则摘要纯文本', summarizedThrough: 2 });
    return defaults(route, path);
  });
  await loginDev(page);
  await page.getByRole('button', { name: '角色与记忆', exact: true }).click();
  await expect(page.getByRole('button', { name: '保存当前会话记忆', exact: true })).toBeEnabled();
  await page.getByLabel('角色名称', { exact: true }).fill(role.name);
  await page.getByLabel('角色描述', { exact: true }).fill(role.description);
  await page.getByLabel('角色设定', { exact: true }).fill(role.instructions);
  await page.getByRole('button', { name: '保存角色', exact: true }).click();
  await expect(page.getByRole('button', { name: role.name, exact: true })).toBeVisible();
  expect(await page.locator('.feature-content script').count()).toBe(0);
  await page.getByLabel('绑定角色', { exact: true }).selectOption('ch1');
  await page.getByLabel('核心记忆', { exact: true }).fill('必须保留的核心');
  await page.getByLabel('关系背景', { exact: true }).fill('朋友');
  await page.getByRole('button', { name: '保存当前会话记忆', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '核心记忆与关系已保存' })).toBeVisible();
  expect(memory).toMatchObject({ characterId: 'ch1', coreMemory: '必须保留的核心', relationship: '朋友' });
  await page.getByRole('button', { name: '生成规则摘要', exact: true }).click();
  await expect(page.getByLabel('当前记忆摘要', { exact: true })).toHaveText('规则摘要纯文本');
  conflict = true;
  await page.getByRole('button', { name: '保存当前会话记忆', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '409' })).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('private-memory-secret');
  await page.getByLabel('确认删除此角色', { exact: true }).check();
  await page.getByRole('button', { name: '删除角色', exact: true }).click();
  await expect(page.getByRole('button', { name: role.name, exact: true })).toHaveCount(0);
});

test('import preview is inert; uncertain commit survives feature switches with exact UUID, bytes and settings', async ({ page }) => {
  const bodies: Record<string, unknown>[] = [];
  let sourceReads = 0;
  const unsafe = JSON.stringify([{ role: 'user', content: '<img src=x onerror="globalThis.pwned=1">' }, { role: 'assistant', content: '历史答复' }]);
  const imported = { ...c1, id: 'imported', title: '导入会话' };
  await fixture(page, async (route, path) => {
    if (path === '/imports/preview') return json(route, { messages: [{ role: 'user', content: unsafe }], warnings: ['纯文本预览'], suggestedTitle: imported.title, sourceHash: 'fixture-hash' });
    if (path === '/imports/commit') { bodies.push(route.request().postDataJSON()); if (bodies.length === 1) return route.abort('connectionfailed'); return json(route, { conversation: imported, importedMessages: 2, archivedMessages: 0, replayed: true }); }
    if (path === '/conversations/imported/state') return json(route, { conversation: imported, activeRun: null, lastSequence: 2 });
    if (path.endsWith('/import-source')) { sourceReads++; return json(route, { format: 'json', content: unsafe, messages: [], warnings: [], suggestedTitle: imported.title, sourceHash: 'fixture-hash', archivedMessages: 0 }); }
    return defaults(route, path);
  });
  await loginDev(page);
  await page.getByRole('button', { name: '历史导入', exact: true }).click();
  await page.getByLabel('历史格式', { exact: true }).selectOption('json');
  await page.getByLabel('粘贴历史正文', { exact: true }).fill(unsafe);
  await page.getByRole('button', { name: '预览历史', exact: true }).click();
  await expect(page.getByLabel('导入预览结果', { exact: true })).toContainText(unsafe);
  expect(await page.locator('.feature-content img').count()).toBe(0);
  await page.getByLabel('导入后的核心记忆', { exact: true }).fill('原核心');
  await page.getByLabel('确认导入会创建新会话并保存历史', { exact: true }).check();
  await page.getByRole('button', { name: '确认导入为新会话', exact: true }).click();
  await expect(page.getByRole('button', { name: '确认导入为新会话', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '能量用量', exact: true }).click();
  await page.getByRole('button', { name: '历史导入', exact: true }).click();
  await expect(page.getByLabel('粘贴历史正文', { exact: true })).toHaveValue(unsafe);
  await page.getByLabel('粘贴历史正文', { exact: true }).fill('另一份草稿');
  await page.getByLabel('导入后的核心记忆', { exact: true }).fill('已改动核心');
  await page.getByLabel('确认导入会创建新会话并保存历史', { exact: true }).check();
  await page.getByRole('button', { name: '确认导入为新会话', exact: true }).click();
  await expect(page.getByRole('heading', { name: imported.title, exact: true })).toBeVisible();
  expect(bodies).toHaveLength(2); expect(bodies[0]).toEqual(bodies[1]);
  expect(bodies[0]).toMatchObject({ format: 'json', content: unsafe, coreMemory: '原核心', clientImportId: expect.stringMatching(/^[0-9a-f-]{36}$/) });
  expect(sourceReads).toBe(0);
  await page.getByRole('button', { name: '历史导入', exact: true }).click();
  await page.getByRole('button', { name: '查看当前会话导入原文', exact: true }).click();
  await expect(page.getByLabel('显式查看的导入原文', { exact: true })).toContainText(unsafe);
  expect(sourceReads).toBe(1);
  await page.getByRole('button', { name: '关闭导入原文', exact: true }).click();
  await expect(page.getByLabel('显式查看的导入原文', { exact: true })).toBeEmpty();
});

test('energy redemption clears the code and usage is reported as characters, not fabricated tokens', async ({ page }) => {
  await fixture(page, async (route, path) => {
    if (path === '/energy/redeem') { expect(route.request().postDataJSON()).toEqual({ code: 'fixture-redemption-code' }); return json(route, { balance: 120, unit: 'energy', economyCost: 1, fastCost: 5 }); }
    if (path === '/runs/run1/usage') return json(route, { runId: 'run1', requestedMode: 'fast', actualMode: 'economy', cost: 1, state: 'settled', promptCharacters: 20, outputCharacters: 14, firstTokenMs: 50, durationMs: 100, createdAt: stamp, updatedAt: stamp });
    return defaults(route, path);
  });
  await loginDev(page);
  await page.getByRole('button', { name: '能量用量', exact: true }).click();
  await expect(page.getByLabel('能量余额', { exact: true })).toContainText('20');
  await page.getByLabel('能量兑换码', { exact: true }).fill('fixture-redemption-code');
  await page.getByRole('button', { name: '兑换能量', exact: true }).click();
  await expect(page.getByLabel('能量兑换码', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('能量余额', { exact: true })).toContainText('120');
  await page.getByLabel('生成任务编号', { exact: true }).fill('run1');
  await page.getByRole('button', { name: '查看任务用量', exact: true }).click();
  await expect(page.getByLabel('生成任务用量', { exact: true })).toContainText('已结算');
  await expect(page.getByLabel('生成任务用量', { exact: true })).toContainText('输入字符');
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});

test('admin CDK validates server bounds, confirms writes, shows full codes once, and discards late result after switching', async ({ page }) => {
  let issues = 0; let disables = 0; const usageGate = gate();
  await fixture(page, async (route, path) => {
    expect(route.request().headers()['authorization']).toBeUndefined();
    if (path === '/admin/session') return json(route, { authenticated: true, csrfToken: 'fixture-csrf', expiresAt: stamp });
    if (path === '/admin/overview') return json(route, { users: 1, conversations: 1, messages: 0, runs: { running: 0, completed: 0, failed: 0, cancelled: 0 }, activeSessions: 1, environment: 'development', provider: 'mock', maxConcurrentRuns: 2 });
    if (path === '/admin/cdks' && route.request().method() === 'GET') return json(route, { items: [{ prefix: 'PREFIX123', energy: 100, expiresAt: 2000000000, disabled: false, redeemedBy: null, redeemedAt: null, createdAt: stamp }], nextCursor: null });
    if (path === '/admin/cdks' && route.request().method() === 'POST') { issues++; expect(route.request().headers()['x-csrf-token']).toBe('fixture-csrf'); return json(route, { items: [{ prefix: 'PREFIX123', code: 'fixture-full-redemption-secret', energy: 100, expiresAt: 2000000000 }] }); }
    if (path === '/admin/cdks/PREFIX123/disable') { disables++; expect(route.request().headers()['x-csrf-token']).toBe('fixture-csrf'); return json(route, { ok: true }); }
    if (path === '/admin/usage') { usageGate.received(); await usageGate.waiting; return json(route, { runs: 999, reservedEnergy: 1, settledEnergy: 1, refundedEnergy: 0, promptCharacters: 10, outputCharacters: 10, firstTokenMsAverage: 1 }).catch(() => {}); }
    return defaults(route, path);
  });
  await page.goto(`${origin}/admin/`);
  await page.getByRole('button', { name: '兑换码', exact: true }).click();
  await expect(page.getByRole('button', { name: '签发兑换码', exact: true })).toBeDisabled();
  await page.getByLabel('确认签发以上数量和能量的兑换码', { exact: true }).check();
  await page.getByLabel('每个码的整数能量', { exact: true }).fill('1000001');
  await page.getByRole('button', { name: '签发兑换码', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '能量 1–100 万' })).toBeVisible(); expect(issues).toBe(0);
  await page.getByLabel('每个码的整数能量', { exact: true }).fill('100');
  await page.getByLabel('签发数量', { exact: true }).fill('51');
  await page.getByRole('button', { name: '签发兑换码', exact: true }).click(); expect(issues).toBe(0);
  await page.getByLabel('签发数量', { exact: true }).fill('1');
  await page.getByRole('button', { name: '签发兑换码', exact: true }).click();
  await expect(page.getByLabel('本次签发的完整兑换码', { exact: true })).toContainText('fixture-full-redemption-secret');
  expect(issues).toBe(1);
  await page.getByRole('button', { name: '清除完整兑换码', exact: true }).click();
  await expect(page.locator('body')).not.toContainText('fixture-full-redemption-secret');
  await page.getByLabel('确认停用此前缀兑换码', { exact: true }).check();
  await page.getByRole('button', { name: '停用兑换码', exact: true }).click();
  await expect(page.getByRole('status').filter({ hasText: '兑换码已停用' })).toBeVisible(); expect(disables).toBe(1);
  await page.getByRole('button', { name: '能量用量', exact: true }).click(); await usageGate.ready;
  await page.getByRole('button', { name: '兑换码', exact: true }).click(); usageGate.release();
  await expect(page.getByRole('heading', { name: '兑换码管理', exact: true })).toBeVisible();
  await expect(page.getByLabel('管理内容', { exact: true })).not.toContainText('999');
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
});
