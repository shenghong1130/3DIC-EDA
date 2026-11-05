import {chromium} from 'playwright';
import fs from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
const target=n=>fileURLToPath(new URL('../assets/'+n,import.meta.url));
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--disable-gpu']});
try{
const page=await browser.newPage({viewport:{width:1640,height:1050},deviceScaleFactor:1});
await page.goto('http://127.0.0.1:8501/',{waitUntil:'domcontentloaded'});
await page.waitForFunction(()=>document.querySelector('#case').options.length>0);
let o=await page.locator('#dataset option').evaluateAll(xs=>xs.map(x=>({value:x.value,text:x.text})));console.log('datasets',o);
for(const d of o){await page.selectOption('#dataset',d.value);await page.waitForTimeout(300);if(await page.locator('#case option[value="open_3d_case001"]').count())break;}
await page.selectOption('#case','open_3d_case001');
await page.waitForTimeout(500);
for(const v of ['v3_5','v3_6','v4_2']){
 const id='open_3d_case001/case001_'+v;await page.selectOption('#run',id);
 await page.waitForFunction(label=>S.data?.run?.label===label&&!S.loading,'case001_'+v,{timeout:60000});
 await page.waitForTimeout(1500);
 await page.click('button[data-view="density"]');await page.waitForTimeout(300);
 await page.locator('#grid').uncheck();await page.locator('#pins').uncheck();await page.selectOption('#tier','bottom');await page.waitForTimeout(300);
 await page.locator('.workspace').screenshot({path:target('ui_case001_'+v+'_density.png')});
 if(v==='v4_2'){
  await page.click('button[data-view="congestion"]');await page.waitForTimeout(200);await page.locator('.workspace').screenshot({path:target('ui_case001_v4_2_congestion.png')});
  await page.click('button[data-view="routing"]');await page.selectOption('#tier','both');await page.locator('#crossOnly').check();await page.locator('#pins').check();await page.selectOption('#net',await page.locator('#net option').evaluateAll(xs=>xs.find(x=>x.value!=='all')?.value));await page.click('#mode3d');await page.locator('#onlyNetPins3d').check();await page.waitForTimeout(700);await page.locator('.workspace').screenshot({path:target('ui_case001_v4_2_3d.png')});
 }
 console.log('captured',v,await page.locator('#subtitle').textContent());
}
}finally{await browser.close();}
