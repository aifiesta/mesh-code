/* Approve the "Start this server?" dialog a test chat is waiting on, then wait for the turn to end and
   save what the window shows.   node approve.js PORT OUT_DIR */
const fs = require('fs'), path = require('path')
const { chromium } = require('/Users/asjadahmad/.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core')
const sleep = ms => new Promise(r => setTimeout(r, ms))
;(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:' + process.argv[2])
  const page = browser.contexts()[0].pages()[0]
  const text = await page.evaluate(() => { const m = document.querySelector('.modal.approval'); return m ? m.innerText : null })
  console.log('dialog:', JSON.stringify(text))
  if (text && /Start this server\?/.test(text) && /mesh-code-prompt-dedup/.test(text) && /python3 -m http\.server/.test(text)) {
    await page.evaluate(() => document.querySelector('.modal.approval button.primary').click())
    console.log('approved')
  } else { console.log('not the expected dialog, left alone'); await browser.close(); return }
  for (let i = 0; i < 60; i++) {
    await sleep(2000)
    const body = await page.evaluate(() => document.body.innerText)
    if (/hops · [\d,]+ tok · \$[\d.]+ · [\d.]+s\s*\n\s*Auto/.test(body.slice(-900)) || /· \d+ hops ·/.test(body.slice(-600))) {
      fs.writeFileSync(path.join(process.argv[3], 'screen-text-at-end.txt'), body); console.log(body.slice(-500)); break }
  }
  await browser.close()
})().catch(e => { console.error('ERR', String(e).slice(0, 300)); process.exit(1) })
