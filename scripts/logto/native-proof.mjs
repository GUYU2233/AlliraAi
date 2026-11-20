#!/usr/bin/env node
/** Real local Logto hosted UI + signed OIDC. Official mock transport only; never Allira API. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { open } from 'node:fs/promises';
import { constants } from 'node:fs';
import { randomBytes, createHash } from 'node:crypto';

if (process.getuid() === 0) throw new Error('Run as allira');
if (process.env.ALLIRA_LOGTO_MAINTENANCE !== '1') throw new Error('Explicit maintenance opt-in required');
process.umask(0o077);
const require = createRequire('/opt/allira/workspace/apps/dev-console/package.json');
const { chromium } = require('@playwright/test');
const base = '/var/lib/allira/logto';
const coreRequire = createRequire(`${base}/releases/v1.46.0/packages/core/package.json`);
const { jwtVerify, createLocalJWKSet } = await import(coreRequire.resolve('jose'));
const endpoint = 'http://127.0.0.1:3001';
const issuer = `${endpoint}/oidc`;
const callback = 'http://127.0.0.1:8000/api/v1/auth/oidc/callback';
async function privateWrite(path,content) {
  const handle=await open(path,constants.O_WRONLY|constants.O_CREAT|constants.O_NOFOLLOW,0o600);
  try {
    const info=await handle.stat();
    assert(info.isFile() && info.uid===process.getuid() && (info.mode&0o077)===0 && info.nlink===1,'Unsafe private proof output');
    await handle.truncate(0);await handle.writeFile(content,'utf8');await handle.sync();
  } finally {await handle.close();}
}
async function privateText(path,{minMtimeMs}={}) {
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const info = await handle.stat();
    assert(info.isFile() && info.uid === process.getuid() && (info.mode & 0o077) === 0 && info.nlink === 1, 'Unsafe private file');
    if(minMtimeMs!==undefined) assert(info.mtimeMs>=minMtimeMs-1,'Stale official mock record');
    return await handle.readFile('utf8');
  } finally { await handle.close(); }
}
const env = Object.fromEntries((await privateText(`${base}/runtime.env`)).split('\n').filter(Boolean).map((line) => { const index = line.indexOf('='); return [line.slice(0, index), line.slice(index + 1)]; }));
const action = process.argv[2] ?? 'inspect';
const phoneLocal = '139' + (BigInt('0x'+randomBytes(4).toString('hex'))%100000000n).toString().padStart(8,'0');
const phoneDigits = '86' + phoneLocal;
const verifier = randomBytes(48).toString('base64url');
const state = randomBytes(24).toString('base64url');
const nonce = randomBytes(24).toString('base64url');
const url = new URL(`${issuer}/auth`);
for (const [key, value] of Object.entries({client_id: env.ALLIRA_LOGTO_CLIENT_ID, redirect_uri: callback, response_type:'code', scope:'openid profile phone', state, nonce, code_challenge:createHash('sha256').update(verifier).digest('base64url'), code_challenge_method:'S256', prompt:'login', max_age:'0'})) url.searchParams.set(key,value);
const browser = await chromium.launch({ executablePath:'/home/allira/.cache/ms-playwright/chromium-1255/chrome-linux64/chrome',headless:true,args:['--disable-background-networking'] });
let callbackUrl;
let resolveCallback;
const callbackArrived = new Promise((resolve) => { resolveCallback = resolve; });
const routedPaths=[];
let externalBlocked = 0;
let smsRequestedAt;
try {
  const context = await browser.newContext();
  await context.route('**/*',async(route) => {
    const requestUrl = new URL(route.request().url());
    if(requestUrl.origin==='http://127.0.0.1:8000') routedPaths.push(requestUrl.pathname);
    if (requestUrl.origin === 'http://127.0.0.1:8000' && requestUrl.pathname === '/api/v1/auth/oidc/callback') {
      callbackUrl = requestUrl;
      resolveCallback();
      return route.fulfill({status:200,contentType:'text/plain',body:'Local OIDC test callback intercepted; no Allira API request.'});
    }
    if (requestUrl.origin === endpoint) return route.continue();
    if (requestUrl.origin === 'http://mock-social') {
      const redirect = new URL(requestUrl.searchParams.get('redirect_uri'));
      assert.equal(redirect.origin, endpoint);
      redirect.searchParams.set('state',requestUrl.searchParams.get('state'));
      redirect.searchParams.set('code','official-mock-transport');
      redirect.searchParams.set('userId',`allira-native-social-${state}`);
      return route.fulfill({status:302,headers:{location:redirect.href},body:''});
    }
    externalBlocked += 1;
    return route.abort('blockedbyclient');
  });
  const page = await context.newPage();
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.setBlockedURLs',{urls:['http://127.0.0.1:8000/*']});
  cdp.on('Network.requestWillBeSent',({request})=>{
    const candidate=new URL(request.url);
    if(candidate.origin==='http://127.0.0.1:8000' && candidate.pathname==='/api/v1/auth/oidc/callback') {
      callbackUrl=candidate;
      resolveCallback();
    }
  });
  await page.goto(url.href,{waitUntil:'networkidle'});
  if (['social-inspect','social-code-inspect','social'].includes(action)) {
    await page.locator('button').filter({hasText:'Continue with Mock Social'}).click();
    await page.waitForFunction(() => location.pathname !== '/sign-in', { timeout: 15000 });
    await page.waitForLoadState('networkidle');
  }
  if (['social-code-inspect','social'].includes(action)) {
    assert.equal(callbackUrl,undefined,'No OIDC callback before phone verification');
    await page.getByRole('button',{name:'+1',exact:true}).click();
    await page.locator('input[name="country-code-search"]').fill('86');
    await page.getByRole('button',{name:'+86',exact:true}).click();
    await page.locator('input[name="identifier"]').fill(phoneLocal);
    smsRequestedAt=Date.now();
    await page.getByRole('button',{name:'Continue',exact:true}).click();
    await page.waitForURL('**/verification-code**',{timeout:15000});
    await page.waitForLoadState('networkidle');
  }
  if(action==='country-inspect') {
    await page.getByRole('button',{name:'+1',exact:true}).click();
  }
  if (['inspect','social-inspect','social-code-inspect','country-inspect'].includes(action)) {
    console.log(JSON.stringify({path:new URL(page.url()).pathname,text:await page.locator('body').innerText(),inputs:await page.locator('input').evaluateAll((items)=>items.map((item)=>({type:item.type,name:item.name,placeholder:item.placeholder}))),buttons:await page.getByRole('button').evaluateAll((items)=>items.map((item)=>({text:item.textContent,ariaLabel:item.getAttribute('aria-label'),title:item.getAttribute('title'),type:item.type}))),links:await page.getByRole('link').allTextContents(),externalBlocked}));
  } else if (action === 'social') {
    assert.equal(callbackUrl,undefined,'Phone gate must not complete OIDC before code proof');
    const mock = JSON.parse(await privateText('/tmp/logto/mock_sms_record.txt',{minMtimeMs:smsRequestedAt}));
    assert.equal(mock.phone,phoneDigits,'Official mock record must match this fixture');
    assert.equal(mock.type,'Register','Official social completion uses Register template');
    assert.match(mock.code,/^\d{6}$/);
    for (let index=0;index<6;index+=1) await page.locator(`input[name="passcode_${index}"]`).fill(mock.code[index]);
    // Upstream auto-submits when the sixth digit is entered; do not race its disabled button.
    await Promise.race([callbackArrived,new Promise((_,reject)=>setTimeout(()=>reject(new Error('Callback redirect not intercepted; currentPath='+new URL(page.url()).pathname)),12000))]);
    assert(callbackUrl,'OIDC callback required after proof');
    assert.equal(callbackUrl.searchParams.get('state'),state);
    assert.equal(callbackUrl.searchParams.get('iss'),issuer);
    assert(!callbackUrl.searchParams.has('error'));
    const basic=Buffer.from(`${env.ALLIRA_LOGTO_CLIENT_ID}:${env.ALLIRA_LOGTO_CLIENT_SECRET}`).toString('base64');
    const tokenResponse=await fetch(`${issuer}/token`,{method:'POST',headers:{authorization:`Basic ${basic}`,'content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'authorization_code',code:callbackUrl.searchParams.get('code'),redirect_uri:callback,code_verifier:verifier})});
    assert(tokenResponse.ok,'Real code exchange must succeed');
    const tokens=await tokenResponse.json();
    const discovery=await (await fetch(`${issuer}/.well-known/openid-configuration`)).json();
    const jwks=await (await fetch(discovery.jwks_uri)).json();
    const {payload,protectedHeader}=await jwtVerify(tokens.id_token,createLocalJWKSet(jwks),{issuer,audience:env.ALLIRA_LOGTO_CLIENT_ID,algorithms:['ES384']});
    assert.equal(protectedHeader.alg,'ES384');
    assert.equal(payload.nonce,nonce);
    assert.equal(typeof payload.auth_time,'number');
    const userinfoResponse=await fetch(discovery.userinfo_endpoint,{headers:{authorization:`Bearer ${tokens.access_token}`}});
    assert(userinfoResponse.ok);
    const userinfo=await userinfoResponse.json();
    assert.equal(userinfo.sub,payload.sub);
    assert.equal(userinfo.phone_number_verified,true);
    assert.equal(userinfo.phone_number,phoneDigits,'Logto phone claim uses country-code digits without plus');
    const m2m=await fetch(`${issuer}/token`,{method:'POST',headers:{authorization:`Basic ${Buffer.from(`${env.ALLIRA_LOGTO_MANAGEMENT_ID}:${env.ALLIRA_LOGTO_MANAGEMENT_SECRET}`).toString('base64')}`,'content-type':'application/x-www-form-urlencoded'},body:new URLSearchParams({grant_type:'client_credentials',resource:'https://default.logto.app/api',scope:'all'})});
    assert(m2m.ok);
    const management=(await m2m.json()).access_token;
    const password=await (await fetch(`${endpoint}/api/users/${encodeURIComponent(payload.sub)}/has-password`,{headers:{authorization:`Bearer ${management}`}})).json();
    assert.equal(password.hasPassword,false);
    await privateWrite(`${base}/native-proof-users.json`,JSON.stringify({ids:[payload.sub]}));
    await privateWrite(`${base}/native-proof.json`,JSON.stringify({testedAt:new Date().toISOString(),officialHostedUi:true,officialMockSocial:true,officialMockSms:true,noAlliraApiRequests:true,requiredPhoneBeforeCallback:true,signedIssuerValidated:true,pkceS256:true,signatureAlgorithm:protectedHeader.alg,nonceValidated:true,authTimePresent:true,phoneClaimVerifiedTrue:true,passwordRequired:false,externalBlocked},null,2));
    console.log(JSON.stringify({nativeProofPassed:true,requiredPhoneBeforeCallback:true,realOidcCodeExchange:true,signedIssuerValidated:true,signatureAlgorithm:protectedHeader.alg,pkceS256:true,nonceValidated:true,authTimePresent:true,phoneClaimVerifiedTrue:true,passwordRequired:false,noAlliraApiRequests:true,realSms:false,realTencent:false,externalBlocked}));
  } else { throw new Error('Unsupported native proof action'); }
  await context.close();
} catch (error) {
  console.error(JSON.stringify({error:error.name,message:'Native Logto proof failed; private assertion/browser details suppressed'}));
  process.exitCode=1;
} finally { await browser.close(); }
