import { AlliraApiError } from '../../../packages/client/src/index.ts';
import type { AuthBackend } from '../../../packages/client/src/index.ts';
import type { OidcAuthController } from './oidc-auth.ts';
import { mountOidcAccount } from './oidc-account.ts';
import { append, button, el, empty, field, input, isAbort, when } from './dom.ts';
import { details, featureTask, heading } from './feature.ts';
import type { FeatureContext } from './feature.ts';
import { validDisplayName, validPassword } from './validation.ts';
import { mountAccountSecurity } from './account-security.ts';
import { mountPhoneAccount } from './phone-account.ts';
import { mountPhoneSecurity } from './phone-security.ts';

export function mountAccount(context: FeatureContext, backend: AuthBackend | null = null, oidc?: OidcAuthController, browserSession = false): void {
  const { client, target, signal } = context;
  heading(target, '账号维护', backend === 'oidc' ? '登录凭据由 Logto 托管。退出业务登录不等于退出 Logto；本地资料和业务数据保持独立。' : '改密与“退出全部登录”会撤销此账号的全部会话，之后需重新登录。');
  if (!context.isAccount) { append(target, empty('当前是开发身份。请退出后使用正式账号注册或登录，才能维护账号资料。')); return; }
  const profile = el('section');
  const displayName = input('displayName'); displayName.maxLength = 160; // Server counts Unicode characters, not UTF-16 code units.
  const save = button('保存显示名称', async () => { if (!validDisplayName(displayName.value)) { context.notify('显示名称需为 1–80 字符、最多 240 UTF-8 字节，且不含控制字符。'); return; } await task(() => client.updateAccount({ displayName: displayName.value }, { signal }), user => { context.profileChanged(user.displayName); context.notify('显示名称已更新。'); }); });
  const currentPassword = el('input', { name: 'currentPassword', type: 'password', autocomplete: 'current-password', maxlength: '256' });
  const newPassword = el('input', { name: 'newPassword', type: 'password', autocomplete: 'new-password', minlength: '10', maxlength: '256' });
  const passwordConfirm = el('input', { type: 'checkbox' });
  const change = button('修改密码并退出全部登录', async () => {
    if (!passwordConfirm.checked) { context.notify('请先确认改密会撤销全部登录。'); return; }
    if (!validPassword(currentPassword.value) || !validPassword(newPassword.value)) { context.notify('当前与新密码需为 10–128 字符且最多 256 UTF-8 字节。'); return; }
    await task(() => client.changePassword({ currentPassword: currentPassword.value, newPassword: newPassword.value }, { signal }), () => { currentPassword.value = ''; newPassword.value = ''; context.signedOut('密码已修改，全部登录已撤销，请使用新密码登录。'); });
  });
  const logoutConfirm = el('input', { type: 'checkbox' });
  const logoutAll = button('退出全部账号登录', async () => {
    if (!logoutConfirm.checked) { context.notify('请先勾选退出全部登录确认。'); return; }
    await task(() => client.logoutAll({ signal }), () => context.signedOut('此账号全部登录已撤销。'));
  });
  const task = featureTask(context, [save, change, logoutAll]);
  const passwordSection = el('section', {}, el('h3', {}, '修改密码'), field('当前密码', currentPassword), field('新密码', newPassword), field('确认改密会撤销全部登录', passwordConfirm), change);
  passwordSection.hidden = true;
  append(target, profile, field('显示名称', displayName), save, passwordSection, el('h3', {}, '全部退出'), field('确认退出此账号全部会话', logoutConfirm), logoutAll);
  const identityTarget = el('section'); append(target, identityTarget);
  function mountLegacySecurity(): void {
    void client.getAccountIdentity({ signal }).then(identity => {
      if (!context.valid()) return;
      passwordSection.hidden = !identity.passwordEnabled;
      const scoped = { ...context, target: identityTarget };
      mountPhoneAccount(scoped, identity);
      mountAccountSecurity(context, identity.passwordEnabled);
      if (!identity.passwordEnabled) mountPhoneSecurity(context);
    }).catch(error => {
      if (!context.valid() || isAbort(error)) return;
      if (error instanceof AlliraApiError && error.status === 404) { passwordSection.hidden = false; mountAccountSecurity(context); }
      else { append(identityTarget, empty('身份资料暂时无法读取。密码与删除操作已保守停用，请重新打开账号页。')); if (error instanceof AlliraApiError && error.status === 401) context.fail(error); }
    });
  }
  function mountSecurity(selected: AuthBackend): void {
    if (!context.valid()) return;
    if (selected === 'oidc') { mountAccountSecurity(context, false, true); mountOidcAccount(context, oidc, browserSession); }
    else mountLegacySecurity();
  }
  if (backend) mountSecurity(backend);
  else void client.getAuthOptions({ signal }).then(options => mountSecurity(options.backend)).catch(error => {
    if (!context.valid() || isAbort(error)) return;
    if (error instanceof AlliraApiError && error.status === 404) mountLegacySecurity();
    else { append(identityTarget, empty('认证配置暂时无法读取；本地密码、恢复和删除入口暂时停用。')); if (error instanceof AlliraApiError && error.status === 401) context.fail(error); }
  });
  signal.addEventListener('abort', () => { currentPassword.value = ''; newPassword.value = ''; }, { once: true });
  void task(() => client.getAccount({ signal }), user => {
    displayName.value = user.displayName;
    profile.replaceChildren(details([['用户名', user.username], ['显示名称', user.displayName], ['注册时间', when(user.createdAt)], ['已接受协议版本', user.termsVersion], ['年龄声明', '已确认成年']]));
  });
}
