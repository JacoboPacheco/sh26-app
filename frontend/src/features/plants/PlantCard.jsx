// The picked plant: what it is, what it's making now, which areas run on it (flow tracing), and
// the one action — take it out (or put it back). The trace loads on its own after the card
// opens; the trip button never waits for it.
import { Button, ErrorBanner } from '../../ui'
import { fmt, fuelFamily, fuelLabel, pct, pretty } from './fuels'
import { useTrace } from './plantsStore'

const SHOWN = 6

export default function PlantCard({ plant, region, traceCase, totalLoad, out, retiredByFuel, busy, onTrip, onRestore, onClose }) {
  const trace = useTrace(region, plant.id, traceCase, !out)
  const t = trace.status === 'done' ? trace.data : null
  // what it makes in the current case (the trace's), else in the snapshot
  const making = out ? 0 : (t?.output_mw ?? plant.output ?? plant.pg ?? 0)
  const share = totalLoad > 0 ? making / totalLoad : 0
  return (
    <section className={`pl-card${out ? ' pl-card--out' : ''}`} aria-label={`Plant ${pretty(plant.sub_name || plant.name)}`}>
      <div className="pl-card__head">
        <span className={`pl-swatch pl--${fuelFamily(plant.fuel)}`} aria-hidden="true" />
        <div className="pl-card__title">
          <h3>{pretty(plant.sub_name || plant.name)}</h3>
          <p className="muted">
            {fuelLabel(plant.fuel)} plant{plant.units > 1 ? ` · ${plant.units} units` : ''} · synthetic, named after its substation
          </p>
        </div>
        <button type="button" className="pl-x" onClick={onClose} aria-label="Close the plant card">
          ×
        </button>
      </div>

      <dl className="pl-facts">
        <div>
          <dt>Capacity</dt>
          <dd>{fmt(plant.pmax)} MW</dd>
        </div>
        <div>
          <dt>Making now</dt>
          <dd>{fmt(making)} MW</dd>
        </div>
        <div>
          <dt>Share of the load</dt>
          <dd>{out || making <= 0.5 ? '—' : share >= 0.001 ? `${(share * 100).toFixed(share >= 0.1 ? 0 : 1)} %` : 'under 0.1 %'}</dd>
        </div>
      </dl>

      <div className="pl-serves">
        <div>
          <h4 className="panel-h">Keeps these areas lit</h4>
          {!out && t && making > 0.5 && (t.serves || []).length > 0 && (
            <p className="pl-fine muted">Share of each area&apos;s power that comes from this plant</p>
          )}
        </div>
        {out ? (
          <p className="muted">Out of service: every area it served now leans on the rest of the grid.</p>
        ) : trace.status === 'error' ? (
          <ErrorBanner error={trace.error} onRetry={trace.retry} />
        ) : !t ? (
          <p className="muted" role="status">
            Tracing where its power goes…
          </p>
        ) : making <= 0.5 ? (
          <p className="muted">Not running right now: it&apos;s spare capacity, so no area depends on it.</p>
        ) : (
          <Serves trace={t} />
        )}
      </div>

      <div className="row pl-card__actions">
        {out ? (
          retiredByFuel ? (
            <p className="muted">Out with every {fuelLabel(plant.fuel).toLowerCase()} plant. Bring the fuel back below to restore it.</p>
          ) : (
            <Button variant="secondary" onClick={onRestore} busy={busy}>
              Put it back
            </Button>
          )
        ) : (
          <Button onClick={onTrip} busy={busy}>
            Trip this plant
          </Button>
        )}
      </div>
    </section>
  )
}

function Serves({ trace }) {
  const serves = trace.serves || []
  if (!serves.length) return <p className="muted">Its power doesn&apos;t make up a traceable share of any area.</p>
  // the areas it sends the most MW to, the most dependent first
  const top = serves.slice(0, SHOWN).sort((a, b) => (b.share || 0) - (a.share || 0))
  const more = serves.length - top.length + (trace.rest?.areas || 0)
  return (
    <>
      <ul className="pl-serves__list">
        {top.map((s) => (
          <li key={s.area} title={`${fmt(s.mw)} MW of this plant's power reaches ${s.area}`}>
            <span className="pl-serves__area">{s.area}</span>
            <span className="pl-bar" aria-hidden="true">
              <span style={{ width: `${Math.max(2, Math.min(100, (s.share || 0) * 100))}%` }} />
            </span>
            <span className="pl-serves__n">{pct(s.share)}</span>
          </li>
        ))}
      </ul>
      <p className="pl-fine muted">
        {more > 0 ? `And ${fmt(more)} more ${more === 1 ? 'area' : 'areas'}. ` : ''}
        {trace.exported_mw >= 1 ? `${fmt(trace.exported_mw)} MW leaves the state. ` : ''}
        Estimate: {trace.method || 'proportional sharing on the solved DC flows'}.
      </p>
    </>
  )
}
