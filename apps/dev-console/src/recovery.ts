import type { AlliraClient } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, friendlyError, input, setNotice } from './dom.ts';
import { validPassword, validUsername } from './validation.ts';

export function mountAccountRecovery(target: HTMLElement, client: AlliraClient, status: HTMLElement, begin: () => number, current: (epoch: number) => boolean): { invalidate(): void } {
  let version = 0;
  let busy = false;
  let abort = new AbortController();
  const username = input('recoveryUsername'); username.maxLength = 32;
  const code = el('input', { name: 'recoveryCode', type: 'password', autocomplete: 'off', maxlength: '128' });
  const password = el('input', { name: 'recoveryNewPassword', type: 'password', autocomplete: 'new-password', maxlength: '256' });
  const confirm = el('input', { type: 'checkbox' });
  const submit = button('使用恢复码重设密码', async () => {
    if (busy) return;
    if (!navigator.onLine) { setNotice(status, '当前离线，不能恢复账号。', true); return; }
    if (!validUsername(username.value) || !validPassword(password.value) || !/^[\x21-\x7e]{1,128}$/.test(code.value) || !confirm.checked) { setNotice(status, '请填写有效用户名、恢复码、新密码，并确认撤销全部登录。', true); return; }
    const request = begin(); const own = version;
    abort.abort(); abort = new AbortController();
    const payload = { username: username.value, recoveryCode: code.value, newPassword: password.value };
    busy = true; submit.disabled = true;
    try {
      await client.recoverAccount(payload, { signal: abort.signal });
      if (own === version && current(request)) { username.value = ''; confirm.checked = false; setNotice(status, '密码已重设，全部旧恢复码与登录已撤销。不会自动登录，请使用新密码登录。'); }
    } catch (error) { if (own === version && current(request)) setNotice(status, friendlyError(error), true); }
    finally { payload.recoveryCode = ''; payload.newPassword = ''; if (own === version) { code.value = ''; password.value = ''; busy = false; submit.disabled = false; } }
  });
  append(target, el('details', {}, el('summary', {}, '忘记密码：使用恢复码'), empty('恢复码只能在已登录账号中主动签发并离线保存。成功恢复会撤销全部旧码与登录，不会自动登录。'), field('恢复账号用户名', username), field('单次恢复码', code), field('恢复后的新密码', password), field('确认重设密码并撤销全部旧码和登录', confirm), submit));
  return { invalidate() { ++version; abort.abort(); busy = false; username.value = ''; code.value = ''; password.value = ''; confirm.checked = false; submit.disabled = false; } };
}
