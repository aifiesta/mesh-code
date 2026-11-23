/* Live check of "the Token savings setting is removed".
   Attaches to a Mesh Code window started from this branch (it does not start
   or close the app) and records the window while it:
     1. checks the setting is gone: not in the settings the engine sends, not
        in the Spend pop-up, not in the settings sheet, not found by the
        command palette;
     2. runs one short read-only task per model given, pinned for that chat,
        and keeps each step's bill (fresh, cached and written input tokens);
     3. reads the config file back to show the old key was dropped.

     node drive.js PORT CONFIG_FILE PROJECTS_DIR OUT_DIR MODEL[,MODEL...] [CAP_DOLLARS]

   The window must have been started with HOME pointing at a test folder whose
   config file still has the old `optimize` key, so the user's own settings
   are never touched. PROJECTS_DIR/<short model name> is the project per run. */
const fs = require('fs'), path = require('path')
const { chromium } = require(path.join(process.env.HOME, '.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core'))
const { makeVideo } = require('../caching_pruning/B-live/scripts/video.js')
const [PORT, CONFIG, PROJECTS, OUT, MODELS, CAPARG] = process.argv.slice(2)
const CAP = Number(CAPARG || 0.05)          // dollars per run; the run is stopped above this
const PROMPT = 'Read calc.py and util.py and tell me in two sentences what score() returns. Do not change any file.'
const sleep = ms => new Promise(r => setTimeout(r, ms))
const hide = s => String(s).replace(/token=[^&\s"']+/g, 'token=[hidden]').replace(/rsk_[A-Za-z0-9]+/g, '[key]')
const short = m => m.split('/').pop().includes('haiku') ? 'haiku' : m.split('/').pop().split('-')[0]

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  const result = { checks: {}, runs: [] }
  const browser = await chromium.connectOverCDP('http://127.0.0.1:' + PORT)
  const page = browser.contexts()[0].pages()[0]
  const u = new URL(page.url())
  const port = u.searchParams.get('port'), token = u.searchParams.get('token')
  const text = () => page.evaluate(() => document.body.innerText)
  const shot = name => page.screenshot({ path: path.join(OUT, name) }).catch(() => {})

  // a second listener on the engine: the greeting carries the settings schema
  const events = []
  const ws = new WebSocket(`ws://127.0.0.1:${port}/ws?token=${token}`)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('could not reach the engine')) })
  ws.onmessage = m => { try { const ev = JSON.parse(m.data); if (!['assistant.delta', 'assistant.progress', 'catalog', 'dir.listing'].includes(ev.type)) events.push(ev) } catch {} }
  const send = o => ws.send(JSON.stringify(o))
  const waitFor = async (pred, ms, from = 0) => {
    const end = Date.now() + ms
    while (Date.now() < end) { const e = events.slice(from).find(pred); if (e) return e; await sleep(150) }
    return null
  }

  const frames = [], fdir = path.join(OUT, 'frames'); fs.mkdirSync(fdir, { recursive: true })
  const cdp = await browser.contexts()[0].newCDPSession(page)
  cdp.on('Page.screencastFrame', f => {
    const file = path.join(fdir, String(frames.length).padStart(6, '0') + '.jpg')
    fs.writeFileSync(file, Buffer.from(f.data, 'base64')); frames.push({ file, at: Date.now() })
    cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {})
  })
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 70, maxWidth: 1280, maxHeight: 880, everyNthFrame: 1 })

  try {
    const hello = await waitFor(e => e.data && e.data.settings && e.data.settings.settings, 8000)
    const keys = hello ? hello.data.settings.settings.map(s => s.key) : null
    result.checks.engine_settings_have_optimize = keys ? keys.includes('optimize') : 'no greeting seen'
    result.checks.engine_settings_have_a_dial = hello ? hello.data.settings.settings.some(s => s.type === 'dial') : null

    // open the first project so the control bar is showing
    const first = path.join(PROJECTS, short(MODELS.split(',')[0]))
    let mark = events.length
    send({ type: 'open.workspace', workspace: first, mode: 'default' })
    await waitFor(e => e.type === 'session.started' && e.data && e.data.workspace === first, 15000, mark)
    await sleep(2000)

    // Spend pop-up: it used to hold the slider
    await page.locator('.controlbar .ctl-cost').first().click(); await sleep(900)
    await shot('ui-1-spend-popup.png')
    const pop = await page.evaluate(() => (document.querySelector('.pop') || {}).innerText || '')
    result.checks.spend_popup_text = pop
    result.checks.spend_popup_has_token_savings = /token savings/i.test(pop)
    await page.keyboard.press('Escape'); await sleep(500)

    // the settings sheet, every category
    await page.keyboard.press('Meta+Comma'); await sleep(1200)
    await shot('ui-2-settings.png')
    const cats = await page.evaluate(() => [...document.querySelectorAll('button')].map(b => b.innerText.trim()).filter(t => /^(Behaviour|Behavior)$/i.test(t)))
    if (cats.length) { await page.getByRole('button', { name: cats[0], exact: true }).first().click().catch(() => {}); await sleep(900); await shot('ui-3-settings-behaviour.png') }
    result.checks.settings_sheet_has_token_savings = /token savings/i.test(await text())
    await page.keyboard.press('Escape'); await sleep(600)

    // the command palette
    await page.keyboard.press('Meta+k'); await sleep(800)
    await page.keyboard.type('token savings', { delay: 40 }); await sleep(800)
    await shot('ui-4-palette-token-savings.png')
    result.checks.palette_token_savings = await page.evaluate(() => (document.querySelector('.palette') || document.body).innerText.slice(0, 600))
    await page.keyboard.press('Meta+a'); await page.keyboard.type('spend', { delay: 40 }); await sleep(800)
    await shot('ui-5-palette-spend.png')
    result.checks.palette_spend = await page.evaluate(() => (document.querySelector('.palette') || document.body).innerText.slice(0, 600))
    await page.keyboard.press('Escape'); await sleep(600)

    // one short task per model
    for (const model of MODELS.split(',')) {
      const project = path.join(PROJECTS, short(model))
      mark = events.length
      send({ type: 'open.workspace', workspace: project, mode: 'default' })
      const started = await waitFor(e => e.type === 'session.started' && e.data && e.data.workspace === project, 15000, mark)
      const sid = started && started.session
      if (!sid) throw new Error('the project did not open: ' + project)
      await sleep(1500)
      for (const [key, value] of [['route_mode', 'off'], ['model', model]]) { send({ type: 'set.config', session: sid, key, value }); await sleep(800) }
      const box = page.getByPlaceholder(/Ask for anything/)
      await box.click(); await box.fill(PROMPT); await sleep(500)
      await shot(`run-${short(model)}-1-before-send.png`)
      mark = events.length
      const t0 = Date.now()
      await page.keyboard.press('Enter')
      let end = null, stopped = null
      const approvals = []
      while (Date.now() - t0 < 4 * 60 * 1000) {
        end = events.slice(mark).find(e => e.session === sid && (e.type === 'turn.finished' || e.type === 'turn.aborted'))
        if (end) break
        const ask = await page.evaluate(() => { const m = document.querySelector('.modal.approval'); return m ? m.innerText : null }).catch(() => null)
        if (ask) {    // read-only task: a read is approved, anything else is turned down
          const ok = /read_file|read file|\bread\b/i.test(ask) && !/write|edit|run_bash|command/i.test(ask)
          approvals.push({ decision: ok ? 'approved' : 'rejected', text: ask.slice(0, 400) })
          await page.evaluate(ok => { const b = document.querySelector('.modal.approval ' + (ok ? 'button.primary' : 'button.ghost')); if (b) b.click() }, ok)
          await sleep(500)
        }
        const cost = Math.max(0, ...events.slice(mark).filter(e => e.session === sid && e.type === 'status').map(e => e.data.turn_cost || 0))
        if (cost > CAP && !stopped) { stopped = `stopped: $${cost} passed the $${CAP} cap`; send({ type: 'interrupt', session: sid }) }
        await sleep(400)
      }
      await sleep(2000)
      await shot(`run-${short(model)}-2-finished.png`)
      const mine = events.slice(mark).filter(e => e.session === sid)
      const status = mine.filter(e => e.type === 'status').pop()
      result.runs.push({
        model, session: sid, prompt: PROMPT, stopped, approvals, seconds: Math.round((Date.now() - t0) / 1000),
        chat_model: status && status.data.model,
        end: end && { type: end.type, hops: end.data.hops, tokens: end.data.tokens, cost: end.data.cost, model: end.data.model },
        steps: mine.filter(e => e.type === 'hop.usage').map(e => ({ hop: e.data.hop, model: e.data.model, cost: e.data.cost, bill: e.data.bill })),
        tools: mine.filter(e => e.type === 'tool.started').map(e => e.data.name),
        errors: mine.filter(e => e.type === 'error' || e.type === 'notice').map(e => e.data.message),
        reply: (mine.filter(e => e.type === 'assistant.message').pop() || { data: {} }).data.text,
      })
    }
    const saved = JSON.parse(fs.readFileSync(CONFIG, 'utf8'))
    result.checks.config_file_still_has_optimize = 'optimize' in saved
  } catch (e) {
    result.error = hide(e && e.stack || e)
  } finally {
    await cdp.send('Page.stopScreencast').catch(() => {})
    result.recording = await makeVideo(frames, path.join(OUT, 'recording.webm')).catch(() => null)
    fs.rmSync(fdir, { recursive: true, force: true })
    fs.writeFileSync(path.join(OUT, 'result.json'), JSON.stringify(result, null, 1))
    ws.close(); await browser.close()          // detaches only; the window stays open
  }
  console.log(JSON.stringify({ checks: result.checks, error: result.error,
    runs: result.runs.map(r => ({ model: r.model, chat_model: r.chat_model, end: r.end, stopped: r.stopped, errors: r.errors,
      steps: r.steps.map(s => s.bill) })) }, null, 1).slice(0, 6000))
})().catch(e => { console.error('ERR', hide(e && e.stack || e).slice(0, 1500)); process.exit(1) })
