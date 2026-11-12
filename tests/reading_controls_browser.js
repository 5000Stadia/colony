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
  const on=()=>page.evaluate(()=>colonyPlayer.following());
  const pressed=()=>page.locator('.cpbar .cpfollow').getAttribute('aria-pressed');
  const inChat=async()=>{await page.waitForURL('**/fixture/reading-chat');await page.waitForFunction(()=>document.querySelector('#chat')?.dataset.key==='0');};
  try{
    await page.goto(base+'/fixture/reading-chat');await page.waitForFunction(()=>colonyPlayer.state().playing);
    // The switch: a button of its own in the bar, on to begin with. No wait to set, no Return beside it.
    assert.equal(await on(),true);assert.equal(await pressed(),'true');
    const box=await page.locator('.cpbar .cpfollow').boundingBox();assert(box.width>=40&&box.height>=40);
    assert.equal(await page.locator('.cppill button').count(),0,'no button nested in the pill');
    assert.equal(await page.locator('.cpgrace').count(),0);assert.equal(await page.locator('.cpreturn').count(),0);await shot('follow-on');
    await page.locator('.cpwhat').click();await page.locator('.cpsetopen').click();
    assert.deepEqual(await page.locator('.cpseg button').allTextContents(),['Aloud','Text only'],'Audio only is the switch now');
    await page.locator('.cpgrab').click();
    // Leaving the chat being read, by hand, turns it off: the sound goes on and the page stays where the person went.
    const was=await snapshot();await manual();const before=await snapshot(),marks=heard;
    assert.equal(await on(),false);assert.equal(await pressed(),'false');
    assert.deepEqual({...before,audio:0},{...was,audio:0},'the reading goes on as it was: only the switch changed');
    await page.clock.fastForward(25000);assert(page.url().endsWith('/fixture/reading-manual'),'off, the page is never moved');await shot('follow-off');
    // Tapped on: at what is being read at once, the sound untouched.
    await page.locator('.cpbar .cpfollow').click();await inChat();
    assert.equal(await on(),true);assert.equal(await pressed(),'true');assert.deepEqual(await snapshot(),before);assert.equal(heard,marks);
    assert(await page.locator('[data-k=part1]').evaluate(e=>e.classList.contains('reading')));
    // Tapped off in its own chat, it stays off through a reload; tapped on again there, nothing moves.
    await page.locator('.cpbar .cpfollow').click();assert.equal(await on(),false);
    await page.reload();await page.waitForFunction(()=>colonyPlayer.state().playing);assert.equal(await on(),false,'the switch is kept on this device');
    await page.locator('.cpbar .cpfollow').click();assert.equal(await on(),true);assert(page.url().endsWith('/fixture/reading-chat'));
    // The same project under a new label is still the chat being read; another project's chat, reached by hand, is not.
    await page.evaluate(()=>colonyNav.go('/fixture/reading-renamed'));await page.waitForURL('**/fixture/reading-renamed');
    await page.waitForFunction(()=>document.querySelector('[data-k=part1]')?.classList.contains('reading'));
    assert(await page.evaluate(()=>colonyPlayer.of('Renamed plants','0')),'renaming a label does not lose reading identity');
    assert.equal(await on(),true);
    await page.evaluate(()=>colonyNav.go('/fixture/reading-other'));await page.waitForURL('**/fixture/reading-other');
    await page.waitForFunction(()=>!colonyPlayer.following());
    assert.equal(await page.evaluate(()=>colonyPlayer.of('plants','1')),null,'a duplicate label belongs to a different project key');
    assert.equal(await page.locator('.say.reading').count(),0,'the other project is not marked active');
    assert(page.url().endsWith('/fixture/reading-other'));
    // Audio only, a mode chosen before the switch existed, is the switch off; tapped on, it is Aloud with follow.
    await page.locator('.cpbar .cpfollow').click();await inChat();
    await page.evaluate(()=>colonyPlayer.setMode('audio'));assert.equal(await pressed(),'false');
    await page.locator('.cpbar .cpfollow').click();assert.equal(await page.evaluate(()=>colonyPlayer.mode()),'both');assert.equal(await on(),true);
    // Paused, a change of page leaves it on, and Play brings the page to the reply.
    await page.getByRole('button',{name:'Pause',exact:true}).click();await manual();assert.equal(await on(),true);
    await page.getByRole('button',{name:'Play',exact:true}).click();await inChat();
    // Words typed on the page and not sent keep it there; sent or cleared, the next part brings the page along.
    await page.evaluate(()=>fixtureSaid.findLast(u=>u.text.includes('First part')).onend());
    await page.waitForFunction(()=>!colonyPlayer.state().reading);
    await page.goto(base+'/fixture/reading-manual');assert.equal(await on(),true,'with nothing being read, going elsewhere leaves it on');
    const sw=await page.locator('.cpfollowidle').boundingBox(),pill=await page.locator('.cppill').boundingBox();
    assert(sw.x+sw.width<=pill.x+2&&pill.x-(sw.x+sw.width)<=8,'at rest the switch is immediately left of the pill');await shot('follow-idle');
    await page.evaluate(()=>{document.querySelector('main').insertAdjacentHTML('beforeend',"<textarea id='typed'></textarea>");
      const t=document.getElementById('typed');t.value='half an answer';
      colonyPlayer.read({key:'0',name:'plants',href:'/fixture/reading-chat',kokoro:false},[
        {t:'B',k:'part1',text:'First part. Another sentence.'},{t:'B',k:'part2',text:'Second part. More sentences.'}]);});
    await page.waitForFunction(()=>colonyPlayer.state().playing);await page.clock.fastForward(3000);
    assert(page.url().endsWith('/fixture/reading-manual'),'typed words are not taken with the page');assert.equal(await on(),true);
    await page.evaluate(()=>{document.getElementById('typed').value='';fixtureSaid.findLast(u=>u.text.includes('First part')).onend();});
    await inChat();assert.equal(await page.evaluate(()=>colonyPlayer.state().reading.k),'part2');
    assert.deepEqual(errors,[]);console.log('PASS the follow switch: on, off, left by hand, kept, and typed words');
  }catch(e){console.error('Page errors:',errors);console.error('Player:',await page.evaluate(()=>({url:location.href,state:window.colonyPlayer&&colonyPlayer.state(),follow:window.colonyPlayer&&colonyPlayer.following()})));throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
