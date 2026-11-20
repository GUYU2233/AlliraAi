import { AlliraApiError } from '../../../packages/client/src/index.ts';
import { validCdkIssue } from './validation.ts';
import type { AlliraAdminClient } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, when } from './dom.ts';
import { details, table } from './feature.ts';

export interface AdminFeatureContext {
  client: AlliraAdminClient;
  target: HTMLElement;
  signal: AbortSignal;
  valid(): boolean;
  fail(error: unknown): void;
  notify(text: string): void;
}
export function mountAdminEconomy(context: AdminFeatureContext, view: 'cdks' | 'usage'): void {
  const { client, target, signal } = context;
  if (view === 'usage') {
    const result = el('section');
    let busy = false;
    const refresh = button('刷新能量用量统计', async () => {
      if (busy || !context.valid()) return;
      busy = true; refresh.disabled = true;
      try {
        const value = await client.getUsageOverview({ signal });
        if (!context.valid()) return;
        result.replaceChildren(details([['任务数', value.runs], ['预留能量', value.reservedEnergy], ['已结算能量', value.settledEnergy], ['已退回能量', value.refundedEnergy], ['输入字符', value.promptCharacters], ['输出字符', value.outputCharacters], ['平均首字毫秒', value.firstTokenMsAverage ?? '未观测']]));
      } catch (error) { if (context.valid()) context.fail(error); }
      finally { if (context.valid()) { busy = false; refresh.disabled = false; } }
    });
    append(target, el('h2', {}, '能量用量统计'), empty('统计单位是字符与整数能量，不是 Token、真实供应商报价、利润或容量验证。'), refresh, result);
    refresh.click(); return;
  }
  const energy = el('input', { type: 'number', name: 'cdkEnergy', min: '1', max: '1000000', step: '1', value: '100' });
  const count = el('input', { type: 'number', name: 'cdkCount', min: '1', max: '50', step: '1', value: '1' });
  const days = el('input', { type: 'number', name: 'cdkDays', min: '1', max: '365', step: '1', value: '30' });
  const confirm = el('input', { type: 'checkbox', name: 'confirmIssueCdks' });
  const issued = el('section', { 'aria-label': '本次签发的完整兑换码' });
  let issuing = false;
  let issuedUncertain = false;
  const issue = button('签发兑换码', async () => {
    if (!confirm.checked || issuing || issuedUncertain || !context.valid()) return;
    const values = { energy: Number(energy.value), count: Number(count.value), expiresInDays: Number(days.value) };
    if (!validCdkIssue(values.energy, values.count, values.expiresInDays)) { context.notify('请填写有效整数：能量 1–100 万、数量 1–50、有效天数 1–365。'); return; }
    issuing = true; issue.disabled = true; issued.replaceChildren();
    try {
      const result = await client.issueCdks(values, { signal });
      if (!context.valid()) return;
      confirm.checked = false;
      append(issued, el('h3', {}, '本次完整兑换码（只显示这一次）'), empty('请立即安全保存。列表不会再次返回完整码；关闭此内容或切换页面即释放。'));
      for (const item of result.items) append(issued, el('pre', { class: 'message-content' }, item.code), empty(`能量 ${item.energy}；到期 ${when(new Date(item.expiresAt * 1000).toISOString())}`));
      append(issued, button('清除完整兑换码', () => issued.replaceChildren()));
      context.notify('已签发兑换码。仅此显式签发结果显示完整码。'); void load(false);
    } catch (error) {
      if (context.valid()) {
        const definite = error instanceof AlliraApiError && error.status >= 400 && error.status < 500;
        issuedUncertain = !definite;
        context.fail(error);
        if (!definite) context.notify('签发结果不确定，为避免重复签发已停用本页签发。请核对审计和兑换码列表后重新进入此页。');
      }
    } finally { if (context.valid()) { issuing = false; issue.disabled = issuedUncertain || !confirm.checked; } }
  });
  issue.disabled = true;
  confirm.addEventListener('change', () => { issue.disabled = issuing || issuedUncertain || !confirm.checked; });
  const listing = table(['前缀', '能量', '到期时间', '状态', '兑换用户', '操作'], '兑换码摘要（不含完整码）');
  const state = empty('正在读取…');
  let cursor: string | null = null;
  let loading = false;
  const more = button('加载更多兑换码', () => load(true));
  const refresh = button('刷新兑换码列表', () => load(false));
  async function load(appendRows: boolean): Promise<void> {
    if (!context.valid() || loading || (appendRows && !cursor)) return;
    loading = true; more.disabled = true; refresh.disabled = true;
    try {
      const page = await client.listCdks({ limit: 30, ...(appendRows && cursor ? { cursor } : {}), signal });
      if (!context.valid()) return;
      if (!appendRows) listing.body.replaceChildren();
      for (const item of page.items) {
        const checked = el('input', { type: 'checkbox' });
        const itemState = el('span', {}, item.disabled ? '已停用' : item.redeemedBy ? '已兑换' : '待兑换');
        let disabling = false;
        const disable = button('停用兑换码', async () => {
          if (!checked.checked || disabling || !context.valid()) return;
          disabling = true; disable.disabled = true;
          try { await client.disableCdk(item.prefix, { signal }); if (context.valid()) { checked.checked = false; itemState.textContent = '已停用'; context.notify('兑换码已停用。'); } }
          catch (error) { if (context.valid()) { disabling = false; context.fail(error); disable.disabled = !checked.checked; } }
        });
        disable.disabled = true; checked.addEventListener('change', () => { disable.disabled = disabling || !checked.checked; });
        const actions = item.disabled || item.redeemedBy ? empty('无需操作') : el('div', {}, field('确认停用此前缀兑换码', checked), disable);
        append(listing.body, el('tr', {}, el('td', {}, item.prefix), el('td', {}, String(item.energy)), el('td', {}, when(new Date(item.expiresAt * 1000).toISOString())), el('td', {}, itemState), el('td', {}, item.redeemedBy ?? '—'), el('td', {}, actions)));
      }
      cursor = page.nextCursor; more.hidden = !cursor; state.textContent = `已显示 ${listing.body.rows.length} 个兑换码摘要。`;
    } catch (error) { if (context.valid()) context.fail(error); }
    finally { if (context.valid()) { loading = false; more.disabled = false; refresh.disabled = false; } }
  }
  append(target, el('h2', {}, '兑换码管理'), empty('签发是管理修改，须明确确认；完整兑换码仅本次签发可见，禁止将其写入日志或浏览器存储。'), field('每个码的整数能量', energy), field('签发数量', count), field('有效天数', days), field('确认签发以上数量和能量的兑换码', confirm), issue, issued, el('h3', {}, '兑换码摘要'), listing.element, state, el('div', { class: 'actions' }, refresh, more));
  signal.addEventListener('abort', () => issued.replaceChildren(), { once: true });
  void load(false);
}
