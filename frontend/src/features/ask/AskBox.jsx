import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { useOverload } from '../../store'
import { Badge, Button, ErrorBanner, Loading } from '../../ui'
import './ask.css'
import { askQuestion, askSuggestions, audioUrl, caseForCascade, voiceConfigured, voiceSegment } from './askApi'
import { factLabel, factValue } from './factText'

// Ask Overload: one question about the scenario on screen, answered from the engine's facts
// (backend/ask.py). Not a chat: one question, one answer, the facts it used, and any engine run it
// needed. Gemini writes the answer when it can; otherwise the fact sheet answers (labeled).
//
// Props (all optional): caseBody — the case to ask about (default: the store's current cascade or
// case); lang / onLangChange — controlled language ('en' | 'es'), else a small EN|ES switch;
// inputRef — lets a host focus the question field (the review stage's "/" key); autoFocus.

const T = {
  en: {
    label: 'Ask about this scenario',
    placeholder: 'e.g. What if it were half the size?',
    ask: 'Ask',
    suggestions: 'Questions this scenario can answer',
    thinking: 'Checking the facts…',
    noCase: 'Place a data center, pick a scenario or run a storm first — then ask about it.',
    ranEngine: 'Engine run',
    used: 'Facts used',
    gemini: (n) => `Gemini · ${n} ${n === 1 ? 'figure' : 'figures'} checked by the engine`,
    pattern: 'Answered from the fact sheet (AI offline)',
    declined: 'Off topic',
    read: 'Read aloud',
    stop: 'Stop',
    voiceBrowser: 'Browser voice',
    voiceEleven: 'Voice: ElevenLabs',
    note: 'SIMULATION · synthetic grid model · every figure is an estimate',
    you: 'You asked',
    lang: 'Answer language',
  },
  es: {
    label: 'Pregunta sobre este escenario',
    placeholder: 'p. ej. ¿Y si fuera de la mitad del tamaño?',
    ask: 'Preguntar',
    suggestions: 'Preguntas que este escenario puede responder',
    thinking: 'Comprobando los datos…',
    noCase: 'Coloca un centro de datos, elige un escenario o lanza una tormenta, y luego pregunta.',
    ranEngine: 'Motor ejecutado',
    used: 'Datos usados',
    gemini: (n) => `Gemini · ${n} ${n === 1 ? 'cifra comprobada' : 'cifras comprobadas'} por el motor`,
    pattern: 'Respondido con la hoja de datos (IA sin conexión)',
    declined: 'Fuera de tema',
    read: 'Leer en voz alta',
    stop: 'Detener',
    voiceBrowser: 'Voz del navegador',
    voiceEleven: 'Voz: ElevenLabs',
    note: 'SIMULACIÓN · modelo sintético de la red · todas las cifras son estimaciones',
    you: 'Preguntaste',
    lang: 'Idioma de la respuesta',
  },
}

// The store's scenario: the finished cascade's case, else the case being built (if it has anything in it).
function useStoreCase() {
  const { cascade, caseBody } = useOverload()
  return useMemo(() => {
    if (cascade) return caseForCascade(cascade, caseBody)
    const has = caseBody && (caseBody.lat != null || caseBody.sites?.length || caseBody.trip?.length || (caseBody.load_factor ?? 1) !== 1)
    return has ? caseBody : null
  }, [cascade, caseBody])
}

export default function AskBox({ caseBody: caseProp, lang: langProp, onLangChange, inputRef, autoFocus = false }) {
  const storeCase = useStoreCase()
  const caseBody = caseProp === undefined ? storeCase : caseProp
  const [ownLang, setOwnLang] = useState('en')
  const lang = langProp || ownLang
  const setLang = onLangChange || setOwnLang
  const t = T[lang] || T.en
  const caseKey = useMemo(() => (caseBody ? JSON.stringify(caseBody) : ''), [caseBody])

  const [question, setQuestion] = useState('')
  const [sugg, setSugg] = useState({ key: '', list: [] })
  const [state, setState] = useState({ key: '', busy: false, error: null, res: null, asked: '' })
  const req = useRef(0)
  const speech = useReadAloud()
  const { stop } = speech
  const fieldId = useId()
  const ownRef = useRef(null)
  const fieldRef = inputRef || ownRef

  // four questions this scenario answers, per case and language (no AI)
  useEffect(() => {
    if (!caseKey) return undefined
    let live = true
    const key = `${caseKey}|${lang}`
    askSuggestions(JSON.parse(caseKey), lang)
      .then((r) => live && setSugg({ key, list: r.questions || [] }))
      .catch(() => live && setSugg({ key, list: [] }))
    return () => {
      live = false
    }
  }, [caseKey, lang])

  // a new scenario or language silences the old answer
  useEffect(() => stop, [caseKey, lang, stop])

  const submit = useCallback(
    async (text) => {
      const q = String(text || '').trim()
      if (q.length < 3 || !caseKey) return
      const id = ++req.current
      stop()
      setQuestion(q)
      setState({ key: caseKey, busy: true, error: null, res: null, asked: q })
      try {
        const res = await askQuestion(JSON.parse(caseKey), q, lang)
        if (id === req.current) setState({ key: caseKey, busy: false, error: null, res, asked: q })
      } catch (error) {
        if (id === req.current) setState({ key: caseKey, busy: false, error, res: null, asked: q })
      }
    },
    [caseKey, lang, stop],
  )

  if (!caseBody) {
    return (
      <section className="ask" aria-label={t.label}>
        <p className="ask__empty">{t.noCase}</p>
      </section>
    )
  }

  const mine = state.key === caseKey
  const res = mine ? state.res : null
  const chips = sugg.key === `${caseKey}|${lang}` ? sugg.list : []

  return (
    <section className="ask" aria-label={t.label}>
      <form
        className="ask__form"
        onSubmit={(e) => {
          e.preventDefault()
          submit(question)
        }}
      >
        <div className="ask__labelrow">
          <label htmlFor={fieldId} className="ask__label">
            {t.label}
          </label>
          {!langProp && (
            <div className="ask__lang" role="group" aria-label={t.lang}>
              {['en', 'es'].map((l) => (
                <button key={l} type="button" className="ask__langbtn" aria-pressed={lang === l} onClick={() => setLang(l)}>
                  {l.toUpperCase()}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="ask__row">
          <input
            id={fieldId}
            ref={fieldRef}
            className="ask__input"
            type="text"
            value={question}
            maxLength={300}
            placeholder={t.placeholder}
            autoComplete="off"
            autoFocus={autoFocus}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <Button type="submit" busy={mine && state.busy} disabled={question.trim().length < 3}>
            {t.ask}
          </Button>
        </div>
      </form>

      {chips.length > 0 && (
        <ul className="ask__chips" aria-label={t.suggestions}>
          {chips.map((q) => (
            <li key={q}>
              <button type="button" className="ask__chip" onClick={() => submit(q)} disabled={mine && state.busy}>
                {q}
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="ask__out" aria-live="polite">
        {mine && state.busy && <Loading label={t.thinking} />}
        {mine && state.error && <ErrorBanner error={state.error} onRetry={/^Too many|quota/i.test(state.error.message || '') ? undefined : () => submit(state.asked)} />}
        {res && <Answer key={`${res.report_key}|${res.question}|${res.lang}`} res={res} t={t} speech={speech} />}
      </div>

      <p className="ask__note">{t.note}</p>
    </section>
  )
}

function Answer({ res, t, speech }) {
  const [open, setOpen] = useState(null) // the cited fact whose text is showing
  const shown = res.cited.find((c) => c.key === open)
  return (
    <article className={`ask-answer${res.declined ? ' ask-answer--declined' : ''}`}>
      <p className="ask-answer__q">
        <span className="ask-answer__qlabel">{t.you}</span> {res.question}
      </p>
      <p className="ask-answer__text">{res.answer}</p>

      {res.tool_calls?.length > 0 && (
        <ul className="ask-answer__tools">
          {res.tool_calls.map((c, i) => (
            <li key={i} className="ask-tool">
              <span className="ask-tool__tag">{t.ranEngine}</span>
              <span className="ask-tool__label">{c.label.replace(/^(Ran the engine|Motor ejecutado):\s*/, '')}</span>
            </li>
          ))}
        </ul>
      )}

      {res.cited.length > 0 && (
        <div className="ask-answer__facts">
          <span className="ask-answer__factslabel">{t.used}</span>
          <ul className="ask-facts">
            {res.cited.map((c) => (
              <li key={c.key}>
                <button
                  type="button"
                  className="ask-fact"
                  title={factValue(c.text, res.lang)}
                  aria-expanded={open === c.key}
                  onClick={() => setOpen(open === c.key ? null : c.key)}
                >
                  {factName(c, res.lang)}
                </button>
              </li>
            ))}
          </ul>
          {shown && (
            <p className="ask-facts__detail">
              <code>{shown.key}</code> {factLabel(shown.label, res.lang)}: {factValue(shown.text, res.lang)}
            </p>
          )}
        </div>
      )}

      <div className="ask-answer__foot">
        {res.declined ? (
          <Badge>{t.declined}</Badge>
        ) : res.source === 'gemini' ? (
          <Badge>{t.gemini(res.numbers_checked || 0)}</Badge>
        ) : (
          <Badge tone="warn">{t.pattern}</Badge>
        )}
        {speech.available && (
          <Button
            variant="secondary"
            aria-pressed={speech.speaking}
            onClick={() => (speech.speaking ? speech.stop() : speech.speak(res.answer, res.lang, res.voice_key))}
          >
            {speech.speaking ? t.stop : t.read}
          </Button>
        )}
        {speech.provider && <span className="ask-answer__voice">{speech.provider === 'elevenlabs' ? t.voiceEleven : t.voiceBrowser}</span>}
      </div>
    </article>
  )
}

// A short chip name for a cited fact: its label (in the answer's language), without the
// "What-if:" / "Simulación:" prefix.
function factName(c, lang) {
  const label = factLabel(c.label || c.key, lang)
    .replace(/^(What-if|Simulación):\s*/i, '')
    .replace(/^Fix \(([^)]+)\)\s*/i, 'Fix, $1 ')
  return label.length > 42 ? `${label.slice(0, 40)}…` : label
}

const canSpeak = typeof window !== 'undefined' && 'speechSynthesis' in window && typeof window.SpeechSynthesisUtterance === 'function'

// Read an answer aloud: the ElevenLabs voice when the voice service is configured (the server only
// speaks text it registered itself, by key), else the browser's own voice. Only on a click.
function useReadAloud() {
  const [speaking, setSpeaking] = useState(false)
  const [provider, setProvider] = useState(null)
  const [voiceOn, setVoiceOn] = useState(false)
  const batch = useRef(0)
  const audio = useRef(null)
  useEffect(() => {
    let live = true
    voiceConfigured().then((on) => live && setVoiceOn(on))
    return () => {
      live = false
    }
  }, [])

  const stop = useCallback(() => {
    batch.current++
    if (audio.current) {
      audio.current.pause()
      audio.current = null
    }
    if (canSpeak) window.speechSynthesis.cancel()
    setSpeaking(false)
  }, [])
  useEffect(() => stop, [stop])

  const browserSpeak = useCallback((text, lang, id) => {
    if (!canSpeak) {
      setSpeaking(false)
      return
    }
    const synth = window.speechSynthesis
    synth.cancel()
    // one utterance per sentence: Chrome cuts a long single utterance off after ~15 s
    const sentences = text.match(/[^.!?]+[.!?]+["')\]»”]*|[^.!?]+$/g) || [text]
    const want = lang === 'es' ? 'es' : 'en'
    const voices = synth.getVoices()
    const voice =
      voices.find((v) => v.lang?.toLowerCase().startsWith(`${want}-us`)) || voices.find((v) => v.lang?.toLowerCase().startsWith(want)) || null
    sentences.forEach((s, i) => {
      const u = new window.SpeechSynthesisUtterance(s.trim())
      u.lang = want === 'es' ? 'es-US' : 'en-US'
      if (voice) u.voice = voice
      const end = () => batch.current === id && setSpeaking(false)
      if (i === sentences.length - 1) u.onend = end
      u.onerror = end
      synth.speak(u)
    })
    setProvider('browser')
  }, [])

  const speak = useCallback(
    async (text, lang, voiceKey) => {
      stop()
      const id = ++batch.current
      setSpeaking(true)
      if (voiceKey && (await voiceConfigured())) {
        try {
          const seg = await voiceSegment(voiceKey)
          if (id !== batch.current) return
          const a = new Audio(audioUrl(seg.audio_url))
          audio.current = a
          a.onended = () => batch.current === id && setSpeaking(false)
          a.onerror = () => batch.current === id && browserSpeak(text, lang, id)
          await a.play()
          setProvider('elevenlabs')
          return
        } catch {
          // no voice for this one (quota, expired script, autoplay blocked): the browser reads it
        }
      }
      if (id === batch.current) browserSpeak(text, lang, id)
    },
    [stop, browserSpeak],
  )

  return { speaking, speak, stop, provider, available: canSpeak || voiceOn }
}
