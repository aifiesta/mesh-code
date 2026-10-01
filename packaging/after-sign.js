'use strict'
/**
 * Re-sign the bundle inside-out after electron-builder packs it.
 *
 * WHY THIS EXISTS — this is the difference between two very different
 * dialogs for whoever you send the app to:
 *
 *   "…is damaged and can't be opened. Move it to the Bin."   ← invalid signature
 *   "…cannot be verified. Open anyway?"                      ← valid, just not notarised
 *
 * The first offers no way forward and looks like malware. We were shipping
 * it, because the frozen Python engine drops ~58 nested Mach-O files
 * (.dylib/.so) into Contents/Resources/engine, electron-builder's ad-hoc
 * pass does not sign them, and an outer seal computed over unsigned nested
 * code is invalid:
 *
 *   code has no resources but signature indicates they must be present
 *
 * codesign requires inner code to be signed BEFORE the enclosing bundle, so
 * this walks the engine's binaries first and signs the app last. It also
 * pins the identifier, which otherwise ends up as the literal string
 * "Electron" rather than the app's own bundle id.
 *
 * This is still an AD-HOC signature: it makes the bundle internally valid,
 * it does not make it notarised. With a real Developer ID configured,
 * electron-builder signs properly and this becomes a no-op.
 */
const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const BUNDLE_ID = 'ai.meshapi.code'

function machOFiles(dir) {
  const out = []
  if (!fs.existsSync(dir)) return out
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name)
    if (entry.isDirectory()) out.push(...machOFiles(p))
    else if (/\.(dylib|so)$/.test(entry.name)) out.push(p)
    else {
      try {
        // executable bit + a Mach-O magic number
        if (fs.statSync(p).mode & 0o111) {
          const fd = fs.openSync(p, 'r')
          const buf = Buffer.alloc(4)
          fs.readSync(fd, buf, 0, 4, 0)
          fs.closeSync(fd)
          const magic = buf.readUInt32BE(0)
          if ([0xcffaedfe, 0xcefaedfe, 0xfeedfacf, 0xfeedface, 0xcafebabe].includes(magic)) out.push(p)
        }
      } catch { /* unreadable — skip */ }
    }
  }
  return out
}

const sign = (target, extra = []) =>
  execFileSync('codesign', ['--force', '--timestamp=none', ...extra, '--sign', '-', target],
    { stdio: 'pipe' })

exports.default = async function afterSign(context) {
  if (context.electronPlatformName !== 'darwin') return
  if (process.env.CSC_NAME || process.env.CSC_LINK) {
    console.log('  • real signing identity present — skipping ad-hoc re-sign')
    return
  }

  const app = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)
  const engine = path.join(app, 'Contents', 'Resources', 'engine')

  const inner = machOFiles(engine)
  for (const f of inner) {
    try { sign(f) } catch (e) { console.warn('  ! could not sign', path.basename(f)) }
  }
  sign(app, ['--deep', '--identifier', BUNDLE_ID])

  // Fail the build rather than ship an invalid signature: an app that
  // reports "damaged" is worse than no build at all.
  try {
    execFileSync('codesign', ['--verify', '--deep', '--strict', app], { stdio: 'pipe' })
  } catch (e) {
    throw new Error('ad-hoc signature is still invalid after re-signing:\n' +
      (e.stderr?.toString() || e.message))
  }
  console.log(`  • ad-hoc re-signed ${inner.length} nested binaries + the bundle (valid)`)
}
