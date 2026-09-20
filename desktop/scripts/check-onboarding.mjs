// Electron + standalone + real local backend. Text/DOM only; no screenshots.
// Run after prepare:frontend; playwright can be provided through NODE_PATH.
import { createRequire } from 'node:module';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { _electron, chromium } = require('playwright');
const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const profile = mkdtempSync(join(tmpdir(), 'excelmanus-onboarding-'));
const env = { ...process.env, EXCELMANUS_HOME: profile };
for (const name of ['EXCELMANUS_DB_PATH','EXCELMANUS_DATA_ROOT','EXCELMANUS_CHAT_HISTORY_DB_PATH','EXCELMANUS_MANAGE_TOKEN','ELECTRON_RUN_AS_NODE']) delete env[name];
const app = await _electron.launch({executablePath:require('electron'), args:[join(root,'src/main.js')], env, timeout:60000});
let browser;
try {
  await app.context().route('**/*', route => route.request().resourceType()==='image' ? route.abort() : route.continue());
  const page = await app.firstWindow({timeout:60000});
  await page.getByRole('button',{name:'开始设置',exact:true}).waitFor({timeout:45000});
  const runtime = await page.evaluate(() => ({frontend:location.origin,backend:window.__EXCELMANUS_RUNTIME__?.backendOrigin}));
  assert(runtime.backend && runtime.backend !== runtime.frontend,'desktop runtime origin must use the allocated backend port');
  const health = await (await fetch(`${runtime.backend}/api/v1/health`)).json();
  assert.equal(health.configured,false);
  const doneWrite = page.waitForResponse(res => res.url().endsWith('/onboarding') && res.request().postDataJSON()?.coach_phase==='done');
  await page.getByRole('button',{name:'结束引导',exact:true}).click(); await doneWrite;
  await page.reload(); await page.locator('.em-app-shell').waitFor();
  assert.equal(await page.locator('.em-onboarding,.em-tour-card').count(),0);
  // Same installation, separate desktop browser: progress is server-owned.
  browser = await chromium.launch({headless:true});
  const web = await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  await web.route('**/*', route => route.request().resourceType()==='image' ? route.abort() : route.continue());
  await web.goto(runtime.frontend); await web.locator('.em-app-shell').waitFor();
  assert.equal(await web.locator('.em-onboarding,.em-tour-card').count(),0);
  // Resume the settings chapter using the same persisted schema as the replay UI.
  const res = await fetch(`${runtime.backend}/api/v1/onboarding`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({wizard_completed:true,coach_marks_completed:true,advanced_guide_completed:true,settings_guide_completed:false,coach_phase:'settings',coach_step_index:0})});
  assert(res.ok);
  await page.reload(); await page.locator('.em-tour-card').waitFor();
  await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setContentSize(1024,700));
  await page.locator('.em-settings-dialog').waitFor();
  assert.equal(await page.getByRole('heading',{name:'模型配置未完成',exact:true}).count(),0);
  await page.locator('.em-tour-practice select').focus();
  assert.equal(await page.locator('.em-tour-practice select').evaluate(el=>document.activeElement===el),true);
  await page.locator('.em-tour-practice select').selectOption({label:'模型 B · 复杂分析'});
  await page.waitForFunction(()=>document.querySelector('.em-tour-status')?.textContent.includes('已完成练习'));
  await page.getByRole('button',{name:'跳过此步',exact:true}).click();
  await page.getByRole('button',{name:'上一步',exact:true}).click();
  await page.getByRole('heading',{name:'管理模型供应商',exact:true}).waitFor();
  await page.keyboard.press('Escape');
  await page.locator('.em-tour-card').waitFor({state:'detached'});
  const saved = await (await fetch(`${runtime.backend}/api/v1/health`)).json();
  assert.equal(saved.onboarding.coach_phase,'done');
  console.log(JSON.stringify({status:'passed',surface:'Electron sandbox + staged standalone frontend + bundled backend',size:'1024x700',sharedBrowser:'390x844',missingModelSkip:true,realPersistence:true,settingsFocus:true,escape:true,imagesRead:0,profile}));
} finally { await browser?.close(); await app.close(); }
