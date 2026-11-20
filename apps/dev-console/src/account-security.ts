import { AlliraApiError } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, when } from './dom.ts';
import type { FeatureContext } from './feature.ts';
import { featureTask, table } from './feature.ts';
import { validPassword } from './validation.ts';
import { downloadText } from './download.ts';

/** Codes live in this mount only and are never requested until password-confirmed rotation. */
export function mountAccountSecurity(context: FeatureContext, passwordEnabled = true, hostedIdentity = false): void {
  const { target, client, signal } = context;
  const recoveryStatus = empty('恢复码状态尚未读取。');
  const password = el('input', { name: 'rotateRecoveryPassword', type: 'password', autocomplete: 'current-password', maxlength: '256' });
  const consent = el('input', { type: 'checkbox' });
  const codes = el('div', { 'aria-label': '本次恢复码' });
  let secrets: string[] = [];
  let rotationUncertain = false;
  const clearCodes = () => { secrets.fill(''); secrets = []; codes.replaceChildren(); };
  const rotate = button('签发并替换恢复码', async () => {
    if (!consent.checked || !validPassword(password.value) || rotationUncertain) { context.notify('请填写当前密码并确认；结果不确定时请先刷新状态，不要自动重复签发。'); return; }
    const payload = { currentPassword: password.value };
    clearCodes();
    try {
      await task(async () => {
        try { const result = await client.rotateRecoveryCodes(payload, { signal }); if (!context.valid()) result.codes.fill(''); return result; }
        catch (error) { if (context.valid() && !(error instanceof AlliraApiError && error.status < 500)) rotationUncertain = true; throw error; }
      }, result => {
        secrets = [...result.codes]; result.codes.fill(''); password.value = ''; consent.checked = false;
        append(codes, empty('仅本次显示。请自行离线妥善保存；每个码仅用一次。离开页面会清除显示，服务器仅保留摘要。'), el('pre', { class: 'message-content' }, secrets.join('\n')), button('下载本次恢复码', () => {
          if (context.valid() && secrets.length) downloadText(secrets.join('\n') + '\n', 'allira-recovery-codes.txt');
        }), button('隐藏并清除本次恢复码', clearCodes));
        recoveryStatus.textContent = `剩余恢复码：${secrets.length}；签发时间：${when(result.createdAt)}`;
        context.notify('已签发新的恢复码，全部旧码失效。');
      });
    } finally { payload.currentPassword = ''; password.value = ''; }
  });
  const refreshRecovery = button('读取恢复码状态', () => task(() => client.getRecoveryStatus({ signal }), value => {
    recoveryStatus.textContent = value.enabled ? `剩余恢复码：${value.remainingCodes}；更新时间：${value.updatedAt ? when(value.updatedAt) : '未知'}` : '未启用可用恢复码。';
    if (rotationUncertain) context.notify('上次签发结果不确定，状态只能显示数量；若未保存新码，请重新打开账号页并主动更换，旧码将失效。');
  }));
  const devices = table(['设备说明', '类型', '创建', '最近使用', '到期', '当前设备', '操作'], '账号登录设备');
  const refreshDevices = button('读取登录设备', () => task(() => client.listAccountSessions({ signal }), value => {
    devices.body.replaceChildren();
    for (const item of value.items) {
      const checked = el('input', { type: 'checkbox' });
      const revoke = button(item.current ? '撤销当前设备登录' : '撤销此设备登录', async () => {
        if (!checked.checked) { context.notify('请先勾选撤销设备确认。'); return; }
        await task(() => client.revokeAccountSession(item.id, { signal }), () => {
          if (item.current) context.signedOut('当前设备登录已撤销；此页内存状态已清除。');
          else { checked.checked = false; revoke.disabled = true; context.notify('此设备登录已撤销，可刷新列表。'); }
        });
      });
      const action = el('div', {}, field('确认撤销此登录设备', checked), revoke);
      append(devices.body, el('tr', {}, ...[item.label, item.type === 'browser' ? '浏览器 Cookie' : '内存 Bearer', when(item.createdAt), when(item.lastSeenAt), when(item.expiresAt), item.current ? '是' : '否'].map(value => el('td', {}, value)), el('td', {}, action)));
    }
    if (!value.items.length) append(devices.body, el('tr', {}, el('td', { colspan: '7' }, '暂无有效登录设备。')));
  }));
  const exportConfirm = el('input', { type: 'checkbox' });
  const exportData = button('下载账号完整数据', async () => {
    if (!exportConfirm.checked) { context.notify('导出包含全部敏感聊天、角色与用量，请先确认下载风险。'); return; }
    await task(() => client.exportAccountData({ signal }), value => {
      downloadText(JSON.stringify(value, null, 2), 'allira-account-export.json', 'application/json;charset=utf-8');
      exportConfirm.checked = false; context.notify('账号数据已下载到本机。导出不包含账号凭据表；你自行粘贴到聊天或导入原文的秘密不会自动脱敏，请妥善保管。');
    });
  });
  const erasePassword = el('input', { name: 'eraseAccountPassword', type: 'password', autocomplete: 'current-password', maxlength: '256' });
  const eraseText = el('input', { name: 'eraseAccountConfirmation', type: 'text', autocomplete: 'off', placeholder: 'DELETE MY ACCOUNT' });
  const eraseConfirm = el('input', { type: 'checkbox' });
  const erase = button('永久删除账号和在线数据', async () => {
    if (!eraseConfirm.checked || eraseText.value !== 'DELETE MY ACCOUNT' || !validPassword(erasePassword.value)) { context.notify('请填写当前密码，逐字输入 DELETE MY ACCOUNT，并勾选不可撤销确认。'); return; }
    const payload = { currentPassword: erasePassword.value, confirmation: 'DELETE MY ACCOUNT' as const };
    try { await task(() => client.eraseAccount(payload, { signal }), () => { erasePassword.value = ''; eraseText.value = ''; context.signedOut('账号与在线业务数据已删除，全部登录已撤销。已有备份仍按服务保留策略处理；此操作不等同所有介质物理擦除。'); }); }
    finally { payload.currentPassword = ''; erasePassword.value = ''; }
  });
  const task = featureTask(context, [rotate, refreshRecovery, refreshDevices, exportData, erase]);
  const recoverySection = el('section', {}, el('h3', {}, '恢复码'), empty('恢复码等同重设密码权限。仅主动签发时显示一次，不会自动读取旧码。'), recoveryStatus, refreshRecovery, field('签发恢复码的当前密码', password), field('确认替换全部旧恢复码，并自行离线保存新码', consent), rotate, codes);
  const eraseSection = el('details', {}, el('summary', {}, '危险操作：删除账号'), empty('不可撤销地删除在线业务数据，撤销全部登录和恢复码。有活动或正在关闭的生成时拒绝（409）。匿名核销墓碑和既有备份按当前保留策略留存，不承诺物理擦除。'), field('删除账号的当前密码', erasePassword), field('逐字输入 DELETE MY ACCOUNT', eraseText), field('确认永久删除账号和在线数据且无法撤销', eraseConfirm), erase);
  recoverySection.hidden = !passwordEnabled; eraseSection.hidden = !passwordEnabled;
  append(target, recoverySection, passwordEnabled || hostedIdentity ? null : empty('设置站内密码后可签发离线恢复码；当前可使用手机验证敏感操作。'),
    el('h3', {}, '登录设备'), empty('撤销设备仅使当前登录失效，不阻止以后通过已开通的登录方式重新登录。'), refreshDevices, devices.element,
    el('h3', {}, '数据导出'), empty('只在显式点击时请求完整敏感数据。活动生成会拒绝导出（409）；超出安全上限会拒绝（413），不会默默截断。'), field('确认下载含敏感聊天内容的账号数据', exportConfirm), exportData, eraseSection);
  signal.addEventListener('abort', () => { clearCodes(); password.value = ''; erasePassword.value = ''; eraseText.value = ''; }, { once: true });
}
