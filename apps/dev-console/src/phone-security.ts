import { AlliraApiError } from '../../../packages/client/src/index.ts';
import type { PhoneChallenge } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, isAbort, when } from './dom.ts';
import type { FeatureContext } from './feature.ts';
import { identityError, identityUncertain, validPhoneCode } from './identity.ts';
import { validPassword } from './validation.ts';

/** Accounts without a password use purpose-bound phone proof, not an empty current password. */
export function mountPhoneSecurity(context: FeatureContext): void {
  const { client, signal, target } = context;
  let challenge: { value: PhoneChallenge; purpose: 'set_password' | 'erase' } | null = null;
  let busy = false; let uncertain = false; let enabled = false; let epoch = 0; let nextSend = 0;
  let abort = new AbortController(); let timer: ReturnType<typeof setInterval> | undefined;
  const status = empty('正在检查手机验证通道…');
  const purpose = el('select', { 'aria-label': '手机安全验证用途' }, el('option', { value: 'set_password' }, '设置站内密码'), el('option', { value: 'erase' }, '删除账号'));
  const code = el('input', { name: 'securityPhoneCode', type: 'password', inputmode: 'numeric', autocomplete: 'off', maxlength: '6' });
  const password = el('input', { name: 'securityPhonePassword', type: 'password', autocomplete: 'new-password', maxlength: '256' });
  const passwordConsent = el('input', { type: 'checkbox' });
  const eraseText = el('input', { name: 'erasePhoneConfirmation', type: 'text', autocomplete: 'off', placeholder: 'DELETE MY ACCOUNT' });
  const eraseConsent = el('input', { type: 'checkbox' });
  const send = button('发送账号安全验证码', sendCode);
  const submit = button('通过手机设置密码并退出全部登录', commit);
  const reset = button('重新开始手机安全验证', () => { if (busy) return; ++epoch; abort.abort(); abort = new AbortController(); clear(); uncertain = false; update(); });
  const setting = el('div', {}, field('手机验证后的新密码', password), field('确认设置密码并撤销此账号全部登录', passwordConsent));
  const erasing = el('div', {}, empty('不可撤销地删除在线业务数据，撤销全部登录和恢复码。有活动或正在关闭的生成时拒绝（409）。匿名核销墓碑和既有备份按当前保留策略留存，不承诺物理擦除。'), field('手机删除：逐字输入 DELETE MY ACCOUNT', eraseText), field('手机删除：确认永久删除账号和在线数据且无法撤销', eraseConsent));
  append(target, el('details', { 'aria-label': '手机验证敏感操作' }, el('summary', {}, '手机验证：设置密码或删除账号'), empty('此账号未设置站内密码。验证码只发往服务器已验证的绑定手机号，不接受在此更换号码。不同用途的验证码不能混用。'), field('手机安全验证用途', purpose), status, send, field('账号安全六位验证码', code), setting, erasing, submit, reset));
  const valid = (request: number): boolean => context.valid() && !signal.aborted && request === epoch;
  function clear(): void { challenge = null; code.value = ''; password.value = ''; eraseText.value = ''; passwordConsent.checked = false; eraseConsent.checked = false; }
  function update(): void {
    const erase = purpose.value === 'erase'; setting.hidden = erase; erasing.hidden = !erase;
    const seconds = Math.max(0, Math.ceil((nextSend - Date.now()) / 1000));
    send.textContent = seconds ? `发送账号安全验证码（${seconds} 秒后可重发）` : '发送账号安全验证码';
    send.disabled = busy || uncertain || !enabled || seconds > 0; submit.disabled = busy || uncertain || !challenge || !enabled;
    purpose.disabled = busy; reset.disabled = busy; code.disabled = busy; password.disabled = busy;
    submit.textContent = erase ? '通过手机永久删除账号和在线数据' : '通过手机设置密码并退出全部登录';
    if (challenge && Date.parse(challenge.value.expiresAt) <= Date.now()) { challenge = null; code.value = ''; submit.disabled = true; status.textContent = '验证码已过期，请重新发送。'; }
  }
  function report(error: unknown, request: number): void { if (!valid(request) || isAbort(error)) return; if (error instanceof AlliraApiError && error.status === 401) context.fail(error); else context.notify(identityError(error)); }
  purpose.addEventListener('change', () => { ++epoch; abort.abort(); abort = new AbortController(); clear(); uncertain = false; update(); });
  async function sendCode(): Promise<void> {
    if (send.disabled || !context.valid() || !navigator.onLine) return;
    const request = ++epoch; abort.abort(); abort = new AbortController(); const desired = purpose.value === 'erase' ? 'erase' : 'set_password';
    clear(); busy = true; nextSend = Date.now() + 60000; if (!timer) timer = setInterval(update, 1000); update();
    try { const value = await client.requestPhoneSecurityChallenge({ purpose: desired }, { signal: abort.signal }); if (valid(request)) { challenge = { value, purpose: desired }; nextSend = Date.now() + value.resendAfterSeconds * 1000; status.textContent = `验证码已发送至 ${value.maskedPhone}，到期：${when(value.expiresAt)}。仅用于当前所选操作。`; } }
    catch (error) { if (valid(request)) uncertain = identityUncertain(error); report(error, request); }
    finally { if (valid(request)) { busy = false; update(); } }
  }
  async function commit(): Promise<void> {
    if (submit.disabled || !challenge || !context.valid() || !navigator.onLine) return;
    const erase = purpose.value === 'erase';
    if (!validPhoneCode(code.value) || challenge.purpose !== (erase ? 'erase' : 'set_password') || Date.parse(challenge.value.expiresAt) <= Date.now()) { context.notify('请重新获取当前用途的有效验证码。'); return; }
    if (erase ? (!eraseConsent.checked || eraseText.value !== 'DELETE MY ACCOUNT') : (!passwordConsent.checked || !validPassword(password.value))) { context.notify(erase ? '请逐字输入 DELETE MY ACCOUNT，并确认不可撤销删除。' : '请填写有效新密码，并确认全部登录将撤销。'); return; }
    const payload = { challengeId: challenge.value.challengeId, code: code.value, newPassword: password.value };
    const request = ++epoch; abort.abort(); abort = new AbortController(); busy = true; update();
    try {
      if (erase) await client.eraseAccountByPhone({ challengeId: payload.challengeId, code: payload.code, confirmation: 'DELETE MY ACCOUNT' }, { signal: abort.signal });
      else await client.setPasswordByPhone(payload, { signal: abort.signal });
      if (valid(request)) { clear(); context.signedOut(erase ? '账号与在线业务数据已删除，全部登录已撤销。已有备份按保留策略处理，不等同物理擦除。' : '站内密码已设置，全部登录已撤销，请重新登录。'); }
    } catch (error) { if (valid(request)) { uncertain = identityUncertain(error); challenge = null; } report(error, request); }
    finally { payload.code = ''; payload.newPassword = ''; if (valid(request)) { code.value = ''; password.value = ''; busy = false; update(); } }
  }
  void client.getAuthOptions({ signal }).then(value => { if (context.valid()) { enabled = value.phone.enabled; status.textContent = enabled ? '发送前请确认操作用途；不会在页面回显验证码。' : '手机验证通道未开通，敏感手机操作当前不可用。'; update(); } }).catch(error => { if (context.valid()) { status.textContent = '无法检查手机验证通道，操作已停用。'; if (error instanceof AlliraApiError && error.status === 401) context.fail(error); } });
  signal.addEventListener('abort', () => { ++epoch; abort.abort(); clear(); if (timer) clearInterval(timer); }, { once: true }); update();
}
