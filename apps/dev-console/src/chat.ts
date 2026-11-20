import { AlliraClient, AlliraApiError, StreamInterruptedError, StreamProtocolError } from '../../../packages/client/src/index.ts';
import type { AccountSession, AccountUser, AuthBackend, AuthOptions, CompanionPreset, CompanionPresetId, Conversation, Message, ModelRoutes, Run, RunMode, StartCompanionInput } from '../../../packages/client/src/index.ts';
import { mountAccountAuth } from './auth.ts';
import { mountPhoneAuth } from './phone-auth.ts';
import { mountOidcAuth } from './oidc-auth.ts';
import { mountAccountRecovery } from './recovery.ts';
import { mountAccount } from './account.ts';
import { mountRoles } from './roles.ts';
import { mountImports } from './imports.ts';
import type { ImportMemoryState } from './imports.ts';
import { mountEnergy } from './energy.ts';
import { mountConversationTools } from './conversation-tools.ts';
import type { ForkMemoryState } from './conversation-tools.ts';
import type { FeatureContext } from './feature.ts';
import { setupPwa } from './pwa.ts';
import { MessageList } from './messages.ts';
import { delay, maxReconnects, retryableStreamError, retryDelay, waitForOnline } from './reconnect.ts';
import { append, button, el, empty, field, friendlyError, input, isAbort, notice, runLabel, setNotice, when } from './dom.ts';
import { applyRunEvent, beginReplay, mergeMessages, newPending, retryPayload, sameScope } from './state.ts';
import type { PendingSend, Scope, StreamView } from './state.ts';
import './style.css';

const app = document.querySelector<HTMLElement>('#app')!;
app.replaceChildren();
const infoText = empty('正在读取服务配置…');
const message = notice();
const userId = input('userId', '1–100 位英文字母、数字、下划线或连字符');
userId.maxLength = 100;
userId.pattern = '[a-zA-Z0-9_-]+';
userId.required = true;
const loginButton = button('开发登录', login);
const loginForm = el('form', { class: 'inline-form', 'aria-label': '开发用户登录' }, field('开发用户 ID', userId), loginButton);
loginForm.addEventListener('submit', event => { event.preventDefault(); void login(); });
const loginPanel = el('section', {}, el('h2', {}, '开发登录'), empty('仅限受 SSH 保护的开发环境。任意开发用户 ID 不代表正式账号，访问令牌只保存在本页内存。'), loginForm);
const identity = el('strong');
const logoutButton = button('退出登录', logout);
const titleInput = input('newTitle', '可留空');
titleInput.maxLength = 200;
const instructionInput = el('textarea', { name: 'newInstructions', rows: '3', placeholder: '可留空', maxlength: '20000' });
const createButton = button('新建会话', createConversation);
const listBox = el('div', { class: 'conversation-list', 'aria-label': '会话列表' });
const moreConversations = button('加载更多会话', () => loadConversations(true));
const refreshConversations = button('刷新会话', () => loadConversations(false));
const startButton = button('新聊天', startCompanion);
const setupRetryButton = button('重试新聊天', startCompanion);
const setupNotice = notice();
const presetSelect = el('select', { name: 'companionPreset', 'aria-label': '聊天风格' }, el('option', { value: 'daily' }, '日常陪伴（默认）'));
const presetDescription = empty('直接点击“新聊天”即可开始，不需要填写设定。');
const presetRefresh = button('重新读取聊天风格', loadPresets);
const presetOptions = el('details', { 'aria-label': '聊天风格选项' }, el('summary', {}, '可选：换一种聊天风格'), field('聊天风格', presetSelect), presetDescription, presetRefresh);
const customCreation = el('details', { 'aria-label': '自定义新会话' }, el('summary', {}, '自定义会话（高级）'), field('新会话标题', titleInput), field('新会话设定', instructionInput), createButton);
const sidebar = el('aside', { class: 'sidebar', 'aria-label': '会话导航' }, el('h2', {}, '聊天记录'), startButton, setupRetryButton, setupNotice, presetOptions, customCreation, refreshConversations, listBox, moreConversations);
const selectedHeading = el('h2', {}, '请选择或新建会话');
const editTitle = input('title');
editTitle.maxLength = 200;
const editInstructions = el('textarea', { name: 'instructions', rows: '3', maxlength: '20000' });
const saveButton = button('保存标题和设定', saveConversation);
const deleteConfirm = el('input', { type: 'checkbox', name: 'confirmDelete' });
const deleteButton = button('删除此会话', deleteConversation);
const editor = el('details', { 'aria-label': '修改会话' }, el('summary', {}, '修改会话'), field('会话标题', editTitle), field('会话设定', editInstructions), saveButton, field('确认永久删除此会话及其消息', deleteConfirm), deleteButton);
const status = el('p', { class: 'run-status', role: 'status' });
const disconnectButton = button('断开订阅', disconnect);
const resumeButton = button('恢复订阅', resume);
const stopButton = button('停止回复', stop);
const simpleStatus = el('p', { class: 'reply-status', role: 'status', 'aria-label': '回复状态' });
const simpleResume = button('再试一次', resume);
const refreshStateButton = button('刷新会话状态', refreshCurrent);
const olderButton = button('加载更早消息', loadOlder);
const messagesBox = el('div', { class: 'messages', 'aria-label': '聊天消息', tabindex: '0' });
const messageList = new MessageList(messagesBox);
const draft = el('textarea', { name: 'message', rows: '4', placeholder: '输入消息，可使用 Markdown。Ctrl / ⌘ + Enter 发送。', 'aria-label': '消息内容' });
const sendButton = button('发送', send);
const retryButton = button('使用同一编号和原文重试', retry);
const discardButton = button('放弃本地待确认消息', discardPending);
const discardConfirm = el('input', { type: 'checkbox', name: 'confirmDiscard' });
const pendingBox = el('div', { class: 'pending' });
const mode = el('select', { name: 'runMode', 'aria-label': '生成模式' }, el('option', { value: 'economy' }, '经济模式'), el('option', { value: 'fast' }, '极速模式'));
const routeText = empty('正在读取模式配置…');
const taskId = empty('');
const technicalControls = el('details', { 'aria-label': '聊天高级选项' }, el('summary', {}, '聊天高级选项'), editor, field('生成模式', mode), routeText, status, taskId, el('div', { class: 'actions' }, disconnectButton, resumeButton, refreshStateButton), field('已刷新确认，仍要放弃本地待确认消息（不会取消后台生成）', discardConfirm), discardButton);
const openingHint = empty('选择已有聊天，或点击“新聊天”开始。');
openingHint.setAttribute('aria-label', '聊天开始建议');
const chatPanel = el('section', { class: 'chat-panel', 'aria-label': '当前会话' }, selectedHeading, simpleStatus, simpleResume, openingHint, olderButton, messagesBox, pendingBox, field('消息内容', draft), el('div', { class: 'actions' }, sendButton, stopButton, retryButton), technicalControls);
const chatLayout = el('div', { class: 'chat-layout' }, sidebar, chatPanel);
const featureTarget = el('section', { class: 'feature-content', 'aria-label': '功能内容' }); featureTarget.hidden = true;
const featureNav = el('nav', { class: 'actions', 'aria-label': '高级功能导航' });
const settings = el('details', { 'aria-label': '设置与高级工具' }, el('summary', {}, '设置与高级工具'), featureNav, infoText, el('a', { href: '/admin/' }, '后台管理'));
const chatHome = button('聊天', () => showFeature('chat'));
const workspace = el('section', {}, el('div', { class: 'identity' }, identity, el('div', { class: 'actions' }, chatHome, logoutButton)), settings, chatLayout, featureTarget);
const accountLoginPanel = el('section', { 'aria-label': '账号认证' });
const oidcLoginTarget = el('section', { 'aria-label': 'Logto 托管认证' });
const phoneLoginTarget = el('section', { 'aria-label': '手机与社交认证' });
const legacyTarget = el('section');
const legacyLogin = el('details', { 'aria-label': '旧用户名登录' }, el('summary', {}, '高级：旧用户名账号与恢复码'), legacyTarget);
append(accountLoginPanel, oidcLoginTarget, phoneLoginTarget, legacyLogin);
const developerLogin = el('details', { 'aria-label': '开发测试登录' }, el('summary', {}, '开发测试入口'), loginPanel);
workspace.hidden = true;
const offlineText = notice();
append(app, el('header', { class: 'page-header' }, el('h1', {}, 'AlliraAi 聊天')), offlineText, accountLoginPanel, developerLogin, message, workspace);

let token: string | undefined;
let browserSessionActive = false;
let authBackend: AuthBackend | null = null;
let authEpoch = 0;
let viewEpoch = 0;
let sessionAbort = new AbortController();
let viewAbort = new AbortController();
let streamAbort: AbortController | undefined;
let subscriptionPaused = false;
let loginPending = false;
let listPending = false;
let listEpoch = 0;
let conversationCursor: string | null = null;
let conversations: Conversation[] = [];
let conversation: Conversation | null = null;
let messages: Message[] = [];
let earlierCursor: string | null = null;
let activeRun: Run | null = null;
let streamView: StreamView | null = null;
let runState = '暂无生成任务';
let connection = '未连接';
let posting = false;
let loadingView = false;
let olderLoading = false;
let mutating = false;
let creating = false;
let setupBusy = false;
let setupPending: { input: StartCompanionInput; uncertain: boolean } | null = null;
let presetLoading = false;
const presets = new Map<CompanionPresetId, CompanionPreset>();
const conversationHints = new Map<string, string>();
let devAllowed = false;
let accountUser: AccountUser | null = null;
let selectedFeature = 'chat';
let featureEpoch = 0;
let featureAbort = new AbortController();
let routes: ModelRoutes | null = null;
const drafts = new Map<string, string>();
const pending = new Map<string, PendingSend>();
let importState: ImportMemoryState = { pending: null };
let forkState: ForkMemoryState = { pending: null };
const publicClient = new AlliraClient({ baseUrl: location.origin });
let client = new AlliraClient({ baseUrl: location.origin, accessToken: () => token });
const authenticated = (): boolean => !!token || browserSessionActive;
const scope = (): Scope => ({ auth: authEpoch, view: viewEpoch, conversationId: conversation?.id ?? '' });
const current = (captured: Scope): boolean => sameScope(captured, scope());
async function completeAccountLogin(session: AccountSession, request: number, remember: boolean): Promise<void> {
  if (request !== authEpoch) return;
  sessionAbort.abort(); sessionAbort = new AbortController();
  if (remember) {
    // The short-lived closure is the only place the incoming Bearer is held during exchange.
    let exchangeToken: string | undefined = session.accessToken;
    const candidate = new AlliraClient({ baseUrl: location.origin, browserSession: true, accessToken: () => exchangeToken });
    try {
      let restored;
      try { restored = await candidate.exchangeBrowserSession({ signal: sessionAbort.signal }); }
      catch (error) {
        // A lost POST response may already have consumed the Bearer. Never repeat this POST blindly.
        if (error instanceof AlliraApiError && error.status < 500) throw error;
        if (request !== authEpoch) return;
        restored = await candidate.getBrowserSession({ signal: sessionAbort.signal });
      }
      if (request !== authEpoch) { candidate.clearBrowserSession(); return; }
      if (restored.user.id !== session.user.id) { candidate.clearBrowserSession(); throw new Error('Browser session identity mismatch'); }
      token = undefined; browserSessionActive = true; client = candidate; accountUser = restored.user;
    } finally { exchangeToken = undefined; session.accessToken = ''; }
  } else {
    token = session.accessToken; browserSessionActive = false; accountUser = session.user;
    client = new AlliraClient({ baseUrl: location.origin, accessToken: () => token });
  }
  if (request !== authEpoch || !accountUser) return;
  accountAuth.invalidate(); recoveryAuth.invalidate(); phoneAuth.invalidate(); oidcAuth.invalidate(); legacyLogin.open = false;
  loginPending = false; loginButton.disabled = !devAllowed;
  showIdentity();
  accountLoginPanel.hidden = true; loginPanel.hidden = true; developerLogin.hidden = true; workspace.hidden = false;
  setNotice(message, browserSessionActive ? '已登录并记住此设备。共享设备使用后请退出登录。' : '已登录，可以开始聊天。未勾选“记住此设备”时刷新需要重新登录。');
  await showFeature('chat'); await loadConversations(false);
}
function beginAuth(source: 'phone' | 'legacy' | 'recovery' | 'oidc'): number {
  if (source !== 'legacy') accountAuth.invalidate();
  if (source !== 'recovery') recoveryAuth.invalidate();
  if (source !== 'phone') phoneAuth.invalidate();
  if (source !== 'oidc') oidcAuth.invalidate();
  sessionAbort.abort(); loginPending = false; loginButton.disabled = !devAllowed;
  return ++authEpoch;
}
const accountAuth = mountAccountAuth(legacyTarget, publicClient, message, completeAccountLogin, () => beginAuth('legacy'), request => request === authEpoch);
const recoveryAuth = mountAccountRecovery(legacyTarget, publicClient, message, () => beginAuth('recovery'), request => request === authEpoch);
const oidcAuth = mountOidcAuth(oidcLoginTarget, publicClient, message, completeAccountLogin, () => beginAuth('oidc'), request => request === authEpoch, () => authEpoch, () => {
  if (!browserSessionActive || !authenticated()) { setNotice(message, '删除重新认证不能独立登录。请先使用已记住此设备的原业务登录，或取消后重新登录。', true); return; }
  settings.open = true; void showFeature('account');
});
const phoneAuth = mountPhoneAuth(phoneLoginTarget, publicClient, message, completeAccountLogin, () => { legacyLogin.open = false; return beginAuth('phone'); }, request => request === authEpoch, () => authEpoch, configureAuthentication);
function configureAuthentication(options: AuthOptions | null, fallback: boolean): void {
  const previous = authBackend;
  authBackend = options?.backend ?? (fallback ? 'legacy' : null);
  const hosted = authBackend === 'oidc';
  phoneLoginTarget.hidden = hosted; legacyLogin.hidden = hosted;
  if (hosted) { accountAuth.invalidate(); recoveryAuth.invalidate(); legacyLogin.open = false; }
  accountAuth.configure(options, fallback);
  oidcAuth.configure(hosted && options ? options.oidc : null);
  if (!hosted) return;
  const request = authEpoch;
  void browserRestoration.then(async () => {
    if (request !== authEpoch) return;
    await oidcAuth.restore();
    if (previous !== authBackend && authenticated() && selectedFeature === 'account' && oidcAuth.pending()?.purpose !== 'erase') await showFeature('account');
  });
}
legacyLogin.addEventListener('toggle', () => { if (legacyLogin.open && !authenticated()) beginAuth('legacy'); });
function showIdentity(): void {
  if (accountUser) identity.textContent = `账号：${accountUser.displayName}（${accountUser.username}）${browserSessionActive ? ' · 已记住此设备' : ''}`;
}
async function restoreBrowserSession(): Promise<void> {
  const request = authEpoch;
  const candidate = new AlliraClient({ baseUrl: location.origin, browserSession: true });
  try {
    const restored = await candidate.getBrowserSession({ signal: sessionAbort.signal });
    if (request !== authEpoch) { candidate.clearBrowserSession(); return; }
    token = undefined; browserSessionActive = true; accountUser = restored.user; client = candidate;
    accountAuth.invalidate(); recoveryAuth.invalidate(); phoneAuth.invalidate(); oidcAuth.invalidate(); legacyLogin.open = false; showIdentity();
    accountLoginPanel.hidden = true; loginPanel.hidden = true; developerLogin.hidden = true; workspace.hidden = false;
    setNotice(message, '已恢复此设备的登录。草稿不会保存在设备上。');
    await showFeature('chat'); await loadConversations(false);
  } catch (error) {
    candidate.clearBrowserSession();
    if (request === authEpoch && !isAbort(error) && !(error instanceof AlliraApiError && error.status === 401)) setNotice(message, '无法检查此设备的浏览器登录；仍可使用已开通的登录方式。', true);
  }
}
const browserRestoration = restoreBrowserSession();
const featureTabs: [string, string][] = [['tools', '会话工具'], ['roles', '角色与记忆'], ['imports', '历史导入'], ['energy', '能量用量'], ['account', '账号']];
for (const [key, label] of featureTabs) append(featureNav, button(label, () => showFeature(key)));
setupPwa(offlineText, () => updateControls());
void loadPresets();
presetSelect.addEventListener('change', () => { presetDescription.textContent = presets.get(presetSelect.value as CompanionPresetId)?.description ?? '默认使用日常陪伴，不需要填写设定。'; });
settings.addEventListener('toggle', () => { if (!settings.open && selectedFeature !== 'chat') void showFeature('chat'); });
void client.getModelRoutes().then(value => { routes = value; routeText.textContent = value.items.map(item => `${item.mode === 'fast' ? '极速' : '经济'}：${item.energyCost} 点/轮，${item.configured ? item.provider : '尚未配置'}${item.fallbackTo ? '，异常时可回退经济' : ''}`).join('；'); }).catch(() => { routeText.textContent = '模式配置暂时无法读取，实际费用与路由以服务端为准。'; });

async function showFeature(key: string): Promise<void> {
  featureAbort.abort(); featureAbort = new AbortController(); ++featureEpoch;
  selectedFeature = key;
  for (let i = 0; i < featureNav.children.length; i++) featureNav.children[i].setAttribute('aria-current', featureTabs[i][0] === key ? 'page' : 'false');
  chatLayout.hidden = key !== 'chat'; featureTarget.hidden = key === 'chat'; featureTarget.replaceChildren();
  if (key === 'chat') { if (conversation && authenticated()) { subscriptionPaused = false; await refreshCurrent(); } return; }
  // Suspend every selected-view request as well as SSE; preserve server run and drafts.
  ++viewEpoch; viewAbort.abort(); viewAbort = new AbortController();
  posting = false; loadingView = false; olderLoading = false; mutating = false;
  disconnect();
  const auth = authEpoch; const mounted = featureEpoch;
  const featureClient = browserSessionActive ? client : new AlliraClient({ baseUrl: location.origin, accessToken: token });
  const context: FeatureContext = {
    client: featureClient, target: featureTarget, signal: featureAbort.signal,
    valid: () => auth === authEpoch && mounted === featureEpoch && authenticated(),
    fail(error) { if (this.valid()) fail(error); },
    notify(text) { if (this.valid()) setNotice(message, text); },
    conversation, isAccount: !!accountUser,
    async selectConversation(item) { if (!this.valid()) return; const nextMount = featureEpoch + 1; await showFeature('chat'); if (auth !== authEpoch || nextMount !== featureEpoch) return; await loadConversations(false); if (auth === authEpoch && nextMount === featureEpoch) await selectConversation(item); },
    signedOut(text) { if (this.valid()) { clearAuth(); setNotice(message, text); } },
    profileChanged(displayName) { if (this.valid() && accountUser) { accountUser = { ...accountUser, displayName }; showIdentity(); } },
  };
  if (key === 'tools') mountConversationTools(context, forkState);
  else if (key === 'roles') mountRoles(context);
  else if (key === 'imports') mountImports(context, importState);
  else if (key === 'energy') mountEnergy(context);
  else if (key === 'account') mountAccount(context, authBackend, oidcAuth, browserSessionActive);
}

void client.getInfo().then(info => {
  devAllowed = info.allowDevAuth && info.consoleEnabled;
  developerLogin.hidden = !devAllowed || authenticated();
  infoText.textContent = `环境：${info.environment}；供应商：${info.provider}。原生 MVP 测试客户端，正式视觉与真实模型质量尚未验证。`;
  loginButton.disabled = !devAllowed;
  if (!info.consoleEnabled) setNotice(message, '此服务未开放聊天页面。', true);
}).catch(error => { setNotice(message, friendlyError(error), true); });

function updateControls(): void {
  const selected = !!conversation;
  const running = activeRun?.status === 'running';
  const p = conversation ? pending.get(conversation.id) : undefined;
  editor.hidden = !selected;
  technicalControls.hidden = !selected;
  openingHint.hidden = messages.length > 0 || !!activeRun;
  stopButton.hidden = !running;
  simpleResume.hidden = !selected || !!streamAbort || !subscriptionPaused;
  simpleResume.disabled = posting || loadingView;
  simpleStatus.textContent = !selected ? '点击“新聊天”就能开始。' : !navigator.onLine ? '网络已断开，恢复联网后可继续。' : subscriptionPaused ? '连接已断开，可以再试一次；不会重复发送消息。' : running ? (connection === '订阅中' ? '正在回复…' : '连接中…') : loadingView ? '正在读取聊天…' : '可以开始聊天。';
  draft.disabled = !selected || loadingView;
  sendButton.disabled = !selected || !!running || !!p || posting || loadingView;
  saveButton.disabled = !selected || !!running || mutating || loadingView;
  deleteButton.disabled = !selected || !!running || mutating || !deleteConfirm.checked || loadingView;
  disconnectButton.disabled = !streamAbort;
  resumeButton.disabled = !selected || !!streamAbort || posting || loadingView;
  stopButton.disabled = !running || mutating;
  refreshStateButton.disabled = !selected || posting || loadingView;
  olderButton.hidden = !earlierCursor;
  olderButton.disabled = olderLoading || loadingView;
  retryButton.hidden = !p;
  retryButton.disabled = !p?.uncertain || posting || loadingView || !!running;
  discardButton.hidden = !p;
  discardConfirm.parentElement!.hidden = !p;
  discardButton.disabled = !p?.uncertain || posting || loadingView || !discardConfirm.checked;
  moreConversations.hidden = !conversationCursor;
  moreConversations.disabled = listPending;
  refreshConversations.disabled = listPending;
  createButton.disabled = !authenticated() || creating || setupBusy || !!setupPending;
  startButton.disabled = !authenticated() || creating || setupBusy || !!setupPending;
  setupRetryButton.hidden = !setupPending?.uncertain;
  setupRetryButton.disabled = !authenticated() || setupBusy || creating;
  presetSelect.disabled = setupBusy || !!setupPending;
  presetRefresh.disabled = presetLoading || setupBusy || !!setupPending;
  taskId.textContent = activeRun || streamView ? `生成任务编号：${(activeRun ?? streamView!.run).id}` : '';
  mode.disabled = !!p || posting;
  if (!navigator.onLine) {
    for (const control of [sendButton, retryButton, saveButton, deleteButton, createButton, startButton, setupRetryButton, presetRefresh, stopButton, refreshStateButton, resumeButton, simpleResume, olderButton, moreConversations, refreshConversations]) control.disabled = true;
  }
  status.textContent = `服务端：${runState}；本地订阅：${connection}。切换会话或断开订阅不会停止后台生成。`;
  pendingBox.replaceChildren();
  if (p) append(pendingBox, el('p', {}, p.uncertain ? '发送结果不确定。下面原文与消息编号绑定；重试不会更改内容。先刷新会话状态可发现已创建的任务。' : '正在提交消息…'), el('pre', { class: 'message-content', 'aria-label': '待确认消息原文' }, p.content));
}
deleteConfirm.addEventListener('change', updateControls);
discardConfirm.addEventListener('change', updateControls);
draft.addEventListener('input', () => { if (conversation) drafts.set(conversation.id, draft.value); });
draft.addEventListener('keydown', event => {
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); if (!sendButton.disabled) void send(); }
});

async function login(): Promise<void> {
  if (loginPending || !devAllowed) return;
  if (!navigator.onLine) { setNotice(message, '当前离线，不能开发登录。', true); return; }
  const id = userId.value.trim();
  if (!/^[a-zA-Z0-9_-]{1,100}$/.test(id)) { setNotice(message, '开发用户 ID 需为 1–100 位英文字母、数字、下划线或连字符。', true); return; }
  accountAuth.invalidate(); recoveryAuth.invalidate(); phoneAuth.invalidate(); oidcAuth.invalidate(); legacyLogin.open = false; sessionAbort.abort();
  const epoch = ++authEpoch;
  loginPending = true;
  loginButton.disabled = true;
  try {
    const session = await publicClient.createDevSession({ userId: id });
    if (epoch !== authEpoch) return;
    token = session.accessToken; accountUser = null; browserSessionActive = false;
    client = new AlliraClient({ baseUrl: location.origin, accessToken: () => token });
    identity.textContent = `开发用户：${id}`;
    userId.value = '';
    loginPanel.hidden = true; developerLogin.hidden = true; accountLoginPanel.hidden = true;
    workspace.hidden = false;
    sessionAbort = new AbortController();
    setNotice(message, '已登录。令牌只在本页内存；刷新页面需重新开发登录。');
    await showFeature('chat'); await loadConversations(false);
  } catch (error) { if (epoch === authEpoch) setNotice(message, friendlyError(error), true); }
  finally { if (epoch === authEpoch) { loginPending = false; loginButton.disabled = !devAllowed; updateControls(); } }
}
function clearView(): void {
  ++viewEpoch;
  viewAbort.abort();
  viewAbort = new AbortController();
  streamAbort?.abort();
  streamAbort = undefined; subscriptionPaused = false;
  messageList.clear();
  conversation = null; activeRun = null; streamView = null; messages = []; earlierCursor = null;
  posting = false; loadingView = false; olderLoading = false; mutating = false;
  connection = '未连接'; runState = '暂无生成任务';
  draft.value = ''; editTitle.value = ''; editInstructions.value = ''; deleteConfirm.checked = false;
  selectedHeading.textContent = '请选择或新建会话';
  openingHint.textContent = '选择已有聊天，或点击“新聊天”开始。';
  updateControls();
}
function clearAuth(): void {
  ++authEpoch; ++listEpoch;
  client.clearBrowserSession();
  token = undefined; accountUser = null; browserSessionActive = false;
  client = new AlliraClient({ baseUrl: location.origin, accessToken: () => token });
  accountAuth.invalidate(); recoveryAuth.invalidate(); phoneAuth.invalidate(); oidcAuth.invalidate(); legacyLogin.open = false; featureAbort.abort(); ++featureEpoch;
  sessionAbort.abort();
  clearView();
  drafts.clear(); pending.clear(); conversationHints.clear(); setupPending = null; setupBusy = false;
  setupNotice.textContent = ''; presetSelect.value = 'daily'; mode.value = 'economy';
  settings.open = false; technicalControls.open = false; customCreation.open = false; presetOptions.open = false; developerLogin.open = false;
  importState = { pending: null }; forkState = { pending: null }; conversations = []; conversationCursor = null;
  listBox.replaceChildren(); identity.textContent = ''; workspace.hidden = true; loginPanel.hidden = false; developerLogin.hidden = !devAllowed; accountLoginPanel.hidden = false;
  featureTarget.replaceChildren();
  loginPending = false; listPending = false; creating = false; loginButton.disabled = !devAllowed;
}
async function logout(): Promise<void> {
  if (!authenticated()) return;
  // Start browser logout before clearAuth discards its CSRF; Bearer logout captures the exact old token.
  const request = browserSessionActive ? client.logoutBrowserSession() : new AlliraClient({ baseUrl: location.origin, accessToken: token }).logout();
  clearAuth();
  setNotice(message, '正在使服务端登录失效…');
  const epoch = authEpoch;
  try { await request; if (epoch === authEpoch) setNotice(message, '已退出，服务端登录已失效。'); }
  catch { if (epoch === authEpoch) setNotice(message, '本地已退出，但服务端注销未确认。此设备 Cookie 可能仍有效；请联网后重新检查登录并注销。', true); }
}
function fail(error: unknown, captured?: Scope): void {
  if (captured && !current(captured)) return;
  if (isAbort(error)) return;
  if (error instanceof AlliraApiError && error.status === 401) clearAuth();
  setNotice(message, friendlyError(error), true);
}
async function loadConversations(more: boolean): Promise<void> {
  if (!authenticated() || (more && (!conversationCursor || listPending))) return;
  const epoch = authEpoch;
  const request = ++listEpoch;
  listPending = true; updateControls();
  try {
    const page = await client.listConversations({ limit: 30, ...(more && conversationCursor ? { cursor: conversationCursor } : {}), signal: sessionAbort.signal });
    if (epoch !== authEpoch || request !== listEpoch) return;
    const items = more ? [...conversations, ...page.items] : page.items;
    conversations = [...new Map(items.map(item => [item.id, item])).values()];
    conversationCursor = page.nextCursor;
    renderList();
  } catch (error) { if (epoch === authEpoch && request === listEpoch) fail(error); }
  finally { if (epoch === authEpoch && request === listEpoch) { listPending = false; updateControls(); } }
}
function renderList(): void {
  listBox.replaceChildren();
  for (const item of conversations) {
    const choose = button(item.title || '未命名会话', () => selectConversation(item));
    choose.setAttribute('aria-pressed', String(conversation?.id === item.id));
    choose.dataset.conversationId = item.id;
    append(listBox, el('div', { class: 'conversation-item' }, choose, el('small', {}, when(item.updatedAt))));
  }
  if (!conversations.length) append(listBox, empty('暂无会话。'));
}
async function loadPresets(): Promise<void> {
  if (presetLoading || setupBusy || setupPending) return;
  presetLoading = true; updateControls();
  try {
    const result = await publicClient.getCompanionPresets();
    if (setupBusy || setupPending) return;
    const selected = presetSelect.value as CompanionPresetId;
    presets.clear();
    for (const item of result.items) presets.set(item.id, item);
    presetSelect.replaceChildren(...result.items.map(item => el('option', { value: item.id }, item.title)));
    if (!presets.has('daily')) append(presetSelect, el('option', { value: 'daily' }, '日常陪伴（默认）'));
    presetSelect.value = presets.has(selected) ? selected : 'daily';
    presetDescription.textContent = presets.get(presetSelect.value as CompanionPresetId)?.description ?? '默认使用日常陪伴，不需要填写设定。';
  } catch {
    presetDescription.textContent = '暂时无法读取风格介绍；“新聊天”仍使用默认日常陪伴。可以手动重新读取。';
  } finally { presetLoading = false; updateControls(); }
}
async function startCompanion(): Promise<void> {
  if (!authenticated() || setupBusy || creating || (setupPending && !setupPending.uncertain)) return;
  if (!navigator.onLine) { setNotice(setupNotice, '当前离线，联网后再创建聊天。', true); return; }
  if (!setupPending) {
    try { setupPending = { input: { clientSetupId: crypto.randomUUID(), presetId: presetSelect.value as CompanionPresetId }, uncertain: false }; }
    catch { setNotice(setupNotice, '无法安全创建聊天，请通过 localhost、127.0.0.1 或 HTTPS 访问。', true); return; }
  }
  const pendingSetup = setupPending;
  const epoch = authEpoch; const selectedView = viewEpoch; const mounted = featureEpoch;
  setupBusy = true; pendingSetup.uncertain = false; updateControls();
  setNotice(setupNotice, '正在准备聊天…');
  try {
    const result = await client.startCompanionConversation({ ...pendingSetup.input }, { signal: sessionAbort.signal });
    if (epoch !== authEpoch) return;
    setupPending = null;
    const hint = presets.get(result.presetId)?.openingHint;
    if (hint) conversationHints.set(result.conversation.id, `开始建议（不是回复）：${hint}`);
    setNotice(setupNotice, result.replayed ? '已找回刚才准备的聊天。' : '聊天准备好了，可以直接发送消息。');
    await loadConversations(false);
    if (epoch === authEpoch && selectedView === viewEpoch && mounted === featureEpoch && selectedFeature === 'chat') {
      mode.value = 'economy';
      await selectConversation(result.conversation);
    }
  } catch (error) {
    if (epoch !== authEpoch) return;
    if (error instanceof AlliraApiError && error.status >= 400 && error.status < 500) {
      setupPending = null; fail(error);
      setNotice(setupNotice, error.status === 404 ? '刚才的聊天已不存在，请刷新列表；不会替你重新创建。' : '聊天未准备成功，请检查登录或稍后重试。', true);
    } else {
      pendingSetup.uncertain = true;
      setNotice(setupNotice, '连接中断，聊天可能已经准备好。可以先刷新聊天记录，或点击“重试新聊天”找回同一次结果；不会重复创建。', true);
    }
  } finally { if (epoch === authEpoch) { setupBusy = false; updateControls(); } }
}
async function createConversation(): Promise<void> {
  if (!authenticated() || creating || setupBusy || setupPending) return;
  const epoch = authEpoch;
  const selectedView = viewEpoch;
  creating = true; updateControls();
  try {
    const created = await client.createConversation({ ...(titleInput.value.trim() ? { title: titleInput.value } : {}), instructions: instructionInput.value || null }, { signal: sessionAbort.signal });
    if (epoch !== authEpoch) return;
    titleInput.value = ''; instructionInput.value = '';
    await loadConversations(false);
    if (epoch === authEpoch && selectedView === viewEpoch) await selectConversation(created);
  } catch (error) { if (epoch === authEpoch) fail(error); }
  finally { if (epoch === authEpoch) { creating = false; updateControls(); } }
}
async function selectConversation(item: Conversation): Promise<void> {
  clearView();
  conversation = item;
  selectedHeading.textContent = item.title || '未命名会话';
  openingHint.textContent = conversationHints.get(item.id) ?? '可以从今天想聊的一件小事开始。';
  editTitle.value = item.title; editInstructions.value = item.instructions ?? '';
  draft.value = drafts.get(item.id) ?? '';
  discardConfirm.checked = false;
  renderList();
  await loadCurrent(true);
}
async function refreshCurrent(): Promise<void> { await loadCurrent(false); }
async function loadCurrent(initial: boolean): Promise<void> {
  if (!conversation || loadingView) return;
  const captured = scope();
  // A fresh state/snapshot must not race an older live stream's terminal event.
  streamAbort?.abort(); streamAbort = undefined;
  messageList.cancelPaint();
  streamView = null; connection = subscriptionPaused ? '已断开（需手动恢复）' : '未连接';
  loadingView = true; updateControls();
  try {
    // Read state before messages: a completed run must not leave an older generating
    // snapshot stranded without an active run to subscribe to. A run begun between
    // these reads is recovered below from the generating assistant's run id.
    const state = await client.getConversationState(captured.conversationId, { signal: viewAbort.signal });
    if (!current(captured)) return;
    const recent = await client.listRecentMessages(captured.conversationId, { limit: 50, signal: viewAbort.signal });
    if (!current(captured)) return;
    let recoveredRun = state.activeRun;
    const generating = [...recent.items].reverse().find(item => item.role === 'assistant' && item.status === 'generating');
    if (generating && recoveredRun?.id !== generating.runId) {
      recoveredRun = await client.getRun(generating.runId, { signal: viewAbort.signal });
      if (!current(captured)) return;
    }
    conversation = state.conversation;
    messages = recent.items;
    earlierCursor = recent.nextCursor;
    editTitle.value = conversation.title; editInstructions.value = conversation.instructions ?? '';
    selectedHeading.textContent = conversation.title || '未命名会话';
    activeRun = recoveredRun?.status === 'running' ? recoveredRun : null;
    const p = pending.get(conversation.id);
    if (p && recoveredRun?.clientMessageId === p.clientMessageId) pending.delete(conversation.id);
    runState = recoveredRun ? runLabel(recoveredRun.status) : '当前无后台生成';
    if (!streamAbort) streamView = null;
    renderMessages(initial ? 'bottom' : 'preserve');
    renderList();
    if (recoveredRun && !streamAbort && !subscriptionPaused) void subscribe(recoveredRun);
  } catch (error) { fail(error, captured); }
  finally { if (current(captured)) { loadingView = false; updateControls(); } }
}
async function loadOlder(): Promise<void> {
  if (!conversation || !earlierCursor || olderLoading) return;
  const captured = scope();
  olderLoading = true; updateControls();
  try {
    const page = await client.listRecentMessages(conversation.id, { limit: 50, before: earlierCursor, signal: viewAbort.signal });
    if (!current(captured)) return;
    messages = mergeMessages(page.items, messages);
    earlierCursor = page.nextCursor;
    renderMessages('prepend');
  } catch (error) { fail(error, captured); }
  finally { if (current(captured)) { olderLoading = false; updateControls(); } }
}
function renderMessages(mode: 'bottom' | 'preserve' | 'prepend'): void {
  messageList.reconcile(messages, streamView, mode);
}
async function saveConversation(): Promise<void> {
  if (!conversation || activeRun?.status === 'running' || mutating) return;
  if (!editTitle.value.trim()) { setNotice(message, '会话标题不能为空。', true); return; }
  const captured = scope();
  mutating = true; updateControls();
  try {
    const updated = await client.updateConversation(conversation.id, { title: editTitle.value, instructions: editInstructions.value || null }, { signal: viewAbort.signal });
    if (!current(captured)) return;
    conversation = updated; selectedHeading.textContent = updated.title || '未命名会话';
    setNotice(message, '会话已更新。');
    await loadConversations(false);
  } catch (error) { fail(error, captured); }
  finally { if (current(captured)) { mutating = false; updateControls(); } }
}
async function deleteConversation(): Promise<void> {
  if (!conversation || !deleteConfirm.checked || activeRun?.status === 'running' || mutating) return;
  const captured = scope();
  mutating = true; updateControls();
  try {
    await client.deleteConversation(conversation.id, { signal: viewAbort.signal });
    if (!current(captured)) return;
    drafts.delete(conversation.id); pending.delete(conversation.id); clearView();
    setNotice(message, '会话已删除。');
    await loadConversations(false);
  } catch (error) { fail(error, captured); }
  finally { if (current(captured)) { mutating = false; updateControls(); } }
}
async function send(): Promise<void> {
  if (!conversation || activeRun?.status === 'running' || posting || loadingView || pending.has(conversation.id)) return;
  const content = draft.value;
  if (!content.trim()) { setNotice(message, '请输入消息内容。', true); return; }
  try {
    pending.set(conversation.id, newPending(crypto.randomUUID(), content, mode.value as RunMode));
  } catch {
    setNotice(message, '无法生成安全消息编号。请通过 SSH 隧道的 localhost / 127.0.0.1 或 HTTPS 访问。', true);
    return;
  }
  await postPending();
}
async function retry(): Promise<void> { if (!retryButton.disabled) await postPending(); }
async function postPending(): Promise<void> {
  if (!conversation || posting) return;
  const captured = scope();
  const p = pending.get(conversation.id);
  if (!p) return;
  posting = true; p.uncertain = false; updateControls();
  try {
    const run = await client.createRun(conversation.id, retryPayload(p), { signal: viewAbort.signal });
    if (captured.auth !== authEpoch) return;
    pending.delete(captured.conversationId);
    if (drafts.get(captured.conversationId) === p.content) drafts.set(captured.conversationId, '');
    if (!current(captured)) return;
    if (draft.value === p.content) draft.value = '';
    activeRun = run; runState = runLabel(run.status); subscriptionPaused = false;
    setNotice(message, '消息已发送，正在等待回复。');
    // Refresh persisted user/assistant message records; subscription replays from zero.
    await loadCurrent(false);
    if (current(captured) && !streamAbort) void subscribe(run);
  } catch (error) {
    if (captured.auth !== authEpoch) return;
    // Explicit client errors are determinate; network errors/5xx/abort keep exact retry identity.
    const definite = error instanceof AlliraApiError && error.status >= 400 && error.status < 500;
    if (definite) pending.delete(captured.conversationId);
    else p.uncertain = true;
    fail(error, captured);
  } finally {
    if (current(captured)) { posting = false; updateControls(); }
    else if (captured.auth === authEpoch && captured.conversationId === conversation?.id) updateControls();
  }
}
function discardPending(): void {
  if (!conversation || !discardConfirm.checked || posting) return;
  pending.delete(conversation.id); discardConfirm.checked = false;
  setNotice(message, '仅放弃本地重试记录；后台任务若已创建仍会继续，请刷新状态确认。'); updateControls();
}
function disconnect(): void {
  subscriptionPaused = true;
  streamAbort?.abort(); streamAbort = undefined;
  messageList.cancelPaint(); renderMessages('preserve');
  connection = '已断开（后台不取消）'; updateControls();
}
async function resume(): Promise<void> {
  if (!conversation || streamAbort || loadingView) return;
  subscriptionPaused = false;
  const run = activeRun ?? streamView?.run;
  if (run) { await subscribe(run); return; }
  await refreshCurrent();
}
async function subscribe(run: Run): Promise<void> {
  if (!conversation || streamAbort || subscriptionPaused) return;
  const captured = scope();
  const controller = new AbortController();
  streamAbort = controller;
  let retries = 0;
  const valid = () => current(captured) && streamAbort === controller && !controller.signal.aborted;
  try {
    while (valid()) {
      await waitForOnline(controller.signal);
      if (!valid()) return;
      streamView = beginReplay(run); // Every reconnect is a GET of the original run after:0, never a POST.
      connection = retries ? `重新连接 ${retries}/${maxReconnects}` : '连接中';
      updateControls(); renderMessages('preserve');
      try {
        for await (const event of client.streamRunEvents(run.id, { after: 0, signal: controller.signal })) {
          if (!valid()) return;
          streamView = applyRunEvent(streamView!, event);
          runState = runLabel(streamView.run.status);
          activeRun = streamView.run.status === 'running' ? streamView.run : null;
          if (streamView.terminal) {
            messages = mergeMessages(messages, [streamView.terminal]);
            connection = '已收到终态';
            renderMessages('preserve'); updateControls(); // Terminal snapshot bypasses the paint timer.
            setNotice(message, `回复${runLabel(streamView.run.status)}。`);
            void loadConversations(false);
            return;
          }
          if (connection !== '订阅中') { connection = '订阅中'; updateControls(); }
          messageList.schedule(() => valid() ? streamView : null);
        }
        throw new StreamInterruptedError(streamView?.lastEventId ?? 0);
      } catch (error) {
        if (!valid()) return;
        renderMessages('preserve');
        if (error instanceof AlliraApiError && error.status === 401) { fail(error, captured); return; }
        if (!retryableStreamError(error) || retries >= maxReconnects) {
          subscriptionPaused = true;
          connection = error instanceof StreamProtocolError ? '协议异常（不会自动重连）' : `已断开，自动恢复已停止（${retries}/${maxReconnects}）`;
          fail(error, captured); updateControls(); return;
        }
        ++retries;
        connection = navigator.onLine ? `等待自动恢复 ${retries}/${maxReconnects}` : `离线，等待网络恢复 ${retries}/${maxReconnects}`;
        updateControls();
        await waitForOnline(controller.signal);
        await delay(retryDelay(retries), controller.signal);
      }
    }
  } catch (error) {
    if (valid() && !isAbort(error)) fail(error, captured);
  } finally {
    if (current(captured) && streamAbort === controller) { streamAbort = undefined; updateControls(); }
  }
}
async function stop(): Promise<void> {
  if (!activeRun || activeRun.status !== 'running' || mutating) return;
  const captured = scope(); const run = activeRun;
  mutating = true; updateControls();
  try {
    const stopped = await client.cancelRun(run.id, { signal: viewAbort.signal });
    if (!current(captured)) return;
    activeRun = stopped.status === 'running' ? stopped : null; runState = runLabel(stopped.status);
    setNotice(message, `停止请求已返回：${runLabel(stopped.status)}；以服务端真实终态为准。`);
    subscriptionPaused = false;
    if (!streamAbort) void subscribe(stopped);
  } catch (error) { fail(error, captured); }
  finally { if (current(captured)) { mutating = false; updateControls(); } }
}
updateControls();
