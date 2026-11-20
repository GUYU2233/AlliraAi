import { AlliraApiError } from '../../../packages/client/src/index.ts';
import type { MemoryCard, MemoryBlock, ContextPreview } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, input, isAbort, when } from './dom.ts';
import { details, featureTask, textArea } from './feature.ts';
import type { FeatureContext } from './feature.ts';
import { characters, validMemoryText } from './validation.ts';

const categories = [['fact', '事实'], ['preference', '偏好'], ['boundary', '边界'], ['relationship', '关系']] as const;
const categoryName = (value: string): string => categories.find(([key]) => key === value)?.[1] ?? '未知分类';
const sectionNames: Record<string, string> = { instructions: '会话设定', character: '角色', core: '核心记忆', relationship: '关系背景', memory_cards: '记忆卡', history_notes: '历史摘录', recent_messages: '最近消息', current_message: '准备发送的文本' };
const roleNames: Record<string, string> = { system: '系统', user: '用户', assistant: '助手' };

/** All state belongs to one conversation and one authenticated feature mount. No background extraction or POST retries. */
export function mountContextMemory(context: FeatureContext): () => void {
  const { client, target, signal, conversation } = context;
  const root = el('section', { 'aria-label': '可追溯记忆与上下文' });
  append(target, root);
  append(root, el('h3', {}, '可追溯记忆与上下文'), empty('记忆卡由你明确确认，不会自动将助手输出当作事实或关系。历史块是规则摘录，不是模型永久记得；摘要视图的 6000 字符预算不代表其他原文被删除。'));
  if (!conversation) { append(root, empty('请先在聊天中选择会话，再读取或修改该会话记忆。')); return () => {}; }
  const conversationId = conversation.id;
  const valid = (): boolean => !signal.aborted && context.valid() && context.conversation?.id === conversationId;
  const scoped: FeatureContext = { ...context, valid, fail: report };
  const uncertain = { cards: false, blocks: false };
  let writing = false;
  function report(error: unknown): void {
    if (!valid() || isAbort(error)) return;
    if (error instanceof AlliraApiError) {
      if (error.status === 409) { context.notify('记忆状态冲突：可能正在生成，或已达 50 张卡 / 总内容 20000 字符限制。请刷新核对后再操作。'); return; }
      if (error.status === 400) { context.notify('记忆参数无效：请核对来源是否为本会话已完成对话对的用户消息，或刷新历史摘录后重新翻页。'); return; }
      if (error.status === 413) { context.notify('本次上下文超出字符预算，请缩短准备发送的文本或调整已启用的记忆与设定后再预览。'); return; }
      if (error.status === 422) { context.notify('记忆或当前上下文包含无效文本、控制字符或被当前服务规则阻止的内容，请调整后再预览。'); return; }
    }
    context.fail(error);
  }
  async function write<T>(kind: 'cards' | 'blocks', load: () => Promise<T>, apply: (value: T) => void): Promise<void> {
    if (writing) return;
    writing = true; invalidatePreview();
    try { const value = await load(); if (valid()) { invalidatePreview(); apply(value); } }
    catch (error) {
      if (valid() && !(error instanceof AlliraApiError && error.status < 500) && !isAbort(error)) { uncertain[kind] = true; context.notify('记忆修改结果不确定，未自动重试。请先刷新对应列表核对服务端结果，再决定是否重新确认修改。'); }
      else if (valid()) report(error);
    } finally { writing = false; }
  }
  function canWrite(confirm: HTMLInputElement, kind: 'cards' | 'blocks'): boolean {
    if (writing) { context.notify('正在保存记忆，请等待本次操作完成。'); return false; }
    if (!confirm.checked) { context.notify('请先勾选本次记忆修改确认。'); return false; }
    if (uncertain[kind]) { context.notify('上次修改结果不确定，请先刷新对应列表核对。'); return false; }
    confirm.checked = false; return true;
  }

  // Cards are owner-authored; recent messages are fetched only when choosing a source explicitly.
  const cardsSection = el('section', { 'aria-label': '记忆卡管理' });
  const cardsList = el('div', { 'aria-label': '当前会话记忆卡' });
  const cardStatus = el('p', { role: 'status' });
  const cardTitle = input('memoryCardTitle'); cardTitle.maxLength = 200;
  const cardContent = textArea('memoryCardContent', 4, 4000);
  const cardCategory = el('select', { 'aria-label': '记忆卡分类' }, ...categories.map(([key, label]) => el('option', { value: key }, label)));
  const cardSource = el('select', { 'aria-label': '绑定用户消息来源' }, el('option', { value: '' }, '不绑定来源'));
  const cardEnabled = el('input', { type: 'checkbox' }); cardEnabled.checked = true;
  const cardConfirm = el('input', { type: 'checkbox' });
  const editorStatus = el('p', {}, '新增记忆卡');
  let cards: MemoryCard[] = [], editingId: string | null = null, editorEpoch = 0;
  let userSources: { id: string; sequence: number; content: string }[] = [];
  for (const control of [cardTitle, cardContent, cardCategory, cardSource, cardEnabled]) control.addEventListener('input', () => { ++editorEpoch; cardConfirm.checked = false; });
  const resetCard = button('清空并新增记忆卡', () => chooseCard(null));
  const refreshCards = button('刷新记忆卡', () => cardTask(loadCards, () => {}));
  const readSources = button('读取最近可绑定用户消息', () => cardTask(async () => client.listRecentMessages(conversationId, { limit: 50, signal }), page => {
    const completedAssistants = new Map(page.items.filter(item => item.conversationId === conversationId && item.role === 'assistant' && item.status === 'completed').map(item => [item.sequence, item]));
    userSources = page.items.filter(item => {
      const assistant = completedAssistants.get(item.sequence + 1);
      return item.conversationId === conversationId && item.role === 'user' && item.status === 'completed' && !!assistant && !!item.runId && item.runId === assistant.runId;
    }).map(item => ({ id: item.id, sequence: item.sequence, content: item.content }));
    refreshSourceOptions(cardSource.value); context.notify('只列出本会话最近 50 条内完整完成对话对的用户消息，不把助手消息作为事实来源；缺少另一半的边界消息不列出。');
  }));
  const saveCard = button('确认保存记忆卡', () => {
    if (!validMemoryText(cardTitle.value, 100) || !validMemoryText(cardContent.value, 2000)) { context.notify('记忆卡标题需 1–100 字符，内容需 1–2000 字符，均不能仅为空白。'); return; }
    if (!canWrite(cardConfirm, 'cards')) return;
    const capturedEditor = editorEpoch, id = editingId;
    const values = { category: cardCategory.value as MemoryCard['category'], title: cardTitle.value.trim(), content: cardContent.value, sourceMessageId: cardSource.value || null, enabled: cardEnabled.checked };
    return cardTask(() => write('cards', () => id ? client.updateMemoryCard(conversationId, id, values, { signal }) : client.createMemoryCard(conversationId, values, { signal }), item => {
      cards = id ? cards.map(card => card.id === id ? item : card) : [...cards, item]; renderCards();
      if (capturedEditor === editorEpoch) chooseCard(item);
      context.notify('记忆卡已按本次确认保存；不代表模型质量验证。');
    }), () => {});
  });
  const cardControls = [saveCard, resetCard, refreshCards, readSources];
  const cardTask = featureTask(scoped, cardControls);
  function refreshSourceOptions(selected: string): void {
    cardSource.replaceChildren(el('option', { value: '' }, '不绑定来源'));
    for (const item of userSources) append(cardSource, el('option', { value: item.id }, `用户消息 #${item.sequence}：${item.content.slice(0, 60)}`));
    if (selected && !userSources.some(item => item.id === selected)) append(cardSource, el('option', { value: selected }, '已绑定用户来源（不在最近已载入页，服务端将重新校验）'));
    cardSource.value = selected;
  }
  function chooseCard(card: MemoryCard | null): void {
    ++editorEpoch; editingId = card?.id ?? null;
    cardTitle.value = card?.title ?? ''; cardContent.value = card?.content ?? ''; cardCategory.value = card?.category ?? 'fact'; cardEnabled.checked = card?.enabled ?? true; cardConfirm.checked = false;
    refreshSourceOptions(card?.sourceMessageId ?? ''); editorStatus.textContent = card ? `编辑记忆卡：${card.title}` : '新增记忆卡';
  }
  async function loadCards(): Promise<void> {
    const result = await client.listMemoryCards(conversationId, { signal });
    if (!valid()) return; cards = result.items; uncertain.cards = false; renderCards();
  }
  function renderCards(): void {
    cardsList.replaceChildren(); cardStatus.textContent = `已保存 ${cards.length} / 50 张卡，内容合计 ${cards.reduce((total, item) => total + characters(item.content), 0)} / 20000 字符（以服务端校验为准）。`;
    for (const card of cards) {
      const toggleConfirm = el('input', { type: 'checkbox' });
      const deleteConfirm = el('input', { type: 'checkbox' });
      const row = el('article', { 'aria-label': `记忆卡：${card.title}` }, el('h4', {}, card.title), details([['分类', categoryName(card.category)], ['状态', card.enabled ? '已启用' : '已停用'], ['来源', card.sourceMessageId ? '已绑定本会话用户消息' : '手工填写，未绑定来源'], ['更新时间', when(card.updatedAt)]]), el('pre', { class: 'message-content' }, card.content));
      append(row, button('编辑此记忆卡', () => chooseCard(card)), field('确认切换此记忆卡状态', toggleConfirm), button(card.enabled ? '停用此记忆卡' : '启用此记忆卡', () => {
        if (!canWrite(toggleConfirm, 'cards')) return;
        return cardTask(() => write('cards', () => client.updateMemoryCard(conversationId, card.id, { enabled: !card.enabled }, { signal }), item => { cards = cards.map(value => value.id === card.id ? item : value); renderCards(); if (editingId === card.id) chooseCard(item); context.notify(item.enabled ? '记忆卡已启用。' : '记忆卡已停用，不会删除聊天原文。'); }), () => {});
      }), field('确认删除此记忆卡', deleteConfirm), button('删除此记忆卡', () => {
        if (!canWrite(deleteConfirm, 'cards')) return;
        return cardTask(() => write('cards', () => client.deleteMemoryCard(conversationId, card.id, { signal }), () => { cards = cards.filter(item => item.id !== card.id); renderCards(); if (editingId === card.id) chooseCard(null); context.notify('记忆卡已删除，聊天原文未删除。'); }), () => {});
      }));
      append(cardsList, row);
    }
    if (!cards.length) append(cardsList, empty('尚无记忆卡；可手工填写并确认保存，不会自动从助手输出提取。'));
  }
  append(cardsSection, el('h4', {}, '人工确认的记忆卡'), empty('单会话最多 50 张，全部卡内容合计最多 20000 字符；全部启用卡都是上下文必选段；若必选内容超预算，服务端返回超限，不会静默丢弃边界或其他启用卡。'), refreshCards, cardStatus, cardsList, el('details', { open: '' }, el('summary', {}, '新增 / 编辑记忆卡'), editorStatus, field('记忆卡分类', cardCategory), field('记忆卡标题', cardTitle), field('记忆卡内容', cardContent), readSources, field('绑定用户消息来源', cardSource), field('此记忆卡启用', cardEnabled), field('确认以上记忆由我填写并按此内容保存', cardConfirm), el('div', { class: 'actions' }, saveCard, resetCard)));
  append(root, cardsSection);

  // Summary pages never fetch original messages until the user explicitly asks for one block.
  const blocksSection = el('section', { 'aria-label': '历史记忆摘录块' });
  const query = input('memoryBlockQuery'); query.maxLength = 400;
  const blocksList = el('div', { 'aria-label': '历史摘录列表' });
  const sourceOutput = el('section', { 'aria-label': '摘录块原文' });
  const blockStatus = el('p', { role: 'status' });
  let blocks: MemoryBlock[] = [], cursor: string | null = null, blockEpoch = 0, sourceEpoch = 0;
  let blocksAbort = new AbortController(), sourceAbort = new AbortController();
  const searchBlocks = button('检索历史摘录', () => loadBlocks(false));
  const refreshBlocks = button('刷新历史摘录', () => loadBlocks(false));
  const moreBlocks = button('更多历史摘录', () => loadBlocks(true)); moreBlocks.hidden = true;
  const closeSource = button('关闭并清除摘录原文', clearSource);
  function clearSource(): void { ++sourceEpoch; sourceAbort.abort(); sourceAbort = new AbortController(); sourceOutput.replaceChildren(); }
  function clearBlockQuery(): void { ++blockEpoch; blocksAbort.abort(); blocks = []; cursor = null; blockStatus.textContent = ''; renderBlocks(); clearSource(); for (const control of [searchBlocks, refreshBlocks, moreBlocks]) control.disabled = false; }
  query.addEventListener('input', clearBlockQuery);
  async function loadBlocks(appendItems: boolean): Promise<void> {
    if (!validMemoryText(query.value, 200, false)) { context.notify('摘录检索词最多 200 字符，仅搜索摘要，不搜索或读取全部原文。'); return; }
    const q = query.value.trim(); const next = appendItems ? cursor : null;
    blocksAbort.abort(); blocksAbort = new AbortController(); const requestSignal = blocksAbort.signal, request = ++blockEpoch;
    clearSource();
    const requestValid = (): boolean => valid() && request === blockEpoch && !requestSignal.aborted;
    await featureTask({ ...scoped, signal: requestSignal, valid: requestValid }, [searchBlocks, refreshBlocks, moreBlocks])(() => client.listMemoryBlocks(conversationId, { q, ...(next ? { cursor: next } : {}), limit: 20, signal: requestSignal }), page => {
      blocks = [...new Map((appendItems ? [...blocks, ...page.items] : page.items).map(item => [item.id, item])).values()]; cursor = page.nextCursor; uncertain.blocks = false; renderBlocks();
    });
  }
  function renderBlocks(): void {
    blocksList.replaceChildren(); moreBlocks.hidden = !cursor; blockStatus.textContent = `本次列表已载入 ${blocks.length} 个规则摘录块；不是全部历史原文的删除或永久记忆声明。`;
    for (const block of blocks) {
      const confirm = el('input', { type: 'checkbox' });
      const row = el('article', { 'aria-label': `历史摘录块 ${block.id}` }, el('h4', {}, block.kind === 'legacy_summary' ? '旧版摘要（来源可能不可追溯）' : '已完成对话对的规则摘录'), details([['消息序号', `${block.startSequence}–${block.endSequence}`], ['状态', block.enabled ? '已启用' : '已停用'], ['建立时间', when(block.createdAt)]]), el('pre', { class: 'message-content' }, block.summary));
      append(row, button('查看此摘录来源原文', () => readSource(block)), field('确认切换此历史摘录状态', confirm), button(block.enabled ? '停用此历史摘录' : '启用此历史摘录', () => {
        if (!canWrite(confirm, 'blocks')) return;
        return blockWriteTask(() => write('blocks', () => client.updateMemoryBlock(conversationId, block.id, { enabled: !block.enabled }, { signal }), item => { blocks = blocks.map(value => value.id === block.id ? item : value); renderBlocks(); clearSource(); context.notify('摘录状态已更新，仅影响历史提示，不删除聊天或存储原文。'); }), () => {});
      }));
      append(blocksList, row);
    }
    if (!blocks.length) append(blocksList, empty('此页暂无历史摘录。可更新规则摘要后刷新；不会因此宣称旧原文已删除。'));
  }
  const blockWriteTask = featureTask(scoped);
  async function readSource(block: MemoryBlock): Promise<void> {
    clearSource(); const request = sourceEpoch, requestSignal = sourceAbort.signal;
    const requestValid = (): boolean => valid() && request === sourceEpoch && !requestSignal.aborted;
    await featureTask({ ...scoped, signal: requestSignal, valid: requestValid })(() => client.getMemoryBlockSource(conversationId, block.id, { signal: requestSignal }), result => {
      sourceOutput.replaceChildren(el('h4', {}, '本次显式读取的摘录来源'), closeSource);
      for (const warning of result.warnings) append(sourceOutput, empty(warning));
      if (!result.messages.length) append(sourceOutput, empty('此旧版摘要没有可核对的来源原文；不将其伪装为完整对话证据。'));
      for (const message of result.messages) append(sourceOutput, el('article', {}, el('h5', {}, `${roleNames[message.role] ?? '未知'} · 消息 #${message.sequence}`), el('pre', { class: 'message-content' }, message.content)));
    });
  }
  append(blocksSection, el('h4', {}, '可回查的历史摘录'), empty('仅搜索摘要；来源原文必须点选单个块后读取。停用块只影响旧历史提示，不删除聊天或存储。'), field('历史摘录检索词', query), el('div', { class: 'actions' }, searchBlocks, refreshBlocks, moreBlocks), blockStatus, blocksList, sourceOutput);
  append(root, blocksSection);

  // This read-only POST never sends a model request and never copies its draft into the chat composer.
  const previewSection = el('section', { 'aria-label': '实际上下文预览' });
  const previewInput = textArea('contextPreviewContent', 5, 40000);
  const previewOutput = el('section', { 'aria-label': '上下文预算结果' });
  let previewEpoch = 0, previewAbort = new AbortController();
  const previewButton = button('预览实际上下文（不会发送）', preview);
  const clearPreview = button('清除上下文预览', () => { previewInput.value = ''; invalidatePreview(); });
  function invalidatePreview(): void { ++previewEpoch; previewAbort.abort(); previewAbort = new AbortController(); previewOutput.replaceChildren(); previewButton.disabled = false; }
  previewInput.addEventListener('input', invalidatePreview);
  async function preview(): Promise<void> {
    if (!validMemoryText(previewInput.value, 20000)) { context.notify('准备预览的文本需非空且不超过 20000 字符；这不会发送聊天。'); return; }
    if (writing) { context.notify('正在保存记忆，请等待完成后再预览。'); return; }
    invalidatePreview(); const request = previewEpoch, requestSignal = previewAbort.signal;
    const requestValid = (): boolean => valid() && request === previewEpoch && !requestSignal.aborted;
    await featureTask({ ...scoped, signal: requestSignal, valid: requestValid }, [previewButton])(() => client.previewContext(conversationId, { content: previewInput.value }, { signal: requestSignal }), renderPreview);
  }
  function renderPreview(result: ContextPreview): void {
    previewOutput.replaceChildren(el('h4', {}, '本次只读上下文预算'), empty('未发送消息、不扣能量、不调用模型。字符不是 Token、缓存命中或实际模型质量；实际发送时状态变化可能改变上下文。'), details([['字符上限', result.characterLimit], ['总字符', result.totalCharacters], ['剩余字符', result.remainingCharacters], ['入选记忆卡', result.includedCardIds.length], ['入选历史块', result.includedBlockIds.length], ['待写入摘录块', result.pendingBlockIds.length], ['未纳入记忆卡', result.excludedCardCount], ['省略历史块', result.omittedBlockCount], ['省略最近对话对', result.omittedRecentPairCount]]), details([['入选记忆卡 ID', result.includedCardIds.join('、') || '无'], ['入选历史块 ID', result.includedBlockIds.join('、') || '无'], ['待写入摘录块 ID', result.pendingBlockIds.join('、') || '无']]));
    for (const section of result.sections) append(previewOutput, details([['上下文段', sectionNames[section.kind] ?? '未知段'], ['字符', section.characters], ['消息数', section.messages]]));
    for (const warning of result.warnings) append(previewOutput, empty(warning));
    const actual = el('details', {}, el('summary', {}, '显式展开本次实际提示词文本（敏感）'));
    actual.addEventListener('toggle', () => {
      if (!valid() || !actual.open || actual.childElementCount > 1) return;
      for (const message of result.messages) append(actual, el('article', {}, el('h5', {}, roleNames[message.role] ?? '未知'), el('pre', { class: 'message-content' }, message.content)));
    });
    append(previewOutput, actual);
  }
  append(previewSection, el('h4', {}, '发送前检查上下文'), empty('显式预览本次准备发送的文本。不会生成、发送或扣费，也不会把此文本自动填入聊天；预览响应已包含敏感提示词并暂存于当前页面内存，只是默认折叠显示；清除预览或离开此页会释放本页面引用。'), field('准备发送的文本（仅预览）', previewInput), el('div', { class: 'actions' }, previewButton, clearPreview), previewOutput);
  append(root, previewSection);

  signal.addEventListener('abort', () => {
    blocksAbort.abort(); sourceAbort.abort(); previewAbort.abort(); ++blockEpoch; ++sourceEpoch; ++previewEpoch; ++editorEpoch;
    cards = []; blocks = []; userSources = []; editingId = null; cardTitle.value = ''; cardContent.value = ''; query.value = ''; previewInput.value = ''; cardSource.replaceChildren(); cardsList.replaceChildren(); blocksList.replaceChildren(); sourceOutput.replaceChildren(); previewOutput.replaceChildren();
  }, { once: true });
  void cardTask(loadCards, () => {});
  void loadBlocks(false);
  return invalidatePreview;
}
