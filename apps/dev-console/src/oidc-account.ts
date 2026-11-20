import { AlliraApiError } from '../../../packages/client/src/index.ts';
import type { OidcPending } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, isAbort, when } from './dom.ts';
import type { FeatureContext } from './feature.ts';
import type { OidcAuthController } from './oidc-auth.ts';
import { oidcAuthorizationUrl, oidcError, oidcUncertain } from './oidc.ts';

/** Deletes only Allira business data after fresh hosted reauthentication and explicit local consent. */
export function mountOidcAccount(context: FeatureContext, flow: OidcAuthController | undefined, browserSession: boolean): void {
  append(context.target, el('h3', {}, '托管身份与账号安全'), empty('身份验证由 Logto 管理。本地密码、短信验证码和恢复码入口已关闭；本页只维护 AlliraAi 业务资料与登录设备，不是 Logto 管理后台。'));
  if (!flow) { append(context.target, empty('托管认证控制器尚未就绪，请重新打开账号页。')); return; }
  let pending: OidcPending | null = flow.pending(); let busy = false; let uncertain = false; let version = 0;
  const warning = empty('此操作只删除 AlliraAi 本地业务账号、在线聊天、角色与用量并撤销业务登录，不删除 Logto 全局身份。既有备份按当前保留策略处理，不承诺物理擦除。');
  const state = empty('');
  const text = el('input', { name: 'oidcEraseConfirmation', type: 'text', autocomplete: 'off', placeholder: 'DELETE MY ACCOUNT' });
  const consent = el('input', { type: 'checkbox' });
  const start = button('前往 Logto 重新认证以删除业务数据', startErase);
  const refresh = button('读取删除重新认证状态', refreshPending);
  const erase = button('永久删除 AlliraAi 业务数据', eraseData);
  const cancel = button('取消删除重新认证', cancelPending);
  const body = el('details', { 'aria-label': '托管身份删除业务数据' }, el('summary', {}, '危险操作：删除 AlliraAi 业务数据'), warning,
    empty(browserSession ? '请先前往 Logto 重新验证身份；返回后仍需逐字确认，不会自动删除。' : '当前仅为本页内存登录。删除要求已记住此设备的独立浏览器 Cookie；请退出并重新登录时主动勾选“记住此设备”。不会偷偷兑换或保存当前令牌。'), start, state, refresh, field('删除业务数据：逐字输入 DELETE MY ACCOUNT', text), field('确认永久删除 AlliraAi 业务数据，理解这不会删除 Logto 全局账号或物理擦除历史备份', consent), erase, cancel);
  append(context.target, body);
  if (pending?.purpose === 'erase') body.open = true;
  const valid = (own: number): boolean => own === version && context.valid() && !context.signal.aborted;
  function update(): void {
    const proved = pending?.purpose === 'erase';
    state.textContent = proved ? `已收到删除用途的重新认证证明，到期：${when(pending!.expiresAt)}。尚未删除任何业务数据。` : pending ? '当前证明不是删除用途，不能用于删除业务数据。请先取消或重新认证。' : '尚无有效的删除重新认证证明。';
    start.disabled = busy || uncertain || !browserSession || !flow!.availability()?.enabled || !!pending;
    refresh.disabled = busy || !browserSession;
    erase.disabled = busy || uncertain || !browserSession || !proved;
    cancel.disabled = busy || !pending || !browserSession;
    text.disabled = busy; consent.disabled = busy;
  }
  async function refreshPending(): Promise<void> {
    if (busy || !browserSession || !navigator.onLine || !context.valid()) return;
    const own = ++version; busy = true; update();
    try { const value = await flow!.client.getSession({ signal: context.signal }); if (valid(own)) { pending = value.pending; uncertain = false; text.value = ''; consent.checked = false; } }
    catch (error) { if (valid(own) && !isAbort(error)) { pending = null; context.notify(oidcError(error)); } }
    finally { if (valid(own)) { busy = false; update(); } }
  }
  async function ordinaryCsrf(own: number): Promise<string | null> {
    // The user explicitly requested this destructive flow. Refresh the ordinary Cookie proof
    // for this request only; never borrow a Bearer or persist ordinary CSRF in the OIDC client.
    try { const session = await context.client.getBrowserSession({ signal: context.signal }); return valid(own) ? session.csrfToken : null; }
    catch (error) { if (valid(own) && !isAbort(error)) context.fail(error); return null; }
  }
  async function startErase(): Promise<void> {
    if (start.disabled || !navigator.onLine || !context.valid()) return;
    const own = ++version; busy = true; update(); let browserCsrfToken = '';
    try {
      const ordinary = await ordinaryCsrf(own); if (!ordinary || !valid(own)) return; browserCsrfToken = ordinary;
      const session = await flow!.client.getSession({ signal: context.signal }); if (!valid(own)) return;
      pending = session.pending; if (pending) return;
      const result = await flow!.client.start({ purpose: 'erase' }, { signal: context.signal, browserCsrfToken }); if (!valid(own)) return;
      const url = oidcAuthorizationUrl(result.authorizationUrl, flow!.availability()?.authorizationOrigin ?? null);
      if (!url) { uncertain = true; context.notify('重新认证地址不在服务端允许的站点，已阻止跳转。'); return; }
      location.assign(url);
    } catch (error) { if (valid(own) && !isAbort(error)) { uncertain = oidcUncertain(error); context.notify(oidcError(error)); } }
    finally { browserCsrfToken = ''; if (valid(own)) { busy = false; update(); } }
  }
  async function eraseData(): Promise<void> {
    if (erase.disabled || !navigator.onLine || !context.valid()) return;
    if (text.value !== 'DELETE MY ACCOUNT' || !consent.checked) { context.notify('请逐字输入 DELETE MY ACCOUNT，并明确勾选本地业务数据删除确认。'); return; }
    const own = ++version; busy = true; update(); let browserCsrfToken = '';
    try {
      const ordinary = await ordinaryCsrf(own); if (!ordinary || !valid(own)) return; browserCsrfToken = ordinary;
      await flow!.client.erase({ confirmation: 'DELETE MY ACCOUNT' }, { signal: context.signal, browserCsrfToken });
      if (valid(own)) { pending = null; text.value = ''; consent.checked = false; flow!.invalidate(); context.signedOut('AlliraAi 业务账号与在线数据已删除，业务登录已撤销。Logto 全局身份未删除，历史备份仍按保留策略处理。'); }
    }
    catch (error) {
      if (!valid(own) || isAbort(error)) return;
      if (error instanceof AlliraApiError && error.status === 409) context.notify('有活动或尚在关闭的任务，业务数据尚未删除。请等待结束后主动重试；重新认证证明仍由服务端校验。');
      else { uncertain = oidcUncertain(error); if (error instanceof AlliraApiError && [400, 401, 403, 410].includes(error.status)) pending = null; context.notify(oidcError(error)); }
    } finally { browserCsrfToken = ''; if (valid(own)) { busy = false; update(); } }
  }
  async function cancelPending(): Promise<void> {
    if (cancel.disabled || !navigator.onLine || !context.valid()) return;
    const own = ++version; busy = true; update();
    try { await flow!.client.cancel({ signal: context.signal }); if (valid(own)) { pending = null; uncertain = false; text.value = ''; consent.checked = false; context.notify('删除重新认证已取消，业务数据未删除。'); } }
    catch (error) { if (valid(own) && !isAbort(error)) { uncertain = oidcUncertain(error); context.notify(oidcError(error)); } }
    finally { if (valid(own)) { busy = false; update(); } }
  }
  context.signal.addEventListener('abort', () => { ++version; pending = null; text.value = ''; consent.checked = false; flow.invalidate(); }, { once: true });
  update();
}
