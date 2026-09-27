import { useGridlock } from './context'
import Gloss from './Gloss'
import { GLOSS } from './glossary'
import { useAgreement } from './useAgreement'
import { displayName, fmtRange, toneOf, utilityShort } from './format'
import { localToday, rangeText, whenText, whereText } from './plain'

// The rail's first screen: what the page does in plain words, one worked example drawn from the live top-ranked pair
// (two companies building close together in the same months, and what building together could save), and the three
// steps of the guided path. The example reads the pair's plain draft (the same answer step 2 opens with, so picking it
// is instant); nothing on it is hardcoded.

// kept in step with the pair sheet's own EN/ES step names (PairSheet.jsx's S.en.steps / S.es.steps), so choosing a
// language inside a pair doesn't leave this mirrored list in the rail stuck in English
const RAIL_STEPS = {
  en: { steps: ['Find an overlap', 'See what it saves', 'Agree on a plan'], label: 'How it works' },
  es: { steps: ['Encontrar una coincidencia', 'Ver cuánto ahorra', 'Acordar un plan'], label: 'Cómo funciona' },
}

export function RailSteps() {
  const g = useGridlock()
  const open = !!g.draft
  const step = !open ? 1 : g.pairStep === 'plans' ? 3 : 2
  const { steps, label } = RAIL_STEPS[g.lang === 'es' ? 'es' : 'en']
  return (
    <ol className="bt-railsteps" aria-label={label}>
      {steps.map((s, i) => {
        const n = i + 1
        const state = n < step ? 'done' : n === step ? 'now' : 'next'
        return (
          <li key={s} className={`is-${state}`} aria-current={state === 'now' ? 'step' : undefined}>
            <span className="bt-railsteps__n" aria-hidden="true">
              {n}
            </span>
            {s}
          </li>
        )
      })}
    </ol>
  )
}

export default function Intro() {
  const g = useGridlock()
  const ready = g.ov.status === 'ready' || g.ov.status === 'refreshing'
  const top = ready ? g.overlaps.find((o) => o.group === 'together') || g.overlaps[0] : null
  const base = useAgreement(g.client, top?.id || null, { months: g.params.window_months, lang: 'en', ai: false })
  return (
    <div className="bt-intro">
      <p className="bt-intro__lede">
        Two power companies, Dominion Energy South Carolina (DESC) and Georgia Power, publish the lines and substations they plan to
        build. Where their projects meet in place and time, building together means{' '}
        <strong>
          <Gloss tip={GLOSS.outage}>one outage instead of two</Gloss>
        </strong>
        , one trip for the crews and shared prep work, and both save.
      </p>
      {/* the pairs failed to load: the error and its Retry sit under the filters, so no card sits here reading "Reading the two filings…" forever */}
      {g.ov.status !== 'error' && (
        <Example top={top} base={base} onOpen={() => top && g.openDraft(top)} loading={!ready || (top && base.status === 'loading')} />
      )}
    </div>
  )
}

function Example({ top, base, onOpen, loading }) {
  const g = useGridlock()
  if (!top) {
    return (
      <div className="bt-ex bt-ex--skel" aria-busy={loading || undefined}>
        <p className="bt-ex__eyebrow">{loading ? 'Reading the two filings…' : 'No pairs at these settings'}</p>
        <p className="bt-skel" />
        <p className="bt-skel bt-skel--short" />
      </div>
    )
  }
  const a = g.byId[top.a]
  const b = g.byId[top.b]
  const doc = base.status === 'ready' ? base.data : null
  const sv = doc?.draft?.savings
  const jw = doc?.draft?.joint_window
  const rank = top.displayRank ?? top.rank
  return (
    <article className="bt-ex" aria-labelledby="bt-ex-h">
      <p className="bt-ex__eyebrow" id="bt-ex-h">
        For example, pair {rank} from the filings
      </p>
      <ul className="bt-ex__who">
        {[a, b].map((p, i) => (
          <li key={p?.id || i}>
            <span className={`gl-swatch gl-swatch--${toneOf(p?.utility)}`} aria-hidden="true" />
            <span>
              <strong>{utilityShort(p?.utility || (i ? top.b_utility : top.a_utility))}</strong> {displayName(p?.name) || (i ? top.b : top.a)}
            </span>
          </li>
        ))}
      </ul>
      {jw && <MiniWindows jw={jw} parties={doc.draft.parties} />}
      <p className="bt-ex__line">
        {whereText(top)}, {whenText(top)}.
        {/* a line built on only part of its length: said in a tip here and in full inside the pair (as a paragraph it pushed the first pairs off a short screen) */}
        {(top.partial || []).filter(Boolean).map((x) => (
          <span key={x.part || x.text}>
            {' '}
            <Gloss tip={x.text} icon label="About this distance" />
          </span>
        ))}
      </p>
      <p className="bt-ex__save">
        {sv && (sv.items || []).length ? (
          <>
            Built together, it could save <strong>{fmtRange(sv.low, sv.high, sv.unit)}</strong>
          </>
        ) : base.status === 'loading' ? (
          <span className="bt-skel bt-skel--short" aria-hidden="true" />
        ) : (
          'Open it to see what building together could share.'
        )}
      </p>
      <div className="bt-ex__act">
        <button type="button" className="bt-ex__go" onClick={onOpen}>
          See what it saves
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M6 3.5L10.5 8 6 12.5" />
          </svg>
        </button>
        {sv && (sv.items || []).length > 0 && <span className="bt-ex__rough">rough estimate, from public unit costs</span>}
      </div>
    </article>
  )
}

// both build windows on one small time axis, the months they share in green, today marked
function MiniWindows({ jw, parties }) {
  const rows = [
    ['a', jw.a],
    ['b', jw.b],
  ].filter(([, w]) => w?.start && w?.end)
  if (!rows.length) return null
  const ms = (iso) => new Date(`${String(iso).slice(0, 10)}${String(iso).length <= 7 ? '-01' : ''}T00:00:00Z`).getTime()
  const y0 = Math.min(...rows.map(([, w]) => new Date(ms(w.start)).getUTCFullYear()))
  const y1 = Math.max(...rows.map(([, w]) => new Date(ms(w.end)).getUTCFullYear())) + 1
  const lo = Date.UTC(y0, 0, 1)
  const hi = Date.UTC(y1, 0, 1)
  const pos = (iso) => Math.max(0, Math.min(100, ((ms(iso) - lo) / (hi - lo)) * 100))
  const today = localToday()
  const now = ms(today) >= lo && ms(today) <= hi ? pos(today) : null
  return (
    <div className="bt-mini" role="img" aria-label={jw.text || 'Both build windows on one time axis'}>
      <div className="bt-mini__plot">
        {jw.overlap && jw.start && (
          <span className="bt-mini__joint" style={{ left: `${pos(jw.start)}%`, width: `${Math.max(1, pos(jw.end) - pos(jw.start))}%` }} />
        )}
        {rows.map(([side, w]) => {
          const p = parties?.find((x) => x.side === side)
          return (
            <span key={side} className="bt-mini__row">
              <span
                className={`bt-mini__bar bt-mini__bar--${toneOf(p?.code)}${w.assumed ? ' is-derived' : ''}`}
                style={{ left: `${pos(w.start)}%`, width: `${Math.max(1, pos(w.end) - pos(w.start))}%` }}
              />
            </span>
          )
        })}
        {now != null && <span className="bt-mini__now" style={{ left: `${now}%` }} />}
      </div>
      <div className="bt-mini__axis" aria-hidden="true">
        <span>{y0}</span>
        {jw.overlap && jw.start && (
          <span className="bt-mini__jl" style={{ left: `${(pos(jw.start) + pos(jw.end)) / 2}%` }}>
            {rangeText(jw.start, jw.end)}
          </span>
        )}
        <span>{y1}</span>
      </div>
    </div>
  )
}
