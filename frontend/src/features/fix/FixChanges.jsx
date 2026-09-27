// WHAT THE FIX CHANGES (CLAUDE.md -> Decisions -> FIX DESCRIPTORS, user Sat 20:32: "explain exactly what it is instead
// of just saying 'yeah it's fixed, trust me'"). Rendered by the flip's result (Flip.jsx FlipResult) whenever a fix is
// on the case: "Run it again with the fix", Gemini's plan from "Let Gemini fix it", a Fix it upgrade, the deck's
// "Apply this fix". Three parts, every number the engine's or the report's own:
//   the before -> after that proves it   the busiest line, the lines over their limit (a what-if solve of each case),
//                                         the people hit and the cost of the outage (the cascade of each case)
//   each element in plain words          which line or transformer, from -> to MVA, the kind of work and its length,
//                                         its loading before -> after, its cost range (costs.py, per element) and a
//                                         typical time to build (backend/leadtimes.py); or, for a fix that changes
//                                         the campus, what it does (its size at each hour, on-site MW, the new site)
//   who applied it                        the engine's plan or Gemini's, and the engine's re-run that verified it
// plus a small green label on the map at each element (FixLabels.jsx). Sober look: green is the fix, red only what
// was lost, plain tabular figures.
import { useEffect, useId, useMemo, useState } from 'react'
import { fmt } from '../../geo'
import { useOverload } from '../../store'
import AiBadge from '../ai/AiBadge'
import { money, moneyRange } from '../cost/money'
import './fixChanges.css'
import { busiest } from './flipCase'
import FixLabels from './FixLabels'
import { campusChanges, fixElements, flexLevels, rangeKind, rangeWords, setAimedElement, slowest, useLeadTimes, workWords, yearsWords } from './fixModel'

const SHOW = 3 // elements listed before "Show all"
const pct = (p) => `${p >= 99.5 && p < 100.5 ? Number(p).toFixed(1) : Math.round(p)} %`

/**
 *   fix       the flip's fix (flipCase: describeFix + key/body/costSources, `fixit` from Fix it, `origin`)
 *   base      the case without the fix ({body, hit, stillOut, cost, measure}; hit/cost may be null: not known)
 *   now       the fixed cascade's people ({hit, stillOut}), or null when no cascade of the case with the fix has
 *             settled yet (ActiveFix before a run): the verdict then reads the what-if; steps: its step count
 *   costNow   the cost of the outage with the fix (the toll's own figure; null: not priced, the row is left out)
 *   fresh     the fixed case's what-if when the one on screen is its own, else null
 *   gridOnly  describe only what the fix raises on the grid (ActiveFix: the campus may have changed since)
 *   subject   who applied it, when the caller knows better ("The 3 upgrades on this case"; plural: say "with them");
 *             badge: show AiBadge
 */
export default function FixChanges({ fix, base, now, steps = 0, costNow = 0, fresh, gridOnly = false, subject: subjectIn, plural = false, badge = true }) {
  const { branchById, branchIndex, subName, subPos, focus, region, cascade } = useOverload()
  const lead = useLeadTimes(true, region)
  const [all, setAll] = useState(false)
  const hid = useId()
  // the highlight on the map must not outlive the list
  useEffect(() => () => setAimedElement(null), [])

  const els = useMemo(
    () =>
      fixElements(fix, {
        branchById,
        subName,
        baseUps: base?.body?.upgrades || {},
        loadsBefore: base?.measure?.loads || {},
        loadsAfter: (id) => {
          const i = branchIndex.get(Number(id))
          return fresh && i != null ? (fresh.loading_pct?.[i] ?? null) : null
        },
      }),
    [fix, base, fresh, branchById, branchIndex, subName],
  )
  const camp = useMemo(() => (gridOnly ? [] : campusChanges(fix)), [fix, gridOnly])
  // on the map: a label at each element, one at the campus for a fix that changes it, kept off the dark areas
  const mapCampus = useMemo(() => (gridOnly ? [] : campusLabel(fix, cascade)), [fix, cascade, gridOnly])
  const dark = useMemo(() => {
    const ids = new Set()
    ;(cascade?.steps || []).forEach((s) => (s.dark_subs || []).forEach((id) => ids.add(id)))
    return [...ids].map((id) => subPos(id)).filter(Boolean)
  }, [cascade, subPos])

  if (!fix) return null
  const by = fix.by === 'gemini' ? 'gemini' : 'engine'
  const ran = !!now // a cascade of the case with the fix has settled
  const nobody = ran && now.hit === 0 && now.stillOut === 0
  // who applied it, and the engine's re-run of the whole case with it (the fixed cascade on the map)
  const subject =
    subjectIn || (by === 'gemini' ? `Gemini's plan${fix.detail?.name ? ` (“${fix.detail.name}”)` : ''}` : fix.origin === 'fixit' ? "The engine's plan from Fix it" : "The engine's plan")
  // the lines over their limit in the what-if of the case with the fix (the verdict before a cascade has run)
  const overAfter = fresh ? (fresh.overloaded || []).length : gridOnly ? null : (fix.strain?.over ?? null)
  const stepWord = `${steps} ${steps === 1 ? 'step' : 'steps'}`
  const them = plural ? 'them' : 'it'
  const verdict = ran
    ? nobody && steps === 0
      ? 'verified by the engine: it re-ran the whole case with the fix, and nothing trips.'
      : nobody
        ? `checked by the engine: it re-ran the whole case with the fix; ${stepWord} still trip, but the grid reroutes around them and nobody loses power (estimate).`
        : steps === 0
          ? `checked by the engine: with ${them} no line trips, but ${fmt(now.stillOut)} people are still without power (estimate).`
          : `checked by the engine: with ${them} ${stepWord} still trip and ${fmt(now.hit)} people are hit (estimate).`
    : overAfter == null
      ? `the engine is solving the case with ${them}…`
      : overAfter === 0
        ? `checked by the engine in one solve of this case: with ${them}, no line is over its limit.`
        : `checked by the engine in one solve of this case: with ${them}, ${fmt(overAfter)} ${overAfter === 1 ? 'line is' : 'lines are'} still over the limit.`
  const verified = by === 'gemini' && (ran ? nobody : fix.verdict === 'holds' && overAfter === 0)

  // the before -> after: a what-if solve of each case (the weak point's own loading, lines over) and each case's
  // cascade. HOW-IT-WORKS.md gap #5: the row used to compare the overall busiest line before the fix (the weak
  // point, e.g. 141 %) against the overall busiest line after it, which can be a different, unrelated element
  // elsewhere on the grid (e.g. a Miami transformer already near its rating on its own) — read together they looked
  // like one line's before/after, so the "after" side is now this same weak point's own value.
  const m = base?.measure
  const weakEl = els.reduce((a, e) => (e.before != null && (!a || e.before > a.before) ? e : a), null)
  const peakAfter = fresh ? busiest(fresh) : gridOnly ? null : (fix.strain?.peak_pct ?? null)
  // move / flexible / on-site / shrink fixes raise no element, so `els` is empty and there is no weakEl to read
  // an after-value from: fall back to the busiest line anywhere after the fix (peakAfter), labelled honestly —
  // it may not be the same physical element as the weak point named on the left (REGRESSION: the row used to
  // require weakEl and simply vanished for these fixes)
  const weakAfter = weakEl ? weakEl.after : peakAfter
  const weakLabel = weakEl ? 'This weak point' : 'The busiest line after the fix'
  // a different, busier line elsewhere on the grid after the fix (not this weak point, which the fix already relieved)
  const elsewhereAfter = weakEl && peakAfter != null && weakAfter != null && peakAfter > weakAfter + 0.5 ? peakAfter : null
  const rows = [
    m?.peak != null && weakAfter != null && { k: weakLabel, unit: '% of its rating', a: pct(m.peak), b: pct(weakAfter), bad: m.peak > 100, ok: weakAfter <= 100 },
    m?.over != null && overAfter != null && { k: 'Lines over their limit', a: fmt(m.over), b: fmt(overAfter), bad: m.over > 0, ok: overAfter === 0 },
    ran && base?.hit != null && { k: 'People hit', unit: 'estimate', a: fmt(base.hit), b: fmt(now.hit), bad: base.hit > 0, ok: now.hit === 0 },
    ran && base?.cost && costNow != null && { k: 'Cost of the outage', unit: 'estimate, high end', a: money(base.cost.high), b: money(costNow), bad: true, ok: !(costNow > 0) },
  ].filter(Boolean)

  const shown = all ? els : els.slice(0, SHOW)
  const items = lead?.items || null
  const slow = items ? slowest(els, items) : null
  const bw = (fix.costSources || []).find((x) => /Black\s*&\s*Veatch/i.test(x?.name || ''))
  // what each priced range's two ends are, once per kind of work (costs.py's method: a line up to twice its rating
  // is reconductored or rebuilt; past twice, a new line or a new double-circuit line; a transformer, a unit beside it
  // or one new, larger unit)
  const ends = [...new Set(els.filter((el) => el.high != null).map(rangeKind))]
  const leadSrc = items ? [...new Set(els.flatMap((el) => items[el.lead]?.sources || []))].map((id) => lead.sources?.[id]).filter(Boolean) : []
  const show = (el) => {
    const b = branchById.get(Number(el.id))
    if (b) focus([subPos(b.from_sub), subPos(b.to_sub)].filter(Boolean))
  }

  return (
    <section className="fxc" aria-labelledby={hid}>
      <h3 className="fxc__h" id={hid}>
        What the fix changes
      </h3>
      <p className="fxc__who">
        {badge && <AiBadge by={by} verified={verified} />}
        <b>{subject}</b>, {verdict}
      </p>

      {rows.length > 0 && (
        <table className="fxc__proof">
          <caption className="fxc__sr">Before and after the fix, measured by the engine</caption>
          <thead>
            <tr>
              <th scope="col">
                <span className="fxc__sr">Measure</span>
              </th>
              <th scope="col">Without the fix</th>
              <th scope="col">With it</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.k}>
                <th scope="row">
                  {r.k}
                  {r.unit && <span className="fxc__unit">{r.unit}</span>}
                </th>
                <td className={r.bad ? 'fxc__was fxc__was--bad' : 'fxc__was'}>{r.a}</td>
                <td className={r.ok ? 'fxc__is fxc__is--ok' : 'fxc__is'}>{r.b}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {elsewhereAfter != null && (
        <p className="fxc__note">
          With the fix, the busiest line anywhere on the grid is {pct(elsewhereAfter)} of its rating — a different part of the network, not this
          weak point (which the fix brings to {pct(weakAfter)}) and not one this fix touches.
        </p>
      )}

      {camp.length > 0 && (
        <ul className="fxc__camp">
          {camp.map((c) => (
            <li key={c.key}>
              <span className="fxc__camp-t">{c.text}</span>
              {c.sub && <span className="fxc__camp-s">{c.sub}</span>}
            </li>
          ))}
        </ul>
      )}

      {els.length > 0 && (
        <>
          <ol className="fxc__list" aria-label={`${els.length} ${els.length === 1 ? 'element' : 'elements'} raised`}>
            {shown.map((el) => (
              <li key={el.id} className="fxc__el">
                <button
                  type="button"
                  className="fxc__name"
                  onClick={() => show(el)}
                  onMouseEnter={() => setAimedElement(el.id)}
                  onMouseLeave={() => setAimedElement(null)}
                  onFocus={() => setAimedElement(el.id)}
                  onBlur={() => setAimedElement(null)}
                  aria-label={`${el.name}${el.xf ? '' : ' line'}: ${el.from != null ? `${fmt(Math.round(el.from))} to ` : ''}${fmt(Math.round(el.to))} MVA. Show it on the map.`}
                >
                  <span className="fxc__nm">{el.name}</span>
                  <span className="fxc__kind">
                    {el.xf ? 'Transformer' : 'Line'}
                    {el.kv ? ` · ${fmt(Math.round(el.kv))} kV` : ''}
                  </span>
                </button>
                <p className="fxc__mva">
                  <b>
                    {el.from != null && <>{fmt(Math.round(el.from))} → </>}
                    {fmt(Math.round(el.to))} MVA
                  </b>{' '}
                  {el.from != null && <span className="fxc__add">(+{fmt(Math.round(el.to - el.from))})</span>} · {workWords(el)}
                  {/* the dataset gives this element no rating; the build step made one up (HOW-IT-WORKS.md gap #9) */}
                  {el.rateEst && (
                    <span className="fxc__est" title="The dataset leaves this element's rating blank; our build step set it 30 % above its flow in the dataset, or its voltage class's standard rating, whichever is larger.">
                      {' '}
                      (rating estimated)
                    </span>
                  )}
                </p>
                {el.before != null && el.after != null && (
                  <p className="fxc__load">
                    Loading <span className={el.before > 100 ? 'fxc__pct fxc__pct--over' : 'fxc__pct'}>{pct(el.before)}</span> →{' '}
                    <span className={el.after <= 100 ? 'fxc__pct fxc__pct--ok' : 'fxc__pct fxc__pct--over'}>{pct(el.after)}</span> of its rating
                  </p>
                )}
                {(el.high != null || items?.[el.lead]) && (
                  <p className="fxc__cost">
                    {el.high != null && (
                      <span title={rangeWords(el)}>
                        <b>{moneyRange(el.low, el.high)}</b>
                      </span>
                    )}
                    {el.high != null && items?.[el.lead] && ' · '}
                    {items?.[el.lead] && <span title={items[el.lead].basis}>typically {yearsWords(items[el.lead])} to build</span>}
                  </p>
                )}
              </li>
            ))}
          </ol>
          {els.length > SHOW && (
            <button type="button" className="fxc__all" aria-expanded={all} onClick={() => setAll((v) => !v)}>
              {all ? 'Show fewer' : `Show all ${els.length}`}
            </button>
          )}
          {(fix.cost || slow) && (
            <p className="fxc__total">
              {fix.cost && (
                <>
                  In all: <b>{moneyRange(fix.cost.low, fix.cost.high)}</b>
                </>
              )}
              {fix.cost && slow && ' · '}
              {slow && (
                <>
                  the slowest part{els.length > 1 ? `, the ${slow.el.xf ? 'transformer' : 'line'},` : ''} typically <b>{yearsWords(slow.it)}</b>
                </>
              )}
            </p>
          )}
          <p className="fxc__src">
            {ends.length > 0 && (
              <>
                Costs: {bw?.url ? <a href={bw.url} target="_blank" rel="noreferrer">Black &amp; Veatch for WECC</a> : 'Black & Veatch for WECC'} (2014) per-mile and
                per-MVA figures in 2024 dollars, each range from the cheaper way to build it to the dearer one ({ends.join('; ')}).{' '}
              </>
            )}
            {leadSrc.length > 0 && (
              <>
                Build times: typical ranges, not a schedule (
                {leadSrc.map((x, i) => (
                  <span key={x.url || x.short}>
                    {i > 0 && '; '}
                    {x.url ? (
                      <a href={x.url} target="_blank" rel="noreferrer">
                        {x.short || x.name}
                      </a>
                    ) : (
                      x.short || x.name
                    )}
                  </span>
                ))}
                ).{' '}
              </>
            )}
            Synthetic grid model; estimates.
          </p>
        </>
      )}
      <FixLabels elements={els} campus={mapCampus} dark={dark} />
    </section>
  )
}

// The campus's own label on the map, for a fix that changes the campus rather than the grid.
function campusLabel(fix, cascade) {
  if (!fix || cascade?.sub_lon == null) return []
  const at = [cascade.sub_lon, cascade.sub_lat]
  const d = fix.detail || {}
  const from = Number(fix.fromMw) || null
  const kept = fix.keptMw ?? d.mw ?? fix.apply?.mw ?? null
  if (fix.family === 'flexible' && kept != null) return [{ at, text: `Steps down to ${fmt(Math.round(kept))} MW`, sub: flexLevels(fix).here?.word || 'at this hour' }]
  if (fix.family === 'onsite' && d.onsite_mw) return [{ at, text: `${fmt(Math.round(d.onsite_mw))} MW made on site`, sub: `the grid supplies ${fmt(Math.round(d.net_mw || 0))} MW` }]
  if (fix.family === 'move') return [{ at, text: 'The campus, moved here', sub: (d.sites || [])[0]?.town || null }]
  if (kept != null && from && kept < from - 0.5 && ['shrink', 'combo', 'agentic'].includes(fix.family))
    return [{ at, text: `Built at ${fmt(Math.round(kept))} MW`, sub: `instead of ${fmt(from)} MW` }]
  return []
}
