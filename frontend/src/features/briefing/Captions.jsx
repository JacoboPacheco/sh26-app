import { useMemo } from 'react'
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
export default function Captions({ caption, lang, reduced }) {
  const chunks = useMemo(() => chunksOf(caption?.text || ''), [caption?.text])
  if (!caption || !chunks.length) return null
  const char = caption.char
  const cur = chunks.find((c) => char < c.end + 1) || chunks[chunks.length - 1]
  return (
    <div className={`rs-captions rs-captions--${caption.role}`} aria-hidden="true">
      <span className="rs-captions__who">{caption.role === 'analyst' ? T[lang].analyst : T[lang].presenter}</span>
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
