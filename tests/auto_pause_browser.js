const {chromium} = require('/opt/colony-test-playwright');
const assert = require('node:assert/strict'), fs = require('node:fs');
(async () => {
  const browser = await chromium.launch({executablePath:'/opt/colony-test-chrome/chrome',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  const base=process.argv[2], errors=[];let line=[];page.setDefaultTimeout(7000);
  page.on('pageerror',e=>errors.push(String(e)));
  await page.clock.install({time:new Date('2026-10-30T18:00:00Z')});
  await page.addInitScript(()=>{
    window.fixtureSaid=[];
    HTMLMediaElement.prototype.play=function(){return Promise.resolve();};HTMLMediaElement.prototype.pause=function(){};
    Object.defineProperty(window,'speechSynthesis',{value:Object.assign(new EventTarget(),{
      getVoices:()=>[],speak(u){if(u.text)fixtureSaid.push(u);if(u.onstart)u.onstart();},cancel(){},pause(){},resume(){}})});
    if(!sessionStorage.getItem('autoPauseStarted')){
      sessionStorage.setItem('autoPauseStarted','1');localStorage.setItem('aloud','1');localStorage.setItem('automode','audio');
      localStorage.setItem('armed',JSON.stringify(Date.now()-1000));localStorage.setItem('alive',JSON.stringify(Date.now()));
    }
  });
  await page.route('**/chat/line?**',r=>r.fulfill({contentType:'application/json',body:JSON.stringify({line,
    projects:{'0':{name:'plants',href:'/fixture/reading-chat'}},total:line.length,ready:false})}));
  await page.route('**/chat/text?**',r=>{
    const keys=new URL(r.request().url()).searchParams.get('k').split(',');
    return r.fulfill({contentType:'application/json',body:JSON.stringify({blocks:keys.map(k=>({k,text:`Queued ${k}. All done.`}))})});
  });
  await page.route('**/chat/feed?**',r=>r.fulfill({status:304,body:''}));
  await page.route('**/chat/heard',r=>r.fulfill({contentType:'application/json',body:'{}'}));
  const pause=page.getByRole('button',{name:'Pause Auto',exact:true}),resume=page.getByRole('button',{name:'Resume Auto',exact:true});
  const queued=()=>page.evaluate(()=>({auto:colonyPlayer.auto(),held:colonyPlayer.autoPaused(),mode:colonyPlayer.mode(),
    waiting:colonyPlayer.state().waiting,reading:colonyPlayer.state().reading,sounds:fixtureSaid.length}));
  const layout=async()=>{
    const pill=await page.locator('.cppill').boundingBox(),button=await resume.boundingBox(),ret=await page.locator('.cpreturn').boundingBox();
    assert(button.width>=44&&button.height>=44,'Resume Auto has a 44px touch target');
    assert(button.x>=pill.x+pill.width-2&&button.x-(pill.x+pill.width)<=8,'Resume sits immediately right of the pill');
    assert(ret&&ret.x+ret.width<=pill.x+2&&pill.x-(ret.x+ret.width)<=8,'Return stays immediately left of the pill');
  };
  try{
    await page.goto(base+'/fixture/reading-chat');await pause.waitFor();
    const target=await pause.boundingBox();assert(target.width>=44&&target.height>=44,'Pause Auto has a 44px touch target');
    await pause.click();assert.equal(await page.evaluate(()=>JSON.parse(localStorage.getItem('autopaused'))),true);
    const armed=await page.evaluate(()=>localStorage.getItem('armed')),at=await page.evaluate(()=>new Date(Date.now()).toISOString());
    line=['alpha','beta'].map(t=>({p:'0',t,at,w:`Queued ${t}.`,b:[[t,at]]}));
    await page.clock.fastForward(4100);await page.waitForFunction(()=>colonyPlayer.state().waiting===2);
    assert.deepEqual(await queued(),{auto:true,held:true,mode:'audio',waiting:2,reading:null,sounds:0});
    assert.equal(await page.locator('.cpsay').textContent(),'Auto paused · 2');
    await page.touchscreen.tap(200,180);await page.goto(base+'/fixture/reading-manual');
    await page.getByRole('heading',{name:'Manual page'}).click();await page.clock.fastForward(4100);
    await page.waitForFunction(()=>colonyPlayer.state().waiting===2);
    assert.deepEqual(await queued(),{auto:true,held:true,mode:'audio',waiting:2,reading:null,sounds:0},'navigation and unrelated taps keep Auto paused');
    // A deliberate reply Play still works, including its ordinary bar pause, while future Auto remains held.
    await page.evaluate(()=>colonyPlayer.read({key:'0',name:'plants',href:'/fixture/reading-chat',kokoro:false},[
      {t:'manual',k:'part1',text:'First part. Another sentence.'}
    ]));
    await page.waitForFunction(()=>colonyPlayer.state().playing);assert.equal(await page.locator('.cpautohold').isVisible(),false,'expanded playback keeps its ordinary controls');
    await page.getByRole('button',{name:'Pause',exact:true}).click();assert.equal(await page.evaluate(()=>colonyPlayer.state().paused),true);
    await page.getByRole('button',{name:'Play',exact:true}).click();assert.equal(await page.evaluate(()=>colonyPlayer.autoPaused()),true);
    await page.evaluate(()=>fixtureSaid.findLast(u=>u.text.includes('First part')).onend());
    await page.waitForFunction(()=>!colonyPlayer.state().reading);await resume.waitFor();await layout();
    await page.reload();await resume.waitFor();await page.waitForFunction(()=>colonyPlayer.state().waiting===2);
    assert.deepEqual(await queued(),{auto:true,held:true,mode:'audio',waiting:2,reading:null,sounds:0},'pause and queue survive reload');
    await page.clock.fastForward(2*3600e3+1);await page.getByRole('heading',{name:'Manual page'}).click();
    assert.equal(await page.evaluate(()=>localStorage.getItem('armed')),armed,'a long manual Auto pause retains its original arrival cutoff');
    await layout();
    if(process.env.COLONY_TEST_ARTIFACTS){fs.mkdirSync(process.env.COLONY_TEST_ARTIFACTS,{recursive:true});await page.screenshot({path:process.env.COLONY_TEST_ARTIFACTS+'/auto-paused-390.png'});}
    await page.locator('.cppill').click();assert.equal(await page.locator('.cpsheet').isVisible(),true,'paused pill opens the queue');
    assert.equal(await page.locator('.cpmain .cprows li').count(),2);assert.equal(await page.evaluate(()=>colonyPlayer.autoPaused()),true);
    await page.locator('.cpgrab').click();await resume.click();
    await page.waitForFunction(()=>colonyPlayer.state().reading?.k==='alpha');
    assert.equal(await page.evaluate(()=>colonyPlayer.autoPaused()),false);assert.equal(await page.evaluate(()=>colonyPlayer.auto()),true);
    assert.equal(await page.evaluate(()=>localStorage.getItem('armed')),armed);
    await page.evaluate(()=>fixtureSaid.findLast(u=>u.text.includes('Queued alpha')).onend());
    await page.waitForFunction(()=>colonyPlayer.state().reading?.k==='beta');
    await page.evaluate(()=>fixtureSaid.findLast(u=>u.text.includes('Queued beta')).onend());
    await page.waitForFunction(()=>!colonyPlayer.state().reading);await pause.waitFor();
    await page.locator('.cppill').click();await page.locator('.cpsw').click();await page.locator('.cpgrab').click();
    assert.equal(await page.locator('.cpautohold').isVisible(),false,'Auto Off hides the collapsed pause control');
    assert.deepEqual(errors,[]);console.log('PASS collapsed Auto pause and persistent queue');
  }catch(e){console.error('Page errors:',errors);throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
