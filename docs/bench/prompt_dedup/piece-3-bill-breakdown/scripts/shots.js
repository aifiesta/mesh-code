/* Screenshots of the bill split in the app: the line under the finished turn,
   the Tokens popover and the Spend popover. Clicks only those two pills.

     node shots.js PORT OUT_DIR */
const path = require('path')
const { chromium } = require('/Users/asjadahmad/.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core')
const sleep = ms => new Promise(r => setTimeout(r, ms))
const within = (p, ms) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error('the window did not answer in time')), ms))])
;(async () => {
  const [PORT, OUT] = process.argv.slice(2)
  const browser = await chromium.connectOverCDP('http://127.0.0.1:' + PORT)
  const page = browser.contexts()[0].pages()[0]
  await within(page.bringToFront(), 5000).catch(() => {})
  const click = sel => within(page.evaluate(s => { const b = document.querySelector(s); if (b) b.click(); return !!b }, sel), 8000)
  const text = sel => within(page.evaluate(s => { const e = document.querySelector(s); return e ? e.innerText : null }, sel), 8000)
  console.log('line under the turn:', JSON.stringify(await text('.turn-foot')))
  await within(page.screenshot({ path: path.join(OUT, '3-split-under-the-turn.png') }), 10000)
  await click('.ctl-tok .ctl-btn'); await sleep(600)
  console.log('tokens popover:', JSON.stringify(await text('.ctl-tok .bill-table')))
  await within(page.screenshot({ path: path.join(OUT, '4-tokens-popover.png') }), 10000)
  await click('.ctl-tok .ctl-btn'); await sleep(300)
  await click('.ctl-cost .ctl-btn'); await sleep(600)
  console.log('spend popover:', JSON.stringify(await text('.ctl-cost .bill-table')))
  await within(page.screenshot({ path: path.join(OUT, '5-spend-popover.png') }), 10000)
  await click('.ctl-cost .ctl-btn')
  await browser.close()
})().catch(e => { console.error('ERR', String(e).slice(0, 300)); process.exit(1) })
