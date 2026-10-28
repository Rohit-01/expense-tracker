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
    await page.goto(origin+'/trash');await page.waitForURL('**/login?next=/trash');
    await page.locator('#username').fill('tester');await page.locator('#password').fill('isolated-browser-password');
    await page.locator('#auth-submit').click();await page.waitForURL('**/trash');await page.waitForFunction(()=>accountReady);
    assert.equal(await page.locator('.summary-filters').isVisible(),false);
    await page.goto(origin+'/');await page.waitForFunction(()=>accountReady);
    assert.equal(await page.locator('#summary-month').inputValue(),await page.evaluate(()=>today().slice(0,7)));
    const ids=await page.evaluate(async()=>{
      const trip=(await api('/api/trips',{name:'Goa holiday'})).trips[0];
      const entry=async body=>(await api('/api/entries',{kind:'expense',title:'Dinner',amount:'200',date:'2026-10-02',category:'Other',tripId:trip.id,...body})).entries[0];
      const dinner=await entry({});await entry({title:'Advance flight',amount:'3000',date:'2026-09-01'});
      await entry({title:'Home coffee',amount:'100',tripId:null});
      const loan=await entry({kind:'loan',title:'Hotel loan',person:'Maya',amount:'500',dueAt:'2026-11-01T09:00'});
      await api('/api/repayments',{entryId:loan.id,amount:'200',date:'2026-10-28',notes:'Part received'});
      const reminder=await entry({kind:'reminder',title:'Call hotel',dueAt:'2026-11-01T09:00'});
      await refreshAccount();return {trip:trip.id,dinner:dinner.id,loan:loan.id,reminder:reminder.id};
    });
    await page.locator('#summary-month').selectOption('2026-10');await page.locator('#summary-trip').selectOption(ids.trip);
    assert.match(await page.locator('#spent').textContent(),/200/);assert.match(await page.locator('#outstanding').textContent(),/300/);
    assert.equal(await page.locator('#reminder-count').textContent(),'2');
    // Journal filtering and totals are independent of summary controls and the draft.
    await page.locator('#quick-entry').fill('Petrol for 250');await page.locator('#parse-entry').click();await page.locator('#entry-trip').selectOption('');
    await page.locator('#entry-title').fill('My unsaved draft');
    await page.locator('#journal-month').selectOption('2026-10');await page.locator('#search').fill('Dinner');await page.locator('#calculate-total').click();
    const total=await page.locator('#journal-total-value').textContent();
    await page.locator('#summary-month').selectOption('2026-09');assert.match(await page.locator('#spent').textContent(),/3,?000/);
    assert.equal(await page.locator('#journal-total-value').textContent(),total);assert.equal(await page.locator('#entry-title').inputValue(),'My unsaved draft');
    assert.equal(await page.locator('#entry-trip').inputValue(),'');assert.equal(await page.locator('#search').inputValue(),'Dinner');
    await page.evaluate(()=>refreshAccount());assert.equal(await page.locator('#summary-month').inputValue(),'2026-09');
    // Cancel and failed mutations must retain the record and current view.
    await page.getByRole('button',{name:'Delete Dinner',exact:true}).click();assert.equal(await page.evaluate(()=>document.activeElement.id),'cancel-delete');
    await page.keyboard.press('Escape');assert.equal(await page.locator('#delete-dialog').isVisible(),false);
    assert.equal(await page.locator('.journal-item').count(),1);
    await page.getByRole('button',{name:'Delete Dinner',exact:true}).click();
    await page.route('**/api/trash',route=>route.abort(),{times:1});await page.locator('#confirm-delete').click();
    await page.waitForFunction(()=>!trashBusy);assert.match(await page.locator('#delete-error').textContent(),/could not be confirmed/);
    assert.equal(await page.locator('#delete-dialog').isVisible(),true);assert.equal(await page.locator('.journal-item').count(),1);
    await page.locator('#confirm-delete').click();await page.waitForFunction(()=>!trashBusy&&state.trash.length===1);
    assert.equal(await page.locator('.journal-item').count(),0);assert.equal(await page.locator('#entry-title').inputValue(),'My unsaved draft');
    // Trash keeps the original trip and restores the same record.
    await page.locator('#nav-trash').click();await page.waitForFunction(()=>accountReady);
    assert.match(await page.locator('.trash-record').textContent(),/Goa holiday/);
    await page.getByRole('button',{name:'Restore Dinner',exact:true}).click();await page.waitForFunction(()=>!trashBusy&&state.trash.length===0);
    assert.equal(await page.evaluate(()=>document.activeElement.id),'trash-heading');
    await page.goto(origin+'/trips/'+ids.trip);await page.waitForFunction(()=>accountReady);
    assert.equal(await page.locator('#summary-month').inputValue(),'');assert.equal(await page.locator('#summary-trip').isDisabled(),true);
    await page.getByRole('button',{name:'Delete Hotel loan',exact:true}).click();assert.match(await page.locator('#delete-details').textContent(),/1 repayment records/);
    await page.locator('#confirm-delete').click();await page.waitForFunction(()=>!trashBusy&&state.repayments.length===0);
    assert.match(await page.locator('#outstanding').textContent(),/0\.00/);
    await page.locator('#nav-trash').click();await page.waitForFunction(()=>accountReady);
    await page.getByRole('button',{name:'Restore Hotel loan',exact:true}).click();await page.waitForFunction(()=>!trashBusy&&state.repayments.length===1);
    // Paused mandate: reverse payment, create replacement, then refuse old restoration.
    await page.goto(origin+'/');await page.waitForFunction(()=>accountReady);
    const mandate=await page.evaluate(async()=>{
      const item=(await api('/api/mandates',{title:'Subscription',amount:'499',firstDueDate:today(),category:'Other'})).mandates[0];
      await api('/api/mandate-payments',{id:item.id,period:today().slice(0,7)});await api('/api/mandate-status',{id:item.id,paused:true});await refreshAccount();return item.id;
    });
    await page.getByRole('button',{name:'Delete Subscription',exact:true}).click();assert.equal(await page.locator('#delete-mandate').isVisible(),true);
    await page.locator('#confirm-delete').click();await page.waitForFunction(()=>!trashBusy);
    await page.locator('[data-filter="mandate"]').click();assert.match(await page.locator('#mandates-list').textContent(),/Payment moved to Trash/);
    await page.locator('.mandate-pay').click();await page.waitForFunction(()=>state.mandates[0].occurrences[0].status==='paid');
    await page.locator('#nav-trash').click();await page.waitForFunction(()=>accountReady);
    await page.getByRole('button',{name:'Restore Subscription',exact:true}).click();await page.waitForFunction(()=>!trashBusy);
    assert.match(await page.locator('.trash-record .error').textContent(),/replacement payment/);assert.equal(await page.locator('.trash-record').count(),1);
    const out=process.env.POCKET_SCREENSHOT_DIR;if(out)fs.mkdirSync(out,{recursive:true});
    for(const route of ['/','/trips/'+ids.trip,'/trash','/quick-entry']){
      await page.goto(origin+route);await page.waitForFunction(()=>accountReady);
      for(const width of [320,390,480,768,1280]){
        await page.setViewportSize({width,height:820});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${route} overflow at ${width}`);
        if(out&&[390,1280].includes(width))await page.screenshot({path:path.join(out,`summary-trash${route.replaceAll('/','_')}-${width}.png`),fullPage:true});
      }
      if(route==='/'){
        await page.getByRole('button',{name:'Delete Dinner',exact:true}).click();await page.setViewportSize({width:320,height:720});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await page.keyboard.press('Escape');
      }
    }
    assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
    assert.deepEqual(await page.evaluate(()=>caches.keys()),[]);
    assert.deepEqual(errors,[]);
    console.log('Summary and Trash browser checks passed: filters, draft independence, confirmation, errors, restoration, loan history, mandate conflicts, keyboard, 5 widths, and no web storage.');
  } finally {await browser?.close();python.stdin.end();}
})().catch(error=>{console.error(error);process.exitCode=1;});
