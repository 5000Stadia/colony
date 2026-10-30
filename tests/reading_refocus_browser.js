const {chromium} = require('/opt/colony-test-playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({executablePath:'/opt/colony-test-chrome/chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const base=process.argv[2], errors=[]; page.setDefaultTimeout(7000);
  page.on('pageerror',e=>errors.push(String(e)));
  await page.clock.install({time:new Date('2026-10-29T18:00:00Z')});
  await page.addInitScript(() => {
    window.fixtureSaid=[]; window.fixtureAudioCalls=[];
    // Keep native layout/scrolling and production targets; remove animation timing from geometry assertions.
    for(const method of ['scrollBy','scrollTo']){
      const original=window[method];window[method]=function(...args){
        if(args[0]&&typeof args[0]==='object')args[0]={...args[0],behavior:'instant'};
        return original.apply(window,args);
      };
    }
    HTMLMediaElement.prototype.play=function(){fixtureAudioCalls.push('play');return Promise.resolve();};
    HTMLMediaElement.prototype.pause=function(){fixtureAudioCalls.push('pause');};
    Object.defineProperty(window,'speechSynthesis',{value:Object.assign(new EventTarget(),{
      getVoices:()=>[],speak(u){if(u.text)fixtureSaid.push(u);},cancel(){},pause(){},resume(){}})});
    localStorage.setItem('aloud','1'); localStorage.setItem('automode','both');
  });
  await page.route('**/chat/line?**',r=>r.fulfill({contentType:'application/json',body:JSON.stringify({line:[],projects:{'0':{name:'plants',href:'/fixture/reading-chat'}},total:0,ready:false})}));
  await page.route('**/chat/feed?**',r=>r.fulfill({status:304,body:''}));
  await page.route('**/chat/heard',r=>r.fulfill({contentType:'application/json',body:'{}'}));
  const sentence=n=>`Current sentence number ${n} follows the winding trail toward the distant lighthouse and its sheltered garden.`;
  // Run the actual speech handlers: queued utterances start only when the fixture advances the voice.
  const advance=async n=>page.evaluate(text=>{
    const u=fixtureSaid.find(x=>x.text.includes(text)); if(!u)throw new Error('No utterance for '+text);
    u.onstart(); u.onboundary({charIndex:u.text.indexOf(text)});
  },sentence(n));
  const geometry=()=>page.evaluate(()=>{
    const r=[...(CSS.highlights.get('cpread')||[])][0], rects=r?[...r.getClientRects()]:[];
    const nav=document.querySelector('nav'), under=nav&&getComputedStyle(nav).position==='sticky'?nav.getBoundingClientRect().bottom:0;
    let bottom=visualViewport?visualViewport.height:innerHeight;
    [document.getElementById('composer'),document.getElementById('gc'),document.querySelector('#cplayer:not(.cpidle) .cpbar')].forEach(el=>{
      if(el&&!el.hidden&&el.offsetHeight){const q=el.getBoundingClientRect();if(q.top>0)bottom=Math.min(bottom,q.top);}
    });
    return {text:r?.toString(),top:rects.length?Math.min(...rects.map(q=>q.top)):null,
      bottom:rects.length?Math.max(...rects.map(q=>q.bottom)):null,wanted:under+12,visibleBottom:bottom,
      partTop:document.querySelector('[data-k=long]')?.getBoundingClientRect().top,y:scrollY};
  });
  const check=async(n,why)=>{
    await page.waitForTimeout(450); const g=await geometry();
    assert.equal(g.text,sentence(n),why+' highlights the current sentence');
    assert(Math.abs(g.top-g.wanted)<4,why+' puts its first line below navigation: '+JSON.stringify(g));
    assert(g.bottom<=g.visibleBottom,why+' keeps the whole highlight visible: '+JSON.stringify(g));
    assert(g.partTop < -500,why+' leaves the beginning of this same reply well above the viewport: '+JSON.stringify(g));
  };
  const snapshot=()=>page.evaluate(()=>({state:colonyPlayer.state(),mode:colonyPlayer.mode(),auto:colonyPlayer.auto(),
    sounds:fixtureSaid.length,audio:fixtureAudioCalls.length}));
  const manual=async()=>{await page.locator('#fixture-manual').click();await page.waitForURL('**/fixture/reading-manual');};
  try {
    await page.goto(base+'/fixture/reading-chat');
    await page.evaluate(()=>colonyPlayer.read({key:'0',name:'plants',href:'/fixture/reading-chat',kokoro:false},[
      {t:'A',k:'long',text:[...document.querySelectorAll('[data-k=long] p')].map(p=>p.textContent).join('\n\n')}
    ]));
    await advance(12); await page.clock.fastForward(1100);
    const initial=await geometry();
    assert.equal(initial.text,sentence(12),'initial speech boundary highlights a deep sentence');
    assert(initial.top>=initial.wanted-4&&initial.bottom<=initial.visibleBottom,'initial sentence is visible: '+JSON.stringify(initial));
    // First hold a sentence already being heard: no pending sentence-turn timer can mask a bad refocus.
    // Then cover playback progressing while the person's hold is active.
    for(const [direction,next,progress] of [[-1,12,false],[1,12,false],[-1,13,true],[1,14,true]]){
      await page.mouse.move(180,300);await page.mouse.wheel(0,direction*100000);await page.waitForTimeout(150);
      await page.touchscreen.tap(320,300);
      assert(await page.evaluate(()=>colonyReadingFollow.remaining()>0),'trusted scroll/touch holds following');
      const before=(await geometry()).y;
      if(progress)await advance(next); await page.waitForTimeout(100);
      const held=await geometry();
      assert.equal(held.text,sentence(next),'the real highlight tracks the current sentence during the hold');
      assert(Math.abs(held.y-before)<2,'playback leaves the user scroll position during the hold');
      assert(direction<0?held.top>held.visibleBottom:held.bottom<held.wanted,'fixture scrolls above/below the current sentence');
      // No subsequent speech boundary: expiry itself must restore the latest sentence, in either direction.
      await page.clock.fastForward(5100);
      await check(next,`Refocus from ${direction<0?'above':'below'} ${progress?'after playback advances':'within the same sentence'}`);
      assert(await page.evaluate(()=>colonyReadingFollow.canFollow()),'the refocus scroll does not create another hold');
    }
    await manual(); await advance(17); await page.clock.fastForward(6000);
    assert(page.url().endsWith('/fixture/reading-manual'),'grace expiry with continued playback leaves non-Chat browsing alone');
    const before=await snapshot();
    await page.getByRole('button',{name:'Return to reading',exact:true}).click();await page.waitForURL('**/fixture/reading-chat');
    await page.waitForFunction(()=>document.querySelector('#chat')?.dataset.key==='0');
    await check(17,'Return after off-Chat playback');
    assert.deepEqual(await snapshot(),before,'Return restores the sentence without changing playback, queue, mode or Auto');
    // A later gesture still revokes a Return whose replacement Chat has not arrived.
    await manual();let requested,release;const loading=new Promise(ok=>requested=ok),gate=new Promise(ok=>release=ok);
    await page.route('**/fixture/reading-chat',async r=>{requested();await gate;await r.continue();});
    await page.locator('.cpreturn').click();await loading;await page.touchscreen.tap(330,360);release();
    await page.waitForFunction(()=>!document.documentElement.classList.contains('going'));
    assert(page.url().endsWith('/fixture/reading-manual'),'new interaction revokes pending Return');
    assert.deepEqual(errors,[]);console.log('PASS current-sentence refocus and Return');
  }catch(e){console.error('Page errors:',errors);console.error('Geometry:',await geometry());throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
