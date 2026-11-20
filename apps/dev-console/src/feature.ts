import type { AlliraClient, Conversation } from '../../../packages/client/src/index.ts';
import { append, el, empty } from './dom.ts';

export interface FeatureContext {
  client: AlliraClient;
  target: HTMLElement;
  signal: AbortSignal;
  valid(): boolean;
  fail(error: unknown): void;
  notify(text: string): void;
  conversation: Conversation | null;
  isAccount: boolean;
  selectConversation(item: Conversation): Promise<void>;
  signedOut(text: string): void;
  profileChanged(displayName: string): void;
}
/** Every module shares a mount-scoped AbortSignal plus an auth/mount epoch check. */
export function featureTask(context: FeatureContext, controls: HTMLButtonElement[] = []) {
  let busy = false;
  return async <T>(load: () => Promise<T>, apply: (value: T) => void | Promise<void>): Promise<void> => {
    if (busy || !context.valid()) return;
    if (!navigator.onLine) { context.notify('当前离线，不能提交或读取账号数据；公开页面壳仍可使用。'); return; }
    busy = true; for (const control of controls) control.disabled = true;
    try {
      const value = await load();
      if (context.valid()) await apply(value);
    } catch (error) { if (context.valid()) context.fail(error); }
    finally { if (context.valid()) { busy = false; for (const control of controls) control.disabled = false; } }
  };
}
export function details(pairs: [string, string | number][]): HTMLElement {
  const list = el('dl', { class: 'metrics' });
  for (const [key, value] of pairs) append(list, el('dt', {}, key), el('dd', {}, String(value)));
  return list;
}
export function heading(target: HTMLElement, title: string, text?: string): void {
  target.replaceChildren(el('h2', {}, title));
  if (text) append(target, empty(text));
}
export function textArea(name: string, rows = 4, maxLength = 20000): HTMLTextAreaElement {
  return el('textarea', { name, rows: String(rows), maxlength: String(maxLength) });
}
export function table(columns: string[], caption: string): { element: HTMLElement; body: HTMLTableSectionElement } {
  const body = el('tbody');
  return { element: el('div', { class: 'table-wrap', tabindex: '0' }, el('table', {}, el('caption', {}, caption), el('thead', {}, el('tr', {}, ...columns.map(text => el('th', { scope: 'col' }, text)))), body)), body };
}
