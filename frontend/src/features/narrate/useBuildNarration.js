import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import useNarration, { reducedMotion } from '../briefing/useNarration'
import { getNarration } from './narrateApi'

// The narrated play-by-play on Strengthen the grid: a presenter voice says what the map draws, package by package
// (backend/narrate.py groups the plan's upgrades into at most four packages; about a minute whatever the budget).
//
//   const nb = useBuildNarration({ region, mw, loadFactor, mode, budget, lang, playing, onStep, onDone })
//
// LAZY: nothing is fetched until `playing` turns true; then the script for these props (backend/narrate.py) is
// fetched once and played with the deck's narration engine (briefing/useNarration): the ElevenLabs voice when the
// viewer's sound is on and the voice service answers, else the browser's voice, else (muted, or no voice here)
// captions on a timer. Sound stays the viewer's choice (localStorage 'overload.sound.muted', shared with Present
// the damage): nothing is heard until they turn it on.
//
// onStep(n, slide?): how many campuses the map should show. Called when a slide enters (its step_from), as the words
//   reach each campus (the script's 'step' cues) and when a slide's words end (its step): the build-up follows the
//   voice instead of a fixed timer.
// onDone(reason): 'end' when the narration has played to its last word (the map is on the budget's answer), or
//   'error' when the script could not be fetched (e.g. 409, no finished study): the page can fall back to its own
//   timer build-up. Pausing (`playing` false) stops the voice at once; playing again resumes where it paused, or
//   starts over after an 'end'. New props (size, mode, budget, state, load level) while playing restart with the new
//   script; a new language keeps the slide and switches the voice.
// fromStart: read when `playing` turns true: start over from the intro even though a run is paused mid-way (the page
//   passes it when its button offers "Watch it get built" rather than "Keep building", e.g. after the view changed).
// stale: the script held is for other props (the page stopped it on a change): the caption hides, and the next play
//   fetches the new script and starts it from the intro. Unmounting drops a fetch still on its way (no late onDone).

// The request body for a script (the server's cache and getNarration's key): the same order everywhere, so the
// play-by-play's reduced-motion path (which steps through the beats without the engine) shares the hook's script.
export function narrationBody({ region, mw, loadFactor = 1, mode = 'firm', budget = 0, ai = true, lang = 'en' }) {
  return {
    region,
    mw: Number(mw) || 0,
    load_factor: Number(loadFactor) || 1,
    mode: mode === 'flexible' ? 'flexible' : 'firm',
    budget: Math.max(0, Number(budget) || 0),
    ai: !!ai,
    lang: lang === 'es' ? 'es' : 'en',
  }
}

export default function useBuildNarration({ region, mw, loadFactor = 1, mode = 'firm', budget = 0, lang = 'en', playing = false, fromStart = false, ai = true, onStep, onDone }) {
  const [script, setScript] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [ended, setEnded] = useState(false)
  const [heldFor, setHeldFor] = useState(null) // `${params}|${lang}` of the script held (state: the caption reads it)
  const [reduced] = useState(reducedMotion)

  const body = useMemo(() => {
    const b = narrationBody({ region, mw, loadFactor, mode, budget, ai })
    delete b.lang // the language joins the key at fetch time (a switch keeps the slide)
    return b
  }, [region, mw, loadFactor, mode, budget, ai])
  const pkey = JSON.stringify(body)

  const stepRef = useRef(onStep)
  const doneRef = useRef(onDone)
  const scriptRef = useRef(null)
  const fromStartRef = useRef(fromStart)
  useEffect(() => {
    stepRef.current = onStep
    doneRef.current = onDone
    scriptRef.current = script
    fromStartRef.current = fromStart
  })

  // A resumed slide replays its words from the start (the engine resumes by segment): the map keeps what it shows
  // (no step back to the slide's start), and the cues it passes again never lower it.
  const lastStep = useRef(null)
  const resumeIdx = useRef(null) // the slide a resume re-enters
  const floor = useRef(null) // while a resumed slide replays: the step the map already shows
  const emit = useCallback((n, s) => {
    lastStep.current = n
    stepRef.current?.(n, s)
  }, [])
  const onEnter = useCallback(
    (i) => {
      const s = scriptRef.current?.slides?.[i]
      if (resumeIdx.current === i) {
        resumeIdx.current = null
        return
      }
      resumeIdx.current = null
      floor.current = null
      if (s) emit(s.step_from, s)
    },
    [emit],
  )
  const onCue = useCallback(
    (name, value) => {
      if (name !== 'step') return
      const n = Number(value)
      if (floor.current != null && n < floor.current) return
      emit(n)
    },
    [emit],
  )
  // the engine speaks the script's language; a new language is fetched first, then both switch together
  const narr = useNarration({ deck: script, lang: script?.lang || lang, onCue, onEnter })
  const { playingRef, pause, play, playFrom, goto } = narr
  const idxRef = useRef(0) // the engine's slide, for a resume
  useEffect(() => {
    idxRef.current = narr.idx
  })

  const want = useRef(false) // the page wants it playing
  const endedRef = useRef(false) // the last run reached its end: the next play starts over
  const pausedByUs = useRef(false) // the next stop is our pause, not the end
  const loadId = useRef(0)
  const startAt = useRef(null) // {from, play}: where to stand (and whether to play) once a fetched script is in the engine
  const heldKey = useRef(null) // `${params}|${lang}` of the script held

  const stopVoice = useCallback(() => {
    if (playingRef.current) {
      pausedByUs.current = true
      pause()
    }
  }, [playingRef, pause])

  // play from slide `i`: the engine's index moves there first (its playFrom only moves it when a slide ends), so the
  // slide shown and the slide spoken agree; while it is speaking, goto restarts it there by itself
  const restart = useCallback(
    (i) => {
      const speaking = playingRef.current
      goto(i)
      if (!speaking) playFrom(i, 0)
    },
    [goto, playFrom, playingRef],
  )

  const load = useCallback(async (key, req, from) => {
    const id = ++loadId.current
    startAt.current = { from, play: true }
    setLoading(true)
    setError(null)
    try {
      const sc = await getNarration(req)
      if (id !== loadId.current) return
      heldKey.current = key
      setLoading(false)
      setHeldFor(key)
      setScript(sc) // the effect below starts it (from startAt) once the engine holds it
    } catch (e) {
      if (id !== loadId.current) return
      startAt.current = null
      setLoading(false)
      setError(e)
      if (want.current) doneRef.current?.('error', e)
    }
  }, [])

  // play / pause / new props / a new language
  useEffect(() => {
    want.current = playing
    const key = `${pkey}|${lang}`
    if (!playing) {
      if (startAt.current) startAt.current.play = false // a script still on its way lands paused
      stopVoice()
      return
    }
    if (script && heldKey.current === key) {
      if (!playingRef.current && startAt.current == null) {
        pausedByUs.current = false
        if (endedRef.current || fromStartRef.current) {
          endedRef.current = false
          setEnded(false)
          resumeIdx.current = null
          floor.current = null
          restart(0)
        } else {
          resumeIdx.current = idxRef.current
          floor.current = lastStep.current
          play()
        }
      }
      return
    }
    // a script for these props in this language: a language switch keeps the slide (unless the last run had ended),
    // anything else starts over
    const keep = !!script && (heldKey.current || '').startsWith(`${pkey}|`) && !endedRef.current
    if (!keep) {
      stopVoice()
      endedRef.current = false
      setEnded(false)
    }
    load(key, { ...JSON.parse(pkey), lang: lang === 'es' ? 'es' : 'en' }, keep ? narr.idx : 0)
    // narr.idx is read at the moment of the switch on purpose (not a trigger)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, pkey, lang, script, load, stopVoice, play, restart, playingRef])

  // a fetched script is in the engine: start it (or, paused meanwhile, just stand on the slide it starts at)
  useEffect(() => {
    const at = startAt.current
    if (!script || !at) return
    const from = Math.min(at.from, script.slides.length - 1)
    startAt.current = null
    pausedByUs.current = false
    if (at.play && want.current) restart(from)
    else goto(from)
  }, [script, restart, goto])

  // unmounted (the page left): a fetch still on its way lands nowhere and reports nothing
  useEffect(
    () => () => {
      loadId.current += 1
      want.current = false
      startAt.current = null
    },
    [],
  )

  // the engine stopped by itself: the last word was said
  const was = useRef(false)
  useEffect(() => {
    const before = was.current
    was.current = narr.playing
    if (!before || narr.playing) return
    if (pausedByUs.current) {
      pausedByUs.current = false
      return
    }
    if (startAt.current != null) return // a new script is on its way: it takes over
    endedRef.current = true
    setEnded(true)
    const last = scriptRef.current?.slides?.at(-1)
    if (last) emit(last.step, last)
    if (want.current) doneRef.current?.('end')
  }, [narr.playing, emit])

  const slides = script?.slides || []
  const idx = Math.min(narr.idx, Math.max(0, slides.length - 1))
  const slide = slides[idx] || null
  const provider = narr.provider
  const voiceKind = narr.muted
    ? 'captions'
    : provider === 'timer'
      ? 'captions'
      : provider === 'browser' || narr.segFellBack
        ? 'browser'
        : provider === 'elevenlabs' || narr.voice?.configured
          ? 'elevenlabs'
          : 'browser'
  const status = error ? 'error' : loading ? 'loading' : narr.playing ? 'playing' : ended ? 'done' : script ? 'paused' : 'idle'
  const stale = !!script && heldFor !== `${pkey}|${lang}`

  return {
    status, // idle | loading | playing | paused | done | error
    stale, // the script held is for other props (a change stopped it): nothing of it is shown
    error,
    script,
    slides,
    idx,
    slide,
    caption: narr.caption, // {role, text, char, key}: the words being spoken, `char` the current word's offset
    progress: narr.progress, // 0..1 through the current slide
    playing: narr.playing,
    provider, // 'elevenlabs' | 'browser' | 'timer' | null (not started)
    voiceKind, // what the viewer hears: 'elevenlabs' | 'browser' | 'captions'
    voice: narr.voice, // /api/voice/status
    muted: narr.muted,
    setMuted: narr.setMuted,
    written: script?.ai?.by || null, // 'gemini' | 'mixed' | 'template'
    lang: script?.lang || lang,
    reduced,
  }
}
