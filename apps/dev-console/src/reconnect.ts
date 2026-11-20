import { AlliraApiError, StreamInterruptedError } from '../../../packages/client/src/index.ts';

export const maxReconnects = 5;

export function retryableStreamError(error: unknown): boolean {
  if (error instanceof AlliraApiError) return error.status >= 500 && error.status <= 599;
  return error instanceof StreamInterruptedError || error instanceof TypeError || (error instanceof DOMException && error.name === 'NetworkError');
}

export function retryDelay(attempt: number, jitter = Math.random()): number {
  return Math.min(6_000, Math.round(600 * 2 ** (attempt - 1) * (0.8 + jitter * 0.4)));
}

export function delay(ms: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const aborted = () => { clearTimeout(timer); reject(signal.reason); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', aborted); resolve(); }, ms);
    signal.addEventListener('abort', aborted, { once: true });
  });
}

export function waitForOnline(signal: AbortSignal): Promise<void> {
  signal.throwIfAborted();
  if (navigator.onLine) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const cleanup = () => { window.removeEventListener('online', online); signal.removeEventListener('abort', aborted); };
    const online = () => { cleanup(); resolve(); };
    const aborted = () => { cleanup(); reject(signal.reason); };
    signal.addEventListener('abort', aborted, { once: true });
    window.addEventListener('online', online, { once: true });
  });
}
