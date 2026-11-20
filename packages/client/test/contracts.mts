import {
  AlliraClient, AlliraAdminClient, AlliraSocialClient, AlliraOidcClient,
  type AccountSession, type AccountUser, type AdminCdk, type AdminUsageOverview,
  type CdkSummary, type UsageOverview, type Character, type ConversationMemory,
  type EnergyWallet, type Wallet, type EnergyLedgerEntry, type RunUsage,
  type ImportPreview, type ImportCommitResult, type ImportSource,
  type IssueCdksResult, type Terms, type ModelRoutes, type Page,
  type CreateRunInput, type RunInput, type Run, type ApiInfo,
  type UpdateCharacterInput, type UpdateConversationMemoryInput,
  type UpdateConversationInput,
  type AccountDataExport, type AccountSessions, type AdminDiagnostics, type BrowserSession,
  type ConversationArchive, type ConversationExport, type ConversationSearchResult,
  type ForkConversationResult, type OkResult, type RecoveryCodesResult, type RecoveryStatus,
  type MemoryCardCategory, type MemoryCard, type MemoryCards, type CreateMemoryCardInput,
  type UpdateMemoryCardInput, type MemoryBlock, type MemoryBlockSource, type MemoryBlockListOptions,
  type UpdateMemoryBlockInput, type ContextPreviewInput, type ContextPreview, type ContextSection, type ContextMessage,
  type CompanionPresetId, type CompanionPreset, type CompanionPresets, type StartCompanionInput, type CompanionStart,
  type AuthOptions, type AccountIdentity, type PhoneChallenge, type PhonePurpose, type PhoneSecurityPurpose,
  type RequestPhoneChallengeInput, type RegisterPhoneInput, type LoginPhoneInput, type LoginPhonePasswordInput,
  type RequestBindPhoneChallengeInput, type BindPhoneInput, type RequestPhoneSecurityChallengeInput,
  type SetPasswordByPhoneInput, type EraseAccountByPhoneInput, type SocialClientOptions,
  type SocialProvider, type SocialPending, type SocialAuthSession, type SocialAuthStart, type SocialPhoneChallengeInput, type SocialBindPhoneInput,
  type AuthBackend, type OidcAvailability, type OidcClientOptions, type OidcPurpose, type OidcPending,
  type OidcSession, type OidcStart, type StartOidcInput, type CompleteOidcInput, type MigrateOidcInput, type EraseOidcInput, type OidcEraseOptions,
  type AdminSession, type AdminUser, type AdminUserListOptions, type AdminDeviceSession, type AdminRouteOverview, type RevokeSessionsResult, type ModelRoute,
} from '../dist/index.js';

// Compile-only fixtures. These declarations are never executed or sent anywhere.
declare const client: AlliraClient;
declare const admin: AlliraAdminClient;
const session: Promise<AccountSession> = client.register({ username: 'fake_user', password: 'fake-password', acceptedTermsVersion: '2026-11-19', isAdult: true });
const signedIn: Promise<AccountSession> = client.login({ username: 'fake_user', password: 'fake-password' });
const user: Promise<AccountUser> = client.getAccount();
const updated: Promise<AccountUser> = client.updateAccount({ displayName: 'Name' });
const characters: Promise<Page<Character>> = client.listCharacters({ cursor: 'cursor', limit: 1 });
const character: Promise<Character> = client.createCharacter({ name: 'Guide' });
const characterPatch: UpdateCharacterInput = { instructions: '' };
const memoryPatch: UpdateConversationMemoryInput = { characterId: null };
const memory: Promise<ConversationMemory> = client.updateConversationMemory('id', memoryPatch);
const summarized: Promise<ConversationMemory> = client.summarizeConversation('id');
const preview: Promise<ImportPreview> = client.previewImport({ format: 'markdown', content: 'Synthetic source' });
const imported: Promise<ImportCommitResult> = client.commitImport({ clientImportId: '11111111-1111-4111-8111-111111111111', format: 'text', content: 'Synthetic source', characterId: null });
const source: Promise<ImportSource> = client.getImportSource('id');
const wallet: Promise<EnergyWallet> = client.getEnergy();
const walletAlias: Promise<Wallet> = wallet;
const ledger: Promise<Page<EnergyLedgerEntry>> = client.listEnergyLedger();
const usage: Promise<RunUsage> = client.getRunUsage('id');
const codes: Promise<IssueCdksResult> = admin.issueCdks({ energy: 10, count: 1, expiresInDays: 7 });
const cdks: Promise<Page<AdminCdk>> = admin.listCdks();
const cdkAlias: Promise<Page<CdkSummary>> = cdks;
const overview: Promise<AdminUsageOverview> = admin.getUsageOverview();
const overviewAlias: Promise<UsageOverview> = overview;
const terms: Promise<Terms> = client.getTerms();
const routes: Promise<ModelRoutes> = client.getModelRoutes();
const legacyRunInput: CreateRunInput = { clientMessageId: 'id', content: 'Text' };
const runInput: RunInput = { ...legacyRunInput, mode: 'fast' };
const run: Promise<Run> = client.createRun('conversation', runInput);
const legacyInfo: ApiInfo = { environment: 'development', provider: 'mock', allowDevAuth: true, consoleEnabled: true, adminEnabled: true };
const legacyRun: Run = { id: 'run', conversationId: 'conversation', clientMessageId: 'client', userMessageId: 'user-message', assistantMessageId: 'assistant-message', status: 'running', errorCode: null, createdAt: '', updatedAt: '' };
const conversationPatch: UpdateConversationInput = { instructions: null };
const browser = new AlliraClient({ baseUrl: 'https://allira.example', browserSession: true });
const exchanged: Promise<BrowserSession> = client.exchangeBrowserSession();
const restored: Promise<BrowserSession> = browser.getBrowserSession();
const browserLogout: Promise<OkResult> = browser.logoutBrowserSession();
const cleared: void = browser.clearBrowserSession();
const recovery: Promise<RecoveryStatus> = browser.getRecoveryStatus();
const rotated: Promise<RecoveryCodesResult> = browser.rotateRecoveryCodes({ currentPassword: 'fake-password' });
const recovered: Promise<OkResult> = client.recoverAccount({ username: 'fake_user', recoveryCode: 'fake-recovery-code', newPassword: 'fake-password' });
const devices: Promise<AccountSessions> = browser.listAccountSessions();
const revokedDevice: Promise<OkResult> = browser.revokeAccountSession('device-id');
const found: Promise<Page<ConversationSearchResult>> = browser.searchConversations({ q: 'Sample', cursor: 'opaque', limit: 1 });
const exported: Promise<ConversationExport> = browser.exportConversation('id', { format: 'markdown' });
const forked: Promise<ForkConversationResult> = browser.forkConversation('id', { clientForkId: 'fork-id', throughMessageId: null, title: null });
const data: Promise<AccountDataExport> = browser.exportAccountData();
const archived: Promise<ConversationArchive[]> = data.then(result => result.conversations);
const archivedMessages: Promise<string[]> = archived.then(result => result.flatMap(item => item.messages.map(message => message.content)));
const erased: Promise<OkResult> = browser.eraseAccount({ currentPassword: 'fake-password', confirmation: 'DELETE MY ACCOUNT' });
const diagnostics: Promise<AdminDiagnostics> = admin.getDiagnostics();
const memoryCardCategory: MemoryCardCategory = 'boundary';
const newMemoryCard: CreateMemoryCardInput = { category: memoryCardCategory, title: 'Boundary', content: 'Approved fact', sourceMessageId: null, enabled: false };
const memoryCardPatch: UpdateMemoryCardInput = { enabled: false };
const sourceCardPatch: UpdateMemoryCardInput = { sourceMessageId: null };
const requestOptions = { signal: new AbortController().signal };
const memoryCards: Promise<MemoryCards> = browser.listMemoryCards('conversation', requestOptions);
const createdCard: Promise<MemoryCard> = browser.createMemoryCard('conversation', newMemoryCard, requestOptions);
const patchedCard: Promise<MemoryCard> = browser.updateMemoryCard('conversation', 'card', memoryCardPatch, requestOptions);
const removedCard: Promise<OkResult> = browser.deleteMemoryCard('conversation', 'card', requestOptions);
const memoryBlockOptions: MemoryBlockListOptions = { q: 'excerpt', cursor: 'opaque', limit: 5, ...requestOptions };
const memoryBlocks: Promise<Page<MemoryBlock>> = browser.listMemoryBlocks('conversation', memoryBlockOptions);
const blockPatch: UpdateMemoryBlockInput = { enabled: false };
const toggledBlock: Promise<MemoryBlock> = browser.updateMemoryBlock('conversation', 'block', blockPatch, requestOptions);
const memorySource: Promise<MemoryBlockSource> = browser.getMemoryBlockSource('conversation', 'block', requestOptions);
const previewInput: ContextPreviewInput = { content: 'Draft' };
const context: Promise<ContextPreview> = browser.previewContext('conversation', previewInput, requestOptions);
const previewSections: Promise<ContextSection[]> = context.then(result => result.sections);
const previewMessages: Promise<ContextMessage[]> = context.then(result => result.messages);
const legacyArchive: ConversationArchive = { format: 'allira.conversation.v1', conversation: { id: 'c', title: '', instructions: null, createdAt: '', updatedAt: '' }, character: null, memory: { conversationId: 'c', characterId: null, coreMemory: '', relationship: '', summary: '', summarizedThrough: 0, updatedAt: '' }, messages: [], importSource: null };
const currentArchive: ConversationArchive = { ...legacyArchive, memoryCards: [], memoryBlocks: [] };
const archiveCards: Promise<MemoryCard[]> = data.then(result => result.conversations.flatMap(item => item.memoryCards ?? []));
const archiveBlocks: Promise<MemoryBlock[]> = data.then(result => result.conversations.flatMap(item => item.memoryBlocks ?? []));
const companionPresetId: CompanionPresetId = 'listen';
const companionPreset: CompanionPreset = { id: companionPresetId, title: 'Listen', description: 'A synthetic preset', openingHint: 'A synthetic hint', defaultMode: 'economy' };
const companionPresets: Promise<CompanionPresets> = browser.getCompanionPresets(requestOptions);
const defaultCompanionPreset: Promise<'daily'> = companionPresets.then(result => result.defaultPresetId);
const setupInput: StartCompanionInput = { clientSetupId: 'bb6a43e2-2d4f-4d9e-ac2a-5c04f630b462', presetId: companionPresetId };
const companion: Promise<CompanionStart> = browser.startCompanionConversation(setupInput, requestOptions);
const defaultCompanion: Promise<CompanionStart> = client.startCompanionConversation({ clientSetupId: setupInput.clientSetupId });
const companionConversationId: Promise<string> = companion.then(result => result.conversation.id);
const companionReplay: Promise<boolean> = companion.then(result => result.replayed);
const phonePurpose: PhonePurpose = 'register';
const phoneChallengeInput: RequestPhoneChallengeInput = { phone: '+8613800000000', purpose: phonePurpose };
const authOptions: Promise<AuthOptions> = client.getAuthOptions(requestOptions);
const phoneChallenge: Promise<PhoneChallenge> = client.requestPhoneChallenge(phoneChallengeInput, requestOptions);
const phoneRegistration: RegisterPhoneInput = { challengeId: 'challenge', code: '001234', acceptedTermsVersion: '2026-11-19', isAdult: true };
const registeredPhone: Promise<AccountSession> = client.registerPhone(phoneRegistration, requestOptions);
const registeredPhoneWithPassword: Promise<AccountSession> = client.registerPhone({ ...phoneRegistration, password: 'fake-password', displayName: 'Name' });
const phoneLogin: LoginPhoneInput = { challengeId: 'challenge', code: '001234' };
const signedInPhone: Promise<AccountSession> = client.loginPhone(phoneLogin, requestOptions);
const phonePasswordLogin: LoginPhonePasswordInput = { phone: '+8613800000000', password: 'fake-password' };
const signedInPhonePassword: Promise<AccountSession> = client.loginPhonePassword(phonePasswordLogin, requestOptions);
const accountIdentity: Promise<AccountIdentity> = browser.getAccountIdentity(requestOptions);
const nullablePhone: Promise<string | null> = accountIdentity.then(result => result.phone);
const identityProvider: Promise<SocialProvider | undefined> = accountIdentity.then(result => result.social[0]?.provider);
const bindPhoneChallengeInput: RequestBindPhoneChallengeInput = { phone: '+8613800000000', currentPassword: 'fake-password' };
const bindPhoneChallenge: Promise<PhoneChallenge> = browser.requestBindPhoneChallenge(bindPhoneChallengeInput, requestOptions);
const bindPhoneInput: BindPhoneInput = { challengeId: 'challenge', code: '001234', currentPassword: 'fake-password' };
const boundPhone: Promise<AccountIdentity> = browser.bindPhone(bindPhoneInput, requestOptions);
const securityPurpose: PhoneSecurityPurpose = 'set_password';
const phoneSecurityInput: RequestPhoneSecurityChallengeInput = { purpose: securityPurpose };
const phoneSecurityChallenge: Promise<PhoneChallenge> = browser.requestPhoneSecurityChallenge(phoneSecurityInput, requestOptions);
const phonePasswordInput: SetPasswordByPhoneInput = { challengeId: 'challenge', code: '001234', newPassword: 'fake-password' };
const phonePasswordSet: Promise<OkResult> = browser.setPasswordByPhone(phonePasswordInput, requestOptions);
const phoneEraseInput: EraseAccountByPhoneInput = { challengeId: 'challenge', code: '001234', confirmation: 'DELETE MY ACCOUNT' };
const phoneAccountErased: Promise<OkResult> = browser.eraseAccountByPhone(phoneEraseInput, requestOptions);
declare const oldAccountArchive: AccountDataExport;
const nullIdentityArchive: AccountDataExport = { ...oldAccountArchive, identity: null };
const nestedIdentity: Promise<AccountIdentity | null | undefined> = data.then(result => result.identity);
const socialOptions: SocialClientOptions = { baseUrl: 'https://allira.example', fetch: globalThis.fetch };
const social = new AlliraSocialClient(socialOptions);
const socialProvider: SocialProvider = 'wechat';
const socialSession: Promise<SocialAuthSession> = social.getSession(requestOptions);
const socialPending: Promise<SocialPending | null> = socialSession.then(result => result.pending);
const socialStart: Promise<SocialAuthStart> = social.start(socialProvider, requestOptions);
const socialPhoneInput: SocialPhoneChallengeInput = { phone: '+8613800000000' };
const socialPhoneChallenge: Promise<PhoneChallenge> = social.requestPhoneChallenge(socialPhoneInput, requestOptions);
const socialBindInput: SocialBindPhoneInput = { ...phoneRegistration, confirmBind: true };
const socialBound: Promise<AccountSession> = social.bindPhone(socialBindInput, requestOptions);
const socialCompleted: Promise<AccountSession> = social.complete(requestOptions);
const socialCancelled: Promise<OkResult> = social.cancel(requestOptions);
const socialCleared: void = social.clearSession();
const authBackend: Promise<AuthBackend> = authOptions.then(result => result.backend);
const oidcAvailability: Promise<OidcAvailability> = authOptions.then(result => result.oidc);
const trustedAuthorizationOrigin: Promise<string | null> = authOptions.then(result => result.oidc.authorizationOrigin);
const oidcOptions: OidcClientOptions = { baseUrl: 'https://allira.example', fetch: globalThis.fetch };
const oidc = new AlliraOidcClient(oidcOptions);
const oidcSession: Promise<OidcSession> = oidc.getSession(requestOptions);
const oidcPending: Promise<OidcPending | null> = oidcSession.then(result => result.pending);
const oidcEnabled: Promise<true> = oidcSession.then(result => result.enabled);
const oidcPurpose: OidcPurpose = 'erase';
const oidcStartInput: StartOidcInput = { purpose: oidcPurpose };
const oidcEraseOptions: OidcEraseOptions = { ...requestOptions, browserCsrfToken: 'fixture-ordinary-csrf' };
const oidcStarted: Promise<OidcStart> = oidc.start(oidcStartInput, oidcEraseOptions);
const oidcDefaultStart: Promise<OidcStart> = oidc.start();
const oidcDefaultStartWithSignal: Promise<OidcStart> = oidc.start(undefined, requestOptions);
const oidcAuthorizationUrl: Promise<string> = oidcStarted.then(result => result.authorizationUrl);
const oidcCompleteInput: CompleteOidcInput = { acceptedTermsVersion: '2026-11-19', isAdult: true, confirmIdentity: true };
const oidcCompleted: Promise<AccountSession> = oidc.complete(oidcCompleteInput, requestOptions);
const oidcMigrationInput: MigrateOidcInput = { ...oidcCompleteInput, username: 'old_fixture', currentPassword: 'fixture-password' };
const oidcMigrated: Promise<AccountSession> = oidc.migrate(oidcMigrationInput, requestOptions);
const oidcEraseInput: EraseOidcInput = { confirmation: 'DELETE MY ACCOUNT' };
const oidcErased: Promise<OkResult> = oidc.erase(oidcEraseInput, oidcEraseOptions);
const oidcCancelled: Promise<OkResult> = oidc.cancel(requestOptions);
const oidcCleared: void = oidc.clear();
const phoneMigrationPurpose: OidcPurpose = 'phone_migration';
const phoneMigrationStartInput = { purpose: phoneMigrationPurpose } satisfies StartOidcInput;
const phoneMigrationStarted: Promise<OidcStart> = oidc.start(phoneMigrationStartInput, requestOptions);
const phoneMigrationWithoutOrdinarySession: Promise<OidcStart> = oidc.start({ purpose: 'phone_migration' });
const phoneMigrationPending: OidcPending = { provider: 'logto', maskedPhone: 'masked', expiresAt: '', purpose: 'phone_migration' };
const phoneMigrationCompleteInput: CompleteOidcInput = { ...oidcCompleteInput, confirmPhoneMigration: true };
const phoneMigrationCompleted: Promise<AccountSession> = oidc.complete(phoneMigrationCompleteInput, requestOptions);
const explicitlyUnconfirmedPhoneMigration: Promise<AccountSession> = oidc.complete({ ...oidcCompleteInput, confirmPhoneMigration: false });
const oldAdminUser: AdminUser = { userId: 'owner', conversationCount: 1, runCount: 2, activeSessions: 0 };
const currentAdminUser: AdminUser = { ...oldAdminUser, createdAt: '', accountType: 'registered', username: null, displayName: null, identityStatus: 'oidc', maskedPhone: null, phoneVerified: true, passwordEnabled: false, walletBalance: 0 };
const adminUserFilters: AdminUserListOptions = { ...requestOptions, q: 'literal_%', identity: 'oidc', cursor: 'opaque', limit: 1 };
const adminUsers: Promise<Page<AdminUser>> = admin.listUsers(adminUserFilters);
const unknownAdminIdentity: Promise<AdminUser['identityStatus'] | undefined> = adminUsers.then(result => result.items[0]?.identityStatus);
const adminSessions: Promise<Page<AdminDeviceSession>> = admin.listUserSessions('owner', { ...requestOptions, cursor: 'opaque', limit: 1 });
const adminSessionType: Promise<'bearer' | 'browser' | undefined> = adminSessions.then(result => result.items[0]?.type);
const adminRevoked: Promise<RevokeSessionsResult> = admin.revokeUserSession('owner', 'device', requestOptions);
const adminRoutes: Promise<AdminRouteOverview> = admin.getRoutes(requestOptions);
const adminPublicRouteShape: Promise<ModelRoute[]> = adminRoutes.then(result => result.items);
const configurationOnly: Promise<true> = adminRoutes.then(result => result.configurationOnly);
declare const adminRouteOverviewFixture: AdminRouteOverview;
const legacyAdminDiagnostics: AdminDiagnostics = { version: 'old', startedAt: '', uptimeSeconds: 1, schemaVersion: 4, database: { ok: true, journalMode: 'wal', databaseBytes: 0, walBytes: 0 }, runs: { running: 0, inFlight: 0, closing: 0, capacity: 1 }, sessions: { bearer: 0, browser: 0, admin: 0 }, backup: { configured: false, latestAt: null, ageSeconds: null, status: 'missing' }, warnings: [] };
const adminIdentityMetadata: NonNullable<AdminDiagnostics['identity']> = { backend: 'oidc', configured: true, enabled: false, simulated: false, linkedAccounts: 1, legacyAccounts: 1, developerUsers: 0, pendingFlows: 0, serviceProbe: 'not_performed', administration: 'separate' };
const adminIdentityBackupMetadata: NonNullable<AdminDiagnostics['identityBackup']> = { configured: true, latestAt: null, ageSeconds: null, status: 'missing', databaseBytes: null, restoreStatus: 'not_reported', restoreVerifiedAt: null, checksumVerified: false };
const currentAdminDiagnostics: AdminDiagnostics = { ...legacyAdminDiagnostics, identity: adminIdentityMetadata, identityBackup: adminIdentityBackupMetadata };
const adminCleared: void = admin.clear();
const legacyAdminSession: AdminSession = { authenticated: true, csrfToken: 'fixture-admin-csrf', expiresAt: '' };
const accessAdminSession: AdminSession = { ...legacyAdminSession, authentication: 'cloudflare-access', identity: { email: 'admin@example.invalid' }, logoutUrl: '/cdn-cgi/access/logout' };
const restoredAdminAccess: Promise<AdminSession> = admin.getSession(requestOptions);
const adminAccessEmail: Promise<string | undefined> = restoredAdminAccess.then(result => result.identity?.email);
const adminAccessMethod: Promise<'cloudflare-access' | undefined> = restoredAdminAccess.then(result => result.authentication);
const adminAccessLogout: Promise<'/cdn-cgi/access/logout' | undefined> = restoredAdminAccess.then(result => result.logoutUrl);
const loggedOutAdmin: Promise<OkResult> = admin.logout(requestOptions);

// @ts-expect-error Terms must be consciously accepted using the current version.
client.register({ username: 'fake_user', password: 'fake-password', isAdult: true });
// @ts-expect-error Register only supports the published terms version.
client.register({ username: 'fake_user', password: 'fake-password', acceptedTermsVersion: 'old', isAdult: true });
// @ts-expect-error Adult eligibility requires an explicit true value.
client.register({ username: 'fake_user', password: 'fake-password', acceptedTermsVersion: '2026-11-19', isAdult: false });
// @ts-expect-error Password exchanges require both current and new passwords.
client.changePassword({ newPassword: 'fake-password' });
// @ts-expect-error Unsupported run modes must not silently widen the contract.
client.createRun('conversation', { clientMessageId: 'id', content: 'Text', mode: 'turbo' });
// @ts-expect-error Character updates require at least one field.
const emptyCharacter: UpdateCharacterInput = {};
// @ts-expect-error Character description is an empty string, not null, when cleared.
client.updateCharacter('id', { description: null });
// @ts-expect-error Memory updates require at least one field.
const emptyMemory: UpdateConversationMemoryInput = {};
// @ts-expect-error Summary is server-managed rather than a memory patch input.
client.updateConversationMemory('id', { summary: 'Do not accept this field' });
// @ts-expect-error coreMemory is a string, not nullable.
client.updateConversationMemory('id', { coreMemory: null });
// @ts-expect-error Import format remains a closed union.
client.previewImport({ format: 'xml', content: 'Synthetic source' });
// @ts-expect-error Import commit requires its application-owned UUID.
client.commitImport({ format: 'text', content: 'Synthetic source' });
// @ts-expect-error Admin construction cannot accept any account or developer Bearer.
new AlliraAdminClient({ baseUrl: 'https://allira.example', accessToken: 'fake-token' });
// @ts-expect-error List-only CDK summaries must not promise plaintext codes.
declare const codeFromList: AdminCdk['code'];
// @ts-expect-error Conversation update retains its nonempty patch constraint.
const emptyConversation: UpdateConversationInput = {};
// @ts-expect-error Browser mode is an explicit boolean, not an authentication credential.
new AlliraClient({ baseUrl: 'https://allira.example', browserSession: 'cookie' });
// @ts-expect-error Account cookie mode cannot be applied to the independent admin client.
new AlliraAdminClient({ baseUrl: 'https://allira.example', browserSession: true });
// @ts-expect-error Exchange uses the configured Bearer callback, not a body or per-request token.
browser.exchangeBrowserSession({ accessToken: 'fake-token' });
// @ts-expect-error Browser session views do not contain a raw cookie or Bearer token.
declare const browserToken: BrowserSession['accessToken'];
// @ts-expect-error A recovery request requires a recovery code, not a preauthenticated session.
client.recoverAccount({ username: 'fake_user', newPassword: 'fake-password' });
// @ts-expect-error Recovery status does not return plaintext codes.
declare const statusCodes: RecoveryStatus['codes'];
// @ts-expect-error Device summaries do not contain raw tokens or hashes.
declare const deviceToken: AccountSessions['items'][number]['tokenHash'];
// @ts-expect-error Search requires the query, including the empty string for recent results.
client.searchConversations({ limit: 10 });
// @ts-expect-error Export format is JSON or Markdown only.
client.exportConversation('id', { format: 'text' });
// @ts-expect-error The application must provide a fork ID for explicit replay.
client.forkConversation('id', { throughMessageId: 'message-id' });
// @ts-expect-error Account erasure requires the exact confirmation phrase.
client.eraseAccount({ currentPassword: 'fake-password', confirmation: 'delete' });
// @ts-expect-error Admin backup diagnostics do not expose filesystem paths.
declare const backupPath: AdminDiagnostics['backup']['path'];
// @ts-expect-error Memory card categories remain a closed union rather than arbitrary instructions.
client.createMemoryCard('c', { category: 'system', title: 'Title', content: 'Text' });
// @ts-expect-error Memory cards require content as well as category and title.
client.createMemoryCard('c', { category: 'fact', title: 'Title' });
// @ts-expect-error Card updates require at least one defined field.
const emptyCardPatch: UpdateMemoryCardInput = {};
// @ts-expect-error Card provenance is an ID or null, not a list of arbitrary sources.
client.updateMemoryCard('c', 'card', { sourceMessageId: ['message'] });
// @ts-expect-error Card content can be cleared only according to server validation, not by null.
client.updateMemoryCard('c', 'card', { content: null });
// @ts-expect-error Card updates do not rewrite server-owned conversation ownership.
client.updateMemoryCard('c', 'card', { conversationId: 'other' });
// @ts-expect-error Block summaries are immutable through the toggle-only endpoint.
client.updateMemoryBlock('c', 'block', { summary: 'Changed' });
// @ts-expect-error Block toggling requires an enabled value.
client.updateMemoryBlock('c', 'block', {});
// @ts-expect-error Block enabled is boolean, not a string.
client.updateMemoryBlock('c', 'block', { enabled: 'false' });
// @ts-expect-error Preview requires the candidate current message content.
client.previewContext('c', {});
// @ts-expect-error Preview does not accept a model or start a Run.
client.previewContext('c', { content: 'Draft', model: 'other' });
// @ts-expect-error Preview measurement is characters, not model token accounting.
const tokenMeasurement: ContextPreview['measurement'] = 'tokens';
// @ts-expect-error Context messages exclude tool/HTML roles.
const toolContext: ContextMessage = { role: 'tool', content: 'Text' };
// @ts-expect-error Context section kinds are a closed contract.
const invalidSection: ContextSection = { kind: 'tools', characters: 1, messages: 1 };
// @ts-expect-error Traceable blocks distinguish extractive pairs and legacy summaries, not an LLM claim.
const semanticBlock: MemoryBlock['kind'] = 'llm_summary';
// @ts-expect-error Memory cards do not add required or arbitrary object fields to older archives.
const malformedArchive: ConversationArchive = { ...legacyArchive, memoryCards: {} };
// @ts-expect-error Companion preset IDs are the three published neutral choices only.
const arbitraryPreset: CompanionPresetId = 'custom';
// @ts-expect-error Setup requires an application-owned UUID for explicit idempotent retry.
client.startCompanionConversation({ presetId: 'daily' });
// @ts-expect-error A null preset is not the same input as omitting the defaulted field.
client.startCompanionConversation({ clientSetupId: 'id', presetId: null });
// @ts-expect-error Clients cannot replace server-controlled preset instructions through setup.
client.startCompanionConversation({ clientSetupId: 'id', instructions: 'Override' });
// @ts-expect-error Setup does not accept a Run mode or start generation.
client.startCompanionConversation({ clientSetupId: 'id', mode: 'fast' });
// @ts-expect-error The public preset endpoint accepts request options, not account credentials.
client.getCompanionPresets({ accessToken: 'fixture-token' });
// @ts-expect-error Preset defaults do not silently widen into a paid/fast mode.
const fastPreset: CompanionPreset = { ...companionPreset, defaultMode: 'fast' };
// @ts-expect-error The published default preset stays daily even though other choices exist.
const nonDailyDefault: CompanionPresets = { defaultPresetId: 'focus', items: [] };
// @ts-expect-error Setup returns a conversation and replay marker, not a Run or charge result.
declare const setupRun: CompanionStart['run'];
// @ts-expect-error Companion user methods are not available on the independent admin client.
admin.startCompanionConversation(setupInput);
// @ts-expect-error Public phone purposes exclude account security and social binding.
client.requestPhoneChallenge({ phone: '+8613800000000', purpose: 'erase' });
// @ts-expect-error Phone registration needs explicit current terms acceptance.
client.registerPhone({ challengeId: 'challenge', code: '001234', isAdult: true });
// @ts-expect-error Phone registration cannot implicitly confirm adulthood.
client.registerPhone({ ...phoneRegistration, isAdult: false });
// @ts-expect-error OTPs remain strings so leading zeros are not discarded.
client.loginPhone({ challengeId: 'challenge', code: 1234 });
// @ts-expect-error A challenge response never exposes the plaintext OTP.
declare const returnedOtp: PhoneChallenge['code'];
// @ts-expect-error Password phone login needs the password rather than a challenge.
client.loginPhonePassword({ phone: '+8613800000000', challengeId: 'challenge' });
// @ts-expect-error Legacy-account binding requires proof of its current password.
client.requestBindPhoneChallenge({ phone: '+8613800000000' });
// @ts-expect-error Binding completes with the challenge, code and current password.
client.bindPhone({ challengeId: 'challenge', code: '001234' });
// @ts-expect-error Security verification uses the server-owned phone, never a client override.
client.requestPhoneSecurityChallenge({ purpose: 'erase', phone: '+8613800000000' });
// @ts-expect-error Security challenge purposes cannot be widened to sign-in.
client.requestPhoneSecurityChallenge({ purpose: 'login' });
// @ts-expect-error Phone password changes need the new password.
client.setPasswordByPhone({ challengeId: 'challenge', code: '001234' });
// @ts-expect-error Phone erase needs the same deliberate exact confirmation phrase.
client.eraseAccountByPhone({ challengeId: 'challenge', code: '001234', confirmation: 'delete' });
// @ts-expect-error Identity metadata does not promise raw account credentials.
declare const identityToken: AccountIdentity['accessToken'];
// @ts-expect-error A new identity is not retrofitted as a required field on AccountUser.
declare const accountUserPhone: AccountUser['phone'];
// @ts-expect-error The social client never accepts ordinary or developer Bearer credentials.
new AlliraSocialClient({ baseUrl: 'https://allira.example', accessToken: 'fixture-token' });
// @ts-expect-error Ordinary browser mode does not initialize the independent transition client.
new AlliraSocialClient({ baseUrl: 'https://allira.example', browserSession: true });
// @ts-expect-error Social clients do not accept a caller-supplied CSRF override.
new AlliraSocialClient({ baseUrl: 'https://allira.example', csrfToken: 'fixture-csrf' });
// @ts-expect-error The provider union contains only the supported WeChat and QQ providers.
social.start('github');
// @ts-expect-error Social start request options contain only a signal, not authentication.
social.start('wechat', { accessToken: 'fixture-token' });
// @ts-expect-error Social challenge purpose is server-controlled by pending state.
social.requestPhoneChallenge({ phone: '+8613800000000', purpose: 'login' });
// @ts-expect-error Social binding must obtain explicit true confirmation.
social.bindPhone({ ...phoneRegistration, confirmBind: false });
// @ts-expect-error Social binding cannot silently omit confirmation.
social.bindPhone(phoneRegistration);
// @ts-expect-error Social binding still requires explicit accepted terms and adult status.
social.bindPhone({ challengeId: 'challenge', code: '001234', confirmBind: true });
// @ts-expect-error A social transition session is not an ordinary Bearer login result.
declare const socialRawToken: SocialAuthSession['accessToken'];
// @ts-expect-error Pending metadata cannot expose an external provider access token.
declare const providerRawToken: SocialPending['accessToken'];
// @ts-expect-error Pending social state alone cannot create conversations.
social.createConversation({});
// @ts-expect-error User phone methods do not grant access to the admin channel.
admin.registerPhone(phoneRegistration);
// @ts-expect-error Account identity identifiers remain the phone or legacy-username union.
const emailIdentity: AccountIdentity['primaryIdentifier'] = 'email';
// @ts-expect-error Archived identity is a DTO or null, not a raw credential string.
const tokenIdentityArchive: AccountDataExport = { ...oldAccountArchive, identity: 'fixture-token' };
// @ts-expect-error Social cancel/complete do not accept raw transition codes in request options.
social.complete({ code: 'fixture-code' });
// @ts-expect-error The OIDC transition client never accepts an ordinary/developer Bearer.
new AlliraOidcClient({ baseUrl: 'https://allira.example', accessToken: 'fixture-token' });
// @ts-expect-error Ordinary browser mode does not initialize independent OIDC CSRF.
new AlliraOidcClient({ baseUrl: 'https://allira.example', browserSession: true });
// @ts-expect-error Callers cannot inject an arbitrary OIDC CSRF token.
new AlliraOidcClient({ baseUrl: 'https://allira.example', csrfToken: 'fixture-csrf' });
// @ts-expect-error Configured auth backends are a closed union.
const unsupportedAuthBackend: AuthBackend = 'self-hosted';
// @ts-expect-error OIDC provider is specifically Logto, not a social provider.
const unsupportedOidcProvider: OidcPending['provider'] = 'wechat';
// @ts-expect-error A trusted authorization origin is metadata, not a boolean availability flag.
const invalidOidcOrigin: OidcAvailability['authorizationOrigin'] = true;
// @ts-expect-error OIDC start purposes exclude arbitrary actions.
oidc.start({ purpose: 'link' });
// @ts-expect-error The application does not send callback redirect URLs to start.
oidc.start({ purpose: 'login', redirectUri: 'https://untrusted.example' });
// @ts-expect-error OIDC write request options only accept an AbortSignal.
oidc.start({}, { accessToken: 'fixture-token' });
// @ts-expect-error Completion requires conscious current terms acceptance.
oidc.complete({ isAdult: true, confirmIdentity: true });
// @ts-expect-error Completion cannot accept an older terms version.
oidc.complete({ ...oidcCompleteInput, acceptedTermsVersion: 'old' });
// @ts-expect-error Adult status is an explicit true literal.
oidc.complete({ ...oidcCompleteInput, isAdult: false });
// @ts-expect-error Identity association requires explicit true consent.
oidc.complete({ ...oidcCompleteInput, confirmIdentity: false });
// @ts-expect-error Provider subject and phone are server-verified, never input overrides.
oidc.complete({ ...oidcCompleteInput, subject: 'fixture-subject' });
// @ts-expect-error Migration requires proof of the existing account password.
oidc.migrate({ ...oidcCompleteInput, username: 'old_fixture' });
// @ts-expect-error Migration requires the legacy username too.
oidc.migrate({ ...oidcCompleteInput, currentPassword: 'fixture-password' });
// @ts-expect-error Fresh erase does not accept an approximate confirmation phrase.
oidc.erase({ confirmation: 'delete' }, oidcEraseOptions);
// @ts-expect-error Fresh OIDC erase never accepts a legacy password override.
oidc.erase({ confirmation: 'DELETE MY ACCOUNT', currentPassword: 'fixture-password' }, oidcEraseOptions);
// @ts-expect-error Pending OIDC metadata is not ordinary account authentication.
declare const oidcRawToken: OidcSession['accessToken'];
// @ts-expect-error Pending metadata must not expose issuer raw tokens.
declare const oidcIssuerToken: OidcPending['idToken'];
// @ts-expect-error OIDC pending purposes remain explicit after compatibility normalization.
const invalidPendingPurpose: OidcPending = { provider: 'logto', maskedPhone: 'masked', expiresAt: '' };
// @ts-expect-error Disabled endpoints fail instead of returning a fake enabled=false session DTO.
const disabledOidcSession: OidcSession = { csrfToken: '', pending: null, enabled: false, simulated: false };
// @ts-expect-error OIDC callback is a browser top-level navigation, not an SDK exchange method.
oidc.callback({ code: 'fixture-code' });
// @ts-expect-error OIDC pending state cannot create conversations.
oidc.createConversation({});
// @ts-expect-error Independent admin clients cannot complete ordinary OIDC authentication.
admin.complete(oidcCompleteInput);
// @ts-expect-error Social and OIDC clients do not share protocol methods.
social.migrate(oidcMigrationInput);
// @ts-expect-error Erase-purpose start requires explicit ordinary browser CSRF in addition to OIDC CSRF.
oidc.start({ purpose: 'erase' });
// @ts-expect-error A plain AbortSignal does not prove the current ordinary browser session.
oidc.start({ purpose: 'erase' }, requestOptions);
// @ts-expect-error Final erase requires that explicit per-request ordinary CSRF too.
oidc.erase(oidcEraseInput);
// @ts-expect-error Erase options cannot omit the ordinary browser CSRF token.
oidc.erase(oidcEraseInput, requestOptions);
// @ts-expect-error Ordinary CSRF must be a string, not a callback or Bearer.
const invalidOidcEraseOptions: OidcEraseOptions = { browserCsrfToken: () => 'fixture' };
// @ts-expect-error Complete never accepts ordinary browser CSRF injection options.
oidc.complete(oidcCompleteInput, { browserCsrfToken: 'fixture' });
// @ts-expect-error Phone-only migration confirmation is a boolean, never a truthy string.
oidc.complete({ ...oidcCompleteInput, confirmPhoneMigration: 'true' });
// @ts-expect-error Null is not the optional boolean migration confirmation.
oidc.complete({ ...oidcCompleteInput, confirmPhoneMigration: null });
// @ts-expect-error Phone migration purpose does not permit client-supplied phone ownership.
oidc.start({ purpose: 'phone_migration', phone: '+8613800000000' });
// @ts-expect-error Pending ownership never permits overriding the account ID at completion.
oidc.complete({ ...phoneMigrationCompleteInput, userId: 'acct_override' });
// @ts-expect-error The published purpose uses its exact snake_case spelling.
const invalidPhoneMigrationPurpose: OidcPending['purpose'] = 'phoneMigration';
// @ts-expect-error The migration consent is not ordinary cookie or Bearer authentication.
oidc.complete(phoneMigrationCompleteInput, { accessToken: 'fixture-token' });
// @ts-expect-error Admin identities are a closed filter union.
admin.listUsers({ identity: 'phone' });
// @ts-expect-error Admin search is not a phone-number lookup endpoint.
admin.listUsers({ phone: '+8613800000000' });
// @ts-expect-error Query text remains literal text, not a numeric filter.
const numericAdminQuery: AdminUserListOptions = { q: 12 };
// @ts-expect-error Registered and developer are the only account categories.
const unsupportedAdminAccountType: AdminUser = { ...oldAdminUser, accountType: 'admin' };
// @ts-expect-error Identity status does not imply a new privileged account type.
const unsupportedAdminIdentity: AdminUser = { ...oldAdminUser, identityStatus: 'administrator' };
// @ts-expect-error A masked admin identity is not permission to expose the full phone.
declare const adminFullPhone: AdminUser['phone'];
// @ts-expect-error Verification flags are optional booleans, never truthy strings.
const invalidAdminVerified: AdminUser = { ...oldAdminUser, phoneVerified: 'true' };
// @ts-expect-error Device-session summaries do not expose raw credentials or hashes.
declare const adminDeviceToken: AdminDeviceSession['tokenHash'];
// @ts-expect-error Only ordinary bearer/browser devices appear here, not admin/OIDC pending sessions.
const adminDeviceKind: AdminDeviceSession['type'] = 'oidc';
// @ts-expect-error Device revocation requires both the owner and selected device ID.
admin.revokeUserSession('owner');
// @ts-expect-error Admin request options cannot inject account Bearer credentials.
admin.revokeUserSession('owner', 'device', { accessToken: 'fixture-token' });
// @ts-expect-error Admin CSRF is initialized privately, never supplied as a request override.
admin.revokeUserSession('owner', 'device', { csrfToken: 'fixture-token' });
// @ts-expect-error User-side clients do not acquire admin device access.
client.listUserSessions('owner');
// @ts-expect-error Route overview is configuration-only, not a successful provider probe.
const probedAdminRoutes: AdminRouteOverview = { ...adminRouteOverviewFixture, configurationOnly: false };
// @ts-expect-error Route requests cannot initiate a provider probe.
admin.getRoutes({ probe: true });
// @ts-expect-error Routes reuse the safe public shape, not private upstream URLs.
declare const adminUpstreamUrl: AdminRouteOverview['items'][number]['baseUrl'];
// @ts-expect-error Identity-service probes are explicitly not performed by diagnostics.
const identityProbeClaim: NonNullable<AdminDiagnostics['identity']> = { ...adminIdentityMetadata, serviceProbe: 'healthy' };
// @ts-expect-error Administration remains independent of the identity service.
const sharedIdentityAdministration: NonNullable<AdminDiagnostics['identity']> = { ...adminIdentityMetadata, administration: 'shared' };
// @ts-expect-error Identity diagnostics retain the standard legacy/OIDC backend union.
const unknownDiagnosticBackend: NonNullable<AdminDiagnostics['identity']> = { ...adminIdentityMetadata, backend: 'custom' };
// @ts-expect-error A saved restore report is not an instantaneous successful restore proof.
const restoreProofClaim: NonNullable<AdminDiagnostics['identityBackup']> = { ...adminIdentityBackupMetadata, restoreStatus: 'verified' };
// @ts-expect-error Diagnostics never claim to have checked backup checksums.
const checkedBackupClaim: NonNullable<AdminDiagnostics['identityBackup']> = { ...adminIdentityBackupMetadata, checksumVerified: true };
// @ts-expect-error Identity backup metadata contains no filesystem location.
declare const adminIdentityBackupPath: NonNullable<AdminDiagnostics['identityBackup']>['path'];
// @ts-expect-error Local clear does not accept a cookie or replacement CSRF.
admin.clear({ csrfToken: 'fixture-token' });
// @ts-expect-error Access metadata is a closed authentication marker, not ordinary account authentication.
const accountAdminMarker: AdminSession = { ...legacyAdminSession, authentication: 'oidc' };
// @ts-expect-error Access identity contains an email string, not a token or a truthy flag.
const invalidAccessEmail: AdminSession = { ...legacyAdminSession, identity: { email: true } };
// @ts-expect-error The published Access logout route is fixed, never an arbitrary redirect URL.
const arbitraryAdminLogout: AdminSession = { ...legacyAdminSession, logoutUrl: 'https://evil.invalid/logout' };
// @ts-expect-error The independent admin session never exposes a JWT assertion.
declare const rawAccessAssertion: AdminSession['accessToken'];
// @ts-expect-error Admin GET cannot be authorized by caller-supplied Access assertions.
admin.getSession({ assertion: 'fixture-assertion' });
