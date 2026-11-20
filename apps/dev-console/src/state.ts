import type { Message, Run, RunEvent, RunMode } from '../../../packages/client/src/index.ts';

export interface StreamView {
  run: Run;
  content: string;
  lastEventId: number;
  terminal: Message | null;
}
/** Replaying from zero MUST reset the local assistant buffer, including a saved partial snapshot. */
export function beginReplay(run: Run): StreamView {
  return { run, content: '', lastEventId: 0, terminal: null };
}
export function applyRunEvent(state: StreamView, event: RunEvent): StreamView {
  if (event.id <= state.lastEventId || state.terminal) return state;
  if (event.type === 'run.started') {
    if (event.data.run.id !== state.run.id) return state;
    return { ...state, run: event.data.run, lastEventId: event.id };
  }
  if (event.type === 'message.delta') {
    if (event.data.messageId !== state.run.assistantMessageId) return state;
    return { ...state, content: state.content + event.data.delta, lastEventId: event.id };
  }
  if (event.data.run.id !== state.run.id || event.data.message.id !== state.run.assistantMessageId) return state;
  // Canonical terminal snapshots replace (not append to) accumulated deltas.
  return { run: event.data.run, content: event.data.message.content, lastEventId: event.id, terminal: event.data.message };
}
export function mergeMessages(older: Message[], current: Message[]): Message[] {
  const merged = new Map<string, Message>();
  for (const message of [...older, ...current]) merged.set(message.id, message);
  return [...merged.values()].sort((a, b) => a.sequence - b.sequence);
}
export interface Scope { auth: number; view: number; conversationId: string }
export function sameScope(captured: Scope, current: Scope): boolean {
  return captured.auth === current.auth && captured.view === current.view && captured.conversationId === current.conversationId;
}
export interface PendingSend { clientMessageId: string; content: string; mode?: RunMode; uncertain: boolean }
export function newPending(clientMessageId: string, content: string, mode?: RunMode): PendingSend {
  return { clientMessageId, content, ...(mode ? { mode } : {}), uncertain: false };
}
/** UUID, original bytes and routing mode stay bound even after an ambiguous POST. */
export function retryPayload(pending: PendingSend): { clientMessageId: string; content: string; mode?: RunMode } {
  return { clientMessageId: pending.clientMessageId, content: pending.content, ...(pending.mode ? { mode: pending.mode } : {}) };
}
