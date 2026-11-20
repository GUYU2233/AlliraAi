import { AlliraApiError } from '../../../packages/client/src/index.ts';
import type { AdminDeviceSession, AdminDiagnostics, AdminUser, AdminUserListOptions, RevokeSessionsResult } from '../../../packages/client/src/index.ts';
import type { AdminFeatureContext } from './admin-economy.ts';
import { append, button, el, empty, field, input, isAbort, when } from './dom.ts';
import { details, table } from './feature.ts';

type ControlView = 'users' | 'routes' | 'diagnostics';
const count = (value: unknown): string => typeof value === 'number' && Number.isFinite(value) ? String(value) : '未知';
const yesNo = (value: unknown): string => value === true ? '是' : value === false ? '否' : '未知';
const date = (value: unknown): string => typeof value === 'string' ? when(value) : value === null ? '无' : '未知';
const identityLabels = { oidc: '已关联开源身份', legacy: '旧账号（待迁移）', developer: '开发身份' } as const;
const backupLabels = { missing: '缺失', recent: '近期', stale: '陈旧', invalid: '无效' } as const;
function identity(value: AdminUser['identityStatus']): string { return value && value in identityLabels ? identityLabels[value] : '未知（旧服务未提供）'; }
function backupStatus(value: string): string { return backupLabels[value as keyof typeof backupLabels] ?? '未知'; }
function maskedPhone(value: unknown): string {
  // Only the explicitly masked DTO field is eligible for display. Never fall back to a raw phone.
  return value === null ? '未提供' : typeof value === 'string' && /\*/.test(value) && /^[+\d* -]{4,32}$/.test(value) ? value : '未知';
}
function queryValue(raw: string): string | null {
  if (/[\p{Cc}\p{Cf}\p{Cs}]/u.test(raw)) return null;
  const value = raw.trim();
  return Array.from(value).length <= 100 && new TextEncoder().encode(value).length <= 400 ? value : null;
}
function uncertain(error: unknown): boolean { return !(error instanceof AlliraApiError) || error.status >= 500; }

/** Native admin-only views. This module never uses ordinary identity clients or Logto Management. */
export function mountAdminControl(context: AdminFeatureContext, view: ControlView): void {
  if (view === 'users') mountUsers(context);
  else if (view === 'routes') mountRoutes(context);
  else mountDiagnostics(context);
}

function mountUsers(context: AdminFeatureContext): void {
  const { client, target, signal } = context;
  const query = input('adminUserQuery', '用户 ID、用户名或显示名称；不搜索手机号'); query.maxLength = 400;
  const kind = el('select', { name: 'adminUserIdentity', 'aria-label': '身份类型' });
  for (const [value, label] of [['all', '全部'], ['oidc', '已关联开源身份'], ['legacy', '旧账号（待迁移）'], ['developer', '开发身份']]) append(kind, el('option', { value }, label));
  const filters = el('form', { class: 'inline-form', 'aria-label': '用户筛选' }, field('搜索用户', query), field('身份类型', kind));
  const listing = table(['用户 ID', '账号资料', '身份摘要', '能量余额', '创建时间', '会话数', '任务数', '有效用户登录', '操作'], '用户身份与业务摘要');
  const state = el('p', { role: 'status' });
  const deviceDetail = el('section', { class: 'sensitive-detail', 'aria-label': '用户设备会话' });
  let queryEpoch = 0, ownerEpoch = 0, pageEpoch = 0;
  let queryAbort = new AbortController(), ownerAbort = new AbortController();
  let selectedOwner: string | null = null, cursor: string | null = null, loading = false;
  let refreshDevices: (() => Promise<void>) | null = null;
  let activeFilter: { q: string; identity: NonNullable<AdminUserListOptions['identity']> } = { q: '', identity: 'all' };
  const records = new Map<string, { user: AdminUser; row: HTMLTableRowElement; summary: HTMLElement; sessions: HTMLElement }>();
  const writing = new Set<string>(), unknownWrites = new Set<string>(), controlUpdates = new Set<() => void>();
  const valid = () => context.valid() && !signal.aborted;
  const sync = () => { for (const update of controlUpdates) update(); };
  const filterValid = (own: number) => valid() && own === queryEpoch && !queryAbort.signal.aborted;
  const more = button('加载更多', () => loadUsers(true));
  const refresh = button('刷新列表', () => loadUsers(false));
  const submit = button('筛选用户', () => loadUsers(false));
  append(filters, submit);
  append(target, el('h2', {}, '用户'), filters, empty('搜索只匹配用户 ID、用户名和显示名称，百分号等按字面处理，不搜索手机原文。身份字段缺失表示未知，不推断为正常或开发账号。'), listing.element, state, el('div', { class: 'actions' }, refresh, more), empty('撤销仅影响 AlliraAi 当前本地账号／开发登录和相应待处理证明，不是封禁、删除账号或 Logto 全局 SSO 注销。撤销当前会话不阻止重新开发登录或经认证重新登录。Logto Console 属于独立的外部身份管理，普通登录不授予后台权限。'), deviceDetail);

  function closeOwner(): void { ++ownerEpoch; ownerAbort.abort(); ownerAbort = new AbortController(); selectedOwner = null; refreshDevices = null; deviceDetail.replaceChildren(); }
  function invalidateQuery(): void {
    ++queryEpoch; ++pageEpoch; queryAbort.abort(); queryAbort = new AbortController(); closeOwner();
    records.clear(); listing.body.replaceChildren(); cursor = null; loading = false; more.hidden = true; refresh.disabled = false; submit.disabled = false;
    state.textContent = '筛选已变更，请点击“筛选用户”。';
  }
  query.addEventListener('input', invalidateQuery); kind.addEventListener('change', invalidateQuery);
  filters.addEventListener('submit', event => { event.preventDefault(); void loadUsers(false); });
  signal.addEventListener('abort', () => { ++queryEpoch; ++ownerEpoch; queryAbort.abort(); ownerAbort.abort(); query.value = ''; records.clear(); writing.clear(); unknownWrites.clear(); controlUpdates.clear(); listing.body.replaceChildren(); deviceDetail.replaceChildren(); }, { once: true });

  function userSummary(user: AdminUser): HTMLElement {
    return details([['业务账号类型', user.accountType === 'registered' ? '注册账号' : user.accountType === 'developer' ? '开发身份' : '未知'], ['身份', identity(user.identityStatus)], ['脱敏手机号', maskedPhone(user.maskedPhone)], ['手机已验证', yesNo(user.phoneVerified)], ['历史密码已设置', yesNo(user.passwordEnabled)]]);
  }
  function confirmAction(ownerId: string, label: string, actionLabel: string, scopeSignal: AbortSignal, localValid: () => boolean, action: () => Promise<RevokeSessionsResult>, after: () => Promise<void>): HTMLElement {
    const consent = el('input', { type: 'checkbox' });
    const go = button(actionLabel, async () => {
      if (!consent.checked || writing.size || unknownWrites.has(ownerId) || !localValid()) return;
      if (!navigator.onLine) { context.notify('当前离线，不能撤销登录。'); return; }
      const selected = ownerEpoch;
      const apply = () => localValid() && selected === ownerEpoch;
      consent.checked = false; writing.add(ownerId); sync();
      try {
        const result = await action();
        if (!apply()) return;
        context.notify(`已撤销 ${count(result.revoked)} 个用户登录（仅本地）。不是封禁或全局 SSO 注销；撤销当前会话不阻止重新开发登录或经认证重新登录。`);
        await after();
      } catch (error) {
        if (apply() && !isAbort(error)) {
          if (uncertain(error)) unknownWrites.add(ownerId);
          context.fail(error);
          if (apply() && uncertain(error)) context.notify('撤销结果不确定，未自动重试。请显式刷新对应设备或用户列表，核对后再重新确认。');
        }
      } finally { writing.delete(ownerId); if (valid()) sync(); }
    });
    const update = () => { go.disabled = !consent.checked || writing.size > 0 || unknownWrites.has(ownerId) || !localValid(); };
    controlUpdates.add(update); consent.addEventListener('change', update); update();
    scopeSignal.addEventListener('abort', () => { consent.checked = false; controlUpdates.delete(update); }, { once: true });
    return el('div', { class: 'confirm-action' }, field(label, consent), go);
  }
  async function refreshOwner(ownerId: string, own: number): Promise<void> {
    if (!filterValid(own)) return;
    const selection = ownerEpoch;
    try {
      const result = await client.listUsers({ q: ownerId, identity: 'all', limit: 100, signal: queryAbort.signal });
      if (!filterValid(own) || selection !== ownerEpoch) return;
      const found = result.items.find(user => user.userId === ownerId), record = records.get(ownerId);
      if (!record || !found) return;
      record.user = found; record.sessions.textContent = count(found.activeSessions); record.summary.replaceChildren(userSummary(found));
    } catch (error) { if (filterValid(own) && selection === ownerEpoch && !isAbort(error)) context.fail(error); }
  }
  function appendUser(user: AdminUser, own: number): void {
    if (records.has(user.userId)) return;
    const localValid = () => filterValid(own) && records.has(user.userId);
    const summary = el('div', {}, userSummary(user)), sessions = el('span', {}, count(user.activeSessions));
    const actions = el('div', { class: 'row-actions' }, button('查看设备', () => openOwner(user.userId, own)));
    append(actions, confirmAction(user.userId, '确认撤销此用户当前全部登录', '撤销用户登录', queryAbort.signal, localValid, () => client.revokeUserSessions(user.userId, { signal: queryAbort.signal }), async () => {
      await refreshOwner(user.userId, own);
      if (selectedOwner === user.userId && localValid()) await refreshDevices?.();
    }));
    const row = el('tr', {}, el('td', {}, user.userId), el('td', {}, details([['用户名', user.username === undefined ? '未知' : user.username ?? '无'], ['显示名称', user.displayName === undefined ? '未知' : user.displayName ?? '无']])), el('td', {}, summary), el('td', {}, user.walletBalance === null ? '未设置' : count(user.walletBalance)), el('td', {}, date(user.createdAt)), el('td', {}, count(user.conversationCount)), el('td', {}, count(user.runCount)), el('td', {}, sessions), el('td', {}, actions));
    records.set(user.userId, { user, row, summary, sessions }); append(listing.body, row); sync();
  }
  async function loadUsers(appendRows: boolean): Promise<void> {
    if (!valid() || (appendRows && (loading || !cursor))) return;
    if (!appendRows) {
      const normalized = queryValue(query.value);
      invalidateQuery();
      if (normalized === null) { state.textContent = '搜索文字不合法：最多 100 字符、400 UTF-8 字节，不可包含控制字符或隐藏格式字符。'; return; }
      activeFilter = { q: normalized, identity: kind.value as NonNullable<AdminUserListOptions['identity']> };
    }
    const own = queryEpoch, page = ++pageEpoch, readSignal = queryAbort.signal;
    loading = true; more.disabled = true; refresh.disabled = true; submit.disabled = true; state.textContent = '正在读取…';
    try {
      const result = await client.listUsers({ ...(activeFilter.q ? { q: activeFilter.q } : {}), identity: activeFilter.identity, ...(appendRows && cursor ? { cursor } : {}), limit: 30, signal: readSignal });
      if (!filterValid(own) || page !== pageEpoch) return;
      for (const user of result.items) { unknownWrites.delete(user.userId); appendUser(user, own); }
      cursor = result.nextCursor; more.hidden = !cursor; state.textContent = records.size ? `已显示 ${records.size} 条记录。` : '暂无记录。';
    } catch (error) { if (filterValid(own) && page === pageEpoch && !isAbort(error)) { state.textContent = '读取失败，请显式刷新。'; context.fail(error); } }
    finally { if (filterValid(own) && page === pageEpoch) { loading = false; more.disabled = false; refresh.disabled = false; submit.disabled = false; sync(); } }
  }
  async function openOwner(ownerId: string, own: number): Promise<void> {
    if (!filterValid(own)) return;
    closeOwner(); selectedOwner = ownerId;
    const selected = ownerEpoch, readSignal = ownerAbort.signal;
    const ownerValid = () => filterValid(own) && selected === ownerEpoch && selectedOwner === ownerId && !readSignal.aborted;
    const devices = table(['设备标签', '本地会话类型', '创建时间', '最近活动', '到期时间', '操作'], '当前用户有效本地设备会话');
    const message = el('p', { role: 'status' });
    let deviceCursor: string | null = null, deviceBusy = false, deviceEpoch = 0, deviceRowsEpoch = 0;
    let deviceRowsAbort = new AbortController();
    readSignal.addEventListener('abort', () => deviceRowsAbort.abort(), { once: true });
    const seen = new Set<string>();
    const next = button('加载更多设备', () => load(true)), reload = button('刷新设备列表', () => load(false));
    deviceDetail.replaceChildren(el('h3', {}, `设备：${ownerId}`), empty('只有点击“查看设备”才请求本页。列表只含仍有效的 AlliraAi 本地登录；撤销不会删除聊天、能量、身份或全局 SSO。'), devices.element, message, el('div', { class: 'actions' }, reload, next, button('关闭设备', closeOwner)));
    async function load(appendRows: boolean): Promise<void> {
      if (!ownerValid() || deviceBusy || (appendRows && !deviceCursor)) return;
      const request = ++deviceEpoch; deviceBusy = true; next.disabled = true; reload.disabled = true;
      message.textContent = '正在读取设备…';
      try {
        const result = await client.listUserSessions(ownerId, { ...(appendRows && deviceCursor ? { cursor: deviceCursor } : {}), limit: 30, signal: readSignal });
        if (!ownerValid() || request !== deviceEpoch) return;
        if (!appendRows) { ++deviceRowsEpoch; deviceRowsAbort.abort(); deviceRowsAbort = new AbortController(); devices.body.replaceChildren(); seen.clear(); unknownWrites.delete(ownerId); }
        for (const item of result.items) {
          if (seen.has(item.id)) continue;
          seen.add(item.id);
          const rows = deviceRowsEpoch;
          const action = confirmAction(ownerId, '确认仅撤销此设备的本地登录', '撤销此设备登录', deviceRowsAbort.signal, () => ownerValid() && rows === deviceRowsEpoch, () => client.revokeUserSession(ownerId, item.id, { signal: readSignal }), async () => { await refreshOwner(ownerId, own); if (ownerValid()) await load(false); });
          append(devices.body, deviceRow(item, action));
        }
        deviceCursor = result.nextCursor; next.hidden = !deviceCursor; message.textContent = `已显示 ${seen.size} 个有效本地设备。`;
      } catch (error) { if (ownerValid() && request === deviceEpoch && !isAbort(error)) { message.textContent = '读取失败，请显式刷新。'; context.fail(error); } }
      finally { if (ownerValid() && request === deviceEpoch) { deviceBusy = false; next.disabled = false; reload.disabled = false; sync(); } }
    }
    refreshDevices = () => load(false);
    await load(false);
  }
  function deviceRow(item: AdminDeviceSession, action: HTMLElement): HTMLTableRowElement {
    return el('tr', {}, el('td', {}, item.label), el('td', {}, item.type === 'browser' ? '浏览器 Cookie' : item.type === 'bearer' ? '内存 Bearer' : '未知'), el('td', {}, date(item.createdAt)), el('td', {}, date(item.lastSeenAt)), el('td', {}, date(item.expiresAt)), el('td', {}, action));
  }
  void loadUsers(false);
}

function mountRoutes(context: AdminFeatureContext): void {
  const output = el('section', { 'aria-label': '只读路由配置' });
  let busy = false;
  const refresh = button('刷新路由配置', async () => {
    if (!context.valid() || busy) return;
    busy = true; refresh.disabled = true;
    try {
      const value = await context.client.getRoutes({ signal: context.signal });
      if (!context.valid()) return;
      const routes = table(['模式', '供应商类型', '已配置', '每次能量', '回退模式'], '当前路由配置（不是可用性探测）');
      for (const item of value.items) append(routes.body, el('tr', {}, el('td', {}, item.mode === 'economy' ? '经济' : item.mode === 'fast' ? '极速' : '未知'), el('td', {}, item.provider === 'mock' ? 'Mock' : item.provider === 'openai' ? 'OpenAI 兼容' : '未知'), el('td', {}, yesNo(item.configured)), el('td', {}, count(item.energyCost)), el('td', {}, item.fallbackTo === 'economy' ? '经济' : item.fallbackTo === null ? '无' : '未知')));
      output.replaceChildren(routes.element, details([['生成超时秒数', count(value.limits.runTimeoutSeconds)], ['最大并发', count(value.limits.maxConcurrentRuns)], ['输入字符上限', count(value.limits.maxPromptCharacters)], ['输出字符上限', count(value.limits.maxOutputCharacters)], ['上游 max_tokens 配置', count(value.limits.upstreamMaxTokens)], ['每分钟生成频率配置', count(value.limits.runRatePerMinute)]]), empty(value.configurationOnly === true ? '仅配置摘要（configurationOnly=true），未调用模型，不是可用性、额度、报价或容量验证。' : '配置标志未知；本页不执行模型探测。'));
    } catch (error) { if (context.valid() && !isAbort(error)) context.fail(error); }
    finally { if (context.valid()) { busy = false; refresh.disabled = false; } }
  });
  append(context.target, el('h2', {}, '路由配置'), empty('只读管理摘要：不展示或编辑 Key、上游地址、模型秘密配置，不发送模型请求。'), refresh, output);
  context.signal.addEventListener('abort', () => output.replaceChildren(), { once: true }); void refresh.click();
}

function mountDiagnostics(context: AdminFeatureContext): void {
  const output = el('section', { 'aria-label': '脱敏健康诊断' });
  let busy = false;
  const refresh = button('刷新健康诊断', async () => {
    if (!context.valid() || busy) return;
    busy = true; refresh.disabled = true;
    try {
      const value = await context.client.getDiagnostics({ signal: context.signal });
      if (!context.valid()) return;
      const identity = value.identity, backup = value.identityBackup;
      const business = details([
        ['版本', value.version], ['启动时间', date(value.startedAt)], ['运行秒数', count(value.uptimeSeconds)], ['数据库结构版本', count(value.schemaVersion)],
        ['数据库检查', value.database.ok ? '正常' : '异常'], ['日志模式', value.database.journalMode], ['数据库字节数', count(value.database.databaseBytes)], ['WAL 字节数', count(value.database.walBytes)],
        ['生成中', count(value.runs.running)], ['运行中处理', count(value.runs.inFlight)], ['关闭中', count(value.runs.closing)], ['并发容量', count(value.runs.capacity)],
        ['Bearer 会话', count(value.sessions.bearer)], ['浏览器会话', count(value.sessions.browser)], ['管理会话', count(value.sessions.admin)],
        ['已配置备份', yesNo(value.backup.configured)], ['最近备份', date(value.backup.latestAt)], ['备份秒龄', count(value.backup.ageSeconds)], ['备份状态', backupStatus(value.backup.status)],
      ]);
      output.replaceChildren(el('h3', {}, '业务数据库（SQLite）'), business, el('h3', {}, '认证运行门禁'), identity ? details([
        ['认证后端', identity.backend === 'oidc' ? 'OIDC / Logto' : identity.backend === 'legacy' ? '旧兼容后端' : '未知'], ['已配置', yesNo(identity.configured)], ['运行门禁', identity.enabled ? '已启用（不代表真实渠道已验收）' : '已关闭'], ['模拟渠道标志', yesNo(identity.simulated)],
        ['已关联账号', count(identity.linkedAccounts)], ['旧账号', count(identity.legacyAccounts)], ['开发身份', count(identity.developerUsers)], ['待处理过渡流程', count(identity.pendingFlows)],
        ['身份服务探测', identity.serviceProbe === 'not_performed' ? '未执行（not_performed）' : '未知'], ['身份管理', identity.administration === 'separate' ? '独立 Logto Console，不授予普通登录者管理权限' : '未知'],
      ]) : empty('身份诊断未知：旧服务未提供 identity，不推断运行门禁、关联人数或身份服务正常。'), el('h3', {}, '身份数据库（Logto PostgreSQL）'), backup ? details([
        ['已配置身份备份', yesNo(backup.configured)], ['最近身份备份', date(backup.latestAt)], ['身份备份秒龄', count(backup.ageSeconds)], ['身份备份状态', backupStatus(backup.status)], ['身份数据库字节数', count(backup.databaseBytes)],
        ['恢复报告', restoreLabel(backup)], ['历史报告时间', date(backup.restoreVerifiedAt)], ['校验和', backup.checksumVerified === false ? '未验证（checksumVerified=false）' : '未知：未证明本次校验'],
      ]) : empty('身份备份未知：旧服务未提供 identityBackup，不推断已备份或已验证恢复。'), empty('本页仅显示脱敏配置和元数据，不展示路径、issuer/sub、手机原文、凭据或聊天内容，也不直接调用 Logto Management。“近期备份”不代表已校验可恢复；recorded 仅是历史报告，不代表本次恢复演练或 checksum 验证。业务 SQLite 与身份 PostgreSQL 是独立备份边界，不承诺全局身份生命周期、物理擦除或异机灾备。'), el('h3', {}, '运行提醒'), empty(value.warnings.length ? `服务报告 ${value.warnings.length} 项提醒；本页不回显可能包含敏感信息的原始诊断文本，请由运维安全核对。` : '服务未报告额外提醒；这不是健康探测通过证明。'));
    } catch (error) { if (context.valid() && !isAbort(error)) context.fail(error); }
    finally { if (context.valid()) { busy = false; refresh.disabled = false; } }
  });
  append(context.target, el('h2', {}, '健康诊断'), refresh, output);
  context.signal.addEventListener('abort', () => output.replaceChildren(), { once: true }); void refresh.click();
}
function restoreLabel(backup: NonNullable<AdminDiagnostics['identityBackup']>): string {
  return backup.restoreStatus === 'recorded' ? '仅有历史报告（recorded），本次未验证恢复' : backup.restoreStatus === 'not_reported' ? '未提供恢复报告（not_reported）' : backup.restoreStatus === 'invalid' ? '历史报告无效（invalid）' : '未知';
}
