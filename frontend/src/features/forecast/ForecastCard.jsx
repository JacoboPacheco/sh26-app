import { useId } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { EmptyState, ErrorBanner, Loading } from '../../ui'
import { forecastBody, modeOf, useForecast } from './forecastApi'
import SweepChart from './SweepChart'
import VerdictBadge from './VerdictBadge'
import './forecast.css'

// Know before you run: the verdict, one sentence that explains it, the site's room, the first line
// to go, and the cascade in both modes side by side (the tiles are also the flexible/firm switch),
// then how the outcome changes with the campus's size (SweepChart; withSweep={false} leaves it out).
// Every number is the backend's (backend/forecast.py runs the same cascade the replay shows).
export default function ForecastCard({ title = 'If you run it', withSweep = true }) {
  const titleId = useId()
  const o = useOverload()
  const body = forecastBody(o)
  const f = useForecast(body)
  const firm = !!o?.firm

  let content
  if (!body) {
    content = <EmptyState title="Nothing to forecast yet">Drop a data center on the map to see what it does before you run the cascade.</EmptyState>
  } else if (f.error) {
    content = <ErrorBanner error={f.error} onRetry={f.retry} />
  } else if (!f.shown) {
    content = <Loading label="Forecasting this case…" />
  } else {
    content = <Answer data={f.shown} firm={firm} setFirm={o.setFirm} />
  }

  const verdict = f.shown && !f.error ? modeOf(f.shown, firm)?.verdict : null
  return (
    <section className="fc" aria-labelledby={titleId}>
      <header className="fc__head">
        <h3 className="fc__title" id={titleId}>
          {title}
        </h3>
        {body && verdict && (
          <span className={f.stale ? 'fc--stale' : undefined}>
            <VerdictBadge verdict={verdict} />
          </span>
        )}
      </header>
      <div className={`fc__answer${f.stale ? ' fc--stale' : ''}`} aria-busy={f.stale || undefined}>
        {content}
      </div>
      {withSweep && body && body.lat != null && !f.error && <SweepChart />}
    </section>
  )
}

function Answer({ data, firm, setFirm }) {
  const flex = data.cascade.flexible
  const firmMode = data.cascade.firm
  const mine = modeOf(data, firm)
  const quiet = flex.verdict === 'holds' && (!firmMode || firmMode.verdict === 'holds')
  const without = data.without_campus
  return (
    <div className="stack fc__body">
      <p className="fc__sentence" aria-live="polite">
        {mine.sentence}
      </p>

      {data.room_mw != null && (
        <dl className="fc__facts">
          <div>
            <dt>Room at {data.sub_area || 'this site'}</dt>
            <dd>{data.room_capped ? `Over ${fmt(data.room_mw)} MW` : `${fmt(data.room_mw)} MW`}</dd>
          </div>
          <div>
            <dt>First to overload</dt>
            <dd>{data.first_to_overload ? capitalize(data.first_to_overload) : `Nothing up to ${fmt(data.room_mw)} MW`}</dd>
          </div>
        </dl>
      )}

      {!quiet && firmMode && (
        <>
          <div className="fc__modes" role="group" aria-label="Flexible or firm service">
            <ModeTile mode={flex} selected={!firm} onSelect={() => setFirm?.(false)} />
            <ModeTile mode={firmMode} selected={firm} onSelect={() => setFirm?.(true)} />
          </div>
          <p className="fc__explain">
            Flexible means the campus can be cut off in an emergency, the kind of curtailment Texas&apos;s SB 6 (2025) allows for large loads;
            firm means it stays on and other customers are cut instead.
          </p>
        </>
      )}

      {mine.people > 0 && mine.areas_top5.length > 0 && <Areas areas={mine.areas_top5} />}

      {without && without.people > 0 && (
        <p className="fc__note">
          Even without your data center, this case leaves {fmt(without.people)} people without power (estimate).
        </p>
      )}
      <p className="fc__foot">Tested on a synthetic grid model, not any utility&apos;s network.</p>
    </div>
  )
}

function ModeTile({ mode, selected, onSelect }) {
  const name = mode.mode === 'firm' ? 'Firm' : 'Flexible'
  const people = mode.people
  let detail
  if (people === 0) {
    detail = mode.steps > 0 ? `Settles after ${plural(mode.steps, 'step')}` : 'Nothing trips'
  } else if (mode.mode === 'firm') {
    detail =
      mode.firm_held === false
        ? `${plural(mode.steps, 'step')} · cut off anyway`
        : `${plural(mode.steps, 'step')} · stays on · ${fmt(mode.shed_mw)} MW of others cut`
  } else {
    const cut = mode.campus_cut_off_at_step
    detail = `${plural(mode.steps, 'step')} · ${cut != null ? `cut off at step ${cut}` : mode.site_cut_off ? 'loses power' : 'stays on'}`
  }
  const worst = mode.peak_people > people * 1.05 ? mode.peak_people : null
  return (
    <button type="button" className={`fc-mode${selected ? ' fc-mode--on' : ''}`} aria-pressed={selected} onClick={onSelect}>
      <span className="fc-mode__name">
        {name}
        {selected && <span className="fc-mode__tag">Your setting</span>}
      </span>
      {people > 0 ? (
        <span className="fc-mode__num">{fmt(people)}</span>
      ) : (
        <span className="fc-mode__num fc-mode__num--zero">No one</span>
      )}
      <span className="fc-mode__unit">{people > 0 ? 'people without power (estimate)' : 'loses power'}</span>
      <span className="fc-mode__detail">{detail}</span>
      {worst && <span className="fc-mode__detail">At its worst {fmt(worst)}</span>}
    </button>
  )
}

function Areas({ areas }) {
  return (
    <div className="fc-areas">
      <h4 className="fc-areas__h">Hardest hit (estimate)</h4>
      <ul className="fc-areas__list">
        {areas.map((a) => (
          <li key={a.area}>
            <span>{a.area}</span>
            <span className="fc-areas__n">{fmt(a.people)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

const plural = (n, word) => `${fmt(n)} ${n === 1 ? word : `${word}s`}`
const capitalize = (s) => s.charAt(0).toUpperCase() + s.slice(1)
