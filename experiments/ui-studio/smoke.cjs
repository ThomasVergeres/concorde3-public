const {chromium}=require('playwright-core');
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox','--disable-dev-shm-usage']});
 try {
  const page=await browser.newPage();
  await page.setContent('<main><h1>Browser qualification</h1><button onclick="this.textContent=\'Done\'">Try</button></main>');
  await page.getByRole('button').click();
  if(await page.getByRole('button').textContent()!=='Done') throw Error('interaction failed');
  for(const [name,width,height] of [['desktop',1440,900],['mobile',390,844]]) {
   await page.setViewportSize({width,height});
   await page.screenshot({path:'/tmp/ui-qualification-'+name+'.png'});
  }
  console.log('Chromium: DOM interaction and desktop/mobile screenshots passed');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
