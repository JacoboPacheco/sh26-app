import { useMemo } from 'react'
import { D } from './describe'
import LevelMeter from './LevelMeter'
import { T } from './text'
import { wordsOf } from './useNarration'

const CHUNK = 118 // about two lines of captions

// Break a segment into caption chunks of about two lines, preferring sentence ends.
function chunksOf(text) {
  const words = wordsOf(text)
  const out = []
  let cur = []
  const flush = () => {
    if (!cur.length) return
    out.push({ start: cur[0].start, end: cur[cur.length - 1].end, words: cur })
    cur = []
  }
  for (const w of words) {
    const len = cur.length ? w.end - cur[0].start : w.w.length
    if (cur.length && len > CHUNK) flush()
    cur.push(w)
    const soFar = w.end - cur[0].start
    if (/[.!?]["')\]]*$/.test(w.w) && soFar > CHUNK * 0.45) flush()
  }
  flush()
  return out
}

// The lower third: who is speaking, the two lines being spoken, the current word bright. Decorative
// for screen readers (aria-hidden): the transcript button opens the same words as text.
//   speakers  /api/voice/status speakers ({presenter: 'George', analyst: 'Sarah'}): named only while ElevenLabs
//             speaks the words (caption.via), the segment's own name first (caption.speaker, from its audio), else
//             the status's; the browser voice and captions-only say "Presenter" / "Analyst"
//   audio     the ElevenLabs element playing ({el, meter}) for the live level meter
export default function Captions({ caption, lang, reduced, speakers, audio }) {
  const chunks = useMemo(() => chunksOf(caption?.text || ''), [caption?.text])
  if (!caption || !chunks.length) return null
  const t = T[lang]
  const char = caption.char
  const cur = chunks.find((c) => char < c.end + 1) || chunks[chunks.length - 1]
  const role = caption.role === 'analyst' ? 'analyst' : 'presenter'
  const eleven = caption.via === 'elevenlabs'
  const name = eleven ? caption.speaker || speakers?.[role] || null : null
  // an audio description of the map ("On the map"): the analyst's voice, named for what it says, in a plainer style
  const describe = !!caption.describe
  return (
    <div className={`rs-captions rs-captions--${role}${eleven ? ' rs-captions--eleven' : ''}${describe ? ' rs-captions--describe' : ''}`} aria-hidden="true" data-via={caption.via || ''}>
      <div className="rs-captions__head">
        <span className="rs-captions__speaker">
          {describe ? (
            D[lang].who
          ) : name ? (
            <>
              <b>{name}</b> · {t.roleOf[role]}
            </>
          ) : (
            t.whoOf[role]
          )}
        </span>
        {eleven && <span className="rs-captions__tag">{t.voiceEleven}</span>}
        {caption.via === 'browser' && <span className="rs-captions__tag">{t.voiceBrowser}</span>}
        {eleven && <LevelMeter audio={audio} reduced={reduced} />}
      </div>
      <p className="rs-captions__text">
        {cur.words.map((w, i) => (
          <span key={w.start} className={!reduced && w.start <= char ? 'rs-w rs-w--said' : 'rs-w'}>
            {w.w}
            {i < cur.words.length - 1 ? ' ' : ''}
          </span>
        ))}
      </p>
    </div>
  )
}
