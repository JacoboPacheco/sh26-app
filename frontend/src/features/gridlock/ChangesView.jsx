import { useMemo, useState } from 'react'
import { EmptyState, ErrorBanner, Loading } from '../../ui'
import { useGridlock } from './context'
import { fmtInt, fmtMoney } from './format'

// What changed since the last filing: DESC's 2026-2030 list against its 2024-2028 list (GET /api/gridlock/changes,
// built offline by backend/demo/gridlock/diff_filings.py). The headline is what the newer list does to the comparison
// (pairs building in the same months, with each list, same engine, same settings); then every project, carried over /
// new / dropped, each with both PDF pages. The lists don't say why a date or an estimate changed, and neither does this.

const KINDS = [
  ['all', 'All'],
  ['later', 'Later dates'],
  ['carried_over', 'Carried over'],
  ['new', 'New'],
  ['dropped', 'Dropped'],
]

function span(months) {
  if (!months) return null
  const y = Math.floor(months / 12)
  const m = months % 12
  return [y ? `${y} year${y === 1 ? '' : 's'}` : '', m ? `${m} month${m === 1 ? '' : 's'}` : ''].filter(Boolean).join(' ')
}

export default function ChangesView() {
  const g = useGridlock()
  const c = g.changes
  const [kind, setKind] = useState('all')
  const [all, setAll] = useState(false)
  const rows = useMemo(() => c.data?.rows || [], [c.data])
  const shownRows = useMemo(
    () => rows.filter((r) => kind === 'all' || (kind === 'later' ? r.tags.includes('later') : r.change === kind)),
    [rows, kind],
  )
  if (c.status === 'idle' || c.status === 'loading') return <Loading label="Comparing the two DESC lists…" />
  if (c.status === 'error') return <ErrorBanner error={c.error} onRetry={g.loadChanges} />
  const d = c.data
  const n = d.counts || {}
  const was = d.comparison?.with_2024_2028 || {}
  const now = d.comparison?.with_2026_2030 || {}
  const [oldSrc, newSrc] = d.sources || []
  const count = { all: rows.length, later: n.later, carried_over: n.carried_over, new: n.new, dropped: n.dropped }
  const list = all ? shownRows : shownRows.slice(0, 14)
  return (
    <section className="gl-changes" aria-label="What changed since DESC's last filing">
      <p className="gl-lede">
        DESC published a newer list,{' '}
        <a href={newSrc?.url} target="_blank" rel="noreferrer">
          2026–2030
        </a>{' '}
        ({fmtInt(n.new_projects)} projects), after the{' '}
        <a href={oldSrc?.url} target="_blank" rel="noreferrer">
          2024–2028 list
        </a>{' '}
        Sperry&apos;s package uses ({fmtInt(n.old_projects)}). The ranking now reads the newer one.
      </p>

      <div className="gl-changes__cmp" role="group" aria-label={`Pairs within ${d.comparison?.limit}, DESC and Georgia Power`}>
        <div>
          <span className="gl-changes__cap">With the 2024–2028 list</span>
          <strong className="gl-changes__big">{fmtInt(was.together)}</strong>
          <span className="gl-fine">
            of {fmtInt(was.flagged)} pairs building in the same months ({fmtInt(was.still_ahead)} still ahead, {fmtInt(was.open_now)} open now)
          </span>
        </div>
        <span className="gl-changes__arrow" aria-hidden="true">
          →
        </span>
        <div className="is-now">
          <span className="gl-changes__cap">With the 2026–2030 list</span>
          <strong className="gl-changes__big">{fmtInt(now.together)}</strong>
          <span className="gl-fine">
            of {fmtInt(now.flagged)} pairs building in the same months ({fmtInt(now.still_ahead)} still ahead, {fmtInt(now.open_now)} open now)
          </span>
        </div>
      </div>
      <p className="gl-fine">
        DESC × Georgia Power within {d.comparison?.limit}, measured today ({d.comparison?.today}) with the same engine and settings.
      </p>

      <ul className="gl-changes__tally">
        <li>
          <strong>{fmtInt(n.carried_over)}</strong> carried over: {fmtInt(n.later)} with a later in-service date
          {n.earlier ? `, ${fmtInt(n.earlier)} earlier` : ''}, {fmtInt(n.cost_higher)} with a higher estimate, {fmtInt(n.cost_lower)} lower
        </li>
        <li>
          <strong>{fmtInt(n.new)}</strong> new in the 2026–2030 list
        </li>
        <li>
          <strong>{fmtInt(n.dropped)}</strong> no longer listed ({fmtInt(n.dropped_due_before_the_new_list)} were due in service before 2026, the
          first year the new list covers); they stay in the comparison, marked
        </li>
        {n.total_cost_old_list != null && (
          <li>
            Estimated total: {fmtMoney(n.total_cost_old_list)} then, {fmtMoney(n.total_cost_new_list)} now (each list&apos;s filed totals)
          </li>
        )}
      </ul>

      <div className="gl-tierset__opts" role="group" aria-label="Show">
        {KINDS.map(([id, label]) => (
          <button key={id} type="button" className={`gl-chip${kind === id ? ' is-on' : ''}`} aria-pressed={kind === id} onClick={() => setKind(id)}>
            {label} · {fmtInt(count[id])}
          </button>
        ))}
      </div>

      {!shownRows.length ? (
        <EmptyState title="Nothing in this group" />
      ) : (
        <ol className="gl-chlist">
          {list.map((r, i) => (
            <ChangeRow key={`${r.change}-${r.old?.page ?? ''}-${r.new?.page ?? ''}-${i}`} r={r} />
          ))}
        </ol>
      )}
      {shownRows.length > 14 && (
        <button type="button" className="gl-more-rows" onClick={() => setAll((v) => !v)} aria-expanded={all}>
          {all ? 'Show fewer' : `Show all ${shownRows.length}`}
        </button>
      )}
      <p className="gl-fine">
        Linked by id, then by title, then by description (each the other&apos;s best match). The lists don&apos;t say why a date or an estimate
        changed, and neither does this page. Rebuilt offline by <code>{d.command}</code>.
      </p>
    </section>
  )
}

function ChangeRow({ r }) {
  const g = useGridlock()
  const ins = r.in_service || {}
  const cost = r.cost || {}
  const badge =
    r.change === 'new'
      ? { cls: 'new', text: 'New' }
      : r.change === 'dropped'
        ? { cls: 'dropped', text: 'No longer listed' }
        : ins.changed && ins.direction
          ? { cls: 'later', text: `${span(ins.months) || 'a new date'} ${ins.direction}` }
          : { cls: 'same', text: 'Same date' }
  return (
    <li className={`gl-chrow gl-chrow--${r.change}`}>
      <div className="gl-chrow__top">
        <span className="gl-chrow__name">{r.name}</span>
        <span className={`gl-chbadge gl-chbadge--${badge.cls}`}>{badge.text}</span>
      </div>
      <dl className="gl-chrow__vals">
        {r.old && (
          <div>
            <dt>
              <a href={r.old.url} target="_blank" rel="noreferrer" title="Open the 2024–2028 list at this page">
                2024–2028, p. {r.old.page}
              </a>
            </dt>
            <dd>
              {r.old.in_service_raw ? `in service ${r.old.in_service_raw}` : 'no date'}
              {r.old.cost != null ? ` · ${fmtMoney(r.old.cost)}` : ''}
            </dd>
          </div>
        )}
        {r.new && (
          <div>
            <dt>
              <a href={r.new.url} target="_blank" rel="noreferrer" title="Open the 2026–2030 list at this page">
                2026–2030, p. {r.new.page}
              </a>
            </dt>
            <dd>
              {r.new.in_service_raw ? `in service ${r.new.in_service_raw}` : 'no date'}
              {r.new.cost != null ? ` · ${fmtMoney(r.new.cost)}` : ''}
              {cost.changed && cost.pct != null ? ` (${cost.pct > 0 ? '+' : ''}${cost.pct} %)` : ''}
            </dd>
          </div>
        )}
      </dl>
      <div className="gl-chrow__foot">
        {r.new && !r.new.kept && (
          <span className="gl-fine gl-n--warn" title={(r.new.reasons || []).join('\n')}>
            Set aside by the checks: {String(r.new.reasons?.[0] || '').split(':')[0]}
          </span>
        )}
        {r.id && r.on_map && (
          <button type="button" className="gl-link" onClick={() => g.openProject(r.id, { fly: true })}>
            Show on the map
          </button>
        )}
        {ins.note && <span className="gl-fine">{ins.note}</span>}
      </div>
    </li>
  )
}
