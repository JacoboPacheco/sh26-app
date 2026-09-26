import { Fragment, useMemo } from 'react'
import LevelMeter from '../briefing/LevelMeter'
import { wordsOf } from '../briefing/useNarration'

const CHUNK = 96 // about two lines at caption size

// Break a line into caption chunks of about two lines, preferring sentence ends.
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
    if (/[.!?;:]["')\]]*$/.test(w.w) && w.end - cur[0].start > CHUNK * 0.5) flush()
  }
  flush()
  return out
}


// Kinetic captions: the words of the line being said land one by one, on the ElevenLabs word timestamps when the
// voice is ElevenLabs, on the browser voice's word boundaries, or at the reading pace with sound off. Words not yet
// said keep their place (no reflow). The speaker shows while they speak; the level meter reads the real audio.
// Decorative for screen readers: the live region below carries each whole line once.
export default function ShowCaptions({ caption, audio, reduced, voices, words }) {
  const chunks = useMemo(() => chunksOf(caption?.text || ''), [caption?.text])
  // between lines (and after a scene's last one) the caption's room stays, empty: nothing below it jumps
  if (!caption || !chunks.length) return <div className="shw-cap shw-cap--idle" aria-hidden="true" />
  const char = caption.char
  const cur = chunks.find((c) => char < c.end + 1) || chunks[chunks.length - 1]
  const role = caption.role === 'analyst' ? 'analyst' : 'presenter'
  const eleven = caption.via === 'elevenlabs'
  // a voice's own name only while that voice speaks (ElevenLabs); otherwise the role
  const name = eleven ? caption.name || voices?.[role]?.name || null : null
  const ROLE = { presenter: words?.presenter || 'Presenter', analyst: words?.analyst || 'Analyst' }
  return (
    <div className={`shw-cap shw-cap--${role}`} data-via={caption.via || ''}>
      <p className="shw-cap__who" aria-hidden="true">
        <span className="shw-cap__role">{name ? `${name}, ${ROLE[role].toLowerCase()}` : ROLE[role]}</span>
        {eleven && <span className="shw-cap__tag">{words?.elevenVoice || 'ElevenLabs voice'}</span>}
        {caption.via === 'browser' && <span className="shw-cap__tag">{words?.browserVoice || 'Browser voice'}</span>}
        {eleven && <LevelMeter audio={audio} reduced={reduced} />}
        {caption.check?.by === 'gemini' && (
          <span className="shw-cap__check" title="Every number in this line was checked against the engine's facts before it was shown">
            <svg viewBox="0 0 12 12" width="10" height="10" aria-hidden="true">
              <path d="M2 6.4 4.8 9 10 3" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {words?.checkedBy || 'Gemini wrote this'} ·{' '}
            {caption.check.rewritten
              ? words?.rewritten || 'rewritten after the checker sent it back'
              : caption.check.facts
                ? (words?.checkedFacts || ((n) => `${n} facts checked`))(caption.check.facts)
                : words?.checkedNone || 'checked'}
          </span>
        )}
      </p>
      <p className="shw-cap__text" aria-hidden="true" key={`${caption.key}:${cur.start}`}>
        {cur.words.map((w, i) => (
          <Fragment key={w.start}>
            <span className={w.start <= char ? 'shw-w is-said' : 'shw-w'}>{w.w}</span>
            {i < cur.words.length - 1 ? ' ' : null}
          </Fragment>
        ))}
      </p>
      <p className="shw-sr" aria-live="polite">
        {`${ROLE[role]}: ${caption.text}`}
      </p>
    </div>
  )
}
