import { useEffect, useMemo, useState } from 'react'
import { tickerItems } from './showDeck'
import { S } from './showText'

// The show's chrome: the ticker of facts along the bottom, the banner over the map when a piece of
// crucial infrastructure falls, and the play-by-play line when a slide has no narration of its own.
// All of it is decoration for the eye (aria-hidden): the same facts are on the slides and in the transcript.

const pad = (n) => String(n).padStart(2, '0')

// facts crawl along the bottom for the whole show; the clock is time on air
export function Ticker({ report, deck, lang, playing }) {
  const t = S[lang]
  const items = useMemo(() => tickerItems(report, deck, lang), [report, deck, lang])
  const [secs, setSecs] = useState(0)
  useEffect(() => {
    if (!playing) return undefined
    const id = setInterval(() => setSecs((s) => s + 1), 1000)
    return () => clearInterval(id)
  }, [playing])
  const chars = items.reduce((n, s) => n + s.length, 0)
  const dur = Math.max(40, Math.round(chars * 0.105)) // seconds per loop: a steady, readable crawl
  const row = items.map((x, i) => (
    <em key={i}>
      {x}
      <i className="sh-ticker__sep" />
    </em>
  ))
  return (
    <div className={`sh-ticker${playing ? ' sh-ticker--on' : ''}`} aria-hidden="true">
      <span className="sh-ticker__cap">
        <i className="sh-ticker__dot" />
        {playing ? t.live : t.paused}
        <b>
          {pad(Math.floor(secs / 60))}:{pad(secs % 60)}
        </b>
      </span>
      <div className="sh-ticker__view">
        <div className="sh-ticker__track" style={{ '--dur': `${dur}s` }}>
          <span>{row}</span>
          <span>{row}</span>
        </div>
      </div>
    </div>
  )
}

// the banner: "Transformer down · North Fort Myers 6 · 141% of its limit" (it clears itself)
export function Callout({ callout }) {
  if (!callout) return null
  return (
    <div key={callout.key} className={`sh-callout sh-callout--${callout.tone || 'red'}`} aria-hidden="true">
      <Flame />
      <div className="sh-callout__txt">
        <p>
          <b>{callout.head}</b>
          <span>{callout.text}</span>
        </p>
        {callout.sub && <p className="sh-callout__sub">{callout.sub}</p>}
      </div>
    </div>
  )
}

// a small flame that flickers beside a failure; still under reduced motion
function Flame() {
  return (
    <span className="sh-flame">
      <svg viewBox="0 0 16 20" width="16" height="20">
        <path className="sh-flame__out" d="M8 1c1 3 5 5 5 10a5 5 0 0 1-10 0c0-2 1-3 2-4 0 2 1 3 2 3-1-3 0-6 1-9z" />
        <path className="sh-flame__in" d="M8 9c.6 1.6 2.4 2.4 2.4 4.6a2.4 2.4 0 0 1-4.8 0C5.6 12 7 11 8 9z" />
      </svg>
      <i />
      <i />
      <i />
    </span>
  )
}

// the play-by-play line for a beat that has no narration of its own
export function SayCaption({ say, lang }) {
  if (!say) return null
  return (
    <div key={say.key} className="rs-captions rs-captions--analyst sh-say" aria-hidden="true">
      <span className="rs-captions__who">{S[lang].playByPlay}</span>
      <p className="rs-captions__text">{say.text}</p>
    </div>
  )
}
