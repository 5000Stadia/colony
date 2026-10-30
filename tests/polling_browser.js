const {chromium} = require('/opt/colony-test-playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({executablePath:'/opt/colony-test-chrome/chrome', headless:true,
    args:['--no-sandbox','--disable-dev-shm-usage']});
  try {
    const page = await browser.newPage({viewport:{width:390,height:844}, isMobile:true, hasTouch:true});
    page.setDefaultTimeout(7000);
    const errors=[]; page.on('pageerror', error=>errors.push(String(error)));
    await page.clock.install({time:new Date('2026-10-30T20:00:00Z')});
    await page.addInitScript(() => {
      localStorage.setItem('aloud','0');
      const original=window.fetch.bind(window);
      const fixture=window.pollFixture={requests:[], sent:[], stops:[], holdLine:false};
      fixture.active=kind=>fixture.requests.filter(r=>r.kind===kind&&!r.done);
      fixture.finish=(id, body, status=200)=>{
        const request=fixture.requests[id]; request.done=true;
        request.resolve(new Response(typeof body==='string'?body:JSON.stringify(body),
          {status,headers:{'Content-Type':typeof body==='string'?'text/html':'application/json','ETag':'"fixture-'+id+'"'}}));
      };
      fixture.fail=id=>{const request=fixture.requests[id];request.done=true;request.reject(new TypeError('Failed to fetch'));};
      window.fetch=(input, options={})=>{
        const url=new URL(typeof input==='string'?input:input.url,location.href);
        if(url.pathname==='/chat/send') {
          fixture.sent.push({p:options.body.get('p'),text:options.body.get('text')});
          return Promise.resolve(new Response('Sent'));
        }
        if(url.pathname==='/chat/stop') {fixture.stops.push(options.body.get('p'));return Promise.resolve(new Response('Stopped'));}
        if(url.pathname==='/chat/measure'||url.pathname==='/chat/heard') return Promise.resolve(new Response('{}'));
        if(url.pathname==='/chat/line'&&!fixture.holdLine) return Promise.resolve(new Response(JSON.stringify({line:[],projects:{},total:0,ready:false})));
        const kind=url.pathname==='/status'?'status':url.pathname==='/chat/feed'?'feed'+url.searchParams.get('p'):
          url.pathname==='/fixture/current'?'current':url.pathname==='/chat/line'?'line':null;
        if(!kind)return original(input,options);
        return new Promise((resolve,reject)=>{
          const request={id:fixture.requests.length,kind,done:false,aborted:false,resolve,reject};
          fixture.requests.push(request);
          // Deliberately allow late completion after abort, so stale-response guards are exercised too.
          if(options.signal)options.signal.addEventListener('abort',()=>{request.aborted=true;});
        });
      };
    });
    const active=kind=>page.evaluate(kind=>pollFixture.active(kind).map(r=>r.id),kind);
    const finish=(id,body,status)=>page.evaluate(([id,body,status])=>pollFixture.finish(id,body,status),[id,body,status||200]);
    const status=state=>({projects:[{state,name:'Fixture',project:'/fixture',waiting:0,work:'',followed:true}],
      monitor:{state:'off'},nav:[],reconnect:[]});
    const advance=async ms=>{await page.clock.runFor(ms);await page.evaluate(()=>Promise.resolve());};
    await page.goto(process.argv[2]+'/?p=0&view=chat');
    await page.locator('#composer').waitFor();
    await advance(10000);
    assert.equal((await active('status')).length,1,'slow status responses stay single-flight across timer ticks');
    assert.equal((await active('feed0')).length,1,'slow Chat responses stay single-flight across timer ticks');
    assert.equal(await page.evaluate(()=>pollFixture.requests.filter(r=>r.kind==='status').length),1);
    assert.equal(await page.evaluate(()=>pollFixture.requests.filter(r=>r.kind==='feed0').length),1);
    const firstStatus=(await active('status'))[0];
    await page.evaluate(id=>pollFixture.fail(id),firstStatus);
    await advance(2500);
    await finish((await active('status'))[0],status('working'));
    await page.waitForFunction(()=>document.getElementById('dot-0').classList.contains('working'));
    await page.locator('#composer textarea').fill('Message while a feed is pending');
    await page.locator('#composer').evaluate(form=>form.requestSubmit());
    await page.waitForFunction(()=>pollFixture.sent.length===1);
    assert.deepEqual(await page.evaluate(()=>pollFixture.sent[0]),{p:'0',text:'Message while a feed is pending'});
    assert.equal((await active('feed0')).length,1,'sending bypasses the pending refresh');
    await advance(2000);
    await finish((await active('feed0'))[0],'<div class="msg"><div class="say">Original reply</div></div>');
    await page.waitForFunction(()=>pollFixture.requests.filter(r=>r.kind==='feed0'&&!r.done).length===1);
    await finish((await active('feed0'))[0],'<div class="msg you">Message while a feed is pending</div><div class="chatlive busy">Working</div>');
    await page.waitForFunction(()=>!document.querySelector('#chat .mine')&&document.querySelector('#chat .chatlive.busy'));
    await page.locator('#composer').evaluate(form=>{form.requestSubmit();form.requestSubmit();});
    await page.waitForFunction(()=>pollFixture.stops.length===1);
    assert.deepEqual(await page.evaluate(()=>pollFixture.stops),['0'],'the explicit two-tap Stop bypasses polling');
    await advance(4000);
    const oldStatus=(await active('status'))[0],oldFeed=(await active('feed0'))[0];
    assert.notEqual(oldStatus,undefined);assert.notEqual(oldFeed,undefined);
    await page.evaluate(()=>colonyNav.go('/?p=1&view=chat',{readingChoice:colonyReadingFollow.version()}));
    await page.waitForFunction(()=>document.querySelector('#chat').dataset.key==='1');
    assert.equal(await page.evaluate(id=>pollFixture.requests[id].aborted,oldStatus),true);
    assert.equal(await page.evaluate(id=>pollFixture.requests[id].aborted,oldFeed),true);
    await finish(oldStatus,status('off'));
    await finish(oldFeed,'<div class="msg">STALE FIRST PROJECT</div>');
    await page.evaluate(()=>Promise.resolve());
    assert(!(await page.locator('#chat').innerText()).includes('STALE FIRST PROJECT'));
    assert(await page.locator('#dot-0').evaluate(dot=>dot.classList.contains('idle')),'late status cannot redraw the next page');
    await advance(10000);
    assert.equal((await active('status')).length,1);
    assert.equal((await active('feed1')).length,1,'the next project gets its own unblocked polling scope');
    await page.evaluate(id=>pollFixture.fail(id),(await active('feed1'))[0]);
    await page.waitForFunction(()=>pollFixture.active('feed1').length===1);
    await finish((await active('feed1'))[0],'<div class="msg">Second project fresh reply</div><form><textarea aria-label="Inline answer"></textarea></form>');
    await page.waitForFunction(()=>document.querySelector('#chat').innerText.includes('Second project fresh reply'));
    await advance(6000);
    const inlineRequest=(await active('feed1'))[0];
    await page.getByRole('textbox',{name:'Inline answer'}).fill('Keep my answer');
    await finish(inlineRequest,'<div class="msg">Update that arrived while answering</div>');
    await page.evaluate(()=>Promise.resolve());
    assert.equal(await page.getByRole('textbox',{name:'Inline answer'}).inputValue(),'Keep my answer',
      'a slow refresh cannot replace an answer begun while it was pending');
    await page.getByRole('textbox',{name:'Inline answer'}).fill('');
    await page.getByRole('textbox',{name:'Inline answer'}).evaluate(input=>input.blur());
    await advance(6000);
    await finish((await active('feed1'))[0],'<div class="msg">Update after answering</div>');
    await page.waitForFunction(()=>document.querySelector('#chat').innerText.includes('Update after answering'));
    // The persistent global composer and line already coalesce looks; a stalled look must not hold a send.
    await page.evaluate(()=>{pollFixture.holdLine=true;localStorage.setItem('aloud','1');colonyPlayer.setMode('text');colonyPlayer.poke();});
    await advance(10000);
    assert.equal((await active('line')).length,1);
    await page.locator('#gc textarea').fill('Global message during polling');
    await page.locator('#gc').evaluate(form=>form.requestSubmit());
    await page.waitForFunction(()=>pollFixture.sent.length===2);
    assert.equal(await page.evaluate(()=>pollFixture.sent[1].p),'1');
    await page.evaluate(()=>colonyNav.go('/fixture/current-page',{readingChoice:colonyReadingFollow.version()}));
    await page.getByRole('heading',{name:'Current items'}).waitFor();
    await advance(12000);
    assert.equal((await active('current')).length,1,'current-item fragments do not overlap either');
    const firstCurrent=(await active('current'))[0];
    await page.evaluate(id=>pollFixture.fail(id),firstCurrent);
    await advance(4000);
    await finish((await active('current'))[0],'<p>Fresh current item</p>');
    await page.waitForFunction(()=>document.getElementById('current').textContent.includes('Fresh current item'));
    assert.deepEqual(errors,[]);
    console.log('PASS polling backpressure: single-flight, failure recovery, navigation discard, message drain, Stop and global send');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
