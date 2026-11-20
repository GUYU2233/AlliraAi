# @allira/client

A lean, framework-neutral TypeScript SDK for Allira's `/api/v1` HTTP and SSE API. No UI, runtime dependencies, EventSource, automatic retries, or automatic reconnection. Requires Fetch, ReadableStream, TextDecoder and AbortController (modern browsers or Node.js 20+). Bundlers can consume the ESM output and generated declarations.

## Build and test

Run these commands through SSH `hklite-lv4` as the unprivileged `allira` user. Local work is limited to lightweight source/document edits and explicit source synchronization; do not install, test, or build locally. Existing remote dependencies do not need to be reinstalled for source-only changes.

```sh
runuser -u allira -- sh -c '
  cd /opt/allira/workspace/packages/client
  export PATH=/opt/allira/tools/node24/bin:$PATH
  # Only when dependencies need installation:
  # npm ci --ignore-scripts
  npm test
  # Build only: npm run build
'
```

Tests use Node's built-in test runner and mocked Fetch responses, never a real model provider. `npm test` first compiles the TypeScript source, then also checks the published declarations against positive and negative compile-only fixtures. Credential/password/CDK fixtures are fake and are not logged. The committed npm lock pins the compiler; there are no runtime dependencies.

## Session and conversation

After building, the example below can run from this package directory as an ESM script. Consumers of the installed package import from `@allira/client` instead of `./dist/index.js`.

```js
import { AlliraClient } from './dist/index.js';

const baseUrl = 'http://localhost:8000'; // SDK appends /api/v1
const bootstrap = new AlliraClient({ baseUrl });
// Development only: the server must explicitly enable its dev session endpoint.
const session = await bootstrap.createDevSession({ userId: 'local-user' });
const client = new AlliraClient({
  baseUrl,
  accessToken: session.accessToken,
});
const conversation = await client.createConversation({
  title: 'First chat',
  instructions: null,
});
const firstPage = await client.listConversations({ limit: 20 });
if (firstPage.nextCursor !== null) {
  const nextPage = await client.listConversations({
    limit: 20,
    cursor: firstPage.nextCursor,
  });
  console.log(nextPage.items);
}
console.log(await client.getConversation(conversation.id));
console.log(await client.listMessages(conversation.id, { limit: 20 }));
```

`accessToken` accepts a string or a synchronous/asynchronous callback returning a token (or undefined). In default Bearer mode, the callback is evaluated separately for each protected request, including SSE. Public info, terms, model routes, registration, login, recovery and dev session creation never evaluate or attach it. Both public requests and default Bearer requests explicitly use `credentials: 'omit'`, so an ambient browser cookie cannot authenticate a missing or invalid Bearer. Credentials and refresh policies remain application-owned. An existing base URL ending in `/api/v1` is accepted without doubling the suffix. An optional `fetch` implementation can be injected for tests or runtime integration.

## Main identity entry: Logto OSS Hosted UI and OIDC

The current integration direction actually adopts **Logto OSS v1.46.0** ([MPL-2.0 source](https://github.com/logto-io/logto/tree/v1.46.0)) as the authentication service and Hosted UI, with **Authlib** as the server-side OIDC relying-party library. Logto is responsible for upstream sign-in/verification; Allira's backend owns the verified issuer/subject-to-immutable-business-account mapping, consent, legacy linking, local sessions and local data controls. This Fetch SDK is only protocol glue: it does not implement an issuer, PKCE, token validation, SMS or provider login itself. Keep upstream license/third-party notices with the deployment. A synthetic SDK fixture is not evidence that the real Logto service, SMS connectors or social platforms have passed integration acceptance; root performs that separate service validation.

`getAuthOptions(options?)` is public `GET /auth/options` with cookies/Bearer omitted. `AuthOptions` includes `backend: 'legacy' | 'oidc'`, `oidc { enabled, provider: 'logto', simulated, reason: string | null, authorizationOrigin: string | null }`, and the existing phone/social/legacy/signup metadata. `authorizationOrigin` is projected from the **fixed server issuer configuration**, not derived from `start()`'s result. When `backend === 'oidc'`, present the enabled Hosted UI action rather than the historical self-written phone/social/password/recovery flows. These historical fields remain for compatibility; OIDC mode disables those primary sign-in actions server-side. Missing new fields from an old server normalize to `backend: 'legacy'`, OIDC disabled/non-simulated, and a null origin. A missing origin on a partial new response also stays null; never infer trust from a returned authorization URL. Malformed metadata is rejected without printing the payload; HTTP errors remain `AlliraApiError`, not fabricated readiness.

```js
import { AlliraClient, AlliraOidcClient } from './dist/index.js';
const baseUrl = window.location.origin;
const publicApi = new AlliraClient({ baseUrl });
const oidc = new AlliraOidcClient({ baseUrl });
const availability = await publicApi.getAuthOptions();
if (availability.backend !== 'oidc' || !availability.oidc.enabled || !availability.oidc.authorizationOrigin) {
  throw new Error('Hosted sign-in is unavailable');
}
await oidc.getSession(); // Explicit GET; initializes only the independent OIDC transition cookie/CSRF.
// On an explicit sign-in action, never automatically retry this POST:
const { authorizationUrl } = await oidc.start(); // Default {}, or { purpose: 'login' }.
const target = new URL(authorizationUrl);
const trusted = new URL(availability.oidc.authorizationOrigin);
const allowLoopbackDevelopmentHttp = false; // Enable only for your explicit loopback deployment.
const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(target.hostname);
const safeProtocol = target.protocol === 'https:' ||
  (allowLoopbackDevelopmentHttp && loopback && target.protocol === 'http:');
if (!safeProtocol || target.username || target.password || target.hash ||
    availability.oidc.authorizationOrigin !== trusted.origin ||
    trusted.username || trusted.password || trusted.hash || target.origin !== trusted.origin) {
  throw new Error('Untrusted identity navigation');
}
window.location.assign(target.href); // Application navigation, never performed by the SDK.
```

The fixed backend callback is a **browser top-level GET**, not an SDK exchange method. After it returns to the fixed application page, call `oidc.getSession()` again. `OidcSession` is `{ csrfToken, pending: OidcPending | null, enabled: true, simulated }`, and pending is `{ provider: 'logto', maskedPhone, expiresAt, purpose: 'login' | 'erase' | 'phone_migration' }`; old pending without `purpose` normalizes to **login**, never erase or phone migration. Pending is not a normal account login. Only after a deliberate user action and current terms/adult/identity confirmation call `oidc.complete({ acceptedTermsVersion: '2026-11-19', isAdult: true, confirmIdentity: true })`. Existing legacy-account linking instead explicitly calls `oidc.migrate({ ...confirmations, username, currentPassword })`; it is not an automatic merge based on phone, display name or subject. Both return the unchanged caller-owned `AccountSession` and preserve the server's immutable business account/data rules. The SDK never installs its Bearer or signs in another instance. Use the existing **ordinary** `AlliraClient.exchangeBrowserSession()` explicitly to consume that Bearer into a browser account cookie, clear the short-lived application Bearer, and never fall back to it after exchange. If a completion/exchange result is uncertain, use explicit GET restoration and intended-account checks, never an automatic POST replay.

### Explicit migration of a historical passwordless phone account

An unmapped historical account with the **same issuer-verified phone** and `passwordEnabled === false` can preserve its immutable business account ID and existing data only after an explicit migration action. First call `oidc.getSession()`, then deliberately `oidc.start({ purpose: 'phone_migration' }, { signal })` and validate/navigate to the Hosted UI as above. After the browser callback, explicitly restore OIDC metadata, require `pending.purpose === 'phone_migration'`, and obtain separate current terms/adult/identity and phone-account-migration confirmation before calling `oidc.complete({ acceptedTermsVersion: '2026-11-19', isAdult: true, confirmIdentity: true, confirmPhoneMigration: true }, { signal })`. This purpose uses only the independent OIDC transition Cookie/CSRF; unlike erase, it does **not** require ordinary browser authentication or `browserCsrfToken`.

`CompleteOidcInput.confirmPhoneMigration?: boolean` is omitted by default (server default **false**); an explicit false remains false. It must never be silently inserted into ordinary login. The server rejects a login-purpose proof with that flag set, mismatched/unverified phone ownership, already-mapped owners and password-enabled account shortcuts. An existing `passwordEnabled === true` account still requires the explicit `migrate` route with its legacy username and current password. Eligible phone-only migration is atomic on the server, preserves the original account/data, and revokes old sessions; the returned `AccountSession` remains application-owned and needs the same explicit ordinary browser exchange. Refused or uncertain POSTs are not automatically retried or replaced by another migration route. **Fresh OIDC authentication is not proof that a real SMS was sent or received**: actual delivery depends on issuer connector configuration and separate service/channel acceptance; synthetic SDK fixtures do not verify it.

All six OIDC HTTP methods share `OidcClientOptions { baseUrl, fetch? }`: `getSession(options?)`, `start(input = {}, options?)`, `complete(input, options?)`, `migrate(input, options?)`, `erase(input, options)`, `cancel(options?)`; `clear(): void` is local only. Only erase-purpose `start` and `erase` require `OidcEraseOptions extends RequestOptions { browserCsrfToken: string }` from an explicitly restored ordinary browser session; it is not constructor state and is never retained by this client. They call only `/auth/oidc/{session,start,complete,migrate,erase,cancel}`. Every request uses `credentials: 'same-origin'`, `mode: 'same-origin'` and **no Authorization**. Writes require private-instance `X-Allira-OIDC-CSRF` plus base-origin `Origin` (browsers supply their actual Origin; Node/custom Fetch needs the explicit header). The server's HttpOnly transition cookie is scoped to `/api/v1/auth/oidc`; it is independent of ordinary, social and admin cookies/CSRF. Inject a cookie-aware `fetch` in Node. Before explicit `getSession()`, writes reject locally with `TypeError`; no implicit GET or POST is performed. Unknown/disabled endpoints propagate typed errors such as 404, `403 oidc_disabled`, `401 oidc_session_required`/`oidc_pending_required`, `409 identity_link_required`, `429 rate_limited` or `503 oidc_unavailable`; no fallback channel is silently opened.

Successful `complete`, `migrate`, `erase` and `cancel` consume/revoke the transition server cookie and clear matching local CSRF, bumping the version so old GET results cannot repopulate it. A new flow needs another explicit GET; a late old response must not clobber a newer distinct transition. `clear()` only clears this instance's memory/invalidates late restores, sends no HTTP and cannot remove HttpOnly cookies or mutate another client. Current 401s clear matching CSRF; 409/refused writes are not retried automatically. Application abort/auth/view guards are still required. State, nonce, PKCE, provider tokens, passwords, CSRF, pending identity and authorization URLs must never go into browser storage, logs, public cache or service-worker entries.

### Fresh OIDC proof for local-data erasure

This is **not** a Logto-global account deletion and **not** physical removal from retained backups. First use an ordinary authenticated **browser Cookie** session; a Bearer-only caller must explicitly exchange it before beginning this flow. After explicit local-data-erasure consent, call `oidc.getSession()` and explicitly restore the ordinary browser session with `browserClient.getBrowserSession({ signal })`; pass only that response's `csrfToken` into `oidc.start({ purpose: 'erase' }, { signal, browserCsrfToken })`, validate/navigation-check the URL as above, and let the browser callback finish. The server asks the issuer for fresh authentication and binds the verified issuer/subject, business user and exact ordinary session; it does not accept a client-supplied token/subject. After reloading both ordinary and OIDC sessions, require `pending.purpose === 'erase'` and the deliberate exact phrase, explicitly obtain fresh ordinary `getBrowserSession()` metadata again, then call `oidc.erase({ confirmation: 'DELETE MY ACCOUNT' }, { signal, browserCsrfToken: restored.csrfToken })`. Both erase POSTs send **ordinary `X-CSRF-Token` and independent `X-Allira-OIDC-CSRF`**, with cookies and same-origin Origin but never Authorization. Missing/empty ordinary CSRF rejects locally without a hidden GET/POST; it is ignored on login/complete/migrate/cancel, never cached for later erase, and must not be copied from a Bearer. Keep it only in the current abort/auth/view-scoped request variable and clear the reference afterwards. The SDK does not call or couple to an ordinary account client automatically. `complete`/`migrate` cannot use erase-purpose proof. `409 account_busy` leaves proof available for an explicit retry once work stops; do not auto-retry. Successful erase clears server account/transition cookies and live local business data; the application must clear **all** ordinary Bearer/browser CSRF and private state itself, because this independent SDK cannot mutate other instances. Historical protected backups and the upstream Logto account remain outside this local erasure guarantee.

## Historical legacy phone sign-in, identity and explicit binding

The following self-written phone APIs remain only for legacy compatibility, not the new Hosted UI primary entry. `AuthOptions` retains `phone { enabled, simulated, reason: string | null, countryCallingCodes }`, `social[] { provider: 'wechat' | 'qq', enabled, simulated, reason }`, `legacyPasswordEnabled` and `registrationEnabled`. Use explicit server availability before presenting actions; no simulated adapter proves real delivery, and no metadata GET starts authentication.

```js
const availability = await bootstrap.getAuthOptions();
const terms = await bootstrap.getTerms();
// Only after the user chooses registration and explicitly requests a verification code:
const challenge = await bootstrap.requestPhoneChallenge({ phone: enteredPhone, purpose: 'register' });
// 202 PhoneChallenge { challengeId, maskedPhone, expiresAt, resendAfterSeconds }; no plaintext code.
// Obtain current terms/adult confirmation before calling registerPhone:
const phoneSession = await bootstrap.registerPhone({
  challengeId: challenge.challengeId,
  code: enteredVerificationCode, // String: preserve all six digits, including leading zeros.
  acceptedTermsVersion: terms.version,
  isAdult: true,
  // password and displayName are optional; omitting password creates a passwordless account.
}); // 201 AccountSession, with the unchanged { accessToken, tokenType, user } shape.
// For sign-in, request purpose: 'login', then loginPhone({ challengeId, code }).
// Alternatively: loginPhonePassword({ phone: enteredPhone, password: enteredPassword }).
// The SDK never installs the returned Bearer, logs it, or exchanges it automatically.
```

These five methods (`getAuthOptions`, `requestPhoneChallenge`, `registerPhone`, `loginPhone`, `loginPhonePassword`) always omit cookies, Bearer and CSRF, including on a `browserSession: true` client, and never read the configured token callback. Keep an account Bearer in application memory, or explicitly consume it with the existing `exchangeBrowserSession()` flow below; do not fall back to it after exchange. `AccountUser`/`AccountSession` are unchanged, and an internal `user.username` is not the phone sign-in identifier.

The server accepts mainland-China ASCII mobile numbers (11 digits starting `1[3-9]`, optionally prefixed by `+86`) and six ASCII-digit verification-code strings. The SDK forwards strings without trimming, coercion or normalization. `PhoneChallenge` contains metadata only; use its server expiry/resend fields rather than assuming a timer proves a code remains valid. Codes are purpose-/subject-bound and single-use. An uncertain OTP submission or SMS request is not automatically replayed, and the SDK never obtains or displays simulated codes. The server remains authoritative for validation, throttling, disabled registration/SMS and credential conflicts. Treat `401 invalid_phone_code`, `409 phone_in_use`, `429 rate_limited`, `503 sms_unavailable` and other error envelopes as normal `AlliraApiError` results, not prompts to try another authentication channel.

`getAccountIdentity()` is a protected `GET /account/identity`, separate from legacy `getAccount()`. `AccountIdentity` contains `primaryIdentifier: 'phone' | 'legacy_username'`, nullable private `phone`/`maskedPhone`, `phoneVerified`, `requiresPhoneBinding`, `passwordEnabled`, and `social[] { provider, linkedAt }`; it never returns OTPs or provider credentials. Do not log or persist the full phone or identity response in browser storage/caches. Use `passwordEnabled` to avoid presenting password-required actions on a passwordless account; legacy recovery/password methods remain available unchanged for accounts that can use them.

For an already signed-in legacy account, explicitly call `requestBindPhoneChallenge({ phone, currentPassword })` (`POST /account/phone/challenges`, 202), then `bindPhone({ challengeId, code, currentPassword })` (`POST /account/phone/bind`) to return the updated `AccountIdentity`. Both use the selected ordinary account auth mode; browser POSTs still require its initialized memory-only CSRF and same-origin Origin. This is not public login or an automatic merge: server ownership/password/OTP checks decide whether binding is allowed. A successful bind preserves the current session and revokes other sessions; the SDK does not switch identities.

Passwordless registered accounts can explicitly request `requestPhoneSecurityChallenge({ purpose: 'set_password' | 'erase' })` at `POST /account/phone/security/challenges` (202). The phone comes from the authenticated account, never a client body override. Complete with `setPasswordByPhone({ challengeId, code, newPassword })` or destructive `eraseAccountByPhone({ challengeId, code, confirmation: 'DELETE MY ACCOUNT' })`, both returning `{ ok: true }`. Successful password change or erase revokes all ordinary sessions and clears this instance's browser CSRF; discard application-owned Bearer/private state and sign in again if appropriate. Erase still needs deliberate confirmation, may fail while account work is active, and does not remove retained backups. These methods never issue another OTP or retry a sensitive POST automatically.

## Historical independent WeChat / QQ transition client

`AlliraSocialClient` is retained unchanged for legacy deployments; it is not the new OIDC Hosted UI client and must not be silently used as an OIDC fallback. In OIDC mode use `AlliraOidcClient` above and let the configured open-source identity service handle its supported upstream providers.

```js
import { AlliraSocialClient } from './dist/index.js';
const social = new AlliraSocialClient({ baseUrl });
const transition = await social.getSession();
// SocialAuthSession { csrfToken, expiresAt, pending: SocialPending | null }
// On an explicit enabled-provider action only:
const authorization = await social.start('wechat'); // Or 'qq'.
// SocialAuthStart { authorizeUrl, expiresAt }. No navigation is performed by the SDK.
// The application must validate provider HTTPS origin/path (or its explicit loopback test
// allowlist) before a user-initiated navigation. Never navigate arbitrary URL schemes.
// After the server callback returns to the fixed test page, GET the pending metadata again:
const returned = await social.getSession();
// If returned.pending?.requiresPhoneBinding is true, ask for a phone and explicit binding:
const bindingChallenge = await social.requestPhoneChallenge({ phone: enteredPhone });
// After user verification plus current terms/adult/association confirmation:
const socialAccount = await social.bindPhone({
  challengeId: bindingChallenge.challengeId, code: enteredVerificationCode,
  acceptedTermsVersion: terms.version, isAdult: true, confirmBind: true,
  // Optional password/displayName; never silently create or overwrite a password.
}); // AccountSession, caller-owned and not automatically exchanged.
// An already linked verified-phone identity instead uses an explicit social.complete().
```

`AlliraSocialClient` accepts only `{ baseUrl, fetch? }` and is neither `AlliraClient` nor `AlliraAdminClient`. Every request is cookie-only with `credentials: 'same-origin'`, `mode: 'same-origin'`, and no `Authorization`; POSTs also send base-origin `Origin` plus the latest social `X-CSRF-Token` held only in private instance memory. Node requires an injected cookie-aware `fetch`, just as ordinary browser/admin sessions do. `getSession()` explicitly calls `GET /auth/social/session` to create/restore this independent transition cookie and cache CSRF. Before that call, writes reject locally with `TypeError` and never bootstrap implicitly. Ordinary/admin cookies or CSRF cannot initialize it.

`start(provider)` POSTs `{}` to `/auth/social/{provider}/start`; `requestPhoneChallenge({ phone })` POSTs only the phone to `/auth/social/phone/challenges` (202); `bindPhone(input)` POSTs only the published binding fields to `/auth/social/bind-phone`; `complete()` POSTs `{}` to `/auth/social/complete`. Pending metadata is `{ provider, displayName, expiresAt, requiresPhoneBinding }`, not an authenticated account: render it safely and do not enable chat from it. The server validates state, provider exchange, verified-phone ownership and explicit association; the SDK does not exchange callback codes or accept provider tokens from URLs. Callback navigation carries no account credentials to the application. `bindPhone`/`complete` return normal `AccountSession` only after success, consume pending identity on the server, and keep the transition CSRF valid for a future explicit start. They never alter an existing ordinary Bearer or create an admin session.

`cancel()` POSTs `{}` to `/auth/social/cancel`, revokes the server transition cookie, and clears matching local CSRF on success. `clearSession()` only clears local memory, makes pending GET results unable to reinitialize it, and sends no HTTP; it does not sign out an ordinary account or revoke the HttpOnly transition cookie. Current 401s clear matching CSRF, while stale GET/401/cancel responses cannot overwrite newer local session state. The application must still abort and scope-guard stale work on navigation/logout. Neither client persists phone inputs, OTPs, passwords, authorization URLs, CSRF or pending identities. No social POST is automatically retried or completed, and a disabled/simulated provider is not evidence of real WeChat/QQ integration or delivery readiness.

## Legacy username accounts, terms and password lifecycle

Legacy username login is a migration entry point for existing legacy deployments, not the default signup flow. New users follow the enabled **Logto Hosted UI**; OIDC migrations use the explicit `oidc.migrate(...)` proof/consent flow rather than reopening old password sign-in. The following compatibility example applies only when `backend === 'legacy'` and its actions are enabled. `register()` is retained but **disabled by default**: the legacy server permits `/auth/register` only in `development` with an explicit `legacy_registration_enabled=true` setting, and `registration_enabled` must also be true. `getAuthOptions().registrationEnabled` is a general signup gate, not permission to bypass OIDC or to enable legacy username registration.

```js
const accountSession = await bootstrap.login({
  username: enteredUsername,
  password: enteredPassword,
}); // Enabled legacy deployments only; current normal signup uses the Hosted UI.
// Keep credentials in application memory; never log them or persist them in browser storage.
let accountToken = accountSession.accessToken;
const accountClient = new AlliraClient({ baseUrl, accessToken: () => accountToken });
const user = await accountClient.getAccount();
// AccountUser { id, username, displayName, createdAt, termsVersion, isAdult: true }
await accountClient.updateAccount({ displayName: 'New display name' });
await accountClient.changePassword({
  currentPassword: enteredCurrentPassword,
  newPassword: enteredNewPassword,
});
accountToken = undefined; // Password change revokes ALL account sessions; sign in again.
// Alternatively, while signed in: await accountClient.logoutAll(); // { ok: true, revoked }
// Clear accountToken after successful logoutAll() or the existing logout().
```

When explicitly enabled for development compatibility only, `register` POSTs `/auth/register` (201); otherwise it fails with `registration_disabled` or `phone_registration_required`. Existing legacy accounts use `login`, which POSTs `/auth/login` (200). Both return `AccountSession { accessToken, tokenType: 'Bearer', user: AccountUser }` without implicitly updating a configured token. `getAccount`, `updateAccount`, `changePassword` and `logoutAll` use the client's selected user authentication mode; neither mode creates an admin session. Password change and logout-all revoke all account sessions on the server. The SDK cannot mutate an application's token storage or callback. The same client can use all protected conversation/character/memory/import/energy APIs below.

The server validates username/password policies and explicit current-version terms/adult acceptance. TypeScript makes the published registration version and `isAdult: true` explicit; it is not proof of age or a security boundary. Do not auto-accept terms, log passwords/session responses, or treat arbitrary development user IDs as account/admin identities. SDK validation is intentionally minimal; the server remains authoritative for validation, authorization, rate limits and conflicts. Use HTTPS outside loopback development.

## Same-origin browser account session

Use `browserSession: true` for a browser app on the same origin as the API. A registered account's OIDC completion/migration (or enabled legacy login/register) Bearer can be explicitly exchanged for the server's HttpOnly, host-only, `SameSite=Strict` account cookie. The exchange **consumes that Bearer** and rotates the previous cookie session on this browser; other devices keep their sessions. Development identities cannot exchange. For example, after login:

```js
const baseUrl = window.location.origin;
// accountSession is the caller-owned result of explicit OIDC complete()/migrate(),
// or an enabled legacy login. Do not automatically replay either authentication POST.
let exchangeToken = accountSession.accessToken;
const browserClient = new AlliraClient({
  baseUrl, browserSession: true, accessToken: () => exchangeToken,
});
try {
  const { user, expiresAt } = await browserClient.exchangeBrowserSession();
  // Update account UI in memory; do not log the session response.
} finally {
  exchangeToken = undefined; // Do not use that Bearer as a fallback or send it to logout.
}
const conversations = await browserClient.listConversations({ limit: 20 });
```

After reload, a new `AlliraClient({ baseUrl, browserSession: true })` calls `getBrowserSession()` to restore identity and CSRF from the existing cookie. These two session methods return `BrowserSession { user: AccountUser, csrfToken: string, expiresAt: string }`, never a raw cookie or Bearer. If the exchange result was lost, restore with this **GET** and verify the intended account before updating UI; do not automatically repeat the exchange POST. An unsuccessful restore does not trigger login or Bearer fallback.

In browser mode all ordinary protected REST and SSE requests use `credentials: 'same-origin'`, `mode: 'same-origin'`, and no `Authorization`. Protected POSTs also require the instance's memory-only `X-CSRF-Token` and send `Origin`. Call `exchangeBrowserSession()` or `getBrowserSession()` before a write, or it rejects locally with `TypeError`. Exchange is the explicit exception: it sends the configured Bearer, same-origin credentials and `Origin`, with no CSRF. The browser supplies its real `Origin`; the SDK sets the base URL's origin for Node/custom Fetch. Node's native Fetch has no cookie jar; a Node integration needs an explicitly supplied cookie-aware `fetch` to use these methods. Prefer default Bearer mode for non-browser clients.

`logoutBrowserSession()` revokes the cookie session; `clearBrowserSession()` only discards local CSRF and prevents a pending restore from caching it again. It does not send a request or remove the HttpOnly cookie. Applicable HTTP 401 responses and successful browser logout, password change, logout-all and erase invalidate cached CSRF. Application-owned credentials and UI still need clearing. Explicit browser session methods work even on a default client without changing its normal authentication mode. Keep account and admin clients independent, and never persist CSRF, Bearer, session response bodies, or private chat data in browser storage or a service worker cache.

## Legacy recovery codes and ordinary device sessions

Local password/recovery APIs below remain compatibility methods, not alternate entry points around OIDC. In OIDC mode follow the Hosted UI's supported credential/recovery flow and the backend's refusal errors; do not silently reopen these old actions. Device listing/revocation still applies to ordinary Allira sessions after successful OIDC exchange.

`getRecoveryStatus()` returns `RecoveryStatus { enabled, remainingCodes, updatedAt }`, where `updatedAt` can be null. `rotateRecoveryCodes({ currentPassword })` replaces all previous codes and returns eight plaintext recovery codes once as `RecoveryCodesResult { codes, createdAt }`. Show them only for explicit user handling; do not log them, cache them in the SDK, or persist them in web storage. This action requires a registered account and is not automatically retried if its result is lost.

`recoverAccount({ username, recoveryCode, newPassword })` is public (`POST /auth/recover`, no Bearer/cookie) and returns `{ ok: true }` without signing in. Recovery invalidates all old account sessions and recovery codes. Ordinary password change also invalidates old codes. Discard old local state and require a fresh login, then rotate a new set if wanted. Invalid credentials return `401 invalid_credentials`; attempts are rate-limited.

`listAccountSessions()` returns `{ items: AccountSessionInfo[] }`. Each entry includes `id`, `createdAt`, `lastSeenAt`, `expiresAt`, `label`, `current`, and `type: 'bearer' | 'browser'`, not the token or its hash. `revokeAccountSession(id)` returns `{ ok: true }` even for an unknown/foreign valid UUID, without exposing other accounts' devices. On revoking the current entry, sign out in the UI and discard local credentials; the SDK does not infer the current device from the opaque ID. `logoutAll()` remains the explicit all-device revoke.

## Private account data export and erasure

`exportAccountData()` returns `AccountDataExport` with `format: 'allira.account.v1'`, `exportedAt`, `user`, typed conversation archives, `characters`, nullable `wallet`, `ledger`, `usage` and `warnings`. New exports can additionally include `identity?: AccountIdentity | null`; it is optional for compatibility with older archives and can contain a private verified phone, but never OTPs or provider credentials. This is a sensitive private archive, not a restore package. Passwords, tokens, recovery codes, admin data and provider credentials are excluded. Export does not silently truncate; it can fail with 409 for an active conversation or 413 for size/record limits (32 MiB, 100,000 records and 1,000 conversations, plus per-conversation limits).

In OIDC mode use the fresh OIDC proof/local-data-erasure flow above, not a legacy password/OTP bypass. The compatibility method `eraseAccount({ currentPassword, confirmation: 'DELETE MY ACCOUNT' })` remains a separate, explicit, destructive operation for an eligible legacy registered account. Prompt for that exact confirmation, offer export first, and require a deliberate user action. The request fails with `409 account_busy` while owned generations or their cleanup are active; check status rather than retrying automatically. On success, sign out and clear private application state. Erasure revokes sessions and removes live account data; a non-identifying redemption marker prevents reusing an already redeemed CDK. Existing protected backups may retain older data until their configured retention expires. No SDK method uploads or logs exports, performs restore, or erases backups.

## One-action companion conversation setup

```js
const presets = await bootstrap.getCompanionPresets();
// CompanionPresets { defaultPresetId: 'daily', items: CompanionPreset[] }
// Each item: { id: 'daily' | 'listen' | 'focus', title, description, openingHint,
//              defaultMode: 'economy' }. Render display text safely.
const setupRequest = {
  clientSetupId: crypto.randomUUID(), // Keep this UUID and preset for an explicit retry.
  presetId: presets.defaultPresetId, // Optional; omission defaults to daily on the server.
};
const setup = await client.startCompanionConversation(setupRequest);
// 201 CompanionStart { conversation, presetId, replayed }
// Open setup.conversation in the chat UI; openingHint is UI guidance, not a stored message.
// Only the user's later explicit createRun(...) sends a message and starts generation.
```

`getCompanionPresets(options?)` is a public GET to `/companion/presets`: it never reads/sends a configured Bearer or ambient cookie, even on a browser-session client. `startCompanionConversation({ clientSetupId, presetId? }, options?)` is a protected POST to `/companion/conversations`, with the existing selected user auth mode and `{ signal? }`. Cookie mode still requires initialized in-memory CSRF plus same-origin Origin; Bearer mode omits cookies. Only those two body fields are sent; custom instructions, relationships, mode, content and credentials are not forwarded.

The three neutral presets are `daily`, `listen` and `focus`, with daily as the default and economy as their mode hint. Setup creates a conversation with server-owned instructions/relationship defaults, not a Character, message, Run or energy charge. It does not contact a model, automatically send the opening hint, or promise model-quality companionship. Existing manual conversation/character/memory APIs remain available unchanged.

Keep `clientSetupId` and the chosen preset stable for an explicit retry after an uncertain result. The server isolates replay by owner and UUID, rejects conflicting preset reuse with 409, and may replay the existing target while it has an active Run. A deleted target returns 404 instead of silently creating a replacement. Do not auto-repeat the POST or allocate a new UUID just because a response was lost; choose an explicit retry or refresh the user's conversation list. Normal errors and original abort/transport reasons propagate unchanged, and the SDK stores no setup IDs or private data.

## Run streaming, cancellation and explicit resume

```js
import { StreamInterruptedError } from './dist/index.js';

// Generate once per logical send. Reuse this exact UUID for any explicit retry.
const clientMessageId = crypto.randomUUID();
const run = await client.createRun(conversation.id, {
  clientMessageId,
  content: 'Hello!',
  mode: 'economy', // Optional: 'economy' | 'fast'; omission keeps the server default.
}); // The server returns 202 with the unchanged Run DTO; idempotent by clientMessageId.
const controller = new AbortController();
let lastProcessedId = 0;

async function consume(after) {
  for await (const event of client.streamRunEvents(run.id, {
    after,
    signal: controller.signal,
  })) {
    if (event.type === 'message.delta') {
      console.log(event.data.delta);
    } else if (event.type === 'run.started') {
      console.log(event.data.run.status);
    } else {
      // Terminal data is canonical: replace locally accumulated content with it.
      console.log(event.data.run, event.data.message);
    }
    // Advance only AFTER application processing succeeds.
    lastProcessedId = event.id;
  }
}

try {
  await consume(lastProcessedId);
} catch (error) {
  if (error instanceof StreamInterruptedError) {
    console.warn('Unconfirmed EOF; choose whether/when to resume', error.lastEventId);
    // Explicit application choice, not an SDK retry loop:
    await consume(lastProcessedId);
  } else {
    throw error; // Abort and transport errors retain their original identity.
  }
}
console.log(await client.getRun(run.id));
// Abort only stops this HTTP stream; it does NOT cancel server-side generation:
// controller.abort();
// To cancel generation explicitly:
// await client.cancelRun(run.id);
```

SSE uses Fetch, not EventSource. It uses the selected auth mode: `Authorization: Bearer …` and no cookies by default, or same-origin cookie without Bearer/CSRF in browser mode. Events expose `{ id: number, type, data }` with the wire names `run.started`, `message.delta`, `run.completed`, `run.failed`, and `run.cancelled`. IDs are decimal, safe positive integers increasing within a run. `after` must be a nonnegative safe integer and is sent as a query parameter (default `0`). Heartbeat comments are ignored. UTF-8 and line endings can span arbitrary network chunks. Multiline `data:` and LF/CRLF/CR framing are supported. A terminal event is yielded before the iterator completes and closes the connection; failure/cancellation are terminal events, not transport exceptions. Breaking out of iteration also releases the connection.

A clean EOF without a complete terminal event throws `StreamInterruptedError`; its `lastEventId` identifies the last parsed event, not necessarily the last event your application processed. Never infer completion from EOF or a partial final frame. Network failures propagate unchanged and may also warrant an explicit resume. Abort reasons propagate unchanged; no POST, token refresh, or reconnect is retried automatically.

SSE performs basic runtime validation before yielding: payloads must be objects, Runs/Messages must have the required field types and valid statuses, `run.started` must be running, and terminal Run/Message statuses must match the terminal event. Started and terminal `run.id` must equal the requested `runId`; terminal `message.runId`, `message.conversationId`, and `message.id` must match that Run and its assistant message. Deltas require string `messageId`/`delta`, retain one consistent message ID, and match the started/terminal assistant message when known. A resumed stream (`after > 0`) may begin with a delta or a terminal snapshot; receiving `run.started` first is not required. UTF-8 decoding is fatal, including incomplete multibyte characters at EOF. Malformed types, IDs, JSON, UTF-8, or inconsistent payloads raise `StreamProtocolError` with a generic message that does not include the raw payload. Successful HTTP JSON bodies remain typed contracts rather than runtime schema validation.

## HTTP failures and request options

```js
import { AlliraApiError } from './dist/index.js';

try {
  await client.getRun('missing-run', { signal: new AbortController().signal });
} catch (error) {
  if (error instanceof AlliraApiError) {
    console.error(error.status, error.code, error.message);
  } else {
    throw error;
  }
}
```

All network methods accept `signal` in their options. Forward-list methods accept `limit` and `cursor`; returned pages contain `items` and nullable `nextCursor`. `listRecentMessages` instead takes `{ before?, limit?, signal? }`. `streamRunEvents` takes `{ after?, signal? }`. HTTP error envelopes `{ error: { code, message } }` become `AlliraApiError` with the HTTP status; non-JSON error bodies use `http_error` and the status text. No SDK operation implicitly creates a dev session or sends a request to a model provider.

## Public info, session maintenance, and conversation history

```js
const info = await bootstrap.getInfo(); // Public; never reads or sends accessToken.
// { environment, provider, allowDevAuth, consoleEnabled, adminEnabled }
const identity = await client.getSession(); // { userId }
await client.updateConversation(conversation.id, { title: 'Renamed' });
await client.updateConversation(conversation.id, { instructions: null });
const state = await client.getConversationState(conversation.id);
// { conversation, activeRun: Run | null, lastSequence }

const newest = await client.listRecentMessages(conversation.id, { limit: 20 });
if (newest.nextCursor !== null) {
  const earlier = await client.listRecentMessages(conversation.id, {
    before: newest.nextCursor,
    limit: 20,
  });
  // Prepend earlier.items to newest.items; each page is already sequence-ascending.
}
// Only conversations without an active Run can be deleted:
await client.deleteConversation(conversation.id); // { ok: true }, or 409 run_in_progress
await client.logout(); // Revokes the selected user session; { ok: true }.
```

`updateConversation(id, input, options?)` requires at least one of `title` or `instructions` in both its TypeScript type and its runtime empty-input check. Omitted fields remain unchanged; `instructions: null` explicitly clears instructions. `getConversationState` allows the application to restore persisted history and discover the active Run without assuming the last message represents a terminal state. `listMessages` retains the original forward cursor contract. `listRecentMessages` returns the most recent window before an optional message ID, still in ascending sequence order; `nextCursor` is the first message ID of the returned page when older messages exist, otherwise null. The SDK does not reverse either list.

`logout()` does not mutate the application-owned token or token callback. The application should clear its own token and UI state after success. Development login still permits arbitrary development user identities when enabled; it is not an account registration/login flow and does not grant admin access.

## Conversation search, export and explicit fork

```js
const found = await client.searchConversations({ q: 'remembered words', limit: 20 });
// Page<ConversationSearchResult { conversation, matchedMessageId: string | null, snippet }>
const recent = await client.searchConversations({ q: '', limit: 20 });
const exported = await client.exportConversation(conversation.id, { format: 'json' });
// ConversationExport { format: 'json' | 'markdown', filename, content }
const forkRequest = {
  clientForkId: crypto.randomUUID(), // Keep exactly this UUID and parameters for an explicit retry.
  throughMessageId: completedAssistantMessage.id,
  title: 'Alternative branch',
};
const fork = await client.forkConversation(conversation.id, forkRequest);
// { conversation, replayed, copiedMessages }
```

`searchConversations({ q, cursor?, limit?, signal? })` searches only the current user's conversations. An empty query lists recent conversations; nonempty queries are bounded literal substring searches, not semantic/vector search. Queries are at most 200 characters, cursors are opaque and tied to the user/query; reuse the exact query for subsequent pages. Snippets are private untrusted text, not HTML. Search never reads other users' data or creates a conversation.

`exportConversation(id, { format: 'json' | 'markdown' }, options?)` is a GET returning a JSON envelope, not an automatic download. The SDK preserves the server's filename and content without parsing or executing it. JSON content is a `ConversationArchive` with marker `allira.conversation.v1`, conversation, nullable character, memory, messages, and nullable original import source. New server exports also include `memoryCards` and `memoryBlocks` arrays; the SDK keeps these two fields optional so older archives without them remain valid. Nested `AccountDataExport.conversations` uses the same archive type. Treat both formats as private data and prompt before storing/downloading. Active conversations return 409; oversized exports return 413 instead of truncating (16 MiB or 50,000 messages per conversation).

`forkConversation(id, { clientForkId, throughMessageId?, title? }, options?)` POSTs an application-owned UUID. Omit `throughMessageId` or pass null to copy the available completed pairs; a supplied point must be an eligible completed assistant message. The result reports the new conversation and copied-message count. Forking does not start generation or call a model. A replay must use the exact source, UUID and original parameters, including omitted versus explicit null values; conflicting reuse returns 409. It is not automatically retried. Memory/import provenance and ownership rules are server-controlled, and an active source cannot be forked.

## Characters, conversation memory and bounded summaries

```js
const character = await client.createCharacter({
  name: 'Guide', description: 'A patient companion', instructions: 'Use concise replies.',
});
const characters = await client.listCharacters({ limit: 20 }); // Page<Character>
await client.getCharacter(character.id);
await client.updateCharacter(character.id, { description: '' });
const memory = await client.updateConversationMemory(conversation.id, {
  characterId: character.id,
  coreMemory: 'A user-approved persistent fact',
  relationship: 'New acquaintances',
});
const restoredMemory = await client.getConversationMemory(conversation.id);
const summarizedMemory = await client.summarizeConversation(conversation.id);
// { conversationId, characterId: string | null, coreMemory, relationship,
//   summary, summarizedThrough: number, updatedAt }
await client.updateConversationMemory(conversation.id, { characterId: null }); // Unbind.
await client.deleteCharacter(character.id); // { ok: true }
```

`Character` contains `id`, `name`, string `description`/`instructions`, `createdAt` and `updatedAt`. `createCharacter` requires `name`; omitted description/instructions default to empty strings on the server. `updateCharacter` requires at least one of `name`, `description`, `instructions`; empty strings can clear the latter fields, but they are not nullable. Character listing uses the existing `Page<T>`/cursor contract.

Character binding belongs to per-conversation memory, not a new required field on `Conversation`. `updateConversationMemory` requires at least one of `characterId`, `coreMemory`, `relationship`; omitted values remain unchanged, `characterId: null` unbinds, and empty memory strings clear those values. Empty patches fail locally with `TypeError`. `summary`, `summarizedThrough` and timestamps are server-managed. `summarizeConversation` sends a bodyless POST to the memory summarize route and returns the full updated memory. Server-side summaries/context bounds are not a guarantee of model-quality recall; rule-based/Mock summaries must not be described as real-model quality validation.

## User-controlled memory cards, traceable blocks and context preview

```js
const cards = await client.listMemoryCards(conversation.id); // MemoryCards { items }, not Page<T>.
const card = await client.createMemoryCard(conversation.id, {
  category: 'preference', // fact | preference | boundary | relationship
  title: 'Tea',
  content: 'A user-approved preference for jasmine tea.',
  sourceMessageId: null, // Omit to use the server default; null explicitly has no linked message.
  enabled: true,
});
await client.updateMemoryCard(conversation.id, card.id, { enabled: false });
await client.updateMemoryCard(conversation.id, card.id, { sourceMessageId: null });
const notes = await client.listMemoryBlocks(conversation.id, { q: 'tea', limit: 20 });
// Page<MemoryBlock>; keep the exact q when following notes.nextCursor.
if (notes.items.length) {
  const note = notes.items[0];
  const original = await client.getMemoryBlockSource(conversation.id, note.id);
  // { block, messages: Message[], warnings }; show these as private, untrusted text.
  await client.updateMemoryBlock(conversation.id, note.id, { enabled: false });
}
const preview = await client.previewContext(conversation.id, {
  content: 'A draft current message; this does not send it.',
}, { signal: new AbortController().signal });
// ContextPreview: measurement='characters_not_tokens', characterLimit, totalCharacters,
// remainingCharacters, sections, messages, includedCardIds, includedBlockIds,
// pendingBlockIds, excludedCardCount, omittedBlockCount, omittedRecentPairCount, warnings.
await client.deleteMemoryCard(conversation.id, card.id); // Explicit removal; { ok: true }.
```

All eight memory-control methods use the selected protected user authentication mode, encode both conversation and card/block IDs, and accept `{ signal? }`. Card creation is `POST .../memory/cards` with a 201 `MemoryCard`; updates and deletion are explicit `/update` and `/delete` POSTs. `listMemoryCards` is an unpaginated `{ items: MemoryCard[] }`. `CreateMemoryCardInput` requires category/title/content, with optional nullable `sourceMessageId` and optional boolean `enabled`. `UpdateMemoryCardInput` requires at least one of those five fields, enforced in both declarations and the local empty-patch check. Omitted fields remain unchanged; `sourceMessageId: null` and `enabled: false` are sent rather than treated as missing. Creation/update bodies exclude server-managed IDs, ownership and timestamps. Cards are owner-controlled persistent information, not automatically extracted or approved facts. The server validates nonblank trimmed titles (1–100 characters), nonblank content (1–2,000 characters), valid UTF-8 and source ownership; limits include disabled cards (50 cards and 20,000 content characters per conversation). Limit errors are surfaced, not silently truncated.

`listMemoryBlocks(id, { q?, cursor?, limit?, signal? })` searches only block summaries using a bounded literal substring, not the complete original transcript, semantic retrieval or an LLM. `q` is at most 200 characters, ASCII case-insensitive without Unicode normalization; its opaque cursor is tied to the current user, conversation and exact query. Empty or omitted query lists blocks. Pages contain `items` and nullable `nextCursor`, at most 100 entries per page. `MemoryBlock` includes its `kind`, `sourceMessageIds`, sequence range, `summary`, `enabled` and creation time. `extractive_pair` is an excerpt of a completed user/assistant pair with exact source messages available through `getMemoryBlockSource`; `legacy_summary` does not have exact source messages and returns an empty `messages` array plus a warning. The SDK preserves the distinction and all warnings instead of fabricating provenance. `updateMemoryBlock` accepts only `{ enabled: boolean }`; it does not rewrite summary text or source IDs. Neither extractive blocks nor legacy summaries are proof of semantic/LLM compression or reliable model recall.

`previewContext(id, { content }, options?)` makes one `POST .../context/preview`. Although read-only computation, it remains a protected POST: browser cookie mode requires a loaded in-memory CSRF token and same-origin Origin just like other writes. It does not create a Run, write a message/memory block, reserve energy, invoke a model or automatically send the draft. The server applies current-message validation (nonblank, 1–20,000 characters and safety checks); active conversations refuse mutations/preview with `409 run_in_progress`, while GET inspection remains available. Mandatory context that exceeds its hard character budget returns `413 context_too_large`; whole optional recent pairs/history notes are omitted to stay within budget rather than splitting a pair or returning an over-budget preview. Error envelopes, including card limits, unsafe content and stale/foreign cursors, retain their normal `AlliraApiError` status/code. No operation is automatically retried.

`ContextPreview.messages` contains the actual constructed system/user/assistant prompt text, not just counts. `sections` breaks down `instructions`, `character`, `core`, `relationship`, `memory_cards`, `history_notes`, `recent_messages` and `current_message` by character/message counts; inclusion, pending and omission fields explain the selected inputs. `measurement: 'characters_not_tokens'` is explicitly not token usage, cost or model quality. A preview is a snapshot, not a reservation or a guarantee that later generation sees unchanged context. Treat preview/source/card text as sensitive: do not log, execute as HTML, persist in browser storage or put it into public PWA caches. The SDK performs no logging, storage or automatic download. The original `ConversationMemory` DTO and existing summarize method remain supported unchanged.

## History import and explicit replay

```js
const input = { format: 'markdown', content: importedText }; // json | markdown | text
const preview = await client.previewImport(input);
// { messages: [{ role: 'user' | 'assistant', content }], warnings, suggestedTitle, sourceHash }
// Let the application review warnings and plain-text preview before committing.
const importRequest = {
  ...input,
  clientImportId: crypto.randomUUID(), // Reuse this UUID with the same request on an explicit retry.
  title: preview.suggestedTitle,
  characterId: null,
  coreMemory: '',
  relationship: '',
};
const result = await client.commitImport(importRequest);
// { conversation, importedMessages, archivedMessages, replayed }
const original = await client.getImportSource(result.conversation.id);
// { format, content: original complete source, archivedMessages: number }
```

All import operations require the client's selected user authentication mode (default Bearer or explicit browser cookie). `commitImport` requires a caller-owned `clientImportId` UUID, `format` and `content`; title/characterId/coreMemory/relationship are optional. The SDK does not generate IDs, parse/modify source, discard warnings, automatically retry, or silently create multiple conversations. `importedMessages` counts messages actually added as paired user/assistant timeline entries; `archivedMessages` is the count of unpaired messages retained with the original source, not an array. Source retrieval returns the original full content. Explicit idempotent replays preserve the server's `replayed` result; validation, ownership, size limits and conflicting UUID errors remain server decisions. Imported text is untrusted: render it as text and do not execute it as HTML/instructions.

## Model routes, integer energy and usage

```js
const routes = await bootstrap.getModelRoutes(); // Public { items: ModelRoute[] }
// ModelRoute { mode, provider, configured, energyCost, fallbackTo: 'economy' | null }
const wallet = await client.getEnergy();
// EnergyWallet (also Wallet) { balance, unit: 'energy', economyCost, fastCost }
const ledger = await client.listEnergyLedger({ limit: 20 });
// Page<EnergyLedgerEntry { id, delta, balanceAfter, kind, referenceId, createdAt }>
await client.redeemEnergy({ code: enteredRedemptionCode }); // Returns the updated wallet.
const usage = await client.getRunUsage(run.id);
// { runId, requestedMode, actualMode, cost, state: 'reserved' | 'settled' | 'refunded',
//   promptCharacters, outputCharacters, firstTokenMs: number | null,
//   durationMs: number | null, createdAt, updatedAt }
```

`CreateRunInput` (also exported as `RunInput`) adds optional `mode: 'economy' | 'fast'`; existing inputs without mode and the `Run` DTO remain unchanged. Use routes/configuration for display, and `RunUsage.actualMode` to see whether the server chose a fallback; the SDK does not implement its own fallback or extra run requests. Ledger `kind` is an open string (for example `welcome`, `run.reserved`, `run.refunded`, `run.route_refunded`, `cdk.redeemed`); `referenceId` is a string. Pages and integer values are preserved as returned. Energy units are application credits, not currency/provider quotations. `promptCharacters`/`outputCharacters` are characters, not measured/billable tokens; latency can be null and is not a capacity/SLA claim. Keep redemption codes in memory; neither redemption nor run creation is automatically retried.

## Cloudflare Access admin session and independent Cookie + CSRF

```js
import { AlliraAdminClient } from './dist/index.js';

const admin = new AlliraAdminClient({ baseUrl });
// The Access-protected document has already passed the configured edge identity policy.
// GET is the sole normal initialization: the backend verifies the assertion and establishes
// its independent HttpOnly admin session + in-memory CSRF; no one-time code or user Bearer.
const adminSession = await admin.getSession();
// Optional metadata: authentication === 'cloudflare-access', identity?.email,
// logoutUrl === '/cdn-cgi/access/logout'. Absent metadata means unknown/unsupported.
const overview = await admin.overview();
const users = await admin.listUsers({ q: 'literal name', identity: 'oidc', limit: 20 });
const devices = await admin.listUserSessions(identity.userId, { limit: 20 });
// After the application confirms a selected device:
// await admin.revokeUserSession(identity.userId, devices.items[0].id); // { ok: true, revoked: 0 | 1 }
const routes = await admin.getRoutes(); // Configuration only, never an upstream probe.
const conversations = await admin.listConversations({ userId: identity.userId, limit: 20 });
const history = await admin.listMessages(conversation.id, { limit: 20 });
const runs = await admin.listRuns({ status: 'running', userId: identity.userId, limit: 20 });
const selectedRun = await admin.getRun(runs.items[0].id);
await admin.cancelRun(selectedRun.id);
await admin.revokeUserSessions(identity.userId); // { ok: true, revoked: number }
await admin.deleteConversation(conversation.id); // 409 run_in_progress if active.
const audit = await admin.listAudit({ limit: 20 });
await admin.logout();
```

`AlliraAdminClient` accepts only `{ baseUrl, fetch? }`, not a developer or account token. It never attaches `Authorization` or creates a dev session. Every admin request uses `credentials: 'same-origin'` and `mode: 'same-origin'`; browser applications must use a same-origin base URL for the HttpOnly session cookie. Node's native Fetch does not retain cookies: a Node integration must inject its own cookie-aware `fetch` if it needs an authenticated session. No cookie jar dependency is bundled. Management is independent of the ordinary account, Social/OIDC transition and upstream identity-service administration channels.

`getSession()` is the normal Access initialization. `AdminSession` retains required `{ authenticated: true, csrfToken: string, expiresAt: string }` and adds optional `authentication?: 'cloudflare-access'`, `identity?: { email: string }`, and `logoutUrl?: '/cdn-cgi/access/logout'`; old three-field servers remain compatible, without fabricated identity metadata. The SDK neither reads browser fragments nor manufactures, accepts or forwards an Access JWT/email assertion; assertion validation and allowlist authorization belong to the backend/edge, not these DTOs. CSRF stays only in the instance's private memory. Only the newest applicable initialization can update it. A GET refresh can retain an already known token so an explicit logout can still revoke that cookie. All other writes require cached `X-CSRF-Token` and otherwise reject locally with `TypeError`, without an implicit GET. Call `getSession()` explicitly after reload/new instance. A current-session HTTP 401 invalidates the cache; stale requests cannot clear a newer login/restore.

`login({ code })` remains only an explicit legacy/test-server compatibility call and POSTs the supplied code exactly once. Access deployments reject this exchange and an old Cookie without a valid assertion. The current admin page has no code/SSH-link login path and never invokes this method; failed Access GETs do not fall back to it. Do not use the historical CLI-link browser tests to validate Access deployment.

`logout()` captures its current proof, **immediately** invalidates the local cache and pending restorations, then makes its one explicit POST and returns `OkResult { ok: true }`. The SDK does not navigate or end the edge Access session. The application must begin this request before clearing local UI, then, only after a successful result in the same auth epoch, navigate to the fixed same-origin `/cdn-cgi/access/logout`; never trust a response-supplied URL, query parameter or old fragment as a destination. Failed/uncertain logout is not retried or reported as successful global logout. The cache stays invalidated even if that logout fails or is aborted; a late logout result cannot clear or navigate over a newer login. Invoke logout before discarding local state. `clear(): void` invalidates only this instance's CSRF and pending cache updates: it makes no HTTP request, does not revoke the HttpOnly cookie or sign out Logto globally, and cannot replace server logout. Late initialization calls still settle their original promises, so applications must also use their own view/auth epochs and abort signals before applying returned data. Every POST, including code exchange, sets `Origin` to `baseUrl`'s origin for Node; browsers enforce and automatically supply their real Origin header. All methods forward an optional `signal`. No automatic GET/POST retry, navigation, credential logging or browser storage is added.

Access-specific verification adds 16 synthetic-Fetch tests for GET-only initialization, optional/old metadata, independent credentials and CSRF, explicit test-only code compatibility, aborts, failures without retry/fallback, and late initialization/logout isolation. The final remote `npm test` passed all 452 tests (436 existing plus 16 Access cases), including strict published-declaration compilation with 149 negative contracts. These tests do not contact Cloudflare or a real API and do not establish that an edge policy, assertion/JWKS validation or a deployed logout route has passed actual acceptance; deployment testing remains the user's explicit follow-up.

Admin read methods are `overview`, `listUsers`, `listUserSessions`, `listConversations`, `listMessages`, `listRuns`, `getRun`, `listAudit`, `getRoutes`, and `getDiagnostics`. Lists use `{ cursor?, limit?, signal? }`, with `userId?` additionally supported for conversations and `status?: Run['status']` plus `userId?` for runs. Message pages retain chronological order. `AdminConversation` extends `Conversation` with `ownerId` and `messageCount`; `AdminRun` extends `Run` with `ownerId`. `AdminOverview` contains `users`, `conversations`, `messages`, per-status `runs` counts, `activeSessions`, `environment`, `provider`, and `maxConcurrentRuns`. `AdminAuditEntry` contains only `id`, `action`, `targetType`, nullable `targetId`, and `createdAt`—no chat bodies or keys.

`listUsers(options?: AdminUserListOptions)` adds `q?: string` and `identity?: 'all' | 'oidc' | 'legacy' | 'developer'`. The query is a literal substring of user ID, username or display name, **not a phone-number search**. Pass the cursor through unchanged and reset pagination when either filter changes; invalid cursors remain typed HTTP errors, not an empty page. The SDK preserves query text rather than adding wildcard or case normalization. `AdminUser` keeps the original required `userId`, `conversationCount`, `runCount`, `activeSessions`; new `createdAt`, `accountType`, nullable `username`/`displayName`, `identityStatus`, nullable `maskedPhone`, `phoneVerified`, `passwordEnabled` and nullable `walletBalance` are optional for old-server compatibility. Missing fields mean **unknown/unsupported**, not a developer account, zero balance or false verification. No full-phone, credential or identity-provider subject field is promised.

`listUserSessions(userId, options?: ListOptions)` returns `Page<AdminDeviceSession>` from the selected owner's ordinary bearer/browser sessions. Each entry contains only `id`, `createdAt`, `lastSeenAt`, `expiresAt`, `label`, and `type: 'bearer' | 'browser'`; no session token/hash, cookie or transition proof is returned. `revokeUserSession(userId, sessionId, options?)` sends one CSRF-protected POST after explicit application confirmation, returning `RevokeSessionsResult { ok: true, revoked: number }` with 0 or 1 for this owner. It cannot revoke another owner's device through a mismatched ID. Associated stale pending proofs may be invalidated for safety; this is local application revocation, **not global identity-service SSO logout**. IDs/cursors are encoded, not interpreted by the SDK.

Other admin writes include `revokeUserSessions(userId)`, `deleteConversation(id)`, `cancelRun(id)`, and `logout()`. Revocation invalidates current sessions but does **not** prevent the user from signing in again, including through the intentionally open development login when enabled. Admin configuration missing (404), no admin session (401), bad CSRF (403), and an active-Run delete conflict (409) remain normal `AlliraApiError` values; old servers lacking a new endpoint do not silently become empty/default results. No retries or authentication exchanges are hidden. Client-side types and UI visibility are not an authorization boundary: the backend independently authenticates and authorizes each admin operation.

### Configuration-only model routes

`getRoutes(options?)` GETs `/admin/routes` as `AdminRouteOverview { items: ModelRoute[], limits, configurationOnly: true }`. Items reuse the safe public `ModelRoute` shape (`mode`, `provider`, `configured`, `energyCost`, `fallbackTo`). Limits contain `runTimeoutSeconds`, `maxConcurrentRuns`, `maxPromptCharacters`, `maxOutputCharacters`, `upstreamMaxTokens`, and `runRatePerMinute`. This read reveals no upstream URL, model name or key, performs no real probe/generation/charge, and changes no configuration. A configured route is not evidence of upstream availability, real-model quality or capacity.

### Read-only admin diagnostics

`getDiagnostics(options?)` calls `GET /admin/diagnostics` through the independent admin cookie channel. It keeps the prior service `version`, `startedAt`, `uptimeSeconds`, numeric `schemaVersion`, bounded database health/byte counts, `runs { running, inFlight, closing, capacity }`, per-type active `sessions`, backup status and `warnings`. Database details include `ok`, `journalMode`, `databaseBytes`, `walBytes`, not paths or connection URLs. Business backup details remain `{ configured, latestAt: string | null, ageSeconds: number | null, status: 'missing' | 'recent' | 'stale' | 'invalid' }`. A backup older than 36 hours is stale.

New `identity?` reports `backend: 'legacy' | 'oidc'`, `configured`, `enabled`, `simulated`, counts `linkedAccounts`, `legacyAccounts`, `developerUsers`, `pendingFlows`, plus fixed `serviceProbe: 'not_performed'` and `administration: 'separate'`. New `identityBackup?` reports its own `configured`, nullable `latestAt`/`ageSeconds`/`databaseBytes`, the same four backup statuses, `restoreStatus: 'not_reported' | 'recorded' | 'invalid'`, nullable `restoreVerifiedAt`, and fixed `checksumVerified: false`. Either new section may be absent on older servers: the application must label it unavailable/unknown, not silently claim disabled or healthy. HTTP failures still reject rather than returning a fabricated old-server result.

These are bounded configuration/manifest/private-report metadata, **not** a live Logto/identity-service probe or present checksum/restore verification. `recorded` and `restoreVerifiedAt` describe an earlier private restore report only; they do not establish the current backup's integrity. No SDK method starts services, creates/restores backups, changes protected configuration, or proves cross-machine disaster recovery or physical erasure. Diagnostics contain no chat text, tokens, keys, full CDK codes, issuer/connector URLs or filesystem paths.

### Admin CDK issuance and character-count usage statistics

```js
// Requires explicit admin.getSession() above; account Bearer cannot authorize this.
const issued = await admin.issueCdks({ energy: 10, count: 2, expiresInDays: 7 });
// IssueCdksResult { items: IssuedCdk[] { code, prefix, energy, expiresAt } }
// Full codes are returned only by this explicit request. Do not log or persist them.
const summaries = await admin.listCdks({ limit: 20 });
// Page<AdminCdk> (also CdkSummary); no plaintext code field.
await admin.disableCdk(summaries.items[0].prefix); // { ok: true }
const usageOverview = await admin.getUsageOverview();
// AdminUsageOverview (also UsageOverview) { reservedEnergy, settledEnergy, refundedEnergy,
//   runs, promptCharacters, outputCharacters, firstTokenMsAverage: number | null,
//   measurement: 'characters_not_tokens' }
```

`issueCdks` requires integer energy; count and expiry days are optional server-validated defaults. `IssuedCdk` has `code`, `prefix`, `energy` and numeric `expiresAt` (Unix epoch seconds, **not** milliseconds). `AdminCdk`/`CdkSummary` has only `prefix`, `energy`, numeric `expiresAt`, nullable string `redeemedBy`/`redeemedAt`, boolean `disabled` and string `createdAt`. Listing cannot recover full plaintext redemption codes. Both issuance and disabling use the same cookie + latest in-memory CSRF + Origin transport as all other admin writes; they are never automatically retried. The SDK itself never displays, logs or stores credentials, issued codes, passwords or source content. Applications must enforce safe one-time code handling and discard sensitive in-memory values on logout. Usage overview reports integer application energy and character counts only, not token accounting, upstream prices, real-model quality or production throughput.
