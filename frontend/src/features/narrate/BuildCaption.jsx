import { useMemo, useSyncExternalStore } from 'react'
import AiBadge from '../ai/AiBadge'
import { wordsOf } from '../briefing/useNarration'
import './narrate.css'

// The caption bar at the bottom of the map while the narrated build-up plays: what the slide is about (the campus
// and its place), the words being spoken with the current one bright, the slide's progress, who speaks (ElevenLabs,
// the browser's voice, or captions alone) and the sound switch the presentation uses (the same remembered choice).
// Who wrote the words is labeled too: Gemini (its numbers checked against the study) or the plain template.
//
//   <BuildCaption nb={useBuildNarration(...)} />       nb: the hook's return; className places it (default: absolute,
//                                                       bottom of the nearest positioned box; on the Strengthen page
//                                                       a direct child of .mc--unlock sits in the map's grid area)

const T = {
  en: {
    presenter: 'Presenter',
    sound: 'Sound',
    on: 'on',
    off: 'off',
    voice: { elevenlabs: 'Voice: ElevenLabs', browser: 'Browser voice', captions: 'Captions only' },
    loading: 'Preparing the narration…',
    of: 'of',
    lines: 'Lines',
    checked: 'numbers checked',
    template: 'template lines',
    templateWhy: { off: 'AI off', none: 'Gemini not set up', gone: 'Gemini unavailable', failed: 'lines failed the checks' },
    region: 'Narration',
  },
  es: {
    presenter: 'Presentador',
    sound: 'Sonido',
    on: 'activado',
    off: 'apagado',
    voice: { elevenlabs: 'Voz: ElevenLabs', browser: 'Voz del navegador', captions: 'Solo subtítulos' },
    loading: 'Preparando la narración…',
    of: 'de',
    lines: 'Texto',
    checked: 'cifras comprobadas',
    template: 'texto de plantilla',
    templateWhy: { off: 'IA desactivada', none: 'Gemini sin configurar', gone: 'Gemini no disponible', failed: 'no pasó las comprobaciones' },
    region: 'Narración',
  },
}

const CHUNK = 132 // about two lines
const CHUNK_NARROW = 84 // about two lines on a phone: the bar covers less of the map
const NARROW = '(max-width: 860px)'

// phones (the page's one-column layout): shorter caption chunks
const narrowNow = () => {
  try {
    return window.matchMedia(NARROW).matches
  } catch {
    return false
  }
}
function subscribeNarrow(cb) {
  let mq
  try {
    mq = window.matchMedia(NARROW)
  } catch {
    return () => {}
  }
  mq.addEventListener?.('change', cb)
  return () => mq.removeEventListener?.('change', cb)
}
const useNarrow = () => useSyncExternalStore(subscribeNarrow, narrowNow, () => false)

// the caption chunk (about two lines) that holds the current word
function chunkAt(text, char, size = CHUNK) {
  const words = wordsOf(text)
  const out = []
  let cur = []
  for (const w of words) {
    if (cur.length && w.end - cur[0].start > size) {
      out.push(cur)
      cur = []
    }
    cur.push(w)
    if (/[.!?]["')\]]*$/.test(w.w) && w.end - cur[0].start > size * 0.5) {
      out.push(cur)
      cur = []
    }
  }
  if (cur.length) out.push(cur)
  return out.find((c) => char < c[c.length - 1].end + 1) || out[out.length - 1] || []
}

// the badge's tooltip: Gemini wrote it, the study checked every number, and how many lines went back once to be
// rewritten after failing a check (backend/narrate.py gemini_lines)
function geminiTitle(ai, lang) {
  const n = ai?.revised || 0
  if (lang === 'es')
    return `Escrito por Gemini; cada cifra comprobada con el estudio${n ? `; ${n} ${n === 1 ? 'línea reescrita' : 'líneas reescritas'} tras no pasar una comprobación` : ''}`
  return `Written by Gemini; every number checked against the study${n ? `; ${n} ${n === 1 ? 'line' : 'lines'} rewritten after failing a check` : ''}`
}

function whyOf(ai) {
  if (!ai?.requested) return 'off'
  if (ai.reason === 'Gemini not configured') return 'none'
  if (ai.reason === 'Gemini unavailable') return 'gone'
  return 'failed'
}

export default function BuildCaption({ nb, className = '' }) {
  const lang = nb?.lang === 'es' ? 'es' : 'en'
  const t = T[lang]
  const narrow = useNarrow()
  const cap = nb?.stale ? null : nb?.caption // a script for other props (a change stopped it) shows nothing of it
  const chunk = useMemo(() => (cap ? chunkAt(cap.text, cap.char, narrow ? CHUNK_NARROW : CHUNK) : []), [cap, narrow])
  if (!nb || nb.status === 'idle' || nb.status === 'error' || (nb.stale && nb.status !== 'loading')) return null
  if (!cap && nb.status !== 'loading' && nb.status !== 'playing') return null
  const slide = nb.slide
  const kind = slide?.kind || 'intro'
  const head = slide?.headline?.[lang] || ''
  const n = nb.slides.length
  const written = nb.script?.ai
  const by = slide?.written_by?.[lang]
  return (
    <div className={`bn-cap bn-cap--${kind}${nb.playing ? ' is-playing' : ''}${nb.reduced ? ' is-still' : ''}${className ? ` ${className}` : ''}`} role="region" aria-label={t.region}>
      <div className="bn-cap__top">
        {nb.status === 'loading' && !cap ? (
          <span className="bn-cap__kicker">{t.loading}</span>
        ) : (
          <>
            <span className="bn-cap__kicker">{head}</span>
            {n > 0 && (
              <span className="bn-cap__count">
                {nb.idx + 1} {t.of} {n}
              </span>
            )}
          </>
        )}
        <span className="bn-cap__tools">
          {written && (
            <AiBadge
              by={by === 'gemini' ? 'gemini' : 'fallback'}
              verified={false}
              why={t.templateWhy[whyOf(written)]}
              compact
              lang={lang}
              title={by === 'gemini' ? geminiTitle(written, lang) : undefined}
            >
              {by === 'gemini' ? t.checked : null}
            </AiBadge>
          )}
          <span className={`bn-cap__voice bn-cap__voice--${nb.voiceKind}`}>{t.voice[nb.voiceKind]}</span>
          <button type="button" className="bn-cap__sound" aria-pressed={!nb.muted} onClick={() => nb.setMuted(!nb.muted)}>
            {t.sound}: {nb.muted ? t.off : t.on}
          </button>
        </span>
      </div>
      {cap && (
        <p className="bn-cap__text" aria-hidden="true">
          <span className="bn-cap__who">{t.presenter}</span>
          {chunk.map((w, i) => (
            <span key={w.start} className={nb.reduced || w.start <= cap.char ? 'bn-w bn-w--said' : 'bn-w'}>
              {w.w}
              {i < chunk.length - 1 ? ' ' : ''}
            </span>
          ))}
        </p>
      )}
      {/* screen readers get each slide's words once, as it starts */}
      <p className="bn-sr" aria-live="polite">
        {cap?.text || ''}
      </p>
      <span className="bn-cap__bar" aria-hidden="true">
        <i style={{ transform: `scaleX(${Math.max(0, Math.min(1, nb.progress || 0))})` }} />
      </span>
    </div>
  )
}
