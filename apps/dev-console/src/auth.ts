import type { AlliraClient, AccountSession, AuthOptions, Terms } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, friendlyError, input, setNotice } from './dom.ts';
import { validDisplayName, validPassword, validUsername } from './validation.ts';

/** Passwords are DOM inputs only while submitting; no browser storage or logs. */
export function mountAccountAuth(target: HTMLElement, client: AlliraClient, status: HTMLElement, signedIn: (session: AccountSession, authEpoch: number, remember: boolean) => Promise<void>, begin: () => number, current: (epoch: number) => boolean): { invalidate(): void; configure(options: AuthOptions | null, legacyFallback?: boolean): void } {
  let epoch = 0;
  let busy = false;
  let terms: Terms | null = null;
  let abort = new AbortController();
  let loginAllowed = false;
  let registerAllowed = false;
  const remember = el('input', { type: 'checkbox', name: 'rememberDevice' });
  const username = input('accountUsername', '3–32 位英文字母、数字或下划线');
  username.autocomplete = 'username'; username.maxLength = 32;
  const password = el('input', { name: 'accountPassword', type: 'password', autocomplete: 'current-password', minlength: '10', maxlength: '256' });
  const displayName = input('accountDisplayName', '注册时可填'); displayName.maxLength = 160; // JS maxlength counts UTF-16; custom validation uses server Unicode character bounds.
  const adult = el('input', { type: 'checkbox', name: 'adult' });
  const accept = el('input', { type: 'checkbox', name: 'terms' });
  const termsText = el('pre', { class: 'message-content' }, '正在读取协议…');
  const login = button('账号登录', () => submit(false));
  const register = button('注册账号', () => submit(true)); register.disabled = true;
  const registration = el('div', {}, field('账号显示名称（注册）', displayName), field('我已满 18 岁', adult), field('我已阅读并接受当前版本协议（注册必选）', accept), el('details', {}, el('summary', {}, '服务协议与边界'), termsText));
  registration.hidden = true;
  const form = el('form', { 'aria-label': '账号注册和登录' }, field('账号用户名', username), field('账号密码（10–128 字符，最多 256 UTF-8 字节）', password), field('记住此设备（可选，使用独立 HttpOnly Cookie，不保存 Bearer）', remember), registration, el('div', { class: 'actions' }, login, register));
  form.addEventListener('submit', event => { event.preventDefault(); void submit(false); });
  const registrationHint = empty('新账号请使用上方手机号注册。旧用户名登录继续保留，原有数据不变。');
  register.hidden = true; login.disabled = true;
  append(target, el('h2', {}, '旧用户名账号'), registrationHint, empty('默认只在本页内存保存登录；仅主动勾选“记住此设备”才交换受保护的浏览器 Cookie。共享设备不建议勾选。'), form);
  void client.getTerms().then(value => { terms = value; termsText.textContent = `版本：${value.version}\n${value.content}`; register.disabled = busy || !registerAllowed; }).catch(() => { termsText.textContent = '协议暂时无法读取，注册已停用。'; });
  async function submit(create: boolean): Promise<void> {
    if (busy || !loginAllowed || (create && (!terms || !registerAllowed))) return;
    if (!navigator.onLine) { setNotice(status, '当前离线，不能登录或注册。', true); return; }
    if (!validUsername(username.value) || !validPassword(password.value)) { setNotice(status, '用户名为 3–32 位英文字母、数字或下划线；密码为 10–128 字符且不超过 256 UTF-8 字节。', true); return; }
    if (create && displayName.value && !validDisplayName(displayName.value)) { setNotice(status, '显示名称需为 1–80 字符、最多 240 UTF-8 字节，且不含控制字符。', true); return; }
    if (create && (!adult.checked || !accept.checked)) { setNotice(status, '注册前请确认已满 18 岁并接受服务协议。', true); return; }
    const captured = epoch;
    const authRequest = begin();
    abort.abort(); abort = new AbortController();
    const requestSignal = abort.signal;
    const remembered = remember.checked;
    const credentials = { username: username.value, password: password.value };
    busy = true; login.disabled = true; register.disabled = true;
    try {
      const session = create
        ? await client.register({ ...credentials, ...(displayName.value ? { displayName: displayName.value } : {}), acceptedTermsVersion: terms!.version, isAdult: true }, { signal: requestSignal })
        : await client.login(credentials, { signal: requestSignal });
      try {
        if (captured !== epoch || !current(authRequest)) return;
        password.value = ''; username.value = ''; displayName.value = ''; adult.checked = false; accept.checked = false;
        await signedIn(session, authRequest, remembered);
      } finally { session.accessToken = ''; }
    } catch (error) { if (captured === epoch && current(authRequest)) setNotice(status, friendlyError(error), true); }
    finally { credentials.password = ''; if (captured === epoch) { password.value = ''; busy = false; login.disabled = !loginAllowed; register.disabled = !terms || !registerAllowed; } }
  }
  return {
    invalidate() { ++epoch; abort.abort(); busy = false; remember.checked = false; password.value = ''; username.value = ''; displayName.value = ''; adult.checked = false; accept.checked = false; login.disabled = !loginAllowed; register.disabled = !terms || !registerAllowed; },
    configure(options, legacyFallback = false) {
      loginAllowed = options?.backend !== 'oidc' && (options?.legacyPasswordEnabled ?? legacyFallback);
      // The new options deliberately do not grant legacy registration. Only a missing old endpoint is compatible.
      registerAllowed = legacyFallback && !options;
      register.hidden = !registerAllowed; registration.hidden = !registerAllowed; register.disabled = busy || !terms || !registerAllowed;
      login.disabled = busy || !loginAllowed;
      registrationHint.textContent = registerAllowed ? '兼容旧服务的用户名注册；新服务请使用手机号。' : loginAllowed ? '新账号请使用上方手机号注册。旧用户名登录继续保留，原有数据不变。' : '旧用户名登录当前不可用。';
    },
  };
}
