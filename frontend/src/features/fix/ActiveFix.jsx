// WHAT THE FIX CHANGES, for a fix that is on the case OUTSIDE the flip (CLAUDE.md -> Decisions -> FIX DESCRIPTORS:
// "when a fix is currently active, provide descriptors … instead of just saying 'yeah it's fixed, trust me'"):
//   Fix it's upgrades applied without the flip ("Apply the upgrades without running it", then Run the cascade)
//   a flipped fix still on the case after the hour or the size changed (the flip's own view has gone: another case)
//   any other upgrades on the case (a Strengthen plan tried on the map, a saved case, AI boom's plan)
// The results column (shell/ImpactPanel.jsx) shows the same descriptors as the flip's result (FixChanges): who applied
// it when that is known, each element in plain words with its cost (when the briefing report priced it) and a typical
// time to build, a green label at each element on the map, and the before -> after the engine measured: a what-if of
// this case WITHOUT these upgrades against the one on screen (busiest line, lines over, each element's loading), and,
// once a cascade of this case has settled, the people hit and the cost (the case without them: the flip's own run, or
// Florida's briefing report). Only what the fix raises on the grid is described: the campus may have changed since.
import { useMemo } from 'react'
import { useOverload } from '../../store'
import { cleanBody } from '../briefing/briefingApi'
import MapOverlay from '../briefing/MapOverlay'
import { cascadePeople, reportPeople } from '../cost/figures'
import FixChanges from './FixChanges'
import { describeFix, fixIds, flipKey, measureOf, plantsOut, useFlip, whatifOf, withFix } from './flipCase'
import { useCaseReport, useWhatIfOf } from './fixModel'
import { isApplied, useFix } from './fixStore'

const near = (a, b) => a != null && Math.abs(Number(a) - Number(b)) < 0.05

/**
 *   rate      the results column's loss rate ($ per person hit, high end): the cost with the fix
 *   settled   the cascade on the map (if any) has played to its end
 */
export default function ActiveFix({ rate, settled }) {
  const O = useOverload()
  const { caseBody, upgrades, result, solving, cascade, region, branchIndex, mode } = O
  const flip = useFlip()
  const found = useFix(caseBody) // Fix it's search for this case (kept across Apply: keyed without the upgrades)
  const n = Object.keys(upgrades || {}).length

  // which fix is on the case: the flip's (its upgrades are all on the case), Fix it's (exactly its upgrades), or
  // upgrades from elsewhere; `own` = the upgrades it adds, the rest were on the case without it
  const src = useMemo(() => {
    if (!n || region === 'US') return null
    const f = flip.fix
    const fu = f?.apply?.upgrades || {}
    if (Object.keys(fu).length && Object.entries(fu).every(([id, v]) => near(upgrades[id], v)))
      // (the report's strain after the fix was measured on the flip's case, not necessarily this one)
      return { kind: 'flip', fix: { ...f, apply: { upgrades: fu }, strain: null }, own: fu }
    const d = found.status === 'done' ? found.data : null
    if (d?.upgrades?.length && isApplied(d.apply, upgrades)) {
      const own = Object.fromEntries(d.upgrades.map((u) => [String(u.id), Number(u.new_mva)]))
      return {
        kind: 'fixit',
        fix: { apply: { upgrades: own }, by: 'engine', origin: 'fixit', verdict: d.calm ? 'holds' : 'partly', upgrades: d.upgrades.map((u) => Number(u.id)), fixit: d.upgrades },
        own,
      }
    }
    return { kind: 'other', fix: { apply: { upgrades: { ...upgrades } }, by: 'engine', upgrades: Object.keys(upgrades).map(Number) }, own: upgrades }
  }, [n, region, flip.fix, found, upgrades])

  // the same case without the fix's upgrades (the ones it had before stay)
  const baseBody = useMemo(() => {
    if (!src) return null
    const b = cleanBody(caseBody)
    delete b.preset
    b.upgrades = Object.fromEntries(Object.entries(upgrades || {}).filter(([id]) => !(id in src.own)))
    return b
  }, [src, caseBody, upgrades])
  const baseKey = useMemo(() => (baseBody ? flipKey(baseBody) : ''), [baseBody])
  const known = flip.base && flip.base.key === baseKey ? flip.base : null // the flip ran exactly this case without it
  const ran = !!settled && !!cascade && !plantsOut(cascade) // a cascade of THIS case (a plant outage is another case)

  // the engine's what-if of the case without the fix (one sparse solve), and the one on screen with it
  const before = useWhatIfOf(baseBody, !!src)
  const fresh = !solving && whatifOf(result, caseBody) ? result : null
  // Florida's briefing report of the case without the fix: Fix it's elements priced (cost.items; Fix it asked for this
  // report before Apply, so it is usually already in) and, after a cascade, its people and cost. Never elsewhere
  // (LAZY: a new report is a full briefing computation), and never for upgrades from elsewhere.
  const wantReport = !!src && !known && region === 'FL' && (src.kind === 'fixit' || (src.kind === 'flip' && ran))
  const report = useCaseReport(baseBody, wantReport)

  const fix = useMemo(() => {
    if (!src) return null
    if (src.kind !== 'fixit') return src.fix
    // the report's verified fix with exactly these upgrades: its per-element prices and sources
    const target = flipKey(caseBody)
    const same = (report?.fixes || []).find((f) => f.apply && (f.verdict === 'holds' || f.verdict === 'partly') && flipKey(withFix(baseBody, f.apply)) === target)
    const priced = same ? describeFix(same, Number(report?.case?.mw) || 0) : null
    return priced ? { ...priced, ...src.fix, strain: null, costSources: report?.cost?.sources || null } : src.fix
  }, [src, report, baseBody, caseBody])

  const measure = useMemo(() => (before.result && fix ? measureOf(before.result, fixIds(fix), branchIndex) : null), [before.result, fix, branchIndex])
  const lines = useMemo(() => (fix?.upgrades || []).map((id) => ({ id, tone: 'fix' })), [fix])

  if (!src || !fix || (!result && !cascade)) return null
  // the case without the fix, as far as it is known: the flip's own run of it, else the report's figures
  const people = known ? { hit: known.hit, stillOut: known.stillOut } : report ? reportPeople(report) : null
  const c = report?.cost
  const cost = known ? known.cost : c?.blackout_high_usd ? { high: Number(c.blackout_high_usd), low: Number(c.ranges?.blackout_usd?.[0]) || 0 } : null
  const base = { body: baseBody, measure, hit: people?.hit ?? null, stillOut: people?.stillOut ?? null, cost }
  const now = ran ? cascadePeople(cascade) : null
  // the cost with the fix: the toll's own rule (people hit x the case's loss rate); unknown without a rate
  const costNow = !now ? null : now.hit > 0 ? (rate?.high ? now.hit * rate.high : null) : 0
  const subject = src.kind === 'other' ? `The ${n} ${n === 1 ? 'upgrade' : 'upgrades'} on this case` : undefined
  return (
    <>
      <FixChanges
        fix={fix}
        base={base}
        now={now}
        steps={ran ? cascade.steps?.length || 0 : 0}
        costNow={costNow}
        fresh={fresh}
        gridOnly
        subject={subject}
        plural={src.kind === 'other' && n !== 1}
        badge={src.kind !== 'other'}
      />
      {/* the upgraded lines in green (Fix it draws its own while its panel is open) */}
      {mode !== 'fix' && <MapOverlay lines={lines} layerKey={`active-${baseKey}`} />}
    </>
  )
}
