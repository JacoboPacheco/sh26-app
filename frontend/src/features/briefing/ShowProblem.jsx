import { fmt } from '../../geo'
import { Count } from './ShowBits'
import { hm, tollOf, usdCompact } from './showDeck'
import { S } from './showText'
import { easeOutQuart } from './useShowClock'

// The pause: the play-by-play freezes on the worst moment, the map dims, and a slate names the problem
// with the big totals. A slow scan crosses it; hazard bands crawl along the top and bottom. Nothing here
// is narrated: it is the beat between "what happened" and "what fixes it".
export default function ShowProblem({ report, deck, lang, animate }) {
  const t = S[lang]
  const ev = report?.event || {}
  const toll = tollOf(deck, report)
  const time = toll.hours != null ? hm(toll.hours) : null
  const tollSlide = (deck?.slides || []).find((s) => (s.kind || s.id) === 'toll')
  const line = lang === 'en' && report?.headline?.text ? report.headline.text : tollSlide?.headline?.[lang] || report?.headline?.text || ''
  const cell = (i) => ({ ms: 1100, delay: 450 + i * 140, active: animate, ease: easeOutQuart })
  return (
    <div className={`sh-slate${animate ? ' sh-slate--go' : ''}`} role="group" aria-label={t.theProblem}>
      <div className="sh-slate__dim" />
      <div className="sh-slate__card">
        <div className="sh-slate__hazard" aria-hidden="true" />
        <div className="sh-slate__body">
          <p className="sh-slate__kicker">{t.slateSub}</p>
          <h2 className="sh-slate__title">{t.theProblem}</h2>
          <dl className="sh-slate__stats">
            {ev.people > 0 && (
              <div className="sh-slate__stat sh-slate__stat--big">
                <dt>{t.problemPeople}</dt>
                <dd>
                  <Count value={ev.people} {...cell(0)} />
                </dd>
              </div>
            )}
            {toll.cost != null && (
              <div className="sh-slate__stat">
                <dt>{t.problemCost}</dt>
                <dd>
                  <Count value={toll.cost} format={usdCompact} {...cell(1)} />
                </dd>
              </div>
            )}
            {time && (
              <div className="sh-slate__stat">
                <dt>{t.problemHours}</dt>
                <dd>
                  <Count value={toll.hours} format={(v) => `${hm(v).h} ${t.hoursShort} ${String(hm(v).m).padStart(2, '0')}`} {...cell(2)} />
                </dd>
              </div>
            )}
            {ev.lost_mw > 0 && (
              <div className="sh-slate__stat">
                <dt>{t.problemMw}</dt>
                <dd>
                  <Count value={ev.lost_mw} {...cell(3)} />
                </dd>
              </div>
            )}
          </dl>
          {line && <p className="sh-slate__line">{line}</p>}
        </div>
        <div className="sh-slate__hazard" aria-hidden="true" />
        <span className="sh-slate__scan" aria-hidden="true" />
      </div>
      <span className="sh-sr">{`${t.theProblem}: ${fmt(ev.people || 0)}`}</span>
    </div>
  )
}
