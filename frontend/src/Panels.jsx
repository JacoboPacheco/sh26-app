import { useEffect, useRef, useState } from 'react'
import { fmt } from './geo'
import { Badge, Button, Card, EmptyState, ErrorBanner, Field, Loading } from './ui'

const HOMES_PER_MW = 700 // matches backend/powerflow.py; an estimate (~1.4 kW per home)
const SEED_MARK = '(demo scenario)' // matches backend/seed.py

export function DataCenterCard({ mw, onMw }) {
  return (
    <Card title="Data center">
      <div
        className="dc-chip"
        draggable
        onDragStart={(e) => {
          e.dataTransfer.setData('text/plain', 'data-center')
          e.dataTransfer.effectAllowed = 'copy'
        }}
      >
        <strong>{fmt(mw)} MW</strong> — drag me anywhere on the map, or click the map
      </div>
      <Field
        label="Size (MW)"
        type="range"
        min={100}
        max={2000}
        step={50}
        value={mw}
        onChange={(e) => onMw(Number(e.target.value))}
        hint={`${fmt(mw)} MW is about as much as ${fmt(mw * HOMES_PER_MW)} homes use (estimate)`}
      />
    </Card>
  )
}

export function ScenarioCard({ user, scenarios, error, onRetry, onPick, onDelete, canSave, onSave, saving }) {
  return (
    <Card title="Saved scenarios">
      <ErrorBanner error={error} onRetry={onRetry} />
      {user === null ? (
        <EmptyState title="Not signed in">Saved scenarios need the demo account.</EmptyState>
      ) : scenarios === undefined ? (
        !error && <Loading />
      ) : scenarios.length === 0 ? (
        <EmptyState title="No saved scenarios">Drop the data center, then save the site.</EmptyState>
      ) : (
        <ul className="chips">
          {scenarios.map((sc) => (
            <li
              key={sc.id}
              title={sc.summary ? `${sc.summary.overloaded} over limit · ${fmt(sc.summary.headroom_mw)} MW headroom` : undefined}
            >
              <Button variant="secondary" onClick={() => onPick(sc)}>
                {sc.name}
              </Button>
              {/* everyone shares the demo account: the seeded demo scenarios can't be deleted from the page */}
              {!sc.note.includes(SEED_MARK) && (
                <Button variant="danger" onClick={() => onDelete(sc.id)} aria-label={`Delete ${sc.name}`}>
                  ×
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canSave && (
        <div className="row">
          <Button variant="secondary" busy={saving} onClick={onSave}>
            {saving ? 'Saving…' : 'Save this site'}
          </Button>
        </div>
      )}
    </Card>
  )
}

export function SiteCard({ site, result, solving, error, subName, cascade, cascading, cascadeError, step, onStep, onCascade }) {
  return (
    <Card title="What happens">
      <ErrorBanner error={error} />
      {!site ? (
        <EmptyState title="Drop the data center on the map">Or pick a saved scenario.</EmptyState>
      ) : !result ? (
        !error && <Loading label="Solving the grid…" />
      ) : (
        <>
          <p>
            Connected at <strong>{result.sub_name}</strong> ({fmt(result.kv)} kV){solving && <span className="muted"> · updating…</span>}
          </p>
          {result.overloaded.length > 0 ? (
            <>
              <p className="verdict verdict--bad">
                <strong>{overLimitText(result.overloaded)}</strong>
              </p>
              <ul className="list list--tight">
                {result.overloaded.slice(0, 6).map((o) => (
                  <li key={o.id}>
                    <span>
                      {o.from === o.to ? `${subName(o.from)} transformer` : `${subName(o.from)} → ${subName(o.to)}`}{' '}
                      <span className="muted">· {fmt(o.kv)} kV</span>
                    </span>
                    <Badge tone="warn">{o.pct.toFixed(0)} %</Badge>
                  </li>
                ))}
              </ul>
              {result.overloaded.length > 6 && <p className="muted">…and {result.overloaded.length - 6} more</p>}
            </>
          ) : (
            <p className="verdict verdict--ok">
              <strong>No line over limit.</strong>
            </p>
          )}
          <p>
            This site can take <strong>{fmt(result.headroom_mw)} MW</strong> before the first line overloads.
          </p>
          <div className="row">
            <Button onClick={onCascade} busy={cascading}>
              {cascading ? 'Running…' : cascade ? 'Run it again' : 'Run the cascade'}
            </Button>
          </div>
          <ErrorBanner error={cascadeError} />
          {cascade && <CascadeView cascade={cascade} step={step} onStep={onStep} />}
        </>
      )}
    </Card>
  )
}

// A branch with both ends in one substation is a transformer; the map can't draw it, so count it apart.
function overLimitText(overloaded) {
  const lines = overloaded.filter((o) => o.from !== o.to).length
  const xfmrs = overloaded.length - lines
  const parts = []
  if (lines) parts.push(`${lines} ${lines === 1 ? 'line' : 'lines'}`)
  if (xfmrs) parts.push(`${xfmrs} ${xfmrs === 1 ? 'transformer' : 'transformers'}`)
  return `${parts.join(' and ')} over limit`
}

// A number that counts up (or down) to its new value instead of jumping.
function useCountUp(target, ms = 500) {
  const [shown, setShown] = useState(target)
  const from = useRef(target)
  useEffect(() => {
    const start = performance.now()
    const a = from.current
    let raf = 0
    const tick = (now) => {
      const t = Math.min(1, (now - start) / ms)
      const v = a + (target - a) * (1 - (1 - t) ** 3)
      from.current = v
      setShown(v)
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return shown
}

function CascadeView({ cascade, step, onStep }) {
  const n = cascade.steps.length
  const cur = step > 0 && n > 0 ? cascade.steps[step - 1] : null
  const homes = useCountUp(cur ? cur.homes : 0)
  if (n === 0) return <p className="muted">Nothing to cascade — no line is over its limit.</p>
  const done = step >= n
  return (
    <div className="stack cascade">
      <p className="homes" aria-live="polite">
        Homes without power: <strong>{fmt(homes)}</strong> <span className="muted">(estimate, ~1.4 kW per home)</span>
      </p>
      <Field
        label="Cascade step"
        type="range"
        min={0}
        max={n}
        step={1}
        value={step}
        onChange={(e) => onStep(Number(e.target.value))}
        hint={step === 0 ? 'Before anything trips' : `Step ${step} of ${n}: ${cur.tripped.length} line tripped`}
      />
      {done && (
        <p className={cascade.outcome === 'islanded' ? 'verdict verdict--bad' : 'verdict'}>
          {cascade.outcome === 'islanded'
            ? `The grid split after ${n} ${n === 1 ? 'step' : 'steps'}: ${fmt(cascade.lost_mw)} MW of existing load lost.`
            : `Settled after ${n} ${n === 1 ? 'step' : 'steps'}.`}
          {cascade.site_dark_mw > 0.5 && ` The data center's own ${fmt(cascade.site_dark_mw)} MW lost power too.`}
        </p>
      )}
    </div>
  )
}

export function HeadroomCard({ on, onToggle, mw, counts, loading, error, onRetry }) {
  const n = (k) => (counts ? ` · ${fmt(counts[k])} ${counts[k] === 1 ? 'substation' : 'substations'}` : '')
  return (
    <Card title="Headroom">
      <div className="row">
        <Button variant={on ? 'primary' : 'secondary'} aria-pressed={on} onClick={onToggle}>
          Where can {fmt(mw)} MW go?
        </Button>
      </div>
      <ErrorBanner error={error} onRetry={onRetry} />
      {on && loading && <Loading />}
      {on && !loading && !error && (
        <div className="legend">
          <strong>Headroom before the first overload</strong>
          <ul>
            <li>
              <span className="swatch swatch--ok" aria-hidden="true" /> Takes {fmt(mw)} MW or more{n('ok')}
            </li>
            <li>
              <span className="swatch swatch--mid" aria-hidden="true" /> {fmt(mw / 2)}–{fmt(mw)} MW{n('mid')}
            </li>
            <li>
              <span className="swatch swatch--low" aria-hidden="true" /> Under {fmt(mw / 2)} MW{n('low')}
            </li>
          </ul>
        </div>
      )}
    </Card>
  )
}
