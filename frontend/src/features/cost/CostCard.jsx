// FEATURE: What it costs (owned by the cost track).
// Contract: default export CostCard({ title?, bare? }) — reads the case through useOverload() and prices it
// with backend/costs.py: four labeled estimates (the blackout, the upgrades to prevent it, the campus's
// power bill, who pays per household), each with its formula, assumption and sources, a total, and a
// Gemini estimate per line that falls back to the formula when Gemini isn't used. The AI column carries
// the shared AI label (features/ai/AiBadge: "Gemini", or "Plain version" when the formula stands in),
// and the footer opens "How AI is used" at this card's entry.
// Mount it anywhere (an inspector, the Brief); it needs no props. `bare` drops the card's own border and
// padding, for a panel that already has them.
import { useEffect, useRef, useState } from 'react'
import { useOverload } from '../../store'
import { EmptyState, ErrorBanner, Field, Loading } from '../../ui'
import AiBadge from '../ai/AiBadge'
import HowAiIsUsed from '../ai/HowAiIsUsed'
import './cost.css'
import { getCost, getCostAi } from './costApi'
import { PER, moneyRange } from './money'

const AI_SETTLE_MS = 900

// a line the formula prices above zero; the engine zeroes the rest whatever Gemini says (costs.py _clean_ai),
// so those lines are never Gemini's
const priced = (line) => line.high > 0

// "NORTH FORT MYERS 6" -> "North Fort Myers 6" (the synthetic substation names are all caps)
const nameCase = (s) => String(s || '').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())

const HOURS = [
  { value: 1, label: '1 hour' },
  { value: 6, label: '6 hours' },
  { value: 16, label: '16 hours' },
  { value: 48, label: '2 days' },
]

export default function CostCard({ title = 'What it costs', bare = false }) {
  const { caseBody, region, grid, site, extraSites, trip, loadFactor } = useOverload()
  const [hours, setHours] = useState(6)
  const [det, setDet] = useState(null) // /api/cost
  const [ai, setAi] = useState(null) // /api/cost/ai → {ai, fallback}
  const [status, setStatus] = useState('idle') // idle | loading | done | error
  const [aiStatus, setAiStatus] = useState('idle')
  const [error, setError] = useState(null)
  const [retry, setRetry] = useState(0)
  const req = useRef(0)

  const ready = region !== 'US' && (grid?.meta?.region || 'FL') === region
  const hasCase = ready && !!(site || extraSites.length || trip.length || loadFactor !== 1.0)
  const key = hasCase ? JSON.stringify(caseBody) : ''

  // price the case (debounced: the size slider fires on every settle), then ask Gemini for its column
  useEffect(() => {
    const id = ++req.current
    if (!key) {
      const t = setTimeout(() => {
        setDet(null)
        setAi(null)
        setStatus('idle')
        setAiStatus('idle')
      }, 0)
      return () => clearTimeout(t)
    }
    const body = JSON.parse(key)
    const t = setTimeout(async () => {
      setStatus('loading')
      setAiStatus('idle')
      setError(null)
      try {
        const d = await getCost(body, hours)
        if (id !== req.current) return
        setDet(d)
        setAi(null)
        setStatus('done')
        if (!d.lines.some(priced)) return // every line is $0: nothing for Gemini to estimate, don't spend the quota
      } catch (err) {
        if (id !== req.current) return
        setError(err)
        setStatus('error')
        return
      }
      setAiStatus('loading')
      // Gemini only once the case has held still for a moment (a slider drag shouldn't spend the AI quota)
      await new Promise((resolve) => setTimeout(resolve, AI_SETTLE_MS))
      if (id !== req.current) return
      try {
        const a = await getCostAi(body, hours)
        if (id !== req.current) return
        setAi({ items: a.ai, fallback: a.fallback })
        setAiStatus('done')
      } catch {
        if (id !== req.current) return
        setAiStatus('error') // the formula column stands on its own
      }
    }, 250)
    return () => clearTimeout(t)
  }, [key, hours, retry])

  const header = (
    <div className="cost__head">
      <div className="stack cost__title">
        <h2>{title}</h2>
        <p className="muted cost__sub">Estimates on a synthetic grid model. Every assumption is shown.</p>
      </div>
      <div className="cost__hours">
        <Field as="select" label="Lights out for" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          {HOURS.map((h) => (
            <option key={h.value} value={h.value}>
              {h.label}
            </option>
          ))}
        </Field>
      </div>
    </div>
  )

  let body
  if (region === 'US') {
    body = <EmptyState title="Pick a state first">Open a state on the map and drop a data center; its cost appears here.</EmptyState>
  } else if (!hasCase) {
    body = <EmptyState title="Nothing to price yet">Drop a data center, draw a storm or change the time of day, and the cost appears here.</EmptyState>
  } else if (status === 'error') {
    body = <ErrorBanner error={error} onRetry={() => setRetry((n) => n + 1)} />
  } else if (!det) {
    body = <Loading label="Pricing the case…" />
  } else {
    body = <CostBody det={det} ai={ai} aiStatus={aiStatus} updating={status === 'loading'} />
  }

  return (
    <section className={`${bare ? '' : 'card '}stack cost`} aria-label={title} aria-busy={status === 'loading' || undefined}>
      {header}
      {body}
    </section>
  )
}

function CostBody({ det, ai, aiStatus, updating }) {
  return (
    <div className={`stack cost__body${updating ? ' cost__body--stale' : ''}`}>
      {det.insights.length > 0 && (
        <ul className="cost__insights">
          {det.insights.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ul>
      )}
      <div className="row cost__ai-state" aria-live="polite">
        <AiState det={det} ai={ai} aiStatus={aiStatus} />
      </div>
      <ol className="cost__lines">
        {det.lines.map((ln) => (
          <CostLine key={ln.key} line={ln} ai={ai?.items?.[ln.key]} aiFallback={!!ai?.fallback} aiStatus={aiStatus} />
        ))}
      </ol>
      <div className="cost__total">
        <span className="cost__total-label">Total: {det.total_one_time.label.toLowerCase()}</span>
        <strong className="cost__total-value">{moneyRange(det.total_one_time.low, det.total_one_time.high)}</strong>
        <span className="muted cost__total-note">
          The blackout and the fix are alternatives: this is the path where nothing is planned. The power bill is paid by the campus, a year at a time.
        </span>
      </div>
      {det.notes.length > 0 && (
        <ul className="cost__notes muted">
          {det.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
      <div className="cost__foot">
        <HowAiIsUsed surface="cost" />
      </div>
    </div>
  )
}

// what Gemini's column is doing, for the whole card. "Gemini" only when at least one line with something to
// price carries Gemini's own range: the engine's $0 lines and formula stand-ins never count as Gemini's.
const GEMINI_TIP = "Gemini estimates each line from the case facts; an answer far outside the formula's range is rejected and that line repeats the formula"
const NOTHING_TIP = 'Every line is $0 for this case, so there is nothing for Gemini to estimate. Computed by the engine, no AI.'
const NOTHING_LINE_TIP = 'This line is $0 for this case. Computed by the engine, no AI.'
const RANGE_WHY = "Gemini's answer failed the range check"
function AiState({ det, ai, aiStatus }) {
  if (!det.lines.some(priced))
    return (
      <AiBadge by="engine" className="aib--wrap" title={NOTHING_TIP}>
        nothing to price, so no AI estimate
      </AiBadge>
    )
  if (aiStatus === 'loading') return <span className="muted cost__ai-wait">Gemini is estimating each line…</span>
  if (aiStatus === 'error') return <AiBadge by="fallback" className="aib--wrap">showing the formula only</AiBadge>
  if (!ai) return null
  const fromGemini = !ai.fallback && det.lines.some((ln) => priced(ln) && ai.items?.[ln.key] && !ai.items[ln.key].fallback)
  if (fromGemini)
    return (
      <AiBadge by="gemini" className="aib--wrap" title={GEMINI_TIP}>
        an estimate next to the formula
      </AiBadge>
    )
  // Gemini unavailable (the server says so), or it answered but none of its priced lines passed the range check
  return (
    <AiBadge by="fallback" why={ai.fallback ? undefined : RANGE_WHY} className="aib--wrap">
      the AI column repeats the formula
    </AiBadge>
  )
}

// the AI column's own label, per line: Gemini's range, the plain version (the formula repeated), or the
// engine's zero (nothing to price, so no AI)
function AiColumnLabel({ none, aiOk, aiMiss, aiStatus, hasAi }) {
  if (none) return <AiBadge by="engine" title={NOTHING_LINE_TIP}>nothing to price</AiBadge>
  if (aiOk || aiStatus === 'loading') return <AiBadge by="gemini" title={GEMINI_TIP} />
  if (aiMiss) return <AiBadge by="fallback" compact why="Gemini's answer for this line failed the range check" className="aib--wrap" />
  if (hasAi || aiStatus === 'error') return <AiBadge by="fallback" compact className="aib--wrap" />
  return 'AI estimate'
}

function CostLine({ line, ai, aiFallback, aiStatus }) {
  const per = PER[line.per] || line.per
  const none = !priced(line) // the engine's zero: nothing to price, whatever Gemini said
  const aiOk = !none && !!ai && !aiFallback && !ai.fallback // Gemini's own range for this line
  const aiMiss = !none && !!ai && !aiFallback && ai.fallback // Gemini answered, but not usably for this line
  return (
    <li className="cost__line">
      <div className="cost__line-head">
        <span className="cost__label">{line.label}</span>
        <span className="muted cost__per">{per}</span>
      </div>
      <div className="cost__figures">
        <div className="cost__fig">
          <span className="cost__fig-name">Formula</span>
          <strong className="cost__fig-value">{moneyRange(line.low, line.high)}</strong>
        </div>
        <div className="cost__fig cost__fig--ai">
          <span className="cost__fig-name">
            <AiColumnLabel none={none} aiOk={aiOk} aiMiss={aiMiss} aiStatus={aiStatus} hasAi={!!ai} />
          </span>
          {aiOk ? (
            <strong className="cost__fig-value">{moneyRange(ai.low, ai.high)}</strong>
          ) : aiStatus === 'loading' && !none ? (
            <span className="muted cost__fig-pending">…</span>
          ) : (
            <span className="muted cost__fig-pending">same as the formula</span>
          )}
        </div>
      </div>
      <p className="cost__formula">{line.formula}</p>
      {aiMiss && <p className="cost__ai-miss">Gemini&apos;s answer for this line was unusable, so it repeats the formula.</p>}
      <details className="cost__how">
        <summary>How we got this</summary>
        <div className="stack cost__how-body">
          <p>{line.assumption}</p>
          {aiOk && (
            <p>
              <span className="cost__how-tag">Gemini:</span> {ai.reasoning}
            </p>
          )}
          {line.key === 'upgrades' && line.items?.length > 0 && <UpgradeList line={line} />}
          <ul className="cost__sources">
            {line.sources.map((s) => (
              <li key={s.url + s.name}>
                <a href={s.url} target="_blank" rel="noreferrer">
                  {s.name}
                </a>
              </li>
            ))}
          </ul>
        </div>
      </details>
    </li>
  )
}

function UpgradeList({ line }) {
  const shown = line.items.slice(0, 8)
  return (
    <div className="stack cost__ups">
      <ul className="cost__up-list">
        {shown.map((u) => (
          <li key={u.id}>
            <span className="cost__up-name">
              {u.kind === 'transformer' ? `Transformer at ${nameCase(u.from_name)}` : `${nameCase(u.from_name)} to ${nameCase(u.to_name)}`}
              {u.applied ? ' (already applied)' : ''}
            </span>
            <span className="muted">
              {Math.round(u.old_mva).toLocaleString('en-US')} to {Math.round(u.new_mva).toLocaleString('en-US')} MVA · {u.method} · {moneyRange(u.low, u.high)}
            </span>
          </li>
        ))}
      </ul>
      {line.count > shown.length && <p className="muted">And {line.count - shown.length} more.</p>}
    </div>
  )
}
