import { assetUrl } from '../../api'
import { canSpeak, readMuted, wordsOf } from '../briefing/useNarration'
import { getSegment, getVoiceStatus } from '../briefing/voiceApi'

// The show's playback engine, outside React: one scene clock that only moves while playing, and the scene's
// lines spoken one after another on it. The player's animation loop calls tick(dt) every frame; the map and the
// timed overlays read `t` (ms into the scene) from onFrame, React re-renders only on discrete changes (scene,
// play state, a new word in the captions) through subscribe().
//
// A scene lasts max(min_ms, its lines). Who says the words, per line:
//   elevenlabs  sound on, the voice service configured, the line has a voice key: the audio's currentTime and
//               ElevenLabs' word timestamps move the captions
//   browser     sound on, no ElevenLabs for this line: SpeechSynthesis, sentence by sentence (word boundaries)
//   timer       sound off (the default), or no voice on this device: captions at characters / 15 per second

const LEAD_MS = 650 // after a scene starts, before its first line
const GAP_MS = 320 // between lines
const TAIL_MS = 900 // after the last line, before the next scene
const CPS = 15
const MUTE_KEY = 'overload.sound.muted'

export const lineMs = (text) => Math.max(1600, ((text || '').length / CPS) * 1000)

// The scene's expected length with captions only (the pace the overlays plan their ticks on).
export function estSceneMs(scene) {
  const lines = scene?.lines || []
  const speech = lines.reduce((n, l) => n + lineMs(l.text), 0) + (lines.length ? LEAD_MS + GAP_MS * (lines.length - 1) + TAIL_MS : 0)
  return Math.max(scene?.min_ms || 0, speech, 2500)
}

export const voiceKey = (line) => {
  const k = line?.voice?.key || line?.voice?.id || line?.voice_key
  return typeof k === 'string' && /^[0-9a-f]{32}$/.test(k) ? k : null
}

function sentencesOf(text) {
  const out = []
  const re = /[^.!?]+(?:[.!?]+["')\]]*)?\s*/g
  let m
  while ((m = re.exec(text))) if (m[0].trim()) out.push({ text: m[0].trim(), start: m.index + (m[0].length - m[0].trimStart().length) })
  return out.length ? out : [{ text, start: 0 }]
}

function pickVoice(lang) {
  const voices = canSpeak ? window.speechSynthesis.getVoices() : []
  const norm = (v) => String(v.lang || '').replace('_', '-')
  const prefs = lang === 'es' ? ['es-US', 'es-MX', 'es-419', 'es-ES'] : ['en-US', 'en-GB']
  for (const p of prefs) {
    const v = voices.find((x) => norm(x) === p && x.localService) || voices.find((x) => norm(x) === p)
    if (v) return v
  }
  return voices.find((x) => norm(x).startsWith(lang)) || null
}

export default class ShowEngine {
  constructor(show) {
    this.show = show
    this.scenes = show?.scenes || []
    this.lang = show?.lang === 'es' ? 'es' : 'en'
    this.idx = 0
    this.t = 0
    this.playing = false
    this.ended = false
    this.muted = readMuted()
    this.version = 0
    this.run = 0 // bumps on every scene entry (the overlays restart on it)
    this.caption = null // {role, text, char, via, name}
    this.audio = null // {el, meter} while an ElevenLabs line plays
    this.via = null // what spoke the last line: 'elevenlabs' | 'browser' | 'timer'
    this.voiceStatus = null
    this.elevenOff = false // ElevenLabs failed before a line started: the rest of the show uses the browser voice
    this.browserOff = !canSpeak
    this.listeners = new Set()
    this.frames = new Set()
    this.token = 0
    this.line = null
    this.closed = false
    getVoiceStatus().then((s) => {
      this.voiceStatus = s
      this.emit()
    })
    this.enter(0)
  }

  // ------------------------------------------------------------------ subscriptions
  subscribe = (fn) => {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }
  getVersion = () => this.version
  emit() {
    this.version++
    this.listeners.forEach((fn) => fn())
  }
  onFrame(fn) {
    this.frames.add(fn)
    fn(this.t, this.run)
    return () => this.frames.delete(fn)
  }

  get scene() {
    return this.scenes[this.idx] || null
  }
  get estMs() {
    return estSceneMs(this.scene)
  }
  // 0..1 through the current scene (the progress bar); an estimate while lines are still to come
  get sceneProgress() {
    const s = this.scene
    if (!s) return 0
    const done = this.line?.phase === 'done'
    const est = Math.max(this.estMs, this.t + (done ? 0 : 1))
    return Math.min(1, this.t / est)
  }

  // ------------------------------------------------------------------ transport
  enter(i) {
    this.stopMedia()
    this.idx = Math.max(0, Math.min(this.scenes.length - 1, i))
    this.t = 0
    this.run++
    this.ended = false
    this.caption = null
    this.line = { i: 0, phase: 'lead', since: 0, w: -2 }
    this.prefetch()
    this.emit()
  }
  play() {
    if (this.closed) return
    if (this.ended) {
      this.enter(0)
    }
    this.playing = true
    const L = this.line
    if (L?.phase === 'speak' && L.via === 'browser') this.startLine() // the browser voice starts the line over
    else if (L?.phase === 'ready') this.beginAudio(L) // loaded, or paused mid-line: a fresh element from where it was
    this.emit()
  }
  pause() {
    this.playing = false
    const L = this.line
    // an ElevenLabs line: drop the element (the level meter's WebAudio tap lets go of a paused element, so it can't be
    // resumed) and remember where it was; play() continues from there with a new one
    if (L?.el) {
      L.resumeAt = L.el.currentTime || 0
      this.dropEl(L)
      L.phase = 'ready'
      this.audio = null
    }
    if (L?.via === 'browser' && canSpeak) {
      this.token++ // the browser voice is cancelled; play() starts the line again
      window.speechSynthesis.cancel()
    }
    this.emit()
  }
  toggle = () => (this.playing ? this.pause() : this.play())
  goto = (i) => {
    if (!this.scenes.length) return
    this.enter(i)
  }
  next = () => this.idx < this.scenes.length - 1 && this.goto(this.idx + 1)
  prev = () => this.goto(this.t > 2500 ? this.idx : this.idx - 1)

  setMuted = (on) => {
    this.muted = !!on
    try {
      localStorage.setItem(MUTE_KEY, on ? '1' : '0')
    } catch {
      // private window or blocked storage: the choice just isn't remembered
    }
    if (!on) this.elevenOff = false
    // the line being said starts again in the new voice (or as captions)
    const L = this.line
    if (L && (L.phase === 'speak' || L.phase === 'load' || L.phase === 'ready')) {
      this.stopMedia()
      this.startLine()
    }
    this.prefetch()
    this.emit()
  }

  close() {
    this.closed = true
    this.playing = false
    this.stopMedia()
  }

  stopMedia() {
    this.token++
    if (this.line?.el) this.dropEl(this.line)
    if (canSpeak) window.speechSynthesis.cancel()
    if (this.audio) this.audio = null
  }

  dropEl(L) {
    const a = L.el
    L.el = null
    if (!a) return
    a.onended = a.onerror = null
    a.pause()
    a.removeAttribute('src')
  }

  // ------------------------------------------------------------------ the clock
  tick(dt) {
    if (this.playing && this.scene) {
      this.t += Math.min(Math.max(dt, 0), 100)
      this.stepLine()
      const s = this.scene
      const L = this.line
      // a long scene (a cascade that plays at its own pace) outlasts its words: the last caption clears after a beat,
      // so the map carries the rest instead of one sentence frozen on screen
      if (L.phase === 'done' && this.caption && this.t - L.since > 1800) {
        this.caption = null
        this.emit()
      }
      if (L.phase === 'done' && this.t >= (s.min_ms || 0) && this.t - L.since >= TAIL_MS) {
        if (this.idx < this.scenes.length - 1) this.enter(this.idx + 1)
        else {
          this.playing = false
          this.ended = true
          this.emit()
        }
      }
    }
    this.frames.forEach((fn) => fn(this.t, this.run))
  }

  modeFor(line) {
    if (this.muted) return 'timer'
    if (!this.elevenOff && this.voiceStatus?.configured && voiceKey(line)) return 'elevenlabs'
    if (!this.browserOff) return 'browser'
    return 'timer'
  }

  stepLine() {
    const L = this.line
    if (!L) return
    const wait = L.phase === 'lead' ? LEAD_MS : L.phase === 'gap' ? GAP_MS : -1
    if (wait >= 0 && this.t - L.since >= wait) return this.startLine()
    if (L.phase !== 'speak') return
    if (L.via === 'timer') {
      const f = (this.t - L.since) / L.dur
      this.setChar(f * L.text.length)
      if (f >= 1) this.finishLine(L.token)
    } else if (L.via === 'elevenlabs' && L.el) {
      const ct = L.el.currentTime
      const al = L.align
      let i = -1
      for (let k = 0; k < al.length && al[k][1] <= ct; k++) i = k
      if (al.length) this.setChar(i < 0 ? -1 : (L.words[Math.min(i, L.words.length - 1)]?.start ?? 0))
      else this.setChar((ct / Math.max(0.5, L.el.duration || 1)) * L.text.length)
    } else if (L.via === 'browser') {
      if (!L.partT0) return
      const est = L.partStart + ((performance.now() - L.partT0) / 1000) * 14.5
      this.setChar(L.boundary >= 0 ? Math.max(L.boundary, L.partStart) : Math.min(L.partEnd, est))
    }
  }

  // move the captions to a character; emits only when a new word lands
  setChar(char) {
    const L = this.line
    let w = -1
    for (let k = 0; k < L.words.length && L.words[k].start <= char; k++) w = k
    if (w === L.w) return
    L.w = w
    this.caption = { ...this.caption, char: w < 0 ? -1 : L.words[w].start }
    this.emit()
  }

  startLine(force) {
    const prev = this.line
    this.stopMedia()
    const lines = this.scene?.lines || []
    const i = prev?.i ?? 0
    if (i >= lines.length) {
      this.line = { i, phase: 'done', since: this.t, w: -2 }
      return
    }
    const line = lines[i]
    const text = line.text || ''
    const role = line.speaker === 'analyst' ? 'analyst' : 'presenter'
    const mode = force || this.modeFor(line)
    const L = { i, phase: 'speak', since: this.t, text, words: wordsOf(text), w: -2, via: mode, role, token: this.token }
    this.line = L
    this.via = mode
    // who wrote the line and what the checker did with it (the captions show it: the agent's work, visible)
    const review = this.scene?.review || {}
    const check = { by: line.by === 'gemini' ? 'gemini' : 'template', facts: (line.facts || []).length, rewritten: !!review.rewritten || (review.self_fixes || 0) > 0 }
    this.caption = { role, text, char: -1, via: mode, name: null, key: `${this.run}:${i}`, check }
    if (mode === 'timer') L.dur = lineMs(text)
    else if (mode === 'elevenlabs') {
      L.phase = 'load'
      this.loadEleven(L, line)
    } else this.speakBrowser(L)
    this.emit()
  }

  finishLine(token) {
    const L = this.line
    if (!L || token !== L.token || L.phase === 'done') return
    this.setChar(Infinity)
    if (L.el) this.dropEl(L)
    this.audio = null
    const n = this.scene?.lines?.length || 0
    this.line = { i: L.i + 1, phase: L.i + 1 >= n ? 'done' : 'gap', since: this.t, w: -2 }
    this.emit()
  }

  // a voice failed on this line: the next voice down says it
  lineFailed(token, fromEleven) {
    const L = this.line
    if (!L || token !== L.token) return
    if (fromEleven) this.elevenOff = true
    else this.browserOff = true
    this.startLine(this.modeFor(this.scene?.lines?.[L.i]))
  }

  // ------------------------------------------------------------------ ElevenLabs
  loadEleven(L, line) {
    const token = L.token
    getSegment(voiceKey(line)).then(
      (data) => {
        if (token !== this.token || this.line !== L) return
        L.align = Array.isArray(data?.words) ? data.words : []
        L.url = assetUrl(data.audio_url)
        const name = typeof data?.speaker === 'string' && data.speaker ? data.speaker : null
        this.caption = { ...this.caption, name }
        L.phase = 'ready'
        if (this.playing) this.beginAudio(L)
        this.emit()
      },
      () => this.lineFailed(token, true),
    )
  }

  // play the loaded line: with CORS first (the level meter may read it), plain if the host refuses CORS
  beginAudio(L, cors = true) {
    const token = L.token
    const a = new Audio()
    if (cors) a.crossOrigin = 'anonymous'
    a.preload = 'auto'
    a.src = L.url
    if (L.resumeAt) a.currentTime = L.resumeAt // before the metadata: the default start position
    L.el = a
    let started = false
    a.onended = () => this.finishLine(token)
    a.onerror = () => {
      if (token !== this.token || L.el !== a) return
      if (cors && !started) return this.beginAudio(L, false)
      this.lineFailed(token, true)
    }
    a.play().then(
      () => {
        if (token !== this.token || L.el !== a) return a.pause()
        started = true
        if (L.phase === 'ready') {
          L.phase = 'speak'
          L.since = this.t
        }
        this.audio = { el: a, meter: cors }
        this.emit()
      },
      (err) => {
        if (token !== this.token || L.el !== a) return
        if (cors && !started && err?.name !== 'NotAllowedError') return this.beginAudio(L, false)
        this.lineFailed(token, true)
      },
    )
  }

  // ElevenLabs renders ahead: this scene's lines and the next scene's first (sound on only: the quota is small)
  prefetch() {
    if (this.muted || this.elevenOff || !this.voiceStatus?.configured) return
    const keys = [...(this.scene?.lines || []), this.scenes[this.idx + 1]?.lines?.[0]].map(voiceKey).filter(Boolean)
    let n = 0
    const lane = () => {
      if (n >= keys.length || this.closed) return
      getSegment(keys[n++])
        .catch(() => {})
        .finally(lane)
    }
    lane()
    lane()
  }

  // ------------------------------------------------------------------ browser voice
  speakBrowser(L) {
    const token = L.token
    const synth = window.speechSynthesis
    const voice = pickVoice(this.lang)
    const parts = sentencesOf(L.text)
    let k = 0
    let started = false
    const next = () => {
      if (token !== this.token) return
      if (k >= parts.length) return this.finishLine(token)
      const part = parts[k++]
      const u = new window.SpeechSynthesisUtterance(part.text)
      u.lang = this.lang === 'es' ? 'es-US' : 'en-US'
      if (voice) u.voice = voice
      u.onstart = () => {
        started = true
        L.partStart = part.start
        L.partEnd = part.start + part.text.length
        L.partT0 = performance.now()
        L.boundary = -1
      }
      u.onboundary = (e) => {
        if (!e.name || e.name === 'word') L.boundary = part.start + e.charIndex
      }
      u.onend = () => next()
      u.onerror = (e) => {
        if (e.error === 'interrupted' || e.error === 'canceled') return
        if (!started) return this.lineFailed(token, false) // refused (no voice, or not allowed yet): captions only
        next()
      }
      synth.speak(u)
    }
    next()
    // no voice ever started on this device: captions only from here on
    setTimeout(() => {
      if (!started && token === this.token && this.playing) this.lineFailed(token, false)
    }, 2500)
  }
}
