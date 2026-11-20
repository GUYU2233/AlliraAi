import { build } from 'vite';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const root = fileURLToPath(new URL('.', import.meta.url));
const output = process.argv.includes('--test') ? '.test-dist' : 'dist';
const outDir = path.join(root, output);
// Read actual locked packages' full license files; notices ship inside the sole JS asset.
const lock = JSON.parse(await readFile(path.join(root, 'package-lock.json'), 'utf8'));
const runtime = Object.entries(lock.packages).filter(([name, info]) => name.startsWith('node_modules/') && info.dev !== true && info.link !== true);
const notices = [];
for (const [relative, info] of runtime.sort(([left], [right]) => left.localeCompare(right))) {
  const dir = path.join(root, relative);
  const names = (await readdir(dir, { withFileTypes: true })).filter(entry => entry.isFile() && /^(licen[cs]e|copying|notice)(?:$|[._-])/i.test(entry.name)).map(entry => entry.name).sort();
  if (!names.length && relative === 'node_modules/lodash-unified' && info.version === '1.0.3' && info.license === 'MIT') {
    notices.push(await readFile(path.join(root, 'third-party-lodash-unified.txt'), 'utf8'));
    continue;
  }
  if (!names.length) throw new Error(`Missing bundled runtime license: ${relative}`);
  const texts = await Promise.all(names.map(name => readFile(path.join(dir, name), 'utf8')));
  notices.push(`${relative.slice('node_modules/'.length)} ${info.version}\n${texts.join('\n')}`);
}
const banner = `/*! AlliraAi admin third-party license notices\n${notices.join('\n\n').replaceAll('*/', '* /')}\n*/`;
await build({
  root, configFile: path.join(root, 'vite.config.mjs'),
  build: {
    outDir, emptyOutDir: true, sourcemap: false, minify: true, target: 'es2022',
    cssCodeSplit: false, assetsInlineLimit: 0,
    lib: { entry: path.join(root, 'src/main.ts'), name: 'AlliraAdmin', formats: ['es'] },
    rolldownOptions: { output: { entryFileNames: 'admin-[hash].js', chunkFileNames: 'admin-[hash].js', assetFileNames: 'admin-[hash][extname]', codeSplitting: false, banner } },
  },
});
const outputs = (await readdir(outDir, { withFileTypes: true }));
if (outputs.some(entry => !entry.isFile())) throw new Error('Admin output must contain exactly two flat assets');
const names = outputs.map(entry => entry.name).sort();
const js = names.filter(name => /^admin-[A-Za-z0-9_-]+\.js$/.test(name));
const css = names.filter(name => /^admin-[A-Za-z0-9_-]+\.css$/.test(name));
if (names.length !== 2 || js.length !== 1 || css.length !== 1) throw new Error('Admin output must contain one hash JS and one hash CSS');
const jsBytes = await readFile(path.join(outDir, js[0]));
const cssBytes = await readFile(path.join(outDir, css[0]));
if (!jsBytes.includes(Buffer.from('AlliraAi admin third-party license notices'))) throw new Error('Admin license banner was not retained');
const html = `<!doctype html>\n<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="referrer" content="no-referrer"><meta name="theme-color" content="#627be5"><title>AlliraAi · 管理控制台</title><link rel="stylesheet" href="/console-assets/${css[0]}"><script type="module" src="/console-assets/${js[0]}"></script></head><body><div id="app"><p>正在加载管理工作空间…</p></div></body></html>\n`;
await writeFile(path.join(outDir, 'index.html'), html);
const metadata = { tool: 'vite', version: JSON.parse(await readFile(path.join(root, 'node_modules/vite/package.json'), 'utf8')).version, js: js[0], css: css[0], sha256: { js: createHash('sha256').update(jsBytes).digest('hex'), css: createHash('sha256').update(cssBytes).digest('hex') }, runtimeLicensePackages: runtime.length };
await mkdir(path.join(root, '.build-metadata'), { recursive: true });
await writeFile(path.join(root, '.build-metadata', `${output.slice(0, 1) === '.' ? 'test-' : ''}manifest.json`), JSON.stringify(metadata, null, 2) + '\n');
await writeFile(path.join(root, '.build-metadata', 'THIRD-PARTY-LICENSES.txt'), notices.join('\n\n'));
console.log(`Vite admin built ${output}: one JS, one CSS, ${runtime.length} runtime license notices.`);
