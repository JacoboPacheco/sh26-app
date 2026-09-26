import { fmt } from '../geo'
import { HOMES_PER_MW, SEED_MARK, useOverload } from '../store'
import { Badge, Button, EmptyState, ErrorBanner, Field, Loading } from '../ui'

// Campus mode: the data center you drop — its size, the saved scenarios, and what it does to the
// lines around it. The cascade itself runs from the timeline at the bottom.
export default function CampusPanel() {
  const o = useOverload()
  const { mw, setMw, site, result, solving, whatifError, subName } = o
  return (
    <div className="stack panel-body">
      <div
        className="dc-chip"
        draggable
        onDragStart={(e) => {
          e.dataTransfer.setData('text/plain', 'data-center')
          e.dataTransfer.effectAllowed = 'copy'
        }}
      >
        <span className="dc-chip__mw">{fmt(mw)} MW</span>
        <span className="dc-chip__hint">AI campus — drag it onto Florida, or click the map</span>
      </div>
      <Field
        label="Size (MW)"
        type="range"
        min={100}
        max={2000}
        step={50}
        value={mw}
        onChange={(e) => setMw(Number(e.target.value))}
        hint={`About as much power as ${fmt(mw * HOMES_PER_MW)} homes use (estimate)`}
      />

      <Scenarios />

      <ErrorBanner error={whatifError} />
      {!site ? null : !result ? (
        !whatifError && <Loading label="Solving the grid…" />
      ) : (
        <div className="stack site-result">
          <p>
            Connected at <strong>{result.sub_name}</strong> ({fmt(result.kv)} kV){solving && <span className="muted"> · updating…</span>}
          </p>
          {result.overloaded.length > 0 ? (
            <>
              <p className="verdict verdict--bad">{overLimitText(result.overloaded)}</p>
              <ul className="over-list">
                {result.overloaded.slice(0, 5).map((o2) => (
                  <li key={o2.id}>
                    <span>
                      {o2.from === o2.to ? `${subName(o2.from)} transformer` : `${subName(o2.from)} → ${subName(o2.to)}`}
                    </span>
                    <Badge tone="warn">{o2.pct.toFixed(0)} %</Badge>
                  </li>
                ))}
              </ul>
              {result.overloaded.length > 5 && <p className="muted">…and {result.overloaded.length - 5} more</p>}
            </>
          ) : (
            <p className="verdict verdict--ok">No line over limit.</p>
          )}
          <p>
            This site can take <strong>{fmt(result.headroom_mw)} MW</strong> before the first line overloads.
          </p>
        </div>
      )}

      <HeadroomToggle />
    </div>
  )
}

function Scenarios() {
  const { user, scenarios, scenarioError, loadScenarios, pickScenario, removeScenario, canSave, saveSite, saving } = useOverload()
  if (user === null) return null
  return (
    <div className="stack scenarios">
      <h3 className="panel-h">Saved sites</h3>
      <ErrorBanner error={scenarioError} onRetry={loadScenarios} />
      {scenarios === undefined ? (
        !scenarioError && <Loading />
      ) : scenarios.length === 0 ? (
        <EmptyState title="No saved sites">Drop the data center, then save the site.</EmptyState>
      ) : (
        <ul className="chips">
          {scenarios.map((sc) => (
            <li
              key={sc.id}
              title={sc.summary ? `${sc.summary.overloaded} over limit · ${fmt(sc.summary.headroom_mw)} MW headroom` : undefined}
            >
              <Button variant="secondary" onClick={() => pickScenario(sc)}>
                {sc.name}
              </Button>
              {/* everyone shares the demo account: the seeded demo scenarios can't be deleted from the page */}
              {!sc.note.includes(SEED_MARK) && (
                <Button variant="danger" onClick={() => removeScenario(sc.id)} aria-label={`Delete ${sc.name}`}>
                  ×
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canSave && (
        <div className="row">
          <Button variant="secondary" busy={saving} onClick={saveSite}>
            {saving ? 'Saving…' : 'Save this site'}
          </Button>
        </div>
      )}
    </div>
  )
}

export function HeadroomToggle() {
  const { headroomOn, toggleHeadroom, mw, headroom, headroomError, fetchHeadroom } = useOverload()
  const counts = headroom ? countBuckets(headroom, mw) : null
  const n = (k) => (counts ? ` · ${fmt(counts[k])} ${counts[k] === 1 ? 'substation' : 'substations'}` : '')
  return (
    <div className="stack headroom">
      <div className="row">
        <Button variant={headroomOn ? 'primary' : 'secondary'} aria-pressed={headroomOn} onClick={toggleHeadroom}>
          Where can {fmt(mw)} MW go?
        </Button>
      </div>
      <ErrorBanner error={headroomError} onRetry={fetchHeadroom} />
      {headroomOn && !headroom && !headroomError && <Loading />}
      {headroomOn && headroom && (
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
    </div>
  )
}

function countBuckets(headroom, mw) {
  const c = { ok: 0, mid: 0, low: 0 }
  Object.values(headroom).forEach((v) => {
    if (v >= mw) c.ok++
    else if (v >= mw / 2) c.mid++
    else c.low++
  })
  return c
}

// A branch with both ends in one substation is a transformer; the map can't draw it, so count it apart.
export function overLimitText(overloaded) {
  const lines = overloaded.filter((o) => o.from !== o.to).length
  const xfmrs = overloaded.length - lines
  const parts = []
  if (lines) parts.push(`${lines} ${lines === 1 ? 'line' : 'lines'}`)
  if (xfmrs) parts.push(`${xfmrs} ${xfmrs === 1 ? 'transformer' : 'transformers'}`)
  return `${parts.join(' and ')} over limit`
}
