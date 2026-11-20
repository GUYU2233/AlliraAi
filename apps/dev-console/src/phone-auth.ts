import { AlliraApiError, AlliraSocialClient } from '../../../packages/client/src/index.ts';
import type { AccountSession, AlliraClient, AuthOptions, PhoneChallenge, SocialPending, SocialProvider, Terms } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, input, isAbort, setNotice, when } from './dom.ts';
import { identityError, identityUncertain, normalizePhone, socialAuthorizeUrl, validPhoneCode } from './identity.ts';
import { validDisplayName, validPassword } from './validation.ts';

/** Public phone flows and the separate OAuth transition cookie never share account credentials. */
export function mountPhoneAuth(target: HTMLElement, client: AlliraClient, status: HTMLElement,
  signedIn: (session: AccountSession, epoch: number, remember: boolean) => Promise<void>,
  begin: () => number, current: (epoch: number) => boolean, snapshot: () => number,
  configured: (options: AuthOptions | null, legacyFallback: boolean) => void): { invalidate(): void } {
  const social = new AlliraSocialClient({ baseUrl: location.origin });
  let options: AuthOptions | null = null; let terms: Terms | null = null;
  let version = 0; let busy = false; let uncertain = false; let abort = new AbortController();
  let challenge: { value: PhoneChallenge; phone: string; purpose: string } | null = null;
  let pending: SocialPending | null = null; let socialReady = false; let nextSend = 0;
  let timer: ReturnType<typeof setInterval> | undefined;
  let optionsVersion = 0; let optionsAbort = new AbortController();
  const availability = empty('正在检查手机与社交登录方式…');
  const purpose = el('select', { name: 'phonePurpose', 'aria-label': '手机号操作' }, el('option', { value: 'login' }, '手机号登录'), el('option', { value: 'register' }, '手机号注册'));
  const phone = el('input', { name: 'authPhone', type: 'tel', inputmode: 'tel', autocomplete: 'off', maxlength: '14', placeholder: '大陆手机号或 +86 号码' });
  const code = el('input', { name: 'authPhoneCode', type: 'password', inputmode: 'numeric', autocomplete: 'off', maxlength: '6' });
  const password = el('input', { name: 'optionalPhonePassword', type: 'password', autocomplete: 'new-password', maxlength: '256' });
  const displayName = input('phoneDisplayName', '可留空'); displayName.maxLength = 160;
  const adult = el('input', { type: 'checkbox' }); const accept = el('input', { type: 'checkbox' });
  const bindConfirm = el('input', { type: 'checkbox' });
  const remember = el('input', { type: 'checkbox', name: 'rememberPhoneDevice' });
  const termsText = el('pre', { class: 'message-content' }, '正在读取协议…');
  const optionalPassword = el('details', { 'aria-label': '可选设置站内密码' }, el('summary', {}, '可选：设置或验证站内密码'), empty('可不设置密码。敏感操作可使用已绑定手机验证；若填写密码，需为 10–128 字符且最多 256 UTF-8 字节。关联已有手机号时，填写的密码还会由服务器核验。'), field('可选站内密码', password));
  const registration = el('div', {}, field('手机账号显示名称（可选）', displayName), field('手机注册或关联：我已满 18 岁', adult), field('手机注册或关联：接受当前服务协议', accept), el('details', {}, el('summary', {}, '手机账号服务协议'), termsText), optionalPassword);
  const challengeText = empty('验证码仅通过已开通的短信通道发送，不会在此页面显示。');
  const send = button('发送手机验证码', sendCode);
  const submit = button('验证码登录', () => finish(false));
  const continueSocial = button('继续社交账号登录', completeSocial);
  const cancelSocial = button('取消本次社交授权', cancelPending);
  const pendingText = empty('');
  const bindField = field('确认验证手机号并关联此微信或 QQ 身份；手机号已有账号时关联至该账号，不合并其他账号数据', bindConfirm);
  const loginPassword = el('input', { name: 'phoneLoginPassword', type: 'password', autocomplete: 'current-password', maxlength: '256' });
  const passwordLogin = button('使用手机号和密码登录', () => finish(true));
  const passwordWay = el('details', { 'aria-label': '手机号密码登录' }, el('summary', {}, '可选：手机号和密码登录'), field('手机号登录密码', loginPassword), passwordLogin);
  const refresh = button('重新检查登录方式', async () => { invalidate(); await loadOptions(); });
  const socialButtons = el('div', { class: 'actions' });
  const starts = new Map<SocialProvider, HTMLButtonElement>();
  for (const provider of ['wechat', 'qq'] as const) { const item = button(provider === 'wechat' ? '微信登录' : 'QQ 登录', () => startSocial(provider)); starts.set(provider, item); append(socialButtons, item); }
  const form = el('form', { 'aria-label': '手机号登录和注册' }, field('手机号操作', purpose), pendingText, field('登录手机号', phone), send, challengeText, field('六位手机验证码', code), registration, bindField, field('手机登录：记住此设备（共享设备请勿勾选）', remember), el('div', { class: 'actions' }, submit, continueSocial, cancelSocial), passwordWay);
  form.addEventListener('submit', event => { event.preventDefault(); void finish(false); });
  append(target, el('h2', {}, '登录后开始聊天'), availability, form, socialButtons, refresh, empty('验证码、手机号和密码不保存到浏览器存储。微信与 QQ 只在服务端实际开通时可用；授权回来不会自动完成账号登录。'));

  function update(): void {
    const binding = !!pending?.requiresPhoneBinding;
    const linked = !!pending && !binding;
    const registrationMode = binding || purpose.value === 'register';
    const legacy = options?.backend !== 'oidc';
    const phoneEnabled = legacy && !!options?.phone.enabled;
    const cooldown = Math.max(0, Math.ceil((nextSend - Date.now()) / 1000));
    purpose.hidden = !!pending; purpose.disabled = busy || uncertain;
    registration.hidden = !registrationMode || linked;
    bindField.hidden = !binding; passwordWay.hidden = !!pending;
    phone.parentElement!.hidden = linked; code.parentElement!.hidden = linked; send.hidden = linked;
    submit.hidden = linked; continueSocial.hidden = !linked; cancelSocial.hidden = !pending;
    submit.textContent = binding ? '验证手机号并关联社交身份' : registrationMode ? '验证手机号并注册' : '验证码登录';
    submit.disabled = busy || uncertain || !phoneEnabled || !challenge || (registrationMode && !terms) || (!binding && registrationMode && !options?.registrationEnabled);
    send.disabled = busy || uncertain || !phoneEnabled || cooldown > 0 || (!binding && registrationMode && !options?.registrationEnabled);
    send.textContent = cooldown ? `发送手机验证码（${cooldown} 秒后可重发）` : '发送手机验证码';
    continueSocial.disabled = busy || uncertain || !socialReady; cancelSocial.disabled = busy || !socialReady;
    passwordLogin.disabled = busy || uncertain || !options || !legacy || !!pending; refresh.disabled = busy;
    phone.disabled = busy; code.disabled = busy; password.disabled = busy; loginPassword.disabled = busy; displayName.disabled = busy; adult.disabled = busy; accept.disabled = busy; bindConfirm.disabled = busy;
    send.hidden = linked || !phoneEnabled; code.parentElement!.hidden = linked || !phoneEnabled;
    for (const [provider, item] of starts) { const enabled = legacy && !!options?.social.find(value => value.provider === provider)?.enabled; item.hidden = !enabled; item.disabled = busy || uncertain || !!pending || !enabled; }
    pendingText.hidden = !pending;
    pendingText.textContent = pending ? `${pending.provider === 'wechat' ? '微信' : 'QQ'}授权身份：${pending.displayName}。${binding ? '尚未登录，必须验证手机号并明确关联。' : '已有关联账号，请点击继续完成登录。'}授权到期：${when(pending.expiresAt)}` : '';
    if (challenge && Date.parse(challenge.value.expiresAt) <= Date.now()) { challenge = null; code.value = ''; challengeText.textContent = '验证码已过期，请重新发送。'; submit.disabled = true; }
  }
  function clearChallenge(): void { challenge = null; code.value = ''; challengeText.textContent = '手机号或用途改变后，旧验证码不可在此表单继续使用。'; }
  function changed(): void { ++version; abort.abort(); abort = new AbortController(); busy = false; uncertain = false; clearChallenge(); bindConfirm.checked = false; update(); }
  phone.addEventListener('input', changed); purpose.addEventListener('change', () => { changed(); password.value = ''; displayName.value = ''; adult.checked = false; accept.checked = false; });
  function startRequest(): { own: number; auth: number; signal: AbortSignal } {
    const auth = begin(); ++version; abort.abort(); abort = new AbortController(); busy = true; update();
    return { own: version, auth, signal: abort.signal };
  }
  const valid = (request: { own: number; auth: number }): boolean => request.own === version && current(request.auth);
  async function receive(session: AccountSession, request: { own: number; auth: number }, remembered: boolean): Promise<void> {
    try { if (valid(request)) { clearSecrets(); pending = null; social.clearSession(); socialReady = false; await signedIn(session, request.auth, remembered); } }
    finally { session.accessToken = ''; }
  }
  function clearSecrets(): void { challenge = null; phone.value = ''; code.value = ''; password.value = ''; loginPassword.value = ''; displayName.value = ''; adult.checked = false; accept.checked = false; bindConfirm.checked = false; remember.checked = false; }
  function invalidate(): void {
    ++version; abort.abort(); abort = new AbortController(); busy = false; uncertain = false; clearSecrets(); pending = null; socialReady = false; social.clearSession(); purpose.value = 'login'; optionalPassword.open = false; passwordWay.open = false;
    nextSend = 0; if (timer) clearInterval(timer); timer = undefined; challengeText.textContent = '验证码仅通过已开通的短信通道发送，不会在此页面显示。'; update();
  }
  async function sendCode(): Promise<void> {
    if (send.disabled || !navigator.onLine) { if (!navigator.onLine) setNotice(status, '当前离线，不能发送验证码。', true); return; }
    const normalized = normalizePhone(phone.value);
    if (!normalized) { setNotice(status, '请输入大陆 11 位 ASCII 手机号，或对应 +86 号码，不含空格。', true); return; }
    const desired = pending?.requiresPhoneBinding ? 'social' : purpose.value;
    const request = startRequest(); clearChallenge(); nextSend = Date.now() + 60000;
    if (!timer) timer = setInterval(update, 1000);
    try {
      const value = desired === 'social' ? await social.requestPhoneChallenge({ phone: normalized }, { signal: request.signal }) : await client.requestPhoneChallenge({ phone: normalized, purpose: desired === 'register' ? 'register' : 'login' }, { signal: request.signal });
      if (!valid(request)) return;
      challenge = { value, phone: normalized, purpose: desired }; nextSend = Date.now() + Math.max(0, value.resendAfterSeconds) * 1000;
      challengeText.textContent = `验证码已发送至 ${value.maskedPhone}，到期：${when(value.expiresAt)}。验证码只在短信中查看。`;
    } catch (error) { if (valid(request) && !isAbort(error)) { uncertain = identityUncertain(error); setNotice(status, identityError(error), true); } }
    finally { if (valid(request)) { busy = false; update(); } }
  }
  async function finish(byPassword: boolean): Promise<void> {
    if (busy || uncertain || !options || options.backend === 'oidc' || (!byPassword && !options.phone.enabled) || (byPassword && !!pending) || !navigator.onLine) return;
    const normalized = normalizePhone(phone.value); const binding = !!pending?.requiresPhoneBinding; const create = !binding && purpose.value === 'register';
    if (!normalized || (byPassword ? !validPassword(loginPassword.value) : !validPhoneCode(code.value))) { setNotice(status, '请填写有效手机号和六位验证码，或有效的登录密码。', true); return; }
    if (!byPassword && (!challenge || challenge.phone !== normalized || challenge.purpose !== (binding ? 'social' : purpose.value) || Date.parse(challenge.value.expiresAt) <= Date.now())) { clearChallenge(); update(); setNotice(status, '请先为当前手机号和用途重新获取验证码。', true); return; }
    if (!byPassword && (binding || create) && (!terms || !adult.checked || !accept.checked || (binding && !bindConfirm.checked))) { setNotice(status, '请确认成年、接受协议，并在社交关联时明确同意手机号关联。', true); return; }
    if (!byPassword && create && !options.registrationEnabled) return;
    if (!byPassword && (binding || create) && ((password.value && !validPassword(password.value)) || (displayName.value && !validDisplayName(displayName.value)))) { setNotice(status, '可选密码或显示名称不符合长度要求。', true); return; }
    const remembered = remember.checked; const payload = { challengeId: challenge?.value.challengeId ?? '', code: code.value, ...(password.value ? { password: password.value } : {}), ...(displayName.value ? { displayName: displayName.value } : {}) };
    const passwordPayload = { phone: normalized, password: loginPassword.value }; const request = startRequest();
    try {
      const session = byPassword ? await client.loginPhonePassword(passwordPayload, { signal: request.signal }) : binding ? await social.bindPhone({ ...payload, acceptedTermsVersion: terms!.version, isAdult: true, confirmBind: true }, { signal: request.signal }) : create ? await client.registerPhone({ ...payload, acceptedTermsVersion: terms!.version, isAdult: true }, { signal: request.signal }) : await client.loginPhone({ challengeId: payload.challengeId, code: payload.code }, { signal: request.signal });
      await receive(session, request, remembered);
    } catch (error) { if (valid(request) && !isAbort(error)) { uncertain = identityUncertain(error); if (error instanceof AlliraApiError && error.status === 401) clearChallenge(); setNotice(status, identityError(error), true); } }
    finally { payload.code = ''; if (payload.password) payload.password = ''; passwordPayload.password = ''; if (valid(request)) { code.value = ''; password.value = ''; loginPassword.value = ''; busy = false; update(); } }
  }
  async function readSocial(initial = false): Promise<void> {
    if (options?.backend === 'oidc' || !options?.social.some(item => item.enabled)) return;
    const own = version; const auth = snapshot();
    try { const value = await social.getSession({ signal: abort.signal }); if (own !== version || !current(auth)) return; pending = value.pending; socialReady = true; update(); }
    catch (error) { if (own === version && current(auth) && !isAbort(error)) { socialReady = false; if (!initial) setNotice(status, identityError(error), true); } }
  }
  async function startSocial(provider: SocialProvider): Promise<void> {
    const configuredProvider = options?.social.find(item => item.provider === provider);
    if (!configuredProvider?.enabled || busy || uncertain || pending || !navigator.onLine) return;
    const request = startRequest();
    try {
      if (!socialReady) { const session = await social.getSession({ signal: request.signal }); if (!valid(request)) return; socialReady = true; if (session.pending) { pending = session.pending; return; } }
      const result = await social.start(provider, { signal: request.signal }); if (!valid(request)) return;
      const url = socialAuthorizeUrl(result.authorizeUrl, provider, configuredProvider.simulated, location.origin);
      if (!url) { uncertain = true; setNotice(status, '授权地址不符合允许的通道，已阻止跳转。请重新检查登录方式。', true); return; }
      location.assign(url);
    } catch (error) { if (valid(request) && !isAbort(error)) { uncertain = identityUncertain(error); setNotice(status, identityError(error), true); } }
    finally { if (valid(request)) { busy = false; update(); } }
  }
  async function completeSocial(): Promise<void> {
    if (!pending || pending.requiresPhoneBinding || !socialReady || busy || uncertain || !navigator.onLine) return;
    const remembered = remember.checked; const request = startRequest();
    try { await receive(await social.complete({ signal: request.signal }), request, remembered); }
    catch (error) { if (valid(request) && !isAbort(error)) { uncertain = identityUncertain(error); setNotice(status, identityError(error), true); } }
    finally { if (valid(request)) { busy = false; update(); } }
  }
  async function cancelPending(): Promise<void> {
    if (!socialReady || busy || !navigator.onLine) return;
    const request = startRequest();
    try { await social.cancel({ signal: request.signal }); if (valid(request)) { pending = null; socialReady = false; clearSecrets(); uncertain = false; setNotice(status, '本次社交授权已取消。'); } }
    catch (error) { if (valid(request) && !isAbort(error)) { uncertain = identityUncertain(error); setNotice(status, identityError(error), true); } }
    finally { if (valid(request)) { busy = false; update(); } }
  }
  async function loadOptions(): Promise<void> {
    const request = ++optionsVersion; const auth = snapshot(); const own = version;
    optionsAbort.abort(); optionsAbort = new AbortController();
    try {
      const value = await client.getAuthOptions({ signal: optionsAbort.signal });
      if (request !== optionsVersion) return;
      options = value; configured(options, false);
      if (value.backend === 'oidc') { invalidate(); return; }
      availability.textContent = options.phone.enabled ? `手机验证码登录已开通。${options.phone.simulated ? '当前为受保护的测试短信通道，不代表已接通真实短信。' : ''}` : '短信通道未开通，验证码与社交登录当前不可用。已设密码的手机账号可展开“手机号和密码登录”，旧账号可展开旧用户名方式。';
      update(); if (own === version && current(auth)) await readSocial(true);
    } catch (error) {
      if (request !== optionsVersion || isAbort(error)) return;
      options = null; configured(null, error instanceof AlliraApiError && error.status === 404);
      availability.textContent = '登录方式配置暂时无法读取，手机与社交登录已停用。不会伪造验证码或自动尝试其他注册方式。'; update();
    }
  }
  void client.getTerms().then(value => { terms = value; termsText.textContent = `版本：${value.version}\n${value.content}`; update(); }).catch(() => { termsText.textContent = '协议暂时无法读取，注册与新关联已停用。'; });
  update(); void loadOptions();
  return { invalidate };
}
