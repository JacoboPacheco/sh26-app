import { fmt } from '../geo'
import { useOverload } from '../store'
import { ErrorBanner, Loading } from '../ui'
import { overLimitText } from './CampusPanel'
import { titleCase } from './cascadeSchedule'

// The top of the results column once a campus is dropped: where it plugs in, what goes over its limit, and
// how much this site can take, with a bar of the size asked against that room. It stays until the cascade
// runs (then the toll takes the column).
export default function SiteVerdict() {
  const { site, mw, result, solving, whatifError } = useOverload()
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
      <p className={over ? 'verdict verdict--bad sv__verdict' : 'verdict verdict--ok sv__verdict'}>
        {over ? overLimitText(result.overloaded) : 'No line over limit.'}
      </p>
      <div className="sv__bar" aria-hidden="true">
        <span className="sv__room" style={{ width: `${(Math.min(room, span) / span) * 100}%` }} />
        {!fits && <span className="sv__past" style={{ left: `${(room / span) * 100}%`, width: `${((mw - room) / span) * 100}%` }} />}
      </div>
      <p className="sv__room-text">
        This site can take <strong>{fmt(room)} MW</strong> before the first line overloads
        {fits ? '.' : <>; this campus asks for {fmt(mw)} MW.</>}
      </p>
    </section>
  )
}
