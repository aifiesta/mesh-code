/**
 * Build the app icon from the brand mark.
 *
 * macOS icons are not bare glyphs — they are a squircle plate with the art
 * inset, which is why the previous transparent-glyph icon looked wrong in
 * the Dock next to every other app. This paints the brand gradient on that
 * plate and knocks the mark out in white.
 *
 * Rendered by Electron rather than rsvg/ImageMagick so a contributor needs
 * no extra image toolchain: Electron is already a dependency.
 *
 *   npx electron packaging/make-icon.js
 */
const { app, BrowserWindow } = require('electron')
const fs = require('fs'), path = require('path')

const ROOT = path.resolve(__dirname, '..')
const MARK = path.join(ROOT, 'ui', 'public', 'brand', 'mark.svg')
const OUT = path.join(__dirname, 'icons', 'icon.png')
const S = 1024
const INSET = 0.19          // Apple's grid leaves the art well inside the plate
const RADIUS = 0.2246 * S   // the macOS squircle-ish corner

app.disableHardwareAcceleration()

app.whenReady().then(async () => {
  const mark = fs.readFileSync(MARK, 'utf8')
    .replace(/width="\d+"/, 'width="100%"')
    .replace(/height="\d+"/, 'height="100%"')

  const html = `<html><body style="margin:0;background:transparent">
    <div style="
      width:${S}px;height:${S}px;border-radius:${RADIUS}px;
      background:linear-gradient(148deg,#8B72FF 0%,#6F5AF5 46%,#5B3FE0 100%);
      display:grid;place-items:center;position:relative;overflow:hidden;
      box-shadow:inset 0 ${S * 0.004}px 0 rgba(255,255,255,.34),
                 inset 0 -${S * 0.01}px ${S * 0.03}px rgba(0,0,0,.18);">
      <div style="position:absolute;inset:0;
        background:radial-gradient(58% 44% at 50% 0%, rgba(255,255,255,.30), transparent 68%);"></div>
      <div style="width:${Math.round(S * (1 - INSET * 2))}px;position:relative;
        filter:brightness(0) invert(1) drop-shadow(0 ${S * 0.012}px ${S * 0.03}px rgba(40,20,110,.34));">
        ${mark}
      </div>
    </div></body></html>`

  const win = new BrowserWindow({
    width: S, height: S, show: false, transparent: true, frame: false,
    useContentSize: true, webPreferences: { offscreen: true, zoomFactor: 1 },
  })
  await win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html))
  await new Promise((r) => setTimeout(r, 900))
  const img = await win.webContents.capturePage()
  fs.mkdirSync(path.dirname(OUT), { recursive: true })
  fs.writeFileSync(OUT, img.toPNG())
  const { width, height } = img.getSize()
  console.log(`wrote ${OUT} (${width}x${height})`)
  win.destroy()
  app.quit()
})
