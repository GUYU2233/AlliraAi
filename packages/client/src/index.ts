/** Framework-neutral Allira v1 Fetch SDK. Requires a Fetch-capable runtime. */
export type AccessToken = string | (() => string | undefined | Promise<string | undefined>);
export interface ClientOptions {
  baseUrl: string;
  accessToken?: AccessToken;
  fetch?: typeof globalThis.fetch;
}
export interface RequestOptions { signal?: AbortSignal }
export interface ListOptions extends RequestOptions { cursor?: string; limit?: number }
export interface Page<T> { items: T[]; nextCursor: string | null }
export interface DevSession { accessToken: string; tokenType: 'Bearer' }
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
export interface CreateConversationInput { title?: string; instructions?: string | null }
export interface CreateRunInput { clientMessageId: string; content: string }
export interface StreamOptions extends RequestOptions { after?: number }
export type RunEvent =
  | { id: number; type: 'run.started'; data: { run: Run } }
  | { id: number; type: 'message.delta'; data: { messageId: string; delta: string } }
  | { id: number; type: 'run.completed' | 'run.failed' | 'run.cancelled'; data: { run: Run; message: Message } };

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

export class AlliraClient {
  private readonly baseUrl: string;
  private readonly token: AccessToken | undefined;
  private readonly fetcher: typeof globalThis.fetch;

  constructor(options: ClientOptions) {
    const url = new URL(options.baseUrl);
    if (!['http:', 'https:'].includes(url.protocol) || url.search || url.hash) {
      throw new TypeError('baseUrl must be an HTTP(S) URL without query or fragment');
    }
    const path = url.pathname.replace(/\/+$/, '');
    url.pathname = path.endsWith('/api/v1') ? path : `${path}/api/v1`;
    this.baseUrl = url.toString().replace(/\/$/, '');
    this.token = options.accessToken;
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
  }

  createDevSession(input: { userId: string }, options: RequestOptions = {}): Promise<DevSession> {
    return this.json('/dev/session', 'POST', options, input, false);
  }
  listConversations(options: ListOptions = {}): Promise<Page<Conversation>> {
    return this.json(this.listPath('/conversations', options), 'GET', options);
  }
  createConversation(input: CreateConversationInput = {}, options: RequestOptions = {}): Promise<Conversation> {
    return this.json('/conversations', 'POST', options, input);
  }
  getConversation(id: string, options: RequestOptions = {}): Promise<Conversation> {
    return this.json(`/conversations/${encodeURIComponent(id)}`, 'GET', options);
  }
  listMessages(conversationId: string, options: ListOptions = {}): Promise<Page<Message>> {
    return this.json(this.listPath(`/conversations/${encodeURIComponent(conversationId)}/messages`, options), 'GET', options);
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

  /** No automatic reconnect: resume using the last event your application processed. */
  async *streamRunEvents(runId: string, options: StreamOptions = {}): AsyncGenerator<RunEvent> {
    const after = options.after ?? 0;
    if (!Number.isSafeInteger(after) || after < 0) throw new TypeError('after must be a nonnegative safe integer');
    const path = `/runs/${encodeURIComponent(runId)}/events?after=${after}`;
    const response = await this.request(path, 'GET', options, undefined, true, 'text/event-stream');
    if (!response.body) throw new StreamInterruptedError(after);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let eventType = '';
    let id = '';
    let data: string[] = [];
    let lastEventId = after;
    const line = (value: string): RunEvent | undefined => {
      if (value === '') {
        const frame = { type: eventType, id, data };
        eventType = ''; id = ''; data = [];
        if (!frame.data.length) return;
        if (!eventTypes.has(frame.type)) throw new StreamProtocolError(`Unsupported event type: ${frame.type}`);
        if (!/^[1-9]\d*$/.test(frame.id)) throw new StreamProtocolError(`Invalid event id: ${frame.id}`);
        const number = Number(frame.id);
        if (!Number.isSafeInteger(number) || number <= lastEventId) {
          throw new StreamProtocolError(`Non-increasing or unsafe event id: ${frame.id}`);
        }
        let payload: unknown;
        try { payload = JSON.parse(frame.data.join('\n')); }
        catch { throw new StreamProtocolError(`Invalid JSON in event ${frame.id}`); }
        lastEventId = number;
        return { id: number, type: frame.type, data: payload } as RunEvent;
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
        buffer += chunk.done ? decoder.decode() : decoder.decode(chunk.value, { stream: true });
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

  private listPath(path: string, options: ListOptions): string {
    const query = new URLSearchParams();
    if (options.cursor !== undefined) query.set('cursor', options.cursor);
    if (options.limit !== undefined) query.set('limit', String(options.limit));
    return query.size ? `${path}?${query}` : path;
  }
  private async json<T>(path: string, method: string, options: RequestOptions, body?: unknown, auth = true): Promise<T> {
    const response = await this.request(path, method, options, body, auth);
    return await response.json() as T;
  }
  private async request(path: string, method: string, options: RequestOptions, body?: unknown, auth = true, accept = 'application/json'): Promise<Response> {
    options.signal?.throwIfAborted();
    const headers = new Headers({ Accept: accept });
    if (auth) {
      const token = typeof this.token === 'function' ? await this.token() : this.token;
      if (token) headers.set('Authorization', `Bearer ${token}`);
    }
    if (body !== undefined) headers.set('Content-Type', 'application/json');
    options.signal?.throwIfAborted();
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      method, headers, signal: options.signal,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) {
      let code = 'http_error';
      let message = response.statusText || `HTTP ${response.status}`;
      try {
        const envelope = await response.json() as { error?: { code?: unknown; message?: unknown } };
        if (typeof envelope.error?.code === 'string') code = envelope.error.code;
        if (typeof envelope.error?.message === 'string') message = envelope.error.message;
      } catch (error) {
        options.signal?.throwIfAborted();
        if (error instanceof Error && error.name === 'AbortError') throw error;
      }
      throw new AlliraApiError(response.status, code, message);
    }
    return response;
  }
}
