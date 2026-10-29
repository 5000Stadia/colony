const {chromium} = require('/opt/colony-test-playwright');
const assert = require('node:assert/strict'), fs = require('node:fs');
(async () => {
  const browser = await chromium.launch({executablePath:'/opt/colony-test-chrome/chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  page.setDefaultTimeout(7000); const base=process.argv[2], errors=[]; let heard=0;
  page.on('pageerror', e=>errors.push(String(e)));
  await page.clock.install({time:new Date('2026-10-29T18:00:00Z')});
  await page.addInitScript(() => {
    window.fixtureSaid=[]; window.fixtureAudioCalls=[];
    HTMLMediaElement.prototype.play=function(){fixtureAudioCalls.push('play');return Promise.resolve();};
    HTMLMediaElement.prototype.pause=function(){fixtureAudioCalls.push('pause');};
    Object.defineProperty(window,'speechSynthesis',{value:Object.assign(new EventTarget(),{
      getVoices:()=>[],speak(u){if(u.text)fixtureSaid.push(u);if(u.onstart)u.onstart();},cancel(){},pause(){},resume(){}})});
    localStorage.setItem('aloud','1'); localStorage.setItem('automode','both');
    if(!sessionStorage.getItem('controlsStarted')){
      sessionStorage.setItem('controlsStarted','1');const time=Date.now();
      localStorage.setItem('linenow',JSON.stringify({N:{id:'0|A',p:'0',t:'A',name:'plants',href:'/fixture/reading-chat',kokoro:false,
        blocks:[{k:'part1',text:'First part. Another sentence.'}],j:0,piece:0,seat:'auto'},
        S:{want:['0|A'],play:[],auto:[['0|A',time]],begun:['0|A']},sounding:true,saved:time}));
    }
  });
  await page.route('**/chat/line?**',r=>r.fulfill({contentType:'application/json',body:JSON.stringify({line:[],projects:{'0':{name:'plants',href:'/fixture/reading-chat'}},total:0,ready:false})}));
  await page.route('**/chat/feed?**',r=>r.fulfill({status:304,body:''}));
  await page.route('**/chat/heard',r=>{heard++;return r.fulfill({contentType:'application/json',body:'{}'});});
  const shot=async name=>{if(process.env.COLONY_TEST_ARTIFACTS){fs.mkdirSync(process.env.COLONY_TEST_ARTIFACTS,{recursive:true});await page.screenshot({path:process.env.COLONY_TEST_ARTIFACTS+'/'+name+'-390.png'});}};
  const snapshot=()=>page.evaluate(()=>({reading:colonyPlayer.state().reading,playing:colonyPlayer.state().playing,paused:colonyPlayer.state().paused,
    clip:colonyPlayer.state().clip,waiting:colonyPlayer.state().waiting,mode:colonyPlayer.mode(),auto:colonyPlayer.auto(),
    sounds:fixtureSaid.length,audio:fixtureAudioCalls.length}));
  const manual=async()=>{await page.locator('#fixture-manual').click();await page.waitForURL('**/fixture/reading-manual');};
  const jump=async()=>{const before=await snapshot(),marks=heard;await page.getByRole('button',{name:'Return to reading',exact:true}).click();
    await page.waitForURL('**/fixture/reading-chat');await page.waitForFunction(()=>document.querySelector('#chat')?.dataset.key==='0');
    assert.deepEqual(await snapshot(),before,'Return preserves current audio, queue, mode and Auto');assert.equal(heard,marks);};
  try{
    await page.goto(base+'/fixture/reading-chat');await page.waitForFunction(()=>colonyPlayer.state().playing);
    assert.equal(await page.evaluate(()=>colonyReadingFollow.grace()),5);
    assert.equal(await page.locator('.cpbar .cptextstatus').evaluate(e=>e.classList.contains('held')),false);
    assert.equal(await page.locator('.cpreturn').isVisible(),false);await shot('follow-ready');
    await page.locator('.cpwhat').click();await page.locator('.cpsetopen').click();
    assert.equal(await page.locator('.cpgrace').inputValue(),'5');await page.locator('.cpgrace').selectOption('20');
    assert.equal(await page.evaluate(()=>localStorage.getItem('followGrace')),'20');await shot('follow-delay');await page.locator('.cpgrab').click();
    assert.equal(await page.locator('.cpbar .cptextstatus').evaluate(e=>e.classList.contains('held')),true);
    await shot('follow-held');await page.clock.fastForward(15000);assert.equal(await page.locator('.cpbar .cptextstatus').evaluate(e=>e.classList.contains('held')),true);
    await page.clock.fastForward(6000);assert.equal(await page.locator('.cpbar .cptextstatus').evaluate(e=>e.classList.contains('held')),false,'status updates at the selected expiry');
    await page.reload();await page.waitForFunction(()=>colonyPlayer.state().playing);assert.equal(await page.evaluate(()=>colonyReadingFollow.grace()),20,'custom duration persists');
    await manual();assert(await page.locator('.cpreturn').isVisible());
    const box=await page.locator('.cpreturn').boundingBox();assert(box.width>=44&&box.height>=44);
    assert.equal(await page.locator('.cpbar .cptextstatus svg').getAttribute('width'),'18');
    assert.equal(await page.locator('.cppill button').count(),0,'status creates no nested button');
    await page.clock.fastForward(25000);assert(page.url().endsWith('/fixture/reading-manual'),'normal browsing stays outside Chat after expiry');
    assert.equal(await page.locator('.cpbar .cptextstatus').evaluate(e=>e.classList.contains('held')),true);
    await jump();
    await page.evaluate(()=>colonyPlayer.setMode('audio'));await manual();await jump();
    assert.equal(await page.evaluate(()=>colonyPlayer.mode()),'audio');
    assert.equal(await page.locator('.cpbar .cptextstatus').evaluate(e=>e.classList.contains('held')),true);
    // A single reply finishes while away: keep the same Return control beside the resting Auto bubble.
    await manual();await page.evaluate(()=>fixtureSaid.findLast(u=>u.text.includes('First part')).onend());
    await page.waitForFunction(()=>!colonyPlayer.state().reading);assert(await page.locator('.cpreturn').isVisible());
    const ret=await page.locator('.cpreturn').boundingBox(),pill=await page.locator('.cppill').boundingBox();
    assert(ret.x+ret.width<=pill.x+2 && pill.x-(ret.x+ret.width)<=8,'separate Return is immediately left of the Auto bubble');
    if(process.env.COLONY_TEST_ARTIFACTS){fs.mkdirSync(process.env.COLONY_TEST_ARTIFACTS,{recursive:true});await page.screenshot({path:process.env.COLONY_TEST_ARTIFACTS+'/return-bubble-390.png'});}
    await page.locator('.cppill').click();await page.locator('.cpsw').click();await page.locator('.cpgrab').click();
    assert.equal(await page.evaluate(()=>colonyPlayer.busy()),false,'completed idle Return also works with Auto off');
    assert(await page.locator('.cpreturn').isVisible());
    await jump();assert.equal(await page.evaluate(()=>colonyPlayer.state().reading),null,'returning to the last reply never restarts it');
    assert(await page.locator('[data-k=part1]').evaluate(e=>e.classList.contains('reading')),'idle Return focuses the saved part, including audio-only');
    // Documents retain that Chat target and keep playing through an explicit Return.
    await page.evaluate(()=>colonyPlayer.document('/open?p=0&f=listening.md&audio=1'));
    await page.evaluate(()=>colonyNav.go('/open?p=0&f=listening.md'));await page.waitForURL('**/open?p=0&f=listening.md');
    assert(await page.locator('.cpreturn').isVisible());const documentState=await snapshot();await jump();
    assert.deepEqual(await snapshot(),documentState);assert(await page.evaluate(()=>colonyPlayer.state().document));
    await page.evaluate(()=>{colonyPlayer.skip();colonyPlayer.skip();colonyPlayer.setMode('both');
      colonyPlayer.read({key:'0',name:'plants',href:'/fixture/reading-chat',kokoro:false},[{t:'A',k:'part1',text:'First part. Another sentence.'}]);});
    await page.evaluate(()=>colonyNav.go('/fixture/reading-renamed'));await page.waitForURL('**/fixture/reading-renamed');
    assert(await page.evaluate(()=>colonyPlayer.of('Renamed plants','0')),'renaming a label does not lose reading identity');
    assert(await page.locator('[data-k=part1]').evaluate(e=>e.classList.contains('reading')));
    await page.evaluate(()=>colonyNav.go('/fixture/reading-other'));await page.waitForURL('**/fixture/reading-other');
    assert.equal(await page.evaluate(()=>colonyPlayer.of('plants','1')),null,'a duplicate label belongs to a different project key');
    assert.equal(await page.locator('.say.reading').count(),0,'the other project is not marked active');
    await manual();await jump();
    // Pending Return is a single explicit intent; a newer gesture cancels its view change.
    await manual();let requested,release;const loading=new Promise(ok=>requested=ok),gate=new Promise(ok=>release=ok);
    await page.route('**/fixture/reading-chat',async r=>{requested();await gate;await r.continue();});
    await page.locator('.cpreturn').click();await loading;await page.touchscreen.tap(330,360);release();
    await page.waitForFunction(()=>!document.documentElement.classList.contains('going'));
    assert(page.url().endsWith('/fixture/reading-manual'),'new interaction revokes a pending Return');
    assert.deepEqual(errors,[]);console.log('PASS text-follow settings, status and persistent Return');
  }catch(e){console.error('Page errors:',errors);throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
