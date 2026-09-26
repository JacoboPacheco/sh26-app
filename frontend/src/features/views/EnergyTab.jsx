import { useMemo, useState } from 'react'
import { ErrorBanner, Loading } from '../../ui'
import { ChartTable, Legend, ShareBar, StackedBars, Swatch } from './charts'
import { FUELS, groupFuels } from './fuels'
import MapLink from './MapLink'
import { compact, fmt, fmt1, mwText, useCached } from './viewsKit'

// Energy: each state model's load against the generation capacity it holds, by fuel, and the national mix.
// Everything is the SYNTHETIC test system (Breakthrough Energy / Texas A&M): capacity is what is installed in
// the model, not what runs, and the mix is the dataset's snapshot, not today's real grid.

const OTHER_NOTE = 'oil, geothermal and unclassified plants'

export default function EnergyTab() {
  const { data, error, retry } = useCached('/api/views/energy')
  const [mode, setMode] = useState('mw')
  const [all, setAll] = useState(false)
  const [asTable, setAsTable] = useState(false)
  const [selected, setSelected] = useState(null)

  const national = useMemo(() => {
    if (!data) return []
    const cap = groupFuels(Object.fromEntries(data.national.by_fuel.map((f) => [f.fuel, f.capacity_mw])))
    const plants = {}
    data.national.by_fuel.forEach((f) => {
      const g = groupFuels({ [f.fuel]: 1 })
      const id = Object.keys(g).find((k) => g[k])
      plants[id] = (plants[id] || 0) + f.plants
    })
    const total = Object.values(cap).reduce((a, b) => a + b, 0) || 1
    return FUELS.map((f) => ({ ...f, value: cap[f.id], share: (100 * cap[f.id]) / total, plants: plants[f.id] || 0 }))
  }, [data])

  const rows = useMemo(() => {
    if (!data) return []
    return data.states.map((s) => {
      const g = groupFuels(s.by_fuel)
      const pct = mode === 'pct'
      const segs = pct ? Object.fromEntries(Object.entries(g).map(([k, v]) => [k, (100 * v) / s.load_mw])) : g
      return {
        key: s.code,
        label: s.name,
        segs,
        marker: pct ? 100 : s.load_mw,
        tip: [
          ['Model base load', mwText(s.load_mw)],
          ['Generation capacity', `${mwText(s.capacity_mw)} (${fmt1(s.capacity_over_load)}× load)`],
          ...FUELS.filter((f) => g[f.id] > 0).map((f) => [f.label, `${mwText(g[f.id])} · ${fmt1((100 * g[f.id]) / s.capacity_mw)}%`]),
        ],
      }
    })
  }, [data, mode])
  const shown = all ? rows : rows.slice(0, 15)
  const sel = data?.states.find((s) => s.code === selected)

  if (error) return <ErrorBanner error={{ message: `Couldn't load the energy view: ${error.message}` }} onRetry={retry} />
  if (!data) return <Loading label="Loading the energy view…" />

  const n = data.national
  // the axis in GW (20 GW, not 20k), the tooltips in MW or GW as the size needs
  const fmtAxis = mode === 'pct' ? (v) => `${fmt(v)}%` : (v, axis) => (axis ? (v ? `${compact(v / 1000)} GW` : '0') : mwText(v))
  const selGroups = sel ? groupFuels(sel.by_fuel) : null

  return (
    <div className="vw-energy">
      <div className="vw-lead">
        <h1 className="vw-h2">Load and generation in the grid models</h1>
        <p>
          Each state model has a base load and a set of plants. The bars show the generation capacity installed in the model, by fuel; the tick marks the model&apos;s base load. Capacity is not output, and the models are a
          synthetic test system, not the real grid.
        </p>
      </div>

      <section className="vw-panel" aria-label="National generation capacity by fuel">
        <h2 className="vw-h3">All 48 models together</h2>
        <p className="vw-fine">
          {mwText(n.capacity_mw)} of generation capacity in {fmt(n.plants)} plants, against {mwText(n.load_mw)} of base load.
        </p>
        <ShareBar parts={national.map((f) => ({ id: f.id, label: f.label, color: f.color, value: f.value }))} ariaLabel="Share of generation capacity by fuel, all 48 state models" />
        <div className="vw-tablewrap">
        <table className="vw-table vw-table--mix">
          <thead>
            <tr>
              <th scope="col">Fuel</th>
              <th scope="col" className="num">
                Capacity
              </th>
              <th scope="col" className="num">
                Share
              </th>
              <th scope="col" className="num">
                Plants
              </th>
            </tr>
          </thead>
          <tbody>
            {national.map((f) => (
              <tr key={f.id}>
                <td>
                  <Swatch color={f.color} /> {f.label}
                  {f.id === 'other' ? <span className="vw-fine"> ({OTHER_NOTE})</span> : null}
                </td>
                <td className="num">{mwText(f.value)}</td>
                <td className="num">{fmt1(f.share)}%</td>
                <td className="num">{fmt(f.plants)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </section>

      <div className="vw-controls">
        <div className="vw-seg" role="group" aria-label="Chart units">
          <button type="button" className={mode === 'mw' ? 'vw-seg__b vw-seg__b--on' : 'vw-seg__b'} aria-pressed={mode === 'mw'} onClick={() => setMode('mw')}>
            Megawatts
          </button>
          <button type="button" className={mode === 'pct' ? 'vw-seg__b vw-seg__b--on' : 'vw-seg__b'} aria-pressed={mode === 'pct'} onClick={() => setMode('pct')}>
            % of model load
          </button>
        </div>
        <button type="button" className="vw-linkbtn" onClick={() => setAll((v) => !v)}>
          {all ? 'Show the 15 largest' : `Show all ${rows.length} states`}
        </button>
        <button type="button" className="vw-linkbtn" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          {asTable ? 'Show the chart' : 'Show as a table'}
        </button>
      </div>

      <section className="vw-panel" aria-label="Load and generation capacity by state">
        <h2 className="vw-h3">{mode === 'pct' ? 'Generation capacity as a share of each model’s base load' : 'Base load and generation capacity by state'}</h2>
        <p className="vw-fine">States are ordered by base load{mode === 'pct' ? '; the tick is 100% of base load, so a bar past the tick has capacity to spare' : ''}. Click a state for its fuels.</p>
        {asTable ? (
          <ChartTable
            caption="Base load and generation capacity by fuel, by state (MW)"
            columns={[
              { key: 'state', label: 'State' },
              { key: 'load', label: 'Base load', num: true },
              { key: 'cap', label: 'Capacity', num: true },
              ...FUELS.map((f) => ({ key: f.id, label: f.label, num: true })),
            ]}
            rows={data.states.map((s) => {
              const g = groupFuels(s.by_fuel)
              return { key: s.code, state: s.name, load: fmt(s.load_mw), cap: fmt(s.capacity_mw), ...Object.fromEntries(FUELS.map((f) => [f.id, fmt(g[f.id])])) }
            })}
          />
        ) : (
          <>
            <Legend items={FUELS} />
            <StackedBars
              rows={shown}
              fmtValue={fmtAxis}
              selectedKey={selected}
              onSelect={(k) => setSelected((cur) => (cur === k ? null : k))}
              ariaLabel={mode === 'pct' ? 'Generation capacity by fuel as a share of base load, by state' : 'Generation capacity by fuel and base load, by state, in megawatts'}
              markerLabel={mode === 'pct' ? 'Tick: 100% of the model’s base load' : 'Tick: the model’s base load'}
            />
          </>
        )}
      </section>

      {sel && selGroups && (
        <section className="vw-panel vw-panel--sel" aria-label={`${sel.name} fuels`}>
          <div className="vw-card__head">
            <h2 className="vw-h3">{sel.name}</h2>
            <button type="button" className="vw-iconbtn" aria-label="Clear the selected state" onClick={() => setSelected(null)}>
              ×
            </button>
          </div>
          <p className="vw-fine">
            Base load {mwText(sel.load_mw)} · generation capacity {mwText(sel.capacity_mw)} ({fmt1(sel.capacity_over_load)}× load) in {fmt(sel.plants)} plants · {mwText(sel.dispatch_mw)} dispatched in the model&apos;s base case.
          </p>
          <ul className="vw-fuels">
            {FUELS.filter((f) => selGroups[f.id] > 0).map((f) => (
              <li key={f.id}>
                <Swatch color={f.color} />
                <span>{f.label}</span>
                <span className="num">{mwText(selGroups[f.id])}</span>
                <span className="num muted">{fmt1((100 * selGroups[f.id]) / sel.capacity_mw)}%</span>
              </li>
            ))}
          </ul>
          <p className="row">
            <MapLink className="vw-link" state={sel.code}>
              Open {sel.name}&apos;s grid model
            </MapLink>
          </p>
        </section>
      )}

      <p className="vw-fine">{data.note}</p>
      <p className="vw-fine">Grid models: {data.source}</p>
    </div>
  )
}
