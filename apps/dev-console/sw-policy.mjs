/** Public-shell allowlist only. Shared with tests; never a broad cache-first route. */
export function publicShellRequest(method, requestUrl, origin, paths, hasAuthorization = false) {
  if (method !== 'GET' || hasAuthorization) return false;
  const url = new URL(requestUrl, origin);
  return url.origin === origin && !url.search && paths.includes(url.pathname);
}
export function serviceWorkerSource(paths, cacheName) {
  return `const PATHS = ${JSON.stringify(paths)};\nconst CACHE = ${JSON.stringify(cacheName)};\nconst PREFIX = 'allira-public-shell-';\n${publicShellRequest.toString()}\n` +
`self.addEventListener('install', event => event.waitUntil((async () => {
  const cache = await caches.open(CACHE);
  for (const path of PATHS) {
    const request = new Request(new URL(path, self.location.origin), { credentials: 'omit', redirect: 'error', cache: 'reload' });
    const response = await fetch(request);
    if (!response.ok || response.type !== 'basic' || new URL(response.url).pathname !== path) throw new Error('Public shell unavailable');
    await cache.put(path, response);
  }
  await self.skipWaiting();
})()));
self.addEventListener('activate', event => event.waitUntil((async () => {
  for (const name of await caches.keys()) if (name.startsWith(PREFIX) && name !== CACHE) await caches.delete(name);
  await self.clients.claim();
})()));
self.addEventListener('fetch', event => {
  const request = event.request;
  if (!publicShellRequest(request.method, request.url, self.location.origin, PATHS, request.headers.has('Authorization'))) return;
  event.respondWith((async () => {
    const path = new URL(request.url).pathname;
    const cache = await caches.open(CACHE);
    try {
      // Public static bytes only: no Cookie/Bearer, no redirects or user-specific response.
      const response = await fetch(new Request(request.url, { method: 'GET', credentials: 'omit', redirect: 'error', cache: 'no-cache' }));
      if (response.ok && response.type === 'basic' && new URL(response.url).pathname === path) await cache.put(path, response.clone());
      return response;
    } catch {
      return (await cache.match(path)) ?? new Response('当前离线，公开页面壳尚未缓存。', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
    }
  })());
});\n`;
}
