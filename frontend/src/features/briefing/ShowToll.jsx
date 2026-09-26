import { Kicker } from './ShowBits'
import { hm, tollOf, usdCompact } from './showDeck'
import { S } from './showText'
import { easeOutQuart, useTween } from './useShowClock'

const pad = (n) => String(n).padStart(2, '0')

// The first thing on screen: the expected cost counts up while an outage clock fast-forwards to the
// average time without power. Both are estimates from the engine's report; the deck's own words follow.
export default function ShowToll({ slide, deck, report, lang, animate }) {
  const t = S[lang]
  const toll = tollOf(deck, report)
  const cost = useTween(toll.cost ?? 0, { ms: 3000, delay: 300, active: animate && toll.cost != null, ease: easeOutQuart })
  const hours = useTween(toll.hours ?? 0, { ms: 4300, delay: 700, active: animate && toll.hours != null, ease: easeOutQuart })
  const { h, m } = hm(hours)
  const end = hm(toll.hours)
  const costDone = !animate || toll.cost == null || cost >= toll.cost - 1
  const costText = toll.cost == null ? '' : costDone ? slide.big?.display?.[lang] || usdCompact(toll.cost) : usdCompact(cost)
  const days = Math.max(1, Math.ceil((toll.hours || 24) / 24))
  const lines = slide.lines?.[lang] || slide.lines?.en || []
  const headline = slide.headline?.[lang] || slide.headline?.en || ''
  return (
    <>
      <section className="sh-block" aria-label={t.tollCost}>
        <Kicker tone="red">{t.tollCost}</Kicker>
        <p className="sh-mega">
          <span aria-hidden="true">{costText}</span>
          <span className="sh-sr">{slide.big?.display?.[lang] || usdCompact(toll.cost)}</span>
        </p>
        <p className="sh-note">{slide.big?.label?.[lang] || slide.big?.label?.en}</p>
      </section>

      {toll.hours != null && (
        <section className="sh-block sh-block--clock" aria-label={t.tollTime}>
          <Kicker tone="red">{t.tollTime}</Kicker>
          <p className="sh-clock">
            <span className="sh-clock__n" aria-hidden="true">
              {pad(h)}
            </span>
            <span className="sh-clock__sep" aria-hidden="true">
              :
            </span>
            <span className="sh-clock__n" aria-hidden="true">
              {pad(m)}
            </span>
            <span className="sh-clock__u" aria-hidden="true">
              {t.hoursShort} : {t.minShort}
            </span>
            <span className="sh-sr">{`${end.h} ${t.hoursShort} ${end.m} ${t.minShort}`}</span>
          </p>
          <div className="sh-hours" aria-hidden="true" style={{ '--p': Math.min(1, hours / (days * 24)), '--days': days }}>
            <span className="sh-hours__fill" />
            {Array.from({ length: days - 1 }, (_, i) => (
              <span key={i} className="sh-hours__tick" style={{ left: `${((i + 1) / days) * 100}%` }} />
            ))}
          </div>
          <p className="sh-note">
            {slide.big2?.label?.[lang] || slide.big2?.label?.en}
            {slide.big2?.display?.[lang] ? ` · ${slide.big2.display[lang]}` : ''}
          </p>
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
