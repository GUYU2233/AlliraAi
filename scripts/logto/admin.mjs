#!/usr/bin/env node
/** Localhost-only bootstrap of the pinned upstream Admin Console. No secrets on stdout. */
import { createRequire } from 'node:module';
import { open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { randomBytes } from 'node:crypto';

const root = '/var/lib/allira/logto';
const require = createRequire('/opt/allira/workspace/apps/dev-console/package.json');
const { chromium } = require('@playwright/test');
const executablePath = '/home/allira/.cache/ms-playwright/chromium-1255/chrome-linux64/chrome';
const action = process.argv[2] ?? 'inspect';
if (process.getuid() === 0) throw new Error('Run as allira');
process.umask(0o077);
async function privateJson(path) {
  const handle = await open(path,constants.O_RDONLY|constants.O_NOFOLLOW);
  try {
    const metadata=await handle.stat();
    if(!metadata.isFile() || metadata.uid!==process.getuid() || (metadata.mode&0o077) || metadata.nlink!==1) throw new Error('Unsafe private admin file');
    return JSON.parse(await handle.readFile('utf8'));
  } finally { await handle.close(); }
}
async function privateWrite(path,content,{exclusive=false}={}) {
  const handle=await open(path,constants.O_WRONLY|constants.O_CREAT|constants.O_NOFOLLOW|(exclusive?constants.O_EXCL:0),0o600);
  try {
    const metadata=await handle.stat();
    if(!metadata.isFile() || metadata.uid!==process.getuid() || (metadata.mode&0o077) || metadata.nlink!==1) throw new Error('Unsafe private admin output');
    await handle.truncate(0);await handle.writeFile(content,'utf8');await handle.sync();
  } finally { await handle.close(); }
}
const browser = await chromium.launch({ executablePath, headless: true, args: ['--disable-background-networking'] });
let blocked = 0;
let adminToken;
try {
  const context = await browser.newContext();
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === '127.0.0.1' && ['3001', '3002'].includes(url.port)) return route.continue();
    blocked += 1;
    return route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  page.on('request', (request) => {
    const url = new URL(request.url());
    const authorization = request.headers().authorization;
    if (url.hostname === '127.0.0.1' && url.pathname.startsWith('/api/') && authorization?.startsWith('Bearer ')) {
      adminToken = authorization.slice(7);
    }
  });
  await page.goto('http://127.0.0.1:3002', { waitUntil: 'networkidle' });
  if (['inspect-sign-up', 'bootstrap'].includes(action)) {
    await page.getByRole('button', { name: 'Create account', exact: true }).click();
    await page.waitForURL('**/register**');
    await page.waitForLoadState('networkidle');
  }
  if (action.startsWith('inspect')) {
    console.log(JSON.stringify({ path: new URL(page.url()).pathname, text: await page.locator('body').innerText(), inputs: await page.locator('input').evaluateAll((items) => items.map((item) => ({ type: item.type, name: item.name, placeholder: item.placeholder }))), buttons: await page.getByRole('button').allTextContents(), blockedExternalRequests: blocked }));
  } else if (action === 'bootstrap') {
    const credentialPath = `${root}/admin-credentials.json`;
    let credentials;
    try {
      credentials = await privateJson(credentialPath);
    } catch (error) {
      if (error?.code !== 'ENOENT') throw error;
      credentials = { username: `allira_admin_${randomBytes(8).toString('hex')}`, password: `Aa9!${randomBytes(32).toString('base64url')}` };
      await privateWrite(credentialPath,JSON.stringify(credentials),{exclusive:true});
    }
    await page.locator('input[name="identifier"]').fill(credentials.username);
    await page.getByRole('button', { name: 'Create account', exact: true }).click();
    await page.locator('input[name="newPassword"]').waitFor({ timeout: 15000 });
    await page.waitForLoadState('networkidle');
    await page.locator('input[name="newPassword"]').fill(credentials.password);
    await page.locator('input[name="confirmPassword"]').fill(credentials.password);
    await page.getByRole('button', { name: 'Save password', exact: true }).click();
    await page.waitForURL('**/console/**', { timeout: 30000 });
    await page.waitForLoadState('networkidle');
    if (!adminToken) throw new Error('No authenticated Management API request observed');
    await privateWrite(`${root}/admin-access-token`,adminToken);
    console.log(JSON.stringify({ adminCreated: true, path: new URL(page.url()).pathname, adminTokenStoredPrivately: true, blockedExternalRequests: blocked }));
  } else {
    throw new Error('Unsupported action');
  }
  await context.close();
} catch (error) {
  console.error(JSON.stringify({error:error?.name??'Error',message:'Admin bootstrap failed; private browser diagnostics suppressed'}));
  process.exitCode = 1;
} finally {
  await browser.close();
}
