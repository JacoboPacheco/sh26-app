import { LABEL, moneyIn, outageText, outageUnit } from '../cost/figures'
import { Kicker } from './ShowBits'
import { tollOf } from './showDeck'
import { S } from './showText'
import { easeOutQuart, useTween } from './useShowClock'

// "$1.08 billion" → the figure and its unit word, set in two sizes (the results panel's toll does the same)
function parts(v, lang) {
  const s = moneyIn(v, lang)
  const m = /^(\S+) (.+)$/.exec(s)
  return m ? { figure: m[1], unit: m[2] } : { figure: s, unit: '' }
}

// The first thing on screen: the expected cost counts up while the time without power counts up to its value,
// both written exactly as the results panel writes them ("$1.08 billion", "about 22 hours"; one set of numbers,
// features/cost/figures.js). Both are estimates from the engine's report; the deck's own words follow.
export default function ShowToll({ slide, deck, report, lang, animate }) {
  const t = S[lang]
  const toll = tollOf(deck, report)
  const cost = useTween(toll.cost ?? 0, { ms: 3000, delay: 300, active: animate && toll.cost != null, ease: easeOutQuart })
  const hours = useTween(toll.hours ?? 0, { ms: 4300, delay: 700, active: animate && toll.hours != null, ease: easeOutQuart })
  const costDone = !animate || toll.cost == null || cost >= toll.cost - 1
  const shown = parts(costDone ? toll.cost : cost, lang)
  const final = toll.hours != null ? outageText(toll.hours, lang) : ''
  // the count runs in the unit the end state is written in ("about 22 hours", "about 3 days")
  const unit = outageUnit(toll.hours)
  const hoursDone = !animate || toll.hours == null || hours >= toll.hours - 0.05
  const n = Math.max(1, Math.round(unit.unit === 'days' ? hours / 24 : hours))
  const unitWord = unit.unit === 'days' ? (lang === 'es' ? 'días' : 'days') : lang === 'es' ? 'horas' : 'hours'
  const about = lang === 'es' ? (unit.unit === 'days' ? 'unos' : 'unas') : 'about'
  const days = Math.max(1, Math.ceil((toll.hours || 24) / 24))
  const lines = slide.lines?.[lang] || slide.lines?.en || []
  const headline = slide.headline?.[lang] || slide.headline?.en || ''
  const L = LABEL[lang] || LABEL.en
  return (
    <>
      <section className="sh-block" aria-label={t.tollCost}>
        <Kicker tone="red">{t.tollCost}</Kicker>
        <p className="sh-mega">
          <span aria-hidden="true">
            {toll.cost == null ? '' : shown.figure}
            {toll.cost != null && shown.unit && <span className="sh-mega__u"> {shown.unit}</span>}
          </span>
          <span className="sh-sr">{toll.cost != null ? moneyIn(toll.cost, lang) : ''}</span>
        </p>
        <p className="sh-note">{slide.big?.label?.[lang] || slide.big?.label?.en}</p>
      </section>

      {toll.hours != null && (
        <section className="sh-block sh-block--clock" aria-label={t.tollTime}>
          <Kicker tone="red">{t.tollTime}</Kicker>
          <p className="sh-clock">
            {(() => {
              // the end state is outageText's own words ("about 22 hours"); counting up, the same shape
              const m = hoursDone ? /^(\S+) (\d+) (\S+)$/.exec(final) : null
              if (hoursDone && !m) return <span className="sh-clock__n" aria-hidden="true">{final}</span>
              return (
                <>
                  <span className="sh-clock__u sh-clock__u--pre" aria-hidden="true">
                    {m ? m[1] : about}
                  </span>
                  <span className="sh-clock__n" aria-hidden="true">
                    {m ? m[2] : n}
                  </span>
                  <span className="sh-clock__u" aria-hidden="true">
                    {m ? m[3] : unitWord}
                  </span>
                </>
              )
            })()}
            <span className="sh-sr">{final}</span>
          </p>
          <div className="sh-hours" aria-hidden="true" style={{ '--p': Math.min(1, hours / (days * 24)), '--days': days }}>
            <span className="sh-hours__fill" />
            {Array.from({ length: days - 1 }, (_, i) => (
              <span key={i} className="sh-hours__tick" style={{ left: `${((i + 1) / days) * 100}%` }} />
            ))}
          </div>
          <p className="sh-note">{L.time}</p>
        </section>
      )}

      <h2 className="rs-headline" id={`rs-h-${slide.id}`}>
        {headline}
      </h2>
      {lines.length > 0 && (
        <ul className="rs-lines sh-stagger">
          {lines.map((line, i) => (
            <li key={i} style={{ '--i': i + 4 }}>
              <span>{line}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}
