import { test } from 'node:test';
import assert from 'node:assert/strict';
import { AlliraApiError } from '../../../packages/client/src/index.ts';
import { identityError, identityUncertain, normalizePhone, socialAuthorizeUrl, validPhoneCode } from '../src/identity.ts';

test('phone normalization accepts only exact mainland ASCII numbers and six-digit proofs', () => {
  assert.equal(normalizePhone('13800138000'), '+8613800138000');
  assert.equal(normalizePhone('+8613800138000'), '+8613800138000');
  for (const value of [' 13800138000', '13800138000 ', '１３８００１３８０００', '138 0013 8000', '+1 2025550100', '12800138000', '13800138000x1', '+86+8613800138000', '1380013800', '138001380000', '13800138000\n']) assert.equal(normalizePhone(value), null, value);
  assert.equal(validPhoneCode('001234'), true);
  for (const value of ['１２３４５６', '12345', '1234567', '123 56', '123456\n']) assert.equal(validPhoneCode(value), false, value);
});

test('social navigation permits exact HTTPS provider endpoints and only WeChat standard fragment', () => {
  const origin = 'http://127.0.0.1:49183';
  assert.ok(socialAuthorizeUrl('https://open.weixin.qq.com/connect/qrconnect?state=opaque#wechat_redirect', 'wechat', false, origin));
  assert.ok(socialAuthorizeUrl('https://graph.qq.com/oauth2.0/authorize?state=opaque', 'qq', false, origin));
  for (const value of ['javascript:alert(1)', 'http://open.weixin.qq.com/connect/qrconnect', 'https://open.weixin.qq.com.evil.test/connect/qrconnect', 'https://evil.test/connect/qrconnect', 'https://u:p@open.weixin.qq.com/connect/qrconnect', 'https://open.weixin.qq.com:8443/connect/qrconnect', 'https://open.weixin.qq.com/connect/qrconnect/extra', 'https://open.weixin.qq.com/connect/qrconnect#token', 'https://graph.qq.com/oauth2.0/authorize']) assert.equal(socialAuthorizeUrl(value, 'wechat', false, origin), null, value);
  assert.equal(socialAuthorizeUrl('https://graph.qq.com/oauth2.0/authorize#wechat_redirect', 'qq', false, origin), null);
});

test('simulated social callback stays on exact current origin/provider route and never accepts fragments', () => {
  const origin = 'http://127.0.0.1:49183';
  assert.ok(socialAuthorizeUrl(`${origin}/api/v1/auth/social/wechat/callback?state=opaque&code=stub`, 'wechat', true, origin));
  for (const value of [`${origin}/api/v1/auth/social/qq/callback`, `${origin}/test/`, `${origin}/api/v1/auth/social/wechat/callback#code=x`, 'https://evil.test/api/v1/auth/social/wechat/callback']) assert.equal(socialAuthorizeUrl(value, 'wechat', true, origin), null, value);
});

test('identity errors are static and uncertain writes are never silently classified as safe to retry', () => {
  for (const status of [400, 401, 403, 409, 410, 422, 429, 503]) assert.doesNotMatch(identityError(new AlliraApiError(status, 'private-code', 'private-phone-credential')), /private/);
  assert.equal(identityUncertain(new AlliraApiError(409, 'private', 'private')), false);
  assert.equal(identityUncertain(new AlliraApiError(503, 'private', 'private')), true);
  assert.equal(identityUncertain(new TypeError('private-network')), true);
});
