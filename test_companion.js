'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname+'/companion.js','utf8');

function fixture() {
  const calls={parse:0,save:0,hide:0,close:0,refresh:0,logout:0};
  let dialog=false, login=false, listener;
  const context={entrySaving:false, accountReady:true, leaving:false,
    window:{webkit:{messageHandlers:{pocket:{postMessage(value){assert.equal(value,'hide');calls.hide++;}}}}},
    document:{body:{dataset:{pocketView:'quick-entry'}},
      activeElement:{},
      getElementById(id){return id==='auth-form'?(login?{}:null):id==='entry-form'?{requestSubmit(){calls.save++;}}:{focus(){}};},
      querySelector(){return dialog?{close(){calls.close++;dialog=false;}}:null;},
      addEventListener(name, callback){listener=callback;}
    },
    parseQuick(){calls.parse++;},
    async refreshAccount(){calls.refresh++;return context.accountReady;},
    async logoutAccount(){calls.logout++;return true;}
  };
  vm.runInNewContext(source,context);
  return {calls,context,dialog(){dialog=true;},login(){login=true;},
    press(key,flags={}){listener({key,preventDefault(){},...flags});}};
}
test('keyboard fill, save, dialog dismissal and hiding have separate actions',()=>{
  const f=fixture(); f.press('Enter',{metaKey:true}); f.press('Enter',{metaKey:true,shiftKey:true});
  assert.equal(f.calls.parse,1); assert.equal(f.calls.save,1);
  f.dialog(); f.press('Escape'); assert.equal(f.calls.close,1); assert.equal(f.calls.hide,0);
  f.press('Escape'); assert.equal(f.calls.hide,1);
});
test('saving, offline, composing, repeat and open dialogs block entry shortcuts',()=>{
  const f=fixture();
  f.context.entrySaving=true; f.press('Enter',{metaKey:true,shiftKey:true});
  f.context.entrySaving=false; f.context.accountReady=false; f.press('Enter',{metaKey:true});
  f.context.accountReady=true; f.press('Enter',{metaKey:true,repeat:true}); f.press('Enter',{metaKey:true,isComposing:true});
  f.dialog(); f.press('Enter',{metaKey:true,shiftKey:true});
  assert.equal(f.calls.save+f.calls.parse,0);
});
test('native resume rechecks the account and logout uses existing revocation',async()=>{
  const f=fixture(); assert.equal(await f.context.window.pocketCompanion.resume(),true);
  f.context.accountReady=false; assert.equal(await f.context.window.pocketCompanion.resume(),false);
  assert.equal(f.calls.refresh,2);
  assert.equal(await f.context.window.pocketCompanion.logout(),true);assert.equal(f.calls.logout,1);
  f.login();assert.equal(await f.context.window.pocketCompanion.resume(),true);assert.equal(f.calls.refresh,2);
});
