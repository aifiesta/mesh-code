/* What is a window showing right now?   node peek.js PORT */
const { chromium } = require('/Users/asjadahmad/.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core')
;(async () => {
  const browser = await chromium.connectOverCDP('http://127.0.0.1:' + process.argv[2])
  const page = browser.contexts()[0].pages()[0]
  const t = p => Promise.race([p, new Promise(r => setTimeout(() => r('(no answer in 5 s)'), 5000))])
  console.log('visibility:', await t(page.evaluate(() => document.visibilityState + ' focus=' + document.hasFocus())))
  console.log('approval dialogs:', await t(page.locator('.modal.approval').count()))
  console.log('any modal:', await t(page.locator('.modal').count()))
  const txt = await t(page.locator('body').innerText()); console.log(String(txt).slice(-700))
  await browser.close()
})().catch(e => { console.error('ERR', String(e).slice(0, 300)); process.exit(1) })
