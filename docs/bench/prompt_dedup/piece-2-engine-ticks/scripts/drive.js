/* Piece 2 (the engine ticks the plan). Drive a Mesh Code window that is
   already open through one test prompt, and record it. The app is NOT started
   or closed by this script: it attaches to the running window, works in it
   like a user would, and detaches. Chats stay in the app.

     node drive.js old|new|new2 CASE REPEAT FRAMES_DIR

   old = the window whose engine has the tick tool        (commit ed6e5f6, debug port 9349)
   new = the window whose engine ticks the plan itself    (first version, commit ee36340, debug port 9351)
   new2 = the same, revised after the first live runs     (commit 671cfbf, debug port 9352)
   CASE = a name in ../prompts.json. Each run gets a fresh copy of that case's project.

   Every call into the window has a time limit, so a window that stops
   answering ends the run with an error instead of hanging. */
const fs = require('fs'), path = require('path')
const { chromium } = require('/Users/asjadahmad/.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core')
const { makeVideo } = require('../../scripts/video.js')
const ROOT = path.resolve(__dirname, '..')
const [SIDE, CASE, REP, FRAMES_ROOT] = process.argv.slice(2)
if (!['old', 'new', 'new2'].includes(SIDE)) throw new Error('first argument: old, new or new2')
const task = JSON.parse(fs.readFileSync(path.join(ROOT, 'prompts.json'), 'utf8')).find(t => t.name === CASE)
if (!task) throw new Error('no such case: ' + CASE)
const PORT = { old: 9349, new: 9351, new2: 9352 }[SIDE]
const PNAME = `${SIDE}-${task.name}-run${REP}`
const PROJECT = path.join(ROOT, SIDE, 'projects', PNAME)
const OUT = path.join(ROOT, SIDE, 'runs', PNAME)
const CAP = task.cap                               // dollars; the run is stopped above this
const MAX_MS = task.max_minutes * 60 * 1000
// approval dialogs that are turned down instead of approved
const DANGER = new RegExp(['safety check', 'sudo ', '\\.mesh-code', '\\.ssh', 'wget ', 'git push',
  'pip3? install', 'npm install', 'r' + 'm -r'].join('|'), 'i')
const sleep = ms => new Promise(r => setTimeout(r, ms))
const hide = s => String(s).replace(/token=[^&\s"']+/g, 'token=[hidden]').replace(/rsk_[A-Za-z0-9]+/g, '[key]')
const within = (p, ms, what) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error('the window did not answer in time: ' + what)), ms))])

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  if (!fs.existsSync(PROJECT)) fs.cpSync(task.project, PROJECT, { recursive: true })
  const browser = await chromium.connectOverCDP('http://127.0.0.1:' + PORT)
  const page = browser.contexts()[0].pages()[0]
  const u = new URL(page.url())
  const port = u.searchParams.get('port'), token = u.searchParams.get('token')
  await within(page.bringToFront(), 5000, 'bring to front').catch(() => {})
  const dom = (fn, arg) => within(page.evaluate(fn, arg), 8000, 'page call')

  // a second listener on the engine's event stream: everything the engine tells the window
  const events = []
  const log = fs.createWriteStream(path.join(OUT, 'events.jsonl'))
  const ws = new WebSocket(`ws://127.0.0.1:${port}/ws?token=${token}`)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('could not reach the engine')) })
  ws.onmessage = m => {
    let ev; try { ev = JSON.parse(m.data) } catch { return }
    if (['engine.ready', 'catalog', 'assistant.delta', 'assistant.progress', 'dir.listing'].includes(ev.type)) return
    events.push(ev); log.write(JSON.stringify(ev) + '\n')
  }
  const waitFor = async (pred, ms, from = 0) => {
    const end = Date.now() + ms
    while (Date.now() < end) { const e = events.slice(from).find(pred); if (e) return e; await sleep(100) }
    return null
  }
  const send = o => ws.send(JSON.stringify(o))

  // screen recording of the window, frame by frame
  const frames = [], fdir = path.join(FRAMES_ROOT, PNAME); fs.mkdirSync(fdir, { recursive: true })
  const cdp = await browser.contexts()[0].newCDPSession(page)
  cdp.on('Page.screencastFrame', f => {
    const file = path.join(fdir, String(frames.length).padStart(6, '0') + '.jpg')
    fs.writeFileSync(file, Buffer.from(f.data, 'base64'))
    frames.push({ file, at: Date.now() })
    cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {})
  })
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 70, maxWidth: 1280, maxHeight: 880, everyNthFrame: 1 })

  // open the project (the same request File > Open Project sends); the window focuses it
  let mark = events.length
  send({ type: 'open.workspace', workspace: PROJECT, mode: 'default' })
  const started = await waitFor(e => e.type === 'session.started' && e.data && e.data.workspace === PROJECT, 15000, mark)
  if (!started) throw new Error('the project did not open')
  const sid = started.session
  await sleep(1500)

  const summary = { side: SIDE, case: task.name, name: PNAME, prompt: task.prompt, effort_setting: task.effort || 'auto', session: sid, project: PROJECT }
  const onThisChat = () => dom(n => document.body.innerText.includes('projects/' + n), PNAME)
  const focusChat = async () => {
    if (await onThisChat()) return
    send({ type: 'open.workspace', workspace: PROJECT, mode: 'default' }); await sleep(1500)
  }
  const effortOf = () => { const st = events.filter(e => e.session === sid && e.type === 'status').pop(); return st && st.data.route_effort }
  const savedEffort = () => JSON.parse(fs.readFileSync(path.join(process.env.HOME, '.mesh-code', 'config.json'), 'utf8')).route_effort
  const setEffort = async value => {                // the same message the Effort control in the window sends
    send({ type: 'set.config', session: sid, key: 'route_effort', value }); await sleep(1200)
    return { session: effortOf(), saved: savedEffort() }
  }

  try {
    if (task.effort) summary.effort_during_run = await setEffort(task.effort)

    // the app's own route preview for this prompt: free, nothing is sent to a model
    mark = events.length
    send({ type: 'route.preview', session: sid, text: task.prompt })
    const pre = await waitFor(e => e.type === 'route.explain' && e.session === sid, 10000, mark)
    summary.route_preview = pre && { task: pre.data.task, why: pre.data.why, pick: pre.data.pick, thinking: pre.data.thinking }
    console.log('preview:', JSON.stringify(summary.route_preview))
    if (task.expect_model && !(summary.route_preview && String(summary.route_preview.pick).includes(task.expect_model)))
      throw new Error(`the router would pick ${summary.route_preview && summary.route_preview.pick}, not ${task.expect_model}: nothing was sent`)

    // approval mode Auto for this chat, so edits and commands inside the project need no click
    const box = page.getByPlaceholder(/Ask for anything/)
    let t0 = 0, sent = null
    for (let attempt = 1; attempt <= 4 && !sent; attempt++) {
      await focusChat()
      const ask = page.getByRole('button', { name: 'Ask every time' })
      if (await within(ask.count(), 8000, 'approval mode button')) {
        await within(ask.click(), 8000, 'open approval mode'); await sleep(600)
        await within(page.getByText('Also auto-approve shell commands').click(), 8000, 'choose Auto'); await sleep(700)
      }
      await within(box.click(), 8000, 'click the prompt box'); await within(box.fill(task.prompt), 8000, 'type the prompt'); await sleep(700)
      if (!(await onThisChat()) || (await within(box.inputValue(), 8000, 'read the prompt box')) !== task.prompt) { summary.send_attempts = attempt; continue }
      await within(page.screenshot({ path: path.join(OUT, '1-before-send.png') }), 10000, 'screenshot').catch(() => {})
      mark = events.length; t0 = Date.now()
      await within(page.keyboard.press('Enter'), 8000, 'press Enter')
      sent = await waitFor(e => e.session === sid && e.type === 'turn.started', 6000, mark)
      summary.send_attempts = attempt
    }
    if (!sent) throw new Error('the prompt could not be sent in this chat (the window kept moving to another chat)')
    const approvals = []
    let stoppedWhy = null, end = null
    while (Date.now() - t0 < MAX_MS) {
      end = events.slice(mark).find(e => e.session === sid && (e.type === 'turn.finished' || e.type === 'turn.aborted'))
      if (end) break
      // approval dialogs are read and clicked through the page itself, which keeps working when the window is covered
      const text = await dom(() => { const m = document.querySelector('.modal.approval'); return m ? m.innerText : null }).catch(() => null)
      if (text) {
        const refuse = DANGER.test(text)
        approvals.push({ at: Math.round((Date.now() - t0) / 1000), decision: refuse ? 'rejected' : 'approved', text: text.slice(0, 1500) })
        await dom(r => { const b = document.querySelector('.modal.approval ' + (r ? 'button.ghost' : 'button.primary')); if (b) b.click() }, refuse).catch(() => {})
        await sleep(500); continue
      }
      const cost = Math.max(0, ...events.slice(mark).filter(e => e.session === sid && e.type === 'status').map(e => e.data.turn_cost || 0))
      if (cost > CAP && !stoppedWhy) {
        stoppedWhy = `stopped by the test: cost $${cost} passed the $${CAP} cap for this run`
        send({ type: 'interrupt', session: sid })
        await dom(() => { const b = [...document.querySelectorAll('button')].find(x => /^Stop/.test(x.innerText)); if (b) b.click() }).catch(() => {})
      }
      if (events.slice(mark).some(e => e.session === sid && e.type === 'ask.user')) { stoppedWhy = stoppedWhy || 'the model asked the user a question'; break }
      await sleep(400)
    }
    if (!end && !stoppedWhy) stoppedWhy = 'time limit reached'
    summary.wall_seconds = Math.round((Date.now() - t0) / 100) / 10
    summary.stopped = stoppedWhy
    summary.approvals = approvals
    const mine = events.slice(mark).filter(e => e.session === sid)
    summary.routed = mine.filter(e => e.type === 'routed').map(e => ({ model: e.data.model, thinking: e.data.thinking, task: e.data.task,
      why: e.data.why, escalated: !!e.data.escalated, settled: !!e.data.settled, reason: e.data.reason }))
    summary.notices = mine.filter(e => e.type === 'notice' || e.type === 'error').map(e => e.data.message)
    summary.end = end && { type: end.type, ...end.data }
    summary.tools = mine.filter(e => e.type === 'tool.started').map(e => e.data.name)
    await sleep(2500)
    if (await onThisChat().catch(() => false)) {
      await within(page.screenshot({ path: path.join(OUT, '2-finished.png') }), 10000, 'screenshot').catch(() => {})
      fs.writeFileSync(path.join(OUT, 'screen-text-at-end.txt'), await dom(() => document.body.innerText).catch(() => ''))
    } else summary.finished_screenshot = 'not taken: the window was showing another chat when the run ended'
  } catch (e) {
    summary.error = hide(e && e.message || e)
    throw e
  } finally {
    // the saved Effort goes back to Auto whatever happened above
    if (task.effort) summary.effort_after_run = await setEffort('auto').catch(e => ({ error: hide(e) }))
    fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 1))
    await sleep(800)
    await cdp.send('Page.stopScreencast').catch(() => {})
    summary.recording = await makeVideo(frames, path.join(OUT, 'recording.webm')).catch(() => null)
    fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 1))
    log.end(); ws.close()
    await browser.close()            // detaches only; the app keeps running
  }
  const e = summary.end || {}
  console.log(JSON.stringify({ name: PNAME, model: e.model, steps: e.hops, tokens: e.tokens, cost: e.cost, plan: e.plan && `${e.plan.done}/${e.plan.total}`,
    stopped: summary.stopped, approvals: (summary.approvals || []).length, notices: summary.notices, effort_after: summary.effort_after_run, recording: summary.recording }))
})().catch(e => { console.error('ERR', hide(e && e.stack || e).slice(0, 1500)); process.exit(1) })
