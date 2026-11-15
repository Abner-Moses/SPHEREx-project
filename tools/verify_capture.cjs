const { chromium } = require('../.audit/browser/node_modules/playwright');
const fs=require('node:fs');
(async()=>{
const browser=await chromium.launch({headless:true,args:['--enable-unsafe-swiftshader']});
const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto('http://localhost:5173');
await page.waitForFunction(()=>window.__sky);
const targets=(await (await page.request.get('http://localhost:5173/api/demo/targets')).json()).targets;
const results=[];
for(const target of targets){
  await page.evaluate(t=>window.__sky.jumpTo(t.ra_epoch,t.dec_epoch,2),target);
  await page.waitForFunction(()=>Math.abs(window.__sky.getState().fov-2)<0.01);
  await page.keyboard.press('c');
  const aim=page.getByRole('application',{name:/Capture: drag/});await aim.waitFor();
  const canvas=await page.locator('canvas.gl').boundingBox();
  await page.mouse.click(canvas.x+canvas.width/2,canvas.y+canvas.height/2);
  const analyze=page.getByRole('button',{name:'Analyze · science',exact:true});
  try{
    await analyze.waitFor({timeout:15000});
    await page.screenshot({path:`audit-evidence/${target.id}-capture.png`});
    await analyze.click();
    await page.getByRole('dialog',{name:'Capture analysis'}).waitFor();
    await page.waitForFunction(()=>!document.querySelector('.da-status.running'),{timeout:180000});
    const spectrum=page.getByRole('button',{name:/^Spectrum/});await spectrum.waitFor({timeout:5000});
    const unavailableAll=await page.getByText(/Chart unavailable/).allTextContents();
    await spectrum.click();
    await page.waitForFunction(()=>document.querySelector('.da-groups button.on')?.textContent.startsWith('Spectrum'));
    await page.getByText('Local cube spectrum (102 channels)',{exact:true}).waitFor({timeout:5000});
    await page.screenshot({path:`audit-evidence/${target.id}-spectrum.png`});
    const unavailableSpectrum=await page.getByText(/Chart unavailable/).allTextContents();
    const text=await page.getByRole('dialog',{name:'Capture analysis'}).innerText();
    results.push({id:target.id,fov:await page.evaluate(()=>window.__sky.getState().fov),localCube:true,unavailableAll,unavailableSpectrum,text});
    await page.getByRole('button',{name:'Close analysis',exact:true}).click();
  }catch(e){
    results.push({id:target.id,error:e.message,text:(await page.locator('body').innerText()).slice(-8000)});
    await page.screenshot({path:`audit-evidence/${target.id}-failure.png`});
    if(await page.getByRole('button',{name:'Close analysis',exact:true}).count())await page.getByRole('button',{name:'Close analysis',exact:true}).click();
  }
  if(await page.getByRole('button',{name:'Close capture',exact:true}).count())await page.getByRole('button',{name:'Close capture',exact:true}).click();
}
fs.writeFileSync('audit-evidence/frontend-browser-results.json',JSON.stringify({results,pageErrors:errors},null,2));
console.log(JSON.stringify({results:results.map(({text,...r})=>r),pageErrors:errors},null,2));
await browser.close();
if(errors.length || results.some(r=>r.error || !r.localCube || r.unavailableAll.length || r.unavailableSpectrum.length)) process.exitCode=1;
})().catch(e=>{console.error(e);process.exitCode=1});
