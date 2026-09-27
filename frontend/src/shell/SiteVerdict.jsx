import { fmt } from '../geo'
import { useOverload } from '../store'
import { ErrorBanner, Loading } from '../ui'
import { overLimitText } from './CampusPanel'
import { titleCase } from './cascadeSchedule'
import './cause.css'

// The top of the results column once a campus is dropped: where it plugs in, what goes over its limit, and
// how much this site can take, with a bar of the size asked against that room. It stays until the cascade
// runs (then the toll takes the column).
export default function SiteVerdict() {
  const { site, mw, result, solving, whatifError, subName } = useOverload()
  if (!site) return null
  if (whatifError) return <ErrorBanner error={whatifError} />
  if (!result) return <Loading label="Solving the grid…" />
  const room = Math.max(0, result.headroom_mw || 0)
  const over = result.overloaded.length > 0
  // the bar spans the larger of the two, so the size asked and the room read against one scale
  const span = Math.max(mw, room, 1)
  const fits = room >= mw
  return (
    <section className={`sv${over ? ' sv--over' : ''}`} aria-label="Where it connects">
      <p className="sv__at">
        Connected at <strong>{titleCase(result.sub_name)}</strong>
        <span className="sv__kv"> · {fmt(result.kv)} kV</span>
        {solving && <span className="muted"> · updating…</span>}
      </p>
      <p
        className={over ? 'verdict verdict--bad sv__verdict' : 'verdict verdict--ok sv__verdict'}
        title="Rating (MVA): how much a line or transformer is built to carry. 'Over limit' means its flow is past that, past 100 % of its rating."
      >
        {over ? overLimitText(result.overloaded) : 'No line over limit.'}
      </p>
      {over && <CauseBars o={result.overloaded[0]} subName={subName} />}
      <div className="sv__bar" aria-hidden="true">
        <span className="sv__room" style={{ width: `${(Math.min(room, span) / span) * 100}%` }} />
        {!fits && <span className="sv__past" style={{ left: `${(room / span) * 100}%`, width: `${((mw - room) / span) * 100}%` }} />}
      </div>
      <p className="sv__room-text" title="Headroom: how many MW a site can add before the first line anywhere reaches 100 % of its rating.">
        This site can take <strong>{fmt(room)} MW</strong> before the first line overloads
        {fits ? '.' : <>; this campus asks for {fmt(mw)} MW.</>}
      </p>
    </section>
  )
}

// The cause in two numbers: the line or transformer that goes furthest past its rating, without the campus and
// with it (the same solve; base_pct from the what-if route). Bars on one scale with a mark at the rating.
function CauseBars({ o, subName }) {
  if (o?.base_pct == null) return null
  const name = o.from === o.to ? `${titleCase(subName(o.from))} transformer` : `${titleCase(subName(o.from))} → ${titleCase(subName(o.to))}`
  const top = Math.max(o.pct, 100) * 1.08
  const w = (v) => `${(Math.min(v, top) / top) * 100}%`
  return (
    <figure className="cause" aria-label={`${name}: ${Math.round(o.base_pct)} percent of its rating without this campus, ${Math.round(o.pct)} percent with it${o.rate_est ? '; rating estimated' : ''}`}>
      <figcaption className="cause__name">
        {name}
        {o.rate_est && (
          <span
            className="cause__est"
            title="The dataset leaves this element's rating blank; our build step set it 30 % above its flow in the dataset, or its voltage class's standard rating, whichever is larger."
          >
            {' '}
            (rating estimated)
          </span>
        )}
      </figcaption>
      <div className="cause__row">
        <span className="cause__k">Without this campus</span>
        <span className="cause__track">
          <span className="cause__fill" style={{ width: w(o.base_pct) }} />
          <span className="cause__limit" style={{ left: w(100) }} />
        </span>
        <span className="cause__v">{Math.round(o.base_pct)} %</span>
      </div>
      <div className="cause__row">
        <span className="cause__k">With it</span>
        <span className="cause__track">
          <span className={`cause__fill${o.pct > 100 ? ' cause__fill--over' : ''}`} style={{ width: w(o.pct) }} />
          <span className="cause__limit" style={{ left: w(100) }} />
        </span>
        <span className={`cause__v${o.pct > 100 ? ' cause__v--over' : ''}`}>{Math.round(o.pct)} %</span>
      </div>
    </figure>
  )
}
