import { useId } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { EmptyState, ErrorBanner } from '../../ui'
import AreaCard from './AreaCard'
import AreaSearch from './AreaSearch'
import { clearArea, conditionsOf, levelText, peopleNum, selectArea, useAreaList, useAreaUi, useExposure } from './areaStore'
import './town.css'

// "Is my area at risk?" — the panel the workspace mounts: search the region's areas, then the
// area's card; before a search, the areas the region's stress tests hit most. Picking an area flies
// the map to its lights (AreaLayer rings them). Everything is per region; the national map asks
// for a state first.
export default function AreaFinder({ title = 'Is my area at risk?' }) {
  const o = useOverload()
  const ui = useAreaUi()
  const list = useAreaList(o.region)
  const hId = useId()
  const slug = ui.region === o.region ? ui.slug : null // an area from another state isn't open here

  const pick = (a) => {
    selectArea(o.region, a)
    const full = list.data?.find((x) => x.slug === a.slug) || a
    if (full.sub_ids) o.focus(full.sub_ids.map(o.subPos), full.lat != null ? [full.lon, full.lat] : undefined)
  }

  return (
    <section className="area-finder" aria-labelledby={hId}>
      <h2 id={hId} className="panel-h">
        {title}
      </h2>
      {o.region === 'US' ? (
        <EmptyState title="Open a state first">Click a state on the map to look up the areas in its synthetic grid model.</EmptyState>
      ) : (
        <>
          <AreaSearch onPick={pick} selectedName={slug ? ui.name : ''} />
          {slug ? <AreaCard key={slug} slug={slug} onClose={clearArea} /> : <MostExposed onPick={pick} />}
        </>
      )}
    </section>
  )
}

// The areas the region's stress tests leave without power most often (a quick way in).
export function MostExposed({ onPick, limit = 5 }) {
  const o = useOverload()
  const c = conditionsOf(o)
  const ex = useExposure(o.region, c)
  const regionName = o.grid?.meta?.region === o.region ? o.grid.meta.region_name : o.region
  const d = ex.data

  if (ex.status === 'error') return <ErrorBanner error={ex.error} onRetry={ex.retry} />
  if (!d)
    return (
      <div className="area-progress" role="status" aria-live="polite">
        <span>Running the stress tests on the synthetic {regionName} model…</span>
        <span className="area-progress__bar" aria-hidden="true" />
      </div>
    )
  const top = d.areas.slice(0, limit)
  return (
    <section className="area-sec" aria-label={`Most exposed areas in ${d.region_name}`}>
      <h3 className="area-sec__h">Most exposed in {d.region_name}</h3>
      {top.length ? (
        <>
          <ol className="area-rank">
            {top.map((a) => (
              <li key={a.slug}>
                <button type="button" className="area-rank__row" onClick={() => onPick(a)}>
                  <span className="area-rank__name">{a.name}</span>
                  <span className="area-rank__n">
                    dark in {fmt(a.hits)} of {fmt(d.tested)}
                  </span>
                  <span className="area-rank__people">up to about {peopleNum(a.people_max)} people (estimate)</span>
                </button>
              </li>
            ))}
          </ol>
          <p className="area-card__est">
            {fmt(d.tested)} stress tests on the synthetic model, {d.firm ? 'firm campuses' : 'flexible campuses'}, at {levelText(d.load_factor)}. Not a
            prediction about any real project or utility.
          </p>
        </>
      ) : (
        <p className="area-card__est">
          None of the {fmt(d.tested)} stress tests leaves an area without power at {levelText(d.load_factor)}.
        </p>
      )}
    </section>
  )
}
