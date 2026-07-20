#!/usr/bin/env node
// say.mjs — Alfred's voice: text → Voicebox (designed "Alfred" profile) → mp3.
// Used by the bridge session to send voice replies to Telegram (the PC has no
// speakers — delivery is always a file). Voice Alfred M1 (2026-07-18).
//
//   node tools/say.mjs "text to speak" [out.mp3] [--lang he|en] [--tone "..."]
// Prints the mp3 path on success. Requires the Voicebox app (launches it if down).
import { writeFileSync } from 'node:fs'
import { execFile } from 'node:child_process'

const VB = process.env.VOICEBOX_URL || 'http://127.0.0.1:17493'
const PROFILE = process.env.ALFRED_VOICE_PROFILE || '0965ceab-deeb-4b51-a991-651587fd5006'
const VB_EXE = process.env.VOICEBOX_EXE || 'C:\\Users\\Yishay\\AppData\\Local\\Voicebox\\Voicebox.exe'

const args = process.argv.slice(2)
const text = args[0]
if (!text) { console.error('usage: node say.mjs "text" [out.mp3] [--lang he|en] [--tone "..."]'); process.exit(1) }
const out = args[1] && !args[1].startsWith('--') ? args[1] : `D:/Projects/telegram-claude-bridge/state/tmp/alfred-say-${Math.random().toString(36).slice(2, 8)}.mp3`
const flag = (n, d) => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : d }
const lang = flag('--lang', /[֐-׿]/.test(text) ? 'he' : 'en')
const tone = flag('--tone', '')

const run = (cmd, a) => new Promise((res, rej) => execFile(cmd, a, { maxBuffer: 1 << 24 }, (e, so, se) => e ? rej(new Error(se || e.message)) : res(so)))
const up = () => fetch(`${VB}/health`, { signal: AbortSignal.timeout(4000) }).then((r) => r.ok).catch(() => false)

if (!(await up())) {
  console.error('voicebox down — launching...')
  await run('cmd', ['/c', 'start', '', '/min', VB_EXE]).catch(() => {})
  const t0 = Date.now()
  while (!(await up())) { if (Date.now() - t0 > 120000) { console.error('voicebox did not come up'); process.exit(1) } await new Promise((x) => setTimeout(x, 5000)) }
}

const body = { text: text.slice(0, 4000), profile_id: PROFILE, engine: 'qwen_custom_voice', language: lang,
  model_size: process.env.VOICEBOX_MODEL_SIZE || '1.7B',
  instruct: 'Composed, warm, articulate assistant. Courteous, quietly confident, a hint of dry wit.' + (tone ? ` ${tone}.` : ''), normalize: true }
const sub = await (await fetch(`${VB}/generate`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(30000) })).json()
if (!sub.id) { console.error('submit failed: ' + JSON.stringify(sub).slice(0, 150)); process.exit(1) }

const t0 = Date.now()
for (;;) {
  const h = await fetch(`${VB}/history/${sub.id}`, { signal: AbortSignal.timeout(15000) }).then((r) => r.json()).catch(() => null)
  const st = String(h?.status || '').toLowerCase()
  if (st === 'failed' || st === 'cancelled') { console.error(`generation ${st}: ${h?.error || ''}`); process.exit(1) }
  if (st === 'completed') break
  if (Date.now() - t0 > 10 * 60000) { console.error('timeout'); process.exit(1) }
  await new Promise((x) => setTimeout(x, 4000))
}
const a = await fetch(`${VB}/audio/${sub.id}`)
const wavTmp = out.replace(/\.mp3$/, '.wav')
writeFileSync(wavTmp, Buffer.from(await a.arrayBuffer()))
await run('ffmpeg', ['-y', '-v', 'error', '-i', wavTmp, '-codec:a', 'libmp3lame', '-b:a', '96k', out])
console.log(out)
