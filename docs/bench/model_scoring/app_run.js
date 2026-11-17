/* Drive the real Mesh Code desktop app through one task and record it.

   node app_run.js OUTDIR MODEL "LEVEL LABEL" PROJECT_DIR "PROMPT"

   LEVEL LABEL is the text of the Reasoning option in Settings (Off, Low,
   Medium, High, Extra high, Max). The app runs with its own home folder under
   OUTDIR, so the user's saved chats and settings are untouched. Makes real,
   billed model calls. Needs:
     PLAYWRIGHT   path to a playwright-core package
     ELECTRON     path to the Electron binary (desktop/node_modules/…)
     RELAY_URL    optional, e.g. http://127.0.0.1:8899/v1 (see record_relay.py)
   Output in OUTDIR: video (.webm), screenshots, events.jsonl (everything the
   engine told the UI), conversation.md. */
const fs = require('fs'), path = require('path'), os = require('os')
const { _electron: electron } = require(process.env.PLAYWRIGHT)
const [OUT, MODEL, LEVEL, PROJECT, PROMPT] = process.argv.slice(2)
const REPO = path.resolve(__dirname, '..', '..', '..')
const shot = (win, name) => win.screenshot({ path: path.join(OUT, name) })

;(async () => {
  fs.mkdirSync(path.join(OUT, 'app-home'), { recursive: true })
  const key = fs.readFileSync(path.join(os.homedir(), '.mesh-code', 'credentials'), 'utf8').trim()
  const env = { ...process.env, HOME: path.join(OUT, 'app-home'), MESHAPI_API_KEY: key,
                MESH_CODE_PYTHON: path.join(REPO, 'engine/.venv/bin/python') }
  if (process.env.RELAY_URL) env.MESHAPI_BASE_URL = process.env.RELAY_URL
  const app = await electron.launch({
    executablePath: process.env.ELECTRON, args: [path.join(REPO, 'desktop')], env,
    recordVideo: { dir: OUT, size: { width: 1280, height: 880 } },
  })
  const win = await app.firstWindow()
  const events = fs.createWriteStream(path.join(OUT, 'events.jsonl'))
  win.on('websocket', ws => ws.on('framereceived', f => { if (typeof f.payload === 'string') events.write(f.payload + '\n') }))
  await win.reload()                                   // so the recorder sees the connection from its first frame
  await win.waitForLoadState('domcontentloaded'); await win.waitForTimeout(2500)

  await app.evaluate(({ BrowserWindow }, dir) => BrowserWindow.getAllWindows()[0].webContents.send('open-workspace', dir), PROJECT)
  await win.waitForTimeout(2500)

  // approval mode: Auto, so edits and commands inside the project need no click
  await win.getByRole('button', { name: 'Ask every time' }).click(); await win.waitForTimeout(700)
  await win.getByText('Also auto-approve shell commands').click(); await win.waitForTimeout(700)

  // model
  await win.getByRole('button', { name: 'Models' }).first().click(); await win.waitForTimeout(900)
  await win.getByPlaceholder(/Fuzzy search/).fill(MODEL.split('/').pop()); await win.waitForTimeout(1200)
  await win.locator('button.use').first().click(); await win.waitForTimeout(1200)
  await win.keyboard.press('Escape'); await win.waitForTimeout(500)

  // thinking level, in Settings where the user would set it
  await win.getByRole('button', { name: 'Settings' }).first().click(); await win.waitForTimeout(900)
  await win.locator('.sheet').getByRole('button', { name: 'Behaviour' }).click(); await win.waitForTimeout(900)
  await win.getByRole('radiogroup', { name: 'Reasoning' }).getByRole('radio', { name: LEVEL, exact: true }).click()
  await win.waitForTimeout(1500); await shot(win, '1-settings.png')
  await win.keyboard.press('Escape'); await win.waitForTimeout(800)

  // the task
  const box = win.getByPlaceholder(/Ask for anything/)
  await box.click(); await box.pressSequentially(PROMPT, { delay: 12 })
  await win.waitForTimeout(600); await shot(win, '2-before-send.png')
  await win.keyboard.press('Enter')
  const stop = win.getByRole('button', { name: /^Stop/ })
  await stop.waitFor({ state: 'visible', timeout: 30000 }).catch(() => {})
  const deadline = Date.now() + 6 * 60 * 1000
  while (Date.now() < deadline) {
    const approve = win.locator('.modal.approval button.primary')
    if (await approve.count()) { await approve.first().click(); await win.waitForTimeout(500); continue }
    if (!(await stop.count())) { await win.waitForTimeout(1500); if (!(await stop.count())) break }
    await win.waitForTimeout(1000)
  }
  await win.waitForTimeout(2500); await shot(win, '3-finished.png')
  fs.writeFileSync(path.join(OUT, 'screen-text-at-end.txt'), await win.locator('body').innerText())
  await app.close(); events.end()
  const vid = fs.readdirSync(OUT).find(f => f.endsWith('.webm') && !f.startsWith('recording'))
  if (vid) fs.renameSync(path.join(OUT, vid), path.join(OUT, 'recording.webm'))
  console.log('done', OUT)
})().catch(e => { console.error('ERR', String(e).replace(/rsk_[A-Za-z0-9]+/g, '[key]').slice(0, 2000)); process.exit(1) })
