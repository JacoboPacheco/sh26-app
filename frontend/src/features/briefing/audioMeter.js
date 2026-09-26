// The captions' level meter reads the ElevenLabs audio element itself: element → AnalyserNode → speakers.
// Routing an element through WebAudio is one-way (its sound then only reaches the speakers through the graph),
// so an element is tapped only when that cannot silence it:
//   - it loaded with CORS (a cross-origin element without it would feed the graph silence),
//   - the page's AudioContext is running (a suspended one, under autoplay rules, would be silent too),
//   - not on iOS without navigator.audioSession (the ringer switch mutes WebAudio but not <audio>),
//   - not in WebKit (Safari, every iOS browser) when the audio comes from another origin, CORS or not: some WebKit
//     versions feed a media element source silence for cross-origin media (untested here), and the deployed page (Vercel) and its audio
//     (Render) are two origins; the voice must never go quiet for a meter.
// Otherwise tap() returns null, the element plays exactly as before and the meter stays hidden: playback wins.

let ctx = null
const taps = new WeakMap() // element → {analyser, buf} | null (tried, not tapped)
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function isIOS() {
  const ua = navigator.userAgent || ''
  return /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
}

// Safari and every iOS browser (all WebKit); Chrome, Edge, Opera and Firefox say so in their user agent
function isWebKit() {
  const ua = navigator.userAgent || ''
  return isIOS() || (/Safari\//.test(ua) && !/Chrome\/|Chromium\/|Edg\/|OPR\/|Firefox\/|Android/.test(ua))
}

function crossOrigin(el) {
  try {
    return new URL(el.currentSrc || el.src, window.location.href).origin !== window.location.origin
  } catch {
    return true
  }
}

async function running() {
  const AC = window.AudioContext || window.webkitAudioContext
  if (!AC) return null
  if (isIOS()) {
    // Safari 17+: play WebAudio like media (not muted by the ringer switch); older iOS: no meter
    if (!('audioSession' in navigator)) return null
    try {
      navigator.audioSession.type = 'playback'
    } catch {
      return null
    }
  }
  try {
    if (!ctx || ctx.state === 'closed') {
      ctx = new AC()
      // a context the browser suspends while an element plays through it: wake it (else that element goes quiet)
      ctx.onstatechange = () => ctx.state === 'suspended' && ctx.resume().catch(() => {})
    }
    if (ctx.state !== 'running') await Promise.race([ctx.resume().catch(() => {}), sleep(400)])
  } catch {
    return null
  }
  return ctx.state === 'running' ? ctx : null
}

// {analyser, buf} for a playing element, or null when it must not be routed (see above). One tap per element;
// the node is released when the element pauses or ends (the narration never reuses a stopped element).
export async function tap(el, cors) {
  if (!el || !cors) return null
  if (taps.has(el)) return taps.get(el)
  if (isWebKit() && crossOrigin(el)) {
    taps.set(el, null)
    return null
  }
  taps.set(el, null)
  const c = await running()
  if (!c || el.paused || el.ended || el.error) return null
  try {
    const src = c.createMediaElementSource(el)
    const analyser = c.createAnalyser()
    analyser.fftSize = 1024
    analyser.smoothingTimeConstant = 0.3
    src.connect(analyser)
    analyser.connect(c.destination)
    const t = { analyser, buf: new Float32Array(analyser.fftSize) }
    taps.set(el, t)
    el.addEventListener(
      'pause',
      () => {
        // a paused element is finished with (the next segment is a new element): drop its nodes
        src.disconnect()
        analyser.disconnect()
        taps.set(el, null)
      },
      { once: true },
    )
    return t
  } catch {
    return null // createMediaElementSource refused (already routed elsewhere, or unsupported): no meter
  }
}

// the level right now, 0..1: the waveform's RMS on a square-root curve, so quiet syllables still show
// (speech sits around 0.03-0.3 RMS; silence stays at the 1 px floor)
export function level(t) {
  if (!t) return 0
  t.analyser.getFloatTimeDomainData(t.buf)
  let sum = 0
  for (let i = 0; i < t.buf.length; i++) sum += t.buf[i] * t.buf[i]
  const rms = Math.sqrt(sum / t.buf.length)
  return rms < 0.004 ? 0 : Math.min(1, Math.sqrt(rms) * 1.7)
}
