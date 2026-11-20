import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { publicShellRequest, serviceWorkerSource } from '../sw-policy.mjs';

const origin = 'http://127.0.0.1:8000';
const allowed = ['/test/', '/test/manifest.webmanifest', '/console-assets/public-chat-HASH.js', '/console-assets/public-chat-HASH.css'];
test('service worker allowlist rejects APIs, admin, non-GET, foreign origin, credentials and query strings', () => {
  for (const path of allowed) assert.equal(publicShellRequest('GET', origin + path, origin, allowed), true);
  for (const path of ['/api/v1/conversations', '/api/v1/energy', '/api/v1/auth/login', '/admin/', '/console-assets/admin-HASH.js', '/console-assets/admin-HASH.css', '/test/sw.js', '/test/?code=secret', '/test/index.html']) assert.equal(publicShellRequest('GET', origin + path, origin, allowed), false);
  assert.equal(publicShellRequest('POST', origin + '/test/', origin, allowed), false);
  assert.equal(publicShellRequest('GET', 'https://other.invalid/test/', origin, allowed), false);
  assert.equal(publicShellRequest('GET', origin + '/test/', origin, allowed, true), false);
});
test('built PWA references hashed public assets only and has no inline script or credential cache path', async () => {
  const html = await readFile(new URL('../.test-dist/test/index.html', import.meta.url), 'utf8');
  const admin = await readFile(new URL('../.test-dist/admin/index.html', import.meta.url), 'utf8');
  const worker = await readFile(new URL('../.test-dist/test/sw.js', import.meta.url), 'utf8');
  const manifest = JSON.parse(await readFile(new URL('../.test-dist/test/manifest.webmanifest', import.meta.url), 'utf8'));
  assert.match(html, /public-chat-[A-Z0-9]+\.js/); assert.match(html, /public-chat-[A-Z0-9]+\.css/);
  assert.match(admin, /admin-[A-Z0-9]+\.js/);
  assert.doesNotMatch(html, /<script(?![^>]*src=)/); assert.doesNotMatch(html, /<style/);
  assert.equal(manifest.start_url, '/test/'); assert.equal(manifest.scope, '/test/');
  const paths = JSON.parse(worker.match(/^const PATHS = (.+);$/m)![1]);
  assert.equal(paths.length, 6); assert.ok(paths.every((path: string) => path === '/test/' || path === '/test/manifest.webmanifest' || /^\/console-assets\/public-chat-[A-Z0-9]+\.(js|css)$/.test(path) || /^\/console-assets\/public-icon-(192|512)-[a-f0-9]{16}\.png$/.test(path)));
  assert.deepEqual(manifest.icons.map((icon: { sizes: string }) => icon.sizes), ['192x192', '512x512']);
  for (const icon of manifest.icons) {
    assert.equal(icon.type, 'image/png'); assert.ok(paths.includes(icon.src));
    const bytes = await readFile(new URL(`../.test-dist/assets/${icon.src.split('/').pop()}`, import.meta.url));
    assert.deepEqual([...bytes.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
    const size = Number(icon.sizes.split('x')[0]); assert.equal(bytes.readUInt32BE(16), size); assert.equal(bytes.readUInt32BE(20), size);
  }
  assert.match(worker, /credentials: 'omit'/); assert.match(worker, /redirect: 'error'/);
  assert.doesNotMatch(worker, /\/api\/|\/admin\//);
  assert.doesNotThrow(() => new Function(serviceWorkerSource(allowed, 'test-cache')));
});
test('public bundle ships all parser/transitive license notices without adding a private cache resource', async () => {
  const html = await readFile(new URL('../.test-dist/test/index.html', import.meta.url), 'utf8');
  const file = html.match(/src="\/console-assets\/(public-chat-[A-Z0-9]+\.js)"/)![1];
  const bytes = await readFile(new URL(`../.test-dist/assets/${file}`, import.meta.url), 'utf8');
  const banner = bytes.slice(0, bytes.indexOf('*/'));
  assert.match(banner, /Third-party licenses/);
  for (const name of ['markdown-it', 'mdurl', 'argparse', 'entities', 'uc.micro', 'linkify-it', 'punycode.js']) assert.ok(banner.includes(name));
  assert.match(banner, /Copyright/i); assert.match(banner, /Permission/i);
  assert.doesNotMatch(bytes, /sourceMappingURL=/);
});
