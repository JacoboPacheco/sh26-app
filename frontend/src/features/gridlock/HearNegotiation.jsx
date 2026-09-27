import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { assetUrl } from '../../api'
import { Button } from '../../ui'
import { getSegment, getVoiceStatus } from '../briefing/voiceApi'
import { readMuted, reducedMotion } from '../briefing/useNarration'
import './hear.css'

// "Hear the negotiation" (Build together, step 2): each agent's verified turn is read in its own voice, one after the
// other, and the closing summary by the presenter voice. The audio is built from the SAME words the trace shows: the
// server registers each turn's concerns and note with voice.py and hands back a key (data.turns[i].voice, data.voice
// .summary); nothing is spoken that the pipeline did not check. Rejected turns are not read aloud.
// Muted until the viewer turns sound on (the same remembered choice as the presentation, localStorage
// 'overload.sound.muted'); nothing plays by itself. Without a voice key on the server (POST /api/voice/segment answers
// 503 'Voice not configured') the control says so and the exchange stays the way to read it.
// onActive(n) tells the page which turn is being spoken (a turn number, 'summary', or null).

const MUTE_KEY = 'overload.sound.muted'
const GAP_MS = 380
const PREFETCH_AT = 0.4 // start rendering the next line once this much of the current one has played

const T = {
  en: {
    title: "Hear the agents' plans",
    lead: 'Each agent reads its own lines in its own voice, and the coordinator closes.',
    play: "Hear the agents' plans",
    playOn: 'Turn sound on and hear it',
    soundOff: 'Sound is off.',
    soundOn: 'Sound is on.',
    turnOff: 'Turn sound off',
    pause: 'Pause',
    resume: 'Resume',
    stop: 'Stop',
    replay: 'Hear it again',
    loading: 'Preparing the voice',
    speaking: (n, total) => `Speaking ${n} of ${total}`,
    done: 'That was everything the agents proposed.',
    checking: 'Checking the voice service',
    unavailable: "Voice isn't set up here: read the exchange below.",
    quota: "Today's voice allowance is used up: read the exchange below.",
    failed: "The voice service didn't answer. Try again in a moment, or read the exchange below.",
    blocked: 'Your browser blocked the sound. Press Resume to hear it.',
    expired: 'These plans were rebuilt: run them again to hear them.',
    words: 'Only words shown in the steps below are spoken.',
    rejectedSkipped: 'A turn the pipeline rejected is not read aloud.',
  },
  es: {
    title: 'Escuchar los planes de los agentes',
    lead: 'Cada agente lee sus propias líneas con su propia voz, y el coordinador cierra.',
    play: 'Escuchar los planes de los agentes',
    playOn: 'Activar el sonido y escucharlos',
    soundOff: 'El sonido está apagado.',
    soundOn: 'El sonido está activado.',
    turnOff: 'Apagar el sonido',
    pause: 'Pausa',
    resume: 'Continuar',
    stop: 'Detener',
    replay: 'Escucharlos otra vez',
    loading: 'Preparando la voz',
    speaking: (n, total) => `Hablando ${n} de ${total}`,
    done: 'Eso fue todo lo que propusieron los agentes.',
    checking: 'Comprobando el servicio de voz',
    unavailable: 'Aquí no hay voz configurada: lee el intercambio de abajo.',
    quota: 'Se agotó la cuota de voz de hoy: lee el intercambio de abajo.',
    failed: 'El servicio de voz no respondió. Prueba de nuevo en un momento, o lee el intercambio de abajo.',
    blocked: 'Tu navegador bloqueó el sonido. Pulsa Continuar para oírlo.',
    expired: 'Estos planes se reconstruyeron: vuelve a ejecutarlos para oírlos.',
    words: 'Solo se dicen palabras que muestran los pasos de los agentes.',
    rejectedSkipped: 'Un turno rechazado por el sistema no se lee en voz alta.',
  },
}

function audioElement(ref) {
  if (!ref.current) ref.current = new Audio()
  return ref.current
}

function setSoundOn(on) {
  try {
    localStorage.setItem(MUTE_KEY, on ? '0' : '1')
  } catch {
    // blocked storage: the choice just isn't remembered
  }
}

const isNoVoice = (e) => /not configured/i.test(e?.message || '')
const isQuota = (e) => /quota|busy|too many/i.test(e?.message || '')
const isExpired = (e) => /expired/i.test(e?.message || '')

// lines: [{key, role, side, text, who, n}] in the order they are spoken (hearLines.js builds them from a negotiation's turns
// or from the collaboration plans' trace); n is what onActive reports while a line is spoken. `who` is the caption label the
// server wrote (which published plan the agent reads, or the coordinator, or the summary), in the language it was spoken in.
export default function HearNegotiation({ lines, lang = 'en', onActive }) {
  const t = T[lang] || T.en
  const reduced = useMemo(() => reducedMotion(), [])
  const sig = lines.map((l) => l.key).join()
  const [svc, setSvc] = useState(null) // the voice service's status, once asked
  const [muted, setMuted] = useState(readMuted)
  const [st, setSt] = useState({ phase: 'idle', i: -1, err: null })
  const [seg, setSeg] = useState(null) // the segment being spoken: {words, ...}
  const [now, setNow] = useState(-1) // index of the word being spoken
  const audioRef = useRef(null)
  const token = useRef(0)
  const raf = useRef(0)
  const timer = useRef(0)
  const prefetched = useRef(-1)
  const cur = useRef(-1) // the line being spoken
  const capRef = useRef(null)
  const activeRef = useRef(onActive)
  const speakRef = useRef(null)
  useEffect(() => {
    activeRef.current = onActive
  }, [onActive])
  const active = useCallback((n) => activeRef.current?.(n), [])

  useEffect(() => {
    let live = true
    getVoiceStatus().then((s) => live && setSvc(s))
    return () => {
      live = false
    }
  }, [])

  const halt = useCallback(() => {
    token.current += 1
    cancelAnimationFrame(raf.current)
    clearTimeout(timer.current)
    const a = audioRef.current
    if (a) {
      a.onended = a.onerror = null
      a.pause()
    }
  }, [])

  // leaving the page, or a new negotiation (new lines): stop speaking
  useEffect(
    () => () => {
      halt()
      active(null)
    },
    [halt, active, sig],
  )

  const fail = useCallback(
    (e) => {
      halt()
      active(null)
      setSeg(null)
      setNow(-1)
      setSt({ phase: 'error', i: -1, err: isNoVoice(e) ? 'unavailable' : isQuota(e) ? 'quota' : isExpired(e) ? 'expired' : 'failed' })
    },
    [halt, active],
  )

  // the word clock: the audio's own time picks the word being spoken; the next line renders while this one is spoken
  const clock = useCallback(
    (a, s, i, me) => {
      const tick = () => {
        if (token.current !== me) return
        const ti = a.currentTime
        const words = s.words || []
        let k = -1
        for (let j = 0; j < words.length && words[j][1] <= ti; j++) k = j
        setNow(k)
        if (i + 1 < lines.length && prefetched.current < i + 1 && s.duration_s && ti >= s.duration_s * PREFETCH_AT) {
          prefetched.current = i + 1
          getSegment(lines[i + 1].key).catch(() => {})
        }
        raf.current = requestAnimationFrame(tick)
      }
      cancelAnimationFrame(raf.current)
      raf.current = requestAnimationFrame(tick)
    },
    [lines],
  )

  const speak = useCallback(
    async (i, me) => {
      if (token.current !== me) return
      if (i >= lines.length) {
        active(null)
        setSeg(null)
        setNow(-1)
        setSt({ phase: 'done', i: -1, err: null })
        return
      }
      const line = lines[i]
      cur.current = i
      active(line.n)
      setSt({ phase: 'loading', i, err: null })
      let s
      try {
        s = await getSegment(line.key)
      } catch (e) {
        if (token.current === me) fail(e)
        return
      }
      if (token.current !== me) return
      const a = audioElement(audioRef)
      a.preload = 'auto'
      a.src = assetUrl(s.audio_url)
      a.onended = () => {
        if (token.current !== me) return
        cancelAnimationFrame(raf.current)
        timer.current = setTimeout(() => speakRef.current?.(i + 1, me), GAP_MS)
      }
      a.onerror = () => token.current === me && fail(new Error('audio failed'))
      setSeg(s)
      setNow(-1)
      try {
        await a.play()
      } catch (e) {
        if (token.current !== me) return
        // the browser refused to start it: wait for the viewer's Resume
        setSt({ phase: 'paused', i, err: e?.name === 'NotAllowedError' ? 'blocked' : null })
        return
      }
      if (token.current !== me) {
        a.pause()
        return
      }
      setSt({ phase: 'playing', i, err: null })
      clock(a, s, i, me)
    },
    [lines, active, fail, clock],
  )

  useEffect(() => {
    speakRef.current = speak
  }, [speak])

  const start = useCallback(() => {
    halt()
    if (muted) {
      setSoundOn(true)
      setMuted(false)
    }
    prefetched.current = -1
    speak(0, token.current)
  }, [halt, muted, speak])

  const pause = useCallback(() => {
    const a = audioRef.current
    if (!a) return
    a.pause()
    cancelAnimationFrame(raf.current)
    setSt((s) => ({ ...s, phase: 'paused', err: null }))
  }, [])

  const resume = useCallback(() => {
    const a = audioRef.current
    const me = token.current
    if (!a) return
    a.play().then(
      () => {
        if (token.current !== me) return
        setSt((s) => ({ ...s, phase: 'playing', err: null }))
        if (seg) clock(a, seg, cur.current, me)
      },
      () => setSt((s) => ({ ...s, phase: 'paused', err: 'blocked' })),
    )
  }, [seg, clock])

  const stop = useCallback(() => {
    halt()
    active(null)
    setSeg(null)
    setNow(-1)
    setSt({ phase: 'idle', i: -1, err: null })
  }, [halt, active])

  const soundOff = useCallback(() => {
    setSoundOn(false)
    setMuted(true)
    stop()
  }, [stop])

  // keep the spoken word in view inside the caption box (only that box scrolls, never the page)
  useEffect(() => {
    const box = capRef.current
    const el = box?.querySelector('.is-now')
    if (!box || !el) return
    const top = el.offsetTop // the caption box is the positioned parent
    if (top < box.scrollTop || top + el.offsetHeight > box.scrollTop + box.clientHeight) {
      box.scrollTo({ top: Math.max(top - box.clientHeight / 3, 0), behavior: reduced ? 'auto' : 'smooth' })
    }
  }, [now, reduced])

  if (!lines.length) return null
  const busy = st.phase === 'loading' || st.phase === 'playing' || st.phase === 'paused'
  const line = st.i >= 0 ? lines[st.i] : null
  const unavailable = !!svc && !svc.configured
  const speakerName = line && svc?.speakers?.[line.role]
  const words = seg?.words?.length ? seg.words.map((w) => w[0]) : line ? line.text.split(/\s+/) : []
  const problem = st.err && t[st.err]
  const status = unavailable
    ? t.unavailable
    : !svc
      ? t.checking
      : st.phase === 'loading'
        ? `${t.loading}…`
        : busy
          ? t.speaking(st.i + 1, lines.length)
          : st.phase === 'done'
            ? t.done
            : muted
              ? t.soundOff
              : t.soundOn

  return (
    <section className="gl-hear" aria-label={t.title}>
      <div className="gl-hear__bar">
        <div className="gl-hear__what">
          <strong className="gl-hear__title">{t.title}</strong>
          <span className="gl-hear__state" role="status">
            {status}
          </span>
        </div>
        {!unavailable && svc && (
          <div className="gl-hear__buttons">
            {!busy && (
              <Button onClick={start}>
                <PlayIcon />
                {st.phase === 'done' ? t.replay : muted ? t.playOn : t.play}
              </Button>
            )}
            {busy && st.phase !== 'paused' && (
              <Button variant="secondary" onClick={pause} disabled={st.phase === 'loading'}>
                <PauseIcon />
                {t.pause}
              </Button>
            )}
            {st.phase === 'paused' && (
              <Button onClick={resume}>
                <PlayIcon />
                {t.resume}
              </Button>
            )}
            {busy && (
              <Button variant="secondary" onClick={stop}>
                <StopIcon />
                {t.stop}
              </Button>
            )}
            {!muted && !busy && (
              <button type="button" className="gl-link gl-hear__mute" onClick={soundOff}>
                {t.turnOff}
              </button>
            )}
          </div>
        )}
      </div>

      {problem && (
        <p className="gl-hear__err" role="alert">
          {problem}
        </p>
      )}
      {!busy && !unavailable && !problem && st.phase !== 'done' && <p className="gl-fine gl-hear__lead">{t.lead}</p>}

      {busy && line && (
        <div className={`gl-hear__cap gl-hear__cap--${line.side === 'a' || line.side === 'b' ? line.side : 'sum'}`} ref={capRef}>
          <p className="gl-hear__who">
            {speakerName ? `${speakerName} · ` : ''}
            {line.who}
          </p>
          <p className="gl-hear__text">
            {words.map((w, k) => (
              <span key={k} className={k < now ? 'is-said' : k === now ? 'is-now' : undefined}>
                {w}{' '}
              </span>
            ))}
          </p>
        </div>
      )}

      <p className="gl-fine gl-hear__fine">
        {t.words} {t.rejectedSkipped}
        {!unavailable && svc ? ` ${svc.attribution || 'Voice: ElevenLabs'}` : ''}
      </p>
    </section>
  )
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
      <path d="M4.5 2.8v10.4L13 8z" fill="currentColor" />
    </svg>
  )
}
function PauseIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
      <path d="M4.5 3h2.6v10H4.5zM8.9 3h2.6v10H8.9z" fill="currentColor" />
    </svg>
  )
}
function StopIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" focusable="false">
      <path d="M4 4h8v8H4z" fill="currentColor" />
    </svg>
  )
}
