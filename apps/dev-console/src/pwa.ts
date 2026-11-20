import { append, button, el, setNotice } from './dom.ts';

interface InstallPrompt extends Event { prompt(): Promise<void>; userChoice: Promise<{ outcome: string }> }
export function setupPwa(status: HTMLElement, changed: () => void): void {
  let prompt: InstallPrompt | null = null;
  const install = button('添加测试客户端到主屏幕', async () => {
    const value = prompt; if (!value) return;
    prompt = null; install.hidden = true;
    try { await value.prompt(); await value.userChoice; } catch { /* Browser controls installation. */ }
  }); install.hidden = true;
  append(status.parentElement!, el('div', { class: 'actions' }, install));
  window.addEventListener('beforeinstallprompt', event => { event.preventDefault(); prompt = event as InstallPrompt; install.hidden = false; });
  const update = () => { setNotice(status, navigator.onLine ? '在线。账号数据、聊天、草稿、兑换码和恢复码不离线缓存；仅主动记住设备可联网恢复 Cookie 登录。' : '当前离线：仅公开页面壳可用，不能登录、读取账号数据或提交消息。', !navigator.onLine); changed(); };
  window.addEventListener('online', update); window.addEventListener('offline', update);
  // Delay initial callback until the caller has initialized all page controls.
  queueMicrotask(update);
  if ('serviceWorker' in navigator && window.isSecureContext) {
    void navigator.serviceWorker.register('/test/sw.js', { scope: '/test/' }).catch(() => {
      if (navigator.onLine) setNotice(status, '在线。此浏览器未启用离线页面壳，仍可正常登录聊天。');
    });
  }
}
