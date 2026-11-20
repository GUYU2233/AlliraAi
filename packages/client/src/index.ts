/** Framework-neutral Allira v1 Fetch SDK. Requires a Fetch-capable runtime. */
export type AccessToken = string | (() => string | undefined | Promise<string | undefined>);
export interface ClientOptions {
  baseUrl: string;
  accessToken?: AccessToken;
  /** Use a same-origin account cookie for protected requests instead of Bearer. */
  browserSession?: boolean;
  fetch?: typeof globalThis.fetch;
}
/** OAuth transition cookies and CSRF are independent of ordinary and admin sessions. */
export interface SocialClientOptions {
  baseUrl: string;
  fetch?: typeof globalThis.fetch;
}
/** OIDC transition cookies and CSRF never share ordinary, social or admin credentials. */
export interface OidcClientOptions {
  baseUrl: string;
  fetch?: typeof globalThis.fetch;
}
/** Admin credentials are independent of developer Bearer sessions. */
export interface AdminClientOptions {
  baseUrl: string;
  fetch?: typeof globalThis.fetch;
}
export interface RequestOptions { signal?: AbortSignal }
export interface ListOptions extends RequestOptions { cursor?: string; limit?: number }
export interface RecentMessagesOptions extends RequestOptions { before?: string; limit?: number }
export interface Page<T> { items: T[]; nextCursor: string | null }
export type Environment = 'development' | 'production';
export type Provider = 'mock' | 'openai';
export interface ApiInfo {
  environment: Environment;
  provider: Provider;
  allowDevAuth: boolean;
  consoleEnabled: boolean;
  adminEnabled: boolean;
}
export interface DevSession { accessToken: string; tokenType: 'Bearer' }
export interface Session { userId: string }
export interface OkResult { ok: true }
export interface Conversation {
  id: string;
  title: string;
  instructions: string | null;
  createdAt: string;
  updatedAt: string;
}
export interface Message {
  id: string;
  conversationId: string;
  runId: string;
  sequence: number;
  role: 'user' | 'assistant';
  content: string;
  status: 'generating' | 'completed' | 'failed' | 'cancelled';
  createdAt: string;
}
export interface Run {
  id: string;
  conversationId: string;
  clientMessageId: string;
  userMessageId: string;
  assistantMessageId: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  errorCode: string | null;
  createdAt: string;
  updatedAt: string;
}
export interface ConversationState { conversation: Conversation; activeRun: Run | null; lastSequence: number }
export interface CreateConversationInput { title?: string; instructions?: string | null }
export type CompanionPresetId = 'daily' | 'listen' | 'focus';
export interface CompanionPreset {
  id: CompanionPresetId;
  title: string;
  description: string;
  openingHint: string;
  defaultMode: 'economy';
}
export interface CompanionPresets { defaultPresetId: 'daily'; items: CompanionPreset[] }
export interface StartCompanionInput { clientSetupId: string; presetId?: CompanionPresetId }
export interface CompanionStart { conversation: Conversation; presetId: CompanionPresetId; replayed: boolean }
/** At least one field must be supplied; null explicitly clears instructions. */
export type UpdateConversationInput =
  | { title: string; instructions?: string | null }
  | { title?: string; instructions: string | null };
export interface ConversationSearchOptions extends ListOptions { q: string }
export interface ConversationSearchResult { conversation: Conversation; matchedMessageId: string | null; snippet: string }
export type ConversationExportFormat = 'json' | 'markdown';
export interface ExportConversationInput { format: ConversationExportFormat }
export interface ConversationExport extends ExportConversationInput { filename: string; content: string }
/** The decoded JSON export; exportConversation returns its serialized content. */
export interface ConversationArchive {
  format: 'allira.conversation.v1';
  conversation: Conversation;
  character: Character | null;
  memory: ConversationMemory;
  messages: Message[];
  importSource: ImportSource | null;
  /** Optional for compatibility with archives created before traceable memory. */
  memoryCards?: MemoryCard[];
  memoryBlocks?: MemoryBlock[];
}
export interface ForkConversationInput { clientForkId: string; throughMessageId?: string | null; title?: string | null }
export interface ForkConversationResult { conversation: Conversation; replayed: boolean; copiedMessages: number }
export type RunMode = 'economy' | 'fast';
export interface CreateRunInput { clientMessageId: string; content: string; mode?: RunMode }
/** Alias for consumers naming the wire request RunInput; existing CreateRunInput stays supported. */
export type RunInput = CreateRunInput;
export interface StreamOptions extends RequestOptions { after?: number }
export type RunEvent =
  | { id: number; type: 'run.started'; data: { run: Run } }
  | { id: number; type: 'message.delta'; data: { messageId: string; delta: string } }
  | { id: number; type: 'run.completed' | 'run.failed' | 'run.cancelled'; data: { run: Run; message: Message } };

export interface Terms { version: '2026-11-19'; adultOnly: true; content: string }
export interface AccountUser {
  id: string;
  username: string;
  displayName: string;
  createdAt: string;
  termsVersion: string;
  isAdult: true;
}
export interface AccountSession { accessToken: string; tokenType: 'Bearer'; user: AccountUser }
export interface BrowserSession { user: AccountUser; csrfToken: string; expiresAt: string }
export interface RecoveryStatus { enabled: boolean; remainingCodes: number; updatedAt: string | null }
export interface RotateRecoveryCodesInput { currentPassword: string }
/** Plaintext recovery codes are returned only on explicit rotation. */
export interface RecoveryCodesResult { codes: string[]; createdAt: string }
export interface RecoverAccountInput { username: string; recoveryCode: string; newPassword: string }
export interface AccountSessionInfo {
  id: string;
  createdAt: string;
  lastSeenAt: string;
  expiresAt: string;
  label: string;
  current: boolean;
  type: 'bearer' | 'browser';
}
export interface AccountSessions { items: AccountSessionInfo[] }
export interface EraseAccountInput { currentPassword: string; confirmation: 'DELETE MY ACCOUNT' }
export interface RegisterInput {
  username: string;
  password: string;
  displayName?: string;
  acceptedTermsVersion: Terms['version'];
  isAdult: true;
}
export interface LoginInput { username: string; password: string }
export interface UpdateAccountInput { displayName: string }
export interface ChangePasswordInput { currentPassword: string; newPassword: string }
export type SocialProvider = 'wechat' | 'qq';
export type PhonePurpose = 'login' | 'register';
export type PhoneSecurityPurpose = 'set_password' | 'erase';
export interface PhoneChallenge { challengeId: string; maskedPhone: string; expiresAt: string; resendAfterSeconds: number }
export interface AccountIdentity {
  primaryIdentifier: 'phone' | 'legacy_username';
  phone: string | null;
  maskedPhone: string | null;
  phoneVerified: boolean;
  requiresPhoneBinding: boolean;
  passwordEnabled: boolean;
  social: { provider: SocialProvider; linkedAt: string }[];
}
export type AuthBackend = 'legacy' | 'oidc';
export interface OidcAvailability {
  enabled: boolean;
  provider: 'logto';
  simulated: boolean;
  reason: string | null;
  /** Trusted origin projected from server issuer configuration, not from start's returned URL. */
  authorizationOrigin: string | null;
}
export interface AuthOptions {
  backend: AuthBackend;
  oidc: OidcAvailability;
  phone: { enabled: boolean; simulated: boolean; reason: string | null; countryCallingCodes: string[] };
  social: { provider: SocialProvider; enabled: boolean; simulated: boolean; reason: string | null }[];
  legacyPasswordEnabled: boolean;
  registrationEnabled: boolean;
}
export interface RequestPhoneChallengeInput { phone: string; purpose: PhonePurpose }
export interface RegisterPhoneInput {
  challengeId: string;
  code: string;
  password?: string;
  displayName?: string;
  acceptedTermsVersion: Terms['version'];
  isAdult: true;
}
export interface LoginPhoneInput { challengeId: string; code: string }
export interface LoginPhonePasswordInput { phone: string; password: string }
export interface RequestBindPhoneChallengeInput { phone: string; currentPassword: string }
export interface BindPhoneInput { challengeId: string; code: string; currentPassword: string }
export interface RequestPhoneSecurityChallengeInput { purpose: PhoneSecurityPurpose }
export interface SetPasswordByPhoneInput { challengeId: string; code: string; newPassword: string }
export interface EraseAccountByPhoneInput { challengeId: string; code: string; confirmation: 'DELETE MY ACCOUNT' }
export interface SocialPending { provider: SocialProvider; displayName: string; expiresAt: string; requiresPhoneBinding: boolean }
export interface SocialAuthSession { csrfToken: string; expiresAt: string; pending: SocialPending | null }
export interface SocialAuthStart { authorizeUrl: string; expiresAt: string }
export interface SocialPhoneChallengeInput { phone: string }
export interface SocialBindPhoneInput {
  challengeId: string;
  code: string;
  password?: string;
  displayName?: string;
  acceptedTermsVersion: Terms['version'];
  isAdult: true;
  confirmBind: true;
}
export type OidcPurpose = 'login' | 'erase' | 'phone_migration';
export interface OidcPending { provider: 'logto'; maskedPhone: string; expiresAt: string; purpose: OidcPurpose }
/** Pending OIDC identity is not an ordinary account session. Disabled endpoints fail with an API error. */
export interface OidcSession { csrfToken: string; pending: OidcPending | null; enabled: true; simulated: boolean }
export interface OidcStart { authorizationUrl: string }
export interface StartOidcInput { purpose?: OidcPurpose }
export interface CompleteOidcInput {
  acceptedTermsVersion: Terms['version'];
  isAdult: true;
  confirmIdentity: true;
  confirmPhoneMigration?: boolean;
}
export interface MigrateOidcInput extends CompleteOidcInput { username: string; currentPassword: string }
export interface EraseOidcInput { confirmation: 'DELETE MY ACCOUNT' }
/** Explicit per-request ordinary browser CSRF proof. Never fetched or retained by the OIDC client. */
export interface OidcEraseOptions extends RequestOptions { browserCsrfToken: string }
export interface Character {
  id: string;
  name: string;
  description: string;
  instructions: string;
  createdAt: string;
  updatedAt: string;
}
export interface CreateCharacterInput { name: string; description?: string; instructions?: string }
type AtLeastOne<T> = { [K in keyof T]: Pick<T, K> & Partial<Omit<T, K>> }[keyof T];
export type UpdateCharacterInput = AtLeastOne<{ name: string; description: string; instructions: string }>;
export interface ConversationMemory {
  conversationId: string;
  characterId: string | null;
  coreMemory: string;
  relationship: string;
  summary: string;
  summarizedThrough: number;
  updatedAt: string;
}
export type UpdateConversationMemoryInput = AtLeastOne<{
  characterId: string | null;
  coreMemory: string;
  relationship: string;
}>;
export type MemoryCardCategory = 'fact' | 'preference' | 'boundary' | 'relationship';
export interface MemoryCard {
  id: string;
  conversationId: string;
  category: MemoryCardCategory;
  title: string;
  content: string;
  sourceMessageId: string | null;
  enabled: boolean;
  createdAt: string;
  updatedAt: string;
}
export interface CreateMemoryCardInput {
  category: MemoryCardCategory;
  title: string;
  content: string;
  sourceMessageId?: string | null;
  enabled?: boolean;
}
export type UpdateMemoryCardInput = AtLeastOne<{
  category: MemoryCardCategory;
  title: string;
  content: string;
  sourceMessageId: string | null;
  enabled: boolean;
}>;
export interface MemoryCards { items: MemoryCard[] }
export interface MemoryBlock {
  id: string;
  conversationId: string;
  kind: 'extractive_pair' | 'legacy_summary';
  sourceMessageIds: string[];
  startSequence: number;
  endSequence: number;
  summary: string;
  enabled: boolean;
  createdAt: string;
}
export interface MemoryBlockSource { block: MemoryBlock; messages: Message[]; warnings: string[] }
export interface MemoryBlockListOptions extends ListOptions { q?: string }
export interface UpdateMemoryBlockInput { enabled: boolean }
export interface ContextPreviewInput { content: string }
export interface ContextMessage { role: 'system' | 'user' | 'assistant'; content: string }
export interface ContextSection {
  kind: 'instructions' | 'character' | 'core' | 'relationship' | 'memory_cards' | 'history_notes' | 'recent_messages' | 'current_message';
  characters: number;
  messages: number;
}
export interface ContextPreview {
  conversationId: string;
  measurement: 'characters_not_tokens';
  characterLimit: number;
  totalCharacters: number;
  remainingCharacters: number;
  sections: ContextSection[];
  messages: ContextMessage[];
  includedCardIds: string[];
  includedBlockIds: string[];
  pendingBlockIds: string[];
  excludedCardCount: number;
  omittedBlockCount: number;
  omittedRecentPairCount: number;
  warnings: string[];
}
export type ImportFormat = 'json' | 'markdown' | 'text';
export interface ImportInput { format: ImportFormat; content: string }
export interface ImportedMessage { role: 'user' | 'assistant'; content: string }
export interface ImportPreview { messages: ImportedMessage[]; warnings: string[]; suggestedTitle: string; sourceHash: string }
export interface CommitImportInput extends ImportInput {
  clientImportId: string;
  title?: string;
  characterId?: string | null;
  coreMemory?: string;
  relationship?: string;
}
export interface ImportCommitResult {
  conversation: Conversation;
  importedMessages: number;
  archivedMessages: number;
  replayed: boolean;
}
export interface ImportSource extends ImportInput { archivedMessages: number }
export interface ModelRoute { mode: RunMode; provider: Provider; configured: boolean; energyCost: number; fallbackTo: 'economy' | null }
export interface ModelRoutes { items: ModelRoute[] }
export interface EnergyWallet { balance: number; unit: 'energy'; economyCost: number; fastCost: number }
export type Wallet = EnergyWallet;
export interface EnergyLedgerEntry { id: string; delta: number; balanceAfter: number; kind: string; referenceId: string; createdAt: string }
export interface RunUsage {
  runId: string;
  requestedMode: RunMode;
  actualMode: RunMode;
  cost: number;
  state: 'reserved' | 'settled' | 'refunded';
  promptCharacters: number;
  outputCharacters: number;
  firstTokenMs: number | null;
  durationMs: number | null;
  createdAt: string;
  updatedAt: string;
}
export interface AccountDataExport {
  format: 'allira.account.v1';
  exportedAt: string;
  user: AccountUser;
  conversations: ConversationArchive[];
  characters: Character[];
  wallet: Wallet | null;
  ledger: EnergyLedgerEntry[];
  usage: RunUsage[];
  warnings: string[];
  /** Optional for compatibility with exports created before phone/social identity. */
  identity?: AccountIdentity | null;
}
export interface IssueCdksInput { energy: number; count?: number; expiresInDays?: number }
/** Full redemption codes are returned only by explicit issuance, never by listing. */
export interface IssuedCdk { code: string; prefix: string; energy: number; expiresAt: number }
export interface IssueCdksResult { items: IssuedCdk[] }
export interface AdminCdk {
  prefix: string;
  energy: number;
  expiresAt: number;
  redeemedBy: string | null;
  redeemedAt: string | null;
  disabled: boolean;
  createdAt: string;
}
export type CdkSummary = AdminCdk;
export interface AdminUsageOverview {
  reservedEnergy: number;
  settledEnergy: number;
  refundedEnergy: number;
  runs: number;
  promptCharacters: number;
  outputCharacters: number;
  firstTokenMsAverage: number | null;
  measurement: 'characters_not_tokens';
}
export type UsageOverview = AdminUsageOverview;

export interface AdminSession {
  authenticated: true;
  csrfToken: string;
  expiresAt: string;
  /** Optional on explicit legacy/test servers; absence does not establish an Access identity. */
  authentication?: 'cloudflare-access';
  identity?: { email: string };
  /** Informational only. Applications must use their fixed Access logout route, never an arbitrary response URL. */
  logoutUrl?: '/cdn-cgi/access/logout';
}
export interface AdminOverview {
  users: number;
  conversations: number;
  messages: number;
  runs: { running: number; completed: number; failed: number; cancelled: number };
  activeSessions: number;
  environment: Environment;
  provider: Provider;
  maxConcurrentRuns: number;
}
export interface AdminUser {
  userId: string;
  conversationCount: number;
  runCount: number;
  activeSessions: number;
  /** Absent on older servers; absence is unknown, not a developer or unverified default. */
  createdAt?: string;
  accountType?: 'registered' | 'developer';
  username?: string | null;
  displayName?: string | null;
  identityStatus?: 'oidc' | 'legacy' | 'developer';
  maskedPhone?: string | null;
  phoneVerified?: boolean;
  passwordEnabled?: boolean;
  walletBalance?: number | null;
}
export interface AdminUserListOptions extends ListOptions { q?: string; identity?: 'all' | 'oidc' | 'legacy' | 'developer' }
export interface AdminDeviceSession {
  id: string;
  createdAt: string;
  lastSeenAt: string;
  expiresAt: string;
  label: string;
  type: 'bearer' | 'browser';
}
export interface AdminRouteOverview {
  items: ModelRoute[];
  limits: {
    runTimeoutSeconds: number;
    maxConcurrentRuns: number;
    maxPromptCharacters: number;
    maxOutputCharacters: number;
    upstreamMaxTokens: number;
    runRatePerMinute: number;
  };
  configurationOnly: true;
}
export interface RevokeSessionsResult extends OkResult { revoked: number }
export interface AdminConversation extends Conversation { ownerId: string; messageCount: number }
export interface AdminRun extends Run { ownerId: string }
export interface AdminAuditEntry { id: string; action: string; targetType: string; targetId: string | null; createdAt: string }
export interface AdminDiagnostics {
  version: string;
  startedAt: string;
  uptimeSeconds: number;
  schemaVersion: number;
  database: { ok: boolean; journalMode: string; databaseBytes: number; walBytes: number };
  runs: { running: number; inFlight: number; closing: number; capacity: number };
  sessions: { bearer: number; browser: number; admin: number };
  backup: { configured: boolean; latestAt: string | null; ageSeconds: number | null; status: 'missing' | 'recent' | 'stale' | 'invalid' };
  /** Optional on older servers; these are configuration/metadata, not an identity-service probe. */
  identity?: {
    backend: AuthBackend;
    configured: boolean;
    enabled: boolean;
    simulated: boolean;
    linkedAccounts: number;
    legacyAccounts: number;
    developerUsers: number;
    pendingFlows: number;
    serviceProbe: 'not_performed';
    administration: 'separate';
  };
  identityBackup?: {
    configured: boolean;
    latestAt: string | null;
    ageSeconds: number | null;
    status: 'missing' | 'recent' | 'stale' | 'invalid';
    databaseBytes: number | null;
    restoreStatus: 'not_reported' | 'recorded' | 'invalid';
    restoreVerifiedAt: string | null;
    checksumVerified: false;
  };
  warnings: string[];
}
export interface AdminConversationListOptions extends ListOptions { userId?: string }
export interface AdminRunListOptions extends ListOptions { status?: Run['status']; userId?: string }

export class AlliraApiError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'AlliraApiError';
    this.status = status;
    this.code = code;
  }
}
/** The connection ended without a canonical terminal event. Resume explicitly. */
export class StreamInterruptedError extends Error {
  constructor(readonly lastEventId: number) {
    super(`Run event stream ended without a terminal event (last event id: ${lastEventId})`);
    this.name = 'StreamInterruptedError';
  }
}
export class StreamProtocolError extends Error {
  constructor(message: string) { super(message); this.name = 'StreamProtocolError'; }
}

const terminalTypes = new Set(['run.completed', 'run.failed', 'run.cancelled']);
const eventTypes = new Set(['run.started', 'message.delta', ...terminalTypes]);

function apiBaseUrl(baseUrl: string): string {
  const url = new URL(baseUrl);
  if (!['http:', 'https:'].includes(url.protocol) || url.search || url.hash) {
    throw new TypeError('baseUrl must be an HTTP(S) URL without query or fragment');
  }
  const path = url.pathname.replace(/\/+$/, '');
  url.pathname = path.endsWith('/api/v1') ? path : `${path}/api/v1`;
  return url.toString().replace(/\/$/, '');
}
function queryPath(path: string, values: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) query.set(key, String(value));
  }
  return query.size ? `${path}?${query}` : path;
}
function listPath(path: string, options: ListOptions, filters: Record<string, string | undefined> = {}): string {
  return queryPath(path, { cursor: options.cursor, limit: options.limit, ...filters });
}
async function checkResponse(response: Response, options: RequestOptions): Promise<Response> {
  if (!response.ok) {
    let code = 'http_error';
    let message = response.statusText || `HTTP ${response.status}`;
    try {
      const envelope = await response.json() as { error?: { code?: unknown; message?: unknown } };
      if (typeof envelope?.error?.code === 'string') code = envelope.error.code;
      if (typeof envelope?.error?.message === 'string') message = envelope.error.message;
    } catch (error) {
      options.signal?.throwIfAborted();
      if (error instanceof Error && error.name === 'AbortError') throw error;
    }
    throw new AlliraApiError(response.status, code, message);
  }
  return response;
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function parseAuthOptions(value: unknown): AuthOptions {
  const invalid = () => new TypeError('Invalid authentication options response');
  if (!isRecord(value) || !isRecord(value.phone) || !Array.isArray(value.social) ||
      typeof value.legacyPasswordEnabled !== 'boolean' || typeof value.registrationEnabled !== 'boolean') throw invalid();
  const phone = value.phone;
  if (typeof phone.enabled !== 'boolean' || typeof phone.simulated !== 'boolean' ||
      (phone.reason !== null && typeof phone.reason !== 'string') || !Array.isArray(phone.countryCallingCodes) ||
      !phone.countryCallingCodes.every((code: unknown) => typeof code === 'string')) throw invalid();
  const social = value.social.map((item: unknown): AuthOptions['social'][number] => {
    if (!isRecord(item) || (item.provider !== 'wechat' && item.provider !== 'qq') ||
        typeof item.enabled !== 'boolean' || typeof item.simulated !== 'boolean' ||
        (item.reason !== null && typeof item.reason !== 'string')) throw invalid();
    return { provider: item.provider, enabled: item.enabled, simulated: item.simulated, reason: item.reason };
  });
  const backend = value.backend === undefined ? 'legacy' : value.backend;
  if (backend !== 'legacy' && backend !== 'oidc') throw invalid();
  let oidc: OidcAvailability = { enabled: false, provider: 'logto', simulated: false, reason: null, authorizationOrigin: null };
  if (value.oidc !== undefined) {
    if (!isRecord(value.oidc)) throw invalid();
    const item = value.oidc;
    const authorizationOrigin = item.authorizationOrigin === undefined ? null : item.authorizationOrigin;
    if (typeof item.enabled !== 'boolean' || item.provider !== 'logto' || typeof item.simulated !== 'boolean' ||
        (item.reason !== null && typeof item.reason !== 'string') ||
        (authorizationOrigin !== null && typeof authorizationOrigin !== 'string')) throw invalid();
    oidc = { enabled: item.enabled, provider: item.provider, simulated: item.simulated, reason: item.reason, authorizationOrigin };
  }
  return { backend, oidc,
    phone: { enabled: phone.enabled, simulated: phone.simulated, reason: phone.reason, countryCallingCodes: phone.countryCallingCodes },
    social, legacyPasswordEnabled: value.legacyPasswordEnabled, registrationEnabled: value.registrationEnabled };
}
function parseOidcSession(value: unknown): OidcSession {
  const invalid = () => new TypeError('Invalid OIDC session response');
  if (!isRecord(value) || typeof value.csrfToken !== 'string' || !value.csrfToken ||
      value.enabled !== true || typeof value.simulated !== 'boolean') throw invalid();
  let pending: OidcPending | null = null;
  if (value.pending !== null) {
    if (!isRecord(value.pending)) throw invalid();
    const item = value.pending;
    const purpose = item.purpose === undefined ? 'login' : item.purpose;
    if (item.provider !== 'logto' || typeof item.maskedPhone !== 'string' || typeof item.expiresAt !== 'string' ||
        (purpose !== 'login' && purpose !== 'erase' && purpose !== 'phone_migration')) throw invalid();
    pending = { provider: item.provider, maskedPhone: item.maskedPhone, expiresAt: item.expiresAt, purpose };
  }
  return { csrfToken: value.csrfToken, pending, enabled: true, simulated: value.simulated };
}
function isRun(value: unknown): value is Run {
  if (!isRecord(value)) return false;
  return ['id', 'conversationId', 'clientMessageId', 'userMessageId', 'assistantMessageId', 'createdAt', 'updatedAt']
    .every(key => typeof value[key] === 'string') &&
    (value.status === 'running' || value.status === 'completed' || value.status === 'failed' || value.status === 'cancelled') &&
    (value.errorCode === null || typeof value.errorCode === 'string');
}
function isMessage(value: unknown): value is Message {
  if (!isRecord(value)) return false;
  return ['id', 'conversationId', 'runId', 'content', 'createdAt'].every(key => typeof value[key] === 'string') &&
    typeof value.sequence === 'number' && Number.isSafeInteger(value.sequence) && value.sequence > 0 &&
    (value.role === 'user' || value.role === 'assistant') &&
    (value.status === 'generating' || value.status === 'completed' || value.status === 'failed' || value.status === 'cancelled');
}

type RequestAuth = 'protected' | 'public' | 'browser' | 'exchange';

export class AlliraClient {
  private readonly baseUrl: string;
  private readonly origin: string;
  private readonly token: AccessToken | undefined;
  private readonly useBrowserSession: boolean;
  private readonly fetcher: typeof globalThis.fetch;
  private browserCsrfToken: string | undefined;
  private browserSessionVersion = 0;

  constructor(options: ClientOptions) {
    this.baseUrl = apiBaseUrl(options.baseUrl);
    this.origin = new URL(this.baseUrl).origin;
    this.token = options.accessToken;
    this.useBrowserSession = options.browserSession ?? false;
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  getInfo(options: RequestOptions = {}): Promise<ApiInfo> {
    return this.json('/info', 'GET', options, undefined, 'public');
  }
  getTerms(options: RequestOptions = {}): Promise<Terms> {
    return this.json('/terms', 'GET', options, undefined, 'public');
  }
  getModelRoutes(options: RequestOptions = {}): Promise<ModelRoutes> {
    return this.json('/models', 'GET', options, undefined, 'public');
  }
  getCompanionPresets(options: RequestOptions = {}): Promise<CompanionPresets> {
    return this.json('/companion/presets', 'GET', options, undefined, 'public');
  }
  async getAuthOptions(options: RequestOptions = {}): Promise<AuthOptions> {
    const value = await this.json<unknown>('/auth/options', 'GET', options, undefined, 'public');
    options.signal?.throwIfAborted();
    return parseAuthOptions(value);
  }
  requestPhoneChallenge(input: RequestPhoneChallengeInput, options: RequestOptions = {}): Promise<PhoneChallenge> {
    return this.json('/auth/phone/challenges', 'POST', options, { phone: input.phone, purpose: input.purpose }, 'public');
  }
  /** Returns a caller-owned Bearer; it never changes this client's authentication state. */
  registerPhone(input: RegisterPhoneInput, options: RequestOptions = {}): Promise<AccountSession> {
    return this.json('/auth/phone/register', 'POST', options, {
      challengeId: input.challengeId, code: input.code, password: input.password, displayName: input.displayName,
      acceptedTermsVersion: input.acceptedTermsVersion, isAdult: input.isAdult,
    }, 'public');
  }
  loginPhone(input: LoginPhoneInput, options: RequestOptions = {}): Promise<AccountSession> {
    return this.json('/auth/phone/login', 'POST', options, { challengeId: input.challengeId, code: input.code }, 'public');
  }
  loginPhonePassword(input: LoginPhonePasswordInput, options: RequestOptions = {}): Promise<AccountSession> {
    return this.json('/auth/phone/password', 'POST', options, { phone: input.phone, password: input.password }, 'public');
  }
  getAccountIdentity(options: RequestOptions = {}): Promise<AccountIdentity> {
    return this.json('/account/identity', 'GET', options);
  }
  requestBindPhoneChallenge(input: RequestBindPhoneChallengeInput, options: RequestOptions = {}): Promise<PhoneChallenge> {
    return this.json('/account/phone/challenges', 'POST', options, { phone: input.phone, currentPassword: input.currentPassword });
  }
  bindPhone(input: BindPhoneInput, options: RequestOptions = {}): Promise<AccountIdentity> {
    return this.json('/account/phone/bind', 'POST', options, { challengeId: input.challengeId, code: input.code, currentPassword: input.currentPassword });
  }
  requestPhoneSecurityChallenge(input: RequestPhoneSecurityChallengeInput, options: RequestOptions = {}): Promise<PhoneChallenge> {
    return this.json('/account/phone/security/challenges', 'POST', options, { purpose: input.purpose });
  }
  /** The server revokes all sessions; the application must discard its own Bearer. */
  setPasswordByPhone(input: SetPasswordByPhoneInput, options: RequestOptions = {}): Promise<OkResult> {
    return this.sessionEndingWrite('/account/phone/password', options, { challengeId: input.challengeId, code: input.code, newPassword: input.newPassword });
  }
  eraseAccountByPhone(input: EraseAccountByPhoneInput, options: RequestOptions = {}): Promise<OkResult> {
    return this.sessionEndingWrite('/account/phone/erase', options, { challengeId: input.challengeId, code: input.code, confirmation: input.confirmation });
  }
  /** Returns credentials to the caller; does not change this client's accessToken. */
  register(input: RegisterInput, options: RequestOptions = {}): Promise<AccountSession> {
    return this.json('/auth/register', 'POST', options, {
      username: input.username, password: input.password, displayName: input.displayName,
      acceptedTermsVersion: input.acceptedTermsVersion, isAdult: input.isAdult,
    }, 'public');
  }
  /** Public credential exchange; a configured Bearer callback is not evaluated. */
  login(input: LoginInput, options: RequestOptions = {}): Promise<AccountSession> {
    return this.json('/auth/login', 'POST', options, { username: input.username, password: input.password }, 'public');
  }
  /** Consumes the configured account Bearer session and obtains a same-origin HttpOnly cookie. */
  exchangeBrowserSession(options: RequestOptions = {}): Promise<BrowserSession> {
    return this.loadBrowserSession('POST', options);
  }
  /** Restore the browser session and initialize this instance's in-memory CSRF token. */
  getBrowserSession(options: RequestOptions = {}): Promise<BrowserSession> {
    return this.loadBrowserSession('GET', options);
  }
  logoutBrowserSession(options: RequestOptions = {}): Promise<OkResult> {
    return this.sessionEndingWrite('/auth/browser/logout', options, undefined, 'browser');
  }
  /** Discard local CSRF state only; does not revoke or clear the HttpOnly cookie. */
  clearBrowserSession(): void {
    this.browserCsrfToken = undefined;
    this.browserSessionVersion++;
  }
  getAccount(options: RequestOptions = {}): Promise<AccountUser> {
    return this.json('/account', 'GET', options);
  }
  updateAccount(input: UpdateAccountInput, options: RequestOptions = {}): Promise<AccountUser> {
    return this.json('/account/update', 'POST', options, { displayName: input.displayName });
  }
  /** The server revokes all account sessions; the application must clear its own token. */
  changePassword(input: ChangePasswordInput, options: RequestOptions = {}): Promise<OkResult> {
    return this.sessionEndingWrite('/account/password', options, { currentPassword: input.currentPassword, newPassword: input.newPassword });
  }
  logoutAll(options: RequestOptions = {}): Promise<RevokeSessionsResult> {
    return this.sessionEndingWrite('/account/logout-all', options);
  }
  getRecoveryStatus(options: RequestOptions = {}): Promise<RecoveryStatus> {
    return this.json('/account/recovery', 'GET', options);
  }
  /** The resulting plaintext codes are shown only by the caller; the SDK does not retain them. */
  rotateRecoveryCodes(input: RotateRecoveryCodesInput, options: RequestOptions = {}): Promise<RecoveryCodesResult> {
    return this.json('/account/recovery', 'POST', options, { currentPassword: input.currentPassword });
  }
  /** Public recovery revokes sessions and does not sign the caller in. */
  recoverAccount(input: RecoverAccountInput, options: RequestOptions = {}): Promise<OkResult> {
    return this.json('/auth/recover', 'POST', options, {
      username: input.username, recoveryCode: input.recoveryCode, newPassword: input.newPassword,
    }, 'public');
  }
  listAccountSessions(options: RequestOptions = {}): Promise<AccountSessions> {
    return this.json('/account/sessions', 'GET', options);
  }
  revokeAccountSession(id: string, options: RequestOptions = {}): Promise<OkResult> {
    return this.json(`/account/sessions/${encodeURIComponent(id)}/revoke`, 'POST', options);
  }
  exportAccountData(options: RequestOptions = {}): Promise<AccountDataExport> {
    return this.json('/account/export', 'GET', options);
  }
  eraseAccount(input: EraseAccountInput, options: RequestOptions = {}): Promise<OkResult> {
    return this.sessionEndingWrite('/account/erase', options, { currentPassword: input.currentPassword, confirmation: input.confirmation });
  }
  createDevSession(input: { userId: string }, options: RequestOptions = {}): Promise<DevSession> {
    return this.json('/dev/session', 'POST', options, input, 'public');
  }
  getSession(options: RequestOptions = {}): Promise<Session> {
    return this.json('/session', 'GET', options);
  }
  logout(options: RequestOptions = {}): Promise<OkResult> {
    return this.sessionEndingWrite('/session/logout', options);
  }
  listConversations(options: ListOptions = {}): Promise<Page<Conversation>> {
    return this.json(listPath('/conversations', options), 'GET', options);
  }
  searchConversations(options: ConversationSearchOptions): Promise<Page<ConversationSearchResult>> {
    return this.json(queryPath('/conversation-search', { q: options.q, cursor: options.cursor, limit: options.limit }), 'GET', options);
  }
  exportConversation(id: string, input: ExportConversationInput, options: RequestOptions = {}): Promise<ConversationExport> {
    return this.json(queryPath(`/conversations/${encodeURIComponent(id)}/export`, { format: input.format }), 'GET', options);
  }
  /** Reuse clientForkId and the exact same parameters when explicitly retrying a fork. */
  forkConversation(id: string, input: ForkConversationInput, options: RequestOptions = {}): Promise<ForkConversationResult> {
    return this.json(`/conversations/${encodeURIComponent(id)}/fork`, 'POST', options, {
      clientForkId: input.clientForkId, throughMessageId: input.throughMessageId, title: input.title,
    });
  }
  createConversation(input: CreateConversationInput = {}, options: RequestOptions = {}): Promise<Conversation> {
    return this.json('/conversations', 'POST', options, input);
  }
  /** Creates preset-only setup; reuse the same UUID and preset for an explicit retry. */
  startCompanionConversation(input: StartCompanionInput, options: RequestOptions = {}): Promise<CompanionStart> {
    return this.json('/companion/conversations', 'POST', options, {
      clientSetupId: input.clientSetupId, presetId: input.presetId,
    });
  }
  getConversation(id: string, options: RequestOptions = {}): Promise<Conversation> {
    return this.json(`/conversations/${encodeURIComponent(id)}`, 'GET', options);
  }
  updateConversation(id: string, input: UpdateConversationInput, options: RequestOptions = {}): Promise<Conversation> {
    if (!input || (input.title === undefined && input.instructions === undefined)) {
      throw new TypeError('updateConversation requires title or instructions');
    }
    return this.json(`/conversations/${encodeURIComponent(id)}/update`, 'POST', options, input);
  }
  deleteConversation(id: string, options: RequestOptions = {}): Promise<OkResult> {
    return this.json(`/conversations/${encodeURIComponent(id)}/delete`, 'POST', options);
  }
  getConversationState(id: string, options: RequestOptions = {}): Promise<ConversationState> {
    return this.json(`/conversations/${encodeURIComponent(id)}/state`, 'GET', options);
  }
  listMessages(conversationId: string, options: ListOptions = {}): Promise<Page<Message>> {
    return this.json(listPath(`/conversations/${encodeURIComponent(conversationId)}/messages`, options), 'GET', options);
  }
  /** Pages are chronological; nextCursor points to the oldest item when earlier items exist. */
  listRecentMessages(conversationId: string, options: RecentMessagesOptions = {}): Promise<Page<Message>> {
    const path = queryPath(`/conversations/${encodeURIComponent(conversationId)}/messages/recent`, {
      before: options.before, limit: options.limit,
    });
    return this.json(path, 'GET', options);
  }
  listCharacters(options: ListOptions = {}): Promise<Page<Character>> {
    return this.json(listPath('/characters', options), 'GET', options);
  }
  createCharacter(input: CreateCharacterInput, options: RequestOptions = {}): Promise<Character> {
    return this.json('/characters', 'POST', options, input);
  }
  getCharacter(id: string, options: RequestOptions = {}): Promise<Character> {
    return this.json(`/characters/${encodeURIComponent(id)}`, 'GET', options);
  }
  updateCharacter(id: string, input: UpdateCharacterInput, options: RequestOptions = {}): Promise<Character> {
    if (!input || (input.name === undefined && input.description === undefined && input.instructions === undefined)) {
      throw new TypeError('updateCharacter requires name, description or instructions');
    }
    return this.json(`/characters/${encodeURIComponent(id)}/update`, 'POST', options, input);
  }
  deleteCharacter(id: string, options: RequestOptions = {}): Promise<OkResult> {
    return this.json(`/characters/${encodeURIComponent(id)}/delete`, 'POST', options);
  }
  getConversationMemory(conversationId: string, options: RequestOptions = {}): Promise<ConversationMemory> {
    return this.json(`/conversations/${encodeURIComponent(conversationId)}/memory`, 'GET', options);
  }
  updateConversationMemory(conversationId: string, input: UpdateConversationMemoryInput, options: RequestOptions = {}): Promise<ConversationMemory> {
    if (!input || (input.characterId === undefined && input.coreMemory === undefined && input.relationship === undefined)) {
      throw new TypeError('updateConversationMemory requires characterId, coreMemory or relationship');
    }
    return this.json(`/conversations/${encodeURIComponent(conversationId)}/memory`, 'POST', options, input);
  }
  summarizeConversation(conversationId: string, options: RequestOptions = {}): Promise<ConversationMemory> {
    return this.json(`/conversations/${encodeURIComponent(conversationId)}/memory/summarize`, 'POST', options);
  }
  listMemoryCards(conversationId: string, options: RequestOptions = {}): Promise<MemoryCards> {
    return this.json(`/conversations/${encodeURIComponent(conversationId)}/memory/cards`, 'GET', options);
  }
  createMemoryCard(conversationId: string, input: CreateMemoryCardInput, options: RequestOptions = {}): Promise<MemoryCard> {
    return this.json(`/conversations/${encodeURIComponent(conversationId)}/memory/cards`, 'POST', options, {
      category: input.category, title: input.title, content: input.content,
      sourceMessageId: input.sourceMessageId, enabled: input.enabled,
    });
  }
  updateMemoryCard(conversationId: string, cardId: string, input: UpdateMemoryCardInput, options: RequestOptions = {}): Promise<MemoryCard> {
    if (!input || (input.category === undefined && input.title === undefined && input.content === undefined &&
        input.sourceMessageId === undefined && input.enabled === undefined)) {
      throw new TypeError('updateMemoryCard requires category, title, content, sourceMessageId or enabled');
    }
    return this.json(`/conversations/${encodeURIComponent(conversationId)}/memory/cards/${encodeURIComponent(cardId)}/update`, 'POST', options, {
      category: input.category, title: input.title, content: input.content,
      sourceMessageId: input.sourceMessageId, enabled: input.enabled,
    });
  }
  deleteMemoryCard(conversationId: string, cardId: string, options: RequestOptions = {}): Promise<OkResult> {
    return this.json(`/conversations/${encodeURIComponent(conversationId)}/memory/cards/${encodeURIComponent(cardId)}/delete`, 'POST', options);
  }
  listMemoryBlocks(conversationId: string, options: MemoryBlockListOptions = {}): Promise<Page<MemoryBlock>> {
    return this.json(listPath(`/conversations/${encodeURIComponent(conversationId)}/memory/blocks`, options, { q: options.q }), 'GET', options);
  }
  updateMemoryBlock(conversationId: string, blockId: string, input: UpdateMemoryBlockInput, options: RequestOptions = {}): Promise<MemoryBlock> {
    return this.json(`/conversations/${encodeURIComponent(conversationId)}/memory/blocks/${encodeURIComponent(blockId)}/update`, 'POST', options, { enabled: input.enabled });
  }
  getMemoryBlockSource(conversationId: string, blockId: string, options: RequestOptions = {}): Promise<MemoryBlockSource> {
    return this.json(`/conversations/${encodeURIComponent(conversationId)}/memory/blocks/${encodeURIComponent(blockId)}/source`, 'GET', options);
  }
  /** Computes private prompt text without starting a Run; cookie mode still requires CSRF. */
  previewContext(conversationId: string, input: ContextPreviewInput, options: RequestOptions = {}): Promise<ContextPreview> {
    return this.json(`/conversations/${encodeURIComponent(conversationId)}/context/preview`, 'POST', options, { content: input.content });
  }
  previewImport(input: ImportInput, options: RequestOptions = {}): Promise<ImportPreview> {
    return this.json('/imports/preview', 'POST', options, input);
  }
  /** Reuse clientImportId for explicit retries of the same logical import. */
  commitImport(input: CommitImportInput, options: RequestOptions = {}): Promise<ImportCommitResult> {
    return this.json('/imports/commit', 'POST', options, input);
  }
  getImportSource(conversationId: string, options: RequestOptions = {}): Promise<ImportSource> {
    return this.json(`/conversations/${encodeURIComponent(conversationId)}/import-source`, 'GET', options);
  }
  getEnergy(options: RequestOptions = {}): Promise<EnergyWallet> {
    return this.json('/energy', 'GET', options);
  }
  listEnergyLedger(options: ListOptions = {}): Promise<Page<EnergyLedgerEntry>> {
    return this.json(listPath('/energy/ledger', options), 'GET', options);
  }
  redeemEnergy(input: { code: string }, options: RequestOptions = {}): Promise<EnergyWallet> {
    return this.json('/energy/redeem', 'POST', options, { code: input.code });
  }
  createRun(conversationId: string, input: CreateRunInput, options: RequestOptions = {}): Promise<Run> {
    return this.json(`/conversations/${encodeURIComponent(conversationId)}/runs`, 'POST', options, input);
  }
  getRun(id: string, options: RequestOptions = {}): Promise<Run> {
    return this.json(`/runs/${encodeURIComponent(id)}`, 'GET', options);
  }
  cancelRun(id: string, options: RequestOptions = {}): Promise<Run> {
    return this.json(`/runs/${encodeURIComponent(id)}/cancel`, 'POST', options);
  }
  getRunUsage(id: string, options: RequestOptions = {}): Promise<RunUsage> {
    return this.json(`/runs/${encodeURIComponent(id)}/usage`, 'GET', options);
  }

  /** No automatic reconnect: resume using the last event your application processed. */
  async *streamRunEvents(runId: string, options: StreamOptions = {}): AsyncGenerator<RunEvent> {
    const after = options.after ?? 0;
    if (!Number.isSafeInteger(after) || after < 0) throw new TypeError('after must be a nonnegative safe integer');
    const path = `/runs/${encodeURIComponent(runId)}/events?after=${after}`;
    const response = await this.request(path, 'GET', options, undefined, 'protected', 'text/event-stream');
    if (!response.body) throw new StreamInterruptedError(after);
    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8', { fatal: true });
    let buffer = '';
    let eventType = '';
    let id = '';
    let data: string[] = [];
    let lastEventId = after;
    let knownRun: Run | undefined;
    let knownMessageId: string | undefined;
    const payloadEvent = (number: number, type: string, payload: unknown): RunEvent => {
      // Errors deliberately exclude untrusted payloads, IDs and event names.
      const invalid = (): never => { throw new StreamProtocolError('Invalid or inconsistent run event payload'); };
      if (!isRecord(payload)) return invalid();
      if (type === 'message.delta') {
        if (typeof payload.messageId !== 'string' || typeof payload.delta !== 'string') return invalid();
        if (knownMessageId !== undefined && payload.messageId !== knownMessageId) return invalid();
        knownMessageId = payload.messageId;
        return { id: number, type, data: { messageId: payload.messageId, delta: payload.delta } };
      }
      const run = payload.run;
      if (!isRun(run) || run.id !== runId) return invalid();
      if (knownRun && (run.conversationId !== knownRun.conversationId ||
          run.assistantMessageId !== knownRun.assistantMessageId || run.userMessageId !== knownRun.userMessageId ||
          run.clientMessageId !== knownRun.clientMessageId)) return invalid();
      if (knownMessageId !== undefined && run.assistantMessageId !== knownMessageId) return invalid();
      if (type === 'run.started') {
        if (run.status !== 'running') return invalid();
        knownRun = { ...run };
        knownMessageId = run.assistantMessageId;
        return { id: number, type, data: { run } };
      }
      if (type !== 'run.completed' && type !== 'run.failed' && type !== 'run.cancelled') return invalid();
      const message = payload.message;
      const status = type.slice(4);
      if (run.status !== status || !isMessage(message) || message.role !== 'assistant' || message.status !== status ||
          message.runId !== runId || message.conversationId !== run.conversationId ||
          message.id !== run.assistantMessageId) return invalid();
      return { id: number, type, data: { run, message } };
    };
    const line = (value: string): RunEvent | undefined => {
      if (value === '') {
        const frame = { type: eventType, id, data };
        eventType = ''; id = ''; data = [];
        if (!frame.data.length) return;
        if (!eventTypes.has(frame.type)) throw new StreamProtocolError('Unsupported run event type');
        if (!/^[1-9]\d*$/.test(frame.id)) throw new StreamProtocolError('Invalid run event id');
        const number = Number(frame.id);
        if (!Number.isSafeInteger(number) || number <= lastEventId) {
          throw new StreamProtocolError('Non-increasing or unsafe run event id');
        }
        let payload: unknown;
        try { payload = JSON.parse(frame.data.join('\n')); }
        catch { throw new StreamProtocolError('Invalid JSON in run event'); }
        const event = payloadEvent(number, frame.type, payload);
        lastEventId = number;
        return event;
      }
      if (value.startsWith(':')) return;
      const colon = value.indexOf(':');
      const field = colon < 0 ? value : value.slice(0, colon);
      let valuePart = colon < 0 ? '' : value.slice(colon + 1);
      if (valuePart.startsWith(' ')) valuePart = valuePart.slice(1);
      if (field === 'event') eventType = valuePart;
      if (field === 'id') id = valuePart;
      if (field === 'data') data.push(valuePart);
      return;
    };
    try {
      while (true) {
        options.signal?.throwIfAborted();
        const chunk = await reader.read();
        options.signal?.throwIfAborted();
        try { buffer += chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true }); }
        catch { throw new StreamProtocolError('Invalid UTF-8 in run event stream'); }
        // Support LF, CRLF (even split across chunks), and CR-only event streams.
        let start = 0;
        for (let i = 0; i < buffer.length; i++) {
          const char = buffer[i];
          if (char !== '\r' && char !== '\n') continue;
          if (char === '\r' && i + 1 === buffer.length && !chunk.done) break;
          const value = buffer.slice(start, i);
          if (char === '\r' && buffer[i + 1] === '\n') i++;
          start = i + 1;
          const event = line(value);
          if (event) {
            yield event;
            if (terminalTypes.has(event.type)) return;
            options.signal?.throwIfAborted();
          }
        }
        buffer = buffer.slice(start);
        if (chunk.done) throw new StreamInterruptedError(lastEventId);
      }
    } finally {
      // Also release the connection when the consumer breaks early or a terminal arrives.
      try { await reader.cancel(); } catch { /* Preserve the original failure/abort. */ }
      reader.releaseLock();
    }
  }

  private async loadBrowserSession(method: 'GET' | 'POST', options: RequestOptions): Promise<BrowserSession> {
    options.signal?.throwIfAborted();
    const version = ++this.browserSessionVersion;
    const session = await this.json<BrowserSession>('/auth/browser/session', method, options, undefined, method === 'POST' ? 'exchange' : 'browser');
    options.signal?.throwIfAborted();
    // Do not reinitialize CSRF from an older session response after clearing or restoring it.
    if (version === this.browserSessionVersion) this.browserCsrfToken = session.csrfToken;
    return session;
  }
  private async sessionEndingWrite<T>(path: string, options: RequestOptions, body?: unknown, auth: 'protected' | 'browser' = 'protected'): Promise<T> {
    const csrfToken = this.browserCsrfToken;
    const result = await this.json<T>(path, 'POST', options, body, auth);
    if ((auth === 'browser' || this.useBrowserSession) && this.browserCsrfToken === csrfToken) this.clearBrowserSession();
    return result;
  }
  private async json<T>(path: string, method: string, options: RequestOptions, body?: unknown, auth: RequestAuth = 'protected'): Promise<T> {
    const response = await this.request(path, method, options, body, auth);
    return await response.json() as T;
  }
  private async request(path: string, method: string, options: RequestOptions, body?: unknown, auth: RequestAuth = 'protected', accept = 'application/json'): Promise<Response> {
    options.signal?.throwIfAborted();
    const headers = new Headers({ Accept: accept });
    const browser = auth === 'browser' || (auth === 'protected' && this.useBrowserSession);
    const exchange = auth === 'exchange';
    const csrfToken = this.browserCsrfToken;
    const version = this.browserSessionVersion;
    if (exchange || (auth === 'protected' && !browser)) {
      const token = typeof this.token === 'function' ? await this.token() : this.token;
      options.signal?.throwIfAborted();
      if (token) headers.set('Authorization', `Bearer ${token}`);
      else if (exchange) throw new TypeError('exchangeBrowserSession requires an account accessToken');
    }
    if ((browser || exchange) && method === 'POST') {
      // Browsers supply their own forbidden Origin header; Node needs this explicitly.
      headers.set('Origin', this.origin);
      if (!exchange) {
        if (!csrfToken) throw new TypeError('Call exchangeBrowserSession() or getBrowserSession() before a browser session write');
        headers.set('X-CSRF-Token', csrfToken);
      }
    }
    if (body !== undefined) headers.set('Content-Type', 'application/json');
    options.signal?.throwIfAborted();
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      method, headers, signal: options.signal,
      credentials: browser || exchange ? 'same-origin' : 'omit',
      ...((browser || exchange) ? { mode: 'same-origin' } : {}),
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if ((browser || exchange) && response.status === 401 &&
        this.browserSessionVersion === version && this.browserCsrfToken === csrfToken) {
      this.clearBrowserSession();
    }
    return checkResponse(response, options);
  }
}

/** Cookie-only OAuth transition client, never an ordinary account or admin session. */
export class AlliraSocialClient {
  private readonly baseUrl: string;
  private readonly origin: string;
  private readonly fetcher: typeof globalThis.fetch;
  private csrfToken: string | undefined;
  private sessionVersion = 0;

  constructor(options: SocialClientOptions) {
    this.baseUrl = apiBaseUrl(options.baseUrl);
    this.origin = new URL(this.baseUrl).origin;
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  async getSession(options: RequestOptions = {}): Promise<SocialAuthSession> {
    options.signal?.throwIfAborted();
    const version = ++this.sessionVersion;
    const session = await this.json<SocialAuthSession>('/auth/social/session', 'GET', options);
    options.signal?.throwIfAborted();
    if (version === this.sessionVersion) this.csrfToken = session.csrfToken;
    return session;
  }
  /** Returns a server-created URL only; callers must validate it before explicit navigation. */
  start(provider: SocialProvider, options: RequestOptions = {}): Promise<SocialAuthStart> {
    return this.json(`/auth/social/${encodeURIComponent(provider)}/start`, 'POST', options, {});
  }
  requestPhoneChallenge(input: SocialPhoneChallengeInput, options: RequestOptions = {}): Promise<PhoneChallenge> {
    return this.json('/auth/social/phone/challenges', 'POST', options, { phone: input.phone });
  }
  /** Pending identity alone is not signed in; only this explicit exchange returns account credentials. */
  bindPhone(input: SocialBindPhoneInput, options: RequestOptions = {}): Promise<AccountSession> {
    return this.json('/auth/social/bind-phone', 'POST', options, {
      challengeId: input.challengeId, code: input.code, password: input.password, displayName: input.displayName,
      acceptedTermsVersion: input.acceptedTermsVersion, isAdult: input.isAdult, confirmBind: input.confirmBind,
    });
  }
  complete(options: RequestOptions = {}): Promise<AccountSession> {
    return this.json('/auth/social/complete', 'POST', options, {});
  }
  async cancel(options: RequestOptions = {}): Promise<OkResult> {
    const version = this.sessionVersion;
    const csrfToken = this.csrfToken;
    const result = await this.json<OkResult>('/auth/social/cancel', 'POST', options, {});
    if (version === this.sessionVersion && this.csrfToken === csrfToken) this.clearSession();
    return result;
  }
  /** Local memory only; use cancel() to revoke the server-side transition cookie. */
  clearSession(): void {
    this.csrfToken = undefined;
    this.sessionVersion++;
  }

  private async json<T>(path: string, method: 'GET' | 'POST', options: RequestOptions, body?: unknown): Promise<T> {
    options.signal?.throwIfAborted();
    const headers = new Headers({ Accept: 'application/json' });
    const version = this.sessionVersion;
    const csrfToken = this.csrfToken;
    if (method === 'POST') {
      if (!csrfToken) throw new TypeError('Call social getSession() before a write request');
      headers.set('Origin', this.origin);
      headers.set('X-CSRF-Token', csrfToken);
    }
    if (body !== undefined) headers.set('Content-Type', 'application/json');
    options.signal?.throwIfAborted();
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      method, headers, credentials: 'same-origin', mode: 'same-origin', signal: options.signal,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (response.status === 401 && version === this.sessionVersion && this.csrfToken === csrfToken) this.clearSession();
    await checkResponse(response, options);
    return await response.json() as T;
  }
}

/** Cookie-only OIDC RP glue. OAuth state, PKCE, token verification and issuer configuration stay on the server. */
export class AlliraOidcClient {
  private readonly baseUrl: string;
  private readonly origin: string;
  private readonly fetcher: typeof globalThis.fetch;
  private csrfToken: string | undefined;
  private sessionVersion = 0;

  constructor(options: OidcClientOptions) {
    this.baseUrl = apiBaseUrl(options.baseUrl);
    this.origin = new URL(this.baseUrl).origin;
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  async getSession(options: RequestOptions = {}): Promise<OidcSession> {
    options.signal?.throwIfAborted();
    const version = ++this.sessionVersion;
    const session = parseOidcSession(await this.json<unknown>('/auth/oidc/session', 'GET', options));
    options.signal?.throwIfAborted();
    if (version === this.sessionVersion) this.csrfToken = session.csrfToken;
    return session;
  }
  /** Returns a URL only; validate its origin against AuthOptions.oidc.authorizationOrigin before explicit navigation. */
  start(input?: { purpose?: 'login' | 'phone_migration' }, options?: RequestOptions): Promise<OidcStart>;
  start(input: StartOidcInput, options: OidcEraseOptions): Promise<OidcStart>;
  start(input: StartOidcInput = {}, options: RequestOptions | OidcEraseOptions = {}): Promise<OidcStart> {
    return this.json('/auth/oidc/start', 'POST', options, { purpose: input.purpose }, input.purpose === 'erase');
  }
  /** Only explicit completion returns caller-owned ordinary credentials. It never changes another SDK instance. */
  complete(input: CompleteOidcInput, options: RequestOptions = {}): Promise<AccountSession> {
    return this.endingWrite('/auth/oidc/complete', {
      acceptedTermsVersion: input.acceptedTermsVersion, isAdult: input.isAdult, confirmIdentity: input.confirmIdentity,
      confirmPhoneMigration: input.confirmPhoneMigration,
    }, options);
  }
  migrate(input: MigrateOidcInput, options: RequestOptions = {}): Promise<AccountSession> {
    return this.endingWrite('/auth/oidc/migrate', {
      acceptedTermsVersion: input.acceptedTermsVersion, isAdult: input.isAdult, confirmIdentity: input.confirmIdentity,
      username: input.username, currentPassword: input.currentPassword,
    }, options);
  }
  /** Requires fresh erase-purpose OIDC proof AND the current ordinary browser cookie. This deletes local business data only. */
  erase(input: EraseOidcInput, options: OidcEraseOptions): Promise<OkResult> {
    return this.endingWrite('/auth/oidc/erase', { confirmation: input.confirmation }, options ?? {}, true);
  }
  cancel(options: RequestOptions = {}): Promise<OkResult> {
    return this.endingWrite('/auth/oidc/cancel', {}, options);
  }
  /** Local memory only. Does not cancel the server flow or sign out an ordinary, social or admin client. */
  clear(): void {
    this.csrfToken = undefined;
    this.sessionVersion++;
  }

  private async endingWrite<T>(path: string, body: unknown, options: RequestOptions, requireBrowserCsrf = false): Promise<T> {
    const version = this.sessionVersion;
    const csrfToken = this.csrfToken;
    const result = await this.json<T>(path, 'POST', options, body, requireBrowserCsrf);
    // Also invalidate an older in-flight GET using this same transition cookie, not a newer distinct session.
    if (version === this.sessionVersion || this.csrfToken === csrfToken) this.clear();
    return result;
  }
  private async json<T>(path: string, method: 'GET' | 'POST', options: RequestOptions, body?: unknown, requireBrowserCsrf = false): Promise<T> {
    options.signal?.throwIfAborted();
    const version = this.sessionVersion;
    const csrfToken = this.csrfToken;
    const headers = new Headers({ Accept: 'application/json' });
    if (method === 'POST') {
      if (!csrfToken) throw new TypeError('Call OIDC getSession() before a write request');
      headers.set('Origin', this.origin);
      headers.set('X-Allira-OIDC-CSRF', csrfToken);
      if (requireBrowserCsrf) {
        const browserCsrfToken: unknown = (options as Partial<OidcEraseOptions>).browserCsrfToken;
        if (typeof browserCsrfToken !== 'string' || !browserCsrfToken.trim()) {
          throw new TypeError('OIDC erase requires an explicit browserCsrfToken from getBrowserSession()');
        }
        headers.set('X-CSRF-Token', browserCsrfToken);
      }
    }
    if (body !== undefined) headers.set('Content-Type', 'application/json');
    options.signal?.throwIfAborted();
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      method, headers, credentials: 'same-origin', mode: 'same-origin', signal: options.signal,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (response.status === 401 && version === this.sessionVersion && this.csrfToken === csrfToken) this.clear();
    await checkResponse(response, options);
    const value = await response.json() as T;
    options.signal?.throwIfAborted();
    return value;
  }
}

/** Cookie-only admin client. CSRF stays in memory, never in browser storage. */
export class AlliraAdminClient {
  private readonly baseUrl: string;
  private readonly origin: string;
  private readonly fetcher: typeof globalThis.fetch;
  private csrfToken: string | undefined;
  private sessionVersion = 0;

  constructor(options: AdminClientOptions) {
    this.baseUrl = apiBaseUrl(options.baseUrl);
    this.origin = new URL(this.baseUrl).origin;
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  /** Explicit legacy/test compatibility only; Cloudflare Access deployments reject code exchange. */
  login(input: { code: string }, options: RequestOptions = {}): Promise<AdminSession> {
    return this.loadSession('POST', options, { code: input.code });
  }
  getSession(options: RequestOptions = {}): Promise<AdminSession> {
    return this.loadSession('GET', options);
  }
  logout(options: RequestOptions = {}): Promise<OkResult> {
    const csrfToken = this.csrfToken;
    // Invalidate immediately, including older restores. The explicit POST still uses its captured proof.
    this.clear();
    return this.json('/admin/logout', 'POST', options, undefined, true, csrfToken);
  }
  /** Invalidate only local admin CSRF and pending restores; does not revoke the server cookie. */
  clear(): void { this.csrfToken = undefined; this.sessionVersion++; }
  overview(options: RequestOptions = {}): Promise<AdminOverview> {
    return this.json('/admin/overview', 'GET', options);
  }
  listUsers(options: AdminUserListOptions = {}): Promise<Page<AdminUser>> {
    return this.json(listPath('/admin/users', options, { q: options.q, identity: options.identity }), 'GET', options);
  }
  listUserSessions(userId: string, options: ListOptions = {}): Promise<Page<AdminDeviceSession>> {
    return this.json(listPath(`/admin/users/${encodeURIComponent(userId)}/sessions`, options), 'GET', options);
  }
  revokeUserSession(userId: string, sessionId: string, options: RequestOptions = {}): Promise<RevokeSessionsResult> {
    return this.json(`/admin/users/${encodeURIComponent(userId)}/sessions/${encodeURIComponent(sessionId)}/revoke`, 'POST', options);
  }
  revokeUserSessions(userId: string, options: RequestOptions = {}): Promise<RevokeSessionsResult> {
    return this.json(`/admin/users/${encodeURIComponent(userId)}/revoke-sessions`, 'POST', options);
  }
  listConversations(options: AdminConversationListOptions = {}): Promise<Page<AdminConversation>> {
    return this.json(listPath('/admin/conversations', options, { userId: options.userId }), 'GET', options);
  }
  listMessages(conversationId: string, options: ListOptions = {}): Promise<Page<Message>> {
    return this.json(listPath(`/admin/conversations/${encodeURIComponent(conversationId)}/messages`, options), 'GET', options);
  }
  deleteConversation(id: string, options: RequestOptions = {}): Promise<OkResult> {
    return this.json(`/admin/conversations/${encodeURIComponent(id)}/delete`, 'POST', options);
  }
  listRuns(options: AdminRunListOptions = {}): Promise<Page<AdminRun>> {
    return this.json(listPath('/admin/runs', options, { status: options.status, userId: options.userId }), 'GET', options);
  }
  getRun(id: string, options: RequestOptions = {}): Promise<AdminRun> {
    return this.json(`/admin/runs/${encodeURIComponent(id)}`, 'GET', options);
  }
  cancelRun(id: string, options: RequestOptions = {}): Promise<AdminRun> {
    return this.json(`/admin/runs/${encodeURIComponent(id)}/cancel`, 'POST', options);
  }
  listAudit(options: ListOptions = {}): Promise<Page<AdminAuditEntry>> {
    return this.json(listPath('/admin/audit', options), 'GET', options);
  }
  /** Returns newly issued plaintext codes to the caller, without logging or persisting them. */
  issueCdks(input: IssueCdksInput, options: RequestOptions = {}): Promise<IssueCdksResult> {
    return this.json('/admin/cdks', 'POST', options, { energy: input.energy, count: input.count, expiresInDays: input.expiresInDays });
  }
  listCdks(options: ListOptions = {}): Promise<Page<AdminCdk>> {
    return this.json(listPath('/admin/cdks', options), 'GET', options);
  }
  disableCdk(prefix: string, options: RequestOptions = {}): Promise<OkResult> {
    return this.json(`/admin/cdks/${encodeURIComponent(prefix)}/disable`, 'POST', options);
  }
  getUsageOverview(options: RequestOptions = {}): Promise<AdminUsageOverview> {
    return this.json('/admin/usage', 'GET', options);
  }
  getDiagnostics(options: RequestOptions = {}): Promise<AdminDiagnostics> {
    return this.json('/admin/diagnostics', 'GET', options);
  }
  getRoutes(options: RequestOptions = {}): Promise<AdminRouteOverview> {
    return this.json('/admin/routes', 'GET', options);
  }

  private async loadSession(method: 'GET' | 'POST', options: RequestOptions, body?: { code: string }): Promise<AdminSession> {
    options.signal?.throwIfAborted();
    const version = ++this.sessionVersion;
    if (method === 'POST') this.csrfToken = undefined;
    const session = await this.json<AdminSession>('/admin/session', method, options, body, false);
    options.signal?.throwIfAborted();
    if (version === this.sessionVersion) this.csrfToken = session.csrfToken;
    return session;
  }
  private async json<T>(path: string, method: string, options: RequestOptions, body?: unknown, csrf = true, csrfToken = this.csrfToken): Promise<T> {
    options.signal?.throwIfAborted();
    const version = this.sessionVersion;
    const headers = new Headers({ Accept: 'application/json' });
    if (method !== 'GET') {
      // Browsers supply their own forbidden Origin header; Node needs the explicit value.
      headers.set('Origin', this.origin);
      if (csrf) {
        if (!csrfToken) throw new TypeError('Call admin login() or getSession() before a write request');
        headers.set('X-CSRF-Token', csrfToken);
      }
    }
    if (body !== undefined) headers.set('Content-Type', 'application/json');
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      method, headers, credentials: 'same-origin', mode: 'same-origin', signal: options.signal,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    options.signal?.throwIfAborted();
    if (response.status === 401 && version === this.sessionVersion && this.csrfToken === csrfToken) this.clear();
    await checkResponse(response, options);
    const result = await response.json() as T;
    options.signal?.throwIfAborted();
    return result;
  }
}
