import { useCallback, useEffect, useRef, useState } from 'react'
import { useOverload } from '../../store'
import { Badge, Button, ErrorBanner, Loading } from '../../ui'
import './bulletin.css'
import { getBulletin } from './bulletinApi'

// AI emergency bulletin: once a cascade has finished playing, three plain-English sentences about
// what just happened, written by Gemini from facts the server recomputes (backend/bulletin.py),
// or a template when the AI is offline. The request goes out as soon as the cascade lands, so the
// text is usually ready by the time the last step plays.

const canSpeak = typeof window !== 'undefined' && 'speechSynthesis' in window && typeof window.SpeechSynthesisUtterance === 'function'

function reducedMotion() {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

// The case the cascade actually ran with. The store's case, with the fields the cascade response
// echoes back taken from the response (a mode may override them for one run, e.g. a storm's trip list).
function bodyFor(cascade, caseBody, cascadeBody) {
  if (cascadeBody) return cascadeBody
  const body = {
    ...caseBody,
    load_factor: cascade.load_factor ?? caseBody.load_factor,
    trip: cascade.trip ?? caseBody.trip,
    upgrades: cascade.upgrades ?? caseBody.upgrades,
  }
  const count = (caseBody.lat != null ? 1 : 0) + (caseBody.sites?.length || 0)
  if (Array.isArray(cascade.sites) && cascade.sites.length !== count) {
    // the run overrode the data centers: use the substations the backend snapped them to
    delete body.lat
    delete body.lon
    delete body.mw
    body.sites = cascade.sites.map((s) => ({ lat: s.sub_lat, lon: s.sub_lon, mw: s.mw }))
  }
  return body
}

export default function Bulletin() {
  const { cascade, step, caseBody, cascadeBody } = useOverload()
  const n = cascade?.steps?.length || 0
  const done = !!cascade && n > 0 && step >= n
  const [res, setRes] = useState({ cascade: null, data: null, error: null })
  const pending = useRef(null)
  const caseRef = useRef(caseBody)
  const cascadeBodyRef = useRef(cascadeBody)
  useEffect(() => {
    caseRef.current = caseBody
    cascadeBodyRef.current = cascadeBody
  }, [caseBody, cascadeBody])

  const request = useCallback((c) => {
    pending.current = c
    getBulletin(bodyFor(c, caseRef.current, cascadeBodyRef.current))
      .then((data) => pending.current === c && setRes({ cascade: c, data, error: null }))
      .catch((error) => pending.current === c && setRes({ cascade: c, data: null, error }))
  }, [])

  // one request per cascade, sent as soon as it lands
  useEffect(() => {
    if (cascade && cascade.steps?.length && pending.current !== cascade) request(cascade)
  }, [cascade, request])

  if (!done) return null
  const mine = res.cascade === cascade
  const data = mine ? res.data : null
  const error = mine ? res.error : null
  const retry = () => {
    setRes({ cascade: null, data: null, error: null })
    request(cascade)
  }

  return (
    <section className="bulletin" aria-label="Emergency bulletin">
      <div className="bulletin__head">
        <h2 className="bulletin__title">
          <span className="bulletin__dot" aria-hidden="true" />
          Emergency bulletin
        </h2>
        {data && (data.fallback ? <Badge tone="warn">AI offline — template</Badge> : <Badge>Written by Gemini</Badge>)}
      </div>
      {error ? (
        <ErrorBanner error={error} onRetry={retry} />
      ) : !data ? (
        <Loading label="Writing the bulletin…" />
      ) : (
        <BulletinBody key={data.text} text={data.text} />
      )}
    </section>
  )
}

// How long the homes counter gets on screen before the column scrolls down to the bulletin:
// its count-up (shell/useCountUp, 500 ms) plus a beat, so the final number is seen first.
const COUNTER_BEAT_MS = 1300

function BulletinBody({ text }) {
  const ref = useRef(null)
  // null until we know whether the column must scroll to show the bulletin; then the typing delay
  const [delay, setDelay] = useState(null)
  const shown = useTypewriter(text, delay)
  const typing = shown.length < text.length
  const speech = useSpeech(text)

  // Bring the bulletin into view inside the floating right-hand column (desktop; not the page),
  // after the counter has landed. Typing starts once the bulletin is in view.
  useEffect(() => {
    const el = ref.current
    const col = el?.closest('.mc-right')
    const below = el && col ? el.getBoundingClientRect().bottom - col.getBoundingClientRect().bottom : 0
    if (below <= 0 || col.scrollHeight <= col.clientHeight || !window.matchMedia('(min-width: 861px)').matches) {
      setDelay(0)
      return undefined
    }
    const instant = reducedMotion()
    setDelay(instant ? 0 : COUNTER_BEAT_MS + 350)
    const t = setTimeout(() => {
      const now = el.getBoundingClientRect().bottom - col.getBoundingClientRect().bottom
      if (now > 0) col.scrollBy({ top: now + 16, behavior: instant ? 'auto' : 'smooth' })
    }, instant ? 0 : COUNTER_BEAT_MS)
    return () => clearTimeout(t)
  }, [])

  return (
    <div className="bulletin__body" ref={ref}>
      <p className="bulletin__text">
        {/* the full text reserves the final height so nothing below jumps while it types */}
        <span className="bulletin__ghost" aria-hidden="true">
          {text}
        </span>
        <span className="bulletin__typed" aria-hidden="true">
          {shown}
          {typing && <span className="bulletin__caret" />}
        </span>
        <span className="bulletin__sr" role="status">
          {text}
        </span>
      </p>
      <div className="bulletin__foot">
        {canSpeak && (
          <Button variant="secondary" onClick={speech.toggle} aria-pressed={speech.speaking}>
            {speech.speaking ? 'Stop reading' : 'Read it aloud'}
          </Button>
        )}
        <span className="bulletin__note">Synthetic grid model · homes are estimates</span>
      </div>
    </div>
  )
}

// Reveal `text` a few characters per frame (about two seconds at most), starting `delay` ms from now
// (null = not yet: show nothing); all at once under reduced motion.
function useTypewriter(text, delay) {
  const [count, setCount] = useState(0)
  const instant = reducedMotion()
  useEffect(() => {
    if (instant || delay == null) return undefined
    const total = Math.min(2400, Math.max(700, text.length * 9))
    const start = performance.now() + delay
    let raf = 0
    const tick = (now) => {
      const t = Math.max(0, Math.min(1, (now - start) / total))
      setCount(Math.ceil(text.length * t))
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [text, instant, delay])
  return instant ? text : text.slice(0, count)
}

// Read the text with the browser's own voice, only when asked. One utterance per sentence:
// Chrome cuts off a single long utterance after about fifteen seconds.
function useSpeech(text) {
  const [speaking, setSpeaking] = useState(false)
  const batch = useRef(0) // the reading in progress; 0 = none of ours
  useEffect(
    () => () => {
      if (batch.current) window.speechSynthesis.cancel()
      batch.current = 0
    },
    [],
  )
  const toggle = useCallback(() => {
    const synth = window.speechSynthesis
    const stop = () => {
      batch.current = 0
      setSpeaking(false)
    }
    synth.cancel()
    if (speaking) return stop()
    const id = Date.now()
    const sentences = text.match(/[^.!?]+[.!?]+["')\]]*|[^.!?]+$/g) || [text]
    const voices = synth.getVoices()
    const voice = voices.find((v) => v.lang?.startsWith('en') && v.localService) || voices.find((v) => v.lang?.startsWith('en'))
    sentences.forEach((s, i) => {
      const u = new window.SpeechSynthesisUtterance(s.trim())
      u.lang = 'en-US'
      if (voice) u.voice = voice
      const end = () => batch.current === id && stop() // ignore events from a reading that was replaced
      if (i === sentences.length - 1) u.onend = end
      u.onerror = end
      synth.speak(u)
    })
    batch.current = id
    setSpeaking(true)
  }, [speaking, text])
  return { speaking, toggle }
}
