import type { Character, ConversationMemory } from '../../../packages/client/src/index.ts';
import { append, button, el, empty, field, input } from './dom.ts';
import { featureTask, heading, textArea } from './feature.ts';
import type { FeatureContext } from './feature.ts';
import { mountContextMemory } from './context-memory.ts';

export function mountRoles(context: FeatureContext): void {
  const { client, target, signal, conversation } = context;
  heading(target, '角色与会话记忆', '设定、核心记忆、关系与摘要会参与受限上下文。规则摘要仅是 MVP，不代表真实模型记忆质量；生成时修改会返回状态冲突。');
  const list = el('section', { 'aria-label': '角色列表' });
  const name = input('characterName'); name.maxLength = 100;
  const description = textArea('characterDescription', 3, 20000);
  const instructions = textArea('characterInstructions');
  const selected = el('p', {}, '正在新建角色');
  const removeConfirm = el('input', { type: 'checkbox' });
  let character: Character | null = null;
  let cursor: string | null = null;
  let characters: Character[] = [];
  const chooseRole = el('select', { name: 'memoryCharacter', 'aria-label': '绑定角色' }, el('option', { value: '' }, '不绑定角色'));
  const createNew = button('清空并新建角色', () => choose(null));
  const save = button('保存角色', async () => {
    if (!name.value.trim()) { context.notify('角色名称不能为空。'); return; }
    const values = { name: name.value, description: description.value, instructions: instructions.value };
    invalidateContextPreview();
    await task(() => character ? client.updateCharacter(character.id, values, { signal }) : client.createCharacter(values, { signal }), async item => { choose(item); context.notify('角色已保存。'); await load(false); });
  });
  const remove = button('删除角色', async () => {
    if (!character || !removeConfirm.checked) { context.notify('请选择角色并确认删除。'); return; }
    await task(() => client.deleteCharacter(character!.id, { signal }), async () => { choose(null); context.notify('角色已删除；会话仍保留，绑定按服务端结果更新。'); await load(false); });
  });
  const more = button('加载更多角色', () => task(() => load(true), () => {}));
  const refresh = button('刷新角色', () => task(() => load(false), () => {}));
  const task = featureTask(context, [save, remove, more, refresh, createNew]);
  function choose(item: Character | null): void {
    character = item; selected.textContent = item ? `编辑角色：${item.name}` : '正在新建角色';
    name.value = item?.name ?? ''; description.value = item?.description ?? ''; instructions.value = item?.instructions ?? ''; removeConfirm.checked = false; remove.disabled = !item;
  }
  async function load(appendItems: boolean): Promise<void> {
    const page = await client.listCharacters({ limit: 30, ...(appendItems && cursor ? { cursor } : {}), signal });
    if (!context.valid()) return;
    characters = [...new Map((appendItems ? [...characters, ...page.items] : page.items).map(item => [item.id, item])).values()]; cursor = page.nextCursor;
    const memorySelection = chooseRole.value;
    list.replaceChildren(); chooseRole.replaceChildren(el('option', { value: '' }, '不绑定角色'));
    for (const item of characters) {
      append(list, button(item.name, () => choose(item)));
      append(chooseRole, el('option', { value: item.id }, item.name));
    }
    if (!characters.length) append(list, empty('暂无角色。'));
    if (memorySelection && !characters.some(item => item.id === memorySelection)) append(chooseRole, el('option', { value: memorySelection }, '当前绑定角色（未载入此页）'));
    chooseRole.value = memorySelection;
    more.hidden = !cursor;
  }
  append(target, el('div', { class: 'actions' }, refresh, more), list, el('details', { open: '' }, el('summary', {}, '创建 / 编辑角色'), selected, field('角色名称', name), field('角色描述', description), field('角色设定', instructions), el('div', { class: 'actions' }, save, createNew), field('确认删除此角色', removeConfirm), remove));
  remove.disabled = true;
  const memorySection = el('section', { 'aria-label': '当前会话记忆' });
  append(target, memorySection);
  const core = textArea('coreMemory');
  const relationship = textArea('relationship', 4, 20000);
  const summary = el('pre', { class: 'message-content', 'aria-label': '当前记忆摘要' });
  const memoryStatus = el('p');
  let memoryReady = false;
  function renderMemory(memory: ConversationMemory): void {
    if (memory.characterId && !Array.from(chooseRole.options).some(option => option.value === memory.characterId)) append(chooseRole, el('option', { value: memory.characterId }, '当前绑定角色（未载入此页）'));
    memoryReady = true; saveMemory.disabled = false; summarize.disabled = false; refreshMemory.disabled = false;
    chooseRole.value = memory.characterId ?? ''; core.value = memory.coreMemory; relationship.value = memory.relationship;
    summary.textContent = memory.summary || '尚无摘要。'; memoryStatus.textContent = `已摘要至消息序号 ${memory.summarizedThrough}。`;
  }
  const saveMemory = button('保存当前会话记忆', async () => {
    if (!conversation || !memoryReady) return;
    invalidateContextPreview();
    await memoryTask(() => client.updateConversationMemory(conversation.id, { characterId: chooseRole.value || null, coreMemory: core.value, relationship: relationship.value }, { signal }), memory => { invalidateContextPreview(); renderMemory(memory); context.notify('当前会话的角色、核心记忆与关系已保存。'); });
  });
  const summarize = button('生成规则摘要', async () => {
    if (!conversation || !memoryReady) return;
    invalidateContextPreview();
    await memoryTask(() => client.summarizeConversation(conversation.id, { signal }), memory => { invalidateContextPreview(); renderMemory(memory); context.notify('规则摘要已更新；不是实际模型质量验证。请显式刷新历史摘录列表查看来源块。'); });
  });
  const refreshMemory = button('刷新当前会话记忆', () => { invalidateContextPreview(); return memoryTask(() => client.getConversationMemory(conversation!.id, { signal }), renderMemory); });
  const memoryTask = featureTask(context, [saveMemory, summarize, refreshMemory]);
  if (!conversation) append(memorySection, empty('请先在聊天中选择会话，再进入此页设置会话记忆。'));
  else {
    append(memorySection, el('h3', {}, `当前会话：${conversation.title || '未命名会话'}`), field('绑定角色', chooseRole), field('核心记忆', core), field('关系背景', relationship), el('div', { class: 'actions' }, saveMemory, summarize, refreshMemory), el('h4', {}, '阶段摘要'), summary, memoryStatus);
  }
  saveMemory.disabled = true; summarize.disabled = true;
  const invalidateContextPreview = mountContextMemory(context);
  void task(() => load(false), () => {});
  if (conversation) void memoryTask(() => client.getConversationMemory(conversation.id, { signal }), renderMemory);
}
