import type { Message } from '../../../packages/client/src/index.ts';
import { el, empty, runLabel } from './dom.ts';
import { renderMarkdown } from './markdown.ts';
import type { StreamView } from './state.ts';

type Display = Pick<Message, 'id' | 'role' | 'content'> & { status: string; replaying?: boolean };
type ScrollMode = 'preserve' | 'bottom' | 'prepend';
interface Row { node: HTMLElement; meta: HTMLElement; body: HTMLElement; value?: Display }

function streaming(view: StreamView): Display {
  return { id: view.run.assistantMessageId, role: 'assistant', content: view.content, status: view.run.status, replaying: !view.terminal };
}

export class MessageList {
  private readonly rows = new Map<string, Row>();
  private readonly placeholder = empty('此会话暂无消息。');
  private paintTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(private readonly box: HTMLElement) {}

  clear(): void {
    this.cancelPaint();
    this.rows.clear();
    this.box.replaceChildren(empty('尚未选择会话。'));
  }

  /** Reconcile persisted snapshots and pagination while retaining existing rows and focus. */
  reconcile(messages: Message[], view: StreamView | null, mode: ScrollMode): void {
    this.cancelPaint();
    this.withScroll(mode, () => {
      const overlay = view && streaming(view);
      const items: Display[] = messages.map(item => item.id === overlay?.id ? overlay : item);
      if (overlay && !messages.some(item => item.id === overlay.id)) items.push(overlay);
      const wanted = new Set(items.map(item => item.id));
      for (const [id, row] of this.rows) {
        if (!wanted.has(id)) { row.node.remove(); this.rows.delete(id); }
      }
      if (items.length === 0) { this.box.replaceChildren(this.placeholder); return; }
      // Remove any empty-state node before inserting rows. Inserting an already ordered row would lose focus.
      if (!this.box.firstElementChild?.hasAttribute('data-message-id')) this.box.firstElementChild?.remove();
      let next = this.box.firstChild;
      for (const item of items) {
        const row = this.update(item);
        if (row.node === next) next = next.nextSibling;
        else this.box.insertBefore(row.node, next);
      }
    });
  }

  /** The live path touches one assistant body at most every 100ms. */
  schedule(readLatest: () => StreamView | null): void {
    if (this.paintTimer !== undefined) return;
    this.paintTimer = setTimeout(() => {
      this.paintTimer = undefined;
      const latest = readLatest();
      if (latest && this.rows.has(latest.run.assistantMessageId)) {
        this.withScroll('preserve', () => this.update(streaming(latest)));
      }
    }, 100);
  }

  cancelPaint(): void {
    if (this.paintTimer !== undefined) clearTimeout(this.paintTimer);
    this.paintTimer = undefined;
  }

  private update(item: Display): Row {
    let row = this.rows.get(item.id);
    if (!row) {
      const meta = el('div', { class: 'message-meta' });
      const body = el('div', { class: 'message-content' });
      const node = el('article', { class: `message ${item.role}`, 'data-message-id': item.id }, meta, body);
      row = { meta, body, node };
      this.rows.set(item.id, row);
    }
    const previous = row.value;
    if (previous?.status !== item.status) row.meta.textContent = `${item.role === 'user' ? '用户' : '助手'} · ${runLabel(item.status)}`;
    const plain = !!item.replaying || item.status === 'generating';
    if (previous?.content !== item.content || !!previous?.replaying !== !!item.replaying || (previous?.status === 'generating') !== (item.status === 'generating')) {
      row.body.classList.toggle('message-markdown', !plain);
      if (plain || !item.content) row.body.textContent = item.content || (plain ? '等待生成…' : '（空内容）');
      else row.body.replaceChildren(renderMarkdown(item.content));
    }
    row.value = item;
    return row;
  }

  private withScroll(mode: ScrollMode, render: () => void): void {
    const top = this.box.scrollTop;
    const height = this.box.scrollHeight;
    const follow = height - top - this.box.clientHeight < 80;
    render();
    if (mode === 'bottom' || (mode === 'preserve' && follow)) this.box.scrollTop = this.box.scrollHeight;
    else if (mode === 'prepend') this.box.scrollTop = top + this.box.scrollHeight - height;
    else this.box.scrollTop = top;
  }
}
