import { AlliraApiError, AlliraOidcClient } from '../../../packages/client/src/index.ts';
import type { AccountSession, AlliraClient, OidcAvailability, OidcPending, Terms } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, input, isAbort, setNotice, when } from './dom.ts';
import { oidcAuthorizationUrl, oidcError, oidcUncertain } from './oidc.ts';
import { validPassword, validUsername } from './validation.ts';

export interface OidcAuthController {
  client: AlliraOidcClient;
  availability(): OidcAvailability | null;
  pending(): OidcPending | null;
  configure(value: OidcAvailability | null): void;
  restore(): Promise<void>;
  invalidate(): void;
}

/** Logto owns credential/OTP/social UI; this page only confirms the private RP pending identity. */
export function mountOidcAuth(target: HTMLElement, client: AlliraClient, status: HTMLElement,
  signedIn: (session: AccountSession, epoch: number, remember: boolean) => Promise<void>,
  begin: () => number, current: (epoch: number) => boolean, snapshot: () => number,
  erasurePending: () => void): OidcAuthController {
  const oidc = new AlliraOidcClient({ baseUrl: location.origin });
  let availability: OidcAvailability | null = null;
  let pending: OidcPending | null = null;
  let terms: Terms | null = null;
  let version = 0; let busy = false; let uncertain = false; let ready = false; let collision = false;
  let abort = new AbortController();
  const availabilityText = empty('正在检查托管认证配置…');
  const pendingText = empty('');
  const adult = el('input', { type: 'checkbox' });
  const accept = el('input', { type: 'checkbox' });
  const confirm = el('input', { type: 'checkbox' });
  const remember = el('input', { type: 'checkbox', name: 'rememberOidcDevice' });
  const termsText = el('pre', { class: 'message-content' }, '正在读取服务协议…');
  const start = button('使用手机号登录 / 注册', () => startLogin('login'));
  const startPhoneMigration = button('原来仅用手机号登录，保留历史', () => startLogin('phone_migration'));
  const phoneMigrationConsent = el('input', { type: 'checkbox' });
  const phoneMigrationField = field('确认把本次新鲜验证的同一手机号关联到原无密码账号，保留历史并撤销旧登录', phoneMigrationConsent);
  const complete = button('确认身份并进入聊天', () => finish(false));
  const cancel = button('取消本次托管认证', cancelPending);
  const refresh = button('重新读取托管认证状态', restore);
  const username = input('oidcLegacyUsername'); username.maxLength = 32; username.autocomplete = 'username';
  const password = el('input', { name: 'oidcLegacyPassword', type: 'password', autocomplete: 'current-password', maxlength: '256' });
  const migrateConfirm = el('input', { type: 'checkbox' });
  const migrate = button('验证旧账号并明确关联', () => finish(true));
  const migration = el('details', { 'aria-label': '我有旧账号，保留历史' }, el('summary', {}, '我有旧账号，保留历史'), empty('以前使用过用户名账号并希望保留聊天或能量，请在继续前主动证明旧账号。即使旧账号还未绑定手机号，也可以在此明确关联；不按昵称或手机号静默合并。成功后由服务端保留原内部账号 ID 和业务数据。'), field('关联旧账号用户名', username), field('关联旧账号当前密码', password), field('确认把本次已验证身份关联到我证明的旧账号，保留原账号数据', migrateConfirm), migrate);
  const confirmations = el('section', { 'aria-label': '托管认证身份确认' }, field('托管登录：我已满 18 岁', adult), field('托管登录：接受当前服务协议', accept), el('details', {}, el('summary', {}, '托管登录服务协议'), termsText), field('确认使用上方已验证手机号身份进入 AlliraAi，不静默合并其他账号', confirm), field('托管登录：记住此设备（共享设备请勿勾选）', remember), phoneMigrationField, empty('若要保留未关联的旧用户名账号，请先展开下方“我有旧账号，保留历史”。仅用过手机号且未设置密码的旧账号，须先取消并选择“原来仅用手机号登录，保留历史”，完成新的手机认证；普通登录不能直接升级成迁移。直接确认不会自动找回旧账号或搬入旧聊天。'), complete);
  append(target, el('h2', {}, '登录后开始聊天'), availabilityText, start, startPhoneMigration, pendingText, confirmations, migration, cancel, refresh, empty('手机验证码、密码与第三方授权由 Logto 托管认证处理。本页不收取验证码；认证返回后仍需明确确认，过渡身份不是普通登录。'));
  target.hidden = true;

  const valid = (request: { own: number; auth: number }): boolean => request.own === version && current(request.auth);
  function request(): { own: number; auth: number; signal: AbortSignal } {
    const auth = begin(); ++version; abort.abort(); abort = new AbortController(); busy = true; update();
    return { own: version, auth, signal: abort.signal };
  }
  function clearInputs(): void { adult.checked = false; accept.checked = false; confirm.checked = false; remember.checked = false; phoneMigrationConsent.checked = false; migrateConfirm.checked = false; username.value = ''; password.value = ''; migration.open = false; }
  function update(): void {
    const login = !!pending && pending.purpose !== 'erase';
    const phoneMigration = pending?.purpose === 'phone_migration';
    start.hidden = !!pending; start.disabled = busy || uncertain || !availability?.enabled || !availability.authorizationOrigin;
    startPhoneMigration.hidden = !!pending; startPhoneMigration.disabled = start.disabled;
    confirmations.hidden = !login; complete.hidden = collision;
    phoneMigrationField.hidden = !phoneMigration;
    complete.disabled = busy || uncertain || !ready || !terms || (!phoneMigration && migration.open);
    migration.hidden = !login || phoneMigration; if (collision && !phoneMigration) migration.open = true; migrate.disabled = busy || uncertain || !ready || !terms || !!phoneMigration;
    cancel.hidden = !pending; cancel.disabled = busy || !ready; refresh.disabled = busy;
    for (const item of [adult, accept, confirm, remember, phoneMigrationConsent, migrateConfirm, username, password]) item.disabled = busy;
    pendingText.hidden = !pending;
    pendingText.textContent = pending ? `已验证的手机号：${pending.maskedPhone}。${pending.purpose === 'erase' ? '这是删除本地业务数据的重新认证，尚未执行删除。' : phoneMigration ? '这是保留原无密码手机账号历史的专用新鲜认证。尚未迁移或登录，请独立确认；只能关联同一手机号的原无密码账号。' : '尚未登录，请核对身份并明确确认。'}到期：${when(pending.expiresAt)}` : '';
  }
  async function restore(): Promise<void> {
    if (!availability?.enabled || busy || !navigator.onLine) return;
    const own = ++version; const auth = snapshot(); abort.abort(); abort = new AbortController(); busy = true; update();
    try {
      const value = await oidc.getSession({ signal: abort.signal });
      if (!valid({ own, auth })) return;
      pending = value.pending; ready = true; uncertain = false; collision = false; clearInputs(); update();
      if (pending?.purpose === 'erase') erasurePending();
    } catch (error) { if (valid({ own, auth }) && !isAbort(error)) { ready = false; pending = null; setNotice(status, oidcError(error), true); } }
    finally { if (valid({ own, auth })) { busy = false; update(); } }
  }
  async function startLogin(purpose: 'login' | 'phone_migration'): Promise<void> {
    if (start.disabled || !navigator.onLine) return;
    const captured = request();
    try {
      // Every start begins with an explicit GET. No SDK or UI retry can duplicate a POST.
      const session = await oidc.getSession({ signal: captured.signal }); if (!valid(captured)) return;
      pending = session.pending; ready = true;
      if (pending) { if (pending.purpose === 'erase') erasurePending(); return; }
      const result = await oidc.start({ purpose }, { signal: captured.signal }); if (!valid(captured)) return;
      const url = oidcAuthorizationUrl(result.authorizationUrl, availability?.authorizationOrigin ?? null);
      if (!url) { uncertain = true; setNotice(status, '托管认证地址不在服务端允许的认证站点，已阻止跳转。', true); return; }
      location.assign(url);
    } catch (error) { if (valid(captured) && !isAbort(error)) { uncertain = oidcUncertain(error); setNotice(status, oidcError(error), true); } }
    finally { if (valid(captured)) { busy = false; update(); } }
  }
  migration.addEventListener('toggle', update);
  async function finish(migrateOld: boolean): Promise<void> {
    const phoneMigration = pending?.purpose === 'phone_migration';
    if (!pending || pending.purpose === 'erase' || busy || uncertain || !ready || !terms || !navigator.onLine || (migrateOld && phoneMigration) || (!migrateOld && (collision || (!phoneMigration && migration.open)))) return;
    if (!adult.checked || !accept.checked || !confirm.checked) { setNotice(status, '请核对手机号，确认成年、当前协议和使用此身份。', true); return; }
    if (phoneMigration && !phoneMigrationConsent.checked) { setNotice(status, '请独立确认保留同一手机号原无密码账号的历史，并撤销旧登录。', true); return; }
    if (migrateOld && (!migrateConfirm.checked || !validUsername(username.value) || !validPassword(password.value))) { setNotice(status, '请填写旧账号用户名和当前密码，并明确确认关联。', true); return; }
    const remembered = remember.checked;
    const proof = { username: username.value, currentPassword: password.value };
    const confirmation = { acceptedTermsVersion: terms.version, isAdult: true as const, confirmIdentity: true as const, ...(phoneMigration ? { confirmPhoneMigration: true } : {}) };
    const captured = request();
    try {
      const session = migrateOld ? await oidc.migrate({ ...confirmation, ...proof }, { signal: captured.signal }) : await oidc.complete(confirmation, { signal: captured.signal });
      try { if (valid(captured)) { pending = null; ready = false; clearInputs(); await signedIn(session, captured.auth, remembered); } }
      finally { session.accessToken = ''; }
    } catch (error) {
      if (!valid(captured) || isAbort(error)) return;
      if (!migrateOld && !phoneMigration && error instanceof AlliraApiError && error.status === 409 && error.code === 'identity_link_required') { collision = true; setNotice(status, '请在下方证明旧账号并明确关联；不会静默合并。', true); }
      else { uncertain = oidcUncertain(error); if (error instanceof AlliraApiError && [400, 401, 403, 410].includes(error.status)) { ready = false; pending = null; } setNotice(status, oidcError(error), true); }
    } finally { proof.currentPassword = ''; password.value = ''; if (valid(captured)) { busy = false; update(); } }
  }
  async function cancelPending(): Promise<void> {
    if (busy || !ready || !navigator.onLine) return;
    const captured = request();
    try { await oidc.cancel({ signal: captured.signal }); if (valid(captured)) { pending = null; ready = false; uncertain = false; collision = false; clearInputs(); setNotice(status, '本次托管认证已取消。'); } }
    catch (error) { if (valid(captured) && !isAbort(error)) { uncertain = oidcUncertain(error); setNotice(status, oidcError(error), true); } }
    finally { if (valid(captured)) { busy = false; update(); } }
  }
  const controller: OidcAuthController = {
    client: oidc, availability: () => availability, pending: () => pending,
    configure(value) {
      availability = value; target.hidden = !value;
      availabilityText.textContent = value?.enabled ? `手机号与第三方登录由 Logto 托管。${value.simulated ? '当前为受保护维护验收配置，不代表真实短信或平台已经开通。' : '实际可用通道以托管页面和服务配置为准，未配置的短信或平台不可用。'}` : '托管认证当前不可用；未配置的短信或第三方通道不会伪装可用。';
      if (!value?.enabled) controller.invalidate(); update();
    },
    restore,
    invalidate() { ++version; abort.abort(); abort = new AbortController(); busy = false; uncertain = false; ready = false; pending = null; collision = false; clearInputs(); oidc.clear(); update(); },
  };
  void client.getTerms().then(value => { terms = value; termsText.textContent = `版本：${value.version}\n${value.content}`; update(); }).catch(() => { termsText.textContent = '当前协议暂时无法读取，确认登录与关联已停用。'; });
  update(); return controller;
}
