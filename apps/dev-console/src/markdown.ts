import MarkdownIt from 'markdown-it';
import type { Token } from 'markdown-it';
import { button, el, notice, setNotice } from './dom.ts';

const parser = new MarkdownIt('commonmark', { html: false, linkify: false, typographer: false, maxNesting: 20 });
const maxMarkdownLength = 64_000;
const tags: Partial<Record<string, keyof HTMLElementTagNameMap>> = {
  paragraph_open: 'p', blockquote_open: 'blockquote', bullet_list_open: 'ul', ordered_list_open: 'ol',
  list_item_open: 'li', strong_open: 'strong', em_open: 'em',
};
const headings = new Set(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']);

/** A user gesture is required. Report a failed clipboard write without exposing the input. */
export async function copyText(text: string): Promise<void> {
  if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
  await navigator.clipboard.writeText(text);
}

function httpUrl(value: string | null): string | null {
  if (!value || !/^https?:\/\//i.test(value) || /[\p{White_Space}\p{Cc}\\]/u.test(value)) return null;
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) return null;
    return url.href;
  } catch { return null; }
}

function codeBlock(content: string): HTMLElement {
  const result = notice();
  return el('div', { class: 'markdown-code' }, el('pre', {}, el('code', {}, content)), button('复制代码', async () => {
    try { await copyText(content); setNotice(result, '代码已复制。'); }
    catch { setNotice(result, '无法写入剪贴板，请手动选择代码复制。', true); }
  }), result);
}

/** Render a bounded CommonMark token tree directly to a fixed DOM vocabulary. */
export function renderMarkdown(source: string): Node {
  const plain = () => el('div', { class: 'markdown-plain' }, source);
  if (source.length > maxMarkdownLength) return plain();
  try {
    const root = document.createDocumentFragment();
    let remaining = 4_096;
    const render = (tokens: Token[], container: Node): void => {
      const stack: Node[] = [container];
      for (const token of tokens) {
        if (--remaining < 0) throw new Error('Markdown render budget exceeded');
        const parent = stack[stack.length - 1];
        if (token.nesting === -1) { if (stack.length > 1) stack.pop(); continue; }
        if (token.nesting === 1) {
          let node: HTMLElement | null = null;
          const tag = tags[token.type];
          if (token.type === 'link_open') {
            const rawHref = token.attrGet('href');
            const href = httpUrl(typeof rawHref === 'string' ? rawHref : null);
            if (href) node = el('a', { href, target: '_blank', rel: 'noopener noreferrer', referrerpolicy: 'no-referrer' });
          } else if (!token.hidden && tag) {
            node = el(tag);
            const start = token.type === 'ordered_list_open' ? token.attrGet('start') : null;
            if ((typeof start === 'string' || typeof start === 'number') && /^\d{1,9}$/.test(String(start))) node.setAttribute('start', String(start));
          } else if (token.type === 'heading_open' && headings.has(token.tag)) {
            node = el(token.tag as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6');
          }
          if (node) parent.appendChild(node);
          stack.push(node ?? parent);
          continue;
        }
        if (token.type === 'inline') { if (token.children) render(token.children, parent); continue; }
        if (token.type === 'image') {
          parent.appendChild(document.createTextNode(token.content ? `[图片：${token.content}]` : '[图片]'));
        } else if (token.type === 'fence' || token.type === 'code_block') {
          parent.appendChild(codeBlock(token.content));
        } else if (token.type === 'code_inline') {
          parent.appendChild(el('code', {}, token.content));
        } else if (token.type === 'hr' || token.type === 'hardbreak') {
          parent.appendChild(el(token.type === 'hr' ? 'hr' : 'br'));
        } else if (token.type === 'softbreak') {
          parent.appendChild(document.createTextNode('\n'));
        } else if (token.content) {
          parent.appendChild(document.createTextNode(token.content));
        }
      }
    };
    render(parser.parse(source, {}), root);
    return root;
  } catch { return plain(); }
}
