import { inject, onScopeDispose, provide, ref, shallowRef } from 'vue';
import type { InjectionKey, Ref } from 'vue';
import { AlliraAdminClient, AlliraApiError } from '@allira/client';
import type { AdminSession, Page } from '@allira/client';

export type AdminView = 'overview' | 'users' | 'conversations' | 'runs' | 'cdks' | 'usage' | 'routes' | 'diagnostics' | 'audit';
export type NoticeTone = 'success' | 'warning' | 'error' | 'info';
export interface AdminState {
  client: AlliraAdminClient;
  phase: Ref<'loading' | 'ready' | 'signingOut' | 'signedOut' | 'error'>;
  session: Ref<AdminSession | null>;
  epoch: Ref<number>;
  view: Ref<AdminView>;
  blockedView: Ref<AdminView | null>;
  mutating: Ref<boolean>;
  blocked: Ref<boolean>;
  error: Ref<string>;
  notification: Ref<{ text: string; tone: NoticeTone } | null>;
  restore(): Promise<void>;
  logout(): Promise<void>;
  notify(text: string, tone?: NoticeTone): void;
  fail(error: unknown, captured: number): void;
}
const key: InjectionKey<AdminState> = Symbol('Allira admin');
export function isAbort(error: unknown): boolean { return error instanceof Error && error.name === 'AbortError'; }
export function safeError(error: unknown): string {
  if (error instanceof AlliraApiError) {
    if (error.status === 401) return '管理登录已失效，请通过 Cloudflare Access 重新验证。';
    if (error.status === 403) return '没有权限执行此操作，请检查管理身份与 CSRF。';
    if (error.status === 409) return '资源当前状态不允许此操作，请刷新核对。';
    if (error.status === 422 || error.status === 400) return '提交内容不符合要求，请核对输入。';
    if (error.status === 429) return '请求过于频繁，请稍后手动重试。';
    if (error.status >= 500) return '服务暂时不可用，请手动刷新核对，不会自动重试。';
    return '请求未能完成，请手动刷新核对。';
  }
  return '网络连接失败或响应无效，请手动刷新核对，不会自动重试。';
}
export function provideAdmin(): AdminState {
  const phase = ref<AdminState['phase']['value']>('loading');
  const session = shallowRef<AdminSession | null>(null);
  const epoch = ref(0), view = ref<AdminView>('overview'), blockedView = ref<AdminView | null>(null);
  const mutating = ref(false), blocked = ref(false), error = ref('');
  const notification = shallowRef<AdminState['notification']['value']>(null);
  let authAbort = new AbortController();
  const client = new AlliraAdminClient({ baseUrl: location.origin, fetch: async (input, init) => {
    const requestEpoch = epoch.value;
    const response = await globalThis.fetch(input, init);
    if (response.status === 401 && requestEpoch === epoch.value && phase.value === 'ready') reset('signedOut');
    return response;
  } });
  const notify = (text: string, tone: NoticeTone = 'info') => { notification.value = { text, tone }; };
  function reset(next: AdminState['phase']['value']): void {
    ++epoch.value; authAbort.abort(); authAbort = new AbortController(); client.clear();
    session.value = null; phase.value = next; error.value = ''; notification.value = null;
    mutating.value = false; blocked.value = false; blockedView.value = null; view.value = 'overview';
  }
  function fail(problem: unknown, captured: number): void {
    if (captured !== epoch.value || isAbort(problem)) return;
    if (problem instanceof AlliraApiError && problem.status === 401) reset('signedOut');
    error.value = safeError(problem);
  }
  async function restore(): Promise<void> {
    if (phase.value === 'signingOut' || (phase.value === 'loading' && epoch.value > 0)) return;
    reset('loading'); const captured = epoch.value;
    try {
      const result = await client.getSession({ signal: authAbort.signal });
      if (captured !== epoch.value) return;
      if (result.authenticated !== true || result.authentication !== 'cloudflare-access' ||
        typeof result.identity?.email !== 'string' || !result.identity.email ||
        typeof result.csrfToken !== 'string' || !result.csrfToken ||
        typeof result.expiresAt !== 'string' || !(Date.parse(result.expiresAt) > Date.now())) {
        client.clear(); throw new TypeError('Invalid Access session');
      }
      session.value = result; phase.value = 'ready';
    } catch (problem) {
      if (captured !== epoch.value || isAbort(problem)) return;
      phase.value = problem instanceof AlliraApiError && problem.status === 401 ? 'signedOut' : 'error';
      fail(problem, captured);
    }
  }
  async function logout(): Promise<void> {
    if (phase.value !== 'ready') return;
    const ending = client.logout(); // SDK captures the old proof synchronously, then invalidates it.
    reset('signingOut'); const captured = epoch.value;
    notify('本地管理内容已清空，正在注销服务会话…');
    try {
      await ending;
      if (captured === epoch.value) location.assign('/cdn-cgi/access/logout');
    } catch {
      if (captured === epoch.value) { phase.value = 'signedOut'; notify('本地已退出，但服务端注销结果不确定。不会重试或自动跳转，请显式检查管理登录。', 'warning'); }
    }
  }
  const state: AdminState = { client, phase, session, epoch, view, blockedView, mutating, blocked, error, notification, restore, logout, notify, fail };
  provide(key, state);
  onScopeDispose(() => { reset('signedOut'); });
  return state;
}
export interface AdminViewContext {
  client: AlliraAdminClient;
  signal: AbortSignal;
  valid(): boolean;
  mutating: Ref<boolean>;
  blocked: Ref<boolean>;
  error: Ref<string>;
  notify(text: string, tone?: NoticeTone): void;
  fail(problem: unknown): void;
  mutate<T>(action: () => Promise<T>, success: (result: T) => void | Promise<void>): Promise<boolean>;
  reconcile(): void;
}
export function useAdminView(): AdminViewContext {
  const state = inject(key);
  if (!state) throw new Error('Admin context is required');
  const captured = state.epoch.value, capturedView = state.view.value;
  const controller = new AbortController();
  const valid = () => !controller.signal.aborted && state.epoch.value === captured && state.phase.value === 'ready';
  onScopeDispose(() => controller.abort());
  return {
    client: state.client, signal: controller.signal, valid, mutating: state.mutating, blocked: state.blocked, error: state.error,
    notify: (text, tone = 'info') => { if (valid()) state.notify(text, tone); },
    fail: problem => { if (valid()) state.fail(problem, captured); },
    reconcile: () => {
      if (valid() && !state.mutating.value && (state.blockedView.value === null || state.blockedView.value === capturedView || capturedView === 'audit')) {
        state.blocked.value = false; state.blockedView.value = null; state.error.value = '';
      }
    },
    async mutate(action, success) {
      if (!valid() || state.mutating.value || state.blocked.value) return false;
      if (!navigator.onLine) { state.notify('当前离线，管理修改已停用。', 'warning'); return false; }
      state.mutating.value = true; state.error.value = '';
      try {
        const result = await action();
        if (!valid()) {
          if (state.epoch.value === captured) {
            state.blocked.value = true; state.blockedView.value = capturedView;
            state.notify('已发送的操作在离开页面后返回。请显式刷新相关列表或审计核对结果，再解除管理修改锁定。', 'warning');
          }
          return false;
        }
        await success(result);
        return valid();
      } catch (problem) {
        if (state.epoch.value !== captured) return false;
        const uncertain = !(problem instanceof AlliraApiError) || problem.status >= 500;
        if (uncertain) { state.blocked.value = true; state.blockedView.value = capturedView; }
        if (!isAbort(problem)) state.fail(problem, captured);
        if (valid()) {
          if (uncertain) state.notify('操作结果不确定，已锁定管理修改。请显式刷新并核对相关列表或审计后重新确认；不会自动重发。', 'warning');
        }
        return false;
      } finally { if (state.epoch.value === captured) state.mutating.value = false; }
    },
  };
}
export function useAdminQuery<T>(loader: (cursor: string | undefined, signal: AbortSignal) => Promise<Page<T>>) {
  const ctx = useAdminView();
  const items = shallowRef<T[]>([]), loading = ref(false), error = ref(''), nextCursor = ref<string | null>(null);
  let version = 0, abort = new AbortController();
  function reset(): void {
    ++version; abort.abort(); abort = new AbortController();
    items.value = []; nextCursor.value = null; loading.value = false; error.value = '';
  }
  async function load(append: boolean): Promise<boolean> {
    if (!ctx.valid() || (append && (loading.value || !nextCursor.value))) return false;
    if (!append) reset();
    const captured = version, signal = abort.signal;
    loading.value = true; error.value = '';
    try {
      const page = await loader(append ? nextCursor.value ?? undefined : undefined, signal);
      if (!ctx.valid() || signal.aborted || captured !== version) return false;
      if (!page || !Array.isArray(page.items) || !(page.nextCursor === null || typeof page.nextCursor === 'string')) throw new TypeError('Invalid page response');
      items.value = append ? [...items.value, ...page.items] : page.items;
      nextCursor.value = page.nextCursor;
      return true;
    } catch (problem) {
      if (!ctx.valid() || signal.aborted || captured !== version || isAbort(problem)) return false;
      error.value = safeError(problem); ctx.fail(problem);
      return false;
    } finally { if (ctx.valid() && !signal.aborted && captured === version) loading.value = false; }
  }
  ctx.signal.addEventListener('abort', reset, { once: true });
  onScopeDispose(reset);
  return { items, loading, error, nextCursor, reset, invalidate: reset, loadMore: () => load(true), refresh: () => load(false) };
}
export function formatNumber(value: unknown): string { return typeof value === 'number' && Number.isFinite(value) ? new Intl.NumberFormat('zh-CN').format(value) : '未知'; }
export function formatDate(value: unknown): string {
  if (value === null) return '无';
  if (typeof value !== 'string' || !Number.isFinite(Date.parse(value))) return '未知';
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short', hour12: false }).format(new Date(value));
}
export function yesNo(value: unknown): string { return value === true ? '是' : value === false ? '否' : '未知'; }
