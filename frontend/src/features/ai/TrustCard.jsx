// FEATURE: "How we know it's right" — the top of the How AI is used panel. It answers the two questions a judge asks
// after the demo: is the physics right (our DC power flow against the dataset's own solved flows, Florida and all 48
// state models: backend/demo/validation.json) and is the AI making numbers up (the checker's ledger: per feature,
// what Gemini proposed, what the engine or checker accepted and rejected, and the last catches — each one only the
// offending token, never the AI's text). Data: GET /api/ai/status → validation, ledger; GET /api/ai/validation.
import { useState } from 'react'
import { getAiValidation } from './aiApi'

const n0 = (v) => (v == null ? '–' : Math.round(v).toLocaleString('en-US'))
const NB = '\u00a0' // a no-break space: a unit never wraps away from its number
const mw1 = (v) => (v == null ? '–' : `${v.toLocaleString('en-US', { maximumFractionDigits: 1, minimumFractionDigits: v < 10 ? 1 : 0 })}${NB}MW`)
const corr4 = (v) => (v == null ? '–' : v.toFixed(4))
const pct0 = (v) => (v == null ? '–' : `${Math.round(v * 100)}${NB}%`)

function when(iso) {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const s = Math.round((Date.now() - t) / 1000)
  if (s < 45) return 'just now'
  const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
  if (s < 3600) return rtf.format(-Math.round(s / 60), 'minute')
  if (s < 86400) return rtf.format(-Math.round(s / 3600), 'hour')
  return rtf.format(-Math.round(s / 86400), 'day')
}

function since(iso) {
  const t = Date.parse(iso || '')
  if (Number.isNaN(t)) return null
  return new Date(t).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

function Physics({ v }) {
  const [open, setOpen] = useState(false)
  const [all, setAll] = useState(null)
  const [err, setErr] = useState(null)
  if (!v?.florida) {
    return <p className="aitrust__muted">The power-flow validation file isn&apos;t built on this server (backend/demo/validate.py --all).</p>
  }
  const fl = v.florida
  const sum = v.summary || {}
  const low = sum.corr_min || {}
  const thr = v.threshold || {}
  const worst = fl.max_err_branch
  const toggle = (e) => {
    const isOpen = e.currentTarget.open
    setOpen(isOpen)
    if (isOpen && !all) {
      getAiValidation()
        .then((d) => (setAll(d), setErr(null)))
        .catch(() => setErr('Could not load the table. Close and open this again to retry.'))
    }
  }
  return (
    <>
      <p className="aitrust__lead">
        Our DC power flow reproduces the dataset&apos;s own solved flows. Florida: correlation {corr4(fl.corr)} across {n0(fl.compared)} branches.
      </p>
      <dl className="aitrust__stats">
        <div>
          <dt>Correlation, Florida</dt>
          <dd>{corr4(fl.corr)}</dd>
        </div>
        <div>
          <dt>Branches compared</dt>
          <dd>{n0(fl.compared)}</dd>
        </div>
        <div>
          <dt>Median gap</dt>
          <dd>{mw1(fl.median_abs_err_mw)}</dd>
        </div>
        <div>
          <dt>State models that pass</dt>
          <dd>
            {n0(sum.passed)} of {n0(sum.states)}
          </dd>
        </div>
      </dl>
      <p className="aitrust__note">
        Same inputs as the dataset&apos;s solution: its loads, its generator dispatch and its flows on the lines that cross the state border (generation is scaled only
        so each island balances). That solution is an AC one, with losses; ours is a lossless DC flow, so small gaps are expected.
      </p>
      <p className="aitrust__note">
        {pct0(fl.within_2mw_or_5pct_share)} of the {n0(fl.compared)} compared are within 2{NB}MW or 5{NB}%; the largest gap is {mw1(fl.max_abs_err_mw)}
        {worst ? ` on a branch carrying ${n0(Math.abs(worst.dataset_mw))}${NB}MW` : ''}. All {n0(sum.states)} state models: {n0(sum.branches_compared)} branches, lowest
        correlation {low.corr != null ? low.corr.toFixed(3) : '–'}
        {low.name ? ` (${low.name})` : ''}. A model passes with a correlation of at least {thr.corr_min ?? 0.9} and a calm base case: at most{' '}
        {Math.round((thr.base_over_max_share ?? 0.02) * 100)}{NB}% of its lines over their rating, and nobody without power.
      </p>
      <details className="aitrust__more" onToggle={toggle}>
        <summary>Every state</summary>
        {open && !all && !err && <p className="aitrust__muted">Loading…</p>}
        {err && <p className="aitrust__muted">{err}</p>}
        {all?.states && (
          <div className="aitrust__scroll">
            <table className="aitrust__table aitrust__table--states">
              <caption className="aitrust__sr">Our DC power flow against the dataset&apos;s own solved flows, by state model</caption>
              <thead>
                <tr>
                  <th scope="col">State</th>
                  <th scope="col">Correlation</th>
                  <th scope="col">Branches</th>
                  <th scope="col">Median gap</th>
                  <th scope="col">Largest gap</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(all.states).map(([code, r]) => (
                  <tr key={code} className={code === 'FL' ? 'aitrust__row--here' : undefined}>
                    <th scope="row" title={r.name}>
                      {code}
                    </th>
                    <td>{corr4(r.corr)}</td>
                    <td>{n0(r.compared)}</td>
                    <td>{mw1(r.median_abs_err_mw)}</td>
                    <td>{mw1(r.max_abs_err_mw)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {all?.command && (
          <p className="aitrust__muted">
            Rebuilt offline by <code>{all.command.replace('backend/venv/Scripts/python ', 'python ')}</code>
            {all.generated ? `, ${all.generated}` : ''}.
          </p>
        )}
      </details>
    </>
  )
}

function Ledger({ led, names }) {
  const rows = Object.entries(led?.by_surface || {})
    .filter(([, r]) => r.proposed > 0)
    .sort((a, b) => b[1].proposed - a[1].proposed)
  const tot = led?.totals || { proposed: 0, verified: 0, rejected: 0 }
  const from = since(led?.since)
  // the newest catch of each feature first, then the rest by time: one busy feature doesn't hide the others
  const all = led?.catches || []
  const firsts = all.filter((c, i) => all.findIndex((x) => x.surface === c.surface) === i)
  const catches = [...firsts, ...all.filter((c) => !firsts.includes(c))].slice(0, 5)
  if (!rows.length) {
    return (
      <p className="aitrust__muted">
        No checks recorded on this server yet. Ask a question about a case, open What it would take on a proposed data center, or draft an agreement on Build
        together: each check on what Gemini proposed lands here.
      </p>
    )
  }
  return (
    <>
      <p className="aitrust__lead">
        {n0(tot.proposed)} {tot.proposed === 1 ? 'check' : 'checks'} on what Gemini proposed: the engine or the checker accepted {n0(tot.verified)} and rejected{' '}
        {n0(tot.rejected)}.
      </p>
      <div className="aitrust__scroll">
        <table className="aitrust__table aitrust__table--ledger">
          <caption className="aitrust__sr">What Gemini proposed and what the checks accepted and rejected, by feature{from ? `, since ${from}` : ''}</caption>
          <thead>
            <tr>
              <th scope="col">Feature</th>
              <th scope="col">Checked</th>
              <th scope="col">Accepted</th>
              <th scope="col">Rejected</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([id, r]) => (
              <tr key={id}>
                <th scope="row">{names[id] || id}</th>
                <td>{n0(r.proposed)}</td>
                <td>{n0(r.verified)}</td>
                <td className={r.rejected ? 'aitrust__rej' : undefined}>{n0(r.rejected)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {catches.length > 0 && (
        <>
          <h5 className="aitrust__h5">Latest catches</h5>
          <ul className="aitrust__catches">
            {catches.map((c, i) => (
              <li key={`${c.at}-${i}`}>
                <span className="aitrust__catch">
                  Rejected {c.reason}
                  {c.token ? (
                    <>
                      {' '}
                      &mdash; <code>{c.token}</code>
                    </>
                  ) : null}
                </span>
                <span className="aitrust__meta">
                  {names[c.surface] || c.surface}
                  {when(c.at) ? ` · ${when(c.at)}` : ''}
                  {c.times > 1 ? ` · caught ${c.times} times` : ''}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="aitrust__muted">
        Counted each time a check runs: a cached Gemini answer is checked again every time it is served{led?.persisted ? '; kept across restarts' : ''}. A catch
        keeps only the offending number or word, with real company names removed.
      </p>
    </>
  )
}

export default function TrustCard({ st }) {
  const names = Object.fromEntries((st?.surfaces || []).map((s) => [s.id, s.name]))
  const limits = st?.validation?.limits
  return (
    <section className="aitrust" aria-labelledby="aitrust-title">
      <h3 id="aitrust-title" className="aitrust__title">
        How we know it&apos;s right
      </h3>
      <div className="aitrust__block">
        <h4 className="aitrust__h4">The physics</h4>
        <Physics v={st?.validation} />
      </div>
      <div className="aitrust__block">
        <h4 className="aitrust__h4">The fixes</h4>
        <p className="aitrust__lead">
          Every fix is re-run by the engine before you see it: a power-flow solve, and the full cascade whenever a line is still over.
        </p>
        <p className="aitrust__note">
          Gemini&apos;s plans are shown only when they hold. The engine&apos;s own fixes are labeled: <em>Holds</em> means no line trips and nobody loses power beyond
          what the event itself cuts off; <em>Partly</em> means it keeps power on for at least 10{NB}% of the people who would lose it, not everyone.
        </p>
      </div>
      <div className="aitrust__block">
        <h4 className="aitrust__h4">
          The checker&apos;s ledger
          {since(st?.ledger?.since) && <span className="aitrust__since">since {since(st.ledger.since)}</span>}
        </h4>
        <Ledger led={st?.ledger} names={names} />
      </div>
      <p className="aitrust__limits">
        <em>Limits:</em> {limits || 'DC power flow, steady state, a synthetic grid.'} Breakthrough Energy / Texas A&amp;M test system (CC-BY 4.0).
      </p>
    </section>
  )
}
