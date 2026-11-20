import { AlliraApiError } from '../../../packages/client/src/index.ts';

/** The only trusted navigation origin is the server's configured issuer projection. */
export function oidcAuthorizationUrl(value: string, authorizationOrigin: string | null): string | null {
  if (!authorizationOrigin) return null;
  try {
    const allowed = new URL(authorizationOrigin);
    const url = new URL(value);
    if (!['http:', 'https:'].includes(allowed.protocol) || allowed.origin !== authorizationOrigin || allowed.username || allowed.password || allowed.search || allowed.hash || allowed.pathname !== '/') return null;
    return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password && !url.hash && url.origin === allowed.origin ? url.href : null;
  } catch { return null; }
}
export function oidcError(error: unknown): string {
  if (error instanceof AlliraApiError) {
    if (error.status === 400 || error.status === 401 || error.status === 410) return '本次认证已失效或已消费，请重新读取状态后主动重新登录。';
    if (error.status === 403) return '独立认证会话校验失败，请重新读取认证状态。';
    if (error.status === 409) return '账号关联或当前业务状态冲突；不会静默合并账号，请按页面提示确认。';
    if (error.status === 422) return '请检查成年、当前协议、身份确认或旧账号证明。';
    if (error.status === 429) return '认证操作过于频繁，请稍后再试。';
    if (error.status < 500) return '认证未完成，请重新读取状态后操作。';
  }
  return '认证服务或网络暂时不可用，提交结果可能不确定。不会自动重试，请手动读取状态。';
}
export const oidcUncertain = (error: unknown): boolean => !(error instanceof AlliraApiError && error.status < 500);
