import { AlliraApiError } from '../../../packages/client/src/index.ts';
import type { AccountIdentity, PhoneChallenge } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, isAbort, when } from './dom.ts';
import type { FeatureContext } from './feature.ts';
import { details } from './feature.ts';
import { identityError, identityUncertain, normalizePhone, validPhoneCode } from './identity.ts';
import { validPassword } from './validation.ts';

/** Binding keeps the immutable owner id. It never silently merges another account. */
export function mountPhoneAccount(context: FeatureContext, initial: AccountIdentity): void {
  const { client, signal, target } = context;
  let identity = initial; let challenge: PhoneChallenge | null = null; let challengedPhone = '';
  let busy = false; let uncertain = false; let enabled = false; let epoch = 0; let cooldown = 0;
  let abort = new AbortController(); let timer: ReturnType<typeof setInterval> | undefined;
  const metadata = el('div');
  const phone = el('input', { name: 'bindAccountPhone', type: 'tel', autocomplete: 'off', maxlength: '14' });
  const password = el('input', { name: 'bindAccountPassword', type: 'password', autocomplete: 'current-password', maxlength: '256' });
  const code = el('input', { name: 'bindAccountCode', type: 'password', inputmode: 'numeric', autocomplete: 'off', maxlength: '6' });
  const consent = el('input', { type: 'checkbox' }); const notice = empty('同一账号绑定后保留原有聊天；号码已属于其他账号时不会自动合并。');
  const send = button('发送绑定手机验证码', () => perform(false));
  const bind = button('确认绑定此手机号', () => perform(true));
  const refresh = button('重新读取手机身份', async () => {
    if (busy || !context.valid() || !navigator.onLine) return;
    const request = ++epoch; abort.abort(); abort = new AbortController(); busy = true; update();
    try { const value = await client.getAccountIdentity({ signal: abort.signal }); if (valid(request)) { identity = value; uncertain = false; clear(); } }
    catch (error) { report(error, request); }
    finally { if (valid(request)) { busy = false; update(); } }
  });
  const form = el('div', {}, field('绑定手机号', phone), field('绑定手机的当前密码', password), send, notice, field('绑定手机的六位验证码', code), field('确认将此手机号绑定到当前账号，保留当前账号数据且不合并其他账号', consent), bind);
  append(target, el('h3', {}, '手机与授权身份'), metadata, form, refresh);
  function valid(request: number): boolean { return context.valid() && !signal.aborted && request === epoch; }
  function clear(): void { challenge = null; challengedPhone = ''; phone.value = ''; password.value = ''; code.value = ''; consent.checked = false; }
  function update(): void {
    metadata.replaceChildren(details([['主身份', identity.primaryIdentifier === 'phone' ? '手机号' : '旧用户名'], ['绑定手机', identity.maskedPhone ?? '尚未绑定'], ['手机验证', identity.phoneVerified ? '已验证' : '未验证'], ['站内密码', identity.passwordEnabled ? '已设置' : '未设置'], ['社交授权', identity.social.map(item => item.provider === 'wechat' ? '微信' : 'QQ').join('、') || '无']]));
    form.hidden = !identity.requiresPhoneBinding || !identity.passwordEnabled;
    const seconds = Math.max(0, Math.ceil((cooldown - Date.now()) / 1000));
    send.textContent = seconds ? `发送绑定手机验证码（${seconds} 秒后可重发）` : '发送绑定手机验证码';
    send.disabled = busy || uncertain || !enabled || seconds > 0; bind.disabled = busy || uncertain || !enabled || !challenge; refresh.disabled = busy;
    phone.disabled = busy; password.disabled = busy; code.disabled = busy;
    if (challenge && Date.parse(challenge.expiresAt) <= Date.now()) { challenge = null; code.value = ''; bind.disabled = true; notice.textContent = '验证码已过期，请重新验证。'; }
  }
  function report(error: unknown, request: number): void { if (!valid(request) || isAbort(error)) return; if (error instanceof AlliraApiError && error.status === 401) context.fail(error); else context.notify(identityError(error)); }
  phone.addEventListener('input', () => { ++epoch; abort.abort(); abort = new AbortController(); challenge = null; challengedPhone = ''; code.value = ''; consent.checked = false; uncertain = false; update(); });
  async function perform(commit: boolean): Promise<void> {
    if (busy || uncertain || !context.valid() || !navigator.onLine || (commit ? bind.disabled : send.disabled)) return;
    const normalized = normalizePhone(phone.value);
    if (!normalized || !validPassword(password.value)) { context.notify('请填写有效大陆手机号和当前账号密码。'); return; }
    if (commit && (!challenge || challengedPhone !== normalized || !validPhoneCode(code.value) || !consent.checked || Date.parse(challenge.expiresAt) <= Date.now())) { context.notify('请为当前手机号获取有效验证码，并确认绑定。'); return; }
    const request = ++epoch; abort.abort(); abort = new AbortController(); busy = true;
    const payload = { phone: normalized, currentPassword: password.value, challengeId: challenge?.challengeId ?? '', code: code.value };
    if (!commit) { challenge = null; code.value = ''; cooldown = Date.now() + 60000; if (!timer) timer = setInterval(update, 1000); } update();
    try {
      if (commit) { const value = await client.bindPhone({ challengeId: payload.challengeId, code: payload.code, currentPassword: payload.currentPassword }, { signal: abort.signal }); if (valid(request)) { identity = value; clear(); context.notify('手机号已绑定，原账号与聊天保持不变。'); } }
      else { const value = await client.requestBindPhoneChallenge({ phone: payload.phone, currentPassword: payload.currentPassword }, { signal: abort.signal }); if (valid(request)) { challenge = value; challengedPhone = normalized; cooldown = Date.now() + value.resendAfterSeconds * 1000; notice.textContent = `验证码已发送至 ${value.maskedPhone}，到期：${when(value.expiresAt)}。不会在网页回显验证码。`; } }
    } catch (error) { if (valid(request)) { uncertain = identityUncertain(error); if (error instanceof AlliraApiError && error.status === 401) challenge = null; } report(error, request); }
    finally { payload.currentPassword = ''; payload.code = ''; if (valid(request)) { password.value = ''; code.value = ''; busy = false; update(); } }
  }
  void client.getAuthOptions({ signal }).then(value => { if (context.valid()) { enabled = value.phone.enabled; if (!enabled) notice.textContent = '短信通道未开通，暂时无法绑定手机号；现有旧用户名账号和聊天保持不变。'; update(); } }).catch(error => { if (context.valid() && !isAbort(error)) { notice.textContent = '暂时无法检查短信通道，手机绑定已停用。'; if (error instanceof AlliraApiError && error.status === 401) context.fail(error); } });
  signal.addEventListener('abort', () => { ++epoch; abort.abort(); clear(); metadata.replaceChildren(); if (timer) clearInterval(timer); }, { once: true });
  update();
}
