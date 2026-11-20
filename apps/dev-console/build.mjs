import { build } from 'esbuild';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { serviceWorkerSource } from './sw-policy.mjs';
import { appIcon } from './icon.mjs';

// Route interception builds the same shell outside the directory served by FastAPI.
const output = process.argv.includes('--test') ? '.test-dist' : 'dist';
const assetDir = `${output}/assets`;
await mkdir(assetDir, { recursive: true });
await Promise.all(['test', 'admin'].map(name => mkdir(`${output}/${name}`, { recursive: true })));
// Include the package licenses in the downloadable bundles as well as npm's notices.
const markdownPackages = ['markdown-it', 'mdurl', 'argparse', 'entities', 'uc.micro', 'linkify-it', 'punycode.js'];
const notices = await Promise.all(markdownPackages.map(async name => {
  const dir = new URL(`./node_modules/${name}/`, import.meta.url);
  const files = (await readdir(dir, { withFileTypes: true }))
    .filter(entry => entry.isFile() && /^(licen[cs]e|copying|notice)(?:$|[._-])/i.test(entry.name))
    .map(entry => entry.name).sort();
  if (files.length === 0) throw new Error(`Missing dependency license: ${name}`);
  const texts = await Promise.all(files.map(file => readFile(new URL(file, dir), 'utf8')));
  return `${name}\n${texts.join('\n')}`;
}));
const result = await build({
  entryPoints: { 'public-chat': 'src/chat.ts', admin: 'src/admin.ts' },
  entryNames: '[name]-[hash]',
  outdir: assetDir,
  bundle: true, format: 'esm', target: 'es2022', minify: true, sourcemap: false,
  legalComments: 'inline', metafile: true,
  banner: { js: `/*! Third-party licenses\n${notices.join('\n\n').replaceAll('*/', '* /')}\n*/` },
});
const outputs = Object.keys(result.metafile.outputs);
const asset = (entry, suffix) => {
  const file = outputs.find(path => path.startsWith(`${assetDir}/${entry}-`) && path.endsWith(suffix));
  if (!file) throw new Error(`Missing ${entry} ${suffix}`);
  return `/console-assets/${file.slice(assetDir.length + 1)}`;
};
const chat = { js: asset('public-chat', '.js'), css: asset('public-chat', '.css') };
const admin = { js: asset('admin', '.js'), css: asset('admin', '.css') };
function html(title, files, publicPage = false) {
  return `<!doctype html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<meta name="referrer" content="no-referrer">\n<meta name="theme-color" content="#343a40">\n<title>${title} · AlliraAi</title>\n${publicPage ? '<link rel="manifest" href="/test/manifest.webmanifest">\n' : ''}<link rel="stylesheet" href="${files.css}">\n<script type="module" src="${files.js}"></script>\n</head>\n<body><main id="app" aria-label="${title}"><p>正在加载页面…</p></main></body>\n</html>\n`;
}
const chatHtml = html('测试客户端', chat, true);
await writeFile(`${output}/test/index.html`, chatHtml);
await writeFile(`${output}/admin/index.html`, html('后台管理', admin));
// Same-origin PNG icons work with img-src 'self'; no data: CSP exception is needed.
const icons = await Promise.all([192, 512].map(async size => {
  const bytes = appIcon(size);
  const hash = createHash('sha256').update(bytes).digest('hex').slice(0, 16);
  const file = `public-icon-${size}-${hash}.png`;
  await writeFile(`${assetDir}/${file}`, bytes);
  return { src: `/console-assets/${file}`, sizes: `${size}x${size}`, type: 'image/png', purpose: 'any' };
}));
await writeFile(`${output}/test/manifest.webmanifest`, JSON.stringify({ id: '/test/', name: 'AlliraAi 测试客户端', short_name: 'AlliraAi', lang: 'zh-CN', start_url: '/test/', scope: '/test/', display: 'standalone', background_color: '#ffffff', theme_color: '#343a40', icons }));
const paths = ['/test/', '/test/manifest.webmanifest', chat.js, chat.css, ...icons.map(icon => icon.src)];
const hash = createHash('sha256').update(paths.join('\n') + chatHtml).digest('hex').slice(0, 16);
await writeFile(`${output}/test/sw.js`, serviceWorkerSource(paths, `allira-public-shell-${hash}`));
if (process.argv.includes('--test')) await build({ entryPoints: ['tests/browser-harness.ts'], outfile: '.test-dist/harness.js', bundle: true, format: 'esm', target: 'es2022', sourcemap: false });
// Keep historical interception builds unchanged; deployment uses the independent Vite admin.
if (!process.argv.includes('--test')) {
  const { execFileSync } = await import('node:child_process');
  const { copyFile } = await import('node:fs/promises');
  const path = await import('node:path');
  const { fileURLToPath } = await import('node:url');
  const viteRoot = fileURLToPath(new URL('../admin-console/', import.meta.url));
  execFileSync(process.execPath, [path.join(viteRoot, 'build.mjs')], { cwd: viteRoot, stdio: 'inherit' });
  const viteDist = path.join(viteRoot, 'dist');
  for (const entry of await readdir(viteDist)) {
    if (/^admin-[A-Za-z0-9_-]+\.(js|css)$/.test(entry)) await copyFile(path.join(viteDist, entry), path.join(assetDir, entry));
  }
  await copyFile(path.join(viteDist, 'index.html'), `${output}/admin/index.html`);
}
console.log(`Built /test/, /admin/ and the public PWA shell in ${output}.`);
