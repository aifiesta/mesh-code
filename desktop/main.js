'use strict'
/**
 * Electron main — window, menus, and the engine's lifecycle.
 *
 * The engine is a child process, not a library. That is the whole point of
 * the architecture: this file knows how to start it, where it is listening,
 * and how to stop it, and nothing else. It never touches a conversation.
 *
 * Startup handshake: spawn the engine, read ONE JSON line from its stdout
 * ({ready, port, token}), then point the window at that origin. The port is
 * ephemeral and the token is per-launch, so there is nothing to guess and
 * nothing to squat on. Everything after that is WebSocket traffic the
 * renderer owns.
 */
const { app, BrowserWindow, dialog, shell, Menu, ipcMain, nativeTheme } = require('electron')
const { spawn } = require('node:child_process')
const path = require('node:path')
const fs = require('node:fs')
const readline = require('node:readline')

const isDev = !app.isPackaged
let engine = null
let win = null
let endpoint = null

/** Where the engine lives: a frozen binary when packaged, the repo in dev. */
function engineCommand() {
  if (!isDev) {
    const dir = path.join(process.resourcesPath, 'engine')
    const exe = process.platform === 'win32' ? 'mesh-code-engine.exe' : 'mesh-code-engine'
    return { cmd: path.join(dir, exe), args: [], cwd: dir }
  }
  const repo = path.resolve(__dirname, '..')
  const py = process.env.MESH_CODE_PYTHON || 'python3'
  return {
    cmd: py,
    args: ['-m', 'meshharness.server'],
    cwd: path.join(repo, 'engine'),
  }
}

function uiDir() {
  return isDev
    ? path.resolve(__dirname, '..', 'ui', 'dist')
    : path.join(process.resourcesPath, 'ui')
}

function startEngine() {
  return new Promise((resolve, reject) => {
    const { cmd, args, cwd } = engineCommand()
    const ui = uiDir()
    if (!fs.existsSync(ui)) {
      reject(new Error(`UI assets are missing at ${ui}. Run \`npm run build\` in ui/ first.`))
      return
    }

    engine = spawn(cmd, [...args, '--ui', ui], {
      cwd,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['ignore', 'pipe', 'pipe'],
    })

    let settled = false
    const timer = setTimeout(() => {
      if (!settled) { settled = true; reject(new Error('the engine did not report ready within 30s')) }
    }, 30000)

    // The engine prints exactly one JSON line, carrying the auth token. It
    // goes to a private pipe — never a shared terminal.
    readline.createInterface({ input: engine.stdout }).on('line', (line) => {
      if (settled) return
      try {
        const msg = JSON.parse(line)
        if (msg.ready) {
          settled = true
          clearTimeout(timer)
          endpoint = { port: msg.port, token: msg.token }
          resolve(endpoint)
        }
      } catch { /* not the handshake line */ }
    })

    let stderrTail = ''
    engine.stderr.on('data', (d) => {
      stderrTail = (stderrTail + d.toString()).slice(-4000)
      if (isDev) process.stderr.write(`[engine] ${d}`)
    })

    engine.on('error', (e) => {
      if (!settled) { settled = true; clearTimeout(timer); reject(e) }
    })
    engine.on('exit', (code) => {
      engine = null
      if (!settled) {
        settled = true
        clearTimeout(timer)
        reject(new Error(`the engine exited with code ${code}\n\n${stderrTail}`))
      } else if (code !== 0 && win) {
        dialog.showErrorBox('Mesh Code',
          `The engine stopped unexpectedly (code ${code}).\n\n${stderrTail}`)
      }
    })
  })
}

function createWindow() {
  const mac = process.platform === 'darwin'
  win = new BrowserWindow({
    width: 1280,
    height: 880,
    minWidth: 940,
    minHeight: 620,
    show: false,
    titleBarStyle: mac ? 'hiddenInset' : 'default',
    trafficLightPosition: mac ? { x: 18, y: 20 } : undefined,
    // Real macOS vibrancy, not a CSS imitation: the compositor samples what
    // is actually behind the window. CSS backdrop-filter can only blur the
    // page's own layers, so it can never produce this. Elsewhere the app
    // paints its own opaque ground and looks the same minus the depth.
    ...(mac
      ? { vibrancy: 'under-window', visualEffectState: 'active',
          backgroundColor: '#00000000' }
      : { backgroundColor: '#0b0b0f' }),
    icon: path.join(__dirname, '..', 'packaging', 'icons', 'icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      // The renderer displays model output and file contents. It gets no
      // Node, no remote module, and a locked-down context — everything
      // privileged goes through the narrow preload bridge below.
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: false,
    },
  })

  win.once('ready-to-show', () => win.show())

  // External links open in the real browser, never inside the app shell.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (e, url) => {
    if (!url.startsWith(`http://127.0.0.1:${endpoint.port}`)) {
      e.preventDefault()
      if (/^https?:\/\//i.test(url)) shell.openExternal(url)
    }
  })

  const q = new URLSearchParams({ port: String(endpoint.port), token: endpoint.token })
  win.loadURL(`http://127.0.0.1:${endpoint.port}/?${q}`)
  win.on('closed', () => { win = null })
}

function buildMenu() {
  const mac = process.platform === 'darwin'
  const template = [
    ...(mac ? [{ role: 'appMenu' }] : []),
    {
      label: 'File',
      submenu: [
        {
          label: 'Open Project…',
          accelerator: 'CmdOrCtrl+O',
          click: async () => {
            const dir = await pickFolder()
            if (dir) win?.webContents.send('open-workspace', dir)
          },
        },
        { type: 'separator' },
        mac ? { role: 'close' } : { role: 'quit' },
      ],
    },
    { role: 'editMenu' },
    { role: 'viewMenu' },
    { role: 'windowMenu' },
    {
      role: 'help',
      submenu: [
        { label: 'Mesh API Docs', click: () => shell.openExternal('https://docs.meshapi.ai') },
        {
          label: 'Show Engine Logs',
          click: () => shell.openPath(app.getPath('logs')),
        },
      ],
    },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

async function pickFolder() {
  const r = await dialog.showOpenDialog(win, {
    title: 'Choose a project folder',
    properties: ['openDirectory', 'createDirectory'],
  })
  return r.canceled ? null : r.filePaths[0]
}

app.whenReady().then(async () => {
  ipcMain.handle('pick-folder', pickFolder)
  // 'system' hands control back to macOS; 'light'/'dark' force it, which is
  // what repaints the vibrancy behind a manually-chosen theme.
  ipcMain.handle('set-theme', (_e, theme) => {
    nativeTheme.themeSource = ['light', 'dark'].includes(theme) ? theme : 'system'
    return nativeTheme.shouldUseDarkColors
  })
  try {
    await startEngine()
  } catch (e) {
    dialog.showErrorBox('Mesh Code could not start', String(e.message || e))
    app.quit()
    return
  }
  buildMenu()
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// The engine owns child processes of its own (dev servers the agent
// started), and it shuts them down on SIGTERM. Give it that chance rather
// than orphaning a tree of node processes on the user's machine.
function stopEngine() {
  if (engine && !engine.killed) {
    try { engine.kill('SIGTERM') } catch { /* already gone */ }
  }
  engine = null
}
app.on('before-quit', stopEngine)
app.on('window-all-closed', () => {
  stopEngine()
  if (process.platform !== 'darwin') app.quit()
})
process.on('exit', stopEngine)
