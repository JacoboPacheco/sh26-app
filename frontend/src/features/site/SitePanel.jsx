// FEATURE: the site report (owned by the site-report track).
// Contract: default export SiteReport() — a "Site report" button that appears once a data center is placed and
// opens a dialog with the report for the case on screen (region, main point, size, load level, service).
// Mounted from shell/CampusPanel.jsx. Nothing is fetched until the button is pressed.
//
// The report answers what a developer wants before choosing a site: which of the nearest substations take the
// campus, what limits each, the size that fits as is, the upgrades that would let the full size in (verified
// by re-running the model) and their cost — and what Overload alone adds: what happens to people if it is
// built anyway. Every figure is an estimate on a SYNTHETIC grid model (backend/sitereport.py).
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import { Button, ErrorBanner, Loading } from '../../ui'
import { moneyRange } from '../cost/money'
import './site.css'
import { cost, downloadText, fmtMw, fmtRoom, getSiteReport, n1Result, reportBody, reportKey, reportToMarkdown, rowVerdict } from './siteApi'

export default function SiteReport() {
  const { site, result, region } = useOverload()
  const [open, setOpen] = useState(false)
  const opener = useRef(null) // wraps the trigger button (ui.jsx's Button doesn't forward refs)
  if (!site || region === 'US') return null
  return (
    <div className="stack site-cta">
      {result && (
        <div className="row site-cta__row" ref={opener}>
          <Button variant="secondary" aria-haspopup="dialog" onClick={() => setOpen(true)}>
            Site report
          </Button>
          <span className="muted site-cta__hint">Nearest substations, room, upgrades, and what happens to people if it is built anyway.</span>
        </div>
      )}
      {open && <ReportDialog onClose={() => setOpen(false)} returnTo={opener} />}
    </div>
  )
}

// The report as it is fetched for the case on screen: {status, data, error}, refetched when the case changes
function useReport() {
  const { region, site, mw, loadFactor, firm } = useOverload()
  const body = site ? reportBody({ region, site, mw, loadFactor, firm }) : null
  const key = body ? reportKey(body) : ''
  const [state, setState] = useState({ key: '', status: 'loading', data: null, error: null })
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    if (!body) return undefined
    let live = true
    getSiteReport(body)
      .then((data) => live && setState({ key, status: 'done', data, error: null }))
      .catch((error) => live && setState({ key, status: 'error', data: null, error }))
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch only when the case (`key`) or a retry changes
  }, [key, attempt])
  const fresh = state.key === key
  return {
    status: fresh ? state.status : 'loading',
    data: fresh ? state.data : null,
    error: fresh ? state.error : null,
    retry: () => {
      setState({ key: '', status: 'loading', data: null, error: null })
      setAttempt((n) => n + 1)
    },
  }
}

function ReportDialog({ onClose, returnTo }) {
  const { status, data, error, retry } = useReport()
  const { grid } = useOverload()
  const box = useRef(null)
  const closeBtn = useRef(null)

  // focus the dialog on open and give it back on close; Escape closes (bound to the document, not a
  // bubbled key event, so it still works if focus lands on something print CSS just hid); Tab stays inside
  useEffect(() => {
    const back = returnTo?.current
    closeBtn.current?.focus()
    const onEscape = (e) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onEscape)
    return () => {
      document.removeEventListener('keydown', onEscape)
      back?.querySelector('button')?.focus()
    }
  }, [returnTo, onClose])
  const onKeyDown = useCallback(
    (e) => {
      if (e.key !== 'Tab' || !box.current) return
      const items = [...box.current.querySelectorAll('button:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])')].filter((el) => el.offsetParent !== null)
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    },
    [],
  )

  const where = grid?.meta?.region_name || 'this state'
  return createPortal(
    <div className="site-overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()} onKeyDown={onKeyDown}>
      <div className="site-report" role="dialog" aria-modal="true" aria-labelledby="site-h" ref={box}>
        <header className="site-head">
          <div className="site-head__title">
            <p className="site-eyebrow">Synthetic grid model · screening estimate · not an interconnection study</p>
            <h2 id="site-h">Site report</h2>
          </div>
          <div className="site-actions no-print">
            <Button variant="secondary" disabled={!data} onClick={() => window.print()}>
              Print or save as PDF
            </Button>
            <Button
              variant="secondary"
              disabled={!data}
              onClick={() => data && downloadText(`site-report-${data.site.region}-${Math.round(data.site.mw)}mw.md`, reportToMarkdown(data))}
            >
              Download as text
            </Button>
            <button type="button" className="site-close" ref={closeBtn} onClick={onClose} aria-label="Close the site report">
              Close
            </button>
          </div>
        </header>

        {status === 'loading' && <Loading label={`Screening the nearest substations in ${where}…`} />}
        {status === 'error' && <ErrorBanner error={error} onRetry={retry} />}
        {status === 'done' && data && <Body r={data} />}
      </div>
    </div>,
    document.body,
  )
}

function Body({ r }) {
  const s = r.site
  const best = r.substations.find((x) => x.id === r.best_id)
  return (
    <div className="site-body">
      <p className="site-where">
        <strong>{fmtMw(s.mw)}</strong> campus near {s.nearest_town}, {s.region_name} · {s.lat.toFixed(3)}, {s.lon.toFixed(3)} · {Math.round(s.load_factor * 100)} % of normal demand ·{' '}
        {s.firm ? 'firm' : 'flexible'} service
      </p>
      <p className={`site-verdict site-verdict--${r.verdict_kind}`}>{r.verdict}</p>

      <section className="site-sec" aria-labelledby="site-subs">
        <h3 className="panel-h" id="site-subs">
          The {r.substations.length} nearest substations
        </h3>
        <SubTable r={r} best={best} />
      </section>

      {r.n_minus_1 && <NMinus1 n1={r.n_minus_1} />}
      {r.if_built_anyway && <BuiltAnyway b={r.if_built_anyway} />}

      <section className="site-sec site-method" aria-labelledby="site-how">
        <h3 className="panel-h" id="site-how">
          How this was computed
        </h3>
        <p>{r.method}</p>
        <ul>
          {r.assumptions.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
        <p className="site-sources">
          Sources:{' '}
          {r.sources.map((src, i) => (
            <span key={src.url}>
              {i > 0 && '; '}
              <a href={src.url} target="_blank" rel="noreferrer">
                {src.name}
              </a>
            </span>
          ))}
          .
        </p>
        <p className="site-disclaimer">{r.disclaimer}</p>
      </section>
    </div>
  )
}

function SubTable({ r, best }) {
  // the recommended row starts open (and is the one a printout shows in full)
  const [open, setOpen] = useState(() => new Set(best ? [best.id] : []))
  const toggle = (id) =>
    setOpen((cur) => {
      const next = new Set(cur)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  return (
    <div className="site-tablewrap">
      <table className="site-table">
        <caption className="sr-only">Substations nearest the site, with distance, voltage, room and whether the campus fits</caption>
        <thead>
          <tr>
            <th scope="col" className="num">
              Rank
            </th>
            <th scope="col">Substation</th>
            <th scope="col" className="num">
              Distance
            </th>
            <th scope="col" className="num">
              kV
            </th>
            <th scope="col" className="num">
              Room
            </th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        {r.substations.map((x) => {
          const v = rowVerdict(x)
          const isOpen = open.has(x.id)
          const panel = `site-detail-${x.id}`
          return (
            <tbody key={x.id} className={isOpen ? 'is-open' : undefined}>
              <tr>
                <td className="num">{x.rank}</td>
                <th scope="row">
                  <button type="button" className="site-toggle" aria-expanded={isOpen} aria-controls={panel} onClick={() => toggle(x.id)}>
                    <span className="site-toggle__mark" aria-hidden="true" />
                    <span>{x.name}</span>
                    {x.id === r.best_id && <span className="site-best">Best</span>}
                  </button>
                </th>
                <td className="num">{x.distance_km.toFixed(1)} km</td>
                <td className="num">{fmt(x.kv)}</td>
                <td className="num">{fmtRoom(x)}</td>
                <td>
                  <span className={`site-chip site-chip--${v.kind}`}>{v.text}</span>
                </td>
              </tr>
              <tr className="site-detail-row" hidden={!isOpen}>
                <td colSpan={6} id={panel}>
                  {isOpen && <Detail x={x} mw={r.site.mw} />}
                </td>
              </tr>
            </tbody>
          )
        })}
      </table>
    </div>
  )
}

function Detail({ x, mw }) {
  const { setMw, place } = useOverload()
  const lim = x.limiting
  const u = x.upgrade
  const right = Math.floor(x.right_size_mw)
  return (
    <div className="site-detail">
      <div className="site-facts">
        <div>
          <h4>Limiting element</h4>
          {lim ? (
            <p>
              {lim.label} ({fmt(lim.kv)} kV, {fmt(lim.rating_mva)} MVA). It is <strong>{fmt(lim.loading_pct_at_mw)} %</strong> loaded at {fmtMw(mw)}
              {lim.limits_headroom ? ' and is the first line to overload here.' : '; nothing limits this substation at any size the model takes.'}
            </p>
          ) : (
            <p>No line comes near its limit at this size.</p>
          )}
        </div>
        <div>
          <h4>Right-size</h4>
          {x.fits ? (
            <p>Takes the full {fmtMw(mw)} with no upgrades.</p>
          ) : right >= 1 ? (
            <p>
              Takes up to <strong>{fmtMw(x.right_size_mw)}</strong> with no upgrades.
            </p>
          ) : (
            <p>No room without upgrades.</p>
          )}
          <div className="row site-detail__acts no-print">
            {!x.fits && right >= 1 && (
              <Button variant="secondary" onClick={() => setMw(right)}>
                Test {fmtMw(right)}
              </Button>
            )}
            {x.rank > 1 && (
              <Button variant="secondary" onClick={() => place(x.lat, x.lon)}>
                Move the campus here
              </Button>
            )}
          </div>
        </div>
      </div>

      {u && (
        <div className="site-upgrade">
          <h4>To take the full {fmtMw(mw)}</h4>
          <p>
            Raise {u.count} {u.count === 1 ? 'line or transformer' : 'lines and transformers'} by {fmt(u.mva_added)} MVA in all: about <strong>{cost(u)}</strong>.{' '}
            {u.verified ? (
              <span className="site-ok">Verified: the case re-run with these ratings leaves no line over its limit.</span>
            ) : (
              <span className="site-note">{u.reason || 'Not verified.'}</span>
            )}
          </p>
          {u.lines.length > 0 && (
            <ul className="site-upgrade__list">
              {u.lines.slice(0, 8).map((ln) => (
                <li key={ln.id}>
                  <span>{ln.label}</span>
                  <span className="num">
                    {fmt(ln.old_mva)} → {fmt(ln.new_mva)} MVA
                  </span>
                  <span className="num">{moneyRange(ln.cost_low_usd, ln.cost_high_usd)}</span>
                </li>
              ))}
              {u.count > Math.min(u.lines.length, 8) && <li className="muted">and {u.count - Math.min(u.lines.length, 8)} more, priced in the total</li>}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

function NMinus1({ n1 }) {
  return (
    <section className="site-sec" aria-labelledby="site-n1">
      <h3 className="panel-h" id="site-n1">
        One line out (N-1) at {n1.substation.name}
      </h3>
      <p className={`site-n1-summary${n1.secure_count === n1.checked ? ' site-ok' : ''}`}>{n1.summary}</p>
      {n1.lines.length > 0 && (
        <div className="site-tablewrap">
          <table className="site-table site-table--n1">
            <caption className="sr-only">Each loss of one line near the site, tested with the campus on</caption>
            <thead>
              <tr>
                <th scope="col">Line lost</th>
                <th scope="col" className="num">
                  Loaded before
                </th>
                <th scope="col">Worst line after</th>
                <th scope="col">Result</th>
              </tr>
            </thead>
            <tbody>
              {n1.lines.map((l) => {
                const res = n1Result(l)
                return (
                  <tr key={l.id}>
                    <th scope="row">
                      {l.label} <span className="muted">({fmt(l.kv)} kV)</span>
                    </th>
                    <td className="num">{fmt(l.pct_before)} %</td>
                    <td>
                      {l.worst_label} <span className="num">{fmt(l.worst_pct)} %</span>
                    </td>
                    <td>
                      <span className={res.kind === 'ok' ? 'site-ok' : 'site-lost'}>{res.text}</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted site-fine">
        The {n1.checked} most loaded lines within {fmt(n1.radius_km)} km, lost one at a time, with the campus at {fmtMw(n1.mw)} ({n1.how}).
      </p>
    </section>
  )
}

function BuiltAnyway({ b }) {
  return (
    <section className="site-sec site-anyway" aria-labelledby="site-any">
      <h3 className="panel-h" id="site-any">
        If it is built anyway
      </h3>
      <p>{b.summary}</p>
      <table className="site-table site-table--any">
        <caption className="sr-only">The cascade at each substation that does not take the campus</caption>
        <thead>
          <tr>
            <th scope="col">Where</th>
            <th scope="col" className="num">
              Steps
            </th>
            <th scope="col" className="num">
              People hit
            </th>
            <th scope="col" className="num">
              Without power at the end
            </th>
          </tr>
        </thead>
        <tbody>
          {b.sites.map((x) => (
            <tr key={x.substation.id}>
              <th scope="row">{x.substation.name}</th>
              <td className="num">{x.steps}</td>
              <td className="num site-lost">{fmt(x.people_hit)}</td>
              <td className="num site-lost">{fmt(x.people_lost)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted site-fine">People counts are estimates: lost megawatts times the state&apos;s people per megawatt of model load. The same cascade the map plays.</p>
    </section>
  )
}
