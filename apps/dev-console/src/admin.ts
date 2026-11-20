import { AlliraAdminClient, AlliraApiError } from '../../../packages/client/src/index.ts';
import type { AdminConversation, AdminRun, AdminAuditEntry, AdminSession, Message, Page, Run } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, friendlyError, header, input, isAbort, notice, runLabel, setNotice, when } from './dom.ts';
import { mountAdminEconomy } from './admin-economy.ts';
import { mountAdminControl } from './admin-control.ts';
import './style.css';

// Old fragments are discarded, never parsed, retained or sent to an authentication endpoint.
if (location.hash) history.replaceState(null, '', location.pathname + location.search);
const client = new AlliraAdminClient({ baseUrl: location.origin });
const app = document.querySelector<HTMLElement>('#app')!;
app.replaceChildren();
const status = notice();
const authText = empty('正在检查 Cloudflare Access 管理登录…');
const accessLoginButton = button('通过 Cloudflare Access 登录', () => location.assign('/admin/'));
const restoreButton = button('检查已有管理登录', restore);
const authPanel = el('section', { 'aria-label': 'Cloudflare Access 管理登录' }, el('h2', {}, 'Cloudflare Access 管理登录'), empty('管理入口由 Cloudflare Access 验证，管理 Cookie 与 CSRF 独立于普通账号、Logto 和开发登录。此页面不接收一次性链接或开发用户 Bearer。'), authText, el('div', { class: 'actions' }, accessLoginButton, restoreButton));
const sessionText = el('p');
const logoutButton = button('注销管理', logout);
const content = el('section', { class: 'admin-content', 'aria-label': '管理内容' });
const nav = el('nav', { class: 'actions', 'aria-label': '管理导航' });
const workspace = el('section', {}, el('div', { class: 'identity' }, sessionText, logoutButton), empty('聊天正文属于敏感数据，只有显式点击“查看消息”才会请求。所有修改都需要勾选确认。'), nav, content);
workspace.hidden = true;
append(app, header('AlliraAi 后台管理', '测试聊天', '/test/'), authPanel, status, workspace);

type View = 'overview' | 'users' | 'conversations' | 'runs' | 'audit' | 'cdks' | 'usage' | 'diagnostics' | 'routes';
let authenticated = false;
let authBusy = false;
let epoch = 0;
let viewEpoch = 0;
let view: View = 'overview';
let abort = new AbortController();
const tabs: [View, string][] = [['overview', '概览'], ['users', '用户'], ['conversations', '会话'], ['runs', '生成任务'], ['cdks', '兑换码'], ['usage', '能量用量'], ['routes', '路由配置'], ['diagnostics', '健康诊断'], ['audit', '审计']];
for (const [key, label] of tabs) append(nav, button(label, () => show(key)));
void restore();

function updateAuth(): void {
  accessLoginButton.disabled = authBusy;
  restoreButton.disabled = authBusy;
  authText.textContent = authBusy ? '正在检查 Cloudflare Access 管理登录…' : '如未通过验证，请完整打开管理入口，让 Cloudflare Access 处理登录；也可显式检查已有管理会话。';
}
function clearAuth(): void {
  ++epoch; ++viewEpoch; abort.abort(); abort = new AbortController(); client.clear();
  authenticated = false; authBusy = false;
  content.replaceChildren(); workspace.hidden = true; authPanel.hidden = false;
  sessionText.textContent = ''; updateAuth();
}
function fail(error: unknown, captured: number): void {
  if (captured !== epoch || isAbort(error)) return;
  if (error instanceof AlliraApiError && error.status === 401) clearAuth();
  setNotice(status, friendlyError(error), true);
}
function showSession(session: AdminSession): void {
  const email = typeof session.identity?.email === 'string' && session.identity.email ? session.identity.email : '未知（Unknown；服务未提供身份）';
  sessionText.textContent = `Cloudflare Access 管理身份：${email}；管理会话有效期至 ${when(session.expiresAt)}`;
}
async function restore(): Promise<void> {
  if (authBusy) return;
  clearAuth();
  const captured = epoch;
  authBusy = true; updateAuth();
  try {
    const session = await client.getSession({ signal: abort.signal });
    if (captured !== epoch) return;
    authenticated = true; authPanel.hidden = true; workspace.hidden = false;
    showSession(session);
    setNotice(status, 'Cloudflare Access 管理会话已就绪。');
    await show(view);
  } catch (error) {
    if (captured !== epoch) return;
    if (error instanceof AlliraApiError && error.status === 401) { clearAuth(); setNotice(status, '尚未通过 Cloudflare Access 管理验证。请打开管理入口完成登录。'); }
    else fail(error, captured);
  } finally { if (captured === epoch) { authBusy = false; updateAuth(); } }
}
async function logout(): Promise<void> {
  if (authBusy || !authenticated) return;
  // The SDK captures the old CSRF synchronously, then invalidates it before starting the request.
  const ending = client.logout();
  clearAuth();
  const captured = epoch;
  setNotice(status, '本地管理内容已清空，正在注销服务会话…');
  try {
    await ending;
    // Never navigate to a server-supplied URL or let an old logout affect a newer session.
    if (captured === epoch) location.assign('/cdn-cgi/access/logout');
  } catch {
    if (captured === epoch) setNotice(status, '本地已退出，但服务端注销结果不确定。不会自动重试或跳转，请显式检查管理会话。', true);
  }
}
async function show(next: View): Promise<void> {
  if (!authenticated) return;
  view = next;
  const captured = epoch;
  const request = ++viewEpoch;
  abort.abort(); abort = new AbortController();
  const signal = abort.signal;
  content.replaceChildren();
  for (let i = 0; i < nav.children.length; i++) nav.children[i].setAttribute('aria-current', tabs[i][0] === next ? 'page' : 'false');
  const valid = () => authenticated && captured === epoch && request === viewEpoch;
  if (next === 'cdks' || next === 'usage') {
    mountAdminEconomy({ client, target: content, signal, valid, fail: error => { if (valid()) fail(error, captured); }, notify: text => { if (valid()) setNotice(status, text); } }, next);
    return;
  }
  if (next === 'users' || next === 'routes' || next === 'diagnostics') {
    mountAdminControl({ client, target: content, signal, valid, fail: error => { if (valid()) fail(error, captured); }, notify: text => { if (valid()) setNotice(status, text); } }, next);
    return;
  }
  if (next === 'overview') {
    append(content, el('h2', {}, '概览'), empty('正在读取…'));
    try {
      const overview = await client.overview({ signal });
      if (!valid()) return;
      const details = el('dl', { class: 'metrics' });
      const pairs: [string, string | number][] = [
        ['用户', overview.users], ['会话', overview.conversations], ['消息', overview.messages],
        ['生成中', overview.runs.running], ['已完成', overview.runs.completed], ['失败', overview.runs.failed], ['已停止', overview.runs.cancelled],
        ['有效用户登录', overview.activeSessions], ['环境', overview.environment], ['供应商', overview.provider], ['最大并发生成', overview.maxConcurrentRuns],
      ];
      for (const [label, value] of pairs) append(details, el('dt', {}, label), el('dd', {}, String(value)));
      content.replaceChildren(el('h2', {}, '概览'), details, button('刷新概览', () => show('overview')));
    } catch (error) { if (valid()) fail(error, captured); }
    return;
  }
  if (next === 'conversations') {
    const filter = input('conversationUserId', '留空查看全部');
    const controls = el('form', { class: 'inline-form', 'aria-label': '会话筛选' }, field('按用户 ID 筛选会话', filter));
    const result = el('div');
    const detail = el('section', { class: 'sensitive-detail', 'aria-label': '显式查看的会话消息' });
    append(content, el('h2', {}, '会话'), controls, result, detail);
    let filterEpoch = 0;
    let selectedDetail = 0;
    const load = async () => {
      ++selectedDetail; detail.replaceChildren();
      const query = filter.value.trim(); const filterRequest = ++filterEpoch;
      const localValid = () => valid() && filterRequest === filterEpoch;
      await paged<AdminConversation>('会话列表', ['标题', '所有者', '消息数', '更新时间', '操作'], cursor => client.listConversations({ cursor, limit: 30, ...(query ? { userId: query } : {}), signal }), item => {
        const actions = el('div', { class: 'row-actions' });
        append(actions, button('查看消息', async () => {
          const detailRequest = ++selectedDetail;
          const detailValid = () => localValid() && detailRequest === selectedDetail;
          detail.replaceChildren(el('h3', {}, `消息：${item.title || '未命名会话'}`), empty('正在读取敏感聊天内容…'));
          await paged<Message>('消息（正序）', ['角色', '状态', '正文', '时间'], cursor => client.listMessages(item.id, { cursor, limit: 50, signal }), m => [m.role === 'user' ? '用户' : '助手', runLabel(m.status), el('pre', { class: 'message-content' }, m.content), when(m.createdAt)], detailValid, captured, detail, false);
          if (detailValid()) append(detail, button('关闭敏感内容', () => { ++selectedDetail; detail.replaceChildren(); }));
        }), confirmedAction('确认永久删除此会话及消息', '删除会话', async () => {
          await client.deleteConversation(item.id, { signal });
          if (localValid()) { ++selectedDetail; detail.replaceChildren(); }
          return '会话已删除，请刷新列表；生成中的会话不可删除（409）。';
        }, localValid, captured));
        return [item.title || '未命名会话', item.ownerId, String(item.messageCount), when(item.updatedAt), actions];
      }, localValid, captured, result);
    };
    append(controls, button('筛选会话', load));
    controls.addEventListener('submit', event => { event.preventDefault(); void load(); });
    await load(); return;
  }
  if (next === 'runs') {
    const filterUser = input('runUserId', '留空查看全部');
    const filterStatus = el('select', { name: 'status', 'aria-label': '任务状态' });
    for (const [value, label] of [['', '全部'], ['running', '生成中'], ['completed', '已完成'], ['failed', '失败'], ['cancelled', '已停止']]) append(filterStatus, el('option', { value }, label));
    const controls = el('form', { class: 'inline-form', 'aria-label': '任务筛选' }, field('按用户 ID 筛选任务', filterUser), field('任务状态', filterStatus));
    const result = el('div');
    append(content, el('h2', {}, '生成任务'), controls, result);
    let filterEpoch = 0;
    const load = async () => {
      const request = ++filterEpoch;
      const localValid = () => valid() && request === filterEpoch;
      const query = filterUser.value.trim(); const selectedStatus = filterStatus.value as Run['status'] | '';
      await paged<AdminRun>('任务列表', ['任务 ID', '所有者', '状态', '更新时间', '操作'], cursor => client.listRuns({ cursor, limit: 30, ...(query ? { userId: query } : {}), ...(selectedStatus ? { status: selectedStatus } : {}), signal }), item => {
        const liveStatus = el('span', {}, runLabel(item.status));
        const actions = el('div', { class: 'row-actions' });
        append(actions, button('刷新任务状态', async () => {
          try {
            const updated = await client.getRun(item.id, { signal });
            if (localValid()) liveStatus.textContent = runLabel(updated.status);
          } catch (error) { if (localValid()) fail(error, captured); }
        }));
        if (item.status === 'running') append(actions, confirmedAction('确认停止此生成任务', '停止任务', async () => {
          const stopped = await client.cancelRun(item.id, { signal });
          if (localValid()) liveStatus.textContent = runLabel(stopped.status);
          return `停止请求已返回：${runLabel(stopped.status)}（服务端真实状态）。`;
        }, localValid, captured));
        return [item.id, item.ownerId, liveStatus, when(item.updatedAt), actions];
      }, localValid, captured, result);
    };
    append(controls, button('筛选任务', load));
    controls.addEventListener('submit', event => { event.preventDefault(); void load(); });
    await load(); return;
  }
  await paged<AdminAuditEntry>('审计记录', ['时间', '操作', '目标类型', '目标 ID'], cursor => client.listAudit({ cursor, limit: 50, signal }), item => [when(item.createdAt), item.action, item.targetType, item.targetId ?? '—'], valid, captured);
}

type Cell = string | Node;
async function paged<T>(title: string, columns: string[], loader: (cursor: string | undefined) => Promise<Page<T>>, row: (item: T) => Cell[], valid: () => boolean, captured: number, target: HTMLElement = content, showHeading = true): Promise<void> {
  const body = el('tbody');
  const table = el('table', {}, el('caption', {}, title), el('thead', {}, el('tr', {}, ...columns.map(name => el('th', { scope: 'col' }, name)))), body);
  const box = el('div', { class: 'table-wrap', tabindex: '0' }, table);
  const state = el('p', { role: 'status' });
  let cursor: string | null = null;
  let busy = false;
  let request = 0;
  let count = 0;
  const more = button('加载更多', () => load(true));
  const refresh = button('刷新列表', () => load(false));
  target.replaceChildren(...(showHeading ? [el('h2', {}, title)] : []), box, state, el('div', { class: 'actions' }, refresh, more));
  async function load(appendRows: boolean): Promise<void> {
    if (!valid() || busy || (appendRows && !cursor)) return;
    const mine = ++request;
    busy = true; more.disabled = true; refresh.disabled = true; state.textContent = '正在读取…';
    try {
      const page = await loader(appendRows ? cursor ?? undefined : undefined);
      if (!valid() || mine !== request) return;
      if (!appendRows) { body.replaceChildren(); count = 0; }
      for (const item of page.items) append(body, el('tr', {}, ...row(item).map(value => el('td', {}, value))));
      count += page.items.length; cursor = page.nextCursor;
      state.textContent = count ? `已显示 ${count} 条记录。` : '暂无记录。';
      more.hidden = !cursor;
    } catch (error) { if (valid()) { state.textContent = '读取失败。'; fail(error, captured); } }
    finally { if (valid() && mine === request) { busy = false; more.disabled = false; refresh.disabled = false; } }
  }
  await load(false);
}
function confirmedAction(label: string, actionLabel: string, action: () => Promise<string>, valid: () => boolean, captured: number): HTMLElement {
  const checked = el('input', { type: 'checkbox' });
  let busy = false;
  const go = button(actionLabel, async () => {
    if (!checked.checked || busy || !valid()) return;
    busy = true; go.disabled = true;
    try {
      const result = await action();
      if (valid()) { checked.checked = false; setNotice(status, result); }
    } catch (error) { if (valid()) fail(error, captured); }
    finally { if (valid()) { busy = false; go.disabled = !checked.checked; } }
  });
  go.disabled = true;
  checked.addEventListener('change', () => { go.disabled = busy || !checked.checked; });
  return el('div', { class: 'confirm-action' }, field(label, checked), go);
}
updateAuth();
