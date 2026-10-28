// Real rendered DOM + production Flask routes, all data held in a disposable fixture.
const engines = require('playwright');
const engine = engines[process.env.POCKET_BROWSER||'chromium'];
const assert = require('node:assert/strict');
const {spawn} = require('node:child_process');
const readline = require('node:readline');
const path = require('node:path');
const fs = require('node:fs');

(async()=>{
  const python=spawn(process.env.POCKET_TEST_PYTHON||'python3',[path.join(__dirname,'browser_fixture.py')],{stdio:['pipe','pipe','inherit']});
  let sequence=0, onReady, ready=new Promise(resolve=>onReady=resolve), pending=new Map();
  readline.createInterface({input:python.stdout}).on('line',line=>{
    const data=JSON.parse(line); if(data.ready) onReady(); else {pending.get(data.id)?.(data);pending.delete(data.id);}
  });
  const bridge=async request=>{
    const headers=await request.allHeaders();
    return new Promise(resolve=>{
      const id=++sequence;pending.set(id,resolve);
      python.stdin.write(JSON.stringify({id,url:request.url(),method:request.method(),headers,body:request.postData()})+'\n');
    });
  };
  let browser;
  try {
    let startupTimeout;
    try { await Promise.race([ready,new Promise((_,reject)=>{
      startupTimeout=setTimeout(()=>reject(Error('Browser fixture did not start; check POCKET_TEST_PYTHON and requirements.txt.')),15000);
      python.once('error',reject);
    })]); } finally {clearTimeout(startupTimeout);}
    browser=await engine.launch({headless:true});
    const context=await browser.newContext({viewport:{width:480,height:720}});
    let entryPosts=0, delayNextSave=false;
    await context.route('**/*',async route=>{
      if(!route.request().url().startsWith('https://pocket.example.test/')) return route.abort();
      if(route.request().url().endsWith('/api/entries') && route.request().method()==='POST') {
        entryPosts++;
        if(delayNextSave){delayNextSave=false;await new Promise(r=>setTimeout(r,200));}
      }
      const response=await bridge(route.request());
      await route.fulfill({status:response.status,headers:response.headers,body:Buffer.from(response.body,'base64')});
    });
    const page=await context.newPage(), errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto('https://pocket.example.test/quick-entry');
    await page.waitForURL('**/login?next=/quick-entry');
    await page.locator('#username').fill('tester');await page.locator('#password').fill('isolated-browser-password');
    await page.locator('#auth-submit').click();await page.waitForURL('**/quick-entry');
    await page.waitForFunction(()=>accountReady);
    assert.equal(await page.locator('.journal-panel').isVisible(),false);
    await page.locator('#quick-entry').fill('petrol for rupees 200');
    await page.keyboard.press('Meta+Enter');
    assert.equal(await page.locator('#entry-amount').inputValue(),'200');
    assert.equal(await page.locator('#entry-category').inputValue(),'Transport');
    delayNextSave=true;
    await page.keyboard.press('Meta+Shift+Enter');await page.keyboard.press('Meta+Shift+Enter');
    await page.waitForFunction(()=>!entrySaving && state.entries.length===1);
    assert.equal(entryPosts,1);assert.equal(await page.locator('#quick-entry').inputValue(),'');
    assert.equal(await page.locator('#toast').textContent(),'Saved to Pocket');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'quick-entry');
    // Refresh on reopening preserves an unsaved draft and hand-edited fields.
    await page.locator('#quick-entry').fill('coffee for 100');await page.locator('#parse-entry').click();
    await page.locator('#entry-title').fill('Morning coffee');
    assert.equal(await page.evaluate(()=>window.pocketCompanion.resume()),true);
    assert.equal(await page.locator('#entry-title').inputValue(),'Morning coffee');
    await page.locator('#open-category').click();await page.keyboard.press('Escape');
    assert.equal(await page.locator('#category-dialog').isVisible(),false);
    // Create another kind, then a reminder and mandate, exercising the same form.
    for(const [text,kind] of [['Lent 500 to Maya','loan'],['Remind me to pay rent tomorrow at 9am','reminder'],['Netflix monthly 499','mandate']]){
      await page.locator(`[data-kind="${kind}"]`).click();await page.locator('#quick-entry').fill(text);
      await page.locator('#parse-entry').click();await page.locator('#save-entry').click();
      await page.waitForFunction(()=>!entrySaving && document.getElementById('quick-entry').value==='');
    }
    assert.equal(await page.evaluate(()=>state.entries.length),3);
    assert.equal(await page.evaluate(()=>state.mandates.length),1);
    // Keyboard and ordinary clicks cannot save offline or send after account expiry.
    await page.evaluate(()=>{accountReady=false;});
    await page.locator('#quick-entry').fill('Pizza for 250');await page.locator('#parse-entry').click();
    await page.locator('#save-entry').click();assert.equal(entryPosts,3);
    assert.match(await page.locator('#form-error').textContent(),/Connect to Pocket/);
    await page.evaluate(()=>window.pocketCompanion.resume());
    // Layout: no horizontal overflow at narrow phones, panel, tablet, desktop.
    const out=process.env.POCKET_SCREENSHOT_DIR;
    if(out)fs.mkdirSync(out,{recursive:true});
    for(const width of [340,390,480,768,1280]){
      await page.setViewportSize({width,height:720});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,`overflow at ${width}`);
      if(out&&width===480)await page.screenshot({path:path.join(out,'quick-entry.png'),fullPage:true});
    }
    assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
    assert.deepEqual(await page.evaluate(()=>caches.keys()),[]);
    assert.deepEqual(await page.evaluate(()=>navigator.serviceWorker.getRegistrations().then(x=>x.map(r=>r.scope))),[]);
    await page.evaluate(()=>window.pocketCompanion.logout());
    await page.waitForURL('**/login?next=/quick-entry');
    assert.equal(await page.locator('#entry-form').count(),0);
    await page.locator('.auth-switch a').click();await page.waitForURL('**/signup?next=/quick-entry');
    await page.setViewportSize({width:340,height:720});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    await page.locator('#username').fill('popup-new');await page.locator('#password').fill('isolated-new-password');await page.locator('#confirmation').fill('isolated-new-password');
    await page.locator('#auth-submit').click();await page.waitForURL('**/quick-entry');await page.waitForFunction(()=>accountReady);
    assert.equal(await page.evaluate(()=>state.entries.length),0);
    assert.deepEqual(errors,[]);
    console.log('Browser checks passed: login/signup/logout, parser, 4 kinds, duplicate-submit guard, shortcuts, draft preservation, offline save guard, 5 widths, and no web storage.');
  } finally {await browser?.close();python.stdin.end();}
})().catch(error=>{console.error(error);process.exitCode=1;});
