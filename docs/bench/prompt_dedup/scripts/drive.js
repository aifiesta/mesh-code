/* Drive a Mesh Code window that is already open through one of the three
   test prompts, and record it. The app is NOT started or closed by this
   script: it attaches to the running window, works in it like a user would,
   and detaches. Chats stay in the app.

     node drive.js before|after N FRAMES_DIR [REPEAT]

   before = the window whose engine runs the code before the change (debug port 9347)
   after  = the window whose engine runs the code with the change   (debug port 9349) */
const fs = require('fs'), path = require('path')
const { chromium } = require(require('path').join(process.env.HOME, '.npm/_npx/e41f203b7505f1fb/node_modules/playwright-core'))
const { makeVideo } = require('./video.js')
const ROOT = path.resolve(__dirname, '..')
const [ENGINE, N, FRAMES_ROOT, REP] = process.argv.slice(2)
if (!['before', 'after', 'diagnosis'].includes(ENGINE)) throw new Error('first argument: before, after or diagnosis')
// diagnosis: a third window (debug port 9350) whose engine takes its prompt from files, so one window can
// run any prompt variant. VARIANT=name picks the folder under diagnosis/variants/.
const VARIANT = ENGINE === 'diagnosis' ? process.env.VARIANT : null
if (ENGINE === 'diagnosis' && !VARIANT) throw new Error('diagnosis needs VARIANT=name')
const task = JSON.parse(fs.readFileSync(path.join(ROOT, 'prompts.json'), 'utf8'))[Number(N) - 1]
const PORT = ENGINE === 'before' ? 9347 : ENGINE === 'after' ? 9349 : 9350
const PNAME = (VARIANT || ENGINE) + '-' + task.name + (REP ? '-run' + REP : '')      // REPEAT 2, 3...: the same prompt again in a fresh copy of the project
const PROJECT = path.join(ROOT, ENGINE, 'projects', PNAME)
const OUT = path.join(ROOT, ENGINE, 'runs', PNAME)
const CAP = 0.10                                   // dollars; the run is stopped above this
const MAX_MS = (task.n === 3 ? 10 : 6) * 60 * 1000
// approval dialogs that are turned down instead of approved
const DANGER = new RegExp(['safety check', 'sudo ', '\\.mesh-code', '\\.ssh', 'wget ', 'git push',
  'pip3? install', 'npm install', 'r' + 'm -r'].join('|'), 'i')
const sleep = ms => new Promise(r => setTimeout(r, ms))
const hide = s => String(s).replace(/token=[^&\s"']+/g, 'token=[hidden]').replace(/rsk_[A-Za-z0-9]+/g, '[key]')

;(async () => {
  fs.mkdirSync(OUT, { recursive: true })
  const browser = await chromium.connectOverCDP('http://127.0.0.1:' + PORT)
  const page = browser.contexts()[0].pages()[0]
  const u = new URL(page.url())
  const port = u.searchParams.get('port'), token = u.searchParams.get('token')

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

  if (!fs.existsSync(PROJECT)) fs.cpSync(path.join(ROOT, '0-projects-before', task.name), PROJECT, { recursive: true })
  if (VARIANT) {                       // the engine reads these two files when the chat starts
    for (const f of ['prompt.txt', 'remember.txt']) fs.copyFileSync(path.join(ROOT, 'diagnosis', 'variants', VARIANT, f), path.join(ROOT, 'diagnosis', 'override', f))
  }
  // open the project (the same request File > Open Project sends); the window focuses it
  let mark = events.length
  send({ type: 'open.workspace', workspace: PROJECT, mode: 'default' })
  const started = await waitFor(e => e.type === 'session.started' && e.data && e.data.workspace === PROJECT, 15000, mark)
  if (!started) throw new Error('the project did not open')
  const sid = started.session
  await sleep(1500)

  const summary = { engine: ENGINE, variant: VARIANT, n: task.n, name: PNAME, kind: task.kind, prompt: task.prompt, session: sid, project: PROJECT }
  // the user may be clicking around in the same window, so check where it is before acting in it
  const onThisChat = async () => (await page.getByText('projects/' + PNAME).count()) > 0
  const focusChat = async () => {
    if (await onThisChat()) return
    send({ type: 'open.workspace', workspace: PROJECT, mode: 'default' }); await sleep(1500)
  }

  try {
    // the app's own route preview for this prompt: free, nothing is sent to a model
    mark = events.length
    send({ type: 'route.preview', session: sid, text: task.prompt })
    const pre = await waitFor(e => e.type === 'route.explain' && e.session === sid, 10000, mark)
    summary.route_preview = pre && { task: pre.data.task, why: pre.data.why, pick: pre.data.pick, thinking: pre.data.thinking }
    console.log('preview:', JSON.stringify(summary.route_preview))

    // approval mode Auto for this chat, so edits and commands inside the project need no click
    const box = page.getByPlaceholder(/Ask for anything/)
    let t0 = 0, sent = null
    for (let attempt = 1; attempt <= 4 && !sent; attempt++) {
      await focusChat()
      const ask = page.getByRole('button', { name: 'Ask every time' })
      if (await ask.count()) {
        await ask.click(); await sleep(600)
        await page.getByText('Also auto-approve shell commands').click(); await sleep(700)
      }
      await box.click(); await box.fill(task.prompt); await sleep(700)
      if (!(await onThisChat()) || (await box.inputValue()) !== task.prompt) { summary.send_attempts = attempt; continue }
      await page.screenshot({ path: path.join(OUT, '1-before-send.png') })
      mark = events.length; t0 = Date.now()
      await page.keyboard.press('Enter')
      sent = await waitFor(e => e.session === sid && e.type === 'turn.started', 6000, mark)
      summary.send_attempts = attempt
    }
    if (!sent) throw new Error('the prompt could not be sent in this chat (the window kept moving to another chat)')
    const approvals = []
    let stoppedWhy = null, end = null
    while (Date.now() - t0 < MAX_MS) {
      end = events.slice(mark).find(e => e.session === sid && (e.type === 'turn.finished' || e.type === 'turn.aborted'))
      if (end) break
      const modal = page.locator('.modal.approval')
      if (await modal.count()) {
        const text = await modal.first().innerText().catch(() => '')
        const refuse = DANGER.test(text)
        approvals.push({ at: Math.round((Date.now() - t0) / 1000), decision: refuse ? 'rejected' : 'approved', text: text.slice(0, 1500) })
        await modal.locator(refuse ? 'button.ghost' : 'button.primary').first().click().catch(() => {})
        await sleep(400); continue
      }
      const cost = Math.max(0, ...events.slice(mark).filter(e => e.session === sid && e.type === 'status').map(e => e.data.turn_cost || 0))
      if (cost > CAP && !stoppedWhy) {
        stoppedWhy = `stopped by the test: cost $${cost} passed the $${CAP} cap for this run`
        await page.getByRole('button', { name: /^Stop/ }).first().click().catch(() => {})
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
    summary.end = end && { type: end.type, ...end.data, plan: undefined }
    summary.tools = mine.filter(e => e.type === 'tool.started').map(e => e.data.name)
    await sleep(2500)
    if (await onThisChat()) {
      await page.screenshot({ path: path.join(OUT, '2-finished.png') })
      fs.writeFileSync(path.join(OUT, 'screen-text-at-end.txt'), await page.locator('body').innerText())
    } else summary.finished_screenshot = 'not taken: the window was showing another chat when the run ended'
  } finally {
    fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 1))
    await sleep(800)
    await cdp.send('Page.stopScreencast').catch(() => {})
    summary.recording = await makeVideo(frames, path.join(OUT, 'recording.webm')).catch(() => null)
    fs.writeFileSync(path.join(OUT, 'summary.json'), JSON.stringify(summary, null, 1))
    log.end(); ws.close()
    await browser.close()            // detaches only; the app keeps running
  }
  console.log(JSON.stringify({ end: summary.end, routed: summary.routed, stopped: summary.stopped,
    approvals: (summary.approvals || []).length, notices: summary.notices, wall: summary.wall_seconds, tools: summary.tools, recording: summary.recording }, null, 1))
})().catch(e => { console.error('ERR', hide(e && e.stack || e).slice(0, 2500)); process.exit(1) })
