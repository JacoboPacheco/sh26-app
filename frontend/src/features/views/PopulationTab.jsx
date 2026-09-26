import { useMemo, useState } from 'react'
import { ChartTable, RankedBars } from './charts'
import { compact, fmt, fmt1, mwText, stateHref, useCached } from './viewsKit'

// Population: how many residents each state's grid model stands for, and the ratio behind every "people without
// power" estimate in Overload (a state's residents divided by its model's base load). The last two measures set
// the reported data-center MW (sources on the Data centers tab) beside those residents, as a scale, never a cause.

const METRICS = [
  {
    id: 'population',
    label: 'Residents',
    short: 'Residents',
    get: (r) => r.population,
    fmt: (v, axis) => (axis ? compact(v) : fmt(v)),
    about: (d) => d.source,
  },
  {
    id: 'ppm',
    label: 'Residents per MW of model load',
    short: 'Residents per MW',
    get: (r) => r.people_per_mw,
    fmt: (v) => fmt(v),
    about: () => "A state's residents divided by its grid model's base load: the ratio that turns MW lost in a cascade into an estimate of people without power.",
  },
  {
    id: 'per100k',
    label: 'Reported data-center MW per 100,000 residents',
    short: 'DC MW per 100k',
    get: (r) => r.dc_mw_per_100k,
    fmt: (v, axis) => (axis ? compact(v) : fmt1(v)),
    about: () => 'Reported MW of operating, under-construction and announced data centers in the state (as reported by the sources on the Data centers tab), per 100,000 residents.',
  },
  {
    id: 'share',
    label: 'Reported data-center MW as % of the model base load',
    short: 'DC MW vs model load',
    get: (r) => r.dc_share_of_load_pct,
    fmt: (v, axis) => (axis ? `${compact(v)}%` : `${fmt1(v)}%`),
    about: () => "Reported data-center MW compared with the state model's base load. A scale comparison only: the model holds no real campus, and a share above 100% just means the reported MW is larger than that model's load.",
  },
]

export default function PopulationTab() {
  const { data, error } = useCached('/api/views/population')
  const [metricId, setMetricId] = useState('population')
  const [all, setAll] = useState(false)
  const [asTable, setAsTable] = useState(false)
  const [selected, setSelected] = useState(null)
  const metric = METRICS.find((m) => m.id === metricId)

  const rows = useMemo(() => {
    if (!data) return []
    return [...data.states]
      .filter((r) => metric.get(r) != null)
      .sort((a, b) => metric.get(b) - metric.get(a))
      .map((r) => ({
        key: r.code,
        label: r.name,
        value: metric.get(r),
        tip: [
          ['Residents', fmt(r.population)],
          ['Model base load', mwText(r.model_load_mw)],
          ['Residents per MW', fmt(r.people_per_mw)],
          ['Reported data centers', `${fmt(r.dc_sites)} sites · ${mwText(r.dc_mw)}`],
        ],
      }))
  }, [data, metric])
  const shownRows = all ? rows : rows.slice(0, 15)
  const sel = data?.states.find((r) => r.code === selected)

  if (error) return <p className="vw-error">Couldn&apos;t load the population view: {error.message}</p>
  if (!data) return <p className="muted">Loading…</p>

  return (
    <div className="vw-pop">
      <div className="vw-lead">
        <h2 className="vw-h2">Who lives behind each grid model</h2>
        <p>
          Overload turns megawatts lost in a cascade into an estimate of people without power by dividing a state&apos;s residents by its model&apos;s base load. These charts show that ratio and the residents behind it.
        </p>
      </div>

      <dl className="vw-figs">
        <div>
          <dt>Residents in the 48 state models</dt>
          <dd>{fmt(data.total_population)}</dd>
        </div>
        <div>
          <dt>Model base load</dt>
          <dd>{mwText(data.total_model_load_mw)}</dd>
        </div>
        <div>
          <dt>Residents per MW, all states</dt>
          <dd>{fmt(data.people_per_mw)}</dd>
        </div>
      </dl>

      <div className="vw-controls">
        <div className="vw-seg" role="group" aria-label="What the bars show">
          {METRICS.map((m) => (
            <button key={m.id} type="button" className={m.id === metricId ? 'vw-seg__b vw-seg__b--on' : 'vw-seg__b'} aria-pressed={m.id === metricId} onClick={() => setMetricId(m.id)}>
              {m.short}
            </button>
          ))}
        </div>
        <button type="button" className="vw-linkbtn" onClick={() => setAll((v) => !v)}>
          {all ? 'Show the top 15' : `Show all ${rows.length} states`}
        </button>
        <button type="button" className="vw-linkbtn" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          {asTable ? 'Show the chart' : 'Show as a table'}
        </button>
      </div>

      <section className="vw-panel" aria-label={metric.label}>
        <h3 className="vw-h3">{metric.label}</h3>
        <p className="vw-fine">{metric.about(data)}</p>
        {asTable ? (
          <ChartTable
            caption={`${metric.label} by state`}
            columns={[
              { key: 'state', label: 'State' },
              { key: 'population', label: 'Residents', num: true },
              { key: 'load', label: 'Model base load (MW)', num: true },
              { key: 'ppm', label: 'Residents per MW', num: true },
              { key: 'sites', label: 'Reported DC sites', num: true },
              { key: 'dcmw', label: 'Reported DC MW', num: true },
              { key: 'per100k', label: 'DC MW per 100k', num: true },
            ]}
            rows={data.states.map((r) => ({
              key: r.code,
              state: r.name,
              population: fmt(r.population),
              load: fmt(r.model_load_mw),
              ppm: fmt(r.people_per_mw),
              sites: fmt(r.dc_sites),
              dcmw: fmt(r.dc_mw),
              per100k: r.dc_mw_per_100k != null ? fmt1(r.dc_mw_per_100k) : '—',
            }))}
          />
        ) : (
          <RankedBars rows={shownRows} fmtValue={metric.fmt} selectedKey={selected} onSelect={(k) => setSelected((cur) => (cur === k ? null : k))} ariaLabel={`${metric.label}, ranked by state`} unit={metric.id === 'population' ? 'residents' : undefined} />
        )}
      </section>

      {sel && (
        <section className="vw-panel vw-panel--sel" aria-label={`${sel.name} in numbers`}>
          <div className="vw-card__head">
            <h3 className="vw-h3">{sel.name}</h3>
            <button type="button" className="vw-iconbtn" aria-label="Clear the selected state" onClick={() => setSelected(null)}>
              ×
            </button>
          </div>
          <dl className="vw-facts vw-facts--wide">
            <div>
              <dt>Residents</dt>
              <dd>{fmt(sel.population)}</dd>
            </div>
            <div>
              <dt>Model base load</dt>
              <dd>{mwText(sel.model_load_mw)}</dd>
            </div>
            <div>
              <dt>Residents per MW</dt>
              <dd>{fmt(sel.people_per_mw)}</dd>
            </div>
            <div>
              <dt>Reported data centers</dt>
              <dd>
                {fmt(sel.dc_sites)} sites · {mwText(sel.dc_mw)} ({fmt1(sel.dc_share_of_load_pct)}% of the model base load, as a scale)
              </dd>
            </div>
          </dl>
          <p className="row">
            <a className="vw-link" href={`#/views/datacenters?state=${sel.code}`}>
              See {sel.name}&apos;s data centers
            </a>
            <a className="vw-link" href={stateHref(sel.code)}>
              Open {sel.name}&apos;s grid model
            </a>
          </p>
        </section>
      )}

      <p className="vw-fine">
        {data.note} Residents: {data.source}. Grid models: {data.model_source}
      </p>
    </div>
  )
}
