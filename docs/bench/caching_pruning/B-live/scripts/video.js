/* Turn saved window frames into a .webm.   node video.js FRAMES_DIR OUT.webm
   Frames are JPEGs named in order; each one's time is its file time. */
const fs = require('fs'), path = require('path'), { spawn } = require('child_process')
const FFMPEG = path.join(process.env.HOME, 'Library/Caches/ms-playwright/ffmpeg-1011/ffmpeg-mac')
async function makeVideo(frames, out) {
  if (!frames.length) return null
  const ff = spawn(FFMPEG, ['-loglevel', 'error', '-f', 'image2pipe', '-c:v', 'mjpeg', '-i', 'pipe:0', '-y', '-an', '-r', '25',
    '-c:v', 'vp8', '-qmin', '0', '-qmax', '50', '-crf', '8', '-deadline', 'realtime', '-speed', '8', '-b:v', '1M',
    '-threads', '1', out], { stdio: ['pipe', 'ignore', 'inherit'] })
  let broken = false
  ff.stdin.on('error', () => { broken = true })
  const done = new Promise(r => ff.on('close', r))
  let i = 0
  for (let t = frames[0].at; t <= frames[frames.length - 1].at + 1500 && !broken; t += 40) {
    while (i + 1 < frames.length && frames[i + 1].at <= t) i++
    if (!ff.stdin.write(fs.readFileSync(frames[i].file))) await new Promise(r => ff.stdin.once('drain', r))
  }
  ff.stdin.end(); const code = await done
  return code === 0 && !broken ? { frames: frames.length, seconds: Math.round((frames[frames.length - 1].at - frames[0].at) / 1000) } : null
}
module.exports = { makeVideo }
if (require.main === module) {
  const [dir, out] = process.argv.slice(2)
  const frames = fs.readdirSync(dir).filter(f => f.endsWith('.jpg')).sort().map(f => ({ file: path.join(dir, f), at: fs.statSync(path.join(dir, f)).mtimeMs }))
  makeVideo(frames, out).then(r => console.log('video', JSON.stringify(r)))
}
