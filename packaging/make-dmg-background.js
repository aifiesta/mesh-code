'use strict'
/**
 * Render the dmg window background.
 *
 * The first-launch instruction has to live where the recipient already is —
 * the window they drag the app out of. Putting it only in a README nobody
 * opens is why "it says damaged" keeps coming back.
 *
 *   npx electron packaging/make-dmg-background.js
 */
const { app, BrowserWindow } = require('electron')
const fs = require('fs'), path = require('path')

const W = 620, H = 470
const OUT = path.join(__dirname, 'dmg-background.png')

app.disableHardwareAcceleration()
app.whenReady().then(async () => {
  const html = `<html><body style="margin:0">
    <div style="width:${W}px;height:${H}px;position:relative;
      background:linear-gradient(160deg,#15131f 0%,#1b1730 45%,#141220 100%);
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
      color:#e9e7f5;overflow:hidden">

      <div style="position:absolute;inset:-40% -20% auto -20%;height:70%;
        background:radial-gradient(50% 60% at 30% 0%,rgba(139,120,247,.30),transparent 70%);
        filter:blur(18px)"></div>

      <div style="position:absolute;top:34px;left:0;right:0;text-align:center">
        <div style="font-size:19px;font-weight:640;letter-spacing:-.02em">Mesh Code</div>
        <div style="font-size:12.5px;color:#a49fc4;margin-top:5px">
          Drag the app into Applications to install
        </div>
      </div>

      <!-- The two icon slots are positioned by electron-builder; this just
           draws the arrow between where they land. -->
      <div style="position:absolute;top:186px;left:281px;width:58px;height:2px;
        background:linear-gradient(90deg,rgba(233,231,245,.15),rgba(233,231,245,.45))"></div>
      <div style="position:absolute;top:180px;left:332px;width:9px;height:9px;
        border-top:2px solid rgba(233,231,245,.45);border-right:2px solid rgba(233,231,245,.45);
        transform:rotate(45deg)"></div>

      <div style="position:absolute;left:38px;right:38px;bottom:26px;
        background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.10);
        border-radius:12px;padding:13px 16px">
        <div style="font-size:12px;font-weight:620;color:#cfc8ff;margin-bottom:5px">
          Says “damaged” on first launch?
        </div>
        <div style="font-size:11.5px;line-height:1.55;color:#a49fc4">
          That’s macOS, not the app. This build isn’t notarised by Apple yet,
          and macOS blocks anything it downloaded. Run this once in Terminal:
        </div>
        <div style="margin-top:7px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
          font-size:11px;color:#e9e7f5;background:rgba(0,0,0,.30);
          border-radius:7px;padding:7px 10px;user-select:all">
          xattr -dr com.apple.quarantine "/Applications/Mesh Code.app"
        </div>
        <div style="margin-top:8px;font-size:11px;line-height:1.5;color:#8f89ad">
          Or skip all of this — installing with
          <span style="font-family:ui-monospace,Menlo,monospace;color:#cfc8ff">curl</span>
          avoids the block entirely:
          <span style="font-family:ui-monospace,Menlo,monospace;color:#cfc8ff">
            curl -fsSL https://code.meshapi.ai/install.sh | sh</span>
        </div>
      </div>
    </div></body></html>`

  const win = new BrowserWindow({
    width: W, height: H, show: false, useContentSize: true,
    webPreferences: { offscreen: true },
  })
  await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html))
  await new Promise((r) => setTimeout(r, 700))
  const img = await win.webContents.capturePage()
  fs.writeFileSync(OUT, img.toPNG())
  // Retina copy: electron-builder picks up @2x automatically.
  fs.writeFileSync(OUT.replace('.png', '@2x.png'), img.toPNG())
  const { width, height } = img.getSize()
  console.log(`wrote ${path.basename(OUT)} (${width}x${height})`)
  win.destroy(); app.quit()
})
