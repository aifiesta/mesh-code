'use strict'
/**
 * The only bridge between the renderer and the OS.
 *
 * Kept deliberately tiny: a folder picker and a menu subscription. The
 * renderer already has a capable, authenticated channel to the engine over
 * WebSocket, so nothing else needs to cross this boundary — and every extra
 * method here would be new attack surface reachable from rendered model
 * output.
 */
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('meshHarness', {
  pickFolder: () => ipcRenderer.invoke('pick-folder'),
  setTheme: (theme) => ipcRenderer.invoke('set-theme', theme),
  onOpenWorkspace: (fn) => {
    const handler = (_e, dir) => fn(dir)
    ipcRenderer.on('open-workspace', handler)
    return () => ipcRenderer.removeListener('open-workspace', handler)
  },
  platform: process.platform,
})
