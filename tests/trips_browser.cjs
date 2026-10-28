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
    const origin='https://pocket.example.test';
    await page.goto(origin+'/trips');
    await page.waitForURL('**/login?next=/trips');
    await page.locator('#username').fill('tester');await page.locator('#password').fill('isolated-browser-password');
    await page.locator('#auth-submit').click();await page.waitForURL('**/trips');
    await page.waitForFunction(()=>accountReady);
    await page.locator('#new-trip').click();
    assert.equal(await page.evaluate(()=>document.activeElement.id),'trip-name');
    await page.locator('#trip-name').fill('Goa holiday');
    await page.locator('#trip-destination').fill('Panaji');
    await page.locator('#trip-start').fill('2026-11-01');await page.locator('#trip-end').fill('2026-11-08');
    await page.locator('#save-trip').click();await page.waitForFunction(()=>state.trips.length===1&&!tripSaving);
    const goa=await page.evaluate(()=>state.trips[0].id);
    await page.locator('#active-trip-'+goa).click();await page.waitForFunction(id=>state.activeTripId===id&&!tripSaving,goa);
    await page.locator('.trip-card-title').click();await page.waitForURL('**/trips/'+goa);await page.waitForFunction(()=>accountReady);
    assert.equal(await page.locator('[data-kind="mandate"]').isVisible(),false);
    assert.equal(await page.locator('[data-filter="mandate"]').isVisible(),false);
    assert.equal(await page.locator('#entry-trip').inputValue(),goa);
    await page.locator('#quick-entry').fill('Petrol for 200');await page.locator('#parse-entry').click();
    await page.locator('#entry-date').fill('2026-09-01');await page.locator('#save-entry').click();
    await page.waitForFunction(()=>state.entries.length===1&&!entrySaving);
    assert.match(await page.locator('#spent').textContent(),/200/);
    // The same record appears in the main journal and is counted once.
    await page.locator('#nav-journal').click();await page.waitForFunction(()=>accountReady);
    assert.equal(await page.locator('#entry-trip').inputValue(),goa);
    await page.locator('#journal-trip').selectOption(goa);await page.locator('#journal-month').selectOption('2026-09');
    await page.locator('#search').fill('transport');await page.locator('#calculate-total').click();
    assert.match(await page.locator('#journal-total-value').textContent(),/200/);
    assert.equal(await page.locator('#journal-list .journal-item').count(),1);
    await page.locator('#search').fill('missing');assert.equal(await page.locator('#journal-total-value').isVisible(),false);
    await page.locator('#calculate-total').click();assert.match(await page.locator('#journal-total-value').textContent(),/0\.00/);
    // Explicit No trip survives parsing, refresh and manual edits.
    await page.locator('#entry-trip').selectOption('');await page.locator('#quick-entry').fill('Pizza for 150');
    await page.locator('#parse-entry').click();await page.locator('#entry-title').fill('Hand edited');
    await page.evaluate(()=>refreshAccount());assert.equal(await page.locator('#entry-trip').inputValue(),'');
    assert.equal(await page.locator('#entry-title').inputValue(),'Hand edited');
    await page.locator('#save-entry').click();await page.waitForFunction(()=>state.entries.length===2&&!entrySaving);
    assert.equal(await page.evaluate(()=>state.entries[0].tripId),null);
    // The Mac compact route uses the active trip and the shared save logic.
    await page.goto(origin+'/quick-entry');await page.waitForFunction(()=>accountReady);
    assert.equal(await page.locator('#entry-trip').inputValue(),goa);
    await page.locator('#quick-entry').fill('Lent 500 to Maya');await page.keyboard.press('Meta+Enter');
    await page.keyboard.press('Meta+Shift+Enter');await page.waitForFunction(()=>state.entries.length===3&&!entrySaving);
    assert.equal(await page.evaluate(()=>state.entries[0].tripId),goa);
    assert.equal(await page.locator('#toast').textContent(),'Saved to Pocket');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'quick-entry');
    await page.goto(origin+'/trips/'+goa);await page.waitForFunction(()=>accountReady);
    await page.locator('#quick-entry').fill('Remind me to call hotel tomorrow at 9am');await page.locator('#parse-entry').click();
    await page.locator('#save-entry').click();await page.waitForFunction(()=>state.entries.length===4&&!entrySaving);
    await page.locator('[data-filter="loan"]').click();await page.getByRole('button',{name:'Mark repaid',exact:true}).click();
    await page.locator('#cancel-settle').click();assert.match(await page.locator('#outstanding').textContent(),/500/);
    await page.getByRole('button',{name:'Mark repaid',exact:true}).click();await page.locator('#confirm-settle').click();
    await page.waitForFunction(()=>state.repayments.length===1);assert.match(await page.locator('#outstanding').textContent(),/0\.00/);
    assert.equal(await page.locator('.repaid-status').textContent(),'✓ Repaid');
    // Completion clears the default but leaves records editable.
    await page.locator('#complete-trip-'+goa).click();await page.waitForFunction(()=>state.activeTripId===null&&!tripSaving);
    assert.match(await page.locator('#trip-state').textContent(),/Completed/);
    await page.locator('#quick-entry').fill('Coffee for 80');await page.locator('#parse-entry').click();
    await page.locator('#save-entry').click();await page.waitForFunction(()=>state.entries.length===5&&!entrySaving);
    assert.match(await page.locator('#spent').textContent(),/280/);
    await page.locator('[data-filter="expense"]').click();
    await page.locator('.journal-item').filter({hasText:'Coffee'}).getByRole('button',{name:'Edit',exact:true}).click();
    await page.locator('#entry-trip').selectOption('');await page.locator('#save-entry').click();await page.waitForFunction(()=>!entrySaving&&state.entries.filter(e=>e.tripId===tripPageId).length===3);
    assert.match(await page.locator('#spent').textContent(),/200/);
    // Check all pages at narrow phones, typical phones, tablet and desktop.
    const out=process.env.POCKET_SCREENSHOT_DIR;if(out)fs.mkdirSync(out,{recursive:true});
    for(const route of ['/','/trips','/trips/'+goa,'/quick-entry']){
      await page.goto(origin+route);await page.waitForFunction(()=>accountReady);
      for(const width of [320,390,480,768,1280]){
        await page.setViewportSize({width,height:820});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,`${route} overflow at ${width}`);
        if(out&&[390,1280].includes(width))await page.screenshot({path:path.join(out,`${route.replaceAll('/','_')||'journal'}-${width}.png`),fullPage:true});
      }
      if(route==='/trips'){
        await page.locator('#new-trip').click();await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(()=>document.activeElement.id),'trip-destination');
        await page.setViewportSize({width:320,height:720});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        await page.keyboard.press('Escape');assert.equal(await page.locator('#trip-dialog').isVisible(),false);
      }
    }
    assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
    assert.deepEqual(await page.evaluate(()=>caches.keys()),[]);
    assert.deepEqual(await page.evaluate(()=>navigator.serviceWorker.getRegistrations().then(x=>x.map(r=>r.scope))),[]);
    assert.deepEqual(errors,[]);
    console.log('Trips browser checks passed: creation, defaults, shared records, combined filters/totals, drafts, popup, repayments, completion, reassignment, keyboard, 5 widths, and no web storage.');
  } finally {await browser?.close();python.stdin.end();}
})().catch(error=>{console.error(error);process.exitCode=1;});
