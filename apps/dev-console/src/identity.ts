import { AlliraApiError } from '../../../packages/client/src/index.ts';
import type { SocialProvider } from '../../../packages/client/src/index.ts';

/** Only mainland +86 ASCII inputs are supported by the current server contract. */
export function normalizePhone(value: string): string | null {
  return (value.length === 11 || value.length === 14) && /^(?:\+86)?1[3-9][0-9]{9}$/.test(value) ? (value.startsWith('+86') ? value : `+86${value}`) : null;
}
export const validPhoneCode = (value: string): boolean => value.length === 6 && /^[0-9]{6}$/.test(value);

/** Server-provided URLs still cross a navigation trust boundary. Never accepts tokens from the page URL. */
export function socialAuthorizeUrl(value: string, provider: SocialProvider, simulated: boolean, origin: string): string | null {
  try {
    const url = new URL(value);
    if (url.username || url.password) return null;
    if (simulated) return !url.hash && url.origin === origin && url.pathname === `/api/v1/auth/social/${provider}/callback` ? url.href : null;
    if (url.hash && !(provider === 'wechat' && url.hash === '#wechat_redirect')) return null;
    const host = provider === 'wechat' ? 'open.weixin.qq.com' : 'graph.qq.com';
    const path = provider === 'wechat' ? '/connect/qrconnect' : '/oauth2.0/authorize';
    return url.protocol === 'https:' && !url.port && url.hostname === host && url.pathname === path ? url.href : null;
  } catch { return null; }
}
export function identityError(error: unknown): string {
  if (error instanceof AlliraApiError) {
    if (error.status === 400 || error.status === 401 || error.status === 410) return '验证码、授权或登录已失效，请重新验证；不要重复提交旧验证码。';
    if (error.status === 403) return '身份或同源会话校验失败，请刷新登录状态后重新操作。';
    if (error.status === 409) return '身份状态冲突，不会合并账号；请刷新状态，或取消后重新授权。';
    if (error.status === 422) return '请检查手机号、六位验证码、密码和协议选项。';
    if (error.status === 429) return '操作过于频繁，请稍后再试；验证码发送也有服务端限制。';
    if (error.status === 503) return '此登录通道暂未开通或暂时不可用，请使用其他已开通方式。';
    if (error.status < 500) return '身份操作未完成，请刷新状态后重新验证。';
  }
  return '网络或服务异常，提交结果可能不确定。不会自动重试，请重新检查状态后操作。';
}
export const identityUncertain = (error: unknown): boolean => !(error instanceof AlliraApiError && error.status < 500);
