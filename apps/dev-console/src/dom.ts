import { AlliraApiError, StreamInterruptedError, StreamProtocolError } from '../../../packages/client/src/index.ts';

export type Child = Node | string | null | undefined | false;
/** All dynamic data is a text node; never parses HTML. */
export function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string> = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  append(node, ...children);
  return node;
}
export function append(node: Node, ...children: Child[]): void {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  }
}
export function button(label: string, action: () => void | Promise<void>): HTMLButtonElement {
  const result = el('button', { type: 'button' }, label);
  result.addEventListener('click', () => { void action(); });
  return result;
}
export function field(label: string, input: HTMLElement): HTMLLabelElement {
  return el('label', {}, el('span', {}, label), input);
}
export function input(name: string, placeholder = ''): HTMLInputElement {
  return el('input', { name, placeholder, type: 'text', autocomplete: 'off' });
}
export function notice(): HTMLParagraphElement {
  return el('p', { class: 'notice', role: 'status', 'aria-live': 'polite' });
}
export function setNotice(node: HTMLElement, message: string, error = false): void {
  node.textContent = message;
  node.classList.toggle('error', error);
}
export function friendlyError(error: unknown): string {
  if (error instanceof AlliraApiError) {
    switch (error.status) {
      case 400: return '请求不正确或一次性登录链接已失效，请检查后重试。';
      case 401: return '登录已失效，请重新登录。';
      case 402: return '能量不足，请检查余额或兑换能量后再试。';
      case 403: return '没有权限，或会话校验失效；请检查登录与服务协议。';
      case 404: return '记录不存在，可能已删除，请刷新列表。';
      case 409: return '状态冲突：可能正在生成，或消息编号已绑定其他内容；请刷新状态后重试。';
      case 410: return '记录或一次性登录链接已过期，请重新签发。';
      case 413: return '内容过长，请缩短后重新发送。';
      case 422: return '输入不符合要求，请检查文字长度和必填项目。';
      case 429: return '请求过于频繁或生成数量达到限制，请稍后重试。';
      default: return error.status >= 500 ? '服务暂时不可用，请稍后重试。' : '请求失败，请刷新状态后重试。';
    }
  }
  if (error instanceof StreamInterruptedError) return '连接已断开，后台生成未取消；可点击“恢复订阅”。';
  if (error instanceof StreamProtocolError) return '订阅数据不符合约定；已停止本地读取，可尝试恢复。';
  if (isAbort(error)) return '已断开本地连接，后台生成未取消。';
  // Never surface raw server errors, codes, request bodies, tokens, or URLs.
  return '网络连接失败；请求结果可能不确定，请先刷新状态或使用相同消息重试。';
}
export function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}
export function when(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? '时间未知' : date.toLocaleString('zh-CN');
}
export function runLabel(status: string): string {
  return ({ running: '生成中', completed: '已完成', failed: '失败', cancelled: '已停止', generating: '生成中' } as Record<string, string>)[status] ?? '未知';
}
export function header(title: string, otherLabel: string, otherPath: string): HTMLElement {
  return el('header', { class: 'page-header' }, el('h1', {}, title), el('a', { href: otherPath }, otherLabel));
}
export function empty(text: string): HTMLElement { return el('p', { class: 'muted' }, text); }
