import test from 'node:test';
import assert from 'node:assert/strict';
import { AlliraApiError } from '../../../packages/client/src/index.ts';
import { oidcAuthorizationUrl, oidcError, oidcUncertain } from '../src/oidc.ts';

const issuer = 'http://127.0.0.1:3001';
test('OIDC navigation only accepts the configured canonical issuer origin, not start URL claims', () => {
  assert.equal(oidcAuthorizationUrl(`${issuer}/oidc/auth?client_id=private&state=private`, issuer), `${issuer}/oidc/auth?client_id=private&state=private`);
  assert.equal(oidcAuthorizationUrl('https://auth.example/oidc/auth', 'https://auth.example'), 'https://auth.example/oidc/auth');
  for (const value of ['https://evil.invalid/auth', 'javascript:alert(1)', '//127.0.0.1:3001/auth', `${issuer}.evil.invalid/auth`, `${issuer}/auth#code=private`, 'http://name:secret@127.0.0.1:3001/auth', 'http://127.0.0.1:3002/auth', 'https://127.0.0.1:3001/auth']) assert.equal(oidcAuthorizationUrl(value, issuer), null);
  for (const origin of [null, '', `${issuer}/`, `${issuer}/path`, `${issuer}?trusted=1`, `${issuer}#fragment`, 'http://user@127.0.0.1:3001', 'ftp://127.0.0.1:3001']) assert.equal(oidcAuthorizationUrl(`${issuer}/auth`, origin), null);
});
test('OIDC errors are static Chinese and only network or server failures are uncertain', () => {
  for (const status of [400, 401, 403, 404, 409, 410, 422, 429, 500, 503]) {
    const error = new AlliraApiError(status, 'private-code', 'private-token-body-url');
    assert.doesNotMatch(oidcError(error), /private/);
    assert.equal(oidcUncertain(error), status >= 500);
  }
  assert.equal(oidcUncertain(new Error('private')), true);
  assert.doesNotMatch(oidcError(new Error('private')), /private/);
});
