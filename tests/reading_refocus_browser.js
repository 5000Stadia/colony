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
    // Scrolled away while one sentence is still being heard: nothing brings the page back by a clock.
    const away=async direction=>{await page.mouse.move(180,300);await page.mouse.wheel(0,direction*100000);await page.waitForTimeout(150);
      await page.touchscreen.tap(320,300);return (await geometry()).y;};
    let y=await away(-1);await page.clock.fastForward(6000);await page.waitForTimeout(100);
    let held=await geometry();
    assert.equal(held.text,sentence(12));assert(Math.abs(held.y-y)<2,'the page stays where the person scrolled while the same sentence is heard');
    assert(held.top>held.visibleBottom,'fixture scrolled above the current sentence');
    // The next sentence, with the switch on, is brought into view at once: no wait after the touch.
    await advance(13);await page.waitForTimeout(450);held=await geometry();
    assert.equal(held.text,sentence(13));
    assert(held.top>=held.wanted-4&&held.bottom<=held.visibleBottom,'the next sentence is in view after scrolling up: '+JSON.stringify(held));
    // Scrolled on past it, a sentence that is not below the view moves nothing; the switch tapped off and on shows it.
    y=await away(1);await advance(14);await page.waitForTimeout(150);held=await geometry();
    assert.equal(held.text,sentence(14),'the highlight tracks the current sentence');assert(Math.abs(held.y-y)<2);
    await page.locator('.cpbar .cpfollow').click();assert.equal(await page.evaluate(()=>colonyPlayer.following()),false);
    await page.locator('.cpbar .cpfollow').click();await check(14,'The switch turned on in its chat');
    // Left by hand while it reads: off, and playback going on leaves the person there.
    await manual();assert.equal(await page.evaluate(()=>colonyPlayer.following()),false);
    await advance(17);await page.clock.fastForward(6000);
    assert(page.url().endsWith('/fixture/reading-manual'),'off: continued playback leaves the page alone');
    const before=await snapshot();
    await page.locator('.cpbar .cpfollow').click();await page.waitForURL('**/fixture/reading-chat');
    await page.waitForFunction(()=>document.querySelector('#chat')?.dataset.key==='0');
    await check(17,'The switch turned on from another page');
    const plain=x=>({...x,state:{...x.state,story:null}});              // the switch itself is noted in the player's log
    assert.deepEqual(plain(await snapshot()),plain(before),'turning it on shows the sentence without changing playback, queue, mode or Auto');
    assert.deepEqual(errors,[]);console.log('PASS the sentence being read: no clock, the next sentence, and the switch');
  }catch(e){console.error('Page errors:',errors);console.error('Geometry:',await geometry());throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
