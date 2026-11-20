import { AlliraApiError } from '../../../packages/client/src/index.ts';
import type { CommitImportInput, ImportFormat, ImportPreview } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, input } from './dom.ts';
import { featureTask, heading, textArea } from './feature.ts';
import type { FeatureContext } from './feature.ts';
import { validImport } from './validation.ts';

export interface ImportMemoryState { pending: CommitImportInput | null }
export function mountImports(context: FeatureContext, state: ImportMemoryState): void {
  const { client, target, signal } = context;
  heading(target, '历史导入', '先预览再提交。原始文件仅发给当前同源后端，正文视为不可信纯文本；不会在浏览器持久保存。未成对消息可能仅归档。');
  const format = el('select', { name: 'importFormat', 'aria-label': '历史格式' });
  for (const [value, label] of [['text', '纯文本'], ['markdown', 'Markdown'], ['json', 'JSON']]) append(format, el('option', { value }, label));
  const file = el('input', { type: 'file', accept: '.txt,.md,.markdown,.json', name: 'importFile' });
  const content = textArea('importContent', 10, 1000000);
  const title = input('importTitle'); title.maxLength = 200;
  const core = textArea('importCoreMemory');
  const relationship = textArea('importRelationship', 3, 20000);
  const result = el('section', { 'aria-label': '导入预览结果' });
  const pendingText = empty('');
  const confirm = el('input', { type: 'checkbox', name: 'confirmImport' });
  let preview: { format: ImportFormat; content: string; value: ImportPreview } | null = null;
  let pending: CommitImportInput | null = state.pending;
  if (pending) { content.value = pending.content; format.value = pending.format; title.value = pending.title ?? ''; core.value = pending.coreMemory ?? ''; relationship.value = pending.relationship ?? ''; pendingText.textContent = '上次导入尚未确认，请勾选确认后以同一编号和原文重试。'; }
  let fileEpoch = 0;
  const previewButton = button('预览历史', async () => {
    if (pending) { context.notify('上次提交结果不确定，请先使用原始导入编号重试。'); return; }
    if (!validImport(content.value)) { context.notify('请填写有效历史文本，最多 1 MiB UTF-8 字节。'); return; }
    const snapshot = { format: format.value as ImportFormat, content: content.value };
    await task(() => client.previewImport(snapshot, { signal }), value => {
      if (snapshot.content !== content.value || snapshot.format !== format.value) { context.notify('输入已改变，请重新预览。'); return; }
      preview = { ...snapshot, value };
      if (!title.value.trim()) title.value = value.suggestedTitle;
      result.replaceChildren(el('h3', {}, `预览：${value.messages.length} 条消息`));
      for (const warning of value.warnings) append(result, empty(warning));
      for (const item of value.messages) append(result, el('article', { class: 'message' }, el('strong', {}, item.role === 'user' ? '用户' : '助手'), el('pre', { class: 'message-content' }, item.content)));
      context.notify('预览完成，确认后可提交为新会话。');
    });
  });
  const commitButton = button('确认导入为新会话', async () => {
    if (!confirm.checked) { context.notify('请确认导入会创建新会话。'); return; }
    if (!pending) {
      if (!preview || preview.content !== content.value || preview.format !== format.value) { context.notify('内容已改变或尚未预览，请先重新预览。'); return; }
      try { pending = { clientImportId: crypto.randomUUID(), format: preview.format, content: preview.content, ...(title.value.trim() ? { title: title.value } : {}), coreMemory: core.value, relationship: relationship.value }; }
      catch { context.notify('此访问方式不支持安全导入编号，请通过 localhost / 127.0.0.1 或 HTTPS 访问。'); return; }
    }
    state.pending = pending;
    const payload = { ...pending };
    pendingText.textContent = '提交使用同一导入编号、原文与设定；网络不确定时再次点击将原样重试。';
    await task(async () => {
      try { return await client.commitImport(payload, { signal }); }
      catch (error) { if (context.valid() && error instanceof AlliraApiError && error.status >= 400 && error.status < 500) { pending = null; state.pending = null; pendingText.textContent = ''; } throw error; }
    }, async value => {
      pending = null; state.pending = null; preview = null; content.value = ''; file.value = ''; core.value = ''; relationship.value = ''; title.value = ''; confirm.checked = false; result.replaceChildren(); pendingText.textContent = '';
      context.notify(`已导入 ${value.importedMessages} 条会话消息，另归档 ${value.archivedMessages} 条。${value.replayed ? '已识别同一导入重试。' : ''}`);
      await context.selectConversation(value.conversation);
    });
  });
  const task = featureTask(context, [previewButton, commitButton]);
  file.addEventListener('change', () => {
    const selected = file.files?.[0]; const request = ++fileEpoch;
    if (!selected) return;
    if (selected.size > 1024 * 1024) { file.value = ''; context.notify('文件超过 1 MiB，请先分割历史。'); return; }
    void selected.text().then(value => {
      if (!context.valid() || request !== fileEpoch || pending) return;
      if (!validImport(value)) { context.notify('文件不是有效文本或超过 1 MiB UTF-8 字节，请先分割历史。'); return; }
      content.value = value; format.value = /\.json$/i.test(selected.name) ? 'json' : /\.(md|markdown)$/i.test(selected.name) ? 'markdown' : 'text'; preview = null; result.replaceChildren();
    }).catch(() => { if (context.valid() && request === fileEpoch) context.notify('无法读取所选文件。'); });
  });
  append(target, field('历史格式', format), field('选择本地历史文件（不超过 1 MiB UTF-8 字节）', file), field('粘贴历史正文', content), previewButton, result, field('导入新会话标题', title), field('导入后的核心记忆', core), field('导入后的关系背景', relationship), field('确认导入会创建新会话并保存历史', confirm), commitButton, pendingText);
  const source = el('section', { 'aria-label': '显式查看的导入原文' });
  if (context.conversation) {
    const showSource = button('查看当前会话导入原文', async () => sourceTask(() => client.getImportSource(context.conversation!.id, { signal }), value => {
      source.replaceChildren(el('h3', {}, `原始格式：${value.format}；归档消息 ${value.archivedMessages} 条`), el('pre', { class: 'message-content' }, value.content), button('关闭导入原文', () => source.replaceChildren()));
    }));
    const sourceTask = featureTask(context, [showSource]); append(target, showSource, source);
  }
  signal.addEventListener('abort', () => { ++fileEpoch; content.value = ''; file.value = ''; core.value = ''; relationship.value = ''; result.replaceChildren(); source.replaceChildren(); pending = null; preview = null; }, { once: true });
}
