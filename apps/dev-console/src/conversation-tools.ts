import { AlliraApiError } from '../../../packages/client/src/index.ts';
import type { Conversation, ConversationSearchResult, ForkConversationInput } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, input, when } from './dom.ts';
import type { FeatureContext } from './feature.ts';
import { featureTask, heading } from './feature.ts';
import { downloadText } from './download.ts';
import { copyText } from './markdown.ts';

export interface ForkMemoryState { pending: { sourceId: string; input: ForkConversationInput; uncertain: boolean } | null }

export function mountConversationTools(context: FeatureContext, state: ForkMemoryState): void {
  const { client, target, signal } = context;
  heading(target, '会话检索、导出与分叉', '检索仅在本人数据内。导出包含敏感聊天，分叉只复制已完成的对话对与允许的角色/记忆关联，不复制费用和用量。');
  const query = input('conversationSearch', '标题或消息关键词；留空列出最近会话'); query.maxLength = 200;
  const results = el('div', { 'aria-label': '会话检索结果' });
  const searchStatus = empty('尚未检索。');
  let cursor: string | null = null;
  let searched = '';
  let request = 0;
  let searchAbort = new AbortController();
  let searchBusy = false;
  let items: ConversationSearchResult[] = [];
  const search = button('检索我的会话', () => load(false));
  const more = button('加载更多检索结果', () => load(true)); more.hidden = true;
  const selected = el('section', { 'aria-label': '选中会话工具' });
  const form = el('form', { 'aria-label': '检索会话' }, field('会话检索关键词', query), search);
  form.addEventListener('submit', event => { event.preventDefault(); void load(false); });
  append(target, form, searchStatus, results, more, selected);
  let selectedEpoch = 0;
  let selectionAbort = new AbortController();
  signal.addEventListener('abort', () => { searchAbort.abort(); selectionAbort.abort(); selected.replaceChildren(); }, { once: true });
  query.addEventListener('input', () => { ++request; searchAbort.abort(); cursor = null; more.hidden = true; searchBusy = false; search.disabled = false; searchStatus.textContent = '关键词已修改，请重新检索；旧分页游标已清除。'; });
  async function load(next: boolean): Promise<void> {
    if (!context.valid() || (next && (!cursor || searchBusy))) return;
    if (!navigator.onLine) { context.notify('当前离线，不能检索私有会话。'); return; }
    const value = query.value.trim();
    if (next && value !== searched) return;
    const own = ++request;
    searchAbort.abort(); searchAbort = new AbortController();
    searchBusy = true; search.disabled = true; more.disabled = true;
    if (!next) { cursor = null; items = []; results.replaceChildren(); }
    searched = value; searchStatus.textContent = '正在检索…';
    try {
      const page = await client.searchConversations({ q: value, limit: 30, ...(next && cursor ? { cursor } : {}), signal: searchAbort.signal });
      if (!context.valid() || own !== request) return;
      items = [...new Map([...items, ...page.items].map(item => [item.conversation.id, item])).values()];
      cursor = page.nextCursor; results.replaceChildren();
      for (const item of items) append(results, el('article', { class: 'search-result' }, el('h3', {}, item.conversation.title || '未命名会话'), empty(when(item.conversation.updatedAt)), el('p', {}, item.snippet), button('选择并打开聊天', () => context.selectConversation(item.conversation)), button('选择导出或分叉', () => select(item.conversation))));
      searchStatus.textContent = items.length ? `已显示 ${items.length} 个会话。` : '未找到会话。'; more.hidden = !cursor;
    } catch (error) { if (context.valid() && own === request) context.fail(error); }
    finally { if (context.valid() && own === request) { searchBusy = false; search.disabled = false; more.disabled = false; } }
  }
  function select(conversation: Conversation): void {
    if (!context.valid()) return;
    const mounted = ++selectedEpoch;
    selectionAbort.abort(); selectionAbort = new AbortController(); const selectedSignal = selectionAbort.signal;
    const valid = () => context.valid() && mounted === selectedEpoch;
    let exported: { filename: string; content: string; format: string } | null = null;
    const format = el('select', { 'aria-label': '会话导出格式' }, el('option', { value: 'json' }, 'JSON 结构归档'), el('option', { value: 'markdown' }, 'Markdown 文本'));
    const exportResult = empty('尚未导出。');
    const copy = button('复制本次会话导出', async () => {
      if (!valid() || !exported) return;
      try { await copyText(exported.content); if (valid()) context.notify('本次会话导出已复制，请注意剪贴板中的敏感聊天。'); }
      catch { if (valid()) context.notify('无法写入剪贴板，可重新下载导出文件。'); }
    }); copy.disabled = true;
    const exportButton = button('下载此会话', () => task(() => client.exportConversation(conversation.id, { format: format.value as 'json' | 'markdown' }, { signal: selectedSignal }), value => {
      exported = value; copy.disabled = false;
      downloadText(value.content, value.filename, value.format === 'json' ? 'application/json;charset=utf-8' : 'text/markdown;charset=utf-8');
      exportResult.textContent = '已下载本次完整会话导出，可主动复制；活动生成或过大归档会明确拒绝，不会静默截断。';
    }));
    const through = el('select', { 'aria-label': '分叉截止消息' }, el('option', { value: '' }, '全部已完成对话（服务端判定）'));
    const title = input('forkTitle', '可留空使用默认标题'); title.maxLength = 200;
    const confirm = el('input', { type: 'checkbox' });
    const pendingText = empty('');
    const retry = button('按同一编号与参数重试分叉', () => fork(true));
    const start = button('创建分叉会话', () => fork(false));
    const forgetConfirm = el('input', { type: 'checkbox' });
    const forget = button('放弃本地分叉重试记录', () => { if (!forgetConfirm.checked || !valid()) return; state.pending = null; forgetConfirm.checked = false; update(); context.notify('只清除了本地重试记录，服务端已创建的分叉不会被删除；请先检索确认。'); });
    const loadPairs = button('读取最近可选分叉点', () => task(() => client.listRecentMessages(conversation.id, { limit: 50, signal: selectedSignal }), page => {
      through.replaceChildren(el('option', { value: '' }, '全部已完成对话（服务端判定）'));
      for (let i = 1; i < page.items.length; i++) {
        const user = page.items[i - 1], assistant = page.items[i];
        if (user.role === 'user' && user.status === 'completed' && assistant.role === 'assistant' && assistant.status === 'completed' && assistant.runId === user.runId) append(through, el('option', { value: assistant.id }, `至助手消息 ${assistant.sequence}：${assistant.content.slice(0, 60)}`));
      }
      context.notify('只列出最近 50 条内完整的已完成对话对；最终合法性由服务器事务判定。');
    }));
    const task = featureTask({ ...context, valid, fail: error => { if (valid()) context.fail(error); } }, [exportButton, loadPairs, start, retry, forget]);
    function update(): void {
      const p = state.pending;
      pendingText.textContent = p ? `本地分叉编号：${p.input.clientForkId}。来源：${p.sourceId}。${p.uncertain ? '结果不确定，只能以原参数重试；不会因修改表单而改变。' : '正在提交。'}` : '';
      retry.hidden = !p; retry.disabled = !p?.uncertain;
      start.disabled = !!p; forget.hidden = !p; forgetConfirm.parentElement!.hidden = !p;
    }
    async function fork(retryOriginal: boolean): Promise<void> {
      if (!valid()) return;
      if (!navigator.onLine) { context.notify('当前离线，不创建或重试分叉。'); return; }
      if (!confirm.checked) { context.notify('请确认创建新的分叉会话。'); return; }
      if (retryOriginal) { if (!state.pending?.uncertain) return; }
      else {
        if (state.pending) return;
        state.pending = { sourceId: conversation.id, input: { clientForkId: crypto.randomUUID(), ...(through.value ? { throughMessageId: through.value } : {}), ...(title.value.trim() ? { title: title.value } : {}) }, uncertain: false };
      }
      const pending = state.pending!; pending.uncertain = false; update();
      await task(async () => {
        try { const result = await client.forkConversation(pending.sourceId, pending.input, { signal: selectedSignal }); if (state.pending === pending) state.pending = null; return result; }
        catch (error) {
          if (state.pending === pending) {
            if (error instanceof AlliraApiError && error.status >= 400 && error.status < 500) state.pending = null;
            else pending.uncertain = true;
          }
          throw error;
        }
      }, async result => {
        if (state.pending === pending) state.pending = null;
        context.notify(`分叉已创建：复制 ${result.copiedMessages} 条消息${result.replayed ? '（返回同一编号的既有结果）' : ''}。`);
        await context.selectConversation(result.conversation);
      });
      if (valid()) update();
    }
    selected.replaceChildren(el('h3', {}, `选中：${conversation.title || '未命名会话'}`), empty('下载与复制均由你主动触发，离开功能会清除本次导出内存。'), field('会话导出格式', format), exportButton, copy, exportResult, el('h4', {}, '安全分叉'), empty('活动生成、未完成或不合法截止点会返回冲突，不会复制半截/失败片段。'), loadPairs, field('分叉截止消息', through), field('新分叉标题', title), field('确认从已完成对话创建新会话', confirm), start, pendingText, retry, field('确认仅放弃本地分叉重试记录', forgetConfirm), forget);
    selectedSignal.addEventListener('abort', () => { exported = null; }, { once: true });
    update();
  }
  if (context.conversation) select(context.conversation);
  else append(selected, empty('请先在聊天中选中会话，或检索后点击“选择导出或分叉”。'));
}
