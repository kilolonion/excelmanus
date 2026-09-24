// Text/DOM-only checks. Never captures, reads, or emits images.
// Supply playwright via NODE_PATH, and start the built web app first.
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium, webkit } = require('playwright');
const url = process.env.ONBOARDING_TEST_URL || 'http://127.0.0.1:3197';
const engines = process.env.ONBOARDING_WEBKIT === '1' ? [webkit] : [chromium];
const sizes = process.env.ONBOARDING_CONFIG_ONLY === '1' ? [] : [[320,568], [390,844], [844,390], [768,1024], [1024,700], [1440,900]];
const originalState = { wizard_completed: false, coach_marks_completed: false, advanced_guide_completed: false, settings_guide_completed: false, coach_phase:'basic', coach_step_index:0, skipped_at:null };

async function inside(page, selector) {
  await page.waitForFunction((selector) => {
    const el = document.querySelector(selector);
    if (!el) return false;
    const r = el.getBoundingClientRect(); const v = window.visualViewport;
    const left = v?.offsetLeft || 0, top = v?.offsetTop || 0;
    return r.left >= left - 1 && r.top >= top - 1 && r.right <= left + (v?.width || innerWidth) + 1 && r.bottom <= top + (v?.height || innerHeight) + 1;
  }, selector, { timeout: 5000 });
}

async function installApi(context, initial = originalState) {
  let state = structuredClone(initial);
  const writes = [];
  let configured = false;
  await context.route('**/*', async route => {
    const req = route.request(), p = new URL(req.url()).pathname;
    if (req.resourceType() === 'image') return route.abort();
    if (!p.startsWith('/api/v1/')) return route.continue();
    if (req.method() !== 'GET' && req.method() !== 'OPTIONS') writes.push({path:p, body:req.postDataJSON()});
    let data = {};
    if (p.endsWith('/health')) data = {status:'ok',version:'1.8.1',configured,onboarding:state,deploy_mode:'standalone',tools:[],skillpacks:[],active_sessions:0};
    else if (p === '/api/v1/config/models/profiles' && req.method() === 'POST') { configured = true; data = {status:'created'}; }
    else if (p === '/api/v1/onboarding') { state = req.postDataJSON(); data = state; }
    else if (p === '/api/v1/sessions' && req.method() === 'GET' || p === '/api/v1/skills') data = [];
    else if (p === '/api/v1/models') data = {models:[],active_model:''};
    else if (p === '/api/v1/config/models') data = {profiles:[],active:null};
    else if (p === '/api/v1/config/models/capabilities/all') data = {items:[]};
    else if (p === '/api/v1/config/runtime') data = {jev_enabled:'off',jev_providers:[]};
    else if (p === '/api/v1/config/models/check-placeholder') data = {has_placeholder:!configured,items:configured ? [] : [{name:'未配置模型',model:'',field:'api_key'}]};
    else if (p === '/api/v1/mcp/servers') data = {servers:[]};
    else if (p.includes('/rules')) data = {rules:[]};
    else if (p.includes('/memory')) data = {entries:[],categories:[]};
    else if (p.includes('version/check')) data = {current:'1.8.1',latest:'1.8.1',has_update:false,check_method:'desktop_installer'};
    else if (p.includes('version/backups')) data = {backups:[]};
    else if (p.includes('version/installations')) data = {installations:[]};
    await route.fulfill({json:data});
  });
  return { writes, getState: () => state };
}

async function practice(page, step) {
  const panel = page.locator('.em-tour-practice');
  if (!await panel.count()) {
    const expand = page.getByRole('button',{name:'展开互动练习',exact:true});
    if (!await expand.count()) return;
    await expand.click();
    await panel.waitFor();
  }
  const prompt = panel.locator('input');
  const select = panel.locator('select');
  if (await prompt.count()) {
    await prompt.fill(step.includes('快捷') ? '/plan' : '金额保留两位小数');
    if (await panel.locator('button[type="submit"]').count()) await panel.locator('button[type="submit"]').click();
  } else if (await select.count()) await select.selectOption({label:'模型 B · 复杂分析'});
  else if (step.includes('发送')) {
    await panel.getByRole('button',{name:'发送演示'}).click();
    await panel.getByRole('button',{name:'暂停演示'}).click();
  } else if (step.includes('切换工作表')) {
    await panel.getByRole('button',{name:'汇总',exact:true}).click();
    await panel.getByRole('button',{name:'选择 汇总 B2'}).click();
  } else await panel.locator('button').last().click();
  await page.waitForFunction(() => document.querySelector('.em-tour-status')?.textContent.includes('已完成练习'));
}

for (const engine of engines) {
  const browser = await engine.launch({ headless: true });
  try {
    for (const [width,height] of sizes) {
      const context = await browser.newContext({viewport:{width,height},hasTouch:width<1024,isMobile:width<768,colorScheme:width===768?'dark':'light',reducedMotion:width===320?'reduce':'no-preference'});
      const api = await installApi(context);
      const page = await context.newPage();
      const errors=[];page.on('pageerror',e=>errors.push(e.message));
      await page.goto(url);
      if (width === 768) await page.evaluate(() => document.documentElement.classList.add('dark'));
      await page.getByRole('button',{name:'开始设置',exact:true}).waitFor();
      await inside(page,'.em-onboarding');
      assert.equal(await page.locator('.em-onboarding-content').evaluate(el=>el.scrollWidth>el.clientWidth+1), false, 'wizard horizontal overflow');
      await page.getByRole('button',{name:'跳过此步',exact:true}).click();
      await page.getByRole('heading',{name:'连接一个 AI 模型'}).waitFor();
      await page.getByRole('button',{name:'使用 DeepSeek',exact:true}).click();
      await page.getByLabel('API Key',{exact:true}).fill('test-only-not-a-real-key');
      await page.getByRole('button',{name:'显示 API Key',exact:true}).click();
      assert.equal(await page.getByLabel('API Key',{exact:true}).getAttribute('type'),'text');
      await inside(page,'.em-onboarding');
      await page.getByRole('button',{name:'跳过此步',exact:true}).click();
      await page.getByRole('heading',{name:'先体验功能，模型稍后连接'}).waitFor();
      await page.getByRole('button',{name:'体验功能引导',exact:true}).click();
      await page.locator('.em-tour-card').waitFor();
      let steps=0;
      for (const [chapter,count] of [['basic',6],['advanced',4],['settings',9]]) {
        if (chapter !== 'basic') await page.getByLabel('选择引导章节').selectOption(chapter);
        for (let i=0;i<count;i++) {
          await page.waitForFunction(([chapter,index]) => document.querySelector('#tour-chapter')?.value===chapter && document.querySelector('[role="progressbar"]')?.getAttribute('aria-valuenow')===String(index+1),[chapter,i]);
          await inside(page,'.em-tour-card');
          await inside(page,'.em-tour-navigation');
          assert.equal(await page.locator('.em-tour-card').evaluate(el=>el.scrollWidth>el.clientWidth+1),false,'tour horizontal overflow');
          assert.equal(await page.locator('.em-tour-navigation button').evaluateAll(buttons=>buttons.every(el=>el.getBoundingClientRect().height>=44)),true,'touch targets');
          const title=await page.locator('#tour-title').innerText();
          if(chapter==='settings') await page.locator('.em-settings-dialog').waitFor();
          await practice(page,title);
          if (i===2 && chapter==='basic') {
            // Simulate a keyboard reducing the visual viewport; rotation must preserve the current step.
            await page.setViewportSize({width,height:280});
            await inside(page,'.em-tour-card');await inside(page,'.em-tour-navigation');
            assert.equal(await page.locator('#tour-title').innerText(),title);
            await page.setViewportSize({width,height});
          }
          steps++;
          if (i<count-1) await page.getByRole('button',{name:'下一步',exact:true}).click();
        }
      }
      const saved = page.waitForResponse(res=>res.url().endsWith('/onboarding') && res.request().postDataJSON()?.coach_phase==='done');
      await page.getByRole('button',{name:'结束引导',exact:true}).click();
      await saved;
      await page.locator('.em-tour-card').waitFor({state:'detached'});
      assert.equal(api.getState().coach_phase,'done');
      await page.reload();
      await page.locator('.em-app-shell').waitFor();
      assert.equal(await page.locator('.em-onboarding,.em-tour-card,.em-tour-transition').count(),0,'skip persists across reload');
      assert.equal(await page.getByRole('heading',{name:'模型配置未完成',exact:true}).count(),0,'skip must not open another blocking configuration alert');
      assert.equal(api.writes.filter(w=>w.path.includes('/chat') || w.path.includes('/config/') || w.path.includes('/files/')).length,0,'practice must not send model/file/config mutations');
      assert.deepEqual(errors,[]);
      console.log(JSON.stringify({engine:engine.name(),width,height,steps,status:'passed',imagesRead:0}));
      await context.close();
    }
    const configuredContext = await browser.newContext({viewport:{width:390,height:844},hasTouch:true});
    await installApi(configuredContext);
    const configuredPage = await configuredContext.newPage();
    await configuredPage.goto(url);
    await configuredPage.getByRole('button',{name:'开始设置',exact:true}).click();
    await configuredPage.getByRole('button',{name:'使用 DeepSeek',exact:true}).click();
    await configuredPage.getByLabel('API Key',{exact:true}).fill('test-only-not-a-real-key');
    await configuredPage.getByRole('button',{name:'保存并继续',exact:true}).click();
    await configuredPage.getByRole('heading',{name:'模型已就绪，来试试吧',exact:true}).waitFor();
    await configuredPage.getByRole('button',{name:'跳过引导，直接进入工作区',exact:true}).click();
    await configuredPage.locator('[data-coach-id="coach-chat-input"] textarea').fill('只检查发送按钮，不发送任务');
    await configuredPage.waitForFunction(()=>document.querySelector('[data-coach-id="coach-send-btn"]')?.disabled===false);
    assert.equal(await configuredPage.getByRole('heading',{name:'模型配置未完成',exact:true}).count(),0);
    console.log(JSON.stringify({engine:engine.name(),scenario:'save-model-then-skip-unblocks-composer',status:'passed',imagesRead:0}));
    await configuredContext.close();
    // Exercise skip/back/section transitions, breakpoint changes and keyboard exit.
    const context = await browser.newContext({viewport:{width:390,height:844},hasTouch:true});
    await installApi(context,{...originalState,wizard_completed:true});
    const page = await context.newPage();
    await page.goto(url); await page.locator('.em-tour-card').waitFor();
    const first = await page.locator('#tour-title').innerText();
    await page.getByRole('button',{name:'跳过此步',exact:true}).click();
    await page.getByRole('button',{name:'上一步',exact:true}).click();
    assert.equal(await page.locator('#tour-title').innerText(),first);
    await page.getByRole('button',{name:'收起引导',exact:true}).click();
    await inside(page,'.em-tour-navigation');
    await page.getByRole('button',{name:'展开引导',exact:true}).click();
    await page.setViewportSize({width:1440,height:900});
    await inside(page,'.em-tour-card');
    assert.equal(await page.locator('#tour-title').innerText(),first);
    await page.getByRole('button',{name:'跳过本节',exact:true}).click();
    await page.locator('.em-tour-transition').waitFor(); await inside(page,'.em-tour-transition');
    await page.getByRole('button',{name:'跳过本节，前往设置引导',exact:true}).click();
    await page.getByRole('heading',{name:'管理模型供应商',exact:true}).waitFor();
    await page.getByRole('button',{name:'展开互动练习',exact:true}).click();
    await page.locator('.em-tour-practice select').focus();
    assert.equal(await page.locator('.em-tour-practice select').evaluate(el=>document.activeElement===el),true);
    const ended = page.waitForResponse(res=>res.url().endsWith('/onboarding') && res.request().postDataJSON()?.coach_phase==='done');
    await page.keyboard.press('Escape'); await ended;
    await page.locator('.em-tour-card').waitFor({state:'detached'});
    console.log(JSON.stringify({engine:engine.name(),scenario:'skip/back/chapters/collapse/rotation/focus/Escape',status:'passed',imagesRead:0}));
    await context.close();
  } finally { await browser.close(); }
}
