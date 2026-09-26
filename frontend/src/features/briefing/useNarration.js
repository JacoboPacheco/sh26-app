import { useCallback, useEffect, useRef, useState } from 'react'
import { assetUrl } from '../../api'
import { getSegment, getVoiceStatus } from './voiceApi'

// The narration engine: plays a deck slide by slide. Each slide's segments play in order with a short
// gap; when the last one ends it holds a beat and advances. While a segment plays, a clock (the audio's
// currentTime, the browser voice's word boundaries, or a timer) moves a character position through the
// text; the segment's cues fire as the position passes them (step=n, area=X, line=id, fix=show, wave=n)
// and the captions follow the same position.
//
// Providers, decided when playback starts:
//   elevenlabs — the voice service is configured and the first segment renders (else the whole deck
//                uses the browser voice); a segment that fails mid-play falls back alone.
//   browser    — SpeechSynthesis, one utterance per sentence (Chrome cuts long ones off).
//   timer      — muted, or no voice on this device: captions advance on a timer.

const GAP_MS = 350
const HOLD_MS = 800
const CPS = { en: 14.5, es: 15.5 } // speaking rate, characters per second (timer and estimates)

export const canSpeak =
  typeof window !== 'undefined' && 'speechSynthesis' in window && typeof window.SpeechSynthesisUtterance === 'function'

// Sound starts off (the user asked, Sat 03:18); the choice is remembered per viewer.
const MUTE_KEY = 'overload.sound.muted'
export function readMuted() {
  try {
    return localStorage.getItem(MUTE_KEY) !== '0'
  } catch {
    return true
  }
}

export function reducedMotion() {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

// words with their character offsets
export function wordsOf(text) {
  const out = []
  const re = /\S+/g
  let m
  while ((m = re.exec(text || ''))) out.push({ w: m[0], start: m.index, end: m.index + m[0].length })
  return out
}

function sentencesOf(text) {
  const out = []
  const re = /[^.!?]+(?:[.!?]+["')\]]*)?\s*/g
  let m
  while ((m = re.exec(text))) if (m[0].trim()) out.push({ text: m[0].trim(), start: m.index + (m[0].length - m[0].trimStart().length) })
  return out.length ? out : [{ text, start: 0 }]
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
// dev-only trace for browser checks: set window.__nl = [] and read it back
const dbg = (...a) => import.meta.env.DEV && window.__nl?.push([Math.round(performance.now()), ...a])

function voicesReady(timeout = 900) {
  if (!canSpeak) return Promise.resolve([])
  const synth = window.speechSynthesis
  const now = synth.getVoices()
  if (now.length) return Promise.resolve(now)
  return new Promise((resolve) => {
    const done = () => {
      synth.removeEventListener?.('voiceschanged', done)
      resolve(synth.getVoices())
    }
    synth.addEventListener?.('voiceschanged', done)
    setTimeout(done, timeout)
  })
}

function pickVoice(lang) {
  const voices = canSpeak ? window.speechSynthesis.getVoices() : []
  const norm = (v) => String(v.lang || '').replace('_', '-')
  const prefs = lang === 'es' ? ['es-US', 'es-MX', 'es-419', 'es-ES'] : ['en-US', 'en-GB']
  for (const p of prefs) {
    // local voices report word boundaries; the network ones often don't
    const v = voices.find((x) => norm(x) === p && x.localService) || voices.find((x) => norm(x) === p)
    if (v) return v
  }
  return voices.find((x) => norm(x).startsWith(lang)) || null
}

// characters per second for a slide in the timer: its estimated length when the deck gives one
function cpsFor(slide, lang) {
  const segs = slide?.narration?.[lang] || []
  const chars = segs.reduce((n, s) => n + (s.chars || s.text?.length || 0), 0)
  const est = slide?.est_s?.[lang]
  if (chars && est && est > 1) return Math.min(22, Math.max(10, chars / Math.max(1, est - (segs.length * GAP_MS) / 1000)))
  return CPS[lang] || CPS.en
}

// `holdFor(slide, elapsedMs)` (optional): how much longer to keep a slide up after its narration ends (the show's
// beats run longer than their words); 0 to move on. `run` counts the slides entered while playing (0 = not playing).
export default function useNarration({ deck, lang, onCue, onEnter, holdFor }) {
  const slides = deck?.slides || []
  const [idx, setIdx] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [provider, setProvider] = useState(null) // what is speaking: 'elevenlabs' | 'browser' | 'timer' (null = not started)
  const [segFellBack, setSegFellBack] = useState(false) // an ElevenLabs segment fell back to the browser voice
  // {role, text, char, key, via: 'elevenlabs' | 'browser' | 'timer', speaker: the ElevenLabs voice's first name | null}
  const [caption, setCaption] = useState(null)
  // the ElevenLabs element playing right now ({el, meter}: meter = it loaded with CORS, so WebAudio may read it)
  const [audio, setAudio] = useState(null)
  const [progress, setProgress] = useState(0) // 0..1 through the current slide's narration
  const [muted, setMutedState] = useState(readMuted) // starts muted until this viewer turns sound on (remembered)
  const [voice, setVoice] = useState(null) // /api/voice/status
  const [slideRun, setSlideRun] = useState(0) // bumps when the loop enters a slide; 0 while not playing

  const run = useRef(0) // the playback in progress; bumping it stops everything
  const cancelRef = useRef(null)
  const providerRef = useRef(null)
  const mutedRef = useRef(muted)
  const segRef = useRef(0) // the segment being spoken (resume point)
  const idxRef = useRef(0)
  const playingRef = useRef(false)
  const deckRef = useRef(deck)
  const langRef = useRef(lang)
  const cueRef = useRef(onCue)
  const enterRef = useRef(onEnter)
  const holdRef = useRef(holdFor)
  const slideStart = useRef(0)
  useEffect(() => {
    deckRef.current = deck
    langRef.current = lang
    cueRef.current = onCue
    enterRef.current = onEnter
    holdRef.current = holdFor
  })

  useEffect(() => {
    let live = true
    let timer = 0
    // the server names the voices in the background: an answer without the names yet is asked again once
    const load = (again) =>
      getVoiceStatus().then((s) => {
        if (!live) return
        setVoice(s)
        if (again && s?.configured && !(s.speakers?.presenter && s.speakers?.analyst)) timer = setTimeout(() => load(false), 4500)
      })
    load(true)
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [])

  const setPlay = (on) => {
    playingRef.current = on
    setPlaying(on)
  }
  const setIndex = (i) => {
    idxRef.current = i
    setIdx(i)
  }
  const chooseProvider = (p) => {
    providerRef.current = p
    setProvider(p)
  }

  const stopMedia = useCallback(() => {
    const c = cancelRef.current
    cancelRef.current = null
    c?.()
    if (canSpeak) window.speechSynthesis.cancel()
  }, [])

  // ------------------------------------------------------------------ one segment
  const speak = useCallback(
    async (seg, slide, token, onFrac) => {
      const text = seg.text || ''
      const cues = seg.cues || []
      const fired = new Set()
      const fireUpTo = (char) =>
        cues.forEach((c, j) => {
          if (fired.has(j) || c.char > char) return
          fired.add(j)
          if (c.name !== 'slide_end') cueRef.current?.(c.name, c.value)
        })
      const words = wordsOf(text)
      let lastWord = -2
      let mode = providerRef.current
      let via = mode || 'timer' // who is saying these words (the captions name the speaker only for ElevenLabs)
      const who = { speaker: null } // the segment's own voice name, from its audio (speakEleven fills it)
      const cap = (char) => ({ role: seg.role, text, char, key: seg.key, via, speaker: via === 'elevenlabs' ? who.speaker : null })
      const show = (char) => {
        let w = -1
        for (let k = 0; k < words.length && words[k].start <= char; k++) w = k
        if (w === lastWord) return
        lastWord = w
        setCaption(cap(w < 0 ? -1 : words[w].start))
        onFrac(text.length ? Math.min(1, char / text.length) : 1)
      }
      const restart = (v) => {
        via = v
        lastWord = -2
        setCaption(cap(-1))
      }
      restart(via)

      if (mode === 'elevenlabs') {
        try {
          await speakEleven(seg, token, words, show, fireUpTo, who)
          if (token === run.current) fireUpTo(Infinity)
          return
        } catch {
          if (token !== run.current) return
          mode = 'browser'
          setSegFellBack(true)
          restart('browser')
        }
      }
      if (mode === 'browser') {
        const r = await speakBrowser(seg, token, show, fireUpTo)
        if (r !== 'novoice') {
          if (token === run.current) fireUpTo(Infinity)
          return
        }
        chooseProvider('timer') // no voice on this device: captions only, for the rest of the deck
        restart('timer')
      }
      await speakTimer(seg, token, show, fireUpTo, cpsFor(slide, langRef.current))
      if (token === run.current) fireUpTo(Infinity)
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  // One ElevenLabs segment. The element loads with CORS (crossOrigin = 'anonymous') so the captions' level meter
  // may read it through WebAudio; if the audio host refuses CORS, the same audio plays again without it and the
  // meter stays hidden: playback always wins over the meter.
  function speakEleven(seg, token, words, show, fireUpTo, who) {
    return new Promise((resolve, reject) => {
      getSegment(seg.key).then((data) => {
        if (token !== run.current) return resolve()
        who.speaker = typeof data.speaker === 'string' && data.speaker ? data.speaker : null
        const al = Array.isArray(data.words) ? data.words : []
        let audio = null
        let raf = 0
        let done = false
        const end = (err) => {
          if (done) return
          done = true
          cancelAnimationFrame(raf)
          const el = audio
          setAudio((a) => (a?.el === el ? null : a))
          if (err) reject(err)
          else resolve()
        }
        const tick = () => {
          if (done) return
          if (token !== run.current) return end()
          const t = audio.currentTime
          let i = -1
          for (let k = 0; k < al.length && al[k][1] <= t; k++) i = k
          // the alignment's i-th word is the text's i-th word (the spoken text equals the caption text)
          const char = i < 0 ? 0 : (words[Math.min(i, words.length - 1)]?.start ?? 0)
          show(char)
          fireUpTo(char)
          raf = requestAnimationFrame(tick)
        }
        const start = (cors) => {
          const a = new Audio()
          if (cors) a.crossOrigin = 'anonymous'
          a.preload = 'auto'
          a.src = assetUrl(data.audio_url)
          audio = a
          let started = false
          let failed = false
          const fail = (err) => {
            if (failed || done || audio !== a) return
            failed = true
            a.onended = a.onerror = null
            // a load failure with CORS on (the audio host sent no CORS headers): play it plain, no meter
            if (cors && !started && err?.name !== 'NotAllowedError') {
              a.removeAttribute('src')
              dbg('eleven retry without cors', seg.key)
              return start(false)
            }
            end(err || new Error('audio failed'))
          }
          a.onended = () => audio === a && end()
          a.onerror = () => fail(a.error)
          cancelRef.current = () => {
            a.pause()
            end()
          }
          a.play()
            .then(() => {
              if (done || audio !== a) return a.pause()
              started = true
              setAudio({ el: a, meter: cors })
              raf = requestAnimationFrame(tick)
            })
            .catch(fail)
        }
        start(true)
      }, reject)
    })
  }

  async function speakBrowser(seg, token, show, fireUpTo) {
    if (!canSpeak) return 'novoice'
    const synth = window.speechSynthesis
    const lang = langRef.current
    const voiceObj = pickVoice(lang)
    const cps = CPS[lang] || CPS.en
    for (const part of sentencesOf(seg.text || '')) {
      if (token !== run.current) return 'stopped'
      const r = await new Promise((resolve) => {
        const u = new window.SpeechSynthesisUtterance(part.text)
        u.lang = lang === 'es' ? 'es-US' : 'en-US'
        if (voiceObj) u.voice = voiceObj
        let startedAt = 0
        let boundary = -1
        let raf = 0
        let done = false
        const timers = []
        const end = (why) => {
          if (done) return
          done = true
          cancelAnimationFrame(raf)
          timers.forEach(clearTimeout)
          resolve(why)
        }
        const tick = () => {
          if (done) return
          if (token !== run.current) return end('stopped')
          // word boundaries when the voice reports them, else an estimate from the speaking rate
          const est = startedAt ? ((performance.now() - startedAt) / 1000) * cps : 0
          const local = boundary >= 0 ? boundary : Math.min(part.text.length - 1, est)
          show(part.start + local)
          fireUpTo(part.start + local)
          raf = requestAnimationFrame(tick)
        }
        u.onstart = () => {
          startedAt = performance.now()
          raf = requestAnimationFrame(tick)
        }
        u.onboundary = (e) => {
          if (!e.name || e.name === 'word') boundary = e.charIndex
        }
        u.onend = () => end('ok')
        u.onerror = (e) => end(e.error === 'interrupted' || e.error === 'canceled' ? 'stopped' : 'ok')
        // never started: no usable voice here
        timers.push(
          setTimeout(() => {
            if (startedAt || done) return
            synth.cancel()
            end('novoice')
          }, 2500),
        )
        // Chrome sometimes never fires onend
        timers.push(setTimeout(() => end('ok'), (part.text.length / cps) * 2500 + 4000))
        cancelRef.current = () => {
          synth.cancel()
          end('stopped')
        }
        synth.speak(u)
      })
      if (r === 'novoice' || r === 'stopped') return r
    }
    return 'ok'
  }

  function speakTimer(seg, token, show, fireUpTo, cps) {
    return new Promise((resolve) => {
      const len = (seg.text || '').length
      const dur = Math.max(1200, (len / cps) * 1000)
      const t0 = performance.now()
      let raf = 0
      const tick = (now) => {
        if (token !== run.current) return resolve()
        const char = Math.min(len, ((now - t0) / dur) * len)
        show(char)
        fireUpTo(char)
        if (now - t0 >= dur) return resolve()
        raf = requestAnimationFrame(tick)
      }
      cancelRef.current = () => {
        cancelAnimationFrame(raf)
        resolve()
      }
      raf = requestAnimationFrame(tick)
    })
  }

  // ------------------------------------------------------------------ provider
  const decide = useCallback(async (slide) => {
    if (mutedRef.current) return chooseProvider('timer')
    if (providerRef.current) return providerRef.current
    const st = await getVoiceStatus()
    setVoice(st) // the names may have arrived since the stage opened (the server looks them up in the background)
    const first = slide?.narration?.[langRef.current]?.[0]
    if (st?.configured && first) {
      try {
        await getSegment(first.key)
        return chooseProvider('elevenlabs')
      } catch {
        // any failure before playback starts: the whole deck uses the browser voice
      }
    }
    if (canSpeak && (await voicesReady()).length) return chooseProvider('browser')
    return chooseProvider('timer')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ElevenLabs only: render this slide's and the next slide's segments ahead, two at a time
  const prefetch = useCallback((i) => {
    if (providerRef.current !== 'elevenlabs') return
    const d = deckRef.current
    const l = langRef.current
    const keys = [d?.slides?.[i], d?.slides?.[i + 1]].flatMap((s) => (s?.narration?.[l] || []).map((g) => g.key))
    let next = 0
    const lane = () => {
      if (next >= keys.length) return
      const k = keys[next++]
      getSegment(k)
        .catch(() => {})
        .finally(lane)
    }
    lane()
    lane()
  }, [])

  // ------------------------------------------------------------------ the loop
  const playFrom = useCallback(
    async (start, segStart = 0) => {
      const token = ++run.current
      stopMedia()
      setPlay(true)
      let i = start
      let s = segStart
      const first = deckRef.current?.slides?.[i]
      await decide(first)
      dbg('play', token, start, segStart, providerRef.current)
      if (token !== run.current) return
      while (token === run.current) {
        const slide = deckRef.current?.slides?.[i]
        if (!slide) break
        const segs = slide.narration?.[langRef.current] || []
        enterRef.current?.(i, true)
        slideStart.current = performance.now()
        setSlideRun((r) => r + 1)
        prefetch(i)
        if (s === 0) setProgress(0)
        // the bar follows the words, or the show's own clock when the beat runs longer than they do
        const frac = { text: 0, time: 0 }
        const bump = () => setProgress(Math.max(frac.text, frac.time))
        const dwell = holdRef.current?.(slide, 0) || 0
        const iv = dwell ? setInterval(() => ((frac.time = Math.min(1, (performance.now() - slideStart.current) / dwell)), bump()), 200) : 0
        try {
          for (; s < segs.length; s++) {
            segRef.current = s
            const k = s
            dbg('seg', i, s, segs[s].text.length)
            await speak(segs[s], slide, token, (f) => ((frac.text = (k + f) / segs.length), bump()))
            dbg('seg end', i, s, token === run.current)
            if (token !== run.current) return
            if (s < segs.length - 1) await sleep(GAP_MS)
            if (token !== run.current) return
          }
          frac.text = 1
          bump()
          await sleep(segs.length ? HOLD_MS : 2500)
          // the beat's own length: keep the slide up until its show has played (the words are done: the show's own
          // play-by-play line takes the captions)
          let more = holdRef.current?.(slide, performance.now() - slideStart.current) || 0
          if (more > 0 && token === run.current) setCaption(null)
          for (; more > 0 && token === run.current; ) {
            await sleep(Math.min(more, 250))
            more = holdRef.current?.(slide, performance.now() - slideStart.current) || 0
          }
        } finally {
          if (iv) clearInterval(iv)
        }
        if (token !== run.current) return
        setProgress(1)
        if (i >= (deckRef.current?.slides?.length || 0) - 1) break
        i += 1
        s = 0
        segRef.current = 0
        setIndex(i)
        setCaption(null)
      }
      dbg('loop end', token === run.current, i)
      if (token === run.current) {
        setPlay(false)
        setSlideRun(0)
        setCaption(null)
      }
    },
    [decide, prefetch, speak, stopMedia],
  )

  const pause = useCallback(() => {
    run.current++
    stopMedia()
    setPlay(false)
    setSlideRun(0)
  }, [stopMedia])

  const play = useCallback(() => playFrom(idxRef.current, segRef.current), [playFrom])
  const toggle = useCallback(() => (playingRef.current ? pause() : play()), [pause, play])

  const goto = useCallback(
    (i) => {
      const n = deckRef.current?.slides?.length || 0
      const to = Math.max(0, Math.min(n - 1, i))
      run.current++
      stopMedia()
      segRef.current = 0
      setIndex(to)
      setProgress(0)
      setCaption(null)
      setSlideRun(0) // the new slide shows finished until the loop enters it and the show plays
      if (playingRef.current) playFrom(to, 0)
    },
    [playFrom, stopMedia],
  )
  const next = useCallback(() => goto(idxRef.current + 1), [goto])
  const prev = useCallback(() => goto(idxRef.current - 1), [goto])

  // a new language restarts the same slide in it
  const firstLang = useRef(lang)
  useEffect(() => {
    if (firstLang.current === lang) return
    firstLang.current = lang
    run.current++
    stopMedia()
    segRef.current = 0
    setProgress(0)
    setCaption(null)
    if (playingRef.current) playFrom(idxRef.current, 0)
  }, [lang, playFrom, stopMedia])

  const setMuted = useCallback(
    (on) => {
      mutedRef.current = !!on
      setMutedState(!!on)
      try {
        localStorage.setItem(MUTE_KEY, on ? '1' : '0')
      } catch {
        // private window / blocked storage: the choice just isn't remembered
      }
      providerRef.current = null // decided again on the next start
      if (playingRef.current) playFrom(idxRef.current, segRef.current)
      else setProvider(null)
    },
    [playFrom],
  )

  // a shorter or longer deck (Gemini's replaces the template): keep the index in range
  useEffect(() => {
    if (slides.length && idxRef.current > slides.length - 1) setIndex(slides.length - 1)
  }, [slides.length])

  // stop speaking when the stage closes
  useEffect(
    () => () => {
      run.current++
      stopMedia()
    },
    [stopMedia],
  )

  return {
    idx,
    playing,
    playingRef,
    run: slideRun,
    provider,
    segFellBack,
    voice, // /api/voice/status (null while loading)
    audio, // {el, meter} while an ElevenLabs segment plays, else null
    caption,
    progress,
    muted,
    setMuted,
    play,
    pause,
    toggle,
    goto,
    next,
    prev,
    playFrom,
  }
}
