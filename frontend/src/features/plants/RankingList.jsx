// The plants a state can least afford to lose: people without power if that plant alone trips, at
// the current load level and with the data center placed (an estimate; computed per state in the
// background and cached). Plants whose loss darkens no one are counted, not listed — "harmless"
// is an answer too.
import { useState } from 'react'
import { Button, ErrorBanner } from '../../ui'
import { approx, fmt, fuelLabel, pretty } from './fuels'
import { useRanking } from './plantsStore'

const TOP = 6

// One row: the substation's plants (under `plant`, or inline) plus people / steps / outcome.
function normalize(row) {
  const p = row.plant && typeof row.plant === 'object' ? row.plant : row
  const ids = p.ids || p.plant_ids || (p.id != null ? [p.id] : [])
  return {
    key: `${p.sub ?? ''}|${ids.join(',')}`,
    ids,
    name: pretty(p.sub_name || p.name || ''),
    fuels: p.fuels || [p.fuel].filter(Boolean),
    pmax: p.pmax ?? 0,
    people: Math.max(0, Number(row.people) || 0),
    added: row.added_people != null ? Math.max(0, Number(row.added_people) || 0) : null,
    steps: row.steps ?? 0,
    outcome: row.outcome,
  }
}

export default function RankingList({ region, regionName, loadFactor, site, siteLabel, selectedId, onPick }) {
  const r = useRanking(region, loadFactor, site)
  const [all, setAll] = useState(false)
  const rows = (r.data?.ranking || []).map(normalize)
  const base = r.data?.baseline?.people || 0
  // with a campus that already darkens people, a plant "hurts" only if it adds to that
  const extra = (x) => (x.added != null ? x.added : Math.max(0, x.people - base))
  const hurts = rows.filter((x) => extra(x) > 0).sort((a, b) => b.people - a.people || extra(b) - extra(a))
  const harmless = rows.length - hurts.length
  const unsolved = r.data?.unsolved?.length || 0 // plants the engine couldn't settle: left out, counted
  const shown = all ? hurts : hurts.slice(0, TOP)
  const computing = r.status === 'computing' || r.status === 'loading'
  const done = r.data?.done ?? 0
  const total = r.data?.total ?? 0
  const lf = Number(loadFactor)
  const level = lf === 1 ? 'at today’s load' : `at ${Math.round(lf * 100)} % of today’s load`

  return (
    <section className="stack pl-rank" aria-label={`Plants ${regionName || region} can least afford to lose`}>
      <div>
        <h3 className="panel-h">The plants {regionName || region} can least afford to lose</h3>
        <p className="pl-fine muted">
          People without power if that plant alone trips, {level}
          {site ? `, with ${siteLabel || 'your data center'}` : ''}. Estimates on a synthetic model.
        </p>
      </div>

      {r.status === 'error' && <ErrorBanner error={r.error} onRetry={r.retry} />}

      {computing && (
        <div className="pl-progress" role="status" aria-live="polite">
          <progress value={done} max={Math.max(total, 1)} aria-label="Plants tested so far" />
          <span className="muted">{total ? `Tripping each plant in turn… ${fmt(done)} of ${fmt(total)}` : 'Tripping each plant in turn…'}</span>
        </div>
      )}

      {r.status === 'done' && base > 0 && (
        <p className="pl-fine muted">
          With every plant running, this case already leaves ~{approx(base)} people without power (estimate); the list counts the total.
        </p>
      )}

      {r.status === 'done' && !hurts.length && (
        <p className="muted">
          No single plant outage {base > 0 ? 'makes it worse' : 'leaves anyone without power'} {level}: the model has spare capacity. Try a heat
          wave, place a data center, or retire a whole fuel.
        </p>
      )}

      {shown.length > 0 && (
        <ol className="pl-rank__list">
          {shown.map((x, i) => {
            const on = selectedId != null && x.ids.includes(selectedId)
            return (
              <li key={x.key}>
                <button type="button" className={`pl-rank__row${on ? ' pl-rank__row--on' : ''}`} aria-pressed={on} onClick={() => onPick(x)}>
                  <span className="pl-rank__i">{i + 1}</span>
                  <span className="pl-rank__name">
                    <span>{x.name}</span>
                    <span className="pl-rank__sub muted">
                      {x.fuels.map((f) => fuelLabel(f).toLowerCase()).join(' + ')} · {fmt(x.pmax)} MW
                    </span>
                  </span>
                  <span className="pl-rank__n">
                    <strong>~{approx(x.people)}</strong>
                    <span className="muted"> people</span>
                  </span>
                </button>
              </li>
            )
          })}
        </ol>
      )}

      {r.status === 'done' && rows.length > 0 && (
        <div className="row pl-rank__foot">
          {harmless > 0 && (
            <p className="pl-fine muted">
              {fmt(harmless)} of {fmt(rows.length)}: losing any one alone {base > 0 ? 'adds no one' : 'leaves no one in the dark'}.
              {unsolved > 0 ? ` ${fmt(unsolved)} more couldn’t be tested on this model.` : ''}
            </p>
          )}
          {hurts.length > TOP && (
            <Button variant="secondary" onClick={() => setAll((v) => !v)}>
              {all ? 'Show the top six' : `Show all ${fmt(hurts.length)}`}
            </Button>
          )}
        </div>
      )}
    </section>
  )
}
