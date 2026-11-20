import { append, button, el, empty, field, input, when } from './dom.ts';
import { details, featureTask, heading, table } from './feature.ts';
import type { FeatureContext } from './feature.ts';

export function mountEnergy(context: FeatureContext): void {
  const { client, target, signal } = context;
  heading(target, '能量与用量', '能量为整数点数，不是货币余额。用量按字符观测，并非真实 Token、上游报价或生产成本。兑换码仅提交同源后端，不在本页保存。');
  const wallet = el('section', { 'aria-label': '能量余额' });
  const code = input('redeemCode'); code.autocomplete = 'off'; code.maxLength = 120;
  const redeem = button('兑换能量', async () => {
    const value = code.value.trim();
    if (value.length < 16 || value.length > 120) { context.notify('兑换码应为 16–120 字符。'); return; }
    await task(() => client.redeemEnergy({ code: value }, { signal }), result => { code.value = ''; renderWallet(result); context.notify('能量兑换成功。'); void loadLedger(false); });
  });
  const refresh = button('刷新能量', async () => task(() => client.getEnergy({ signal }), renderWallet));
  const task = featureTask(context, [redeem, refresh]);
  function renderWallet(value: { balance: number; economyCost: number; fastCost: number }): void {
    wallet.replaceChildren(details([['当前能量', value.balance], ['经济模式每轮', value.economyCost], ['极速模式每轮', value.fastCost]]));
  }
  const ledger = table(['时间', '变化', '变化后余额', '类型', '关联编号'], '能量账本');
  const ledgerStatus = empty('正在读取能量账本…');
  let cursor: string | null = null;
  let ledgerBusy = false;
  const more = button('加载更多账本', () => loadLedger(true));
  const refreshLedger = button('刷新账本', () => loadLedger(false));
  async function loadLedger(appendRows: boolean): Promise<void> {
    if (ledgerBusy || !context.valid() || (appendRows && !cursor)) return;
    ledgerBusy = true; more.disabled = true; refreshLedger.disabled = true;
    try {
      const page = await client.listEnergyLedger({ limit: 30, ...(appendRows && cursor ? { cursor } : {}), signal });
      if (!context.valid()) return;
      if (!appendRows) ledger.body.replaceChildren();
      for (const item of page.items) append(ledger.body, el('tr', {}, ...[when(item.createdAt), String(item.delta), String(item.balanceAfter), item.kind, item.referenceId].map(value => el('td', {}, value))));
      cursor = page.nextCursor; more.hidden = !cursor; ledgerStatus.textContent = ledger.body.rows.length ? `已显示 ${ledger.body.rows.length} 条账本记录。` : '暂无能量流水。';
    } catch (error) { if (context.valid()) context.fail(error); }
    finally { if (context.valid()) { ledgerBusy = false; more.disabled = false; refreshLedger.disabled = false; } }
  }
  const runId = input('usageRunId', '在生成任务中可复制任务编号');
  const usage = el('section', { 'aria-label': '生成任务用量' });
  const showUsage = button('查看任务用量', async () => {
    if (!runId.value.trim()) { context.notify('请输入当前用户拥有的生成任务编号。'); return; }
    await usageTask(() => client.getRunUsage(runId.value.trim(), { signal }), value => usage.replaceChildren(details([
      ['请求模式', value.requestedMode === 'fast' ? '极速' : '经济'], ['实际模式', value.actualMode === 'fast' ? '极速' : '经济'], ['能量点数', value.cost], ['结算状态', ({ reserved: '已预留', settled: '已结算', refunded: '已退回' })[value.state]], ['输入字符', value.promptCharacters], ['输出字符', value.outputCharacters], ['首字毫秒', value.firstTokenMs ?? '未观测'], ['总耗时毫秒', value.durationMs ?? '未观测'],
    ])));
  });
  const usageTask = featureTask(context, [showUsage]);
  append(target, wallet, el('div', { class: 'actions' }, refresh), field('能量兑换码', code), redeem, el('h3', {}, '能量账本'), ledger.element, ledgerStatus, el('div', { class: 'actions' }, refreshLedger, more), el('h3', {}, '单次生成用量'), field('生成任务编号', runId), showUsage, usage);
  signal.addEventListener('abort', () => { code.value = ''; }, { once: true });
  void task(() => client.getEnergy({ signal }), renderWallet);
  void loadLedger(false);
}
